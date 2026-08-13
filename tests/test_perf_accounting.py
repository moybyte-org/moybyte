"""The frame's perf split has to ADD UP, and it has to be in the units it says.

Both halves of this file exist because of one number. CHROMEBRK's `other` read
~7.6ms on the S3 while bar/cmp/cur all read ~0.00 -- the largest single item in
an 18ms console frame, with every named bucket saying "not me". It was treated
as a real cost to hunt. It was partly an artefact:

  * `chrome` is a residual (draw - upd - cart - audio) and `other` was a residual
    OF that residual (chrome - bar - cmp - cur). Six terms, all integer
    MILLISECONDS, each truncating toward zero, every one of their losses landing
    in the last term.
  * the dearest genuinely-unnamed component -- the WM stack walk -- was never
    measured into the split at all, though #172 had already built the per-layer
    timer that could.

Fixed 2026-08-14: every bracket is microseconds (converted once, at the EMA, so
every public number stays ms), and the walk is its own `stk` bucket. What is left
in `other` is the router itself.

Neither fix has a natural failure signal. A units regression does not crash and
does not move a golden -- it makes a number quietly wrong in the direction of
"there is a mystery here", which is the most expensive kind of wrong this project
has hit. So: an arithmetic test that the partition closes, and a source pin that
the brackets are on the microsecond clock.
"""

import re
from pathlib import Path

import pytest

from runtime import console as console_mod

ROOT = Path(__file__).resolve().parent.parent


# -- the partition closes ---------------------------------------------------

class _WS:
    """Just the fields perf_chrome/perf_breakdown read. Constructing a whole
    Workstation would drag in a canvas and a store to test six subtractions."""

    perf_chrome = console_mod.Workstation.perf_chrome
    perf_breakdown = console_mod.Workstation.perf_breakdown

    def __init__(self, chrome, bar, cmp_, cur, stk):
        self._chrome_ms = chrome
        self._bar_ms = bar
        self._cmp_ms = cmp_
        self._cur_ms = cur
        self._stk_ms = stk
        self._upd_ms = self._cart_ms = self._audio_ms = 0.0


def test_the_chrome_split_is_a_partition_not_a_pile():
    ws = _WS(chrome=8.0, bar=1.5, cmp_=0.25, cur=0.75, stk=4.0)
    bar, cmp_, cur, stk, other = ws.perf_chrome()
    assert (bar, cmp_, cur, stk) == (1.5, 0.25, 0.75, 4.0)
    assert other == pytest.approx(1.5)
    # ...and the five sum back to the bucket they subdivide. If a future bucket
    # is added and forgotten in `other`, this is what notices.
    assert bar + cmp_ + cur + stk + other == pytest.approx(ws._chrome_ms)


def test_stk_is_reported_and_not_folded_into_other():
    """The regression this bucket exists to prevent: `stk` silently rejoining
    `other` (by being dropped from the subtraction) would restore exactly the
    unreadable number the split was built to replace, and every assertion about
    `other` above would still pass without it."""
    named = _WS(chrome=8.0, bar=0.0, cmp_=0.0, cur=0.0, stk=6.0).perf_chrome()
    lumped = _WS(chrome=8.0, bar=0.0, cmp_=0.0, cur=0.0, stk=0.0).perf_chrome()
    assert named[3] == 6.0 and named[4] == pytest.approx(2.0)
    assert lumped[4] == pytest.approx(8.0), "the un-split case must still total"
    assert named[4] < lumped[4], "naming the walk must SHRINK the remainder"


def test_a_clamped_other_never_hides_a_double_count():
    """`other` floors at zero, so an over-subtraction reads as 'all accounted
    for' rather than as an error. That is the correct display and the wrong
    diagnostic, so the clamp is pinned here as deliberate: if a bucket is ever
    counted twice, this is the shape the numbers take."""
    ws = _WS(chrome=2.0, bar=1.0, cmp_=1.0, cur=1.0, stk=1.0)   # sums to 4 > 2
    assert ws.perf_chrome()[4] == 0.0


# -- the brackets are on the microsecond clock ------------------------------

def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_the_cart_phase_brackets_use_the_microsecond_clock():
    """player.tick's four brackets (backdrop / _update / _draw / audio) feed
    DRAWBRK directly and CHROMEBRK by subtraction. On ticks_ms each lost up to
    1ms into `other`."""
    src = _src("runtime/player.py")
    block = src[src.index("_ts = _ticks_us() if _perf else 0"):
                src.index("self._maybe_diag_slow_logic")]
    assert "_ticks_ms()" not in block, \
        "a millisecond bracket is back in the cart phase split"
    for bracket in ("_tb = _ticks_us()", "_ts = _ticks_us()",
                    "_tm = _ticks_us()", "_td = _ticks_us()"):
        assert bracket in src, bracket


def test_the_frame_and_flush_brackets_use_the_microsecond_clock():
    """_frame_perf_end's own two spans -- the whole frame and the panel flush --
    are the minuend of every subtraction above them."""
    src = _src("runtime/console.py")
    body = src[src.index("def _frame_perf_end"):
               src.index("def arm_splash")]
    assert "_ticks_ms()" not in body, \
        "a millisecond bracket is back in the frame perf tail"
    assert "_flush_t0 = _ticks_us()" in body
    assert "_total = _ticks_diff(_ticks_us(), frame_t0)" in body
    # Converted ONCE, at the EMA -- every public number stays milliseconds.
    assert "self._flush_ms = _ema(self._flush_ms, _flush / 1000.0)" in body
    assert "self._draw_ms = _ema(self._draw_ms, _draw / 1000.0)" in body


def test_the_bar_bracket_uses_the_microsecond_clock():
    src = _src("runtime/console.py")
    hits = re.findall(r"self\._pf_bar = _ticks_diff\((_ticks_\w+)\(\)", src)
    assert hits, "the bar's CHROMEBRK bracket vanished"
    assert set(hits) == {"_ticks_us"}, hits


def test_the_device_diag_prints_the_stack_bucket():
    """CHROMEBRK is how any of this reaches glass; a five-tuple printed through
    a four-field format silently drops the new bucket."""
    src = _src("firmware/lilygo_t_deck_plus_micropython/modules/device_diag.py")
    assert "stk=%.2f other=%.2f" in src
    assert "if len(c) < 5:" in src, \
        "the older-console fallback is what keeps this line crash-free"
