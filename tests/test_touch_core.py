"""The touch drivers' shared no-news contract (native/moy_input/moy_touch.c):
one body for the GT911, the GSL3680 and the AXS15231, held to its three
clauses -- hold the point, mark the repeat stale, bound the hold -- and to the
opt-in glide, through the host's binding on an injected clock. The drivers'
integration is pinned on glass (every suite's swipe/tap tests ride it)."""

from runtime import moy_input as mi
from runtime.moy_input import HeldPoint


def test_sample_reports_the_press_edge_once():
    hp = HeldPoint()
    assert hp.sample(10, 20, 0) == (10, 20, True)    # press edge
    assert hp.sample(11, 21, 5) == (11, 21, False)   # held, no re-tap
    assert hp.fresh is True


def test_hold_repeats_the_point_stale_then_bounds_it():
    hp = HeldPoint()
    hp.sample(5, 6, 1000)
    # Clauses 1 + 2: the point survives a no-news pass, marked stale.
    assert hp.hold(1100) == (5, 6, False)
    assert hp.fresh is False, "a repeat must not read as a measured sample"
    # Clause 3: past the bound, a missed finger-up frees the pointer.
    assert hp.hold(1100 + mi.TOUCH_HOLD_MS) is None
    assert hp.down is False and hp.fresh is True


def test_the_bound_is_the_drivers_own():
    """The AXS streams while touched, so its no-news window is the lift."""
    hp = HeldPoint(hold_ms=mi.TOUCH_HOLD_MS_STREAM)
    hp.sample(5, 6, 0)
    assert hp.hold(mi.TOUCH_HOLD_MS_STREAM - 1) is not None
    assert hp.hold(mi.TOUCH_HOLD_MS_STREAM) is None


def test_the_glide_anchor_is_the_point_that_was_DISPLAYED():
    """The recovery snap reads the last DISPLAYED glide point, which is the
    clamped one: anchored off the glass, the next trailing sample would be
    "recovered" to a pixel nobody ever saw."""
    hp = HeldPoint(extrapolate=True, w=100, h=100)
    hp.sample(80, 50, 0)
    hp.sample(95, 50, 20)             # 0.75 px/ms, headed off the right edge
    assert hp.hold(60) == (99, 50, False)    # the glide is clamped to the glass
    x, y, _ = hp.sample(97, 50, 80)   # a trailing sample -> recovery snap
    assert (x, y) == (99, 50)         # ...to a point ON the glass


def test_the_glide_runs_through_a_gap_and_respects_the_bound():
    hp = HeldPoint(extrapolate=True, w=480, h=320)
    hp.sample(100, 100, 0)
    hp.sample(120, 100, 20)           # 1 px/ms rightward
    x, y, edge = hp.hold(40)          # 20 ms of no news
    assert not edge and not hp.fresh
    assert 128 <= x <= 132 and y == 100      # ~EMA/2 of the raw speed, gliding
    x2, _, _ = hp.hold(60)
    assert x2 > x                     # still gliding, anchored to the last sample
    assert hp.hold(20 + mi.TOUCH_HOLD_MS) is None
    assert hp.fresh
    frozen = HeldPoint()              # the default contract does not glide
    frozen.sample(10, 10, 0)
    frozen.sample(30, 10, 20)
    assert frozen.hold(50) == (30, 10, False)


def test_release_is_news():
    hp = HeldPoint()
    hp.sample(1, 2, 0)
    hp.hold(5)
    assert hp.release() is None
    assert hp.down is False and hp.fresh is True
    assert hp.hold(10) is None        # no zombie point after a release


def test_map_point_scales_then_flips_then_clamps_both_ends():
    assert mi.map_point(160, 120, 320, 240, False, False, False) == (160, 120)
    assert mi.map_point(10, 20, 320, 240, True, False, False) == (20, 10)
    assert mi.map_point(0, 0, 320, 240, False, True, True) == (319, 239)
    # A press past the panel edge reads bigger than the axis, which a flip turns
    # negative: both ends of the clamp hold it on the glass.
    assert mi.map_point(400, -5, 320, 240, False, True, False) == (0, 0)
    # The controller's own space, an origin and a span, scaled first.
    assert mi.map_point(10 + 1640, 21 + 865, 1280, 800, False, False, False,
                        1640, 865, 10, 21) == (1279, 799)
    assert mi.map_point(9, 20, 1280, 800, False, False, False, 1640, 865, 10, 21) == (0, 0)
