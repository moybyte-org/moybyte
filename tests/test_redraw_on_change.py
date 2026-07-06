"""Redraw-on-change (#44 step 1): the console must NOT redraw + flush a static UI
screen every frame -- idle UI costs ~0, an input triggers exactly one redraw, and a
running cart still animates every frame. Driven through the SAME shared console the
device runs (runtime.host_app + ConsoleDriver), so this asserts host==device
behaviour, not a host-only path.

`Workstation._frames_drawn` counts the frames that actually painted+flushed (idle
frames are skipped in frame()), so it's the witness for "did the UI redraw".
"""

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import host_app  # noqa: E402

DT = 1.0 / 30


def _static_launcher(tmp_path):
    """A launcher on a SOLID-FILL wallpaper -- genuinely static (no live wallpaper
    _update animating the backdrop), plus the achievement toast cleared. The
    cleanest 'nothing changes' screen to prove idle frames are skipped."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    ws.select_wallpaper("fill:dark_blue", persist=False)
    ws.ach.toast = None
    return ws, drv


def _static_code_editor(tmp_path):
    """Open the first seeded cart straight into the (static, full-screen) code
    editor, with the 'First Steps' celebration toast cleared so nothing animates."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    assert ws.launcher.items, "system carts should be seeded"
    ws.launcher.sel = 0
    ws.open()
    ws._open_menu()
    ws.set_menu_view("code")
    ws.ach.toast = None
    assert ws.menu_view == "code" and ws.editor is not None
    return ws, drv


def _settle(ws, drv, n=3):
    """Run a few frames so any opening transition/toast settles, then return the
    drawn-frame count to measure from."""
    for _ in range(n):
        drv.frame(DT)
    ws.ach.toast = None
    drv.frame(DT)
    return ws._frames_drawn


# -- the core win: a static screen does NOT redraw ---------------------------

def test_static_code_editor_skips_idle_frames(tmp_path):
    """A static code editor must skip the draw+flush entirely across many idle
    frames -- the dirty flag stays clear and no frame paints."""
    ws, drv = _static_code_editor(tmp_path)
    base = _settle(ws, drv)
    for _ in range(100):
        drv.frame(DT)
    assert ws._frames_drawn == base, (
        "static code editor redrew %d of 100 idle frames" % (ws._frames_drawn - base))
    assert ws._dirty is False


def test_static_launcher_skips_idle_frames(tmp_path):
    """A launcher on a solid-fill (non-live) wallpaper is static, so idle frames are
    skipped too -- proves the skip isn't editor-specific."""
    ws, drv = _static_launcher(tmp_path)
    base = _settle(ws, drv)
    for _ in range(100):
        drv.frame(DT)
    assert ws._frames_drawn == base, (
        "static launcher redrew %d of 100 idle frames" % (ws._frames_drawn - base))


# -- an input triggers exactly one redraw ------------------------------------

def test_button_press_triggers_exactly_one_redraw(tmp_path):
    """A single button press on a static screen causes exactly one redraw, then the
    UI goes idle again (no lingering per-frame redraw)."""
    ws, drv = _static_launcher(tmp_path)
    _settle(ws, drv)
    before = ws._frames_drawn
    drv.press("right")          # one nav press
    drv.frame(DT)
    assert ws._frames_drawn - before == 1, "press should draw exactly one frame"
    # ...and the screen is idle again afterwards.
    after = ws._frames_drawn
    for _ in range(10):
        drv.frame(DT)
    assert ws._frames_drawn == after, "screen should be static again after the press"


def test_keypress_in_editor_triggers_exactly_one_redraw(tmp_path):
    """Typing one character in the static code editor draws exactly one frame, then
    the editor is static again."""
    ws, drv = _static_code_editor(tmp_path)
    _settle(ws, drv)
    before = ws._frames_drawn
    drv.type_char(ord("x"))
    drv.frame(DT)
    assert ws._frames_drawn - before == 1, "one keypress should draw exactly one frame"
    after = ws._frames_drawn
    for _ in range(10):
        drv.frame(DT)
    assert ws._frames_drawn == after, "editor should be static again after typing"


# -- a running cart still redraws every frame --------------------------------

def test_running_cart_redraws_every_frame(tmp_path):
    """Games animate: a running cart must report dirty every frame, so it redraws +
    flushes on every frame exactly as before (no skipping)."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    assert ws.launcher.items, "system carts should be seeded"
    ws.launcher.sel = 0
    ws.open()                       # -> screen == "desktop", cart running
    assert ws.screen == "desktop"
    assert ws._update is not None or ws._draw is not None
    base = _settle(ws, drv)
    for _ in range(60):
        drv.frame(DT)
    assert ws._frames_drawn - base == 60, (
        "running cart drew %d of 60 frames (should be 60)" % (ws._frames_drawn - base))


def test_live_wallpaper_keeps_launcher_animating(tmp_path):
    """A live wallpaper (its _update advances each frame) keeps the home backdrop
    animating, so the launcher redraws every frame -- the skip must NOT freeze a
    live wallpaper."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    # Pick a wallpaper CART (not a fill) so it has a live _update, if one is seeded.
    cart_opts = [o for o in ws.wallpaper_options() if not str(o).startswith("fill:")]
    if not cart_opts:
        import pytest
        pytest.skip("no live wallpaper cart seeded")
    ws.select_wallpaper(cart_opts[0], persist=False)
    if ws.wallpaper._wp_update is None:
        import pytest
        pytest.skip("seeded wallpaper has no _update (static)")
    ws.ach.toast = None
    base = _settle(ws, drv)
    for _ in range(30):
        drv.frame(DT)
    assert ws._frames_drawn - base == 30, "live wallpaper should redraw every frame"


# -- correctness: state changes still show (no stale screen) -----------------

def test_navigation_updates_pixels(tmp_path):
    """Moving the launcher selection must actually change the visible pixels -- the
    skip optimisation must never leave a stale frame on a real change."""
    ws, drv = _static_launcher(tmp_path)
    if len(ws.launcher.items) < 2:
        import pytest
        pytest.skip("need >=2 carts to move the selection")
    _settle(ws, drv)
    before_pixels = hashlib.md5(drv.rgb888()).hexdigest()
    before_sel = ws.launcher.sel
    drv.press("right")
    drv.frame(DT)
    assert ws.launcher.sel != before_sel, "selection should have moved"
    after_pixels = hashlib.md5(drv.rgb888()).hexdigest()
    assert before_pixels != after_pixels, "moving the selection must repaint the grid"


def test_screen_switch_updates_pixels(tmp_path):
    """Switching screens (launcher -> settings) repaints, not a stale launcher."""
    ws, drv = _static_launcher(tmp_path)
    _settle(ws, drv)
    launcher_pixels = hashlib.md5(drv.rgb888()).hexdigest()
    ws.open_settings()
    drv.frame(DT)
    assert ws.screen == "settings"
    settings_pixels = hashlib.md5(drv.rgb888()).hexdigest()
    assert launcher_pixels != settings_pixels, "settings screen must repaint"


def test_idle_redraw_measurement(tmp_path):
    """Quantify the win: across 120 idle frames on a static screen, the count of
    frames that actually painted is 0 (vs 120 in the old always-redraw model)."""
    ws, drv = _static_code_editor(tmp_path)
    base = _settle(ws, drv)
    idle_frames = 120
    for _ in range(idle_frames):
        drv.frame(DT)
    painted = ws._frames_drawn - base
    # The whole point of #44: an idle static UI draws ~0 frames.
    assert painted == 0, "idle static UI painted %d/%d frames" % (painted, idle_frames)
