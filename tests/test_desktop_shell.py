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
    ws.cart_error = "boom"         # Stage 5 retired pause: the in-cart bar is CRASH chrome
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
    dock 'run' slot was removed from the launcher with the dock, #46.) In the DEFAULT
    maker mode (spec Section 4) the tap opens the cart in the Editor on Config
    (screen == "menu"); a player-mode device would play it instead (test below)."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    x, y, w, h = ws.layout.tile_rect(0, ws.launcher.page)
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.screen == "menu"                  # maker default: tap -> the Editor
    assert ws.cart is not None                  # ...with the tapped cart loaded


def test_tapping_a_cart_icon_in_player_mode_plays_it(tmp_path):
    """Player-mode devices (spec Section 4): a tap PLAYS the cart immediately, caller =
    the launcher home (so its QUIT pops home). The tap-mode setting is the only
    difference from the maker default above."""
    from runtime import host_app
    ws = _ws(tmp_path)
    ws.system["tap_mode"] = "player"
    drv = host_app.ConsoleDriver(ws)
    x, y, w, h = ws.layout.tile_rect(0, ws.launcher.page)
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.screen == "desktop"               # player mode: tap -> plays


_ONE_CARD = [{"key": "c", "type": "choice", "choices": ["red", "blue"], "card": "C IS {value}"}]


def test_tool_cart_always_launches_on_tap_even_in_maker_mode(tmp_path):
    """Part 2 (owner device feedback): a non-game cart (e.g. the `type=="tool"` wifi tool)
    ALWAYS LAUNCHES on a tap, even in the DEFAULT maker mode where a GAME opens the Editor.
    You RUN the wifi tool, you don't edit it. So launch_selected() on a tool lands on the
    running cart (screen == "desktop"), never the Editor; a game in the same mode still opens
    the Editor (the contrast). Dispatch keys on manifest TYPE -- only `type=="game"` edits."""
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    assert ws.tap_mode() == "maker"                 # the mode where an EDITABLE cart EDITs

    moy_carts.create("MyTool", carts_dir, src="def _draw():\n    cls(1)\n", type="tool")
    ws.launcher.set_items(moy_carts.scan(carts_dir))
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("title") == "MyTool")
    assert not ws.launcher.selected().get("edit")   # no edit schema -> nothing to customize
    ws.launch_selected()                            # the tap dispatch
    assert ws.screen == "desktop"                   # LAUNCHED (ran), NOT the Editor
    assert ws.cart is not None and ws.cart.get("type") == "tool"

    # An EDITABLE game in the SAME maker mode still opens the Editor (the contrast).
    moy_carts.create("MyGame", carts_dir, src="def _draw():\n    cls(2)\n",
                     type="game", edit=_ONE_CARD)
    ws.go_home()
    ws.launcher.set_items(moy_carts.scan(carts_dir))
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("title") == "MyGame")
    ws.launch_selected()
    assert ws.screen == "menu"                      # maker default for an EDITABLE cart


def test_new_cart_opens_editor_on_maker_tap(tmp_path):
    """The HIGH anti-trap regression guard: a kid's brand-new cart (moy_carts.NEW_TEMPLATE)
    is `type=="game"` -- the maker project you tweak. Dispatch keys on TYPE, so a maker-mode
    tap opens the Editor (Config) instead of running it bar-less with no way back. (The
    template used to be a `wallpaper`, which -- once dispatch keyed on type -- would have run
    with no tap path to its cards; making the NEW default a game closes that trap.)"""
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    assert ws.tap_mode() == "maker"
    new = moy_carts.new_from_template(carts_dir, title="Freshie")
    assert new["type"] == "game" and new["edit"]    # a game project WITH edit cards
    ws.launcher.set_items(moy_carts.scan(carts_dir))
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("title") == "Freshie")
    ws.launch_selected()
    assert ws.screen == "menu"                      # opened the Editor (Config), did NOT run
    assert ws.menu_view == "cards"                  # ...landing on the Make-it-mine cards


