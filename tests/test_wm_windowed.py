"""Tests for the Picotron-style windowed WM (runtime/wm_windowed.py, #73 -- the
big-screen / P4 "One" presentation of the v0.5 shell).

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver / Workstation) with `windowed=True`, so these pin the windowed
presentation's contracts:

  * install/degradation -- the windowed WM only mounts on a distinct big system
    canvas; at 320x240 the fullscreen-stack WM stays (byte-identical tier);
  * the back-stack <-> window mapping -- every pushed process gets a window,
    pops drop it, the launcher root is the desktop (never a window);
  * launch-and-return presented spatially -- PLAY opens a playtest window above
    the still-visible editor window; exit pops back to the editor;
  * input routing -- keyboard to the focused window, taps in WINDOW-LOCAL
    coords under the window's own layout context, drags move windows, clicking
    a lower window raises it by popping everything above (the same pop);
  * the game viewport == the player window's content rect (ws._game_xy).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws(tmp_path, **kw):
    from runtime import host_app
    kw.setdefault("sys_size", (1024, 600))
    kw.setdefault("font_scale", 2)
    kw.setdefault("windowed", True)
    ws = host_app.build_workstation(str(tmp_path / "carts"), **kw)
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path"))
    return ws


def _drv(ws):
    from runtime import host_app
    return host_app.ConsoleDriver(ws)


def _quiesce(ws):
    ws.pointer.visible = False
    ws.ach.toast = None
    ws.ach.toast_until = 0


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
    """With just the launcher on the stack there are no windows: the visible
    stack is [launcher] + overlays, exactly the fullscreen tier's shape."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    drv.frame(1 / 30)
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


def test_picker_and_editor_share_one_make_window(tmp_path):
    """The project picker and the Editor are ONE window (the Make flow, owner
    call): picking a project swaps the window's CONTENT to the Editor -- no
    second window spawns -- and PROJECTS swaps it back. The back-stack still
    holds both kinds (exit pops one level at a time)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_picker()
    drv.frame(1 / 30)
    assert ws.wm._order == ["make"]
    win = ws.wm._wins["make"]
    assert win.kind == "picker"
    ws.pick_selected()
    drv.frame(1 / 30)
    assert ws.wm._order == ["make"]              # STILL one window
    assert ws.wm._wins["make"] is win            # the same record
    assert win.kind == "menu"                    # showing the Editor now
    assert ws.wm._stack == ["launcher", "picker", "menu"]   # stack unchanged
    assert win.buf is not None and win.ctx is not None
    # The window's layout context is sized to the window, not the desktop.
    assert win.ctx.layout.w == win.buf.w and win.ctx.layout.w < 1024
    ws.open_picker()                             # PROJECTS: back in the same window
    drv.frame(1 / 30)
    assert ws.wm._order == ["make"] and win.kind == "picker"


def test_desktop_root_still_draws_beneath_windows(tmp_path):
    """The launcher home (the desktop) keeps drawing at FULL canvas size while a
    window is open -- its layout stays the root's."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()
    _quiesce(ws)
    drv.frame(1 / 30)
    assert ws.layout.w == 1024      # ambient (root) layout after the frame
    assert ws.launcher.layout.w == 1024


# ---------------------------------------------------------------------------
# Launch-and-return: PLAY = a playtest window above the editor
# ---------------------------------------------------------------------------

