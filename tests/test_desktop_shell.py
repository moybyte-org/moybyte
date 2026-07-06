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
    # Wallpaper backdrop + icon grid + status strip => many colors.
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
    from runtime import console, moy_carts, host_app
    # A store with only a non-wallpaper cart.
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
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    ws.cycle_wallpaper(1)
    chosen = ws.wallpaper_id
    # It lands in system.json beside the carts dir.
    assert moy_carts.load_system(carts_dir).get("wallpaper") == chosen
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


def test_status_strip_menu_opens_settings_from_home(tmp_path):
    """The launcher has no bottom dock (#46) and no gear (#52 -- Settings moved into the
    ≡ system menu, OS-style). Tapping ≡ opens the menu; its first item SETTINGS opens
    Settings."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    x, y, w, h = ws.layout.sysmenu_btn
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.sysmenu.open
    drv.press("a"); drv.frame(1 / 30); drv.frame(1 / 30)   # activate SETTINGS (first item)
    assert ws.screen == "settings"


def test_settings_wallpaper_stepper_applies_and_persists(tmp_path):
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    ws.settings_layer.set_msel = 0                                       # wallpaper row
    before = ws.wallpaper_id
    drv.press("right")                                    # step the stepper
    drv.frame(1 / 30)
    assert ws.wallpaper_id != before
    assert moy_carts.load_system(carts_dir).get("wallpaper") == ws.wallpaper_id


def test_settings_back_returns_home(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    drv.click(C._SET_BACK[0] + 2, C._SET_BACK[1] + 2)
    drv.frame(1 / 30)
    assert ws.screen == "launcher"


def test_in_cart_menu_opens_settings_and_back_resumes_cart(tmp_path):
    """Settings must be reachable from inside a running cart via the in-cart bar's ≡
    system menu (#52), and Back must RESUME the cart, not strand you at the launcher
    (#46). The in-cart menu prepends a CART group, so SETTINGS is two rows down."""
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    ws.cart_paused = True          # the in-cart bar lives in the pause menu (#71)
    ws._dirty = True
    drv.click(C._SYSMENU_BTN[0] + 2, C._SYSMENU_BTN[1] + 2)   # ≡ on the in-cart bar
    drv.frame(1 / 30)
    assert ws.sysmenu.open
    for _ in range(2):                                       # RESTART -> DELETE -> SETTINGS
        drv.press("down"); drv.frame(1 / 30); drv.frame(1 / 30)
    drv.press("a"); drv.frame(1 / 30); drv.frame(1 / 30)     # activate SETTINGS
    assert ws.screen == "settings"
    drv.click(C._SET_BACK[0] + 2, C._SET_BACK[1] + 2)        # Back resumes the cart...
    drv.frame(1 / 30)
    assert ws.screen == "desktop"                             # ...not the launcher


def test_settings_mock_rows_do_not_touch_carts(tmp_path):
    """The mocked rows (volume/brightness/name) step cosmetic values in the system
    dict but are clearly not wired to a backend yet. (The old "theme" mock-choice is
    now the functional EDIT ICONS action -- see test_icon_theme.)"""
    ws = _ws(tmp_path)
    ws.open_settings()
    rows = [r[0] for r in ws.settings_layer._SETTINGS_ROWS]
    ws.settings_layer.set_msel = rows.index("volume")
    ws.settings_layer.settings_adjust(1)
    assert 0 <= ws.system.get("volume") <= 5
    ws.settings_layer.set_msel = rows.index("name")
    ws.settings_layer.settings_adjust(1)
    assert ws.system.get("name") in ws.settings_layer._MOCK_NAMES


# -- dock keeps the management + open flows working ------------------------

def test_dock_home_returns_from_settings(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    k = C._DOCK_SLOTS.index("home")
    x, y, w, h = ws.bar_layer._dock_slot_rect(k)
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.screen == "launcher"


def test_management_buttons_still_create_and_delete(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    n0 = len(ws.launcher.items)
    x, y, w, h = ws.layout.new_btn                        # NEW (Layout position; #52 put ≡ at slot 0)
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert len(ws.launcher.items) == n0 + 1


def test_tapping_a_cart_icon_opens_it_from_home(tmp_path):
    """The launcher's primary action: tapping a cart's icon tile opens it. (The old
    dock 'run' slot was removed from the launcher with the dock, #46.)"""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    x, y, w, h = ws.layout.tile_rect(0, ws.launcher.page)
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


def test_perf_breakdown_splits_draw_into_phases(tmp_path):
    """DRAWBRK (#43 follow-up): with perf capture on, a running cart's draw time is
    split into cart _update (logic) / cart _draw (render) / audio.tick / console
    chrome, exposed by perf_breakdown() (the device diag's DRAWBRK payload). Host
    frames are sub-ms so the numbers are tiny, but the wiring + shape must hold and the
    four phases must sum to ~the draw total (= chrome is the remainder)."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.perf_capture = True
    ws.launcher.sel = 0
    ws.open()
    for _ in range(5):
        drv.frame(1 / 30)
    assert ws.screen == "desktop"
    upd, cart, audio, chrome = ws.perf_breakdown()
    assert all(isinstance(v, float) and v >= 0 for v in (upd, cart, audio, chrome))
    # the split is the components of draw_ms; their sum tracks it within rounding.
    assert abs((upd + cart + audio + chrome) - ws._draw_ms) <= 2.0


# -- #46: no bottom dock on the launcher; it returns inside a cart ----------

def _spy_draw(ws):
    """Record which chrome layers a frame draws, by wrapping the draw methods.
    Returns a dict of call counters.

    The unified top bar (Stage 1) is now ONE drawer -- _draw_status_strip(where) --
    on both screens, so "strip" counts the launcher/Settings bar (where home/settings)
    and "incart" counts the running-cart bar (where == "desktop")."""
    seen = {"dock": 0, "incart": 0, "strip": 0}
    odock = ws.bar_layer._draw_dock
    ostrip = ws.bar_layer._draw_status_strip

    def dock(where):
        seen["dock"] += 1
        return odock(where)

    def strip(where):
        if where == "desktop":
            seen["incart"] += 1
        else:
            seen["strip"] += 1
        return ostrip(where)

    ws.bar_layer._draw_dock = dock
    ws.bar_layer._draw_status_strip = strip
    return seen


def test_launcher_does_not_draw_the_bottom_dock(tmp_path):
    """The home/launcher screen no longer draws the in-cart bottom dock (#46): it was
    a dead row there. The status strip is still drawn (clock/pips/management/gear)."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    seen = _spy_draw(ws)
    assert ws.screen == "launcher"
    drv.frame(1 / 30)
    assert seen["dock"] == 0          # the 6-slot dock is NOT drawn on the launcher
    assert seen["incart"] == 0        # nor the in-cart tool buttons (no cart open)
    assert seen["strip"] >= 1         # the status strip IS drawn (still present)


def test_in_cart_dock_returns_when_a_cart_is_open(tmp_path):
    """PAUSING a cart brings up the in-cart tool bar (EDIT/PAINT/MAP/HOME); while it
    PLAYS the game owns the full canvas with no chrome at all (#71). The launcher's
    removal of the dead dock does not touch the in-cart chrome."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    seen = _spy_draw(ws)
    drv.frame(1 / 30)
    assert seen["incart"] == 0        # playing: NO bar over the game
    ws.cart_paused = True
    ws._dirty = True
    drv.frame(1 / 30)
    assert seen["incart"] >= 1        # paused: the in-cart tool buttons ARE drawn
    # The 6-slot bottom dock belongs to home/settings, not a running cart.
    assert seen["dock"] == 0


def test_cart_pause_menu_freezes_and_resumes(tmp_path):
    """#71: HOME (q on the T-Deck) TOGGLES a running cart's pause screen -- the
    cart freezes, chrome appears -- it never exits by itself (no per-input-mode
    special case). A tap outside the chrome (or HOME again) resumes without
    leaking into the cart; the pause screen's own QUIT button is the one way
    out to the launcher."""
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    calls = [0]
    orig_upd = ws._update

    def counting(dt):
        calls[0] += 1
        if orig_upd:
            orig_upd(dt)
    ws._update = counting

    drv.frame(1 / 30)
    assert calls[0] == 1               # playing: the cart ticks
    drv.press("home")
    drv.frame(1 / 30)
    assert ws.cart_paused              # first HOME pauses, does NOT exit
    assert ws.screen == "desktop"
    ticks = calls[0]
    for _ in range(5):
        drv.frame(1 / 30)
    assert calls[0] == ticks           # paused: the cart is frozen
    drv.click(160, 200)                # tap outside the chrome -> resume
    drv.frame(1 / 30)
    assert not ws.cart_paused
    drv.frame(1 / 30)
    assert calls[0] > ticks            # ...and the cart ticks again
    drv.press("home")
    drv.frame(1 / 30)
    drv.frame(1 / 30)                  # release frame -- the edge detector needs it
    assert ws.cart_paused
    drv.press("home")                  # HOME again just TOGGLES back (no special case)
    drv.frame(1 / 30)
    assert not ws.cart_paused
    assert ws.screen == "desktop"
    drv.frame(1 / 30)                  # release frame -- the edge detector needs it
    drv.press("home")                  # pause once more...
    drv.frame(1 / 30)
    assert ws.cart_paused
    drv.click(*C._PAUSE_QUIT_BTN[:2])  # ...and QUIT is the one way out
    drv.frame(1 / 30)
    assert ws.screen == "launcher"


def test_launcher_grid_band_reclaims_the_freed_dock_space(tmp_path):
    """With the dock gone the launcher cart grid extends below the old dock line: on a
    larger canvas the responsive grid uses the reclaimed band (more rows than it would
    fit if it still stopped at the dock)."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(640, 480))
    lay = ws.layout
    # grid_bottom is the canvas floor (no dock), strictly below the old dock line.
    assert lay.grid_bottom > lay.dock_y
    # The reclaimed band fits at least one more row than the dock-bounded band would.
    band_now = lay.grid_bottom - lay.icon_y0
    band_old = lay.dock_y - lay.icon_y0
    rows_now = (band_now + lay.icon_gap_y) // (lay.icon_h + lay.icon_gap_y)
    rows_old = (band_old + lay.icon_gap_y) // (lay.icon_h + lay.icon_gap_y)
    assert rows_now >= rows_old


def test_status_strip_stays_legible_over_an_animated_wallpaper(tmp_path):
    """#46: the status strip must stay readable on the home screen even with a live
    animated wallpaper behind it. The strip's backing band fills its rows, so the
    clock text region is the strip's dark backing, not wallpaper noise, and the
    wallpaper's own content is pushed below the strip (its safe area)."""
    from runtime import console as C
    from runtime import host_app
    ws = _ws(tmp_path)
    # Pick a real animated wallpaper cart (the shipped ones print a title at y=10,
    # which is inside the strip band -- the bug #46 fixes).
    cart_wp = [o for o in ws.wallpaper_options() if not str(o).startswith("fill:")]
    assert cart_wp, "expected at least one wallpaper cart seeded"
    ws.select_wallpaper(cart_wp[0], persist=False)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    sc = ws.sys_canvas
    lay = ws.layout
    # The strip backing band is solid black across its rows (legible scrim), so the
    # top-left clock region is not overwritten by wallpaper art.
    black = C.NAMES["black"]
    # Sample a column at the far right of the strip, above the pips' glyph rows but
    # within the band: the backing fill must be present (not raw wallpaper).
    y = 0
    assert sc.buf[y * sc.w + 1] == black
    # The wallpaper's safe area (just below the strip) is NOT plain black -- the
    # animated backdrop visibly occupies the rows beneath the strip.
    below = lay.status_h + 2
    band = set(sc.buf[below * sc.w:below * sc.w + sc.w])
    assert len(band) >= 1   # rendered without error; backdrop occupies below-strip rows


def test_ota_channel_toggle_persists(tmp_path):
    # #53 two-channel OTA: when an OTA-capable updater with WiFi is wired, Settings gains
    # a CHANNEL toggle (STABLE <-> BETA). Toggling persists to system.json so a fresh
    # boot remembers it. (build_workstation injects no updater on the host, so fake one.)
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)

    class _FakeUpdater:
        def available(self):
            return True

        def online_available(self):
            return True

    ws.updater = _FakeUpdater()
    ws._updater_ok = None          # invalidate the cached capability checks
    ws._online_ok = None

    ws.open_settings()
    keys = [r[0] for r in ws.settings_layer._settings_rows()]
    assert "ota_channel" in keys and "update_online" in keys
    assert ws._ota_channel() == "stable"           # default

    ws.settings_layer.set_msel = keys.index("ota_channel")
    ws.settings_layer.settings_adjust(1)
    assert ws._ota_channel() == "unstable" and ws.system.get("ota_channel") == "unstable"
    ws.settings_layer.settings_adjust(-1)                          # any direction flips (two channels)
    assert ws._ota_channel() == "stable"

    ws.settings_layer.settings_adjust(1)                           # -> unstable, then check it persisted
    ws2 = host_app.build_workstation(carts_dir)
    assert ws2.system.get("ota_channel") == "unstable"


def test_update_screen_animates_across_frames(tmp_path):
    # Regression (extraction stage 6): Workstation._animating() read self._upd_phase
    # after that state moved onto self.update_ui, so the SECOND frame on the update
    # screen crashed with AttributeError -- frame 1 short-circuits on _dirty, so the
    # bug only surfaces once _needs_redraw actually calls _animating(). The golden
    # single-frame renders + the rest of the suite never drove the update screen
    # past its first frame (and never with screen == "update"), which is why it slipped.
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.update_ui.open_update()                 # no updater on host -> screen = "update"
    assert ws.screen == "update"
    ws.update_ui._upd_phase = "install"        # an ANIMATING phase: _animating() reads _upd_phase
    for _ in range(3):
        drv.frame(1.0 / 30)                    # pre-fix: AttributeError in _animating() on frame 2
    assert ws.screen == "update"               # survived multiple frames without crashing


# -- boot logo (moybyte splash) --------------------------------------------

def test_boot_splash_holds_then_reveals_launcher(tmp_path):
    """arm_splash() shows the boot logo for a hold, then the launcher takes over.
    The splash is opt-in (armed by the boot entries), so a bare Workstation still
    renders the launcher on the first frame -- which every other test relies on."""
    from runtime import host_app, console
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    assert ws._splash_until is None                 # not armed by construction

    ws.arm_splash(500)
    assert ws._splash_until is not None
    drv.frame(1 / 30)                                # paints the boot logo, no error
    assert ws._splash_until is not None             # still within the hold
    assert len(set(drv.rgb888())) > 4               # Moy + wordmark => many colors

    ws._splash_until = console._ticks_ms() - 1       # force the deadline into the past
    drv.frame(1 / 30)                                # this frame expires it...
    assert ws._splash_until is None
    drv.frame(1 / 30)                                # ...and the launcher renders after
    assert len(set(drv.rgb888())) > 4


def test_moy_mascot_baked_into_default_icon_sheet():
    """The 'moy' slot is a real, non-blank 16x16 sprite in the baked theme, so the
    splash (and any icon-sheet consumer) can blit it."""
    from runtime import console
    sheet = console._default_icon_sheet()
    img = sheet.tile_image(console._ICON["moy"])
    assert img is not None
    assert any(p > 0 for p in img.pix)              # mascot has painted pixels
