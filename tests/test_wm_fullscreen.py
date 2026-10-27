"""Full screen: what maximise means on the desk (the owner's E-05,
docs/media/look_2026-10/MAXIMISE_VARIANTS.md's V2).

A maximised window takes the whole glass: no bar, no chips, no strip, no
frame. Its row is the top of the glass -- the app's own band where its
content draws one, the band's ground under the WM's row otherwise -- with
the window's icon and title at its head and minimise, restore and close at
its right, the controls every strip draws. Leaving is the corner's restore
or F11 (the `full` button), never Esc; a restore puts the window back
exactly where it was. A game's window is the game alone, at the largest
whole scale that leaves the corner's controls clear, on black. The state
is remembered per board (`win_full`), and the Editor opens full screen the
first time (E-04)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from ws_helpers import build_desktop_ws, open_cart  # noqa: E402

DT = 1 / 30
_TAP_FLOOR = 32


def _desk(tmp_path, size=(1024, 600), restored_editor=False):
    """The P4 as shipped (the Moy look, font scale 1 on 7-inch glass), on
    the desk."""
    ws = build_desktop_ws(tmp_path, sys_size=size, font_scale=1, panel_diagonal_in=7.0)
    ws.look.set_theme("moy", persist=False, variant="light")
    if restored_editor:
        ws.system.set("win_full", {"menu": False})
    ws.open_desk()
    _frames(ws)
    return ws


def _frames(ws, n=3):
    for _ in range(n):
        if ws.pointer is not None:
            ws.pointer.visible = False
        ws._toast_until = 0
        ws.show_fps = False
        ws._dirty = True
        ws.frame(DT)


def _drv(ws):
    from runtime import host_app
    return host_app.ConsoleDriver(ws)


def _tap(ws, drv, x, y):
    drv.click(x, y)
    drv.frame(DT)
    _frames(ws, 2)


def _open(ws, app):
    ws.open_app(ws._apps_by_id[app])
    _frames(ws)
    return ws.wm._wins[app]


def _btn(ws, win, name):
    return dict(ws.wm._strip_buttons(win))[name]


def _centre(r):
    return r[0] + r[2] // 2, r[1] + r[3] // 2


def _px(cv, x, y):
    i = (y * cv.w + x) * 2
    return cv._buf[i] << 8 | cv._buf[i + 1]


def test_the_strip_control_enters_and_the_corner_restore_leaves(tmp_path):
    ws = _desk(tmp_path)
    drv = _drv(ws)
    win = _open(ws, "files")
    was = (win.x, win.y, win.w, win.h)
    _tap(ws, drv, *_centre(_btn(ws, win, "max")))
    assert win.full() and (win.x, win.y, win.w, win.h) == (0, 0, 1024, 600)
    assert ws.wm.full_cover() == ws.wm._order.index("files")
    # the row above the content: Files draws no band of its own
    assert win.row == ws.wm._row_h() == 40
    assert win.content_rect() == (0, 40, 1024, 560)
    assert (win.buf.w, win.buf.h) == (1024, 560)
    assert win.ctx.layout.w == 1024 and win.ctx.layout.h == 560
    # the corner's controls: minimise, restore and close, right to left
    names = [n for n, _r in ws.wm._strip_buttons(win)]
    assert names[:3] == ["close", "max", "min"]
    for _n, r in ws.wm._strip_buttons(win):
        assert r[1] >= 0 and r[1] + r[3] <= 40
    _tap(ws, drv, *_centre(_btn(ws, win, "max")))
    assert not win.full() and (win.x, win.y, win.w, win.h) == was   # unchanged
    assert ws.wm.full_cover() == -1


def test_f11_toggles_and_esc_never_leaves(tmp_path):
    ws = _desk(tmp_path)
    drv = _drv(ws)
    win = _open(ws, "calc")
    was = (win.x, win.y, win.w, win.h)
    drv.full_key()                        # the host's and the page's F11
    drv.frame(DT)
    assert win.full()
    drv.press("b")                        # B: the app's
    drv.frame(DT)
    assert win.full()
    if drv.takes_escape():                # Esc: the host's key, a game's
        drv.escape()
        drv.frame(DT)
    assert win.full()
    drv.full_key()
    drv.frame(DT)
    assert not win.full() and (win.x, win.y, win.w, win.h) == was


class _Keys:
    """A frame's input that pressed `name` alone."""

    last_key = 0
    text_mode = False

    def __init__(self, name):
        self.name = name

    def steps(self, n):
        return int(bool(self.pressed(n)))

    def pressed(self, name, player=None):
        return name == self.name

    def held(self, name, player=None):
        return False


