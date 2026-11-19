"""Hardware-free checks for the boot service decision logic."""
import sys, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'service'))
import macthreadrouter as m
m.socket.if_nametoindex = lambda name: 16
cfg = {"interface": "en8", "extended_pan_id": bytes.fromhex("0000000000000000"), "network_name": b"ExampleNetwork"}

class FakeCli:
    def __init__(self, script):
        self.cmds = []; self.role = 'disabled'; self.script = script; self.n = 0; self.part = b'1234'; self.elig = False
    def command(self, label, c):
        self.cmds.append(c)
        if c == b'thread start': self.role = 'child'
        if c == b'state router': self.role = 'router'
        if c == b'routereligible disable': self.elig = False
        if c == b'state':
            self.n += 1
            r = self.script(self) if self.n > 3 else self.role
            self.role = r
            return [r.encode()]
        return {b'routereligible': [b'Disabled' if not self.elig else b'Enabled'],
                b'br state': [b'disabled'],
                b'trel peers list': [b'| 1 | ExtPanId:0000000000000000 | x |'],
                b'partitionid': [self.part],
                b'child list': [b'1'],
               }.get(c, [])

class P:
    returncode=None
    def poll(self): return None
class R:
    done = threading.Event(); error=None

m.stop.wait = lambda s=None: m.stop.is_set()
# 1. normal join -> router, monitor stops after a few iterations
cli = FakeCli(lambda c: 'router')
joined = m.join_as_router(cli, cfg, b'\x00\x03\x00\x00\x19')
assert joined == '1234'
assert cli.cmds.index(b'br disable') < cli.cmds.index(b'thread start') < cli.cmds.index(b'routereligible enable') < cli.cmds.index(b'state router')
count = {'n':0}
orig = m.role
def role_stop(c):
    count['n'] += 1
    if count['n'] > 3: m.stop.set()
    return orig(c)
m.role = role_stop
m.monitor(cli, P(), R(), joined)
m.role = orig; m.stop.clear()
print('join + monitor ok')

# 2. leader -> error
cli = FakeCli(lambda c: 'leader')
cli.n = 10
try:
    m.join_as_router(cli, cfg, b'x'); raise SystemExit('expected leader failure')
except RuntimeError as e: assert 'leader' in str(e), e
print('leader refusal ok')

# 3. partition change in monitor
cli = FakeCli(lambda c: 'router'); cli.role='router'
cli.part = b'999'
try:
    m.monitor(cli, P(), R(), '1234'); raise SystemExit('expected partition failure')
except RuntimeError as e: assert 'partition' in str(e)
print('partition guard ok')

# 4. TLV parse + dataset mismatch
raw = bytes([2,8]) + bytes.fromhex("0000000000000000") + bytes([3,14]) + b"ExampleNetwork"
assert m.parse_tlvs(raw)[3] == b"ExampleNetwork"
try: m.parse_tlvs(raw + b"\x01"); raise SystemExit('expected malformed')
except RuntimeError: pass
print('tlv ok')

# 5. extract frames
p = bytearray(); assert list(m.extract_frames(p, b"\x7e\x81\x02\x01\x7e\x82\x7e")) == [b"\x7e\x81\x02\x01\x7e", b"\x7e\x82\x7e"]
print('hdlc ok')
