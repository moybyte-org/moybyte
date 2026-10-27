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

import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


from ws_helpers import build_desktop_ws, select_on_home, use_theme


def _ws(tmp_path, **kw):
    """The desk on the `night` fixture (tests/fixtures/themes/), in its dark
    mode: the WM's mechanics are pinned on a palette theme with no look (the
    default's, flat, under its classic frame); a test of a look sets its
    theme."""
    ws = build_desktop_ws(tmp_path, **kw)
    use_theme(ws, "night", "dark")
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
    sc = ws.sys_canvas
    desk = bytes(sc._buf)
    ws.open()                                    # a cart, in a player WINDOW
    for _ in range(3):
        drv.frame(1 / 30)
    assert ws.cart_error is None, ws.cart_error
    win = ws.wm._wins["desktop"]
    assert win is not None and win.w < ws.sys_canvas.w

    # Sample the desk WELL clear of the window, along a row and a column the
    # bands would cross whole: the wallpaper and the icon column there are as
    # the desk drew them before the game opened (Moy's night is a black sky,
    # so a lone pixel's colour would not tell a band from the night).
    outside = [(x, win.y + win.h // 2) for x in range(0, sc.w, 3)] + \
              [(x, sc.h - 3) for x in range(0, sc.w, 3)] + \
              [(48, y) for y in range(0, sc.h, 3)]       # down the icon column
    outside = [p for p in outside if ws.wm._win_at(*p) is None]
    assert len(outside) > sc.w // 3

    def px(buf, x, y):
        i = (y * sc.w + x) * 2
        return buf[i] << 8 | buf[i + 1]

    assert any(px(desk, x, y) != px(desk, 0, sc.h - 3) for x, y in outside)
    assert all(px(sc._buf, x, y) == px(desk, x, y) for x, y in outside), \
        "the game composite letterboxed the desktop"


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


def test_editor_playtest_opens_at_2x_over_the_editor(tmp_path):
    """#178: one run() verb, two jobs, read from run()'s caller. A PLAY from
    the Editor opens at 2x centred over the Editor where the desk holds it
    (the owner's D-03.4, 2026-10-19); a desk run opens as big as the desk
    fits."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open()                       # desk run: play-sized
    drv.frame(1 / 30)
    play = ws.wm._wins["desktop"]
    play_scale = ws.wm._player_view(play)[2]
    ws._exit_to_caller()
    drv.frame(1 / 30)

    _open_editor(ws)                # Editor PLAY: the dev size
    drv.frame(1 / 30)
    ws._leave_menu()
    drv.frame(1 / 30)
    dev = ws.wm._wins["desktop"]
    ed = ws.wm._wins["menu"]
    assert ws.wm._player_view(dev)[2] == min(2, play_scale) == 2
    assert abs((dev.x + dev.w // 2) - (ed.x + ed.w // 2)) <= 1 or dev.x in (
        ws.wm._desk_rect()[0], ws.wm._desk_rect()[0] + ws.wm._desk_rect()[2] - dev.w)
    dx, dy, dw, dh = ws.wm._desk_rect()
    assert dx <= dev.x and dev.x + dev.w <= dx + dw
    assert dy <= dev.y and dev.y + dev.h <= dy + dh
    # A window the kid has since resized is never re-sized under them: the
    # intent is read ONCE, when the window is created.
    ws.wm._resize_window(dev, play.w, play.h)
    drv.frame(1 / 30)
    assert ws.wm._wins["desktop"].w == play.w


def test_dev_playtest_is_2x_where_it_fits_else_native(tmp_path):
    """The dev playtest is 2x where the desk holds it and native cart pixels
    where it does not; a play run still grows with the desktop."""
    from runtime import wm_windowed
    for i, sys_size in enumerate(((640, 480), (1024, 600), (1440, 900))):
        ws = _ws(tmp_path / str(i), sys_size=sys_size, font_scale=1)
        full = ws.wm._root_canvas
        ws.wm.set_play_intent("dev")
        dev = ws.wm._win_size("desktop", full, 1)
        ws.wm.set_play_intent("play")
        play = ws.wm._win_size("desktop", full, 1)
        assert play[0] >= dev[0]
        s = (dev[0] - 2) // ws.canvas.w
        assert s == (wm_windowed._DEV_SCALE if sys_size != (640, 480) else 1), sys_size
        assert dev == (ws.canvas.w * s + 2, ws.canvas.h * s + 2 + 18)


def test_a_native_playtest_opens_clear_of_the_editors_symbol_keys(tmp_path):
    """Where 2x does not fit, the playtest opens at native size at the
    Editor's top right, clear of the symbol keys along its foot (D-03.4)."""
    ws = _ws(tmp_path, sys_size=(640, 480), font_scale=1)
    drv = _drv(ws)
    _open_editor(ws)
    drv.frame(1 / 30)
    ws._leave_menu()
    drv.frame(1 / 30)
    dev = ws.wm._wins["desktop"]
    ed = ws.wm._wins["menu"]
    assert ws.wm._player_view(dev)[2] == 1
    assert dev.y < ed.y + ed.h // 2


@pytest.mark.parametrize("dev", [True, False], ids=["playtest", "desk-run"])
def test_a_game_window_holds_its_game_whole_over_rounded_corners(tmp_path, dev):
    """Under a depth with rounded corners the frame takes rows under the
    content (the foot): the game window is sized to the game PLUS them, so
    the game's rows are all inside its content rect -- none under the title
    strip, none under the foot -- at its scale."""
    ws = _moy_desk(tmp_path)
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path"))
    drv = _drv(ws)
    if dev:
        _open_editor(ws)
        drv.frame(1 / 30)
        ws._leave_menu()                          # PLAY from the Editor
    else:
        ws.open()
    drv.frame(1 / 30)
    wm = ws.wm
    win = wm._wins["desktop"]
    assert win.foot > 0
    cx, cy, cw, ch = win.content_rect()
    ox, oy, s = wm._player_view(win)
    assert s >= 1
    assert (ox, oy) == (cx, cy)
    assert (cw, ch) == (ws.canvas.w * s, ws.canvas.h * s)


_TRIM_CART = {
    "manifest.json": ('{"format": "moy-1", "title": "Trim Fill", "version": 1,'
                      ' "main": "main.lua", "runtime": "lua", "fps": 30,'
                      ' "canvas": "128x128", "input": ["buttons"]}'),
    # the p8 importer's 128x120 trim (p8_writer.P8_CROP), every pixel drawn
    "main.lua": "view(128, 120)\nfunction _draw() cls(8) end\n",
}


def _trim_desk(tmp_path):
    ws = _moy_desk(tmp_path)
    root = Path(ws.carts_root) / "trim_fill.moy"
    root.mkdir()
    for name, text in _TRIM_CART.items():
        (root / name).write_text(text)
    ws.carts.rescan()
    assert select_on_home(ws, "Trim Fill") is not None
    return ws


def _settle(ws, n=4):
    """`n` frames with no overlay over the game: no FPS chip, no toast (the
    first open's achievement)."""
    ws.show_fps = False
    for _ in range(n):
        ws._toast_until = 0
        ws.frame(1 / 30)


def _nothing_beneath_shows(ws, win):
    """Every pixel inside the player window's ring, from the title strip's
    foot to the frame's foot, is the window's own: the game, the bezel a
    content rect larger than the game takes, or the window's body (the
    matte at the sides, the colour its foot is painted)."""
    wm = ws.wm
    sc = wm._root_canvas
    cx, cy, cw, ch = win.content_rect()
    ox, oy, s = wm._player_view(win)
    view = wm._view_src() or (0, 0, ws.canvas.w, ws.canvas.h)
    game = _px(ws.canvas, view[0] + view[2] // 2, view[1] + view[3] // 2)
    body = _px(sc, win.x + win.w // 2, cy + ch + max(1, win.foot // 2))
    bezel = _px(sc, cx, cy) if (ox, oy) != (cx, cy) else game
    e = wm._frame_edge()
    x0, x1 = win.x + 1 + e, win.x + win.w - 1 - e
    bad = []
    for y in range(cy, cy + ch):
        for x in range(x0, x1):
            p = _px(sc, x, y)
            if ox <= x < ox + view[2] * s and oy <= y < oy + view[3] * s:
                want = (game,)
            elif cx <= x < cx + cw:
                want = (bezel,)
            else:
                want = (body,)
            if p not in want:
                bad.append((x, y, hex(p)))
    return bad


@pytest.mark.parametrize("size", ["play", "dev", "resized"])
def test_a_trimmed_game_window_shows_nothing_beneath(tmp_path, size):
    """celeste's 128x120 trim in a game window at every size the WM gives
    one: the game covers its content rect and the matte round it is the
    window's body, so no pixel of the desk shows through at its sides (the
    owner's "celeste is transparent on the borders", 2026-10-20)."""
    ws = _trim_desk(tmp_path)
    ws.open()
    _settle(ws)
    wm = ws.wm
    win = wm._wins["desktop"]
    assert ws.cart.get("title") == "Trim Fill"
    if size == "dev":
        wm.set_play_intent("dev")
        wm._resize_window(win, *wm._win_size("desktop", wm._root_canvas, wm._fs()))
    elif size == "resized":
        wm._resize_window(win, win.w + 37, win.h - 23)
    _settle(ws)
    assert win.foot > 0 and win.edge >= win.foot     # the matte this pins
    bad = _nothing_beneath_shows(ws, win)
    assert not bad, "%d pixels of what is beneath show, first %r" % (len(bad), bad[:4])


def test_a_trimmed_game_fullscreen_covers_its_rect(tmp_path):
    """The same cart in console mode: the scaled game covers its rect, and
    every pixel round it is the letterbox bezel."""
    ws = _trim_desk(tmp_path)
    ws.wm.enter_console()
    _settle(ws, 2)
    assert select_on_home(ws, "Trim Fill") is not None
    ws.open()
    _settle(ws)
    wm = ws.wm
    assert wm.mode == "console" and not wm._wins
    sc = wm._root_canvas
    ox, oy, s = wm.viewport()
    view = wm._view_src() or (0, 0, ws.canvas.w, ws.canvas.h)
    game = _px(ws.canvas, view[0] + view[2] // 2, view[1] + view[3] // 2)
    bezel = _px(sc, 0, 0)
    assert bezel != game
    gw, gh = view[2] * s, view[3] * s
    bad = [(x, y) for y in range(sc.h) for x in range(sc.w)
           if _px(sc, x, y) != (game if ox <= x < ox + gw and oy <= y < oy + gh
                                 else bezel)]
    assert not bad, "%d pixels off, first %r" % (len(bad), bad[:4])


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
    # draw/tap path uses (editor_app._zone_parts + _shelf_rects), so this
    # test tracks the layout instead of a hardcoded stride. The app's bar row
    # sits below the WM title strip.
    zone = win.ctx.layout.zone_left
    tabs_area, _play = ws.editor_app._zone_parts(zone)
    rects = dict((tid, r) for tid, r, _l in ws.editor_app._shelf_rects(tabs_area))
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
    # The click raised the editor over the playtest, which it now covers;
    # refocusing the playtest from its chip raises it back and restores its
    # pointer.
    assert ws.wm._order[-1] == "menu"
    assert ws.wm._win_at(player.x + 40, player.y + 4) == "menu"
    x, y, w, h = next(r for k, r, _ in ws.wm._chip_rects() if k == "desktop")
    drv.touch(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.wm._order[-1] == "desktop"
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
    lay = win.ctx.code_layout                     # less the line-number gutter
    assert ws.editor.COLS == lay.cols - ws.code_layer._gutter_cols(ws.editor)
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
    """Every window carries minimize + maximize + close, the player window's
    too (one control set on every frame, as every desktop has it)."""
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
    assert names == ["close", "max", "min"]


def test_a_minimised_game_is_held_and_its_chip_gives_it_back(tmp_path):
    """Minimising the player window holds the run (no tick while hidden, as
    the pause menu holds it); a restore lands on the pause menu (D-25), whose
    Resume gives the run back, and the held chip's icon is the pause glyph."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_desk()
    ws.open()
    drv.frame(1 / 30)
    win = ws.wm._wins["desktop"]
    mn = dict(ws.wm._strip_buttons(win))["min"]
    drv.touch(mn[0] + 2, mn[1] + 2)
    drv.frame(1 / 30)
    assert win.minimized and ws.player.paused and win.held
    ws.wm._minimize(win, False)
    assert not win.minimized and ws.player.paused and ws.pause.open
    ws.pause.close()
    assert not ws.player.paused
    # A run the pause menu holds stays held through a minimise and restore.
    ws.player.pause()
    ws.wm._minimize(win, True)
    ws.wm._minimize(win, False)
    assert ws.player.paused
    ws.player.resume()


@pytest.mark.parametrize("intent", ["dev", "play"])
def test_the_game_window_fits_the_game_and_its_pause_card_centres_on_it(tmp_path, intent):
    """The player window's content is the game, inset from its frame as far
    at the sides as above the rounded foot (design review R4-25), and the
    pause card over a restored game centres on it (R4-06)."""
    ws = _ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant="dark")
    drv = _drv(ws)
    ws.open_desk()
    ws.wm.set_play_intent(intent)
    ws.open()
    drv.frame(1 / 30)
    win = ws.wm._wins["desktop"]
    cx, cy, cw, ch = win.content_rect()
    ox, oy, scale = ws.wm._player_view(win)
    gw, gh = ws.canvas.w * scale, ws.canvas.h * scale
    assert (cx, cy, cw, ch) == (ox, oy, gw, gh), "the content rect is the game's"
    left = cx - win.x
    right = win.x + win.w - (cx + cw)
    foot = win.y + win.h - (cy + ch)
    assert left == right == foot, (left, right, foot)
    ws.wm._minimize(win, True)
    ws.wm._minimize(win, False)
    assert ws.pause.open
    for _ in range(3):
        drv.frame(1 / 30)
    bx, by, bw, bh = ws.pause._host.box("Pause")
    assert abs((bx + bw / 2) - (ox + gw / 2)) <= 1
    assert abs((by + bh / 2) - (oy + gh / 2)) <= 1
    ws.pause.close()


def test_a_held_games_chip_icon_is_the_pause_glyph(tmp_path, monkeypatch):
    """The state is the icon (design review R4-12): a held game's chip
    draws the pause glyph in its icon's place, and no badge over an icon."""
    ws = _ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant="light")
    drv = _drv(ws)
    ws.open_desk()
    ws.open()
    drv.frame(1 / 30)
    win = ws.wm._wins["desktop"]
    ws.wm._minimize(win, True)
    assert win.held
    glyphs, icons = [], []
    monkeypatch.setattr(type(ws.wm), "_pause_glyph",
                        staticmethod(lambda sc, slot, ink: glyphs.append(slot)))
    draw_icon = ws.draw_icon
    monkeypatch.setattr(ws, "draw_icon",
                        lambda kind, *a, **k: icons.append(kind) or draw_icon(kind, *a, **k))
    ws.wm._draw_look_chips(ws.sys_canvas, ws.theme_colors, ws.layout.fs, 8)
    assert len(glyphs) == 1
    assert "run" not in icons, "the held chip also drew the game's icon"
    ws.wm._minimize(win, False)
    ws.pause.close()


def test_maximize_toggle_fills_the_glass_and_restores(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    win = ws.wm._wins["settings"]
    orig = (win.x, win.y, win.w, win.h)
    mx = dict(ws.wm._strip_buttons(win))["max"]
    drv.touch(mx[0] + 2, mx[1] + 2)
    drv.frame(1 / 30)
    assert (win.x, win.y, win.w, win.h) == (0, 0, 1024, 600)   # full screen
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


# -- the z-order: a window behind comes to the front -----------------------------

def _two_apps(tmp_path):
    """Files, then Calc opened above it and lying over its right half."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_desk()
    drv.frame(1 / 30)
    ws.open_app(ws._apps_by_id["files"])
    drv.frame(1 / 30)
    ws.open_app(ws._apps_by_id["calc"])
    drv.frame(1 / 30)
    files, calc = ws.wm._wins["files"], ws.wm._wins["calc"]
    ws.wm._move_window(calc, files.x + files.w // 2, files.y + 60)
    _quiesce(ws)
    for _ in range(3):
        ws._dirty = True
        drv.frame(1 / 30)
    assert ws.wm._order == ["files", "calc"]
    return ws, drv, files, calc


def _open_point(ws, win, key, strip=False):
    """A point of `win` (of its title strip, with `strip`) no window covers
    and no window's resize zone holds."""
    ys = [win.y + 3] if strip else range(win.y + 2, win.y + win.h - 2, 6)
    return next((x, y) for y in ys for x in range(win.x + 2, win.x + win.w - 2, 6)
                if ws.wm._win_at(x, y) == key and ws.wm._resize_zone_at(x, y) is None)


def _shows_content(ws, win, rect):
    """Whether the glass shows `win`'s content buffer 1:1 over `rect`."""
    root = ws.sys_canvas
    rb, wb = bytes(root._buf), bytes(win.buf._buf)
    cx, cy, cw, _ch = win.content_rect()
    x, y, w, h = rect
    for Y in range(y, y + h):
        a = (Y * root.w + x) * 2
        b = ((Y - cy) * cw + x - cx) * 2
        if rb[a:a + 2 * w] != wb[b:b + 2 * w]:
            return False
    return True


def test_a_click_on_a_window_behind_brings_it_to_the_front(tmp_path):
    """Clicking a window behind another focuses it AND raises it, as on
    every desktop: it paints over the window that covered it, and nothing
    closes."""
    ws, drv, files, calc = _two_apps(tmp_path)
    cx, cy, cw, ch = files.content_rect()
    band = (calc.x + 8, cy + 80, cx + cw - calc.x - 16, 60)   # Files' content, under Calc
    assert ws.wm._win_at(band[0], band[1]) == "calc"
    assert not _shows_content(ws, files, band)
    kinds = ws.wm.stack.kinds()
    x, y = _open_point(ws, files, "files")
    drv.touch(x, y)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.wm._order == ["calc", "files"] and ws.wm._focus == "files"
    assert ws.wm.stack.kinds() == kinds                # nothing closed
    assert ws.wm._win_at(band[0], band[1]) == "files"
    assert _shows_content(ws, files, band)             # painted on top
    x, y = _open_point(ws, calc, "calc")               # and back again
    drv.touch(x, y)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.wm._order == ["files", "calc"] and ws.wm._focus == "calc"
    assert ws.wm._win_at(band[0], band[1]) == "calc"


def test_a_drag_of_a_window_behind_raises_it_and_moves_it(tmp_path):
    ws, drv, files, calc = _two_apps(tmp_path)
    x0, y0 = files.x, files.y
    x, y = _open_point(ws, files, "files", strip=True)
    drv.touch(x, y)
    drv.frame(1 / 30)
    assert ws.wm._order[-1] == "files"
    drv.touch_drag(x + 60, y + 30)
    drv.frame(1 / 30)
    drv.touch_drag(x + 61, y + 31)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert (files.x, files.y) == (x0 + 61, y0 + 31)
    assert ws.wm._order == ["calc", "files"] and ws.wm._focus == "files"


def test_the_chips_keep_the_order_the_windows_opened(tmp_path):
    ws, drv, files, calc = _two_apps(tmp_path)
    ws.wm._chip_tap("files")                           # a chip raises too
    drv.frame(1 / 30)
    assert ws.wm._order[-1] == "files"
    assert [k for k, _r, _l in ws.wm._chip_rects()] == ["files", "calc"]


def test_opening_an_open_app_brings_its_window_forward_as_it_was(tmp_path):
    """The desk's launcher opens an app that has a window by bringing that
    window forward: the windows above it stay open (a goto would have
    returned to it, closing them) and the app keeps what it showed."""
    ws, drv, files, calc = _two_apps(tmp_path)
    app = ws._apps_by_id["calc"]
    app = getattr(app, "_inst", app)
    for k in "12+3":
        app._key(k)
    entry = app.entry
    ws.wm._chip_tap("files")
    drv.frame(1 / 30)
    kinds = ws.wm.stack.kinds()
    ws.open_app(ws._apps_by_id["calc"])
    drv.frame(1 / 30)
    assert ws.wm.stack.kinds() == kinds
    assert ws.wm._order == ["files", "calc"] and ws.wm._focus == "calc"
    assert app.entry == entry
    calc.minimized = True                              # a minimized one is shown
    ws.wm._move_focus("files")
    ws.open_app(ws._apps_by_id["calc"])
    drv.frame(1 / 30)
    assert not calc.minimized and ws.wm._order[-1] == "calc"


def test_a_window_over_the_game_takes_the_game_off_the_quiet_stack(tmp_path):
    """The quiet stack repaints the game window alone; a window over it (its
    shadow included) would be painted over, so while one is, every frame is
    a full paint."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open()
    drv.frame(1 / 30)
    ws.open_settings()
    drv.frame(1 / 30)
    _quiesce(ws)
    wm = ws.wm
    game, st = wm._wins["desktop"], wm._wins["settings"]
    ring = _Ring(ws, 1)
    wm._move_window(st, game.x + 40, game.y + 40)
    ws._dirty = True
    assert "quiet" not in ring.settle(drv, 6)
    wm._move_window(st, game.x + game.w + 60, st.y)
    ws._dirty = True
    if wm._covers_player():
        pytest.skip("no room on this desk beside the game window")
    assert ring.settle(drv, 6)[-1] == "quiet"


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
    """A taskbar chip carried its title's first eight letters ("Appearan")
    with the bar half empty. It carries the whole title, and only a bar out
    of room shortens the longest first, every chip inside the cell."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_desk()
    for app in ("files", "appearance", "calc"):
        ws.open_app(ws._apps_by_id[app])
        drv.frame(1 / 30)
    labels = {k: lb for k, _r, lb in ws.wm._chip_rects()}
    assert labels["appearance"] == "Appearance" and labels["files"] == "Files"
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


def _game_and_settings(tmp_path):
    """A desk with a running game's window and Settings' beside it, Settings
    focused on its Name field with one letter typed."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open()
    drv.frame(1 / 30)
    ws.open_settings()
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    sl = ws.settings_layer
    sl.set_msel = sl.row_index("name")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.wm._focus == "settings" and sl._focused == "Name"
    assert ws.input.text_mode
    drv.type_char(ord("K"))
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    return ws, drv


def _click_window(ws, drv, key):
    """Click a point of window `key`'s title strip that no other window covers."""
    win = ws.wm._wins[key]
    for x in range(win.x + 2, win.x + win.w - 2, 4):
        y = win.y + 3
        if ws.wm._win_at(x, y) == key:
            drv.touch(x, y)
            drv.frame(1 / 30)
            drv.touch_up()
            drv.frame(1 / 30)
            return
    raise AssertionError("no uncovered point on %s's strip" % key)


def _stored_name(tmp_path):
    from runtime import moy_carts
    return moy_carts.load_system(str(tmp_path / "carts")).get("name")


def test_text_mode_follows_the_focused_window(tmp_path):
    """The keyboard is in text mode only while the focused window's focused
    stop is a text field: Settings' Name field keeps it only while Settings'
    window has focus. Focus moving to the game's window puts the keyboard
    back in button mode (and stores the Name typed); focus coming back to
    Settings, still on the field, puts it in text mode again."""
    ws, drv = _game_and_settings(tmp_path)
    ws.wm._move_window(ws.wm._wins["settings"], 300, 200)
    drv.frame(1 / 30)
    _click_window(ws, drv, "desktop")
    assert ws.wm._focus == "desktop"
    assert not ws.input.text_mode, "the game's window has focus: button mode"
    assert _stored_name(tmp_path) == "K", "focus leaving Settings stores the Name"
    assert ws.wm.stack.top() == "settings", "a click pops nothing"
    _click_window(ws, drv, "settings")
    assert ws.wm._focus == "settings"
    assert ws.settings_layer._focused == "Name"
    assert ws.input.text_mode, "Settings' field has focus again: text mode"
    ws.wm._chip_tap("desktop")                 # a chip moves focus the same way
    assert ws.wm._focus == "desktop" and not ws.input.text_mode
    ws.wm._chip_tap("settings")
    assert ws.wm._focus == "settings" and ws.input.text_mode


def test_a_cart_keeps_its_own_text_mode_across_focus(tmp_path):
    """A running cart's own textmode(True) is its window's: focus going to
    Settings (no field focused) is button mode, and back to the cart is its
    text mode again."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open()
    drv.frame(1 / 30)
    ws.input.text_mode = True                  # what the cart's textmode(True) writes
    ws.open_settings()
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert not ws.input.text_mode
    ws.wm._move_window(ws.wm._wins["settings"], 300, 200)
    drv.frame(1 / 30)
    _click_window(ws, drv, "desktop")
    assert ws.input.text_mode
    _click_window(ws, drv, "settings")
    assert not ws.input.text_mode


def test_closing_the_settings_window_stores_a_name_being_typed(tmp_path):
    """The strip's X is a way out like any other: the Name typed is stored,
    and the window it reveals gets its own button mode back."""
    ws, drv = _game_and_settings(tmp_path)
    ws.wm.close_window_kind("settings")
    assert _stored_name(tmp_path) == "K"
    drv.frame(1 / 30)
    assert ws.wm._focus == "desktop"
    assert not ws.input.text_mode


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
    """Closing the playtest (X / the pause menu's Quit -> _exit_to_caller) removes ONLY
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
    ws._exit_to_caller()                        # the pause menu's Quit path
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
    persists to system.json; the default is Moy in its Day mode."""
    ws = build_desktop_ws(tmp_path)               # aliases the bare-name modules
    from runtime.chrome import THEME_NAMES, live_colors
    drv = _drv(ws)
    assert (ws.look.theme_name, ws.look.theme_variant) == ("moy", "light")
    assert ws.theme_colors == live_colors("moy", "light").tokens
    ws.look.cycle_theme(1)
    assert ws.look.theme_name == THEME_NAMES[1]           # "blocks"
    assert ws.theme_colors != live_colors("moy", "light").tokens
    assert ws.launcher.theme is ws.theme_colors    # accents follow
    assert ws.system.get("theme") == ws.look.theme_name  # persisted
    # A themed frame renders (window strip uses the new title token).
    ws.open_settings()
    drv.frame(1 / 30)
    # And an unknown persisted name falls back to the default.
    ws.look.set_theme("nonsense", persist=False)
    assert ws.look.theme_name == "moy"


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
    # Shrunk, so the rubber rect stays on the glass: the window opens in
    # the work area right of the icon column, near the desk's right edge.
    win, cw, ch = _engage_resize(ws, drv, dx=-60)
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
    assert calls[0] == 0                   # the release is the WM's too
    cx, cy, cw, ch = win.content_rect()
    drv.touch(cx + cw // 2, cy + ch // 2)
    drv.frame(1 / 30)
    assert calls[0] > 0                    # a press in it renders live again


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
        # The clock string changes, as at a minute boundary.
        ws.bar_layer._clock_text = lambda: "12:34"
        for _ in range(4):
            ws.mark_dirty()
            drv.frame(1 / 30)
    finally:
        backdrop._draw_desktop = real
        ws.bar_layer.redraw_clock = real_clock
        del ws.bar_layer._clock_text
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
def test_a_shelf_tap_marks_dirty_inside_the_projects_window(tmp_path):
    """#177: a selection that moves WITHOUT marking dirty never repaints
    inside a window -- the content freeze reuses the retained buffer (state
    moved, pixels didn't: dead on desktop + web). A tap on a shelf card of
    the PROJECTS window marks the frame dirty and lands on that card."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_projects()
    drv.frame(1 / 30)
    win = ws.wm._wins["projects"]
    p = ws.pointer
    p.visible = True
    ws.wm._install(win.ctx)
    try:
        t2 = ws.projects_grid.tile_rect(2)
    finally:
        ws.wm._install(ws.wm._root_ctx)
    cx, cy, _cw, _ch = win.content_rect()
    tx, ty = cx + t2[0] + 8, cy + t2[1] + 8
    p.x, p.y, p.down = tx, ty, True
    ws.wm._route_pointer(tx, ty, True)
    ws._dirty = False
    p.down = False
    ws.wm._route_pointer(tx, ty, False)
    assert ws._dirty, "a shelf tap must mark dirty or the window never repaints"
    for _ in range(30):                            # the glide settles on it
        drv.frame(1 / 30)
    assert ws.projects_grid.sel == 2


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
# and once after Calc is reopened: its window comes forward, Files open
# behind it. Each app lays itself out and draws
# from its OWN grant's surface row (native/moy_app), which the WM writes as it
# installs each window's layout context: a row the console shared, or one read
# from the last window installed, lays an app out at the other window's size.
TWO_WINDOWS = (
    "7a61927a38f73949447cc95dad3a78706cdbe5c9db6d093f555207607cec0a5e",
    "cfdd23f9eee9b0fa633d9e1d56d5e8571065c9864ccd49e84d0bf1c3046056e6",
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
    assert seen == [], "the focused window too is stamped, not drawn"
    drv.press("down")                            # a key it takes: it renders
    drv.frame(1 / 30)
    assert seen == ["menu"]


def test_the_x_back_is_a_full_repaint_in_every_buffer(tmp_path):
    """The home's X on a triple-buffered root: the windows come back
    stamped, the focused one's content held (§14.1), and every buffer
    equals a full repaint of the desk they make."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _desk_with_three(ws, drv)
    ring = _Ring(ws, 3)
    for _ in range(3):
        ws._dirty = True
        ring.frame(drv)
    ws.open_library()
    ring.settle(drv, 4)
    ws.open_desk()
    ring.settle(drv, 4)
    seen = list(ring.bufs)
    assert seen == ring.full(drv)


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
    """The Make tab and CHANGE open the Editor in a desk window, console
    mode giving way to the desk; B on the home leaves; a game's tap stays
    in console mode."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_library()
    ws.set_home_tab("make")
    ws.launcher.sel = 1                          # a project: Make's tap edits
    ws.launch_selected()
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop" and ws.wm.top_is("menu")
    assert ws.wm._order == ["menu"]
    ws.wm.close_window_kind("menu")
    ws.open_library()
    ws.set_home_tab("games")
    ws.launcher.sel = 1
    ws.change_selected()
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop" and ws.wm._order == ["menu"]
    ws.wm.close_window_kind("menu")
    ws.open_library()
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
                           "quiet" if st is wm._quiet_stack else
                           "repeat" if st is wm._repeat_stack else "full")
        self.bufs[self.k] = bytes(self.cv._buf)
        self.cv._comp.last = self.bufs[self.k] if self.n > 1 else None
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
    use_theme(ws, "night", "dark")
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
        # hangs from the top bar, a component's card the popover's gap below it
        assert y == ws.layout.bars[0][1][3] + (0 if name == "menu" else 8)
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


# Under the look (every theme's since docs/look_2026-10.md step 33) the
# riding popover's rounded card re-blends its corners over its own last
# frame, and the system menu's card over a playtest takes five full frames
# before its quiet ones: a ridden frame no longer equals a full repaint.
# Both nets held only while they ran on a theme with no look.
_LOOK_RIDE = pytest.mark.xfail(strict=True, reason="the look's popover ride differs "
                               "from a full repaint (docs/look_2026-10.md step 33)")


def test_a_component_popovers_card_repaints_over_what_it_covers(tmp_path):
    """A bar widget's popover (a component card under the look) that
    changes while it rides repaints over the snapshot of what it covers,
    never over its own last paint, whose corners and shadow it would blend
    again: after a move every buffer's card is a full repaint's."""
    ws, drv = _popover_desk(tmp_path)
    ring = _Ring(ws, 3)
    for _ in range(3):
        ws._dirty = True
        ring.frame(drv)
    ring.settle(drv)
    ws.widget_tap("clock")
    ring.settle(drv)
    ws._sysmenu_layer.handle_input(_Key("right"))
    assert ring.settle(drv) == ["pop"] * 3
    x, y, w, h = ws.sysmenu.panel_rect()
    W = ws.sys_canvas.w

    def card(buf):
        return [buf[2 * (r * W + x):2 * (r * W + x + w)] for r in range(y, y + h)]
    seen = [card(b) for b in ring.bufs]
    assert seen == [card(b) for b in ring.full(drv)]


@_LOOK_RIDE
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


def _differ(a, b):
    """The pixels (as 16-bit offsets) where two buffers differ."""
    return {i for i in range(0, len(a), 2) if a[i:i + 2] != b[i:i + 2]}


@_LOOK_RIDE
def test_the_system_menu_over_a_playtest_keeps_the_quiet_stack(tmp_path):
    """The system menu's scrim (D-01) over a running game: once the frame it
    came on is in every buffer, the game's frames are quiet ones -- its own
    pixels composited and washed, the menu's face put back over them -- and
    every buffer equals a full repaint of the same state, as a quiet frame
    without the menu does."""
    ws, drv = _popover_desk(tmp_path, game=True)
    player = ws._content_layers["desktop"]
    ticks = [0]

    def still_game(dt):                  # the same frame every tick
        ticks[0] += 1
        ws.canvas.cls(8)
        ws.canvas.rect(10, 10, 40, 30, 12)

    player.draw = still_game
    ring = _Ring(ws, 3)
    assert set(ring.settle(drv, 4)) == {"quiet"}
    # what a quiet frame leaves unlike a full one without the menu: the
    # window's corner over the game's last row
    owed = _differ(ring.bufs[0], ring.full(drv)[0])
    ring.settle(drv, 4)
    ws.widget_tap("menu")
    assert ws.menu_ui.scrims()
    assert ring.settle(drv, 6)[-3:] == ["qpop"] * 3
    assert ws.wm._pop_over_game, "the menu's card is not over the game"
    n = ticks[0]
    seen = list(ring.bufs)
    full = ring.full(drv)
    assert ticks[0] > n
    for a, b in zip(seen, full):
        assert _differ(a, b) == owed, "the quiet frames wash the game once"
    ws.widget_tap("menu")
    assert not ws.menu_ui.scrims()
    ring.settle(drv, 4)
    assert ring.settle(drv, 3) == ["quiet"] * 3


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
    assert labels["menu"] == "Editor"                # the chip names the app alone
    # Day: on this undeclared glass (the handheld class at font scale 2) the
    # chips' Day Sans cuts a name to what fits; the chip's label is pinned
    ws.look.set_theme("moy", persist=False, variant="light")
    drv.frame(1 / 30)
    labels = {k: lb for k, _r, lb in ws.wm._chip_rects()}
    assert "Editor".startswith(labels["menu"])
    assert cart.startswith(labels["desktop"])      # the playtest's chip names the cart (D-23)


def test_while_a_playtest_runs_play_reads_stop_and_restart(tmp_path):
    """D-23 (2026-10-19): with the playtest's window open, the Editor's Play
    is Restart and Stop; Stop closes the window, and Play is back."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_editor(ws)
    drv.frame(1 / 30)
    app = ws.editor_app
    assert not app.playing()
    ws._leave_menu()
    drv.frame(1 / 30)
    assert app.playing()
    zone = ws.bar_layer._zone_rect("menu")
    tabs_area, play_r = app._zone_parts(zone)
    restart_r, stop_r = app._run_rects(play_r)
    assert app.zone_tap(stop_r[0] + stop_r[2] // 2, stop_r[1] + stop_r[3] // 2, zone)
    drv.frame(1 / 30)
    assert "desktop" not in ws.wm._wins and not app.playing()


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
    the title its app names, 2 under its tile (nearer it than the next tile),
    on the backdrop with no plate."""
    ws = _moy_desk(tmp_path)
    rects = ws.wm._backdrop_layer._icon_rects()
    labels = [r[3] for r in rects]
    assert labels[:2] == ["Play", "Projects"]
    assert {"Paint", "Storybook", "Files", "Calc", "Get carts"} <= set(labels)
    dx, dy, _dw, _dh = ws.wm._desk_rect()
    cv = ws.sys_canvas
    for i, (_key, box, band, _label, _cart) in enumerate(rects):
        assert box == (dx + 32, dy + 18 + 74 * i, 44, 44)
        assert band[1] == box[1] + 46 and band[0] + band[2] // 2 == box[0] + 22
        # no plate: the band's edge is the backdrop either side of it
        assert _px(cv, band[0] + 1, band[1] + 1) == _px(cv, band[0] - 1, band[1] + 1)
        assert _px(cv, box[0] + 2, box[1] + 22) == 0xFFFF       # the tile's fill


def test_a_desk_tile_draws_its_apps_icon_from_the_set(tmp_path):
    """Every tile's icon is its app's slot of the theme's set (Paint's by
    its alias), at the 24-pixel size in the ink."""
    ws = _moy_desk(tmp_path)
    drawn = []
    real = ws.draw_icon
    ws.draw_icon = lambda kind, rect, ink, cv=None: (
        drawn.append((kind, rect[2], real(kind, rect, ink, cv))), drawn[-1][2])[1]
    ws.wm._backdrop_layer._draw_desk_icons()
    keys = [r[0] for r in ws.wm._backdrop_layer._icon_rects()]
    assert [d[0] for d in drawn] == keys
    assert all(size == 24 and ok for _k, size, ok in drawn)
    from runtime import iconsets
    assert iconsets._pick("default", 24)[1] == 24


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


def test_blocks_tiles_are_each_apps_colour_and_their_icons_the_tile_ink(tmp_path):
    """Blocks' `tiles` (docs/look_2026-10.md step 26b) name an app by its
    icon slot, Paint's `artwork` by `paint`; each tile is that colour inside
    the ink outline, its icon in `tile_ink`, and an app the map leaves out
    keeps the raised fill."""
    from runtime import ui_draw
    from runtime.wm_desk import _tile_fill
    ws = build_desktop_ws(tmp_path, font_scale=1, panel_diagonal_in=7.0)
    ws.look.set_theme("blocks", persist=False, variant="light")
    ws.open_desk()
    _quiesce(ws)
    ws.frame(1 / 30)
    tiles = dict(ws.look.live.look[2])
    assert _tile_fill(tiles, "artwork") == tiles["paint"]
    assert _tile_fill(tiles, "appearance") is None
    word = {k: ui_draw._C.theme_rgb(0x1000000 | int(v, 16))[0] for k, v in (
        ("play", "ff6b4a"), ("artwork", "ffd23f"), ("files", "7cc8ff"), ("calc", "ff9a62"),
        ("getcarts", "9be36b"), ("ink", "1a1530"))}
    cv = ws.sys_canvas
    seen = set()
    for key, box, _band, _label, _cart in ws.wm._backdrop_layer._icon_rects():
        if key in word:
            assert _px(cv, box[0] + 4, box[1] + 22) == word[key], key
            assert _px(cv, box[0], box[1] + 22) == word["ink"], key
            seen.add(key)
    assert seen == {"play", "artwork", "files", "calc", "getcarts"}
    inks = []
    real = ws.draw_icon
    ws.draw_icon = lambda kind, rect, ink, c=None: (inks.append(ink), real(kind, rect, ink, c))[1]
    ws.wm._backdrop_layer._draw_desk_icons()
    assert inks and set(inks) == {ws.look.live.look[0]["tile_ink"]}


def test_a_looks_label_ink_and_shadow_draw_the_desk_labels(tmp_path, monkeypatch):
    """`label.ink` and `label.shadow` (THEMES.md's launcher group) are the
    labels' over the theme's backdrop; a look that sets neither keeps the
    ink and no shadow."""
    from test_ui_style_data import _install, _uninstall
    from runtime import ui_draw
    _install({"format": 2, "name": "lb", "inherits": "moy", "look": {
        "label": {"ink": "#ff0000", "shadow": ["#000000", 0.5, 2, 1]}}})
    try:
        ws = build_desktop_ws(tmp_path, font_scale=1, panel_diagonal_in=7.0)
        ws.look.set_theme("lb", persist=False, variant="light")
        ws.open_desk()
        _quiesce(ws)
        got = []
        real = ui_draw.label
        monkeypatch.setattr(ui_draw, "label", lambda cv, r, t, ink, align=0, bg=None,
                            shadow=None: (got.append((ink, shadow)),
                                          real(cv, r, t, ink, align, bg, shadow))[1])
        ws.wm._backdrop_layer._draw_desk_icons()
        leaves = ws.look.live.look[0]
        assert got and set(got) == {(leaves["label.ink"],
                                     (leaves["label.shadow.c"], 128, 2, 1))}
    finally:
        _uninstall()


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


@pytest.mark.parametrize("held", [True, False], ids=["under-a-menu", "free"])
def test_the_root_takes_back_its_marks_after_the_windows_draw(tmp_path, held):
    """The console turns the focus marks off under a menu that holds the
    keys; a window's install sets them for its own content, and the root's
    reinstall after the windows gives back what the root had, so desk
    content drawn after them marks no second focus under the menu."""
    from runtime import ui_draw
    ws = _moy_desk(tmp_path)
    ws.open_app(ws._apps_by_id["calc"])
    ws.frame(1 / 30)
    wm = ws.wm
    win = wm._wins["calc"]
    try:
        ui_draw.marks(not held)
        wm._install(win.ctx, win)
        assert ui_draw.marks()[0] == (not wm.popover_takes_keys())
        wm._install(wm._root_ctx)
        assert ui_draw.marks()[0] == (not held)
    finally:
        ui_draw.marks(True)


@pytest.mark.parametrize("theme", ["moy", "blocks", "contrast"])
def test_calcs_keys_stand_as_far_from_the_frames_bottom_as_its_sides(tmp_path, theme):
    """A rounded frame's foot is part of the window's bottom inset, not an
    addition to the app's: Calc's keypad ends as far above the frame's
    outer bottom edge as it starts in from the frame's outer left edge, in
    every look, whatever its corner radius."""
    ws = _moy_desk(tmp_path)
    if theme != "moy":
        ws.look.set_theme(theme, persist=False, variant="light")
    ws.open_app(ws._apps_by_id["calc"])
    ws.frame(1 / 30)
    wm = ws.wm
    win = wm._wins["calc"]
    assert win.foot > 0
    cx, cy, _cw, _ch = win.content_rect()
    keys = win.ctx.app_layouts["calc"].keys
    left = cx + min(k[0] for k in keys) - win.x
    right = win.x + win.w - (cx + max(k[0] + k[2] for k in keys))
    bottom = win.y + win.h - (cy + max(k[1] + k[3] for k in keys))
    assert left == right == bottom


@pytest.mark.parametrize("theme", ["moy", "contrast"])
def test_the_focused_chip_prints_in_the_current_ink(tmp_path, theme):
    """The focused window's chip is current content: its icon and title take
    the ink the look gives that (Moy's accent, beside the raised plate;
    Contrast's `value_ink`, on its solid ink plate, D-14); the others the
    dim ink."""
    ws = _moy_desk(tmp_path)
    if theme != "moy":
        ws.look.set_theme(theme, persist=False, variant="light")
    for app in ("files", "calc"):
        ws.open_app(ws._apps_by_id[app])
        ws.frame(1 / 30)
    wm = ws.wm
    sc, th = wm._root_canvas, ws.theme_colors
    inks = {}
    real = sc.print
    sc.print = lambda text, x, y, c=None, *a: (inks.__setitem__(text, c),
                                               real(text, x, y, c, *a))[1]
    try:
        wm._draw_look_chips(sc, th, 1, wm._root_ctx.layout.line_h)
    finally:
        sc.print = real
    want = th["ink_accent"] if theme == "moy" else th["value_ink"]
    assert inks["Calc"] == want
    assert inks["Files"] == th["chrome_ink_dim"]


# -- the wallpaper's subject (docs/look_2026-10.md step 28) -------------------

def _overlaps(a, b):
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
            and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])


def test_moys_night_sleeps_clear_of_the_icon_column(tmp_path):
    """The desk hands its wallpaper the bars and the icon column as quiet
    rectangles; Moy's night, drawing per glass, puts Moy where neither is,
    and its subject is where Moy sleeps."""
    ws = _moy_desk(tmp_path)
    ws.look.select_wallpaper("moybyte.moy_night", persist=False)
    ws.wm._backdrop_layer._draw_desktop(1 / 30)
    quiet = ws.wm._backdrop_layer.quiet()
    assert quiet[:len(ws.wallpaper.bars())] == ws.wallpaper.bars()
    col = quiet[len(ws.wallpaper.bars()):]
    assert col and col[0][0] < 100                      # the column, at the left
    s = ws.wallpaper.subject
    assert s is not None and not any(_overlaps(s, q) for q in quiet)
    assert s[0] > ws.sys_canvas.w // 2                  # so Moy sleeps bottom right


def test_a_new_window_keeps_clear_of_the_wallpapers_subject(tmp_path):
    """The WM's default placement moves a window that would cover the
    subject to the nearest place beside it that holds the whole window, and
    leaves one that does not cover it, or that nothing holds, where the
    cascade put it."""
    ws = _moy_desk(tmp_path)
    wm = ws.wm
    ws.open_app(ws._apps_by_id["calc"])
    ws.frame(1 / 30)
    win = wm._wins["calc"]
    plain = (win.x, win.y, win.w, win.h)
    ws.exit()
    ws.frame(1 / 30)
    assert "calc" not in wm._wins

    # a subject square in the middle of where Calc opens
    sub = (plain[0] + plain[2] // 2 - 20, plain[1] + plain[3] // 2 - 20, 40, 40)
    ws.wallpaper.subject = sub
    ws.open_app(ws._apps_by_id["calc"])
    ws.frame(1 / 30)
    win = wm._wins["calc"]
    got = (win.x, win.y, win.w, win.h)
    dx, dy, dw, dh = wm._desk_rect()
    assert got[2:] == plain[2:]
    assert not _overlaps(got, sub)
    assert dx <= got[0] and got[0] + got[2] <= dx + dw
    assert dy <= got[1] and got[1] + got[3] <= dy + dh
    ws.exit()
    ws.frame(1 / 30)

    # a subject Calc does not cover moves nothing
    ws.wallpaper.subject = (dx + dw - 30, dy + dh - 30, 20, 20)
    ws.open_app(ws._apps_by_id["calc"])
    ws.frame(1 / 30)
    win = wm._wins["calc"]
    assert (win.x, win.y) == plain[:2]
    ws.exit()
    ws.frame(1 / 30)

    # a subject no place on the desk avoids leaves the cascade's place
    ws.wallpaper.subject = (dx, dy, dw, dh)
    ws.open_app(ws._apps_by_id["calc"])
    ws.frame(1 / 30)
    win = wm._wins["calc"]
    assert (win.x, win.y) == plain[:2]


@pytest.mark.parametrize("key", ["menu", "files", "calc", "appearance"])
def test_a_new_window_opens_clear_of_the_icon_column(tmp_path, key):
    """A new window opens in the work area right of the launchers (macOS and
    GNOME place one in the free area), never over their labels."""
    ws = _moy_desk(tmp_path)
    if key == "menu":
        ws.system.set("win_full", {"menu": False})     # restored (E-04 opens it full)
        ws.open_in_editor(next(c for c in ws.carts.all if c.get("title") == "Star Catcher"))
    elif key == "appearance":
        ws.open_app(ws.appearance_app)
    else:
        ws.open_app(ws._apps_by_id[key])
    ws.frame(1 / 30)
    win = ws.wm._wins[key]
    right = max(max(b[0] + b[2], l[0] + l[2])
                for _k, b, l, _t, _c in ws.wm._backdrop_layer._icon_rects())
    assert win.x >= right, (key, win.x, right)


# -- every edge and corner resizes; a full-screen row's drag restores --------
# (docs/shell_layout_2026-10.md §3, owner 2026-10-23)

from runtime.wm_chrome import RZ_L, RZ_R, RZ_T, RZ_B   # noqa: E402

_MASKS = (RZ_L, RZ_R, RZ_T, RZ_B, RZ_L | RZ_T, RZ_R | RZ_T,
          RZ_L | RZ_B, RZ_R | RZ_B)


def _desk(tmp_path):
    """A 7-inch desk: the touch-sized zones and full screen's first open."""
    ws = build_desktop_ws(tmp_path, font_scale=1, panel_diagonal_in=7.0)
    use_theme(ws, "night", "dark")
    return ws, _drv(ws)


def _settled(tmp_path, kind="settings"):
    ws, drv = _desk(tmp_path)
    drv.frame(1 / 30)
    ws.open_settings()
    for _ in range(4):
        drv.frame(1 / 30)
    _quiesce(ws)
    return ws, drv, ws.wm._wins[kind]


def _zone_point(ws, win, mask):
    """A point in the middle of `win`'s outside band on the edges `mask` names."""
    o, _i, _c = ws.wm._rz_band()
    x = (win.x - o // 2 if mask & RZ_L else win.x + win.w + o // 2 if mask & RZ_R
         else win.x + win.w // 2)
    y = (win.y - o // 2 if mask & RZ_T else win.y + win.h + o // 2 if mask & RZ_B
         else win.y + win.h // 2)
    return x, y


def _resize_by(drv, x, y, dx, dy, steps=4):
    drv.touch(x, y)
    drv.frame(1 / 30)
    for k in range(1, steps + 1):
        drv.touch_drag(x + dx * k // steps, y + dy * k // steps)
        drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)


@pytest.mark.parametrize("mask", _MASKS)
def test_every_edge_and_corner_resizes(tmp_path, mask):
    ws, drv, win = _settled(tmp_path)
    x0, y0, w0, h0 = win.x, win.y, win.w, win.h
    px, py = _zone_point(ws, win, mask)
    assert ws.wm._resize_zone_at(px, py) == ("settings", mask)
    dx = 20 if mask & RZ_L else -20 if mask & RZ_R else 0     # inward: no clamp
    dy = 20 if mask & RZ_T else -20 if mask & RZ_B else 0
    _resize_by(drv, px, py, dx, dy)
    assert ws.wm._resize is None
    want = (x0 + (dx if mask & RZ_L else 0), y0 + (dy if mask & RZ_T else 0),
            w0 - abs(dx), h0 - abs(dy))
    assert (win.x, win.y, win.w, win.h) == want
    assert (win.buf.w, win.buf.h) == win.content_rect()[2:]   # relaid out


def test_the_strip_drags_and_the_band_above_it_resizes(tmp_path):
    ws, drv, win = _settled(tmp_path)
    assert ws.wm._resize_zone_at(win.x + win.w // 2, win.y + 2) is None
    assert ws.wm._resize_zone_at(win.x + win.w // 2, win.y - 2) == ("settings", RZ_T)


def test_a_resize_stops_at_the_minimum_and_at_the_desk(tmp_path):
    ws, drv, win = _settled(tmp_path)
    wm = ws.wm
    dx, dy, dw, dh = wm._desk_rect()
    min_w, min_h = wm._min_size(win, dw, dh)
    right, bottom = win.x + win.w, win.y + win.h
    _resize_by(drv, *_zone_point(ws, win, RZ_L), 2000, 0)
    assert (win.w, win.x + win.w) == (min_w, right)       # the right edge stays
    _resize_by(drv, *_zone_point(ws, win, RZ_T), 0, 2000)
    assert (win.h, win.y + win.h) == (min_h, bottom)
    # pressed just inside the frame, so the edge would pass the glass's
    _resize_by(drv, win.x + 1, win.y - 1, -2000, -2000)
    assert (win.x, win.y) == (dx, dy)                     # the desk's edges
    assert (win.x + win.w, win.y + win.h) == (right, bottom)
    _resize_by(drv, win.x + win.w - 1, win.y + win.h - 1, 2000, 2000)
    assert (win.x + win.w, win.y + win.h) == (dx + dw, dy + dh)


def test_a_resize_keeps_the_content_frozen_and_relays_it_out_once(tmp_path):
    ws, drv, win = _settled(tmp_path)
    calls = [0]
    layer = ws.wm._content_for("settings")
    orig = layer.draw
    layer.draw = lambda dt: (calls.__setitem__(0, calls[0] + 1), orig(dt))[1]
    px, py = _zone_point(ws, win, RZ_L | RZ_B)
    drv.touch(px, py)
    drv.frame(1 / 30)
    calls[0] = 0
    for k in range(1, 5):
        drv.touch_drag(px + 8 * k, py - 8 * k)
        drv.frame(1 / 30)
    assert calls[0] == 0, "the content re-rendered mid-resize"
    drv.touch_up()
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert calls[0] == 1, "the release relays the content out once"


def test_the_pointer_shows_the_resize_it_would_start(tmp_path):
    ws, drv, win = _settled(tmp_path)
    p = ws.pointer
    for mask, shape in ((RZ_R, "ew"), (RZ_B, "ns"), (RZ_L | RZ_T, "nwse"),
                        (RZ_R | RZ_T, "nesw")):
        p.x, p.y = _zone_point(ws, win, mask)
        assert ws.wm.cursor_shape() == shape
    p.x, p.y = win.x + win.w // 2, win.y + win.h // 2
    assert ws.wm.cursor_shape() is None


def _drag_row(drv, x, y, tx, ty, steps=4):
    drv.touch(x, y)
    drv.frame(1 / 30)
    for k in range(1, steps + 1):
        drv.touch_drag(x + (tx - x) * k // steps, y + (ty - y) * k // steps)
        drv.frame(1 / 30)


def test_a_drag_from_a_full_windows_row_restores_it_under_the_finger(tmp_path):
    ws, drv, win = _settled(tmp_path)
    wm = ws.wm
    w0, h0 = win.w, win.h
    wm._toggle_max(win)
    for _ in range(3):
        drv.frame(1 / 30)
    assert win.saved is not None
    fw, row = win.w, wm._row_h()
    x, y = 40, row // 2
    assert wm._on_row_head(win, x, y)
    _drag_row(drv, x, y, 300, 200)
    assert win.saved is None and (win.w, win.h) == (w0, h0)
    assert wm._drag is not None and wm._drag[0] == "settings"
    # the finger holds the same point across the title, as far down the strip
    assert 300 - win.x == x * w0 // fw
    assert 200 - win.y == y * win.title_h // row
    drv.touch_up()
    drv.frame(1 / 30)
    assert wm._drag is None and win.saved is None
    assert ws.system.get("win_full", {}).get("settings") is False


def test_a_window_that_opened_full_restores_at_its_designed_size(tmp_path):
    ws, drv = _desk(tmp_path)
    drv.frame(1 / 30)
    ws.system.set("win_full", {"settings": True})
    ws.open_settings()
    for _ in range(4):
        drv.frame(1 / 30)
    win = ws.wm._wins["settings"]
    assert win.saved is not None
    _sx, _sy, w, h = win.saved                          # the size it opened at
    _drag_row(drv, 40, ws.wm._row_h() // 2, 400, 300)
    assert win.saved is None and (win.w, win.h) == (w, h)
    drv.touch_up()
    drv.frame(1 / 30)


def test_a_bar_is_never_a_resize_zone(tmp_path):
    ws, drv, win = _settled(tmp_path)
    wm = ws.wm
    dx, dy, _dw, _dh = wm._desk_rect()
    wm._move_window(win, win.x, dy)                    # right under the top bar
    assert wm._resize_zone_at(win.x + win.w // 2, dy - 2) is None


@pytest.mark.parametrize("how", ["restore", "left", "top_right"])
def test_after_a_restore_or_a_resize_the_glass_is_a_full_repaint(tmp_path, how):
    ws, drv, win = _settled(tmp_path)
    if how == "restore":
        ws.wm._toggle_max(win)
        for _ in range(3):
            drv.frame(1 / 30)
        _drag_row(drv, 40, ws.wm._row_h() // 2, 300, 200)
        drv.touch_up()
        drv.frame(1 / 30)
    else:
        mask = RZ_L if how == "left" else RZ_R | RZ_T
        _resize_by(drv, *_zone_point(ws, win, mask), 30 if how == "left" else -40,
                   0 if how == "left" else 40)
    _quiesce(ws)
    for _ in range(3):
        drv.frame(1 / 30)
    ring = _Ring(ws, 1)
    seen = bytes(ws.sys_canvas._buf)
    assert seen == ring.full(drv)[0]


def test_a_restoring_drag_clears_what_the_full_screen_window_covered(tmp_path):
    """Mid-drag, each frame shows the desk wherever the restored window is
    not, in every buffer it lands in: the full-screen window's pixels go
    (glass, P4 2026-10-23: they stayed behind the window as it was
    dragged)."""
    ws, drv, win = _settled(tmp_path)
    wm = ws.wm
    ring = _Ring(ws, 3)
    wm._toggle_max(win)
    ring.settle(drv, 4)
    drv.touch(40, wm._row_h() // 2)
    ring.frame(drv)
    W, H = ws.sys_canvas.w, ws.sys_canvas.h
    _dx, dy, _dw, _dh = wm._desk_rect()
    for k in range(1, 8):
        drv.touch_drag(40 + 40 * k, wm._row_h() // 2 + 25 * k)
        assert ring.frame(drv) is not None
        assert wm._drag is not None and win.saved is None
        b = ring.bufs[(ring.k - 1) % ring.n]
        cache = bytes(wm._backdrop._buf)
        x0, y0, w, h = wm._pad_extent(win, win.x, win.y, win.w, win.h)
        for y in range(dy, H, 7):
            for x in range(0, W, 7):
                if x0 <= x < x0 + w and y0 <= y < y0 + h:
                    continue
                o = 2 * (y * W + x)
                assert b[o:o + 2] == cache[o:o + 2], (k, x, y)


def test_a_drag_right_after_a_close_leaves_no_ghost(tmp_path):
    """A window closed, then a drag begun before the close reached every
    buffer: each drag frame shows the desk wherever the dragged window is
    not, the closed window in none of them."""
    ws, drv, win = _settled(tmp_path)
    wm = ws.wm
    ring = _Ring(ws, 3)
    ws.open_app(ws._apps_by_id["calc"])
    _quiesce(ws)
    ring.settle(drv, 6)
    wm.close_window_kind("calc")
    ring.frame(drv)
    x, y = win.x + win.w // 2, win.y + win.title_h // 2
    drv.touch(x, y)
    ring.frame(drv)
    W, H = ws.sys_canvas.w, ws.sys_canvas.h
    _dx, dy, _dw, _dh = wm._desk_rect()
    for k in range(1, 7):
        drv.touch_drag(x + 30 * k, y + 20 * k)
        assert ring.frame(drv) is not None
        b = ring.bufs[(ring.k - 1) % ring.n]
        cache = bytes(wm._backdrop._buf)
        x0, y0, w, h = wm._pad_extent(win, win.x, win.y, win.w, win.h)
        for yy in range(dy, H, 7):
            for xx in range(0, W, 7):
                if x0 <= xx < x0 + w and y0 <= yy < y0 + h:
                    continue
                o = 2 * (yy * W + xx)
                assert b[o:o + 2] == cache[o:o + 2], (k, xx, yy)


def test_a_resizing_window_casts_its_shadow_where_it_stands(tmp_path):
    """Mid-resize, the glass outside the rubber window is what it is once
    the window settles there: no shadow or fringe stays at the rect it
    started from (glass, P4 2026-10-23: its old bottom corners' fringes
    did)."""
    from runtime import ui_draw
    ws, drv, _win = _settled(tmp_path)
    ws.look.set_theme("blocks", persist=False, variant="light")
    drv.frame(1 / 30)
    _quiesce(ws)
    assert ui_draw.depth()[12], "a theme whose frame is the depth's draws the shadows"
    wm = ws.wm
    win = wm._wins["settings"]
    ring = _Ring(ws, 3)
    ring.settle(drv, 6)
    x, y = _zone_point(ws, win, RZ_L | RZ_B)
    drv.touch(x, y)
    ring.frame(drv)
    for k in range(1, 6):
        drv.touch_drag(x + 15 * k, y - 15 * k)
        assert ring.frame(drv) is not None
    seen = ring.bufs[(ring.k - 1) % ring.n]
    rx, ry, rw, rh = wm._shown_rect(win)
    drv.touch_up()
    ring.frame(drv)
    _quiesce(ws)
    assert (win.x, win.y, win.w, win.h) == (rx, ry, rw, rh)
    full = ring.full(drv)[0]
    W, H = ws.sys_canvas.w, ws.sys_canvas.h
    for yy in range(H):
        for xx in range(0, W, 2):
            if rx <= xx < rx + rw and ry <= yy < ry + rh:
                continue
            o = 2 * (yy * W + xx)
            assert seen[o:o + 2] == full[o:o + 2], (xx, yy)


def test_a_press_in_a_resize_zone_is_never_a_content_capture(tmp_path):
    """A resize zone's press is the WM's: the window's content does not
    capture it (`_held_in`), and is handed none of the gesture."""
    ws, drv, win = _settled(tmp_path)
    fed = [0]
    layer = ws.wm._content_for("settings")
    orig = layer.handle_pointer

    def spy(px, py, click):
        if click or ws.pointer.down:
            fed[0] += 1
        return orig(px, py, click)

    layer.handle_pointer = spy
    x, y = win.x + 1, win.y + win.h // 2           # just inside the left edge
    assert ws.wm._resize_zone_at(x, y) == ("settings", RZ_L)
    drv.touch(x, y)
    drv.frame(1 / 30)
    for k in range(1, 4):
        drv.touch_drag(x + 10 * k, y)
        drv.frame(1 / 30)
        assert ws.wm._held_in is None and ws.wm._resize is not None
    drv.touch_up()
    drv.frame(1 / 30)
    assert fed[0] == 0


# -- the system menu's scrim rides on a still desk -----------------------------

def _scrim_desk(tmp_path, ride=True, windows=False):
    """The desk under the default look (its card and its scrim) -- bare, or
    with three windows, the last focused -- on a triple-buffered root, every
    buffer current. `ride` False is the reference: the scrim's changes
    repaint whole, as they did."""
    ws = build_desktop_ws(tmp_path, font_scale=1, panel_diagonal_in=7.0)
    drv = _drv(ws)
    ws.bar_layer._clock_text = lambda: "00:00"
    ws.bar_layer._today = lambda: (2026, 10, 14)
    ws.open_desk()
    if windows:
        ws.open_settings()
        for app in ("calc", "files"):
            ws.open_app(ws._apps_by_id[app])
    for _ in range(20):
        _quiesce(ws)
        ws._confetti_until = ws._egg_until = 0
        drv.frame(1 / 30)
    ws.show_fps = False
    if not ride:
        ws.wm._wash_rides = lambda: False
        del ws.sys_canvas.copy_last       # the reference paints every frame
    ring = _Ring(ws, 3)
    for _ in range(3):
        ws._dirty = True
        ring.frame(drv)
    ring.settle(drv)
    assert ws.wm._full_debt <= 0
    return ws, drv, ring


def _touch_widget(ws, drv, ring, name):
    """A finger on bar widget `name`, lifted on the next frame (a board's
    tap: `click` marks its release pass dirty, as a script's verb)."""
    from runtime import shell_layout
    lay = ws.wm._root_ctx.layout
    i, (x, y, w, h) = shell_layout.cell_of(lay.bars, name)
    bx, by = lay.bars[i][1][:2]
    drv.touch(bx + x + w // 2, by + y + h // 2)
    ring.frame(drv)
    drv.touch_up()


def _menu_steps(ws, drv, ring):
    """The system button tapped, two rows down, the button tapped again."""
    return (lambda: _touch_widget(ws, drv, ring, "menu"),
            lambda: ws._sysmenu_layer.handle_input(_Key("down")),
            lambda: ws._sysmenu_layer.handle_input(_Key("down")),
            lambda: _touch_widget(ws, drv, ring, "menu"))


@pytest.mark.parametrize("windows", [False, True])
def test_the_system_menus_scrim_rides_with_it_on_a_still_desk(tmp_path, windows):
    """Open, two moves, close: every frame is the popover's alone -- the
    wash painted into each buffer once (the focused window rendered as it
    shows without the keys first), its snapshot put back once it closes --
    and after each step every buffer holds the bytes the whole-repaint
    path leaves, the focused window's own buffer too."""
    ws, drv, ring = _scrim_desk(tmp_path / "ride", windows=windows)
    ref, rdrv, rring = _scrim_desk(tmp_path / "ref", ride=False, windows=windows)
    copies = []
    real = ws.sys_canvas.copy_last
    ws.sys_canvas.copy_last = lambda *r: copies.append(r) or real(*r)
    for i, (step, rstep) in enumerate(zip(_menu_steps(ws, drv, ring), _menu_steps(ref, rdrv, rring))):
        ring.stacks = []
        step()
        rstep()
        stacks = ring.stacks + ring.settle(drv)
        rring.settle(rdrv)
        assert stacks and set(stacks) == {"pop"}, (i, stacks)
        for a, b in zip(ring.bufs, rring.bufs):
            assert a == b, (i, _bbox(_differ(a, b), ws.sys_canvas.w))
    # the open's and each move's later frames copy the last one's buffer
    assert len(copies) >= 3 * 2, copies
    assert not ws.wm._pop_live() and ring.settle(drv) == []
    for key, win in ws.wm._wins.items():    # what a drag of each would stamp
        assert bytes(win.buf._buf) == bytes(ref.wm._wins[key].buf._buf), key


def _bbox(px, w):
    """The (x0, y0, x1, y1) the 16-bit offsets `px` span, for a message."""
    xs = [i // 2 % w for i in px]
    ys = [i // 2 // w for i in px]
    return (min(xs), min(ys), max(xs), max(ys)) if px else None



def test_a_card_still_settling_over_a_still_scrim_copies_the_scrim(tmp_path):
    """The menu's card asks for frames as it settles (its focus moving):
    each of those frames copies the scrim out of the last frame's buffer
    and paints the card again over it (§14.6), and every buffer ends as
    whole repaints leave it."""
    ws, drv, ring = _scrim_desk(tmp_path / "ride", windows=True)
    ref, rdrv, rring = _scrim_desk(tmp_path / "ref", ride=False, windows=True)
    for w in (ws, ref):
        real = w.menu_ui._draw_sysmenu
        n = [0]

        def draw(real=real, n=n, w=w):
            real()
            n[0] += 1
            if n[0] <= 2:
                w.request_frame()
        w.menu_ui._draw_sysmenu = draw
    washes = []
    real_copy = ws.sys_canvas.copy_last
    ws.sys_canvas.copy_last = lambda *r: washes.append(r) or real_copy(*r)
    ws.widget_tap("menu")
    ref.widget_tap("menu")
    assert set(ring.settle(drv)) == {"pop"}
    rring.settle(rdrv)
    wash = ws.wm._pop_wash[1]
    assert sum(1 for r in washes if tuple(r) == tuple(wash)) >= 2, washes
    assert list(ring.bufs) == rring.full(rdrv)


def test_a_full_frame_under_the_menu_makes_its_close_repaint_whole(tmp_path):
    """A frame that repaints whole while the menu is up washes what it
    painted and keeps no snapshot: the close repaints whole too, and every
    buffer ends as the reference's."""
    ws, drv, ring = _scrim_desk(tmp_path / "ride")
    ref, rdrv, rring = _scrim_desk(tmp_path / "ref", ride=False)
    for w, d, r in ((ws, drv, ring), (ref, rdrv, rring)):
        w.widget_tap("menu")
        r.settle(d)
        w._dirty = True
        r.settle(d)
        w.widget_tap("menu")
    assert "full" in ring.settle(drv)
    rring.settle(rdrv)
    assert ring.bufs == rring.bufs


def test_a_menu_opened_while_its_restore_is_owed_repaints_whole(tmp_path):
    ws, drv, ring = _scrim_desk(tmp_path / "ride")
    ref, rdrv, rring = _scrim_desk(tmp_path / "ref", ride=False)
    for w, d, r in ((ws, drv, ring), (ref, rdrv, rring)):
        w.widget_tap("menu")
        r.settle(d)
        w.widget_tap("menu")
        r.frame(d)                        # one buffer of three restored
        w.widget_tap("menu")
        r.settle(d)
    assert ring.bufs == rring.bufs
    ws.widget_tap("menu")
    ref.widget_tap("menu")
    ring.settle(drv)
    rring.settle(rdrv)
    assert ring.bufs == rring.bufs


def test_a_debt_frame_repeats_the_last_painted_one(tmp_path):
    """X back to three windows: the frame that comes back paints the desk
    whole, and the frames that bring the other buffers to it each copy the
    last painted frame's buffer (§14.6) -- every buffer then holds what
    whole repaints leave."""
    ws, drv, ring = _scrim_desk(tmp_path, windows=True)
    ws.open_library()
    ring.settle(drv)
    ws.open_desk()
    stacks = ring.settle(drv)
    assert stacks[-2:] == ["repeat", "repeat"], stacks
    seen = list(ring.bufs)
    assert len(set(seen)) == 1
    assert seen == ring.full(drv)


def test_a_repeated_frame_counts_for_the_desk_and_window_streaks(tmp_path):
    """A repeat holds what the frame it copied held, so it counts as that
    frame painted once more: after a change and its repeats, the next dirty
    frame skips the desk's restore and the settled windows' stamps as it
    would after whole repaints, and every buffer ends as whole repaints
    leave it."""
    ws, drv, ring = _scrim_desk(tmp_path, windows=True)
    wm = ws.wm
    wm.close_window_kind("calc")              # a shape change: the desk anew
    assert ring.settle(drv) == ["full", "repeat", "repeat"]
    ws._dirty = True
    assert ring.settle(drv) == ["full", "repeat", "repeat"]
    ws._dirty = True
    assert ring.frame(drv) == "full"
    assert wm._desk_painted is False
    seen = list(ring.bufs)
    assert seen == ring.full(drv)


def test_a_calm_desk_pays_its_buffers_debt_and_then_rests(tmp_path):
    """After a change every other buffer is owed it: a calm desk pays them
    at once, each the last painted frame repeated (its buffer copied, §14.6),
    so the next popover finds them current -- then no frame at all. A
    visible cursor is never calm: it asks for nothing."""
    ws, drv, ring = _scrim_desk(tmp_path)
    ws._dirty = True
    assert ring.settle(drv) == ["full", "repeat", "repeat"]
    assert ring.settle(drv) == []
    assert len(set(ring.bufs)) == 1
    ws.widget_tap("battery")
    assert ring.settle(drv) == ["pop"] * 3
    ws.widget_tap("battery")
    ring.settle(drv)
    ws.pointer.visible = True
    ws._dirty = True
    assert len(ring.settle(drv)) <= 2


def _pause_desk(tmp_path, ride=True):
    """A playtest of Star Catcher in its window on the desk, drawing the same
    frame every tick, under the default look; triple-buffered, current."""
    ws = build_desktop_ws(tmp_path, font_scale=1, panel_diagonal_in=7.0)
    drv = _drv(ws)
    ws.bar_layer._clock_text = lambda: "00:00"
    ws.bar_layer._today = lambda: (2026, 10, 14)
    ws.open_desk()
    select_on_home(ws, "Star Catcher")
    ws.open()
    player = ws._content_layers["desktop"]

    def still_game(dt):
        ws.canvas.cls(8)
        ws.canvas.rect(10, 10, 40, 30, 12)
    player.draw = still_game
    for _ in range(20):
        _quiesce(ws)
        ws._confetti_until = ws._egg_until = 0
        drv.frame(1 / 30)
    ws.show_fps = False
    if not ride:
        ws.wm._wash_rides = lambda: False
        del ws.sys_canvas.copy_last       # the reference paints every frame
    ring = _Ring(ws, 3)
    for _ in range(3):
        ws._dirty = True
        ring.frame(drv)
    ring.settle(drv, 4)
    return ws, drv, ring


def _pause_key(ws, name):
    """A key handed to the pause menu as the router does: the frame is
    marked only where the menu does not paint its own change."""
    ws._pause_layer.handle_input(_Key(name))
    if not ws.wm.popover_takes_keys():
        ws.mark_dirty()


def test_the_pause_menu_rides_over_its_paused_playtest(tmp_path):
    """Over a playtest paused in its window: the open, each move and the
    Options page are the popover's frames alone, the close puts the
    snapshot back under the resumed game's quiet frames, and after each
    step every buffer holds the bytes the whole-repaint path leaves."""
    ws, drv, ring = _pause_desk(tmp_path / "ride")
    ref, rdrv, rring = _pause_desk(tmp_path / "ref", ride=False)
    copies = []
    real = ws.sys_canvas.copy_last
    ws.sys_canvas.copy_last = lambda *r: copies.append(r) or real(*r)
    steps = (lambda w: w.pause.show(),
             lambda w: _pause_key(w, "down"),
             lambda w: _pause_key(w, "down"),
             lambda w: _pause_key(w, "a"),          # Options: a card of its own size
             lambda w: _pause_key(w, "b"),          # back to the menu
             lambda w: _pause_key(w, "up"),
             lambda w: setattr(w, "_dirty", True),  # a whole frame under it...
             lambda w: _pause_key(w, "down"))       # ...and the next move still rides
    for i, step in enumerate(steps):
        step(ws)
        step(ref)
        stacks = ring.settle(drv, 12)
        rring.settle(rdrv, 12)
        assert stacks and set(stacks) == ({"full"} if i == 6 else {"pop"}), (i, stacks)
        for a, b in zip(ring.bufs, rring.full(rdrv)):
            assert a == b, (i, _bbox(_differ(a, b), ws.sys_canvas.w))
    # each riding step's later frames copy the last one's buffer (§14.6)
    assert len(copies) >= 2 * (len(steps) - 1), len(copies)
    ws.pause.close()
    ref.pause.close()
    stacks = ring.settle(drv, 4)
    rring.settle(rdrv, 4)
    assert stacks[:3] == ["qpop"] * 3, stacks
    for a, b in zip(ring.bufs, rring.full(rdrv)):
        assert a == b, _bbox(_differ(a, b), ws.sys_canvas.w)


def test_a_running_playtest_leaves_the_desk_to_its_cache(tmp_path):
    """A game running in its window animates inside its window: a whole
    frame beside it (a mark, the system menu's open and close) lays the
    desk from its cache instead of rendering it, and every frame's buffer
    holds the bytes the desk rendered live leaves."""
    ws, drv, ring = _pause_desk(tmp_path / "cache")
    ref, rdrv, rring = _pause_desk(tmp_path / "ref")
    ref.wm.desk_moves = lambda dt: True               # the desk rendered live
    assert ws._animating(1 / 30) and not ws.wm.desk_moves(1 / 30)
    desk = ws.wm._backdrop_layer
    renders = []
    draw = desk._draw_desktop
    desk._draw_desktop = lambda dt: renders.append(1) or draw(dt)
    steps = (lambda w: setattr(w, "_dirty", True),
             lambda w: w.widget_tap("menu"),
             lambda w: w.widget_tap("menu"))
    for i, step in enumerate(steps):
        step(ws)
        step(ref)
        for k in range(6):
            got = ring.frame(drv)
            assert got == rring.frame(rdrv), (i, k)
            assert ring.bufs == rring.bufs, (i, k)
    assert ring.stacks.count("full") >= 2
    assert renders == []


def test_a_stopped_runs_card_rides_over_its_playtest(tmp_path):
    """A run stopped in its window (a crash): the stop card's open and each
    move are the popover's frames alone, as the pause menu's are, and every
    buffer holds the bytes the whole-repaint path leaves."""
    ws, drv, ring = _pause_desk(tmp_path / "ride")
    ref, rdrv, rring = _pause_desk(tmp_path / "ref", ride=False)

    def crash(w):
        w.cart_error = "TypeError: a bench stop"
        w.pause.stop()
    steps = (crash,
             lambda w: _pause_key(w, "down"),
             lambda w: _pause_key(w, "down"),
             lambda w: _pause_key(w, "up"))
    for i, step in enumerate(steps):
        step(ws)
        step(ref)
        stacks = ring.settle(drv, 12)
        rring.settle(rdrv, 12)
        assert ws.pause.open and ws.pause.stopped
        assert stacks and set(stacks) == {"pop"}, (i, stacks)
        for a, b in zip(ring.bufs, rring.full(rdrv)):
            assert a == b, (i, _bbox(_differ(a, b), ws.sys_canvas.w))


# -- a drag over other windows draws them only where the desk was laid -------

def _drag_over(tmp_path, theme, under):
    """Three windows on the desk, the last of them dragged over the other two
    on a triple-buffered root: every presented buffer. `under` False is the
    reference: every window under the gesture draws whole, as it did."""
    ws = build_desktop_ws(tmp_path, font_scale=1, panel_diagonal_in=7.0)
    drv = _drv(ws)
    ws.bar_layer._clock_text = lambda: "00:00"
    ws.bar_layer._today = lambda: (2026, 10, 14)
    ws.open_desk()
    if theme is not None:
        ws.look.set_theme(theme, persist=False, variant="light")
    ws.open_settings()
    for app in ("calc", "files"):
        ws.open_app(ws._apps_by_id[app])
    for _ in range(20):
        _quiesce(ws)
        ws._confetti_until = ws._egg_until = 0
        drv.frame(1 / 30)
    ws.show_fps = False
    wm = ws.wm
    drew = {"skipped": 0}
    if not under:
        wm._under_gesture = lambda win, focused, top: None
    else:
        real = wm._under_gesture

        def counted(win, focused, top):
            u = real(win, focused, top)
            drew["skipped"] += u is not None
            return u
        wm._under_gesture = counted
    ring = _Ring(ws, 3)
    ring.settle(drv)
    win = wm._wins[wm._order[-1]]
    x, y = win.x + 40, win.y + win.title_h // 2
    drv.touch(x, y)
    shots = []
    ring.frame(drv)
    for k in range(1, 10):
        drv.touch_drag(x - 25 * k, y + 9 * k)
        ring.frame(drv)
        shots.append(ring.bufs[(ring.k - 1) % ring.n])
    drv.touch_up()
    held = drew["skipped"]
    shots += [b for _ in ring.settle(drv) for b in [ring.bufs[(ring.k - 1) % ring.n]]]
    drew["after"] = drew["skipped"] - held
    drew["stacks"] = list(ring.stacks)
    if under:
        assert list(ring.bufs) == list(ring.full(drv)), "the release's buffers"
    return shots, drew


@pytest.mark.parametrize("theme", [None, "blocks"], ids=["shipped", "blocks"])
def test_a_drag_over_windows_draws_them_only_where_the_desk_was_laid(tmp_path, theme):
    """Each drag frame's buffer is the one drawing every window under the
    gesture whole leaves -- shadows, fringes and casts included -- while the
    windows under it draw only where the restore laid the desk."""
    ref, _ = _drag_over(tmp_path / "ref", theme, False)
    got, drew = _drag_over(tmp_path / "got", theme, True)
    assert len(ref) == len(got)
    for i, (a, b) in enumerate(zip(ref, got)):
        assert a == b, (i, _bbox(_differ(a, b), 1024))
    assert drew["skipped"] >= 10
    # the release's frame is a drag frame that does not move: the windows
    # under it draw where the restore laid the desk; the frames after it
    # repeat that one (§14.6)
    assert drew["after"] >= 2
    assert drew["stacks"][1:] == ["repeat"] * 2, drew["stacks"]
