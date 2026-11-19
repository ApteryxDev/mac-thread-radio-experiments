import sys
sys.path.insert(0, "bridge")
import check_thread_attachment as checker

fields = {
    0: bytes.fromhex("000019"), 1: bytes.fromhex("1234"),
    2: bytes.fromhex("aabbccddeeff0011"), 3: b"test-only",
    4: bytes(range(16)), 5: bytes(range(16, 32)),
    7: bytes.fromhex("fd00010203040506"), 12: bytes.fromhex("02a0f7f8"),
    14: bytes.fromhex("0000000000010000"), 53: bytes.fromhex("000407fff800"),
}
def probe_discovery(cli, interface_index):
    cli.command("Enable TREL", b"trel enable")
    cli.command("Enable Ethernet discovery", f"mdns enable {interface_index}".encode())
    raise RuntimeError("Synthetic Ethernet discovery startup passed")

if "--trel" in sys.argv:
    checker.wait_for_trel_peer = probe_discovery
    checker.collect_trace = lambda pipe, counts: [print(line.decode(errors="replace").rstrip()) for line in pipe]

checker.load_dataset = lambda: b"".join(bytes([key, len(value)]) + value for key, value in fields.items())
attach = checker.attach
checker.attach = lambda cli, dataset, **kwargs: attach(cli, dataset, seconds=2, **kwargs)
try:
    checker.main()
except RuntimeError as error:
    if str(error) == "Synthetic Ethernet discovery startup passed":
        print(str(error))
        sys.exit(0)
    if str(error) != "The Mac did not attach as a child within 30 seconds":
        raise
    print("Synthetic test passed: dataset applied, attachment timed out, cleanup succeeded.")
