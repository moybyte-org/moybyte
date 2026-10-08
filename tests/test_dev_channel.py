"""The unified serial dev channel (runtime/dev_channel.py).

Until 2026-08-17 the channel existed three times: this module (extracted for
the fork, which was then deleted -- zero importers), a verbatim copy inside the
T-Deck's moy_runtime, and an older inline loop in the P4's. Both boards
construct `DevChannel` now, so this file is the host half of the regression
net; the on-glass halves are tests/test_p4_on_glass.py and
tests/test_tdeck_on_glass.py, which drive the same commands over real serial.

Everything here runs against stub objects on CPython -- the channel is
deliberately importable with no board and no console (its device_util import
falls back to a self-contained shim), which is what makes this testable at all.
"""

import hashlib
import json
import types

import pytest

from runtime import moy_input
from runtime.dev_channel import (DevChannel, PERF_EVENTS, _remote_state,
                                 heapcaps_line,
                                 luaprof_line, perfcnt_line, shim_line_range,
                                 cart_shim_range,
                                 verbs_line)


class FakePointer:
    def __init__(self):
        self.down = False
        self.fresh = False
        self.click = False
        self.placed = []

    def place(self, x, y):
        self.placed.append((x, y))


class _Stack:
    def kinds(self):
        return ["home"]


class FullscreenWM:
    stack = _Stack()


class Win:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.title_h = 18
        self.kind = "settings"
        self.minimized = False
        self.buf = None
        self.ctx = None


class WindowedWM:
    def __init__(self):
        self._order = ["settings"]
        self._focus = "settings"
        self._wins = {"settings": Win(100, 80, 640, 400)}

    def desk_open(self):
        return True


class _FakeCarts:
    """`ws.carts` narrowed to the roster the `state` snapshot walks (#209
    landing C)."""

    def __init__(self):
        self.all = []


class FakeWS:
    def __init__(self, wm=None):
        self.wm = wm or FullscreenWM()
        self.screen = "home"
        self.wifi = None
        self.carts = _FakeCarts()
        self._apps = ()
        self._dirty = False
        self._psave_ms = 300000
        self._psave_asleep = False
        self.input = moy_input.HostInputTable()


def make(ws=None, **kw):
    ws = ws or FakeWS()
    return ws, DevChannel(ws, FakePointer(), **kw)


# -- the state snapshot --------------------------------------------------------


def test_state_carries_both_tiers_shapes(capsys):
    """ONE snapshot for every tier: the fullscreen back-stack when the WM has
    one, the windowed fields when it has windows -- the P4 suite's keys
    (psave/desk/order/wins) and the T-Deck's (stack) from the same function."""
    full = _remote_state(FakeWS())
    assert full["stack"] == ["home"]
    assert "wins" not in full

    windowed = _remote_state(FakeWS(wm=WindowedWM()))
    assert windowed["desk"] is True
    assert windowed["order"] == ["settings"]
    assert windowed["wins"]["settings"][:4] == [100, 80, 640, 400]
    assert "stack" not in windowed


def test_state_is_one_line_json(capsys):
    ws, ch = make()
    ch.run(ws, "state")
    out = capsys.readouterr().out
    line = [l for l in out.splitlines() if l.startswith("STATE ")][0]
    st = json.loads(line.split("STATE ", 1)[1])
    assert st["screen"] == "home"


def test_state_reports_every_frame_stage_and_the_ladder_from_the_kernel(monkeypatch):
    """#210's route. `state` is the one every board serves -- the Guition
    stages no device_diag and has no PUMP line -- so the kernel loop's
    per-stage deadline meters ride it, in the loop's invariant order and with
    its field shape, with the idle ladder and the frame's upcalls beside."""
    from runtime import dev_channel
    from runtime import moy_loop as L

    monkeypatch.setattr(dev_channel, "_loop", L)
    L.trace_init(60, True, 1000)
    L.register(lambda: None, lambda: None, lambda dt: 1)
    L.idle(L.BLANK, 300)
    L.capture(True)
    L.step()
    st = _remote_state(FakeWS())
    L.unregister()
    assert list(st["stages"]) == list(L.stages())
    for row in st["stages"].values():
        assert sorted(row) == ["avg_us", "budget_us", "last_us", "max_us",
                               "misses", "n"]
    frame = st["stages"]["frame"]
    assert frame["n"] == 1 and frame["budget_us"] == 16 * 780
    # A stage with no deadline to miss: None, never the 0 that reads
    # identically to a broken meter.
    assert st["stages"]["tail"]["budget_us"] is None
    assert st["psave"] == [False, 300]
    assert st["idle"]["blank"] == 300
    assert st["upcalls"] == [3, 0, 0, 0]


def test_state_reports_no_stages_at_all_where_no_kernel_loop_runs(monkeypatch):
    """A tier whose frames are not the kernel's has no stage meters to dump --
    and that is None, not eleven zeroed rows."""
    from runtime import dev_channel
    monkeypatch.setattr(dev_channel, "_loop", None)
    st = _remote_state(FakeWS())
    assert st["stages"] is None and st["psave"] is None and st["upcalls"] is None


def test_state_reports_the_sram_headroom_the_run_had_or_none():
    """#211's route, and it is `state` for the same reason #210's is: every
    board serves it. A run that tipped into PSRAM reports the regime change as
    a boolean beside the low-water mark it tipped at; a Python cart and a tier
    whose allocator has one region report None -- never zeros, which is also
    what a meter that stopped working looks like."""

    class PlayerWS(FakeWS):
        def __init__(self, report):
            FakeWS.__init__(self)
            self.player = type("P", (), {"sram_report": lambda _s: report})()

    tipped = {"sram_free_min": 21504, "psram_fallback": True, "floor": 24576}
    assert _remote_state(PlayerWS(tipped))["sram"] == tipped
    assert _remote_state(PlayerWS(None))["sram"] is None


# -- gesture scripts -----------------------------------------------------------
#
# The words find their aim (a named button, the top window's title strip) and
# the kernel plays the gesture (native/moy_kernel/moy_devch.c): one sample a
# frame into the channel's source, logged here by the loop's trace tier.


def _played(frames=40):
    """The pointer samples the kernel's gesture player wrote, a frame each."""
    from runtime import moy_loop as L

    out = []
    for _ in range(frames):
        L.step()
        out += [t[3:] for t in L.trace_log().split() if t.startswith("pt=")]
    return out


@pytest.fixture
def kernel():
    from runtime import moy_loop as L

    L.trace_init(60, True, 1000)
    L.register(lambda: None, lambda: None, lambda dt: 1)
    L.trace_log()
    yield L
    L.unregister()


def test_swipe_is_press_hold_release(capsys, kernel):
    """i==0 press edge, held interpolation, i==n a real RELEASE sample at the
    end point (down=False) -- the shape the fling estimators need."""
    ws, ch = make()
    ch.run(ws, "swipe 0 0 100 0 5")
    samples = _played()
    assert "REMOTE swipe 0,0 -> 100,0 frames=5" in capsys.readouterr().out
    assert samples == ["0,0,1,1", "25,0,1,0", "50,0,1,0", "75,0,1,0",
                       "100,0,1,0", "100,0,0,0"]


def test_a_tap_is_a_press_then_a_release_a_frame_apart(capsys, kernel):
    ws, ch = make()
    ch.run(ws, "tap 40 50")
    assert "REMOTE tap 40 50" in capsys.readouterr().out
    assert kernel.trace_log().split() == ["pt=40,50,1,1"]   # pressed by the word
    kernel.step()
    assert "pt=" not in kernel.trace_log()                   # the press is merged
    kernel.step()
    assert "pt=40,50,0,0" in kernel.trace_log()


def test_drag_declines_without_windows_and_runs_with(capsys, kernel):
    ws, ch = make()                                        # fullscreen tier
    ch.run(ws, "drag")
    assert "REMOTE drag: no window open" in capsys.readouterr().out
    assert _played(3) == []

    ws2 = FakeWS(wm=WindowedWM())
    ch2 = DevChannel(ws2, moy_input.Pointer(320, 240))
    ch2.run(ws2, "drag 12 3")
    out = capsys.readouterr().out
    assert "REMOTE drag win=settings" in out and "frames=12 step=3" in out
    samples = _played(20)
    assert len(samples) == 13                             # 12 frames + release
    assert samples[0] == "130,89,1,1"                     # the title strip
    assert samples[-1] == "130,89,0,0"                    # released at the end


# -- board extras and the py env -----------------------------------------------


