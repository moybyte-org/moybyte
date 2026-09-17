"""`P4Compositor`, EXECUTED (`device/dsi_panel.py`, bound through
`firmware/esp32_p4_wifi6_touch_lcd_7b/modules/p4_display.py`).

The P4's compositor is the async-overlap lever (#58) -- the deferred composite,
the drag stamp-defer, the triple-framebuffer rotation and the fences that hold
them together. `tests/test_banded_panel.py` is the sibling for the two S3
boards; this is the same argument for the board whose panel SCANS.

`p4_display` is ordinary Python with every hardware module imported lazily, so
it loads from its path against the stubs below and runs exactly as it does on
glass. It is loaded BY PATH rather than through the board's `modules/`
directory: that directory is gitignored staging, so a fresh checkout has only
the six tracked files in it and the `from ticks import ...` ladder's second rung
(`runtime.ticks`) is what resolves here.
"""

import contextlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
P4_MODULES = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b" / "modules"
DEVICE = ROOT / "device"

from runtime import device_boot                                    # noqa: E402
from runtime.dev_channel import _remote_state                      # noqa: E402


# -- the doubles ---------------------------------------------------------------


class FB:
    """A framebuffer with an identity, so a test can say WHICH buffer was
    drawn into and which one reached scan-out."""

    def __init__(self, i):
        self.i = i


class FakeDsi:
    """`moy_dsi`'s shape. `shown` is the scan-out history IN ORDER, which is
    where a deferred-present bug shows up: an older frame after a newer."""

    WIDTH, HEIGHT = 1024, 600

    def __init__(self, n=3):
        self._n = n
        self._fbs = [FB(i) for i in range(n)]
        self.shown = []
        self.flushes = 0

    def init(self):
        pass

    def nfbs(self):
        return self._n

    def fb(self, i=0):
        return self._fbs[i]

    def show(self, i):
        self.shown.append(i)

    def flush(self):
        self.flushes += 1

    def underruns(self):
        return 0


class FakePpa:
    """`moy_ppa`'s fence surface. `done` is settable so the non-blocking
    "stamp" path can be driven both ways."""

    def __init__(self, done=True):
        self.syncs = 0
        self.kicks = 0
        self.timeouts = 0
        self.done_flag = done

    def sync(self):
        self.syncs += 1

    def done(self):
        return self.done_flag

    def blit_async(self, *a):
        self.kicks += 1

    def stats(self):
        # (submitted, done, timeouts) -- modmoy_ppa.c
        return (self.kicks, self.kicks, self.timeouts)


class FakeGfx:
    def fill(self, *a):
        pass


class FakeCanvas:
    """Just enough for console.draw_splash -- the same surface
    tests/test_device_boot.py uses."""

    w, h = 1024, 600

    def __init__(self):
        self.syncs = 0

    def sync_back(self):
        self.syncs += 1

    def cls(self, c=0):
        pass

    def spr(self, *a, **k):
        pass

    def print(self, *a, **k):
        pass

    def rect(self, *a, **k):
        pass

    def rectb(self, *a, **k):
        pass


class FakeWM:
    _stack = ["home"]


class _FakeCarts:
    """`ws.carts` narrowed to the one member `_remote_state` reads (#209
    landing C): the roster is a plain attribute on the collaborator now."""

    def __init__(self):
        self.all = []


class FakeWS:
    """The `_remote_state` surface, plus a compositor."""

    def __init__(self, comp=None):
        self.wm = FakeWM()
        self.comp = comp
        self.screen = "home"
        self.wifi = None
        self.carts = _FakeCarts()
        self._apps = ()
        self._psave_ms = 0
        self._psave_asleep = False


