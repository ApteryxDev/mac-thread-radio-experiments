import sys
sys.path.insert(0, "bridge")
import check_thread_border_routing as checker

class Cli:
    def __init__(self):
        self.commands = []
        self.role = b'child'

    def command(self, label, command):
        self.commands.append(command)
        if command == b'state router':
            self.role = b'router'
        return {
            b'routereligible': [b'Disabled'],
            b'trel peers list': [b'001 ExtPanId:0000000000000000'],
            b'state': [self.role],
            b'partitionid': [b'1234'],
            b'br infraif': [b'if-index:16, is-running:yes'],
            b'br state': [b'running'],
            b'child list': [b'1 2'],
            b'child table': [
                b'| ID  | RLOC16 | Timeout    | Age        | LQ In | C_VN |R|D|N|Ver|CSL|QMsgCnt|Suprvsn| Extended MAC     |',
                b'+-----+--------+------------+------------+-------+------+-+-+-+---+---+-------+-------+------------------+',
                b'|   1 | 0xc801 |        240 |         24 |     3 |  131 |1|0|0|  3| 0 |     0 |   129 | 4ecede68435358ac |',
                b'|   2 | 0xc802 |        240 |          2 |     3 |  131 |0|0|0|  4| 1 |     0 |   129 | a1b2c3d4e5f60718 |',
            ],
            b'counters br': [b'Inbound Unicast: Packets 2 Bytes 48',
                            b'Outbound Unicast: Packets 3 Bytes 72', b'RA TxSuccess: 1'],
            b'trel counters': [b'Inbound: Packets 4 Bytes 96', b'Outbound: Packets 5 Bytes 120 Failures 0'],
        }.get(command, [])

value = '0'
writes = []
def sysctl(flag, key):
    global value
    if flag == '-w':
        value = key.split('=')[1]
        writes.append(value)
    return value

checker.sysctl = sysctl
checker.sleep = lambda seconds: None
cli = Cli()
checker.run_trial(cli, b'mock', 16, hold_seconds=0)
assert not checker.cleanup(cli)
assert writes == ['1', '0']
assert cli.commands.index(b'state router') < cli.commands.index(b'br enable')
assert cli.commands.index(b'br enable') < cli.commands.index(b'thread stop')
print('Mock verified promotion, border routing, and IPv6 forwarding restoration.')

table = checker.child_table(Cli())
assert [c['Extended MAC'] for c in table] == ['4ecede68435358ac', 'a1b2c3d4e5f60718']
assert table[0]['RLOC16'] == '0xc801' and table[0]['LQ In'] == '3'
class EmptyCli(Cli):
    def command(self, label, command):
        if command == b'child table':
            return [b'| ID  | RLOC16 | Timeout    | Age        | LQ In | C_VN |R|D|N|Ver|CSL|QMsgCnt|Suprvsn| Extended MAC     |',
                    b'+-----+--------+------------+------------+-------+------+-+-+-+---+---+-------+-------+------------------+']
        return super().command(label, command)
assert checker.child_table(EmptyCli()) == []
router = Cli()
router.role = b'router'
checker.monitor_routing(router, [b'1234'], 0.01)
print('Mock verified child table parsing.')

cli = Cli()
command = cli.command
def failing_command(label, data):
    if data == b'br state':
        raise RuntimeError('synthetic failure')
    return command(label, data)
cli.command = failing_command
try:
    checker.run_trial(cli, b'mock', 16, hold_seconds=0)
except RuntimeError as error:
    assert str(error) == 'synthetic failure'
else:
    raise AssertionError('expected failure')
assert cli.commands[-1] == b'br disable'
assert writes[-2:] == ['1', '0'] and value == '0'
print('Mock verified route withdrawal and forwarding restoration after failure.')
