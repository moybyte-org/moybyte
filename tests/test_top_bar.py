"""Tests for the unified, themeable 18px top bar (Stage 1): the launcher's old 14px
status strip and the running-cart's labeled button row are replaced by ONE 18px bar of
16x16 IconSheet sprites on both screens.

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver: mouse == touch, arrows == trackball), so these assert host==device
behavior. The IconSheet/storage tests poke the cores directly."""

from pathlib import Path
from ws_helpers import shelf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

import canvas_probe as probe  # noqa: E402  (pixel-width-agnostic "it drew" probes)
from ws_helpers import build_ws as _build_ws, use_theme  # noqa: E402


def _ws(tmp_path, **kw):
    """A workstation on the `night` fixture (tests/fixtures/themes/) in its
    dark mode: a palette theme with no look, so the default's, flat; a test
    of a look sets its theme. Its bar's HH:MM is
    pinned (`_pin_clock`): a test that counts strip renders across frames
    counts a real minute rollover too, and a loaded machine stretches the
    frames across one; a test of the clock sets `_clock_text` itself."""
    ws = _build_ws(tmp_path, **kw)
    use_theme(ws, "night", "dark")
    ws.bar_layer._clock_text = lambda: "12:00"
    return ws


def _pin_clock(ws, drv):
    """Freeze the bar's HH:MM and settle one frame, so a zero-redraw assertion
    is about the CACHE and not about the wall clock. _cart_bar_key folds
    _clock_text in on purpose (a minute rollover must repaint the bar), which
    means a test that counts zero re-renders across several frames straddles a
    real :00 sooner or later -- it did, in CI, on 2026-09-03 (the same rollover
    the redraw-on-change tests are already isolated for). The settle frame
    absorbs the one legitimate rebuild the pin itself causes."""
    ws.bar_layer._clock_text = lambda: "12:00"
    ws._dirty = True
    drv.frame(1 / 30)


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
    from runtime import chrome as _chrome, console as C
    sheet = _chrome._default_icon_sheet()
    assert sheet.TILE == 16
    for kind, slot in C._ICON.items():
        assert 0 <= slot < sheet.count, kind
        img = sheet.tile_image(slot)
        assert img is not None and img.w == 16 and img.h == 16, kind
        assert any(p for p in img.pix), "%s should not be a blank tile" % kind


def test_the_shells_icon_sheet_is_baked(tmp_path):
    """The workstation's IconSheet is the baked default (a 16x16 sheet, not
    None), whatever the store holds: a theme's own icons are its pixel
    tiles, never a file beside the carts."""
    from runtime import chrome as _chrome
    ws = _ws(tmp_path)
    assert ws.look.icon_sheet is not None
    assert ws.look.icon_sheet.TILE == 16
    assert ws.look.icon_sheet.count == 32
    assert ws.look.icon_sheet.to_hex() == _chrome._default_icon_sheet().to_hex()


# -- the bar renders without error on both screens ----------------------------

def test_the_bar_renders_on_both_screens(tmp_path):
    """The handheld bar is the look's, `hhbar.h` rows (28: the default's,
    which a theme with no look takes); the game canvas's strip keeps 18."""
    from runtime import console as C
    assert C._STATUS_H == 18
    ws = _ws(tmp_path)
    assert ws.layout.status_h == 28
    from runtime import host_app
    drv = host_app.ConsoleDriver(ws)
    # Launcher bar renders.
    assert ws.screen == "launcher"
    drv.frame(1 / 30)
    assert probe.distinct_pixels_in(drv.rgb888(), 3) > 4
    # Running-cart bar renders.
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    drv.frame(1 / 30)
    assert probe.distinct_pixels_in(drv.rgb888(), 3) > 4


def test_bar_falls_back_to_glyphs_without_an_icon_sheet(tmp_path):
    """A workstation with no icon sheet wired (icon_sheet is None) still draws the bar
    -- _icon falls back to the _glyph bitmap so nothing crashes."""
    from runtime import console, moy_carts, host_app, host_canvas
    carts_dir = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts_dir)
    moy_carts.create("Plain", carts_dir, src="def _draw():\n    cls(1)\n", type="app")
    carts = moy_carts.scan(carts_dir)
    canvas = host_canvas.make_system_canvas(320, 240)
    inp = host_app.InputState()
    ws = console.Workstation(host_app._NullComp(), canvas, inp, carts)
    ws.make_api = host_app.make_api
    ws.carts_store = moy_carts
    ws.carts_root = carts_dir
    ws.set_pointer(console.Pointer(320, 240))
    ws.load_system()
    assert ws.look.icon_sheet is None              # never wired
    ws.frame(1 / 30)                          # launcher bar renders (glyph fallback)
    ws.set_home_tab("mine")                   # Plain is the kid's own
    ws.launcher.sel = 0
    ws.open()
    ws.frame(1 / 30)                          # running-cart bar renders too