def test_game_without_edit_schema_opens_editor_on_maker_tap(tmp_path):
    """The Fix 1 regression guard: a `type=="game"` cart with NO "Make it mine" cards
    (edit schema empty/absent -- e.g. system_carts/tap_game.moy, `edit: []`) used to
    LAUNCH on a maker tap because the old dispatch keyed on the edit-schema. Dispatch now
    keys on manifest TYPE: a non-tool cart follows tap_mode, so a maker tap opens the
    Editor even with no cards -- editing is for everything that isn't a pure tool."""
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    assert ws.tap_mode() == "maker"
    moy_carts.create("Cardless", carts_dir, src="def _draw():\n    cls(3)\n", type="game")
    ws.launcher.set_items(moy_carts.scan(carts_dir))
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("title") == "Cardless")
    assert not ws.launcher.selected().get("edit")   # no cards -> old logic would LAUNCH
    ws.launch_selected()
    assert ws.screen == "menu"                      # ...now opens the Editor (Fix 1)


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


def test_in_cart_bar_shows_only_on_crash_never_while_playing(tmp_path):
    """Stage 5 retired the #71 pause frame: while a cart PLAYS the game owns the full
    canvas with NO chrome at all -- the in-cart tool bar (EDIT/PAINT/MAP/HOME) appears
    ONLY on a CRASH, so the fix-it tools stay reachable. (Exit while playing a game is the
    hold-BACKSPACE gesture, not a bar tap.) The launcher's removal of the dead dock does
    not touch this in-cart chrome."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    seen = _spy_draw(ws)
    drv.frame(1 / 30)
    assert seen["incart"] == 0        # playing: NO bar over the game
    ws.cart_error = "boom"            # a crash brings up the in-cart tool buttons
    ws._dirty = True
    drv.frame(1 / 30)
    assert seen["incart"] >= 1        # crashed: the in-cart tool buttons ARE drawn
    # The 6-slot bottom dock belongs to home/settings, not a running cart.
    assert seen["dock"] == 0


def test_single_backspace_tap_reaches_the_cart_never_exits(tmp_path):
    """Stage 5 exit model (spec Section 9): the #71 pause frame is GONE -- a running cart
    owns the full 320x240 and BACKSPACE ("home") is a plain key/button the cart reads. A
    single quick tap keeps playing (it is not the hold-to-exit gesture); the cart keeps
    ticking (no freeze), proving there is no pause state left."""
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
    drv.press("home")                  # ONE quick BACKSPACE tap...
    drv.frame(1 / 30)
    assert ws.screen == "desktop"      # ...does NOT exit (no pause, no quit)
    drv.frame(1 / 30)
    assert calls[0] > 1                # ...and the cart keeps ticking (never froze)


def test_hold_backspace_exits_to_caller(tmp_path, monkeypatch):
    """Stage 5: a sustained BACKSPACE hold (~700ms) pops the running cart back to its
    caller -- the launcher root here. Wired to input.held("home") + a threshold on the
    raw-matrix held-key stream; the host maps a held BACKSPACE to a held "home". Drive
    the module clock (monkeypatch auto-restores) so the 700ms elapses deterministically."""
    from runtime import host_app
    from runtime import player as P
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    clock = [10_000]
    monkeypatch.setattr(P, "_ticks_ms", lambda: clock[0])   # deterministic hold timer
    drv.hold("home", True)             # begin holding BACKSPACE ...
    drv.frame(1 / 30)
    assert ws.screen == "desktop"      # ... not yet (no time elapsed)
    clock[0] += P._HOLD_EXIT_MS - 1
    drv.frame(1 / 30)
    assert ws.screen == "desktop"      # ... still short of the threshold
    clock[0] += 2                      # now past _HOLD_EXIT_MS since the first held frame
    drv.frame(1 / 30)
    assert ws.screen == "launcher"     # hold complete -> popped to the caller
    drv.hold("home", False)


def test_triple_backspace_tap_does_not_exit_reaches_the_cart(tmp_path):
    """The triple-tap BACKSPACE exit alias was DROPPED (owner tested on device): quick
    BACKSPACE taps are plain 'home' key edges the running GAME reads -- even three of them
    in a row do NOT exit. Hold-BACKSPACE (test above) is the only game exit now; tools/apps
    exit via their bar X (Part 4). Each drv.press is a one-frame 'home' edge the cart sees;
    the cart keeps ticking and stays on the desktop."""
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
    for _ in range(3):                 # three quick BACKSPACE taps in a row...
        drv.press("home"); drv.frame(1 / 30); drv.frame(1 / 30)
    assert ws.screen == "desktop"      # ...does NOT exit (triple-tap gesture is gone)
    assert calls[0] > 0                # ...and the cart kept ticking (never froze/popped)


def test_hold_progress_toast_is_transient(tmp_path, monkeypatch):
    """The hold-to-exit affordance is TRANSIENT (spec Section 12): _draw_hold_progress
    fires ONLY on frames where BACKSPACE is being held, NEVER on a plain play frame --
    so no chrome is re-added to the sacred play path. Spy the toast draw across a
    playing frame, held frames, and the released frame."""
    from runtime import host_app
    from runtime import player as P
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    calls = [0]
    orig = ws.player._draw_hold_progress

    def counting():
        calls[0] += 1
        return orig()
    ws.player._draw_hold_progress = counting
    clock = [1000]
    monkeypatch.setattr(P, "_ticks_ms", lambda: clock[0])
    drv.frame(1 / 30)                      # playing, no hold
    assert calls[0] == 0                   # NO toast on a plain play frame
    drv.hold("home", True)
    drv.frame(1 / 30)                      # holding -> toast drawn
    clock[0] += 100                        # still short of _HOLD_EXIT_MS
    drv.frame(1 / 30)                      # still holding -> toast again
    assert calls[0] >= 2
    held_calls = calls[0]
    assert ws.screen == "desktop"          # short hold: has NOT exited yet
    drv.hold("home", False)
    drv.frame(1 / 30)                      # released -> toast gone
    drv.frame(1 / 30)
    assert calls[0] == held_calls          # no more toast draws once the hold ends


# -- Part 4: a TOOL/APP runs WITH a minimal bar so it's EXITABLE ------------

def _make_tool_ws(tmp_path):
    """A workstation with a launched TOOL cart (screen "desktop", the minimal bar up)."""
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    drv = host_app.ConsoleDriver(ws)
    moy_carts.create("MyTool", carts_dir, src="def _draw():\n    cls(1)\n", type="tool")
    ws.launcher.set_items(moy_carts.scan(carts_dir))
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("title") == "MyTool")
    ws.launch_selected()                             # a tool always LAUNCHES (Part 2)
    assert ws.screen == "desktop"
    return ws, drv


def test_tool_cart_runs_with_a_bar_x_exits_and_backspace_is_not_stolen(tmp_path, monkeypatch):
    """Part 4 (the one architectural call to confirm on device): a launched TOOL/APP runs
    WITH a minimal bar so it is EXITABLE -- the right-zone context-X exits it, while
    BACKSPACE stays a FREE key (never a hold-to-exit) so a text tool's password field keeps
    its DELETE. (A GAME by contrast stays fullscreen-bar-hidden and exits via
    hold-BACKSPACE -- the bar-visibility-by-type rule keys on the cart's manifest type.)"""
    from runtime import player as P
    from runtime import bar_layer as BL
    ws, drv = _make_tool_ws(tmp_path)

    # 1) The minimal bar IS drawn while the tool plays (a game draws NO bar). Spy the
    #    "tool" status strip across a play frame.
    where_seen = []
    orig = ws.bar_layer._draw_status_strip

    def spy(where):
        where_seen.append(where)
        return orig(where)
    ws.bar_layer._draw_status_strip = spy
    drv.frame(1 / 30)
    assert "tool" in where_seen                       # the tool bar shows during play

    # 2) BACKSPACE is NOT stolen as an exit: a sustained hold-"home" does NOT pop the tool
    #    (for a game this would exit; a tool keeps BACKSPACE free for its text fields).
    clock = [10_000]
    monkeypatch.setattr(P, "_ticks_ms", lambda: clock[0])
    drv.hold("home", True)
    drv.frame(1 / 30)
    clock[0] += P._HOLD_EXIT_MS + 100                  # well past a game's hold threshold
    drv.frame(1 / 30)
    drv.hold("home", False)
    assert ws.screen == "desktop"                      # the tool did NOT exit on BACKSPACE

    # 3) The context-X EXITS the tool (tap the right-zone X on the 320x240 game canvas).
    x, y, w, h = BL._ZONE_CONTEXT_X
    drv.touch(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "launcher"                     # the X popped the tool back home


def test_tool_bar_does_not_lend_a_zone_during_play(tmp_path):
    """Part 4 refines the play-frame guardrail from 'no bar during play' to 'no lent ZONE
    during play': a running TOOL now shows a bar, but it still MUST NOT dispatch the editor
    zone (owner.draw_zone) -- the tool bar is title + status + X only. Spy every zone
    owner's draw_zone and drive the tool through several frames; none may fire."""
    ws, drv = _make_tool_ws(tmp_path)
    calls = []
    for owner in (ws.launcher_layer, ws.settings_layer, ws.editor_app):
        orig = owner.draw_zone

        def spy(cv, rect, _orig=orig, _owner=owner):
            calls.append(_owner)
            return _orig(cv, rect)
        owner.draw_zone = spy
    for _ in range(5):
        ws._dirty = True
        drv.frame(1 / 30)
    assert calls == [], "the tool bar must not lend an editor zone during play: %r" % calls


# -- Fix 4: a text-reading GAME is always exitable (Letter Blitz was trapped) -----

def _text_game_ws(tmp_path, title="Typer"):
    """A workstation running a minimal GAME that opts into text input (textmode(True)),
    like the typing game Letter Blitz."""
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    drv = host_app.ConsoleDriver(ws)
    src = ("def _init():\n    textmode(True)\n\n"
           "def _update(dt):\n    pass\n\ndef _draw():\n    cls(1)\n")
    moy_carts.create(title, carts_dir, src=src, type="game")
    ws.launcher.set_items(moy_carts.scan(carts_dir))
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("title") == title)
    ws.open()                                          # PLAY it
    assert ws.screen == "desktop"
    assert ws.input.text_mode is True                  # the cart asked for text input
    return ws, drv


def test_text_reading_game_is_exitable_via_bar_and_keeps_backspace(tmp_path, monkeypatch):
    """Fix 4: a typing GAME (textmode(True), e.g. Letter Blitz) must ALWAYS be exitable. In
    text mode BACKSPACE arrives as a typed 0x08 the cart reads (never the "home" button) and
    the T-Deck keyboard has no autorepeat, so hold-BACKSPACE can't accumulate the ~700ms hold
    -- the game was TRAPPED. Fix: a text-reading game runs WITH the same minimal bar
    (context-X) as a tool -- a device-robust, always-tappable exit -- and its hold gesture is
    suppressed so BACKSPACE stays FREE for the cart's own text handling."""
    from runtime import player as P
    from runtime import bar_layer as BL
    ws, drv = _text_game_ws(tmp_path)

    # 1) The minimal exit bar IS shown while the text game plays (a bare game shows none).
    assert ws._running_cart_shows_bar() is True
    where_seen = []
    orig = ws.bar_layer._draw_status_strip

    def spy(where):
        where_seen.append(where)
        return orig(where)
    ws.bar_layer._draw_status_strip = spy
    drv.frame(1 / 30)
    assert "tool" in where_seen                         # the minimal (context-X) bar draws

    # 2) BACKSPACE is NOT stolen as a hold-to-exit: a sustained hold-"home" does NOT pop the
    #    game (backspace must stay free for the cart's typing).
    clock = [10_000]
    monkeypatch.setattr(P, "_ticks_ms", lambda: clock[0])
    drv.hold("home", True)
    drv.frame(1 / 30)
    clock[0] += P._HOLD_EXIT_MS + 100                   # well past a bare game's hold threshold
    drv.frame(1 / 30)
    drv.hold("home", False)
    assert ws.screen == "desktop"                       # did NOT exit on hold-BACKSPACE

    # 3) The context-X EXITS the game (a device-robust touch exit that never depends on the
    #    keyboard), so the typing game can always be left.
    x, y, w, h = BL._ZONE_CONTEXT_X
    drv.touch(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "launcher"                      # the X popped the game home


def test_letter_blitz_seed_cart_shows_the_exit_bar(tmp_path):
    """The exact reported cart: system_carts/letter_blitz.moy calls textmode(True) in _init,
    so it runs as a text-reading game -- it MUST show the minimal exit bar (never trapped)."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.launcher.sel = next(i for i, c in enumerate(ws.launcher.items)
                           if c["title"] == "Letter Blitz")
    ws.open()
    assert ws.screen == "desktop"
    assert ws.input.text_mode is True                   # the typing game asked for text input
    assert ws._running_cart_shows_bar() is True         # ...so it shows the always-tappable X


def test_tap_mode_setting_toggles_and_persists(tmp_path):
    """Section 4 tap-mode: system.json's tap_mode defaults to "maker" (a launcher tap
    opens the Editor); Settings -> TAP OPENS steps it MAKER <-> PLAYER and persists it
    across a reload. Drives both the direct setter and the Settings-row dispatch."""
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    assert ws.tap_mode() == "maker"                 # default (spec Section 4)
    keys = [r[0] for r in ws.settings_layer._SETTINGS_ROWS]
    assert "tap_mode" in keys                       # the Settings row exists
    # Direct toggle + persistence to system.json (survives a fresh load).
    ws.cycle_tap_mode(1)
    assert ws.tap_mode() == "player"
    assert moy_carts.load_system(carts_dir).get("tap_mode") == "player"
    # The Settings-row dispatch (select TAP OPENS, step it) flips it back.
    ws.settings_layer.set_msel = keys.index("tap_mode")
    ws.settings_layer.settings_adjust(1)
    assert ws.tap_mode() == "maker"


def test_play_from_editor_returns_to_the_editor_tab_on_exit(tmp_path, monkeypatch):
    """Stage 3b + Stage 5: PLAY runs the cart recording the EDITOR as the run caller, so
    the cart's EXIT gesture (hold-BACKSPACE here) returns to the Editor on the tab it left
    (spec Section 2/Section 6's launch-and-return) -- NOT the launcher home. The launcher
    stays the caller only when IT launched the cart (test above), while the Editor's PLAY
    makes the Editor the second caller, proving the Player is caller-agnostic and the exit
    gesture pops to whoever launched it."""
    from runtime import host_app
    from runtime import player as P
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()                          # launcher launch: caller = the home root
    assert ws.screen == "desktop"
    ws._open_menu()                    # tap EDIT -> into the Editor (Config/code)
    assert ws.screen == "menu"
    tab = ws.menu_view
    ws._leave_menu()                   # PLAY: run the cart, caller = the Editor now
    assert ws.screen == "desktop"      # ...still runs fullscreen (transition preserved)
    clock = [10_000]
    monkeypatch.setattr(P, "_ticks_ms", lambda: clock[0])   # deterministic hold timer
    drv.hold("home", True)             # hold-BACKSPACE = the game exit gesture
    drv.frame(1 / 30)                  # first held frame (timer starts)
    clock[0] += P._HOLD_EXIT_MS + 1    # elapse past the hold threshold
    drv.frame(1 / 30)
    drv.hold("home", False)
    assert ws.screen == "menu"         # exit returned to the EDITOR...
    assert ws.menu_view == tab         # ...on the very tab it left (not the launcher)


def test_settings_opened_during_play_preserves_the_run_caller(tmp_path):
    """Stage-3 review fix, re-checked under the Stage-5 exit model: opening Settings over
    a cart the EDITOR launched (via PLAY) must NOT clobber _run_caller. _exit_settings
    now resumes the cart with a bare screen flip (not run(), which would reset the caller
    to the launcher), so a later exit still lands back on the Editor tab, not home."""
    from runtime import host_app
    ws = _ws(tmp_path)
    ws.launcher.sel = 0
    ws.open()                          # launcher launch
    ws._open_menu()                    # into the Editor
    ws._leave_menu()                   # PLAY: caller = the Editor
    assert ws._run_caller is ws.editor_app
    ws.open_settings()                 # Settings over the running cart (crash-bar gear)
    assert ws.screen == "settings"
    ws._exit_settings()                # Back resumes the cart...
    assert ws.screen == "desktop"
    assert ws._run_caller is ws.editor_app   # ...and the caller is PRESERVED (the fix)
    ws._exit_to_caller()               # so the cart's exit still returns to the Editor
    assert ws.screen == "menu"


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