def test_extras_dispatch_after_builtins_and_cannot_shadow(capsys):
    calls = []
    ws, ch = make(extra={"bt": lambda ws, p, l: calls.append(l),
                         "state": lambda ws, p, l: calls.append("SHADOW")})
    ch.run(ws, "bt status")
    assert calls == ["bt status"]
    ch.run(ws, "state")                                   # built-in wins
    assert calls == ["bt status"]
    assert "STATE " in capsys.readouterr().out


def test_unknown_command_echoes(capsys):
    ws, ch = make()
    ch.run(ws, "frobnicate 1")
    assert "REMOTE ? frobnicate 1" in capsys.readouterr().out


def test_py_env_reaches_injected_names(capsys):
    ws, ch = make(env={"marker": 41})
    ch.run(ws, "py marker + 1")
    assert "PY 42" in capsys.readouterr().out


def test_kstale_hands_the_spine_four_dead_handles_and_all_are_refused(capsys):
    """The spine's handle gate on glass is this word (#224): a released, a
    forged, a wrong-kind and a zero handle, each a StaleHandle that names its
    table, with the live handle still served."""
    from runtime.moy_spine import AppRegistry

    ws, ch = make()
    ws.apps = AppRegistry()
    ws.apps.register("artwork", "Paint")
    ch.run(ws, "kstale")
    line = [ln for ln in capsys.readouterr().out.splitlines()
            if ln.startswith("REMOTE kstale")][0]
    assert line.startswith("REMOTE kstale ok impl=python "), line
    for name in ("released", "forged", "kind", "zero"):
        assert "%s=stale app handle " % name in line, line


def test_kstale_fails_loudly_when_a_dead_handle_is_served(capsys):
    from runtime.moy_spine import AppRegistry

    class Serving(AppRegistry):
        def title(self, h):
            return "served"

    ws, ch = make()
    ws.apps = Serving()
    ws.apps.register("artwork", "Paint")
    ch.run(ws, "kstale")
    out = capsys.readouterr().out
    assert "REMOTE kstale FAIL" in out and "forged=SERVED" in out


# -- `link`: arming the radio from outside a cart ------------------------------


class FakeLink:
    """`ws.link` (device/moy_espnow.py's Link) narrowed to what `link` drives."""

    def __init__(self):
        self.active = False
        self.announced = []

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def announce(self, cart="", state=0):
        self.announced.append((cart, state))

    def stats(self):
        return {"active": self.active, "peers": []}


def test_link_declines_on_a_board_with_no_radio(capsys):
    ws, ch = make()
    ch.run(ws, "link")
    assert "no radio on this board" in capsys.readouterr().out


def test_link_reports_and_arms_the_radio_by_hand(capsys):
    """The Player only arms the radio for a cart that declares the multiplayer
    permission, so a two-board bench needs a way in from outside one."""
    ws, ch = make()
    ws.link = FakeLink()

    def said():
        """The one LINK line the command prints, parsed. Every branch reports,
        including the ones that changed something."""
        out = [ln for ln in capsys.readouterr().out.splitlines()
               if ln.startswith("LINK ")]
        assert len(out) == 1, out
        return json.loads(out[0][len("LINK "):])

    ch.run(ws, "link")                        # bare: reports, changes nothing
    assert said() == {"active": False, "peers": []}
    assert ws.link.active is False and ws.link.announced == []

    ch.run(ws, "link on")
    assert ws.link.active is True and said()["active"] is True

    ch.run(ws, "link cart Brick Siege")
    assert ws.link.announced == [("Brick Siege", 1)]
    said()

    ch.run(ws, "link off")
    assert ws.link.active is False and said()["active"] is False


# -- `recv`: the raw upload, off the board -------------------------------------
#
# The loop is driven here through the two objects it actually talks to -- the
# 8-bit stdin and the poll -- so what runs is the real body: the windowing, the
# ack ordering, the idle timeout, the interrupt-char switch and the read-back
# hash. What a host CANNOT model is the ISR underneath (a UART ring that drops
# a byte with no error is hardware); those live in tests/test_push_cart.py as
# what the TOOL does about them, and on glass.


class FakeRawIn:
    """`sys.stdin.buffer`: bytes, and only the ones that have ARRIVED.

    `over_read` is the guarantee the idle timeout rests on -- every read is
    preceded by a poll that already promised a byte, because a bulk read on a
    real board blocks inside `mp_hal_stdin_rx_chr` with no timeout at all."""

    def __init__(self, data=b""):
        self.data = bytearray(data)
        self.over_read = False

    def readinto(self, buf):
        if not self.data:
            self.over_read = True
            return 0
        buf[0] = self.data.pop(0)
        return 1


class FakePoll:
    """POLLIN on the stream above. `on_dry` fires the moment the board asks for
    a byte that has not arrived -- which is where the host is waiting for an
    ack, and so where the ordering can be observed."""

    def __init__(self, raw, on_dry=None):
        self.raw = raw
        self.on_dry = on_dry
        self.waits = []

    def ipoll(self, timeout=-1):
        if self.raw.data:
            return ((None, 1),)
        self.waits.append(timeout)
        if self.on_dry is not None:
            self.on_dry()
        return ()


def raw_channel(data=b"", on_dry=None):
    """A channel whose stdin is `data`. Returns (ws, channel, stdin, poll)."""
    ws, ch = make()
    raw = FakeRawIn(data)
    poll = FakePoll(raw, on_dry)
    ch._rawin = raw
    ch._poll = poll
    ch._ipoll = poll.ipoll
    return ws, ch, raw, poll


def _said(capsys, prefix="RECV "):
    return [l for l in capsys.readouterr().out.splitlines()
            if l.startswith(prefix)]


EVERY_BYTE = bytes(range(256)) * 5          # 0x03 and both newlines included


def test_recv_writes_every_byte_value_and_hashes_what_it_wrote(
        tmp_path, capsys):
    """8 BITS, no base64: the interrupt char, CR and LF all ride through. On a
    board the first is swallowed by the RX ISR and CR is rewritten by the TEXT
    stdin -- which is why the loop reads `stdin.buffer` with kbd_intr off."""
    ws, ch, raw, _poll = raw_channel(EVERY_BYTE)
    dst = str(tmp_path / "main.lua")
    ch.run(ws, "recv %d 512 %s" % (len(EVERY_BYTE), dst))
    lines = _said(capsys)
    assert (tmp_path / "main.lua.new").read_bytes() == EVERY_BYTE
    assert lines[0] == "RECV ready %d 512 %s.new" % (len(EVERY_BYTE), dst)
    assert lines[-1] == "RECV done %s %d" % (
        hashlib.sha256(EVERY_BYTE).hexdigest()[:12], len(EVERY_BYTE))
    assert [l for l in lines if l.startswith("RECV ack")] == [
        "RECV ack %d" % min(n, len(EVERY_BYTE))
        for n in range(512, len(EVERY_BYTE) + 512, 512)]
    assert ch.raw == len(EVERY_BYTE)
    assert raw.over_read is False


def test_the_ack_goes_out_before_the_write_and_the_next_read_after_it(
        tmp_path, capsys, monkeypatch):
    """The host puts the next window on the wire the moment it reads the ack,
    so acking before the file write lets that window cross while the store
    writes -- and it is still the ONLY window in flight, which the Waveshare's
    ring holds whole whatever the write costs. Observed at both instants: the
    write, with the ack already out, and the board asking for window two's
    first byte, with window one already on disk."""
    import builtins

    real = builtins.open
    wrote = []
    seen = {}

    class Noted:
        def __init__(self, f):
            self.f = f

        def write(self, data):
            if not wrote:
                seen["at_write"] = capsys.readouterr().out
            wrote.append(len(data))
            return self.f.write(bytes(data))

        def __getattr__(self, name):
            return getattr(self.f, name)

    def on_dry():
        # The board asks again after each re-send offer, so this fires more
        # than once; the instant being observed is the FIRST one -- the board
        # waiting on window two.
        if "wrote" not in seen:
            seen["wrote"] = list(wrote)

    ws, ch, _raw, _poll = raw_channel(EVERY_BYTE[:512], on_dry=on_dry)
    monkeypatch.setattr(builtins, "open",
                        lambda p, m="r", *a, **k: Noted(real(p, m, *a, **k))
                        if "w" in m else real(p, m, *a, **k))
    ch.run(ws, "recv %d 512 %s" % (len(EVERY_BYTE), str(tmp_path / "main.lua")))
    assert "RECV ack 512" in seen["at_write"]      # acked before the write
    assert seen["wrote"] == [512]                  # written before the next read