# -- tapping each icon position fires the right action ----------------------

def _center(rect):
    x, y, w, h = rect
    return x + w // 2, y + h // 2


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
    for _ in range(len(ws.sysmenu.items)):                  # past the card's group
        if ws.sysmenu.items[ws.sysmenu.sel][1] == "SETTINGS":
            break
        drv.press("down"); drv.frame(1 / 30); drv.frame(1 / 30)
    drv.press("a"); drv.frame(1 / 30); drv.frame(1 / 30)    # activate SETTINGS
    assert ws.screen == "settings"


# NEW/CHANGE/COPY/DEL are the home's Make-tab verbs (docs/shell_layout_2026-10.md
# §12: Make is the one projects list) -- chips in the lent zone, tapped
# through the bar.

def _make_chip(ws, verb):
    return _center(ws.launcher_layer._zone_action_rects(ws.layout.zone_left)[verb])


def test_make_tab_copy_and_delete_through_the_bar(tmp_path):
    """COPY duplicates the selected project at once (a copy only ADDS); DEL asks
    first (a cart's delete is permanent), and the dialog's Delete deletes it."""
    from runtime import host_app
    ws = _ws(tmp_path)
    assert ws.can_manage
    drv = host_app.ConsoleDriver(ws)
    ws.set_home_tab("make")
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items) if it.get("path"))
    drv.frame(1 / 30)
    n0 = len(ws.launcher.items)
    drv.click(*_make_chip(ws, "copy"))
    drv.frame(1 / 30)
    assert len(ws.launcher.items) == n0 + 1
    drv.click(*_make_chip(ws, "delete"))
    drv.frame(1 / 30)
    assert ws.confirm.open and len(ws.launcher.items) == n0 + 1
    x, y, w, h = ws.confirm._host.box("Delete")
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)                 # the release taps it; the tail deletes
    drv.frame(1 / 30)
    assert not ws.confirm.open and len(ws.launcher.items) == n0


def test_make_tab_management_hidden_when_read_only(tmp_path):
    """With writes disabled (can_manage False) Make carries no verb: nothing
    to make, copy or delete, and no "+ New" card."""
    ws = _ws(tmp_path)
    ws.can_manage = False
    ws.set_home_tab("make")
    assert all(it.get("path") for it in ws.launcher.items)
    for i in range(min(3, len(ws.launcher.items))):
        ws.launcher.sel = i
        assert ws.launcher_layer.zone_verbs() == ()


# -- Part 3: the wifi icon is a STATUS glyph + a shortcut to the WIFI panel ----

def test_wifi_off_glyph_is_a_distinct_nonblank_tile():
    """The new "wifi_off" (wifi-with-a-red-slash) icon is a real, non-blank 16x16 tile
    at its own slot, and it DIFFERS from the connected "wifi" tile (the two states must be
    visually distinguishable). It also carries red (index 8) -- the slash."""
    from runtime import host_app  # noqa: F401 -- registers the editors aliases
    from runtime import chrome as _chrome, console as C
    assert "wifi_off" in C._ICON and C._ICON["wifi_off"] < 32
    sheet = _chrome._default_icon_sheet()
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
    _pin_clock(ws, drv)
    calls = [0]
    orig = ws.bar_layer._render_cart_bar

    def counting(cv, key, i=0):
        calls[0] += 1
        return orig(cv, key, i)
    ws.bar_layer._render_cart_bar = counting
    drv.frame(1 / 30)
    assert calls[0] == 0                        # unchanged state -> cache reused
    ws.wifi.connect("Home WiFi", "pw")          # link comes up -> the wifi glyph changes
    ws._dirty = True
    drv.frame(1 / 30)
    assert calls[0] == 1, "a wifi status change must re-render the bar once"


