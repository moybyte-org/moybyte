"""The host's Lua runs on the boards' PIXEL, not just their VM (#161).

`runtime/lua_binding.py` compiles libmoy for CPython, and it used to compile it
INDEXED: one byte a pixel, bound straight to the buffer of `runtime/canvas.py`,
the host's own raster. That file is deleted and the host canvas is the boards'
`DeviceCanvas`, which is RGB565 -- and `sizeof(moy_pixel)` is not a detail
libmoy carries at runtime. A library built the other way computes `y*w+x` over
the wrong stride: half-width rows of raw palette indices, written into a
direct-colour framebuffer, with nothing anywhere reporting a problem. So the
format is checked HERE, by drawing.

The check that matters is the one at the top: the same picture, drawn once by a
Lua cart through libmoy's verb table and once by the canvas's own Python verbs,
must be the same bytes. That is the real contract -- a Lua cart and a Python
cart are two ways of asking for one raster -- and it is what a stride or a
palette-resolution mistake cannot survive.

Skipped without a C compiler, like the other host bindings.
"""

import pytest

from runtime import host_canvas, lua_binding as lb, lua_host
from runtime.editors_sheet import SpriteSheet

host_canvas.install()
import device_canvas as dc                                      # noqa: E402

pytestmark = pytest.mark.skipif(
    not lb.HostLuaRun.available(),
    reason="no C compiler for the host lua binding")

W, H = 96, 64


def _sheet():
    """A deterministic 128x256 sheet -- SPEC.md 3.2's fixed geometry, which is
    what libmoy addresses with. Pixel values 0-15, as a sheet holds nibbles."""
    s = SpriteSheet(16, 32)
    v = 0
    for i in range(len(s.pix)):
        v = (v * 1103515245 + 12345) & 0x7FFFFFFF
        s.pix[i] = (v >> 16) & 15
    return s


class _Tilemap:
    def __init__(self, w=16, h=12):
        self.w, self.h = w, h
        self.cells = bytearray((i * 7) % 40 for i in range(w * h))


def _run(canvas, lua, sheet=None, tilemap=None):
    """Draw `lua` into `canvas` through the binding, as the console would."""
    buf, wire, indexed = lua_host.canvas_target(canvas)
    r = lb.HostLuaRun(buf, canvas.w, canvas.h, sheet, tilemap,
                      wire=wire, indexed=indexed)
    try:
        assert r.load("function _draw() %s end" % lua, "@cart") is None
        assert r.tick(1 / 30.0) is None
    finally:
        r.close()
    return bytes(buf)


def _differing(a, b):
    return sum(1 for i in range(0, len(a), 2) if a[i:i + 2] != b[i:i + 2])


# One picture per row, said twice: as a cart says it, and as the console's own
# chrome says it. spr QUEUES into device_canvas's batch, so the Python side
# flushes -- the cart's frame ends with libmoy's own equivalent.
SHEET = _sheet()
TILEMAP = _Tilemap()

