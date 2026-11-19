#!/usr/bin/env python3
"""Relay one Spinel HDLC TCP session to the Apple TSI radio."""
import argparse
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
import signal
import socket

FLAG = 0x7E
MAX_FRAME = 2048
IO_TIMEOUT = 0.05
PROBE_PROPERTIES = (
    (0x01, "Spinel protocol version"),
    (0x05, "Spinel capabilities"),
    (0xB0, "RCP API version"),
    (0xB1, "Minimum host RCP API version"),
    (0x120B, "Radio capabilities bitmask"),
)


# Exact startup GET sequence from OpenThread 806842f, with no reset.
OPENTHREAD_STARTUP_GETS = (
    (1, 0x01), (1, 0x02), (1, 0x05), (1, 0x08),
    (2, 0xB0), (3, 0xB1), (4, 0x120B),
)


# Normal initialization writes only radio-local configuration while Thread is down.
# Each entry contains the request prefix, length, and expected response header.
OPENTHREAD_INIT_WRITES = (
    ("86032001", 4, "860620"),
    ("870336ffff", 5, "870636"),
    ("8803350000", 5, "880635"),
    ("8a0334", 11, "8a0634"),
    ("8b0335feff", 5, "8b0635"),
    ("8c038426", 4, "8c068426"),
    ("8d038526", 4, "8d068526"),
    ("8e03801008011000", 60, "8e068010"),
)


def initialization_writes():
    return {(bytes.fromhex(prefix), length): bytes.fromhex(reply)
            for prefix, length, reply in OPENTHREAD_INIT_WRITES}


def extract_frames(pending: bytearray, chunk: bytes) -> Iterator[bytes]:
    """Split TCP bytes on HDLC flags, including shared delimiters."""
    for byte in chunk:
        if byte == FLAG:
            if len(pending) > 1:
                yield bytes(pending) + bytes([FLAG])
            pending[:] = bytes([FLAG])
        elif pending:
            if len(pending) >= MAX_FRAME - 1:
                raise ValueError("TCP HDLC frame exceeds the slot limit")
            pending.append(byte)


def serve(listener: socket.socket, open_radio: Callable[[], AbstractContextManager],
          timeout: float, *, allowed: dict[bytes, bytes] | None = None,
          writes: dict[tuple[bytes, int], bytes] | None = None) -> None:
    """Serve one client. Close the radio and listener on session failure."""
    listener.settimeout(timeout)
    client, _ = listener.accept()
    awaiting = {}
    with client, open_radio() as (read, write, decode):
        pending = bytearray()
        while True:
            client.settimeout(IO_TIMEOUT)
            try:
                chunk = client.recv(4096)
            except TimeoutError:
                chunk = None
            if chunk == b"":
                return
            if chunk:
                for frame in extract_frames(pending, chunk):
                    payload = decode(frame)
                    if payload is None or len(payload) < 2 or payload[0] & 0xC0 != 0x80:
                        raise ValueError("Invalid Spinel HDLC frame from client")
                    if allowed is not None:
                        reply = allowed.pop(payload, None)
                        if reply is None and writes:
                            for prefix, length in writes:
                                if len(payload) == length and payload.startswith(prefix):
                                    reply = writes.pop((prefix, length))
                                    break
                        if reply is None:
                            raise ValueError("Command is outside the startup allowlist")
                        awaiting[payload[0]] = reply
                    if not write(frame):
                        raise TimeoutError("TSI transmit slot timed out")
            incoming = read(timeout_ms=1 if chunk else 10)
            if incoming:
                if allowed is not None:
                    payload = decode(incoming) or b""
                    expected = awaiting.get(payload[0]) if payload else None
                    if expected is None or not payload.startswith(
                        (expected, expected[:1] + b"\x06\x00")
                    ):
                        continue
                    del awaiting[payload[0]]
                client.settimeout(1)
                client.sendall(incoming)


def packed_values(data, decode):
    values = []
    while data:
        value, size = decode(data)
        if size > 5 or data[size - 1] & 0x80 or value > 0xFFFFFFFF:
            raise ValueError("Malformed Spinel packed integer")
        values.append(value)
        data = data[size:]
    return values


def receive_frames(client, pending, deadline):
    from time import monotonic

    while (remaining := deadline - monotonic()) > 0:
        client.settimeout(remaining)
        try:
            chunk = client.recv(4096)
        except TimeoutError:
            return
        if not chunk:
            raise ConnectionError("Bridge disconnected during probe")
        yield from extract_frames(pending, chunk)


