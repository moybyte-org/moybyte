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
    """The flat .moygfx hex format serializes/parses a 128x64 (16x16-tile) sheet just
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
    """No system_icons.moygfx on disk => the workstation falls back to the baked default
    IconSheet (a 16x16 themeable sheet, not None)."""
    from runtime import moy_carts
    carts_dir = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts_dir)
    assert moy_carts.load_system_icons(carts_dir) is None     # nothing saved
    ws = _ws(tmp_path)
    assert ws.icon_sheet is not None
    assert ws.icon_sheet.TILE == 16
    assert ws.icon_sheet.count == 32


def test_system_icons_load_save_round_trip(tmp_path):
    """load/save_system_icons round-trip the theme hex through a file beside the carts
    dir (mirrors shared.moygfx)."""
    from runtime import moy_carts
    from runtime import console as C
    carts_dir = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts_dir)
    hexs = C._default_icon_sheet().to_hex()
    moy_carts.save_system_icons(hexs, carts_dir)
    assert moy_carts.load_system_icons(carts_dir) == hexs
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
    from runtime import console, moy_carts, host_app
    carts_dir = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts_dir)
    moy_carts.create("Plain", carts_dir, src="def _draw():\n    cls(1)\n", type="app")
    carts = moy_carts.scan(carts_dir)
    canvas = host_app.Canvas(320, 240)
    inp = host_app.InputState()
    ws = console.Workstation(host_app._NullComp(), canvas, inp, carts)
    ws.make_api = host_app.make_api
    ws.carts_store = moy_carts
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


# -- cached running-cart top bar (#43): render once, blit each frame ---------

def _bar_rows(canvas):
    """The top-bar band of the GAME canvas as a flat list of palette indices (the
    _STATUS_H rows the bar occupies)."""
    from runtime import console as C
    return list(canvas.buf[:canvas.w * C._STATUS_H])


def _run_a_cart(tmp_path):
    """A workstation with a cart open (screen == 'desktop'), one frame drawn."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    drv.frame(1 / 30)
    return ws, drv


def test_cached_cart_bar_is_pixel_identical_to_direct_render(tmp_path):
    """The cached strip blitted onto the canvas must equal a DIRECT render of the same
    bar state. Drive a running-cart frame (cached path), snapshot the bar band, then
    render the bar straight onto a fresh game-sized canvas via the SAME _render_cart_bar
    body and compare the band byte-for-byte."""
    from runtime.canvas import Canvas
    ws, drv = _run_a_cart(tmp_path)
    cached = _bar_rows(ws.canvas)                 # what the cache+blit produced
    # Direct render of the identical state onto a clean canvas.
    direct = Canvas(ws.canvas.w, ws.canvas.h, ws.canvas.palette)
    ws._render_cart_bar(direct, ws._cart_bar_key())
    assert _bar_rows(direct) == cached
    # And it's a REAL picture (icons + glyph + text), not a blank band.
    assert len(set(cached)) > 2


def test_cart_bar_reuses_cache_when_state_unchanged(tmp_path):
    """A second running-cart frame with no state change must NOT re-render the bar -- it
    blits the cached strip. Witness it by counting _render_cart_bar calls across frames."""
    ws, drv = _run_a_cart(tmp_path)
    calls = [0]
    orig = ws._render_cart_bar

    def counting(cv, key):
        calls[0] += 1
        return orig(cv, key)
    ws._render_cart_bar = counting
    # Several more frames at the same state -> the strip is already built + clean.
    for _ in range(5):
        drv.frame(1 / 30)
    assert calls[0] == 0, "stale-but-clean bar should reuse the cache, not re-render"


def test_cart_bar_invalidates_on_theme_change(tmp_path):
    """A theme/IconSheet swap (set_icon_sheet bumps _bar_cache_gen) forces exactly one
    bar re-render on the next frame, then the new strip is reused."""
    ws, drv = _run_a_cart(tmp_path)
    calls = [0]
    orig = ws._render_cart_bar

    def counting(cv, key):
        calls[0] += 1
        return orig(cv, key)
    ws._render_cart_bar = counting
    ws.set_icon_sheet(ws.icon_sheet)              # bumps _bar_cache_gen -> key changes
    drv.frame(1 / 30)
    assert calls[0] == 1, "a theme change must re-render the bar once"
    for _ in range(3):
        drv.frame(1 / 30)
    assert calls[0] == 1, "after the re-render the new strip is reused"


def test_cart_bar_invalidates_on_clock_change(tmp_path):
    """When the formatted clock string changes the cached key differs, so the bar
    re-renders. Force it by monkeypatching _clock_text (the only per-time input to the
    key) and asserting the next frame rebuilds the strip."""
    ws, drv = _run_a_cart(tmp_path)
    calls = [0]
    orig = ws._render_cart_bar

    def counting(cv, key):
        calls[0] += 1
        return orig(cv, key)
    ws._render_cart_bar = counting
    drv.frame(1 / 30)
    assert calls[0] == 0                          # clock unchanged -> reused
    ws._clock_text = lambda: "99:99"              # the clock "ticked"
    drv.frame(1 / 30)
    assert calls[0] == 1, "a clock change must re-render the bar"


def test_cart_bar_blit_strip_used_each_frame(tmp_path):
    """Every running-cart frame stamps the cached strip via blit_strip (the per-frame
    cost is one flat copy, not a re-render). Count blit_strip calls over several frames."""
    ws, drv = _run_a_cart(tmp_path)
    calls = [0]
    orig = ws.canvas.blit_strip

    def counting(layer, dx=0, dy=0):
        calls[0] += 1
        return orig(layer, dx, dy)
    ws.canvas.blit_strip = counting
    for _ in range(4):
        drv.frame(1 / 30)
    assert calls[0] == 4, "the bar should blit its cached strip once per frame"


def test_icon_theme_versioning_reseeds_stale_keeps_current(tmp_path):
    """A saved icon theme older than _ICON_VERSION is re-seeded to the baked default at
    load (so shipped icon changes land on an already-themed device/desktop without a
    manual wipe, #47-style); a theme stamped at the current version is kept (a user's
    EDIT ICONS edit survives until the next bump)."""
    from runtime import console as C
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    store, root = ws.carts_store, ws.carts_root
    default_hex = C._default_icon_sheet().to_hex()
    other = ("f" if default_hex[0] != "f" else "0") + default_hex[1:]   # valid but different
    assert other != default_hex

    # STALE (version 0 < _ICON_VERSION): re-seeded to the baked default, version stamped.
    store.save_system_icons(other, root, 0)
    ws.load_icon_sheet()
    assert ws.icon_sheet.to_hex() == default_hex
    assert store.load_system_icons_version(root) == C._ICON_VERSION

    # CURRENT (>= _ICON_VERSION): the saved theme is kept untouched.
    store.save_system_icons(other, root, C._ICON_VERSION)
    ws.load_icon_sheet()
    assert ws.icon_sheet.to_hex() == other
