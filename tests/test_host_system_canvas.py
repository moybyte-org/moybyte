"""The host's SYSTEM canvas, on the boards' raster (#161).

`runtime/host_canvas.py` runs `DeviceCanvas` on CPython; `HostSystemCanvas` is
the rest of what the shared console asks a system SURFACE for:

  * font_scale text and font-scale-carrying layers (#39/#73),
  * `blit_cover`, the wallpaper's cover-crop composite,
  * `to_rgb888`, the readout pygame blits and the GIF export writes.

WHAT THIS FILE USED TO BE, AND WHY IT IS SMALLER. Its acceptance test was
byte-identity against `runtime/canvas.py`: every scene was drawn twice, once on
the shipping indexed host raster and once here, and the two readouts compared
byte for byte. That was the check that made swapping the host's canvas a swap
rather than a new look. The swap landed and `runtime/canvas.py` is deleted, so
those eight comparisons became `new == new` -- a suite that cannot fail. They
are retired rather than re-pointed: what they asserted about the PIXELS is now
`tests/test_spec_conformance.py`, which replays the spec's own traces through
this exact canvas and hashes every frame against a golden nobody here can move.

What conformance does NOT reach is kept, and one of them is new. SPEC.md 6 fixes
cart text at 8px, so scaled system text has no golden at all -- and the two
lanes inside `HostSystemCanvas.print` (libmoy's kernel op, and the petme128
fallback for a build with no `moy_font`) are pinned against each other here.

Everything drives the REAL DeviceCanvas through the real host kernel, like
tests/test_cart_palette.py. No canvas is faked, because a twin of the class
under test proves nothing about the class under test.
"""

import pytest

from runtime import host_canvas
from runtime.editors import SpriteSheet, TileMap
from runtime.moy_image import Image
from runtime.palette import MOY64

host_canvas.install()
import device_canvas as dc                                       # noqa: E402
from runtime.host_canvas import HostSystemCanvas                 # noqa: E402

W, H = 96, 64


def _text_lanes(w=W, h=H, font_scale=1):
    """(kernel-text canvas, fallback-text canvas) of the same size.

    The second one has its native text op removed, which is the state of a
    checkout where nobody has built firmware: `moy_font` is what build.sh stages
    runtime/font.py AS, and `device_canvas` gates the kernel op on importing it.
    install() registers the canonical module under that name so the host does
    not depend on a build artefact -- but the fallback still has to draw the
    same glyphs, or a clean tree renders text nobody has looked at.
    """
    a = host_canvas.make_system_canvas(w, h, font_scale=font_scale)
    b = host_canvas.make_system_canvas(w, h, font_scale=font_scale)
    b._gfx_text = None
    return a, b


def _same(old, new, label):
    a = old.to_rgb888()
    b = new.to_rgb888()
    assert len(a) == len(b) == old.w * old.h * 3, label
    if a != b:
        diff = sum(1 for i in range(0, len(a), 3) if a[i:i + 3] != b[i:i + 3])
        first = next(i // 3 for i in range(0, len(a), 3)
                     if a[i:i + 3] != b[i:i + 3])
        raise AssertionError(
            "%s: %d/%d px differ (first at %d,%d: a=%s b=%s)"
            % (label, diff, old.w * old.h, first % old.w, first // old.w,
               a[first * 3:first * 3 + 3], b[first * 3:first * 3 + 3]))


# --------------------------------------------------------------------------- #
# to_rgb888: byte-identity with the raster it replaces.                       #
# --------------------------------------------------------------------------- #
def _primitives(c):
    c.cls(1)
    c.rect(4, 4, 30, 20, 8)
    c.rectb(40, 4, 25, 18, 12)
    c.circ(20, 45, 10, 11)
    c.circb(60, 45, 12, 9)
    c.line(0, 0, W - 1, H - 1, 7)
    c.tri(70, 5, 90, 5, 80, 25, 14)
    c.trib(6, 34, 30, 34, 18, 58, 10)
    c.pix(3, 60, 10)


