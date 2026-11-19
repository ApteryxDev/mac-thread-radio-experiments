#!/usr/bin/env python3
"""Run OpenThread's dry-run compatibility check through the local TCP bridge."""
import argparse
from pathlib import Path
import re
import select
import socket
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread

root = Path(__file__).resolve().parents[1]
binary = root / "work/ot-build/src/posix/ot-cli"


def relay(listener, errors, idle_timeout=20):
    try:
        listener.settimeout(5)
        local, _ = listener.accept()
        with local, socket.create_connection(("127.0.0.1", 8765), timeout=3) as radio:
            destinations = {local: radio, radio: local}
            local.settimeout(3)
            while ready := select.select(list(destinations), [], [], idle_timeout)[0]:
                for source in ready:
                    data = source.recv(4096)
                    if not data:
                        return
                    destinations[source].sendall(data)
    except OSError as error:
        errors.append(str(error))


def check_ethernet(interface):
    if not re.fullmatch(r"en[0-9]+", interface):
        raise SystemExit("Specify a macOS Ethernet interface, for example en8")
    status = subprocess.run(
        ["/sbin/ifconfig", interface], capture_output=True, text=True, check=False,
    )
    if status.returncode != 0 or "status: active" not in status.stdout:
        raise SystemExit(f"{interface} has no active link")
    try:
        result = subprocess.run(
            ["/usr/bin/sudo", "-n", "/usr/bin/true"], check=False,
        )
    except PermissionError:
        raise SystemExit("Run the Ethernet check from your Mac's Terminal") from None
    if result.returncode != 0:
        raise SystemExit("Run sudo -v in this terminal before starting the Ethernet check")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize", action="store_true",
                        help="Initialize OpenThread and verify its disabled role")
    parser.add_argument("--ethernet", metavar="INTERFACE",
                        help="Open the Thread tunnel and specified Ethernet interface")
    args = parser.parse_args()
    selected_binary = binary
    if args.ethernet:
        check_ethernet(args.ethernet)
        selected_binary = root / "work/ot-net-build/src/posix/ot-cli"
    if not selected_binary.is_file():
        raise SystemExit(f"OpenThread build is missing: {selected_binary}")
    initialize = args.initialize or args.ethernet
    cli_input = "state\nifconfig\n" if initialize else ""
    if args.ethernet:
        cli_input += "netif\n"
    # Short path for the macOS Unix socket address limit.
    with TemporaryDirectory(prefix="ot-init-", dir="/tmp") as settings:
        path = str(Path(settings) / "radio.sock")
        errors = []
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(path)
            listener.listen(1)
            worker = Thread(target=relay, args=(listener, errors), daemon=True)
            worker.start()
            command = [str(selected_binary), "-v", "-d", "5", "--data-path", settings,
                       f"spinel+hdlc+unix://{path}?no-reset"]
            if not initialize:
                command.insert(1, "--dry-run")
            if args.ethernet:
                command[1:1] = ["-B", args.ethernet]
                command[:0] = ["/usr/bin/sudo", "-n"]
            try:
                result = subprocess.run(
                    command, input=cli_input,
                    capture_output=True, text=True, timeout=20, check=False,
                )
            except subprocess.TimeoutExpired:
                raise SystemExit("OpenThread initialization timed out") from None
            worker.join(1)
        print(result.stdout, end="")
        print(result.stderr, end="")
        for error in errors:
            print(f"Adapter: {error}")
        if result.returncode != 0 or errors:
            raise SystemExit(f"OpenThread initialization failed (exit {result.returncode})")
    if initialize:
        lines = {line.strip().removeprefix("> ") for line in result.stdout.splitlines()}
        if not {"disabled", "down"}.issubset(lines):
            raise SystemExit("Could not verify Thread role and interface state")
        print("OpenThread initialized with Thread disabled and its interface down.")
        if args.ethernet:
            tunnel = next((line for line in lines if re.fullmatch(r"utun[0-9]+:[1-9][0-9]*", line)), None)
            if tunnel is None:
                raise SystemExit("Could not verify the macOS Thread tunnel")
            print(f"Ethernet setup passed on {args.ethernet}; Thread tunnel {tunnel.split(':')[0]} was created.")
    else:
        print("OpenThread dry run passed with Thread left down.")


if __name__ == "__main__":
    main()
