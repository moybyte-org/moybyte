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

from array import array

try:
    import ui as _ui              # frozen on device
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui

try:
    from editors import MapEditor, KeyEdge
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import MapEditor, KeyEdge

# The shared pre-literate glyph vocabulary (#91 icon pass): the #91 toolbar (TOOL/
# UNDO/REDO/resize) + the resize panel's -/+ draw a 12x12 chrome glyph instead of a
# terse label. The shared glyph-button body lives in chrome (one implementation).
try:
    from chrome import _gbtn as _chrome_gbtn
except ImportError:  # pragma: no cover - direct host import (chrome not yet aliased)
    from runtime.chrome import _gbtn as _chrome_gbtn

# Map (tilemap) editor (#32): a panned view of the map on the left where each cell
# is the scaled sprite tile placed there, and a paged tile palette on the right to
# pick the brush tile. Tap a map cell to stamp the brush (or erase, when the ERASE
# toggle is on); tap a palette cell to select that tile id. Mirrors the paint
# editor's structure (grid + picker + close), with pan controls for maps
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
# ERASE toggle + CLOSE along the bottom-left (clear of the d-pad column). (SAVE
# lived at x58 here; #111 removed it -- the freed strip stays empty.)
_MAP_ERASE = (14, 198, 40, 20)
_MAP_CLOSE = (126, 198, 76, 20)
# Editor toolbar (#91): a compact strip in the title band (between the panel top
# and the map view, y 16..32) on the RIGHT, clear of the "MAP TILE n z1 *" title
# text on the left. TOOL cycles the paint tool (pen/box/fill), DIM opens the
# map-resize panel. UNDO/REDO left the strip in #111 (the ONE bar pair is THE undo).
# 26px buttons keep the labels two chars so the whole strip fits the 320px baseline.
_MAP_TOOL = (196, 17, 26, 13)
_MAP_DIM = (280, 17, 26, 13)
# The paint tools (#91), cycled by the TOOL button: STAMP (today's per-cell / SIZE
# brush), RECT (drag corner-to-corner, fill the rectangle), FLOOD (contiguous
# same-tile fill), SELECT (drag a marquee -> COPY/CUT the region -> tap-to-stamp
# PASTE, the paint editor's #90 region model applied to map cells). Two-char labels
# for the compact toolbar button.
_MAP_TOOLS = ("stamp", "rect", "flood", "select")
_MAP_TOOL_LABEL = {"stamp": "PN", "rect": "BX", "flood": "FL", "select": "SE"}
# The pre-literate glyph the TOOL button shows for the ACTIVE tool (#91 icon pass):
# a pen/brush for STAMP, a hollow box for RECT, a paint bucket for FLOOD, a dashed
# marquee for SELECT. _blit_glyph draws nothing for an unknown kind, so
# _MAP_TOOL_LABEL stays the fallback.
_MAP_TOOL_GLYPH = {"stamp": "paint", "rect": "rect_tool", "flood": "fill",
                   "select": "select"}
# SELECT-mode region actions (#91), mirroring the paint editor's copy/paste/cut. The
# tile palette is idle while selecting (region ops don't use the brush), so when the
# SELECT tool is active a COPY/CUT/PASTE strip is drawn OVER the palette column and
# hit-tested there instead of the palette. Geometry is computed on demand from the
# palette rect (like the resize panel's _dims_rects), so MapLayout gains no permanent
# field and the 320x240 idle-parity baseline is untouched.
_MAP_SEL_ACTIONS = ("copy", "cut", "paste")
_MAP_SEL_GLYPH = {"copy": "copy", "cut": "cut", "paste": "paste"}
_MAP_SEL_LABEL = {"copy": "CP", "cut": "CT", "paste": "PS"}
# Map resize (#91): the largest grid the +/- steppers will grow to. 96x96 = 9216
# cells (~9KB) is a generous kid level and still device-RAM-safe; shrink floors at
# 1x1 (TileMap.resize clamps too).
_MAP_MAX_DIM = 96
_MAP_MIN_DIM = 1
# Map editor gesture threshold (#37): a pointer drag farther than this many pixels
# from its press origin pans the visible window (drag = pan); a shorter press +
# release taps one cell (tap = paint). Touch-drag panning is the primary way to
# navigate a map larger than the 320x240 view, so it wins over drag-to-stamp.
_MAP_PAN_THRESH = 6