@contextlib.contextmanager
def p4_display(dsi, ppa, gfx=None):
    """Load `p4_display` fresh against stubbed native modules.

    Fresh every time on purpose: `set_backlight` caches its Pin in a module
    global, so a shared import would carry one test's pin into the next.
    """
    keys = ("p4_display", "dsi_panel", "moy_dsi", "moy_ppa", "moy_gfx", "machine")
    saved = {k: sys.modules.get(k) for k in keys}
    machine = types.ModuleType("machine")
    machine.Pin = _FakePin
    sys.modules["moy_dsi"] = dsi
    sys.modules["moy_ppa"] = ppa
    sys.modules["machine"] = machine
    if gfx is None:
        sys.modules.pop("moy_gfx", None)
    else:
        sys.modules["moy_gfx"] = gfx
    # The compositor BODY is device/dsi_panel.py (one copy for both P4 boards
    # since 2026-09-06); the board file binds its backlight to it. Load the
    # body first under the flat device name the board file imports, then the
    # board file, so `mod.P4Compositor()` is exactly what the board constructs.
    dspec = importlib.util.spec_from_file_location(
        "dsi_panel", DEVICE / "dsi_panel.py")
    dmod = importlib.util.module_from_spec(dspec)
    sys.modules["dsi_panel"] = dmod
    spec = importlib.util.spec_from_file_location(
        "p4_display", P4_MODULES / "p4_display.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["p4_display"] = mod
    try:
        dspec.loader.exec_module(dmod)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class _FakePin:
    OUT = 1

    def __init__(self, gpio, mode=None, value=None):
        self.gpio = gpio
        self.level = value

    def value(self, v):
        self.level = v


class StepTicks:
    """A monotonic microsecond clock that advances a fixed step per READ.

    The fences under test wrap a stubbed `moy_ppa.sync()` that costs no real
    time, so a wall clock would record 0us and every "the cost is recorded"
    assertion would pass for the wrong reason.
    """

    def __init__(self, step=1000):
        self.t = 0
        self.step = step

    def us(self):
        self.t += self.step
        return self.t

    @staticmethod
    def diff(a, b):
        return a - b


@pytest.fixture
def stepped(monkeypatch):
    def install(mod, step=1000):
        clock = StepTicks(step)
        # The fences read the clock through dsi_panel's globals (the body),
        # whichever board file `mod` is.
        target = sys.modules.get("dsi_panel", mod)
        monkeypatch.setattr(target, "_ticks_us", clock.us)
        monkeypatch.setattr(target, "_ticks_diff", StepTicks.diff)
        return clock
    return install


def build(n=3, done=True, gfx=True):
    dsi, ppa = FakeDsi(n), FakePpa(done)
    return dsi, ppa, p4_display(dsi, ppa, FakeGfx() if gfx else None)


# -- sync(): the contract fence ------------------------------------------------


def test_sync_is_free_when_nothing_is_in_flight():
    """A full opaque paint leaves no PPA op flying and the DSI needs no drain,
    so `sync()` must cost nothing there -- otherwise the T-Deck's
    session-bracket idiom is unaffordable on this board."""
    dsi, ppa, ctx = build()
    with ctx as mod:
        comp = mod.P4Compositor()
        comp.flush()
        before = list(dsi.shown)
        comp.sync()
        assert ppa.syncs == 0
        assert dsi.shown == before


def test_sync_drains_the_pending_composite_and_shows_it():
    """After a deferred composite the board HAS DMA in flight and a frame
    queued, and sync() owes the caller both."""
    dsi, ppa, ctx = build()
    with ctx as mod:
        comp = mod.P4Compositor()
        comp._composite_pending = True
        comp.flush()
        assert comp._busy3 and comp._pend3
        comp.sync()
        assert ppa.syncs == 1
        assert comp._busy3 == [] and comp._pend3 == []
        assert dsi.shown[-1] == 1


def test_sync_fences_a_dropped_show_whose_dma_still_flies():
    """`_pend3` empty is NOT "idle": a full opaque paint drops the queued show
    and leaves `_busy3` armed on purpose, because the DMA is still reading that
    buffer."""
    dsi, ppa, ctx = build()
    with ctx as mod:
        comp = mod.P4Compositor()
        comp._composite_pending = True
        comp.flush()
        comp.flush()                       # obsoletes the queued show
        assert comp._pend3 == [] and comp._busy3
        comp.sync()
        assert ppa.syncs == 1
        assert comp._busy3 == []


def test_sync_drains_the_two_buffer_degrade_too():
    """The `n == 2` lane (an older moy_dsi build) keeps its pending in
    `_pending`, not `_busy3`, so a `_busy3`-only fence answers wrong here."""
    dsi, ppa, ctx = build(n=2)
    with ctx as mod:
        comp = mod.P4Compositor()
        comp._composite_pending = True
        comp.flush()
        assert comp._pending is not None
        comp.sync()
        assert ppa.syncs == 1
        assert comp._pending is None


def test_the_backlight_gate_is_a_REAL_caller_of_sync():
    """`device_boot.DeviceBoot.note` fences before lighting the panel (#45), so
    sync() has a real caller. Asserted as an ORDER, not a call count: the fence
    is worth nothing if it lands after the backlight."""
    dsi, ppa, ctx = build()
    with ctx as mod:
        comp = mod.P4Compositor()
        seen = []

        def set_backlight(on):
            seen.append((on, ppa.syncs, list(dsi.shown)))

        boot = device_boot.DeviceBoot(FakeCanvas(), comp,
                                      set_backlight=set_backlight,
                                      label="Moybyte P4")
        comp._composite_pending = True
        boot.note("loading")
        assert boot.lit, "the splash never composed -- the gate was not reached"
        on, syncs_at_light, shown_at_light = seen[-1]
        assert on is True
        assert syncs_at_light == 1
        assert shown_at_light[-1] == 1


# -- the meters ----------------------------------------------------------------


def test_a_fresh_compositor_reports_seven_zeroes():
    _dsi, _ppa, ctx = build()
    with ctx as mod:
        assert mod.P4Compositor().overlap_stats() == (0, 0, 0, 0, 0, 0, 0)


def test_every_deferred_frame_is_counted():
    """The denominator: without it, "2 fences" has no scale."""
    _dsi, _ppa, ctx = build()
    with ctx as mod:
        comp = mod.P4Compositor()
        for _ in range(3):
            comp._composite_pending = True
            comp.flush()
            comp.present_pending()
        assert comp.overlap_stats()[0] == 3


def test_a_full_paint_that_drops_a_queued_show_counts_it_obsolete():
    """A frame composited and never seen."""
    dsi, _ppa, ctx = build()
    with ctx as mod:
        comp = mod.P4Compositor()
        comp._composite_pending = True
        comp.flush()
        comp.flush()
        assert comp.overlap_stats()[1] == 1
        assert 1 not in dsi.shown, (
            "the dropped frame reached the glass anyway -- an older frame after "
            "a newer one is the flash this drop exists to prevent")


def test_the_blocking_reuse_fence_is_counted_and_timed(stepped):
    """This board's `idle`/`gaps`: the flush blocked because the next paint
    target still had DMA in flight. One per deferred frame means the third
    framebuffer buys nothing."""
    _dsi, ppa, ctx = build()
    with ctx as mod:
        stepped(mod)
        comp = mod.P4Compositor()
        for _ in range(3):                 # three deferrals, no present between
            comp._composite_pending = True
            comp.flush()
        deferred, _obs, fences, fence_us = comp.overlap_stats()[:4]
        assert deferred == 3
        assert fences == 1
        assert fence_us > 0
        assert ppa.syncs == 1


def test_the_reuse_fence_stays_unarmed_while_presents_keep_up():
    """The shipped steady state: a present runs every loop, so the rotation
    hands out a free buffer and the fence is never reached."""
    _dsi, ppa, ctx = build()
    with ctx as mod:
        comp = mod.P4Compositor()
        for _ in range(6):
            comp._composite_pending = True
            comp.flush()
            comp.present_pending()
        assert comp.overlap_stats()[2] == 0


def test_the_blocking_game_fence_is_timed(stepped):
    """The blind spot: this runs inside FrameLoop's UNTIMED present() hook --
    outside `ws._flush_ms` and unattributed inside `busy=`."""
    _dsi, ppa, ctx = build()
    with ctx as mod:
        stepped(mod)
        comp = mod.P4Compositor()
        comp._composite_pending = True
        comp.flush()
        comp.present_pending()
        game_n, game_us = comp.overlap_stats()[4:6]
        assert (game_n, ppa.syncs) == (1, 1)
        assert game_us > 0


def test_the_two_buffer_degrade_times_its_fence_too(stepped):
    _dsi, ppa, ctx = build(n=2)
    with ctx as mod:
        stepped(mod)
        comp = mod.P4Compositor()
        comp._composite_pending = True
        comp.flush()
        comp.present_pending()
        assert comp.overlap_stats()[4] == 1
        assert comp.overlap_stats()[5] > 0


def test_a_stamp_pending_never_blocks_and_is_no_game_fence(stepped):
    """The stamp's SOURCE is frozen for the gesture, so its show can wait for
    the next loop. Billing that wait as a game fence would put the lever's
    cheapest path into the meter that measures its dearest."""
    _dsi, ppa, ctx = build(done=False)
    with ctx as mod:
        stepped(mod)
        comp = mod.P4Compositor()
        comp._stamp_pending = (FB(9), 8, 8, 0, 0, FB(8), 8, 8)
        comp.flush()
        assert comp._pend3[0][1] == "stamp"
        comp.present_pending()             # DMA still flying
        assert (ppa.syncs, comp.overlap_stats()[4]) == (0, 0)
        ppa.done_flag = True
        comp.present_pending()
        assert ppa.syncs == 1
        assert comp.overlap_stats()[4] == 0, (
            "the ~free done()-gated drain was billed as a blocking fence")


def test_a_frame_with_both_a_stamp_and_a_game_composite_fences_blocking(stepped):
    """The kind is decided by the frame's WEAKEST guarantee. The stamp kick
    sets the same `_composite_pending` the game composite does, so filing the
    entry on `stamp_kicked` alone downgraded a real game composite to the
    non-blocking done() gate -- and the cart's next _draw overwrites the canvas
    that DMA is reading."""
    _dsi, ppa, ctx = build(done=False)
    with ctx as mod:
        stepped(mod)
        comp = mod.P4Compositor()
        comp._composite_pending = True                    # blit_game(defer=True)
        comp._stamp_pending = (FB(9), 8, 8, 0, 0, FB(8), 8, 8)
        comp.flush()
        assert comp._pend3[0][1] == "game"
        comp.present_pending()
        assert ppa.syncs == 1, "the game composite got the stamp's free gate"
        game_n, game_us = comp.overlap_stats()[4:6]
        assert (game_n, game_us > 0) == (1, True)


def test_a_game_queued_BEHIND_a_stamp_still_fences_blocking(stepped):
    """`_pend3` grows past one entry exactly when the non-blocking gate held a
    present back, so the head is a "stamp" and the game sits behind it."""
    _dsi, ppa, ctx = build(done=False)
    with ctx as mod:
        stepped(mod)
        comp = mod.P4Compositor()
        comp._stamp_pending = (FB(9), 8, 8, 0, 0, FB(8), 8, 8)
        comp.flush()
        comp.present_pending()                            # DMA still flying
        assert (ppa.syncs, comp._pend3[0][1]) == (0, "stamp")
        comp._composite_pending = True                    # next frame: a game
        comp.flush()
        assert [k for _fb, k in comp._pend3] == ["stamp", "game"]
        comp.present_pending()
        assert ppa.syncs == 1
        assert comp.overlap_stats()[4] == 1


def test_a_stamp_only_queue_keeps_its_free_gate(stepped):
    """The upgrade is one-way: a frame with no game composite must not start
    paying the blocking fence."""
    _dsi, ppa, ctx = build(done=False)
    with ctx as mod:
        stepped(mod)
        comp = mod.P4Compositor()
        for _ in range(2):                 # two entries queued, both "stamp"
            comp._stamp_pending = (FB(9), 8, 8, 0, 0, FB(8), 8, 8)
            comp.flush()
            comp.present_pending()
        assert [k for _fb, k in comp._pend3] == ["stamp", "stamp"]
        assert (ppa.syncs, comp.overlap_stats()[4]) == (0, 0)


# -- where the numbers are readable --------------------------------------------


def test_state_carries_the_whole_overlap_tuple():
    """`state` is the machine-readable route (tests/test_p4_on_glass.py reads
    it), and the only one: the P4 stages no `device_diag`, so it has no PUMP
    line to hang these on."""
    _dsi, _ppa, ctx = build()
    with ctx as mod:
        comp = mod.P4Compositor()
        comp._composite_pending = True
        comp.flush()
        st = _remote_state(FakeWS(comp))
        assert st["ppa"] == list(comp.overlap_stats())
        assert st["ppa"][0] == 1


def test_state_reports_ppa_as_None_on_a_board_with_no_overlap():
    """None, not 0 -- the `fold` rule: all-zeroes is also what a live overlap
    that never deferred looks like."""
    assert _remote_state(FakeWS(None))["ppa"] is None
    assert "pump_err" not in _remote_state(FakeWS(None))


# The PERF line's format, its %-count and its `-`-never-0 marker moved to
# tests/test_device_boot.py with the sampler itself (#206 item 2): the line is
# device_boot.PerfSampler now, so those assertions can EXECUTE the emitter
# instead of parsing a print out of this board's source. What stays a P4
# question -- that the overlap tuple this file exercises is what the line's
# ppa=/fence_ms= fields carry -- is pinned there against this board's own
# declaration.


# -- the ROTATED compositor: a landscape desk on portrait glass -----------------
#
# device/dsi_panel.RotatedCompositor (the Guition P4, 2026-09-06). Executed
# against the same doubles: a portrait FakeDsi, a FakePpa that RECORDS every
# rotate so the tests can say which buffer got which rect, at what angle.


class PortraitDsi(FakeDsi):
    WIDTH, HEIGHT = 800, 1280


class RotatingPpa(FakePpa):
    def __init__(self):
        FakePpa.__init__(self, done=True)
        self.rotates = []          # (dst, dx, dy, src, sx, sy, w, h, angle)
        self.direct = []           # (dst, dx, dy, src, sw, sh, scale, angle)
        self.nbs = []              # the nb flag of every rotate/rotate_scale
        self.wbs = []              # (dst, wb) of every rotate/rotate_scale
        self.waits = []            # every wait(keep)

    def init(self):
        return True

    def rotate(self, dst, dw, dh, dx, dy, src, sw, sh, sx, sy, w, h, angle,
               nb=False, wb=True):
        self.rotates.append((dst, dx, dy, src, sx, sy, w, h, angle))
        self.nbs.append(nb)
        self.wbs.append((dst, wb))

    def rotate_scale(self, dst, dw, dh, dx, dy, src, sw, sh, scale, angle,
                     nb=False, wb=True):
        self.direct.append((dst, dx, dy, src, sw, sh, scale, angle))
        self.nbs.append(nb)
        self.wbs.append((dst, wb))

    def wait(self, keep):
        self.waits.append(keep)
        return True

    # The SRAM-bounce rotate: `bands` transactions per block (1 keeps every
    # older count honest; a test sets more to check the op accounting), 0 for
    # a full queue this once, -1 to say the pipeline is not to be had.
    bands = 1

    stalls = 0

    def rotate_bounce(self, dst, dw, dh, dx, dy, src, sw, sh, sx, sy, w, h,
                      angle, nb=False):
        self.rotates.append((dst, dx, dy, src, sx, sy, w, h, angle))
        self.nbs.append(nb)
        self.wbs.append((dst, False))
        return self.bands

    def rotate_bounce_stats(self):
        # modmoy_ppa.c's tuple, in its order.
        return (0, 0, 0, 0, 0, 0, self.stalls, 0, False, False)


@contextlib.contextmanager
def rotated(angle=90):
    dsi, ppa = PortraitDsi(3), RotatingPpa()
    keys = ("dsi_panel", "moy_dsi", "moy_ppa", "moy_gfx", "moy_alloc")
    saved = {k: sys.modules.get(k) for k in keys}
    sys.modules["moy_dsi"] = dsi
    sys.modules["moy_ppa"] = ppa
    sys.modules["moy_gfx"] = FakeGfx()
    sys.modules.pop("moy_alloc", None)          # -> the bytearray fallback
    spec = importlib.util.spec_from_file_location("dsi_panel", DEVICE / "dsi_panel.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dsi_panel"] = mod
    try:
        spec.loader.exec_module(mod)
        lit = []
        comp = mod.RotatedCompositor(lit.append, angle=angle)
        comp.strip_h = 0                          # the strip has its own test
        yield mod, comp, dsi, ppa, lit
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


GAME = bytearray(4)          # the game canvas double (identity is what matters)


def step(comp):
    """One loop frame: flush, then the present the loop runs before the next
    paint -- every frame's show is deferred to it now."""
    comp.flush()
    comp.present_pending()


def game(comp, ox=100, oy=50, quiet=True, direct=True, painted=None):
    """Register a 320x240 game composite at 3x like the canvas does."""
    painted = [] if painted is None else painted
    comp.mark_game(GAME, 320, 240, ox, oy, 2, lambda: painted.append(1),
                   lambda: quiet, direct)
    return painted


def test_rotate_rect_maps_the_landscape_corners_onto_portrait_glass():
    from device.dsi_panel import rotate_rect
    lw, lh = 1280, 800
    # 90 CCW: landscape top-left -> portrait bottom-left, top-right -> top-left.
    assert rotate_rect(0, 0, 1, 1, 90, lw, lh) == (0, 1279, 1, 1)
    assert rotate_rect(1279, 0, 1, 1, 90, lw, lh) == (0, 0, 1, 1)
    assert rotate_rect(0, 799, 1, 1, 90, lw, lh) == (799, 1279, 1, 1)
    # a rect's width and height swap, and the whole frame maps onto the whole panel
    assert rotate_rect(0, 0, lw, lh, 90, lw, lh) == (0, 0, 800, 1280)
    assert rotate_rect(100, 50, 640, 480, 90, lw, lh) == (50, 1280 - 100 - 640, 480, 640)
    # 270: landscape top-left -> portrait top-right.
    assert rotate_rect(0, 0, 1, 1, 270, lw, lh) == (799, 0, 1, 1)
    assert rotate_rect(0, 0, lw, lh, 270, lw, lh) == (0, 0, 800, 1280)
    with pytest.raises(ValueError):
        rotate_rect(0, 0, 1, 1, 180, lw, lh)


def test_the_rotated_compositor_is_landscape_over_a_portrait_panel():
    with rotated() as (mod, comp, dsi, ppa, lit):
        assert comp.size() == (1280, 800)
        assert comp.framebuffer() is comp.back_buffer()
        assert len(comp.framebuffer()) == 1280 * 800 * 2
        assert comp.rotated is True and comp.retained_frames == 2
        assert lit == [False], "dark until the first composed frame"
        assert dsi.shown == [0]


def test_a_full_frame_rotates_the_whole_paint_buffer_and_ping_pongs():
    with rotated() as (mod, comp, dsi, ppa, lit):
        p0 = comp.framebuffer()
        comp.flush()
        assert dsi.shown == [0], "queued, not shown: the show is the present's"
        assert ppa.rotates == [(dsi.fb(1), 0, 0, p0, 0, 0, 1280, 800, 90)]
        assert ppa.nbs == [True]
        assert comp.framebuffer() is not p0, "the next frame paints the other buffer"
        comp.present_pending()
        assert ppa.waits == [1] and dsi.shown == [0, 1]
        p1 = comp.framebuffer()
        step(comp)
        assert dsi.shown == [0, 1, 0]
        assert ppa.rotates[-1][0] is dsi.fb(0) and ppa.rotates[-1][3] is p1
        assert comp.framebuffer() is p0
        assert comp.rotate_stats()[0] == 2          # two full frames


def test_a_quiet_game_after_a_change_is_full_once_then_direct():
    """Ping-pong: the buffer a rect frame lands in missed the last full frame,
    so the first quiet game frame after a change composites into the paint
    buffer and rotates it whole; the second lands in a buffer whose only
    stale rect IS the game rect -- ONE scale+rotate straight from the game
    canvas, and the paint buffer is not touched."""
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)                                     # full -> fb1
        painted = game(comp)
        step(comp)                                     # fb0 missed a full: full again
        assert painted == [1]
        assert ppa.rotates[-1][5:] == (0, 1280, 800, 90)
        assert comp.rotate_stats()[0] == 2 and comp.rotate_stats()[2] == 0
        painted = game(comp)
        comp.flush()                                   # fb1: stale == the rect
        assert painted == [], "a quiet direct frame never touches the paint buffer"
        assert len(ppa.direct) == 1
        dst, dx, dy, src, sw, sh, scale, angle = ppa.direct[0]
        assert dst is dsi.fb(1) and src is comp._scratch
        assert (sw, sh, scale, angle) == (320, 240, 2, 90)
        assert (dx, dy) == (50, 1280 - 100 - 640)
        # ...from a 1:1 copy of the game canvas queued just before it
        gcopy = ppa.rotates[-1]
        assert gcopy[0] is comp._scratch and gcopy[3] is GAME and gcopy[8] == 0
        assert ppa.nbs[-2:] == [True, True]
        assert comp.rotate_stats()[2] == 1 and comp.rotate_stats()[4] == 0
        assert dsi.shown == [0, 1, 0], "the show is deferred to the present"
        comp.present_pending()
        assert ppa.waits[-1] == 1 and dsi.shown == [0, 1, 0, 1]
        painted = game(comp)
        comp.flush()                                   # fb0 likewise
        comp.present_pending()
        assert painted == [] and len(ppa.direct) == 2
        assert comp.rotate_stats()[2] == 2 and comp.rotate_stats()[4] == 0
        assert dsi.shown == [0, 1, 0, 1, 0]
        assert comp.async_stats()[:3] == (4, 4, 0), "every frame defers its show now"


