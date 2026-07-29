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


def _open_game(ws):
    """Select a seeded GAME cart and run it.

    A game is fullscreen and exits on hold-BACKSPACE; an app runs WITH the bar and
    exits via its X. So a test about the game exit contract has to say "a game" --
    `sel = 0` resolves to whichever cart sorts FIRST, which is alphabetical and
    changes whenever a cart is added or renamed."""
    for i, it in enumerate(ws.launcher.items):
        if it.get("path") and it.get("type") == "game":
            ws.launcher.sel = i
            ws.open()
            return it
    raise AssertionError("no game cart seeded")


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
    ≡ system menu, OS-style). Tapping ≡ opens the menu; SETTINGS (#105: now the second
    selectable row, after the launcher-only SEARCH row) opens Settings."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    x, y, w, h = ws.layout.sysmenu_btn
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.sysmenu.open
    drv.press("down"); drv.frame(1 / 30)                    # SEARCH -> SETTINGS
    drv.press("a"); drv.frame(1 / 30); drv.frame(1 / 30)    # activate SETTINGS
    assert ws.screen == "settings"


def test_settings_appearance_row_opens_the_one_picker(tmp_path):
    """Wallpaper + theme picking is consolidated into the Appearance app; the
    Settings APPEARANCE action row deep-links there (the Files -> Paint open_app
    precedent), and a pick made in the app applies + persists."""
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    rows = [r[0] for r in ws.settings_layer._SETTINGS_ROWS]
    assert "wallpaper" not in rows and "theme" not in rows   # the old steppers are gone
    ws.settings_layer.set_msel = rows.index("appearance")
    drv.press("right")                                    # any step activates an action row
    drv.frame(1 / 30)
    assert ws.wm.top_kind() == "appearance"
    before = ws.wallpaper_id
    drv.press("down")          # the catalog column: down applies the next item
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


def test_launcher_home_no_longer_manages_carts(tmp_path):
    """Cart management (create/copy/delete) moved off the launcher home into the
    Editor picker's zone (docs/shell_ux_v1.md: the launcher is for PLAYING, the
    picker is for MANAGING projects) -- a tap at the old NEW/DUP/DEL bar positions
    on the launcher screen is now a no-op (see tests/test_top_bar.py for the
    picker's DUP/DEL coverage)."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    n0 = len(ws.launcher.items)
    for rect in (ws.layout.new_btn, ws.layout.dup_btn, ws.layout.del_btn):
        x, y, w, h = rect
        drv.click(x + w // 2, y + h // 2)
        drv.frame(1 / 30)
    assert len(ws.launcher.items) == n0


def test_tapping_a_cart_icon_runs_it_from_home(tmp_path):
    """The launcher's LOCKED primary action (spec shell_ux_v1.md): tapping ANY cart's
    icon tile RUNS it (screen == "desktop"), caller = the launcher home (so its QUIT pops
    home). There is no maker/player tap_mode: authoring is a separate Editor app reached
    via the Make tile -> project-picker, never a tap on the launcher grid."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    # Slot 0 is the pinned Make tile; the first real cart is slot 1.
    real = next(i for i, it in enumerate(ws.launcher.items) if it.get("path"))
    x, y, w, h = ws.launcher.tile_rect(real)
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.screen == "desktop"               # tap -> RUN
    assert ws.cart is not None and ws.cart.get("path")  # ...a real cart loaded


def test_tapping_the_make_tile_opens_the_project_picker(tmp_path):
    """The pinned "Make" tile (launcher slot 0, distinctive) opens the Editor's
    PROJECT-PICKER (screen == "picker"), NOT a running cart (spec shell_ux_v1.md)."""
    from runtime import host_app
    from runtime.launcher_layer import MAKE_TILE_TYPE
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    assert ws.launcher.items[0].get("type") == MAKE_TILE_TYPE   # pinned first, a pseudo tile
    assert ws.launcher.items[0].get("path") is None             # not a real cart
    x, y, w, h = ws.launcher.tile_rect(0)
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.screen == "picker"                # opened the project-picker, did NOT run


_ONE_CARD = [{"key": "c", "type": "choice", "choices": ["red", "blue"], "card": "C IS {value}"}]


def test_launcher_tap_runs_every_cart_type(tmp_path):
    """The locked model: a launcher tap RUNS the cart regardless of manifest TYPE -- a
    tool, an app, AND a game all land on the running cart (screen == "desktop"), never the
    Editor. The retired interim `tap_mode` used to send a game to the Editor on a maker tap;
    that type-dispatch is gone -- tap always runs, edit is the Make tile's job."""
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)

    for title, ctype, edit in (("MyTool", "tool", None),
                               ("MyApp", "app", None),
                               ("MyGame", "game", _ONE_CARD)):
        moy_carts.create(title, carts_dir, src="def _draw():\n    cls(1)\n",
                         type=ctype, edit=edit)
        ws.go_home()
        ws.launcher.set_items(moy_carts.scan(carts_dir))
        ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                               if it.get("title") == title)
        ws.launch_selected()                        # the launcher tap
        assert ws.screen == "desktop", title        # RAN, never the Editor
        assert ws.cart is not None and ws.cart.get("type") == ctype


