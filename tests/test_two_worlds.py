"""The windowed tier's two modes (docs/shell_layout_2026-10.md §3): DESKTOP
mode -- the desk, wallpaper + icons + windows -- and CONSOLE mode -- the home
and everything fullscreen above it -- and the one way between them (the desk's
PLAY icon in, the home's X out)."""

import pytest

from runtime import host_app, ui_draw
from runtime.host_canvas import make_system_canvas


from ws_helpers import build_desktop_ws as _ws, select_on_home
from ws_helpers import boot_on_fixture

@pytest.fixture(autouse=True)
def _on_the_night_fixture(monkeypatch):
    """These pin the lookless pixel bar and its coordinates: every
    workstation here boots on the `night` fixture (ws_helpers)."""
    boot_on_fixture(monkeypatch)


def _drv(ws):
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    return drv


def _select(ws, title):
    if select_on_home(ws, title) is not None:
        return True
    return False


def test_boot_lands_on_the_desk(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    assert ws.wm.stack.kinds() == ["launcher", "desk"]
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
        assert ui_draw.text_width(label, fs, "ui") + 4 <= pill[2], label


# -- #174: the desk column, DRAWN, on the wallpaper that hid the bug ----------
#
# The geometry assertion above cannot see the failure it was written for: the
# overflow was drawn in title_ink on Open Machine's black field, so it left the
# pill and vanished. These render the desk and count what actually landed.

_WINDOWED = [((480, 320), 1), ((1024, 600), 1), ((1024, 600), 2),
             ((1024, 600), 3)]


def _desk_on_open_machine(tmp_path, size=(1024, 600), fs=2, titles=None):
    ws = _ws(tmp_path, sys_size=size, font_scale=fs)
    if titles:
        # An app's manifest title, as declared: the registry's rows are
        # written once, so the test registers its own title over the read.
        declared = ws.app_title
        ws.app_title = lambda kind: titles.get(kind) or declared(kind)
    ws.look.select_wallpaper("moybyte.open_machine", persist=False)
    ws.open_desk()
    ws.pointer.visible = False
    ws._toast_until = 0
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    return ws


def _ink(cv, rect, col):
    x, y, w, h = rect
    return sum(1 for yy in range(y, y + h) for xx in range(x, x + w)
               if cv.pix(xx, yy) == col)


def _label_ink(label, fs, ink, field):
    """The ink a WHOLE label costs, rasterized outside ui_draw.chip -- comparing a
    chip against a chip would only prove the clip agrees with itself."""
    cv = make_system_canvas(ui_draw.print_width(label, fs) + 8, ui_draw.font("ui")[2] * fs + 8,
                            font_scale=fs)
    cv.cls(field)
    cv.print(label, 4, 4, ink, 1)
    return _ink(cv, (0, 0, cv.w, cv.h), ink)


def _label_colors(ws):
    """The default theme paints a label in a pill of its `desk` surface, in
    the bar's colours."""
    th = ws.theme_colors
    return th["bar"], th["chrome_ink"]


def _on_canvas(cv, r):
    return (r[0] >= 0 and r[1] >= 0
            and r[0] + r[2] <= cv.w and r[1] + r[3] <= cv.h)


def _overlaps(a, b):
    return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0]
                or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])


@pytest.mark.parametrize("size,fs", _WINDOWED)
def test_desk_labels_render_whole_on_the_open_machine_wallpaper(tmp_path, size, fs):
    """Every shipped desk label draws every one of its glyphs, at every
    windowed config -- PROJECTS drew as PROJECT and STORYBOOK as STORYBO."""
    ws = _desk_on_open_machine(tmp_path, size, fs)
    cv = ws.sys_canvas
    efs = ws.look.effective_font_scale()
    seen = []
    desk = ws.wm._backdrop_layer
    field, ink = _label_colors(ws)
    for key, box, pill, label, _cart in desk._icon_rects():
        drawn = desk.label_rect(pill, label, efs)
        assert _ink(cv, drawn, ink) == _label_ink(label, efs, ink, field), label
        assert _on_canvas(cv, box) and _on_canvas(cv, pill), (key, box, pill)
        assert not any(_overlaps(pill, p) for p in seen), key
        seen.append(pill)