def test_a_frame_that_drew_anything_else_paints_and_rotates_whole():
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)
        game(comp)
        step(comp)
        game(comp)
        step(comp)                                     # now converged: direct
        assert len(ppa.direct) == 1
        painted = game(comp, quiet=False)              # the gates moved
        comp.flush()
        assert painted == [1]
        assert ppa.rotates[-1][5:] == (0, 1280, 800, 90)
        assert len(ppa.direct) == 1


def test_crisp_mode_composites_into_the_paint_buffer_then_rotates_the_rect():
    with rotated() as (mod, comp, dsi, ppa, lit):
        comp.BOUNCE_MIN_PX = 64 * 1024      # the mechanism, on a block this size
        step(comp)
        game(comp, direct=False)
        step(comp)
        painted = game(comp, direct=False)
        src = comp.framebuffer()
        comp.flush()
        assert painted == [1]
        assert ppa.direct == []
        assert ppa.rotates[-1][3] is src
        assert ppa.rotates[-1][4:] == (0, 50, 1280, 480, 90), "bounced: full rows"
        assert comp.rotate_stats()[2] == 1


def test_the_chrome_strip_rides_every_quiet_frame():
    with rotated() as (mod, comp, dsi, ppa, lit):
        comp.strip_h = 18
        step(comp)
        game(comp)
        step(comp)
        game(comp)
        comp.flush()                                   # direct + the strip
        strip = [r for r in ppa.rotates if r[4:] == (0, 0, 1280, 18, 90)]
        assert len(strip) == 1 and strip[0][0] is dsi.fb(1)
        assert strip[0][1:3] == (0, 0)                 # 90 CCW: the top bar lands at the panel's left