def test_play_opens_playtest_window_and_exit_returns(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_picker()
    ws.pick_selected()
    ws.set_menu_view("code")
    drv.frame(1 / 30)
    ws._leave_menu()                # PLAY: run the cart, caller = the Editor
    drv.frame(1 / 30)
    assert ws.wm._order[-1] == "desktop"
    assert "make" in ws.wm._order   # the editor window is still open beneath
    win = ws.wm._wins["desktop"]
    assert win.buf is None                       # the game canvas IS its content
    assert ws.wm._player_view(win)[2] >= 1       # integer viewport scale
    ws._exit_to_caller()
    drv.frame(1 / 30)
    assert ws.wm._order[-1] == "make"
    assert ws.menu_view == "code"   # back on the tab PLAY left


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
    ws.open_picker()
    ws.pick_selected()
    drv.frame(1 / 30)
    ws._leave_menu()
    drv.frame(1 / 30)
    win = ws.wm._wins["desktop"]
    xr = dict(ws.wm._strip_buttons(win))["close"]
    drv.touch(xr[0] + 2, xr[1] + 2)
    drv.frame(1 / 30)
    assert ws.wm._order[-1] == "make"     # back to the editor window
    assert ws.wm._wins["make"].kind == "menu"


# ---------------------------------------------------------------------------
# Pointer routing: window-local taps, drags, click-to-focus, desktop fall-through
# ---------------------------------------------------------------------------

def test_window_local_tap_hits_the_apps_bar(tmp_path):
    """A tap on the editor window's own bar row (its title strip) lands in the
    app's zone -- switching tabs via the ladder works at the WINDOW's position."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_picker()
    ws.pick_selected()
    drv.frame(1 / 30)
    assert ws.menu_view == "cards"        # Config-first landing
    win = ws.wm._wins["make"]
    # The Editor's lent zone: icon slot 3 is the CODE tab (projects, cards,
    # blocks, code, ... -- editor_app._ZONE_TABS order), stride 18*fs. The app's
    # bar row sits below the WM title strip.
    fs = ws._effective_font_scale()
    lx = win.ctx.layout.zone_left[0] + 3 * (16 + 2) * fs + 2
    ly = win.ctx.layout.zone_left[1] + 2
    drv.touch(win.x + 1 + lx, win.y + 1 + win.title_h + ly)
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
    the launcher (they blit the cache)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    win = _engage_drag(ws, drv)
    hx, hy = win._hold
    # The drag engaged on the last _engage_drag frame -> the cache is built (the
    # capture runs in the same frame handle_pointer sets _drag).
    assert ws.wm._backdrop_valid and ws.wm._backdrop is not None

    calls = [0]
    real_draw = ws.launcher_layer.draw
    ws.launcher_layer.draw = lambda dt: (calls.__setitem__(0, calls[0] + 1),
                                         real_draw(dt))[1]
    # Steady drag frames: cache reused, launcher NOT re-rendered.
    for _ in range(3):
        drv.touch_drag(hx, hy)
        drv.frame(0.0)
    assert calls[0] == 0
    # Release: the next frame renders the launcher live again (cache invalidated).
    drv.touch_up()
    ws._dirty = True
    drv.frame(0.0)
    assert not ws.wm._backdrop_valid
    assert calls[0] == 1
    ws.launcher_layer.draw = real_draw


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
    cached = bytes(ws.sys_canvas.buf)
    # Force a live re-render of the identical frame (cache off), no window move.
    ws.wm._backdrop_valid = False
    ws._dirty = True
    drv.touch_drag(hx, hy)
    drv.frame(0.0)
    live = bytes(ws.sys_canvas.buf)
    assert cached == live


def test_click_moves_focus_without_popping_the_playtest(tmp_path):
    """The owner call that retired raise-by-pop: clicking the editor window while
    a playtest runs beside it moves FOCUS to the editor -- the back-stack is
    untouched, the Player keeps its stack-top slot (it keeps ticking), and only
    an explicit exit ends the run. Typing then lands in the editor."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_picker()
    ws.pick_selected()
    ws.set_menu_view("code")
    drv.frame(1 / 30)
    ws._leave_menu()                       # PLAY
    drv.frame(1 / 30)
    assert ws.wm._focus == "desktop"
    make = ws.wm._wins["make"]
    # Drag the playtest window aside so a corner of the editor is exposed, then
    # click that corner.
    player = ws.wm._wins["desktop"]
    ws.wm._move_window(player, 600, 300)
    drv.frame(1 / 30)
    px, py = make.x + 4, make.y + 4
    assert ws.wm._win_at(px, py) == "make"
    drv.touch(px, py)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.wm._focus == "make"
    assert ws.wm._stack[-1] == "desktop"   # the playtest was NOT popped
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
    ws.open_picker()
    ws.pick_selected()
    drv.frame(1 / 30)
    ws._leave_menu()                       # PLAY
    drv.frame(1 / 30)
    ws.wm._focus = "make"                  # focus the editor beside it
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


def test_desktop_click_launches_a_cart_with_a_window_open(tmp_path):
    """The desktop stays interactive beneath windows: tapping a cart tile on the
    home grid (outside every window) runs it."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    win = ws.wm._wins["settings"]
    # Find a cart tile whose rect is fully outside the settings window.
    tile = None
    for i, it in enumerate(ws.launcher.items):
        if not it.get("path"):
            continue
        r = ws.launcher.layout.tile_rect(i, ws.launcher.page)
        if r is None:
            continue
        tx, ty = r[0] + r[2] // 2, r[1] + r[3] // 2
        if not (win.x <= tx < win.x + win.w and win.y <= ty < win.y + win.h):
            tile = (i, tx, ty)
            break
    if tile is None:
        return                             # window covers the whole grid -- skip
    i, tx, ty = tile
    drv.touch(tx, ty)                      # select
    drv.frame(1 / 30)
    drv.touch(tx, ty)                      # confirm-tap runs it (launcher rule)
    drv.frame(1 / 30)
    assert ws.wm._order[-1] == "desktop"   # a playtest window opened


# ---------------------------------------------------------------------------
# Keyboard focus
# ---------------------------------------------------------------------------

def test_keyboard_goes_to_focused_window(tmp_path):
    """Typing lands in the focused editor window's code buffer, sized to the
    WINDOW's layout (not the desktop's)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_picker()
    ws.pick_selected()
    ws.set_menu_view("code")
    drv.frame(1 / 30)
    win = ws.wm._wins["make"]
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
    """Inside a window the zoned bar drops its OS right zone and the dock never
    draws -- the desktop's full-width bar is the one taskbar (owner feedback)."""
    ws = _ws(tmp_path)
    assert ws.windowed_chrome
    bar = ws.bar_layer
    assert bar._in_window("menu") and bar._in_window("settings")
    assert not bar._in_window("home")          # the desktop bar keeps OS chrome
    assert bar._dock_slot_at(10, ws.sys_canvas.h - 4) is None   # dock is gone


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
    bar_h = ws.wm._bar_h()
    assert (win.x, win.y) == (0, bar_h)         # fills below the OS bar
    assert win.w == 1024 and win.h == 600 - bar_h
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


