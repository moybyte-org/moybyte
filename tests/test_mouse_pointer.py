"""A mouse beside the keyboard: the wheel, the middle button and the left
button's text gestures, in the one input table (native/moy_input) and the
shared text editor (`editors_code.TextMouse`, which the Code tab and an
editor handle -- Notes' -- both use).

  * the wheel's notches and the pointer's buttons are a source's, summed and
    edged by the merge; the host's and the browser's events reach them;
  * the public UI engine scrolls the page under the pointer with the wheel
    and drags it with the middle button;
  * in the text views the wheel scrolls, the middle button autoscrolls (an
    exact trajectory under an injected dt), and a mouse's press, drag,
    Shift+press, double and triple press select -- while a finger's drag
    still pans.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import editors_code, moy_carts, moy_input as mi, web_input  # noqa: E402
from ws_helpers import build_ws, make_drv  # noqa: E402

DT = 1 / 30


# -- the table ----------------------------------------------------------------

def test_the_wheel_is_summed_once_a_merge_and_the_buttons_edged():
    t = mi.InputTable()
    a, b = t.source("mouse"), t.source("ble")
    a.scroll(0, 2)
    a.scroll(1, -1)
    b.scroll(0, 3)
    t.begin_frame()
    assert (t.wheel_x, t.wheel_y) == (1, 4)
    t.begin_frame()
    assert (t.wheel_x, t.wheel_y) == (0, 0), "a notch is one frame's"
    a.pointer_buttons = mi.PB_MIDDLE
    t.begin_frame()
    assert t.pointer_buttons == mi.PB_MIDDLE and t.pointer_pressed == mi.PB_MIDDLE
    t.begin_frame()
    assert t.pointer_buttons == mi.PB_MIDDLE and t.pointer_pressed == 0
    a.pointer_buttons = 0
    t.begin_frame()
    assert t.pointer_buttons == 0


def test_the_pages_wheel_and_middle_button_reach_the_table(tmp_path):
    ws = build_ws(tmp_path)
    d = make_drv(ws)
    web_input.apply_events(
        [{"type": "wheel", "dx": 0, "dy": 2, "mods": mi.SHIFT},
         {"type": "mdown", "x": 40, "y": 50}],
        d.input, d.pointer, on_wheel=d.wheel, on_middle=d.middle, on_mods=d.set_mods)
    ws.input.begin_frame()
    assert ws.input.wheel_y == 2 and ws.input.mods & mi.SHIFT
    assert ws.input.pointer_buttons == mi.PB_MIDDLE
    assert (d.pointer.x, d.pointer.y) == (40, 50)


# -- the engine ---------------------------------------------------------------

def _settings(tmp_path):
    from test_settings_scroll import _drv, _force_ota_rows, _ws
    ws = _ws(tmp_path)
    _force_ota_rows(ws)
    d = _drv(ws)
    lay = ws.layout
    x, y = lay.cx + lay.cw // 2, lay.cy + lay.ch * 2 // 3
    d.hover(x, y)
    d.frame(DT)
    return ws, d, x, y


def _band(ws):
    from test_settings_scroll import _band
    return _band(ws)


def _frames(d, n=2):
    """n frames, then on until the page's glide lands (a notch glides)."""
    for _ in range(n):
        d.frame(DT)
    for _ in range(60):
        if not d.ws.settings_layer._host.moving:
            return
        d.frame(DT)


def test_the_wheel_scrolls_the_page_under_the_pointer(tmp_path):
    ws, d, _x, _y = _settings(tmp_path)
    top = _band(ws)
    d.wheel(0, 1)
    _frames(d)
    down = _band(ws)
    assert down != top, "a notch down scrolled the rows"
    d.wheel(0, 1, mi.SHIFT)             # Shift turns it across: no page does
    _frames(d)
    assert _band(ws) == down
    d.wheel(0, -1)
    _frames(d)
    assert _band(ws) == top, "and a notch up brings them back"


def test_the_middle_button_drags_the_page_as_far_as_a_finger(tmp_path):
    ws, d, x, y = _settings(tmp_path)
    d.middle(x, y, True)
    _frames(d, 1)
    for i in range(1, 7):
        d.hover(x, y - 5 * i)
        _frames(d, 1)
    d.middle(x, y - 30, False)
    _frames(d)
    by_middle = _band(ws)
    ws2, d2, x2, y2 = _settings(tmp_path / "finger")
    from test_settings_scroll import _drag
    _drag(ws2, d2, x2, y2, -30, steps=6)
    _frames(d2)
    assert by_middle == _band(ws2)
    assert not (ws.settings_layer.wifi_view or ws.settings_layer.bt_view), "and taps nothing"


