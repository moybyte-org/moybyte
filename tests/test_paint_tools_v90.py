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


# -- shape tools: rectangle / line / oval (#90) ------------------------------

def test_line_tool_rasterizes_bresenham():
    pe = _pe()
    pe.set_tool(pe.LINE)
    pe.color = 6
    pe.stamp_shape(0, 0, 4, 4)         # a clean diagonal
    reg = _region(pe)
    for i in range(5):
        assert reg[i * 8 + i] == 6
    assert reg[0 * 8 + 4] == 0         # off-diagonal untouched
    assert pe.can_undo()


def test_line_tool_horizontal_and_undo():
    pe = _pe()
    pe.set_tool(pe.LINE)
    pe.color = 3
    pe.stamp_shape(1, 2, 6, 2)
    reg = _region(pe)
    assert all(reg[2 * 8 + x] == 3 for x in range(1, 7))
    pe.undo()
    assert _region(pe) == [0] * 64


def test_rect_tool_is_a_hollow_outline():
    pe = _pe()
    pe.set_tool(pe.RECT)
    pe.color = 9
    pe.stamp_shape(1, 1, 5, 4)         # corners inclusive
    reg = _region(pe)
    # top + bottom edges
    assert all(reg[1 * 8 + x] == 9 for x in range(1, 6))
    assert all(reg[4 * 8 + x] == 9 for x in range(1, 6))
    # left + right edges
    assert all(reg[y * 8 + 1] == 9 for y in range(1, 5))
    assert all(reg[y * 8 + 5] == 9 for y in range(1, 5))
    # interior stays empty (hollow)
    assert reg[2 * 8 + 3] == 0


def test_rect_tool_normalizes_reversed_drag():
    pe = _pe()
    pe.set_tool(pe.RECT)
    pe.color = 4
    pe.stamp_shape(5, 4, 1, 1)         # dragged up-left
    reg = _region(pe)
    assert reg[1 * 8 + 1] == 4 and reg[4 * 8 + 5] == 4


def test_oval_tool_hits_the_four_extremes_and_stays_in_bbox():
    pe = _pe()
    pe.set_tool(pe.OVAL)
    pe.color = 5
    pe.stamp_shape(0, 0, 6, 6)         # a circle radius 3, centre (3,3)
    reg = _region(pe)
    for (x, y) in ((3, 0), (3, 6), (0, 3), (6, 3)):
        assert reg[y * 8 + x] == 5     # the 4 cardinal points are on the ellipse
    assert reg[3 * 8 + 3] == 0         # hollow centre
    # every painted cell is inside the bounding box
    for y in range(8):
        for x in range(8):
            if reg[y * 8 + x] == 5:
                assert 0 <= x <= 6 and 0 <= y <= 6


def test_shape_respects_erase_toggle():
    pe = _pe()
    # seed a filled row, then erase a line through it with the LINE tool + erase.
    pe.color = 7
    pe.begin_stroke()
    for x in range(8):
        pe.paint(x, 3)
    pe.end_stroke()
    pe.set_tool(pe.LINE)
    pe.erase = True
    pe.stamp_shape(2, 3, 5, 3)
    reg = _region(pe)
    assert reg[3 * 8 + 1] == 7 and reg[3 * 8 + 6] == 7   # ends of the seed row
    assert all(reg[3 * 8 + x] == 0 for x in range(2, 6))  # carved transparent


def test_shape_no_change_records_nothing():
    # A shape that reproduces the existing pixels (index 0 on a blank tile) reads the
    # region once and records no undo step -- matching the pen/fill no-op discipline.
    pe = _pe()
    pe.set_tool(pe.RECT)
    pe.color = 0
    pe.stamp_shape(0, 0, 7, 7)
    assert not pe.can_undo()


