# Map (grep -n a name to jump there):
#   MapLayout          the map editor's geometry: the handheld page's or a desk pane's
#   MapEditorUI        the map editor's UI: the panned, zoomed view and its gestures
#   MapEditorUI.build  build the MapEditor over the cart's map
"""The map (tilemap) editor's UI layer (issue #32): a panned, zoomed view of
the map where each cell shows the placed sprite tile, and the tap-to-paint /
drag-to-pan gestures over it (#37). On the handheld the Map tab is the page
`runtime/editor_map.py` draws round the view (its brushes, tools and foot);
in a desk Editor's Map pane the view fills the pane above a status line, the
brush is the Sheet pane's and the tools the pane header's
(`runtime/editor_desk.py`).

One instance lives on Workstation (`ws.map_ui`), with a back-reference to it
for the cart's shared resources (the sheet and the tilemap, which a run
copies at its start). `NAMES` is injected at construction instead of
imported back from console.py, which would be a circular import.
"""

from array import array

try:
    import ui as _ui              # frozen on device
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
_in = _ui.rect_in   # one hit-test (ui.rect_in)

try:
    from editors import MapEditor, KeyEdge
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import MapEditor, KeyEdge

try:
    from editor_map import bands as _bands
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editor_map import bands as _bands

try:
    from layout_base import LayoutBase, BASE_W as _BASE_W, BASE_H as _BASE_H, pane as _in_pane
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import (LayoutBase, BASE_W as _BASE_W,
                                     BASE_H as _BASE_H, pane as _in_pane)

# The handheld page (editor_map.py): the right column's width and the foot's
# height, beside and under the view.
HAND_COL = 104
HAND_FOOT = 27

# TIC-80-style zoom: a small ascending list of DETAIL CELL SIZES in px. Every rung
# is a multiple of the 8px tile, so a tile UPSCALES to fill its cell exactly (scale
# = cell // 8) -- crisp pixel art, never a floating 8px tile in a bigger box. 8px is
# the floor of this list because `spr` cannot downscale: below it a tile spills over
# its neighbours.
#
# The cell size is the MAP FIELD SIZE and zoom is what changes it. It deliberately
# does NOT adapt to the map or to the screen: a wider view then shows MORE OF THE
# LEVEL at the same field size, which is what the extra pixels are for.
#
# It used to compute a cell that fit a fixed 20x15 target instead, and that inverted
# on the desktop tier -- the P4's 1024x600 picked a 29px cell and showed 26x15, FEWER
# rows than the 320x240 T-Deck on eight times the pixels, because the extra space went
# into bigger cells rather than more map.
_MV_ZOOMS = [8, 16, 24, 32]
# The OVERVIEW rung (#215), appended to `layout.zooms` AFTER the detail sizes: the
# sentinel is not a cell size but "compute the largest cell that shows the most map",
# resolved by _mv_fit_cell against the live map + view. It is the only rung that draws
# a cell as a solid block of its tile's dominant colour instead of the tile -- a
# minimap. The detail rungs are untouched, and the rung the editor OPENS on is still
# zooms[0], the 8px field size: overview is where you find yourself in a level, not
# where you paint it, and a tap on a 2px cell is not an edit gesture. Cycling reaches
# it in one wrap.
#
# The cell lives BETWEEN the two constants. _MV_FIT_MAX is one below the tile, so the
# rung is ALWAYS sub-8 and always draws blocks -- a map small enough to fit at 12px
# still gets the block view rather than a fifth, near-identical tile rung. _MV_FIT_MIN
# is the READABILITY floor (owner call on T-Deck glass, 2026-09-06): Sky Run's 100
# columns fit the 192px view only at 1px per cell, and a 1px minimap is not a picture
# of a level. Below the floor the rung stops shrinking and PANS like every other rung
# -- same camera, same clamp, and the cursor/tap/trackball paths already work at
# sub-8px cells. Seeing most of a level at 4px beats seeing all of it at 1px.
#
# This is a FIT TARGET, not a survey of shipped maps. The constants it replaced
# (_MV_FIT_COLS = 20 "widest shipped map") were read as a survey and went stale the
# day a wider map shipped; nothing here needs updating when one does, because the
# target is the whole map, whatever its size.
_MV_OVERVIEW = 0
_MV_FIT_MAX = 7
_MV_FIT_MIN = 4
# The OVERVIEW rung's name in a pane's status line ("zoom OV").
_MV_OV_LABEL = "OV"
# The tiles a page of the handheld page's palette shows: four rows of four.
_TP_PAGE = 16
# Map resize (#91): the largest grid Resize grows to. 96x96 = 9216
# cells (~9KB) is a generous kid level and still device-RAM-safe; shrink floors at
# 1x1 (TileMap.resize clamps too).
_MAP_MAX_DIM = 96
_MAP_MIN_DIM = 1
# Map editor gesture threshold (#37): a pointer drag farther than this many pixels
# from its press origin pans the visible window (drag = pan); a shorter press +
# release taps one cell (tap = paint). Touch-drag panning is the primary way to
# navigate a map larger than the 320x240 view, so it wins over drag-to-stamp.
_MAP_PAN_THRESH = 6