# -- the autoscroll, an exact trajectory ------------------------------------------

def _ed(n=200):
    ed = editors_code.CodeEditor("\n".join("line %d" % i for i in range(n)), 30, 10)
    return ed


def test_the_autoscroll_speed_grows_past_a_dead_zone_and_is_capped():
    m = editors_code.TextMouse()
    ed = _ed()
    m.middle(50, 100, True)
    assert m.armed
    # inside the dead zone (one line, 10 px): nothing
    assert [m.autoscroll(ed, 50, 108, DT, 10) for _ in range(30)] == [0] * 30
    # one line past it: 8 rows a second, 33 ms frames -> 264 milli-rows each
    got = [m.autoscroll(ed, 50, 120, DT, 10) for _ in range(8)]
    assert got == [0, 0, 0, 1, 0, 0, 0, 1], got
    # far past it: capped at 60 rows a second (1980 milli-rows a frame)
    m.stop()
    m.middle(50, 100, True)
    got = [m.autoscroll(ed, 50, 600, DT, 10) for _ in range(4)]
    assert got == [1, 2, 2, 2], got
    # above the anchor scrolls up
    top = ed.top
    assert m.autoscroll(ed, 50, -400, DT, 10) < 0 and ed.top < top


def test_a_middle_click_latches_and_a_press_and_drag_runs_only_while_held():
    m = editors_code.TextMouse()
    ed = _ed()
    m.middle(50, 100, True)
    m.middle(50, 100, False)            # a click: it latches
    assert m.armed and m.latched
    assert sum(m.autoscroll(ed, 50, 140, DT, 10) for _ in range(30)) > 0
    m.middle(50, 140, True)             # the next click ends it
    m.middle(50, 140, False)
    assert not m.armed
    m.middle(50, 100, True)
    m.autoscroll(ed, 50, 140, DT, 10)   # it moved: a drag
    m.middle(50, 140, False)
    assert not m.armed, "released, a dragged run ends"


def test_the_caret_does_not_move_while_it_autoscrolls():
    m = editors_code.TextMouse()
    ed = _ed()
    m.middle(50, 100, True)
    m.middle(50, 100, False)
    for _ in range(60):
        m.autoscroll(ed, 50, 200, DT, 10)
    assert ed.top > 0 and (ed.row, ed.col) == (0, 0)


# -- the left button in the text --------------------------------------------------

def test_press_drag_shift_double_and_triple_select():
    m = editors_code.TextMouse()
    ed = editors_code.CodeEditor("alpha beta_2 gamma\nsecond line", 40, 10)
    m.press(ed, 0, 2, False, 1000)
    m.drag(ed, 0, 9)
    m.release(ed)
    assert ed.selected_text() == "pha bet"
    m.press(ed, 1, 3, False, 5000)
    m.press(ed, 0, 14, True, 9000)      # Shift extends from the caret
    assert ed.selected_text() == "amma\nsec"
    m.press(ed, 0, 8, False, 20000)
    m.press(ed, 0, 8, False, 20200)     # a double press: the word
    assert ed.selected_text() == "beta_2"
    m.press(ed, 0, 8, False, 20400)     # a triple: the line
    assert ed.selected_text() == "alpha beta_2 gamma\n"
    m.press(ed, 0, 8, False, 21000)     # too late: one press again
    assert not ed.has_selection()


def _code(tmp_path, text):
    ws = build_ws(tmp_path)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws.set_menu_view("code")
    ed = ws.editor
    ed.set_text(text)
    d = make_drv(ws)
    d.frame(DT)
    return ws, d, ed


def _code_xy(ws, ed, row, col):
    lay = ws.code_layout
    x0 = ws.code_layer._text_x0(lay, ed)
    return x0 + col * lay.cell + lay.cell // 2, lay.y0 + (row - ed.top) * lay.lh + lay.lh // 2


