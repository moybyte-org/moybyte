"""The host's SYSTEM canvas, on the boards' raster (#161).

`runtime/canvas.py` is the second raster. `runtime/host_canvas.py` already runs
`DeviceCanvas` on CPython; `HostSystemCanvas` is the rest of what the shared
console asks a system SURFACE for, so the host can eventually run that one class
and the second raster can be deleted:

  * font_scale text and font-scale-carrying layers (#39/#73),
  * `blit_cover`, the wallpaper's cover-crop composite,
  * `to_rgb888`, the readout pygame blits and the GIF export writes.

The acceptance test is byte-identity: every scene below is drawn TWICE, once on
the shipping `runtime.canvas.SystemCanvas` and once here, and the two readouts
are compared byte for byte. That is what makes the swap a swap rather than a
new look -- and it is the check that catches the one tempting way to write
`to_rgb888` wrong (see `test_the_readout_is_not_a_bit_expansion`).

Everything here drives the REAL DeviceCanvas through the real host kernel, like
tests/test_cart_palette.py. No canvas is faked, because a twin of the class
under test proves nothing about the class under test.
"""

import pytest

from runtime import host_canvas
from runtime.canvas import Image, SystemCanvas
from runtime.editors import SpriteSheet, TileMap
from runtime.palette import MOY64

host_canvas.install()
import device_canvas as dc                                       # noqa: E402
from runtime.host_canvas import HostSystemCanvas                 # noqa: E402

W, H = 96, 64


def _pair(w=W, h=H, font_scale=1):
    """(old host canvas, new host canvas) of the same size."""
    return (SystemCanvas(w, h, font_scale=font_scale),
            host_canvas.make_system_canvas(w, h, font_scale=font_scale))


def _same(old, new, label):
    a = old.to_rgb888()
    b = new.to_rgb888()
    assert len(a) == len(b) == old.w * old.h * 3, label
    if a != b:
        diff = sum(1 for i in range(0, len(a), 3) if a[i:i + 3] != b[i:i + 3])
        first = next(i // 3 for i in range(0, len(a), 3)
                     if a[i:i + 3] != b[i:i + 3])
        raise AssertionError(
            "%s: %d/%d px differ (first at %d,%d: canvas.py=%s host_canvas=%s)"
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


def test_primitives_read_back_identically():
    old, new = _pair()
    _primitives(old)
    _primitives(new)
    _same(old, new, "primitives")


def test_camera_and_clip_read_back_identically():
    old, new = _pair()
    _camera_and_clip(old)
    _camera_and_clip(new)
    _same(old, new, "camera+clip")


def test_text_reads_back_identically():
    old, new = _pair()
    _text(old)
    _text(new)
    _same(old, new, "text")


def test_sprites_read_back_identically():
    """Sprites are the readout's hard case: this canvas bakes each variant to
    RGB565 up front, so those words reached the buffer without passing through
    the palette on the way in. They still have to come back out of it.

    A fresh Image per canvas, not one shared: the bake caches on the Image, and
    handing the same one to both would let the second canvas read the first's
    pixels."""
    old, new = _pair()
    _sprites(old, Image.from_ascii(_sprite_rows(), _sprite_map()))
    _sprites(new, Image.from_ascii(_sprite_rows(), _sprite_map()))
    _same(old, new, "sprites")


def test_paint_image_reads_back_identically():
    iw, ih = 20, 12
    idx = bytearray((r * 5 + c * 3) % 63 for r in range(ih) for c in range(iw))
    old, new = _pair()
    _paint_image(old, idx, iw, ih)
    _paint_image(new, idx, iw, ih)
    _same(old, new, "blit_indices")


def test_tilemap_reads_back_identically():
    old, new = _pair()
    _tiles(old, *_sheet_and_map())
    _tiles(new, *_sheet_and_map())
    _same(old, new, "map")


def test_scaled_text_reads_back_identically():
    """The #39 system font, which is the whole reason this class exists. The
    host kernel has no text op, so the scaled path here is petme128 blocks --
    and it has to land on the same pixels runtime/canvas.py puts down, or every
    label on a big desktop shifts on the day the canvas is swapped."""
    for fs in (2, 3):
        old, new = _pair(font_scale=fs)
        for c in (old, new):
            c.cls(0)
            c.print("Aa Bb 123", 3, 3, 12)
            c.print("clip me", W - 20, 30, 7)
        _same(old, new, "scaled text fs=%d" % fs)


def test_a_layers_readout_is_identical_too():
    """Layers are surfaces: the WM's window buffers and the bar's strip cache
    are read back through this same path."""
    old, new = _pair()
    lold = old.new_layer(40, 24)
    lnew = new.new_layer(40, 24)
    for lay in (lold, lnew):
        lay.cls(4)
        lay.rect(2, 2, 20, 10, 9)
        lay.print("hi", 3, 14, 7)
    _same(lold, lnew, "layer")


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
    stride. (runtime/canvas.py's version dumps the whole buffer instead; the two
    agree on every full-surface canvas, which is every canvas that is read.)"""
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


def test_blit_cover_takes_the_indexed_game_canvas_too():
    """The migration case, and the only one that runs until runtime/canvas.py is
    deleted: the system surface is this class while the GAME canvas -- which is
    what the wallpaper cart draws its frame on -- is still an indexed
    `Canvas`. A 565-only blit_cover dies on `gc._buf` at the first desktop
    frame, which is the whole desktop, not a cosmetic bug."""
    gc = SystemCanvas(320, 240)                  # indices, not 565
    gc.cls(1)
    gc.rect(0, 0, 320, 120, 8)
    sc = host_canvas.make_system_canvas(1024, 600)
    sc.cls(0)
    sc.blit_cover(gc)

    # Same pixels the 565 source produces, so a wallpaper looks identical
    # before and after the game canvas is swapped.
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
