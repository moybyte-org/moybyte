"""The shared banded compositor, EXECUTED (`device/banded_panel.py`, #206 item 1).

`BandedCompositor` is the one body both S3 boards' frame machine runs -- the
drain/swap/kick overlap, the ping-pong, the async gate and the flush meters. The
other net over it is the source-text greps in `tests/test_micropython_spike.py`,
which pin that a mechanism is still NAMED where a reader expects it; a substring
cannot tell drain-then-kick from kick-then-drain, cannot notice a ping-pong that
stopped advancing, and cannot see a meter wired to a name nothing defines. So
this suite aims at ORDER and STATE, and treats "a consumer can actually read
this number" as part of the contract. A third banded board inherits all of it by
subclassing, which is why it is pinned here and not in a board file.

`banded_panel` is ordinary Python: every hardware object arrives as the `lcd`
argument and the only import is a try/except'd `moy_gfx`, so it runs against the
doubles below. `tests/test_device_boot.py` is the house model for the order
tests.
"""

import contextlib
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEVICE = ROOT / "device"
TDECK_MODULES = ROOT / "firmware" / "lilygo_t_deck_plus_mainline" / "modules"
GUITION_MODULES = ROOT / "firmware" / "guition_jc3248w535" / "modules"

from device import banded_panel                                    # noqa: E402
from device.banded_panel import BandedCompositor                   # noqa: E402
from runtime.dev_channel import _remote_state                      # noqa: E402


# -- the doubles ---------------------------------------------------------------


class FB:
    """A framebuffer with an identity. `.i` is the slot it came from, so a test
    can say which buffer was drawn into and which one was shipped."""

    def __init__(self, i):
        self.i = i


class BlockingLcd:
    """A panel module that CANNOT split its flush: no `kick`.

    Not a straw man -- it is what `ASYNC_FLUSH = False` degrades both real
    boards to, and the shape the async gate's `hasattr(lcd, "kick")` arm exists
    for. Every call is logged as `(name, arg, back_at_call)`, where the third
    element is the ping-pong index AT THE MOMENT of the call -- which is what
    makes the flush ORDER observable to a swap moved to either side of it.
    """

    WIDTH = 480
    HEIGHT = 320

    def __init__(self, nfbs=2, pump=(10, 20, 30, 40, 50, 60, 70, 80, 90),
                 stats=(1234, 5678)):
        self._nfbs = nfbs
        self._fbs = [FB(i) for i in range(nfbs)]
        self._pump = pump
        self._stats = stats
        self.calls = []
        self.comp = None          # set by the test right after construction

    # the observation hook
    def _log(self, name, arg=None):
        self.calls.append((name, arg, getattr(self.comp, "_back", None)))

    def names(self):
        return [c[0] for c in self.calls]

    # -- the verb set BandedCompositor's docstring requires --------------------

    def init(self, nfbs=2):
        self._log("init", nfbs)

    def fb(self, i):
        return self._fbs[i]

    def nfbs(self):
        return self._nfbs

    def show(self, i):
        self._log("show", i)

    def drain(self):
        self._log("drain")

    def backlight(self, on):
        self._log("backlight", on)

    def stats(self):
        return self._stats

    def pump_stats(self):
        return self._pump


class FakeLcd(BlockingLcd):
    """The overlap-capable shape -- `BlockingLcd` plus `kick`."""

    def kick(self, i):
        self._log("kick", i)


def build(lcd=None, nfbs=2, async_flush=True, cls=FakeLcd):
    lcd = lcd if lcd is not None else cls(nfbs=nfbs)
    comp = BandedCompositor(lcd, nfbs=nfbs, async_flush=async_flush)
    lcd.comp = comp               # arm the observation hook
    return lcd, comp


def fresh(**kw):
    """A built pair with construction noise cleared off the call log."""
    lcd, comp = build(**kw)
    lcd.calls.clear()
    return lcd, comp


# -- construction --------------------------------------------------------------


