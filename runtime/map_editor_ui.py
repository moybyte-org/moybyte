"""The map (tilemap) editor's UI layer (issue #32): a panned view of the map on
the left where each cell shows the placed sprite tile, a paged tile palette on
the right to pick the brush, a pan d-pad + zoom control, and tap-to-paint /
drag-to-pan gesture handling (#37 follow-up).

Extracted from Workstation (runtime/console.py), mirroring block_editor_ui.py's
BlockEditorUI: this class owns the map editor's UI state (mapedit/map_erase/
map_page/map_zoom/the pan-drag gesture fields, plus the responsive view-metrics
helpers `_mv_metrics`/`_mv_area`) and every `_map_*` method, verbatim (no
renaming), via a back-reference to the owning Workstation (`self.ws`) for the
handful of primitives it shares with the rest of the console (canvas, _btn,
_leave_menu, sheet, tilemap, save_map -- the last two are the cart's actual
resources, shared with the running game and the paint editor, so they stay on
Workstation rather than becoming map-only state). `NAMES`/`_in` are injected at
construction instead of imported back from console.py, which would be a real
circular import: console.py imports MapEditorUI to build the one instance a
Workstation holds (same reasoning as BlockEditorUI -- see its docstring).

Kept name-for-name with the pre-extraction Workstation methods/fields (no
renaming): Workstation._open_map/set_menu_view/open/go_home/handle_input/
handle_pointer/frame all just gained one level of indirection (`self.map_ui.X`
instead of `self.X`), and so did the tests that poke the map editor's
internals directly.
"""

try:
    from editors import MapEditor
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import MapEditor

# Map (tilemap) editor (#32): a panned view of the map on the left where each cell
# is the scaled sprite tile placed there, and a paged tile palette on the right to
# pick the brush tile. Tap a map cell to stamp the brush (or erase, when the ERASE
# toggle is on); tap a palette cell to select that tile id. Mirrors the paint
# editor's structure (grid + picker + save/close), with pan controls for maps
# larger than the on-screen window.
_MV_X0 = 14            # map view top-left (cells drawn scaled here)
_MV_Y0 = 32
# The map-view rectangle is sized dynamically from the current ZOOM level (#37
# follow-up): the cell size in px drives how many cells fit (and how big each tile
# is drawn). The available rectangle is x 14..~206, y 32..~196 -- clear of the tile
# palette (x >= 210) / pan d-pad / bottom button row (y = 198). All hit-testing,
# panning and drawing route through MapEditorUI._mv_metrics() so they share one
# live cell size; there is no fixed _MV_CELL/_MV_COLS/_MV_ROWS/_MV_AREA any more.
_MV_AVAIL_W = 192      # usable map-view width  (14 .. 206)
_MV_AVAIL_H = 164      # usable map-view height (32 .. 196)
# TIC-80-style zoom: a small ascending list of cell sizes in px. Zoom only goes IN
# from the default (bigger cells -> fewer cells -> more detail), so a tile is only
# ever UPscaled (no sub-8px downscaling). The DEFAULT (index 0, most zoomed-OUT) is
# computed -- not hardcoded -- as the largest cell that still shows the whole map of
# both shipped games with NO panning: battle_city is 15x15 and platformer is 20x13,
# so the default must fit >= 20 columns AND >= 15 rows in the available rectangle.
_MV_FIT_COLS = 20      # widest shipped map (platformer)
_MV_FIT_ROWS = 15      # tallest shipped map (battle_city)


def _mv_default_cell(avail_w=_MV_AVAIL_W, avail_h=_MV_AVAIL_H):
    """The largest cell size (px) that still fits the whole shipped maps with no
    panning: >= _MV_FIT_COLS columns AND >= _MV_FIT_ROWS rows in the available
    rectangle. Computed rather than hardcoded so the fit guarantee is provable.
    With the base 192x164 area this is 9px (192//9 = 21 cols, 164//9 = 18 rows);
    MapLayout (#39 step 3) passes the reflowed rectangle so a bigger view fits the
    whole map at a bigger cell."""
    cell = 4
    best = cell
    while cell <= 40:
        if avail_w // cell >= _MV_FIT_COLS and avail_h // cell >= _MV_FIT_ROWS:
            best = cell
        cell += 1
    return best


