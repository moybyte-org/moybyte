"""The page's start option `?edit=<cart>&tab=<tab>&play=1` (web_boot.start_edit)
on the desk: the Editor opens as a window with the desk around it in sight,
not full screen as the Editor's first open is (E-04), unless this browser
left it full screen; `play` runs the cart in the Editor's Game pane. The
project site's hero starts the desk so (site/build.py)."""

import os
import sys

import pytest

from ws_helpers import build_desktop_ws

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DT = 1 / 30


@pytest.fixture
def web_boot():
    for p in ("firmware/web_runner", "device", "runtime"):
        d = os.path.join(ROOT, p)
        if d not in sys.path:
            sys.path.insert(0, d)
    try:
        import web_boot
    except Exception as exc:  # noqa: BLE001 -- a wasm-only dep would skip, not fail
        pytest.skip("web_boot not importable on the host: %s" % exc)
    saved = dict(web_boot._S)
    yield web_boot
    web_boot._S.clear()
    web_boot._S.update(saved)


def _start(web_boot, tmp_path, play, left=None):
    ws = build_desktop_ws(tmp_path, font_scale=1, panel_diagonal_in=7.0)
    if left is not None:
        ws.system.set("win_full", {"menu": left})
    web_boot._S["ws"] = ws
    web_boot._S["exit"] = lambda: None
    got = web_boot.start_edit("moybyte.star_catcher.moy", "code", play)
    for _ in range(5):
        ws.frame(DT)
    return ws, got


@pytest.mark.parametrize("play", [False, True])
def test_the_desk_starts_with_the_editor_in_a_window(web_boot, tmp_path, play):
    ws, got = _start(web_boot, tmp_path, play)
    assert '"ok": true' in got and ws.menu_view == "code"
    win = ws.wm.windows["menu"]
    assert not win.full()
    assert win.w < ws.wm.root_canvas.w and win.h < ws.wm.root_canvas.h
    assert ws.editor_app.playing() is play


def test_a_browser_that_left_the_editor_full_screen_gets_it_so(web_boot, tmp_path):
    ws, _got = _start(web_boot, tmp_path, False, left=True)
    assert ws.wm.windows["menu"].full()