def test_the_panel_is_initialised_with_the_slot_count_it_was_asked_for():
    lcd, comp = build(nfbs=3)
    assert lcd.calls[0][:2] == ("init", 3)
    assert comp.size() == (480, 320)


def test_construction_leaves_the_backlight_ALONE():
    """#45: a freshly-powered panel's GRAM is noise, so nothing lights the glass
    until a frame has been composed. `run_desktop`'s backlight gate is what
    turns it on; a compositor that lit it here would show that noise."""
    lcd, _comp = build()
    assert "backlight" not in lcd.names()


def test_the_framebuffers_are_cached_once_not_rebuilt_per_call():
    """back_buffer() is called every frame and on every layer bind; the
    memoryviews are taken once at init on purpose."""
    _lcd, comp = build()
    assert comp.framebuffer() is comp.framebuffer()
    assert comp.back_buffer() is comp.framebuffer()


@contextlib.contextmanager
def moy_gfx_is(stub):
    """Pin what `import moy_gfx` resolves to for the duration.

    Both arms have to be FORCED: `runtime/host_canvas.py` does
    `sys.modules.setdefault("moy_gfx", gfx_binding)`, which is process-wide and
    permanent, so whether a bare import succeeds here depends on which other
    suite this worker already ran. `None` in sys.modules is the documented way
    to make an import raise ImportError.
    """
    saved = sys.modules.get("moy_gfx", KeyError)
    sys.modules["moy_gfx"] = stub
    try:
        yield stub
    finally:
        if saved is KeyError:
            sys.modules.pop("moy_gfx", None)
        else:
            sys.modules["moy_gfx"] = saved


def test_no_moy_gfx_in_the_build_degrades_instead_of_failing():
    """A board built without the kernel still comes up; DeviceCanvas falls back
    to framebuf. `gfx()` returning None is how it finds out."""
    with moy_gfx_is(None):
        _lcd, comp = build()
        assert comp.gfx() is None
        assert comp.has_gfx() is False


def test_a_build_with_moy_gfx_hands_the_kernel_through():
    stub = type("moy_gfx", (), {})()
    with moy_gfx_is(stub):
        _lcd, comp = build()
        assert comp.gfx() is stub
        assert comp.has_gfx() is True


# -- the flush ORDER: drain -> swap -> kick ------------------------------------


def test_flush_drains_then_swaps_then_kicks():
    """THE invariant, and the one a refactor breaks silently: the drain must
    finish the PREVIOUS frame while the ping-pong still points at the buffer
    this frame was drawn into, and the kick must ship that buffer after the
    pointer has moved off it -- otherwise the next frame draws into pixels the
    panel is reading. Each of the four reorderings shows up in a different
    element below, which is why the ping-pong index is logged per call.
    """
    lcd, comp = fresh()
    assert comp.framebuffer().i == 0

    comp.flush()

    assert lcd.names() == ["drain", "kick"]
    assert lcd.calls[0] == ("drain", None, 0)
    assert lcd.calls[1] == ("kick", 0, 1)


def test_the_kicked_buffer_is_the_one_the_frame_was_drawn_into():
    lcd, comp = fresh()
    drawn = comp.framebuffer()
    comp.flush()
    assert lcd.calls[-1][0] == "kick"
    assert lcd.calls[-1][1] == drawn.i
    # ...and drawing has MOVED OFF it, so the next frame cannot scribble on a
    # buffer that is being read out.
    assert comp.framebuffer() is not drawn


def test_an_overlapped_flush_never_blocks_on_show():
    """`show()` is the blocking whole-frame push. The overlap's entire thesis is
    that flush() costs the drain residue plus the kick and returns."""
    lcd, comp = fresh()
    comp.flush()
    assert "show" not in lcd.names()


def test_consecutive_flushes_alternate_the_shipped_buffer():
    lcd, comp = fresh()
    for _ in range(4):
        comp.flush()
    kicked = [c[1] for c in lcd.calls if c[0] == "kick"]
    assert kicked == [0, 1, 0, 1]
    assert lcd.names() == ["drain", "kick"] * 4


