"""The map tab's visible tile window is ONE map() (#163).

The tab used to blit the window a cell at a time -- `sheet.tile_image(tid, -1)`
plus a `cv.spr` per cell, so a full 24x20 view was ~480 Image-blit crossings
per drag frame and the tab's cost scaled with how much level a kid had drawn.
It draws the window with the cart's own tilemap kernel instead: one call, the
same `blit_map` a running cart's `map()` uses, so a tile in the editor is the
tile the cart draws.

That substitution rests on two claims, and both are checked here rather than
argued: the tiles land on exactly the lattice the kernel walks (every detail
rung's cell is a whole multiple of the tile), and the pixels are the ones the
per-cell blit painted. The shell goldens cannot say the second -- their editor
cart has no map at all, so `editor_map` renders an empty field.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CARTS = ROOT / "system_carts"

from runtime.host_canvas import make_canvas                      # noqa: E402

# The golden matrix's tiers (tests/test_shell_goldens.py CONFIGS), as the map
# layout sees them.
TIERS = ((320, 240, 1), (480, 320, 1), (800, 480, 3), (1024, 600, 2))


def _cart(name="brick_siege"):
    """A real seed cart's sheet + tilemap: drawn art and a drawn level, which
    is what a parity claim about tile pixels needs."""
    from runtime.editors import SpriteSheet, TileMap
    folder = CARTS / (name + ".moy")
    sheet = SpriteSheet.from_hex((folder / "sprites.moygfx").read_text())
    tm = TileMap.from_hex((folder / "map.moymap").read_text())
    assert any(tm.cells), name + " has no tiles placed"
    return sheet, tm


def _reference(cv, tm, sheet, cam_x, cam_y, nx, ny, x0, y0, cell):
    """The per-cell blit the tab used to run, transcribed. An independent
    oracle: it asks the sheet for each tile and stamps it, which is what the
    kernel must agree with."""
    scale = max(1, cell // sheet.TILE)
    off = (cell - sheet.TILE * scale) // 2
    for ry in range(ny):
        for rx in range(nx):
            tid = tm.mget(cam_x + rx, cam_y + ry)
            if tid < 0:
                continue
            img = sheet.tile_image(tid, -1)
            if img:
                cv.spr(img, x0 + rx * cell + off, y0 + ry * cell + off, scale)


def _both(w, h, draw_ref, draw_new):
    a, b = make_canvas(w, h), make_canvas(w, h)
    a.cls(1)
    b.cls(1)
    draw_ref(a)
    draw_new(b)
    return bytes(a._buf), bytes(b._buf)


def _pair(cell, cam_x=0, cam_y=0, nx=None, ny=None, x0=14, y0=32,
          clip=None, cart="brick_siege"):
    sheet, tm = _cart(cart)
    nx = tm.w - cam_x if nx is None else nx
    ny = tm.h - cam_y if ny is None else ny
    scale = cell // sheet.TILE

    def ref(cv):
        if clip:
            cv.clip(*clip)
        _reference(cv, tm, sheet, cam_x, cam_y, nx, ny, x0, y0, cell)

    def new(cv):
        if clip:
            cv.clip(*clip)
        cv.map(tm, sheet, cam_x, cam_y, nx, ny, x0, y0, -1, scale)

    return _both(320, 240, ref, new)


def test_every_detail_rung_is_a_whole_tile_multiple():
    """The lattice claim. `blit_map` lays tiles out on a `tile * scale` grid, so
    a rung whose cell is not a whole multiple of the tile would draw them at the
    wrong spacing -- silently, and only on that rung. A new zoom size is the
    change that would do it, so the ladder is checked, not assumed."""
    from runtime import map_editor_ui as M
    from runtime.editors import SpriteSheet
    for w, h, fs in TIERS:
        for cell in M.MapLayout(w, h, fs).zooms:
            if cell == M._MV_OVERVIEW:
                continue                  # sub-tile: the block path draws no tiles
            assert cell >= SpriteSheet.TILE
            assert cell % SpriteSheet.TILE == 0, (w, h, fs, cell)


def test_the_window_is_the_pixels_the_per_cell_blit_drew():
    from runtime import map_editor_ui as M
    from runtime.editors import SpriteSheet
    rungs = [c for c in M.MapLayout(1024, 600, 2).zooms if c != M._MV_OVERVIEW]
    assert len(rungs) >= len(M._MV_ZOOMS)
    for cell in rungs:
        ref, new = _pair(cell)
        assert ref == new, "rung %d" % cell
    assert cell // SpriteSheet.TILE > 1        # the scaled rungs were covered


def test_a_panned_camera_and_a_partial_window_agree():
    # The two things a real pan produces: a non-zero map origin, and a window
    # narrower than the view once the camera reaches the map's edge.
    ref, new = _pair(16, cam_x=3, cam_y=5)
    assert ref == new
    ref, new = _pair(8, cam_x=7, cam_y=9, nx=4, ny=3)
    assert ref == new


def test_the_clip_rect_cuts_both_the_same_way():
    ref, new = _pair(16, clip=(20, 40, 90, 70))
    assert ref == new


def test_a_second_cart_with_different_art_agrees_too():
    ref, new = _pair(8, cart="platformer")
    assert ref == new
