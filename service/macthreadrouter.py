#!/usr/bin/env python3
"""Keep this Mac on the Apple TV Thread mesh as a Thread router.

Runs as a root launchd daemon. Each session:
  1. Suppresses Apple's threadradiod with daemonslayer (resumed on exit).
  2. Relays OpenThread's Spinel stream to the Mac's TSI radio.
  3. Joins the existing mesh as a child (cannot become leader), then asks
     for the router role in the same partition.
  4. Watches the role. Leader role, a partition change or a long detach
     ends the session so the Mac never runs its own partition.

Border routing stays disabled and IPv6 forwarding is never touched.
On any exit OpenThread is stopped, the radio channel is closed and Apple's
daemon is resumed. Repeated failures back off up to 30 minutes.
"""
import json
import os
import re
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

APP = Path(__file__).resolve().parent
LIB = APP / "lib"
sys.path.insert(0, str(LIB))

BINARY = APP / "ot-cli"
CONFIG = APP / "config.json"
STATE = APP / "state.json"
KEYCHAIN = "/Library/Keychains/System.keychain"
KEY_SERVICE = "local.macthreadrouter.dataset"
KEY_ACCOUNT = "active-dataset"
APPLE_DAEMON = "threadradiod"
FLAG = 0x7E
MAX_FRAME = 2048

stop = threading.Event()


def log(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


# ---------------------------------------------------------------- config

def load_config():
    cfg = json.loads(CONFIG.read_text())
    if not re.fullmatch(r"en[0-9]+", cfg["interface"]):
        raise RuntimeError("Config interface must look like en8")
    cfg["extended_pan_id"] = bytes.fromhex(cfg["extended_pan_id"])
    cfg["network_name"] = cfg["network_name"].encode()
    return cfg


def parse_tlvs(raw):
    fields, offset = {}, 0
    while offset + 2 <= len(raw):
        kind, size = raw[offset:offset + 2]
        end = offset + 2 + size
        if kind in fields or end > len(raw):
            raise RuntimeError("Stored dataset is malformed")
        fields[kind] = raw[offset + 2:end]
        offset = end
    if offset != len(raw):
        raise RuntimeError("Stored dataset is malformed")
    return fields


def load_dataset(cfg):
    result = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", KEY_SERVICE,
         "-a", KEY_ACCOUNT, "-w", KEYCHAIN],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Thread dataset is missing from the System keychain")
    try:
        raw = bytes.fromhex(result.stdout.strip())
    except ValueError:
        raise RuntimeError("Stored dataset is not hex") from None
    fields = parse_tlvs(raw)
    if fields.get(2) != cfg["extended_pan_id"] or fields.get(3) != cfg["network_name"]:
        raise RuntimeError("Stored dataset does not match the configured Apple TV network")
    return raw