def test_a_host_that_goes_quiet_takes_the_tmp_with_it(tmp_path, capsys):
    """A dead host must not park the frame loop, and must not leave a half cart
    behind either. The wait is bounded by RECV_IDLE_MS per byte, refreshed by
    every byte that does arrive, so a slow host is not a dead one.

    It now OFFERS the window back first -- a short window is a dropped byte far
    more often than a dead host -- and only gives up once RECV_DEAD_WINDOWS of
    them arrive completely empty. The count it reports is what LANDED IN THE
    FILE (512 here, one whole window) rather than what had been buffered when
    the stream stopped: 188 bytes of a window that was thrown away were never
    part of the cart, and naming them sent a reader looking for a file that
    was 700 bytes long."""
    from runtime.dev_channel import RECV_DEAD_WINDOWS, RECV_IDLE_MS

    ws, ch, _raw, poll = raw_channel(EVERY_BYTE[:700])
    dst = str(tmp_path / "main.lua")
    ch.run(ws, "recv 5000 512 %s" % dst)
    said = _said(capsys)
    assert said[-1] == "RECV ERR timeout after 512 of 5000 bytes"
    assert [l for l in said if l.startswith("RECV retry")] == [
        "RECV retry 512"] * RECV_DEAD_WINDOWS
    assert not (tmp_path / "main.lua.new").exists()
    assert poll.waits == [RECV_IDLE_MS] * (RECV_DEAD_WINDOWS + 1)


class _StoreWS(FakeWS):
    """A console with a store and a notice banner, for `recv`'s full store."""

    def __init__(self):
        super().__init__()
        from runtime import moy_carts
        self.carts_store = moy_carts
        self.notices = []

    def notice(self, title, sub="", kind="ok", ms=6000):
        self.notices.append((title, sub, kind))


def _full_open(monkeypatch, room):
    """`open` whose written files hold `room` bytes and then fail as a full
    store does."""
    import builtins
    real = builtins.open

    class Full:
        def __init__(self, f):
            self.f = f
            self.n = 0

        def write(self, data):
            if self.n + len(data) > room:
                raise OSError(28, "No space left on device")
            self.n += len(data)
            return self.f.write(bytes(data))

        def __getattr__(self, name):
            return getattr(self.f, name)

    monkeypatch.setattr(builtins, "open",
                        lambda p, m="r", *a, **k: Full(real(p, m, *a, **k))
                        if "w" in m else real(p, m, *a, **k))


def test_a_store_with_no_room_stops_the_upload_plainly_and_on_screen(
        tmp_path, capsys, monkeypatch):
    """A cart that does not fit is a plain `store full` from the board -- the
    words push_cart puts in front of the person pushing -- and the console's
    banner says it to whoever is looking at the glass. The half-written file
    goes, as on every other failure."""
    ws, ch = make(_StoreWS())
    raw = FakeRawIn(EVERY_BYTE)
    poll = FakePoll(raw)
    ch._rawin, ch._poll, ch._ipoll = raw, poll, poll.ipoll
    _full_open(monkeypatch, 700)
    dst = str(tmp_path / "main.aot")
    ch.run(ws, "recv %d 512 %s" % (len(EVERY_BYTE), dst))
    said = _said(capsys)
    assert said[-1] == "RECV ERR store full after 512 of %d bytes" % len(EVERY_BYTE)
    assert ws.notices == [("CAN'T ADD CART", "the store is full", "warn")]
    assert not (tmp_path / "main.aot.new").exists()


def test_another_write_failure_is_named_and_not_called_a_full_store(
        tmp_path, capsys, monkeypatch):
    import builtins
    real = builtins.open

    class Broken:
        def __init__(self, f):
            self.f = f

        def write(self, data):
            raise OSError(5, "EIO")

        def __getattr__(self, name):
            return getattr(self.f, name)

    ws, ch = make(_StoreWS())
    raw = FakeRawIn(EVERY_BYTE)
    poll = FakePoll(raw)
    ch._rawin, ch._poll, ch._ipoll = raw, poll, poll.ipoll
    monkeypatch.setattr(builtins, "open",
                        lambda p, m="r", *a, **k: Broken(real(p, m, *a, **k))
                        if "w" in m else real(p, m, *a, **k))
    ch.run(ws, "recv %d 512 %s" % (len(EVERY_BYTE), str(tmp_path / "x")))
    said = _said(capsys)
    assert said[-1].startswith("RECV ERR OSError:"), said[-1]
    assert ws.notices == []


class FeedingPoll(FakePoll):
    """FakePoll, but a host that WROTE during `on_dry` is answered.

    The base class fires the hook and then reports not-ready regardless, which
    is right when the hook only observes. Here the hook IS the host: it puts a
    window on the wire, and a poll that ignored it would make every window look
    dropped."""

    def ipoll(self, timeout=-1):
        ready = super().ipoll(timeout)
        if not ready and self.raw.data:
            return ((None, 1),)
        return ready


class ReSendingHost:
    """The push loop in miniature, over the same FakeRawIn the board reads.

    It writes one window, waits, and does what the board's last line asks:
    `retry <n>` re-sends the window at n, `ack` moves on. `drop` names the
    windows the WIRE eats a byte from -- which is the whole failure this
    exists to model, because the host cannot see it happen and neither can the
    board until the stream stops."""

    def __init__(self, payload, window, drop=()):
        self.payload, self.window = payload, window
        self.drop = set(drop)
        self.raw = FakeRawIn()
        self.sent = 0
        self.nth = 0
        self.resends = 0
        self.said = []

    def _feed(self):
        blk = self.payload[self.sent:self.sent + self.window]
        # The host always believes it wrote the whole window; the ring is what
        # loses the byte, silently, which is why `sent` advances by the FULL
        # window even on a dropped one.
        self.sent += len(blk)
        if self.nth in self.drop:
            self.drop.discard(self.nth)
            blk = blk[:-1]
        self.nth += 1
        self.raw.data.extend(blk)

    def on_dry(self, capsys):
        new = [l for l in capsys.readouterr().out.splitlines()
               if l.startswith("RECV ")]
        self.said += new
        if not new:
            # The board is still waiting inside a window this host already
            # finished writing -- which is exactly the shape of a dropped
            # byte, and a real host would be blocked on the reply. Writing
            # here would hand it the NEXT window as the tail of this one.
            return
        last = new[-1]
        if last.startswith("RECV retry "):
            self.sent = int(last.split()[2])
            self.resends += 1
        elif last.startswith("RECV ERR") or last.startswith("RECV done"):
            return
        if self.sent < len(self.payload):
            self._feed()


def test_a_dropped_byte_costs_its_window_and_not_the_cart(tmp_path, capsys):
    """The failure this whole retry exists for, end to end.

    A UART ring with no flow control drops a byte with no error when it
    overflows. Measured on the P4's stock 260-byte ring: a handful of bytes
    lost about once every 300 windows, which killed a 120KB push one time in
    five. The file only ever advances by WHOLE windows, so the board can throw
    the short one away and name the boundary it is still standing on -- and
    the cart lands byte-exact, hash and all, having paid one window."""
    payload = EVERY_BYTE                      # 1280 B = 5 windows of 256
    host = ReSendingHost(payload, 256, drop=(1, 3))
    ws, ch = make()
    poll = FeedingPoll(host.raw, lambda: host.on_dry(capsys))
    ch._rawin, ch._poll, ch._ipoll = host.raw, poll, poll.ipoll
    dst = str(tmp_path / "main.lua")

    ch.run(ws, "recv %d 256 %s" % (len(payload), dst))
    host.said += [l for l in capsys.readouterr().out.splitlines()
                  if l.startswith("RECV ")]

    assert (tmp_path / "main.lua.new").read_bytes() == payload
    assert host.said[-1] == "RECV done %s %d" % (
        hashlib.sha256(payload).hexdigest()[:12], len(payload))
    # one re-send per dropped window, each at the boundary the file was on
    assert [l for l in host.said if l.startswith("RECV retry")] == [
        "RECV retry 256", "RECV retry 512"]
    assert host.resends == 2
    assert ch.raw == len(payload)


def test_a_wire_that_drops_every_window_gives_up_rather_than_crawling(
        tmp_path, capsys):
    """The budget. A cable that eats a byte from EVERY window is broken, and
    saying so beats re-sending forever -- so the retries are counted, and the
    count is what ends it rather than the dead-host rule (every window here
    arrives nearly full, so none of them is empty)."""
    from runtime.dev_channel import RECV_RETRIES

    payload = EVERY_BYTE
    host = ReSendingHost(payload, 256, drop=range(200))    # every window
    ws, ch = make()
    poll = FeedingPoll(host.raw, lambda: host.on_dry(capsys))
    ch._rawin, ch._poll, ch._ipoll = host.raw, poll, poll.ipoll

    ch.run(ws, "recv %d 256 %s" % (len(payload), str(tmp_path / "main.lua")))
    host.said += [l for l in capsys.readouterr().out.splitlines()
                  if l.startswith("RECV ")]

    assert len([l for l in host.said if l.startswith("RECV retry")]) \
        == RECV_RETRIES
    assert host.said[-1] == "RECV ERR timeout after 0 of %d bytes" % len(payload)
    assert not (tmp_path / "main.lua.new").exists()


