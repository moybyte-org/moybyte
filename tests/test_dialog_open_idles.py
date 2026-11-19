"""A dialog or a popover opened costs the frames that change the glass and no
more (native/moy_ui/moy_ui_api.c, `moy_ui_api_begin`; `AppHost.due`): the
frame that opens it draws the card and owes the next, on which focus opens in
it; that owed frame owes nothing, and on a canvas that retains one buffer the
focus it moved is already the glass, so the frame after it is no frame.
Files' "Empty the Trash?" and the console's delete question over the home, on
the handheld and on the desk; a bar popover, a page on the menu's panel,
owes nothing past the frame that opens it."""

import pytest

from runtime import host_app, moy_carts
from ws_helpers import build_desktop_ws, build_ws, no_unlock_banner, quiesce, shelf

DT = 1 / 30


def _idle(ws):
    ws.bar_layer.clock_text = lambda: "10:30"
    no_unlock_banner(ws)
    quiesce(ws)
    drv = host_app.ConsoleDriver(ws)
    for _ in range(30):
        drv.frame(DT)
    n = ws.frame_state.frames_drawn
    for _ in range(5):
        drv.frame(DT)
    assert ws.frame_state.frames_drawn == n, "the console idles before the open"
    return drv


def _painted(ws, drv, cv):
    """The frames painted over the next twenty loop passes: (pass, whether
    the glass changed from the frame before)."""
    last = bytes(cv._buf)
    painted = []
    for i in range(20):
        k = ws.frame_state.frames_drawn
        drv.frame(DT)
        if ws.frame_state.frames_drawn != k:
            now = bytes(cv._buf)
            painted.append((i, now != last))
            last = now
    return painted


@pytest.mark.parametrize("desk", (False, True), ids=("handheld", "desk"))
def test_the_frame_after_a_dialogs_owed_frame_paints_nothing(tmp_path, desk):
    ws = (build_desktop_ws if desk else build_ws)(tmp_path)
    assert ws.spine.open_app(ws.app("files")) is not False
    drv = _idle(ws)
    files = ws.app("files")
    files = getattr(files, "_inst", files)
    files.trash = (("drawings", "Old"),)
    files._act("EMPTY")
    ws.mark_dirty()
    painted = _painted(ws, drv, ws.wm.root_canvas if desk else ws.sys_canvas)
    assert files.dialog == "empty"
    assert painted == [(0, True), (1, True)], (
        "the open and its owed frame each change the glass, then it idles: %r" % painted)


@pytest.mark.parametrize("desk", (False, True), ids=("handheld", "desk"))
def test_the_delete_question_over_the_home_paints_no_repeat(tmp_path, desk):
    ws = (build_desktop_ws if desk else build_ws)(tmp_path)
    moy_carts.create("Extra", str(ws.carts_root), src="def _draw():\n    cls(1)\n", type="game")
    ws.carts.apply(shelf(str(ws.carts_root)))
    if not desk:
        ws.home.set_home_tab("make")
    drv = _idle(ws)
    ws.confirm.ask(next(c for c in ws.carts.all if c.get("title") == "Extra"))
    painted = _painted(ws, drv, ws.wm.root_canvas if desk else ws.sys_canvas)
    assert ws.confirm.open
    assert painted == [(0, True), (1, True)], (
        "the open and its owed frame each change the glass, then it idles: %r" % painted)


@pytest.mark.parametrize("desk", (False, True), ids=("handheld", "desk"))
@pytest.mark.parametrize("widget", ("wifi", "battery", "status"))
def test_a_bar_popover_opens_at_rest_in_one_frame(tmp_path, widget, desk):
    # A bar popover is a page on the menu's panel, not a ui.popover card: its
    # focus lands on the frame that opens it, which owes nothing.
    ws = (build_desktop_ws if desk else build_ws)(tmp_path)
    cv = ws.wm.root_canvas if desk else ws.sys_canvas
    drv = _idle(ws)
    ws.toggle_popover(widget)
    painted = _painted(ws, drv, cv)
    assert ws.sysmenu.open
    assert painted == [(0, True)], "the open alone changes the glass: %r" % painted
    shown = bytes(cv._buf)
    ws.mark_dirty()
    drv.frame(DT)
    assert bytes(cv._buf) == shown, "the frame that opened it is the frame at rest"