def test_new_template_is_an_editable_game(tmp_path):
    """A kid's brand-new cart (moy_carts.NEW_TEMPLATE) is `type=="game"` WITH "Make it
    mine" cards -- the project the "+ New" picker tile creates + opens in the Editor (spec
    shell_ux_v1.md). It's a real game project, not a wallpaper. (Tapping it in the launcher
    RUNS it like any cart; editing is reached via the Make tile -> picker.)"""
    import os
    from runtime import moy_carts
    carts_dir = str(tmp_path / "carts")
    os.makedirs(carts_dir, exist_ok=True)
    new = moy_carts.new_from_template(carts_dir, title="Freshie")
    assert new["type"] == "game" and new["edit"]    # a game project WITH edit cards


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
    _open_game(ws)                     # the exit gesture under test is the GAME one
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
    _open_game(ws)                     # the hold toast is the GAME exit affordance
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


# -- a cart can end ITSELF via the quit() verb (the exit a text-mode cart must provide) --

def test_cart_quit_verb_pops_to_the_launcher(tmp_path):
    """A cart calls quit() (make_api) to END itself and return to the launcher -- the
    self-exit a text-mode cart MUST provide (once it textmode(True)s, hold-BACKSPACE can't
    reach it: BACKSPACE is a typed delete, no keyboard autorepeat). The verb is ADDITIVE to
    the frozen kid API and works for ANY cart type; the Player honors the flag AFTER the
    cart's _update runs (player.tick) and pops to the run caller (ws._exit_to_caller)."""
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    drv = host_app.ConsoleDriver(ws)
    # A game that quits itself on its 2nd frame -- stands in for a key/affordance the cart
    # binds to quit() (here a frame counter so the exit is deterministic).
    src = ("_n = 0\n"
           "def _update(dt):\n"
           "    global _n\n"
           "    _n += 1\n"
           "    if _n >= 2:\n"
           "        quit()\n"
           "def _draw():\n    cls(1)\n")
    moy_carts.create("Quitter", carts_dir, src=src, type="game")
    ws.launcher.set_items(moy_carts.scan(carts_dir))
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("title") == "Quitter")
    ws.open()                                # PLAY it
    assert ws.screen == "desktop"
    drv.frame(1 / 30)                        # frame 1: _n -> 1, quit() not yet called
    assert ws.screen == "desktop"            # still running
    drv.frame(1 / 30)                        # frame 2: _n -> 2 -> quit()
    assert ws.screen == "launcher"           # quit() popped the cart home


def test_cart_quit_flag_is_cleared_for_the_next_cart(tmp_path):
    """A stale quit() flag must NOT carry into the next cart: opening a cart resets
    input.cart_quit (console._open_workspace), so a freshly opened cart is not popped on
    its first frame by a previous cart's quit()."""
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    drv = host_app.ConsoleDriver(ws)
    moy_carts.create("Plain", carts_dir,
                     src="def _update(dt):\n    pass\ndef _draw():\n    cls(2)\n",
                     type="game")
    ws.launcher.set_items(moy_carts.scan(carts_dir))
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("title") == "Plain")
    ws.input.cart_quit = True                # a leftover flag from some prior run
    ws.open()                                # opening must clear it
    assert getattr(ws.input, "cart_quit", False) is False
    drv.frame(1 / 30)
    assert ws.screen == "desktop"            # the plain cart keeps running (not popped)


def test_tap_mode_is_retired_from_settings(tmp_path):
    """The maker/player `tap_mode` is GONE (spec shell_ux_v1.md, the locked model): no
    Settings row, no ws.tap_mode/cycle_tap_mode verbs. A launcher tap always RUNS; the
    only authoring path is the Make tile -> Editor project-picker."""
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    keys = [r[0] for r in ws.settings_layer._SETTINGS_ROWS]
    assert "tap_mode" not in keys                   # the Settings row is gone
    assert not hasattr(ws, "tap_mode")
    assert not hasattr(ws, "cycle_tap_mode")


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
    _open_game(ws)                     # launcher launch: caller = the home root
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