def main() -> None:
    from contextlib import contextmanager

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live-radio", action="store_true", help="Open the TSI radio")
    mode.add_argument("--probe", action="store_true", help="Query protocol version via TCP")
    session = parser.add_mutually_exclusive_group()
    session.add_argument("--capabilities", action="store_true",
                        help="Use the extended read-only probe in both terminals")
    session.add_argument("--openthread-check", action="store_true",
                         help="Allow only the pinned OpenThread startup GETs")
    session.add_argument("--openthread-init", action="store_true",
                         help="Allow the pinned OpenThread initialization sequence")
    session.add_argument("--full-session", action="store_true",
                        help="Allow all Spinel commands in a live session")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if (args.full_session or args.openthread_check or args.openthread_init) and not args.live_radio:
        parser.error("OpenThread and full-session modes require --live-radio")
    from threadradio import hdlc, open_channel, read_slot, spinel, write_slot

    properties = PROBE_PROPERTIES if args.capabilities else PROBE_PROPERTIES[:1]
    requests = {}
    for tid, (prop, name) in enumerate(properties, start=1):
        header = bytes([0x80 | tid])
        key = spinel.encode_i(prop)
        requests[header + b"\x02" + key] = (header + b"\x06" + key, name)

    @contextmanager
    def radio():
        with open_channel("tsi") as channel:
            raw_rx = 0
            invalid_rx = 0

            def read(timeout_ms):
                nonlocal raw_rx, invalid_rx
                slot = read_slot(channel, timeout_ms=timeout_ms)
                if slot and args.full_session:
                    payload = hdlc.decode(slot)
                    if payload is None:
                        invalid_rx += 1
                    elif (len(payload) >= 3 and payload[0] & 0xCF == 0x80
                          and payload[1:3] == b"\x06\x71"):
                        raw_rx += 1
                return slot

            def write(frame):
                return write_slot(channel, frame, timeout_ms=100)

            try:
                yield read, write, hdlc.decode
            finally:
                if args.full_session:
                    print(f"TSI raw receive frames: {raw_rx}", flush=True)
                    print(f"TSI invalid HDLC slots: {invalid_rx}", flush=True)

    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        if args.probe:
            from time import monotonic

            with socket.create_connection(("127.0.0.1", args.port), timeout=2) as client:
                pending = bytearray()
                for request, (reply, name) in requests.items():
                    client.sendall(hdlc.encode(request))
                    status = reply[:1] + b"\x06\x00"
                    deadline = monotonic() + 2
                    value = None
                    for frame in receive_frames(client, pending, deadline):
                        payload = hdlc.decode(frame) or b""
                        if payload.startswith(reply):
                            value = payload[len(reply):]
                            break
                        if payload.startswith(status):
                            codes = packed_values(payload[len(status):], spinel.decode_i)
                            print(f"{name}: LAST_STATUS {codes}")
                            break
                    else:
                        print(f"{name}: no reply")
                    if value is not None:
                        numbers = packed_values(value, spinel.decode_i)
                        if args.capabilities:
                            print(f"{name}: {numbers}")
                        else:
                            print("Received matching Spinel protocol-version response")
                    elif not args.capabilities:
                        raise RuntimeError("No matching Spinel protocol-version response")
            return
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", args.port))
            listener.listen(1)
            if args.full_session:
                print("TSI receive counters enabled for this session.", flush=True)
            print(f"Waiting for one local Spinel client on 127.0.0.1:{args.port}",
                  flush=True)
            allowed = None if args.full_session else {
                request: reply for request, (reply, _) in requests.items()
            }
            if args.openthread_check or args.openthread_init:
                gets = OPENTHREAD_STARTUP_GETS
                if args.openthread_init:
                    gets += ((5, 0x22), (9, 0x27))
                allowed = {
                    bytes([0x80 | tid, 2]) + spinel.encode_i(prop):
                    bytes([0x80 | tid, 6]) + spinel.encode_i(prop)
                    for tid, prop in gets
                }
            writes = initialization_writes() if args.openthread_init else None
            serve(listener, radio, timeout=30, allowed=allowed, writes=writes)
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"bridge: {error}\n")


if __name__ == "__main__":
    main()
