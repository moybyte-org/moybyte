"""The Appearance monitor's cart-wallpaper preview, on every tier (#31).

WHAT WENT WRONG, so the tests below say why they exist. The preview renders the
wallpaper cart a second time onto an offscreen canvas and caches the frame; it
used to build that canvas by IMPORTING one -- `runtime.host_canvas`, else
`web_canvas`, else give up. Both boards fell through to "give up", so the panel
was a black rectangle on hardware from the day it shipped, and no test noticed
because every caller of a preview is guarded and a missing preview looks exactly
like a preview of something dark. The first version of the black-rectangle bug
was `runtime/palette.py` importing `colorsys`; deleting that raster changed the
reason and not the outcome, which is the shape of failure these tests are for.

So they assert PIXELS, not booleans (`_ensure_preview()` returning True says
only that a cart compiled), and they drive the DEVICE's canvas factory
explicitly -- the one expression both boards' moy_runtime.py evaluates -- rather
than trusting that "the host works, so the board must".
"""

from runtime import host_app
from runtime.wallpaper import Wallpaper
from tests import canvas_probe as probe


PREVIEW_RECT = (10, 10, 152, 114)


def _ws(tmp_path, **kw):
    ws = host_app.build_workstation(str(tmp_path / "carts"), **kw)
    ws.look.select_wallpaper("moy_night", persist=False)
    return ws


def _board_factory(ws):
    """The offscreen canvas factory BOTH BOARDS inject, evaluated here.

    Copied from firmware/*/modules/moy_runtime.py verbatim in shape: a
    DeviceCanvas over a `_LayerComp`, sharing the live canvas's native kernel.
    On the host `_LayerComp` finds no moy_alloc and falls back to a gc-heap
    bytearray, which is the only difference -- the canvas class, the kernel and
    every pixel it draws are the ones the board runs.
    """
    from runtime.host_canvas import install
    install()
    from device_canvas import DeviceCanvas, _LayerComp
    gfx = ws.canvas._gfx
    assert gfx is not None, "no native kernel: the board factory returns None"
    return lambda w, h: DeviceCanvas(_LayerComp(int(w), int(h), gfx))


# -- it renders, and the pixels are the evidence -----------------------------

def test_preview_renders_a_non_blank_frame(tmp_path):
    ws = _ws(tmp_path)
    pix = ws.wallpaper._render_static(152, 114)
    assert pix is not None and len(pix) == 152 * 114
    assert probe.distinct_pixels_in(pix, 1) > 1, "the preview rendered flat"


def test_preview_reaches_the_monitor_panel(tmp_path):
    """Through the whole chain -- render, _Blit, spr onto a system canvas."""
    ws = _ws(tmp_path, sys_size=(1024, 600))
    ws.sys_canvas.cls(0)
    ws.wallpaper.draw_preview(ws.sys_canvas, (10, 20, 500, 380), 1 / 30)
    assert probe.distinct_pixels(ws.sys_canvas) > 2   # black bezel + real art
    assert probe.painted_pixels_rect(ws.sys_canvas, 10, 20, 500, 380) > 10000


def test_the_boards_own_canvas_factory_renders_the_same_frame(tmp_path):
    """THE POINT OF #31. A board has neither `runtime.host_canvas` nor
    `web_canvas`; what it has is the factory it injects on `ws`, and the preview
    now asks for that instead of importing a tier. Driving it explicitly is as
    close to on-glass as a host test gets -- and the frame has to MATCH the
    host's, or "it renders on the device" would only mean "it renders
    something".
    """
    ws = _ws(tmp_path)
    host_pix = ws.wallpaper._render_static(152, 114)

    ws2 = _ws(tmp_path, sys_size=(1024, 600))
    ws2.make_game_canvas = _board_factory(ws2)
    ws2.wallpaper._pv_canvas = None                   # force the new factory
    ws2.wallpaper._pv_draw = None
    board_pix = ws2.wallpaper._render_static(152, 114)

    assert board_pix is not None
    assert probe.distinct_pixels_in(board_pix, 1) > 1
    assert bytes(board_pix) == bytes(host_pix)