def test_a_keyboard_s_full_button_toggles_the_focused_window(tmp_path):
    """A board's keyboards press the `full` button (a BLE keyboard's F11):
    the windows' key route answers it before the window's content."""
    ws = _desk(tmp_path)
    win = _open(ws, "calc")
    assert ws.wm._route_key(_Keys("full")) and win.full()
    ws.wm._route_key(_Keys("stop"))       # a keyboard's Esc: never leaves
    assert win.full()
    assert ws.wm._route_key(_Keys("full")) and not win.full()


def test_a_ble_keyboard_s_f11_is_the_full_button():
    from test_input_sources import _Ble, device_input
    inp = device_input.InputState()
    inp.text_mode = False
    ble = _Ble(inp)
    ble.press(0x44)                       # F11
    ble.poll()
    inp.begin_frame()
    assert inp.pressed("full") and inp.last_key == 0


def test_the_bar_and_its_chips_are_hidden_and_the_desk_is_not_drawn(tmp_path, monkeypatch):
    ws = _desk(tmp_path)
    win = _open(ws, "files")
    ws.wm._toggle_max(win)
    _frames(ws)
    drawn = []
    real = ws.bar_layer._draw_status_strip
    monkeypatch.setattr(ws.bar_layer, "_draw_status_strip",
                        lambda where: (drawn.append(where), real(where)))
    chips = []
    monkeypatch.setattr(ws.wm, "_draw_taskbar_chips", lambda quiet=False: chips.append(1))
    desk = []
    bd = ws.wm._backdrop_layer
    monkeypatch.setattr(bd, "_draw_desktop", lambda dt: desk.append(1))
    _frames(ws, 4)
    assert "desk" not in drawn and not chips and not desk
    # a tap where a chip was is the row's, not the chip's
    for _k, rect, _label in ws.wm._chip_rects():
        assert ws.wm._win_at(*_centre(rect)) == "files"
    # the glass's top row is the band's ground, the bar's colours nowhere
    sc = ws.wm._root_canvas
    band = _px(sc, 600, 4)
    assert all(_px(sc, x, 4) == band for x in range(200, 800, 50))
    ws.wm._toggle_max(win)
    _frames(ws)
    assert chips and (desk or "desk" in drawn)


def test_windows_under_a_full_screen_one_are_not_drawn(tmp_path, monkeypatch):
    ws = _desk(tmp_path)
    _open(ws, "calc")
    win = _open(ws, "files")
    ws.wm._toggle_max(win)
    _frames(ws)
    seen = []
    real = ws.wm._draw_app_window
    monkeypatch.setattr(ws.wm, "_draw_app_window",
                        lambda w, f, dt: (seen.append(w.kind), real(w, f, dt)))
    _frames(ws, 4)
    assert "calc" not in seen and "files" in seen


@pytest.mark.parametrize("name", ["min", "max", "close"])
def test_the_corner_controls_take_a_tap_32_pixels_wide_and_tall(tmp_path, name):
    ws = _desk(tmp_path)
    win = _open(ws, "files")
    ws.wm._toggle_max(win)
    _frames(ws)
    cx, cy = _centre(_btn(ws, win, name))
    hit = ws.wm._strip_button_hit
    xs = [x for x in range(cx - 40, cx + 41) if hit(win, x, cy) == name]
    ys = [y for y in range(0, 80) if hit(win, cx, y) == name]
    assert len(xs) >= _TAP_FLOOR and xs == list(range(xs[0], xs[-1] + 1)), xs
    assert len(ys) >= _TAP_FLOOR and ys[0] == 0, ys