def test_the_hash_is_of_the_file_not_of_the_bytes_that_went_in(
        tmp_path, capsys, monkeypatch):
    """`open(p,'wb')` has reported a byte count on this console for a file that
    read back EMPTY (push_cart's header, item 2). A hash taken from the buffer
    would agree with the host about a cart that is not on the store, so the
    file is read back through the same window buffer and hashed from there."""
    import builtins

    real = builtins.open

    class Lossy:
        def __init__(self, f):
            self.f = f

        def write(self, data):
            return self.f.write(bytes(data)[:-1])      # the store eats one

        def __getattr__(self, name):
            return getattr(self.f, name)

    def fake_open(path, mode="r", *a, **kw):
        f = real(path, mode, *a, **kw)
        return Lossy(f) if "w" in mode else f

    monkeypatch.setattr(builtins, "open", fake_open)
    ws, ch, _raw, _poll = raw_channel(EVERY_BYTE[:512])
    ch.run(ws, "recv 512 512 %s" % (tmp_path / "main.lua"))
    monkeypatch.undo()
    landed = (tmp_path / "main.lua.new").read_bytes()
    assert len(landed) == 511
    assert _said(capsys)[-1].split()[2] == hashlib.sha256(
        landed).hexdigest()[:12]


def test_the_interrupt_char_goes_off_for_the_transfer_and_comes_back(
        tmp_path, capsys, monkeypatch):
    """A payload byte equal to it never reaches the ring -- both esp32 RX ISRs
    swallow it, and the CDC path empties the ring as well. Restored in a
    `finally`, so the timeout path leaves the board interruptible too."""
    import runtime.dev_channel as dc

    calls = []
    monkeypatch.setattr(dc, "_micropython",
                        type("M", (), {"kbd_intr": staticmethod(calls.append)}))
    ws, ch, _raw, _poll = raw_channel(EVERY_BYTE[:64])
    ch.run(ws, "recv 64 64 %s" % (tmp_path / "a.lua"))
    assert calls == [-1, 3]
    del calls[:]
    ws, ch, _raw, _poll = raw_channel(b"")
    ch.run(ws, "recv 64 64 %s" % (tmp_path / "b.lua"))
    assert "RECV ERR timeout" in _said(capsys)[-1]
    assert calls == [-1, 3]


def test_a_build_that_cannot_go_8_bit_declines_instead(tmp_path, capsys):
    """One fallback, not two: a board with no way to disable the interrupt char
    refuses the raw mode, and the host pushes through base64 as before, rather
    than shipping 255 of 256 byte values and finding out from the hash."""
    import runtime.dev_channel as dc

    ws, ch, _raw, _poll = raw_channel(EVERY_BYTE)
    was = dc.RECV_8BIT
    dc.RECV_8BIT = False
    try:
        ch.run(ws, "recv 64 64 %s" % (tmp_path / "a.lua"))
    finally:
        dc.RECV_8BIT = was
    assert "RECV ERR no 8-bit route" in _said(capsys)[0]
    assert not (tmp_path / "a.lua.new").exists()


def test_bare_recv_is_the_capability_line(capsys):
    """The whole handshake. An image without the command answers `REMOTE ?
    recv` from the same dispatcher, which is a positive no -- see
    tools/push_cart.raw_link."""
    from runtime.dev_channel import RECV_IDLE_MS, RECV_MAX_WINDOW

    ws, ch, _raw, _poll = raw_channel()
    ch.run(ws, "recv")
    assert _said(capsys) == ["RECV caps max=%d idle=%d"
                             % (RECV_MAX_WINDOW, RECV_IDLE_MS)]


def test_a_window_the_board_cannot_hold_is_refused(tmp_path, capsys):
    """The window is also the buffer the board allocates for it, so its own
    ceiling binds -- and it refuses BEFORE arming, while the host is still
    reading lines rather than blasting bytes at a reader that moved on."""
    from runtime.dev_channel import RECV_MAX_WINDOW

    ws, ch, _raw, _poll = raw_channel(EVERY_BYTE)
    ch.run(ws, "recv 64 %d %s" % (RECV_MAX_WINDOW * 2, tmp_path / "a.lua"))
    assert _said(capsys)[0].startswith("RECV ERR")
    assert not (tmp_path / "a.lua.new").exists()


def test_a_channel_with_no_8_bit_stdin_declines_the_probe_too(capsys):
    """The decline comes BEFORE the caps line, whatever was asked: a board that
    cannot carry every byte value must not advertise a window. push_cart reads
    `RECV ERR` as a no exactly like `REMOTE ? recv`."""
    ws, ch = make()
    ch._rawin = None
    ch.run(ws, "recv")
    assert "RECV ERR no 8-bit route" in _said(capsys)[0]


# -- `recv` over native/moy_serial, and the payload at another UART rate -------
#
# The boards read the ring from C (moy_serial.readinto) and the Waveshare P4
# switches its console UART for the payload (moy_serial.baud). The fakes below
# put both over the same FakeRawIn, and RateHost is push_cart's half of the
# rate protocol over a wire that only carries what both ends say at the same
# rate.


class FakeMoySerial:
    """native/moy_serial without a UART REPL: `readinto` over FakeRawIn,
    giving up when nothing has arrived, which on a board is idle_ms of quiet."""

    def __init__(self, raw, on_dry=None):
        self.raw = raw
        self.on_dry = on_dry
        self.quiet = []

    def readinto(self, buf, i, n, idle_ms):
        while i < n:
            if not self.raw.data and self.on_dry is not None:
                self.on_dry()
            if not self.raw.data:
                self.quiet.append(idle_ms)
                break
            buf[i] = self.raw.data.pop(0)
            i += 1
        return i


class FakeUartSerial(FakeMoySerial):
    """...and with one: `baud` switches the rate the board is listening at."""

    def __init__(self, raw, rate=115200, on_dry=None):
        super().__init__(raw, on_dry)
        self.rate = rate
        self.switches = []

    def baud(self, rate=None):
        if rate is not None:
            self.rate = rate
            self.switches.append(rate)
        return self.rate


class RateHost:
    """tools/push_cart's side of `recv rate=`, in miniature.

    The board's lines reach it only when it is at the rate they were said at,
    and its bytes reach the board only when the board is at its rate --
    anything else arrives as noise, which is what a mismatched UART makes of
    it. It reacts when the board next waits for bytes, which is the earliest a
    real host can have read the line."""

    NOISE = b"\xfe\x00"

    def __init__(self, payload, window, fast, console=115200, sync=True,
                 noise=b""):
        from runtime.dev_channel import RECV_SYNC
        self.payload, self.window, self.fast = payload, window, fast
        self.rate = console
        self.console = console
        self.sync, self.noise = sync, noise
        self.SYNC = RECV_SYNC
        self.raw = FakeRawIn()
        self.mod = FakeUartSerial(self.raw, console, self.on_dry)
        self.said = []          # (line, the board's rate when it said it)
        self.heard = []
        self.done = 0
        self.sent = 0

    def board_print(self, *args, **kw):
        self.said.append((" ".join(str(a) for a in args), self.mod.rate))

    def write(self, data):
        self.raw.data.extend(data if self.mod.rate == self.rate
                             else self.NOISE * len(data))

    def _send(self):
        blk = self.payload[self.sent:self.sent + self.window]
        self.sent += len(blk)
        self.write(blk)

    def on_dry(self):
        while self.done < len(self.said):
            line, rate = self.said[self.done]
            self.done += 1
            if rate != self.rate or not line.startswith("RECV "):
                continue
            self.heard.append(line)
            words = line.split()
            if words[1] == "ready" and "rate=%d" % self.fast in words:
                self.rate = self.fast
                if self.sync:
                    self.write(self.noise)
                    self.write(self.SYNC)
            elif words[1] in ("ready", "sync"):
                self._send()
            elif words[1] == "ack":
                if int(words[2]) == len(self.payload):
                    if self.rate != self.console:
                        self.rate = self.console
                        self.write(self.SYNC)
                else:
                    self._send()
            elif words[1] == "retry":
                self.sent = int(words[2])
                self._send()
            elif words[1] == "ERR":
                self.rate = self.console