def test_no_factory_degrades_to_the_black_fill(tmp_path):
    """The remaining honest degradation: a backend that cannot build an
    offscreen canvas at all (a bare Workstation; the T-Deck on a build with no
    native kernel, whose factory answers None). Both must fall back, not raise.
    """
    ws = _ws(tmp_path)
    ws.make_game_canvas = None
    assert ws.wallpaper._ensure_preview() is False
    assert ws.wallpaper._render_static(152, 114) is None

    ws.make_game_canvas = lambda w, h: None           # the no-kernel board
    assert ws.wallpaper._ensure_preview() is False
    assert ws.wallpaper._render_static(152, 114) is None


def test_the_import_ladder_is_gone(tmp_path):
    """A tripwire, because the ladder is exactly the kind of thing that grows a
    rung back. `runtime/wallpaper.py` must name no per-tier canvas module: the
    surface comes from `ws.make_game_canvas`, which every head injects.

    Parsed, not grepped: the prose in that file has to be free to DISCUSS the
    ladder it replaced, and a substring search cannot tell an explanation from a
    statement.
    """
    import ast
    import pathlib

    src = (pathlib.Path(__file__).resolve().parent.parent
           / "runtime" / "wallpaper.py").read_text(encoding="utf-8")
    named = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            named.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            named.add(node.module or "")
    assert not {n for n in named if "host_canvas" in n or "web_canvas" in n}, \
        "a per-tier canvas import came back: %s" % sorted(named)
    # `device_canvas` is fine and is NOT a rung -- it is the reduction
    # (to_indices), and it resolves on every tier including a board.
    assert "make_game_canvas" in src


# -- the reduction at the render boundary ------------------------------------

def test_reduction_is_lossless_over_the_whole_palette(tmp_path):
    """Every MOY64 index survives 565 and comes back as itself. This is what
    lets the preview stay index-native downstream (the .mct sidecar's validator
    checks len == w*h), and it is only true because the 64 entries resolve to 64
    DISTINCT words."""
    from runtime.host_canvas import install
    install()
    from device_canvas import PAL565_WIRE, to_indices

    words = [PAL565_WIRE[i] for i in range(64)]
    assert len(set(words)) == 64, "two palette entries share a 565 word"
    buf = bytearray()
    for w in words:
        buf += bytes((w & 0xFF, (w >> 8) & 0xFF))
    assert list(to_indices(buf, None, True)) == list(range(64))