# -- the ping-pong -------------------------------------------------------------


@pytest.mark.parametrize("n,expected", [
    (1, [0, 0, 0, 0]),          # nothing to pong to; the index must not move
    (2, [1, 0, 1, 0]),
    (3, [1, 2, 0, 1]),
])
def test_the_ping_pong_walks_every_slot_and_wraps(n, expected):
    """A modulo that lost its wrap indexes off the end of `_fbs`.

    One mutation this CANNOT catch: weakening `_swap`'s `if n > 1` to
    `if n >= 1` -- `(back + 1) % 1` is always 0, so the guard is arithmetically
    redundant and that mutant is EQUIVALENT. The guard stays because it says
    the intent out loud; do not "simplify" it away expecting a test to object.
    """
    _lcd, comp = build(nfbs=n)
    seen = []
    for _ in range(4):
        comp._swap()
        seen.append(comp._back)
    assert seen == expected


def test_every_slot_is_reachable_so_no_buffer_is_stranded():
    _lcd, comp = build(nfbs=3)
    seen = set()
    for _ in range(6):
        seen.add(comp.framebuffer().i)
        comp._swap()
    assert seen == {0, 1, 2}


# -- the async gate ------------------------------------------------------------


def test_two_buffers_and_a_kick_verb_arm_the_overlap():
    _lcd, comp = build(nfbs=2, async_flush=True)
    assert comp._async is True
    assert comp.bounce_flush is True


@pytest.mark.parametrize("kw,cls,why", [
    (dict(async_flush=False), FakeLcd,
     "the board's ASYNC_FLUSH revert flag must actually revert it"),
    (dict(async_flush=True, nfbs=1), FakeLcd,
     "one framebuffer means the DMA would read what the next frame draws"),
    (dict(async_flush=True), BlockingLcd,
     "a panel module with no kick() cannot split its flush at all"),
])
def test_the_overlap_stays_DISARMED_when_any_of_its_three_terms_is_missing(
        kw, cls, why):
    """All three terms of the gate are load-bearing, so all three get a case.
    Losing any one of them arms an overlap the hardware cannot honour: the
    revert flag stops reverting, a single-buffer board tears against its own
    render, and a kick-less module raises AttributeError inside the frame loop.
    """
    _lcd, comp = build(cls=cls, **kw)
    assert comp._async is False, why
    assert comp.bounce_flush is False, why


def test_bounce_flush_is_the_gate_device_diag_reads():
    """`_diag_pump` prints nothing unless `bounce_flush`, so it must track the
    real gate rather than the flag the board asked for."""
    _lcd, comp = build(nfbs=1, async_flush=True)
    assert comp.bounce_flush == comp._async


def test_a_serialized_flush_shows_and_swaps_and_never_kicks():
    lcd, comp = fresh(async_flush=False)
    drawn = comp.framebuffer()
    comp.flush()
    assert lcd.names() == ["show"]
    assert lcd.calls[0] == ("show", drawn.i, 0)     # shown BEFORE the swap
    assert comp.framebuffer() is not drawn          # ...and it still ping-pongs
    assert "drain" not in lcd.names()


def test_a_kickless_module_is_never_asked_to_kick():
    lcd, comp = fresh(cls=BlockingLcd)
    comp.flush()
    comp.flush()
    assert lcd.names() == ["show", "show"]


# -- the contract --------------------------------------------------------------


def test_sync_is_a_drain_and_nothing_else():
    """The fence the backlight gate, the idle-band drain and (on the T-Deck)
    every SD session take. It must leave the ping-pong ALONE -- a sync that
    swapped would hand the next frame a buffer the panel just finished with."""
    lcd, comp = fresh()
    before = comp.framebuffer()
    comp.sync()
    assert lcd.names() == ["drain"]
    assert comp.framebuffer() is before


