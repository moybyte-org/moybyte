"""Two windows on the desk keep apart: a window-bound app cart (Notes on a
note page) and a shell app (the Terminal) over it. Each window's title strip
shows its own title, icon and back, and its own pixels; the keyboard reaches
the focused window alone -- typed text, an editor handle's keys, Q and E --
and opening an app leaves the project another surface holds as it was."""

import pytest

from ws_helpers import build_desktop_ws, make_drv, open_cart, quiesce
from test_app_ui import _open, _pmem

DT = 1.0 / 30.0
NOTE = ("story_ideas", "# Story ideas\n\n- [x] A dragon\n")


def _frame(drv):
    quiesce(drv.ws)
    drv.ws._dirty = True
    drv.frame(DT)


def _type(drv, text):
    for ch in text:
        drv.key_down(ord(ch))
        _frame(drv)
        drv.key_up(ord(ch))
        _frame(drv)


def _desk(tmp_path):
    """Notes on its note in a window, the Terminal opened over it and
    focused."""
    ws = build_desktop_ws(tmp_path)
    ws.look.set_theme("moy", persist=False)
    ws.bar_layer._clock_text = lambda: "10:30"
    ws.carts_store.save_file("docs", NOTE[0], NOTE[1], ws.carts_root)
    ws.open_desk()
    drv = make_drv(ws)
    open_cart(ws, "Notes")
    for _ in range(3):
        _frame(drv)
    ws.run.player.ns["_open"](NOTE[0])
    for _ in range(3):
        _frame(drv)
    term = ws._apps_by_id["terminal"]
    ws.open_app(term)
    for _ in range(4):
        _frame(drv)
    assert ws.wm._focus == "terminal"
    return ws, drv, term


def _note_text(ws):
    return ws.run.player._focus_editor.text()


def test_each_window_strip_shows_its_own_title_icon_and_back(tmp_path):
    ws, drv, _term = _desk(tmp_path)
    wm = ws.wm
    notes, term = wm._wins["desktop"], wm._wins["terminal"]
    assert wm._win_title(notes) == "Notes · Story ideas"
    assert wm._win_title(term) == "Terminal"
    assert wm._icon_slot(notes) == "notes"
    assert ws.cart.get("title") == "Notes"      # the running app's project is its own
    shows = lambda w: "back" in dict(wm._strip_buttons(w))
    assert shows(notes) == ws.run.player.has_page()     # its own way back, or none
    assert not shows(term)
    assert wm._chip_title(notes) == "Notes" and wm._chip_title(term) == "Terminal"


def _strip(ws, win):
    cv = ws.glass_canvas
    x0, y0 = max(0, win.x), max(0, win.y)
    x1, y1 = min(cv.w, win.x + win.w), min(cv.h, win.y + win.title_h)
    buf = bytes(cv._buf)
    return [buf[2 * (y * cv.w + x0):2 * (y * cv.w + x1)] for y in range(y0, y1)]


def test_a_window_drawn_under_another_leaves_its_strip(tmp_path):
    """Notes draws its window every frame under the Terminal's, which
    overlaps it: the Terminal's strip on a quiet frame is the one a whole
    repaint draws, never Notes' page."""
    ws, drv, _term = _desk(tmp_path)
    wm = ws.wm
    term = wm._wins["terminal"]
    for _ in range(4):
        _frame(drv)
    wm._win_sig = None                  # a whole repaint: every window and its chrome
    _frame(drv)
    _frame(drv)
    whole = _strip(ws, term)
    _type(drv, "ls")
    for _ in range(4):
        _frame(drv)
    assert _strip(ws, term) == whole


def test_typing_reaches_the_focused_window_alone(tmp_path):
    ws, drv, term = _desk(tmp_path)
    was = _note_text(ws)
    _type(drv, "xyq")
    assert term.face.line() == "xyq"
    assert _note_text(ws) == was, "the Terminal's keys typed into Notes"
    for name in ("left", "right", "up", "down"):    # the arrows step no other caret
        drv.press(name)
        _frame(drv)
        _frame(drv)
    _type(drv, "\x1b")
    assert ws.run.player.ns["ed"] is not None, "the Terminal's Esc closed Notes' note"
    ws.wm._bring_forward("desktop")
    for _ in range(2):
        _frame(drv)
    assert ws.wm._focus == "desktop"
    _type(drv, "ab")
    assert _note_text(ws) != was
    assert "ab" in _note_text(ws)
    assert term.face.line() == "xyq", "Notes' keys typed into the Terminal"


def test_opening_an_app_leaves_the_editors_project(tmp_path):
    ws = build_desktop_ws(tmp_path)
    ws.open_desk()
    drv = make_drv(ws)
    cart = next(c for c in ws.carts.all if c["title"] == "Star Catcher")
    ws.open_in_editor(cart)
    for _ in range(3):
        _frame(drv)
    ws.open_app(ws._apps_by_id["terminal"])
    for _ in range(3):
        _frame(drv)
    assert ws.editor_app.project.cart.get("title") == "Star Catcher"
    assert ws.wm._win_title(ws.wm._wins["menu"]) == "Editor · Star Catcher"


@pytest.mark.parametrize("tier", ("python", "lua"))
def test_q_and_e_step_the_tabs_of_the_focused_window_alone(tmp_path, tier):
    ws = _open("tabs", tier, tmp_path, desk=True)
    drv = make_drv(ws)
    for _ in range(4):
        _frame(drv)
    assert _pmem(ws, 1) == [1]
    term = ws._apps_by_id["terminal"]
    ws.open_app(term)
    for _ in range(3):
        _frame(drv)
    _type(drv, "eeq")
    assert term.face.line() == "eeq"
    assert _pmem(ws, 1) == [1], "the Terminal's E stepped the app's tabs"
    ws.wm._bring_forward("desktop")
    for _ in range(2):
        _frame(drv)
    _type(drv, "e")
    assert _pmem(ws, 1) == [2]
    assert term.face.line() == "eeq"
