"""Tests for the Picotron-style windowed WM (runtime/wm_windowed.py, #73 -- the
big-screen / P4 "One" presentation of the 2026-07 shell).

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver / Workstation) with `windowed=True`, so these pin the windowed
presentation's contracts:

  * install/degradation -- the windowed WM only mounts on a distinct big system
    canvas; at 320x240 the fullscreen-stack WM stays (byte-identical tier);
  * the back-stack <-> window mapping -- every pushed process gets a window,
    pops drop it, and the Library gives way to the wallpaper desktop while any
    window is open;
  * launch-and-return presented spatially -- PLAY opens a playtest window above
    the still-visible editor window; exit pops back to the editor;
  * input routing -- keyboard to the focused window, taps in WINDOW-LOCAL
    coords under the window's own layout context, drags move windows, clicking
    a lower window raises it by popping everything above (the same pop);
  * the game viewport == the player window's content rect (ws._game_xy).
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


from ws_helpers import build_desktop_ws, select_on_home


def _ws(tmp_path, **kw):
    ws = build_desktop_ws(tmp_path, **kw)
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path"))
    return ws


def _drv(ws):
    from runtime import host_app
    return host_app.ConsoleDriver(ws)


def _open_editor(ws):
    """The desk's PROJECTS, then its first project: the Editor in a window of
    its own above the home window."""
    ws.open_projects()
    g = ws.projects_grid
    g.sel = next(i for i, it in enumerate(g.items) if it.get("path"))
    ws.launch_selected(g)


def _quiesce(ws):
    ws.pointer.visible = False
    ws._toast_until = 0


# ---------------------------------------------------------------------------
# The game composite belongs to its WINDOW
# ---------------------------------------------------------------------------

def test_a_game_window_does_not_black_out_the_desk(tmp_path):
    """`blit_game` means two different things and this tier wants the quiet one.

    The shared `DeviceCanvas.blit_game` is the T-Deck's, where the canvas IS the
    glass: it paints four black LETTERBOX bands over everything outside the game
    rect, and `FullscreenStackWM.composite_game` depends on that (it probes for
    the verb and returns, so its own bezel fill never runs). Here the same call
    places a cart inside a WINDOW, and those bands cover the desk, the icon
    column, the OS bar and every other window.

    It is not hypothetical: the host inherited that method the day its canvas
    became the boards' (`runtime/canvas.py` deleted, 2026-08-15) and the desktop
    went black behind the first game window opened on it. The P4 never felt it
    because `P4SystemCanvas.blit_game` overrides the bands away and that board
    only runs this WM; the host runs both tiers, so `build_workstation` tells
    the canvas which meaning it serves.
    """
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    ws.open_desk()
    drv.frame(1 / 30)
    ws.open()                                    # a cart, in a player WINDOW
    for _ in range(3):
        drv.frame(1 / 30)
    assert ws.cart_error is None, ws.cart_error
    win = ws.wm._wins["desktop"]
    assert win is not None and win.w < ws.sys_canvas.w

    sc = ws.sys_canvas
    # Sample the desk WELL clear of the window: bands span the full width and
    # the full height, so any of these catches them.
    outside = [(2, sc.h - 3), (sc.w - 3, sc.h - 3), (2, win.y + win.h // 2),
               (sc.w - 3, win.y + win.h // 2)]
    for x, y in outside:
        assert ws.wm._win_at(x, y) is None, (x, y)   # really outside every window
    assert any(sc.pix(x, y) != 0 for x, y in outside), \
        "the game composite letterboxed the whole desktop black"


def test_the_fullscreen_tier_still_gets_its_letterbox(tmp_path):
    """The other half of the same contract, so fixing one does not lose the
    other: on the fullscreen tier `composite_game` returns straight after the
    native call, so `blit_game` is the ONLY thing that paints the bezel."""
    from runtime import wm as WM
    ws = _ws(tmp_path, sys_size=(960, 600), windowed=False)
    drv = _drv(ws)
    _quiesce(ws)
    ws.open()
    for _ in range(3):
        drv.frame(1 / 30)
    assert ws.cart_error is None, ws.cart_error
    ox, oy, _scale = ws.wm.viewport()
    assert ox > 0 and oy > 0                     # there IS a letterbox here
    # STAIN the bezel first. `_VIEWPORT_BEZEL` is 0 and a fresh RGB565 buffer
    # reads back as index 0 everywhere, so the bare assertion below also passes
    # on a corner nothing ever drew -- i.e. it passed with the bands turned off.
    # The sentinel is what makes this a measurement of the fill.
    ws.sys_canvas.cls(8)
    drv.frame(1 / 30)
    assert ws.sys_canvas.pix(0, 0) == WM._VIEWPORT_BEZEL


def _web_canvas_module():
    """`firmware/web_runner/web_canvas.py`, imported on CPython.

    It stages `device_canvas` from the T-Deck tree exactly as the wasm build
    does, so `host_canvas.install()` (which registers `moy_gfx`/`framebuf` and
    puts that tree on the path) is all it needs -- the same trick that lets the
    host run the boards' raster. Imported on demand, and the runner's directory
    is dropped off sys.path again afterwards, so nothing else in the suite can
    accidentally resolve a module out of it.
    """
    import importlib
    from runtime import host_canvas
    host_canvas.install()
    runner = str(ROOT / "firmware" / "web_runner")
    sys.path.append(runner)
    try:
        return importlib.import_module("web_canvas")
    finally:
        if runner in sys.path:
            sys.path.remove(runner)


def test_the_wasm_head_shares_the_same_two_meanings(tmp_path):
    """The browser's half, on the REAL `WebSystemCanvas`.

    The wasm head presents both tiers out of one binary (handheld 320x240 and
    the windowed desk), so it needs both meanings of `blit_game` -- and it
    shipped only one: it inherited `DeviceCanvas.blit_game` unchanged and its
    desk went black behind every game window. Screenshotted through
    `pageshot.mjs` before the fix.

    Both halves are asserted here on one canvas, because it is the SAME method
    now: the default letterboxes (the T-Deck's meaning, which
    `FullscreenStackWM.composite_game` depends on) and clearing the flag leaves
    everything outside the game rect untouched.
    """
    web_canvas = _web_canvas_module()
    from device_canvas import DeviceCanvas

    # The default is the letterboxing meaning: a canvas nobody told otherwise
    # IS the glass. (The T-Deck's whole tier rests on this line.)
    assert DeviceCanvas.letterbox_composite is True
    assert web_canvas.WebSystemCanvas.letterbox_composite is True

    sc = web_canvas.make_canvas(200, 120)
    gc = web_canvas.WebSystemCanvas(web_canvas.WebCompositor(40, 30))
    gc.cls(8)

    sc.cls(12)                                   # "the desk", in one colour
    sc.blit_game(gc, 20, 15, 2)
    assert sc.pix(25, 20) == 8                   # the game landed
    assert sc.pix(0, 0) == 0                     # ...and so did the bezel

    sc.cls(12)
    sc.letterbox_composite = False               # what web_boot does for the desk
    sc.blit_game(gc, 20, 15, 2)
    assert sc.pix(25, 20) == 8                   # the same composite...
    assert sc.pix(0, 0) == 12, \
        "the game composite letterboxed the whole desktop black"
    assert sc.pix(199, 119) == 12 and sc.pix(199, 0) == 12


# Every place a tier is chosen, and what it owes the flag. A file that builds a
# WindowedWM either clears `letterbox_composite` in the same branch or carries a
# reason it need not -- and the discovery test below fails on a fourth tier, so
# the next presentation cannot inherit the black desk by saying nothing.
_CLEARS = True
WINDOWED_INSTALLERS = {
    "runtime/host_app.py": _CLEARS,
    "firmware/web_runner/web_boot.py": _CLEARS,
    # ONE row for both P4 boards: they take one P4 tier body, which hands the
    # WM to the shared spine; the walk reads the canonical file, never the
    # copies their builds stage.
    "device/p4_desktop.py":
        "P4SystemCanvas overrides blit_game outright (its composite is the "
        "hardware PPA) and paints no bands at all, so the shared flag never "
        "reaches a fill on either P4 board -- and they only ever run this WM",
}


def _windowed_install_sites():
    """Every module that CONSTRUCTS a WindowedWM, found rather than listed.

    `device/` is walked because a shared body lives there (`p4_desktop.py`
    installs the WM for both P4 boards since 2026-09-09), and a board's
    `modules/` copy of ANY shared module is skipped: that copy is build output
    -- gitignored, present only on a tree that has built that board -- so
    counting it would make this ratchet's answer depend on whether someone had
    run a firmware build, which is how a guard starts failing for a reason that
    has nothing to do with what it guards.
    """
    import ast
    import warnings
    shared = {p.name for base in ("runtime", "device")
              for p in (ROOT / base).glob("*.py")}
    found = {}
    # Parsing a whole tree re-raises every SyntaxWarning in it (stale regex
    # escapes in vendored/staged sources); this walk is a search, not a lint.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for base in ("runtime", "device", "firmware"):
            for dirpath, dirnames, filenames in os.walk(ROOT / base):
                dirnames[:] = [d for d in dirnames
                               if d not in (".build", "__pycache__", "dist")]
                for name in filenames:
                    if not name.endswith(".py"):
                        continue
                    path = Path(dirpath) / name
                    if path.parent.name == "modules" and name in shared:
                        continue          # a staged copy; the canonical one is walked
                    try:
                        tree = ast.parse(path.read_text(encoding="utf-8",
                                                        errors="replace"))
                    except SyntaxError:
                        continue
                    for node in ast.walk(tree):
                        # Constructed here, or NAMED as the tier a shared
                        # spine installs (`wm=WindowedWM`): either way this
                        # is the module that decided the board runs windowed.
                        if isinstance(node, ast.Call) and any(
                                isinstance(f, ast.Name) and f.id == "WindowedWM"
                                for f in [node.func]
                                + [k.value for k in node.keywords]):
                            found[path.relative_to(ROOT).as_posix()] = (path,
                                                                        tree)
    return found


def test_every_windowed_tier_answers_for_the_letterbox_flag():
    """The ratchet. `blit_game` means two things and the INSTALL SITE is what
    picks one, so the table above must cover every install site there is."""
    found = _windowed_install_sites()
    assert set(found) == set(WINDOWED_INSTALLERS), (
        "a tier was added or moved -- say whether it clears "
        "letterbox_composite: " + repr(sorted(set(found) ^ set(WINDOWED_INSTALLERS))))
    for rel, verdict in WINDOWED_INSTALLERS.items():
        if verdict is not _CLEARS:
            assert isinstance(verdict, str) and verdict.strip(), rel
            continue
        import ast
        _path, tree = found[rel]
        # The clear must sit in the SAME branch that installs the WM: a clear
        # somewhere else in the file is a clear that a future refactor drops.
        branches = [n for n in ast.walk(tree)
                    if isinstance(n, ast.If)
                    and "WindowedWM" in ast.dump(n)]
        assert branches, rel
        inner = min(branches, key=lambda n: len(ast.dump(n)))
        cleared = [
            n for n in ast.walk(inner)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Attribute)
                    and t.attr == "letterbox_composite" for t in n.targets)
            and isinstance(n.value, ast.Constant) and n.value.value is False
        ]
        assert cleared, (
            rel + ": installs WindowedWM without clearing letterbox_composite "
            "-- the desk goes black behind the first game window")


# ---------------------------------------------------------------------------
# Install + degradation
# ---------------------------------------------------------------------------

def test_windowed_wm_installs_on_big_canvas(tmp_path):
    from runtime.wm_windowed import WindowedWM
    ws = _ws(tmp_path)
    assert isinstance(ws.wm, WindowedWM)


def test_windowed_ignored_at_320x240(tmp_path):
    """The shared-canvas 320x240 build never mounts the windowed WM -- the
    T-Deck presentation is untouched even if the flag is passed."""
    from runtime import host_app
    from runtime.wm_windowed import WindowedWM
    ws = host_app.build_workstation(str(tmp_path / "carts"), windowed=True)
    assert not isinstance(ws.wm, WindowedWM)


def test_root_only_defers_to_fullscreen_shape(tmp_path):
    """Console mode: no windows -- the visible stack is [launcher] +
    overlays, exactly the fullscreen tier's shape. Boot lands on the DESK, so
    the desk's PLAY enters console mode first."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    drv.frame(1 / 30)
    assert ws.wm.stack.kinds() == ["launcher", "desk"]      # boot = the desk
    ws.open_library()
    drv.frame(1 / 30)
    assert ws.wm.stack.kinds() == ["launcher"]
    assert ws.wm._order == []
    assert ws.wm.visible_stack()[0] is ws.launcher_layer


# ---------------------------------------------------------------------------
# Back-stack <-> windows
# ---------------------------------------------------------------------------

