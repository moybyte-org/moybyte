"""A dialog opened in an app on the verbs costs the frames that change the
glass and no more (native/moy_ui/moy_ui_api.c, `moy_ui_api_begin`): the frame
that opens it draws the card and owes the next, on which focus opens on its
first button; that owed frame owes nothing, so the frame after it is no
frame. Files' "Empty the Trash?" on the handheld and on the desk."""

import pytest

from runtime import host_app
from ws_helpers import build_desktop_ws, build_ws, no_unlock_banner, quiesce

DT = 1 / 30


@pytest.mark.parametrize("desk", (False, True), ids=("handheld", "desk"))
def test_the_frame_after_a_dialogs_owed_frame_paints_nothing(tmp_path, desk):
    ws = (build_desktop_ws if desk else build_ws)(tmp_path)
    assert ws.spine.open_app(ws.app("files")) is not False
    ws.bar_layer.clock_text = lambda: "10:30"
    no_unlock_banner(ws)
    quiesce(ws)
    drv = host_app.ConsoleDriver(ws)
    for _ in range(30):
        drv.frame(DT)
    n = ws.frame_state.frames_drawn
    for _ in range(5):
        drv.frame(DT)
    assert ws.frame_state.frames_drawn == n, "Files alone idles"
    files = ws.app("files")
    files = getattr(files, "_inst", files)
    files.trash = (("drawings", "Old"),)
    files._act("EMPTY")
    ws.mark_dirty()
    cv = ws.wm.root_canvas if desk else ws.sys_canvas
    last = bytes(cv._buf)
    painted = []
    for i in range(20):
        k = ws.frame_state.frames_drawn
        drv.frame(DT)
        if ws.frame_state.frames_drawn != k:
            now = bytes(cv._buf)
            painted.append((i, now != last))
            last = now
    assert files.dialog == "empty"
    assert painted == [(0, True), (1, True)], (
        "the open and its owed frame each change the glass, then it idles: %r" % painted)
