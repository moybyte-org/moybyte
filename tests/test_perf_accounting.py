"""The frame's perf split has to be in the units it says.

`chrome` is a residual (draw - upd - cart - audio), and on integer
MILLISECONDS each term truncated toward zero with every loss landing in the
last one -- up to several ms of manufactured cost in the bucket that was being
read to explain an S3 frame. Fixed 2026-08-14: every bracket is microseconds,
converted once, at the EMA, so every public number stays ms.

A units regression has no natural failure signal. It does not crash and does
not move a golden -- it makes a number quietly wrong in the direction of
"there is a mystery here", which is the most expensive kind of wrong this
project has hit. So: source pins that the brackets are on the microsecond
clock.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


# -- the brackets are on the microsecond clock ------------------------------

def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_the_cart_phase_brackets_use_the_microsecond_clock():
    """player.tick's four brackets (backdrop / _update / _draw / audio) feed
    DRAWBRK directly and `chrome` by subtraction. On ticks_ms each lost up to
    1ms into the residual."""
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
    src = _src("runtime/console_perf.py")
    body = src[src.index("def _frame_perf_end"):
               src.index("def perf_sample")]
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
    assert hits, "the bar's bracket vanished"
    assert set(hits) == {"_ticks_us"}, hits
