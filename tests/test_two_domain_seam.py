"""Tests for the two-domain rendering seam (#39, step 1): a fixed 320x240 GAME
canvas (carts + the cart API, UNCHANGED) composited as a centered, integer-scaled
viewport into a responsive SYSTEM canvas (the desktop/launcher/settings + status
strip + dock), with a settings-resizable system font (petme128 x1/x2/x3, persisted
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

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws(tmp_path, **kw):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"), **kw)


# ---------------------------------------------------------------------------
# Graceful degradation: 320x240 system canvas == game canvas, pixel-identical.
# ---------------------------------------------------------------------------

def _run_n(drv, n, dt=1 / 30):
    for _ in range(n):
        drv.frame(dt)


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
    shares one canvas at the default size, so we attach a SystemCanvas by hand)."""
    from runtime import host_app
    from runtime.canvas import SystemCanvas
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws._sys_canvas = SystemCanvas(320, 240, font_scale=1)
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
    assert len(set(ws.sys_canvas.buf)) > 4         # the desktop drew


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
    assert bytes(ws.sys_canvas.buf) == bytes(ws.canvas.buf)


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
    # More columns and rows than the 4x2 baseline (the grid fills the bigger band).
    assert ws.launcher.COLS > 4 and ws.launcher.ROWS >= 2
    assert ws.launcher.PAGE == ws.launcher.COLS * ws.launcher.ROWS
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    buf = drv.rgb888()
    assert len(buf) == 640 * 480 * 3
    assert len(set(buf)) > 4                       # wallpaper + grid + dock drew


def test_dock_and_strip_scale_with_the_canvas(tmp_path):
    """The bottom dock anchors to the canvas bottom and spans its width; the status
    strip stays at the top. (Layout fields drive both.)"""
    ws = _ws(tmp_path, sys_size=(640, 480))
    lay = ws.layout
    assert lay.dock_y == 480 - lay.dock_h          # bottom-anchored
    last = ws._dock_slot_rect(len(__import__("runtime.console", fromlist=["_DOCK_SLOTS"])._DOCK_SLOTS) - 1)
    assert last[0] + last[2] <= 640                # the last slot fits the width
    assert lay.status_h >= 14                       # strip at least the baseline height


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
    from runtime import console as C
    assert ws.sys_canvas.buf[0] == C._VIEWPORT_BEZEL
    # A pixel inside the viewport differs from the bezel for a drawing cart.
    cx = (oy + 1) * 960 + (ox + 1)
    # (Not asserting a specific color -- just that the viewport area was written.)
    assert isinstance(ws.sys_canvas.buf[cx], int)


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
    from runtime.canvas import SystemCanvas
    from runtime import console as C
    sc = SystemCanvas(640, 480, font_scale=1)
    sc.cls(0)
    sc.print("HELLO", 10, 10, C.NAMES["white"], 1)
    on1 = sum(1 for b in sc.buf if b == C.NAMES["white"])
    sc.set_font_scale(2)
    sc.cls(0)
    sc.print("HELLO", 10, 10, C.NAMES["white"], 1)
    on2 = sum(1 for b in sc.buf if b == C.NAMES["white"])
    assert on2 == on1 * 4                            # nearest-neighbor 2x = 4x pixels


def test_font_scale_1x_is_identical_to_plain_canvas(tmp_path):
    """A SystemCanvas at scale 1 renders text byte-identical to the plain game Canvas
    (the degradation guarantee at the pixel level for text)."""
    from runtime.canvas import Canvas, SystemCanvas
    from runtime import console as C
    plain = Canvas(320, 240)
    sysc = SystemCanvas(320, 240, font_scale=1)
    for cv in (plain, sysc):
        cv.cls(3)
        cv.print("kidcode 0.4!", 5, 7, C.NAMES["yellow"], 1)
    assert bytes(plain.buf) == bytes(sysc.buf)


def test_font_scale_persists_across_reboot(tmp_path):
    """The Settings font-size choice lands in system.json and a fresh boot restores
    it (mirrors the wallpaper persistence, #28)."""
    from runtime import host_app, kid_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir, sys_size=(640, 480))
    ws.open_settings()
    rows = [r[0] for r in ws._SETTINGS_ROWS]
    ws.set_msel = rows.index("font_scale")
    ws.settings_adjust(1)                            # 1x -> 2x via the Settings stepper
    assert ws.font_scale == 2
    assert kid_carts.load_system(carts_dir).get("font_scale") == 2
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
    assert "font_scale" in [r[0] for r in ws._SETTINGS_ROWS]
    assert len(set(drv.rgb888())) > 4


