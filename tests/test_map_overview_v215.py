"""The map editor's OVERVIEW zoom rung (#215).

`spr` upscales by whole numbers only, so 8px is the floor of the detail rungs: a
tile cannot shrink into a smaller cell, it spills over its neighbours. Below the
tile the editor stops drawing tiles and paints each cell as a solid block of its
tile's DOMINANT COLOUR -- a minimap that fits any map at 1-7px per cell.

Three layers:
  * the dominant colour itself (`SpriteSheet.tile_color`) and its invalidation,
  * the fit arithmetic against every SHIPPED map on every tier's view rectangle,
  * the rung through the shared console the device runs -- cycling, drawing,
    hit-testing, the keyboard/trackball path, and a relayout (#216 re-enters the
    tab through `relayout`, so the rung has to survive one).
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CARTS = ROOT / "system_carts"

# The golden matrix's tiers (tests/test_shell_goldens.py CONFIGS), as the map
# layout sees them: (w, h, font_scale). The 320x240 row is the tightest view and
# the one the issue measured.
TIERS = ((320, 240, 1), (480, 320, 1), (800, 480, 3), (1024, 600, 2))


def _sheet(pixels=None):
    """A fixture sheet -- spec=False: nothing here draws, tile_color only counts
    indices (see editors_sheet's SPEC.md 3.2 note)."""
    from runtime.editors import SpriteSheet
    sh = SpriteSheet(cols=4, rows=4, spec=False)
    if pixels:
        for (n, lx, ly), c in pixels.items():
            sh.tset(n, lx, ly, c)
    return sh


# -- the dominant colour -----------------------------------------------------

def test_tile_color_is_the_most_common_index():
    sh = _sheet()
    for ly in range(8):                       # tile 1 all colour 3...
        for lx in range(8):
            sh.tset(1, lx, ly, 3)
    for lx in range(8):                       # ...except one row of colour 9
        sh.tset(1, lx, 0, 9)
    assert sh.tile_color(1) == 3


def test_tile_color_counts_index_zero_like_any_other():
    sh = _sheet()
    for lx in range(8):                       # 8 of 64 pixels coloured, 56 black
        sh.tset(2, lx, 0, 11)
    assert sh.tile_color(2) == 0              # the tile really is mostly black
    for ly in range(1, 8):                    # colour it in -> the answer moves
        for lx in range(8):
            sh.tset(2, lx, ly, 11)
    assert sh.tile_color(2) == 11


def test_tile_color_breaks_a_tie_on_the_lower_index():
    sh = _sheet()
    for ly in range(8):
        for lx in range(8):
            sh.tset(3, lx, ly, 12 if ly < 4 else 5)
    assert sh.tile_color(3) == 5              # 32/32 -- deterministic, not arbitrary


def test_tile_color_rejects_an_id_off_the_sheet():
    sh = _sheet()
    assert sh.tile_color(-1) == -1
    assert sh.tile_color(sh.count) == -1


def test_tile_color_is_memoised_and_a_pset_invalidates_it():
    sh = _sheet()
    for ly in range(8):
        for lx in range(8):
            sh.tset(0, lx, ly, 4)
    assert sh.tile_color(0) == 4
    before = sh.gen
    assert sh.tile_color(0) == 4              # served from the memo
    assert sh.gen == before                   # a read never bumps the generation
    for ly in range(8):                       # the Paint tab repaints the tile
        for lx in range(8):
            sh.tset(0, lx, ly, 14)
    assert sh.tile_color(0) == 14             # the memo was dropped, not stale


def test_tile_color_memo_is_dropped_by_an_edit_to_a_DIFFERENT_tile():
    # `gen` is sheet-wide, so any pset drops the whole memo. The point of the test
    # is that it is never SERVED stale, not that it is minimally invalidated.
    sh = _sheet()
    for ly in range(8):
        for lx in range(8):
            sh.tset(5, lx, ly, 6)
    assert sh.tile_color(5) == 6
    sh.tset(9, 0, 0, 2)
    assert sh.tile_color(5) == 6


# -- the fit, against the shipped maps ---------------------------------------

def _shipped_maps():
    """(cart folder name, w, h) for every seed cart that ships a tilemap."""
    from runtime.editors import TileMap
    out = []
    for d in sorted(CARTS.glob("*.moy")):
        f = d / "map.moymap"
        if f.exists():
            tm = TileMap.from_hex(f.read_text())
            out.append((d.name, tm.w, tm.h))
    return out


def test_the_seed_store_still_ships_a_map_bigger_than_the_view():
    # The premise of #215. If every shipped map ever fits at 8px again, the fit
    # tests below stop measuring anything and this one says so.
    from runtime import map_editor_ui as M
    maps = _shipped_maps()
    assert maps, "no seed cart ships a map.moymap"
    base = M.MapLayout(320, 240, 1)
    cell = base.zooms[0]
    assert any(w > base.mv_avail_w // cell or h > base.mv_avail_h // cell
               for _n, w, h in maps)


def test_every_shipped_map_fits_whole_at_overview_on_every_tier():
    from runtime import map_editor_ui as M
    for w, h, fs in TIERS:
        lay = M.MapLayout(w, h, fs)
        for name, mw, mh in _shipped_maps():
            cell = M._mv_fit_cell(lay.mv_avail_w, lay.mv_avail_h, mw, mh)
            where = "%s %dx%d on %dx%d fs%d" % (name, mw, mh, w, h, fs)
            assert 1 <= cell <= M._MV_FIT_MAX, where
            assert cell * mw <= lay.mv_avail_w, where
            assert cell * mh <= lay.mv_avail_h, where


def test_the_resize_ceiling_fits_at_overview_on_the_smallest_view():
    # A kid cannot make a map the overview rung would fail to show whole: the DIM
    # panel stops at _MAP_MAX_DIM, which fits at 1px on the tightest view.
    from runtime import map_editor_ui as M
    lay = M.MapLayout(320, 240, 1)
    d = M._MAP_MAX_DIM
    cell = M._mv_fit_cell(lay.mv_avail_w, lay.mv_avail_h, d, d)
    assert cell >= 1
    assert cell * d <= lay.mv_avail_w and cell * d <= lay.mv_avail_h


def test_fit_cell_never_reaches_the_tile_and_never_divides_by_zero():
    from runtime import map_editor_ui as M
    assert M._mv_fit_cell(192, 164, 1, 1) == M._MV_FIT_MAX      # a 1x1 map
    assert M._mv_fit_cell(192, 164, 0, 0) == M._MV_FIT_MAX      # a map with no cells
    assert M._MV_FIT_MAX < 8                                    # always sub-tile
    # A map wider in cells than the view is in pixels floors at 1 and pans.
    assert M._mv_fit_cell(192, 164, 400, 2) == 1


# -- the rung, through the shared console ------------------------------------

def _open_map(tmp_path, title="Hop Quest"):
    from runtime import host_app
    import ws_helpers
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws_helpers.open_cart(ws, title)
    ws._open_map()
    assert ws.menu_view == "map" and ws.map_ui.mapedit is not None
    return ws, host_app.ConsoleDriver(ws)


def _paint(ws, drv):
    """One settled frame with nothing transient on it -- opening a cart notes an
    achievement, and its toast covers a third of the map view."""
    import ws_helpers
    ws_helpers.quiesce(ws)
    ws._dirty = True
    drv.frame(1 / 30)


def _tap_button(drv, name, dt=1 / 30):
    """One press EDGE of a console button. The released frame is load-bearing: the
    driver holds the name down across `frame`, and the press edge is computed
    against the previous frame's held set, so two back-to-back pressed frames are
    one edge."""
    drv.press(name)
    drv.frame(dt)
    drv.frame(dt)


def _to_overview(ws):
    """Cycle the zoom to the OVERVIEW rung (it is last on every tier)."""
    ws.map_ui.map_zoom = len(ws.map_ui.layout.zooms) - 1
    ws.map_ui._map_clamp_cam()
    assert ws.map_ui._mv_overview()


def _dominant(sheet, n):
    """The test's OWN answer for a tile's dominant colour -- an independent count,
    not `sheet.tile_color`, which is the thing under test. A pixel test that asked
    the implementation what it should have drawn would pass for any answer."""
    count = [0] * 16
    for ly in range(sheet.TILE):
        for lx in range(sheet.TILE):
            count[sheet.tget(n, lx, ly) & 15] += 1
    best = 0
    for i in range(1, 16):
        if count[i] > count[best]:
            best = i
    return best


def _cell_px(ws, cx, cy):
    me = ws.map_ui.mapedit
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    return (x0 + (cx - me.cam_x) * cell + cell // 2,
            y0 + (cy - me.cam_y) * cell + cell // 2)


def test_overview_shows_the_whole_map_and_pins_the_camera(tmp_path):
    # Sky Run's 100x30 is the widest shipped map and pans at every detail rung.
    ws, _drv = _open_map(tmp_path, "Sky Run")
    tm = ws.project.tilemap
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    assert cols < tm.w                                   # ...it pans at the default
    _to_overview(ws)
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    assert cell < 8
    assert cols >= tm.w and rows >= tm.h                 # the whole level, at once
    ws.map_ui._map_pan(1, 1)                             # nowhere left to pan to
    assert (ws.map_ui.mapedit.cam_x, ws.map_ui.mapedit.cam_y) == (0, 0)


def test_overview_is_the_default_rung_for_nobody(tmp_path):
    # DECIDED (#215): the editor still OPENS on the 8px field size, on every cart.
    # Overview is where you find yourself in a level; a tap on a 2px cell is not an
    # edit gesture, and a per-map default would make the editor open differently
    # for every cart.
    from runtime import map_editor_ui as M
    for title in ("Sky Run", "Hop Quest", "Brick Siege"):
        ws, _drv = _open_map(tmp_path / title.replace(" ", "_"), title)
        assert ws.map_ui.map_zoom == 0
        assert not ws.map_ui._mv_overview()
        assert ws.map_ui._mv_metrics()[2] == M._MV_ZOOMS[0]


def test_zoom_button_and_title_name_the_overview_rung(tmp_path):
    from runtime import map_editor_ui as M
    ws, _drv = _open_map(tmp_path)
    assert ws.map_ui._mv_zoom_label() == "1"
    _to_overview(ws)
    assert ws.map_ui._mv_zoom_label() == M._MV_OV_LABEL
    assert len(M._MV_OV_LABEL) == 2          # the 24px ZOOM button prints 8px glyphs


def test_overview_paints_each_cell_its_tiles_dominant_colour(tmp_path):
    ws, drv = _open_map(tmp_path)            # Hop Quest, 40x26 -> a 4px cell
    tm = ws.project.tilemap
    sheet = ws.project.sheet
    _to_overview(ws)
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    assert cell >= 2                         # enough to sample a block's middle
    _paint(ws, drv)
    cv = ws.sys_canvas
    checked = 0
    for cy in range(tm.h):
        for cx in range(tm.w):
            tid = tm.mget(cx, cy)
            if tid < 0:
                continue
            px, py = _cell_px(ws, cx, cy)
            assert cv.pix(px, py) == _dominant(sheet, tid)
            checked += 1
    assert checked > 0, "the fixture map has no placed tiles"


def test_overview_leaves_empty_cells_as_the_map_field(tmp_path):
    from runtime.palette import NAMES
    ws, drv = _open_map(tmp_path)
    tm = ws.project.tilemap
    _to_overview(ws)
    _paint(ws, drv)
    cv = ws.sys_canvas
    empty = [(cx, cy) for cy in range(tm.h) for cx in range(tm.w)
             if tm.mget(cx, cy) < 0]
    assert empty, "the fixture map has no empty cells"
    for cx, cy in empty[:40]:
        px, py = _cell_px(ws, cx, cy)
        assert cv.pix(px, py) == NAMES["dark_blue"]


def test_overview_paints_the_view_as_blocks_and_field_and_no_lattice(tmp_path):
    # At 2-4px a 1px line on every side is most of the cell, so the lattice is
    # skipped: the minimap must not read as a field of grey. Counted rather than
    # sampled -- every pixel over the map is either a block or the field, so a
    # lattice of ANY colour (even one a tile happens to share) shows up as a count
    # that does not match.
    from collections import Counter
    from runtime.palette import NAMES
    ws, drv = _open_map(tmp_path)
    _to_overview(ws)
    _paint(ws, drv)
    cv = ws.sys_canvas
    sheet = ws.project.sheet
    tm = ws.project.tilemap
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    # Counted over the WHOLE view rectangle, not just the map: a block that runs
    # one pixel wide spills into the out-of-bounds black, and a map-sized window
    # would never see it.
    want = Counter()
    for ry in range(rows):
        for rx in range(cols):
            tid = tm.mget(rx, ry) if (rx < tm.w and ry < tm.h) else None
            if tid is None:
                want[NAMES["black"]] += cell * cell           # off the map
            elif tid < 0:
                want[NAMES["dark_blue"]] += cell * cell       # the map field
            else:
                want[_dominant(sheet, tid)] += cell * cell
    got = Counter()
    for y in range(y0, y0 + rows * cell):
        for x in range(x0, x0 + cols * cell):
            got[cv.pix(x, y)] += 1
    assert got == want


def test_overview_splits_a_colour_run_at_every_boundary(tmp_path):
    # The row runs are an optimisation, so the shape they collapse to has to be
    # pinned: alternating colours must produce one block PER CELL, edge to edge.
    ws, drv = _open_map(tmp_path)
    tm = ws.project.tilemap
    sheet = ws.project.sheet
    for n, c in ((1, 8), (2, 11)):           # two solid, distinguishable tiles
        for ly in range(8):
            for lx in range(8):
                sheet.tset(n, lx, ly, c)
    for cx in range(tm.w):
        tm.mset(cx, 0, 1 + (cx % 2))
    _to_overview(ws)
    _paint(ws, drv)
    cv = ws.sys_canvas
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    for cx in range(tm.w):
        want = _dominant(sheet, tm.mget(cx, 0))
        for edge in (0, cell - 1):
            assert cv.pix(x0 + cx * cell + edge, y0 + cell // 2) == want, cx


def test_overview_hit_tests_and_paints_the_cell_under_the_pointer(tmp_path):
    ws, drv = _open_map(tmp_path)
    tm = ws.project.tilemap
    _to_overview(ws)
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    target = (tm.w - 1, tm.h - 1)            # the far corner, only reachable here
    px, py = _cell_px(ws, *target)
    assert ws.map_ui._map_cell_at(px, py) == target
    ws.map_ui.mapedit.n = 5
    drv.touch(px, py); drv.frame(1 / 30); drv.touch_up(); drv.frame(1 / 30)
    assert tm.mget(*target) == 5


def test_overview_marquee_selects_the_dragged_cells(tmp_path):
    # SELECT (#91) at sub-8px cells: the marquee is cell-space, so it rubber-bands
    # over the whole level here rather than over one screenful of it.
    ws, drv = _open_map(tmp_path, "Sky Run")
    me = ws.map_ui.mapedit
    _to_overview(ws)
    ws.map_ui.map_tool = "select"
    tm = ws.project.tilemap
    far = (tm.w - 1, tm.h - 1)
    sx, sy = _cell_px(ws, 0, 0)
    ex, ey = _cell_px(ws, *far)
    drv.touch(sx, sy); drv.frame(1 / 30)
    drv.touch_drag(ex, ey); drv.frame(1 / 30)
    drv.touch_up(); drv.frame(1 / 30)
    assert me.sel == (0, 0, far[0], far[1])


def test_overview_ignores_a_tap_past_the_map_edge(tmp_path):
    # A small map leaves the rest of the view rectangle out of bounds; a tap there
    # hit-tests to a cell off the map and must stamp nothing.
    ws, drv = _open_map(tmp_path, "Brick Siege")
    tm = ws.project.tilemap
    _to_overview(ws)
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    assert cols > tm.w                        # there IS an out-of-bounds strip
    before = bytes(tm.cells)
    ws.map_ui.mapedit.n = 5
    px = x0 + (tm.w + 1) * cell + cell // 2
    py = y0 + cell // 2
    drv.touch(px, py); drv.frame(1 / 30); drv.touch_up(); drv.frame(1 / 30)
    assert bytes(tm.cells) == before


def test_keyboard_cycles_into_overview_and_the_dpad_still_clamps(tmp_path):
    # The T-Deck path: A cycles the zoom, the trackball/arrows pan. At overview the
    # map fits, so a pan is a no-op rather than a scroll off the level.
    ws, drv = _open_map(tmp_path, "Sky Run")
    rungs = len(ws.map_ui.layout.zooms)
    for _ in range(rungs - 1):
        _tap_button(drv, "a")
    assert ws.map_ui._mv_overview()
    for name in ("right", "down", "left", "up"):
        _tap_button(drv, name)
    assert (ws.map_ui.mapedit.cam_x, ws.map_ui.mapedit.cam_y) == (0, 0)
    _tap_button(drv, "a")                     # ...and it wraps back to the default
    assert ws.map_ui.map_zoom == 0


def test_overview_survives_a_relayout(tmp_path):
    # EditorApp.set_tab relayouts every tab on entry (#216), so re-entering the Map
    # tab must not silently drop the reader back to 8px. The rung is carried by
    # NAME, and the fitted cell is recomputed for the new view.
    ws, _drv = _open_map(tmp_path, "Sky Run")
    _to_overview(ws)
    cell = ws.map_ui._mv_metrics()[2]
    lay = ws.map_ui.layout
    ws.map_ui.relayout(lay.w, lay.h, lay.fs)          # same tier: nothing moves
    assert ws.map_ui._mv_overview()
    assert ws.map_ui._mv_metrics()[2] == cell
    ws.map_ui.relayout(1024, 600, 2)                  # a bigger view, more rungs
    assert ws.map_ui._mv_overview()
    assert ws.map_ui.map_zoom == len(ws.map_ui.layout.zooms) - 1
    assert ws.map_ui._mv_metrics()[2] >= cell
    ws.map_ui.map_zoom = 2                            # a DETAIL rung is carried too
    ws.map_ui.relayout(320, 240, 1)
    assert ws.map_ui.map_zoom == 2 and not ws.map_ui._mv_overview()


def test_a_map_resize_refits_the_overview_cell(tmp_path):
    # The rung holds no fitted state: the cell is computed from the live map, so
    # growing the map through the DIM panel re-fits on the next frame.
    ws, _drv = _open_map(tmp_path, "Brick Siege")
    tm = ws.project.tilemap
    _to_overview(ws)
    small = ws.map_ui._mv_metrics()[2]
    tm.resize(96, 96)
    ws.map_ui._map_clamp_cam()
    big = ws.map_ui._mv_metrics()[2]
    assert big < small
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    assert cols >= tm.w and rows >= tm.h


def test_overview_is_the_only_sub_tile_rung(tmp_path):
    from runtime.editors import SpriteSheet
    ws, _drv = _open_map(tmp_path, "Sky Run")
    for idx in range(len(ws.map_ui.layout.zooms)):
        ws.map_ui.map_zoom = idx
        cell = ws.map_ui._mv_metrics()[2]
        assert (cell < SpriteSheet.TILE) == ws.map_ui._mv_overview()


def test_the_map_editor_renders_at_overview_on_every_tier(tmp_path):
    # The draw itself, on the tiers whose layouts are not the frozen baseline.
    from runtime import host_app
    import ws_helpers
    for w, h, fs in TIERS:
        ws = host_app.build_workstation(
            str(tmp_path / ("t%dx%d" % (w, h))), sys_size=(w, h), font_scale=fs)
        ws_helpers.open_cart(ws, "Sky Run")
        ws._open_map()
        drv = host_app.ConsoleDriver(ws)
        _to_overview(ws)
        tm = ws.project.tilemap
        x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
        assert cols >= tm.w and rows >= tm.h, "%dx%d fs%d" % (w, h, fs)
        _paint(ws, drv)
        assert ws.sys_canvas.pix(x0 + cell // 2, y0 + cell // 2) >= 0


def test_the_issue_numbers_still_describe_the_shipped_maps():
    # #215 measured four maps that no longer fit; if a seed cart's map is retitled
    # or resized, this names it rather than letting a fit test quietly widen.
    want = {"platformer.moy": (40, 26), "harpoon_pop.moy": (40, 30),
            "layer_test.moy": (64, 30), "scroll_demo.moy": (100, 30)}
    got = {n: (w, h) for n, w, h in _shipped_maps() if n in want}
    assert got == want


def test_seed_cart_manifests_are_readable():
    # _shipped_maps walks folders, not the store; a folder without a manifest is a
    # broken seed cart and would make every fit assertion above vacuous.
    for d in sorted(CARTS.glob("*.moy")):
        if (d / "map.moymap").exists():
            assert json.loads((d / "manifest.json").read_text())["title"]
