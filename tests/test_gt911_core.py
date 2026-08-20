"""gt911.HeldPoint -- the shared no-news contract (#202 Phase C).

Both boards' GT911 drivers maintained this state machine separately and
cross-referenced each other in prose; the P4's copy shipped with the hold and
NEITHER guard for months (its own comment records it). These tests pin the
three clauses so the one copy cannot lose one silently. The drivers'
integration is pinned on glass (both suites' swipe/tap tests ride poll()).
"""

from device import gt911
from device.gt911 import HeldPoint


def test_sample_reports_the_press_edge_once():
    hp = HeldPoint()
    assert hp.sample(10, 20) == (10, 20, True)      # press edge
    assert hp.sample(11, 21) == (11, 21, False)     # held, no re-tap
    assert hp.fresh is True


def test_hold_repeats_the_point_stale_then_bounds_it(monkeypatch):
    now = [1000]
    monkeypatch.setattr(gt911, "_ticks_ms", lambda: now[0])
    monkeypatch.setattr(gt911, "_ticks_diff", lambda a, b: a - b)
    hp = HeldPoint()
    hp.sample(5, 6)
    # Clause 1 + 2: the point survives a no-news pass, marked stale.
    now[0] += 100
    assert hp.hold() == (5, 6, False)
    assert hp.fresh is False, "a repeat must not read as a measured sample"
    # Clause 3: past the bound, a missed finger-up frees the pointer.
    now[0] += HeldPoint.HOLD_SAMPLE_MS
    assert hp.hold() is None
    assert hp.down is False and hp.fresh is True


def test_the_glide_anchor_is_the_point_that_was_DISPLAYED(monkeypatch):
    """`_gx` is documented as "last DISPLAYED glide point", and both readers of
    it -- the monotonic-recovery dot product and the snap it feeds -- are only
    sound if that is true. It was set from the RAW extrapolation, before the
    clamp, so a finger gliding into an edge anchored off the glass and the next
    trailing sample was "recovered" to a pixel nobody ever saw."""
    now = [0]
    monkeypatch.setattr(gt911, "_ticks_ms", lambda: now[0])
    monkeypatch.setattr(gt911, "_ticks_diff", lambda a, b: a - b)
    hp = HeldPoint(extrapolate=True, w=100, h=100)
    hp.sample(80, 50)
    now[0] = 20
    hp.sample(95, 50)                 # 0.75 px/ms, headed off the right edge
    now[0] = 60
    assert hp.hold() == (99, 50, False)      # the glide is clamped to the glass
    assert hp._gx == (99, 50), "the anchor must be the clamped point"
    now[0] = 80
    x, y, _ = hp.sample(97, 50)       # a trailing sample -> recovery snap
    assert (x, y) == (99, 50)         # ...to a point ON the glass


def test_release_is_news():
    hp = HeldPoint()
    hp.sample(1, 2)
    hp.hold()
    assert hp.release() is None
    assert hp.down is False and hp.last is None and hp.fresh is True
    # ...and a hold after a release stays None (no zombie point).
    assert hp.hold() is None


def test_the_register_map_is_the_shared_one():
    assert gt911.REG_STATUS == 0x814E
    assert gt911.REG_POINT0 == 0x8150
    assert gt911.ADDRS == (0x5D, 0x14)