def test_tapping_the_wifi_icon_opens_the_settings_wifi_panel(tmp_path):
    """The wifi widget's target is the Settings WIFI panel, the one wifi
    implementation (docs/shell_layout_2026-10.md §8): in console mode too
    its popover hung from the widget (docs/look_2026-10.md step 33), over
    whatever ran; never a cart."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    assert ws.screen == "launcher"
    drv.click(*_center(ws.layout.wifi_btn))     # tap the wifi status icon
    drv.frame(1 / 30)
    assert ws.sysmenu.open and ws.sysmenu.widget == "wifi"
    assert ws.screen == "launcher" and ws.cart is None   # nothing was launched
    assert "settings" in ws.leases.holders()    # the panel holds the radio
    drv.click(*_center(ws.layout.wifi_btn))     # the widget again closes it
    drv.frame(1 / 30)
    assert not ws.sysmenu.open
    assert ws.leases.holders() == []            # ...and lets the radio go


def test_tapping_the_wifi_icon_on_the_desk_opens_the_panel_as_a_popover(tmp_path):
    """The same tap in desktop mode: the panel in a popover anchored at the
    widget, no Settings window, the lease held while it is open."""
    from ws_helpers import build_desktop_ws
    from runtime import host_app
    ws = build_desktop_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    lay = ws.wm._root_ctx.layout
    drv.click(*_center(lay.wifi_btn))
    drv.frame(1 / 30)
    assert ws.sysmenu.open and ws.sysmenu.widget == "wifi"
    assert not ws.wm.top_is("settings")
    assert "settings" in ws.leases.holders()
    assert not any(c.get("path", "").endswith("wifi.moy") for c in ws.carts.all)


# -- cached running-cart top bar (#43): render once, blit each frame ---------

def _bar_rows(canvas):
    """The top-bar band of the GAME canvas as a flat list of PIXELS (the
    _STATUS_H rows the bar occupies)."""
    from runtime import console as C
    return list(memoryview(canvas._buf).cast("H")[:canvas.w * C._STATUS_H])


def _run_a_cart(tmp_path):
    """A workstation running a TOOL, one frame drawn: the running-cart bar a
    game never shows (it owns the glass, and a run that stopped shows the
    stop card) -- the tool's minimal bar on the game canvas."""
    from runtime import host_app, moy_carts
    from ws_helpers import no_unlock_banner
    ws = no_unlock_banner(_ws(tmp_path))
    drv = host_app.ConsoleDriver(ws)
    made = moy_carts.create("Tooly", ws.carts_root, src="def _draw():\n    cls(1)\n",
                            type="tool")
    ws.carts.rescan()
    ws._open_workspace(next(c for c in ws.carts.all if c["path"] == made["path"]))
    ws.run(ws.project, ws.launcher_layer)
    assert ws.screen == "desktop"
    ws._dirty = True
    drv.frame(1 / 30)
    return ws, drv


def test_cached_cart_bar_is_pixel_identical_to_direct_render(tmp_path):
    """The cached strip blitted onto the canvas must equal a DIRECT render of the same
    bar state. Drive a running-cart frame (cached path), snapshot the bar band, then
    render the bar straight onto a fresh game-sized canvas via the SAME _render_cart_bar
    body and compare the band byte-for-byte."""
    from runtime.host_canvas import make_system_canvas
    ws, drv = _run_a_cart(tmp_path)
    cached = _bar_rows(ws.canvas)                 # what the cache+blit produced
    # Direct render of the identical state onto a clean canvas.
    direct = make_system_canvas(ws.canvas.w, ws.canvas.h)
    direct.palette = ws.canvas.palette
    ws.bar_layer._render_cart_bar(direct, ws.bar_layer._cart_bar_key("tool"))
    assert _bar_rows(direct) == cached
    # And it's a REAL picture (icons + glyph + text), not a blank band.
    assert len(set(cached)) > 2


def test_cart_bar_reuses_cache_when_state_unchanged(tmp_path):
    """A second running-cart frame with no state change must NOT re-render the bar -- it
    blits the cached strip. Witness it by counting _render_cart_bar calls across frames."""
    ws, drv = _run_a_cart(tmp_path)
    _pin_clock(ws, drv)
    calls = [0]
    orig = ws.bar_layer._render_cart_bar

    def counting(cv, key, i=0):
        calls[0] += 1
        return orig(cv, key, i)
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

    def counting(cv, key, i=0):
        calls[0] += 1
        return orig(cv, key, i)
    ws.bar_layer._render_cart_bar = counting
    ws.look.set_icon_sheet(ws.look.icon_sheet)              # moves look_gen -> key changes
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

    def counting(cv, key, i=0):
        calls[0] += 1
        return orig(cv, key, i)
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


