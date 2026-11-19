from collections import deque
from contextlib import contextmanager
import socket
import subprocess
import sys
import threading

sys.path.insert(0, "bridge")
from spinel_bridge import OPENTHREAD_STARTUP_GETS, serve
from threadradio import hdlc, spinel

encode = spinel.encode_i
properties = {
    1: bytes([4, 3]),
    2: b"Mock RCP\x00",
    5: b"".join(map(encode, [5, 12, 24, 34, 513, 64, 65, 518, 15361])),
    8: bytes.fromhex("0200000000000001"),
    0xB0: encode(11),
    0xB1: encode(4),
    0x120B: encode(127),
    0x22: bytes(range(11, 27)),
}
pending = deque()
commands = []

def write(frame):
    payload = hdlc.decode(frame)
    command, offset = spinel.decode_i(payload[1:])
    prop, _ = spinel.decode_i(payload[1 + offset:])
    commands.append((command, hex(prop)))
    print("request", payload.hex(), flush=True)
    if command == 2 and prop in properties:
        response = encode(prop) + properties[prop]
    else:
        response = b"\x00\x0d"
    pending.append(hdlc.encode(payload[:1] + b"\x06" + response))
    return True

@contextmanager
def radio():
    yield lambda timeout_ms: pending.popleft() if pending else None, write, hdlc.decode

with socket.socket() as listener:
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 8765))
    listener.listen(1)
    allowed = {
        bytes([0x80 | tid, 2]) + encode(prop): bytes([0x80 | tid, 6]) + encode(prop)
        for tid, prop in OPENTHREAD_STARTUP_GETS
    }
    worker = threading.Thread(target=serve, args=(listener, radio, 5),
                              kwargs={"allowed": allowed}, daemon=True)
    worker.start()
    result = subprocess.run([sys.executable, "bridge/check_openthread.py"], timeout=25)
    worker.join(3)
    print("Mock observed", commands)
    assert not allowed and len(commands) == 7
    sys.exit(result.returncode)