def test_a_stale_rect_the_new_frame_does_not_cover_is_copied_from_the_front():
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)                                     # full -> fb1
        game(comp)
        step(comp)                                     # full -> fb0 (missed one)
        game(comp)
        step(comp)                                     # direct -> fb1
        # The game window MOVED (a windowed drag would be a full frame; a
        # popup-sized change is the shape): fb0 lacks the old rect the last
        # frame put into fb1 -> copied 1:1 from fb1 first.
        game(comp, ox=0, oy=0)
        comp.flush()
        copy = [r for r in ppa.rotates if r[8] == 0 and r[3] is not GAME]
        assert len(copy) == 1
        dst, dx, dy, src, sx, sy, w, h, angle = copy[0]
        assert dst is dsi.fb(0) and src is dsi.fb(1)
        assert (dx, dy, w, h) == (sx, sy, w, h) == (50, 1280 - 100 - 640, 480, 640)
        assert ppa.direct[-1][1:3] == (0, 1280 - 640)
        assert comp.rotate_stats()[4] == 1


def test_a_trail_of_moved_rects_costs_one_copy_a_frame_never_a_full():
    """Ping-pong bounds the bookkeeping by itself: each buffer is visited every
    other frame, so it can only ever lack the ONE rect the frame between
    painted."""
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)
        game(comp, ox=0)
        step(comp)
        n = comp.STALE_LIMIT + 2
        for i in range(n):
            game(comp, ox=i * 20)
            step(comp)
            assert all(len(st) <= 1 for st in comp._stale if st is not None)
        assert comp.rotate_stats()[0] == 2
        assert comp.rotate_stats()[2] == n
        assert comp.rotate_stats()[4] == n - 1      # the first repeats its predecessor


def test_set_angle_forces_every_buffer_current_the_other_way_up():
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)
        game(comp)
        step(comp)
        game(comp)
        step(comp)                                     # direct
        comp.set_angle(270)
        game(comp)
        step(comp)
        assert ppa.rotates[-1][5:] == (0, 1280, 800, 270)
        game(comp)
        step(comp)
        assert ppa.rotates[-1][5:] == (0, 1280, 800, 270)
        game(comp)
        step(comp)
        assert ppa.direct[-1][7] == 270
        assert ppa.direct[-1][1:3] == (800 - 50 - 480, 100)


def test_present_and_sync_are_inert_once_a_frame_is_shown():
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)
        comp.present_pending()
        comp.sync()
        assert ppa.syncs == 0
        assert len(comp.overlap_stats()) == len(mod.OVERLAP_FIELDS)


# -- damage frames: the WM describes what it painted (the desktop lever) -------


def test_a_described_frame_rotates_its_rects_not_the_whole_buffer():
    """A drag frame: the WM notes the gesture union; the compositor rotates
    that rect (and the bar strip) from the paint buffer instead of 4MB."""
    with rotated() as (mod, comp, dsi, ppa, lit):
        comp.BOUNCE_MIN_PX = 64 * 1024      # the mechanism, on a block this size
        comp.strip_h = 18
        step(comp)                                     # full -> fb1
        comp.note_damage(200, 100, 700, 500)
        step(comp)                                     # fb0 missed a full: full
        assert ppa.rotates[-1][5:] == (0, 1280, 800, 90)
        comp.note_damage(210, 100, 700, 500)
        comp.flush()                                   # fb1: rect frame
        assert comp._pending == 1 and comp._keep == 2, "two ops may fly: the rect and the strip"
        rot = [r for r in ppa.rotates if r[0] is dsi.fb(1) and r[8] == 90]
        # the sibling owed the first union: the rect GREW 10px to cover it.
        # The strip (a plain rotate) goes first, the bounced block last.
        assert [r[4:8] for r in rot[-2:]] == [(0, 0, 1280, 18), (0, 100, 1280, 500)]
        assert ppa.direct == []
        assert comp.damage_stats() == (2, 2, 0, 1)
        assert comp.rotate_stats()[2] == 1 and comp.rotate_stats()[0] == 2


def test_a_described_frame_with_a_game_paints_the_game_and_rotates_its_rect_too():
    """A drag beside a running game window: the game's composite reaches the
    paint buffer (never direct), and its rect is one of the frame's rects."""
    with rotated() as (mod, comp, dsi, ppa, lit):
        comp.BOUNCE_MIN_PX = 64 * 1024      # the mechanism, on a block this size
        step(comp)
        game(comp)
        step(comp)
        game(comp)
        step(comp)                                     # converged: direct
        assert len(ppa.direct) == 1
        painted = game(comp, quiet=True)               # gates unmoved (blit-only WM)...
        comp.note_damage(900, 300, 300, 200)           # ...but the WM says it drew
        comp.flush()
        assert painted == [1], "noted damage means the game is not the only write"
        assert len(ppa.direct) == 1
        rects = [r[4:8] for r in ppa.rotates if r[0] is dsi.fb(0) and r[8] == 90]
        # the small damage rect is a plain rotate and goes first; the game
        # rect (307K px) is bounced and goes last
        assert rects[-2:] == [(900, 300, 300, 200), (0, 50, 1280, 480)]