def test_a_mouse_selects_in_the_code_tab_and_a_finger_still_pans(tmp_path):
    ws, d, ed = _code(tmp_path, "\n".join("row%d = %d" % (i, i) for i in range(80)))
    x, y = _code_xy(ws, ed, 1, 0)
    x2, y2 = _code_xy(ws, ed, 1, 4)
    d.touch(x, y)                       # a finger: the press places, the drag pans
    d.frame(DT)
    d.touch_drag(x, y - 3 * ws.code_layout.lh)
    d.frame(DT)
    d.touch_up()
    d.frame(DT)
    assert not ed.has_selection()
    ed.top = 0
    d.hover(x, y)                       # a mouse hovers: its drag selects
    d.frame(DT)
    d.touch(x, y)
    d.frame(DT)
    d.touch_drag(x2, y2)
    d.frame(DT)
    d.touch_up()
    d.frame(DT)
    assert ed.selected_text() == "row1"


def test_the_wheel_scrolls_the_code_and_shift_scrolls_it_across(tmp_path):
    ws, d, ed = _code(tmp_path, "\n".join("x" * 200 for _ in range(80)))
    d.wheel(0, 2)
    d.frame(DT)
    assert ed.top == 6 and (ed.row, ed.col) == (0, 0)
    d.wheel(0, 1, mi.SHIFT)
    d.frame(DT)
    assert ed.left == 3 and ed.top == 6


def test_the_code_tab_autoscrolls_until_a_key(tmp_path):
    ws, d, ed = _code(tmp_path, "\n".join("v%d = 1" % i for i in range(300)))
    x, y = _code_xy(ws, ed, 2, 0)
    d.hover(x, y)
    d.middle(x, y, True)
    d.frame(DT)
    d.middle(x, y, False)               # a click latches it
    d.frame(DT)
    d.hover(x, y + 6 * ws.code_layout.lh)
    for _ in range(30):
        d.frame(DT)
    assert ed.top > 0 and ws.code_layer.autoscrolling()
    assert (ed.row, ed.col) == (0, 0), "the caret stays"
    d.key_down(mi.KEY_DOWN)
    d.frame(DT)
    d.key_up(mi.KEY_DOWN)
    assert not ws.code_layer.autoscrolling(), "a key ends it"


# -- an editor handle (Notes') ------------------------------------------------------

def _handle(ws, name):
    ctx = ws.app_context("probe", ("files", "clipboard"))
    return ws.player._open_cart_editor(ctx.files, "docs", name, None, ws.canvas, ctx.clipboard)