def test_push_opens_window_pop_closes_it(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    assert ws.wm._order == ["settings"]
    assert "settings" in ws.wm._wins
    ws.exit()                       # the settings X path
    drv.frame(1 / 30)
    assert ws.wm._order == []
    assert ws.wm._wins == {}


def test_the_editor_opens_in_its_own_window_above_projects(tmp_path):
    """The desk's PROJECTS is the home in a window; a Make pick opens the
    Editor in a window of its own above it, laid out at the window's size."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_projects()
    drv.frame(1 / 30)
    assert ws.wm._order == ["projects"]
    home = ws.wm._wins["projects"]
    g = ws.projects_grid
    g.sel = next(i for i, it in enumerate(g.items) if it.get("path"))
    ws.launch_selected(g)
    drv.frame(1 / 30)
    assert ws.wm._order == ["projects", "menu"]
    assert ws.wm._wins["projects"] is home
    assert ws.wm.stack.kinds() == ["launcher", "desk", "projects", "menu"]
    win = ws.wm._wins["menu"]
    assert win.buf is not None and win.ctx is not None
    # The window's layout context is sized to the window, not the desktop.
    assert win.ctx.layout.w == win.buf.w and win.ctx.layout.w < 1024


def test_change_leaves_library_for_desktop_backdrop(tmp_path):
    """CHANGE opens Studio over the wallpaper desktop; the Library is a launch
    surface, not an interactive backdrop behind process windows."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.change_selected()
    launcher_draws = []
    original_draw = ws.launcher.draw

    def tracked_draw(*args, **kwargs):
        launcher_draws.append(1)
        return original_draw(*args, **kwargs)

    ws.launcher.draw = tracked_draw
    _quiesce(ws)
    drv.frame(1 / 30)
    assert ws.screen == "menu"
    assert ws.wm._order == ["menu"]
    assert launcher_draws == []       # wallpaper + OS bar, no hidden Library grid
    assert ws.layout.w == 1024      # ambient (root) layout after the frame
    assert ws.launcher.layout.w == 1024

    # Even a direct background dispatch at the old selected-card coordinates
    # cannot activate the hidden Library. The desk may claim the tap for its
    # own icons (the column is label-wide since #174), but the grid underneath
    # must never run a cart or surface.
    launched = []
    original_launch = ws.launch_selected
    ws.launch_selected = lambda *a, **k: (launched.append(1),
                                          original_launch(*a, **k))
    tile = ws.launcher.tile_rect(ws.launcher.sel)
    ws.wm._backdrop_layer.handle_pointer(tile[0] + 2, tile[1] + 2, True)
    assert launched == []
    assert ws.screen != "launcher" and ws.wm.stack.has("desk")


# ---------------------------------------------------------------------------
# Launch-and-return: PLAY = a playtest window above the editor
# ---------------------------------------------------------------------------

def test_play_opens_playtest_window_and_exit_returns(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    ws.set_menu_view("code")
    drv.frame(1 / 30)
    ws._leave_menu()                # PLAY: run the cart, caller = the Editor
    drv.frame(1 / 30)
    assert ws.wm._order[-1] == "desktop"
    assert "menu" in ws.wm._order   # the editor window is still open beneath
    win = ws.wm._wins["desktop"]
    assert win.buf is None                       # the game canvas IS its content
    assert ws.wm._player_view(win)[2] >= 1       # integer viewport scale
    ws._exit_to_caller()
    drv.frame(1 / 30)
    assert ws.wm._order[-1] == "menu"
    assert ws.menu_view == "code"   # back on the tab PLAY left


def test_editor_playtest_opens_smaller_than_a_desk_run(tmp_path):
    """#178: one run() verb, two jobs. A PLAY from the Editor is a dev action --
    the playtest window opens small so the code beside it stays workable -- while
    running a cart from the desk opens it as big as the desktop fits. The entry
    point carries that meaning (run()'s caller), so nobody picks a scale."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open()                       # desk run: play-sized
    drv.frame(1 / 30)
    play = ws.wm._wins["desktop"]
    play_scale = ws.wm._player_view(play)[2]
    ws._exit_to_caller()
    drv.frame(1 / 30)

    _open_editor(ws)                # Editor PLAY: dev-sized
    drv.frame(1 / 30)
    ws._leave_menu()
    drv.frame(1 / 30)
    dev = ws.wm._wins["desktop"]
    assert dev.w < play.w and dev.h < play.h
    assert ws.wm._player_view(dev)[2] == 1        # native cart pixels
    assert play_scale > 1                         # ... vs as-big-as-fits
    # Small enough to leave the editor beneath it usable: under half the desktop,
    # and parked in the corner rather than centered on the code column.
    assert dev.w * 2 < ws.sys_canvas.w
    assert dev.x + dev.w <= ws.sys_canvas.w and dev.x > play.x
    assert dev.y + dev.h <= ws.sys_canvas.h and dev.y > play.y
    # A window the kid has since resized is never re-sized under them: the
    # intent is read ONCE, when the window is created.
    ws.wm._resize_window(dev, play.w, play.h)
    drv.frame(1 / 30)
    assert ws.wm._wins["desktop"].w == play.w


def test_dev_playtest_is_native_size_on_every_desktop(tmp_path):
    """The dev playtest is ONE size everywhere: native cart pixels, not a
    fraction of the screen. A play run still grows with the desktop."""
    sizes = []
    for i, sys_size in enumerate(((640, 480), (1024, 600), (1440, 900))):
        ws = _ws(tmp_path / str(i), sys_size=sys_size, font_scale=1)
        full = ws.wm._root_canvas
        ws.wm.set_play_intent("dev")
        sizes.append(ws.wm._win_size("desktop", full, 1))
        ws.wm.set_play_intent("play")
        play_w = ws.wm._win_size("desktop", full, 1)[0]
        assert play_w >= sizes[-1][0]
    assert sizes[0] == sizes[1] == sizes[2] == (ws.canvas.w + 2, ws.canvas.h + 2 + 18)


def test_game_viewport_is_the_player_window(tmp_path):
    """ws._game_xy maps a system point at the playtest window's viewport origin
    into 320x240 game coords (the cart's touch() contract)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open()                       # launcher tap: run the selected cart
    drv.frame(1 / 30)
    win = ws.wm._wins["desktop"]
    ox, oy, scale = ws.wm._player_view(win)
    assert scale >= 1
    gx, gy = ws._game_xy(ox, oy)
    assert (gx, gy) == (0, 0)
    gx2, gy2 = ws._game_xy(ox + 10 * scale, oy + 7 * scale)
    assert (gx2, gy2) == (10, 7)
    # The viewport sits inside the window's content rect (centered letterbox).
    cx, cy, cw, ch = win.content_rect()
    assert ox >= cx and oy >= cy


def test_player_window_x_exits_to_caller(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    drv.frame(1 / 30)
    ws._leave_menu()
    drv.frame(1 / 30)
    win = ws.wm._wins["desktop"]
    xr = dict(ws.wm._strip_buttons(win))["close"]
    drv.touch(xr[0] + 2, xr[1] + 2)
    drv.frame(1 / 30)
    assert ws.wm._order[-1] == "menu"     # back to the editor window
    assert ws.wm._wins["menu"].kind == "menu"


# ---------------------------------------------------------------------------
# Pointer routing: window-local taps, drags, click-to-focus, desktop fall-through
# ---------------------------------------------------------------------------

def test_window_local_tap_hits_the_apps_bar(tmp_path):
    """A tap on the editor window's own bar row (its title strip) lands in the
    app's zone -- switching tabs via the ladder works at the WINDOW's position."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    drv.frame(1 / 30)
    assert ws.menu_view == "cards"        # Config-first landing
    win = ws.wm._wins["menu"]
    # The Editor's lent zone at shelf density: the labeled CODE tab chip (visual
    # identity v1 Phase 3) -- resolve its rect through the SAME geometry the
    # draw/tap path uses (editor_app._zone_parts + ui.tab_row_rects), so this
    # test tracks the layout instead of a hardcoded stride. The app's bar row
    # sits below the WM title strip.
    from runtime import ui_draw as _ui_draw
    from runtime import editor_app as _ea
    zone = win.ctx.layout.zone_left
    tabs_area, _play = ws.editor_app._zone_parts(zone)
    slim = [(tid, label) for tid, label, _ic in _ea._TAB_CHIPS]
    rects = dict((tid, r) for tid, r, _l in
                 _ui_draw.tab_row_rects(tabs_area, slim, max(1, zone[3] // 16)))
    cx, cy = rects["code"][0] + 2, rects["code"][1] + 2
    drv.touch(win.x + 1 + cx, win.y + 1 + win.title_h + cy)
    drv.frame(1 / 30)
    assert ws.menu_view == "code"


def test_window_drag_moves_it(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    win = ws.wm._wins["settings"]
    x0, y0 = win.x, win.y
    gx = win.x + win.w // 2               # grab the middle of the title strip
    gy = win.y + 4
    drv.touch(gx, gy)                     # press edge (arms the drag)
    drv.frame(1 / 30)
    drv.touch_drag(gx + 60, gy + 40)      # held move -> the drag engages
    drv.frame(1 / 30)
    drv.touch_drag(gx + 61, gy + 41)      # one more tick of the in-flight drag
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert (win.x, win.y) != (x0, y0)
    assert win.x > x0 and win.y > y0


def _engage_drag(ws, drv):
    """Open Settings and get its window into an in-flight drag, settled at a
    fixed position (the drag stays engaged while the pointer is held, so a repeat
    tick at the same point is the same state). Returns the window."""
    ws.open_settings()
    drv.frame(0.0)
    _quiesce(ws)
    win = ws.wm._wins["settings"]
    gx, gy = win.x + win.w // 2, win.y + 4
    drv.touch(gx, gy)
    drv.frame(0.0)                       # press edge: arms the drag
    drv.touch_drag(gx + 80, gy + 50)
    drv.frame(0.0)                       # travel > _DRAG_MIN: the drag engages
    assert ws.wm._drag is not None
    win._hold = (gx + 80, gy + 50)       # the settled grab point (stash for reuse)
    return win


def test_drag_backdrop_cache_engages(tmp_path):
    """During a drag the retained backdrop cache is built and reused: the first
    drag frame allocates + validates it, and steady drag frames don't re-render
    the desktop wallpaper/bar (they blit the cache)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    win = _engage_drag(ws, drv)
    hx, hy = win._hold
    # The drag engaged on the last _engage_drag frame -> the cache is built (the
    # capture runs in the same frame handle_pointer sets _drag).
    assert ws.wm._backdrop_valid and ws.wm._backdrop is not None

    calls = [0]
    backdrop = ws.wm._backdrop_layer
    real_draw = backdrop._draw_desktop
    backdrop._draw_desktop = lambda dt: (calls.__setitem__(0, calls[0] + 1),
                                         real_draw(dt))[1]
    # Steady drag frames: cache reused, desktop NOT re-rendered.
    for _ in range(3):
        drv.touch_drag(hx, hy)
        drv.frame(0.0)
    assert calls[0] == 0
    # Release: the desk itself did not CHANGE, so the cache keeps serving and
    # the desktop is NOT re-rendered. It used to re-render on every non-gesture
    # frame, which put a 120ms frame on every release (#155, measured on glass:
    # layer:launcher=77ms of which wallpaper=37ms, for a STATIC wallpaper).
    drv.touch_up()
    ws._dirty = True
    drv.frame(0.0)
    assert ws.wm._backdrop_valid
    assert calls[0] == 0
    # ...and it does re-render as soon as the desk really changes.
    ws.look.set_theme("berry" if ws.look.theme_name != "berry" else "forest", persist=False)
    ws._dirty = True
    drv.frame(0.0)
    assert calls[0] == 1
    backdrop._draw_desktop = real_draw


def test_drag_backdrop_cache_matches_live_render(tmp_path):
    """The cached drag frame is PIXEL-IDENTICAL to a live re-render of the same
    frame -- the cache faithfully reproduces the wallpaper + grid backdrop, so
    the optimization is invisible."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    win = _engage_drag(ws, drv)
    hx, hy = win._hold
    # A settled drag frame using the cache.
    drv.touch_drag(hx, hy)
    drv.frame(0.0)
    assert ws.wm._backdrop_valid
    cached = bytes(ws.sys_canvas._buf)
    # Force a live re-render of the identical frame (cache off), no window move.
    ws.wm._backdrop_valid = False
    ws._dirty = True
    drv.touch_drag(hx, hy)
    drv.frame(0.0)
    live = bytes(ws.sys_canvas._buf)
    assert cached == live


def test_click_moves_focus_without_popping_the_playtest(tmp_path):
    """The owner call that retired raise-by-pop: clicking the editor window while
    a playtest runs beside it moves FOCUS to the editor -- the back-stack is
    untouched, the Player keeps its stack-top slot (it keeps ticking), and only
    an explicit exit ends the run. Typing then lands in the editor."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    ws.set_menu_view("code")
    drv.frame(1 / 30)
    ws._leave_menu()                       # PLAY
    drv.frame(1 / 30)
    assert ws.wm._focus == "desktop"
    make = ws.wm._wins["menu"]
    # Drag the playtest window aside so a corner of the editor is exposed, then
    # click that corner.
    player = ws.wm._wins["desktop"]
    ws.wm._move_window(player, 600, 300)
    drv.frame(1 / 30)
    px, py = make.x + 4, make.y + 4
    assert ws.wm._win_at(px, py) == "menu"
    drv.touch(px, py)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.wm._focus == "menu"
    assert ws.wm.stack.top() == "desktop"   # the playtest was NOT popped
    assert "desktop" in ws.wm._order       # its window is still up
    # The game-pointer gate: a background cart must not see live taps.
    assert not ws.wm.player_has_pointer()
    # Keys go to the focused editor, not the game.
    ws.editor.set_text("")
    drv.type_char(ord("q"))
    drv.frame(1 / 30)
    assert ws.editor.text() == "q"
    # Refocusing the playtest restores its pointer.
    drv.touch(player.x + 40, player.y + 4)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.wm._focus == "desktop" and ws.wm.player_has_pointer()


def test_player_keeps_ticking_while_editor_is_focused(tmp_path):
    """The Player ticks by STACK position, not focus: with the editor focused,
    the running cart's frame counter keeps advancing."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    drv.frame(1 / 30)
    ws._leave_menu()                       # PLAY
    drv.frame(1 / 30)
    ws.wm._focus = "menu"                  # focus the editor beside it
    ws._dirty = True
    before = ws.player.frames if hasattr(ws.player, "frames") else None
    ticks = [0]
    real_tick = ws.player.tick
    def counting_tick(dt):
        ticks[0] += 1
        return real_tick(dt)
    ws.player.tick = counting_tick
    for _ in range(3):
        drv.frame(1 / 30)
    assert ticks[0] >= 3, "the playtest froze when it lost focus"


# ---------------------------------------------------------------------------
# Keyboard focus
# ---------------------------------------------------------------------------

def test_keyboard_goes_to_focused_window(tmp_path):
    """Typing lands in the focused editor window's code buffer, sized to the
    WINDOW's layout (not the desktop's)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    ws.set_menu_view("code")
    drv.frame(1 / 30)
    win = ws.wm._wins["menu"]
    assert ws.editor.COLS == win.ctx.code_layout.cols
    ws.editor.set_text("")
    for ch in "ok":
        drv.type_char(ord(ch))
        drv.frame(1 / 30)
    assert ws.editor.text() == "ok"


# ---------------------------------------------------------------------------
# v2 chrome: no copied taskbar, min/max/close strips, resize, taskbar chips
# ---------------------------------------------------------------------------

def test_window_bars_suppress_os_chrome(tmp_path):
    """Inside a window the zoned bar drops its OS right zone -- the desktop's
    full-width bar is the one taskbar (owner feedback). (It also used to prove the
    bottom dock never drew here; the dock was deleted 2026-08-21.)"""
    ws = _ws(tmp_path)
    assert ws.windowed_chrome
    bar = ws.bar_layer
    assert bar._in_window("menu") and bar._in_window("settings")
    assert not bar._in_window("home")          # the desktop bar keeps OS chrome


def test_title_strip_buttons_min_max_close(tmp_path):
    """Every app window carries minimize + maximize + close; the player window
    has no minimize (hiding a running game would silently pause it)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    names = [n for n, _r in ws.wm._strip_buttons(ws.wm._wins["settings"])]
    assert names == ["close", "max", "min"]
    ws.go_home()
    ws.open_desk()                              # a player WINDOW needs the desk (#105)
    ws.open()                                   # run a cart
    drv.frame(1 / 30)
    names = [n for n, _r in ws.wm._strip_buttons(ws.wm._wins["desktop"])]
    assert names == ["close", "max"]


def test_maximize_toggle_fills_desktop_and_restores(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    win = ws.wm._wins["settings"]
    orig = (win.x, win.y, win.w, win.h)
    mx = dict(ws.wm._strip_buttons(win))["max"]
    drv.touch(mx[0] + 2, mx[1] + 2)
    drv.frame(1 / 30)
    dx, dy, dw, dh = ws.wm._desk_rect()
    assert (dx, dy, dw, dh) == (0, ws.layout.status_h, 1024, 600 - ws.layout.status_h)
    assert (win.x, win.y, win.w, win.h) == (dx, dy, dw, dh)   # the desk, bar left
    assert win.ctx.layout.w == win.buf.w        # content relaid out to the new size
    mx = dict(ws.wm._strip_buttons(win))["max"]
    drv.touch(mx[0] + 2, mx[1] + 2)             # toggle back
    drv.frame(1 / 30)
    assert (win.x, win.y, win.w, win.h) == orig


def test_resize_grip_reflows_content(tmp_path):
    """Dragging the bottom-right grip resizes the window on release, and the
    content layout reflows to the new size."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    win = ws.wm._wins["settings"]
    # Shrink first so the grown size stays inside the desktop clamp.
    ws.wm._resize_window(win, win.w - 120, win.h - 120)
    drv.frame(1 / 30)
    w0, h0 = win.w, win.h
    gx, gy, gw, gh = ws.wm._grip_rect(win)
    drv.touch(gx + gw // 2, gy + gh // 2)       # press the grip
    drv.frame(1 / 30)
    assert ws.wm._resize is not None
    drv.touch_drag(gx + gw // 2 + 80, gy + gh // 2 + 50)
    drv.frame(1 / 30)
    assert (win.w, win.h) == (w0, h0)           # rubber band only while dragging
    drv.touch_up()
    drv.frame(1 / 30)
    assert (win.w, win.h) == (w0 + 80, h0 + 50)
    assert win.ctx.layout.w == win.buf.w == win.w - 2


def test_minimize_hides_window_and_chip_restores(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    win = ws.wm._wins["settings"]
    mn = dict(ws.wm._strip_buttons(win))["min"]
    drv.touch(mn[0] + 2, mn[1] + 2)
    drv.frame(1 / 30)
    assert win.minimized
    assert ws.wm._win_at(win.x + 10, win.y + 10) is None   # hidden from hit-tests
    # A minimized top window swallows no keys -- they fall to the desktop root.
    assert ws.wm._route_key(ws.input) is False
    # Its taskbar chip restores it.
    chips = ws.wm._chip_rects()
    assert chips and chips[0][0] == "settings"
    _kind, (cx, cy, cw, ch), _label = chips[0]
    drv.touch(cx + 2, cy + 2)
    drv.frame(1 / 30)
    assert not win.minimized


def test_a_chip_names_its_window_whole_while_the_bar_has_room(tmp_path):
    """A taskbar chip carried its title's first eight letters ("APPEARAN")
    with the bar half empty. It carries the whole title, and only a bar out
    of room shortens the longest first, every chip inside the cell."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_desk()
    for app in ("files", "appearance", "calc"):
        ws.open_app(ws._apps_by_id[app])
        drv.frame(1 / 30)
    labels = {k: lb for k, _r, lb in ws.wm._chip_rects()}
    assert labels["appearance"] == "APPEARANCE" and labels["files"] == "FILES"
    (_e, (bx, _by, _bw, _bh), _dy, _c), (_n, (cx, _cy, cw, _ch), _t, _s) = \
        ws.wm._chips_cell()
    ws.wm._win_title = lambda win: "A VERY LONG WINDOW TITLE INDEED " * 2
    chips = ws.wm._chip_rects()
    assert len(chips) == 3
    assert all(len(lb) < 64 for _k, _r, lb in chips)
    assert all(bx + cx <= r[0] and r[0] + r[2] <= bx + cx + cw for _k, r, _l in chips)


def test_chip_click_is_focus_only_never_a_pop(tmp_path):
    """Taskbar chips: click the focused window's chip to minimize, again to
    restore, and an unfocused window's chip to FOCUS it -- the back-stack never
    changes from the taskbar (nothing closes by looking at it)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    drv.frame(1 / 30)
    ws._leave_menu()                             # PLAY: playtest above the editor
    drv.frame(1 / 30)
    chips = {k: r for k, r, _l in ws.wm._chip_rects()}
    assert set(chips) == {"projects", "menu", "desktop"}
    stack_before = ws.wm.stack.kinds()
    # Clicking the editor's chip FOCUSES it -- no pop, the playtest stays up.
    r = chips["menu"]
    drv.touch(r[0] + 2, r[1] + 2)
    drv.frame(1 / 30)
    assert ws.wm._focus == "menu"
    assert ws.wm.stack.kinds() == stack_before
    # Clicking the (now focused) editor chip minimizes it; again restores+focuses.
    drv.touch(r[0] + 2, r[1] + 2)
    drv.frame(1 / 30)
    assert ws.wm._wins["menu"].minimized
    drv.touch(r[0] + 2, r[1] + 2)
    drv.frame(1 / 30)
    assert not ws.wm._wins["menu"].minimized and ws.wm._focus == "menu"
    assert ws.wm.stack.kinds() == stack_before         # still nothing popped


def test_editor_close_returns_to_the_projects_window(tmp_path):
    """The Editor window's X closes it alone -- back to the home window it was
    opened from (not a jump all the way to the desk)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    drv.frame(1 / 30)
    win = ws.wm._wins["menu"]
    assert win.kind == "menu"
    xr = dict(ws.wm._strip_buttons(win))["close"]
    drv.touch(xr[0] + 2, xr[1] + 2)
    drv.frame(1 / 30)
    assert ws.wm._order == ["projects"]


def test_font_scale_change_rebuilds_windows(tmp_path):
    """A Settings font-size change drops + rebuilds the window records at the
    new scale (the on_relayout hook), re-anchored to the ROOT canvas."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    assert "settings" in ws.wm._wins
    ws.look.set_font_scale(1, persist=False)
    assert ws.wm._wins == {}               # dropped for rebuild
    assert ws._sys_canvas is ws.wm._root_canvas
    drv.frame(1 / 30)
    assert "settings" in ws.wm._wins       # rebuilt at the new scale
    assert ws.layout.w == 1024


# ---------------------------------------------------------------------------
# WiFi mid-game (#38): Settings is an app, so it coexists with the one cart slot
# ---------------------------------------------------------------------------

def test_game_keeps_running_under_settings_window(tmp_path):
    """Opening Settings over a running cart does NOT pause it in windowed mode:
    the player window keeps ticking (and the frame loop keeps animating) while
    Settings floats above -- wifi setup mid-game."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open()                                  # run the selected cart
    drv.frame(1 / 30)
    ws.open_settings()
    drv.frame(1 / 30)
    assert ws.wm.stack.kinds() == ["launcher", "desk", "desktop", "settings"]
    assert ws.wm.keeps_animating(1 / 30)       # the redraw gate stays live
    ticks = [0]
    real_tick = ws.player.tick
    ws.player.tick = lambda dt: (ticks.__setitem__(0, ticks[0] + 1), real_tick(dt))
    for _ in range(3):
        drv.frame(1 / 30)
    assert ticks[0] >= 3, "the game paused under the Settings window"


def test_settings_wifi_panel_connects_while_game_runs(tmp_path):
    """The Settings WIFI panel drives the injected wifi service -- scan, pick a
    locked network, type the password, ENTER connects -- while a cart keeps
    running in its window. The wifi TOOL cart (which would replace the game in
    the one cart slot) is not involved."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open()                                  # a game in the cart slot
    drv.frame(1 / 30)
    cart_title = ws.cart.get("title")
    ws.open_settings()
    ws.settings_layer.open_wifi()
    drv.frame(1 / 30)
    sl = ws.settings_layer
    assert sl.wifi_view and sl.wifi_nets       # FakeWifi's canned networks
    # Pick the locked "Home WiFi" (row 0) -> password mode.
    sl.wifi_sel = 0
    sl._wifi_activate()
    assert sl.wifi_pick == "Home WiFi"
    for ch in "hunter2":
        drv.type_char(ord(ch))
        drv.frame(1 / 30)
    drv.type_char(13)                          # ENTER = connect
    drv.frame(1 / 30)
    assert ws.wifi.status()[0] and ws.wifi.status()[1] == "Home WiFi"
    assert "Home WiFi" in sl.wifi_known        # credentials remembered
    # The game never left the cart slot.
    assert ws.cart.get("title") == cart_title
    assert ws.wm.stack.kinds()[2] == "desktop"   # above the desk (#105)


def test_bar_wifi_icon_opens_the_wifi_panel_as_a_popover(tmp_path):
    """On the desk the bar's wifi widget opens the Settings WIFI panel as a
    popover over whatever runs (docs/shell_layout_2026-10.md §8): the game
    keeps its slot, no Settings window opens."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open()                                  # a game is running
    drv.frame(1 / 30)
    cart_title = ws.cart.get("title")
    lay = ws.wm._root_ctx.layout
    handled = ws.bar_layer.handle_bar_tap("home", lay.wifi_btn[0] + 1,
                                          lay.wifi_btn[1] + 1)
    drv.frame(1 / 30)
    assert handled
    assert ws.sysmenu.open and ws.sysmenu.widget == "wifi"
    assert not ws.wm.stack.has("settings")
    assert ws.settings_layer.wifi_nets         # the panel scanned
    assert ws.cart.get("title") == cart_title  # the game was NOT replaced


def test_closing_make_never_takes_settings_with_it(tmp_path):
    """Regression (owner-reported): closing the Make window used to TRUNCATE the
    back-stack, killing Settings (and anything else) stacked above it. Closing is
    surgical now: only the closed process leaves the stack."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    ws.open_settings()                          # Settings ABOVE the Make flow
    drv.frame(1 / 30)
    assert ws.wm.stack.kinds() == ["launcher", "desk", "projects", "menu", "settings"]
    make = ws.wm._wins["menu"]
    xr = dict(ws.wm._strip_buttons(make))["close"]
    drv.touch(xr[0] + 2, xr[1] + 2)             # X on the Make window (the EDITOR)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert "settings" in ws.wm._order           # Settings SURVIVED
    assert "menu" not in ws.wm._order           # the Editor alone closed
    assert ws.wm.stack.kinds() == ["launcher", "desk", "projects", "settings"]


def test_closing_playtest_keeps_settings_and_refocuses_editor(tmp_path):
    """Closing the playtest (X / hold-BACKSPACE -> _exit_to_caller) removes ONLY
    the player: Settings floating above stays open, and focus returns to the run
    caller's window (the Editor)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    drv.frame(1 / 30)
    ws._leave_menu()                            # PLAY (caller = the Editor)
    ws.open_settings()                          # Settings over the running game
    drv.frame(1 / 30)
    assert ws.wm.stack.kinds() == ["launcher", "desk", "projects", "menu", "desktop", "settings"]
    ws._exit_to_caller()                        # the hold-BACKSPACE exit path
    drv.frame(1 / 30)
    assert ws.wm.stack.kinds() == ["launcher", "desk", "projects", "menu", "settings"]
    assert "settings" in ws.wm._order           # Settings SURVIVED
    assert ws.wm._focus == "menu"               # back to the Editor's window


def test_settings_keyboard_close_is_surgical_too(tmp_path):
    """The Settings 'b' close (and its exit verb generally) removes just the
    Settings window -- a game running beneath keeps its window."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open()                                   # a game
    ws.open_settings()
    drv.frame(1 / 30)
    ws._exit_settings()
    drv.frame(1 / 30)
    assert ws.wm.stack.kinds() == ["launcher", "desk", "desktop"]
    assert "desktop" in ws.wm._order            # the game window survived


def test_theme_setting_cycles_and_persists(tmp_path):
    """Settings -> THEME cycles chrome.THEME_NAMES, retints the panel chrome
    everywhere (settings panel, window strips, chips, selection accents), and
    persists to system.json; the default "night" keeps today's exact colors."""
    ws = _ws(tmp_path)                             # aliases the bare-name modules
    from runtime.chrome import THEME_NAMES, theme_colors
    drv = _drv(ws)
    assert ws.look.theme_name == "night"
    assert ws.theme_colors == theme_colors("night")
    ws.look.cycle_theme(1)
    assert ws.look.theme_name == THEME_NAMES[1]           # "indigo"
    assert ws.theme_colors is not theme_colors("night")
    assert ws.launcher.theme is ws.theme_colors    # accents follow
    assert ws.system.get("theme") == ws.look.theme_name  # persisted
    # A themed frame renders (window strip uses the new title token).
    ws.open_settings()
    drv.frame(1 / 30)
    # And an unknown persisted name falls back to the default.
    ws.look.set_theme("nonsense", persist=False)
    assert ws.look.theme_name == "night"


def _engage_resize(ws, drv, dx=60, dy=-40):
    """Open Settings and get its window into an in-flight grip resize, grown by
    (dx, dy). Returns (win, cw, ch) -- the window and the rubber size."""
    ws.open_settings()
    drv.frame(0.0)
    _quiesce(ws)
    win = ws.wm._wins["settings"]
    gx0, gy0, gw, gh = ws.wm._grip_rect(win)
    gx, gy = gx0 + gw // 2, gy0 + gh // 2
    drv.touch(gx, gy)
    drv.frame(0.0)                       # press in the grip: resize engages
    assert ws.wm._resize is not None
    drv.touch_drag(gx + dx, gy + dy)
    drv.frame(0.0)
    assert ws.wm._resize is not None
    cw, ch = ws.wm._resize[5], ws.wm._resize[6]
    return win, cw, ch


def test_union_restore_engages_and_is_partial(tmp_path):
    """Steady drag frames restore the backdrop via the RECT-clipped stamp with a
    window-sized union -- never the full-screen copy (#58 dirty-union restore)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    win = _engage_drag(ws, drv)
    hx, hy = win._hold
    rects = []
    full = [0]
    cache = ws.wm._backdrop
    real_rect = ws.sys_canvas.blit_strip_rect
    real_full = ws.sys_canvas.blit_strip

    def spy_rect(layer, dst_x, dst_y, rx, ry, rw, rh):
        if layer is cache:
            rects.append((rx, ry, rw, rh))
        return real_rect(layer, dst_x, dst_y, rx, ry, rw, rh)

    def spy_full(layer, dst_x=0, dst_y=0):
        if layer is cache:
            full[0] += 1
        return real_full(layer, dst_x, dst_y)

    ws.sys_canvas.blit_strip_rect = spy_rect
    ws.sys_canvas.blit_strip = spy_full
    try:
        for i in range(4):
            drv.touch_drag(hx + i * 10, hy)
            drv.frame(0.0)
    finally:
        ws.sys_canvas.blit_strip_rect = real_rect
        ws.sys_canvas.blit_strip = real_full
    assert full[0] == 0                       # never the 1.2MB full restore
    assert rects                              # the strip stamps ran
    # Body subtraction: the window's own footprint is NOT restored (the stamp
    # covers it opaquely every frame), so the total restored area per frame is
    # the thin EXPOSED margin -- a small fraction of the window, let alone the
    # screen. 4 frames of 10px moves: allow a generous margin budget.
    area = sum(rw * rh for _rx, _ry, rw, rh in rects)
    frames = 4
    assert area < frames * (win.w + win.h) * 130   # ~margin strips, not bodies
    assert area < ws.sys_canvas.w * ws.sys_canvas.h  # sanity: << one full screen


def test_union_restore_matches_full_restore_while_moving(tmp_path):
    """A MOVING drag under the union restore is pixel-identical to the same drag
    under the full-screen restore -- the damage history covers every trail the
    window leaves (compared below the bar row; the bar clock is time-dependent)."""
    def run(union_on):
        ws = _ws(tmp_path)
        drv = _drv(ws)
        win = _engage_drag(ws, drv)
        ws.wm._union_disabled = not union_on
        hx, hy = win._hold
        frames = []
        for i in range(5):
            drv.touch_drag(hx + i * 17, hy + i * 9)
            drv.frame(0.0)
            # 60 rows of PIXELS, two bytes each: exclude the bar strip (live clock)
            skip = 60 * ws.sys_canvas.w * 2
            frames.append(bytes(ws.sys_canvas._buf[skip:]))
        return frames

    a = run(True)
    b = run(False)
    for i, (fa, fb) in enumerate(zip(a, b)):
        assert fa == fb, "union restore diverged at drag frame %d" % i


def test_live_resize_body_follows_grip(tmp_path):
    """During a grip resize the window BODY tracks the rubber size (the 'real OS'
    feel, #58): the focused border lands at the rubber corner, grown area shows
    the panel field, the real relayout still only happens on release."""
    ws = _ws(tmp_path)
    _BORDER_TOP = ws.theme_colors["shade_dark"]   # classic's ring
    drv = _drv(ws)
    win, cw, ch = _engage_resize(ws, drv)
    ow, oh = win.w, win.h
    assert (cw, ch) != (ow, oh)              # the rubber actually grew
    assert win.w == ow and win.h == oh       # no mid-gesture relayout
    sc = ws.sys_canvas
    # Focused border drawn at the RUBBER corner, not the old one. pix() reads a
    # palette INDEX back (the buffer holds RGB565), so a theme token compares
    # directly.
    assert sc.pix(win.x + cw - 1, win.y + ch - 1) == _BORDER_TOP
    # A grown-area probe (beyond the old width, inside the new content rect)
    # shows the panel field fill -- the content crop anchored top-left.
    px = win.x + ow + 10
    py = win.y + win.title_h + 20
    assert px < win.x + cw - 1
    assert sc.pix(px, py) == ws.theme_colors["panel"]
    # Release applies the REAL resize (the existing apply-on-release contract).
    drv.touch_up()
    drv.frame(0.0)
    assert ws.wm._resize is None
    assert (win.w, win.h) == (cw, ch)


def test_resize_outline_fallback_without_rect_stamp(tmp_path):
    """A canvas without blit_strip_rect (the web RecordingLayer) keeps the old
    rubber-band OUTLINE preview and the full-screen backdrop restore."""
    ws = _ws(tmp_path)
    _BORDER_TOP = ws.theme_colors["shade_dark"]   # classic's ring
    drv = _drv(ws)
    ws.sys_canvas.blit_strip_rect = None     # instance attr shadows the method
    win, cw, ch = _engage_resize(ws, drv)
    sc = ws.sys_canvas
    # The accent outline is drawn at the rubber rect...
    accent = ws.theme_colors["accent"]
    assert sc.pix(win.x + cw - 1, win.y + ch - 1) == accent
    # ... and the body was NOT drawn at the rubber size (the border at the
    # rubber corner would be _BORDER_TOP under live-body).
    assert sc.pix(win.x + cw - 1, win.y + ch - 1) != _BORDER_TOP


# ---------------------------------------------------------------------------
# Key input vs the redraw gate (#44/#58: the P4 BLE-keyboard slowdown)
# ---------------------------------------------------------------------------

def _held_key_frame(ws, key=ord("q")):
    """One router pass with a held/typed key; report whether it dirtied the
    shell. Mimics a BLE keyboard: last_key is LEVEL state (a held byte every
    frame), unlike the T-Deck's one-shot press edge."""
    ws._dirty = False
    ws.input._pressed = set()
    ws.input.last_key = key
    ws.handle_input()
    ws.input.last_key = 0
    return ws._dirty


def test_held_key_to_focused_playtest_stays_quiet(tmp_path):
    """A key whose only consumer is the RUNNING cart must not mark the shell
    dirty: on this tier a dirty frame is the FULL desktop repaint
    (backdrop+windows+bar) instead of the quiet game-window blit. Measured on
    the P4: ANY mashed key (even ones the game ignores) collapsed play from
    ~30fps to ~10 via exactly this mark. Typing into a focused editor beside
    the playtest must keep repainting as before."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    drv.frame(1 / 30)
    ws._leave_menu()                        # PLAY: playtest window, focused
    drv.frame(1 / 30)
    assert ws.wm._focus == "desktop"
    assert ws.wm.keys_to_cart()
    assert not _held_key_frame(ws), "cart-bound key repainted the desktop"
    ws.wm._focus = "menu"                   # the editor beside it takes keys
    assert not ws.wm.keys_to_cart()
    assert _held_key_frame(ws), "editor typing must still repaint"


def test_crash_panel_keys_repaint_again(tmp_path):
    """cart_error hands keys back to the system chrome (EDIT/CODE nav), so the
    dirty mark must return on the crash panel."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    drv.frame(1 / 30)
    ws._leave_menu()
    drv.frame(1 / 30)
    ws.cart_error = "boom"
    assert not ws.wm.keys_to_cart()
    assert _held_key_frame(ws)


def test_window_drag_freezes_its_content(tmp_path):
    """#113: gesture tuples carry the window REGISTRY key; the freeze resolves
    gestures through _wins identity, so a window dragged by its strip renders
    no content mid-drag (on the web transport a re-render also re-shipped the
    window's whole recorded stream per frame -- the payload autopsy)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    drv.frame(1 / 30)
    _quiesce(ws)
    ws.open_projects()
    for _ in range(4):
        drv.frame(1 / 30)
    win = ws.wm._wins["projects"]
    calls = [0]
    orig = ws.projects_layer.draw

    def spy(dt):
        calls[0] += 1
        return orig(dt)

    ws.projects_layer.draw = spy
    sx, sy = win.x + win.w // 2, win.y + win.title_h // 2
    drv.touch(sx, sy)
    drv.frame(1 / 30)
    x = sx
    for _ in range(4):                     # arm the strip drag, then move
        x += 8
        drv.touch_drag(x, sy)
        drv.frame(1 / 30)
    assert ws.wm._drag is not None and ws.wm._drag[0] == "projects"
    calls[0] = 0
    drv.touch_drag(x + 8, sy)
    drv.frame(1 / 30)
    assert calls[0] == 0                   # frozen: no content render mid-drag
    drv.touch_up()
    drv.frame(1 / 30)
    assert calls[0] > 0                    # release: live rendering resumes


# -- settled windows are not re-stamped every frame (#155) -------------------

def _open_two_windows(ws, drv):
    ws.open_settings()
    drv.frame(1 / 30)
    ws.open_projects()
    for _ in range(40):            # settle covers + the stamp streaks
        drv.frame(1 / 30)
    assert len(ws.wm._order) >= 2
    return ws.wm._order


def _settle_gesture(ws, drv, frames=6):
    """Put the WM in the state where retained stamps are legal: a CONTENT
    gesture (a finger scrolling inside a window). Only then does the desk
    backdrop serve its cache instead of re-rendering -- and a live desk render
    wipes the buffer, so windows can never skip while it runs. (That coupling
    is why a non-gesture frame still repaints everything; see the desk-cache
    note in _BackdropLayer.draw.)"""
    win = ws.wm._wins[ws.wm._order[-1]]
    cx = win.x + 1 + win.w // 2
    cy = win.y + 1 + win.title_h + (win.h - win.title_h) // 2
    drv.touch(cx, cy)                     # press inside the top window's CONTENT
    drv.frame(1 / 30)
    for _ in range(frames):
        drv.touch_drag(cx, cy)            # held (the flag is per-frame, set by
        ws.mark_dirty()                   # _route_pointer's content dispatch)
        drv.frame(1 / 30)
    assert ws.wm._content_gesture, "the content gesture never registered"


def test_settled_lower_windows_are_skipped(tmp_path):
    """A window whose shape and content haven't changed must not be re-stamped
    and re-chromed every frame.

    Measured on glass 2026-07-26: every open window was repainted every frame
    even when only the top one was being used, so each extra window cost ~44ms
    a frame (1 window 56ms, 2 windows 99ms, 3 windows 144ms). Skipping is sound
    only for a contiguous run at the BOTTOM of the z-order (a repainting window
    overwrites the overlap of everything below it), which is what
    _lowest_dirty_window returns."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    _open_two_windows(ws, drv)
    _settle_gesture(ws, drv)

    drawn = []
    wm = ws.wm
    real = wm._draw_app_window

    def spy(win, focused, dt, **kw):
        drawn.append(win.kind)
        return real(win, focused, dt, **kw)

    wm._draw_app_window = spy
    ws.mark_dirty()
    drv.frame(1 / 30)
    assert drawn, "no window drew at all"
    assert len(drawn) < len(wm._order) or len(wm._order) == 1, \
        "every window still repainted: %s of %s" % (drawn, wm._order)


def test_skipping_is_pixel_identical_to_a_full_repaint(tmp_path):
    """The skipped frame's screen must equal a frame that repainted every
    window -- the correctness contract behind the skip (verified on glass with
    a same-buffer byte compare; this is its host pin)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    _open_two_windows(ws, drv)
    _settle_gesture(ws, drv)

    def full():
        ws.wm._win_sig = None            # void every retained stamp
        ws.wm._desk_painted = True
        ws.mark_dirty()
        drv.frame(1 / 30)

    full(); full()
    reference = bytes(ws.sys_canvas._buf)
    ws.mark_dirty()
    drv.frame(1 / 30)                    # a frame free to skip settled windows
    assert bytes(ws.sys_canvas._buf) == reference


def test_moving_a_window_voids_the_skip(tmp_path):
    """Any change to the window SHAPE of the frame (geometry, z-order, focus,
    minimise) must force a full window repaint -- the signature guard."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    _open_two_windows(ws, drv)
    _settle_gesture(ws, drv)
    assert ws.wm._lowest_dirty_window(1 / 30) > 0     # settled: something skips

    win = ws.wm._wins[ws.wm._order[-1]]
    ws.wm._move_window(win, win.x + 11, win.y + 7)
    assert ws.wm._lowest_dirty_window(1 / 30) == 0, \
        "a moved window must void every retained stamp"


# -- the chrome freeze (#155) ------------------------------------------------

def test_window_chrome_freezes_on_quiet_frames(tmp_path):
    """A window's title strip / border / shadow is disjoint from its content
    stamp, so on a quiet frame those pixels are already correct in this
    ping-pong buffer. Measured on P4 glass: redrawing them cost 8.2ms of a 70ms
    picker-scroll frame, every frame, for a bar that never changed."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    _open_two_windows(ws, drv)
    _settle_gesture(ws, drv, frames=10)
    win = ws.wm._wins[ws.wm._order[-1]]
    assert ws.wm._chrome_quiet, "the frame never went quiet"
    assert win._chrome_streak >= 2, \
        "chrome never reached both ping-pong buffers (streak=%s)" % (
            getattr(win, "_chrome_streak", None),)


def test_chrome_freeze_survives_only_two_paints_then_skips(tmp_path):
    """The streak counts CONSECUTIVE quiet paints: a disturbance restarts it, so
    both ping-pong buffers are refreshed before skipping resumes."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    _open_two_windows(ws, drv)
    _settle_gesture(ws, drv, frames=10)
    win = ws.wm._wins[ws.wm._order[-1]]
    assert win._chrome_streak >= 2

    # A desk repaint wipes the buffer under the chrome -> restart.
    ws.wm._desk_painted = True
    ws.wm._draw_windows(1 / 30)
    assert win._chrome_streak == 1, \
        "a desk repaint must restart the chrome streak"


def test_chrome_freeze_refreshes_when_the_title_changes(tmp_path):
    """The freeze keys on what the chrome DRAWS (geometry, focus, title, theme,
    font scale) -- change any of it and both buffers must be repainted."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    _open_two_windows(ws, drv)
    _settle_gesture(ws, drv, frames=10)
    win = ws.wm._wins[ws.wm._order[-1]]
    assert win._chrome_streak >= 2
    before = win._chrome_sig

    ws.wm._win_chrome(win, True, quiet=True)      # unchanged -> still frozen
    assert win._chrome_streak >= 2

    ws.look.set_theme("berry" if ws.look.theme_name != "berry" else "forest",
                 persist=False)
    ws.wm._win_chrome(win, True, quiet=True)
    assert win._chrome_sig != before
    assert win._chrome_streak == 1, "a theme change must repaint the chrome"


def test_chrome_freeze_still_draws_the_resize_grip(tmp_path):
    """The grip sits INSIDE the content rect, so the window's content stamp
    overwrites it every frame -- it is the one piece the freeze can never
    skip."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    _open_two_windows(ws, drv)
    _settle_gesture(ws, drv, frames=10)
    win = ws.wm._wins[ws.wm._order[-1]]
    assert win._chrome_streak >= 2            # frozen

    grips = []
    real = ws.wm._win_grip
    ws.wm._win_grip = lambda w, f: (grips.append(w.kind), real(w, f))[1]
    try:
        ws.wm._win_chrome(win, True, quiet=True)
    finally:
        ws.wm._win_grip = real
    assert grips == [win.kind], "a frozen chrome must still redraw the grip"


def test_chrome_freeze_is_pixel_identical(tmp_path):
    """The frozen frame's screen must equal a frame that repainted all chrome --
    the correctness contract behind the freeze."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    _open_two_windows(ws, drv)
    _settle_gesture(ws, drv, frames=10)

    def full():
        for w in ws.wm._wins.values():
            w._chrome_sig = None          # void the freeze
        ws.wm._chip_sig = None
        ws.mark_dirty()
        drv.frame(1 / 30)

    full(); full()
    reference = bytes(ws.sys_canvas._buf)
    ws.mark_dirty()
    drv.frame(1 / 30)                     # a frame free to freeze the chrome
    assert bytes(ws.sys_canvas._buf) == reference


def test_taskbar_chips_freeze_and_refresh_on_focus_change(tmp_path):
    """The chips live on the OS bar, which no window overlaps -- same freeze,
    same restart rule (here: the focused chip is a different colour)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    order = _open_two_windows(ws, drv)
    _settle_gesture(ws, drv, frames=10)
    assert ws.wm._chip_streak >= 2, "the chips never froze"

    ws.wm._focus = order[0]               # focus moves -> a chip changes colour
    ws.wm._draw_taskbar_chips(quiet=True)
    assert ws.wm._chip_streak == 1, "a focus change must repaint the chips"


# -- changing the font scale from inside a window (#155) ---------------------

def test_font_scale_change_from_a_window_reaches_the_root_canvas(tmp_path):
    """Changing FONT SIZE in Settings must resize the console for real.

    On this tier ws._sys_canvas is whatever WINDOW BUFFER is installed while a
    window's content draws or handles input -- and the Settings row that changes
    the font is exactly that. Pushing the new scale into the ambient canvas set
    it on a buffer on_relayout then discarded, so the layout reflowed to the new
    scale while the root canvas (and every window buffer, which clones its
    font_scale from the root) kept rendering text at the old one. Owner, on
    glass 2026-07-26: "changing it while running messes it up, if I change and
    reboot it looks great"."""
    ws = _ws(tmp_path, font_scale=2)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    win = ws.wm._wins["settings"]

    ws.wm._install(win.ctx)               # what input dispatch does
    try:
        ws.look.set_font_scale(1, persist=False)
    finally:
        ws.wm._install(ws.wm._root_ctx)

    assert ws.look.font_scale == 1
    assert ws.layout.fs == 1
    assert ws.wm._root_canvas.font_scale == 1, \
        "the scale landed on a throwaway window buffer, not the root canvas"


def _pin_clock(ws):
    """Freeze the OS bar's clock for a whole-frame pixel comparison.

    The bar prints HH:MM, so a minute boundary falling between the reference
    render and the compared one makes two otherwise-identical frames differ --
    a real once-in-a-while flake, seen once in a full-suite run before this."""
    ws.bar_layer._clock_text = lambda: "10:30"


def test_font_scale_change_matches_booting_at_that_scale(tmp_path):
    """The pixel contract: switching at runtime must render EXACTLY what booting
    at that scale renders -- the mismatch is what read as 'bunched up'."""
    ws_boot = _ws(tmp_path, font_scale=1)
    drv_boot = _drv(ws_boot)
    _pin_clock(ws_boot)
    ws_boot.open_settings()
    for _ in range(30):
        ws_boot.mark_dirty()
        drv_boot.frame(1 / 30)
    reference = bytes(ws_boot.sys_canvas._buf)

    ws = _ws(tmp_path, font_scale=2)
    drv = _drv(ws)
    _pin_clock(ws)
    ws.open_settings()
    for _ in range(30):
        ws.mark_dirty()
        drv.frame(1 / 30)
    win = ws.wm._wins["settings"]
    ws.wm._install(win.ctx)
    try:
        ws.look.set_font_scale(1, persist=False)
    finally:
        ws.wm._install(ws.wm._root_ctx)
    for _ in range(30):
        ws.mark_dirty()
        drv.frame(1 / 30)

    assert bytes(ws.sys_canvas._buf) == reference


def test_new_window_buffers_adopt_the_changed_font_scale(tmp_path):
    """A window opened AFTER the change must render at the new scale too --
    new_layer clones font_scale from the root, so this is the same bug seen from
    the other end."""
    ws = _ws(tmp_path, font_scale=2)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    ws.wm._install(ws.wm._wins["settings"].ctx)
    try:
        ws.look.set_font_scale(1, persist=False)
    finally:
        ws.wm._install(ws.wm._root_ctx)
    ws.open_projects()
    for _ in range(20):
        ws.mark_dirty()
        drv.frame(1 / 30)
    for key, win in ws.wm._wins.items():
        assert getattr(win.buf, "font_scale", 1) == 1, key


# -- direct render: no window buffer, no stamp (#155) ------------------------

def _scroll_frames(ws, drv, frames=8):
    """Hold a content drag inside the top window and paint `frames` frames."""
    win = ws.wm._wins[ws.wm._order[-1]]
    cx = win.x + 1 + win.w // 2
    cy = win.y + 1 + win.title_h + (win.h - win.title_h) // 2
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    for i in range(frames):
        drv.touch_drag(cx - 4 * i, cy)
        ws.mark_dirty()
        drv.frame(1 / 30)


def test_direct_render_is_taken_during_a_content_scroll(tmp_path):
    """The whole point: while a window's content is being scrolled it must draw
    straight into the framebuffer instead of into a private buffer that is then
    copied. On the P4 that copy is ~900KB of bus traffic a frame."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    _open_two_windows(ws, drv)

    hits = []
    real = ws.wm._direct_render
    ws.wm._direct_render = lambda win, dt: (hits.append(win.kind),
                                            real(win, dt))[1]
    _scroll_frames(ws, drv)
    assert hits, "the direct path never ran during a content scroll"


def test_direct_render_matches_the_stamp_path_pixel_for_pixel(tmp_path):
    """Equivalence on a REAL frame, not just the canvas unit test: the same
    gesture with the direct path forced off must produce the same screen."""
    def run(direct, sub):
        # A DISTINCT save dir per run: two workstations sharing one would have
        # the second start from state the first persisted (selection, system.json),
        # which shows up as a pixel diff that has nothing to do with the path.
        ws = _ws(tmp_path / sub)
        drv = _drv(ws)
        _quiesce(ws)
        _open_two_windows(ws, drv)
        _pin_clock(ws)
        if not direct:
            ws.wm._direct_render = lambda win, dt: False   # force the stamp
        _scroll_frames(ws, drv)
        return bytes(ws.sys_canvas._buf)

    assert run(True, "a") == run(False, "b")


def test_direct_render_declines_when_the_window_was_resized(tmp_path):
    """The window's layouts were built for win.buf's size, so a viewport that
    doesn't match it would lay the content out for the wrong surface. Decline
    rather than draw something subtly wrong."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    _open_two_windows(ws, drv)
    win = ws.wm._wins[ws.wm._order[-1]]
    win.w += 40                       # geometry moved, buffer did not
    assert ws.wm._direct_render(win, 1 / 30) is False


def test_direct_render_leaves_no_viewport_installed(tmp_path):
    """A leaked viewport would silently mask every later draw on the frame."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    _open_two_windows(ws, drv)
    _scroll_frames(ws, drv)
    root = ws.wm._root_canvas
    assert (root._ox, root._oy) == (0, 0)
    assert (root.w, root.h) == (root._stride, len(root._buf) // 2 // root._stride)


# -- the desk cache is keyed on the desk, not on gesturing (#155) -------------

def test_a_clock_tick_does_not_re_render_the_desk(tmp_path):
    """The desk render is wallpaper cover-crop + icon column + bar, ~77ms on P4
    glass. The clock is the only part of it that changes while nothing else
    does, so it is repainted over the cached blit rather than being allowed to
    invalidate the cache -- otherwise the desk re-rendered once a second."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    _open_two_windows(ws, drv)
    _settle_gesture(ws, drv, frames=6)
    assert ws.wm._backdrop_valid

    renders = [0]
    backdrop = ws.wm._backdrop_layer
    real = backdrop._draw_desktop
    backdrop._draw_desktop = lambda dt: (renders.__setitem__(0, renders[0] + 1),
                                         real(dt))[1]
    clocks = [0]
    real_clock = ws.bar_layer.redraw_clock
    ws.bar_layer.redraw_clock = lambda where: (
        clocks.__setitem__(0, clocks[0] + 1), real_clock(where))[1]
    try:
        # Force the clock string to change, as a minute boundary would.
        ws.bar_layer._clock_at = -1
        ws.bar_layer._clock_cache = "00:00"
        for _ in range(4):
            ws.mark_dirty()
            drv.frame(1 / 30)
    finally:
        backdrop._draw_desktop = real
        ws.bar_layer.redraw_clock = real_clock
    assert renders[0] == 0, "a clock tick re-rendered the whole desk"
    assert clocks[0] > 0, "the clock was never repainted over the cache"


def test_the_desk_sig_excludes_the_cover_generation(tmp_path):
    """Covers landing in the home window must not invalidate the desk: those are
    exactly the frames a scroll is trying to keep cheap."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    _open_two_windows(ws, drv)
    before = ws.wm._desk_sig_value() if hasattr(ws.wm, "_desk_sig_value") \
        else ws.wm._backdrop_layer._desk_sig()
    ws.covers.gen += 5
    after = ws.wm._backdrop_layer._desk_sig()
    assert before == after


def test_the_desk_sig_moves_with_the_theme_and_size(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _quiesce(ws)
    _open_two_windows(ws, drv)
    layer = ws.wm._backdrop_layer
    a = layer._desk_sig()
    ws.look.set_theme("berry" if ws.look.theme_name != "berry" else "forest", persist=False)
    assert layer._desk_sig() != a


# ---------------------------------------------------------------------------
# Fat-finger strip buttons (owner report 2026-07-27): at font scale 1 the
# 16px buttons at 18px pitch were finger-unhittable on the 7" glass -- a
# near-miss tap armed a window DRAG instead of closing ("exit a window and it
# stays on the desktop"). A tap anywhere in the button block resolves to the
# nearest button center; taps left of the block still arm the drag.
# ---------------------------------------------------------------------------

def test_near_miss_tap_on_the_x_still_closes(tmp_path):
    ws = _ws(tmp_path, font_scale=1)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    win = ws.wm._wins["settings"]
    xr = dict(ws.wm._strip_buttons(win))["close"]
    # 5px below the visual rect, in the border gap -- the old exact-rect test
    # missed here and the window drag-armed instead.
    drv.touch(xr[0] + xr[2] // 2, xr[1] + xr[3] + 5)
    drv.frame(1 / 30)
    assert "settings" not in ws.wm._order
    assert ws.wm._drag_armed is None


def test_between_buttons_resolves_to_the_nearest(tmp_path):
    ws = _ws(tmp_path, font_scale=1)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    win = ws.wm._wins["settings"]
    mr = dict(ws.wm._strip_buttons(win))["min"]
    # 2px left of the min button: nearest center is min -> minimized, not drag.
    drv.touch(mr[0] - 2, mr[1] + mr[3] // 2)
    drv.frame(1 / 30)
    assert ws.wm._wins["settings"].minimized


def test_left_of_the_button_block_still_arms_the_drag(tmp_path):
    ws = _ws(tmp_path, font_scale=1)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    win = ws.wm._wins["settings"]
    mr = dict(ws.wm._strip_buttons(win))["min"]
    assert ws.wm._strip_button_hit(win, mr[0] - 40, mr[1] + 2) is None
    drv.touch(mr[0] - 40, mr[1] + 2)      # strip, well left of the block
    drv.frame(1 / 30)
    assert ws.wm._drag_armed is not None
    assert "settings" in ws.wm._order


def test_closed_window_leaves_no_artifact_on_the_desk(tmp_path):
    """Owner report 2026-07-27: 'if I open and close it, it is closed but
    remains as an artefact on the desktop.' The desk-restore skip keyed only on
    desk STATICS, so the close frame skipped the restore and the dead window's
    pixels stayed. The shape signature now resets the skip streak. Fails on the
    old code."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    lay = ws.layout
    # Settle the desk past the skip streak, then baseline the pixels below the bar.
    for _ in range(6):
        ws._dirty = True
        drv.frame(1 / 30)
    base = bytes(ws.sys_canvas._buf)
    ws.open_settings()
    for _ in range(4):
        drv.frame(1 / 30)
    win = ws.wm._wins["settings"]
    region = (win.x, win.y, win.w, win.h)
    ws.wm._close_window("settings")
    for _ in range(4):
        drv.frame(1 / 30)
    after = bytes(ws.sys_canvas._buf)
    x, y, w, h = region
    stride = ws.sys_canvas.w
    bar = lay.status_h + 2
    diff = 0
    for yy in range(max(y, bar), y + h):
        a = base[yy * stride + x:yy * stride + x + w]
        c = after[yy * stride + x:yy * stride + x + w]
        if a != c:
            diff += sum(1 for p, q in zip(a, c) if p != q)
    assert "settings" not in ws.wm._order
    assert diff == 0, "%d stale pixels where the window was" % diff


# ---------------------------------------------------------------------------
# The `view(w, h)` cart verb: a declared logical viewport composites at the
# biggest integer scale that FITS THE VIEW (celeste's 128x128 -> 4x on a
# 1024x600 surface instead of the 320x240 container's 2x), and tap mapping
# shifts back into full canvas coords. Windowed player WINDOWS keep the full
# canvas (v1), so their mapping must not shift.
# ---------------------------------------------------------------------------

def test_view_verb_scales_the_fullscreen_composite(tmp_path):
    ws = _ws(tmp_path)
    _drv(ws)
    ws.input.game_view = (128, 128)
    assert ws.game_view == (96, 56, 128, 128)      # centered source rect
    assert ws.wm._wins.get("desktop") is None      # fullscreen (no player window)
    ox, oy, scale = ws.wm.viewport()
    assert scale == 4                              # 600 // 128, not 600 // 240
    assert (ox, oy) == ((1024 - 512) // 2, (600 - 512) // 2)
    # Tap mapping: the composite's top-left is the view's source origin.
    assert ws.wm.game_xy(ox, oy) == (96, 56)
    assert ws.wm.game_xy(ox + 511, oy + 511) == (96 + 127, 56 + 127)
    ws.input.game_view = None
    assert ws.game_view is None
    assert ws.wm.viewport()[2] == 2                # back to the container scale


def test_view_full_canvas_and_zero_are_identity(tmp_path):
    ws = _ws(tmp_path)
    _drv(ws)
    for v in (None, (0, 0), (320, 240)):
        ws.input.game_view = v
        assert ws.game_view is None


def test_view_scales_the_windowed_player_too(tmp_path):
    # The desk-world game WINDOW honors the view like the fullscreen composite:
    # one _view_src drives viewport, blit and tap mapping on both paths.
    # Launched from the desk (a PLAY-sized window, #178): a dev playtest opens at
    # 1x of the full canvas deliberately, which leaves a 128x128 view no room to
    # upscale -- that is the small window doing its job, not the view being
    # ignored (the mapping assertions below hold identically there).
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open()
    drv.frame(1 / 30)
    win = ws.wm._wins.get("desktop")
    if win is None:
        return                                     # tier launched fullscreen
    ws.input.game_view = (128, 128)
    cx, cy, cw, ch = win.content_rect()
    ox, oy, scale = ws.wm.viewport()
    assert scale == max(1, min(cw // 128, ch // 128))
    assert scale > max(1, min(cw // 320, ch // 240))   # bigger than full-canvas
    assert ws.wm.game_xy(ox, oy) == (96, 56)
def test_hover_select_marks_dirty_inside_the_projects_window(tmp_path):
    """#177: a hover-preview that moves sel WITHOUT marking dirty never
    repaints inside a window -- the content freeze reuses the retained buffer
    (state moved, pixels didn't: dead on desktop + web)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_projects()
    drv.frame(1 / 30)
    win = ws.wm._wins["projects"]
    p = ws.pointer
    p.visible = True
    p.down = False
    ws.wm._install(win.ctx)
    try:
        t2 = ws.projects_grid.tile_rect(2)
    finally:
        ws.wm._install(ws.wm._root_ctx)
    cx, cy, _cw, _ch = win.content_rect()
    p.x, p.y = 900, 560
    ws.wm._route_pointer(900, 560, False)          # seed the hover tracker
    ws._dirty = False
    tx, ty = cx + t2[0] + 8, cy + t2[1] + 8
    p.x, p.y = tx, ty
    ws.wm._route_pointer(tx, ty, False)
    assert ws.projects_grid.sel == 2
    assert ws._dirty, "hover-select must mark dirty or the window never repaints"


def test_moving_cursor_leaves_no_trail_on_the_desk(tmp_path):
    """The backdrop streak-skip (#155) assumed nothing above the desk needed
    erasing. A VISIBLE cursor is drawn over the desk every painted frame, so
    from the third consecutive moving frame on, skipping the restore baked a
    trail of stale cursor sprites into the retained buffer. The skip now
    yields to any drawn-pointer-state change while a cursor is visible."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    p = ws.pointer
    p.visible = True
    cv = ws.sys_canvas

    def snap(x0):
        return [cv.pix(x, y) for x in range(x0, x0 + 44) for y in range(326, 370)]

    p.x, p.y = 400, 330
    drv.frame(1 / 30)
    base = {x: snap(x) for x in (500, 560, 620)}   # clean desk, cursor far away
    stale = 0
    for x in (500, 560, 620, 680, 740, 800):       # sweep across bare desk
        p.x, p.y = x, 330
        drv.frame(1 / 30)
        for bx, b in base.items():
            if abs(bx - x) >= 50:                  # away from the live cursor
                stale += sum(1 for a, c in zip(b, snap(bx)) if a != c)
    assert stale == 0, "stale cursor pixels remained on skipped desk frames"


def test_a_dead_window_releases_its_buffer_and_the_backdrop_survives_a_relayout(tmp_path):
    """Window buffers are off-heap layers on a board and nothing collects
    them: the WM releases one when its window dies, when a resize rebuilds
    it, and when a relayout drops every window. The drag backdrop is kept
    across a relayout (same root size) instead of being re-minted at 2MB
    a round. Measured leak this closes: ~3.6MB of PSRAM per Library ->
    CHANGE -> home round on the Guition P4 (2026-09-09)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    win = ws.wm._wins["settings"]
    comp = win.buf._comp
    assert comp._buf is not None
    # A resize rebuilds the buffer: the old one is released first.
    ws.wm._resize_window(win, win.w - 40, win.h - 40)
    assert comp._buf is None, "the pre-resize buffer was released"
    comp2 = win.buf._comp
    assert comp2 is not comp and comp2._buf is not None
    # Closing the window releases the buffer.
    ws.exit()
    drv.frame(1 / 30)
    assert ws.wm._wins == {}
    assert comp2._buf is None
    # The drag backdrop: minted once, invalidated (not dropped) by a relayout.
    ws.open_settings()
    drv.frame(1 / 30)
    cache = ws.wm._ensure_backdrop()
    assert ws.wm._backdrop is cache
    comp3 = ws.wm._wins["settings"].buf._comp
    ws._relayout()                                  # the relayout hook
    assert ws.wm._wins == {} and comp3._buf is None
    assert ws.wm._backdrop is cache and not ws.wm._backdrop_valid
    assert cache._comp._buf is not None
    # A root that changed size re-mints it and releases the old one.
    ws.wm._root_canvas.w -= 0                       # unchanged: the same layer
    assert ws.wm._ensure_backdrop() is cache


# -- two app windows of different sizes (#224, sprint 5) ------------------------

# The desk with Calc and Files open as windows of two sizes, each frame hashed
# the way tests/test_shell_goldens.py hashes a surface, once with Files on top
# and once after Calc is reopened above it. Each app lays itself out and draws
# from its OWN grant's surface row (native/moy_app), which the WM writes as it
# installs each window's layout context: a row the console shared, or one read
# from the last window installed, lays an app out at the other window's size.
TWO_WINDOWS = (
    "72790000c31ff55f3853de5ffdd0771a19c6fba9f7bd353c1d9283d9ceb4682b",
    "e4f789c4a5b9cab45daaf1eee21fe2a08b12ca94ea46caf2cfc31b5f2e907629",
)


def test_two_app_windows_of_different_sizes_match_their_golden(tmp_path):
    from test_shell_goldens import _quiesce as _still, _render
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _still(ws)
    ws.open_desk()
    drv.frame(1 / 30)
    ws.open_app(ws._apps_by_id["calc"])
    for _ in range(5):
        drv.frame(1 / 30)
    calc = ws.wm._wins["calc"]
    calc.x, calc.y = 24, 48
    ws.wm._resize_window(calc, 360, 300)
    ws.open_app(ws._apps_by_id["files"])
    for _ in range(5):
        drv.frame(1 / 30)
    files = ws.wm._wins["files"]
    assert (calc.w, calc.h) != (files.w, files.h)
    got = [_render(ws, "two windows", "desk")]
    ws.open_app(ws._apps_by_id["calc"])
    for _ in range(5):
        drv.frame(1 / 30)
    assert ws.wm._order[-1] == "calc"
    got.append(_render(ws, "two windows, calc on top", "desk"))
    assert tuple(got) == TWO_WINDOWS


# -- console mode as WM state (docs/shell_layout_2026-10.md §3, step 1) ---------
#
# The desk's PLAY enters console mode: the windows park outside the back-stack,
# the route truncates to its root and the home presents fullscreen; the home's X
# leaves it, and the route's restore op puts the parked kinds back. Every branch
# §3 names reads the mode, and each is pinned here by name.

def _center(r):
    return r[0] + r[2] // 2, r[1] + r[3] // 2


def _desk_with_three(ws, drv):
    """The desk with Settings, Calc and the Editor (CHANGE on the home's
    selected cart), the Editor on top and focused."""
    ws.open_settings()
    ws.open_app(ws._apps_by_id["calc"])
    ws.change_selected()
    drv.frame(1 / 30)
    assert ws.wm._order == ["settings", "calc", "menu"]
    return {k: ws.wm._wins[k] for k in ws.wm._order}


def _content_draws(ws, monkeypatch):
    """Count each content layer's draws by window kind."""
    seen = []
    for kind in ("settings", "calc"):
        layer = ws.wm._content_for(kind)
        orig = layer.draw
        monkeypatch.setattr(layer, "draw",
                            lambda dt, _k=kind, _o=orig: (seen.append(_k), _o(dt))[1])
    menu = ws.wm._content_for("menu")
    orig_menu = menu.draw
    monkeypatch.setattr(menu, "draw",
                        lambda dt: (seen.append("menu"), orig_menu(dt))[1])
    return seen


def test_play_enters_console_mode_and_parks_the_windows(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    wins = _desk_with_three(ws, drv)
    bufs = {k: w.buf._comp for k, w in wins.items()}
    rects = {k: (w.x, w.y, w.w, w.h) for k, w in wins.items()}
    ws.open_library()                            # the desk's PLAY
    drv.frame(1 / 30)
    wm = ws.wm
    assert wm.mode == "console"
    assert wm.stack.kinds() == ["launcher"]      # the route at its root
    assert wm._order == [] and wm._wins == {}
    assert wm.visible_stack()[0] is ws.launcher_layer
    p = wm._parked
    assert p.kinds == ["settings", "calc", "menu"]
    assert p.order == ["settings", "calc", "menu"] and p.focus == "menu"
    for k, w in wins.items():                    # the records, stamps and all
        assert p.wins[k] is w
        assert (w.x, w.y, w.w, w.h) == rects[k]
        assert w.buf._comp is bufs[k] and bufs[k]._buf is not None
    assert wm.is_parked("menu") and not wm.is_parked("desk")


def test_the_x_leaves_console_mode_with_the_windows_unrendered(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    wins = _desk_with_three(ws, drv)
    bufs = {k: w.buf for k, w in wins.items()}
    ws.open_library()
    drv.frame(1 / 30)
    assert ws.bar_layer._show_x("home")          # the desk is beneath the home
    seen = _content_draws(ws, monkeypatch)
    x, y = _center(ws.layout.context_x_btn)
    assert ws.bar_layer.handle_bar_tap("home", x, y)   # the home's X
    wm = ws.wm
    assert wm.mode == "desktop"
    assert wm.stack.kinds() == ["launcher", "desk", "settings", "calc",
                                "menu"]
    assert seen == [], "a restored window re-rendered on the way back"
    assert wm._order == ["settings", "calc", "menu"] and wm._focus == "menu"
    for k, w in wins.items():
        assert wm._wins[k] is w and w.buf is bufs[k]
    assert wm._parked is None
    drv.frame(1 / 30)
    assert "settings" not in seen and "calc" not in seen   # stamped, not drawn


def test_a_released_stamp_re_renders_once_on_return(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    wins = _desk_with_three(ws, drv)
    ws.open_library()
    drv.frame(1 / 30)
    old = wins["settings"].buf._comp
    assert ws.wm.release_cache()                  # the oldest parked stamp
    assert wins["settings"].buf is None and old._buf is None
    assert wins["calc"].buf is not None
    seen = _content_draws(ws, monkeypatch)
    ws.open_desk()                               # the X's verb
    assert seen == ["settings"]                  # once, the path a resize takes
    w = wins["settings"]
    assert w.buf is not None and w.ctx is not None and not w._buf_stale
    assert ws.wm._wins["settings"] is w


def test_the_fit_release_goes_oldest_first_then_the_home_frame(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    wins = _desk_with_three(ws, drv)
    ws.open_library()
    drv.frame(1 / 30)
    ws.launcher_layer.prealloc_retained()
    assert ws.launcher_layer._lib_cache is not None
    freed = []
    while ws.wm.release_cache():
        freed.append([k for k in ("settings", "calc", "menu")
                      if wins[k].buf is None])
    assert freed[:3] == [["settings"], ["settings", "calc"],
                         ["settings", "calc", "menu"]]
    assert len(freed) == 4 and ws.launcher_layer._lib_cache is None
    assert not ws.wm.release_cache()
    ws.open_desk()
    assert all(w.buf is not None for w in wins.values())


def test_a_parked_kind_reopened_fullscreen_is_found_in_place(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    wins = _desk_with_three(ws, drv)
    rect = (wins["settings"].x, wins["settings"].y)
    ws.open_library()
    ws.open_settings()                           # the one Settings process
    drv.frame(1 / 30)
    assert ws.wm.mode == "console" and ws.wm._order == []
    assert ws.wm.stack.kinds() == ["launcher", "settings"]
    assert ws.wm._parked.wins["settings"] is wins["settings"]
    ws.exit()                                    # its X: back to the home
    drv.frame(1 / 30)
    assert ws.wm.stack.kinds() == ["launcher"] and ws.wm.mode == "console"
    seen = _content_draws(ws, monkeypatch)
    ws.open_desk()
    assert seen == ["settings"]                  # shows what the process holds now
    w = ws.wm._wins["settings"]
    assert w is wins["settings"] and (w.x, w.y) == rect


def test_play_ends_a_running_playtest(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    drv.frame(1 / 30)
    ws._leave_menu()                             # the Editor's PLAY
    drv.frame(1 / 30)
    assert "desktop" in ws.wm._wins
    released = []
    orig = ws.player.release_world
    ws.player.release_world = lambda: (released.append(1), orig())[1]
    ws.open_library()
    assert not ws.wm.stack.has("desktop") and released
    assert "desktop" not in ws.wm._parked.kinds
    assert "desktop" not in ws.wm._parked.wins
    assert ws.wm._parked.kinds == ["projects", "menu"]


def test_branch_slots_reads_the_mode(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _desk_with_three(ws, drv)
    assert ws.wm._slots() == ["settings", "calc", "menu"]
    ws.open_library()
    ws.wm.stack.goto("desk")                     # a stack shape no longer asks
    assert ws.wm._slots() == []
    ws.wm.stack.goto("launcher")


def test_branch_keeps_animating_reads_the_mode(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    monkeypatch.setattr(ws.wallpaper, "is_animating", lambda dt: True)
    assert ws.wm.keeps_animating(1 / 30)
    ws.open_library()
    assert not ws.wm.keeps_animating(1 / 30)     # a parked desk is not drawn


def test_branch_desk_open_and_windowed_chrome_read_the_mode(tmp_path):
    ws = _ws(tmp_path)
    assert ws.wm.desk_open() and ws.windowed_chrome
    ws.open_library()
    assert not ws.wm.desk_open() and not ws.windowed_chrome
    ws.open_desk()
    assert ws.wm.desk_open() and ws.windowed_chrome


def test_branch_the_mode_switch_relayouts_and_never_drops_a_window(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _desk_with_three(ws, drv)
    drops = []
    relayouts = []
    monkeypatch.setattr(ws.wm, "_drop_wins", lambda: drops.append(1))
    orig = ws._relayout
    monkeypatch.setattr(ws, "_relayout", lambda: (relayouts.append(1), orig())[1])
    ws.open_library()
    assert relayouts == [1]
    ws.open_desk()
    assert relayouts == [1, 1]
    assert drops == []
    assert ws.wm._root_ctx.layout is ws.layout


def test_branch_focus_and_sync_windows_come_back_as_parked(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    wins = _desk_with_three(ws, drv)
    ws.wm._focus = "calc"                        # a lower window held focus
    ws.open_library()
    ws.open_desk()
    ws.wm._sync_windows()
    assert ws.wm._focus == "calc"
    assert ws.wm._order == ["settings", "calc", "menu"]
    assert ws.wm._wins["menu"] is wins["menu"] and wins["menu"].kind == "menu"


def test_branch_a_relayout_in_console_mode_releases_the_parked_stamps(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    wins = _desk_with_three(ws, drv)
    ws.open_library()
    ws._relayout()                               # a font-scale or theme relayout
    assert all(w.buf is None for w in wins.values())
    assert ws.wm._parked.order == ["settings", "calc", "menu"]
    ws.open_desk()
    for w in wins.values():                      # rebuilt at the new layout
        assert w.buf is not None and w.ctx.layout.w == w.buf.w


def test_branch_go_home_in_console_mode_touches_no_parked_window(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _desk_with_three(ws, drv)
    project, cart = ws.project, ws.cart
    assert ws.editor_app.project is project
    ws.open_library()
    ws.open_settings()
    ws.go_home()                                 # HOME from a console surface
    assert ws.wm.stack.kinds() == ["launcher"] and ws.wm.mode == "console"
    assert ws.project is project and ws.cart is cart
    assert ws.editor_app.project is project
    ws.open_desk()
    assert ws.wm._wins["menu"].kind == "menu"


def test_branch_go_home_in_desktop_mode_lands_on_the_desk(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _desk_with_three(ws, drv)
    ws.go_home()
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop"
    assert ws.wm.stack.kinds() == ["launcher", "desk"]
    assert ws.wm._order == [] and ws.cart is None


def test_branch_a_console_run_closes_the_parked_editors_project(tmp_path):
    """A cart run from the home swaps the workspace -- the Editor's exit path
    (#111) -- so its parked window does not come back; the others do, in
    their places."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    wins = _desk_with_three(ws, drv)
    rect = (wins["calc"].x, wins["calc"].y)
    ws.open_library()
    ws.open()                                    # a game, fullscreen
    drv.frame(1 / 30)
    assert ws.wm.mode == "console" and ws.wm._order == []
    ws._exit_to_caller()
    drv.frame(1 / 30)
    assert ws.wm.stack.kinds() == ["launcher"]
    ws.open_desk()
    assert ws.wm.stack.kinds() == ["launcher", "desk", "settings", "calc"]
    assert ws.wm._order == ["settings", "calc"]
    assert "menu" not in ws.wm._wins and wins["menu"].buf is None
    w = ws.wm._wins["calc"]
    assert w is wins["calc"] and (w.x, w.y) == rect


def test_branch_exit_doors_in_console_mode(tmp_path):
    """The Make tab and CHANGE open the Editor fullscreen; B on the home
    leaves; no door flips the mode but the X and its keyboard twin."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_library()
    ws.set_home_tab("make")
    ws.launcher.sel = 1                          # a project: Make's tap edits
    ws.launch_selected()
    drv.frame(1 / 30)
    assert ws.wm.mode == "console" and ws.wm.top_is("menu")
    assert ws.wm._order == []
    ws.go_home()
    ws.set_home_tab("games")
    ws.launcher.sel = 1
    ws.change_selected()
    drv.frame(1 / 30)
    assert ws.wm.mode == "console" and ws.wm.top_is("menu")
    ws.go_home()
    drv.frame(1 / 30)
    drv.press("b")
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop" and ws.wm.top_is("desk")


def test_branch_presentation_reads_the_mode(tmp_path):
    ws = _ws(tmp_path)
    wm = ws.wm
    assert not wm.game_is_fullscreen() and not wm.player_has_pointer()
    ws.open_library()
    assert wm.game_is_fullscreen() and wm.player_has_pointer()


def test_the_home_x_follows_the_floor(tmp_path):
    """Console mode's home has an X on a P4 (the desk is beneath it) and none
    on the fullscreen tier, where the home is the floor."""
    from ws_helpers import build_ws
    ws = _ws(tmp_path / "p4")
    assert not ws.bar_layer._show_x("desk")
    ws.open_library()
    assert ws.bar_layer._show_x("home")
    drv = _drv(ws)
    drv.frame(1 / 30)
    drv.click(*_center(ws.layout.context_x_btn))
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop" and not ws.bar_layer._show_x("desk")
    small = build_ws(tmp_path / "tdeck")
    assert small.wm.mode == "console" and small.wm.floor() == 1
    assert not small.bar_layer._show_x("home")
    small.open_library()
    assert small.wm.stack.kinds() == ["launcher"]


# -- the HOME key: back out one level, everywhere (owner, 2026-10-13) -----------

def test_home_on_the_desk_minimises_every_window_and_home_again_restores(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    wins = _desk_with_three(ws, drv)
    bufs = {k: w.buf for k, w in wins.items()}
    drv.frame(1 / 30)                            # a key's press is an edge
    drv.press("home")
    drv.frame(1 / 30)
    wm = ws.wm
    assert wm.mode == "desktop" and wm._order == ["settings", "calc", "menu"]
    assert all(w.minimized for w in wins.values())       # kept, as chips
    assert wm._focus is None
    assert wm.stack.kinds()[-1] == "menu"                # nothing closed
    assert all(w.buf is bufs[k] for k, w in wins.items())
    drv.frame(1 / 30)                            # a key's press is an edge
    drv.press("home")
    drv.frame(1 / 30)
    assert not any(w.minimized for w in wins.values())
    assert wm._focus == "menu"


def test_home_never_minimises_a_running_game(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    drv.frame(1 / 30)
    ws._leave_menu()                             # a playtest window
    drv.frame(1 / 30)
    ws.home_key()
    assert ws.wm._wins["menu"].minimized
    assert not ws.wm._wins["desktop"].minimized
    assert ws.wm._focus == "desktop" and ws.wm.stack.has("desktop")


def test_home_in_console_mode_backs_out_one_level(tmp_path):
    """From a console-mode surface HOME goes home; from the home on a P4 it
    goes to the desktop, its windows where they were."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _desk_with_three(ws, drv)
    ws.open_library()
    ws.open_settings()
    drv.frame(1 / 30)
    drv.frame(1 / 30)                            # a key's press is an edge
    drv.press("home")
    drv.frame(1 / 30)
    assert ws.wm.mode == "console" and ws.wm.stack.kinds() == ["launcher"]
    drv.frame(1 / 30)                            # a key's press is an edge
    drv.press("home")
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop"
    assert ws.wm._order == ["settings", "calc", "menu"]
    assert not any(w.minimized for w in ws.wm._wins.values())


def test_home_on_the_fullscreen_tier_is_unchanged(tmp_path):
    from ws_helpers import build_ws
    ws = build_ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    drv.frame(1 / 30)                            # a key's press is an edge
    drv.press("home")
    drv.frame(1 / 30)
    assert ws.wm.stack.kinds() == ["launcher"]
    drv.press("home")                            # the home is the floor
    drv.frame(1 / 30)
    assert ws.wm.stack.kinds() == ["launcher"]


# -- the popover (docs/shell_layout_2026-10.md §8, step 6) ----------------------
#
# One popover at a time, anchored at the widget's cell, closed by a tap outside,
# B, ESC or HOME. A change that stays inside it -- open, a row moved, close --
# rides the quiet stack: the frame draws the popover's rect alone over the
# retained one (the pixels under it put back from a snapshot on close) and hands
# that rect as its damage.

class _Key:
    """One frame's key press, as the popover layer reads it."""

    last_key = 0

    def __init__(self, name):
        self.name = name

    def pressed(self, n):
        return n == self.name

    def held(self, n):
        return n == self.name

    def any_pressed(self):
        return True


class _Ring:
    """N physical buffers behind the root canvas: each drawn frame lands in the
    next one, which still holds what was drawn N frames before -- the Waveshare
    P4's triple buffer, on the host's one."""

    def __init__(self, ws, n):
        self.ws, self.cv, self.n = ws, ws.sys_canvas, n
        self.cv.RETAINED_FRAMES = n
        self.bufs = [bytes(self.cv._buf)] * n
        self.k = 0
        self.stacks = []

    def frame(self, drv):
        ws, wm = self.ws, self.ws.wm
        before = ws._frames_drawn
        got = []
        real = wm.draw_stack
        wm.draw_stack = lambda: got.append(real()) or got[-1]
        try:
            drv.frame(1 / 30)
        finally:
            del wm.draw_stack
        if ws._frames_drawn == before:
            return None
        st = got[-1]
        self.stacks.append("pop" if st is wm._pop_stack else
                           "qpop" if st is wm._quiet_pop_stack else
                           "quiet" if st is wm._quiet_stack else "full")
        self.bufs[self.k] = bytes(self.cv._buf)
        self.k = (self.k + 1) % self.n
        self.cv._buf[:] = self.bufs[self.k]
        return self.stacks[-1]

    def settle(self, drv, n=10):
        self.stacks = []
        for _ in range(n):
            self.frame(drv)
        return self.stacks

    def full(self, drv):
        """Every buffer after N forced full repaints of the state as it is."""
        wm = self.ws.wm
        for _ in range(self.n):
            wm._win_sig = None
            wm._desk_painted = True
            wm._backdrop_valid = False
            self.ws._dirty = True
            self.frame(drv)
        return list(self.bufs)


def _popover_desk(tmp_path, game=False):
    ws = build_desktop_ws(tmp_path, font_scale=1, panel_diagonal_in=7.0)
    drv = _drv(ws)
    ws.bar_layer._clock_text = lambda: "00:00"
    ws.bar_layer._today = lambda: (2026, 10, 14)
    ws.open_desk()
    ws.open_app(ws._apps_by_id["calc"])
    if game:
        select_on_home(ws, "Star Catcher")
        ws.open()
    for _ in range(20):
        _quiesce(ws)
        ws._confetti_until = ws._egg_until = 0
        drv.frame(1 / 30)
    ws.show_fps = False
    return ws, drv


def _tap_widget(ws, drv, name):
    from runtime import shell_layout
    lay = ws.wm._root_ctx.layout
    i, (x, y, w, h) = shell_layout.cell_of(lay.bars, name)
    bx, by = lay.bars[i][1][:2]
    drv.click(bx + x + w // 2, by + y + h // 2)


def test_a_widget_tap_opens_its_popover_at_its_cell(tmp_path):
    ws, drv = _popover_desk(tmp_path)
    for name in ("clock", "wifi", "battery", "menu"):
        _tap_widget(ws, drv, name)
        drv.frame(1 / 30)
        m = ws.sysmenu
        assert m.open and m.widget == name
        x, y, w, h = m.panel_rect()
        assert y == ws.layout.bars[0][1][3]           # hangs from the top bar
        assert 0 <= x and x + w <= ws.sys_canvas.w
        assert (m.comp is None) == (name == "menu")
        _tap_widget(ws, drv, name)                    # the anchor again closes it
        drv.frame(1 / 30)
        assert not m.open


def test_a_tap_outside_closes_the_popover_and_reaches_nothing(tmp_path):
    ws, drv = _popover_desk(tmp_path)
    _tap_widget(ws, drv, "clock")
    drv.frame(1 / 30)
    calc = ws.wm._wins["calc"]
    focus = ws.wm._focus
    drv.click(calc.x + calc.w // 2, calc.y + calc.h // 2)
    drv.frame(1 / 30)
    assert not ws.sysmenu.open
    assert ws.wm._focus == focus                      # the tap was the popover's


def test_b_esc_and_home_close_the_popover(tmp_path):
    ws, drv = _popover_desk(tmp_path)
    for key in ("b", "stop", "home"):
        ws.widget_tap("battery")
        assert ws.sysmenu.open
        ws._sysmenu_layer.handle_input(_Key(key))
        assert not ws.sysmenu.open, key
    ws.widget_tap("battery")
    esc = _Key(None)
    esc.last_key = 27
    ws._sysmenu_layer.handle_input(esc)
    assert not ws.sysmenu.open
    assert ws.wm.mode == "desktop"                    # HOME did not show the desk


def test_one_popover_at_a_time(tmp_path):
    ws, drv = _popover_desk(tmp_path)
    ws.widget_tap("clock")
    ws.widget_tap("wifi")                             # another widget replaces it
    assert ws.sysmenu.widget == "wifi"
    assert "settings" in ws.leases.holders()
    ws.widget_tap("menu")
    assert ws.sysmenu.widget == "menu" and ws.sysmenu.comp is None
    assert ws.leases.holders() == []                  # the wifi panel let go


def test_the_wifi_popover_releases_the_radio_on_every_close(tmp_path):
    ws, drv = _popover_desk(tmp_path)
    closes = (
        lambda: _tap_widget(ws, drv, "wifi"),                         # the anchor
        lambda: drv.click(5, ws.sys_canvas.h - 5),                    # outside
        lambda: ws._sysmenu_layer.handle_input(_Key("b")),            # B
        lambda: ws._sysmenu_layer.handle_input(_Key("home")),         # HOME
    )
    for close in closes:
        _tap_widget(ws, drv, "wifi")
        drv.frame(1 / 30)
        assert ws.sysmenu.widget == "wifi"
        assert ws.leases.holders() == ["settings"] and ws.wifi.radio is True
        close()
        drv.frame(1 / 30)
        assert not ws.sysmenu.open
        assert ws.leases.holders() == [] and ws.wifi.radio is False


def test_the_wifi_popover_runs_the_password_step_in_place(tmp_path):
    ws, drv = _popover_desk(tmp_path)
    ws.widget_tap("wifi")
    drv.frame(1 / 30)
    sl = ws.settings_layer
    k = next(i for i, n in enumerate(sl.wifi_nets) if n[2])            # locked
    ws.sysmenu.comp.host.focus("net%d" % k)
    drv.press("a")
    drv.frame(1 / 30)
    assert sl.wifi_pick is not None and ws.sysmenu.comp.typing()
    for ch in "hunter2":
        drv.type_char(ord(ch))
        drv.frame(1 / 30)
    assert ws.sysmenu.open                          # "b" was typed, not B
    drv.type_char(13)
    drv.frame(1 / 30)
    assert ws.wifi.status()[0]
    assert ws.sysmenu.open and ws.sysmenu.widget == "wifi"


def test_a_popover_change_paints_its_rect_alone(tmp_path):
    """Open, a row moved, close: on a persistent root each is ONE frame of the
    popover layers alone, its rect the frame's damage -- never the desk."""
    ws, drv = _popover_desk(tmp_path)
    damage = []
    ws.sys_canvas.note_damage = lambda *r: damage.append(r)
    ring = _Ring(ws, 1)
    ring.settle(drv)
    for step in (lambda: ws.widget_tap("battery"),
                 lambda: ws._sysmenu_layer.handle_input(_Key("down")),
                 lambda: ws.widget_tap("battery")):
        rect = ws.sysmenu.panel_rect() if ws.sysmenu.open else None
        damage.clear()
        step()
        rect = rect or ws.sysmenu.panel_rect()
        assert ring.settle(drv) == ["pop"]
        assert damage == [rect]
    assert ring.settle(drv) == []                     # idle: no frame at all


def test_a_popover_is_pixel_identical_in_every_buffer(tmp_path):
    """On a triple-buffered root, after the popover opens, moves and closes,
    every buffer equals a full repaint of the same state."""
    for name in ("clock", "wifi", "menu"):
        ws, drv = _popover_desk(tmp_path / name)
        ring = _Ring(ws, 3)
        for _ in range(3):
            ws._dirty = True
            ring.frame(drv)
        ring.settle(drv)
        ws.widget_tap(name)
        ring.settle(drv)
        ws._sysmenu_layer.handle_input(_Key("right" if name == "clock" else "down"))
        assert ring.settle(drv) == ["pop"] * 3
        assert ring.bufs[(ring.k - 1) % 3] == ring.full(drv)[(ring.k - 1) % 3]
        ring.settle(drv)
        ws.widget_tap(name)
        ring.settle(drv)
        seen = list(ring.bufs)
        assert seen == ring.full(drv), name


def test_a_popover_over_a_playtest_keeps_the_quiet_stack(tmp_path):
    ws, drv = _popover_desk(tmp_path, game=True)
    ring = _Ring(ws, 1)
    assert set(ring.settle(drv, 4)) == {"quiet"}
    ws.widget_tap("clock")
    assert set(ring.settle(drv, 4)) == {"qpop"}
    assert ws.wm._pop_over_game and ws.wm._pop_face_ok   # its face rides the game
    _game_only_moved(ws, drv, ring, ws.sysmenu.panel_rect())
    assert ring.settle(drv, 4)[1:] == ["qpop"] * 3    # the repaint's debt paid
    ws.widget_tap("clock")
    assert ring.settle(drv, 4) == ["qpop", "quiet", "quiet", "quiet"]
    _game_only_moved(ws, drv, ring)


def _game_only_moved(ws, drv, ring, over=None):
    """The retained frame equals a full repaint everywhere but the game's
    own pixels (it moved between the two); `over`, a rect drawn above it,
    must match too."""
    win = ws.wm._wins["desktop"]
    seen = ring.bufs[0]
    full = ring.full(drv)[0]
    w = ws.sys_canvas.w
    cx, cy, cw, ch = win.content_rect()
    ox, oy, ow, oh = over or (0, 0, 0, 0)
    for i in range(0, len(seen), 2):
        if seen[i:i + 2] != full[i:i + 2]:
            x, y = (i // 2) % w, (i // 2) // w
            assert not (ox <= x < ox + ow and oy <= y < oy + oh), (x, y)
            assert cx <= x < cx + cw and cy <= y < cy + ch, (x, y)


def test_the_fit_check_releases_a_closed_popovers_snapshots(tmp_path):
    ws, drv = _popover_desk(tmp_path)
    ws.widget_tap("battery")
    for _ in range(3):
        drv.frame(1 / 30)
    wm = ws.wm
    snap = wm._pop_slots[wm._pop_cur][0]
    assert snap is not None and snap._comp._buf is not None
    while wm.release_cache():
        pass
    assert wm._pop_slots[wm._pop_cur][0] is snap         # live: kept
    ws.widget_tap("battery")
    for _ in range(3):
        drv.frame(1 / 30)
    while wm.release_cache():
        pass
    assert all(s[0] is None for s in wm._pop_slots)
    assert snap._comp._buf is None


def test_the_editor_and_its_playtest_are_titled_by_app_and_cart(tmp_path):
    """docs/look_2026-10.md step 23: "Editor \u00b7 Star Catcher" over "Play \u00b7
    Star Catcher" (a hyphen where the title face has no dot), and under the
    look's depth a chip names the app alone."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    drv.frame(1 / 30)
    ws._leave_menu()
    drv.frame(1 / 30)
    cart = ws.cart.get("title")
    wins = ws.wm._wins
    assert ws.wm._win_title(wins["menu"]) == "Editor - " + cart
    assert ws.wm._win_title(wins["desktop"]) == "Play - " + cart
    labels = {k: lb for k, _r, lb in ws.wm._chip_rects()}
    assert labels["menu"].startswith("Editor - ")
    ws.look.set_theme("moy", persist=False)
    drv.frame(1 / 30)
    labels = {k: lb for k, _r, lb in ws.wm._chip_rects()}
    assert (labels["menu"], labels["desktop"]) == ("Editor", "Play")


# -- the desk and windows (docs/look_2026-10.md step 25) ----------------------

def _moy_desk(tmp_path, variant="light"):
    ws = build_desktop_ws(tmp_path, font_scale=1, panel_diagonal_in=7.0)
    ws.look.set_theme("moy", persist=False, variant=variant)
    ws.open_desk()
    _quiesce(ws)
    ws.frame(1 / 30)
    return ws


def _px(cv, x, y):
    i = (y * cv.w + x) * 2
    return cv._buf[i] << 8 | cv._buf[i + 1]


def test_the_desk_column_is_tiles_with_their_labels_over_the_backdrop(tmp_path):
    """Under the look the column is 44-pixel tiles on the 74-row pitch, the
    first 18 under the bar, centred 54 in from the desk's edge; each label is
    the title its app names, under its tile, on the backdrop with no plate."""
    ws = _moy_desk(tmp_path)
    rects = ws.wm._backdrop_layer._icon_rects()
    labels = [r[3] for r in rects]
    assert labels[:2] == ["Play", "Projects"]
    assert {"Paint", "Storybook", "Files", "Calc", "Get carts"} <= set(labels)
    dx, dy, _dw, _dh = ws.wm._desk_rect()
    cv = ws.sys_canvas
    for i, (_key, box, band, _label, _cart) in enumerate(rects):
        assert box == (dx + 32, dy + 18 + 74 * i, 44, 44)
        assert band[1] == box[1] + 52 and band[0] + band[2] // 2 == box[0] + 22
        # no plate: the band's edge is the backdrop either side of it
        assert _px(cv, band[0] + 1, band[1] + 1) == _px(cv, band[0] - 1, band[1] + 1)
        assert _px(cv, box[0] + 2, box[1] + 22) == 0xFFFF       # the tile's fill


def test_a_desk_tile_draws_its_apps_icon_from_the_set(tmp_path):
    """Every tile's icon is its app's slot of the theme's set (Paint's by
    its alias), at the 20-pixel size in the ink."""
    ws = _moy_desk(tmp_path)
    drawn = []
    real = ws.draw_icon
    ws.draw_icon = lambda kind, rect, ink, cv=None: (
        drawn.append((kind, rect[2], real(kind, rect, ink, cv))), drawn[-1][2])[1]
    ws.wm._backdrop_layer._draw_desk_icons()
    keys = [r[0] for r in ws.wm._backdrop_layer._icon_rects()]
    assert [d[0] for d in drawn] == keys
    assert all(size == 20 and ok for _k, size, ok in drawn)
    from runtime import iconsets
    assert iconsets._pick("default", 20)[1] == 20


def test_a_desk_label_over_a_wallpaper_is_white_over_a_shadow(tmp_path, monkeypatch):
    """Over the theme's backdrop a label takes the ink; over a wallpaper,
    whose pixels the theme does not know, white over a black shadow."""
    from runtime import ui_draw
    ws = _moy_desk(tmp_path)
    got = []
    real = ui_draw.label
    monkeypatch.setattr(ui_draw, "label", lambda cv, r, t, ink, align=0, bg=None,
                        shadow=None: (got.append((t, ink, shadow)),
                                      real(cv, r, t, ink, align, bg, shadow))[1])
    ws.wm._backdrop_layer._draw_desk_icons()
    assert got and all(ink == ws.theme_colors["ink"] and sh is None for _t, ink, sh in got)
    got.clear()
    monkeypatch.setattr(ws.look, "wallpaper_id", ws.look.PICTURE)
    ws.wm._backdrop_layer._draw_desk_icons()
    black, white = ws.look.live.fixed[2:]
    assert got and all(ink == white and sh[0] == black for _t, ink, sh in got)


def test_a_theme_without_a_look_keeps_its_capitals_and_pills(tmp_path):
    ws = build_desktop_ws(tmp_path, font_scale=1, panel_diagonal_in=7.0)
    ws.open_desk()
    ws.frame(1 / 30)
    labels = [r[3] for r in ws.wm._backdrop_layer._icon_rects()]
    assert labels[:2] == ["PLAY", "PROJECTS"] and "CALC" in labels
    ws.open_app(ws._apps_by_id["calc"])
    ws.frame(1 / 30)
    assert ws.wm._win_title(ws.wm._wins["calc"]) == "CALC"


def test_a_window_under_the_look_is_titled_and_drawn_whole(tmp_path):
    """The title is the app's as it names it, the line icon and the chip's
    are the set's slot for the app, and the rounded frame draws no grip
    (its corner still resizes)."""
    ws = _moy_desk(tmp_path)
    ws.open_app(ws._apps_by_id["calc"])
    ws.frame(1 / 30)
    wm = ws.wm
    win = wm._wins["calc"]
    assert wm._win_title(win) == "Calc"
    drawn = []
    real = ws.draw_icon
    ws.draw_icon = lambda kind, rect, ink, cv=None: (
        drawn.append(kind), real(kind, rect, ink, cv))[1]
    grips = []
    real_fill = wm._root_canvas.rect
    wm._win_chrome(win, True)
    wm._draw_look_chips(wm._root_canvas, ws.theme_colors, 1, wm._root_ctx.layout.line_h)
    assert drawn == ["calc", "calc"]
    gx, gy, gw, gh = wm._grip_rect(win)
    wm._root_canvas.rect = lambda *a: (grips.append(a), real_fill(*a))[1]
    try:
        wm._win_grip(win, True)
    finally:
        wm._root_canvas.rect = real_fill
    assert grips == []
    hx, hy, _hw, _hh = wm._grip_hit_rect(win)
    assert hx <= gx and hy <= gy


def test_a_chip_that_minimizes_its_window_takes_the_focus_with_it(tmp_path):
    """The chip's minimize leaves no window focused, as the title strip's
    does, so no window behind draws as the focused one and no key reaches a
    hidden window; the chip again restores and focuses it."""
    ws = _moy_desk(tmp_path)
    for app in ("files", "calc"):
        ws.open_app(ws._apps_by_id[app])
        ws.frame(1 / 30)
    wm = ws.wm
    assert wm._focus == "calc"
    wm._chip_tap("calc")
    assert wm._wins["calc"].minimized and wm._focus is None
    wm._chip_tap("calc")
    assert not wm._wins["calc"].minimized and wm._focus == "calc"