def rate_channel(monkeypatch, host, ws=None, mod=None):
    import runtime.dev_channel as dc
    ws, ch = make(ws)
    ch._rawin = host.raw
    monkeypatch.setattr(dc, "_moy_serial", mod or host.mod)
    monkeypatch.setattr(dc, "print", host.board_print, raising=False)
    return ws, ch


def test_the_payload_runs_at_the_rate_and_the_console_comes_back(
        tmp_path, monkeypatch):
    """The Waveshare's console UART stays at its own rate for every line --
    `moy push`, a terminal and the boot log all expect it -- and only the
    payload runs fast. The board switches after `ready` has left, hears the
    host's sync at the new rate, and is back at the console rate the moment
    the last ack is out; the end sync is taken off the wire before `done`."""
    payload = EVERY_BYTE * 3
    host = RateHost(payload, 512, fast=2000000, noise=b"\x00\xff\x13")
    ws, ch = rate_channel(monkeypatch, host)
    dst = tmp_path / "main.aot"
    ch.run(ws, "recv %d 512 rate=2000000 %s" % (len(payload), dst))
    host.on_dry()                             # the host reads the last line
    assert (tmp_path / "main.aot.new").read_bytes() == payload
    assert host.mod.switches == [2000000, 115200]
    assert host.mod.rate == 115200
    assert host.heard[0] == "RECV ready %d 512 rate=2000000 %s.new" % (
        len(payload), dst)
    assert host.heard[1] == "RECV sync 2000000"
    assert host.heard[-1] == "RECV done %s %d" % (
        hashlib.sha256(payload).hexdigest()[:12], len(payload))
    acks = [l for l in host.heard if l.startswith("RECV ack")]
    assert len(acks) == len(range(0, len(payload), 512))
    assert host.raw.data == bytearray()       # both syncs consumed


def test_no_sync_at_the_new_rate_puts_the_console_back_and_keeps_nothing(
        tmp_path, monkeypatch):
    """A rate the link cannot carry shows as a sync that never arrives. The
    board goes back to the console rate on its own clock, so the host -- which
    waits longer -- finds it there; nothing was written."""
    payload = EVERY_BYTE
    host = RateHost(payload, 512, fast=6000000, sync=False)
    ws, ch = rate_channel(monkeypatch, host)
    ch.run(ws, "recv %d 512 rate=6000000 %s" % (len(payload),
                                                 tmp_path / "main.aot"))
    assert host.mod.switches == [6000000, 115200]
    assert host.said[-1] == ("RECV ERR no sync at 6000000", 115200)
    assert not (tmp_path / "main.aot.new").exists()


def test_a_failure_at_the_payload_rate_is_said_there_before_going_back(
        tmp_path, monkeypatch):
    """The host is at the payload's rate, waiting on a window's reply, so that
    is where the board says it -- and the window the host had already sent on
    the ack is taken off the wire, so its bytes never reach the line reader as
    commands."""
    payload = EVERY_BYTE * 2
    host = RateHost(payload, 512, fast=2000000)
    ws, ch = rate_channel(monkeypatch, host, _StoreWS())
    _full_open(monkeypatch, 700)
    ch.run(ws, "recv %d 512 rate=2000000 %s" % (len(payload),
                                                 tmp_path / "main.aot"))
    assert ("RECV ERR store full after 512 of %d bytes" % len(payload),
            2000000) in host.said
    assert host.mod.rate == 115200
    assert host.raw.data == bytearray()
    assert ws.notices == [("CAN'T ADD CART", "the store is full", "warn")]
    assert not (tmp_path / "main.aot.new").exists()


def test_a_board_that_cannot_switch_takes_the_payload_at_its_own_rate(
        tmp_path, monkeypatch):
    """`rate=` is a request. A build with no UART REPL to switch (the USB
    boards) says `ready` without it, and the host streams at the console's
    rate -- the token never reaches the path."""
    payload = EVERY_BYTE
    host = RateHost(payload, 512, fast=2000000)
    ws, ch = rate_channel(monkeypatch, host,
                          mod=FakeMoySerial(host.raw, host.on_dry))
    dst = tmp_path / "main.lua"
    ch.run(ws, "recv %d 512 rate=2000000 %s" % (len(payload), dst))
    assert host.said[0][0] == "RECV ready %d 512 %s.new" % (len(payload), dst)
    assert (tmp_path / "main.lua.new").read_bytes() == payload


def test_moy_serial_reads_the_window_and_times_out_the_same(
        tmp_path, capsys, monkeypatch):
    """The C reader is the same contract as the poll loop: the window as it
    lands, and a quiet stretch of RECV_IDLE_MS as the end of a short one."""
    import runtime.dev_channel as dc
    from runtime.dev_channel import RECV_DEAD_WINDOWS, RECV_IDLE_MS

    raw = FakeRawIn(EVERY_BYTE[:700])
    mod = FakeMoySerial(raw)
    monkeypatch.setattr(dc, "_moy_serial", mod)
    ws, ch = make()
    ch._rawin = raw
    ch.run(ws, "recv 5000 512 %s" % (tmp_path / "main.lua"))
    assert _said(capsys)[-1] == "RECV ERR timeout after 512 of 5000 bytes"
    assert mod.quiet == [RECV_IDLE_MS] * (RECV_DEAD_WINDOWS + 1)


def test_the_caps_line_names_the_console_rate_only_where_it_can_switch(
        capsys, monkeypatch):
    import runtime.dev_channel as dc
    from runtime.dev_channel import RECV_IDLE_MS, RECV_MAX_WINDOW

    raw = FakeRawIn()
    monkeypatch.setattr(dc, "_moy_serial", FakeUartSerial(raw, 115200))
    ws, ch = make()
    ch._rawin = raw
    ch.run(ws, "recv")
    monkeypatch.setattr(dc, "_moy_serial", FakeMoySerial(raw))
    ch.run(ws, "recv")
    assert _said(capsys) == [
        "RECV caps max=%d idle=%d rate=115200" % (RECV_MAX_WINDOW, RECV_IDLE_MS),
        "RECV caps max=%d idle=%d" % (RECV_MAX_WINDOW, RECV_IDLE_MS)]


def test_the_transfer_runs_inside_the_storage_gate(tmp_path, capsys):
    """On the T-Deck the card shares the panel's SPI host, and a store op that
    overlaps a panel transfer hangs the board -- so the whole transfer, read
    back included, is one session of the console's gate."""
    ws, ch, _raw, _poll = raw_channel(EVERY_BYTE)
    sessions = []

    def gate(fn):
        sessions.append("in")
        try:
            return fn()
        finally:
            sessions.append("out")

    ws._with_sd = gate
    ch.run(ws, "recv %d 512 %s" % (len(EVERY_BYTE), tmp_path / "main.lua"))
    assert sessions == ["in", "out"]
    assert "RECV done" in _said(capsys)[-1]


# -- the VERBS line (the Lua/p8 per-verb profiler's report) -------------------


def test_the_perfcnt_line_leads_with_the_ratio_it_exists_for():
    """IPC is the question -- four null levers on the S3 tick were read as
    "memory, not instructions", which is an elimination rather than a
    measurement, and this is the number that confirms or re-opens it. It is a
    RATIO on purpose: the four boards do not share a clock, so a count would
    not compare and `r` does."""
    # 100 frames; update 240k cycles and 168k instructions a frame (r=0.7),
    # draw 120k cycles and 24k instructions (r=0.2) -- one cart, both answers
    st = (240000000, 100, 24000000, 16800000, 12000000, 2400000,
          2, 0xffff, True)
    line = perfcnt_line(st, "insn")
    assert line.startswith("PERFCNT frames=100 insn")
    assert "upd cyc=240000 insn=168000 r=0.700" in line
    assert "draw cyc=120000 insn=24000 r=0.200" in line
    # and the wall-clock size of each half, so a ratio is never read without
    # knowing whether the half is worth anything
    assert "1.000ms" in line and "0.500ms" in line


def test_the_perfcnt_line_keeps_the_halves_apart():
    """moss moss is update-bound and dank tomb draw-bound. One IPC over both
    would average the answer away, so a half with no cycles is simply absent
    rather than folded in."""
    st = (240000000, 10, 2400000, 1680000, 0, 0, 2, 0xffff, True)
    line = perfcnt_line(st, "insn")
    assert "upd " in line and "draw " not in line


def test_the_perfcnt_line_says_when_the_silicon_only_counts_two_things():
    """The RISC-V part has the two architectural CSRs and no selector, so a
    reading from it must not look like a chosen event that happened to be
    instructions."""
    st = (400000000, 5, 1000000, 700000, 0, 0, 2, 0xffff, False)
    assert "(riscv: retired only)" in perfcnt_line(st, "insn")
    assert "(riscv: retired only)" not in perfcnt_line(
        (400000000, 5, 1000000, 700000, 0, 0, 2, 0xffff, True), "insn")


