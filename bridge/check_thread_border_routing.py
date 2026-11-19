#!/usr/bin/env python3
"""Run a temporary border router on the verified Apple TV Thread network."""
from contextlib import contextmanager, suppress
from pathlib import Path
import re
import resource
import signal
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep

from check_openthread import check_ethernet, relay, root
from check_thread_attachment import Cli, load_dataset, show_trel_counters, wait_for_trel_peer

ETHERNET = "en8"
BINARY = root / "work/ot-border-build/src/posix/ot-cli"
FORWARDING = "net.inet6.ip6.forwarding"
SUDO = ["/usr/bin/sudo", "-n"]


def sysctl(*args):
    try:
        return subprocess.check_output(
            SUDO + ["/usr/sbin/sysctl", *args], text=True, timeout=4,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("Could not read or update macOS IPv6 forwarding") from None


@contextmanager
def temporary_forwarding():
    original = sysctl("-n", FORWARDING)
    if original not in {"0", "1"}:
        raise RuntimeError("Unexpected IPv6 forwarding setting")
    print(f"Original IPv6 forwarding value: {original}", flush=True)
    try:
        if original == "0":
            sysctl("-w", f"{FORWARDING}=1")
        if sysctl("-n", FORWARDING) != "1":
            raise RuntimeError("IPv6 forwarding did not become active")
        yield
    finally:
        if original == "0":
            sysctl("-w", f"{FORWARDING}=0")
        if sysctl("-n", FORWARDING) != original:
            raise RuntimeError("IPv6 forwarding setting was not restored")
        print(f"IPv6 forwarding restored to its original value ({original}).", flush=True)


def role(cli):
    return cli.command("Check Thread role", b"state")


def require_role(cli, target, seconds):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        state = role(cli)
        if b"leader" in state:
            raise RuntimeError("Unexpected leader role; stopping the test")
        if target in state:
            return
        sleep(1)
    raise RuntimeError(f"Mac did not reach the Thread {target.decode()} role")


def child_count(cli):
    lines = cli.command("Count Thread children", b"child list")
    ids = [token for line in lines for token in line.split() if token != b">"]
    if any(not token.isdigit() for token in ids):
        return None
    return len(ids)


CHILD_FIELDS = ("RLOC16", "Extended MAC", "LQ In", "Timeout", "Age")


def child_table(cli):
    """Return one dict per attached child, or None if the table is unreadable."""
    lines = cli.command("Read Thread child table", b"child table")
    header = None
    children = []
    for line in lines:
        if not line.startswith(b"|"):
            continue
        cells = [cell.strip().decode(errors="replace") for cell in line.strip(b"|").split(b"|")]
        if header is None:
            if "Extended MAC" in cells and "RLOC16" in cells:
                header = cells
            continue
        if len(cells) != len(header):
            return None
        row = dict(zip(header, cells))
        if not re.fullmatch(r"[0-9a-f]{16}", row.get("Extended MAC", "")):
            return None
        children.append({field: row.get(field, "?") for field in CHILD_FIELDS})
    return children if header is not None else None


def show_children(label, children):
    if children is None:
        print(f"{label}: child table unavailable", flush=True)
        return
    print(f"{label}: {len(children)} child(ren)", flush=True)
    for child in children:
        print("  " + ", ".join(f"{field} {child[field]}" for field in CHILD_FIELDS), flush=True)


def routing_counts(cli):
    counts = {}
    for line in cli.command("Read border routing counters", b"counters br"):
        match = re.fullmatch(rb"(Inbound Unicast|Outbound Unicast): Packets ([0-9]+) Bytes [0-9]+", line)
        if not match:
            match = re.fullmatch(rb"(RA TxSuccess): ([0-9]+)", line)
        if match:
            label, count = match.groups()
            counts[label.decode()] = int(count)
    if len(counts) != 3:
        raise RuntimeError("Border routing counters were unavailable")
    return counts


def require_partition(cli, expected):
    lines = cli.command("Check existing Thread partition", b"partitionid")
    if lines != expected:
        raise RuntimeError("Thread partition changed; stopping the test")


def monitor_routing(cli, partition, seconds):
    initial = routing_counts(cli)
    start_children = child_count(cli)
    high_children = start_children
    peak_table = None
    seen = {}
    print(f"Border routing running on {ETHERNET} for up to {seconds} seconds.", flush=True)
    print("Press a bedside HS2 button with a known Home action now and note what happens.", flush=True)
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        if b"router" not in role(cli):
            raise RuntimeError("The Mac left the router role; stopping the test")
        require_partition(cli, partition)
        current_children = child_count(cli)
        if current_children is not None:
            if peak_table is None or current_children > (high_children or 0):
                table = child_table(cli)
                if table is not None:
                    peak_table = table
            high_children = max(high_children or 0, current_children)
            if current_children:
                for child in child_table(cli) or ():
                    seen[child["Extended MAC"]] = child
        sleep(min(2, max(0, deadline - monotonic())))
    start_label = start_children if start_children is not None else "unavailable"
    peak_label = high_children if high_children is not None else "unavailable"
    print(f"Attached children at start: {start_label}; peak: {peak_label}", flush=True)
    show_children("Child table at peak", peak_table)
    show_children("Every child seen during the window", list(seen.values()))
    for name, value in routing_counts(cli).items():
        print(f"{name} during test: {value - initial[name]}", flush=True)
    show_trel_counters(cli)


def run_trial(cli, dataset, interface_index, hold_seconds=75):
    cli.command("Disable automatic border routing", b"br disable")
    cli.command("Disable router eligibility", b"routereligible disable")
    if b"Disabled" not in cli.command("Verify router eligibility", b"routereligible"):
        raise RuntimeError("Could not verify that router eligibility is off")
    cli.command("Load Apple TV dataset", b"dataset set active " + dataset.hex().encode())
    wait_for_trel_peer(cli, interface_index)
    cli.command("Bring up Thread interface", b"ifconfig up")
    cli.command("Join existing Apple TV mesh", b"thread start")
    require_role(cli, b"child", 30)
    partition = cli.command("Read attached Thread partition", b"partitionid")
    if len(partition) != 1 or not partition[0].isdigit():
        raise RuntimeError("Could not identify the existing Thread partition")
    print("Attached to the Apple TV network as a child.", flush=True)
    cli.command("Enable router eligibility", b"routereligible enable")
    cli.command("Request router role in existing partition", b"state router")
    require_role(cli, b"router", 35)
    require_partition(cli, partition)
    print("Promoted to a router in the same Thread partition.", flush=True)
    infra = cli.command("Check Ethernet interface status", b"br infraif")
    if f"if-index:{interface_index}, is-running:yes".encode() not in infra:
        raise RuntimeError("OpenThread did not report the Ethernet interface as running")
    cli.command("Set low Thread route preference", b"br routeprf low")
    with temporary_forwarding():
        try:
            cli.command("Enable border routing", b"br enable")
            if b"running" not in cli.command("Verify border routing", b"br state"):
                raise RuntimeError("Border Routing Manager did not start")
            monitor_routing(cli, partition, hold_seconds)
        finally:
            cli.command("Withdraw experimental border routes", b"br disable")
            sleep(2)


def quiet_stderr(pipe):
    for _ in pipe:
        pass


def cleanup(cli):
    failures = []
    for label, command in (
        ("Disable border routing", b"br disable"),
        ("Stop Thread", b"thread stop"),
        ("Disable router eligibility", b"routereligible disable"),
        ("Bring interface down", b"ifconfig down"),
        ("Disable TREL", b"trel disable"),
        ("Disable Ethernet discovery", b"mdns disable"),
    ):
        try:
            cli.command(label, command)
        except RuntimeError as error:
            failures.append(str(error))
    return failures


def main():
    check_ethernet(ETHERNET)
    if not BINARY.is_file():
        raise RuntimeError("Border routing build is missing")
    dataset = load_dataset()
    interface_index = socket.if_nametoindex(ETHERNET)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with TemporaryDirectory(prefix="ot-border-", dir="/tmp") as directory:
        path = str(Path(directory) / "radio.sock")
        errors = []
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(path)
            listener.listen(1)
            worker = Thread(target=relay, args=(listener, errors, 160), daemon=True)
            worker.start()
            command = SUDO + [
                str(BINARY), "-d", "0", "--data-path", directory, "-B", ETHERNET,
                f"spinel+hdlc+unix://{path}?no-reset", f"trel://{ETHERNET}",
            ]
            # Keep Terminal's interrupt focused on the checker so OpenThread
            # can process route withdrawal before its input is closed.
            with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, process_group=0) as process:
                stderr = Thread(target=quiet_stderr, args=(process.stderr,), daemon=True)
                stderr.start()
                cli = Cli(process)
                try:
                    run_trial(cli, dataset, interface_index)
                finally:
                    active_error = sys.exception()
                    failures = cleanup(cli)
                    with suppress(BrokenPipeError):
                        process.stdin.close()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    stderr.join(1)
                    if failures:
                        print(f"Cleanup could not confirm: {failures[0]}", flush=True)
                        if active_error is None:
                            raise RuntimeError("Could not confirm clean shutdown")
            worker.join(1)
            if errors:
                raise RuntimeError("The bridge disconnected during the trial")
    print("Border routing test ended; OpenThread stopped.")


def interrupt(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupt)
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit("Trial interrupted; restore Apple's radio service.") from None
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
