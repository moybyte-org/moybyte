"""The two-worlds windowed tier (#105): the DESK (make world -- wallpaper +
icons + windows) vs the fullscreen PLAY world (Library + games), and the
navigation loop between them (PLAY icon <-> Make tile / CHANGE)."""

from runtime import host_app


from ws_helpers import build_desktop_ws as _ws


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


def test_desk_icon_labels_fit_their_pills(tmp_path):
    """#174: PROJECTS/STORYBOOK overflowed the fixed 56*fs pill -- the cell
    now sizes to the longest catalog label, so every label fits unclipped."""
    ws = _ws(tmp_path)
    _drv(ws)
    fs = ws.look.effective_font_scale()
    for _key, _box, pill, label, _cart in ws.wm._backdrop_layer._icon_rects():
        assert len(label) * 8 * fs + 4 <= pill[2], label


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
    assert scale >= 1
    # pix() reads a palette INDEX on every tier (the framebuffer holds RGB565),
    # so the two surfaces are comparable without naming a colour.
    game_px = ws.canvas.pix(160, 100)
    sys_px = ws.sys_canvas.pix(ox + 160 * scale, oy + 100 * scale)
    assert sys_px == game_px
    ws._exit_to_caller()
    drv.frame(1 / 30)
    assert ws.wm._stack == ["launcher"]           # back in the Library
    assert ws.cart is None                        # full go_home cleanup ran


def test_play_world_composites_onto_a_device_shaped_canvas(tmp_path):
    """The play-world composite must not read "no public .buf" as "command-only".

    When this was written the host canvas had a public `.buf`, so it never
    covered the shape that ships on the P4: a raster game canvas keeping its
    framebuffer in `_buf` (no `.buf` at all) beside a system canvas with a
    native `blit_game`. The #175 command-only bail matched that too, so every
    play-world frame ticked the cart, reported it running, and composited
    nothing. The host IS that shape now -- which is why the stand-ins below
    exist only to make the THIRD shape (command-only: neither `.buf` nor a
    native blit) distinguishable from it.
    """
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_library()
    drv.frame(1 / 30)
    assert _select(ws, "Star Catcher")
    ws.open()
    drv.frame(1 / 30)
    assert ws.wm._order == []                     # fullscreen -> composite_game

    blits, bezels = [], []

    class _NoBufGame:                             # DeviceCanvas: _buf, no .buf
        def __init__(self, real):
            self.w, self.h = real.w, real.h
            self._buf = real._buf

    class _RasterSys(_NoBufGame):                 # P4SystemCanvas: native scaled blit
        def cls(self, color):
            bezels.append(color)

        def blit_game(self, gc, ox, oy, scale, defer=False, src=None):
            blits.append((ox, oy, scale))

    class _CommandSys(_NoBufGame):                # web CommandCanvas: neither
        def cls(self, color):
            bezels.append(color)

    real_game, real_sys = ws.canvas, ws._sys_canvas
    ws.canvas = _NoBufGame(real_game)
    ws._sys_canvas = _RasterSys(real_sys)
    ws.wm.composite_game()
    assert blits, "the fullscreen composite bailed on a device-shaped canvas"
    ox, oy, scale = blits[0]
    assert scale == 2 and (ox, oy) == (192, 60)   # 320x240 centered in 1024x600

    # ...while a genuinely command-only canvas (no .buf AND no native blit)
    # still bails BEFORE the letterbox cls -- the #175 property this relaxed
    # guard must keep: that fill would wipe the frame already in the stream.
    bezels.clear()
    ws._sys_canvas = _CommandSys(real_sys)
    ws.wm.composite_game()
    assert bezels == []


def test_relaunching_a_cart_repaints_the_letterbox_bezel(tmp_path):
    """The bezel's "every buffer holds it" latch must not survive a screen change.

    _bezel_key/_bezel_paints live on the long-lived WM and were keyed on the
    composite GEOMETRY only. Run a cart, exit to the Library (which repaints the
    whole screen, letterbox included), relaunch at the same viewport: the key
    still matched and the count was still saturated, so the letterbox was never
    repainted and kept the Library's pixels -- N buffers' worth of them, which
    on a live wallpaper is N different animation phases rotating behind the game.
    """
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_library()
    drv.frame(1 / 30)
    assert _select(ws, "Star Catcher")
    ws.open()
    for _ in range(3):                            # saturate the paint latch
        drv.frame(1 / 30)
    wm = ws.wm
    assert wm._bezel_paints >= wm._retained_n()
    ox, oy, _scale = wm.viewport()
    assert ox > 0 and oy > 0                      # there IS a letterbox here

    ws._exit_to_caller()
    for _ in range(2):
        drv.frame(1 / 30)

    # Stamp a sentinel into the letterbox: it distinguishes a real repaint from
    # a leftover of whatever was on screen before the relaunch. Sample the LEFT
    # letterbox at mid-height -- clear of the game rect AND of the OS bar, which
    # spans the top rows and would clear a sentinel there whatever the bezel did.
    sc = ws.sys_canvas
    px, py = ox // 2, sc.h // 2
    sc.pix(px, py, 42)                            # a palette index nothing here draws
    assert _select(ws, "Star Catcher")
    ws.open()                                     # same cart, same geometry
    drv.frame(1 / 30)
    assert sc.pix(px, py) != 42, "the letterbox kept the previous screen's pixels"


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
    ws.look.set_font_scale(1)                          # relayout INSIDE the desk
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