def test_a_perfcnt_reading_with_no_frames_says_so_rather_than_dividing():
    assert perfcnt_line((240000000, 0, 0, 0, 0, 0, 2, 0xffff, True)) == \
        "PERFCNT no frames (run a cart with `perfcnt on`)"


def test_every_named_perf_event_is_a_selector_and_a_mask():
    """The serial word is a word so nobody types a magic integer at a board.
    `insn` is the default and must exist; the rest are the follow-up once IPC
    has said which way to look."""
    assert "insn" in PERF_EVENTS
    for name, pair in PERF_EVENTS.items():
        sel, mask = pair
        assert 0 <= sel <= 0xffff and 0 <= mask <= 0xffff, name


def test_the_verbs_line_reports_per_frame_not_per_window():
    """The shape the line has to make legible: 180 cheap calls and one
    expensive one are different problems, and per-window totals hide which is
    which behind whatever sample length the host happened to choose."""
    line = verbs_line(1000000, 100, [
        ("spr", 18000, 1400000),        # 180/frame, 14ms/frame
        ("map", 100, 1400000),          #   1/frame, 14ms/frame
    ])
    assert "frames=100" in line
    assert "spr n=180.0 t=14.00" in line
    assert "map n=1.0 t=14.00" in line
    assert "tot=28.00" in line


def test_the_verbs_line_sorts_by_time_and_keeps_the_top():
    """The first row is nearly always the whole answer, and a serial line has
    to end somewhere -- so the cut is by cost, never by name or arrival."""
    rows = [("v%d" % i, 10, i * 1000) for i in range(20)]
    line = verbs_line(1000000, 10, rows, top=3)
    names = [p.split()[0] for p in line.split(" | ")[1:]]
    assert names == ["v19", "v18", "v17"]


def test_a_window_with_no_frames_says_so_instead_of_dividing_by_it():
    """`verbs` read before a cart has ticked is the ordinary operator mistake;
    it must answer, not raise inside the frame loop."""
    assert verbs_line(1000000, 0, [("spr", 5, 5)]) == "VERBS frames=0"
    assert verbs_line(0, 10, [("spr", 5, 5)]) == "VERBS frames=0"


def test_verbs_declines_on_a_board_with_no_moycore(capsys):
    """Every board freezes this channel; only the ones with the Lua tier can
    answer. The decline is a line, not an ImportError into the loop."""
    ws, ch = make()
    ch.run(ws, "verbs")
    assert "no moycore on this board" in _said(capsys, "REMOTE ")[0]


def test_a_verb_that_runs_lua_under_it_is_not_charged_for_it():
    """moss moss' foreach: five calls a frame, ten milliseconds INCLUSIVE, and
    none of it foreach's own. Charged inclusively it reads as the slowest thing
    in the cart and points a fix at the wrong file, so `t` is self and `in`
    carries the frame that ran underneath."""
    line = verbs_line(1000000, 100, [
        ("__moy_foreach", 500, 12000, 1016000),
        ("cls", 100, 44000, 44000),
    ])
    assert "__moy_foreach n=5.0 t=0.12 in=10.16" in line
    assert "cls n=1.0 t=0.44" in line
    cls_cell = [c for c in line.split(" | ") if c.startswith("cls")][0]
    assert "in=" not in cls_cell                 # no callees, no second number
    assert "tot=0.56" in line                    # SELF, so foreach's Lua is out
    # and the sort is by SELF, so the cheap-but-inclusive verb drops below
    assert line.index("cls") < line.index("__moy_foreach")


def test_the_verbs_line_still_reads_a_three_field_row():
    """The formatter predates the self/inclusive split and the on-glass tools
    parse its output; a row with no inclusive column means "no callees", not a
    crash."""
    assert "spr n=1.0 t=1.00" in verbs_line(1000000, 10, [("spr", 10, 10000)])


# -- the Lua-tier sampling profiler ------------------------------------------

def _shim_cart(tmp_path, before=25, body=("function p8_go() end",)):
    """A ported cart's main.lua in miniature: `before` lines of data tables,
    then the generator's two shim markers, then the cart's own code. The line
    OFFSET is the point -- the data tables above the shim vary per cart, which
    is why the range is read from the file instead of being a constant."""
    d = tmp_path / "port.moy"
    d.mkdir(parents=True)
    lines = ["-- data %d" % i for i in range(1, before)]
    lines.append("-- =========================")            # the banner
    lines.append("-- PICO-8 compatibility shim (generated by tools/p8_lua_port.py)")
    lines += ["  function shim_fn_%d() end" % i for i in range(40)]
    lines.append("-- ============== end shim =============")
    lines += list(body)
    (d / "main.lua").write_text("\n".join(lines) + "\n")
    return str(d / "main.lua")


def test_the_shim_range_is_read_from_the_cart_that_is_loaded(tmp_path):
    """The emitted block is a fixed 1,348 lines but it starts wherever that
    cart's data tables ended -- 26 lines into moss moss, 163 into a cart that
    needs the raw sheet. A constant here would file 137 lines of one cart's
    generated code as its own."""
    a = shim_line_range(_shim_cart(tmp_path / "a", before=25))
    b = shim_line_range(_shim_cart(tmp_path / "b", before=140))
    assert a == (25, 67), a
    assert b == (140, 182), b
    assert b[1] - b[0] == a[1] - a[0]           # same shim, different offset


def test_the_shim_range_reader_never_holds_the_file(tmp_path):
    """It is read in blocks with a carry because the board being asked has that
    same ~100KB cart resident and barely fitting. The carry is where such a
    reader goes wrong, so the markers are found identically at a block size
    smaller than either marker."""
    p = _shim_cart(tmp_path, before=30)
    assert shim_line_range(p, block=3) == shim_line_range(p, block=1 << 20)


def test_the_range_comes_from_the_script_that_holds_the_shim(tmp_path):
    """SPEC.md 4: a port is two scripts, and the shim is p8.lua's.

    The range is pinned against the chunk that defines `_draw`, and the shim
    owns `_draw` -- so on a split port the VM reports p8.lua's line numbers. A
    range read off main.lua would fail the pin and charge nothing as shim,
    which reads as "this cart has no generated half" and is exactly wrong for
    the carts the profiler exists to measure."""
    d = tmp_path / "port.moy"
    d.mkdir()
    (d / "main.lua").write_text(
        "-- Localized p8 API (generated)\nfunction p8_draw() end\n")
    lines = ["-- data %d" % i for i in range(1, 12)]
    lines.append("-- =========================")
    lines.append("-- PICO-8 compatibility shim (generated by tools/p8_lua_port.py)")
    lines += ["  function shim_fn_%d() end" % i for i in range(9)]
    lines.append("-- ============== end shim =============")
    (d / "p8.lua").write_text("\n".join(lines) + "\n")

    assert cart_shim_range(str(d)) == (12, 23)
    assert shim_line_range(str(d / "main.lua")) is None, \
        "main.lua is the cart -- no marker in it to find"

    # A single-file port predates the split and still answers.
    one = tmp_path / "one"
    _shim_cart(one, before=25)
    assert cart_shim_range(str(one / "port.moy")) == (25, 67)


def test_a_cart_with_no_shim_reports_no_range(tmp_path):
    """A hand-written Lua cart is not a port and has no generated half. That is
    "no split to make", which the profiler must say rather than guess at."""
    d = tmp_path / "plain.moy"
    d.mkdir()
    (d / "main.lua").write_text("function _draw() cls(0) end\n")
    assert shim_line_range(str(d / "main.lua")) is None
    assert shim_line_range(str(tmp_path / "gone.moy" / "main.lua")) is None
    assert cart_shim_range(str(d)) is None


def _stats(rows, smp=1000, shim_smp=600, cyc=2000, shim_cyc=900, pinned=True,
           srcs=("cart",)):
    tot = (smp, cyc, 4000, 3000, shim_smp, shim_cyc, 2400, 0, 31, pinned)
    return (1000000, 100, 1024, tot, rows, srcs)