def test_a_long_app_title_keeps_every_desk_icon_on_the_canvas(tmp_path):
    """A user may add an app, and its manifest title is what the desk labels
    its icon with. The cell sizes to the LONGEST label, so an unbounded title
    marched the last column past the right edge -- and an icon drawn off the
    canvas is unreachable, not merely ugly. It clips inside its pill instead."""
    long_title = "CALCULATING MACHINE DELUXE"
    ws = _desk_on_open_machine(tmp_path, fs=3, titles={"calc": long_title})
    cv = ws.sys_canvas
    fs = ws.look.effective_font_scale()
    rects = ws.wm._backdrop_layer._icon_rects()
    assert any(label == long_title for _k, _b, _p, label, _c in rects)
    seen = []
    for key, box, pill, label, _cart in rects:
        assert _on_canvas(cv, box) and _on_canvas(cv, pill), (key, box, pill)
        assert not any(_overlaps(pill, p) for p in seen), key
        seen.append(pill)
        field, ink = _label_colors(ws)
        drawn = ws.wm._backdrop_layer.label_rect(pill, label, fs)
        fits = len(ui_draw.print_fit(label, drawn[2], fs))
        assert _ink(cv, drawn, ink) == _label_ink(label[:fits], fs, ink, field), label


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


def test_play_icon_enters_console_mode(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()                            # an open desk window
    drv.frame(1 / 30)
    assert ws.wm._order == ["settings"]
    ws.wm._backdrop_layer._open_icon("play")
    drv.frame(1 / 30)
    assert ws.wm.stack.kinds() == ["launcher"]    # the route at its root
    assert not ws.windowed_chrome
    assert ws.wm._order == [] and ws.wm._parked.order == ["settings"]
    assert ws.wm.visible_stack()[0] is ws.launcher_layer


def test_the_home_splits_games_and_apps_the_same_on_every_tier(tmp_path):
    for ws in (_ws(tmp_path), host_app.build_workstation(str(tmp_path / "c2"))):
        titles = [c.get("title") for c in ws.launcher.items]
        for app_cart in ("Files", "Paint", "Storybook", "Calc", "Notes"):
            assert app_cart not in titles             # Games holds no app
        assert "Star Catcher" in titles
        ws.set_home_tab("apps")
        titles = [c.get("title") for c in ws.launcher.items]
        for app_cart in ("Files", "Paint", "Storybook", "Calc", "Notes"):
            assert app_cart in titles                 # the Apps tab holds them
        assert "Appearance" not in titles             # one of HIDDEN_APPS
        assert "Star Catcher" not in titles


def test_library_game_runs_fullscreen_and_exits_back(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_library()
    drv.frame(1 / 30)
    assert _select(ws, "Star Catcher")
    ws.open()
    drv.frame(1 / 30)
    assert ws.cart_error is None
    assert ws.wm.stack.kinds() == ["launcher", "desktop"]
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
    assert ws.wm.stack.kinds() == ["launcher"]           # back in the Library
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


def test_make_change_and_new_from_the_console_home_open_a_desk_window(tmp_path):
    """On a board with a desk the Editor is a window (owner, 2026-10-20): a
    tap on the Make tab, CHANGE and NEW from the console-mode home each give
    console mode back to the desk and open the Editor in a window there,
    over the windows the desk had. Closing that window gives the glass back
    to the console-mode home, on the tab and the card it came from (R5-05)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_app(ws._apps_by_id["calc"])
    drv.frame(1 / 30)
    ws.open_library()
    drv.frame(1 / 30)
    ws.set_home_tab("make")
    assert _select(ws, "Star Catcher")
    want = ws.launcher.selected().get("path")
    ws.launch_selected()                          # Make's tap edits
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop" and ws.wm.desk_open()
    assert ws.wm._order == ["calc", "menu"] and ws.screen == "menu"
    assert ws.cart.get("path") == want
    ws.wm.close_window_kind("menu")               # the Editor's X
    drv.frame(1 / 30)
    assert ws.wm.mode == "console" and ws.screen == "launcher"
    assert ws.launcher.tab == "make" and ws.launcher.selected().get("path") == want
    ws.set_home_tab("games")
    drv.frame(1 / 30)
    assert _select(ws, "Star Catcher")
    ws.change_selected()                          # CHANGE = the Editor, a window
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop" and ws.wm._order == ["calc", "menu"]
    ws.wm.close_window_kind("menu")
    drv.frame(1 / 30)
    assert ws.wm.mode == "console" and ws.launcher.tab == "games"
    assert ws.launcher.selected().get("path") == want
    ws.set_home_tab("make")
    ws.launcher.sel = 0                           # the "+ New" card
    n0 = len(ws.carts.all)
    ws.launch_selected()
    drv.frame(1 / 30)
    assert len(ws.carts.all) == n0 + 1
    assert ws.wm.mode == "desktop" and ws.wm._order == ["calc", "menu"]
    assert ws.cart.get("type") == "game"
    made = ws.cart.get("path")
    ws.wm.close_window_kind("menu")
    drv.frame(1 / 30)
    assert ws.wm.mode == "console" and ws.launcher.tab == "make"
    assert ws.launcher.selected().get("path") == made, "back on the cart it made"
    # The parked windows came through the trip untouched.
    ws.open_desk()
    drv.frame(1 / 30)
    assert ws.wm._order == ["calc"]


def test_closing_the_editor_stays_on_the_desk_once_another_window_opened(tmp_path):
    """The one exception to R5-05's return: a window the kid opened on the
    desk meanwhile keeps them there when the Editor closes."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_library()
    drv.frame(1 / 30)
    ws.set_home_tab("make")
    assert _select(ws, "Star Catcher")
    ws.launch_selected()
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop" and ws.wm._order == ["menu"]
    ws.wm._backdrop_layer._open_icon("files")
    drv.frame(1 / 30)
    ws.wm.close_window_kind("menu")
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop" and ws.wm._order == ["files"]


def test_an_editor_opened_on_the_desk_closes_onto_the_desk(tmp_path):
    """Only a hand-off from the console-mode home returns there: an Editor
    the desk's PROJECTS opened closes onto the desk."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_projects()
    drv.frame(1 / 30)
    g = ws.projects_grid
    g.sel = next(i for i, it in enumerate(g.items) if it.get("path"))
    ws.launch_selected(g)
    drv.frame(1 / 30)
    assert ws.wm._order == ["projects", "menu"]
    ws.wm.close_window_kind("menu")
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop" and ws.wm._order == ["projects"]


def test_desk_icons_open_windows(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.wm._backdrop_layer._open_icon("files")
    drv.frame(1 / 30)
    assert ws.wm._order == ["files"]
    ws.wm._backdrop_layer._open_icon("projects")
    drv.frame(1 / 30)
    assert ws.wm._order == ["files", "projects"]
    # Pointer path: a tap on the PLAY icon box through the desk root layer.
    for key, box, _pill, _label, _cart in ws.wm._backdrop_layer._icon_rects():
        if key == "play":
            ws.wm._backdrop_layer.handle_pointer(box[0] + 2, box[1] + 2, True)
            break
    drv.frame(1 / 30)
    assert ws.wm.stack.kinds() == ["launcher"]


def test_font_scale_flip_inside_desk_never_leaks_chrome(tmp_path):
    """The F4 regression: layouts rebuilt inside the desk carry desk chrome;
    the mode switch must rebuild them for console mode on the way out."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.look.set_font_scale(1)                          # relayout INSIDE the desk
    drv.frame(1 / 30)
    ws.open_library()
    drv.frame(1 / 30)                             # the mode switch relayouts again
    assert not ws.windowed_chrome
    assert _select(ws, "Star Catcher")
    ws.open()
    drv.frame(1 / 30)
    assert ws.cart_error is None                  # console mode renders cleanly
    ws._exit_to_caller()
    drv.frame(1 / 30)
    assert ws.wm.stack.kinds() == ["launcher"]


def test_fullscreen_tiers_are_untouched(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    assert not getattr(ws.wm, "has_desk", False)
    assert not ws.windowed_chrome
    ws.set_home_tab("make")
    ws.launcher.sel = 0                           # Make's "+ New"
    ws.launch_selected()
    assert ws.wm.top_kind() == "menu"             # a new game, in the Editor