def test_set_backlight_forwards_both_ways():
    lcd, comp = fresh()
    comp.set_backlight(True)
    comp.set_backlight(False)
    assert [c[:2] for c in lcd.calls] == [("backlight", True),
                                          ("backlight", False)]


def test_stats_is_the_panel_modules_wall_span_untouched():
    lcd, comp = fresh()
    assert comp.stats() == lcd._stats


# -- the meters: all NINE moy_flush fields -------------------------------------
#
# `native/moy_flush/moy_flush.c`'s pump_stats tuple is
#   (pump_us, idle_us, idle_n, feed_us, bands, blocked_us, timeouts, errs,
#    stop_fails)
# and every value in the fixture below is distinct, so a transposed or dropped
# index cannot pass by coincidence.

PUMP = (10, 20, 30, 40, 50, 60, 70, 80, 90)


def test_bounce_stats_carries_every_field_the_C_exports():
    _lcd, comp = build()
    assert comp.bounce_stats() == PUMP


def test_the_four_fields_that_reached_nobody_are_carried():
    """blocked_us is the CPU the VM core spent waiting in drain; timeouts is a
    band that never reported done inside the feeder's bound; errs is a queue
    error DURING a drain, which `moy_flush` cannot raise (a drain must not throw
    into the frame loop), so that counter is the only place such a failure is
    visible anywhere; stop_fails is deinit having given up on the feeder and
    LEFT the bounce slots allocated rather than free them under a live ISR --
    equally unraisable, for the same reason."""
    lcd, comp = build()
    lcd._pump = (1, 2, 3, 4, 5, 1400, 9, 7, 2)
    blocked_us, timeouts, errs, stop_fails = comp.bounce_stats()[5:]
    assert blocked_us == 1400
    assert timeouts == 9
    assert errs == 7
    assert stop_fails == 2


def test_a_serialized_compositor_reports_the_no_overlap_sentinel():
    """Nine fields either way, so no consumer has to branch on length -- and an
    asymmetry here would be invisible on whichever board you happened to be
    looking at. feed_us keeps its -1 "never measured" sentinel; the counters are
    honestly 0, because a blocking show() raises its failures instead of banking
    them."""
    _lcd, comp = build(async_flush=False)
    assert comp.bounce_stats() == (0, 0, 0, -1, 0, 0, 0, 0, 0)
    assert len(comp.bounce_stats()) == len(build()[1].bounce_stats())


def test_pump_last_us_is_the_first_field_and_zero_without_an_overlap():
    _lcd, comp = build()
    assert comp.pump_last_us == PUMP[0]
    _lcd2, comp2 = build(async_flush=False)
    assert comp2.pump_last_us == 0


def test_the_base_compositor_claims_NO_board_lever():
    """Absence is how a board says it lacks a lever (`DeviceCanvas.blit_game`
    and `_diag_pump` both getattr these). The shared body must therefore claim
    none of them, or every board inherits a lever it does not have."""
    _lcd, comp = build()
    for lever in ("fold_supported", "fold_count", "arm_scale_fold",
                  "disarm_scale_fold", "fold_fence", "sd_bracket",
                  "pump_if_pending"):
        assert not hasattr(comp, lever), lever


# -- the boards' subclasses, constructed for real ------------------------------


