"""`tools/board.py` with no board attached.

The console on the far side is `runtime/dev_channel.py`'s real `DevChannel`
over a fake workstation, behind a fake serial port whose clock advances as the
driver reads it -- so the verbs, the replies and the `py` evaluation are the
channel's own, and a ten-second measurement takes no time. What this cannot
prove is the device half of a shot or a pmem read against real MicroPython
buffers; that is a board's to show (the list in the tool's commit).

Port resolution runs against a synthetic port table and the tree's real
board.toml files, so a board added to the tree is resolved here too.
"""

import argparse
import contextlib
import io
import json
import os
import sys
import types
import zlib
from array import array

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "device"))

import board                                                    # noqa: E402
import p4_autotest                                              # noqa: E402
from runtime.dev_channel import DevChannel                      # noqa: E402
from runtime.moy_loop import perf_format as format_perf          # noqa: E402

DIRS = board.boards()
TDECK = DIRS["tdeck"]
P4 = DIRS["p4"]


# -- the port table -----------------------------------------------------------

S3_USB = "303a:1001"
CH343 = "1a86:55d3"
ZERO_SERIAL = p4_autotest.declared_serial(DIRS["xiao_zero"])["serial_number"]
TABLE = [
    ("/dev/ttyACM0", S3_USB, ZERO_SERIAL),
    ("/dev/ttyACM1", S3_USB, "E8:F6:0A:E6:F3:08"),
    ("/dev/ttyACM2", S3_USB, "E0:72:A1:CC:E7:08"),
    ("/dev/ttyACM3", S3_USB, "DC:B4:D9:04:44:EC"),
    ("/dev/ttyACM4", CH343, "5B3E088359"),
]


def test_every_console_board_is_a_choice():
    assert {"p4", "tdeck", "guition_s3", "guition_p4", "xiao_zero"} <= set(DIRS)


def test_the_only_ch343_and_the_zeros_serial_need_no_open():
    """Two boards are settled by data alone: the Waveshare P4 is the only
    board declaring its usb id, and the Zero's board.toml names its serial."""
    claimed = board.claim(TABLE, DIRS, known={})
    assert claimed["/dev/ttyACM4"][0] == "p4"
    assert claimed["/dev/ttyACM0"][0] == "xiao_zero"
    assert board.resolve("p4", DIRS, TABLE, known={})[0] == "/dev/ttyACM4"


def test_a_shared_usb_id_is_never_guessed():
    """Three boards share 303a:1001 with no serial on file. A guess would
    open the wrong board with the right line state, so the answer is the
    candidates and how to settle them."""
    with pytest.raises(board.BoardError) as exc:
        board.resolve("tdeck", DIRS, TABLE, known={})
    msg = str(exc.value)
    for port in ("/dev/ttyACM1", "/dev/ttyACM2", "/dev/ttyACM3"):
        assert port in msg
    assert "/dev/ttyACM0" not in msg, "the Zero is claimed by its serial"
    assert "--port" in msg and "ports --probe" in msg


def test_a_learned_serial_settles_it_whatever_the_number():
    known = {"E0:72:A1:CC:E7:08": "tdeck"}
    assert board.resolve("tdeck", DIRS, TABLE, known=known) == (
        "/dev/ttyACM2", "learned serial")
    moved = [(p.replace("ACM2", "ACM7"), u, s) for p, u, s in TABLE]
    assert board.resolve("tdeck", DIRS, moved, known=known)[0] == "/dev/ttyACM7"


def test_an_unplugged_board_says_so():
    table = [t for t in TABLE if t[1] != CH343]
    with pytest.raises(board.BoardError, match="plugged in"):
        board.resolve("p4", DIRS, table, known={})


def test_learning_round_trips(tmp_path, monkeypatch):
    path = tmp_path / "cfg" / "boards.json"
    monkeypatch.setenv("MOYBYTE_BOARDS_FILE", str(path))
    assert board.load_identities() == {}
    board.learn("E0:72:A1:CC:E7:08", "tdeck")
    board.learn("DC:B4:D9:04:44:EC", "guition_s3")
    board.learn(None, "p4")
    assert json.loads(path.read_text()) == {
        "E0:72:A1:CC:E7:08": "tdeck", "DC:B4:D9:04:44:EC": "guition_s3"}
    assert os.listdir(path.parent) == ["boards.json"], "no temp file left"


