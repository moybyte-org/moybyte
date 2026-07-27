"""The chrome glyph vocabulary renders from a memoised span list (#58 perf).

_blit_glyph used to walk the 12x12 mask bit by bit on every call: 144 iterations
of Python to emit ~14 native rects whose kernel time was ~1.5us. On glass that
made one glyph cost ~48us, and the Sprites tab draws 19 of them a frame -- 13ms
of a 52ms tab (tools/p4_attrib.py). The spans are now computed once per
(kind, scale).

These tests pin the thing that matters: the cached path must emit the EXACT same
rect sequence as the bit walk, for every glyph and every scale, because the
320x240 baselines are byte-identical contracts (#39).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import chrome                        # noqa: E402


class RecCanvas:
    """Records rect() calls; font_scale drives the glyph scale. Implements
    fill_rects (#163) by expanding quads to rect tuples, so the oracle compares
    the packed-quad path _blit_glyph now takes against the bit walk."""

    def __init__(self, font_scale=1):
        self.font_scale = font_scale
        self.rects = []

    def rect(self, x, y, w, h, c):
        self.rects.append((x, y, w, h, c))

    def fill_rects(self, arr, n=-1, ox=0, oy=0, c=-1):
        if n is None or n < 0:
            n = len(arr) // 5
        for i in range(0, n * 5, 5):
            self.rect(arr[i] + ox, arr[i + 1] + oy, arr[i + 2], arr[i + 3],
                      c if c >= 0 else arr[i + 4])


def _reference(cv, kind, rect, c, scale=None):
    """The pre-cache implementation, verbatim, as the oracle."""
    bits = chrome._GLYPHS.get(kind)
    if bits is None:
        return
    x, y, w, h = rect
    n = chrome._GLYPH_SIZE
    fs = int(scale) if scale else getattr(cv, "font_scale", 1)
    if fs < 1:
        fs = 1
    span = n * fs
    ox = x + (w - span) // 2
    oy = y + (h - span) // 2
    for r in range(n):
        row = bits[r]
        if not row:
            continue
        yy = oy + r * fs
        run = 0
        for col in range(n):
            if row & (1 << (n - 1 - col)):
                run += 1
            elif run:
                cv.rect(ox + (col - run) * fs, yy, run * fs, fs, c)
                run = 0
        if run:
            cv.rect(ox + (n - run) * fs, yy, run * fs, fs, c)


def test_every_glyph_at_every_scale_matches_the_bit_walk():
    for kind in sorted(chrome._GLYPHS):
        for fs in (1, 2, 3):
            for rect in ((0, 0, 12, 12), (7, 31, 40, 24), (100, 5, 13, 12)):
                got = RecCanvas(fs)
                want = RecCanvas(fs)
                chrome._blit_glyph(got, kind, rect, 7)
                _reference(want, kind, rect, 7)
                assert got.rects == want.rects, (kind, fs, rect)


def test_explicit_scale_overrides_the_canvas_font_scale():
    got, want = RecCanvas(1), RecCanvas(1)
    chrome._blit_glyph(got, "run", (0, 0, 60, 60), 7, scale=4)
    _reference(want, "run", (0, 0, 60, 60), 7, scale=4)
    assert got.rects == want.rects
    assert got.rects and max(r[3] for r in got.rects) == 4


def test_unknown_kind_draws_nothing():
    # The fallback contract: every caller keeps a text label for this case.
    cv = RecCanvas(1)
    chrome._blit_glyph(cv, "no_such_glyph", (0, 0, 12, 12), 7)
    assert cv.rects == []


def test_spans_are_cached_per_kind_and_scale():
    chrome._GLYPH_RUNS.clear()
    cv = RecCanvas(2)
    chrome._blit_glyph(cv, "run", (0, 0, 24, 24), 7)
    first = chrome._GLYPH_RUNS[("run", 2)]
    chrome._blit_glyph(cv, "run", (40, 0, 24, 24), 7)
    assert chrome._GLYPH_RUNS[("run", 2)] is first     # reused, not rebuilt
    chrome._blit_glyph(cv, "run", (0, 0, 12, 12), 7, scale=1)
    assert ("run", 1) in chrome._GLYPH_RUNS            # a scale is its own entry


def test_a_blank_glyph_row_emits_no_span():
    # Several glyphs have empty top/bottom rows; they must not cost a rect.
    runs = chrome._glyph_runs("run", 1)
    assert len(runs) % 3 == 0
    ys = {runs[i + 1] for i in range(0, len(runs), 3)}
    assert 0 not in ys                     # "run" row 0 is 0x000