CASES = (
    ("cls", "cls(3)",
     lambda c, s, m: c.cls(3)),
    ("rect", "cls(0) rect(4,5,20,11,9)",
     lambda c, s, m: (c.cls(0), c.rect(4, 5, 20, 11, 9))),
    ("rectb", "cls(0) rectb(4,5,20,11,9)",
     lambda c, s, m: (c.cls(0), c.rectb(4, 5, 20, 11, 9))),
    ("line", "cls(0) line(1,2,80,50,12)",
     lambda c, s, m: (c.cls(0), c.line(1, 2, 80, 50, 12))),
    ("circ", "cls(0) circ(40,30,17,10)",
     lambda c, s, m: (c.cls(0), c.circ(40, 30, 17, 10))),
    ("circb", "cls(0) circb(40,30,17,10)",
     lambda c, s, m: (c.cls(0), c.circb(40, 30, 17, 10))),
    ("tri", "cls(0) tri(3,3,70,20,25,60,14)",
     lambda c, s, m: (c.cls(0), c.tri(3, 3, 70, 20, 25, 60, 14))),
    ("trib", "cls(0) trib(3,3,70,20,25,60,14)",
     lambda c, s, m: (c.cls(0), c.trib(3, 3, 70, 20, 25, 60, 14))),
    ("pix", "cls(0) pix(9,9,5) pix(95,63,6)",
     lambda c, s, m: (c.cls(0), c.pix(9, 9, 5), c.pix(95, 63, 6))),
    ("print", "cls(0) print('Hi 42!', 5, 9, 7)",
     lambda c, s, m: (c.cls(0), c.print("Hi 42!", 5, 9, 7, 1))),
    ("camera", "cls(0) camera(5,7) rect(10,10,20,9,8) camera()",
     lambda c, s, m: (c.cls(0), c.camera(5, 7), c.rect(10, 10, 20, 9, 8),
                      c.camera())),
    ("clip", "cls(0) clip(10,10,30,20) rect(0,0,90,60,11) clip()",
     lambda c, s, m: (c.cls(0), c.clip(10, 10, 30, 20),
                      c.rect(0, 0, 90, 60, 11), c.clip())),
    ("pal", "cls(0) pal(8,12) rect(2,2,40,20,8) pal()",
     lambda c, s, m: (c.cls(0), c.pal(8, 12), c.rect(2, 2, 40, 20, 8),
                      c.pal())),
    ("spr", "cls(0) spr(5, 10, 12)",
     lambda c, s, m: (c.cls(0), c.spr_tile(s, 5, 10, 12), c.flush_batch())),
    ("spr_colorkey", "cls(0) spr(5, 10, 12, 0)",
     lambda c, s, m: (c.cls(0), c.spr_tile(s, 5, 10, 12, 0), c.flush_batch())),
    ("spr_scaled", "cls(0) spr(9, 4, 4, -1, 2)",
     lambda c, s, m: (c.cls(0), c.spr_tile(s, 9, 4, 4, -1, 2),
                      c.flush_batch())),
    ("spr_flipped", "cls(0) spr(9, 4, 4, -1, 1, 1)",
     lambda c, s, m: (c.cls(0), c.spr_tile(s, 9, 4, 4, -1, 1, 1),
                      c.flush_batch())),
    ("spr_run", "cls(0) for i=0,9 do spr(i, i*8, i*5, 0) end",
     lambda c, s, m: (c.cls(0),
                      [c.spr_tile(s, i, i * 8, i * 5, 0) for i in range(10)],
                      c.flush_batch())),
    ("palt", "cls(0) palt(3, true) spr(5, 10, 12, 0)",
     lambda c, s, m: (c.cls(0), c.palt(3, True), c.spr_tile(s, 5, 10, 12, 0),
                      c.flush_batch())),
    ("sspr", "cls(0) sspr(8,8,16,16,10,10,32,32)",
     lambda c, s, m: (c.cls(0), c.sspr(s, 8, 8, 16, 16, 10, 10, 32, 32))),
    ("map", "cls(0) map(0,0,8,6,3,4)",
     lambda c, s, m: (c.cls(0), c.map(m, s, 0, 0, 8, 6, 3, 4))),
    # u/v/du/dv are 16.16 texels into the map-as-texture, so these are (8,8)
    # stepping one texel a pixel -- (0,0) is an empty cell and would draw
    # nothing, which is a comparison of two blank canvases.
    ("tline", "cls(0) tline(2,2,80,50,524288,524288,65536,32768)",
     lambda c, s, m: (c.cls(0), c.tline(m, s, 2, 2, 80, 50,
                                        524288, 524288, 65536, 32768))),
)


@pytest.mark.parametrize("name,lua,py", CASES, ids=[c[0] for c in CASES])
def test_a_lua_cart_draws_the_same_pixels_as_the_canvas_verbs(name, lua, py):
    """The whole point, verb by verb: ONE raster, two ways of asking.

    Both sides here are libmoy compiled RGB565 -- the cart reaches it through
    the Lua table, the canvas through moy_gfx -- so a difference is the BINDING
    (a stride, an unresolved index, a wire table that never arrived), which is
    exactly the class of bug the format change could introduce.
    """
    lua_px = _run(host_canvas.make_canvas(W, H), lua, SHEET, TILEMAP)
    ref = host_canvas.make_canvas(W, H)
    py(ref, SHEET, TILEMAP)
    assert _differing(lua_px, bytes(ref._buf)) == 0


def test_the_cart_drew_something_in_every_case():
    """A guard on the guard: comparing two blank canvases passes forever."""
    for name, lua, _py in CASES:
        px = _run(host_canvas.make_canvas(W, H), lua, SHEET, TILEMAP)
        assert any(px), name