def test_remember_writes_without_opening_anything(tmp_path, monkeypatch):
    monkeypatch.setenv("MOYBYTE_BOARDS_FILE", str(tmp_path / "b.json"))
    monkeypatch.setattr(board, "port_table", lambda *a, **k: TABLE)
    monkeypatch.setattr(board, "holders", lambda port: [])

    def no_open(*a, **k):
        raise AssertionError("ports opened a port")

    monkeypatch.setattr(board, "P4Board", no_open)
    monkeypatch.setattr(p4_autotest, "P4Board", no_open)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert board.main(["ports", "--remember",
                           "DC:B4:D9:04:44:EC=guition_s3"]) == 0
    line = [ln for ln in out.getvalue().splitlines() if "ttyACM3" in ln][0]
    assert "guition_s3" in line and "learned serial" in line


def test_probe_asks_each_unclaimed_port_once(tmp_path, monkeypatch):
    """`ports --probe` opens only what no fact settles, and only where an
    open is side-effect free: an unheld 303a:1001 port no serial names. The
    CH343 and the Zero are claimed by data and never opened, a held port is
    somebody's session, and every answer is learned."""
    monkeypatch.setenv("MOYBYTE_BOARDS_FILE", str(tmp_path / "b.json"))
    answers = {"/dev/ttyACM1": "guition_p4", "/dev/ttyACM2": "tdeck"}
    asked = []
    monkeypatch.setattr(p4_autotest, "_probe_identity", lambda port, d, log: (
        asked.append(port) or answers.get(port)))
    monkeypatch.setattr(board, "holders", lambda port: (
        [(1, "pytest")] if port == "/dev/ttyACM3" else []))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        board._probe(TABLE, DIRS, board.load_identities())
    assert asked == ["/dev/ttyACM1", "/dev/ttyACM2"]
    assert board.load_identities() == {"E8:F6:0A:E6:F3:08": "guition_p4",
                                       "E0:72:A1:CC:E7:08": "tdeck"}


def test_usb_id_and_serial_come_from_sysfs(tmp_path):
    """The facts resolution runs on are read from the USB device node above
    the tty's interface -- no open, no udevadm."""
    dev = tmp_path / "devices" / "usb1" / "1-2"
    iface = dev / "1-2:1.0"
    iface.mkdir(parents=True)
    (dev / "idVendor").write_text("303A\n")
    (dev / "idProduct").write_text("1001\n")
    (dev / "serial").write_text("E0:72:A1:CC:E7:08\n")
    tty = tmp_path / "class" / "tty" / "ttyACM2"
    tty.mkdir(parents=True)
    os.symlink(str(iface), str(tty / "device"))
    sys_tty = str(tmp_path / "class" / "tty")
    assert p4_autotest.usb_id_of("/dev/ttyACM2", sys_tty) == "303a:1001"
    assert p4_autotest.usb_serial_of("/dev/ttyACM2", sys_tty) == (
        "E0:72:A1:CC:E7:08")
    assert p4_autotest.usb_id_of("/dev/ttyACM9", sys_tty) is None
    assert p4_autotest.usb_serial_of("/dev/ttyACM9", sys_tty) is None


def test_holders_names_the_process(tmp_path):
    proc = tmp_path / "proc"
    tty = tmp_path / "ttyACM2"
    tty.write_text("")
    (proc / "4242" / "fd").mkdir(parents=True)
    os.symlink(str(tty), str(proc / "4242" / "fd" / "5"))
    (proc / "4242" / "cmdline").write_bytes(b"python\0-m\0pytest\0suite.py\0")
    (proc / "77" / "fd").mkdir(parents=True)
    os.symlink("/dev/null", str(proc / "77" / "fd" / "0"))
    (proc / "self").mkdir()
    assert board.holders(str(tty), proc=str(proc)) == [
        (4242, "python -m pytest suite.py")]


def test_a_held_port_is_refused(monkeypatch):
    monkeypatch.setattr(board, "holders",
                        lambda port: [(99, "python -m pytest tests/x.py")])
    with pytest.raises(board.BoardError) as exc:
        board.refuse_if_held("/dev/ttyACM2")
    assert "pid 99" in str(exc.value) and "--force" in str(exc.value)


def test_the_driver_takes_the_port_exclusively():
    """The lock is what makes a second open fail instead of splitting the
    replies between two readers."""
    import inspect
    src = inspect.getsource(p4_autotest.P4Board.__init__)
    assert "self.ser.exclusive = True" in src
    assert src.index("exclusive") < src.index("self.ser.open()")


