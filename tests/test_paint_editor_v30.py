"""Sprite-editor improvements (#30): drag-to-draw (host mouse == device touch)
and larger sprites (1x1 / 2x2 / 3x3) in the shared paint editor.

These build the SAME shared console the device runs (runtime.host_app) and drive
it through ConsoleDriver -- mouse == touch, arrows == trackball -- so the drag
stroke is exercised through the real pointer/input path (the same handle_pointer
the device loop calls), not by poking the editor directly. The editor/sheet logic
lives in runtime.editors, shared with the frozen device modules.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _cell_center(C, lx, ly, dim):
    """Screen pixel at the centre of grid cell (lx, ly) for an editor of side
    `dim` pixels -- mirrors console._paint_grid_cell's inverse mapping."""
    cell = C._PG_SPAN // dim
    return (C._PG_X0 + lx * cell + cell // 2,
            C._PG_Y0 + ly * cell + cell // 2)


def _open_paint(ws):
    from runtime import host_app  # noqa: F401
    # Open the first cart that has a sheet, then the paint editor.
    ws.launcher.sel = 0
    ws.open()
    ws._open_paint()
    assert ws.menu_view == "paint"


# -- Part B: larger sprites (PaintEditor logic) ------------------------------

def test_paint_editor_default_is_8x8():
    from runtime.editors import PaintEditor, SpriteSheet
    pe = PaintEditor(SpriteSheet())
    assert pe.size == 1 and pe.dim == 8


def test_cycle_size_steps_1_2_3():
    from runtime.editors import PaintEditor, SpriteSheet
    pe = PaintEditor(SpriteSheet(cols=16, rows=16))
    assert pe.size == 1
    pe.cycle_size(); assert pe.size == 2 and pe.dim == 16
    pe.cycle_size(); assert pe.size == 3 and pe.dim == 24
    pe.cycle_size(); assert pe.size == 1          # wraps back


def test_2x2_writes_the_four_constituent_sheet_tiles():
    # A 2x2 sprite at top-left tile n spans tiles n, n+1, n+cols, n+cols+1; painting
    # the 16x16 region must land pixels in all four (TIC-80 multi-tile layout).
    from runtime.editors import PaintEditor, SpriteSheet
    cols = 16
    sh = SpriteSheet(cols=cols, rows=16)
    pe = PaintEditor(sh)
    pe.n = 0
    pe.cycle_size()                               # 2x2 (16x16)
    pe.color = 9
    # Paint one pixel in each tile-quadrant of the 16x16 block.
    pe.paint(2, 3)        # top-left tile (n=0)
    pe.paint(10, 3)       # top-right tile (n=1)
    pe.paint(2, 11)       # bottom-left tile (n=cols)
    pe.paint(10, 11)      # bottom-right tile (n=cols+1)
    assert sh.tget(0, 2, 3) == 9
    assert sh.tget(1, 2, 3) == 9                  # x 10 -> tile 1 local x 2
    assert sh.tget(cols, 2, 3) == 9              # y 11 -> tile cols local y 3
    assert sh.tget(cols + 1, 2, 3) == 9


def test_size_clamps_near_sheet_edge():
    # A 3x3 sprite whose origin sits one tile from the right edge can't fit; size
    # shrinks so the span never reads out of bounds.
    from runtime.editors import PaintEditor, SpriteSheet
    sh = SpriteSheet(cols=2, rows=2)              # only a 2x2 tile sheet
    pe = PaintEditor(sh)
    pe.n = 0
    pe.cycle_size()                               # asks for 2x2 -> fits (max here)
    assert pe.size == 2
    pe.cycle_size()                               # asks for 3x3 -> clamps to 2
    assert pe.size == 2
    pe.n = 3                                       # bottom-right tile -> only 1x1 fits
    pe.select(0)                                   # re-clamps for the new origin
    assert pe.size == 1


# -- Part B: spr() draws the multi-tile span ---------------------------------

def test_tile_span_image_covers_the_block():
    from runtime.editors import SpriteSheet
    sh = SpriteSheet(cols=16, rows=16)
    sh.pset(0, 0, 5)
    sh.pset(15, 15, 6)                            # bottom-right pixel of a 2x2 block
    img = sh.tile_span_image(0, 2, 2)
    assert (img.w, img.h) == (16, 16)
    assert img.pix[0] == 5
    assert img.pix[15 * 16 + 15] == 6


def test_spr_with_wh_span_blits_a_16x16_image():
    # The cart-facing spr(n, x, y, w=2, h=2) builds a 16x16 image from the sheet.
    from runtime import host_app
    from runtime.editors import SpriteSheet

    calls = []

    class StubCanvas:
        w, h = 320, 240

        def spr(self, img, x, y, scale=1, flip=0):
            calls.append((img.w, img.h, x, y, scale))

        def spr_tile(self, sheet, tile, x, y, colorkey=-1, scale=1, flip=0):
            # 1x1 sheet tiles now auto-batch (#63): resolve the tile like the flush would
            # and record it as an 8x8, so the span assertion still exercises canvas.spr.
            img = sheet.tile_image(int(tile), colorkey)
            calls.append((img.w, img.h, x, y, scale))

        def __getattr__(self, name):
            return lambda *a, **k: 0

    class StubInput:
        pointer = None

        def held(self, n):
            return False

        def pressed(self, n):
            return False

    sheet = SpriteSheet(cols=16, rows=16)
    api = host_app.make_api(StubCanvas(), StubInput(), {}, sheet)
    api["spr"](0, 10, 20)                          # default 8x8 -> auto-batch (spr_tile)
    assert calls[-1] == (8, 8, 10, 20, 1)
    api["spr"](0, 10, 20, w=2, h=2)                # 2x2 span -> 16x16 immediate spr
    assert calls[-1] == (16, 16, 10, 20, 1)


# -- Part A: drag-to-draw through the pointer path ---------------------------

def test_tap_paints_one_pixel(tmp_path):
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    ws.paint.color = 11
    x, y = _cell_center(C, 3, 4, ws.paint.dim)
    drv.touch(x, y)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    ox, oy = ws.sheet.tile_origin(ws.paint.n)
    assert ws.sheet.pget(ox + 3, oy + 4) == 11


def test_drag_paints_a_continuous_stroke_no_gaps(tmp_path):
    # Press on cell (0,0), drag to (7,0) sampling only the endpoints (a fast drag):
    # every cell on the line must be painted -- the line-fill closes the gaps.
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    ws.paint.color = 12
    dim = ws.paint.dim

    x0, y0 = _cell_center(C, 0, 0, dim)
    drv.touch(x0, y0)
    drv.frame(1 / 30)                              # press paints the first cell
    x1, y1 = _cell_center(C, 7, 0, dim)
    drv.touch_drag(x1, y1)                          # jump straight to the far cell
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)

    ox, oy = ws.sheet.tile_origin(ws.paint.n)
    painted = [ws.sheet.pget(ox + lx, oy) for lx in range(8)]
    assert painted == [12] * 8                     # no gaps along the row


