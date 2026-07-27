"""#163 span-batch verb: `fill_rects` must be byte-identical to the rect loop.

The contract every backend implements (host Canvas here; the device DrawCtx
method mirrors gate_fill, which mirrors _fill; the web recorders expand to
plain rect ops): n packed int16 quads (x, y, w, h, ci), an (ox, oy) shift for
relative span lists, and a call-level color override. Camera, clip and the
palette map must apply exactly as cv.rect applies them.
"""

import sys
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime.canvas import Canvas                 # noqa: E402

QUADS = [
    (3, 4, 10, 6, 5),
    (-4, -2, 8, 8, 9),          # clips the top-left corner
    (300, 200, 60, 60, 12),     # clips the bottom-right corner
    (50, 50, 0, 4, 1),          # empty width -> nothing
    (120, 30, 1, 180, 14),      # tall 1px line (the lattice shape)
]


def _pack(quads):
    lst = []
    for q in quads:
        lst.extend(q)
    return array("h", lst)


def _canvas():
    cv = Canvas(320, 240)
    cv.cls(0)
    return cv


def _snap(cv):
    return bytes(cv.buf)


def test_fill_rects_matches_the_rect_loop():
    a, b = _canvas(), _canvas()
    a.fill_rects(_pack(QUADS))
    for x, y, w, h, ci in QUADS:
        b.rect(x, y, w, h, ci)
    assert _snap(a) == _snap(b)


def test_offset_and_color_override():
    a, b = _canvas(), _canvas()
    a.fill_rects(_pack(QUADS), -1, 7, -3, 11)
    for x, y, w, h, _ci in QUADS:
        b.rect(x + 7, y - 3, w, h, 11)
    assert _snap(a) == _snap(b)


def test_camera_and_clip_apply_like_rect():
    a, b = _canvas(), _canvas()
    for cv in (a, b):
        cv.camera(5, 9)
        cv.clip(10, 10, 100, 80)
    a.fill_rects(_pack(QUADS))
    for x, y, w, h, ci in QUADS:
        b.rect(x, y, w, h, ci)
    a.camera()
    b.camera()
    a.clip()
    b.clip()
    assert _snap(a) == _snap(b)


def test_count_limits_the_draw():
    a, b = _canvas(), _canvas()
    a.fill_rects(_pack(QUADS), 2)
    for x, y, w, h, ci in QUADS[:2]:
        b.rect(x, y, w, h, ci)
    assert _snap(a) == _snap(b)