def test_the_luaprof_line_splits_the_emitted_shim_from_the_cart():
    """The question the whole instrument exists for: of the interpreter time a
    ported cart spends, how much is the 1,348 lines the importer emitted into
    it. `shim=` is that share of SAMPLES, with the wall-clock share beside it
    -- the two differ exactly where the collector and the C verbs are."""
    line = luaprof_line(_stats([(0, 1200, 6410, 300, 900000),
                                (0, 1600, 120, 100, 300000)]), (26, 1373))
    assert "shim=60%/45%" in line
    # 4000 Lua calls a frame-window over 100 frames, 2400 of them into the shim.
    assert "n=40 sn=24 c=30" in line
    # s/c is the row's own side, from its linedefined -- 1200 is inside the
    # shim's lines, 1600 is the cart's own code below them.
    assert "s1200 30.0% n=64.1 t=9.00" in line
    assert "c1600 10.0% n=1.2 t=3.00" in line


def test_a_refused_pin_reports_no_split_rather_than_a_wrong_one():
    """The range is checked against the shim's own `_draw` on the device. When
    they disagree the two are looking at different files, and a confident 60%
    would be a number about the wrong cart."""
    line = luaprof_line(_stats([(0, 1200, 10, 100, 1000)], pinned=False),
                        (26, 1373))
    assert "shim=n/a" in line
    assert "unpinned" in line
    assert "c1200" in line                      # nothing is claimed as shim


def test_the_luaprof_line_survives_a_window_with_no_frames():
    """Read before the cart has ticked -- "measured nothing", which is a real
    answer and not a division by zero in the loop's own print path."""
    assert "smp=0" in luaprof_line((1000000, 0, 1024, (0,) * 10, (), ()), None)


def test_luaprof_declines_on_a_board_without_the_lua_tier(capsys):
    """Same shape as `verbs`: a decline is a line, not an ImportError thrown
    into the frame loop."""
    ws, ch = make()
    ch.run(ws, "luaprof")
    assert "no moycore on this board" in _said(capsys, "REMOTE ")[0]
    ch.run(ws, "luagc")
    assert "no moycore on this board" in _said(capsys, "REMOTE ")[0]


# -- `moy push`: proposals/sideload.md's tier 1 (moy-spec) --------------------
#
# Driven through poll(), the way moy-spec's sideload.py reaches a board: lines
# on the TEXT stdin, a whole file's base64 streamed without waiting, and the
# replies read back off the channel's prints.


class FakeText:
    """`sys.stdin`: characters, only the ones that have arrived."""

    def __init__(self, text=""):
        self.data = list(text)

    def read(self, n):
        return self.data.pop(0) if self.data else ""


class FakeTextPoll:
    def __init__(self, stdin):
        self.stdin = stdin

    def ipoll(self, timeout=-1):
        return ((None, 1),) if self.stdin.data else ()


class _StoreWS2(FakeWS):
    """A console with a cart store at `root`, a rescan and a launcher."""

    def __init__(self, root):
        super().__init__()
        self.carts_root = str(root)
        self.rescans = 0
        self.launched = []
        self.notices = []

    def rescan_carts(self):
        self.rescans += 1

    def launch_named(self, name):
        self.launched.append(name)
        return name if name == "Plasma" else None

    def notice(self, title, sub="", kind="ok", ms=6000):
        self.notices.append((title, sub, kind))


def text_channel(root):
    ws = _StoreWS2(root)
    ws, ch = make(ws)
    stdin = FakeText()
    poll = FakeTextPoll(stdin)
    ch._stdin, ch._rawin, ch._ipoll = stdin, stdin, poll.ipoll
    return ws, ch, stdin


def pump(ws, ch, stdin, frames=200):
    """Frames until stdin is drained; how many it took. The kernel's line
    reader (native/moy_kernel/moy_devch.c, tested in tests/test_moy_loop.py)
    is played by a line a frame: what arrives up to a newline goes to the
    channel's words, as the loop's word upcall hands it."""
    for n in range(frames):
        if not stdin.data:
            return n
        line = []
        while stdin.data:
            c = stdin.read(1)
            if c in ("\n", "\r", b"\n", b"\r"):
                break
            line.append(c if isinstance(c, str) else c.decode("latin-1"))
        text = "".join(line).strip()
        if text:
            ch.word(ws, text)
    raise AssertionError("stdin never drained")


def put_lines(path, data):
    """What sideload.push_serial writes for one file."""
    import base64
    b64 = base64.b64encode(data).decode()
    lines = ["moy-put %s %d" % (path, len(data))]
    lines += [b64[i:i + 504] for i in range(0, len(b64), 504)]
    return "\n".join(lines + ["."]) + "\n"


def test_moy_query_answers_the_descriptor(tmp_path, capsys):
    """`moy?` is the whole probe: one `moy-info` line of JSON."""
    ws, ch, stdin = text_channel(tmp_path)
    stdin.data = list("moy?\n")
    pump(ws, ch, stdin)
    line = _said(capsys, "moy-info ")[0]
    desc = json.loads(line[len("moy-info "):])
    assert desc["moy_console"] == "0.1"
    assert desc["transports"] == ["serial"]
    assert "lua" in desc["runtimes"] and desc["free_kb"] > 0


def test_moy_put_writes_a_cart_through_a_new_and_rescans(tmp_path, capsys):
    """A pushed cart lands whole in the store, every byte value included, the
    stamped .bak beside an old copy dropped; the rescan puts it on the shelf.
    A whole file streams in few frames, not a line a frame."""
    ws, ch, stdin = text_channel(tmp_path)
    (tmp_path / "plasma.moy").mkdir()
    (tmp_path / "plasma.moy" / "main.lua").write_bytes(b"old")
    (tmp_path / "plasma.moy" / "main.lua.bak").write_bytes(b"stamp")
    big = bytes(range(256)) * 64
    stdin.data = list(put_lines("plasma.moy/main.lua", b"function _draw() end\n")
                      + put_lines("plasma.moy/data/blob.bin", big)
                      + "moy-rescan\n")
    frames = pump(ws, ch, stdin)
    said = _said(capsys, "moy-")
    assert said == ["moy-ok"] * 5
    assert (tmp_path / "plasma.moy" / "main.lua").read_bytes() == b"function _draw() end\n"
    assert not (tmp_path / "plasma.moy" / "main.lua.bak").exists()
    assert (tmp_path / "plasma.moy" / "data" / "blob.bin").read_bytes() == big
    assert not list(tmp_path.rglob("*.new"))
    assert ws.rescans == 1
    assert frames < 12


def test_moy_put_reads_through_moy_serial_and_hands_back_what_follows(
        tmp_path, capsys, monkeypatch):
    """On a board the drain takes what has arrived from C in one call: the
    line reader polling a byte at a time cannot keep up with the stream on a
    P4. Bytes after the `.` belong to the line reader, and a store write runs
    inside the console's storage gate."""
    import runtime.dev_channel as dc

    data = bytes(range(256)) * 3
    ws, ch, _stdin = text_channel(tmp_path)
    sessions = []
    ws._with_sd = lambda fn: sessions.append(1) or fn()
    head, rest = put_lines("c.moy/main.lua", data).split("\n", 1)
    raw = FakeRawIn((rest + "sta").encode())
    monkeypatch.setattr(dc, "_moy_serial", FakeMoySerial(raw))
    handed = []
    monkeypatch.setattr(dc, "_loop", types.SimpleNamespace(
        devch_unread=lambda b: handed.append(bytes(b)),
        devch_budget=lambda n: None))
    ch.run(ws, head)
    assert (tmp_path / "c.moy" / "main.lua").read_bytes() == data
    assert _said(capsys, "moy-") == ["moy-ok", "moy-ok"]
    assert handed == [b"sta"]
    assert sessions == [1]


def test_moy_put_refuses_a_path_outside_the_store(tmp_path, capsys):
    ws, ch, stdin = text_channel(tmp_path / "carts")
    for path in ("../evil.lua", "a//b", "a/./b", "a\\b", ""):
        ch.run(ws, "moy-put %s 4" % path)
    assert _said(capsys, "moy-") == ["moy-err usage: moy-put <path in the cart store> <bytes>"] * 5
    assert ch._put is None


def test_a_short_moy_put_leaves_nothing_behind(tmp_path, capsys):
    """A file whose lines came up short -- a byte lost on a UART -- is refused
    by its count, and the old file stays."""
    ws, ch, stdin = text_channel(tmp_path)
    (tmp_path / "c.moy").mkdir()
    (tmp_path / "c.moy" / "main.lua").write_bytes(b"kept")
    text = put_lines("c.moy/main.lua", b"0123456789").replace("moy-put c.moy/main.lua 10",
                                                               "moy-put c.moy/main.lua 12")
    stdin.data = list(text)
    pump(ws, ch, stdin)
    assert _said(capsys, "moy-") == ["moy-ok", "moy-err c.moy/main.lua: 10 of 12 bytes arrived"]
    assert (tmp_path / "c.moy" / "main.lua").read_bytes() == b"kept"
    assert not list(tmp_path.rglob("*.new"))