# -- a console on the far end -------------------------------------------------


class Clock:
    """Stands in for `time` inside the driver: reading the port is what
    moves it, so drains of seconds cost nothing."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


class Canvas:
    def __init__(self, w, h, stride=None, ox=0, oy=0, buf=None):
        self.w, self.h = w, h
        self._stride = stride or w
        self._bh = h
        self._ox, self._oy = ox, oy
        self._buf = buf if buf is not None else bytearray(
            2 * self._stride * (h + oy))


class Sched:
    rate, div, misses, steady, uncapped = 30, 1, 0, False, False


class Lua:
    def __init__(self, ws, cells):
        self.ws, self.cells = ws, cells
        self.flushed = 0

    def flush_pmem(self):
        self.flushed += 1
        for i, v in enumerate(self.cells):
            self.ws.pmem.cells[i] = v


class Player:
    def __init__(self):
        self.tick_ms = 0
        self.sched = Sched()
        self.notice = None
        self._lua = None


class WM:
    def __init__(self, windowed):
        self._stack = ["launcher", "desk"] if windowed else ["launcher"]
        self.windowed = windowed
        if windowed:
            self._wins, self._order, self._focus = {}, [], None
            self.desk = True

    def desk_open(self):
        return self.desk

    @property
    def stack(self):
        return types.SimpleNamespace(kinds=lambda: list(self._stack))


class WS:
    """The workstation surface the channel and the tool reach, behaving the
    way the real one does where the tool depends on it: `exit` from a cart on
    the windowed tier drops the desk with it (testing.md, 2026-09-22)."""

    def __init__(self, windowed=False, titles=("Brick Siege",)):
        self.wm = WM(windowed)
        self.cart = None
        self.cart_error = None
        self.diag_live = False
        self._uncap = False
        self.leases = types.SimpleNamespace(holders=lambda: [])
        self.wifi = None
        self._psave_ms = 300000
        self._psave_asleep = False
        self._frames_drawn = 5
        self._apps = ()
        self.carts = types.SimpleNamespace(
            all=[{"title": t} for t in titles])
        self.player = Player()
        self.pmem = None
        self.canvas = Canvas(8, 4)
        self.homes = 0
        self.net_tps = None           # a lockstep rate, when a test sets one

    def perf_net(self):
        return self.net_tps

    @property
    def screen(self):
        return self.wm._stack[-1]

    def set_diag_live(self, on, persist=True):
        self.diag_live = on

    def launch_named(self, name):
        for c in self.carts.all:
            if c["title"].lower() == name.lower():
                self.cart = dict(c)
                self.wm._stack.append("player")
                return c["title"]
        return None

    def exit(self):
        if self.cart:
            self.cart = None
            self.wm._stack = ["launcher"]
            if self.wm.windowed:
                self.wm.desk = False

    def go_home(self):
        self.homes += 1
        self.wm._stack = ["launcher"]
        if self.wm.windowed:
            self.wm.desk = False

    def open_desk(self):
        self.wm.desk = True
        self.wm._stack = ["launcher", "desk"]


class Pump:
    """FramePump.last: the board's ticks_ms at the top of the current frame,
    which is where a dev command runs."""

    def __init__(self, clock):
        self.clock = clock

    @property
    def last(self):
        return int(self.clock.now * 1000)


class Wire:
    """The serial port: lines written go to the real DevChannel, what it
    prints comes back, a running cart draws at the head of `fps` -- the rate
    moves on every 2 s -- and under PERF DIAG it emits a PERF line every 2 s
    naming the rate it drew at."""

    port = "/dev/fake"

    def __init__(self, ws, clock, fps=(58, 60, 57, 59, 60), comp=None):
        self.ws, self.clock = ws, clock
        self.ch = DevChannel(ws, types.SimpleNamespace(
            place=lambda x, y: None, down=False),
            env={"comp": comp, "pump": Pump(clock)})
        self.to_host = b""
        self.pending = b""
        self.sent = []
        self.fps = list(fps)
        self.next_perf = clock.now + 2.0
        self._drawn = float(ws._frames_drawn)

    def _tick(self, dt):
        """The console's frames over `dt` s: a cart draws at the head rate,
        the idle launcher draws nothing."""
        self.clock.now += dt
        if self.ws.cart and self.fps:
            self._drawn += self.fps[0] * dt
            self.ws._frames_drawn = int(self._drawn)

    def write(self, data):
        self.pending += data
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            text = line.decode()
            self.sent.append(text)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.ch.run(self.ws, text)
            self.to_host += out.getvalue().encode()
        return len(data)

    def flush(self):
        pass

    @property
    def in_waiting(self):
        return len(self.to_host)

    def read(self, n=1):
        if not self.to_host:
            self._tick(0.05)
            if self.clock.now >= self.next_perf:
                self.next_perf += 2.0
                cart = self.ws.cart["title"] if self.ws.cart else None
                fps = self.fps[0] if self.fps else 60
                if self.ws.cart and self.fps:
                    self.fps.append(self.fps.pop(0))
                if not self.ws.diag_live:      # the line is PERF DIAG's
                    return b""
                self.to_host += (format_perf({
                    "cart": cart, "fps": (fps, 60), "render": 9.0,
                    "logic": 3.0}) + "\n").encode()
            return b""
        d, self.to_host = self.to_host[:n], self.to_host[n:]
        return d

    def close(self):
        pass


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(p4_autotest, "time", c)
    monkeypatch.setattr(board, "time", c)
    return c


def console(clock, board_dir=TDECK, **kw):
    ws = WS(**{k: v for k, v in kw.items() if k in ("windowed", "titles")})
    wire = Wire(ws, clock, **{k: v for k, v in kw.items()
                              if k in ("fps", "comp")})
    return ws, wire, p4_autotest.P4Board(None, board_dir=board_dir, ser=wire)


def args(name="tdeck", **kw):
    return argparse.Namespace(board=name, **kw)


def run(fn, *a):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = fn(*a)
    return code, out.getvalue()


def test_state_prints_the_summary(clock):
    ws, wire, b = console(clock)
    code, out = run(board.cmd_state, b, args(json=False, keys=None))
    assert code == 0
    assert out.splitlines()[0].split() == ["stack", '["launcher"]']
    assert "cart" in out and "wifi_held" in out
    code, out = run(board.cmd_state, b, args(json=False, keys="screen"))
    assert out.strip() == 'screen      "launcher"'


def test_a_silent_board_names_the_way_back(clock):
    ws, wire, b = console(clock)
    wire.write = lambda data: len(data)          # a desktop that is not running
    with pytest.raises(board.BoardError) as exc:
        board.cmd_state(b, args(json=False, keys=None))
    assert "reboot" in str(exc.value) and "--soft" in str(exc.value)


def test_run_then_leave_puts_the_desk_back(clock):
    ws, wire, b = console(clock, board_dir=P4, windowed=True)
    code, out = run(board.cmd_run, b, args("p4", title="brick siege", settle=1.0))
    assert code == 0 and "running Brick Siege" in out
    assert ws.cart is not None
    code, out = run(board.cmd_leave, b, args("p4"))
    assert ws.cart is None
    assert ws.wm.desk is True and ws.wm._stack == ["launcher", "desk"]


def test_run_of_an_unknown_title_says_why(clock):
    ws, wire, b = console(clock)
    code, out = run(board.cmd_run, b, args(title="Nope", settle=1.0))
    assert code == 1 and "rescan" in out


def test_desk_comes_back_from_anywhere(clock):
    ws, wire, b = console(clock, board_dir=P4, windowed=True)
    ws.wm._stack = ["launcher", "settings"]
    ws.wm.desk = False
    code, out = run(board.cmd_desk, b, args("p4"))
    assert ws.homes == 1
    assert ws.wm._stack == ["launcher", "desk"] and ws.wm.desk is True


def test_perf_reports_the_median_and_restores_the_switches(clock):
    ws, wire, b = console(clock, fps=(50, 58, 60, 57, 59, 61))
    code, out = run(board.cmd_perf, b, args(titles=["Brick Siege"], secs=8.0,
                                            diag=False, uncap=True))
    assert code == 0, out
    assert "Brick Siege on tdeck:" in out
    assert "fps drawn (median of" in out
    assert ws.cart is None, "perf ends its cart"
    assert ws._uncap is False and ws.diag_live is False
    assert "uncap 1" in wire.sent and "uncap 0" in wire.sent
    # The shipping fps: PERF DIAG stays off, and the reading is the counter's.
    assert "diag 1" not in wire.sent and "(diag off" in out
    assert any(line.startswith("py (pump.last") for line in wire.sent)


def test_perf_keeps_a_switch_that_was_already_on(clock):
    ws, wire, b = console(clock)
    ws._uncap = True
    ws.diag_live = True
    run(board.cmd_perf, b, args(titles=["Brick Siege"], secs=4.0, diag=True,
                                uncap=True))
    assert ws._uncap is True and ws.diag_live is True
    assert "uncap 0" not in wire.sent


def test_perf_refuses_samples_that_name_another_cart(clock, monkeypatch):
    ws, wire, b = console(clock)
    real = ws.launch_named

    def wrong(name):
        got = real(name)
        ws.cart = {"title": "Other"}               # what PERF will name
        return got

    ws.launch_named = wrong
    code, out = run(board.cmd_perf, b, args(titles=["Brick Siege"], secs=4.0,
                                            diag=True, uncap=False))
    assert code == 1 and "PERF names cart=Other" in out


@pytest.mark.parametrize("rate", [30, 60])
def test_the_counter_reads_what_the_PERF_line_reads(clock, rate):
    """The shipping fps (PERF DIAG off, the drawn-frame counter) and --diag's
    (the PERF samples) are the same number off the same console -- a paced
    cart drawing 30 and an uncapped one drawing 60 -- so a new row compares
    with #66's."""
    import p4_perf
    got = {}
    for diag in (False, True):
        ws, wire, b = console(clock, fps=(rate,))
        ws.diag_live = diag
        got[diag] = p4_perf.measure(b, "Brick Siege", 8.0, lambda *x: None,
                                    diag=diag)
    assert abs(got[False]["fps"] - got[True]["fps"]) < 1.0, got
    assert abs(got[False]["fps"] - rate) < 1.0, got
    assert got[False]["n"] == 4 and got[False]["phases"]["render"] is None
    assert got[True]["phases"]["render"] == 9.0