def _camera_and_clip(c):
    c.cls(2)
    c.camera(6, -4)
    c.clip(8, 8, 60, 40)
    c.rect(10, 10, 40, 30, 9)
    c.circ(30, 20, 12, 3)
    c.line(0, 0, W, H, 15)
    c.print("clipped", 10, 12, 7)
    c.clip()
    c.camera()


def _text(c):
    c.cls(0)
    c.print("Moybyte 0123", 2, 4, 7)
    c.print("!@#$%^&*()", 2, 16, 12)
    c.print("edge", W - 12, 30, 8)          # clipped by the right edge
    c.print("under", 4, H - 4, 11)          # clipped by the bottom edge
    # Camera, clip and pal all ride text. The clip is the one that bites: the
    # canvas's no-kernel fallback is framebuf.text, which has no clip rect at
    # all, so a label that should stop at a panel edge runs past it.
    c.camera(-3, 2)
    c.clip(10, 34, 40, 12)
    c.pal(7, 14)
    c.print("panel edge stops here", 8, 36, 7)
    c.pal()
    c.clip()
    c.camera()


def _sprites(c, img):
    c.cls(3)
    c.spr(img, 5, 5, 2, 0)
    c.spr(img, 30, 30, 1, 3)
    c.palt(0, True)
    c.spr(img, 40, 5, 1, 0)
    c.palt()
    c.pal(8, 12)
    c.spr(img, 2, 40, 1, 0)
    c.pal()


def _paint_image(c, idx, iw, ih):
    c.cls(5)
    for pos in ((6, 4), (-5, -4), (W - 6, H - 3)):
        c.blit_indices(idx, iw, ih, pos[0], pos[1])


def _tiles(c, sheet, tilemap):
    c.cls(0)
    c.camera(2, 2)
    c.clip(4, 4, 50, 50)
    c.map(tilemap, sheet, 0, 0, 4, 4, 0, 0, -1, 2)
    c.clip()
    c.camera()


def _sprite_rows():
    return ["AB..", "C..D", ".EF.", "G..H"]


def _sprite_map():
    return {"A": 8, "B": 9, "C": 10, "D": 11, "E": 12, "F": 13, "G": 14, "H": 15}


def _sheet_and_map():
    # 16 x 32 tiles = the 128 x 256 sheet SPEC.md 3.2 fixes (runtime/project.py
    # builds exactly this). A differently-shaped sheet is REFUSED by libmoy's
    # map kernel and draws nothing at all -- silently, on every tier.
    sheet = SpriteSheet(16, 32)
    sheet.tset(1, 0, 0, 8)
    sheet.tset(1, 3, 3, 11)
    sheet.tset(1, 1, 2, 7)
    sheet.tset(2, 1, 1, 14)
    sheet.tset(2, 5, 5, 3)
    tilemap = TileMap(4, 4)
    tilemap.mset(0, 0, 1)
    tilemap.mset(2, 1, 2)
    tilemap.mset(3, 3, 1)
    return sheet, tilemap


def test_the_two_text_lanes_draw_the_same_glyphs():
    """libmoy's compiled-in petme128 against runtime/font.py, at every font
    scale the shell offers.

    These are two different programs drawing the same font, and only one of them
    is exercised by a normal run: the C op. The Python lane is what a clean
    checkout without a firmware build takes, and it is also the ONLY lane at
    font scales above 1 on a kernel with no scaled text op -- so a drift here is
    invisible until someone else's machine renders different chrome.

    The scene deliberately includes camera, clip and pal, because the clip is
    what bites: `DeviceCanvas`'s own no-kernel fallback is `framebuf.text`,
    which has no clip rect at all, and this class rasterizes petme128 itself
    rather than delegating to it for exactly that reason.
    """
    for fs in (1, 2, 3):
        kernel, fallback = _text_lanes(font_scale=fs)
        assert kernel._gfx_text is not None, "the native text op did not resolve"
        _text(kernel)
        _text(fallback)
        _same(kernel, fallback, "text fs=%d" % fs)