def test_chip_click_is_focus_only_never_a_pop(tmp_path):
    """Taskbar chips: click the focused window's chip to minimize, again to
    restore, and an unfocused window's chip to FOCUS it -- the back-stack never
    changes from the taskbar (nothing closes by looking at it)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_picker()
    ws.pick_selected()
    drv.frame(1 / 30)
    ws._leave_menu()                             # PLAY: playtest above the editor
    drv.frame(1 / 30)
    chips = {k: r for k, r, _l in ws.wm._chip_rects()}
    assert set(chips) == {"make", "desktop"}
    stack_before = list(ws.wm._stack)
    # Clicking the editor's chip FOCUSES it -- no pop, the playtest stays up.
    r = chips["make"]
    drv.touch(r[0] + 2, r[1] + 2)
    drv.frame(1 / 30)
    assert ws.wm._focus == "make"
    assert ws.wm._stack == stack_before
    # Clicking the (now focused) editor chip minimizes it; again restores+focuses.
    drv.touch(r[0] + 2, r[1] + 2)
    drv.frame(1 / 30)
    assert ws.wm._wins["make"].minimized
    drv.touch(r[0] + 2, r[1] + 2)
    drv.frame(1 / 30)
    assert not ws.wm._wins["make"].minimized and ws.wm._focus == "make"
    assert ws.wm._stack == stack_before         # still nothing popped


def test_editor_close_pops_one_level_to_picker(tmp_path):
    """The Make window's X on the EDITOR pops one level -- the SAME window flips
    back to the picker (not a jump all the way home, not a second window)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_picker()
    ws.pick_selected()
    drv.frame(1 / 30)
    win = ws.wm._wins["make"]
    assert win.kind == "menu"
    xr = dict(ws.wm._strip_buttons(win))["close"]
    drv.touch(xr[0] + 2, xr[1] + 2)
    drv.frame(1 / 30)
    assert ws.wm._order == ["make"]
    assert ws.wm._wins["make"].kind == "picker"


def test_font_scale_change_rebuilds_windows(tmp_path):
    """A Settings font-size change drops + rebuilds the window records at the
    new scale (the on_relayout hook), re-anchored to the ROOT canvas."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    assert "settings" in ws.wm._wins
    ws.set_font_scale(1, persist=False)
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
    assert ws.wm._stack == ["launcher", "desktop", "settings"]
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
    assert ws.wm._stack[1] == "desktop"


def test_bar_wifi_icon_deep_links_to_settings_wifi(tmp_path):
    """On the windowed desktop the bar's wifi icon opens Settings -> WIFI (a
    window over whatever runs) instead of launching the wifi.moy tool cart."""
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
    assert ws.wm.top_is("settings")
    assert ws.settings_layer.wifi_view
    assert ws.cart.get("title") == cart_title  # the game was NOT replaced


def test_closing_make_never_takes_settings_with_it(tmp_path):
    """Regression (owner-reported): closing the Make window used to TRUNCATE the
    back-stack, killing Settings (and anything else) stacked above it. Closing is
    surgical now: only the closed process leaves the stack."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_picker()
    ws.pick_selected()
    ws.open_settings()                          # Settings ABOVE the Make flow
    drv.frame(1 / 30)
    assert ws.wm._stack == ["launcher", "picker", "menu", "settings"]
    make = ws.wm._wins["make"]
    xr = dict(ws.wm._strip_buttons(make))["close"]
    drv.touch(xr[0] + 2, xr[1] + 2)             # X on the Make window (the EDITOR)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert "settings" in ws.wm._order           # Settings SURVIVED
    assert ws.wm._wins["make"].kind == "picker"  # make popped one level only
    assert ws.wm._stack == ["launcher", "picker", "settings"]


