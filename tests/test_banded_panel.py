"""The banded compositor, EXECUTED: the kernel's frame state machine
(`native/moy_glass/moy_present.h`, sprint 3's glass pass) over a fake
transport, and the meters its consumers read.

`moy_banded_*` is the one engine both S3 boards' `moy_glass.BandedCompositor`
runs -- the drain/swap/kick overlap, the ping-pong and the overlap gate --
built here by the host's glass library with a transport that logs every call.
So this suite aims at ORDER and STATE: a substring cannot tell drain-then-kick
from kick-then-drain, or notice a ping-pong that stopped advancing. The
binding's face (the fold verbs it forwards, the meters it reads off the panel
module) is the boards' and runs in their on-glass suites; the consumers below
are held to what they read off any compositor.
"""

import ctypes
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEVICE = ROOT / "device"

from runtime import glass_binding                                  # noqa: E402
from runtime.dev_channel import _remote_state                      # noqa: E402


def _lib():
    lib = glass_binding._lib()
    for name, args, res in (
            ("moy_glass_host_banded_init", [ctypes.c_int] * 3, None),
            ("moy_glass_host_banded_present", [], ctypes.c_int),
            ("moy_glass_host_banded_fence", [], ctypes.c_int),
            ("moy_glass_host_banded_back", [], ctypes.c_int),
            ("moy_glass_host_banded_overlap", [], ctypes.c_int),
            ("moy_glass_host_kick_err", [ctypes.c_int], None),
            ("moy_glass_host_log", [], ctypes.c_char_p)):
        fn = getattr(lib, name)
        fn.argtypes = args
        fn.restype = res
    return lib


class Engine:
    """One banded engine over the logging transport."""

    def __init__(self, nfbs=2, overlap=True, kick=True):
        self.lib = _lib()
        self.lib.moy_glass_host_banded_init(nfbs, int(overlap), int(kick))

    def flush(self):
        return self.lib.moy_glass_host_banded_present()

    def sync(self):
        return self.lib.moy_glass_host_banded_fence()

    @property
    def back(self):
        return self.lib.moy_glass_host_banded_back()

    @property
    def overlap(self):
        return bool(self.lib.moy_glass_host_banded_overlap())

    def log(self):
        return self.lib.moy_glass_host_log().decode().split()


# -- the frame state machine -----------------------------------------------------


def test_flush_drains_then_swaps_then_kicks():
    e = Engine()
    e.flush()
    assert e.log() == ["drain", "kick0"]
    assert e.back == 1


def test_the_kicked_buffer_is_the_one_the_frame_was_drawn_into():
    e = Engine()
    for _ in range(5):
        drawn = e.back
        e.flush()
        assert e.log() == ["drain", "kick%d" % drawn]
        assert e.back != drawn


def test_an_overlapped_flush_never_blocks_on_show():
    e = Engine()
    for _ in range(4):
        e.flush()
    assert not any(c.startswith("show") for c in e.log())


@pytest.mark.parametrize("n, expected", [(1, [0, 0, 0, 0]), (2, [1, 0, 1, 0]),
                                         (3, [1, 2, 0, 1]), (4, [1, 2, 3, 0])])
def test_the_ping_pong_walks_every_slot_and_wraps(n, expected):
    e = Engine(nfbs=n)
    seen = []
    for _ in range(4):
        e.flush()
        seen.append(e.back)
    assert seen == expected


def test_the_overlap_needs_two_buffers_a_kick_and_the_flag():
    assert Engine().overlap
    assert not Engine(nfbs=1).overlap
    assert not Engine(kick=False).overlap
    assert not Engine(overlap=False).overlap


def test_a_serialized_flush_shows_and_swaps_and_never_kicks():
    e = Engine(overlap=False)
    e.flush()
    e.flush()
    assert e.log() == ["show0", "show1"]
    assert e.back == 0


def test_sync_is_a_drain_and_nothing_else():
    e = Engine()
    e.sync()
    assert e.log() == ["drain"]
    assert e.back == 0


def test_the_last_frames_transport_error_comes_back_after_the_swap():
    """The feeder runs the SPI, so an error surfaces one frame late: the kick
    returns the finished frame's, and the binding raises it. The swap has
    happened -- the next frame draws into the other buffer either way."""
    e = Engine()
    e.lib.moy_glass_host_kick_err(0x107)
    assert e.flush() == 0x107
    assert e.back == 1


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