def test_the_scenes_the_conformance_goldens_now_own_still_draw():
    """A smoke pass over the scenes the retired identity tests drew.

    They are no longer compared against a second raster -- `test_spec_conformance`
    hashes this canvas's primitives, sprites, text, camera/clip/pal and map
    against the spec's goldens, which is a stronger claim than agreeing with a
    twin. What that suite does NOT touch is this SUBCLASS, its layers, or
    `blit_indices`, so the scenes stay as a "these verbs still reach pixels
    through a system surface" check.
    """
    c = host_canvas.make_system_canvas(W, H)
    for scene in (_primitives, _camera_and_clip, _text):
        c.cls(0)
        scene(c)
        assert len(set(memoryview(c._buf).cast("H"))) > 1, scene.__name__
    c.cls(0)
    _sprites(c, Image.from_ascii(_sprite_rows(), _sprite_map()))
    assert len(set(memoryview(c._buf).cast("H"))) > 1, "sprites"
    c.cls(0)
    iw, ih = 20, 12
    _paint_image(c, bytearray((r * 5 + q * 3) % 63
                              for r in range(ih) for q in range(iw)), iw, ih)
    assert len(set(memoryview(c._buf).cast("H"))) > 1, "blit_indices"
    c.cls(0)
    _tiles(c, *_sheet_and_map())
    assert len(set(memoryview(c._buf).cast("H"))) > 1, "map"


def test_a_layer_is_read_back_through_the_same_path():
    """Layers are surfaces: the WM's window buffers and the bar's strip cache
    are read back through `to_rgb888` too."""
    lay = host_canvas.make_system_canvas(W, H).new_layer(40, 24)
    lay.cls(4)
    lay.rect(2, 2, 20, 10, 9)
    lay.print("hi", 3, 14, 7)
    out = lay.to_rgb888()
    assert len(out) == 40 * 24 * 3
    assert out[:3] == bytes(MOY64[4])                 # the cls colour, resolved
    assert out[(2 * 40 + 2) * 3:(2 * 40 + 2) * 3 + 3] == bytes(MOY64[9])


# --------------------------------------------------------------------------- #
# to_rgb888: the trap it exists to avoid, and the palette it reads through.   #
# --------------------------------------------------------------------------- #
def test_the_readout_is_not_a_bit_expansion():
    """The tempting implementation -- take the 565 word and widen 5/6/5 to
    8/8/8 -- is wrong, and wrong in a way that looks right on a colour ramp.

    MOY64 is an AUTHORED table: 565 throws bits away on the way in and no
    expansion puts back what was thrown. This pins the size of the error, so
    nobody "simplifies" the readout into it later.
    """
    def expand(word):
        r, g, b = (word >> 11) & 0x1F, (word >> 5) & 0x3F, word & 0x1F
        return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))

    exact = [i for i in range(16)
             if expand(dc.PAL565[i]) == tuple(MOY64[i])]
    assert len(exact) == 1, exact          # only index 0 survives the round trip

    # And the real readout gets all 64 right, one flat colour at a time.
    for i in range(64):
        c = host_canvas.make_system_canvas(2, 2)
        c.cls(i)
        assert c.to_rgb888() == bytes(MOY64[i]) * 4, i


def test_a_cart_palette_reaches_the_readout():
    """SPEC.md 3.1: a cart ships its own 64-entry table and runtime/player.py
    assigns it to the canvas. The readout has to resolve through THAT table --
    reading through the module's stock LUT would show the kid MOY64 while the
    glass shows their own colours."""
    table = [(i * 3 % 256, 255 - i * 2, (i * 7) % 256) for i in range(64)]
    c = host_canvas.make_system_canvas(2, 2)
    c.palette = table
    c.cls(8)
    # 565 quantisation, then back: the readout is the palette entry itself.
    assert c.to_rgb888() == bytes(table[8]) * 4
    c.palette = [tuple(x) for x in MOY64]         # player.py's exit path
    c.cls(8)
    assert c.to_rgb888() == bytes(MOY64[8]) * 4