def test_damage_is_clipped_and_an_empty_rect_is_nothing():
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)
        comp.note_damage(0, 0, 1, 1)
        step(comp)                                     # both buffers current
        comp.note_damage(-50, -20, 100, 60)
        comp.note_damage(1250, 780, 100, 100)
        comp.note_damage(10, 10, 0, 40)
        comp.flush()
        rects = [r[4:8] for r in ppa.rotates if r[8] == 90 and r[7] != 800]
        assert rects == [(0, 0, 50, 40), (1250, 780, 30, 20)]


def test_too_many_rects_take_their_bounding_box_and_a_covered_one_is_dropped():
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)
        comp.note_damage(0, 0, 1, 1)
        step(comp)                                     # both buffers current
        comp.note_damage(0, 0, 100, 100)
        comp.note_damage(10, 10, 20, 20)               # inside the first
        comp.note_damage(300, 300, 50, 50)
        comp.note_damage(600, 600, 50, 50)
        comp.note_damage(800, 100, 50, 50)             # a fourth distinct: bbox
        comp.flush()
        rects = [r[4:8] for r in ppa.rotates if r[8] == 90 and r[7] != 800]
        assert rects == [(0, 0, 1280, 650)]      # bounced: full rows
        assert comp.damage_stats()[:3] == (2, 2, 0)


def test_a_description_dearer_than_the_full_rotate_is_declined():
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)
        comp.note_damage(0, 0, 1, 1)
        step(comp)
        comp.note_damage(0, 0, 1280, 700)              # > 60% of the frame
        comp.flush()
        assert ppa.rotates[-1][5:] == (0, 1280, 800, 90)
        assert comp.damage_stats()[:3] == (1, 1, 1)


def test_a_damage_frame_leaves_the_other_buffer_a_stale_rect_it_copies_next():
    """Ping-pong: the buffer that did not get this frame's rect lacks it, and
    the next frame into that buffer copies it 1:1 before its own rects."""
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)                                     # full -> fb1
        comp.note_damage(100, 100, 200, 200)
        step(comp)                                     # fb0 missed a full: full; fb1 owes the rect
        comp.note_damage(400, 400, 200, 200)
        step(comp)                                     # fb1: copy (100,100) from fb0, rotate (400,400)
        copies = [r for r in ppa.rotates if r[8] == 0]
        assert len(copies) == 1 and copies[0][0] is dsi.fb(1) and copies[0][3] is dsi.fb(0)
        assert copies[0][4:8] == mod.rotate_rect(100, 100, 200, 200, 90, 1280, 800)
        assert comp.rotate_stats()[4] == 1


def test_an_undescribed_frame_after_damage_frames_is_full_and_resets_the_other():
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)
        comp.note_damage(100, 100, 200, 200)
        step(comp)
        comp.note_damage(100, 100, 200, 200)
        step(comp)                                     # -> fb1, a rect frame
        assert comp.rotate_stats()[2] == 1
        step(comp)                                     # -> fb0, nothing noted: full
        assert ppa.rotates[-1][5:] == (0, 1280, 800, 90)
        assert comp._stale[1] is None                  # the other missed a full
        comp.note_damage(100, 100, 200, 200)
        step(comp)                                     # -> fb1: must be full again
        assert ppa.rotates[-1][5:] == (0, 1280, 800, 90)


# -- the async quiet frame -----------------------------------------------------


def test_a_quiet_frame_queues_strip_then_copy_then_rotate_and_presents_later():
    with rotated() as (mod, comp, dsi, ppa, lit):
        comp.strip_h = 18
        step(comp)
        game(comp)
        step(comp)
        game(comp)
        n = len(ppa.rotates)
        comp.flush()                                   # the async direct frame
        tail = ppa.rotates[n:]
        assert [r[4:8] for r in tail][0] == (0, 0, 1280, 18), "the strip first"
        assert tail[-1][3] is GAME and tail[-1][0] is comp._scratch, "then the game copy"
        assert ppa.direct[-1][3] is comp._scratch, "then the rotate from the scratch"
        assert all(ppa.nbs[-3:])
        assert comp._pending == 1 and dsi.shown[-1] == 0
        comp.present_pending()
        assert ppa.waits[-1] == 1 and dsi.shown[-1] == 1 and comp._pending is None


def test_a_flush_that_finds_a_deferred_frame_fences_and_shows_it_first():
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)
        game(comp)
        step(comp)
        game(comp)
        comp.flush()                                   # deferred into fb1
        assert comp._pending == 1
        comp.flush()                                   # a chrome frame, no present between
        assert ppa.syncs == 1
        assert dsi.shown[-1] == 1, "the late show; the full frame into fb0 is queued"
        assert comp._pending == 0
        assert comp.async_stats()[:3] == (4, 2, 1)
        assert ppa.rotates[-1][5:] == (0, 1280, 800, 90)


def test_a_present_whose_rotate_still_flies_leaves_the_show_for_later():
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)
        game(comp)
        step(comp)
        game(comp)
        comp.flush()
        ppa.done_flag = False
        comp.present_pending()
        assert ppa.waits[-1] == 1 and comp._pending == 1 and dsi.shown[-1] == 0
        ppa.done_flag = True
        comp.present_pending()
        assert comp._pending is None and dsi.shown[-1] == 1


def test_sync_drains_a_deferred_frame():
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)
        game(comp)
        step(comp)
        game(comp)
        comp.flush()
        comp.sync()
        assert ppa.syncs == 1 and comp._pending is None and dsi.shown[-1] == 1
        comp.sync()
        assert ppa.syncs == 1


def test_a_ppa_without_wait_keeps_the_blocking_direct_frame():
    with rotated() as (mod, comp, dsi, ppa, lit):
        del RotatingPpa.wait
        try:
            comp._async = hasattr(ppa, "wait")
            assert comp._async is False
            comp.flush()
            game(comp)
            comp.flush()
            game(comp)
            comp.flush()
            assert ppa.direct[-1][3] is GAME and ppa.nbs[-1] is False
            assert comp._pending is None and dsi.shown[-1] == 1
            assert all(nb is False for nb in ppa.nbs), "blocking throughout"
        finally:
            RotatingPpa.wait = lambda self, keep: (self.waits.append(keep), True)[1]


def test_unrotate_rect_inverts_rotate_rect():
    from device.dsi_panel import rotate_rect, unrotate_rect
    lw, lh = 1280, 800
    for angle in (90, 270):
        for r in ((0, 0, 1, 1), (100, 50, 640, 480), (1279, 799, 1, 1), (0, 0, lw, lh)):
            assert unrotate_rect(*rotate_rect(*r, angle, lw, lh), angle, lw, lh) == r


def test_a_drag_grows_the_union_over_the_stale_one_instead_of_copying_it():
    """Frame N's union lands in fb1; fb0 lacks it. Frame N+1's union overlaps
    it almost entirely, so growing N+1's rect by a few px to cover N's beats
    a copy the size of the whole window."""
    with rotated() as (mod, comp, dsi, ppa, lit):
        comp.BOUNCE_MIN_PX = 64 * 1024      # the mechanism, on a block this size
        step(comp)
        comp.note_damage(200, 100, 700, 500)
        step(comp)                                     # fb0: full
        for i in range(1, 6):
            comp.note_damage(200 + 6 * i, 100, 700, 500)
            step(comp)
        copies = [r for r in ppa.rotates if r[8] == 0]
        assert copies == [], "every stale union was swallowed, none copied"
        assert comp.damage_stats()[3] == 5
        # the last rotate is the grown rect: the union plus ONE frame's 6px
        # trail -- the growth never compounds, the sibling owes only real damage
        # (the engine gets the grown rect's ROWS, full width -- the bounce)
        last = [r for r in ppa.rotates if r[8] == 90 and r[7] != 800][-1]
        assert last[4:8] == (0, 100, 1280, 500)
        assert all(st is None or all(r[2:4] == (500, 700) for r in st) for st in comp._stale)
        assert comp.rotate_stats()[4] == 0