def test_drag_paints_a_diagonal_run(tmp_path):
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    ws.paint.color = 8
    dim = ws.paint.dim

    x0, y0 = _cell_center(C, 0, 0, dim)
    drv.touch(x0, y0)
    drv.frame(1 / 30)
    x1, y1 = _cell_center(C, 7, 7, dim)
    drv.touch_drag(x1, y1)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)

    ox, oy = ws.sheet.tile_origin(ws.paint.n)
    for d in range(8):                             # the diagonal is fully painted
        assert ws.sheet.pget(ox + d, oy + d) == 8


def test_release_resets_stroke_so_taps_dont_connect(tmp_path):
    # Tap (0,0), lift, tap (7,7): the two separate taps must NOT be joined by a line
    # (the gap between them stays blank). This guards the stroke-origin reset.
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    ws.paint.color = 10
    dim = ws.paint.dim
    ox, oy = ws.sheet.tile_origin(ws.paint.n)
    mid_before = ws.sheet.pget(ox + 3, oy + 3)     # seed art may live here; capture it

    for (lx, ly) in ((0, 0), (7, 7)):
        x, y = _cell_center(C, lx, ly, dim)
        drv.touch(x, y)
        drv.frame(1 / 30)
        drv.touch_up()
        drv.frame(1 / 30)

    assert ws.sheet.pget(ox + 0, oy + 0) == 10
    assert ws.sheet.pget(ox + 7, oy + 7) == 10
    # The midpoint is untouched -- the lift between the two taps reset the stroke so
    # they were NOT joined by a line (it still holds whatever it held before).
    assert ws.sheet.pget(ox + 3, oy + 3) == mid_before


