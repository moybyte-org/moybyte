"""Map editor region select / copy / paste / move (#91).

The MAP tab gains the paint editor's #90 clipboard model applied to tile cells: a
SELECT tool drags a marquee, COPY/CUT lift the region, and a tap stamps a PASTE.
The clipboard stores raw TileMap cell bytes (tile_id+1, 0 = EMPTY) so a copy round-
trips a cell exactly, and every mutating op is ONE undo step over the existing
begin_edit/end_edit journal.

Two layers, mirroring test_paint_tools_v90.py:
  * the MapEditor core verbs (set_selection / copy_selection / paste / cut_selection),
  * the map UI gestures through the SAME shared console the device runs (marquee
    drag, tap-to-stamp paste, the COPY/CUT/PASTE action strip).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# -- core: MapEditor region verbs -------------------------------------------

def _me(w=10, h=10):
    # spec=False: a MapEditor fixture sheet, not a cart sheet -- nothing here
    # draws, only tile ids are placed (see editors_sheet's SPEC.md 3.2 note).
    from runtime.editors import MapEditor, TileMap, SpriteSheet
    return MapEditor(TileMap(w, h), SpriteSheet(cols=16, rows=16, spec=False))


def _put(me, x, y, tile):
    """Place one tile through the batch path (one undo step)."""
    me.begin_edit()
    me._set(x, y, tile)
    me.end_edit()


def test_map_selection_normalizes_and_clamps():
    me = _me(10, 8)
    me.set_selection(6, 5, 2, 1)        # reversed drag
    assert me.sel == (2, 1, 6, 5)
    me.set_selection(-4, -4, 100, 100)  # out of range -> clamped to the 10x8 grid
    assert me.sel == (0, 0, 9, 7)
    me.clear_selection()
    assert me.sel is None


def test_map_copy_paste_round_trip_moves_a_region():
    me = _me()
    _put(me, 1, 1, 5)
    _put(me, 2, 1, 6)                   # a 2x1 run of tiles 5,6
    me.set_selection(1, 1, 2, 1)
    assert me.copy_selection() is True
    assert me.has_clip
    assert me.paste(4, 4) is True
    tm = me.tilemap
    assert tm.mget(1, 1) == 5 and tm.mget(2, 1) == 6   # source intact (a COPY)
    assert tm.mget(4, 4) == 5 and tm.mget(5, 4) == 6   # clip landed at (4,4)


def test_map_paste_is_transparent_by_default():
    me = _me()
    # clip = a 2x2 with an EMPTY (0) corner: (0,0)=3, (1,0)=3, (0,1)=3, (1,1) empty.
    _put(me, 0, 0, 3)
    _put(me, 1, 0, 3)
    _put(me, 0, 1, 3)
    me.set_selection(0, 0, 1, 1)
    me.copy_selection()
    # destination has an existing tile where the clip's EMPTY cell would land.
    _put(me, 6, 6, 7)                  # this is (1,1) of a paste at origin (5,5)
    me.paste(5, 5)
    tm = me.tilemap
    assert tm.mget(5, 5) == 3          # opaque clip cell written
    assert tm.mget(6, 6) == 7          # under the clip's EMPTY corner: preserved


def test_map_paste_opaque_overwrites_with_empty():
    me = _me()
    _put(me, 0, 0, 3)                  # a 2x2 clip, only (0,0) set; (1,1) is EMPTY
    me.set_selection(0, 0, 1, 1)
    me.copy_selection()
    _put(me, 6, 6, 7)
    me.paste(5, 5, transparent=False)  # opaque: the clip's EMPTY erases the dest tile
    assert me.tilemap.mget(6, 6) == -1


def test_map_paste_clips_to_map_edge():
    me = _me(8, 8)
    _put(me, 0, 0, 4)
    _put(me, 1, 0, 4)
    me.set_selection(0, 0, 1, 0)       # a 2x1 clip
    me.copy_selection()
    me.paste(7, 7)                     # (8,7) is off-map -> dropped, no crash
    assert me.tilemap.mget(7, 7) == 4
    assert me.tilemap.mget(0, 7) == -1  # nothing wrapped to the far edge


def test_map_cut_selection_moves_tiles():
    me = _me()
    _put(me, 2, 2, 5)
    me.set_selection(2, 2, 2, 2)
    assert me.cut_selection() is True
    assert me.tilemap.mget(2, 2) == -1   # source cleared by the cut
    me.paste(5, 5)
    assert me.tilemap.mget(5, 5) == 5    # landed at the destination
    # cut = one undo step (the clear) + one for the paste.
    me.undo()                            # undo the paste
    assert me.tilemap.mget(5, 5) == -1
    me.undo()                            # undo the cut's clear
    assert me.tilemap.mget(2, 2) == 5


def test_map_copy_with_no_selection_is_a_noop():
    me = _me()
    assert me.copy_selection() is False
    assert me.paste(0, 0) is False       # nothing on the clipboard


def test_map_paste_is_a_single_undo_step():
    me = _me()
    _put(me, 0, 0, 1)
    _put(me, 1, 0, 2)
    _put(me, 0, 1, 3)
    _put(me, 1, 1, 4)                    # a 2x2 block, all 4 cells set
    me.set_selection(0, 0, 1, 1)
    me.copy_selection()
    steps_before = len(me._undo)         # #111: the op-history undo stack
    assert me.paste(5, 5) is True
    assert len(me._undo) == steps_before + 1          # 4 cells, ONE undo step
    me.undo()                            # one undo reverts the whole paste
    assert me.tilemap.mget(5, 5) == -1
    assert me.tilemap.mget(6, 6) == -1


def test_map_undo_of_paste_restores_the_overwritten_tile():
    me = _me()
    _put(me, 0, 0, 8)
    me.set_selection(0, 0, 0, 0)         # a 1x1 clip of tile 8
    me.copy_selection()
    _put(me, 5, 5, 2)                    # an existing tile the paste overwrites
    me.paste(5, 5)
    assert me.tilemap.mget(5, 5) == 8
    me.undo()
    assert me.tilemap.mget(5, 5) == 2    # the overwritten tile comes back


def test_map_resize_drops_selection_keeps_clip():
    me = _me(8, 8)
    _put(me, 1, 1, 3)
    me.set_selection(1, 1, 1, 1)
    me.copy_selection()
    me.clear_history()                   # a resize calls this
    assert me.sel is None                # stale cell coords dropped
    assert me.has_clip                   # the coordinate-free clip survives
    me.paste(4, 4)
    assert me.tilemap.mget(4, 4) == 3


# -- UI: gestures through the shared console ---------------------------------

def _open_map(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.launcher.sel = 0
    ws.open()
    ws._open_map()
    drv = host_app.ConsoleDriver(ws)
    return ws, drv


def _cell_px(ws, cx, cy):
    """The pixel center of visible map cell (cx, cy) at the current zoom."""
    me = ws.map_ui.mapedit
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    px = x0 + (cx - me.cam_x) * cell + cell // 2
    py = y0 + (cy - me.cam_y) * cell + cell // 2
    return px, py


def test_ui_marquee_drag_sets_selection(tmp_path):
    ws, drv = _open_map(tmp_path)
    me = ws.map_ui.mapedit
    tm = ws.project.tilemap
    if me is None or tm is None:
        return
    ws.map_ui.map_tool = "select"
    sx, sy = _cell_px(ws, 0, 0)
    ex, ey = _cell_px(ws, 2, 1)
    drv.touch(sx, sy)
    drv.frame(1 / 30)
    drv.touch_drag(ex, ey)             # rubber-band the marquee
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert me.sel == (0, 0, 2, 1)
    assert not me.has_clip             # a marquee selects; it does not auto-copy


def test_ui_copy_button_then_tap_pastes(tmp_path):
    ws, drv = _open_map(tmp_path)
    me = ws.map_ui.mapedit
    tm = ws.project.tilemap
    if me is None or tm is None:
        return
    # Seed two known tiles, then select + copy + tap-paste elsewhere.
    _put(me, 0, 0, 5)
    _put(me, 1, 0, 6)
    ws.map_ui.map_tool = "select"
    me.set_selection(0, 0, 1, 0)
    # COPY via the SELECT-mode action strip (over the palette column).
    r = ws.map_ui._sel_actions_rects()
    cbx, cby, cbw, cbh = r["copy"]
    drv.touch(cbx + cbw // 2, cby + cbh // 2)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert me.has_clip
    # A tap in the map view (no drag) stamps the clip at the tapped cell.
    tx, ty = _cell_px(ws, 4, 4)
    drv.touch(tx, ty)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert tm.mget(4, 4) == 5 and tm.mget(5, 4) == 6


def test_ui_tap_without_clip_clears_selection(tmp_path):
    ws, drv = _open_map(tmp_path)
    me = ws.map_ui.mapedit
    tm = ws.project.tilemap
    if me is None or tm is None:
        return
    ws.map_ui.map_tool = "select"
    me.set_selection(1, 1, 3, 3)
    assert not me.has_clip
    tx, ty = _cell_px(ws, 5, 5)         # a tap with nothing on the clipboard
    drv.touch(tx, ty)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert me.sel is None               # the tap cleared the selection


def test_ui_select_tool_baseline_render_unaffected_until_active(tmp_path):
    """Opening the map editor (default STAMP tool) must not draw the SELECT overlay
    -- the #39 byte-identical baseline still holds. Rendering in select mode then
    draws without error (the strip + marquee are additive)."""
    ws, drv = _open_map(tmp_path)
    if ws.map_ui.mapedit is None:
        return
    drv.frame(1 / 30)                   # default tool: no overlay, no crash
    ws.map_ui.map_tool = "select"
    ws.map_ui.mapedit.set_selection(0, 0, 2, 2)
    drv.frame(1 / 30)                   # select overlay + action strip render cleanly
