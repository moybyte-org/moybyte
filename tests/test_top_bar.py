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
    ws.cart_error = "boom"   # Stage 5: the in-cart bar is CRASH chrome (pause retired)
    ws._dirty = True
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
    ws.cart_error = "boom"   # Stage 5: the in-cart bar is CRASH chrome (pause retired)
    ws._dirty = True
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
    ws.cart_error = "boom"   # Stage 5: the in-cart bar is CRASH chrome (pause retired)
    ws._dirty = True
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
    ws.cart_error = "boom"   # Stage 5: the in-cart bar is CRASH chrome (pause retired)
    ws._dirty = True
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
    ws.cart_error = "boom"   # Stage 5: the in-cart bar is CRASH chrome (pause retired)
    ws._dirty = True
    drv.click(*_center(C._BLOCKS_BTN))
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "blocks"


def test_launcher_bar_menu_opens_settings(tmp_path):
    """Settings moved off the bar into the ≡ system menu (#52). Tapping ≡ on the home
    bar opens the dropdown; SETTINGS (#105: the second selectable row now, after the
    launcher-only SEARCH row) opens Settings."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    drv.click(*_center(ws.layout.sysmenu_btn))
    drv.frame(1 / 30)
    assert ws.sysmenu.open
    drv.press("down"); drv.frame(1 / 30)                    # SEARCH -> SETTINGS
    drv.press("a"); drv.frame(1 / 30); drv.frame(1 / 30)    # activate SETTINGS
    assert ws.screen == "settings"


# NEW/DUP/DEL moved off the launcher bar into the Editor picker's zone
# (docs/shell_ux_v1.md: the launcher is for PLAYING, the picker is for MANAGING
# projects) -- see tests/test_desktop_shell.py::test_launcher_home_no_longer_manages_carts
# for the launcher-side regression guard.

def test_picker_bar_dup_del_when_manageable(tmp_path):
    """Cart management moved off the launcher home into the Editor picker's lent
    zone (docs/shell_ux_v1.md: the launcher is for PLAYING, the picker is for
    MANAGING projects). DUP/DEL icons fire there when can_manage: DUP duplicates
    the picker's SELECTED cart immediately (a copy only ADDS, so it needs no
    guard); DEL is two-tap guarded -- the first tap arms, only the second deletes
    (see test_editor_picker.py for the "DELETE? TAP AGAIN" prompt + disarm rules)."""
    from runtime import host_app
    ws = _ws(tmp_path)
    assert ws.can_manage
    drv = host_app.ConsoleDriver(ws)
    ws.open_picker()
    ws.picker.sel = next(i for i, it in enumerate(ws.picker.items) if it.get("path"))
    n0 = len(ws.picker.items)
    drv.click(*_center(ws.layout.dup_btn))           # DUP
    drv.frame(1 / 30)
    assert len(ws.picker.items) == n0 + 1
    ws.picker.sel = next(i for i, it in enumerate(ws.picker.items) if it.get("path"))
    drv.click(*_center(ws.layout.del_btn))           # DEL tap 1: arms, does NOT delete
    drv.frame(1 / 30)
    assert len(ws.picker.items) == n0 + 1
    drv.click(*_center(ws.layout.del_btn))           # DEL tap 2: confirms
    drv.frame(1 / 30)
    assert len(ws.picker.items) == n0


def test_picker_bar_management_hidden_when_read_only(tmp_path):
    """With writes disabled (can_manage False), a tap on the picker's DUP icon
    position does nothing -- the management cluster isn't active (it's not drawn
    either)."""
    from runtime import host_app
    ws = _ws(tmp_path)
    ws.can_manage = False
    drv = host_app.ConsoleDriver(ws)
    ws.open_picker()
    n0 = len(ws.picker.items)
    drv.click(*_center(ws.layout.dup_btn))
    drv.frame(1 / 30)
    assert len(ws.picker.items) == n0


# -- Part 3: the wifi icon is a STATUS glyph + a shortcut to the wifi tool ----

def test_wifi_off_glyph_is_a_distinct_nonblank_tile():
    """The new "wifi_off" (wifi-with-a-red-slash) icon is a real, non-blank 16x16 tile
    at its own slot, and it DIFFERS from the connected "wifi" tile (the two states must be
    visually distinguishable). It also carries red (index 8) -- the slash."""
    from runtime import host_app  # noqa: F401 -- registers the editors aliases
    from runtime import console as C
    assert "wifi_off" in C._ICON and C._ICON["wifi_off"] < 32
    sheet = C._default_icon_sheet()
    off = sheet.tile_image(C._ICON["wifi_off"])
    on = sheet.tile_image(C._ICON["wifi"])
    assert off is not None and any(p for p in off.pix)     # non-blank
    assert list(off.pix) != list(on.pix)                   # distinct from connected wifi
    assert 8 in off.pix                                    # the red slash is present


def test_wifi_icon_kind_tracks_connection_state(tmp_path):
    """ws._wifi_icon_kind() -- the glyph the bar draws -- is "wifi_off" when there's no
    connection and "wifi" once the injected wifi service reports a link. FakeWifi boots
    disconnected, so the host default is deterministic ("wifi_off")."""
    ws = _ws(tmp_path)
    assert ws.wifi.status()[0] is False        # host FakeWifi boots offline
    assert ws._wifi_icon_kind() == "wifi_off"  # ...so the status glyph is the slashed wifi
    ws.wifi.connect("Home WiFi", "pw")         # now associated
    assert ws.wifi.status()[0] is True
    assert ws._wifi_icon_kind() == "wifi"      # connected -> the plain wifi glyph
    ws.wifi.disconnect()
    assert ws._wifi_icon_kind() == "wifi_off"  # back offline -> slashed again


def test_wifi_status_change_repaints_the_bar_strip(tmp_path):
    """The wifi kind is folded into the bar's cache key, so a connect/disconnect forces
    exactly the strip re-render that shows the new glyph (the #43 cache can't go stale)."""
    ws, drv = _run_a_cart(tmp_path)
    calls = [0]
    orig = ws.bar_layer._render_cart_bar

    def counting(cv, key):
        calls[0] += 1
        return orig(cv, key)
    ws.bar_layer._render_cart_bar = counting
    drv.frame(1 / 30)
    assert calls[0] == 0                        # unchanged state -> cache reused
    ws.wifi.connect("Home WiFi", "pw")          # link comes up -> the wifi glyph changes
    ws._dirty = True                            # crashed frames are static: force a repaint
    drv.frame(1 / 30)
    assert calls[0] == 1, "a wifi status change must re-render the bar once"


def test_tapping_the_wifi_icon_launches_the_wifi_tool(tmp_path):
    """Part 3: the right-zone wifi icon is a shortcut -- tapping it LAUNCHES the wifi.moy
    tool (runs it; never the editor). From the launcher home the tap lands on the running
    tool (screen "desktop", cart type "tool")."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    assert ws.screen == "launcher"
    drv.click(*_center(ws.layout.wifi_btn))     # tap the wifi status icon
    drv.frame(1 / 30)
    assert ws.screen == "desktop"               # launched the wifi tool (ran it)
    assert ws.cart is not None and ws.cart.get("type") == "tool"
    assert (ws.cart.get("path") or "").endswith("wifi.moy")


def test_launch_wifi_tool_is_a_noop_when_absent(tmp_path):
    """launch_wifi_tool() degrades cleanly (returns False, no crash, no screen change)
    when no wifi tool is installed -- so the shortcut is safe on a stripped device."""
    from runtime import host_app
    ws = _ws(tmp_path)
    # Drop every wifi tool from the launcher store view.
    ws.launcher.set_items([it for it in ws.launcher.items
                           if not (it.get("path") or "").endswith("wifi.moy")])
    assert ws.launch_wifi_tool() is False
    assert ws.screen == "launcher"


# -- cached running-cart top bar (#43): render once, blit each frame ---------

def _bar_rows(canvas):
    """The top-bar band of the GAME canvas as a flat list of palette indices (the
    _STATUS_H rows the bar occupies)."""
    from runtime import console as C
    return list(canvas.buf[:canvas.w * C._STATUS_H])


def _run_a_cart(tmp_path):
    """A workstation with a cart open and CRASHED, one frame drawn. Stage 5 retired the
    #71 pause frame -- the bar auto-hides while a cart PLAYS and shows only on a CRASH
    (the surviving in-cart chrome), so the running-cart bar tests drive the crashed
    state. A crashed frame is static, like the old paused one, so the tests force
    repaints with ws._dirty."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    ws.cart_error = "boom"
    ws._dirty = True
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
    ws.bar_layer._render_cart_bar(direct, ws.bar_layer._cart_bar_key())
    assert _bar_rows(direct) == cached
    # And it's a REAL picture (icons + glyph + text), not a blank band.
    assert len(set(cached)) > 2


def test_cart_bar_reuses_cache_when_state_unchanged(tmp_path):
    """A second running-cart frame with no state change must NOT re-render the bar -- it
    blits the cached strip. Witness it by counting _render_cart_bar calls across frames."""
    ws, drv = _run_a_cart(tmp_path)
    calls = [0]
    orig = ws.bar_layer._render_cart_bar

    def counting(cv, key):
        calls[0] += 1
        return orig(cv, key)
    ws.bar_layer._render_cart_bar = counting
    # Several more frames at the same state -> the strip is already built + clean.
    for _ in range(5):
        drv.frame(1 / 30)
    assert calls[0] == 0, "stale-but-clean bar should reuse the cache, not re-render"


def test_cart_bar_invalidates_on_theme_change(tmp_path):
    """A theme/IconSheet swap (set_icon_sheet bumps _bar_cache_gen) forces exactly one
    bar re-render on the next frame, then the new strip is reused."""
    ws, drv = _run_a_cart(tmp_path)
    calls = [0]
    orig = ws.bar_layer._render_cart_bar

    def counting(cv, key):
        calls[0] += 1
        return orig(cv, key)
    ws.bar_layer._render_cart_bar = counting
    ws.set_icon_sheet(ws.icon_sheet)              # bumps _bar_cache_gen -> key changes
    drv.frame(1 / 30)
    assert calls[0] == 1, "a theme change must re-render the bar once"
    for _ in range(3):
        ws._dirty = True                          # force repaints; crashed frames are otherwise static
        drv.frame(1 / 30)
    assert calls[0] == 1, "after the re-render the new strip is reused"


def test_cart_bar_invalidates_on_clock_change(tmp_path):
    """When the formatted clock string changes the cached key differs, so the bar
    re-renders. Force it by monkeypatching _clock_text (the only per-time input to the
    key) and asserting the next frame rebuilds the strip."""
    ws, drv = _run_a_cart(tmp_path)
    calls = [0]
    orig = ws.bar_layer._render_cart_bar

    def counting(cv, key):
        calls[0] += 1
        return orig(cv, key)
    ws.bar_layer._render_cart_bar = counting
    drv.frame(1 / 30)
    assert calls[0] == 0                          # clock unchanged -> reused
    ws.bar_layer._clock_text = lambda: "99:99"              # the clock "ticked"
    ws._dirty = True                              # crashed screens are static: force a repaint
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
        ws._dirty = True                          # crashed frames are static unless dirty
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


# -- the zoned bar (Stage 4 of docs/shell_ux_technical_plan_v1.md, #46): the
# right zone (clock/wifi/batt/gear) is OS-owned, the left zone is LENT to the
# active app (launcher_layer/settings_layer/editor_app). The #43 strip cache is
# GENERALIZED (not duplicated) to cover home/settings/menu too -- these tests are
# the redraw-count proof the perf guardrail demands, mirroring the existing
# cached-cart-bar tests above but for _render_zoned_bar's new callers.

def test_zoned_bar_reuses_cache_when_state_unchanged(tmp_path):
    """Repeated frames on a static zoned screen (the launcher home) must reuse the
    cached strip, not re-render -- the same proof as the running-cart cache, now
    for _render_cart_bar's "home" branch."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)              # one clean frame: the strip is built + current
    calls = [0]
    orig = ws.bar_layer._render_cart_bar

    def counting(cv, key):
        calls[0] += 1
        return orig(cv, key)
    ws.bar_layer._render_cart_bar = counting
    for _ in range(5):
        ws._dirty = True            # force a screen repaint (#44's own gate is not
                                     # what we're testing -- the BAR's cache is)
        drv.frame(1 / 30)
    assert calls[0] == 0, "an unchanged zoned bar should reuse its cached strip"


def test_zoned_bar_rerenders_on_app_switch(tmp_path):
    """Switching the active app (launcher -> the Editor) must re-render the bar
    exactly once (a new `where` is always a key change), then reuse the new strip."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    calls = [0]
    orig = ws.bar_layer._render_cart_bar

    def counting(cv, key):
        calls[0] += 1
        return orig(cv, key)
    ws.bar_layer._render_cart_bar = counting
    ws.launcher.sel = 0
    ws.open_in_editor()              # maker landing -> screen "menu"
    ws._dirty = True
    drv.frame(1 / 30)
    assert calls[0] == 1, "switching from the launcher to the Editor must re-render once"
    for _ in range(3):
        ws._dirty = True
        drv.frame(1 / 30)
    assert calls[0] == 1, "after the re-render the new strip is reused"


def test_zoned_bar_rerenders_on_editor_tab_switch(tmp_path):
    """Switching Editor tabs bumps EditorApp.zone_gen, which the cache key folds
    in -- so a tab switch re-renders the bar exactly once, proving zone_gen (not a
    per-frame poll) is what invalidates it."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    drv.frame(1 / 30)
    calls = [0]
    orig = ws.bar_layer._render_cart_bar

    def counting(cv, key):
        calls[0] += 1
        return orig(cv, key)
    ws.bar_layer._render_cart_bar = counting
    ws._open_paint()                 # EditorApp.set_tab bumps zone_gen
    ws._dirty = True
    drv.frame(1 / 30)
    assert calls[0] == 1, "switching Editor tabs must re-render the zoned bar once"
    for _ in range(3):
        ws._dirty = True
        drv.frame(1 / 30)
    assert calls[0] == 1, "after the re-render the new strip is reused"


def test_zoned_bar_rerenders_on_launcher_selection_change(tmp_path):
    """Moving the launcher selection bumps Launcher.zone_gen (via the `sel`
    property), so the cached strip picks up the new cart name -- proving the
    zone_gen wiring covers a raw `.sel = i` assignment (nav/tap/hover), not just a
    dedicated setter method."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    calls = [0]
    orig = ws.bar_layer._render_cart_bar

    def counting(cv, key):
        calls[0] += 1
        return orig(cv, key)
    ws.bar_layer._render_cart_bar = counting
    ws.launcher.nav2d(1, 0)           # move the selection -> zone_gen bumps
    ws._dirty = True
    drv.frame(1 / 30)
    assert calls[0] == 1, "a selection change must re-render the zoned bar once"
    for _ in range(3):
        ws._dirty = True
        drv.frame(1 / 30)
    assert calls[0] == 1


def test_zoned_bar_editor_zone_switches_tabs_and_plays(tmp_path):
    """The Editor's lent left zone (the tab ladder + PLAY) is really tappable: a
    tap on the PAINT icon switches menu_view, a tap on PLAY runs the cart."""
    from runtime import bar_layer as BL
    from runtime import editor_app as EA
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    drv.frame(1 / 30)
    paint_i = [t for t, _g in EA._ZONE_TABS].index("paint")
    x = BL._ZONE_LEFT_GAME[0] + paint_i * EA._ZONE_STRIDE + BL._BAR_ICON // 2
    y = BL._ZONE_LEFT_GAME[1] + BL._BAR_ICON // 2
    drv.click(x, y)
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "paint"
    play_i = [t for t, _g in EA._ZONE_TABS].index(None)   # PLAY (the ladder's last icon)
    x = BL._ZONE_LEFT_GAME[0] + play_i * EA._ZONE_STRIDE + BL._BAR_ICON // 2
    drv.click(x, y)
    drv.frame(1 / 30)
    assert ws.screen == "desktop", "PLAY must run the cart"


# -- the unified bar on the CODE/BLOCKS/MUSIC editors (Stage-4 rollout): the tab
# ladder + PLAY + X shows on these tabs too (no SAVE, #111), so navigation is
# IDENTICAL to the cards/paint/map tabs. code/blocks are SYSTEM-canvas (responsive
# layout.zone_left), so the tab-ladder icons hit-test in system coords (no
# _game_xy translation).

def _sys_zone_center(ws, target):
    """Center of the tab-ladder / PLAY icon `target` on the SYSTEM-canvas bar
    (the code + blocks tabs). `target` is a tab name or None (PLAY)."""
    from runtime import editor_app as EA, bar_layer as BL
    i = [t for t, _g in EA._ZONE_TABS].index(target)
    zx, zy, _zw, _zh = ws.layout.zone_left
    return (zx + i * EA._ZONE_STRIDE + BL._BAR_ICON // 2, zy + BL._BAR_ICON // 2)


def test_code_tab_shows_unified_bar_ladder_switches(tmp_path):
    """The CODE tab shows the SAME zoned bar: tapping the tab ladder switches views
    (proves the system-canvas responsive-rect zone_tap wiring)."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws.set_menu_view("code")                 # land on the code tab (system canvas)
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "code"
    drv.click(*_sys_zone_center(ws, "paint"))    # the ladder switches code -> paint
    drv.frame(1 / 30)
    assert ws.menu_view == "paint"


def test_code_tab_bar_play_runs_the_cart(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws.set_menu_view("code")
    drv.frame(1 / 30)
    drv.click(*_sys_zone_center(ws, None))       # PLAY
    drv.frame(1 / 30)
    assert ws.screen == "desktop"


def test_code_tab_context_x_exits_to_home(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws.set_menu_view("code")
    drv.frame(1 / 30)
    drv.click(*_center(ws.layout.context_x_btn))  # the right-zone X (system canvas)
    drv.frame(1 / 30)
    assert ws.screen == "launcher"


def test_play_hard_commits_the_active_tab(tmp_path):
    """#111: there is no SAVE icon anymore -- PLAY is a hard-commit trigger itself,
    dispatching to the ACTIVE tab's persist verb (EditorApp.leave calls
    save_current() before running). On the code tab that must reach ws.save_code."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws.set_menu_view("code")
    drv.frame(1 / 30)
    ws.editor.set_text("def _draw():\n    cls(3)\n")     # a valid edit to persist
    calls = []
    orig = ws.save_code
    ws.save_code = lambda: (calls.append(1), orig())[-1]
    drv.click(*_sys_zone_center(ws, None))                # PLAY
    drv.frame(1 / 30)
    assert calls == [1], "PLAY must hard-commit the code tab via save_code"


def test_tab_switch_hard_commits_the_outgoing_tab(tmp_path):
    """#111: a tab switch is an exit path too -- EditorApp.set_tab commits the
    OUTGOING tab (via save_current) before the ladder moves on, so navigating
    away from CODE persists it exactly like the removed SAVE icon used to."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws.set_menu_view("code")
    drv.frame(1 / 30)
    ws.editor.set_text("def _draw():\n    cls(5)\n")
    calls = []
    orig = ws.save_code
    ws.save_code = lambda: (calls.append(1), orig())[-1]
    drv.click(*_sys_zone_center(ws, "paint"))             # switch away from CODE
    drv.frame(1 / 30)
    assert calls == [1], "leaving the code tab must hard-commit it via save_code"


def test_bar_undo_redo_icons_dispatch_the_journal_walk(tmp_path):
    """#88: the Editor's lent bar zone grows shared UNDO/REDO icons wired to the
    SAME journal verbs the code editor's Ctrl+Z/Y already drives -- reachable from
    every tab, not just code. Two real commits, then a tap on each bar icon must
    actually walk the live source, and ws.can_undo()/can_redo() must track the
    journal cursor (the icons' dimmed/enabled state) at each step."""
    from runtime import host_app, editor_app as EA
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws.set_menu_view("code")
    drv.frame(1 / 30)

    # Nothing journaled for this cart yet -- UNDO must read disabled.
    assert ws.can_undo() is False
    assert ws.can_redo() is False

    ws.editor.set_text(ws.editor.text() + "\n# commit A\n")
    assert ws.save_code() is True          # commit #1: the floor -- still nothing before it
    assert ws.can_undo() is False

    ws.editor.set_text(ws.editor.text() + "# commit B\n")
    assert ws.save_code() is True          # commit #2: now there's a step back to A
    assert ws.can_undo() is True
    assert ws.can_redo() is False
    src_b = ws.editor.text()

    drv.click(*_sys_zone_center(ws, EA._ZONE_UNDO))    # the bar's UNDO icon
    drv.frame(1 / 30)
    assert "# commit A" in ws.editor.text() and "# commit B" not in ws.editor.text(), \
        "the bar UNDO icon must walk the journal back to commit A"
    assert ws.can_redo() is True

    drv.click(*_sys_zone_center(ws, EA._ZONE_REDO))    # the bar's REDO icon
    drv.frame(1 / 30)
    assert ws.editor.text() == src_b, "the bar REDO icon must re-apply commit B"
    assert ws.can_redo() is False


def test_bar_undo_redo_icons_are_a_no_op_when_disabled(tmp_path):
    """A tap on a disabled UNDO/REDO icon must be a safe no-op (ws.undo()/redo()
    already floor/ceiling-guard the walk) -- no crash, no spurious reload."""
    from runtime import host_app, editor_app as EA
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws.set_menu_view("code")
    drv.frame(1 / 30)
    assert ws.can_undo() is False and ws.can_redo() is False
    src = ws.editor.text()

    drv.click(*_sys_zone_center(ws, EA._ZONE_UNDO))
    drv.frame(1 / 30)
    drv.click(*_sys_zone_center(ws, EA._ZONE_REDO))
    drv.frame(1 / 30)
    assert ws.editor.text() == src


def test_blocks_tab_shows_unified_bar_ladder_play_and_x(tmp_path):
    """The BLOCKS tab (system canvas, like code) shows the SAME zoned bar: the tab
    ladder switches, PLAY runs, the context X exits."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws._open_blocks()
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "blocks"
    drv.click(*_sys_zone_center(ws, "code"))     # ladder: blocks -> code
    drv.frame(1 / 30)
    assert ws.menu_view == "code"
    ws._open_blocks()
    drv.frame(1 / 30)
    drv.click(*_sys_zone_center(ws, None))       # PLAY
    drv.frame(1 / 30)
    assert ws.screen == "desktop"


def test_blocks_tab_play_hard_commits_via_save_blocks(tmp_path):
    """#111: PLAY on the blocks tab hard-commits it (save_current -> save_blocks)
    before running -- no SAVE icon exists to do it explicitly anymore."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws._open_blocks()
    drv.frame(1 / 30)
    calls = []
    orig = ws.block_ui.save_blocks
    ws.block_ui.save_blocks = lambda: (calls.append(1), orig())[-1]
    drv.click(*_sys_zone_center(ws, None))       # PLAY
    drv.frame(1 / 30)
    assert calls == [1], "PLAY on the blocks tab must reach save_blocks"


def _game_zone_center(target):
    """Center of the ladder/PLAY icon `target` on the GAME-canvas bar
    (cards/paint/map/music). `target` is a tab name or None (PLAY)."""
    from runtime import editor_app as EA, bar_layer as BL
    i = [t for t, _g in EA._ZONE_TABS].index(target)
    return (BL._ZONE_LEFT_GAME[0] + i * EA._ZONE_STRIDE + BL._BAR_ICON // 2,
            BL._ZONE_LEFT_GAME[1] + BL._BAR_ICON // 2)


def test_music_tab_shows_unified_bar_ladder_play_and_x(tmp_path):
    """The MUSIC tab (game canvas, like cards/paint/map) shows the SAME zoned bar:
    the tab ladder switches, PLAY runs, the context X exits."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws._open_music()
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "music"
    drv.click(*_game_zone_center("paint"))       # ladder: music -> paint
    drv.frame(1 / 30)
    assert ws.menu_view == "paint"
    ws._open_music()
    drv.frame(1 / 30)
    drv.click(*_game_zone_center(None))          # PLAY
    drv.frame(1 / 30)
    assert ws.screen == "desktop"


def test_music_tab_play_hard_commits_via_save_sounds(tmp_path):
    """#111: PLAY on the music tab hard-commits it (save_current -> save_sounds)
    before running -- no SAVE icon exists to do it explicitly anymore."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws._open_music()
    drv.frame(1 / 30)
    calls = []
    orig = ws.save_sounds
    ws.save_sounds = lambda: (calls.append(1), orig())[-1]
    drv.click(*_game_zone_center(None))          # PLAY
    drv.frame(1 / 30)
    assert calls == [1], "PLAY on the music tab must reach save_sounds"


def test_music_tab_context_x_exits_to_home(tmp_path):
    from runtime import bar_layer as BL
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws._open_music()
    drv.frame(1 / 30)
    drv.click(*_center(BL._ZONE_CONTEXT_X))       # game-canvas right-zone X
    drv.frame(1 / 30)
    assert ws.screen == "launcher"


def _open_edit_cart(ws):
    """Copy a system cart that HAS a config ('edit') schema into the carts dir and
    select it, so open_in_editor lands on the Config (cards) tab."""
    import os
    import shutil
    from runtime import host_app
    src = os.path.join(str(ROOT), "system_carts", "star_catcher.moy")
    dst = os.path.join(ws.carts_root, "star_catcher.moy")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    ws.launcher.items = host_app.moy_carts.scan(ws.carts_root)
    ws.launcher.sel = [i for i, c in enumerate(ws.launcher.items)
                       if "star_catcher" in c["path"]][0]


def test_config_tab_play_runs_and_persists_config(tmp_path):
    """Fix B: the Config screen's GO button is gone -- PLAY (bar) must do GO's job:
    re-run the cart with the tuned config AND persist config.json. So PLAY on the
    Config tab reaches _save_config (the old GO), and the cart runs."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _open_edit_cart(ws)
    ws.open_in_editor()
    assert ws.screen == "menu" and ws.menu_view == "cards"   # Config-first landing
    saved = []
    orig = ws._save_config
    ws._save_config = lambda: (saved.append(1), orig())[-1]
    drv.click(*_game_zone_center(None))          # PLAY (game-canvas bar)
    drv.frame(1 / 30)
    assert ws.screen == "desktop", "PLAY must run the cart"
    assert saved == [1], "PLAY on Config must persist config (the old GO)"


def test_config_tab_body_has_no_go_code_close_buttons(tmp_path):
    """Fix B: the Config body drops its own GO / CODE / CLOSE -- those actions all
    live in the unified bar now (PLAY / the Code tab / the context X). The cards_layer
    module no longer defines their rects."""
    from runtime import cards_layer
    assert not hasattr(cards_layer, "_RUN_BTN")
    assert not hasattr(cards_layer, "_CODE_BTN")
    assert not hasattr(cards_layer, "_CLOSE_BTN")


def test_zoned_bar_gear_opens_sysmenu_from_every_zoned_screen(tmp_path):
    """The right zone's ≡ (moved off the left edge, Stage 4) is OS-owned and
    IDENTICAL everywhere the bar shows: home, Settings, and an Editor game-domain
    tab all open the same dropdown."""
    from runtime import bar_layer as BL
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)

    def _center(rect):
        x, y, w, h = rect
        return x + w // 2, y + h // 2

    drv.click(*_center(ws.layout.sysmenu_btn))     # home
    drv.frame(1 / 30)
    assert ws.sysmenu.open
    ws.sysmenu.close()

    ws.open_settings()
    drv.frame(1 / 30)
    drv.click(*_center(ws.layout.sysmenu_btn))     # settings
    drv.frame(1 / 30)
    assert ws.sysmenu.open
    ws.sysmenu.close()
    ws.go_home()

    ws.launcher.sel = 0
    ws.open_in_editor()
    ws._open_paint()
    drv.frame(1 / 30)
    drv.click(*_center(BL._ZONE_GEAR))             # Editor, game-domain tab
    drv.frame(1 / 30)
    assert ws.sysmenu.open


# -- the context X (Stage 5 of docs/shell_ux_technical_plan_v1.md, spec Section 9):
# the right zone's X exits the active TASKBAR app back toward the launcher root. The
# launcher IS the root -> it draws NO X; only the Editor / Settings get one.

def test_context_x_exits_the_editor_to_home(tmp_path):
    """A tap on the right-zone context X pops the Editor back to the launcher root. Drawn
    + hit-tested on an Editor game-domain tab via the fixed game-canvas _ZONE_CONTEXT_X."""
    from runtime import bar_layer as BL
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws._open_paint()                 # a game-domain tab -> the game-canvas zoned bar
    drv.frame(1 / 30)
    assert ws.screen == "menu"
    drv.click(*_center(BL._ZONE_CONTEXT_X))
    drv.frame(1 / 30)
    assert ws.screen == "launcher"   # the context X exited the Editor to home


def test_context_x_exits_settings_to_home(tmp_path):
    """The context X on Settings (a taskbar app, not the root) exits it back to the
    launcher home -- routed through the responsive Layout.context_x_btn on the system
    canvas, proving the X works on BOTH bar geometries."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()               # opened from the launcher home
    drv.frame(1 / 30)
    assert ws.screen == "settings"
    drv.click(*_center(ws.layout.context_x_btn))
    drv.frame(1 / 30)
    assert ws.screen == "launcher"   # the X exited Settings to home


def test_launcher_draws_no_context_x_and_a_tap_there_is_a_no_op(tmp_path):
    """The launcher is the back-stack ROOT: its right zone draws NO context X (spec
    Section 9) and a tap on that slot is a no-op -- there is nowhere to exit to, so the
    home screen stays put (handle_bar_tap guards the X on where != "home")."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    assert ws.screen == "launcher"
    drv.click(*_center(ws.layout.context_x_btn))
    drv.frame(1 / 30)
    assert ws.screen == "launcher"   # root: no X drawn, tap does not exit


def test_draw_zone_never_invoked_while_player_is_top_of_stack(tmp_path):
    """The #46 zoned bar's dispatch (draw_zone) MUST stay off the play frame: a
    Player -- playing OR crashed -- owns the "desktop" screen, and _render_cart_bar's
    "desktop" branch returns before any owner.draw_zone call. Patch every zone owner's
    draw_zone and drive a running cart through several playing AND crashed frames; none
    may ever fire (this is what keeps the lent-zone dispatch off the 50fps play frame --
    a zone drawn during play would be both a pixel regression and a per-frame cost the
    golden set can't catch). Stage 5 retired the pause frame, so the chrome case here is
    now the crash bar."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    calls = []
    for owner in (ws.launcher_layer, ws.settings_layer, ws.editor_app):
        orig = owner.draw_zone

        def spy(cv, rect, _orig=orig, _owner=owner):
            calls.append(_owner)
            return _orig(cv, rect)
        owner.draw_zone = spy

    ws.launcher.sel = 0
    ws.open()                      # PLAY landing: straight to the running "desktop"
    assert ws.screen == "desktop"
    for _ in range(5):             # playing
        ws._dirty = True
        drv.frame(1 / 30)
    ws.cart_error = "boom"
    for _ in range(5):             # crash chrome (the untouched desktop crash bar)
        ws._dirty = True
        drv.frame(1 / 30)
    assert calls == [], (
        "the zoned bar's draw_zone must never fire while a Player is top-of-stack "
        "(playing or crashed) -- got calls from: %r" % calls)