def test_a_far_stale_rect_is_still_copied_not_grown_over():
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)
        comp.note_damage(0, 0, 200, 200)
        step(comp)
        comp.note_damage(0, 0, 200, 200)
        step(comp)                                     # fb1 rect; fb0 owes it
        comp.note_damage(1000, 600, 200, 200)          # far corner
        step(comp)                                     # fb0: copy the old, rotate the new
        copies = [r for r in ppa.rotates if r[8] == 0]
        assert len(copies) == 1 and comp.damage_stats()[3] == 0


# -- the paint ping-pong and the drag stamp (2026-09-08, the desk lever) --------

WIN = bytearray(8)           # a window buffer double


def test_a_frame_is_painted_into_the_other_buffer_while_the_last_one_flies():
    """The pipeline: frame N's ops read paint buffer A; frame N+1 paints B;
    the present before N+2 fences A's readers (everything older than N+1's
    ops) and only then does the console re-point at A."""
    with rotated() as (mod, comp, dsi, ppa, lit):
        a = comp.framebuffer()
        step(comp)                                     # frame 1 from A
        b = comp.framebuffer()
        assert b is not a
        comp.note_damage(10, 10, 100, 100)
        comp.flush()                                   # frame 2 from B, two ops (rect... no strip: 0)
        assert comp.framebuffer() is a
        assert comp._keep == 1, "one op this frame (strip_h is 0 in the fixture)"
        comp.present_pending()
        assert ppa.waits[-1] == 1, "wait until at most this frame's ops fly"
        assert dsi.shown[-1] == 0
        # frame 3 paints A while frame 2's rect (from B) may still fly
        comp.note_damage(10, 10, 100, 100)
        comp.flush()
        assert ppa.rotates[-1][3] is a


def test_the_deferred_window_stamp_is_the_frames_first_op_into_the_paint_buffer():
    with rotated() as (mod, comp, dsi, ppa, lit):
        comp.BOUNCE_MIN_PX = 64 * 1024      # the mechanism, on a block this size
        comp.strip_h = 18
        step(comp)
        comp.note_damage(0, 0, 1, 1)
        step(comp)                                     # both scan buffers current
        paint = comp.framebuffer()
        comp._stamp_pending = (paint, 1280, 800, 300, 200, WIN, 512, 480)
        comp.note_damage(298, 198, 519, 487)
        n = len(ppa.rotates)
        comp.flush()
        ops = ppa.rotates[n:]
        assert ops[0][:3] == (paint, 300, 200) and ops[0][3] is WIN
        assert ops[0][4:] == (0, 0, 512, 480, 0), "a 1:1 copy, queued"
        assert ppa.nbs[n] is True
        # the strip first (plain), the window rect last (bounced)
        assert [o[4:8] for o in ops[1:]] == [(0, 0, 1280, 18), (0, 198, 1280, 487)]
        assert all(o[3] is paint for o in ops[1:]), "the rotates read the stamped buffer"
        assert comp._keep == 3 and comp._stamp_pending is None
        assert comp.async_stats()[4] == 1


def test_a_deferred_stamp_makes_a_quiet_game_frame_a_painted_one():
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)
        game(comp)
        step(comp)
        game(comp)
        step(comp)                                     # direct
        assert len(ppa.direct) == 1
        paint = comp.framebuffer()
        painted = game(comp, quiet=True)
        comp._stamp_pending = (paint, 1280, 800, 0, 0, WIN, 64, 64)
        comp.flush()
        assert painted == [1] and len(ppa.direct) == 1


def test_the_canvas_hands_the_rotated_compositor_its_stamp():
    """P4SystemCanvas.blit_strip_async registers the stamp on a rotated
    compositor that can queue (has `wait`), and refuses on one that cannot.
    Driven on a stand-in with the verb's own inputs -- the class's
    constructor wants MicroPython's framebuf."""
    import importlib.util as ilu
    import types

    class Comp:
        rotated = True
        _async = True
        _stamp_pending = None

    class Layer:
        w, h = 512, 480
        _buf = WIN

    spec = ilu.spec_from_file_location("p4_canvas_under_test", DEVICE / "p4_canvas.py")
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    verb = mod.P4SystemCanvas.blit_strip_async
    buf = bytearray(16)
    comp = Comp()
    cv = types.SimpleNamespace(_ppa=object(), _comp=comp, w=1280, h=800,
                               _buf=buf, flush_batch=lambda: None)
    assert verb(cv, Layer(), 300, 200) is True
    assert comp._stamp_pending == (buf, 1280, 800, 300, 200, WIN, 512, 480)
    comp2 = Comp()
    comp2._async = False
    cv2 = types.SimpleNamespace(_ppa=object(), _comp=comp2, w=1280, h=800,
                                _buf=buf, flush_batch=lambda: None)
    assert verb(cv2, Layer(), 300, 200) is False
    assert comp2._stamp_pending is None
    cv3 = types.SimpleNamespace(_ppa=object(), _comp=Comp(), w=1280, h=800,
                                _buf=buf, flush_batch=lambda: None)
    assert verb(cv3, Layer(), 900, 200) is False, "a stamp off the edge stays on the CPU"


def test_only_the_cpu_painted_destination_gets_the_cache_writeback():
    """`wb` is the op's destination cache writeback, and it costs a walk of
    the rows per submit. Only the PAINT buffer is CPU-painted (the drag stamp
    lands beside this frame's chrome); the scan buffers and the game-copy
    scratch are written by the PPA alone, so they skip it."""
    with rotated() as (mod, comp, dsi, ppa, lit):
        paints = {id(comp.framebuffer()), id(comp._paints[1])}
        scan = {id(dsi.fb(0)), id(dsi.fb(1))}
        step(comp)                                  # full rotate -> scan buffer
        assert ppa.wbs and all(wb is False for (_d, wb) in ppa.wbs)

        # A quiet game frame: scratch copy + scale-rotate, and a stale copy
        # between the scan buffers. None of them is CPU-painted.
        game(comp)
        comp.note_damage(10, 10, 40, 40)
        step(comp)
        game(comp)
        step(comp)
        for (dst, wb) in ppa.wbs:
            assert id(dst) not in paints
            assert wb is False, "no CPU writes these -- do not walk their cache"
        assert any(id(d) in scan for (d, _wb) in ppa.wbs)

        # The deferred window stamp, into the paint buffer beside the chrome.
        n = len(ppa.wbs)
        comp._stamp_pending = (comp.framebuffer(), comp._w, comp._h,
                               20, 30, bytearray(4), 8, 8)
        step(comp)
        stamped = ppa.wbs[n]
        assert id(stamped[0]) in paints and stamped[1] is True
        assert all(wb is False for (d, wb) in ppa.wbs[n + 1:]
                   if id(d) not in paints)


def test_a_refused_queued_submit_fences_and_retries_blocking():
    """The driver fails a submit outright on a full queue; the compositor
    fences and resubmits blocking, and counts it."""
    with rotated() as (mod, comp, dsi, ppa, lit):
        step(comp)
        comp.note_damage(0, 0, 1, 1)
        step(comp)
        real = ppa.rotate
        calls = []

        def refusing(*a):
            nb = a[-2]                      # ... rotate(..., angle, nb, wb)
            calls.append(nb)
            if nb is True and len(calls) == 1:
                raise OSError("ppa rotate failed: 1")
            return real(*a)

        ppa.rotate = refusing
        try:
            comp.note_damage(50, 50, 100, 100)
            comp.flush()
        finally:
            ppa.rotate = real
        assert calls[:2] == [True, False], "queued, refused, then blocking"
        assert ppa.syncs == 1
        assert comp.async_stats()[5] == 1


