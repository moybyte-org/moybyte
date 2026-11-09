"""The radio link as a device by handle (docs/netplay_v1.md, Ports): a process
opens `net` on a port and hears only that port's messages, as whole records
carrying their sender, beside the game in front's `net.send`/`on_net` (port 0)
and its lockstep match, which no port reaches. Two consoles share the fake air
of tests/test_espnow_link.py; the rows are runtime/system/dev_handles.py's,
called as a process's door serves them. The compiled tiers' rows are
tests/test_wasm_app.py's, tests/test_wasi_services.py's and
tests/test_elf_commands.py's."""

import pytest

from runtime import dev_handles as H
from runtime import devices, moybyte
from runtime import moy_log as L
from runtime import players as P
from test_espnow_link import _Air, _Clock, _FakeRadio, _FakeWlan, _Ws

A_MAC, B_MAC, C_MAC = b"\x01" * 6, b"\x02" * 6, b"\x03" * 6


class _Console:
    """The slice of the Workstation a `net` handle touches: its link and the
    radio's lease."""

    def __init__(self, link):
        self.link = link
        self.holds = set()

    def wifi_hold(self, tag, pid=None):
        self.holds.add((tag, pid))
        return True

    def wifi_release(self, tag, pid=None):
        self.holds.discard((tag, pid))


def _make(air, clock, mac, name):
    radio, wlan = _FakeRadio(air, mac), _FakeWlan(mac)
    radio.wlan = wlan
    return P.EspNowLink(board="tdeck", name=name, ticks_ms=clock, radio=radio, wlan=wlan)


@pytest.fixture
def two():
    """Console A (whose link the handles open: `devices.console`) and B, on
    one air, neither radio up."""
    air, clock = _Air(), _Clock()
    a, b = _make(air, clock, A_MAC, "Ada"), _make(air, clock, B_MAC, "Bo")
    was = devices.console
    devices.console = _Console(a)
    yield air, clock, a, b
    for pid in list(H.HANDLES):
        moybyte.end(pid)
    devices.console = was
    devices.grants = None


def _as(pid, fn):
    was = L.current()
    L.current(pid)
    try:
        return fn()
    finally:
        L.current(was)


def _open(pid, cfg):
    return _as(pid, lambda: H.open(pid, "net", cfg))


def _records(b):
    out = []
    while b:
        n = int.from_bytes(b[8:10], "little")
        out.append((bytes(b[:6]), int.from_bytes(b[6:8], "little"), bytes(b[10:10 + n])))
        b = b[10 + n:]
    return out


def _hear(link):
    """The frame tail's poll, as the kernel's loop runs it on a board."""
    link.poll()


def test_two_processes_on_two_ports_each_hear_only_their_own(two):
    _air, _clock, a, b = two
    h7, h9 = _open(41, "port=7"), _open(42, "port=9")
    assert h7 > 0 and h9 > 0
    assert a.active and devices.console.holds == {("net", 41), ("net", 42)}
    assert b.open_port(7) == 0 and b.open_port(9) == 0
    assert b.port_send(7, b"seven") == 0 and b.port_send(9, b"nine") == 0
    assert b.port_send(8, b"nobody's") == 0
    _hear(a)
    assert _records(H.read(41, h7, 512)) == [(B_MAC, 7, b"seven")]
    assert _records(H.read(42, h9, 512)) == [(B_MAC, 9, b"nine")]
    assert H.read(41, h7, 512) == b"" and H.read(42, h9, 512) == b""
    assert H.read(42, h7, 512) == H.ABSENT, "a handle is its process's"
    assert H.write(41, h7, b"back") == 4
    _hear(b)
    assert b.port_take(7) == (A_MAC, b"back") and b.port_take(9) is None
    assert H.get(41, h7, "port") == 7 and H.get(41, h7, "peers") == 0


def test_a_port_is_one_opener_s_and_port_0_is_the_game_s(two):
    assert _open(41, "port=7") > 0
    assert _open(42, "port=7") == H.BUSY
    assert _open(41, "port=7") == H.BUSY
    assert _open(41, "port=0") == H.BAD
    assert _open(41, "port=65536") == H.BAD
    assert _open(41, "") == H.BAD, "the port is required"
    assert _open(41, "port=8 from=everyone") == H.BAD
    assert _open(41, "port=8 baud=9") == H.BAD


def test_a_net_handle_needs_multiplayer_and_a_link(two):
    devices.grants = lambda pid: ("pins",) if pid == 41 else ("multiplayer",)
    assert _open(41, "port=7") == H.DENIED
    assert _open(42, "port=7") > 0
    devices.console.link = None
    assert _open(42, "port=8") == H.ABSENT


def test_a_game_s_on_net_works_beside_a_service_s_open_port(two):
    """Port 0 is the game's: B's net.send reaches A's on_net and no port, and
    B's port message reaches A's handle and never on_net."""
    _air, clock, a, b = two
    h = _open(41, "port=5")
    a.start()
    b.start()
    a.announce("Brick Siege", 1)
    b.announce("Brick Siege", 1)
    clock.advance(P.BEACON_MS + 1)
    a.poll()
    b.poll()
    got = []
    a.net.on_message(got.append)
    assert b.net.send({"hi": 1}) is True
    assert b.open_port(5) == 0 and b.port_send(5, b"svc") == 0
    _hear(a)
    a.net.pump()
    assert got == [{"hi": 1}]
    assert _records(H.read(41, h, 512)) == [(B_MAC, 5, b"svc")]
    assert H.read(41, h, 512) == b""