_BASE_W = 320
_BASE_H = 240


class MapLayout:
    """Responsive map-editor geometry (#39 step 3): the panel, the panned map view,
    the paged tile palette + pan d-pad + zoom column, and the ERASE/CLOSE/SKY
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
            self.erase_btn, self.close_btn = _MAP_ERASE, _MAP_CLOSE
            # UNDO/REDO left the strip in #111 (the ONE bar pair is now THE undo,
            # routed to this editor's op-history) -- TOOL + DIM keep their slots,
            # the freed middle is spacing.
            self.tool_btn, self.dim_btn = _MAP_TOOL, _MAP_DIM
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
        self.close_btn = (px + 118 * fs, row_y, 76 * fs, 20 * fs)
        # Editor toolbar (#91/#111): the TOOL/DIM strip, right-anchored in the title
        # band, mirrors the baseline geometry scaled to the panel (UNDO/REDO moved to
        # the ONE bar pair, so TOOL now abuts DIM).
        tb_y = py + 2 * fs
        tbw, tbh, gap = 26 * fs, 13 * fs, 2 * fs
        tb_right = p_right - 6 * fs
        self.dim_btn = (tb_right - tbw, tb_y, tbw, tbh)
        self.tool_btn = (self.dim_btn[0] - gap - tbw, tb_y, tbw, tbh)
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
        self.map_tool = "stamp"        # active paint tool (#91): stamp/rect/flood/select
        self._map_rect = None          # (x0,y0,x1,y1) cell corners while a RECT drags
        self._map_sel = None           # (x0,y0,x1,y1) live SELECT marquee drag box (#91)
        self.dims_open = False         # the map-resize (DIM) panel is showing (#91)
        self._mkey = KeyEdge()         # Ctrl+Z/Y edge tracker (#91)
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
        self.map_tool = "stamp"        # (#91) back to the plain per-cell brush
        self._map_rect = None
        self._map_sel = None
        self.dims_open = False
        self._mkey.reset()

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
            # In-editor undo/redo (#91): the host keyboard shortcut Ctrl+Z (0x1A) /
            # Ctrl+Y (0x19), edge-triggered (act on the 0->key press, no autorepeat),
            # mirroring the code editor's journal shortcut. On the device the on-screen
            # UNDO/REDO buttons are the touch-first affordance (no Ctrl key).
            k = getattr(i, "last_key", 0)
            self._mkey.undo_redo(k, self._map_undo, self._map_redo)
        ws._leave_or_home(ws._leave_menu)

    def _map_undo(self):
        """Step the in-editor edit journal back one gesture (#91), then re-clamp the
        camera (an undo can't move it, but a resize-undo path might)."""
        me = self.mapedit
        if me is not None and me.undo():
            self._map_clamp_cam()

    def _map_redo(self):
        me = self.mapedit
        if me is not None and me.redo():
            self._map_clamp_cam()

    def _map_cycle_tool(self):
        """Cycle the paint tool stamp -> rect -> flood -> select -> stamp (#91). Drops
        any half-built RECT/SELECT drag preview so switching mid-drag can't leave a
        stray outline."""
        try:
            idx = _MAP_TOOLS.index(self.map_tool)
        except ValueError:
            idx = 0
        self.map_tool = _MAP_TOOLS[(idx + 1) % len(_MAP_TOOLS)]
        self._map_rect = None
        self._map_sel = None

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

    def _map_cell_at_clamped(self, px, py):
        """Like _map_cell_at but never None: the pointer is snapped to the nearest
        visible cell then clamped to the map bounds (#91). Used by the RECT drag so
        a rubber-band that leaves the view still tracks a valid opposite corner."""
        me = self.mapedit
        tm = self.ws.project.tilemap
        if me is None or tm is None:
            return None
        x0, y0, cell, cols, rows = self._mv_metrics()
        rx = (px - x0) // cell
        ry = (py - y0) // cell
        if rx < 0:
            rx = 0
        elif rx > cols - 1:
            rx = cols - 1
        if ry < 0:
            ry = 0
        elif ry > rows - 1:
            ry = rows - 1
        cx = me.cam_x + rx
        cy = me.cam_y + ry
        if cx > tm.w - 1:
            cx = tm.w - 1
        if cy > tm.h - 1:
            cy = tm.h - 1
        return (cx, cy)

    # -- map resize panel (#91) ------------------------------------------------

    def _dims_rects(self):
        """Geometry for the map-resize (DIM) overlay panel: the panel itself plus its
        W-/W+/H-/H+ steppers and DONE button, centered in the content area and scaled
        with the font. Computed on demand (drawn/hit-tested only while dims_open) so
        it needs no permanent MapLayout real estate."""
        lay = self.layout
        fs = lay.fs
        pw = 168 * fs
        ph = 96 * fs
        bx, by, bw, bh = lay.body_fill
        x = bx + (bw - pw) // 2
        y = by + (bh - ph) // 2
        b = 22 * fs
        wy = y + 26 * fs
        hy = y + 50 * fs
        margin = 8 * fs
        return {
            "panel": (x, y, pw, ph),
            "w_dn": (x + margin, wy, b, b),
            "w_up": (x + pw - margin - b, wy, b, b),
            "h_dn": (x + margin, hy, b, b),
            "h_up": (x + pw - margin - b, hy, b, b),
            "done": (x + (pw - 64 * fs) // 2, y + ph - 26 * fs, 64 * fs, 20 * fs),
            "rows": (wy, hy),
        }

    def _map_resize(self, dw, dh):
        """Grow/shrink the map by (dw, dh) cells on the right/bottom edge (#91),
        clamped to [_MAP_MIN_DIM, _MAP_MAX_DIM]; content is preserved (top-left
        anchored). A structural change, so it drops the in-editor undo history and
        re-clamps the camera to the new bounds."""
        tm = self.ws.project.tilemap
        me = self.mapedit
        if tm is None:
            return
        new_w = tm.w + dw
        new_h = tm.h + dh
        if new_w < _MAP_MIN_DIM:
            new_w = _MAP_MIN_DIM
        elif new_w > _MAP_MAX_DIM:
            new_w = _MAP_MAX_DIM
        if new_h < _MAP_MIN_DIM:
            new_h = _MAP_MIN_DIM
        elif new_h > _MAP_MAX_DIM:
            new_h = _MAP_MAX_DIM
        if new_w == tm.w and new_h == tm.h:
            return
        tm.resize(new_w, new_h)
        if me is not None:
            me.clear_history()
        self._map_rect = None
        self._map_clamp_cam()

    def _dims_click(self, px, py):
        """Route a tap while the resize panel is open (#91): the +/- steppers resize
        by one row/column, DONE (or a tap outside the panel) closes it. Returns True
        so the tap never falls through to the map/palette behind the panel."""
        r = self._dims_rects()
        if self._in(px, py, r["w_dn"]):
            self._map_resize(-1, 0)
        elif self._in(px, py, r["w_up"]):
            self._map_resize(1, 0)
        elif self._in(px, py, r["h_dn"]):
            self._map_resize(0, -1)
        elif self._in(px, py, r["h_up"]):
            self._map_resize(0, 1)
        elif self._in(px, py, r["done"]) or not self._in(px, py, r["panel"]):
            self.dims_open = False
        return True

    # -- SELECT-mode region actions (#91) --------------------------------------

    def _sel_actions_rects(self):
        """Geometry for the SELECT-mode COPY/CUT/PASTE strip, computed on demand (drawn
        + hit-tested only while the select tool is active) so it needs no permanent
        MapLayout field and the 320x240 baseline stays byte-identical. Three buttons
        stacked vertically over the (idle) tile-palette column, scaled with the font."""
        lay = self.layout
        fs = lay.fs
        ax, ay, aw, ah = lay.tp_area
        bh = 20 * fs
        gap = 4 * fs
        r = {"panel": lay.tp_area}
        for i in range(len(_MAP_SEL_ACTIONS)):
            r[_MAP_SEL_ACTIONS[i]] = (ax, ay + i * (bh + gap), aw, bh)
        return r

    def _sel_actions_click(self, px, py):
        """Route a tap on the SELECT-mode action strip (over the palette). COPY needs a
        selection, CUT needs a selection, PASTE needs a clip -- an unusable button is a
        no-op (it's drawn dimmed). Returns True iff the tap hit the strip's column, so a
        tap there never falls through to the brush-pick behind it."""
        me = self.mapedit
        if me is None:
            return False
        r = self._sel_actions_rects()
        if self._in(px, py, r["copy"]):
            me.copy_selection()
            return True
        if self._in(px, py, r["cut"]):
            me.cut_selection()
            return True
        if self._in(px, py, r["paste"]):
            self._sel_paste_default(me)
            return True
        # A tap anywhere else in the palette column while selecting is swallowed (the
        # strip owns the column in this mode) so it can't accidentally re-pick a brush.
        return self._in(px, py, r["panel"])

    def _sel_paste_default(self, me):
        """The PASTE button stamps the clip at the active selection's top-left (so
        copy->paste lands in place), or at the top-left visible cell with no selection.
        In SELECT mode a tap on the map pastes at the tapped cell instead (#91)."""
        if not me.has_clip:
            return
        if me.sel is not None:
            me.paste(me.sel[0], me.sel[1])
        else:
            me.paste(me.cam_x, me.cam_y)

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
        if self.map_tool == "rect":
            # RECT rubber-band (#91): track the opposite corner; no panning while a
            # box is being drawn (the d-pad/arrows still pan). Drawn as a preview.
            cell = self._map_cell_at_clamped(px, py)
            if cell is not None and self._map_rect is not None:
                x0, y0, _, _ = self._map_rect
                self._map_rect = (x0, y0, cell[0], cell[1])
            return
        if self.map_tool == "select":
            # SELECT marquee (#91): rubber-band the selection box like RECT; no pan
            # while dragging (the d-pad still pans). Committed on release.
            cell = self._map_cell_at_clamped(px, py)
            if cell is not None and self._map_sel is not None:
                x0, y0, _, _ = self._map_sel
                self._map_sel = (x0, y0, cell[0], cell[1])
            return
        if self.map_tool == "flood":
            return                             # flood already applied on the press
        if not self._map_panning:
            thresh = self.layout.pan_thresh
            if abs(px - press[0]) < thresh and abs(py - press[1]) < thresh:
                return                         # still within the tap dead-zone
            self._map_panning = True           # crossed the threshold -> this is a pan
            self._map_drag = press
            self._map_revert_paint()           # undo the press-edge stamp (it was a pan)
            if self.mapedit is not None:       # discard the open edit batch (#91): the
                self.mapedit.abort_edit()      # cells are reverted, so it must not commit
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
        """Pointer up in the map view: the STAMP tap-paint already landed on the press
        edge (a pan would have reverted it), so it just closes the edit batch; the
        RECT tool fills its rubber-banded rectangle here as ONE undo step (#37/#91);
        the SELECT tool commits its marquee (a drag sets the selection, a tap either
        pastes the clip there or clears the selection -- mirroring the paint editor)."""
        me = self.mapedit
        if me is not None:
            if self.map_tool == "rect" and self._map_press is not None \
                    and self._map_rect is not None:
                x0, y0, x1, y1 = self._map_rect
                me.begin_edit()
                me.fill_rect(x0, y0, x1, y1, erase=self.map_erase)
                me.end_edit()
            elif self.map_tool == "select" and self._map_press is not None \
                    and self._map_sel is not None:
                x0, y0, x1, y1 = self._map_sel
                if x0 == x1 and y0 == y1:      # a tap (no marquee) -> paste or deselect
                    if me.has_clip:
                        me.paste(x0, y0)       # tap-to-stamp: drop the clip here (#91)
                    else:
                        me.clear_selection()   # tap on empty space clears the selection
                else:
                    me.set_selection(x0, y0, x1, y1)
            else:
                # STAMP: commit the press-edge stamp's batch (a no-op for flood, whose
                # batch already ended, or a pan, whose batch was aborted).
                me.end_edit()
        self._map_press = None
        self._map_panning = False
        self._map_drag = None
        self._map_paint_undo = None
        self._map_rect = None
        self._map_sel = None

    def _map_click(self, px, py):
        ws = self.ws
        me = self.mapedit
        if me is None:
            return
        if self.dims_open:                     # the resize panel eats every tap (#91)
            self._dims_click(px, py)
            return
        if self._in(px, py, self._mv_area()):  # a press in the map view: start a
            self._map_press = (px, py)         # gesture; the tool decides what it does.
            self._map_panning = False
            self._map_drag = None
            tm = ws.project.tilemap
            if tm is None:
                return
            tool = self.map_tool
            if tool == "rect":
                # RECT (#91): remember the start corner; the drag rubber-bands the
                # rectangle, release fills it. No paint yet, no pan (the d-pad pans).
                cell = self._map_cell_at_clamped(px, py)
                self._map_rect = (cell[0], cell[1], cell[0], cell[1]) if cell else None
            elif tool == "flood":
                # FLOOD (#91): a single tap fills the contiguous region right away --
                # one committed undo step. No drag/pan follow-up.
                cell = self._map_cell_at(px, py)
                if cell is not None and 0 <= cell[0] < tm.w and 0 <= cell[1] < tm.h:
                    me.begin_edit()
                    me.flood(cell[0], cell[1], erase=self.map_erase)
                    me.end_edit()
            elif tool == "select":
                # SELECT (#91): remember the marquee start corner; the drag rubber-bands
                # the box, release commits it (a drag sets the selection, a tap pastes
                # the clip / clears). No paint, no pan (the d-pad still pans).
                cell = self._map_cell_at_clamped(px, py)
                self._map_sel = (cell[0], cell[1], cell[0], cell[1]) if cell else None
            else:
                # STAMP (#37/#57): paint immediately so a tap is responsive; remember
                # the block + its prior bytes so a drag-that-becomes-a-pan reverts it.
                cell = self._map_cell_at(px, py)
                if cell is not None:
                    cx, cy = cell
                    if 0 <= cx < tm.w and 0 <= cy < tm.h:
                        me.begin_edit()
                        self._map_paint_undo = (self._map_stamp_cells(cx, cy),
                                                tm.dirty, tm.gen)
                        self._map_paint(cx, cy)
            return
        lay = self.layout
        # SELECT mode (#91): the COPY/CUT/PASTE strip is drawn OVER the palette column,
        # so it eats taps there before the brush-pick / palette-page logic below.
        if self.map_tool == "select" and self._sel_actions_click(px, py):
            return
        if self._in(px, py, lay.tool_btn):     # cycle stamp/rect/flood (#91)
            self._map_cycle_tool()
            return
        if self._in(px, py, lay.dim_btn):      # open the map-resize panel (#91)
            self.dims_open = True
            return
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
        elif self._in(px, py, lay.close_btn):
            # CLOSE runs+leaves to the cart (ws._leave_menu is EditorApp.leave --
            # PLAY, itself a hard-commit trigger now, #111: no SAVE tap exists).
            ws._leave_menu()

    # -- drawing -----------------------------------------------------------------

    def _gbtn(self, kind, label, rect, fill, cv):
        # #91 icon pass -- one shared body, chrome._gbtn.
        _chrome_gbtn(self.ws, self._NAMES, kind, label, rect, fill, cv)

    def _draw_map(self):
        # The map (tilemap) editor (#32): a panned view of the map on the left where
        # each cell shows the placed sprite tile, and a paged tile palette on the
        # right to pick the brush. Mirrors _draw_paint's structure (grid + picker +
        # close), drawn with the indexed API only so host == device. SYSTEM
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
        light = (not lay._base) or ws.light_chrome()  # tokens on every responsive tier; _base stays frozen only in DARK chrome
        cv.rect(*(lay.body_fill + ((th["surface"] if light else NAMES["black"]),)))
        # Map cells + the tile-palette strip back themselves (dark map field),
        # so the panel joins the surface on the light tiers too.
        # Only the sliver of `panel` that `body_fill` did not already cover in
        # this exact colour -- the full re-fill rewrote ~94% of ~450K px for
        # nothing (see ui.fill_uncovered).
        _ui.fill_uncovered(cv, lay.panel, lay.body_fill,
                           th["surface"] if light else NAMES["black"])
        cv.rectb(*(lay.panel + (NAMES["green"],)))
        # Live zoom metrics (#37 follow-up): one cell size drives the grid, the tile
        # upscale and the title's "z<level>" badge.
        x0, y0, cell, cols, rows = self._mv_metrics()
        if me is not None and me.n < 0:        # the EMPTY/"sky" brush (#37)
            title = "MAP  SKY"
        else:
            title = "MAP  TILE " + str(me.n if me else 0)
        title = title + "  z" + str(self.map_zoom + 1)
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
        # #163 span-batch: the per-cell rect+rectb pair (2 gated calls x every
        # visible cell, the tab's dominant dispatch cost) becomes THREE merged
        # quad groups in two fill_rects calls, pixel-identical by construction:
        #   1. backgrounds -- cells are disjoint, so the in-bounds block is ONE
        #      dark_blue quad and the out-of-bounds remainder two black strips;
        #   2. (sprites, unchanged -- they ride the existing spr batch);
        #   3. the grid lattice -- each cell's rectb edges merged into full-length
        #      1px lines (interior edges stay DOUBLED at k*cell and k*cell-1,
        #      exactly the pixels the per-cell outlines painted; overlaps at
        #      corners are same-color idempotent).
        # Lattice draws after sprites, like the per-cell order did.
        nx = min(cols, tm.w - me.cam_x)
        ny = min(rows, tm.h - me.cam_y)
        if nx < 0:
            nx = 0
        if ny < 0:
            ny = 0
        # The packed geometry is a pure function of this key, so it is memoised:
        # a paint gesture re-hits it every frame (camera fixed), a pan/zoom/
        # relayout re-keys and rebuilds (~4k int stores, ≈ the old per-cell
        # dispatch it replaces). The LATTICE array is ordered band-by-band (per
        # cell-row: 2 wide lines + the 2·cols short verticals of that band) --
        # full-height vertical lines drawn last measured +16ms on glass: 1px
        # column-major writes fetch one cache line per row, ~3.4MB of traffic,
        # where a band's writes stay inside an L2-resident ~122KB window.
        memo = getattr(self, "_grid_memo", None)
        key = (x0, y0, cols, rows, cell, nx, ny)
        if memo is None or memo[0] != key:
            blue = NAMES["dark_blue"]
            black = NAMES["black"]
            grey = NAMES["dark_grey"]
            bg = []
            if nx and ny:
                bg += [x0, y0, nx * cell, ny * cell, blue]
            if nx < cols:
                bg += [x0 + nx * cell, y0,
                       (cols - nx) * cell, rows * cell, black]
            if ny < rows and nx:
                bg += [x0, y0 + ny * cell,
                       nx * cell, (rows - ny) * cell, black]
            lat = []
            gw = cols * cell
            for k in range(rows):
                yb = y0 + k * cell
                lat += [x0, yb, gw, 1, grey]
                lat += [x0, yb + cell - 1, gw, 1, grey]
                for j in range(cols):
                    lat += [x0 + j * cell, yb, 1, cell, grey]
                    lat += [x0 + (j + 1) * cell - 1, yb, 1, cell, grey]
            memo = (key, array("h", bg), array("h", lat))
            self._grid_memo = memo
        cv.fill_rects(memo[1])
        for ry in range(ny):
            cy = me.cam_y + ry
            y = y0 + ry * cell
            for rx in range(nx):
                tid = tm.mget(me.cam_x + rx, cy)
                if tid >= 0:
                    img = cache.get(tid)
                    if img is None:
                        img = sheet.tile_image(tid, -1)
                        cache[tid] = img if img is not None else False
                    if img:
                        cv.spr(img, x0 + rx * cell + off, y + off, scale)
        cv.fill_rects(memo[2])
        # RECT preview (#91): while a box is being dragged, outline the covered cells
        # (clamped to the visible window) so the fill region is visible before release.
        if self.map_tool == "rect" and self._map_rect is not None \
                and self._map_press is not None:
            rx0, ry0, rx1, ry1 = self._map_rect
            if rx1 < rx0:
                rx0, rx1 = rx1, rx0
            if ry1 < ry0:
                ry0, ry1 = ry1, ry0
            ix0 = rx0 if rx0 > me.cam_x else me.cam_x
            iy0 = ry0 if ry0 > me.cam_y else me.cam_y
            vx1 = me.cam_x + cols - 1
            vy1 = me.cam_y + rows - 1
            ix1 = rx1 if rx1 < vx1 else vx1
            iy1 = ry1 if ry1 < vy1 else vy1
            if ix0 <= ix1 and iy0 <= iy1:
                rpx = x0 + (ix0 - me.cam_x) * cell
                rpy = y0 + (iy0 - me.cam_y) * cell
                rpw = (ix1 - ix0 + 1) * cell
                rph = (iy1 - iy0 + 1) * cell
                cv.rectb(rpx, rpy, rpw, rph, NAMES["yellow"])
                if rpw > 2 and rph > 2:
                    cv.rectb(rpx + 1, rpy + 1, rpw - 2, rph - 2, NAMES["yellow"])
        # SELECT overlay (#91): the committed selection is a solid white box, a live
        # marquee drag a yellow box (like the RECT preview). Both clamp to the visible
        # window. Drawn only in select mode, so the frozen baseline is untouched.
        if self.map_tool == "select":
            if me.sel is not None:
                self._draw_map_box(cv, x0, y0, cell, cols, rows, me.cam_x, me.cam_y,
                                   me.sel[0], me.sel[1], me.sel[2], me.sel[3],
                                   NAMES["white"], False)
            if self._map_sel is not None and self._map_press is not None:
                self._draw_map_box(cv, x0, y0, cell, cols, rows, me.cam_x, me.cam_y,
                                   self._map_sel[0], self._map_sel[1],
                                   self._map_sel[2], self._map_sel[3],
                                   NAMES["yellow"], True)
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
        # SELECT-mode region actions (#91): the COPY/CUT/PASTE strip is drawn OVER the
        # palette column (idle while selecting), so it sits after the palette tiles.
        if self.map_tool == "select":
            self._draw_sel_actions(cv, NAMES)
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
        # ERASE toggle (highlighted when active) + CLOSE.
        ws._btn("ER", lay.erase_btn, NAMES["red"] if self.map_erase else NAMES["dark_grey"], cv)
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
        # Editor toolbar (#91/#111): TOOL (the active tool's pen/box/bucket glyph) +
        # the resize glyph (open the resize panel). UNDO/REDO moved to the ONE bar
        # pair. Icons instead of the terse two-char labels (#91 icon pass).
        self._gbtn(_MAP_TOOL_GLYPH.get(self.map_tool),
                   _MAP_TOOL_LABEL.get(self.map_tool, "PN"), lay.tool_btn,
                   NAMES["orange"], cv)
        self._gbtn("resize", "WH", lay.dim_btn, NAMES["dark_green"], cv)
        # Map-resize overlay (#91): drawn LAST so it sits over the whole editor.
        if self.dims_open:
            self._draw_dims(cv, NAMES)

    def _draw_map_box(self, cv, x0, y0, cell, cols, rows, cam_x, cam_y,
                      bx0, by0, bx1, by1, color, double):
        """Outline the map-cell rectangle (bx0,by0)..(bx1,by1) (any orientation),
        clamped to the visible window, in `color` (#91 SELECT overlay). `double` draws
        an inner outline too (the thick live-drag box); a box fully off-window draws
        nothing. Mirrors the RECT-preview clamp so the two tools read alike."""
        if bx1 < bx0:
            bx0, bx1 = bx1, bx0
        if by1 < by0:
            by0, by1 = by1, by0
        ix0 = bx0 if bx0 > cam_x else cam_x
        iy0 = by0 if by0 > cam_y else cam_y
        vx1 = cam_x + cols - 1
        vy1 = cam_y + rows - 1
        ix1 = bx1 if bx1 < vx1 else vx1
        iy1 = by1 if by1 < vy1 else vy1
        if ix0 > ix1 or iy0 > iy1:
            return
        rpx = x0 + (ix0 - cam_x) * cell
        rpy = y0 + (iy0 - cam_y) * cell
        rpw = (ix1 - ix0 + 1) * cell
        rph = (iy1 - iy0 + 1) * cell
        cv.rectb(rpx, rpy, rpw, rph, color)
        if double and rpw > 2 and rph > 2:
            cv.rectb(rpx + 1, rpy + 1, rpw - 2, rph - 2, color)

    def _draw_sel_actions(self, cv, NAMES):
        """The SELECT-mode COPY/CUT/PASTE strip over the palette column (#91). COPY/CUT
        need a selection, PASTE a clip -- an unusable action is dimmed (mirrors the
        paint tool row). Geometry from _sel_actions_rects(); drawn only in select mode
        so the baseline is untouched."""
        me = self.mapedit
        if me is None:
            return
        r = self._sel_actions_rects()
        # Back the whole column so the palette tiles underneath don't bleed through.
        cv.rect(*(r["panel"] + (NAMES["black"],)))
        enabled = {"copy": me.sel is not None,
                   "cut": me.sel is not None,
                   "paste": me.has_clip}
        for act in _MAP_SEL_ACTIONS:
            on = enabled[act]
            self._gbtn(_MAP_SEL_GLYPH.get(act), _MAP_SEL_LABEL.get(act, "?"),
                       r[act], NAMES["indigo"] if on else NAMES["dark_grey"], cv)

    def _draw_dims(self, cv, NAMES):
        """The map-resize (DIM) overlay panel (#91): the current W x H with +/-
        steppers on the right/bottom edge and a DONE button, centered over the
        editor. Drawn only while dims_open; geometry from _dims_rects()."""
        ws = self.ws
        tm = ws.project.tilemap
        lay = self.layout
        fs = lay.fs
        r = self._dims_rects()
        px, py, pw, ph = r["panel"]
        cv.rect(px, py, pw, ph, NAMES["dark_blue"])
        cv.rectb(px, py, pw, ph, NAMES["white"])
        cv.print("MAP SIZE", px + 8 * fs, py + 6 * fs, NAMES["white"], 1)
        wy, hy = r["rows"]
        w = tm.w if tm is not None else 0
        h = tm.h if tm is not None else 0
        self._gbtn("minus", "-", r["w_dn"], NAMES["red"], cv)
        self._gbtn("plus", "+", r["w_up"], NAMES["green"], cv)
        self._gbtn("minus", "-", r["h_dn"], NAMES["red"], cv)
        self._gbtn("plus", "+", r["h_up"], NAMES["green"], cv)
        yoff = (22 * fs - 8 * fs) // 2
        cv.print("W " + str(w), px + pw // 2 - 14 * fs, wy + yoff, NAMES["white"], 1)
        cv.print("H " + str(h), px + pw // 2 - 14 * fs, hy + yoff, NAMES["white"], 1)
        ws._btn("DONE", r["done"], NAMES["green"], cv)