def test_a_word_no_palette_index_produced_still_reads_as_a_colour():
    """Not every word in the buffer came from the palette (a sprite bake nudges
    a pixel off the magenta colour-key; a raw 565 blit answers to nobody). The
    reverse lookup misses those, and the miss must not read as black -- the
    fallback is the one place a 5/6/5 expansion is the right answer."""
    c = host_canvas.make_system_canvas(2, 1)
    c.cls(0)
    stray = 0xF81F                                # _RGB_KEY, absent from MOY64
    wire = (((stray & 0xFF) << 8) | (stray >> 8)) & 0xFFFF   # this build's order
    memoryview(c._buf).cast("H")[0] = wire
    out = c.to_rgb888()
    assert out[0:3] == bytes((255, 0, 255)), out[0:3]
    assert out[3:6] == bytes(MOY64[0])            # the untouched pixel is intact


def test_the_readout_is_the_viewport_not_the_buffer():
    """A viewport canvas (#155) draws into a sub-rect of a wider buffer, and
    `w`/`h` are the LOGICAL surface -- so a w*h*3 readout has to walk rows by
    stride. (The deleted indexed raster dumped the whole buffer instead; the two
    agreed on every full-surface canvas, which is every canvas that is read.)"""
    c = host_canvas.make_system_canvas(16, 8)
    c.cls(0)
    c.set_viewport(4, 2, 6, 3)
    c.cls(9)
    out = c.to_rgb888()
    assert len(out) == 6 * 3 * 3
    assert out == bytes(MOY64[9]) * (6 * 3)
    c.clear_viewport()
    assert len(c.to_rgb888()) == 16 * 8 * 3       # and the rest is still there


# --------------------------------------------------------------------------- #
# The system-surface contract: font scale, layers, cover.                    #
# --------------------------------------------------------------------------- #
def test_the_font_scale_is_set_before_the_base_init(monkeypatch):
    """DeviceCanvas.__init__ seeds the native draw gate's state array from
    `font_scale` (via _install_draw_gates). Set it afterwards -- the obvious
    order -- and every system surface gates at 1x until the next
    set_font_scale. Both other subclasses carry the same warning comment; this
    is the test of it that neither has."""
    seen = []
    real = dc.DeviceCanvas._install_draw_gates

    def spy(self):
        seen.append(getattr(self, "font_scale", None))
        return real(self)

    monkeypatch.setattr(HostSystemCanvas, "_install_draw_gates", spy,
                        raising=False)
    host_canvas.make_system_canvas(8, 8, font_scale=3)
    assert seen == [3], seen


def test_set_font_scale_exists_and_moves_the_gate_state():
    """console.py calls this UNGUARDED (the settings row and the font-scale
    relayout), so its absence is a boot crash, not a degradation. The gate array
    is None on the host kernel, so the write is exercised against a stand-in --
    the guard is what keeps the method correct on a build that does gate."""
    c = host_canvas.make_system_canvas(8, 8)
    c.set_font_scale(2)
    assert c.font_scale == 2
    c.set_font_scale(0)
    assert c.font_scale == 1                     # floored, like every tier

    from array import array
    c._gate_state = array("i", bytearray(4 * (dc._ST_FONT_SCALE + 1)))
    c.set_font_scale(3)
    assert c._gate_state[dc._ST_FONT_SCALE] == 3


def test_the_surface_and_its_layers_retain_one_frame():
    """RETAINED_FRAMES is 1, not the class default 2: HostCompositor holds ONE
    persistent buffer, so a scroll-as-blit surface (#113) must measure against
    the last paint. The P4 shipped this bug on its layers and it ghosted every
    card in a picker drag, on glass."""
    c = host_canvas.make_system_canvas(32, 32, font_scale=2)
    assert c.RETAINED_FRAMES == 1
    assert dc.DeviceCanvas.RETAINED_FRAMES == 2, "the default this overrides moved"
    lay = c.new_layer(16, 8)
    assert lay.RETAINED_FRAMES == 1