def _mv_fit_cell(avail_w, avail_h, mw, mh):
    """The largest whole-pixel cell at which an mw x mh map fits an avail_w x
    avail_h view, capped at _MV_FIT_MAX and floored at _MV_FIT_MIN (#215). A map
    that needs a smaller cell than the floor does not fit whole: it draws at the
    floor and PANS, exactly as the detail rungs do."""
    if mw < 1 or mh < 1:
        return _MV_FIT_MAX
    cell = avail_w // mw
    ch = avail_h // mh
    if ch < cell:
        cell = ch
    if cell > _MV_FIT_MAX:
        return _MV_FIT_MAX
    return cell if cell > _MV_FIT_MIN else _MV_FIT_MIN


class MapLayout(LayoutBase):
    """The map editor's geometry, from the SYSTEM canvas size (w, h) and font
    scale. On the handheld (`verbs`) the view runs from the content rect's
    top left to the page's right column and foot, which
    `editor_map.MapPage` draws; in a desk Editor's Map pane (`pane`) the
    view fills the pane above a status line.

    The zoom list is the detail sizes, then the OVERVIEW sentinel LAST on
    every tier, so the rung a reader is on is a name, not an index."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1,
                 chrome_scale=None):
        LayoutBase.__init__(self, w, h, font_scale, chrome_scale=chrome_scale)
        fs = self.fs
        cx, cy, cw, ch = self.cx, self.cy, self.cw, self.ch
        self.body_fill = (cx, cy, cw, ch)
        self.zooms = tuple(_MV_ZOOMS) + (48, 64, _MV_OVERVIEW)
        self.pan_thresh = _MAP_PAN_THRESH * fs
        self.tp_page = _TP_PAGE
        self.pane = _in_pane()
        self.verbs = not self.pane
        if self.pane:
            line = _ui_draw.font("ui")[2] * fs
            self.status_h = line + 8 * fs
            self.mv_x0, self.mv_y0 = cx + 2 * fs, cy + 2 * fs
            self.mv_avail_w = cw - 4 * fs
            self.mv_avail_h = ch - 4 * fs - self.status_h
            self.status_xy = (cx + 8 * fs, cy + ch - self.status_h + 4 * fs)
        else:
            self.mv_x0, self.mv_y0 = cx, cy
            self.mv_avail_w = cw - HAND_COL
            self.mv_avail_h = ch - HAND_FOOT - 1
            self.tp_x0 = cx + cw - HAND_COL + 6
            self.tp_y0 = cy + 6


class MapEditorUI:
    """The map/tilemap editor's UI: pan/zoom view + tile palette + gesture
    handling (draw + input/pointer). One instance lives on Workstation
    (`self.map_ui`), built once in Workstation.__init__; `ws.map_ui.build()` is
    called lazily from `set_menu_view("map")` the first time a cart's map
    editor is opened, exactly like the pre-extraction code did inline."""

    def __init__(self, ws, names):
        self.ws = ws
        # Injected instead of imported back from console.py -- see module docstring.
        self._NAMES = names
        self.mapedit = None            # MapEditor while menu_view == "map" (#32)
        self.map_erase = False         # tap-to-erase instead of stamp
        self.map_page = 0              # first tile id shown in the palette
        self.map_zoom = 0              # zoom level index into layout.zooms (0 = fit)
        self.map_tool = "stamp"        # active paint tool (#91): stamp/rect/flood/select
        self._map_rect = None          # (x0,y0,x1,y1) cell corners while a RECT drags
        self._map_sel = None           # (x0,y0,x1,y1) live SELECT marquee drag box (#91)
        self._mkey = KeyEdge()         # Ctrl+Z/Y edge tracker (#91)
        self._map_drag = None          # last pointer (px,py) during a map pan drag (#37)
        self._map_press = None         # gesture origin (px,py); set on press, None on release
        self._map_panning = False      # this gesture has crossed the pan threshold (#37)
        self._map_paint_undo = None    # ([(cx,cy,prev_byte),...],dirty,gen) for the
                                       # block painted on press (#57: the SIZE brush
                                       # stamps s x s cells); reverted if the gesture
                                       # turns out to be a pan
        self._ov_buf = None            # the OVERVIEW rung's reused quad buffer (#215)
        sc = ws.sys_canvas
        self.layout = MapLayout(sc.w, sc.h, getattr(sc, "font_scale", 1))

    _pan_acc = [0, 0]
    _hover_cell = None
    _page = None
    _hold = 0                          # frames a still press has been held

    def page(self):
        """The handheld's Map page on the verbs, built at its first use."""
        if self._page is None:
            try:
                from editor_map import MapPage
            except ImportError:  # pragma: no cover - host fallback
                from runtime.editor_map import MapPage
            self._page = MapPage(self, _ui_draw.AppHost("editor_map"))
        return self._page

    def pointer(self, px, py, click, down):
        """A pointer sample on the handheld's page (the layer's, after the
        bar's): a press in the view is the map's gesture, held still it picks
        the cell's tile; the rest are the verbs'."""
        page = self.page()
        if page.pop is None:
            if click and _in(px, py, self._mv_area()):
                self._hold = 0
                self._map_click(px, py)
                return True
            if self._map_press is not None:
                if down:
                    self._map_pan_drag(px, py)
                    return self._held_pick(px, py)
                self._map_release(px, py)
                return True
            if click and page.palette_tap(px, py):
                return True
        return page.point(px, py, click, down)

    # A press held still this many frames picks the tile under it.
    HOLD_FRAMES = 15

    def _held_pick(self, px, py):
        """A press held still in the view: its cell's tile becomes the
        brush and the press's stamp is taken back. Whether it picked."""
        me = self.mapedit
        if (me is None or self._map_panning or self.map_tool != "stamp"
                or self._hold < 0):
            return False
        self._hold += 1
        if self._hold < self.HOLD_FRAMES:
            return False
        self._hold = -1
        cell = self._map_cell_at(*self._map_press)
        tm = self.ws.project.tilemap
        if cell is None or tm is None:
            return False
        self._map_revert_paint()
        me.abort_edit()
        me.n = tm.mget(cell[0], cell[1])
        self.map_erase = False
        self._map_press = None
        return True

    def relayout(self, w, h, fs, cs=None):
        """Rebuild the responsive geometry (#39 step 3) -- called by ws._relayout on
        a font-scale change AND by EditorApp.set_tab on every entry into the tab
        (#216). Re-clamps the zoom index + camera, since the reflowed view may have
        a different zoom list / visible span; the OVERVIEW rung is carried by NAME
        rather than by index, because it is last in a list whose length differs per
        tier and a plain clamp would silently drop the reader back to 8px."""
        old = self.layout.zooms
        overview = (0 <= self.map_zoom < len(old)
                    and old[self.map_zoom] == _MV_OVERVIEW)
        self.layout = MapLayout(w, h, fs, chrome_scale=cs)
        if overview:
            self.map_zoom = len(self.layout.zooms) - 1
        elif self.map_zoom >= len(self.layout.zooms):
            self.map_zoom = 0
        self._map_clamp_cam()

    def build(self):
        """Build the MapEditor over the cart's TileMap + sheet (both always exist
        after Workstation.open()). Edits go into the Editor's tilemap, which a
        run copies at its start and at Play (`ProjectStore.play_world`, E-02). Called from
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
        self.map_zoom = 0              # the 8px field-size rung (#37 follow-up)
        self._map_press = None         # fresh gesture state on open (#37)
        self._map_panning = False
        self._map_drag = None
        self._map_paint_undo = None
        self.map_tool = "stamp"        # (#91) back to the plain per-cell brush
        self._map_rect = None
        self._map_sel = None
        self._mkey.reset()

    # -- input -----------------------------------------------------------------

    def at_top(self):
        """The map's camera on its top row and no popover open: an up goes
        on to the bar (Workstation._bar_keys)."""
        me = self.mapedit
        page = self._page if self.layout.verbs else None
        return me is not None and me.cam_y <= 0 and (page is None or page.pop is None)

    def _map_input(self):
        """The d-pad pans the visible map window (the grid is bigger than the
        screen); B leaves (#37). Painting stays pointer/touch-driven. Called from
        Workstation.handle_input's menu_view == "map" branch."""
        ws = self.ws
        i = ws.input
        me = self.mapedit
        page = self._page if self.layout.verbs else None
        if page is not None:
            if page.pop is not None:
                if page.keys(i):
                    ws._dirty = True
                if i.pressed("b"):
                    page.pop = None
                    ws._dirty = True
                return
            if page.owed():
                ws._dirty = True
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
        ws._leave_or_home(ws.exit)

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
        size; the rectangle + zoom list come from the responsive MapLayout (#39).

        The OVERVIEW rung's entry is a sentinel, not a size: its cell is fitted to
        the live map here (#215), so a resize re-fits it with no state to keep."""
        lay = self.layout
        idx = self.map_zoom
        if idx < 0:
            idx = 0
        elif idx >= len(lay.zooms):
            idx = len(lay.zooms) - 1
        cell = lay.zooms[idx]
        if cell == _MV_OVERVIEW:
            cell = self._mv_overview_cell()
        cols = lay.mv_avail_w // cell
        rows = lay.mv_avail_h // cell
        return (lay.mv_x0, lay.mv_y0, cell, cols, rows)

    def _mv_overview_cell(self):
        """The OVERVIEW rung's cell size for the map that is open."""
        lay = self.layout
        tm = self.ws.project.tilemap
        if tm is None:
            return _MV_FIT_MAX
        return _mv_fit_cell(lay.mv_avail_w, lay.mv_avail_h, tm.w, tm.h)

    def _mv_overview(self):
        """True while the view is on the OVERVIEW rung -- the cell is below one
        tile, so cells draw as dominant-colour blocks instead of sprites."""
        lay = self.layout
        idx = self.map_zoom
        return 0 <= idx < len(lay.zooms) and lay.zooms[idx] == _MV_OVERVIEW

    def _mv_zoom_label(self):
        """The rung's badge: its ordinal for a detail size, _MV_OV_LABEL for the
        OVERVIEW rung, which has no fixed cell size to number."""
        return _MV_OV_LABEL if self._mv_overview() else str(self.map_zoom + 1)

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
        """Cycle to the next zoom rung -- the detail sizes in order, then OVERVIEW,
        then back to the 8px field size -- and re-clamp the camera so a zoom-out
        can't leave it scrolled off-map."""
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

    def wheel_zoom(self, px, py, d):
        """A wheel notch at (px, py): the next zoom in (`d` < 0, a notch up)
        or out, a whole step, the cell under the pointer kept under it. The
        overview is the press's and the A button's, never the wheel's."""
        me = self.mapedit
        lay = self.layout
        if me is None:
            return
        last = len(lay.zooms) - 1
        if lay.zooms[last] == _MV_OVERVIEW:
            last -= 1
        z = max(0, min(last, self.map_zoom - d))
        if z == self.map_zoom:
            return
        x0, y0, cell, _c, _r = self._mv_metrics()
        cx = me.cam_x + (px - x0) // cell
        cy = me.cam_y + (py - y0) // cell
        self.map_zoom = z
        x0, y0, cell, _c, _r = self._mv_metrics()
        me.cam_x = cx - (px - x0) // cell
        me.cam_y = cy - (py - y0) // cell
        self._map_clamp_cam()

    def drag_pan(self, dx, dy):
        """The view dragged `dx`, `dy` pixels (a middle drag, the wheel
        across): the camera the other way, a cell at a time."""
        me = self.mapedit
        if me is None:
            return
        _x0, _y0, cell, _c, _r = self._mv_metrics()
        acc = self._pan_acc
        acc[0] -= dx
        acc[1] -= dy
        sx, sy = int(acc[0] / cell), int(acc[1] / cell)
        if sx or sy:
            acc[0] -= sx * cell
            acc[1] -= sy * cell
            self._map_pan(sx, sy)

    def _map_cell_at(self, px, py):
        """The map cell (cx, cy) under pointer (px, py) accounting for the pan
        offset, or None when the pointer is outside the visible map view."""
        me = self.mapedit
        if me is None or not _in(px, py, self._mv_area()):
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

    # -- SELECT-mode region actions (#91) --------------------------------------

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
        got = self._map_cell_at(px, py)
        if got is not None:
            self._hover_cell = got
        ws = self.ws
        me = self.mapedit
        if me is None:
            return
        if _in(px, py, self._mv_area()):  # a press in the map view: start a
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

    # -- drawing -----------------------------------------------------------------

    def _draw_map(self):
        """The Map tab: the handheld's page round the view, or a desk pane's
        view over its status line. Drawn with the indexed API only, so host
        == device."""
        ws = self.ws
        cv = ws.sys_canvas
        lay = self.layout
        th = ws.theme_colors
        if lay.verbs:
            self.page().draw(cv, th, (lay.cx, lay.cy, lay.cw, lay.ch), False)
            return
        bx, by, bw, bh = lay.body_fill
        col = th["surface"]
        me = self.mapedit
        tm = ws.project.tilemap
        if me is None or ws.project.sheet is None or tm is None:
            cv.rect(bx, by, bw, bh, col)
            return
        # The view fills its own rect first (its cells' backgrounds): the
        # body round it is all that takes the surface.
        vx, vy, vw, vh = self._mv_area()
        vx = max(bx, min(vx, bx + bw))
        vy = max(by, min(vy, by + bh))
        vr = max(vx, min(vx + vw, bx + bw))
        vb = max(vy, min(vy + vh, by + bh))
        cv.rect(bx, by, bw, vy - by, col)
        cv.rect(bx, vb, bw, by + bh - vb, col)
        cv.rect(bx, vy, vx - bx, vb - vy, col)
        cv.rect(vr, vy, bx + bw - vr, vb - vy, col)
        self.draw_view(cv)
        self._pane_status(cv, lay, me, tm, th)

    def draw_view(self, cv):
        """The map field: the window of the map at the zoom, its lattice, and
        the RECT and SELECT overlays (the handheld page's view as well)."""
        ws = self.ws
        NAMES = self._NAMES
        me = self.mapedit
        sheet = ws.project.sheet
        x0, y0, cell, cols, rows = self._mv_metrics()
        tm = ws.project.tilemap
        # Visible map region: each cell is the sprite tile placed there, UPSCALED to
        # fill the cell (scale = cell // TILE, crisp pixel-art) and centered, with grid
        # lines so empty cells read as empty. Tile images are cached by id within the
        # draw so a repeated tile builds once.
        #
        # On the OVERVIEW rung (#215) the cell is below one tile, so a cell is a solid
        # block of its tile's dominant colour instead, and the lattice is SKIPPED: at
        # 2-4px a 1px grid line on every side is most of the cell, and at 1px it is the
        # whole of it -- the minimap would be a field of grey.
        overview = self._mv_overview()
        # #163 span-batch: the per-cell rect+rectb pair (2 gated calls x every
        # visible cell, the tab's dominant dispatch cost) becomes THREE merged
        # quad groups in a few fill_rects calls (the lattice a call per
        # `editor_map.CHUNK` of it), pixel-identical by construction:
        #   1. backgrounds -- cells are disjoint, so the in-bounds block is ONE
        #      dark_blue quad and the out-of-bounds remainder two black strips;
        #   2. (the tiles between them are ONE map() -- see below);
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
        # relayout re-keys and rebuilds: one band of the lattice, copied down
        # the rows (`editor_map.bands`). The LATTICE array is ordered band-by-band (per
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
            lat = ()
            if not overview:
                gw = cols * cell
                band = [x0, y0, gw, 1, grey, x0, y0 + cell - 1, gw, 1, grey]
                for j in range(cols):
                    band += (x0 + j * cell, y0, 1, cell, grey,
                             x0 + (j + 1) * cell - 1, y0, 1, cell, grey)
                lat = _bands(array("h", band), rows, cell)
            memo = (key, array("h", bg), lat)
            self._grid_memo = memo
        cv.fill_rects(memo[1])
        if overview:
            self._draw_map_blocks(cv, tm, sheet, me, x0, y0, cell, nx, ny)
        else:
            # The visible window IS a map() region -- every detail rung's cell is a
            # whole multiple of the tile (tests/test_map_tile_blit.py pins the
            # ladder), so the cells sit on exactly the lattice blit_map walks. One
            # native call for the window replaces an Image blit per cell, and it is
            # the SAME kernel the cart's own map() runs, so a tile in the editor is
            # the tile the cart draws.
            cv.map(tm, sheet, me.cam_x, me.cam_y, nx, ny, x0, y0, -1,
                   cell // sheet.TILE)
            for lat in memo[2]:
                cv.fill_rects(lat)
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
        # window. Drawn only in select mode.
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

    def _pane_status(self, cv, lay, me, tm, th):
        """The Map pane's status line: the cell the pointer is over, the
        brush's tile, the map's size, the zoom, and the hold's pick."""
        fs = lay.fs
        cell = getattr(self, "_hover_cell", None)
        parts = []
        if cell is not None:
            parts.append("Cell %d, %d" % cell)
        parts.append("tile %d" % me.n if me.n >= 0 else "sky")
        parts.append("%d\u00d7%d" % (tm.w, tm.h) if _ui.has_symbols() else "%dx%d" % (tm.w, tm.h))
        parts.append("zoom " + self._mv_zoom_label())
        parts.append("hold a cell to pick its tile")
        text = "  \u00b7  ".join(parts) if _ui.has_symbols() else "  -  ".join(parts)
        cv.print(_ui_draw.print_cut(text, lay.cw - 16 * fs, fs),
                 lay.status_xy[0], lay.status_xy[1], th["ink_dim"], 1)

    def _draw_map_blocks(self, cv, tm, sheet, me, x0, y0, cell, nx, ny):
        """The OVERVIEW rung's minimap (#215): every placed cell as a solid block of
        its tile's dominant colour, equal-colour neighbours merged into one quad per
        RUN along a row, one fill_rects batch per row. An empty cell emits nothing, so
        the map field's own blue reads as sky exactly like it does at 8px.

        The quad buffer is sized to the VISIBLE COLUMNS and reused across rows and
        frames. A whole-map array would be up to 9216 quads on a 96x96 map (the resize
        ceiling) -- ~90KB on a board, to draw a picture 96px wide."""
        if nx < 1 or ny < 1:
            return
        need = nx * 5
        buf = self._ov_buf
        if buf is None or len(buf) < need:
            buf = array("h", (0,) * need)
            self._ov_buf = buf
        cells = tm.cells
        w = tm.w
        tile_color = sheet.tile_color
        tint = {}
        for ry in range(ny):
            base = (me.cam_y + ry) * w + me.cam_x
            y = y0 + ry * cell
            n = 0
            run_c = -1
            run_x = 0
            run_n = 0
            for rx in range(nx):
                v = cells[base + rx]
                if v:
                    c = tint.get(v, -2)
                    if c == -2:
                        c = tile_color(v - 1)
                        tint[v] = c
                else:
                    c = -1
                if c == run_c:
                    run_n += 1
                    continue
                if run_c >= 0:
                    buf[n] = x0 + run_x * cell
                    buf[n + 1] = y
                    buf[n + 2] = run_n * cell
                    buf[n + 3] = cell
                    buf[n + 4] = run_c
                    n += 5
                run_c = c
                run_x = rx
                run_n = 1
            if run_c >= 0:
                buf[n] = x0 + run_x * cell
                buf[n + 1] = y
                buf[n + 2] = run_n * cell
                buf[n + 3] = cell
                buf[n + 4] = run_c
                n += 5
            if n:
                cv.fill_rects(buf, n // 5)

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
