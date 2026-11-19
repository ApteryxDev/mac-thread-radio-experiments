#!/usr/bin/env python3
"""Briefly try to attach to the verified Apple TV mesh as a Thread child."""
import argparse
from collections import Counter
from contextlib import suppress
import json
import os
from pathlib import Path
import resource
import re
import select
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep

from check_openthread import relay, root

# Your network's identity lives in bridge/network.json (ignored by Git).
# Copy network.example.json and fill in the values from your own dataset.
NETWORK_FILE = Path(__file__).with_name("network.json")


def _network_identity():
    try:
        data = json.loads(NETWORK_FILE.read_text())
    except OSError:
        data = {"network_name": "ExampleNetwork", "extended_pan_id": "0000000000000000"}
    return data["network_name"].encode(), bytes.fromhex(data["extended_pan_id"])


NETWORK_NAME, EXTENDED_PAN_ID = _network_identity()
DATASETS = root / "work/ha-thread-import/.storage/thread.datasets"
BINARY = root / "work/ot-attach-build/src/posix/ot-cli"
TREL_BINARY = root / "work/ot-trel-build/src/posix/ot-cli"


TRACE_STAGES = (
    "Parent requests queued",
    "Parent responses processed",
    "Parent candidates accepted",
    "Child ID requests queued",
    "Child ID request TX succeeded",
    "Child ID request TX failed",
    "Child ID responses processed",
    "Child role entered",
)


def collect_trace(pipe, counts):
    labels = {f"thread-attach: {stage}".encode(): stage for stage in TRACE_STAGES}
    for line in pipe:
        stage = labels.get(line.rstrip(b"\r\n"))
        if stage is not None:
            counts[stage] += 1


def load_dataset():
    try:
        records = json.loads(DATASETS.read_text())["data"]["datasets"]
        matches = []
        for record in records.values() if isinstance(records, dict) else records:
            raw = bytes.fromhex(record["tlv"])
            if not 1 <= len(raw) <= 254:
                continue
            fields = {}
            offset = 0
            while offset + 2 <= len(raw):
                field, size = raw[offset:offset + 2]
                end = offset + 2 + size
                if field in fields or end > len(raw):
                    break
                fields[field] = raw[offset + 2:end]
                offset = end
            required_sizes = {0: 3, 1: 2, 2: 8, 4: 16, 5: 16, 7: 8, 12: 4, 14: 8}
            if (offset == len(raw) and fields.get(2) == EXTENDED_PAN_ID
                    and fields.get(3) == NETWORK_NAME
                    and all(len(fields.get(k, b"")) == v for k, v in required_sizes.items())
                    and record["source"] == "iOS-app"):
                matches.append(raw)
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError("Cannot read the Home Assistant Thread dataset") from None
    if len(matches) != 1:
        raise RuntimeError("Expected exactly one verified Apple TV dataset")
    return matches[0]


class Cli:
    def __init__(self, process):
        self.process = process
        self.pending = bytearray()

    def read_line(self, deadline):
        fd = self.process.stdout.fileno()
        while b"\n" not in self.pending:
            remaining = deadline - monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                raise RuntimeError("OpenThread did not respond")
            data = os.read(fd, 4096)
            if not data:
                raise RuntimeError("OpenThread disconnected")
            self.pending.extend(data)
            if len(self.pending) > 8192:
                raise RuntimeError("Unexpected OpenThread output")
        index = self.pending.index(b"\n")
        line = bytes(self.pending[:index]).strip().removeprefix(b"> ")
        del self.pending[:index + 1]
        return line

    def command(self, label, command):
        try:
            self.process.stdin.write(command + b"\n")
            self.process.stdin.flush()
            deadline = monotonic() + 4
            result = []
            while (line := self.read_line(deadline)) != b"Done":
                if line.startswith(b"Error"):
                    match = re.fullmatch(rb"Error (\d+): ([A-Za-z]+)", line)
                    reason = match.group(0).decode() if match else "command rejected"
                    raise RuntimeError(reason)
                result.append(line)
            return result
        except (OSError, RuntimeError) as error:
            raise RuntimeError(f"{label}: {error}") from None


def show_radio_counters(cli):
    allowed = {
        b"TxTotal", b"TxAckRequested", b"TxAcked", b"TxErrCca", b"TxErrAbort",
        b"RxTotal", b"RxErrSec", b"RxDestAddrFiltered",
    }
    for line in cli.command("Read radio counters", b"counters mac"):
        key, _, value = line.partition(b":")
        if key in allowed and value.strip().isdigit():
            print(f"{key.decode()}: {int(value)}")