def test_port_0_is_the_wire_net_send_always_spoke():
    """net.send is T_MSG with no port byte, as it was before ports: a console
    without ports hears it and a console with them sends it."""
    air, clock = _Air(), _Clock()
    a = _make(air, clock, A_MAC, "Ada")
    a.start()
    sent = []
    a.radio.send = lambda mac, payload, sync=False: sent.append(bytes(payload)) or True
    a.net.send({"x": 1})
    assert sent == [bytes([P.PROTO, P.T_MSG]) + b'{"x": 1}']
    assert a.open_port(3) == 0 and a.port_send(3, b"hey") == 0
    assert sent[1] == bytes([P.PROTO, P.T_PORT, 3, 0]) + b"hey"
    assert a.port_send(0, b"x") == P.PORT_BAD, "port 0 is net.send's"


def test_a_full_queue_drops_the_newest_and_a_short_read_is_full(two):
    _air, _clock, a, b = two
    h = _open(41, "port=7")
    b.start()
    for i in range(6):
        assert b.port_send(7, bytes([65 + i]) * 20) == 0
    _hear(a)
    assert H.get(41, h, "drops") == 2
    assert H.read(41, h, 29) == H.FULL, "a record is 10 + its bytes, whole"
    one = H.read(41, h, 30)
    assert _records(one) == [(B_MAC, 7, b"A" * 20)]
    assert [r[2][:1] for r in _records(H.read(41, h, 512))] == [b"B", b"C", b"D"]
    assert H.read(41, h, 1) == b"", "nothing waits: no FULL"
    assert H.write(41, h, b"x" * 241) == H.BAD
    assert H.write(41, h, b"x" * 240) == 240


def test_a_process_holds_four_ports_and_the_console_eight(two):
    for port in range(1, H.NET_PORTS + 1):
        assert _open(41, "port=%d" % port) > 0
    assert _open(41, "port=99") == H.FULL
    for port in range(10, 14):
        assert _open(42, "port=%d" % port) > 0
    assert _open(43, "port=20") == H.FULL, "MOY_MATCH_PORTS"


def test_the_end_sweep_closes_its_ports_and_lets_the_radio_go(two):
    _air, _clock, a, b = two
    h = _open(41, "port=7")
    assert _open(41, "port=8") > 0
    assert _open(42, "port=9") > 0
    b.start()
    b.port_send(7, b"left")
    _hear(a)
    moybyte.end(41)
    assert H.HANDLES.get(41) is None and a.ports_open() == 1
    assert devices.console.holds == {("net", 42)}
    assert H.read(41, h, 512) == H.ABSENT
    assert a.open_port(7) == 0 and a.port_take(7) is None, "what waited went with it"
    a.close_port(7)
    assert a.active, "process 42's port holds the radio"
    moybyte.end(42)
    assert not a.active and devices.console.holds == set()


def test_a_game_s_stop_leaves_a_port_s_radio_up_and_its_close_takes_it_down(two):
    _air, _clock, a, _b = two
    a.start()
    a.announce("Brick Siege", 1)
    h = _open(41, "port=7")
    a.stop()
    assert a.active and a.cart == "" and a.state == 0, "up, at the launcher's beacon"
    assert H.close(41, h) == 0
    assert not a.active


def test_a_from_match_port_hears_only_the_match_s_peer(two):
    """from=match: the live match's peer, nobody outside a match; from=any
    hears every console in range, the sender in its record."""
    air, clock, a, b = two
    c = _make(air, clock, C_MAC, "Cy")
    any_h, match_h = _open(41, "port=7"), _open(41, "port=8 from=match")
    for lk in (b, c):
        lk.start()
    b.port_send(8, b"no match yet")
    _hear(a)
    assert H.read(41, match_h, 512) == b""
    a.start()
    for lk in (a, b):
        lk.announce("Brick Siege", 0)
    clock.advance(10)
    a.poll()
    b.poll()
    a.state = b.state = 1
    wsa, wsb = _Ws(), _Ws()
    a.offer(wsa, "Brick Siege")
    b.poll(wsb)
    assert wsa.netplay is not None
    b.port_send(8, b"peer")
    c.port_send(8, b"stranger")
    b.port_send(7, b"b")
    c.port_send(7, b"c")
    _hear(a)
    assert _records(H.read(41, match_h, 512)) == [(B_MAC, 8, b"peer")]
    assert _records(H.read(41, any_h, 512)) == [(B_MAC, 7, b"b"), (C_MAC, 7, b"c")]


def test_no_port_reaches_the_match_s_inputs(two):
    """A frame on a port is never the session's: a T_PORT whose bytes read as
    an input packet is queued on its port and the match's input count stays."""
    air, clock, a, b = two
    a.start()
    b.start()
    for lk in (a, b):
        lk.announce("Brick Siege", 0)
    clock.advance(10)
    a.poll()
    b.poll()
    a.state = b.state = 1
    wsa, wsb = _Ws(), _Ws()
    a.offer(wsa, "Brick Siege")
    b.poll(wsb)
    s = wsa.netplay
    h = _open(41, "port=%d" % (P.T_INPUT | (s.session << 8)))
    before = s.packets_in
    body = bytes([9, 0, 0xFF, 0] + [0x3F] * 10 + [255, 255, 0, 0, 0, 0, 0])
    b.port_send(P.T_INPUT | (s.session << 8), body)
    a.drain_input()
    _hear(a)
    assert s.packets_in == before
    assert _records(H.read(41, h, 512)) == [(B_MAC, P.T_INPUT | (s.session << 8), body)]