def test_the_counter_names_a_lockstep_match(clock):
    import p4_perf
    ws, wire, b = console(clock, fps=(30,))
    ws.net_tps = 30.0
    r = p4_perf.measure(b, "Brick Siege", 4.0, lambda *x: None)
    assert r["linked"] == 30.0


def test_the_counter_refuses_a_run_of_another_cart(clock):
    import p4_perf
    ws, wire, b = console(clock)
    real = ws.launch_named

    def wrong(name):
        got = real(name)
        ws.cart = {"title": "Other"}
        return got

    ws.launch_named = wrong
    with pytest.raises(RuntimeError, match="ran Other, not Brick Siege"):
        p4_perf.measure(b, "Brick Siege", 4.0, lambda *x: None)


def _perf_run(tmp_path, monkeypatch, uncap):
    """Coin Quest -- the paced fixture, 30 by default -- on the REAL host
    console for 7 s of 60 Hz loop, under the kernel's loop and its PERF
    sampler (the trace tier's clock) with PERF DIAG on: each PERF line's
    drawn fps, beside the drawn-frame counter read the way tools/p4_perf.py
    reads it, at the same instants."""
    import p4_perf
    from runtime import moy_loop
    from runtime.perf_line import parse_perf
    from ws_helpers import build_ws, open_cart
    ws = build_ws(tmp_path)
    ws._uncap = uncap
    open_cart(ws, "Coin Quest")
    assert ws.cart_error is None, ws.cart_error
    ws.diag_live = True
    moy_loop.trace_init(60, True, 0)

    def frame(dt):
        ws.frame(1 / 60.0)
        if moy_loop.perf_due():
            ws.perf_push(moy_loop)
        return ws._frames_drawn

    moy_loop.register(ws.input.begin_frame, lambda: None, frame)
    moy_loop.capture(True)
    moy_loop.tick(ws.player.tick_ms)
    reads = []
    out = []
    for f in range(7 * 60):
        moy_loop.trace_clock(f * 1000 // 60)
        moy_loop.step()
        lines = [t[4:-1].replace("_", " ").replace("cart=Coin Quest", "cart=x")
                 .replace("fence ms", "fence_ms")
                 for t in moy_loop.trace_log().split() if t.startswith("say[PERF")]
        if lines:
            out += lines
            reads.append((f * 1000 // 60, ws._frames_drawn))
    moy_loop.unregister()
    perf = [parse_perf(l)["fps"][0] for l in out]
    counter = [p4_perf.window_fps(a, b) for a, b in zip(reads, reads[1:])]
    return perf[1:], counter


@pytest.mark.parametrize("uncap, rate", [(False, 30), (True, 60)])
def test_the_counter_is_the_one_PERF_is_taken_from(tmp_path, monkeypatch,
                                                   uncap, rate):
    """On the real console, a paced cart and an uncapped one: the drawn-frame
    counter across a PERF window is that line's drawn fps, to the whole frame
    per second the line truncates to. A paced cart's tick-only frames are not
    drawn frames in either."""
    perf, counter = _perf_run(tmp_path, monkeypatch, uncap)
    assert len(counter) >= 2 and len(counter) == len(perf), (perf, counter)
    for p, c in zip(perf, counter):
        assert p <= c < p + 1, (perf, counter)
        assert abs(c - rate) <= 1.0, (perf, counter)


def test_pmem_flushes_the_cart_before_reading(clock):
    ws, wire, b = console(clock)
    ws.pmem = types.SimpleNamespace(cells=[0] * 256)
    text = b"zone 1 failed\0"
    cells = [int.from_bytes(text[i:i + 4].ljust(4, b"\0"), "little")
             for i in range(0, len(text), 4)]
    ws.player._lua = Lua(ws, [7, -1] + [0] * 254)
    code, out = run(board.cmd_pmem, b, args(text=False, start=0))
    assert ws.player._lua.flushed == 1
    assert out.split()[:3] == ["0:", "7", "-1"]
    ws.player._lua = Lua(ws, cells + [0] * (256 - len(cells)))
    code, out = run(board.cmd_pmem, b, args(text=True, start=0))
    assert out.strip() == "zone 1 failed"


def test_pmem_with_no_cart_says_so(clock):
    ws, wire, b = console(clock)
    code, out = run(board.cmd_pmem, b, args(text=False, start=0))
    assert code == 1 and out.startswith("no pmem")


# -- the shot -----------------------------------------------------------------


PAL565 = (0x0000, 0x194A)


@pytest.fixture
def device_modules(monkeypatch):
    """What the helper imports on a board: `device_canvas`'s two tables and
    MicroPython's streaming `deflate`, here over zlib (whose smallest window is
    512 bytes where the board's is 256 -- the stream is the same format)."""
    dc = types.ModuleType("device_canvas")
    dc.PAL565 = PAL565
    dc.PAL565_WIRE = tuple(((c << 8) | (c >> 8)) & 0xFFFF for c in PAL565)
    zl = types.ModuleType("deflate")
    zl.ZLIB = 1

    class DeflateIO:
        def __init__(self, stream, fmt, wbits):
            self.s = stream
            self.c = zlib.compressobj(9, zlib.DEFLATED, max(9, wbits))

        def write(self, data):
            self.s.write(self.c.compress(bytes(data)))

        def close(self):
            self.s.write(self.c.flush())

    zl.DeflateIO = DeflateIO
    monkeypatch.setitem(sys.modules, "device_canvas", dc)
    monkeypatch.setitem(sys.modules, "deflate", zl)
    return dc


def rgb(v):
    r, g, bl = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (bl << 3) | (bl >> 2))


def frame(w, h, little_endian):
    """A frame whose every pixel is distinct, as the board stores it."""
    px = array("H", [(i * 2654435761) & 0xFFFF for i in range(w * h)])
    raw = array("H", px)
    if little_endian != (sys.byteorder == "little"):
        raw.byteswap()
    return px, bytearray(raw.tobytes())


def decode_png(data):
    """(w, h, rows of RGB tuples) from a filter-0 RGB PNG."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w, h = 8, b"", 0, 0
    while pos < len(data):
        n = int.from_bytes(data[pos:pos + 4], "big")
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            w, h = int.from_bytes(body[:4], "big"), int.from_bytes(body[4:8], "big")
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = 1 + 3 * w
    return w, h, [[tuple(raw[y * stride + 1 + 3 * x:y * stride + 4 + 3 * x])
                   for x in range(w)] for y in range(h)]


class BandedComp:
    """A ping-pong compositor after a flush: `_back` has moved on, so the
    buffer the panel last took is the one before it."""

    def __init__(self, w, h, shown):
        self._w, self._h = w, h
        self._fbs = [bytearray(2 * w * h), shown]
        self._back = 0


@pytest.mark.parametrize("raw", [False, True])
def test_shot_of_the_screen_is_the_last_shown_buffer(clock, device_modules,
                                                     tmp_path, raw):
    w, h = 16, 12
    px, fb = frame(w, h, little_endian=False)      # an SPI panel's order
    comp = BandedComp(w, h, fb)
    ws, wire, b = console(clock, comp=comp)
    out = tmp_path / "s.png"
    code, text = run(board.cmd_shot, b, args(out=str(out), source="screen",
                                             raw=raw))
    assert code == 0, text
    gw, gh, rows = decode_png(out.read_bytes())
    assert (gw, gh) == (w, h)
    assert rows[0][0] == rgb(px[0]) and rows[h - 1][w - 1] == rgb(px[-1])
    assert all(rows[y][x] == rgb(px[y * w + x])
               for y in range(h) for x in range(w))
    assert ws._g["_shot"] is None, "the board lets go of the frame"


def test_shot_of_a_dsi_board_reads_little_endian(clock, device_modules, tmp_path):
    device_modules.PAL565_WIRE = PAL565
    w, h = 10, 6
    px, fb = frame(w, h, little_endian=True)
    comp = BandedComp(w, h, fb)
    comp._fbs = [bytearray(2 * w * h), bytearray(2 * w * h), fb]
    comp._back = 1
    comp._pend3 = [(0, "game")]                # queued, not yet on the glass
    ws, wire, b = console(clock, board_dir=P4, comp=comp)
    out = tmp_path / "p4.png"
    run(board.cmd_shot, b, args("p4", out=str(out), source="screen", raw=False))
    _w, _h, rows = decode_png(out.read_bytes())
    assert rows[2][3] == rgb(px[2 * w + 3])


def test_shot_of_the_game_canvas_crops_its_view(clock, device_modules, tmp_path):
    stride, bh = 20, 10
    px, buf = frame(stride, bh, little_endian=False)
    ws, wire, b = console(clock, comp=BandedComp(4, 4, bytearray(32)))
    ws.canvas = Canvas(6, 3, stride=stride, ox=4, oy=2, buf=buf)
    out = tmp_path / "g.png"
    run(board.cmd_shot, b, args(out=str(out), source="game", raw=False))
    w, h, rows = decode_png(out.read_bytes())
    assert (w, h) == (6, 3)
    assert rows[0][0] == rgb(px[2 * stride + 4])
    assert rows[2][5] == rgb(px[4 * stride + 9])


@pytest.mark.parametrize("angle", [90, 270])
def test_a_portrait_scan_buffer_turns_back_to_landscape(angle):
    """The inverse of the rotated compositor's rect rotation, pixel by pixel: rotate
    a landscape picture onto portrait glass the way the PPA does, and the
    shot's unrotate hands back the landscape."""
    from runtime.glass_binding import rotate_rect
    lw, lh = 7, 5
    land = array("H", range(1, lw * lh + 1))
    pw, ph = lh, lw
    portrait = array("H", bytes(2 * pw * ph))
    for y in range(lh):
        for x in range(lw):
            px_, py_, _w, _h = rotate_rect(x, y, 1, 1, angle, lw, lh)
            portrait[py_ * pw + px_] = land[y * lw + x]
    back, w, h = board.unrotate(portrait, pw, ph, angle)
    assert (w, h) == (lw, lh)
    assert back == land


def test_shot_of_a_rotated_compositor_is_landscape(clock, device_modules,
                                                   tmp_path):
    device_modules.PAL565_WIRE = PAL565
    pw, ph = 6, 9                                   # portrait glass
    px, fb = frame(pw, ph, little_endian=True)
    comp = types.SimpleNamespace(_fbs=[bytearray(2 * pw * ph), fb,
                                       bytearray(2 * pw * ph)],
                                 _front=1, _back=2, _pw=pw, _ph=ph,
                                 _w=ph, _h=pw, angle=90)
    ws, wire, b = console(clock, board_dir=DIRS["guition_p4"], comp=comp)
    out = tmp_path / "r.png"
    run(board.cmd_shot, b, args("guition_p4", out=str(out), source="screen",
                                raw=False))
    w, h, rows = decode_png(out.read_bytes())
    assert (w, h) == (ph, pw)
    # landscape (x, y) sits at portrait (y, lw - 1 - x) at 90 degrees
    assert rows[1][2] == rgb(px[(ph - 1 - 2) * pw + 1])


def test_the_shot_helpers_are_one_py_upload_per_shot(clock, device_modules,
                                                     tmp_path):
    """Round trips are the cost on a board -- one per frame, 200 ms each --
    so the bands must be few: the helpers, the open, and one reply a band."""
    w, h = 64, 64
    _px, fb = frame(w, h, little_endian=False)
    ws, wire, b = console(clock, comp=BandedComp(w, h, fb))
    run(board.cmd_shot, b, args(out=str(tmp_path / "x.png"), source="screen",
                                raw=True))
    nexts = [s for s in wire.sent if s == "py ws._g['_shot_next']()"]
    assert len(nexts) == 1, "8 KB is one band"


# -- waiting for a board after a reset ------------------------------------------


class _Booting:
    """A board just reset, on a virtual clock: silent until `quiet`, its
    console up after that (the first line), the desk answering `state` from
    `desk` on. Records every open and the time of every `state` sent."""

    def __init__(self, clock, quiet, desk, repl=False):
        self.clock, self.quiet, self.desk, self.repl = clock, quiet, desk, repl
        self.opens = 0
        self.sent = []

    def __call__(self, port, board_dir=None, log=None):
        self.opens += 1
        self.lines = []
        return self

    def drain(self, secs):
        self.clock[0] += secs
        if self.clock[0] >= self.quiet and not self.lines:
            self.lines.append("Moybyte T-Deck (mainline) boot")
            return self.lines[:]
        return []

    def state(self, timeout=8.0):
        self.sent.append(self.clock[0])
        self.clock[0] += timeout
        if self.repl:
            self.lines += [">>> state", "NameError: name 'state' isn't defined"]
        elif self.clock[0] >= self.desk:
            return {"stack": ["launcher"]}
        raise RuntimeError("no STATE reply")

    def close(self):
        pass


def _virtual_time(monkeypatch, clock):
    def sleep(s):
        clock[0] += s
    monkeypatch.setattr(board, "time", types.SimpleNamespace(
        time=lambda: clock[0], sleep=sleep))


def test_a_board_just_reset_is_not_written_to_before_it_speaks(monkeypatch):
    """A line that reaches a USB-Serial/JTAG console before its boot reads
    stdin stalls the endpoint, and a reopen then lands 0x03 in the board's
    stdin -- Ctrl-C to the boot (tools/patch_usj_rx_init.py). So after a
    reset the waiter sends nothing until a line arrives. It still reopens
    the port between polls: a reset can replace the node under a handle held
    across it (the Guition S3's does), and an open alone is harmless."""
    clock = [0.0]
    _virtual_time(monkeypatch, clock)
    b = _Booting(clock, quiet=2.5, desk=15.0)
    monkeypatch.setattr(board, "P4Board", b)
    st = board.wait_for_desk("tdeck", DIRS, port="/dev/null", quiet=True,
                             reset=True)
    assert st == {"stack": ["launcher"]}
    assert b.opens > 1
    assert b.sent and min(b.sent) >= 2.5, b.sent


def test_a_silent_board_is_asked_once_the_quiet_has_run_out(monkeypatch):
    """A board whose boot is already over prints nothing in kid mode."""
    clock = [0.0]
    _virtual_time(monkeypatch, clock)
    b = _Booting(clock, quiet=1e9, desk=0.0)
    monkeypatch.setattr(board, "P4Board", b)
    assert board.wait_for_desk("tdeck", DIRS, port="/dev/null", quiet=True,
                               reset=True)
    assert b.sent[0] >= board.HEAR_S


def test_a_wait_with_no_reset_asks_at_once(monkeypatch):
    clock = [0.0]
    _virtual_time(monkeypatch, clock)
    b = _Booting(clock, quiet=1e9, desk=0.0)
    monkeypatch.setattr(board, "P4Board", b)
    assert board.wait_for_desk("tdeck", DIRS, port="/dev/null", quiet=True)
    assert b.sent[0] < 1.0


def test_a_board_at_the_repl_is_named_as_one(monkeypatch):
    clock = [0.0]
    _virtual_time(monkeypatch, clock)
    b = _Booting(clock, quiet=0.0, desk=0.0, repl=True)
    monkeypatch.setattr(board, "P4Board", b)
    with pytest.raises(board.BoardError) as exc:
        board.wait_for_desk("tdeck", DIRS, port="/dev/null", timeout=30,
                            quiet=True, reset=True)
    assert "REPL" in str(exc.value) and "reboot --soft" in str(exc.value)
