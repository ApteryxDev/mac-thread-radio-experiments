from collections import deque
from contextlib import contextmanager
from pathlib import Path
import queue
import socket
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from spinel_bridge import extract_frames, serve
from threadradio import hdlc


class BridgeTest(unittest.TestCase):
    def test_framing(self):
        request = hdlc.encode(b"\x81\x02\x7e\x7d")
        pending = bytearray()
        self.assertEqual(list(extract_frames(pending, b"garbage" + request[:3])), [])
        self.assertEqual(list(extract_frames(pending, request[3:] + request[1:])),
                         [request, request])
        with self.assertRaises(ValueError):
            list(extract_frames(pending, b"x" * 2048))

    def test_loopback(self):
        request = hdlc.encode(b"\x81\x02\x01")
        reply = hdlc.encode(b"\x81\x06\x01\x04\x03")
        seen, responses, errors = [], deque(), queue.Queue()
        closed = threading.Event()

        @contextmanager
        def mock():
            def write(frame):
                seen.append(frame)
                responses.append(reply)
                return True

            def read(timeout_ms):
                return responses.popleft() if responses else None

            try:
                yield read, write, hdlc.decode
            finally:
                closed.set()

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)

            def run():
                try:
                    serve(listener, mock, timeout=2)
                except Exception as error:
                    errors.put(error)

            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            with socket.create_connection(listener.getsockname(), timeout=2) as client:
                client.sendall(request[:3])
                client.sendall(request[3:] + request[1:])
                with client.makefile("rb") as stream:
                    self.assertEqual(stream.read(2 * len(reply)), reply * 2)
                client.sendall(b"\x7e\x81\x02\x01\x00\x00\x7e")
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(closed.is_set())
        self.assertEqual(seen, [request, request])
        self.assertIsInstance(errors.get_nowait(), ValueError)


unittest.main(verbosity=2)
