"""Sprite/paint editor tools (#90): in-editor undo/redo, bucket flood-fill, and
the whole-sprite transforms (flip / rotate / shift-wrap / clear), plus the tool-row
UI and the host Ctrl+Z/Y shortcut.

The core (PaintEditor) is exercised directly like test_paint_editor_v30's Part B;
the UI paths build the SAME shared console (runtime.host_app) and drive it through
ConsoleDriver (mouse == touch), so the tool buttons + keyboard undo run through the
real pointer/input dispatch the device loop uses.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _pe(cols=16, rows=16):
    from runtime.editors import PaintEditor, SpriteSheet
    return PaintEditor(SpriteSheet(cols=cols, rows=rows))


def _region(pe):
    """The current editable region as a flat list (dim*dim) for assertions."""
    ox, oy = pe._origin()
    return [pe.sheet.pget(ox + (i % pe.dim), oy + (i // pe.dim))
            for i in range(pe.dim * pe.dim)]


# -- undo / redo -------------------------------------------------------------

def test_stroke_undo_redo_round_trips():
    pe = _pe()
    pe.color = 9
    ox, oy = pe._origin()
    assert pe.sheet.pget(ox + 2, oy + 3) == 0
    pe.begin_stroke()
    pe.paint(2, 3)
    pe.paint(4, 5)
    pe.end_stroke()
    assert pe.sheet.pget(ox + 2, oy + 3) == 9 and pe.sheet.pget(ox + 4, oy + 5) == 9
    assert pe.can_undo() and not pe.can_redo()
    assert pe.undo() is True
    assert pe.sheet.pget(ox + 2, oy + 3) == 0 and pe.sheet.pget(ox + 4, oy + 5) == 0
    assert not pe.can_undo() and pe.can_redo()
    assert pe.redo() is True
    assert pe.sheet.pget(ox + 2, oy + 3) == 9 and pe.sheet.pget(ox + 4, oy + 5) == 9


def test_a_whole_drag_is_one_undo_step():
    # begin_stroke..paint*..end_stroke collapses to a single undo entry: one undo
    # reverts the entire stroke, not pixel by pixel.
    pe = _pe()
    pe.color = 3
    pe.begin_stroke()
    for x in range(8):
        pe.paint(x, 0)
    pe.end_stroke()
    assert _region(pe)[:8] == [3] * 8
    pe.undo()
    assert _region(pe)[:8] == [0] * 8
    assert not pe.can_undo()          # exactly one step existed


def test_undo_at_floor_is_a_noop():
    pe = _pe()
    assert pe.undo() is False and pe.redo() is False


def test_empty_stroke_records_nothing():
    # A press that painted the same color that was already there changes no pixels,
    # so it must NOT push an undo step.
    pe = _pe()
    pe.color = 0                       # the sheet is already all 0
    pe.begin_stroke()
    pe.paint(1, 1)
    pe.end_stroke()
    assert not pe.can_undo()


def test_undo_ring_is_bounded():
    pe = _pe()
    for i in range(pe.UNDO_DEPTH + 10):
        pe.color = (i % 15) + 1
        pe.begin_stroke()
        pe.paint(0, 0)
        pe.end_stroke()
    assert len(pe._undo) == pe.UNDO_DEPTH


def test_new_edit_forks_history_dropping_redo():
    pe = _pe()
    pe.color = 5
    pe.begin_stroke(); pe.paint(0, 0); pe.end_stroke()
    pe.undo()
    assert pe.can_redo()
    pe.color = 6
    pe.begin_stroke(); pe.paint(1, 1); pe.end_stroke()
    assert not pe.can_redo()           # the fresh edit dropped the redo stack


def test_undo_restores_the_right_sprite_after_switching():
    # An undo entry carries its own (n, size); undoing re-selects that sprite so the
    # revert is visible even after the kid moved on to another tile.
    pe = _pe()
    pe.color = 7
    pe.begin_stroke(); pe.paint(0, 0); pe.end_stroke()
    pe.select(1)                       # move to sprite 1
    assert pe.n == 1
    pe.undo()
    assert pe.n == 0                   # jumped back to the reverted sprite
    assert pe.sheet.tget(0, 0, 0) == 0


# -- bucket / flood fill -----------------------------------------------------

def test_fill_floods_the_contiguous_region():
    pe = _pe()
    pe.tool = pe.FILL
    pe.color = 4
    pe.fill(0, 0)                       # whole 8x8 tile is one contiguous 0-run
    assert _region(pe) == [4] * 64
    assert pe.can_undo()


def test_fill_on_same_color_is_a_noop():
    pe = _pe()
    pe.color = 0                       # region already all 0
    pe.fill(3, 3)
    assert _region(pe) == [0] * 64
    assert not pe.can_undo()           # no pixels changed -> no undo step


def test_fill_stops_at_a_color_boundary():
    pe = _pe()
    pe.color = 2
    # Wall down column x==4 (rows 0..7) splits the tile in two.
    for y in range(8):
        pe.paint(4, y)
    pe.color = 5
    pe.tool = pe.FILL
    pe.fill(0, 0)                       # fill the LEFT half only
    reg = _region(pe)
    for y in range(8):
        for x in range(8):
            v = reg[y * 8 + x]
            if x < 4:
                assert v == 5          # left of the wall filled
            elif x == 4:
                assert v == 2          # the wall itself
            else:
                assert v == 0          # right of the wall untouched


def test_fill_is_bounded_to_the_sprite_region():
    # A fill in sprite 0 must not bleed into the neighbouring sheet tiles.
    pe = _pe()
    pe.color = 6
    pe.tool = pe.FILL
    pe.fill(0, 0)
    assert pe.sheet.tget(0, 7, 7) == 6         # sprite 0 filled
    assert pe.sheet.tget(1, 0, 0) == 0         # right neighbour untouched
    assert pe.sheet.tget(pe.sheet.cols, 0, 0) == 0   # tile below untouched


def test_fill_undo_reverts():
    pe = _pe()
    pe.color = 8
    pe.tool = pe.FILL
    pe.fill(2, 2)
    assert _region(pe) == [8] * 64
    pe.undo()
    assert _region(pe) == [0] * 64


# -- transforms --------------------------------------------------------------

def test_flip_h_mirrors_left_right():
    pe = _pe()
    pe.color = 5
    pe.paint(0, 0)
    pe.flip_h()
    assert pe.sheet.pget(*_xy(pe, 7, 0)) == 5 and pe.sheet.pget(*_xy(pe, 0, 0)) == 0


def test_flip_v_mirrors_top_bottom():
    pe = _pe()
    pe.color = 5
    pe.paint(0, 0)
    pe.flip_v()
    assert pe.sheet.pget(*_xy(pe, 0, 7)) == 5 and pe.sheet.pget(*_xy(pe, 0, 0)) == 0


def test_rotate_90_clockwise():
    pe = _pe()
    pe.color = 5
    pe.paint(0, 0)                     # top-left
    pe.rotate()
    assert pe.sheet.pget(*_xy(pe, 7, 0)) == 5    # -> top-right
    assert pe.sheet.pget(*_xy(pe, 0, 0)) == 0


def test_rotate_four_times_is_identity():
    pe = _pe()
    pe.color = 9
    pe.paint(1, 0)
    pe.paint(0, 3)
    before = _region(pe)
    for _ in range(4):
        pe.rotate()
    assert _region(pe) == before


def test_shift_wraps_pixels():
    pe = _pe()
    pe.color = 5
    pe.paint(0, 0)
    pe.shift(1, 0)                     # right by one
    assert pe.sheet.pget(*_xy(pe, 1, 0)) == 5
    pe.shift(-1, 0)                    # back left
    assert pe.sheet.pget(*_xy(pe, 0, 0)) == 5
    pe.shift(-1, 0)                    # off the left edge -> wraps to the right
    assert pe.sheet.pget(*_xy(pe, 7, 0)) == 5


def test_clear_blanks_the_sprite():
    pe = _pe()
    pe.color = 5
    pe.paint(1, 1)
    pe.paint(6, 6)
    pe.clear()
    assert _region(pe) == [0] * 64
    pe.undo()
    assert pe.sheet.pget(*_xy(pe, 1, 1)) == 5


def test_transform_respects_multi_tile_size():
    # A 2x2 sprite transforms as one 16x16 block: a pixel in the bottom-right tile
    # mirrors across the whole span, not just its own tile.
    pe = _pe()
    pe.cycle_size()                    # 2x2 (16x16)
    assert pe.dim == 16
    pe.color = 7
    pe.paint(0, 0)                     # top-left of the block
    pe.flip_h()
    assert pe.sheet.pget(*_xy(pe, 15, 0)) == 7   # far side of the 16px span
    assert pe.sheet.pget(*_xy(pe, 0, 0)) == 0


def test_transform_no_change_records_no_undo():
    # Flipping an empty (symmetric) sprite reproduces it -> no undo step.
    pe = _pe()
    pe.flip_h()
    assert not pe.can_undo()


def _xy(pe, lx, ly):
    ox, oy = pe._origin()
    return (ox + lx, oy + ly)


# -- tool-row UI + keyboard shortcut (through the shared console) -------------

def _open_paint(ws):
    from runtime import host_app  # noqa: F401
    ws.launcher.sel = 0
    ws.open()
    ws._open_paint()
    assert ws.menu_view == "paint"


def _tool_center(ws, tid):
    from runtime import paint_layer as P
    rect = ws.paint_layer.layout.tool_btns[P._TOOLS.index(tid)]
    return (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)


def test_fill_tool_button_toggles_and_fills(tmp_path):
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    ws.paint.n = 0
    ws.paint.color = 11
    # Blank the tile so the flood has a single contiguous run to work on.
    ws.paint.clear()

    fx, fy = _tool_center(ws, "fill")
    drv.touch(fx, fy)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.paint.tool == ws.paint.FILL

    # A grid tap now floods the whole 8x8 tile with the current color.
    cx, cy = _cell_center(C, 3, 4, ws.paint.dim)
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    ox, oy = ws.sheet.tile_origin(ws.paint.n)
    assert all(ws.sheet.pget(ox + x, oy + y) == 11
               for y in range(8) for x in range(8))


def test_undo_redo_buttons_revert_a_stroke(tmp_path):
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    ws.paint.color = 13
    ox, oy = ws.sheet.tile_origin(ws.paint.n)
    before = ws.sheet.pget(ox + 2, oy + 2)

    cx, cy = _cell_center(C, 2, 2, ws.paint.dim)
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    drv.touch_up()                     # release commits the stroke as one undo step
    drv.frame(1 / 30)
    assert ws.sheet.pget(ox + 2, oy + 2) == 13

    ux, uy = _tool_center(ws, "undo")
    drv.touch(ux, uy)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.sheet.pget(ox + 2, oy + 2) == before

    rx, ry = _tool_center(ws, "redo")
    drv.touch(rx, ry)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.sheet.pget(ox + 2, oy + 2) == 13


def test_ctrl_z_y_keyboard_shortcut(tmp_path):
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    ws.paint.color = 10
    ox, oy = ws.sheet.tile_origin(ws.paint.n)
    before = ws.sheet.pget(ox + 5, oy + 5)   # seed art may already live here

    cx, cy = _cell_center(C, 5, 5, ws.paint.dim)
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.sheet.pget(ox + 5, oy + 5) == 10

    drv.type_char(0x1A)                # Ctrl+Z
    drv.frame(1 / 30)
    assert ws.sheet.pget(ox + 5, oy + 5) == before

    drv.type_char(0x19)                # Ctrl+Y
    drv.frame(1 / 30)
    assert ws.sheet.pget(ox + 5, oy + 5) == 10


def _cell_center(C, lx, ly, dim):
    cell = C._PG_SPAN // dim
    return (C._PG_X0 + lx * cell + cell // 2,
            C._PG_Y0 + ly * cell + cell // 2)