# Zoom levels, ascending: index 0 is the fit-both default; the rest zoom IN. The
# zoomed-in sizes are multiples of the 8px tile (16/24/32) so each tile UPSCALES to
# fill its cell exactly (scale = cell // 8 = 2/3/4) -- crisp pixel art, no floating
# 8px tile in a big box.
_MV_ZOOMS = [_mv_default_cell(), 16, 24, 32]
# ZOOM control: a small button in the map editor that cycles the zoom level. Sits
# in the empty CENTER of the pan d-pad (between UP/DOWN/LEFT/RIGHT) -- a natural,
# TIC-80-ish spot that overlaps nothing (palette PREV/NEXT end at y 140; the d-pad
# arrows surround x 244,y 164 without filling its center).
_MAP_ZOOM = (244, 164, 24, 16)
# Tile palette (right): a paged grid of sheet tiles; tap to pick the brush.
_TP_X0 = 210
_TP_Y0 = 32
_TP_CELL = 22
_TP_COLS = 4
_TP_ROWS = 4
_TP_PAGE = _TP_COLS * _TP_ROWS          # tiles shown per palette page
_TP_AREA = (_TP_X0, _TP_Y0, _TP_COLS * _TP_CELL, _TP_ROWS * _TP_CELL)
_TP_PREV = (_TP_X0, _TP_Y0 + _TP_ROWS * _TP_CELL + 2, 42, 18)        # page back
_TP_NEXT = (_TP_X0 + 46, _TP_Y0 + _TP_ROWS * _TP_CELL + 2, 42, 18)   # page forward
# Empty/"sky" swatch (#37): a first-class, selectable palette entry on the bottom
# button row (right of CLOSE). Picking it sets the brush to EMPTY so a tap paints
# "nothing" -- the transparent/background cell map() skips -- like any other tile.
_TP_SKY = (206, 198, 100, 20)
# SIZE brush (#57): cycles the stamp size 1/2/3 (one tile / a 16x16 sprite's 2x2
# block / 24x24), mirroring the paint editor's SIZE. Sits in the free top-left
# corner slot of the pan d-pad cluster, styled like the ZOOM button ("S2" ~ "Z2").
_MAP_SIZE = (218, 146, 24, 16)
# Pan d-pad (right column, under the palette): 4 arrow buttons that scroll the
# view. Kept clear of the map view (x < 196) and the bottom button row (y = 198).
_PAN_UP = (244, 146, 24, 16)
_PAN_LF = (218, 164, 24, 16)
_PAN_RT = (270, 164, 24, 16)
_PAN_DN = (244, 182, 24, 16)
# ERASE toggle + SAVE + CLOSE along the bottom-left (clear of the d-pad column).
_MAP_ERASE = (14, 198, 40, 20)
_MAP_SAVE = (58, 198, 64, 20)
_MAP_CLOSE = (126, 198, 76, 20)
# Map editor gesture threshold (#37): a pointer drag farther than this many pixels
# from its press origin pans the visible window (drag = pan); a shorter press +
# release taps one cell (tap = paint). Touch-drag panning is the primary way to
# navigate a map larger than the 320x240 view, so it wins over drag-to-stamp.
_MAP_PAN_THRESH = 6

_BASE_W = 320
_BASE_H = 240