def read_failures():
    try:
        return int(json.loads(STATE.read_text())["failures"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def write_failures(count):
    try:
        STATE.write_text(json.dumps({"failures": count}))
    except OSError:
        pass


# ---------------------------------------------------------------- system

def link_active(interface):
    result = subprocess.run(["/sbin/ifconfig", interface],
                            capture_output=True, text=True, check=False)
    return result.returncode == 0 and "status: active" in result.stdout


def apple_pids():
    result = subprocess.run(["/usr/bin/pgrep", "-x", APPLE_DAEMON],
                            capture_output=True, text=True, check=False)
    return [int(pid) for pid in result.stdout.split()]


def is_stopped(pid):
    result = subprocess.run(["/bin/ps", "-o", "state=", "-p", str(pid)],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip().startswith("T")


def start_slayer():
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from threadradio.daemonslayer import main; "
        f"sys.argv = ['daemonslayer', {APPLE_DAEMON!r}]; main()"
    )
    return subprocess.Popen([sys.executable, "-I", "-c", code, str(LIB)])


def wait_for_radio_release(slayer, seconds=15):
    """Wait until threadradiod is either stopped or absent.

    launchd starts threadradiod on demand, so after daemonslayer kills it
    there may be no new instance for a while. That also leaves the radio
    free; daemonslayer stops any instance that appears later.
    """
    deadline = time.monotonic() + seconds
    absent_since = None
    while time.monotonic() < deadline:
        if slayer.poll() is not None:
            raise RuntimeError("daemonslayer exited early")
        pids = apple_pids()
        if pids and all(is_stopped(pid) for pid in pids):
            time.sleep(0.5)
            return
        if not pids:
            absent_since = absent_since or time.monotonic()
            if time.monotonic() - absent_since >= 2:
                return
        else:
            absent_since = None
        time.sleep(0.2)
    raise RuntimeError("Apple's threadradiod was not suppressed")


def stop_slayer(slayer):
    if slayer is None:
        return
    if slayer.poll() is None:
        slayer.terminate()  # daemonslayer resumes threadradiod on SIGTERM
        try:
            slayer.wait(timeout=10)
        except subprocess.TimeoutExpired:
            slayer.kill()
            slayer.wait()
    for pid in apple_pids():
        if is_stopped(pid):
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
    pids = apple_pids()
    if pids and not any(is_stopped(pid) for pid in pids):
        log(f"Apple's {APPLE_DAEMON} is running again (pid {', '.join(map(str, pids))}).")
    elif not pids:
        subprocess.run(["/bin/launchctl", "kickstart", f"system/com.apple.{APPLE_DAEMON}"],
                       check=False, capture_output=True)
        log(f"Asked launchd to start Apple's {APPLE_DAEMON}.")
    else:
        log(f"WARNING: Apple's {APPLE_DAEMON} still looks stopped.")


# ---------------------------------------------------------------- radio relay

def extract_frames(pending, chunk):
    for byte in chunk:
        if byte == FLAG:
            if len(pending) > 1:
                yield bytes(pending) + bytes([FLAG])
            pending[:] = bytes([FLAG])
        elif pending:
            if len(pending) >= MAX_FRAME - 1:
                raise ValueError("HDLC frame exceeds the slot limit")
            pending.append(byte)


class Relay(threading.Thread):
    """Relay one OpenThread Unix-socket session to the TSI radio channel."""

    def __init__(self, listener):
        super().__init__(daemon=True)
        self.listener = listener
        self.done = threading.Event()
        self.error = None

    def run(self):
        try:
            from threadradio import hdlc, open_channel, read_slot, write_slot

            self.listener.settimeout(15)
            conn, _ = self.listener.accept()
            with conn, open_channel("tsi") as channel:
                conn.setblocking(False)
                pending = bytearray()
                while not self.done.is_set():
                    try:
                        chunk = conn.recv(4096)
                    except BlockingIOError:
                        chunk = None
                    if chunk == b"":
                        return
                    if chunk:
                        for frame in extract_frames(pending, chunk):
                            payload = hdlc.decode(frame)
                            if payload is None or len(payload) < 2 or payload[0] & 0xC0 != 0x80:
                                raise ValueError("Invalid Spinel frame from OpenThread")
                            if not write_slot(channel, frame, timeout_ms=100):
                                raise TimeoutError("TSI transmit slot timed out")
                    slot = read_slot(channel, timeout_ms=1 if chunk else 5)
                    if slot:
                        conn.setblocking(True)
                        conn.settimeout(1)
                        conn.sendall(slot)
                        conn.setblocking(False)
        except Exception as error:  # reported to the supervisor
            self.error = f"{type(error).__name__}: {error}"
        finally:
            self.done.set()


# ---------------------------------------------------------------- OpenThread CLI

class Cli:
    def __init__(self, process):
        self.process = process
        self.pending = bytearray()

    def read_line(self, deadline):
        fd = self.process.stdout.fileno()
        while b"\n" not in self.pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                raise RuntimeError("OpenThread did not respond")
            data = os.read(fd, 4096)
            if not data:
                raise RuntimeError("OpenThread disconnected")
            self.pending.extend(data)
            if len(self.pending) > 16384:
                raise RuntimeError("Unexpected OpenThread output")
        index = self.pending.index(b"\n")
        line = bytes(self.pending[:index]).strip().removeprefix(b"> ")
        del self.pending[:index + 1]
        return line

    def command(self, label, command):
        try:
            self.process.stdin.write(command + b"\n")
            self.process.stdin.flush()
            deadline = time.monotonic() + 4
            result = []
            while (line := self.read_line(deadline)) != b"Done":
                if line.startswith(b"Error"):
                    match = re.fullmatch(rb"Error (\d+): ([A-Za-z]+)", line)
                    raise RuntimeError(match.group(0).decode() if match else "command rejected")
                result.append(line)
            return result
        except (OSError, RuntimeError) as error:
            raise RuntimeError(f"{label}: {error}") from None


def role(cli):
    lines = cli.command("Check Thread role", b"state")
    for name in (b"leader", b"router", b"child", b"detached", b"disabled"):
        if any(name in line for line in lines):
            return name.decode()
    return "unknown"


def partition(cli):
    lines = cli.command("Read Thread partition", b"partitionid")
    if len(lines) != 1 or not lines[0].isdigit():
        raise RuntimeError("Could not read the Thread partition")
    return lines[0].decode()


def wait_for_role(cli, targets, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not stop.is_set():
        current = role(cli)
        if current == "leader":
            raise RuntimeError("OpenThread became leader; refusing to run its own partition")
        if current in targets:
            return current
        stop.wait(1)
    return None


def wait_for_trel_peer(cli, cfg, seconds=60):
    marker = b"ExtPanId:" + cfg["extended_pan_id"].hex().encode()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not stop.is_set():
        if any(marker in line for line in cli.command("List TREL peers", b"trel peers list")):
            return
        stop.wait(1)
    raise RuntimeError("Apple TV TREL peer was not found on the Ethernet link")


def child_count(cli):
    lines = cli.command("Count Thread children", b"child list")
    ids = [token for line in lines for token in line.split() if token != b">"]
    return len(ids) if all(token.isdigit() for token in ids) else None


def join_as_router(cli, cfg, dataset):
    cli.command("Disable border routing", b"br disable")
    if any(b"running" in line for line in cli.command("Check border routing", b"br state")):
        raise RuntimeError("Border routing did not stay disabled")
    cli.command("Disable router eligibility", b"routereligible disable")
    if not any(b"Disabled" in line for line in cli.command("Verify router eligibility", b"routereligible")):
        raise RuntimeError("Could not verify that router eligibility is off")
    cli.command("Load Apple TV dataset", b"dataset set active " + dataset.hex().encode())
    cli.command("Enable TREL", b"trel enable")
    index = socket.if_nametoindex(cfg["interface"])
    cli.command("Enable Ethernet discovery", f"mdns enable {index}".encode())
    wait_for_trel_peer(cli, cfg)
    log("Found the Apple TV over TREL.")
    cli.command("Bring up Thread interface", b"ifconfig up")
    cli.command("Join existing mesh", b"thread start")
    if wait_for_role(cli, {"child"}, 60) is None:
        raise RuntimeError("Did not attach to the Apple TV mesh within 60 seconds")
    joined = partition(cli)
    log(f"Attached as a child in partition {joined}.")
    cli.command("Enable router eligibility", b"routereligible enable")
    request_router(cli)
    if wait_for_role(cli, {"router"}, 60) == "router":
        if partition(cli) != joined:
            raise RuntimeError("Partition changed during router promotion")
        log("Promoted to router in the same partition.")
    else:
        log("Still a child after 60 seconds; will keep asking for the router role.")
    return joined


def request_router(cli):
    try:
        cli.command("Request router role", b"state router")
    except RuntimeError as error:
        log(f"Router request not accepted yet ({error}).")


def monitor(cli, process, relay, joined):
    last_role, last_children = None, None
    detached_since = None
    last_request = time.monotonic()
    attached_since = None
    while not stop.is_set():
        if process.poll() is not None:
            raise RuntimeError(f"OpenThread exited (code {process.returncode})")
        if relay.done.is_set():
            raise RuntimeError(f"Radio relay ended: {relay.error or 'disconnected'}")
        current = role(cli)
        now = time.monotonic()
        if current == "leader":
            raise RuntimeError("Became leader; stopping so the Mac never runs its own partition")
        if current in {"child", "router"}:
            detached_since = None
            if partition(cli) != joined:
                raise RuntimeError("Thread partition changed; restarting the session")
        else:
            detached_since = detached_since or now
            if now - detached_since > 120:
                raise RuntimeError(f"Thread role was '{current}' for over 2 minutes")
        if current == "child" and now - last_request > 300:
            request_router(cli)
            last_request = now
        if current in {"child", "router"}:
            attached_since = attached_since or now
            if now - attached_since > 600 and read_failures():
                write_failures(0)  # stable for 10 minutes: clear the backoff
        else:
            attached_since = None
        children = child_count(cli) if current == "router" else 0
        if current != last_role or children != last_children:
            log(f"Role: {current}; attached children: {children}")
            last_role, last_children = current, children
        stop.wait(5)


def shutdown_openthread(cli, process):
    for label, command in (
        ("Stop Thread", b"thread stop"),
        ("Disable router eligibility", b"routereligible disable"),
        ("Bring interface down", b"ifconfig down"),
        ("Disable TREL", b"trel disable"),
        ("Disable Ethernet discovery", b"mdns disable"),
    ):
        if process.poll() is not None:
            break
        try:
            cli.command(label, command)
        except RuntimeError as error:
            log(f"Cleanup: {error}")
    try:
        process.stdin.close()
    except OSError:
        pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def drain(pipe):
    for _ in pipe:
        pass


def run_session(cfg, dataset):
    slayer = None
    try:
        slayer = start_slayer()
        wait_for_radio_release(slayer)
        log("Apple's threadradiod suppressed; radio released.")
        with TemporaryDirectory(prefix="mtr-", dir="/private/var/run") as directory:
            sock_path = str(Path(directory) / "radio.sock")
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(sock_path)
                os.chmod(sock_path, 0o600)
                listener.listen(1)
                relay = Relay(listener)
                relay.start()
                command = [
                    str(BINARY), "-d", "0", "--data-path", directory,
                    "-B", cfg["interface"],
                    f"spinel+hdlc+unix://{sock_path}?no-reset",
                    f"trel://{cfg['interface']}",
                ]
                with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE) as process:
                    threading.Thread(target=drain, args=(process.stderr,), daemon=True).start()
                    cli = Cli(process)
                    try:
                        joined = join_as_router(cli, cfg, dataset)
                        monitor(cli, process, relay, joined)
                    finally:
                        shutdown_openthread(cli, process)
                        relay.done.set()
                        relay.join(5)
                log("OpenThread stopped; radio channel closed.")
    finally:
        stop_slayer(slayer)


def main():
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if os.geteuid() != 0:
        raise SystemExit("macthreadrouter must run as root")
    os.umask(0o077)
    cfg = load_config()
    dataset = load_dataset(cfg)
    failures = read_failures()
    if failures:
        delay = min(30 * 2 ** (failures - 1), 1800)
        log(f"{failures} recent failure(s); waiting {delay} s before retrying.")
        if stop.wait(delay):
            return 0
    deadline = time.monotonic() + 120
    while not link_active(cfg["interface"]):
        if time.monotonic() > deadline:
            write_failures(failures + 1)
            log(f"{cfg['interface']} has no active link.")
            return 1
        if stop.wait(2):
            return 0
    log(f"Starting Thread router session on {cfg['interface']}.")
    try:
        run_session(cfg, dataset)
    except Exception as error:
        if stop.is_set():
            log(f"Stopped during shutdown ({error}).")
            return 0
        write_failures(failures + 1)
        log(f"Session ended: {error}")
        return 1
    log("Service stopped cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