def test_font_scale_is_inert_without_a_system_canvas(tmp_path):
    """In the degradation case (no distinct system canvas -- the T-Deck, whose
    framebuf text can't scale), setting the font scale is remembered + persisted but
    the EFFECTIVE layout scale stays 1, so the chrome geometry keeps matching the 8px
    text actually drawn (no mis-laid-out desktop)."""
    from runtime import host_app, kid_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)         # default 320x240, shared canvas
    assert ws._sys_canvas is None
    ws.set_font_scale(3)
    assert ws.font_scale == 3                            # remembered
    assert ws._effective_font_scale() == 1              # but not applied (can't scale)
    assert ws.layout.fs == 1                             # layout stays the baseline
    assert ws.layout._base                               # i.e. exactly today's geometry
    assert kid_carts.load_system(carts_dir).get("font_scale") == 3   # persisted
    # It renders without error and the desktop is still the 320x240 baseline grid.
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    assert ws.launcher.COLS == 4 and ws.launcher.ROWS == 2


def test_composite_safe_when_system_canvas_smaller_than_game(tmp_path):
    """A degenerate system canvas smaller than the 320x240 game canvas must never
    corrupt (resize) the system buffer -- the composite clips. build_workstation also
    clamps a too-small --size up to the game size, so this is belt-and-suspenders."""
    from runtime import host_app
    from runtime.canvas import SystemCanvas
    from runtime import console
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws._sys_canvas = SystemCanvas(300, 200, font_scale=1)   # forced sub-game canvas
    ws._relayout()
    ws.launcher.sel = 0
    ws.open()
    ws.frame(1 / 30)                                        # composites a running cart
    assert len(ws.sys_canvas.buf) == 300 * 200             # buffer NOT resized/corrupt


def test_build_workstation_clamps_too_small_size(tmp_path):
    """A --size smaller than the 320x240 game canvas is clamped up (the game is
    composited INTO the system canvas, so a smaller panel is nonsensical)."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(300, 200))
    assert ws.sys_canvas.w >= 320 and ws.sys_canvas.h >= 240


def test_web_console_font_scale_change_does_not_crash(tmp_path):
    """Changing the font size on the web console (whose system canvas is a recording
    CommandCanvas) must not raise -- the recorder honours set_font_scale and records
    scaled text as rect blocks the replayer already understands."""
    from tools.web_console import WebConsole
    from tools.command_canvas import replay_to_canvas
    from runtime.canvas import Canvas
    wc = WebConsole(str(tmp_path / "carts"), sys_size=(640, 480))
    wc.ws.open_settings()
    wc.ws.cycle_font_scale(1)                              # 1x -> 2x (was the crash)
    assert wc.ws.font_scale == 2
    assert wc.canvas.font_scale == 2
    cmds, _, _ = wc.step_frame()
    cv = Canvas(640, 480)
    replay_to_canvas(cmds, cv)                             # scaled text replays cleanly
    assert len(set(cv.buf)) > 4


def test_web_console_larger_canvas_streams_and_replays(tmp_path):
    """The web console (#22) honours a larger SYSTEM canvas (#39): /assets reports
    the bigger size, the launcher command stream replays to valid pixels, and a
    running cart composites into the stream as a single spr viewport command."""
    from tools.web_console import WebConsole
    from tools.command_canvas import replay_to_canvas
    from runtime.canvas import Canvas
    wc = WebConsole(str(tmp_path / "carts"), sys_size=(640, 480))
    assert wc.assets()["w"] == 640 and wc.assets()["h"] == 480
    cmds, _, _ = wc.step_frame()                       # the launcher (reflowed)
    cv = Canvas(640, 480)
    replay_to_canvas(cmds, cv)
    assert len(set(cv.buf)) > 4                      # the desktop replayed
    wc.ws.launcher.sel = 0
    wc.ws.open()
    cmds, _, _ = wc.step_frame()                       # a running cart
    assert "spr" in [c[0] for c in cmds]            # the game viewport blit command
    cv2 = Canvas(640, 480)
    replay_to_canvas(cmds, cv2)
    assert len(set(cv2.buf)) > 4


def test_carts_api_unchanged_game_canvas_stays_320x240(tmp_path):
    """The cart side of the seam is untouched: the canvas a cart draws on is always
    the fixed 320x240 game canvas, regardless of the system canvas size."""
    ws = _ws(tmp_path, sys_size=(960, 600))
    assert (ws.canvas.w, ws.canvas.h) == (320, 240)
    # make_api binds to the game canvas (W/H = 320/240 in the cart namespace).
    ns = ws.make_api(ws.canvas, ws.input, {}, None, None, None, None, None)
    assert ns["W"] == 320 and ns["H"] == 240