def wait_for_trel_peer(cli, interface_index):
    cli.command("Enable TREL", b"trel enable")
    cli.command("Enable Ethernet discovery", f"mdns enable {interface_index}".encode())
    marker = b"ExtPanId:" + EXTENDED_PAN_ID.hex().encode()
    deadline = monotonic() + 10
    while monotonic() < deadline:
        peers = cli.command("Check TREL peer discovery", b"trel peers list")
        if any(marker in peer for peer in peers):
            print("TREL peer on the Apple TV network: discovered")
            return
        sleep(0.5)
    raise RuntimeError("OpenThread did not discover a TREL peer on the Apple TV network")


def show_trel_counters(cli):
    for line in cli.command("Read TREL counters", b"trel counters"):
        match = re.match(rb"(Inbound|Outbound):\s+Packets (\d+)", line)
        if match:
            direction, count = match.groups()
            print(f"TREL {direction.decode().lower()} packets: {int(count)}")


def attach(cli, dataset, seconds=30, trel_index=None):
    cli.command("Disable router eligibility", b"routereligible disable")
    if b"Disabled" not in cli.command("Verify router eligibility", b"routereligible"):
        raise RuntimeError("Could not verify that router eligibility is off")
    cli.command("Load Apple TV dataset", b"dataset set active " + dataset.hex().encode("ascii"))
    try:
        if trel_index is not None:
            wait_for_trel_peer(cli, trel_index)
        cli.command("Enable Thread interface", b"ifconfig up")
        cli.command("Start Thread child attachment", b"thread start")
        deadline = monotonic() + seconds
        while monotonic() < deadline:
            if b"child" in cli.command("Check Thread role", b"state"):
                if trel_index is not None:
                    show_trel_counters(cli)
                return True
            sleep(1)
        try:
            show_radio_counters(cli)
            if trel_index is not None:
                show_trel_counters(cli)
        except RuntimeError:
            print("Radio counters were unavailable.")
        return False
    finally:
        active_error = sys.exception()
        commands = [("Stop Thread", b"thread stop"), ("Bring interface down", b"ifconfig down")]
        if trel_index is not None:
            commands.extend([
                ("Disable TREL", b"trel disable"),
                ("Disable Ethernet discovery", b"mdns disable"),
            ])
        failures = []
        for label, command in commands:
            try:
                cli.command(label, command)
            except RuntimeError as error:
                failures.append(str(error))
        if failures and active_error is None:
            raise RuntimeError(failures[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true",
                        help="Confirm the build and dataset are available without opening the radio")
    parser.add_argument("--trel", choices=["en8"],
                        help="Enable Thread over Infrastructure on the active USB Ethernet port")
    args = parser.parse_args()
    selected_binary = TREL_BINARY if args.trel else BINARY
    if not selected_binary.is_file():
        raise RuntimeError("Attachment build is missing")
    dataset = load_dataset()
    trel_index = None
    if args.trel:
        status = subprocess.run(["/sbin/ifconfig", args.trel], capture_output=True, check=False)
        if status.returncode or b"status: active" not in status.stdout:
            raise RuntimeError("The USB Ethernet link is inactive")
        trel_index = socket.if_nametoindex(args.trel)
    if args.preflight:
        print("Verified Apple TV dataset and RAM-only OpenThread build are available.")
        return
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with TemporaryDirectory(prefix="ot-child-", dir="/tmp") as directory:
        unix_path = str(Path(directory) / "radio.sock")
        errors = []
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(unix_path)
            listener.listen(1)
            worker = Thread(target=relay, args=(listener, errors, 55), daemon=True)
            worker.start()
            command = [
                str(selected_binary), "-d", "0", "--data-path", directory,
                f"spinel+hdlc+unix://{unix_path}?no-reset",
            ]
            if args.trel:
                command.append(f"trel://{args.trel}")
            counts = Counter()
            with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) as process:
                trace = Thread(target=collect_trace, args=(process.stderr, counts), daemon=True)
                trace.start()
                try:
                    attached = attach(Cli(process), dataset, trel_index=trel_index)
                finally:
                    with suppress(BrokenPipeError):
                        process.stdin.close()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    trace.join(1)
                    if counts:
                        print("Thread attachment stages:")
                        for stage in TRACE_STAGES:
                            print(f"  {stage}: {counts[stage]}")
            worker.join(1)
            if errors:
                raise RuntimeError("Radio relay disconnected during the attachment test")
    if not attached:
        raise RuntimeError("The Mac did not attach as a child within 30 seconds")
    print("The Mac attached to the Apple TV mesh as a Thread child and then detached.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