def test_a_565_canvas_gets_two_byte_words_not_indices():
    """The failure the old build would have produced, stated directly: an
    indexed libmoy writes `8` into byte 0 and leaves byte 1 alone, and fills
    only the left half of the canvas."""
    c = host_canvas.make_canvas(W, H)
    _run(c, "cls(8)")
    word = dc.PAL565_WIRE[8]
    expect = bytes((word & 0xFF, word >> 8)) * (W * H)   # little-endian, as stored
    assert bytes(c._buf) == expect


def test_the_canvas_wire_table_is_what_a_draw_resolves_through():
    """A 565 canvas resolves colour at DRAW time, so libmoy has to be TOLD what
    an index looks like. A cart's own SPEC.md 3.1 palette rewrites that table,
    and reading the module constant instead of the canvas's would silently draw
    every cart-palette cart in stock colours."""
    c = host_canvas.make_canvas(8, 4)
    c.palette = [(255, 0, 255)] * 64
    assert c._wire is not dc._PAL565_WIRE_BUF           # a private table now
    _run(c, "cls(8)")
    ref = host_canvas.make_canvas(8, 4)
    ref.palette = [(255, 0, 255)] * 64
    ref.cls(8)
    assert bytes(c._buf) == bytes(ref._buf)
    assert bytes(c._buf) != bytes(bytearray(8 * 4 * 2))


# -- the indexed lane, which no tier ships any more ---------------------------
#
# `HostLuaRun(indexed=True)` keeps a 565 SHADOW with an identity wire table and
# narrows the frame back out on each tick. It existed for `runtime/canvas.py`,
# the host's deleted indexed raster; the binding still offers it, so it is still
# driven -- DIRECTLY, over a bare bytearray, rather than through a canvas class,
# because there is no longer a canvas class that speaks it. Retiring the lane is
# a separate call (it lives in moyhost_lua.c too); until then this is what keeps
# it from rotting.

class _IndexCanvas:
    """The minimum `lua_host.canvas_target` calls indexed: `.buf`, `w`, `h`."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.buf = bytearray(w * h)


def test_an_indexed_canvas_draws_the_same_picture_one_byte_wide():
    """The bridge is a widen and a narrow, so the two lanes are the same frame
    at two widths: map the indices through the wire table and the buffers must
    be equal, pixel for pixel, for every verb above."""
    for name, lua, _py in CASES:
        idx = _run(_IndexCanvas(W, H), lua, SHEET, TILEMAP)
        wide = _run(host_canvas.make_canvas(W, H), lua, SHEET, TILEMAP)
        expect = bytearray()
        for i in idx:
            word = dc.PAL565_WIRE[i]
            expect += bytes((word & 0xFF, word >> 8))
        assert bytes(expect) == wide, name


def test_lua_host_reads_the_format_off_the_canvas_it_was_handed():
    """The one decision this file exists to pin: the runtime asks the canvas
    rather than assuming what it was handed."""
    buf, wire, indexed = lua_host.canvas_target(host_canvas.make_canvas(8, 4))
    assert indexed is False and wire is not None and len(buf) == 8 * 4 * 2
    buf, wire, indexed = lua_host.canvas_target(_IndexCanvas(8, 4))
    assert indexed is True and wire is None and len(buf) == 8 * 4


# -- the bounds ctypes cannot check -------------------------------------------

def test_an_undersized_canvas_is_refused_rather_than_overrun():
    """ctypes hands C a bare pointer, so a w*h past the end of the buffer is a
    heap overwrite with no Python-side trace. The C checks and returns NULL."""
    with pytest.raises(RuntimeError):
        lb.HostLuaRun(bytearray(64 * 32 * 2), 64, 64, indexed=False)
    with pytest.raises(RuntimeError):
        lb.HostLuaRun(bytearray(64 * 32), 64, 64, indexed=True)


def test_a_sheet_that_is_not_the_spec_geometry_is_declined():
    """libmoy addresses a sheet as 128x256 whatever it was handed, so a shorter
    one is an out-of-bounds READ on every sprite. The device raises on it; this
    declines the sheet, and the cart draws nothing rather than garbage."""
    # spec=False is the point of the fixture: SpriteSheet REFUSES to build a
    # non-spec sheet unasked now (that default was this bug's other half), and the
    # opt-out is what lets a test still hand libmoy the shape it declines.
    small = SpriteSheet(16, 16, spec=False)     # 128x128: half a spec sheet
    for i in range(len(small.pix)):
        small.pix[i] = 7
    px = _run(host_canvas.make_canvas(W, H), "cls(0) spr(5, 10, 12)", small)
    assert not any(px)