def test_a_layer_is_a_system_canvas_carrying_the_font_scale():
    """bar_layer, launcher_layer, wm_windowed and host_api all build layers and
    print into them. A bare DeviceCanvas layer would silently drop back to 8px
    text on a scaled desktop."""
    c = host_canvas.make_system_canvas(40, 40, font_scale=3)
    lay = c.new_layer(32, 24, owner="cart")       # `owner` accepted and ignored
    assert isinstance(lay, HostSystemCanvas)
    assert lay.font_scale == 3
    assert (lay.w, lay.h) == (32, 24)
    assert lay._nocache is True
    plain = host_canvas.make_system_canvas(40, 40).new_layer(32, 24)
    dark = bytes(MOY64[0])

    def lit(surface):
        surface.cls(0)
        surface.print("A", 0, 0, 7)
        out = surface.to_rgb888()
        return sum(1 for i in range(0, len(out), 3) if out[i:i + 3] != dark)

    # Nearest-neighbour: one glyph pixel becomes a 3x3 block, exactly.
    assert lit(lay) == lit(plain) * 9


def test_blit_cover_fills_the_whole_desktop():
    """wallpaper._backdrop_blit probes for this method; without it a 565 system
    canvas falls through to the palette-INDEX path, finds no index buffer and
    returns having drawn NOTHING -- a black desk with correct chrome on top,
    which is exactly what the wasm head's first build shipped. So: it exists,
    and it covers."""
    gc = host_canvas.make_canvas(320, 240)
    gc.cls(1)
    gc.rect(0, 0, 320, 120, 8)
    gc.rect(150, 110, 20, 20, 12)
    sc = host_canvas.make_system_canvas(1024, 600)
    sc.cls(0)
    sc.blit_cover(gc)

    black = bytes(MOY64[0]) * 1
    out = sc.to_rgb888()
    assert all(out[i:i + 3] != black for i in range(0, len(out), 3)), \
        "the cover left bare canvas -- it letterboxed instead of covering"

    # Cover, not fit: the smallest integer upscale that covers, centered and
    # cropped, so ox/oy are <= 0 and the source is sampled by nearest neighbour.
    scale = max(1, (1024 + 319) // 320, (600 + 239) // 240)
    ox = (1024 - 320 * scale) // 2
    oy = (600 - 240 * scale) // 2
    assert scale == 4 and ox < 0 and oy < 0
    src = memoryview(gc._buf).cast("H")
    dst = memoryview(sc._buf).cast("H")
    for x, y in ((0, 0), (1023, 599), (512, 300), (7, 411), (1000, 13)):
        assert dst[y * 1024 + x] == src[((y - oy) // scale) * 320 + (x - ox) // scale], \
            (x, y)


def test_blit_cover_at_scale_one_is_the_frame_itself():
    """The 320x240 tier (and the S3): cover degenerates to a straight copy."""
    gc = host_canvas.make_canvas(320, 240)
    gc.cls(2)
    gc.circ(160, 120, 60, 14)
    sc = host_canvas.make_system_canvas(320, 240)
    sc.cls(0)
    sc.blit_cover(gc)
    assert bytes(sc._buf) == bytes(gc._buf)


class _IndexSource:
    """The smallest thing `blit_cover` accepts that is NOT a DeviceCanvas: a
    surface publishing palette INDICES as `.buf`. Nothing in the tree ships one
    since `runtime/canvas.py` was deleted, which is precisely why the branch
    needs a caller here -- an untested fallback is a fallback that has rotted by
    the time something needs it."""

    def __init__(self, w, h, fill):
        self.w, self.h = w, h
        self.buf = bytearray([fill]) * (w * h)

    def rect(self, x, y, w, h, c):
        for yy in range(y, y + h):
            self.buf[yy * self.w + x:yy * self.w + x + w] = bytes([c]) * w


def test_blit_cover_takes_an_indexed_source_too():
    """`blit_cover`'s `_cover_py` lane resolves an index source through THIS
    canvas's table, and must land the same pixels the 565 source does -- so a
    backend that hands over indices composites identically."""
    gc = _IndexSource(320, 240, 1)
    gc.rect(0, 0, 320, 120, 8)
    sc = host_canvas.make_system_canvas(1024, 600)
    sc.cls(0)
    sc.blit_cover(gc)

    twin = host_canvas.make_canvas(320, 240)
    twin.cls(1)
    twin.rect(0, 0, 320, 120, 8)
    ref = host_canvas.make_system_canvas(1024, 600)
    ref.cls(0)
    ref.blit_cover(twin)
    assert sc.to_rgb888() == ref.to_rgb888()


def test_blit_cover_crops_a_source_bigger_than_the_surface():
    """A window smaller than the wallpaper frame: every destination pixel is
    still painted, from the middle of the source."""
    gc = host_canvas.make_canvas(320, 240)
    gc.cls(6)
    sc = host_canvas.make_system_canvas(100, 60)
    sc.cls(0)
    sc.blit_cover(gc)
    assert sc.to_rgb888() == bytes(MOY64[6]) * (100 * 60)


# --------------------------------------------------------------------------- #
# The game tier is untouched.                                                #
# --------------------------------------------------------------------------- #
def test_make_canvas_still_returns_a_plain_device_canvas():
    """The two-domain seam (#39) as two factories: chrome scales, a cart's
    320x240 surface never does (SPEC.md 6). `make_canvas` predates this class
    and must keep returning what it returned."""
    c = host_canvas.make_canvas(16, 8)
    assert type(c) is dc.DeviceCanvas
    assert c.RETAINED_FRAMES == 2
    assert not hasattr(c, "font_scale")
    c.cls(3)                                     # and it still draws
    assert bytes(c._buf[:2]) == dc.PAL565_WIRE[3].to_bytes(2, "little")


def test_the_class_is_importable_by_name():
    """It is built lazily (subclassing DeviceCanvas needs install() to have
    run), so `from runtime.host_canvas import HostSystemCanvas` goes through a
    module __getattr__. One class object, not one per import."""
    from runtime.host_canvas import HostSystemCanvas as again
    assert again is HostSystemCanvas
    assert isinstance(host_canvas.make_system_canvas(4, 4), HostSystemCanvas)
    with pytest.raises(AttributeError):
        host_canvas.NoSuchCanvas


# -- the three gaps found by review of this file's own first landing ----------


def test_a_layer_inherits_the_cart_palette():
    """DeviceCanvas.new_layer propagates it; OVERRIDING new_layer means not
    inheriting that. Both this class and WebSystemCanvas define their own (to
    carry font_scale), so both had to be told separately -- otherwise a layer
    draws stock MOY64 while the surface it composites onto honours the cart's,
    in the same frame."""
    cv = host_canvas.make_system_canvas(16, 8)
    cv.palette = [(255, 0, 0)] * 64
    lay = cv.new_layer(8, 4)
    cv.cls(8)
    lay.cls(8)
    assert bytes(lay._buf[:2]) == bytes(cv._buf[:2]), (
        "the layer drew a different colour for index 8 than its parent")


def test_a_stock_layer_still_shares_the_module_table():
    """Copy-on-write, guarded on the WIRE table's identity and not `_palette`:
    the getter populates that lazily, so testing it would drag every layer on a
    stock console off the shared table for nothing."""
    cv = host_canvas.make_system_canvas(16, 8)
    assert cv.new_layer(8, 4)._wire is dc._PAL565_WIRE_BUF


def test_the_game_tier_clips_its_text():
    """`moy_font` is what build.sh stages runtime/font.py AS, and device_canvas
    gates its native text op on importing it -- so on a clean checkout (that
    file is a gitignored build artefact) the host fell back to framebuf.text,
    which has NO clip rect and was measured drawing 252 pixels past an edge.
    install() registers the canonical module under the staged name, so whether
    host text clips no longer depends on whether firmware was built here.

    On the GAME canvas specifically: HostSystemCanvas rasterizes its own scaled
    text and was never affected, which is why this went unnoticed.
    """
    cv = host_canvas.make_canvas(64, 16)
    assert cv._gfx_text is not None, "the native text op did not resolve"
    cv.cls(0)
    cv.clip(0, 0, 20, 16)
    cv.print("ABCDEFGHIJ", 0, 4, 7)
    bg = cv._buf[0:2]
    past = sum(1 for y in range(16) for x in range(20, 64)
               if cv._buf[(y * 64 + x) * 2:(y * 64 + x) * 2 + 2] != bg)
    assert past == 0, "%d pixels drawn past the clip edge" % past
