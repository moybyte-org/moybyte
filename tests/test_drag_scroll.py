"""A drag scrolls content by the distance the finger moved, in both
directions: the Code tab's pan (`code_layer._code_drag`) and a text handle's
(`editor_handle.EditorHandle.drag`, the Notes cart's page), both through
`ui_draw.drag_steps`, which keeps the sub-line remainder with the anchor.
A slow drag -- a pixel a frame -- is the case that ran away: floor division
made every upward pixel a whole line."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from ws_helpers import boot_on_fixture, build_ws          # noqa: E402
from runtime import moy_carts, ui_draw                    # noqa: E402

DT = 1 / 30


@pytest.fixture(autouse=True)
def _night(monkeypatch):
    boot_on_fixture(monkeypatch)


@pytest.fixture(autouse=True)
def _keys_after():
    ui_draw.input_kind("keys")
    yield
    ui_draw.input_kind("keys")


def test_drag_steps_round_toward_zero():
    assert [ui_draw.drag_steps(d, 10) for d in (-21, -20, -19, -1, 0, 1, 9, 10, 25)] \
        == [-2, -2, -1, 0, 0, 0, 0, 1, 2]


def _slow(move, x, y, dy, step=1):
    """Drag from (x, y) by `dy` pixels, `step` pixels a frame; the new y."""
    end = y + dy
    sign = 1 if dy > 0 else -1
    while y != end:
        y += sign * min(step, abs(end - y))
        move(x, y)
    return y


# -- the Code tab --------------------------------------------------------------

def _code_ws(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.launcher.sel = 0
    ws.open()
    ws.ach.toast = None
    ws.screen = "menu"
    ws.set_menu_view("code")
    drv = host_app.ConsoleDriver(ws)
    ui_draw.input_kind("touch")
    drv.frame(DT)
    ws.editor.lines = ["line %d" % i for i in range(300)]
    ws.editor.top = 100
    drv.frame(DT)
    return ws, drv


@pytest.mark.parametrize("step", (1, 3))
def test_a_slow_code_drag_moves_the_lines_the_finger_moved(tmp_path, step):
    ws, drv = _code_ws(tmp_path)
    lay = ws.code_layout
    lh = lay.lh
    ax, ay, aw, ah = lay.code_area()
    x, y = ax + aw // 2, ay + ah // 2

    def move(x, y):
        drv.touch_drag(x, y)
        drv.frame(DT)

    drv.touch(x, y)
    drv.frame(DT)
    top = ws.editor.top
    y = _slow(move, x, y, -(4 * lh + lh // 2), step)        # up 4.5 lines
    assert ws.editor.top == top + 4, "up 4.5 lines scrolled %d" % (ws.editor.top - top)
    y = _slow(move, x, y, 2 * lh, step)                     # down 2 lines
    assert ws.editor.top == top + 2
    y = _slow(move, x, y, lh // 2 + 3 * lh, step)           # down 3.5: back past the press
    assert ws.editor.top == top - 1
    drv.touch_up()
    drv.frame(DT)


def test_a_code_drag_past_the_top_need_not_be_undone(tmp_path):
    ws, drv = _code_ws(tmp_path)
    ws.editor.top = 1
    lay = ws.code_layout
    lh = lay.lh
    ax, ay, aw, ah = lay.code_area()
    x, y = ax + aw // 2, ay + ah // 3

    def move(x, y):
        drv.touch_drag(x, y)
        drv.frame(DT)

    drv.touch(x, y)
    drv.frame(DT)
    y = _slow(move, x, y, 5 * lh, 2)                        # down 5: stops at the top
    assert ws.editor.top == 0
    _slow(move, x, y, -lh, 1)                               # up one line
    assert ws.editor.top == 1
    drv.touch_up()
    drv.frame(DT)


# -- a text handle (the Notes page) ---------------------------------------------

def _handle(ws, name):
    ctx = ws.app_context("probe", ("files", "clipboard"))
    return ws.player._open_cart_editor(ctx.files, "docs", name, None,
                                       ws.canvas, ctx.clipboard)


@pytest.mark.parametrize("step", (1, 3))
def test_a_slow_note_drag_moves_the_lines_the_finger_moved(tmp_path, step):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "long", "\n".join("line %d" % i for i in range(300)),
                        ws.carts_root)
    ed = _handle(ws, "long")
    ws.canvas.cls(0)
    ed.draw(0, 0, 320, 200)
    lh = ed._geom[3]
    ed.ed.top = 100
    x, y = 100, 100

    def move(x, y):
        ed.drag(x, y, True)

    move(x, y)                                              # the press origin
    top = ed.ed.top
    y = _slow(move, x, y, -(4 * lh + lh // 2), step)
    assert ed.ed.top == top + 4, "up 4.5 lines scrolled %d" % (ed.ed.top - top)
    y = _slow(move, x, y, 2 * lh, step)
    assert ed.ed.top == top + 2
    _slow(move, x, y, lh // 2 + 3 * lh, step)
    assert ed.ed.top == top - 1