def test_the_corner_minimises_and_closes(tmp_path):
    ws = _desk(tmp_path)
    drv = _drv(ws)
    win = _open(ws, "files")
    ws.wm._toggle_max(win)
    _frames(ws)
    _tap(ws, drv, *_centre(_btn(ws, win, "min")))
    assert win.minimized and ws.wm.full_cover() == -1     # the bar is back
    ws.wm._chip_tap("files")                               # its chip: full screen again
    _frames(ws)
    assert not win.minimized and win.full() and ws.wm.full_cover() >= 0
    _tap(ws, drv, *_centre(_btn(ws, win, "close")))
    assert "files" not in ws.wm._wins


def test_a_band_app_s_band_is_its_row(tmp_path):
    """The Editor full screen: its content takes the glass, its band is the
    row, and the band's zone lies between the title slot and the corner."""
    ws = _desk(tmp_path)
    ws.open_in_editor(next(c for c in ws.carts.all if c.get("title") == "Star Catcher"))
    _frames(ws)
    wm = ws.wm
    win = wm._wins["menu"]
    assert win.full(), "the Editor opens full screen the first time (E-04)"
    assert win.row == 0 and win.content_rect() == (0, 0, 1024, 600)
    wm._install(win.ctx, win)
    try:
        x0, x1 = wm.band_span()
        bar = win.ctx.layout.bars[0]
        zone = ws.bar_layer.lent_zone("menu", bar)
    finally:
        wm._install(wm._root_ctx)
    assert zone[0] == x0 and zone[0] + zone[2] == x1
    assert x0 == 16 + 144                          # the title slot at 1024
    assert x1 < min(r[0] for _n, r in wm._strip_buttons(win))
    assert wm._full_title(win) == "Star Catcher"
    assert wm.band_span() is None                  # the root's layouts: no row


def test_full_screen_is_remembered_per_board(tmp_path):
    ws = _desk(tmp_path)
    win = _open(ws, "files")
    ws.wm._toggle_max(win)
    assert ws.system.get("win_full")["files"] is True
    ws.wm.close_window_kind("files")
    _frames(ws)
    win = _open(ws, "files")
    assert win.full() and (win.x, win.y, win.w, win.h) == (0, 0, 1024, 600)
    ws.wm._toggle_max(win)
    assert ws.system.get("win_full")["files"] is False
    ws.wm.close_window_kind("files")
    _frames(ws)
    assert not _open(ws, "files").full()


def test_a_restored_editor_stays_restored(tmp_path):
    ws = _desk(tmp_path, restored_editor=True)
    ws.open_in_editor(next(c for c in ws.carts.all if c.get("title") == "Star Catcher"))
    _frames(ws)
    assert not ws.wm._wins["menu"].full()


@pytest.mark.parametrize("size,scale", [((1024, 600), 2), ((1280, 800), 3), ((800, 480), 1)])
def test_a_full_screen_game_is_the_largest_scale_clear_of_the_corner(tmp_path, size, scale):
    """A playing game's window full screen: the game alone at the largest
    whole scale that leaves the corner's controls clear, on black; no
    row. At 800 x 480 the 2x game would run under the controls, so it is
    1x."""
    ws = _desk(tmp_path, size)
    open_cart(ws, "Star Catcher")
    _frames(ws)
    wm = ws.wm
    win = wm._wins["desktop"]
    wm._toggle_max(win)
    _frames(ws, 4)
    assert win.full() and win.row == 0
    ox, oy, s = wm._player_view(win)
    assert s == scale
    gw, gh = ws.canvas.w * s, ws.canvas.h * s
    corner = [r for _n, r in wm._strip_buttons(win)]
    kx = min(r[0] for r in corner)
    assert ox + gw <= kx or oy >= wm._row_h()
    sc = wm._root_canvas
    # black round the game, but under the corner's controls
    for x, y in ((2, 2), (2, size[1] - 3), (ox - 1 if ox else 0, size[1] // 2)):
        if not (ox <= x < ox + gw and oy <= y < oy + gh):
            assert _px(sc, x, y) == 0, (x, y)
    # the controls answer over the black
    drv = _drv(ws)
    _tap(ws, drv, *_centre(_btn(ws, win, "max")))
    assert not win.full()