def test_a_full_store_is_said_plainly_to_moy_push_and_on_screen(tmp_path, capsys,
                                                              monkeypatch):
    ws, ch, stdin = text_channel(tmp_path)
    from runtime import moy_carts
    ws.carts_store = moy_carts
    _full_open(monkeypatch, 100)
    stdin.data = list(put_lines("big.moy/main.wasm", bytes(1000)))
    pump(ws, ch, stdin)
    said = _said(capsys, "moy-")
    assert said[0] == "moy-ok" and said[1].startswith("moy-err big.moy/main.wasm: store full")
    assert ws.notices == [("CAN'T ADD CART", "the store is full", "warn")]
    assert not list(tmp_path.rglob("*.new"))


def test_a_compiled_cart_without_its_module_is_noted_at_rescan(tmp_path, capsys,
                                                             monkeypatch):
    """`moy push` cannot build the module a board runs a compiled cart from
    at full speed, so the rescan says so -- in a moy-note, which the tool shows
    the person. The module is named for the chip AND the format."""
    from runtime import dev_channel
    monkeypatch.setattr(dev_channel, "_moy_chip", lambda: "esp32s3")
    monkeypatch.setattr(dev_channel, "_moy_format", lambda: "2")
    ws, ch, stdin = text_channel(tmp_path)
    man = b'{"format": "moy-1", "title": "P", "runtime": "wasm", "memory": 4}'
    stdin.data = list(put_lines("p.moy/manifest.json", man)
                      + put_lines("p.moy/main.wasm", b"\0asm\1\0\0\0")
                      + put_lines("q.moy/manifest.json", man)
                      + put_lines("q.moy/main.wasm", b"\0asm\1\0\0\0")
                      + put_lines("q.moy/main.esp32s3.f2.aot", b"module")
                      + "moy-rescan\n")
    pump(ws, ch, stdin)
    notes = _said(capsys, "moy-note ")
    assert len(notes) == 1 and notes[0].startswith("moy-note p.moy is a compiled cart")
    assert "tools/push_cart.py" in notes[0] and "interpreter" in notes[0]


def test_moy_del_and_moy_run(tmp_path, capsys):
    ws, ch, stdin = text_channel(tmp_path)
    (tmp_path / "old.moy" / "src").mkdir(parents=True)
    (tmp_path / "old.moy" / "src" / "main.c").write_bytes(b"x")
    (tmp_path / "old.moy" / "manifest.json").write_bytes(b"{}")
    ch.run(ws, "moy-del old.moy")
    ch.run(ws, "moy-run Plasma")
    ch.run(ws, "moy-run Nothing Here")
    assert _said(capsys, "moy-") == ["moy-ok", "moy-ok", "moy-err no cart called Nothing Here"]
    assert not (tmp_path / "old.moy").exists()


def test_a_put_whose_stream_stops_ends_short_and_leaves_nothing(tmp_path, capsys):
    """The tool died mid-file: no `.` ever comes. The put ends on the idle
    timeout as short, and the frame loop has its channel back."""
    ws, ch, stdin = text_channel(tmp_path)
    text = put_lines("d.moy/main.lua", bytes(2000))
    stdin.data = list(text[:text.rindex("\n.")])        # every line but the `.`
    pump(ws, ch, stdin)
    said = _said(capsys, "moy-")
    assert said[0] == "moy-ok"
    assert said[1].startswith("moy-err d.moy/main.lua: the stream stopped after")
    assert ch._put is None
    assert not list(tmp_path.rglob("*.new")) and not (tmp_path / "d.moy" / "main.lua").exists()


# -- the HEAPCAPS line (docs/native_kernel_2026-09.md sprint 0) ----------------


class FakeEsp32:
    """`esp32.idf_heap_info(caps)`: a list of (total, free, largest, min free)
    per region carrying those caps."""

    def __init__(self, regions):
        self.regions = regions

    def idf_heap_info(self, caps):
        return self.regions.get(caps, [])


class FakeGc:
    def __init__(self, areas=(2, 3145728), live=812000, has_areas=True):
        self.calls = []
        self._areas = areas
        self._live = live
        if has_areas:
            self.areas = self._read_areas

    def _read_areas(self):
        self.calls.append("areas")
        return self._areas

    def collect(self):
        self.calls.append("collect")

    def mem_alloc(self):
        self.calls.append("mem_alloc")
        return self._live


def test_the_heapcaps_line_sums_regions_as_idf_does():
    """Total, free and low-water add over a set's regions -- the low-water sum
    is exactly IDF's own heap_caps_get_minimum_free_size -- and the largest
    free block is the largest of any one region, since no allocation spans
    two."""
    esp = FakeEsp32({
        0x400: [(8388608, 4000000, 3900000, 3500000)],
        0x800: [(200000, 30000, 12000, 9000), (100000, 20000, 15000, 4000)],
        0x808: [(200000, 25000, 12000, 8000)],
    })
    line = heapcaps_line(esp, FakeGc())
    assert line == ("HEAPCAPS psram=8388608/4000000/3900000/3500000 "
                    "sram=300000/50000/15000/13000 "
                    "dma=200000/25000/12000/8000 gc=3145728/812000/2")


def test_the_heapcaps_line_reads_what_the_heap_holds_before_collecting():
    """A split heap gives an area back only when a sweep empties it, so a
    collect ahead of gc.areas() would report what the heap held after the
    word's own collect. Held first; then the collect that makes `live` mean
    live."""
    g = FakeGc()
    heapcaps_line(FakeEsp32({}), g)
    assert g.calls == ["areas", "collect", "mem_alloc"]


def test_a_figure_the_board_cannot_read_is_a_dash_and_never_a_zero():
    """A host has no esp32 module; a board with no PSRAM has no region with
    those caps; a build without the gc meters patch has no gc.areas(); CPython
    has no gc.mem_alloc(). Each is absence, and each says so in its own slot."""
    assert heapcaps_line(None, None) == \
        "HEAPCAPS psram=- sram=- dma=- gc=-/-/-"
    esp = FakeEsp32({0x800: [(300000, 50000, 15000, 13000)],
                     0x808: [(200000, 25000, 12000, 8000)]})
    line = heapcaps_line(esp, FakeGc(has_areas=False))
    assert line.startswith("HEAPCAPS psram=- sram=300000/")
    assert line.endswith(" gc=-/812000/-")

    class Raises:
        def idf_heap_info(self, caps):
            raise OSError(caps)
    assert heapcaps_line(Raises(), None).startswith(
        "HEAPCAPS psram=- sram=- dma=- ")


def test_heapcaps_is_a_dev_channel_word(capsys):
    """On the host: no esp32, and CPython's gc has neither meter."""
    ws, ch = make()
    ch.run(ws, "heapcaps")
    out = capsys.readouterr().out.splitlines()
    assert "HEAPCAPS psram=- sram=- dma=- gc=-/-/-" in out


def test_each_subsystem_registers_its_words():
    """The words are registered per subsystem (docs/kernel_survival_2026-10.md
    section 2 item 3), so a pass edits its own table and never the reader's."""
    from runtime import devch_audio, devch_input, devch_links
    assert sorted(devch_input.WORDS) == ["drag", "swipe", "tap"]
    assert sorted(devch_audio.WORDS) == ["hush", "vol"]
    assert sorted(devch_links.WORDS) == ["link", "moy-del", "moy-put",
                                         "moy-rescan", "moy-run", "moy?",
                                         "recv", "web"]
    _ws, ch = make()
    assert ch.words == dict(devch_input.WORDS, **devch_audio.WORDS,
                            **devch_links.WORDS)


def test_a_short_swipe_falls_through_to_the_extras():
    seen = []
    _ws, ch = make(extra={"swipe": lambda ws, parts, line: seen.append(line)})
    ch.run(_ws, "swipe 1 2")
    assert seen == ["swipe 1 2"]


def test_kstop_hands_the_kernel_its_count_and_leaves_the_console(monkeypatch,
                                                                 capsys):
    """`kstop N` is the kernel's teardown guard (#224): the kernel runs N soft
    resets, so the word hands it N and the console leaves by SystemExit, the
    exit the VM service turns into a soft reset."""
    import sys
    import types
    asked = []

    def kstop(n):
        asked.append(n)
        raise SystemExit

    monkeypatch.setitem(sys.modules, "moy_kernel", types.SimpleNamespace(kstop=kstop))
    ws, ch = make()
    try:
        ch.run(ws, "kstop 3")
    except SystemExit:
        pass
    else:
        raise AssertionError("the console stayed")
    assert asked == [3] and "REMOTE kstop 3" in capsys.readouterr().out
