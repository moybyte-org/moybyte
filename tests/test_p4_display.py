"""`P4Compositor`, EXECUTED (`firmware/esp32_p4_wifi6_touch_lcd_7b/modules/p4_display.py`).

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

import ast
import contextlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
P4_MODULES = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b" / "modules"

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


class FakeWS:
    """The `_remote_state` surface, plus a compositor."""

    def __init__(self, comp=None):
        self.wm = FakeWM()
        self.comp = comp
        self.screen = "home"
        self.wifi = None
        self._all_carts = []
        self._apps = ()
        self._psave_ms = 0
        self._psave_asleep = False


@contextlib.contextmanager
def p4_display(dsi, ppa, gfx=None):
    """Load `p4_display` fresh against stubbed native modules.

    Fresh every time on purpose: `set_backlight` caches its Pin in a module
    global, so a shared import would carry one test's pin into the next.
    """
    keys = ("p4_display", "moy_dsi", "moy_ppa", "moy_gfx", "machine")
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
    spec = importlib.util.spec_from_file_location(
        "p4_display", P4_MODULES / "p4_display.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["p4_display"] = mod
    try:
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
        monkeypatch.setattr(mod, "_ticks_us", clock.us)
        monkeypatch.setattr(mod, "_ticks_diff", StepTicks.diff)
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


def _const_str(node):
    return node.value if isinstance(node, ast.Constant) \
        and isinstance(node.value, str) else None


def _conversions(fmt):
    n, i = 0, 0
    while i < len(fmt):
        if fmt[i] == "%":
            if fmt[i + 1:i + 2] == "%":
                i += 2
                continue
            n += 1
        i += 1
    return n


def test_the_PERF_line_formats_against_the_arguments_it_is_given():
    """The P4's other route, and one whose breakage is SILENT: the print sits
    inside `_account`'s guard, so a %-count mismatch costs the whole line and
    surfaces only as `PERF sample failed` every two seconds."""
    tree = ast.parse((P4_MODULES / "moy_runtime.py").read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "print" and len(node.args) == 1):
            continue
        arg = node.args[0]
        if not (isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Mod)):
            continue
        fmt = _const_str(arg.left)
        if fmt is not None and fmt.startswith("PERF "):
            assert isinstance(arg.right, ast.Tuple)
            found.append((fmt, len(arg.right.elts)))
    for fmt, argc in found:
        assert _conversions(fmt) == argc, fmt
    sample = [f for f, _ in found if f.startswith("PERF fps=")]
    assert len(sample) == 1
    assert "ppa=%d/%d/%d/%d/%d" in sample[0]
    assert "fence_ms=" in sample[0] and "gfence_ms=" in sample[0]