class MapLayout:
    """Responsive map-editor geometry (#39 step 3): the panel, the panned map view,
    the paged tile palette + pan d-pad + zoom column, and the ERASE/SAVE/CLOSE/SKY
    bottom row, derived from the SYSTEM canvas size (w, h) + font scale.

    The single hard contract (mirrors Layout/CodeLayout/PaintLayout): at (w, h, fs)
    == (320, 240, 1) every field equals the frozen `_MV_*`/`_TP_*`/`_PAN_*`/`_MAP_*`
    module constant, byte for byte (the `_base` branch); the responsive formulas only
    run on a larger canvas / bigger font.

    The map VIEW is the star of the reflow: its available rectangle grows to fill
    the panel (so a big screen shows far more of the map at once), the zoom list's
    fit-both default is recomputed for the bigger view, and larger zoom-in cell
    sizes (48/64) join the cycle once the view can afford them. The palette gains
    rows to fill its column."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(font_scale))
        fs = self.fs
        self._base = (self.w == _BASE_W and self.h == _BASE_H and fs == 1)
        if self._base:
            self.body_fill = (0, 18, _BASE_W, _BASE_H - 18)
            self.panel = (8, 16, 304, 204)
            self.title_xy = (14, 18)
            self.mv_x0, self.mv_y0 = _MV_X0, _MV_Y0
            self.mv_avail_w, self.mv_avail_h = _MV_AVAIL_W, _MV_AVAIL_H
            self.zooms = tuple(_MV_ZOOMS)
            self.tp_x0, self.tp_y0 = _TP_X0, _TP_Y0
            self.tp_cell, self.tp_cols, self.tp_rows = _TP_CELL, _TP_COLS, _TP_ROWS
            self.tp_page = _TP_PAGE
            self.tp_area = _TP_AREA
            self.tp_prev, self.tp_next = _TP_PREV, _TP_NEXT
            self.sky_btn = _TP_SKY
            self.zoom_btn = _MAP_ZOOM
            self.size_btn = _MAP_SIZE
            self.pan_up, self.pan_lf, self.pan_rt, self.pan_dn = \
                _PAN_UP, _PAN_LF, _PAN_RT, _PAN_DN
            self.erase_btn, self.save_btn, self.close_btn = \
                _MAP_ERASE, _MAP_SAVE, _MAP_CLOSE
            self.pan_thresh = _MAP_PAN_THRESH
            return
        # -- responsive: anchor the palette/d-pad column to the panel's right edge,
        # the button row to its bottom, and grow the map view to fill the rest ----
        bar_h = 18 * fs
        px, py = 8 * fs, bar_h - 2 * fs
        pw, ph = self.w - 16 * fs, self.h - (bar_h - 2 * fs) - 20 * fs
        self.body_fill = (0, bar_h, self.w, self.h - bar_h)
        self.panel = (px, py, pw, ph)
        p_right = px + pw
        p_bottom = py + ph
        self.title_xy = (px + 6 * fs, py + 2 * fs)
        row_y = p_bottom - 22 * fs                # bottom button row (base 198)
        rc_x = p_right - 106 * fs                 # right column origin (base 206)
        self.mv_x0 = px + 6 * fs
        self.mv_y0 = py + 16 * fs
        self.mv_avail_w = rc_x - self.mv_x0
        self.mv_avail_h = row_y - self.mv_y0 - 2 * fs
        fit = _mv_default_cell(self.mv_avail_w, self.mv_avail_h)
        self.zooms = (fit,) + tuple(z for z in (16, 24, 32, 48, 64) if z > fit)
        # Pan d-pad cluster, bottom-anchored just above the button row.
        pan_dn_y = row_y - 16 * fs
        pan_mid_y = row_y - 34 * fs
        pan_up_y = row_y - 52 * fs
        bw, bh = 24 * fs, 16 * fs
        self.pan_up = (rc_x + 38 * fs, pan_up_y, bw, bh)
        self.pan_lf = (rc_x + 12 * fs, pan_mid_y, bw, bh)
        self.pan_rt = (rc_x + 64 * fs, pan_mid_y, bw, bh)
        self.pan_dn = (rc_x + 38 * fs, pan_dn_y, bw, bh)
        self.zoom_btn = (rc_x + 38 * fs, pan_mid_y, bw, bh)
        self.size_btn = (rc_x + 12 * fs, pan_up_y, bw, bh)
        # Tile palette fills the column between the title band and the d-pad.
        self.tp_x0 = rc_x + 4 * fs
        self.tp_y0 = self.mv_y0
        self.tp_cell = _TP_CELL * fs
        self.tp_cols = _TP_COLS
        self.tp_rows = max(1, (pan_up_y - 22 * fs - self.tp_y0) // self.tp_cell)
        self.tp_page = self.tp_cols * self.tp_rows
        self.tp_area = (self.tp_x0, self.tp_y0,
                        self.tp_cols * self.tp_cell, self.tp_rows * self.tp_cell)
        tp_by = self.tp_y0 + self.tp_rows * self.tp_cell + 2 * fs
        self.tp_prev = (self.tp_x0, tp_by, 42 * fs, 18 * fs)
        self.tp_next = (self.tp_x0 + 46 * fs, tp_by, 42 * fs, 18 * fs)
        self.sky_btn = (rc_x, row_y, 100 * fs, 20 * fs)
        self.erase_btn = (px + 6 * fs, row_y, 40 * fs, 20 * fs)
        self.save_btn = (px + 50 * fs, row_y, 64 * fs, 20 * fs)
        self.close_btn = (px + 118 * fs, row_y, 76 * fs, 20 * fs)
        self.pan_thresh = _MAP_PAN_THRESH * fs


class MapEditorUI:
    """The map/tilemap editor's UI: pan/zoom view + tile palette + gesture
    handling (draw + input/pointer). One instance lives on Workstation
    (`self.map_ui`), built once in Workstation.__init__; `ws.map_ui.build()` is
    called lazily from `set_menu_view("map")` the first time a cart's map
    editor is opened, exactly like the pre-extraction code did inline."""

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        # Injected instead of imported back from console.py -- see module docstring.
        self._NAMES = names
        self._in = in_rect
        self.mapedit = None            # MapEditor while menu_view == "map" (#32)
        self.map_erase = False         # tap-to-erase instead of stamp
        self.map_page = 0              # first tile id shown in the palette
        self.map_zoom = 0              # zoom level index into layout.zooms (0 = fit)
        self._map_drag = None          # last pointer (px,py) during a map pan drag (#37)
        self._map_press = None         # gesture origin (px,py); set on press, None on release
        self._map_panning = False      # this gesture has crossed the pan threshold (#37)
        self._map_paint_undo = None    # ([(cx,cy,prev_byte),...],dirty,gen) for the
                                       # block painted on press (#57: the SIZE brush
                                       # stamps s x s cells); reverted if the gesture
                                       # turns out to be a pan
        sc = ws.sys_canvas
        self.layout = MapLayout(sc.w, sc.h, getattr(sc, "font_scale", 1))

    def relayout(self, w, h, fs):
        """Rebuild the responsive geometry (#39 step 3) -- called by ws._relayout on
        a font-scale change. Re-clamps the zoom index + camera, since the reflowed
        view may have a different zoom list / visible span."""
        self.layout = MapLayout(w, h, fs)
        if self.map_zoom >= len(self.layout.zooms):
            self.map_zoom = 0
        self._map_clamp_cam()

    def build(self):
        """Build the MapEditor over the cart's TileMap + sheet (both always exist
        after Workstation.open()). Edits go straight into the live tilemap, so a
        running cart picks them up via tilemap.gen (#32). Called from
        Workstation.set_menu_view("map")."""
        ws = self.ws
        if self.mapedit is None and ws.project.tilemap is not None and ws.project.sheet is not None:
            self.mapedit = MapEditor(ws.project.tilemap, ws.project.sheet)

    def reset(self):
        """Drop the active editor (a stale one must never leak into an unrelated
        cart or back to the launcher). Called from Workstation.open() (switching
        carts) and Workstation.go_home()."""
        self.mapedit = None

    def on_open(self):
        """Reset gesture/zoom state (#37) -- called from Workstation._open_map,
        before set_menu_view("map") (re)builds the editor."""
        self.map_erase = False
        self.map_zoom = 0              # reset to the fit-both default zoom (#37 follow-up)
        self._map_press = None         # fresh gesture state on open (#37)
        self._map_panning = False
        self._map_drag = None
        self._map_paint_undo = None

    # -- input -----------------------------------------------------------------

    def _map_input(self):
        """The d-pad pans the visible map window (the grid is bigger than the
        screen); B leaves (#37). Painting stays pointer/touch-driven. Called from
        Workstation.handle_input's menu_view == "map" branch."""
        ws = self.ws
        i = ws.input
        me = self.mapedit
        if me is not None:
            if i.pressed("up"):
                self._map_pan(0, -1)
            if i.pressed("down"):
                self._map_pan(0, 1)
            if i.pressed("left"):
                self._map_pan(-1, 0)
            if i.pressed("right"):
                self._map_pan(1, 0)
            if i.pressed("a"):          # A cycles the zoom level (#37 follow-up)
                self._map_cycle_zoom()
        ws._leave_or_home(ws._leave_menu)

    def _map_palette_ids(self):
        """The tile ids shown on the current palette page (a window into the sheet,
        clamped so the last page never runs past the sheet's tile count)."""
        sheet = self.ws.project.sheet
        if sheet is None:
            return []
        count = sheet.count
        start = self.map_page
        return list(range(start, min(start + self.layout.tp_page, count)))

    def _mv_metrics(self):
        """The LIVE map-view metrics for the current zoom level (#37 follow-up):
        (x0, y0, cell, cols, rows). `cell` is the px per cell at the current zoom;
        `cols`/`rows` are how many whole cells fit the available rectangle. All map
        hit-testing, panning and drawing route through this so they share one cell
        size; the rectangle + zoom list come from the responsive MapLayout (#39)."""
        lay = self.layout
        idx = self.map_zoom
        if idx < 0:
            idx = 0
        elif idx >= len(lay.zooms):
            idx = len(lay.zooms) - 1
        cell = lay.zooms[idx]
        cols = lay.mv_avail_w // cell
        rows = lay.mv_avail_h // cell
        return (lay.mv_x0, lay.mv_y0, cell, cols, rows)

    def _mv_area(self):
        """The current map-view rectangle (x, y, w, h) for _in() hit-tests."""
        x0, y0, cell, cols, rows = self._mv_metrics()
        return (x0, y0, cols * cell, rows * cell)

    def _map_clamp_cam(self):
        """Clamp the camera so you can't scroll far past the map edges at the
        current zoom: the top-left visible cell stays in [0, max(0, dim - visible)],
        so a map smaller than the view always pins to (0, 0) (no panning needed)."""
        me = self.mapedit
        tm = self.ws.project.tilemap
        if me is None or tm is None:
            return
        x0, y0, cell, cols, rows = self._mv_metrics()
        me.cam_x = max(0, min(max(0, tm.w - cols), me.cam_x))
        me.cam_y = max(0, min(max(0, tm.h - rows), me.cam_y))

    def _map_cycle_zoom(self):
        """Cycle to the next zoom level (wrapping back to the fit-both default),
        then re-clamp the camera so a zoom-out can't leave it scrolled off-map."""
        self.map_zoom = (self.map_zoom + 1) % len(self.layout.zooms)
        self._map_clamp_cam()

    def _map_pan(self, dcx, dcy):
        """Pan the camera by (dcx, dcy) cells then clamp to the map edges at the
        current zoom (the editor's clamp, which knows the visible cols/rows -- the
        MapEditor.pan clamp is zoom-agnostic so we re-clamp here)."""
        me = self.mapedit
        if me is None:
            return
        me.cam_x = me.cam_x + dcx
        me.cam_y = me.cam_y + dcy
        self._map_clamp_cam()

    def _map_cell_at(self, px, py):
        """The map cell (cx, cy) under pointer (px, py) accounting for the pan
        offset, or None when the pointer is outside the visible map view."""
        me = self.mapedit
        if me is None or not self._in(px, py, self._mv_area()):
            return None
        x0, y0, cell, cols, rows = self._mv_metrics()
        cx = me.cam_x + (px - x0) // cell
        cy = me.cam_y + (py - y0) // cell
        return (cx, cy)

    def _map_paint(self, cx, cy):
        """Stamp the brush at map cell (cx, cy): the EMPTY brush (#37) clears the
        cell (paints sky/background), otherwise the brush's tile is placed. The
        ERASE toggle still forces a clear regardless of the brush. With SIZE > 1
        (#57) both stamp and erase cover the brush's whole cell block."""
        me = self.mapedit
        if me is None:
            return
        if self.map_erase or me.n < 0:
            me.erase(cx, cy)
        else:
            me.place(cx, cy)

    def _map_stamp_cells(self, cx, cy):
        """Every in-map cell the press-edge paint at (cx, cy) will touch, with its
        prior byte: [(cx, cy, prev), ...] -- the snapshot _map_revert_paint puts
        back when the gesture turns out to be a pan. Size 1 is one cell, the SIZE
        brush (#57) an s x s block (erase always the full square, a stamp the
        sheet-clamped stamp_span)."""
        me = self.mapedit
        tm = self.ws.project.tilemap
        if self.map_erase or me.n < 0:
            tw = th = me.size
        else:
            tw, th = me.stamp_span()
        cells = []
        for dy in range(th):
            for dx in range(tw):
                x = cx + dx
                y = cy + dy
                if 0 <= x < tm.w and 0 <= y < tm.h:
                    cells.append((x, y, tm.cells[y * tm.w + x]))
        return cells

    def _map_pan_drag(self, px, py):
        """Held-drag handler for the map view (#37): once a drag that began inside
        the map view moves past _MAP_PAN_THRESH px it latches PAN mode for the rest
        of the gesture and scrolls the camera by the drag delta (in cells), so the
        content follows the finger. A drag is a pan, not a paint -- so when it
        latches it REVERTS the cell stamped on the press edge (the tap-paint), which
        means a tap paints and a drag pans without a stray stamp at the origin."""
        press = self._map_press
        if press is None:
            return
        if not self._map_panning:
            thresh = self.layout.pan_thresh
            if abs(px - press[0]) < thresh and abs(py - press[1]) < thresh:
                return                         # still within the tap dead-zone
            self._map_panning = True           # crossed the threshold -> this is a pan
            self._map_drag = press
            self._map_revert_paint()           # undo the press-edge stamp (it was a pan)
        me = self.mapedit
        last = self._map_drag
        if me is None or last is None:
            return
        x0, y0, cell, cols, rows = self._mv_metrics()
        dcx = (last[0] - px) // cell           # content follows the finger: drag
        dcy = (last[1] - py) // cell           # right -> see cells to the left
        if dcx or dcy:
            self._map_pan(dcx, dcy)
            # advance the anchor by whole cells consumed (keep the sub-cell remainder
            # so a slow drag still accumulates instead of stalling).
            self._map_drag = (last[0] - dcx * cell, last[1] - dcy * cell)

    def _map_revert_paint(self):
        """Undo the block stamped on the press edge (used when a press turns into a
        pan): restore every touched cell's previous byte AND the map's dirty/gen
        counters so a pure pan is side-effect-free (no false '*' dirty flag, no
        spurious cache rebuild in a running cart). The snapshot's cells were
        validated in-map when _map_stamp_cells built it."""
        u = self._map_paint_undo
        tm = self.ws.project.tilemap
        if u is not None and tm is not None:
            cells, dirty, gen = u
            for cx, cy, prev in cells:
                tm.cells[cy * tm.w + cx] = prev
            tm.dirty = dirty
            tm.gen = gen
        self._map_paint_undo = None

    def _map_release(self, px, py):
        """Pointer up in the map view (#37): the tap-paint already landed on the
        press edge (and a pan would have reverted it), so release just clears the
        gesture state."""
        self._map_press = None
        self._map_panning = False
        self._map_drag = None
        self._map_paint_undo = None

    def _map_click(self, px, py):
        ws = self.ws
        me = self.mapedit
        if me is None:
            return
        if self._in(px, py, self._mv_area()):  # a press in the map view: start a
            self._map_press = (px, py)         # gesture (tap=paint / drag=pan).
            self._map_panning = False
            self._map_drag = None
            # Paint immediately so a tap is responsive; remember the cell + its prior
            # byte so a drag-that-becomes-a-pan can revert it (no stray stamp) (#37).
            cell = self._map_cell_at(px, py)
            tm = ws.project.tilemap
            if cell is not None and tm is not None:
                cx, cy = cell
                if 0 <= cx < tm.w and 0 <= cy < tm.h:
                    self._map_paint_undo = (self._map_stamp_cells(cx, cy),
                                            tm.dirty, tm.gen)
                    self._map_paint(cx, cy)
            return
        lay = self.layout
        if self._in(px, py, lay.sky_btn):      # the EMPTY/"sky" swatch (#37)
            me.n = ws.project.tilemap.EMPTY if ws.project.tilemap is not None else -1
            return
        if self._in(px, py, lay.tp_area):      # pick the brush tile from the palette
            col = (px - lay.tp_x0) // lay.tp_cell
            row = (py - lay.tp_y0) // lay.tp_cell
            if 0 <= col < lay.tp_cols and 0 <= row < lay.tp_rows:
                k = row * lay.tp_cols + col
                ids = self._map_palette_ids()
                if 0 <= k < len(ids):
                    me.n = ids[k]
        elif self._in(px, py, lay.tp_prev):    # page the palette back/forward
            self.map_page = max(0, self.map_page - lay.tp_page)
        elif self._in(px, py, lay.tp_next):
            if ws.project.sheet is not None and self.map_page + lay.tp_page < ws.project.sheet.count:
                self.map_page += lay.tp_page
        elif self._in(px, py, lay.size_btn):   # cycle the SIZE brush 1/2/3 (#57)
            me.cycle_size()
        elif self._in(px, py, lay.zoom_btn):   # cycle the zoom level (#37 follow-up)
            self._map_cycle_zoom()
        elif self._in(px, py, lay.pan_up):
            self._map_pan(0, -1)
        elif self._in(px, py, lay.pan_dn):
            self._map_pan(0, 1)
        elif self._in(px, py, lay.pan_lf):
            self._map_pan(-1, 0)
        elif self._in(px, py, lay.pan_rt):
            self._map_pan(1, 0)
        elif self._in(px, py, lay.erase_btn):  # toggle stamp <-> erase
            self.map_erase = not self.map_erase
        elif self._in(px, py, lay.save_btn):
            ws.save_map()
        elif self._in(px, py, lay.close_btn):
            ws._leave_menu()

    # -- drawing -----------------------------------------------------------------

    def _draw_map(self):
        # The map (tilemap) editor (#32): a panned view of the map on the left where
        # each cell shows the placed sprite tile, and a paged tile palette on the
        # right to pick the brush. Mirrors _draw_paint's structure (grid + picker +
        # save/close), drawn with the indexed API only so host == device. SYSTEM
        # canvas + MapLayout geometry (#39 step 3): a bigger panel shows more map.
        ws = self.ws
        NAMES = self._NAMES
        cv = ws.sys_canvas
        lay = self.layout
        me = self.mapedit
        sheet = ws.project.sheet
        # Cover the FULL content area below the bar first (Fix 3): the panel doesn't
        # span edge to edge, so without this fill stale pixels would bleed through
        # the side/bottom strips. Match the cards tab (fills the whole area) so the
        # editor is fully opaque.
        # Phase 3 (visual identity v1): warm body on the shelf tiers, dark map
        # canvas kept on every tier. Baseline literals byte-identical.
        th = ws.theme_colors
        light = (not lay._base) and ws.light_chrome()
        cv.rect(*(lay.body_fill + ((th["surface"] if light else NAMES["black"]),)))
        # Map cells + the tile-palette strip back themselves (dark map field),
        # so the panel joins the surface on the light tiers too.
        cv.rect(*(lay.panel + ((th["surface"] if light else NAMES["black"]),)))
        cv.rectb(*(lay.panel + (NAMES["green"],)))
        # Live zoom metrics (#37 follow-up): one cell size drives the grid, the tile
        # upscale and the title's "z<level>" badge.
        x0, y0, cell, cols, rows = self._mv_metrics()
        if me is not None and me.n < 0:        # the EMPTY/"sky" brush (#37)
            title = "MAP  SKY"
        else:
            title = "MAP  TILE " + str(me.n if me else 0)
        title = title + "  z" + str(self.map_zoom + 1)
        if ws.project.tilemap is not None and ws.project.tilemap.dirty:
            title = title + " *"
        cv.print(title, lay.title_xy[0], lay.title_xy[1],
                 th["ink"] if light else NAMES["green"], 1)
        if me is None or sheet is None or ws.project.tilemap is None:
            return
        tm = ws.project.tilemap
        # Visible map region: each cell is the sprite tile placed there, UPSCALED to
        # fill the cell (scale = cell // TILE, crisp pixel-art) and centered, with grid
        # lines so empty cells read as empty. Tile images are cached by id within the
        # draw so a repeated tile builds once.
        cache = {}
        scale = max(1, cell // sheet.TILE)
        off = (cell - sheet.TILE * scale) // 2
        for ry in range(rows):
            cy = me.cam_y + ry
            for rx in range(cols):
                cx = me.cam_x + rx
                x = x0 + rx * cell
                y = y0 + ry * cell
                inb = (cx < tm.w and cy < tm.h)
                cv.rect(x, y, cell, cell,
                        NAMES["dark_blue"] if inb else NAMES["black"])
                if inb:
                    tid = tm.mget(cx, cy)
                    if tid >= 0:
                        img = cache.get(tid)
                        if img is None:
                            img = sheet.tile_image(tid, -1)
                            cache[tid] = img if img is not None else False
                        if img:
                            cv.spr(img, x + off, y + off, scale)
                cv.rectb(x, y, cell, cell, NAMES["dark_grey"])
        # Tile palette (right): a page of sheet tiles; the brush tile is boxed white.
        # The tile art stays its native size (a bigger palette shows MORE tiles per
        # page via extra rows, and the fs-scaled cell just gives it more air).
        ids = self._map_palette_ids()
        tscale = max(1, lay.tp_cell // (sheet.TILE + 6))
        # SIZE-brush footprint (#57): the OTHER tiles of the brush's stamp block
        # get a lighter outline in the palette, so a 2x2/3x3 pick visibly shows
        # which consecutive tiles will land on the map with it.
        block = set()
        if me.n >= 0 and me.size > 1:
            tw, th = me.stamp_span()
            for dy in range(th):
                for dx in range(tw):
                    block.add(me.n + dy * sheet.cols + dx)
        for k in range(len(ids)):
            tid = ids[k]
            x = lay.tp_x0 + (k % lay.tp_cols) * lay.tp_cell
            y = lay.tp_y0 + (k // lay.tp_cols) * lay.tp_cell
            cv.rect(x, y, lay.tp_cell, lay.tp_cell, NAMES["black"])
            img = sheet.tile_image(tid, -1)
            if img is not None:
                cv.spr(img, x + (lay.tp_cell - sheet.TILE * tscale) // 2,
                       y + (lay.tp_cell - sheet.TILE * tscale) // 2, tscale)
            cv.rectb(x, y, lay.tp_cell, lay.tp_cell,
                     NAMES["white"] if tid == me.n else
                     (NAMES["light_grey"] if tid in block else NAMES["dark_grey"]))
        ws._btn("<", lay.tp_prev, NAMES["blue"], cv)
        ws._btn(">", lay.tp_next, NAMES["blue"], cv)
        # SIZE brush (#57): cycles the stamp size (top-left d-pad corner slot);
        # labeled like the zoom button ("S2" ~ "Z2").
        ws._btn("S" + str(me.size), lay.size_btn, NAMES["dark_purple"], cv)
        # ZOOM control (#37 follow-up): cycles the zoom level (in the d-pad center);
        # the title's "z<level>" badge shows which level is active.
        ws._btn("Z" + str(self.map_zoom + 1), lay.zoom_btn, NAMES["dark_purple"], cv)
        # Pan d-pad under the map view.
        ws._btn("^", lay.pan_up, NAMES["indigo"], cv)
        ws._btn("v", lay.pan_dn, NAMES["indigo"], cv)
        ws._btn("<", lay.pan_lf, NAMES["indigo"], cv)
        ws._btn(">", lay.pan_rt, NAMES["indigo"], cv)
        # ERASE toggle (highlighted when active) + SAVE + CLOSE.
        ws._btn("ER", lay.erase_btn, NAMES["red"] if self.map_erase else NAMES["dark_grey"], cv)
        ws._btn("SAVE", lay.save_btn, NAMES["green"], cv)
        ws._btn("CLOSE", lay.close_btn, NAMES["red"], cv)
        # EMPTY/"sky" swatch (#37): a selectable brush that paints "nothing". Drawn
        # as a checkerboard (the universal transparent cue) + "SKY" label, boxed
        # white when it's the active brush so it reads like any other palette pick.
        sx, sy, sw, sh = lay.sky_btn
        fs = lay.fs
        cb = sh // 2
        cv.rect(sx, sy, sw, sh, NAMES["dark_blue"])
        cv.rect(sx, sy, cb, cb, NAMES["light_grey"])
        cv.rect(sx + cb, sy + cb, cb, cb, NAMES["light_grey"])
        cv.print("SKY", sx + sw - 26 * fs, sy + (sh - 8 * fs) // 2, NAMES["white"], 1)
        cv.rectb(sx, sy, sw, sh,
                 NAMES["white"] if me.n < 0 else NAMES["dark_grey"])