class Comp:
    """What the diag and `state` read off a banded compositor: the overlap
    gate, the pump tuple, and the fold meters when the board has the lever
    (absent otherwise -- the readers getattr them)."""

    def __init__(self, overlap=True, pump=(0,) * 9):
        self.bounce_flush = overlap
        self.pump = pump

    def bounce_stats(self):
        return self.pump if self.bounce_flush else (0, 0, 0, -1, 0, 0, 0, 0, 0)


class FoldingComp(Comp):
    """A compositor that HAS the fold lever."""

    def __init__(self, folded):
        Comp.__init__(self)
        self.fold_count = folded


def test_the_PUMP_line_carries_blocked_timeouts_errs_and_stopfail():
    """Every value distinct, and blocked= is converted from us to ms like the
    other durations on the line."""
    comp = Comp(pump=(1000, 2000, 3, 4000, 5, 1400, 9, 7, 2))
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
    comp = FoldingComp(5763)
    dd = _device_diag()

    diag = FakeDiag()
    dd._diag_pump(diag, comp)
    assert "fold=5763" in diag.line("PUMP")

    comp.fold_count = 5767
    diag = FakeDiag()
    dd._diag_pump(diag, comp)
    assert "fold=5767" in diag.line("PUMP")


def test_the_PUMP_line_carries_the_snapshot_meters_only_where_they_exist():
    """snap=dma/memcpy snapto= snapwait= come from `snap_stats`, and a
    compositor without the verb prints none of them -- absence, never 0,
    is how a board says it lacks the lever."""
    comp = FoldingComp(12)
    dd = _device_diag()
    diag = FakeDiag()
    dd._diag_pump(diag, comp)
    assert "snap=" not in diag.line("PUMP")

    comp.snap_stats = lambda: (5763, 2, 1, 1400)
    diag = FakeDiag()
    dd._diag_pump(diag, comp)
    line = diag.line("PUMP")
    assert "fold=12" in line
    assert "snap=5763/2" in line
    assert "snapto=1" in line
    assert "snapwait=1.40" in line


def test_a_serialized_board_prints_no_PUMP_line_at_all():
    comp = Comp(overlap=False)
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
    comp = Comp(pump=(1, 2, 3, 4, 5, 1400, 9, 7, 2))
    st = _remote_state(FakeWS(comp))
    assert st["pump"] == [1, 2, 3, 4, 5, 1400, 9, 7, 2]


def test_state_reports_fold_as_None_when_the_board_has_no_fold():
    """None, not 0: `fold=0` is also what a fold that never fires looks like.
    The frame fold's count follows the same rule."""
    st = _remote_state(FakeWS(Comp()))
    assert st["fold"] is None and st["ffold"] is None


def test_state_reports_the_frame_fold_count_from_the_C():
    comp = FoldingComp(0)
    comp.frame_fold_count = 412
    assert _remote_state(FakeWS(comp))["ffold"] == 412


def test_state_reports_a_live_fold_count_when_the_board_has_one():
    comp = FoldingComp(5763)
    assert _remote_state(FakeWS(comp))["fold"] == 5763


def test_state_is_harmless_on_a_board_with_no_compositor_meters():
    """The P4's panel scans a framebuffer; it has no bands and no flush, so it
    has no `bounce_stats`. That must read as an absent field, not an error."""
    st = _remote_state(FakeWS(None))
    assert "pump" not in st
    assert st["fold"] is None
    assert "pump_err" not in st




def test_the_boards_build_the_kernel_compositor():
    """Both banded boards construct the one C compositor over their own panel
    module; neither carries a Python compositor of its own."""
    for board, panel in (("lilygo_t_deck_plus_mainline", "moy_lcd"),
                         ("guition_jc3248w535", "moy_axs")):
        src = (ROOT / "firmware" / board / "modules" / "moy_runtime.py").read_text(
            encoding="utf-8")
        assert "moy_glass.BandedCompositor(%s" % panel in src
        assert not (ROOT / "device" / "banded_panel.py").exists()