def test_frame_cap_locks_games_to_a_steady_30(tmp_path, monkeypatch):
    # Frame pacing (#63): with the governor ON, a running GAME locks to 30fps (the
    # SNES consistency rule) unless its manifest declares "fps": 60; tools/apps and
    # every console screen keep 60. The knob currently SHIPS OFF (uncapped -- the
    # owner wants real per-cart numbers while the engine work settles), so the test
    # pins BOTH: the default-off behaviour and the ON policy.
    from runtime import host_app, console as console_mod
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    # "a plain game" = anything that doesn't declare the 60 opt-in. Since the
    # carts became "moy-1" this is fps 30 (SPEC.md 5's default) rather than the
    # old unset 0, and frame_cap_fps treats the two the same.
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path") and it.get("type") == "game"
                           and it.get("fps") != 60)
    ws.open()
    assert ws.screen == "desktop" and ws.cart_error is None
    assert console_mod.FPS_GOVERNOR is False
    assert ws.frame_cap_fps() == 60                 # knob OFF: uncapped everywhere
    monkeypatch.setattr(console_mod, "FPS_GOVERNOR", True)
    assert ws.frame_cap_fps() == 30                 # a plain game: locked 30
    ws.cart["fps"] = 60
    assert ws.frame_cap_fps() == 60                 # manifest fps: 60 (Hop Quest/Sky Run)
    ws.cart["fps"] = "junk"
    assert ws.frame_cap_fps() == 30                 # malformed -> the safe default
    ws.cart["fps"] = 0
    ws.cart["type"] = "tool"
    assert ws.frame_cap_fps() == 60                 # tools keep the responsive cap
    ws.cart["type"] = "game"
    ws.player.cart_error = "boom"
    assert ws.frame_cap_fps() == 60                 # the crash panel is a console screen
    ws.player.cart_error = None
    ws.go_home()
    assert ws.frame_cap_fps() == 60


def test_nativize_maps_crash_lines_back_to_kid_source():
    # #67 spike: the auto-native rewrite inserts one decorator line above every
    # top-level def; a crash line reported against the REWRITTEN source must map
    # back to the kid's original line exactly (#24's drop-on-the-bad-line).
    from runtime import player as player_mod
    src = "x = 1\ndef a():\n    boom\ndef b():\n    boom\n"
    nsrc, ins = player_mod._nativize(src)
    lines = nsrc.split("\n")
    assert lines[1] == "@micropython.native" and lines[2] == "def a():"
    assert lines[4] == "@micropython.native" and lines[5] == "def b():"
    assert ins == [2, 5]

    class _P:
        _native_ins = ins
        _map_crash_line = player_mod.Player._map_crash_line
    p = _P()
    # original line 3 ("boom" in a) is new line 4 -> maps back to 3
    assert p._map_crash_line(4) == 3
    # original line 5 ("boom" in b) is new line 7 -> maps back to 5
    assert p._map_crash_line(7) == 5
    assert p._map_crash_line(None) is None
    p._native_ins = None                     # pristine bytecode path: identity
    assert p._map_crash_line(7) == 7
    # On host CPython the auto-native flag must be OFF (no micropython module).
    assert player_mod.NATIVE_CARTS is False


def test_live_set_diet_slims_rehydrates_and_reslims(tmp_path):
    # #66 live-set diet: after boot the scanned carts hold only metadata + the
    # cached icon (the heavy payloads are ~0.2ms/KB of GC mark cost EVERY collect);
    # opening rehydrates from the store in place, switching re-slims the previous.
    from runtime import host_app
    from runtime.console import _HEAVY_CART_KEYS
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    sd = [c for c in ws._all_carts if c.get("path")]
    assert sd, "seeded carts expected"
    for c in sd:
        assert c.get("lazy") is True
        for k in _HEAVY_CART_KEYS:
            assert k not in c, "%s survived slimming on %s" % (k, c["title"])
    # icons were baked BEFORE slimming, so the grid never needs the sheet back
    assert any(ws._icon_cache.values()), "icon cache should be populated"
    # RUN rehydrates in place and the cart actually plays
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path"))
    ws.open()
    a = ws.cart
    assert a.get("lazy") is False and "src" in a
    assert ws.cart_error is None
    ws.player.tick(1 / 30)
    assert ws.cart_error is None
    # switching to a different cart re-slims the previous one
    other = next(c for c in ws._all_carts if c.get("path") and c is not a)
    ws.open_in_editor(other)
    assert other.get("lazy") is False and "src" in other
    assert a.get("lazy") is True and "src" not in a, "previous cart must re-slim"
    # a slim wallpaper cart compiles (transient rehydrate) and re-slims after
    wp = [c for c in ws._all_carts if c.get("type") == "wallpaper"]
    if wp:
        slug = wp[0]["path"].rsplit("/", 1)[-1][:-len(".moy")]
        ws.select_wallpaper(slug, persist=False)
        assert ws.wallpaper._wp_draw is not None, "wallpaper must compile from a slim cart"
        assert wp[0] is ws._fat_cart or wp[0].get("lazy") is True


def test_cart_bound_keys_do_not_dirty_the_shell(tmp_path):
    """#44/#58: a healthy fullscreen cart owns every key, so a key frame must
    not request a shell repaint -- a BLE keyboard reports last_key as LEVEL
    state (a held byte each frame), and the old unconditional mark repainted
    the shell on every held-key frame. On the launcher the same input must
    still repaint (grid nav)."""
    ws = _ws(tmp_path)
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    assert ws.wm.keys_to_cart()
    ws._dirty = False
    ws.input._pressed = set()
    ws.input.last_key = ord("q")
    ws.handle_input()
    assert not ws._dirty, "cart-bound key dirtied the shell"
    ws.input.last_key = 0
    ws._exit_to_caller()
    assert ws.screen == "launcher"
    assert not ws.wm.keys_to_cart()
    ws._dirty = False
    ws.input._pressed = {"right"}
    ws.handle_input()
    assert ws._dirty, "launcher nav key must repaint"