def test_closing_playtest_keeps_settings_and_refocuses_editor(tmp_path):
    """Closing the playtest (X / hold-BACKSPACE -> _exit_to_caller) removes ONLY
    the player: Settings floating above stays open, and focus returns to the run
    caller's window (the Editor)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_picker()
    ws.pick_selected()
    drv.frame(1 / 30)
    ws._leave_menu()                            # PLAY (caller = the Editor)
    ws.open_settings()                          # Settings over the running game
    drv.frame(1 / 30)
    assert ws.wm._stack == ["launcher", "picker", "menu", "desktop", "settings"]
    ws._exit_to_caller()                        # the hold-BACKSPACE exit path
    drv.frame(1 / 30)
    assert ws.wm._stack == ["launcher", "picker", "menu", "settings"]
    assert "settings" in ws.wm._order           # Settings SURVIVED
    assert ws.wm._focus == "make"               # back to the Editor's window


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
    assert ws.wm._stack == ["launcher", "desktop"]
    assert "desktop" in ws.wm._order            # the game window survived


def test_theme_setting_cycles_and_persists(tmp_path):
    """Settings -> THEME cycles chrome.THEMES, retints the panel chrome
    everywhere (settings panel, window strips, chips, selection accents), and
    persists to system.json; the default "night" keeps today's exact colors."""
    ws = _ws(tmp_path)                             # aliases the bare-name modules
    from runtime.chrome import THEMES, theme_colors
    drv = _drv(ws)
    assert ws.theme_name == "night"
    assert ws.theme_colors == theme_colors("night")
    ws.cycle_theme(1)
    assert ws.theme_name == THEMES[1][0]           # "indigo"
    assert ws.theme_colors is not theme_colors("night")
    assert ws.launcher.theme is ws.theme_colors    # accents follow
    assert ws.system.get("theme") == ws.theme_name  # persisted
    # A themed frame renders (window strip uses the new title token).
    ws.open_settings()
    drv.frame(1 / 30)
    # And an unknown persisted name falls back to the default.
    ws.set_theme("nonsense", persist=False)
    assert ws.theme_name == "night"


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
            skip = 60 * ws.sys_canvas.w      # exclude the bar strip (live clock)
            frames.append(bytes(ws.sys_canvas.buf[skip:]))
        return frames

    a = run(True)
    b = run(False)
    for i, (fa, fb) in enumerate(zip(a, b)):
        assert fa == fb, "union restore diverged at drag frame %d" % i


def test_live_resize_body_follows_grip(tmp_path):
    """During a grip resize the window BODY tracks the rubber size (the 'real OS'
    feel, #58): the focused border lands at the rubber corner, grown area shows
    the panel field, the real relayout still only happens on release."""
    from runtime.wm_windowed import _BORDER_TOP
    ws = _ws(tmp_path)
    drv = _drv(ws)
    win, cw, ch = _engage_resize(ws, drv)
    ow, oh = win.w, win.h
    assert (cw, ch) != (ow, oh)              # the rubber actually grew
    assert win.w == ow and win.h == oh       # no mid-gesture relayout
    buf, W = ws.sys_canvas.buf, ws.sys_canvas.w
    # Focused border drawn at the RUBBER corner, not the old one.
    corner = buf[(win.y + ch - 1) * W + (win.x + cw - 1)]
    assert corner == _BORDER_TOP
    # A grown-area probe (beyond the old width, inside the new content rect)
    # shows the panel field fill -- the content crop anchored top-left.
    px = win.x + ow + 10
    py = win.y + win.title_h + 20
    assert px < win.x + cw - 1
    assert buf[py * W + px] == ws.theme_colors["panel"]
    # Release applies the REAL resize (the existing apply-on-release contract).
    drv.touch_up()
    drv.frame(0.0)
    assert ws.wm._resize is None
    assert (win.w, win.h) == (cw, ch)


def test_resize_outline_fallback_without_rect_stamp(tmp_path):
    """A canvas without blit_strip_rect (the web RecordingLayer) keeps the old
    rubber-band OUTLINE preview and the full-screen backdrop restore."""
    from runtime.wm_windowed import _BORDER_TOP
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.sys_canvas.blit_strip_rect = None     # instance attr shadows the method
    win, cw, ch = _engage_resize(ws, drv)
    buf, W = ws.sys_canvas.buf, ws.sys_canvas.w
    # The accent outline is drawn at the rubber rect...
    accent = ws.theme_colors["accent"]
    assert buf[(win.y + ch - 1) * W + (win.x + cw - 1)] == accent
    # ... and the body was NOT drawn at the rubber size (the border at the
    # rubber corner would be _BORDER_TOP under live-body).
    assert buf[(win.y + ch - 1) * W + (win.x + cw - 1)] != _BORDER_TOP
