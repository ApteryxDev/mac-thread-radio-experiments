from contextlib import contextmanager
from collections import deque
import socket
import subprocess
import sys
import threading

sys.path.insert(0, "bridge")
from spinel_bridge import PROBE_PROPERTIES, serve
from threadradio import spinel, hdlc

replies = {}
for tid, (prop, name) in enumerate(PROBE_PROPERTIES, 1):
    header = bytes([0x80 | tid])
    key = spinel.encode_i(prop)
    reply = header + b"\x06" + key
    replies[header + b"\x02" + key] = reply
allowed = replies.copy()
pending = deque()

def write(frame):
    reply = replies[hdlc.decode(frame)]
    pending.append(hdlc.encode(b"\x8f\x06\x01\x02"))
    if reply[0] == 0x84:
        pending.append(hdlc.encode(b"\x84\x06\x00\x0d"))
    else:
        pending.append(hdlc.encode(reply + spinel.encode_i(257)))
    return True

@contextmanager
def radio():
    yield lambda timeout_ms: pending.popleft() if pending else None, write, hdlc.decode

with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    worker = threading.Thread(target=serve, args=(listener, radio, 5),
                              kwargs={"allowed": allowed})
    worker.start()
    result = subprocess.check_output([sys.executable, "bridge/spinel_bridge.py",
                                      "--probe", "--capabilities", "--port",
                                      str(listener.getsockname()[1])], text=True)
    worker.join(5)
    assert not worker.is_alive() and not allowed
    assert result.count("[257]") == 4 and "LAST_STATUS [13]" in result, result
    print(result)