def test_size_button_cycles_through_the_ui(tmp_path):
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    assert ws.paint.size == 1
    bx, by, bw, bh = C._PAINT_SIZE
    drv.touch(bx + bw // 2, by + bh // 2)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.paint.size == 2


def test_save_persists_a_2x2_sprite(tmp_path):
    # Paint a 2x2 sprite, SAVE, reload from disk: all four constituent tiles survive.
    from runtime import console as C
    from runtime import host_app, moy_carts
    from runtime.editors import SpriteSheet

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    cols = ws.sheet.cols
    ws.paint.n = 0
    ws.paint.cycle_size()                          # 2x2
    ws.paint.color = 14
    for q in ((1, 1), (9, 1), (1, 9), (9, 9)):     # one pixel per quadrant tile
        ws.paint.paint(*q)
    # SAVE through the UI button.
    bx, by, bw, bh = C._PAINT_SAVE
    drv.touch(bx + bw // 2, by + bh // 2)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)

    reloaded = SpriteSheet.from_hex(moy_carts.load(ws.cart["path"])["sprites"],
                                    cols=cols, rows=ws.sheet.rows)
    assert reloaded.tget(0, 1, 1) == 14
    assert reloaded.tget(1, 1, 1) == 14
    assert reloaded.tget(cols, 1, 1) == 14
    assert reloaded.tget(cols + 1, 1, 1) == 14


# -- Part A applied to the map editor (tap = paint) --------------------------

def _map_cell_center(ws, cx, cy):
    # Pixel center of visible map cell (cx, cy) at the workstation's LIVE zoom (#37
    # follow-up): the map-view metrics are dynamic now, so compute from _mv_metrics().
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    return (x0 + cx * cell + cell // 2,
            y0 + cy * cell + cell // 2)


def test_map_tap_paints_one_cell(tmp_path):
    # In the map editor a tap (press + release in place) stamps the brush into the
    # single cell under it -- the drag gesture is reserved for panning (#37).
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.launcher.sel = 0
    ws.open()
    ws._open_map()
    assert ws.menu_view == "map"
    drv = host_app.ConsoleDriver(ws)
    me = ws.map_ui.mapedit
    me.n = 4                                       # a recognizable brush tile id
    cells_before = bytes(ws.tilemap.cells)

    x0, y0 = _map_cell_center(ws, 2, 1)
    drv.touch(x0, y0)                              # press
    drv.frame(1 / 30)
    drv.touch_up()                                 # release in place -> a tap
    drv.frame(1 / 30)

    cx, cy = me.cam_x + 2, me.cam_y + 1
    assert ws.tilemap.mget(cx, cy) == 4           # the tapped cell got the brush
    # Exactly one cell changed (the tap stamped a single cell, not a run).
    changed = [k for k in range(len(cells_before))
               if cells_before[k] != ws.tilemap.cells[k]]
    assert changed == [cy * ws.tilemap.w + cx]


# -- #37: touch-drag pans the map, tap still paints ---------------------------

def test_map_drag_pans_view_while_tap_still_paints(tmp_path):
    # A drag across the map view (press + move past the pan threshold + release)
    # PANS the visible window -- it scrolls the camera and does NOT stamp tiles --
    # while a plain tap still paints one cell. This is the tap=paint / drag=pan
    # scheme that makes a map larger than the 320x240 screen navigable by touch.
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.launcher.sel = 0
    ws.open()
    ws._open_map()
    assert ws.menu_view == "map"
    drv = host_app.ConsoleDriver(ws)
    me = ws.map_ui.mapedit
    me.n = 4
    # Zoom all the way IN so the map is bigger than the view and a pan has room to
    # move (the default fit-both zoom shows the whole shipped map -> nothing to pan).
    ws.map_ui.map_zoom = len(C._MV_ZOOMS) - 1
    x0m, y0m, cell, cols, rows = ws.map_ui._mv_metrics()
    assert ws.tilemap.w > cols and ws.tilemap.h > rows
    cam0 = (me.cam_x, me.cam_y)
    cells_before = bytes(ws.tilemap.cells)         # snapshot to prove a pan is no-paint

    # Drag left/up by several cells: press near the right, move far to the left.
    x0, y0 = _map_cell_center(ws, 4, 3)
    x1 = x0 - 3 * cell                             # well past _MAP_PAN_THRESH
    y1 = y0 - 2 * cell
    drv.touch(x0, y0)
    drv.frame(1 / 30)
    drv.touch_drag(x1, y1)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)

    # The view scrolled (content followed the finger) and nothing was stamped.
    assert (me.cam_x, me.cam_y) != cam0
    assert me.cam_x > 0 and me.cam_y > 0
    assert bytes(ws.tilemap.cells) == cells_before  # a pan must not paint any cell
    assert not ws.tilemap.dirty                     # a pure pan leaves no edits

    # A plain tap still paints, even after panning.
    me = ws.map_ui.mapedit
    tx, ty = _map_cell_center(ws, 1, 1)
    drv.touch(tx, ty)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.tilemap.mget(me.cam_x + 1, me.cam_y + 1) == 4


def test_map_drag_with_size_brush_reverts_the_whole_block(tmp_path):
    # #57 x #37: with SIZE=2 the press edge stamps a 2x2 block; when the gesture
    # crosses the pan threshold, ALL of the block's cells revert (plus the map's
    # dirty flag and gen counter) -- not just the anchor cell -- so a pan with a
    # big brush is still completely side-effect-free.
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.launcher.sel = 0
    ws.open()
    ws._open_map()
    drv = host_app.ConsoleDriver(ws)
    me = ws.map_ui.mapedit
    me.n = 4
    me.size = 2
    ws.map_ui.map_zoom = len(C._MV_ZOOMS) - 1      # zoom in so a pan has room
    x0m, y0m, cell, cols, rows = ws.map_ui._mv_metrics()
    assert ws.tilemap.w > cols and ws.tilemap.h > rows
    cells_before = bytes(ws.tilemap.cells)
    gen0 = ws.tilemap.gen

    x0, y0 = _map_cell_center(ws, 4, 3)
    drv.touch(x0, y0)                              # press edge stamps 4 cells...
    drv.frame(1 / 30)
    drv.touch_drag(x0 - 3 * cell, y0 - 2 * cell)   # ...then the drag latches PAN
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)

    assert bytes(ws.tilemap.cells) == cells_before  # every stamped cell reverted
    assert not ws.tilemap.dirty
    assert ws.tilemap.gen == gen0


def test_map_empty_sky_tile_is_selectable_and_clears_a_cell(tmp_path):
    # The empty/"sky" swatch (#37) is a first-class palette pick: selecting it sets
    # the brush to EMPTY (-1), and tapping a filled cell with it clears the cell so
    # mget() returns 0/-1 (the transparent value map() skips). This is how a kid
    # paints sky/background or undoes a placement by overpainting.
    from runtime import console as C
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.launcher.sel = 0
    ws.open()
    ws._open_map()
    drv = host_app.ConsoleDriver(ws)

    # First fill a cell with a real tile.
    ws.map_ui.mapedit.n = 4
    fx, fy = _map_cell_center(ws, 0, 0)
    drv.touch(fx, fy)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.tilemap.mget(0, 0) == 4

    # Tap the SKY swatch to pick the empty brush, then tap the filled cell.
    sx = C._TP_SKY[0] + C._TP_SKY[2] // 2
    sy = C._TP_SKY[1] + C._TP_SKY[3] // 2
    drv.touch(sx, sy)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.map_ui.mapedit.n < 0                         # the brush is now EMPTY

    drv.touch(fx, fy)                              # paint sky over the filled cell
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.tilemap.mget(0, 0) == ws.tilemap.EMPTY   # cleared to transparent (-1)
    assert ws.tilemap.cells[0] == 0                # stored as byte 0 == "no tile"