@contextlib.contextmanager
def board_panel(module_name, native_name, modules_dir, lcd):
    """Import a board's panel module against a STUBBED native module.

    Both subclasses import their C module lazily inside `__init__`, so the stub
    has to stay in `sys.modules` across the CONSTRUCTION and not only the
    import -- which is why this is a context manager.
    """
    saved_path = list(sys.path)
    keys = (module_name, "banded_panel", native_name)
    saved_mods = {k: sys.modules.get(k) for k in keys}
    sys.path.insert(0, str(modules_dir))
    sys.path.insert(0, str(DEVICE))
    sys.modules[native_name] = lcd
    sys.modules.pop(module_name, None)
    sys.modules.pop("banded_panel", None)
    try:
        yield importlib.import_module(module_name)
    finally:
        sys.path[:] = saved_path
        for k, v in saved_mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class FoldingLcd(FakeLcd):
    """`moy_axs`'s shape: the banded verb set plus the #190-cousin game fold."""

    def __init__(self, folded=0, **kw):
        FakeLcd.__init__(self, **kw)
        self.folded = folded

    def fold_stats(self):
        # (frames_folded, armed, inflight, windowed) -- modmoy_axs.c
        return (self.folded, False, False, 0)

    def arm_fold(self, src, vw, vh, ox, oy):
        self._log("arm_fold", (vw, vh, ox, oy))

    def disarm_fold(self, i):
        self._log("disarm_fold", i)

    def fold_fence(self):
        self._log("fold_fence")


def test_the_guition_fold_counter_is_LIVE_and_reads_the_C():
    """`device_diag._diag_pump` reads `getattr(comp, "fold_count", 0)`, and a
    getattr default silently taken forever is the failure mode -- so this
    asserts both halves: the attribute EXISTS, and it MOVES."""
    lcd = FoldingLcd(folded=5763)
    with board_panel("guition_panel", "moy_axs", GUITION_MODULES, lcd) as gp:
        comp = gp.GuitionCompositor()
        lcd.comp = comp

        assert comp.fold_supported is True
        assert comp.fold_count == 5763
        lcd.folded = 5767
        assert comp.fold_count == 5767, (
            "fold_count must read the C every time: a frozen number is exactly "
            "the symptom (something disarming every frame) the meter "
            "distinguishes from a healthy fold")


def test_the_tdeck_declares_no_fold_at_all():
    """The T-Deck has no fold lever and must carry NO fold attribute: absence is
    how a board says so, and `fold=0` on its PUMP line is only distinguishable
    from a broken fold because the board that HAS one defines the name."""
    lcd = FakeLcd()
    with board_panel("tdeck_panel", "moy_lcd", TDECK_MODULES, lcd) as tp:
        comp = tp.TDeckCompositor()
        lcd.comp = comp

        assert not hasattr(comp, "fold_supported")
        assert not hasattr(comp, "fold_count")
        # ...and it inherits the shared frame machine unmodified.
        assert comp.bounce_stats() == PUMP
        lcd.calls.clear()
        comp.flush()
        assert lcd.names() == ["drain", "kick"]


# -- the consumers: a meter nobody can read is not a meter ---------------------


class FakeDiag:
    def __init__(self):
        self.lines = []

    def log(self, tag, msg):
        self.lines.append((tag, msg))

    def line(self, tag):
        for t, m in self.lines:
            if t == tag:
                return m
        return None


