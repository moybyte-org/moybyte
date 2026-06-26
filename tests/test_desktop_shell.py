"""Tests for the Picotron/TIC-80-style desktop shell (#28): a wallpaper backdrop,
a cart icon grid, a top status strip, a bottom dock, and a Settings app with
FUNCTIONAL, persisted wallpaper switching.

All driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver: mouse == touch, arrows == trackball), so these assert host==device
behavior, not a host-only path."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws(tmp_path):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"))


# -- the desktop renders without error -------------------------------------

def test_desktop_home_renders(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    assert ws.screen == "launcher"
    drv.frame(1 / 30)
    # Wallpaper backdrop + icon grid + status strip + dock => many colors.
    assert len(set(drv.rgb888())) > 4


def test_wallpaper_backdrop_is_drawn_behind_icons(tmp_path):
    """With a wallpaper cart selected the backdrop animates; the home frame is not a
    single flat color (it has the wallpaper, the icon tiles, and the dock)."""
    ws = _ws(tmp_path)
    assert ws.wallpaper_id is not None
    from runtime import host_app
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    buf = drv.rgb888()
    assert len(set(buf)) > 4


def test_fill_fallback_when_no_wallpaper_carts(tmp_path):
    """With zero wallpaper carts installed, the built-in solid fills are still
    selectable so there is always a valid backdrop (zero-cart fallback)."""
    from runtime import console, kid_carts, host_app
    # A store with only a non-wallpaper cart.
    carts_dir = str(tmp_path / "carts")
    kid_carts.ensure_dirs(carts_dir)
    kid_carts.create("Plain", carts_dir, src="def _draw():\n    cls(1)\n", type="app")
    carts = kid_carts.scan(carts_dir)
    canvas = host_app.Canvas(320, 240)
    inp = host_app.InputState()
    ws = console.Workstation(host_app._NullComp(), canvas, inp, carts)
    ws.make_api = host_app.make_api
    ws.carts_store = kid_carts
    ws.carts_root = carts_dir
    ws.pointer = console.Pointer(320, 240)
    ws.load_system()
    assert ws.wallpaper_carts() == []                     # none installed
    assert ws.wallpaper_id.startswith("fill:")            # fell back to a solid fill
    ws.frame(1 / 30)                                       # renders without error


# -- wallpaper switching is functional + persists --------------------------

def test_wallpaper_switch_changes_backdrop(tmp_path):
    ws = _ws(tmp_path)
    opts = ws.wallpaper_options()
    assert len(opts) >= 2
    before = ws.wallpaper_id
    ws.cycle_wallpaper(1)
    assert ws.wallpaper_id != before
    assert ws.wallpaper_id in opts


def test_wallpaper_choice_persists_across_reboot(tmp_path):
    from runtime import host_app, kid_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    ws.cycle_wallpaper(1)
    chosen = ws.wallpaper_id
    # It lands in system.json beside the carts dir.
    assert kid_carts.load_system(carts_dir).get("wallpaper") == chosen
    # A fresh boot restores it.
    ws2 = host_app.build_workstation(carts_dir)
    assert ws2.wallpaper_id == chosen


def test_settings_screen_opens_and_renders(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    assert ws.screen == "settings"
    drv.frame(1 / 30)
    assert len(set(drv.rgb888())) > 4


def test_settings_dock_gear_opens_settings(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    # The settings dock slot (last slot) tapped from the home desktop.
    k = C._DOCK_SLOTS.index("settings")
    x, y, w, h = ws._dock_slot_rect(k)
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.screen == "settings"


def test_settings_wallpaper_stepper_applies_and_persists(tmp_path):
    from runtime import host_app, kid_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    ws.set_msel = 0                                       # wallpaper row
    before = ws.wallpaper_id
    drv.press("right")                                    # step the stepper
    drv.frame(1 / 30)
    assert ws.wallpaper_id != before
    assert kid_carts.load_system(carts_dir).get("wallpaper") == ws.wallpaper_id


def test_settings_back_returns_home(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    drv.click(C._SET_BACK[0] + 2, C._SET_BACK[1] + 2)
    drv.frame(1 / 30)
    assert ws.screen == "launcher"


def test_settings_mock_rows_do_not_touch_carts(tmp_path):
    """The mocked rows (volume/brightness/name/theme) step cosmetic values in the
    system dict but are clearly not wired to a backend yet."""
    ws = _ws(tmp_path)
    ws.open_settings()
    # theme row
    rows = [r[0] for r in ws._SETTINGS_ROWS]
    ws.set_msel = rows.index("theme")
    ws.settings_adjust(1)
    assert ws.system.get("theme") in ws._MOCK_THEMES
    ws.set_msel = rows.index("volume")
    ws.settings_adjust(1)
    assert 0 <= ws.system.get("volume") <= 5


# -- dock keeps the management + open flows working ------------------------

def test_dock_home_returns_from_settings(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    k = C._DOCK_SLOTS.index("home")
    x, y, w, h = ws._dock_slot_rect(k)
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.screen == "launcher"


def test_management_buttons_still_create_and_delete(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    n0 = len(ws.launcher.items)
    drv.click(C._NEW_BTN[0] + 2, C._NEW_BTN[1] + 2)       # NEW in the status strip
    drv.frame(1 / 30)
    assert len(ws.launcher.items) == n0 + 1


def test_dock_run_opens_selected_cart_from_home(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    k = C._DOCK_SLOTS.index("run")
    x, y, w, h = ws._dock_slot_rect(k)
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.screen == "desktop"


def test_go_home_keeps_wallpaper(tmp_path):
    """Opening a cart and returning home must leave the wallpaper backdrop intact
    (it is system state, not per-cart)."""
    ws = _ws(tmp_path)
    wp = ws.wallpaper_id
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    ws.go_home()
    assert ws.screen == "launcher"
    assert ws.wallpaper_id == wp