def test_a_clear_is_never_a_quiet_frame():
    """The quiet-frame snapshot (sync_back -> _gates_unchanged) must see a
    cls: it is the one whole-surface write the native gates cannot count --
    moy_ppa.fill on the PPA, a direct moy_gfx.fill on the CPU -- and the
    PLAY world's letterbox is one. Invisible, the bezel frame read as quiet,
    only the game rect reached the scan buffers, and the two of them kept
    different Library pixels in the bezel: the flicker behind a fullscreen
    game (owner, Guition P4, 2026-09-09)."""
    import importlib.util as ilu
    from array import array
    from runtime import host_canvas
    from device.device_canvas import _ST_LEN, _ST_N_FILL, _ST_N_TEXT
    host_canvas.install()
    spec = ilu.spec_from_file_location("p4_canvas_under_test", DEVICE / "p4_canvas.py")
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    comp = host_canvas.HostCompositor(320, 200)
    comp.rotated = True
    cv = mod.P4SystemCanvas(comp, font_scale=1)
    assert cv._ppa is None, "the host has no PPA: cls takes the CPU fill"
    # The host kernel installs no gates; stand the state array in so the
    # snapshot has counters to compare (the device's gates write these).
    cv._gate_state = array("i", bytearray(4 * _ST_LEN))
    assert not cv._gates_unchanged(), "no frame yet: never quiet"
    cv.sync_back()
    assert cv._gates_unchanged()
    cv.cls(0)
    assert not cv._gates_unchanged(), "a clear is a paint-buffer write"
    cv.sync_back()
    assert cv._gates_unchanged()
    cv._gate_state[_ST_N_FILL] += 1                  # a gated rect
    assert not cv._gates_unchanged()
    cv.sync_back()
    cv._gate_state[_ST_N_TEXT] += 1                  # a gated print
    assert not cv._gates_unchanged()
    cv.sync_back()
    lay = cv.new_layer(64, 32)
    lay.cls(3)                                       # a LAYER's clear is its own
    assert cv._gates_unchanged()


def test_a_layer_gives_its_off_heap_buffer_back_on_release():
    """A layer's pixel buffer lives outside the gc heap on a board, so nothing
    collects it: release() is the owner's word. Where the buffer came from
    decides what that means -- an alloc() buffer is freed through the
    registry (and the view neutered), a pool buffer goes back to the pool, a
    gc-heap bytearray is simply dropped. Before this the windowed WM leaked
    ~3.6MB of PSRAM per Library -> CHANGE -> home round on the Guition P4."""
    import sys
    import types
    from device import device_canvas as dc

    class FakeAlloc:
        MEMORY_SPIRAM = 1
        MEMORY_DMA = 2

        def __init__(self):
            self.allocs = []
            self.frees = []

        def alloc(self, n, caps=1):
            buf = memoryview(bytearray(n))
            self.allocs.append((n, caps))
            return buf

        def free(self, view):
            self.frees.append(view)

        def malloc_dma(self, n, caps=1):
            raise AssertionError("alloc() is preferred when the firmware has it")

    fake = FakeAlloc()
    saved = sys.modules.get("moy_alloc")
    saved_bus = sys.modules.get("lcd_bus")
    sys.modules["moy_alloc"] = fake
    sys.modules["lcd_bus"] = None                 # -> ImportError: caps from moy_alloc
    pool = dc._LAYER_POOL
    n = 64 * 32 * 2
    pool.pop(n, None)
    try:
        comp = dc._LayerComp(64, 32, None)
        assert fake.allocs == [(n, 3)], "SPIRAM|DMA, through the registry alloc"
        assert comp.pooled and comp._origin == dc._ORIGIN_ALLOC
        buf = comp._buf
        comp.release()
        assert fake.frees == [buf] and comp._buf is None
        comp.release()                            # idempotent
        assert len(fake.frees) == 1
        # A pool buffer is returned to the pool, never freed.
        pooled = memoryview(bytearray(n))
        pool[n] = [pooled]
        comp2 = dc._LayerComp(64, 32, None)
        assert comp2._buf is pooled and comp2._origin == dc._ORIGIN_POOL
        assert pool[n] == []
        comp2.release()
        assert pool[n] == [pooled] and len(fake.frees) == 1
        pool.pop(n, None)
        # The canvas-level verb reaches the comp; a root (host compositor) ignores it.
        from runtime import host_canvas
        host_canvas.install()
        root = host_canvas.make_canvas(64, 32)
        root.release()                            # no release on HostCompositor
        lay = root.new_layer(64, 32)
        assert lay._comp._origin == dc._ORIGIN_ALLOC
        lay.release()
        assert lay._comp._buf is None and len(fake.frees) == 2
    finally:
        pool.pop(n, None)
        if saved is None:
            sys.modules.pop("moy_alloc", None)
        else:
            sys.modules["moy_alloc"] = saved
        if saved_bus is None:
            sys.modules.pop("lcd_bus", None)
        else:
            sys.modules["lcd_bus"] = saved_bus
    # No allocator at all (the host): a gc-heap bytearray, dropped on release.
    comp3 = dc._LayerComp(64, 32, None)
    assert comp3._origin == dc._ORIGIN_HEAP and not comp3.pooled
    comp3.release()
    assert comp3._buf is None


def test_a_big_block_rotates_through_the_bounce_and_counts_its_bands():
    """A paint-buffer block of BOUNCE_MIN_PX or more goes through
    moy_ppa.rotate_bounce, whose transaction count is what the present must
    fence by; the bar strip stays a plain rotate. A bounce that has no bands
    (0) is never asked again."""
    with rotated() as (mod, comp, dsi, ppa, lit):
        comp.BOUNCE_MIN_PX = 64 * 1024      # the mechanism, on a block this size
        comp.strip_h = 18
        ppa.bands = 5
        step(comp)                                  # full rotate: 5 bands
        assert comp._keep == 5 and comp._bounced == 5
        comp.note_damage(0, 0, 1, 1)
        step(comp)                                  # the sibling's full
        assert comp._bounced == 10
        n = len(ppa.rotates)
        comp.note_damage(10, 20, 400, 300)          # 120K px: bounced
        step(comp)                                  # a damage frame
        new = ppa.rotates[n:]
        big = [r for r in new if r[6] * r[7] >= comp.BOUNCE_MIN_PX]
        assert big, "the damage rect went through the bounce"
        strip = [r for r in new if r[7] == comp.strip_h and r[6] == comp._w]
        assert strip, "the strip is a plain rotate"
        assert comp.async_stats()[6] == 15
        # five bands plus the strip: the 1px stale rect was grown over, not copied
        assert comp._keep == 5 + 1
        ppa.bands = 0                                # the queue is full this once
        n = len(ppa.rotates)
        comp.note_damage(10, 20, 400, 300)
        step(comp)
        assert comp._bounce is not None and comp.async_stats()[5] == 1
        assert len(ppa.rotates) > n, "rotated the plain way this frame"
        ppa.bands = -1                               # never for this picture
        comp.note_damage(10, 20, 400, 300)
        step(comp)
        assert comp._bounce is None
        comp.note_damage(10, 20, 400, 300)
        step(comp)
        assert comp.async_stats()[6] == 15, "no bounce after a refusal"


# -- one field set, two compositors --------------------------------------------
#
# `runtime/perf_line.py` labels the ppa= slots ONCE for every board and
# `tools/p4_perf.py` parses every board with one table, so the two compositors
# in `device/dsi_panel.py` must answer the same question in each slot. The
# rotated one used to answer with its rotate meters -- rect frames under
# `deferred`, copies under `obsolete`, full frames under `fences` -- so the
# Guition P4 printed one counter under two labels and its rect-frame count
# arrived at the ledger as a deferral count. These drive both bodies through
# the same event and read the slot by NAME.


def _slot(comp, name):
    from device.dsi_panel import OVERLAP_FIELDS
    return comp.overlap_stats()[OVERLAP_FIELDS.index(name)]


def test_both_compositors_report_the_same_overlap_field_set():
    from device.dsi_panel import OVERLAP_FIELDS
    assert OVERLAP_FIELDS == ("deferred", "obsolete", "fences", "fence_us",
                              "game_n", "game_us", "timeouts")
    _dsi, _ppa, ctx = build()
    with ctx as mod:
        assert len(mod.P4Compositor().overlap_stats()) == len(OVERLAP_FIELDS)
    with rotated() as (rmod, comp, _dsi2, _ppa2, _lit):
        assert rmod.OVERLAP_FIELDS == OVERLAP_FIELDS
        assert len(comp.overlap_stats()) == len(OVERLAP_FIELDS)