# -- the zoned bar (Stage 4 of docs/history/shell_ux_technical_plan_v1.md, #46): the
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
    _pin_clock(ws, drv)            # one clean frame: the strip is built + current
    calls = [0]
    orig = ws.bar_layer._render_cart_bar

    def counting(cv, key, i=0):
        calls[0] += 1
        return orig(cv, key, i)
    ws.bar_layer._render_cart_bar = counting
    for _ in range(5):
        ws._dirty = True            # force a screen repaint (#44's own gate is not
                                     # what we're testing -- the BAR's cache is)
        drv.frame(1 / 30)
    assert calls[0] == 0, "an unchanged zoned bar should reuse its cached strip"


def test_a_warm_bar_frame_fills_one_key_with_only_what_it_shows(tmp_path):
    """A painted frame's bar key is `where`'s one list, refilled in place (no
    key tuple a frame), and reads only the fields its bars draw: the home's
    zone is the launcher's, so the Editor's undo/redo dim state is never
    asked for there."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _pin_clock(ws, drv)
    bl = ws.bar_layer
    key = bl._keys["home"]
    asked = [0]

    def undo_bits():
        asked[0] += 1
        return 0
    ws.history.bar_undo_bits = undo_bits
    for _ in range(3):
        ws._dirty = True
        drv.frame(1 / 30)
    assert bl._keys["home"] is key
    assert asked[0] == 0
    assert bl._cart_bar_key("home")[9] == 0 and asked[0] == 1


def test_zoned_bar_rerenders_on_app_switch(tmp_path):
    """Switching the active app (launcher -> the Editor) must re-render the bar
    exactly once (a new `where` is always a key change), then reuse the new strip."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    calls = [0]
    orig = ws.bar_layer._render_cart_bar

    def counting(cv, key, i=0):
        calls[0] += 1
        return orig(cv, key, i)
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

    def counting(cv, key, i=0):
        calls[0] += 1
        return orig(cv, key, i)
    ws.bar_layer._render_cart_bar = counting
    ws._open_paint()                 # EditorApp.set_tab bumps zone_gen
    ws._dirty = True
    drv.frame(1 / 30)
    assert calls[0] == 1, "switching Editor tabs must re-render the zoned bar once"
    for _ in range(3):
        ws._dirty = True
        drv.frame(1 / 30)
    assert calls[0] == 1, "after the re-render the new strip is reused"


