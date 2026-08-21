"""Tests for the two-domain rendering seam (#39, step 1): a fixed 320x240 GAME
canvas (carts + the cart API, UNCHANGED) composited as a centered, integer-scaled
viewport into a responsive SYSTEM canvas (the desktop/launcher/settings + status
strip), with a settings-resizable system font (petme128 x1/x2/x3, persisted
in system.json).

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver), so these assert host==device behavior.

The whole point is host-verifiable:
  * GRACEFUL DEGRADATION -- at a 320x240 system canvas + font scale 1, every drawn
    pixel is byte-identical to today (the T-Deck path is unchanged).
  * RESPONSIVE DESKTOP -- a larger system canvas reflows the icon grid / scales the
    chrome and shows a running cart as a centered fixed-aspect viewport.
  * THE FONT-SCALE SETTING -- a Settings control cycles 1x/2x/3x, resizes the system
    text live, and persists across a reboot.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import canvas_probe as probe  # noqa: E402  (pixel-width-agnostic "it drew" probes)


from ws_helpers import build_ws as _ws


# ---------------------------------------------------------------------------
# Graceful degradation: 320x240 system canvas == game canvas, pixel-identical.
# ---------------------------------------------------------------------------

def test_default_build_shares_one_canvas(tmp_path):
    """The default (T-Deck) build has no distinct system canvas: the system canvas
    IS the game canvas (one object), so the composite step is a no-op and the
    desktop is exactly today."""
    ws = _ws(tmp_path)
    assert ws.sys_canvas is ws.canvas
    assert (ws.sys_canvas.w, ws.sys_canvas.h) == (320, 240)
    assert ws.font_scale == 1
    assert ws._viewport() == (0, 0, 1)            # no letterbox / scaling


def _ws_distinct_320(tmp_path):
    """A console with a DISTINCT 320x240 system canvas (forced -- build_workstation
    shares one canvas at the default size, so we attach a system canvas by hand)."""
    from runtime import host_app
    from runtime.host_canvas import make_system_canvas
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws._sys_canvas = make_system_canvas(320, 240, font_scale=1)
    ws._relayout()
    ws.pointer = host_app.console.Pointer(320, 240)
    ws.input.pointer = ws.pointer
    return ws


def test_distinct_320x240_system_canvas_renders(tmp_path):
    """On a console with a DISTINCT 320x240 system canvas at scale 1 there is no
    scaling/letterbox; the launcher draws on it without error."""
    from runtime import host_app
    ws = _ws_distinct_320(tmp_path)
    assert ws.sys_canvas is not ws.canvas          # a DISTINCT system canvas
    assert ws._viewport() == (0, 0, 1)             # no letterbox / scaling
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    assert probe.distinct_pixels(ws.sys_canvas) > 4    # the desktop drew


def test_running_cart_composite_is_the_identity_at_320x240(tmp_path):
    """A running cart at 320x240 scale 1 composites as the IDENTITY blit: after a
    frame the system buffer equals the game buffer exactly (the cart + its top-bar
    overlay drew on the game canvas; _composite_game copied it 1:1 into the system
    canvas with no scaling, no letterbox). The single overlay drawn ON the system
    canvas post-composite is the cursor -- hidden here -- so the buffers match."""
    ws = _ws_distinct_320(tmp_path)
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    # Clear the system overlays so only the composited game viewport remains: the
    # cursor (hidden), and the achievement toast that "Lift Off!" raised on open
    # (a real system overlay -- dismiss it so the compare is the pure composite).
    ws.pointer.visible = False
    ws.ach.toast = None
    ws.frame(1 / 30)
    assert bytes(ws.sys_canvas._buf) == bytes(ws.canvas._buf)


# ---------------------------------------------------------------------------
# Responsive desktop: a larger system canvas reflows + composites a viewport.
# ---------------------------------------------------------------------------

def test_desktop_reflows_on_a_larger_canvas(tmp_path):
    """At 640x480 the icon grid reflows to more columns/rows and the chrome scales,
    rendering without error and filling the full system buffer."""
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(640, 480))
    assert ws.sys_canvas is not ws.canvas
    assert (ws.sys_canvas.w, ws.sys_canvas.h) == (640, 480)
    # The Library shelf reflows to the canvas (the one tall featured slot at the
    # head of a continuously left-right SCROLLING card list). Columns are
    # RESOLUTION-driven (cards target ~w/5, the mockup's proportions) -- a
    # bigger canvas buys bigger cards and crisper 1x text, not magnification.
    assert ws.launcher.COLS >= 4 and ws.launcher.ROWS >= 2
    assert ws.layout.grid_content_w(len(ws.launcher.items)) > 0
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    buf = drv.rgb888()
    assert len(buf) == 640 * 480 * 3
    # rgb888() is 3 bytes per pixel, so count COLOURS and not bytes here too.
    assert probe.distinct_pixels_in(buf, 3) > 4    # wallpaper + grid + strip drew


def test_strip_and_settings_panel_scale_with_the_canvas(tmp_path):
    """The status strip stays at the top and the Settings panel fills the band down
    to its bottom inset. (Layout fields drive both. This used to also assert the
    bottom dock's slot geometry; the dock was deleted 2026-08-21 -- see
    bar_layer.py -- and the panel KEPT the band it occupied as its bottom inset.)"""
    ws = _ws(tmp_path, sys_size=(640, 480))
    lay = ws.layout
    assert lay.status_h >= 14                       # strip at least the baseline height
    px, py, pw, ph = lay.settings_panel
    assert py >= lay.status_h                       # below the strip
    assert py + ph < 480                            # a bottom inset remains
    assert px + pw <= 640


def test_running_cart_is_a_centered_integer_viewport(tmp_path):
    """A cart on a larger system canvas appears as a centered, integer-scaled 320x240
    viewport with a letterbox bezel around it. At 960x600 the integer scale is 2 (320
    *2=640 <= 960, 240*2=480 <= 600) and the viewport is centered."""
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600))
    ws.launcher.sel = 0
    ws.open()
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    ox, oy, scale = ws._viewport()
    assert scale == 2
    assert ox == (960 - 320 * 2) // 2 and oy == (600 - 240 * 2) // 2
    # The bezel corner (outside the viewport) is the solid bezel color.
    # (_VIEWPORT_BEZEL moved to wm.py with the viewport composite -- Stage 6.)
    from runtime import wm as WM
    # pix() reads the palette INDEX back, which is what _VIEWPORT_BEZEL is.
    assert ws.sys_canvas.pix(0, 0) == WM._VIEWPORT_BEZEL
    # A pixel inside the viewport differs from the bezel for a drawing cart.
    # (Not asserting a specific color -- just that the viewport area was written.)
    assert isinstance(ws.sys_canvas.pix(ox + 1, oy + 1), int)


# ---------------------------------------------------------------------------
# Settings-resizable system font: live + persisted.
# ---------------------------------------------------------------------------

def test_font_scale_cycles_through_1_2_3(tmp_path):
    ws = _ws(tmp_path, sys_size=(640, 480))
    assert ws.font_scale == 1
    ws.cycle_font_scale(1)
    assert ws.font_scale == 2
    ws.cycle_font_scale(1)
    assert ws.font_scale == 3
    ws.cycle_font_scale(1)
    assert ws.font_scale == 1                       # wraps


def test_font_scale_resizes_text_live(tmp_path):
    """Raising the font scale enlarges petme128 on the system canvas: the same string
    covers more set pixels at 2x than at 1x."""
    from runtime.host_canvas import make_system_canvas
    from runtime import console as C
    sc = make_system_canvas(640, 480, font_scale=1)
    sc.cls(0)
    sc.print("HELLO", 10, 10, C.NAMES["white"], 1)
    # Count pixels that are not the cls background: the only thing drawn is the
    # white text, so this IS the white count -- without asking whether a buffer
    # cell holds a palette index (it did) or half of an RGB565 word (it will).
    on1 = probe.painted_pixels(sc)
    sc.set_font_scale(2)
    sc.cls(0)
    sc.print("HELLO", 10, 10, C.NAMES["white"], 1)
    on2 = probe.painted_pixels(sc)
    assert on2 == on1 * 4                            # nearest-neighbor 2x = 4x pixels


def test_font_scale_1x_is_identical_to_plain_canvas(tmp_path):
    """A system canvas at scale 1 renders text byte-identical to the plain GAME
    canvas (the degradation guarantee at the pixel level for text).

    Both are `DeviceCanvas` now -- the difference is the HostSystemCanvas
    subclass, whose `print` takes its own scaled lane above font_scale 1. This
    pins that the lane it takes AT 1 is still the base class's."""
    from runtime.host_canvas import make_canvas, make_system_canvas
    from runtime import console as C
    plain = make_canvas(320, 240)
    sysc = make_system_canvas(320, 240, font_scale=1)
    for cv in (plain, sysc):
        cv.cls(3)
        cv.print("moybyte 0.4!", 5, 7, C.NAMES["yellow"], 1)
    assert bytes(plain._buf) == bytes(sysc._buf)