def test_shape_points_matches_stamp():
    # The live preview (shape_points) and the committed pixels are the same cells.
    pe = _pe()
    pe.set_tool(pe.OVAL)
    pts = set(pe.shape_points(1, 1, 6, 5))
    pe.color = 8
    pe.stamp_shape(1, 1, 6, 5)
    reg = _region(pe)
    drawn = {(i % 8, i // 8) for i in range(64) if reg[i] == 8}
    assert drawn == pts


# -- transparency / color-erase toggle (#90) ---------------------------------

def test_erase_toggle_makes_pen_write_transparent():
    pe = _pe()
    pe.color = 5
    pe.paint(0, 0)
    assert _region(pe)[0] == 5
    pe.toggle_erase()
    assert pe.erase is True
    pe.paint(0, 0)                     # same cell, now erased
    assert _region(pe)[0] == 0
    pe.toggle_erase()
    assert pe.erase is False


def test_erase_toggle_makes_fill_transparent():
    pe = _pe()
    pe.color = 6
    pe.tool = pe.FILL
    pe.fill(0, 0)                      # flood color 6
    assert _region(pe) == [6] * 64
    pe.toggle_erase()
    pe.fill(0, 0)                      # flood back to 0 (transparent)
    assert _region(pe) == [0] * 64


# -- region select / copy / paste (#90) --------------------------------------

def test_selection_normalizes_and_clamps():
    pe = _pe()
    pe.set_selection(6, 5, 2, 1)       # reversed drag
    assert pe.sel == (2, 1, 6, 5)
    pe.set_selection(-3, -3, 100, 100)  # out of range
    assert pe.sel == (0, 0, 7, 7)      # clamped to the 8x8 region
    pe.clear_selection()
    assert pe.sel is None


def test_copy_paste_round_trip_moves_a_region():
    pe = _pe()
    pe.color = 9
    pe.begin_stroke()
    pe.paint(1, 1)
    pe.paint(2, 1)
    pe.end_stroke()
    pe.set_selection(1, 1, 2, 1)
    assert pe.copy_selection() is True
    assert pe.has_clip
    # paste the 2x1 clip lower-right
    assert pe.paste(4, 4) is True
    reg = _region(pe)
    assert reg[1 * 8 + 1] == 9 and reg[1 * 8 + 2] == 9   # source intact (copy)
    assert reg[4 * 8 + 4] == 9 and reg[4 * 8 + 5] == 9   # clip landed at (4,4)


def test_paste_is_transparent_by_default():
    pe = _pe()
    # clip = a 2x2 with a transparent (0) corner
    pe.color = 3
    pe.paint(0, 0)
    pe.paint(1, 0)
    pe.paint(0, 1)                      # (1,1) stays 0
    pe.set_selection(0, 0, 1, 1)
    pe.copy_selection()
    # destination has an existing pixel where the clip's 0 would land
    pe.color = 7
    pe.paint(6, 6)                     # will be (1,1) of the paste at origin (5,5)
    pe.paste(5, 5)
    reg = _region(pe)
    assert reg[5 * 8 + 5] == 3         # opaque clip pixel written
    assert reg[6 * 8 + 6] == 7         # under the clip's transparent corner: preserved


def test_paste_opaque_overwrites():
    pe = _pe()
    pe.color = 3
    pe.paint(0, 0)
    pe.set_selection(0, 0, 1, 1)       # a 2x2 clip, only (0,0) set
    pe.copy_selection()
    pe.color = 7
    pe.paint(6, 6)
    pe.paste(5, 5, transparent=False)  # opaque: the clip's 0s overwrite
    assert _region(pe)[6 * 8 + 6] == 0


def test_paste_clips_to_region_edge():
    pe = _pe()
    pe.color = 4
    pe.paint(0, 0)
    pe.paint(1, 0)
    pe.set_selection(0, 0, 1, 0)
    pe.copy_selection()
    pe.paste(7, 7)                     # (8,7) is off-grid -> dropped, no crash
    assert _region(pe)[7 * 8 + 7] == 4


def test_cut_selection_moves_pixels():
    pe = _pe()
    pe.color = 5
    pe.paint(2, 2)
    pe.set_selection(2, 2, 2, 2)
    assert pe.cut_selection() is True
    assert _region(pe)[2 * 8 + 2] == 0   # source cleared
    pe.paste(5, 5)
    assert _region(pe)[5 * 8 + 5] == 5   # landed at destination
    # cut = one undo step for the clear + one for the paste
    pe.undo()                            # undo paste
    assert _region(pe)[5 * 8 + 5] == 0
    pe.undo()                            # undo cut's clear
    assert _region(pe)[2 * 8 + 2] == 5


def test_copy_with_no_selection_is_a_noop():
    pe = _pe()
    assert pe.copy_selection() is False
    assert pe.paste(0, 0) is False       # nothing on the clipboard


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


def test_fill_fires_once_even_if_press_wobbles_off_the_grid(tmp_path):
    # Regression (#90 review): the once-per-press FILL guard used _paint_drag, which
    # _paint_stroke resets whenever the held pointer leaves the grid -- so one press
    # that wobbled off the grid edge and back in flooded a SECOND region on re-entry
    # (+ recorded a surprise extra undo step). The guard is a dedicated per-press
    # flag now, cleared only on pointer release: press in the grid -> drag out ->
    # drag back in over a DIFFERENT color region, all one press == exactly ONE fill.
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    ws.paint.n = 0
    ws.paint.clear()                   # blank canvas (one undo step of its own)
    ws.paint.color = 2
    # Wall down column x==4 splits the tile into two separate flood regions.
    ws.paint.begin_stroke()
    for y in range(8):
        ws.paint.paint(4, y)
    ws.paint.end_stroke()
    steps_before = len(ws.paint._undo)
    ox, oy = ws.sheet.tile_origin(ws.paint.n)

    # FILL tool on, then one press: left region -> off the grid -> right region.
    ws.paint.tool = ws.paint.FILL
    ws.paint.color = 11
    lx, ly = _cell_center(C, 1, 1, ws.paint.dim)
    drv.touch(lx, ly)                  # press: floods the LEFT region
    drv.frame(1 / 30)
    drv.touch_drag(C._PG_X0 - 6, ly)   # wobble off the grid's left edge (held)
    drv.frame(1 / 30)
    rx, ry = _cell_center(C, 6, 1, ws.paint.dim)
    drv.touch_drag(rx, ry)             # back in, over the RIGHT region (still held)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)

    assert ws.sheet.pget(ox + 1, oy + 1) == 11        # left region flooded...
    assert ws.sheet.pget(ox + 6, oy + 1) == 0         # ...right region UNTOUCHED
    assert len(ws.paint._undo) == steps_before + 1    # exactly one undo step added

    # The release re-armed the guard: a fresh press fills again.
    drv.touch(rx, ry)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.sheet.pget(ox + 6, oy + 1) == 11


def test_undo_redo_reverts_a_stroke(tmp_path):
    # #111: the paint toolbar's local UNDO/REDO buttons were removed -- the ONE bar
    # pair (ws.undo()/ws.redo(), routed to this editor's op-history) is now the undo.
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

    assert ws.undo() is True           # the bar UNDO reverts the whole stroke
    assert ws.sheet.pget(ox + 2, oy + 2) == before
    assert ws.redo() is True           # ...and the bar REDO re-lays it
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


def _tap_tool(drv, ws, tid):
    tx, ty = _tool_center(ws, tid)
    drv.touch(tx, ty)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)


def test_line_tool_button_and_grid_drag(tmp_path):
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    ws.paint.clear()
    ws.paint.color = 12

    _tap_tool(drv, ws, "line")
    assert ws.paint.tool == ws.paint.LINE

    # Press-drag-release a horizontal line: press at (1,1), drag to (5,1), release.
    x0, y0 = _cell_center(C, 1, 1, ws.paint.dim)
    x1, y1 = _cell_center(C, 5, 1, ws.paint.dim)
    drv.touch(x0, y0)
    drv.frame(1 / 30)
    drv.touch_drag(x1, y1)
    drv.frame(1 / 30)
    # mid-drag the line is only a PREVIEW -- not yet committed to the sheet.
    ox, oy = ws.sheet.tile_origin(ws.paint.n)
    assert ws.sheet.pget(ox + 3, oy + 1) == 0
    drv.touch_up()
    drv.frame(1 / 30)
    assert all(ws.sheet.pget(ox + x, oy + 1) == 12 for x in range(1, 6))
    assert ws.paint.can_undo()


def test_erase_toggle_button(tmp_path):
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    ws.paint.color = 9
    ox, oy = ws.sheet.tile_origin(ws.paint.n)

    cx, cy = _cell_center(C, 2, 2, ws.paint.dim)
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.sheet.pget(ox + 2, oy + 2) == 9

    _tap_tool(drv, ws, "erase")
    assert ws.paint.erase is True
    drv.touch(cx, cy)                  # paint again -> now erases
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.sheet.pget(ox + 2, oy + 2) == 0


def test_select_copy_paste_through_the_ui(tmp_path):
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    ws.paint.clear()
    ws.paint.color = 7
    ox, oy = ws.sheet.tile_origin(ws.paint.n)
    # seed a 2x1 mark
    ws.paint.begin_stroke()
    ws.paint.paint(1, 1)
    ws.paint.paint(2, 1)
    ws.paint.end_stroke()

    # SELECT tool, drag a box over (1,1)-(2,1).
    _tap_tool(drv, ws, "select")
    assert ws.paint.tool == ws.paint.SELECT
    a = _cell_center(C, 1, 1, ws.paint.dim)
    b = _cell_center(C, 2, 1, ws.paint.dim)
    drv.touch(*a)
    drv.frame(1 / 30)
    drv.touch_drag(*b)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.paint.sel == (1, 1, 2, 1)

    _tap_tool(drv, ws, "copy")
    assert ws.paint.has_clip

    # A tap in SELECT mode (no drag) stamps the clip at the tapped cell.
    dest = _cell_center(C, 4, 4, ws.paint.dim)
    drv.touch(*dest)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.sheet.pget(ox + 4, oy + 4) == 7 and ws.sheet.pget(ox + 5, oy + 4) == 7
    assert ws.sheet.pget(ox + 1, oy + 1) == 7   # source intact (copy, not cut)


def _cell_center(C, lx, ly, dim):
    cell = C._PG_SPAN // dim
    return (C._PG_X0 + lx * cell + cell // 2,
            C._PG_Y0 + ly * cell + cell // 2)