def test_resample_then_reduce_equals_reduce_then_resample(tmp_path):
    """The order _preview_indices takes is a pure speed choice, so it has to be
    provably free. Nearest-neighbour picks source pixels verbatim, so the two
    orders agree byte for byte -- if they ever stop, the fast path is wrong."""
    from runtime.host_canvas import install
    install()
    from device_canvas import PAL565_WIRE, to_indices

    gw, gh, vw, vh = 64, 48, 25, 17
    buf = bytearray(gw * gh * 2)
    for i in range(gw * gh):
        word = PAL565_WIRE[(i * 7 + i // gw) & 63]
        buf[2 * i] = word & 0xFF
        buf[2 * i + 1] = (word >> 8) & 0xFF

    fast = to_indices(Wallpaper._sample565(buf, gw, gh, vw, vh), None, False)
    slow = Wallpaper._sample(to_indices(buf, None, False), gw, gh, vw, vh)
    assert bytes(fast) == bytes(slow)
    assert len(set(fast)) > 1


def test_a_viewport_canvas_reads_out_as_its_own_surface():
    """`_sample565` carries stride/origin, so a canvas drawing into a sub-rect of
    a wider buffer (#155) previews ITS pixels and not its neighbour's. No head's
    factory returns one today; the argument is that this is cheap insurance and
    a silent wrong-window readout is not."""
    gw, gh, stride, ox, oy = 4, 3, 10, 2, 1
    buf = bytearray(stride * (oy + gh) * 2)
    for y in range(gh):                      # value = 100 + y*10 + x, inside only
        for x in range(gw):
            i = (oy + y) * stride + ox + x
            buf[2 * i] = 100 + y * 10 + x
    out = Wallpaper._sample565(buf, gw, gh, gw, gh, stride, ox, oy)
    assert [out[2 * i] for i in range(gw * gh)] == [
        100 + y * 10 + x for y in range(gh) for x in range(gw)]


# -- the sidecar the reduction exists to keep valid --------------------------

def test_the_rendered_frame_persists_as_a_valid_mct_sidecar(tmp_path):
    """The reason the conversion happens at the RENDER boundary: everything past
    it is index-native, including this file, whose reader checks
    `len == 8 + w*h` before trusting a byte."""
    from runtime import moy_image

    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    ws.look.select_wallpaper("moy_night", persist=False)
    ws.wallpaper.draw_preview(ws.sys_canvas, PREVIEW_RECT, 1 / 30)

    cart = ws.wallpaper._wp_cart
    sig = ws.wallpaper._src_sig(cart)
    _x, _y, w, h = PREVIEW_RECT
    gw, gh = ws.canvas.w, ws.canvas.h
    vw, vh = (w, max(1, gh * w // gw)) if gw * h >= gh * w else \
             (max(1, gw * h // gh), h)
    pix = moy_image.load_wallpaper_preview(cart["path"], vw, vh, sig)
    assert pix is not None, "no sidecar was written"
    assert len(pix) == vw * vh                     # the validator's own check
    assert probe.distinct_pixels_in(pix, 1) > 1    # ...and it holds a real frame
    assert max(pix) < 64                           # palette indices, not 565


def test_a_sidecar_written_by_one_tier_is_read_by_a_tier_that_cannot_render(
        tmp_path):
    """The degradation is now only about RENDERING. A build with no canvas
    factory still shows a preview another session computed -- which is what
    makes the sidecar worth writing at all."""
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    ws.look.select_wallpaper("moy_night", persist=False)
    ws.wallpaper.draw_preview(ws.sys_canvas, PREVIEW_RECT, 1 / 30)

    ws2 = host_app.build_workstation(carts)
    ws2.make_game_canvas = None                    # cannot render anything
    ws2.look.select_wallpaper("moy_night", persist=False)
    ws2.sys_canvas.cls(0)
    ws2.wallpaper.draw_preview(ws2.sys_canvas, PREVIEW_RECT, 1 / 30)
    assert probe.painted_pixels_rect(ws2.sys_canvas, *PREVIEW_RECT) > 1000


# -- the offscreen surface stays offscreen -----------------------------------

def test_a_bound_run_canvas_never_becomes_the_preview_surface(tmp_path):
    """draw_preview brackets to the STOCK canvas like draw() and compile(). On
    the windowed tier the Appearance window is drawn beside a live game, and a
    cart with a small raster (SPEC.md 3.1) has ws.canvas bound to ITS size --
    which would otherwise key the sidecar and shape the render to that cart."""
    ws = _ws(tmp_path, sys_size=(1024, 600))
    assert ws.bind_run_canvas(128, 120)
    assert (ws.canvas.w, ws.canvas.h) == (128, 120)
    before = bytes(ws.canvas._buf)

    ws.wallpaper.draw_preview(ws.sys_canvas, PREVIEW_RECT, 1 / 30)

    assert bytes(ws.canvas._buf) == before         # the run's frame is untouched
    pv = ws.wallpaper._pv_canvas
    assert pv is not None and (pv.w, pv.h) == (320, 240)
    assert ws.canvas is ws._run_canvas             # ...and the bind survived