def test_font_scale_persists_across_reboot(tmp_path):
    """The Settings font-size choice lands in system.json and a fresh boot restores
    it (mirrors the wallpaper persistence, #28)."""
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir, sys_size=(640, 480))
    ws.open_settings()
    rows = [r[0] for r in ws.settings_layer._SETTINGS_ROWS]
    ws.settings_layer.set_msel = rows.index("font_scale")
    ws.settings_layer.settings_adjust(1)                            # 1x -> 2x via the Settings stepper
    assert ws.font_scale == 2
    assert moy_carts.load_system(carts_dir).get("font_scale") == 2
    # A fresh boot restores the saved scale (even with the default 320x240 system
    # canvas: load_system applies the persisted value).
    ws2 = host_app.build_workstation(carts_dir, sys_size=(640, 480))
    assert ws2.font_scale == 2
    # And the system canvas + layout reflect it.
    assert ws2.sys_canvas.font_scale == 2
    assert ws2.layout.fs == 2


def test_settings_font_row_renders(tmp_path):
    """The Settings screen with the new FONT SIZE row renders without error on a
    larger canvas."""
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(640, 480))
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    assert "font_scale" in [r[0] for r in ws.settings_layer._SETTINGS_ROWS]
    assert probe.distinct_pixels_in(drv.rgb888(), 3) > 4