def test_an_editor_handle_takes_the_wheel_the_middle_button_and_mouse_selection(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "long", "\n".join("word%d and more" % i for i in range(80)),
                        ws.carts_root)
    h = _handle(ws, "long")
    h.draw(0, 0, 320, 200)
    inp = ws.input
    src = inp.source("mouse")
    src.scroll(0, 2)
    inp.begin_frame()
    assert h.mouse_frame(inp, 100, 100, DT) and h.ed.top == 6
    inp.begin_frame()
    src.pointer_buttons = mi.PB_MIDDLE
    inp.begin_frame()
    h.mouse_frame(inp, 100, 100, DT)
    src.pointer_buttons = 0
    inp.begin_frame()
    h.mouse_frame(inp, 100, 100, DT)
    assert h.autoscrolling() == (100, 100)
    top = h.ed.top
    for _ in range(30):
        h.mouse_frame(inp, 100, 160, DT)
    assert h.ed.top > top
    h.key(0x1B)
    assert h.autoscrolling() is None, "a key ends it"
    # a mouse's press and drag select; a double press takes the word
    h.ed.top = 0
    h.draw(0, 0, 320, 200)
    ws.pointer.hovers = True
    x0, _y0, cell, _lh, _s = h._geom
    y = next(yy for brow, si, yy in h._visible() if brow == 0 and si == 0) + 2
    h.tap(x0 + cell // 2, y)
    h.drag(x0 + 5 * cell + cell // 2, y, True)
    assert h.ed.selected_text() == "word0"
    h.drag(0, 0, False)
    h.tap(x0 + 7 * cell + cell // 2, y)
    h.tap(x0 + 7 * cell + cell // 2, y)
    assert h.ed.selected_text() == "and"


def test_a_click_between_two_merges_is_held_for_one_frame():
    t = mi.InputTable()
    a = t.source("mouse")
    a.pointer_buttons = mi.PB_MIDDLE
    a.pointer_buttons = 0
    t.begin_frame()
    assert t.pointer_buttons == mi.PB_MIDDLE and t.pointer_pressed == mi.PB_MIDDLE
    t.begin_frame()
    assert t.pointer_buttons == 0


def test_the_code_window_autoscrolls_in_its_own_space(tmp_path):
    """On the desk the Code tab is a window: the anchor and the pointer are
    the window's content coordinates, not the glass's."""
    from ws_helpers import build_desktop_ws
    ws = build_desktop_ws(tmp_path)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws.set_menu_view("code")
    ed = ws.editor
    ed.set_text("\n".join("v%d = 1" % i for i in range(300)))
    d = make_drv(ws)
    for _ in range(3):
        d.frame(DT)
    lay = ws.code_layout
    cx, cy, _w, _h = ws.wm._wins[ws.wm._focus].content_rect()
    x, y = cx + ws.code_layer._text_x0(lay, ed) + 10, cy + lay.y0 + 4 * lay.lh
    d.hover(x, y)
    d.frame(DT)
    d.middle(x, y, True)
    d.frame(DT)
    d.middle(x, y, False)
    d.frame(DT)
    assert ws.code_layer.autoscrolling()
    d.hover(x, y + 8 * lay.lh)
    for _ in range(30):
        d.frame(DT)
    assert ed.top > 0 and ws.code_layer.autoscrolling()


# -- the desk: a held press is its window's, a window-bound app its window's size --

def _desk_files(tmp_path, n=60):
    """Files on the desk over `n` drawings, its Art place open: (ws, driver,
    a point on the list, the window's content rect)."""
    from ws_helpers import build_desktop_ws
    from test_files_app import _files, _frames, _press, _seed_drawing
    ws = build_desktop_ws(tmp_path)
    for i in range(n):
        _seed_drawing(str(tmp_path / "carts"), "pic%02d" % i, 8, 8, i % 16)
    _app, d = _files(ws)
    _press(d, "down", "down", "a")
    cr = ws.wm._wins[ws.wm._focus].content_rect()
    x, y = cr[0] + cr[2] // 2, cr[1] + cr[3] * 3 // 4
    d.hover(x, y)
    _frames(d)
    return ws, d, x, y, cr


def _shot(ws):
    import hashlib
    return hashlib.sha1(bytes(ws.sys_canvas._buf)).hexdigest()


@pytest.mark.parametrize("by", ["middle", "finger"])
def test_a_drag_that_leaves_the_window_still_scrolls_it(tmp_path, by):
    """A middle-button drag (or a finger's) that went down on a window's
    list keeps scrolling it once the pointer is past the window's top, and
    its release there ends it: the press is the window's until it comes up."""
    from test_files_app import _frames
    ws, d, x, y, cr = _desk_files(tmp_path)
    if by == "middle":
        d.middle(x, y, True)
    else:
        d.touch(x, y)
    _frames(d, 1)
    out = []
    for yy in range(y - 20, -1, -20):
        if by == "middle":
            d.hover(x, yy)
        else:
            d.touch_drag(x, yy)
        _frames(d, 1)
        if yy < cr[1]:
            out.append(_shot(ws))
    assert len(set(out)) == len(out) >= 3, "the list stopped when the pointer left the window"
    if by == "middle":
        d.middle(x, 0, False)
    else:
        d.touch_up()
    _frames(d)
    assert ws.wm._held_in is None


def test_a_window_bound_app_lays_out_in_its_window_and_scrolls_to_its_end(tmp_path):
    """Notes on the desk lays out in the content its window shows -- the
    size the window opened at on this desk, clamped to it -- so the wheel and
    the middle button bring its last note into the window."""
    from ws_helpers import build_desktop_ws, open_cart
    from runtime import ui_draw
    ws = build_desktop_ws(tmp_path)
    for i in range(40):
        moy_carts.save_file("docs", "note%02d.md" % i, b"hello\n", str(tmp_path / "carts"))
    d = make_drv(ws)
    d.frame(DT)
    open_cart(ws, "Notes")
    for _ in range(4):
        d.frame(DT)
    cx, cy, cw, ch = ws.wm._wins["desktop"].content_rect()
    assert tuple(ws.player.ns["content_rect"]()[2:]) == (cw, ch)
    x, y = cx + 60, cy + ch // 2
    d.hover(x, y)
    d.frame(DT)
    for _ in range(30):
        d.wheel(0, 1)
        d.frame(DT)
        d.frame(DT)
    bx, by, bw, bh = ui_draw._C.api_box(ws.player._ui[0], "vault")
    assert by + bh <= ch, "the list runs on under the window's bottom"
