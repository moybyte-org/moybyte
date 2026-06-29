"""Tests for the unified, themeable 18px top bar (Stage 1): the launcher's old 14px
status strip and the running-cart's labeled button row are replaced by ONE 18px bar of
16x16 IconSheet sprites on both screens.

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver: mouse == touch, arrows == trackball), so these assert host==device
behavior. The IconSheet/storage tests poke the cores directly."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws(tmp_path):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"))


# -- IconSheet (16x16 tiles) ------------------------------------------------

def test_icon_sheet_is_16x16():
    from runtime.editors import IconSheet
    s = IconSheet()
    assert s.TILE == 16
    assert (s.cols, s.rows) == (8, 4)           # 32 slots = 128x64 px
    assert (s.w, s.h) == (128, 64)
    assert s.count == 32
    img = s.tile_image(0)
    assert img is not None and img.w == 16 and img.h == 16
    assert len(img.pix) == 16 * 16
    assert s.tile_image(31) is not None         # last slot in range
    assert s.tile_image(32) is None             # out of range


def test_icon_sheet_kgfx_roundtrips_for_16x16():
    """The flat .kgfx hex format serializes/parses a 128x64 (16x16-tile) sheet just
    like an 8x8 SpriteSheet -- one nibble per pixel, h rows of w nibbles."""
    from runtime.editors import IconSheet
    s = IconSheet()
    s.tset(0, 1, 2, 9)                           # paint a pixel in tile 0
    s.tset(31, 15, 15, 11)                       # ... and the last tile's corner
    hexs = s.to_hex()
    rows = hexs.split("\n")
    assert len(rows) == 64                       # one line per pixel row (h)
    assert len(rows[0]) == 128                   # one nibble per pixel column (w)
    s2 = IconSheet.from_hex(hexs)
    assert (s2.cols, s2.rows) == (8, 4)          # default geometry preserved on parse
    assert s2.tget(0, 1, 2) == 9
    assert s2.tget(31, 15, 15) == 11


# -- the default (baked-in) icon theme --------------------------------------

def test_default_icon_theme_paints_every_chrome_icon():
    """The baked default theme fills a non-blank, in-bounds 16x16 tile for every icon
    in the _ICON map, so the bar renders a real picture for each kind."""
    from runtime import host_app  # noqa: F401 -- registers the editors/audio aliases
    from runtime import console as C
    sheet = C._default_icon_sheet()
    assert sheet.TILE == 16
    for kind, slot in C._ICON.items():
        assert 0 <= slot < sheet.count, kind
        img = sheet.tile_image(slot)
        assert img is not None and img.w == 16 and img.h == 16, kind
        assert any(p for p in img.pix), "%s should not be a blank tile" % kind


def test_default_theme_loads_when_system_icons_absent(tmp_path):
    """No system_icons.kgfx on disk => the workstation falls back to the baked default
    IconSheet (a 16x16 themeable sheet, not None)."""
    from runtime import kid_carts
    carts_dir = str(tmp_path / "carts")
    kid_carts.ensure_dirs(carts_dir)
    assert kid_carts.load_system_icons(carts_dir) is None     # nothing saved
    ws = _ws(tmp_path)
    assert ws.icon_sheet is not None
    assert ws.icon_sheet.TILE == 16
    assert ws.icon_sheet.count == 32


def test_system_icons_load_save_round_trip(tmp_path):
    """load/save_system_icons round-trip the theme hex through a file beside the carts
    dir (mirrors shared.kgfx)."""
    from runtime import kid_carts
    from runtime import console as C
    carts_dir = str(tmp_path / "carts")
    kid_carts.ensure_dirs(carts_dir)
    hexs = C._default_icon_sheet().to_hex()
    kid_carts.save_system_icons(hexs, carts_dir)
    assert kid_carts.load_system_icons(carts_dir) == hexs
    # And a workstation built over that store loads the saved theme (not the default).
    ws = _ws(tmp_path)
    assert ws.icon_sheet.to_hex() == hexs


# -- the bar renders without error on both screens at 18px ------------------

def test_bar_is_18px_on_both_screens(tmp_path):
    from runtime import console as C
    assert C._STATUS_H == 18
    ws = _ws(tmp_path)
    assert ws.layout.status_h == 18
    from runtime import host_app
    drv = host_app.ConsoleDriver(ws)
    # Launcher bar renders.
    assert ws.screen == "launcher"
    drv.frame(1 / 30)
    assert len(set(drv.rgb888())) > 4
    # Running-cart bar renders.
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    drv.frame(1 / 30)
    assert len(set(drv.rgb888())) > 4


def test_bar_falls_back_to_glyphs_without_an_icon_sheet(tmp_path):
    """A workstation with no icon sheet wired (icon_sheet is None) still draws the bar
    -- _icon falls back to the _glyph bitmap so nothing crashes."""
    from runtime import console, kid_carts, host_app
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
    assert ws.icon_sheet is None              # never wired
    ws.frame(1 / 30)                          # launcher bar renders (glyph fallback)
    ws.launcher.sel = 0
    ws.open()
    ws.frame(1 / 30)                          # running-cart bar renders too


# -- tapping each icon position fires the right action ----------------------

def _center(rect):
    x, y, w, h = rect
    return x + w // 2, y + h // 2


def test_cart_bar_home_icon_goes_home(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    drv.click(*_center(C._HOME_BTN))
    drv.frame(1 / 30)
    assert ws.screen == "launcher"


def test_cart_bar_edit_icon_opens_editor(tmp_path):
    """The EDIT/CODE icon opens the cards menu (edit-schema cart) or the code editor
    (no schema) -- either way leaves the running-cart screen for the menu."""
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    drv.click(*_center(C._MENU_BTN))
    drv.frame(1 / 30)
    assert ws.screen == "menu"


def test_cart_bar_paint_icon_opens_paint(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    drv.click(*_center(C._PAINT_BTN))
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "paint"


def test_cart_bar_map_icon_opens_map(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    drv.click(*_center(C._MAP_BTN))
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "map"


def test_cart_bar_blocks_icon_opens_blocks(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    drv.click(*_center(C._BLOCKS_BTN))
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "blocks"


def test_launcher_bar_menu_opens_settings(tmp_path):
    """Settings moved off the bar into the ≡ system menu (#52). Tapping ≡ on the home
    bar opens the dropdown; its first item (SETTINGS) opens Settings."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    drv.click(*_center(ws.layout.sysmenu_btn))
    drv.frame(1 / 30)
    assert ws.sysmenu.open
    drv.press("a"); drv.frame(1 / 30); drv.frame(1 / 30)   # activate SETTINGS (first item)
    assert ws.screen == "settings"


def test_launcher_bar_new_dup_del_when_manageable(tmp_path):
    """NEW/DUP/DEL icons fire on the home bar when can_manage. NEW creates, DUP
    duplicates, DEL deletes -- the same actions the old labeled buttons fired."""
    from runtime import host_app
    ws = _ws(tmp_path)
    assert ws.can_manage
    drv = host_app.ConsoleDriver(ws)
    n0 = len(ws.launcher.items)
    drv.click(*_center(ws.layout.new_btn))           # NEW
    drv.frame(1 / 30)
    assert len(ws.launcher.items) == n0 + 1
    drv.click(*_center(ws.layout.dup_btn))           # DUP
    drv.frame(1 / 30)
    assert len(ws.launcher.items) == n0 + 2
    drv.click(*_center(ws.layout.del_btn))           # DEL
    drv.frame(1 / 30)
    assert len(ws.launcher.items) == n0 + 1


def test_launcher_bar_management_hidden_when_read_only(tmp_path):
    """With writes disabled (can_manage False) a tap on the NEW icon position does
    nothing -- the management cluster isn't active (it's not drawn either)."""
    from runtime import host_app
    ws = _ws(tmp_path)
    ws.can_manage = False
    drv = host_app.ConsoleDriver(ws)
    n0 = len(ws.launcher.items)
    drv.click(*_center(ws.layout.new_btn))
    drv.frame(1 / 30)
    assert len(ws.launcher.items) == n0
