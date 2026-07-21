"""Stage 2 of the themeable top bar: a kid repaints the SYSTEM icon sheet in the
PAINT editor (Settings -> EDIT ICONS) and it PERSISTS, re-theming the bar live.

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver: mouse == touch, arrows == trackball), so these assert host==device
behavior. The theme save uses the exact _with_sd wrapper the cart sprite save uses
(host: direct write; device: with_sd_live), so what passes here is what the device runs."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws(tmp_path):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"))


def _center(rect):
    x, y, w, h = rect
    return x + w // 2, y + h // 2


def _icons_row_index(ws):
    return [r[0] for r in ws.settings_layer._SETTINGS_ROWS].index("icons")


def _click_icons_row(ws, drv):
    """Scroll the EDIT ICONS row into view (the WIFI row pushed it below the
    320x240 fold, #38) then click it -- the same scroll a kid's d-pad does."""
    sl = ws.settings_layer
    idx = _icons_row_index(ws)
    sl.set_msel = idx
    sl._settings_scroll()
    drv.frame(1 / 30)
    drv.click(*_center(sl._settings_row_rect(idx)))


# -- the Settings entry point opens the PAINT editor on the icon sheet -------

def test_settings_has_edit_icons_action_row(tmp_path):
    ws = _ws(tmp_path)
    rows = ws.settings_layer._SETTINGS_ROWS
    icons = [r for r in rows if r[0] == "icons"]
    assert icons, "Settings must have an EDIT ICONS row"
    key, label, kind = icons[0]
    assert label == "EDIT ICONS"
    assert kind == "action"


def test_edit_icons_opens_paint_on_the_icon_sheet(tmp_path):
    """Tapping the EDIT ICONS row opens the PAINT editor targeting ws.icon_sheet
    (not a cart sheet), with the editing-icons flag set."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    _click_icons_row(ws, drv)
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "theme"
    assert ws._editing_icons is True
    assert ws.paint is not None
    assert ws.paint.sheet is ws.icon_sheet         # editing the SYSTEM theme, not a cart


def test_edit_icons_reachable_by_keyboard_a(tmp_path):
    """A (or RUN) on the EDIT ICONS row opens the theme editor too (device d-pad)."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    ws.settings_layer.set_msel = _icons_row_index(ws)
    drv.press("a")
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "theme"


# -- paint a pixel + a hard commit persists + round-trips + re-themes the bar --
#
# There is no SAVE button (#111): ws.save_icons() is the hard-commit verb every
# real exit path (CLOSE/leave, a window/context-X, going home) now dispatches to
# automatically -- these tests call it directly to verify the persistence
# mechanism itself; test_close_auto_commits_the_icon_edit below (and the CLOSE/
# B-key/HOME tests further down) prove the AUTOMATIC dispatch.

def test_paint_and_save_persists_and_round_trips(tmp_path):
    """Paint a pixel in the theme editor, commit -> system_icons.moygfx exists,
    loads back non-None, round-trips the edit, and ws.icon_sheet reflects it."""
    from runtime import host_app, console as C, moy_carts
    ws = _ws(tmp_path)
    carts_dir = ws.carts_root
    assert moy_carts.load_system_icons(carts_dir) is None     # nothing saved yet
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    pe = ws.paint
    pe.n = 0
    pe.color = 9                                              # a color that isn't index 0
    before = ws.icon_sheet.pget(0, 0)
    # Tap the top-left grid cell to paint pixel (0,0) of tile 0.
    drv.click(C._PG_X0 + 1, C._PG_Y0 + 1)
    drv.frame(1 / 30)
    assert ws.icon_sheet.pget(0, 0) == 9 and before != 9      # painted in-RAM
    ws.save_icons()
    assert ws.save_status == "SAVED"
    # Persisted: the file now exists, loads non-None, and the edit round-trips.
    hexs = moy_carts.load_system_icons(carts_dir)
    assert hexs is not None
    reloaded = C.IconSheet.from_hex(hexs)
    assert reloaded.pget(0, 0) == 9
    # ws.icon_sheet reflects the edit and serializes identically to what was saved.
    assert ws.icon_sheet.to_hex() == hexs


def test_save_invalidates_bar_cache_and_bar_still_draws(tmp_path):
    """A theme commit drops the per-kind bar image cache so the next bar draw
    rebuilds its sprites from the new pixels -- and the next frame doesn't crash."""
    from runtime import host_app, console as C
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    # Warm the bar image cache (the running-cart bar draws icons via _bar_image).
    ws._bar_image("home")
    assert "home" in ws._bar_img_cache
    ws.paint.color = 11
    drv.click(C._PG_X0 + 1, C._PG_Y0 + 1)
    drv.frame(1 / 30)
    ws.save_icons()
    assert ws._bar_img_cache == {}                            # cache invalidated by save
    # Back to a desktop bar so the changed pixels are actually re-blit; no crash.
    drv.click(*_center(C._PAINT_CLOSE))
    drv.frame(1 / 30)
    assert ws.screen == "settings"
    drv.frame(1 / 30)
    from runtime import host_app as _h  # noqa: F401
    assert len(set(drv.rgb888())) > 4                         # the Settings bar renders


def test_close_auto_commits_the_icon_edit(tmp_path):
    """#111 regression: CLOSE hard-commits the icon sheet with NO explicit SAVE
    call -- ThemeLayer.leave() persists on the way out. Paint a pixel, tap CLOSE
    immediately (no save_icons() call), and the edit must already be on disk."""
    from runtime import host_app, console as C, moy_carts
    ws = _ws(tmp_path)
    carts_dir = ws.carts_root
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    ws.paint.n = 0
    ws.paint.color = 13
    drv.click(C._PG_X0 + 1, C._PG_Y0 + 1)
    drv.frame(1 / 30)
    assert ws.icon_sheet.pget(0, 0) == 13                      # painted in-RAM only
    drv.click(*_center(C._PAINT_CLOSE))                        # CLOSE, no SAVE tap
    drv.frame(1 / 30)
    assert ws.screen == "settings"
    hexs = moy_carts.load_system_icons(carts_dir)
    assert hexs is not None, "CLOSE must hard-commit the icon edit (#111)"
    assert C.IconSheet.from_hex(hexs).pget(0, 0) == 13


# -- BACK returns to Settings (not a cart) ----------------------------------

def test_close_returns_to_settings(tmp_path):
    from runtime import host_app, console as C
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    _click_icons_row(ws, drv)
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "theme"
    drv.click(*_center(C._PAINT_CLOSE))
    drv.frame(1 / 30)
    assert ws.screen == "settings"                            # back to Settings, not a cart
    assert ws._editing_icons is False
    assert ws.paint is None


def test_back_key_returns_to_settings(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    drv.press("b")
    drv.frame(1 / 30)
    assert ws.screen == "settings"


def test_home_key_from_theme_clears_editing_flag(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    drv.press("home")
    drv.frame(1 / 30)
    assert ws.screen == "launcher"
    assert ws._editing_icons is False


def test_home_key_from_theme_auto_commits_the_icon_edit(tmp_path):
    """#111 regression: HOME bypasses ThemeLayer.leave() entirely (it goes
    straight to Workstation.go_home -- see _leave_or_home), so go_home() itself
    must hard-commit a dirty icon sheet, or a HOME tap right after painting
    would silently lose the edit."""
    from runtime import host_app, console as C, moy_carts
    ws = _ws(tmp_path)
    carts_dir = ws.carts_root
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    ws.paint.n = 0
    ws.paint.color = 5
    drv.click(C._PG_X0 + 1, C._PG_Y0 + 1)
    drv.frame(1 / 30)
    assert ws.icon_sheet.pget(0, 0) == 5                       # painted in-RAM only
    drv.press("home")                                          # no CLOSE, no save_icons()
    drv.frame(1 / 30)
    assert ws.screen == "launcher"
    hexs = moy_carts.load_system_icons(carts_dir)
    assert hexs is not None, "HOME must hard-commit the icon edit (#111)"
    assert C.IconSheet.from_hex(hexs).pget(0, 0) == 5


# -- the theme editor must NOT touch a cart's sheet --------------------------

def test_editing_icons_does_not_modify_a_cart_sheet(tmp_path):
    """Editing the system icon sheet leaves every cart's own sprites.moygfx untouched
    (the theme editor is a parallel PaintEditor target, not the cart's)."""
    from runtime import host_app, console as C, moy_carts
    ws = _ws(tmp_path)
    carts_dir = ws.carts_root
    # Snapshot each on-disk cart sheet before theming.
    before = {}
    for c in moy_carts.scan(carts_dir):
        before[c["path"]] = c.get("sprites")
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    ws.paint.color = 7
    drv.click(C._PG_X0 + 1, C._PG_Y0 + 1)
    drv.frame(1 / 30)
    ws.save_icons()
    # No cart sheet changed on disk.
    for c in moy_carts.scan(carts_dir):
        assert c.get("sprites") == before.get(c["path"]), c["path"]
    # And the system theme file is what got written instead.
    assert moy_carts.load_system_icons(carts_dir) is not None


def test_cart_paint_flow_still_targets_the_cart_sheet(tmp_path):
    """Don't-regress: opening PAINT for a running cart still edits the CART sheet
    (editing-icons flag stays off, paint.sheet is the cart sheet)."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    ws._open_paint()
    drv.frame(1 / 30)
    assert ws.menu_view == "paint"
    assert ws._editing_icons is False
    assert ws.paint.sheet is ws.sheet                        # the cart's own sheet


# -- the theme editor starts from the current (default) theme ----------------

def test_theme_editor_starts_from_default_when_no_file(tmp_path):
    """With no system_icons.moygfx yet, the editor opens on the baked default IconSheet
    (a real 16x16 themeable sheet), and the first save creates the file."""
    from runtime import host_app, moy_carts
    ws = _ws(tmp_path)
    assert moy_carts.load_system_icons(ws.carts_root) is None
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    assert ws.paint.sheet.TILE == 16 and ws.paint.sheet.count == 32
    assert not ws.paint.sheet.is_blank()                     # the baked default is painted
