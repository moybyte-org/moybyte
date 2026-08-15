"""A cart's own palette (SPEC.md 3.1), on the raster every tier draws with.

This was host-only and nobody knew. `runtime/player.py` assigns
`ws.canvas.palette = table` when a cart ships one and restores it on exit, and
player.py is STAGED TO BOTH BOARDS -- but `DeviceCanvas` had no `palette` at
all, so on device the assignment set an attribute nothing read.

VERIFIED ON P4 GLASS 2026-08-15, before the fix: `hasattr(ws.canvas, "palette")`
was False, assigning one was accepted, and `PAL565[8]` stayed `0xf809`. A cart
palette worked in the simulator and was inert on the hardware it shipped to --
the same shape as the T-Deck's missing web console and the wallpaper preview
that never rendered: a feature that looks supported because nothing fails.

So these tests drive the REAL DeviceCanvas (through runtime/host_canvas, which
is the boards' class on CPython), not a host-only twin. `test_moy_spec_cart`
covers the player-level save/restore; this covers the canvas that has to honour
it.
"""

import struct

import pytest

from runtime import host_canvas
from runtime.palette import MOY64

host_canvas.install()
import device_canvas as dc                                      # noqa: E402


def _canvas(w=8, h=4):
    return host_canvas.make_canvas(w, h)


def _word0(cv):
    """The first pixel as a raw 565 word, in whatever order this build writes."""
    return struct.unpack("<H", bytes(cv._buf[:2]))[0]


def test_the_baked_rgb_table_is_the_real_MOY64():
    """MOY64_RGB is a literal twin of runtime/palette.py, which cannot be staged
    to a board (it builds its ramp with CPython's colorsys). A twin that drifts
    would recolour every cart on device and nothing else would notice."""
    m = dc.MOY64_RGB
    assert len(m) == 64 * 3
    baked = [(m[i * 3], m[i * 3 + 1], m[i * 3 + 2]) for i in range(64)]
    assert baked == [tuple(c) for c in MOY64]


def test_a_canvas_reports_the_stock_palette():
    cv = _canvas()
    assert len(cv.palette) == 64
    assert tuple(cv.palette[8]) == tuple(MOY64[8])       # PICO-8 red, FF004D


def test_a_cart_palette_changes_what_a_draw_writes():
    cv = _canvas()
    cv.cls(8)
    stock = _word0(cv)
    cv.palette = [(255, 0, 0)] * 64
    cv.cls(8)
    assert _word0(cv) != stock, "the palette swap did not reach the raster"
    assert tuple(cv.palette[8]) == (255, 0, 0)


def test_restoring_the_table_restores_the_pixels():
    """player.py's exit path: it saves the table it found and assigns it back."""
    cv = _canvas()
    saved = [tuple(c) for c in cv.palette]
    cv.cls(8)
    stock = _word0(cv)
    cv.palette = [(255, 0, 0)] * 64
    cv.cls(8)
    cv.palette = saved
    cv.cls(8)
    assert _word0(cv) == stock


def test_the_stock_canvas_shares_the_module_table():
    """Copy-on-write: a console that never sees a cart palette allocates no
    private LUT, which is what keeps this free on a board."""
    cv = _canvas()
    assert cv._wire is dc._PAL565_WIRE_BUF
    cv.palette = [(1, 2, 3)] * 64
    assert cv._wire is not dc._PAL565_WIRE_BUF


def test_a_malformed_table_is_ignored_rather_than_raising():
    """A draw verb that throws mid-frame takes the cart down; the device's other
    malformed-input guards are all silent, and this matches them."""
    cv = _canvas()
    before = list(cv.palette)
    for bad in (None, [], [(0, 0, 0)] * 63, [(0, 0, 0)] * 65):
        cv.palette = bad
        assert list(cv.palette) == before


def _ink(cv):
    """The brightest word on the canvas -- the colour a verb just drew."""
    return max(struct.unpack("<%dH" % (cv.w * cv.h), bytes(cv._buf)))


def test_every_verb_resolves_through_the_same_table():
    """The half of this fix I shipped without at first.

    Colour resolution lives in several places: `_col` (cls/pix/line/circ), the
    C gate's mirrored table, and the direct `PAL565_WIRE[...]` reads inside
    rect/rectb/print and the sprite paths. The first commit converted `_col` and
    the gate and left the rest on the module table, so `cls(8)` drew the cart's
    colour and `rect(..., 8)` drew stock MOY64 -- on the same canvas, in the same
    frame. Caught by review, not by a test, which is why there is one now.
    """
    verbs = {
        "cls": lambda c: c.cls(8),
        "rect": lambda c: (c.cls(0), c.rect(0, 0, 4, 4, 8)),
        "rectb": lambda c: (c.cls(0), c.rectb(0, 0, 6, 6, 8)),
        "circ": lambda c: (c.cls(0), c.circ(4, 4, 3, 8)),
        "line": lambda c: (c.cls(0), c.line(0, 0, 7, 3, 8)),
        "print": lambda c: (c.cls(0), c.print("A", 0, 0, 8)),
    }
    stock, cart = {}, {}
    for name, draw in verbs.items():
        cv = _canvas(16, 8)
        draw(cv)
        stock[name] = _ink(cv)
        cv.palette = [(255, 0, 0)] * 64
        draw(cv)
        cart[name] = _ink(cv)
    assert len(set(stock.values())) == 1, (
        "verbs disagree on the STOCK palette: %s"
        % {k: hex(v) for k, v in stock.items()})
    assert len(set(cart.values())) == 1, (
        "verbs disagree under a cart palette -- some still read the module "
        "table: %s" % {k: hex(v) for k, v in cart.items()})
    assert set(stock.values()) != set(cart.values()), "the swap changed nothing"


def test_a_palette_swap_invalidates_pal_keyed_bakes():
    """Sprite variants are cached on (scale, flip, _palgen), and an identity pal
    map ids as 0. Without the epoch a cart that ships a palette but never calls
    pal() would key both tables as 0 and reuse the other one's baked pixels."""
    cv = _canvas()
    first = cv._palgen
    cv.palette = [(255, 0, 0)] * 64
    assert cv._palgen != first, "pal-state id collided across a palette swap"