def test_zoned_bar_follows_the_home_tabs_not_its_selection(tmp_path):
    """The home's zone draws its tabs alone: moving the selection along the
    row (a held key's glide) reuses the cached strip, whose pixels are the
    ones a fresh render paints, and a tab switch renders it once."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    calls = [0]
    orig = ws.bar_layer._render_cart_bar

    def counting(cv, key, i=0):
        calls[0] += 1
        return orig(cv, key, i)
    ws.bar_layer._render_cart_bar = counting
    ws.launcher.nav2d(1, 0)
    ws._dirty = True
    for _ in range(60):
        drv.frame(1 / 30)
    assert not ws.launcher.moving and ws.launcher.sel == 1
    assert calls[0] == 0, "a selection the zone does not show re-rendered the bar"
    bar = ws.layout.bars[0][1]
    w = ws.sys_canvas.w

    def strip():
        return [drv.rgb888()[(y * w + bar[0]) * 3:(y * w + bar[0] + bar[2]) * 3]
                for y in range(bar[1], bar[1] + bar[3])]
    kept = strip()
    ws.bar_layer.invalidate()
    ws._dirty = True
    drv.frame(1 / 30)
    assert strip() == kept
    calls[0] = 0
    ws.set_home_tab("apps")
    for _ in range(3):
        ws._dirty = True
        drv.frame(1 / 30)
    assert calls[0] == 1, "a tab switch must re-render the zoned bar once"


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
    drv.click(*_game_zone_center("paint", ws))
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "paint"
    drv.click(*_game_zone_center(None, ws))   # PLAY (the ladder's last icon)
    drv.frame(1 / 30)
    assert ws.screen == "desktop", "PLAY must run the cart"


# -- the unified bar on the CODE/BLOCKS/MUSIC editors (Stage-4 rollout): the tab
# ladder + PLAY + X shows on these tabs too (no SAVE, #111), so navigation is
# IDENTICAL to the cards/paint/map tabs. code/blocks are SYSTEM-canvas (responsive
# layout.zone_left), so the tab-ladder icons hit-test in system coords (no
# _game_xy translation).

def _ladder_center(ws, rect, target):
    """Center of the ladder icon `target` in the lent `rect`, through the
    geometry the draw and the tap share (the open tab's chip is wider)."""
    for tab, _g, (x, y, w, h) in ws.editor_app._ladder_rects(rect):
        if tab == target:
            return (x + w // 2, y + h // 2)
    raise AssertionError("not on the ladder: %r" % (target,))


def _sys_zone_center(ws, target):
    """Center of the tab-ladder / PLAY icon `target` on the SYSTEM-canvas bar
    (the code + blocks tabs). `target` is a tab name or None (PLAY)."""
    return _ladder_center(ws, ws.bar_layer._zone_rect("menu"), target)


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
    drv.click(*_center(ws.bar_layer.cell("menu", "close")[1]))  # the right-zone X
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
    ws.save_code = lambda **kw: (calls.append(1), orig(**kw))[-1]
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
    ws.save_code = lambda **kw: (calls.append(1), orig(**kw))[-1]
    drv.click(*_sys_zone_center(ws, "paint"))             # switch away from CODE
    drv.frame(1 / 30)
    assert calls == [1], "leaving the code tab must hard-commit it via save_code"


def test_bar_undo_redo_icons_dispatch_the_journal_walk(tmp_path):
    """#88: the Editor's lent bar zone grows shared UNDO/REDO icons wired to the
    SAME journal verbs the code editor's Ctrl+Z/Y already drives -- reachable from
    every tab, not just code. Two real commits, then a tap on each bar icon must
    actually walk the live source, and ws.history.can_undo()/can_redo() must track the
    journal cursor (the icons' dimmed/enabled state) at each step."""
    from runtime import host_app, editor_app as EA
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws.set_menu_view("code")
    drv.frame(1 / 30)

    # Nothing journaled for this cart yet -- UNDO must read disabled.
    assert ws.history.can_undo() is False
    assert ws.history.can_redo() is False

    ws.editor.set_text(ws.editor.text() + "\n# commit A\n")
    assert ws.save_code() is True          # commit #1: the floor -- still nothing before it
    assert ws.history.can_undo() is False

    ws.editor.set_text(ws.editor.text() + "# commit B\n")
    assert ws.save_code() is True          # commit #2: now there's a step back to A
    assert ws.history.can_undo() is True
    assert ws.history.can_redo() is False
    src_b = ws.editor.text()

    drv.click(*_sys_zone_center(ws, EA._ZONE_UNDO))    # the bar's UNDO icon
    drv.frame(1 / 30)
    assert "# commit A" in ws.editor.text() and "# commit B" not in ws.editor.text(), \
        "the bar UNDO icon must walk the journal back to commit A"
    assert ws.history.can_redo() is True

    drv.click(*_sys_zone_center(ws, EA._ZONE_REDO))    # the bar's REDO icon
    drv.frame(1 / 30)
    assert ws.editor.text() == src_b, "the bar REDO icon must re-apply commit B"
    assert ws.history.can_redo() is False


def test_bar_undo_redo_icons_are_a_no_op_when_disabled(tmp_path):
    """A tap on a disabled UNDO/REDO icon must be a safe no-op (ws.history.undo()/redo()
    already floor/ceiling-guard the walk) -- no crash, no spurious reload."""
    from runtime import host_app, editor_app as EA
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws.set_menu_view("code")
    drv.frame(1 / 30)
    assert ws.history.can_undo() is False and ws.history.can_redo() is False
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


def _game_zone_center(target, ws):
    """Center of the ladder/PLAY icon `target` on the GAME-canvas bar
    (cards/paint/map/music). `target` is a tab name or None (PLAY)."""
    from runtime import bar_layer as BL
    return _ladder_center(ws, ws.bar_layer._zone_rect("menu"), target)


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
    drv.click(*_game_zone_center("paint", ws))       # ladder: music -> paint
    drv.frame(1 / 30)
    assert ws.menu_view == "paint"
    ws._open_music()
    drv.frame(1 / 30)
    drv.click(*_game_zone_center(None, ws))          # PLAY
    drv.frame(1 / 30)
    assert ws.screen == "desktop"


def test_music_tab_play_hard_commits_via_save_sounds(tmp_path):
    """#111: PLAY on an EDITED music tab hard-commits it (save_current ->
    save_sounds) before running -- no SAVE icon exists to do it explicitly
    anymore. (An untouched tab skips instead since music joined the #154
    clean-tab guard -- that half is pinned by test_autosave_exit.)"""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws._open_music()
    drv.frame(1 / 30)
    ws.music_ui.musicedit.toggle_rest()          # a real edit: PLAY must persist it
    calls = []
    orig = ws.save_sounds
    ws.save_sounds = lambda: (calls.append(1), orig())[-1]
    drv.click(*_game_zone_center(None, ws))          # PLAY
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
    drv.click(*_center(ws.bar_layer.cell("menu", "close")[1]))  # the right-zone X
    drv.frame(1 / 30)
    assert ws.screen == "launcher"


def _open_edit_cart(ws):
    """Copy a system cart that HAS a config ('edit') schema into the carts dir and
    select it, so open_in_editor lands on the Config (cards) tab."""
    import os
    import shutil
    from runtime import host_app
    src = os.path.join(str(ROOT), "system_carts", "moybyte.star_catcher.moy")
    dst = os.path.join(ws.carts_root, "moybyte.star_catcher.moy")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    ws.launcher.items = shelf(ws.carts_root)
    ws.launcher.sel = [i for i, c in enumerate(ws.launcher.items)
                       if "moybyte.star_catcher" in c["path"]][0]


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
    drv.click(*_game_zone_center(None, ws))          # PLAY (game-canvas bar)
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
    drv.click(*_center(ws.bar_layer.cell("menu", "menu")[1]))  # the Editor's ≡
    drv.frame(1 / 30)
    assert ws.sysmenu.open


# -- the context X (Stage 5 of docs/history/shell_ux_technical_plan_v1.md, spec Section 9):
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
    drv.click(*_center(ws.bar_layer.cell("menu", "close")[1]))
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


# -- the strip cache survives an alternating destination canvas (P4, 2026-07-26) --

def test_bar_strip_cache_survives_alternating_canvases(tmp_path):
    """The windowed WM draws the SAME `where` into different canvases on
    successive frames -- straight into the root canvas through a viewport on a
    quiet frame (wm_windowed._direct_render), into the window's own buffer
    otherwise. The strip cache used to remember exactly one canvas, so every
    switch read as a canvas swap and rebuilt the strip via new_layer.

    On P4 glass that was 72ms of an 86ms Settings frame, twice per gesture (press
    edge and release edge), because the P4's new_layer pre-collects. Counting
    new_layer is the assertion because it is the expensive part; a rebuild that
    reused the buffer would be cheap and would not have shown up on glass."""
    ws = _ws(tmp_path)
    ws.open_settings()
    root = ws.sys_canvas
    other = root.new_layer(root.w, root.h)      # stand-in for a window buffer
    builds = []
    for cv in (root, other):
        orig = cv.new_layer

        def spy(w, h, *a, _orig=orig, _cv=cv, **k):
            builds.append(_cv)          # host Canvas.new_layer has no `owner`
            return _orig(w, h, *a, **k)
        cv.new_layer = spy

    # Alternate destinations the way a gesture does: buffer, root, root, ..., buffer.
    order = [other] + [root] * 6 + [other]
    for cv in order:
        ws._sys_canvas = cv
        ws.bar_layer._draw_status_strip("settings")

    # One build per DISTINCT canvas, not one per switch.
    assert len(builds) == 2, (
        "expected one strip build per canvas; got %d builds across %d draws"
        % (len(builds), len(order)))
    assert builds.count(root) == 1 and builds.count(other) == 1


def test_bar_strip_slots_stay_bounded(tmp_path):
    """...and the per-canvas cache must not grow without limit: each strip pins a
    layer (~36KB at 1024 wide) AND the canvas it was built for, so an unbounded
    dict keyed by canvas would retain dead window buffers."""
    from runtime import bar_layer as _bl
    ws = _ws(tmp_path)
    ws.open_settings()
    root = ws.sys_canvas
    for _ in range(6):                          # six distinct destinations
        ws._sys_canvas = root.new_layer(root.w, root.h)
        ws.bar_layer._draw_status_strip("settings")
    slots = ws.bar_layer._bar_strips["settings"][0]
    assert len(slots) <= _bl._BAR_STRIP_SLOTS


def test_bar_strip_rebuild_budget(tmp_path):
    """The REGRESSION NET for the 2026-07-26 bar-strip bug, expressed as a budget.

    That bug was invisible: the cache silently missed 100% of the time and the only
    symptom was two frames per gesture costing 86ms instead of 12. Nothing broke, so
    nothing failed -- it took a day of on-glass profiling to corner. A build budget
    is what makes it fail here instead: alternating destinations must cost a
    BOUNDED number of strip builds, not one per switch.

    Asserts on ws.costs (ws.note_cost), which is counted on the BUILD path only, so
    the net costs nothing in the healthy case."""
    ws = _ws(tmp_path)
    ws.open_settings()
    root = ws.sys_canvas
    other = root.new_layer(root.w, root.h)
    ws.costs.clear()
    for i in range(24):                        # 24 frames alternating like the WM
        ws._sys_canvas = other if i % 4 == 0 else root
        ws.bar_layer._draw_status_strip("settings")
    allocs = ws.costs.get("bar.strip.alloc", 0)
    renders = ws.costs.get("bar.strip.render", 0)
    # Lower bound FIRST: without it this test passes when the counters are gone,
    # which is how a regression net quietly stops being one (caught while
    # verifying it -- stashing the fix removed the note_cost calls too, and the
    # assertion below read 0 and sailed through).
    assert renders >= 1, "no strip build counted -- is ws.note_cost still wired?"
    # One per distinct destination is right; one per SWITCH is the bug (would be 12).
    assert allocs <= 2, "strip layer reallocated %d times in 24 frames" % allocs
    assert renders <= 2, "strip re-rendered %d times in 24 frames" % renders


# -- the 2026-10 look's bar (docs/look_2026-10.md step 14) --------------------

def test_a_looked_clock_tick_repaints_its_cell_as_the_strip_draws_it(tmp_path):
    """Under Moy's look the clock's own repaint (`redraw_clock`) lays the
    solid band and the chrome-ink clock exactly where a whole strip would."""
    ws = _ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant="light")
    cv = ws.sys_canvas
    bh = ws.layout.status_h
    assert bh == 28

    def frame(text):
        ws.bar_layer._clock_text = lambda: text
        ws.bar_layer.invalidate()
        ws._dirty = True
        ws.frame(1 / 30)
        return bytes(cv._buf[:cv.w * bh * 2])

    frame("12:34")
    whole = frame("12:35")
    frame("12:34")
    ws.bar_layer._clock_text = lambda: "12:35"
    ws.bar_layer.redraw_clock("home")
    assert bytes(cv._buf[:cv.w * bh * 2]) == whole


def test_the_editor_ladder_under_an_icon_set_is_the_looks(tmp_path, monkeypatch):
    """docs/look_2026-10.md step 23: under a theme's icon set the ladder
    draws the set's gear for Config and face for Sprites, the open tab its
    name in sentence case, and Play bare in `value`."""
    from runtime import host_app
    ws = _ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant="light")
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws.set_menu_view("code")
    import sys
    sets = sys.modules.get("iconsets") or sys.modules["runtime.iconsets"]
    drew = []
    real = sets.draw

    def spy(cv, kind, rect, ink, spec):
        drew.append((kind, ink))
        return real(cv, kind, rect, ink, spec)
    monkeypatch.setattr(sets, "draw", spy)
    ws.bar_layer.invalidate()
    drv.frame(1 / 30)
    kinds = [k for k, _i in drew]
    assert "sliders" in kinds and "sprite" in kinds and "code" in kinds
    assert "settings" not in kinds, "the gear is Settings', never the Config tab's"
    assert "edit" not in kinds and "paint" not in kinds
    assert ("play", ws.theme_colors["value"]) in drew
    # Day and Night take one rule: the T-Deck's one bar (no chevron, no
    # Wi-Fi, look step 30) names the open tab in either mode.
    zone = ws.bar_layer._zone_rect("menu")
    day = (ws.editor_app._open_label(zone), ws.editor_app._ladder_layout(zone)[1])
    ws.look.set_theme("moy", persist=False, variant="dark")
    zone = ws.bar_layer._zone_rect("menu")
    night = (ws.editor_app._open_label(zone), ws.editor_app._ladder_layout(zone)[1])
    assert day[0] == night[0] == "Code" and day[1] >= 0 and night[1] >= 0