def _device_diag():
    """`device/device_diag.py` imports its tick helpers flat (`device_util`),
    the way the frozen device tree resolves them; load it the same way
    tests/test_device_canvas_parity.py loads its device modules."""
    if "device_util" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "device_util", DEVICE / "device_util.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["device_util"] = mod
        spec.loader.exec_module(mod)
    spec = importlib.util.spec_from_file_location(
        "device_diag", DEVICE / "device_diag.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FoldingComp(BandedCompositor):
    """A compositor that HAS the fold lever, without needing a board file."""

    def __init__(self, lcd, folded):
        BandedCompositor.__init__(self, lcd, 2, True)
        self.fold_count = folded


def test_the_PUMP_line_carries_blocked_timeouts_errs_and_stopfail():
    """Every value distinct, and blocked= is converted from us to ms like the
    other durations on the line."""
    lcd, comp = build()
    lcd._pump = (1000, 2000, 3, 4000, 5, 1400, 9, 7, 2)
    diag = FakeDiag()
    _device_diag()._diag_pump(diag, comp)

    line = diag.line("PUMP")
    assert "blocked=1.40" in line
    assert "timeouts=9" in line
    assert "errs=7" in line
    assert "stopfail=2" in line
    # ...and the fields that were already there did not shift meaning.
    assert "pump=1.00" in line and "idle=2.00" in line
    assert "gaps=3" in line and "feed=4.00" in line and "bands=5" in line


def test_the_PUMP_line_reports_a_LIVE_fold_count():
    lcd = FakeLcd()
    comp = FoldingComp(lcd, 5763)
    lcd.comp = comp
    dd = _device_diag()

    diag = FakeDiag()
    dd._diag_pump(diag, comp)
    assert "fold=5763" in diag.line("PUMP")

    comp.fold_count = 5767
    diag = FakeDiag()
    dd._diag_pump(diag, comp)
    assert "fold=5767" in diag.line("PUMP")


def test_a_serialized_board_prints_no_PUMP_line_at_all():
    _lcd, comp = build(async_flush=False)
    diag = FakeDiag()
    _device_diag()._diag_pump(diag, comp)
    assert diag.lines == []


def test_a_throwing_compositor_never_breaks_the_diag_tick():
    class Exploding:
        bounce_flush = True

        def bounce_stats(self):
            raise OSError("panel gone")

    diag = FakeDiag()
    _device_diag()._diag_pump(diag, Exploding())
    assert diag.lines == []


# -- the dev channel: the ONLY route on a board with no diag line --------------


class FakeWM:
    _stack = ["home"]


class _FakeCarts:
    """`ws.carts` narrowed to the one member `_remote_state` reads (#209
    landing C): the roster is a plain attribute on the collaborator now."""

    def __init__(self):
        self.all = []


class FakeWS:
    """The `_remote_state` surface, plus a compositor -- which is what a board
    with no `device_diag` has to read its flush meters through."""

    def __init__(self, comp=None):
        self.wm = FakeWM()
        self.comp = comp
        self.screen = "home"
        self.wifi = None
        self.carts = _FakeCarts()
        self._apps = ()
        self._psave_ms = 0
        self._psave_asleep = False


def test_state_carries_the_whole_pump_tuple():
    """The Guition denies `device_diag` in its board.toml, so `state` is the
    only place its flush meters can surface."""
    lcd, comp = build()
    lcd._pump = (1, 2, 3, 4, 5, 1400, 9, 7, 2)
    st = _remote_state(FakeWS(comp))
    assert st["pump"] == [1, 2, 3, 4, 5, 1400, 9, 7, 2]


def test_state_reports_fold_as_None_when_the_board_has_no_fold():
    """None, not 0: `fold=0` is also what a fold that never fires looks like."""
    _lcd, comp = build()
    assert _remote_state(FakeWS(comp))["fold"] is None


def test_state_reports_a_live_fold_count_when_the_board_has_one():
    lcd = FakeLcd()
    comp = FoldingComp(lcd, 5763)
    assert _remote_state(FakeWS(comp))["fold"] == 5763


def test_state_is_harmless_on_a_board_with_no_compositor_meters():
    """The P4's panel scans a framebuffer; it has no bands and no flush, so it
    has no `bounce_stats`. That must read as an absent field, not an error."""
    st = _remote_state(FakeWS(None))
    assert "pump" not in st
    assert st["fold"] is None
    assert "pump_err" not in st


# -- the greps and the executable lane must not drift apart --------------------


def test_the_shared_body_is_what_both_boards_subclass():
    """`tests/test_micropython_spike.py` greps `device/banded_panel.py` for the
    mechanism; this file runs it. Both are pointing at the same file only for
    as long as the boards subclass it, so pin that too."""
    tdeck = (TDECK_MODULES / "tdeck_panel.py").read_text(encoding="utf-8")
    guition = (GUITION_MODULES / "guition_panel.py").read_text(encoding="utf-8")
    for src in (tdeck, guition):
        assert "from banded_panel import BandedCompositor" in src
        assert "BandedCompositor.__init__(self," in src
    assert banded_panel.__file__.endswith("device/banded_panel.py")
