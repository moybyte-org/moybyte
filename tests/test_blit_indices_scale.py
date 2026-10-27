"""`blit_indices_scale` against the per-pixel path it replaces, byte for byte.

The verb is the sprite pickers' kernel (docs/scroll_and_text_2026-10.md §9):
a source rect of an index sheet at an integer scale, one palette lookup per
SOURCE pixel. Its contract is sspr's pixels for a destination of
sw*scale x sh*scale, so every case here draws the same call both ways on the
real DeviceCanvas over the host kernel -- sspr through libmoy's moy_sspr, the
new verb through moy_gfx's compositor, two independent loops -- over a
patterned ground, so a pixel either leaves as it was shows. The host binding
against the compiled MicroPython module is tests/test_gfx_binding.py's op
script; the C on glass is each on-glass suite's case.
"""

import random

import pytest

from runtime import host_canvas
from runtime.editors import SpriteSheet

W, H = 160, 120


def _sheet():
    sheet = SpriteSheet(16, 32)
    rnd = random.Random(7)
    pix = sheet.pix
    for i in range(len(pix)):
        # Runs of one index and runs of 0 (the pickers' clear), so a block
        # edge and a colorkey hole both land inside the drawn rects.
        pix[i] = 0 if rnd.random() < 0.3 else rnd.randrange(64)
    return sheet


SHEET = _sheet()


def _ground(c):
    for y in range(0, H, 5):
        for x in range(0, W, 7):
            c.rect(x, y, 7, 5, (x // 7 + y // 5) % 64)


def _pair(setup):
    a = host_canvas.make_canvas(W, H)
    b = host_canvas.make_canvas(W, H)
    for c in (a, b):
        _ground(c)
        setup(c)
    return a, b


def _check(a, b, label):
    pa = memoryview(a._buf).cast("H")
    pb = memoryview(b._buf).cast("H")
    if bytes(a._buf) != bytes(b._buf):
        diff = [i for i in range(len(pa)) if pa[i] != pb[i]]
        i = diff[0]
        stride = a._stride
        raise AssertionError("%s: %d px differ, first at (%d, %d): sspr %04x, "
                             "scaled %04x" % (label, len(diff), i % stride,
                                              i // stride, pa[i], pb[i]))


def _none(c):
    pass


def _clip(c):
    c.clip(13, 9, 101, 77)


def _camera(c):
    c.camera(-11, 6)


def _pal(c):
    for i in range(0, 64, 3):
        c.pal(i, (i * 5 + 1) % 64)


def _palt(c):
    c.palt(0, False)
    c.palt(17, True)
    c.palt(40, True)


def _all(c):
    _clip(c)
    _camera(c)
    _pal(c)
    _palt(c)


STATES = {"plain": _none, "clip": _clip, "camera": _camera, "pal": _pal,
          "palt": _palt, "all": _all}

# (sx, sy, sw, sh, dx, dy): a tile, a sprite-sized span, the picker's whole
# sheet, odd offsets and sizes, and rects that overhang every edge of the
# destination.
RECTS = [
    (0, 0, 8, 8, 4, 4),
    (8, 16, 16, 16, 20, 10),
    (0, 0, 128, 256, 0, 0),
    (3, 5, 13, 7, 17, 23),
    (40, 100, 33, 19, -9, -5),
    (100, 200, 28, 56, 140, 100),
    (1, 1, 1, 1, 70, 60),
]


@pytest.mark.parametrize("state", sorted(STATES))
@pytest.mark.parametrize("scale", [1, 2, 3, 4, 5, 8])
@pytest.mark.parametrize("colorkey", [-1, 0, 17])
def test_it_draws_sspr_pixels(state, scale, colorkey):
    for sx, sy, sw, sh, dx, dy in RECTS:
        a, b = _pair(STATES[state])
        a.sspr(SHEET, sx, sy, sw, sh, dx, dy, sw * scale, sh * scale, colorkey)
        b.blit_indices_scale(SHEET, sx, sy, sw, sh, dx, dy, scale, colorkey)
        _check(a, b, "%s scale %d key %d rect %s"
               % (state, scale, colorkey, (sx, sy, sw, sh, dx, dy)))


@pytest.mark.parametrize("rect", [(-3, 0, 10, 8), (0, -5, 8, 12),
                                  (120, 250, 20, 20), (-4, -4, 140, 270)])
@pytest.mark.parametrize("scale", [1, 3])
def test_a_rect_past_the_sheet_draws_nothing_there(rect, scale):
    # sspr samples 0 past the sheet; under colorkey 0 that is nothing drawn,
    # which is this verb's contract for the part outside the bitmap.
    sx, sy, sw, sh = rect
    a, b = _pair(_none)
    a.sspr(SHEET, sx, sy, sw, sh, 9, 7, sw * scale, sh * scale, 0)
    b.blit_indices_scale(SHEET, sx, sy, sw, sh, 9, 7, scale, 0)
    _check(a, b, "rect %s scale %d" % (rect, scale))


def test_a_degenerate_call_draws_nothing():
    a, b = _pair(_none)
    for args in ((0, 0, 0, 8, 1, 1, 2), (0, 0, 8, -1, 1, 1, 2),
                 (0, 0, 8, 8, 1, 1, 0), (0, 0, 8, 8, 1, 1, -2),
                 (200, 0, 8, 8, 1, 1, 2), (0, 300, 8, 8, 1, 1, 2),
                 (0, 0, 8, 8, W, 1, 2), (0, 0, 8, 8, 1, -16, 2)):
        b.blit_indices_scale(SHEET, *args)
    _check(a, b, "degenerate")

