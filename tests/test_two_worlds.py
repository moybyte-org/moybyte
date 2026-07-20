"""The two-worlds windowed tier (#105): the DESK (make world -- wallpaper +
icons + windows) vs the fullscreen PLAY world (Library + games), and the
navigation loop between them (PLAY icon <-> Make tile / CHANGE)."""

from runtime import host_app


def _ws(tmp_path, **kw):
    kw.setdefault("sys_size", (1024, 600))
    kw.setdefault("font_scale", 2)
    kw.setdefault("windowed", True)
    return host_app.build_workstation(str(tmp_path / "carts"), **kw)


def _drv(ws):
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    return drv


def _select(ws, title):
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == title:
            ws.launcher.sel = i
            return True
    return False


def test_boot_lands_on_the_desk(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    assert ws.wm._stack == ["launcher", "desk"]
    assert ws.wm.desk_open() and ws.windowed_chrome
    assert ws.wm._order == []                     # a floor, not a window
    # The desk draws its icon column: PLAY + PROJECTS + the system apps.
    keys = [k for k, _b, _p, _l, _c in ws.wm._backdrop_layer._icon_rects()]
    assert keys[:2] == ["play", "projects"]
    assert "files" in keys and "artwork" in keys
    assert "appearance" not in keys               # reachable via Settings
    drv.frame(1 / 30)                             # icons render without error


def test_desk_bar_has_no_context_x(tmp_path):
    ws = _ws(tmp_path)
    _drv(ws)
    bar = ws.bar_layer
    assert not bar._in_window("desk")             # the desk bar keeps its OS zone
    # Tapping where the X would sit must NOT leave the desk (the desk is the
    # floor; only the PLAY icon leaves).
    x_hit = ws.layout.context_x_btn
    bar.handle_bar_tap("desk", x_hit[0] + 1, x_hit[1] + 1)
    assert ws.wm.desk_open()


def test_play_icon_drops_to_the_fullscreen_library(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()                            # an open desk window
    drv.frame(1 / 30)
    assert ws.wm._order == ["settings"]
    ws.wm._backdrop_layer._open_icon("play")
    drv.frame(1 / 30)
    assert ws.wm._stack == ["launcher"]           # leaving closes the desk's windows
    assert not ws.windowed_chrome
    assert ws.wm._order == []
    assert ws.wm.visible_stack()[0] is ws.launcher_layer


def test_library_shelf_hides_system_apps_but_keeps_kid_carts(tmp_path):
    ws = _ws(tmp_path)
    _drv(ws)
    titles = [c.get("title") for c in ws.launcher.items]
    for app_cart in ("Files", "Paint", "Writer", "Sheets", "Storybook", "Calc"):
        assert app_cart not in titles
    assert "Star Catcher" in titles               # games stay
    assert "Beeper" in titles                     # kid-style app carts stay
    # The fullscreen tier keeps EVERYTHING on its launcher.
    ws2 = host_app.build_workstation(str(tmp_path / "carts2"))
    titles2 = [c.get("title") for c in ws2.launcher.items]
    assert "Paint" in titles2 and "Files" in titles2


def test_library_game_runs_fullscreen_and_exits_back(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_library()
    drv.frame(1 / 30)
    assert _select(ws, "Star Catcher")
    ws.open()
    drv.frame(1 / 30)
    assert ws.cart_error is None
    assert ws.wm._stack == ["launcher", "desktop"]
    assert ws.wm._order == []                     # fullscreen: no player window
    # The fullscreen composite paints the game onto the big system canvas
    # (probe a mid-viewport pixel -- corners can carry overlay stamps).
    ox, oy, scale = ws.wm.viewport()
    assert scale >= 1 and ws.sys_canvas.buf is not None
    game_px = ws.canvas.buf[100 * ws.canvas.w + 160]
    sys_px = ws.sys_canvas.buf[(oy + 100 * scale) * ws.sys_canvas.w
                               + ox + 160 * scale]
    assert sys_px == game_px
    ws._exit_to_caller()
    drv.frame(1 / 30)
    assert ws.wm._stack == ["launcher"]           # back in the Library
    assert ws.cart is None                        # full go_home cleanup ran


def test_make_tile_and_change_return_to_the_desk(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_library()
    drv.frame(1 / 30)
    ws.launcher.sel = 0                           # the pinned Make tile
    ws.launch_selected()
    drv.frame(1 / 30)
    assert ws.wm._stack == ["launcher", "desk"]
    ws.open_library()
    drv.frame(1 / 30)
    assert _select(ws, "Star Catcher")
    ws.change_selected()                          # CHANGE = desk + Editor window
    drv.frame(1 / 30)
    assert ws.wm.desk_open()
    assert ws.wm._order == ["make"]
    assert ws.wm._wins["make"].kind == "menu"


def test_desk_icons_open_windows(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.wm._backdrop_layer._open_icon("files")
    drv.frame(1 / 30)
    assert ws.wm._order == ["files"]
    ws.wm._backdrop_layer._open_icon("projects")
    drv.frame(1 / 30)
    assert ws.wm._order == ["files", "make"]
    # Pointer path: a tap on the PLAY icon box through the desk root layer.
    for key, box, _pill, _label, _cart in ws.wm._backdrop_layer._icon_rects():
        if key == "play":
            ws.wm._backdrop_layer.handle_pointer(box[0] + 2, box[1] + 2, True)
            break
    drv.frame(1 / 30)
    assert ws.wm._stack == ["launcher"]


def test_font_scale_flip_inside_desk_never_leaks_chrome(tmp_path):
    """The F4 regression: layouts rebuilt inside the desk carry desk chrome;
    the world flip must rebuild them for the play world on the way out."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.set_font_scale(1)                          # relayout INSIDE the desk
    drv.frame(1 / 30)
    ws.open_library()
    drv.frame(1 / 30)                             # world flip relayouts again
    assert not ws.windowed_chrome
    assert _select(ws, "Star Catcher")
    ws.open()
    drv.frame(1 / 30)
    assert ws.cart_error is None                  # play world renders cleanly
    ws._exit_to_caller()
    drv.frame(1 / 30)
    assert ws.wm._stack == ["launcher"]


def test_fullscreen_tiers_are_untouched(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    assert not getattr(ws.wm, "has_desk", False)
    assert not ws.windowed_chrome
    ws.launcher.sel = 0                           # Make tile
    ws.launch_selected()
    assert ws.wm.top_kind() == "picker"           # fullscreen Make -> picker