def test_a_deferred_show_counts_under_deferred_on_both():
    """Slot 0 on both: a frame whose scan-out switch was held a loop."""
    _dsi, _ppa, ctx = build()
    with ctx as mod:
        comp = mod.P4Compositor()
        comp._composite_pending = True
        comp.flush()
        assert _slot(comp, "deferred") == 1
    with rotated() as (_rmod, rcomp, _dsi2, _ppa2, _lit):
        rcomp.flush()
        assert _slot(rcomp, "deferred") == 1


def test_the_flush_fence_counts_under_fences_on_both(stepped):
    """Slots 2/3 on both: the blocking fence in flush() that frees the buffer
    the next frame paints, and what it cost."""
    _dsi, ppa, ctx = build()
    with ctx as mod:
        stepped(mod)
        comp = mod.P4Compositor()
        for _ in range(3):                 # three deferrals, no present between
            comp._composite_pending = True
            comp.flush()
        assert (_slot(comp, "fences"), ppa.syncs) == (1, 1)
        assert _slot(comp, "fence_us") > 0
    with rotated() as (rmod, rcomp, _dsi2, rppa, _lit):
        stepped(rmod)
        rcomp.flush()
        rcomp.flush()                      # the first frame's ops outlived a loop
        assert (_slot(rcomp, "fences"), rppa.syncs) == (1, 1)
        assert _slot(rcomp, "fence_us") > 0


def test_the_present_fence_counts_under_game_on_both(stepped):
    """Slots 4/5 on both: the fence inside FrameLoop's UNTIMED present() hook
    -- the one that must land before the cart's tick overwrites the source.
    Nowhere else in the line is it visible."""
    _dsi, _ppa, ctx = build()
    with ctx as mod:
        stepped(mod)
        comp = mod.P4Compositor()
        comp._composite_pending = True
        comp.flush()
        comp.present_pending()
        assert _slot(comp, "game_n") == 1 and _slot(comp, "game_us") > 0
    with rotated() as (rmod, rcomp, _dsi2, _ppa2, _lit):
        stepped(rmod)
        rcomp.flush()
        rcomp.present_pending()
        assert _slot(rcomp, "game_n") == 1 and _slot(rcomp, "game_us") > 0


def test_timeouts_is_the_ppa_counter_on_both():
    """Slot 6 on both: the only sign of a wedged fence, since a fence cannot
    raise where it runs."""
    _dsi, ppa, ctx = build()
    with ctx as mod:
        ppa.timeouts = 4
        assert _slot(mod.P4Compositor(), "timeouts") == 4
    with rotated() as (_rmod, rcomp, _dsi2, rppa, _lit):
        rppa.timeouts = 4
        assert _slot(rcomp, "timeouts") == 4


class RefreshingDsi(PortraitDsi):
    """A portrait panel whose refresh count the test advances by hand: a show
    takes effect only at the next refresh, which is the fact the third scan
    buffer exists for."""

    def __init__(self, n=3):
        PortraitDsi.__init__(self, n)
        self.refresh = 0

    def refreshes(self):
        return self.refresh


@contextlib.contextmanager
def refreshing_rotated(angle=90):
    dsi, ppa = RefreshingDsi(3), RotatingPpa()
    keys = ("dsi_panel", "moy_dsi", "moy_ppa", "moy_gfx", "moy_alloc")
    saved = {k: sys.modules.get(k) for k in keys}
    sys.modules["moy_dsi"] = dsi
    sys.modules["moy_ppa"] = ppa
    sys.modules["moy_gfx"] = FakeGfx()
    sys.modules.pop("moy_alloc", None)
    spec = importlib.util.spec_from_file_location("dsi_panel", DEVICE / "dsi_panel.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dsi_panel"] = mod
    try:
        spec.loader.exec_module(mod)
        comp = mod.RotatedCompositor(lambda on: None, angle=angle)
        comp.strip_h = 0
        yield mod, comp, dsi, ppa
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_a_buffer_still_on_glass_is_never_the_rotate_target():
    """The DPI driver moves the scan-out at the refresh AFTER a show. Until
    that refresh the buffer the show left is still being read by the panel,
    and the ping-pong used to rotate the next frame straight into it -- the
    frame landed on glass mid-scan. With the panel's refresh count the
    compositor knows which buffer is free: the partner once a refresh has
    passed, the third buffer while the partner is still on glass."""
    with refreshing_rotated() as (mod, comp, dsi, ppa):
        dsi.refresh = 1                       # the init show(0) has taken effect
        step(comp)                            # frame A: into the partner
        assert ppa.rotates[-1][0] is dsi.fb(1) and dsi.shown == [0, 1]
        # No refresh since show(1): fb0 is what the panel scans, fb1 is the
        # show waiting for the next refresh -- neither may be written.
        step(comp)                            # frame B
        assert ppa.rotates[-1][0] is dsi.fb(2), "the third buffer, not the one on glass"
        assert dsi.shown == [0, 1, 2] and comp._vsync_waits == 0
        # A refresh passed: show(2) took effect (it superseded show(1), which
        # never reached glass), so fb1 is free again and the ping-pong resumes.
        dsi.refresh = 2
        step(comp)                            # frame C
        assert ppa.rotates[-1][0] is dsi.fb(1) and dsi.shown == [0, 1, 2, 1]
        assert comp._on_glass() == 2
        # Every frame here was a full rotate, and a full frame is a debt the
        # other TWO buffers owe (None: current again only by a full rotate).
        assert comp._stale[0] is None and comp._stale[2] is None


def test_the_rotated_path_reports_obsolete_absent_never_zero():
    """Slot 1 is the one mechanism the rotated path does not have: a deferred
    frame there is fenced and shown LATE, never dropped. None says so; a 0
    would read as a drop counter that is working and quiet."""
    with rotated() as (_rmod, comp, dsi, _ppa, _lit):
        comp.flush()
        comp.flush()
        assert _slot(comp, "obsolete") is None
        assert dsi.shown == [0, 1], "the deferred frame was shown, not dropped"
        assert comp.rotate_stats()[0] == 2, "the rotate meters have their own tuple"


# -- the bounce pipeline's own meters ------------------------------------------


PPA_C = ROOT / "native" / "p4" / "moy_ppa" / "modmoy_ppa.c"

PPA_BOUNCE_FIELDS = ("s_rb_pending", "s_rb_fallbacks", "s_rb_t_fence",
                     "s_rb_t_flag", "s_rb_t_dma", "s_rb_t_submit",
                     "s_rb_stalls", "s_rb_cb_flagged",
                     "s_rb_busy[0]", "s_rb_busy[1]")


def _ppa_verb_body(name):
    src = PPA_C.read_text(encoding="utf-8")
    head = src.index("static mp_obj_t moy_ppa_%s(" % name)
    return src[head:src.index("\n}\n", head)]


def test_the_bounce_meters_report_the_stall_count_that_retires_them():
    """`rotate_bounce` answers -1 for TWO different things: a picture that can
    never bounce, and a pipeline that stalled past RB_MAX_STALLS and retired
    itself for the session. The second is the whole lever going away, and the
    counter that decides it has to be readable or the two are the same
    answer."""
    src = PPA_C.read_text(encoding="utf-8")
    assert "s_rb_stalls > RB_MAX_STALLS" in src, (
        "the retirement gate moved -- this pin names the variable it reads")
    body = _ppa_verb_body("rotate_bounce_stats")
    for field in PPA_BOUNCE_FIELDS:
        assert field in body, "rotate_bounce_stats no longer reports %s" % field
    assert "mp_obj_new_tuple(%d," % len(PPA_BOUNCE_FIELDS) in body


def test_the_ppa_bounce_meters_do_not_collide_with_the_compositor_verb():
    """`bounce_stats` is `BandedCompositor.bounce_stats()` -- the moy_flush
    PUMP 9-tuple, which is what every Python caller and every doc in the tree
    means by the name. A second, different 9-tuple under the same name on a
    native module is a meter nobody can ask for by name and be sure what they
    got."""
    src = PPA_C.read_text(encoding="utf-8")
    assert "MP_QSTR_rotate_bounce_stats" in src
    assert "MP_QSTR_bounce_stats" not in src
