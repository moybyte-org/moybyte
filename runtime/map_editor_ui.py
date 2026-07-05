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


def _mv_default_cell():
    """The largest cell size (px) that still fits the whole shipped maps with no
    panning: >= _MV_FIT_COLS columns AND >= _MV_FIT_ROWS rows in the available
    rectangle. Computed rather than hardcoded so the fit guarantee is provable.
    With a 192x164 area this is 9px (192//9 = 21 cols, 164//9 = 18 rows)."""
    cell = 4
    best = cell
    while cell <= 40:
        if _MV_AVAIL_W // cell >= _MV_FIT_COLS and _MV_AVAIL_H // cell >= _MV_FIT_ROWS:
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
        self.map_zoom = 0              # zoom level index into _MV_ZOOMS (0 = fit)
        self._map_drag = None          # last pointer (px,py) during a map pan drag (#37)
        self._map_press = None         # gesture origin (px,py); set on press, None on release
        self._map_panning = False      # this gesture has crossed the pan threshold (#37)
        self._map_paint_undo = None    # (cx,cy,prev_byte,dirty,gen) painted on press;
                                       # reverted if the gesture turns out to be a pan

    def build(self):
        """Build the MapEditor over the cart's TileMap + sheet (both always exist
        after Workstation.open()). Edits go straight into the live tilemap, so a
        running cart picks them up via tilemap.gen (#32). Called from
        Workstation.set_menu_view("map")."""
        ws = self.ws
        if self.mapedit is None and ws.tilemap is not None and ws.sheet is not None:
            self.mapedit = MapEditor(ws.tilemap, ws.sheet)

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
        sheet = self.ws.sheet
        if sheet is None:
            return []
        count = sheet.count
        start = self.map_page
        return list(range(start, min(start + _TP_PAGE, count)))

    def _mv_metrics(self):
        """The LIVE map-view metrics for the current zoom level (#37 follow-up):
        (x0, y0, cell, cols, rows). `cell` is the px per cell at the current zoom;
        `cols`/`rows` are how many whole cells fit the available rectangle. All map
        hit-testing, panning and drawing route through this so they share one cell
        size -- there is no fixed _MV_CELL/_MV_COLS/_MV_ROWS any more."""
        idx = self.map_zoom
        if idx < 0:
            idx = 0
        elif idx >= len(_MV_ZOOMS):
            idx = len(_MV_ZOOMS) - 1
        cell = _MV_ZOOMS[idx]
        cols = _MV_AVAIL_W // cell
        rows = _MV_AVAIL_H // cell
        return (_MV_X0, _MV_Y0, cell, cols, rows)

    def _mv_area(self):
        """The current map-view rectangle (x, y, w, h) for _in() hit-tests."""
        x0, y0, cell, cols, rows = self._mv_metrics()
        return (x0, y0, cols * cell, rows * cell)

    def _map_clamp_cam(self):
        """Clamp the camera so you can't scroll far past the map edges at the
        current zoom: the top-left visible cell stays in [0, max(0, dim - visible)],
        so a map smaller than the view always pins to (0, 0) (no panning needed)."""
        me = self.mapedit
        tm = self.ws.tilemap
        if me is None or tm is None:
            return
        x0, y0, cell, cols, rows = self._mv_metrics()
        me.cam_x = max(0, min(max(0, tm.w - cols), me.cam_x))
        me.cam_y = max(0, min(max(0, tm.h - rows), me.cam_y))

    def _map_cycle_zoom(self):
        """Cycle to the next zoom level (wrapping back to the fit-both default),
        then re-clamp the camera so a zoom-out can't leave it scrolled off-map."""
        self.map_zoom = (self.map_zoom + 1) % len(_MV_ZOOMS)
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
        ERASE toggle still forces a clear regardless of the brush."""
        me = self.mapedit
        if me is None:
            return
        if self.map_erase or me.n < 0:
            me.erase(cx, cy)
        else:
            me.place(cx, cy)

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
            if abs(px - press[0]) < _MAP_PAN_THRESH and abs(py - press[1]) < _MAP_PAN_THRESH:
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
        """Undo the cell stamped on the press edge (used when a press turns into a
        pan): restore the cell's previous byte AND the map's dirty/gen counters so a
        pure pan is side-effect-free (no false '*' dirty flag, no spurious cache
        rebuild in a running cart)."""
        u = self._map_paint_undo
        tm = self.ws.tilemap
        if u is not None and tm is not None:
            cx, cy, prev, dirty, gen = u
            if 0 <= cx < tm.w and 0 <= cy < tm.h:
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
            tm = ws.tilemap
            if cell is not None and tm is not None:
                cx, cy = cell
                if 0 <= cx < tm.w and 0 <= cy < tm.h:
                    self._map_paint_undo = (cx, cy, tm.cells[cy * tm.w + cx],
                                            tm.dirty, tm.gen)
                    self._map_paint(cx, cy)
            return
        if self._in(px, py, _TP_SKY):          # the EMPTY/"sky" swatch (#37)
            me.n = ws.tilemap.EMPTY if ws.tilemap is not None else -1
            return
        if self._in(px, py, _TP_AREA):         # pick the brush tile from the palette
            col = (px - _TP_X0) // _TP_CELL
            row = (py - _TP_Y0) // _TP_CELL
            if 0 <= col < _TP_COLS and 0 <= row < _TP_ROWS:
                k = row * _TP_COLS + col
                ids = self._map_palette_ids()
                if 0 <= k < len(ids):
                    me.n = ids[k]
        elif self._in(px, py, _TP_PREV):       # page the palette back/forward
            self.map_page = max(0, self.map_page - _TP_PAGE)
        elif self._in(px, py, _TP_NEXT):
            if ws.sheet is not None and self.map_page + _TP_PAGE < ws.sheet.count:
                self.map_page += _TP_PAGE
        elif self._in(px, py, _MAP_ZOOM):      # cycle the zoom level (#37 follow-up)
            self._map_cycle_zoom()
        elif self._in(px, py, _PAN_UP):
            self._map_pan(0, -1)
        elif self._in(px, py, _PAN_DN):
            self._map_pan(0, 1)
        elif self._in(px, py, _PAN_LF):
            self._map_pan(-1, 0)
        elif self._in(px, py, _PAN_RT):
            self._map_pan(1, 0)
        elif self._in(px, py, _MAP_ERASE):     # toggle stamp <-> erase
            self.map_erase = not self.map_erase
        elif self._in(px, py, _MAP_SAVE):
            ws.save_map()
        elif self._in(px, py, _MAP_CLOSE):
            ws._leave_menu()

    # -- drawing -----------------------------------------------------------------

    def _draw_map(self):
        # The map (tilemap) editor (#32): a panned view of the map on the left where
        # each cell shows the placed sprite tile, and a paged tile palette on the
        # right to pick the brush. Mirrors _draw_paint's structure (grid + picker +
        # save/close), drawn with the indexed API only so host == device.
        ws = self.ws
        NAMES = self._NAMES
        cv = ws.canvas
        me = self.mapedit
        sheet = ws.sheet
        cv.rect(8, 16, 304, 204, NAMES["black"])
        cv.rectb(8, 16, 304, 204, NAMES["green"])
        # Live zoom metrics (#37 follow-up): one cell size drives the grid, the tile
        # upscale and the title's "z<level>" badge.
        x0, y0, cell, cols, rows = self._mv_metrics()
        if me is not None and me.n < 0:        # the EMPTY/"sky" brush (#37)
            title = "MAP  SKY"
        else:
            title = "MAP  TILE " + str(me.n if me else 0)
        title = title + "  z" + str(self.map_zoom + 1)
        if ws.tilemap is not None and ws.tilemap.dirty:
            title = title + " *"
        cv.print(title, 14, 18, NAMES["green"], 1)
        if me is None or sheet is None or ws.tilemap is None:
            return
        tm = ws.tilemap
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
        ids = self._map_palette_ids()
        for k in range(len(ids)):
            tid = ids[k]
            x = _TP_X0 + (k % _TP_COLS) * _TP_CELL
            y = _TP_Y0 + (k // _TP_COLS) * _TP_CELL
            cv.rect(x, y, _TP_CELL, _TP_CELL, NAMES["black"])
            img = sheet.tile_image(tid, -1)
            if img is not None:
                cv.spr(img, x + (_TP_CELL - sheet.TILE) // 2,
                       y + (_TP_CELL - sheet.TILE) // 2, 1)
            cv.rectb(x, y, _TP_CELL, _TP_CELL,
                     NAMES["white"] if tid == me.n else NAMES["dark_grey"])
        ws._btn("<", _TP_PREV, NAMES["blue"])
        ws._btn(">", _TP_NEXT, NAMES["blue"])
        # ZOOM control (#37 follow-up): cycles the zoom level (in the d-pad center);
        # the title's "z<level>" badge shows which level is active.
        ws._btn("Z" + str(self.map_zoom + 1), _MAP_ZOOM, NAMES["dark_purple"])
        # Pan d-pad under the map view.
        ws._btn("^", _PAN_UP, NAMES["indigo"])
        ws._btn("v", _PAN_DN, NAMES["indigo"])
        ws._btn("<", _PAN_LF, NAMES["indigo"])
        ws._btn(">", _PAN_RT, NAMES["indigo"])
        # ERASE toggle (highlighted when active) + SAVE + CLOSE.
        ws._btn("ER", _MAP_ERASE, NAMES["red"] if self.map_erase else NAMES["dark_grey"])
        ws._btn("SAVE", _MAP_SAVE, NAMES["green"])
        ws._btn("CLOSE", _MAP_CLOSE, NAMES["red"])
        # EMPTY/"sky" swatch (#37): a selectable brush that paints "nothing". Drawn
        # as a checkerboard (the universal transparent cue) + "SKY" label, boxed
        # white when it's the active brush so it reads like any other palette pick.
        sx, sy, sw, sh = _TP_SKY
        cb = sh // 2
        cv.rect(sx, sy, sw, sh, NAMES["dark_blue"])
        cv.rect(sx, sy, cb, cb, NAMES["light_grey"])
        cv.rect(sx + cb, sy + cb, cb, cb, NAMES["light_grey"])
        cv.print("SKY", sx + sw - 26, sy + (sh - 8) // 2, NAMES["white"], 1)
        cv.rectb(sx, sy, sw, sh,
                 NAMES["white"] if me.n < 0 else NAMES["dark_grey"])