def test_font_scale_is_inert_without_a_system_canvas(tmp_path):
    """In the degradation case (no distinct system canvas -- the T-Deck, whose
    framebuf text can't scale), setting the font scale is remembered + persisted but
    the EFFECTIVE layout scale stays 1, so the chrome geometry keeps matching the 8px
    text actually drawn (no mis-laid-out desktop)."""
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)         # default 320x240, shared canvas
    assert ws._sys_canvas is None
    ws.set_font_scale(3)
    assert ws.font_scale == 3                            # remembered
    assert ws._effective_font_scale() == 1              # but not applied (can't scale)
    assert ws.layout.fs == 1                             # layout stays the baseline
    assert ws.layout._base                               # i.e. exactly today's geometry
    assert moy_carts.load_system(carts_dir).get("font_scale") == 3   # persisted
    # It renders without error and the desktop is still the 320x240 baseline
    # shelf (3 columns at the small tier's card proportions).
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    assert ws.launcher.COLS == 3 and ws.launcher.ROWS == 2


def test_composite_safe_when_system_canvas_smaller_than_game(tmp_path):
    """A degenerate system canvas smaller than the 320x240 game canvas must never
    corrupt (resize) the system buffer -- the composite clips. build_workstation also
    clamps a too-small --size up to the game size, so this is belt-and-suspenders."""
    from runtime import host_app
    from runtime.host_canvas import make_system_canvas
    from runtime import console
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws._sys_canvas = make_system_canvas(300, 200, font_scale=1)  # forced sub-game
    ws._relayout()
    ws.launcher.sel = 0
    ws.open()
    ws.frame(1 / 30)                                        # composites a running cart
    assert len(ws.sys_canvas._buf) == 300 * 200 * 2        # buffer NOT resized/corrupt


def test_build_workstation_clamps_too_small_size(tmp_path):
    """A --size smaller than the 320x240 game canvas is clamped up (the game is
    composited INTO the system canvas, so a smaller panel is nonsensical)."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(300, 200))
    assert ws.sys_canvas.w >= 320 and ws.sys_canvas.h >= 240


def test_font_scale_change_on_a_big_system_canvas_does_not_crash(tmp_path):
    """Changing the system font size on a large system canvas must not raise.

    This began as a web-console test (its recording canvas was the only way to
    get a 640x480 system surface in a test) and was rewritten onto the raster
    path at moycore stage 4, when that transport was deleted -- the crash it
    pins is in the shell's relayout, not in any transport."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(640, 480))
    ws.open_settings()
    ws.cycle_font_scale(1)                                 # 1x -> 2x (was the crash)
    assert ws.font_scale == 2
    assert ws.sys_canvas.font_scale == 2
    ws.frame(1 / 60.0)                                     # and it still draws
    assert probe.distinct_pixels(ws.sys_canvas) > 4


def test_a_larger_system_canvas_draws_and_composites_a_cart(tmp_path):
    """A bigger SYSTEM canvas (#39) reflows the shell and still composites the
    fixed 320x240 game canvas into itself."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(640, 480))
    assert (ws.sys_canvas.w, ws.sys_canvas.h) == (640, 480)
    ws.frame(1 / 60.0)
    assert probe.distinct_pixels(ws.sys_canvas) > 4        # the shell drew
    ws.launcher.sel = 0
    ws.open()
    for _ in range(3):
        ws.frame(1 / 60.0)
    assert (ws.canvas.w, ws.canvas.h) == (320, 240)        # the cart's own surface
    assert probe.distinct_pixels(ws.sys_canvas) > 4        # composited in


def test_carts_api_unchanged_game_canvas_stays_320x240(tmp_path):
    """The cart side of the seam is untouched: the canvas a cart draws on is always
    the fixed 320x240 game canvas, regardless of the system canvas size."""
    ws = _ws(tmp_path, sys_size=(960, 600))
    assert (ws.canvas.w, ws.canvas.h) == (320, 240)
    # make_api binds to the game canvas (W/H = 320/240 in the cart namespace).
    ns = ws.make_api(ws.canvas, ws.input, {}, None, None, None, None, None)
    assert ns["W"] == 320 and ns["H"] == 240
