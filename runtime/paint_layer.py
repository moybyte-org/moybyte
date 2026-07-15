"""The sprite/icon PAINT editor (#4/#30), extracted from Workstation
(runtime/console.py) as its own Layer -- docs/shell_layers_refactor_v1.md Phase 2.

ONE renderer/input serves BOTH sheets (so the theme chunk reuses it, no duplication):
  * menu_view == "paint"  -> edits the cart's SpriteSheet   (ws.project.sheet)
  * menu_view == "theme"  -> edits the system IconSheet     (ws.icon_sheet, EDIT ICONS)
The active editor is `ws.paint` (a PaintEditor over whichever sheet); `ws._editing_icons`
selects the mode (which sheet, where SAVE persists, where CLOSE returns, GET/PUT hidden).

Boundary (the anti-spaghetti line, per the doc): the SHEETS + the current-editor handle
+ the SAVE persistence stay on Workstation -- `ws.project.sheet` / `ws.icon_sheet` (single
source of the pixels), `ws.paint` (the PaintEditor handle, device/test-pinned like
ws.editor), `ws._editing_icons` / `ws.paint_status` (lifecycle mode/status), and
`ws.save_sprites` / `ws.save_icons` / `ws.share_tile_get` / `ws.share_tile_put` (cart/
system state the device + tests pin). PaintLayer READS those and DISPATCHES to them; it
owns only the paint-UI: the DRAW, the grid/palette/button hit-testing, and the drag-
stroke continuity state (_paint_drag). The paint-only constants live here (single source;
console.py imports them back so tests + tools resolve console._PG_X0 / _PAINT_SAVE / ...).
`NAMES` (palette) and `_in` (rect hit-test) are injected (the circular-import dodge);
the shared draw toolkit (ws._btn/_icon_btn) stays on Workstation.

The icon-theme editor (EDIT ICONS) is the same paint flow over the system icon sheet,
so `ThemeLayer` (below) lives here too: it owns the theme lifecycle + mode flag and
delegates all the editing to the shared PaintLayer.

Stage 4 (#46 zoned bar): PaintLayer.draw() calls ws.bar_layer._draw_status_strip("menu")
LAST (chrome over content) -- ThemeLayer has its OWN draw(), so this only ever fires for
the cart-sprite "paint" tab, never EDIT ICONS. handle_pointer IS shared with ThemeLayer,
so its bar-tap check is guarded on menu_view == "paint" (a theme-editor tap must reach
the grid, not the Editor's tab ladder).

Responsive (#39 step 3): the paint editor is SYSTEM-domain now -- it draws on the
reflowed system canvas at the panel's native size via `PaintLayout` (the CodeLayout
pattern: every field equals the frozen module constant at (320, 240, 1), byte for
byte, and the responsive formulas only run on a larger canvas / bigger font). On a
big panel the zoomed pixel grid GROWS to fill the space (a multiple of 48px so the
1x1/2x2/3x3 sprite sizes all divide it into whole pixels) and the chrome scales with
the font. Hit-testing is in SYSTEM coords (no _game_xy translation).
"""
from editors import PaintEditor


# -- paint geometry (single source; console.py imports these back) ------------
# The frozen 320x240 baseline PaintLayout reproduces VERBATIM (#39 graceful
# degradation). Zoomed pixel grid: a fixed _PG_SPAN square; the per-pixel cell
# shrinks as the sprite size grows (1x1 -> 18px cells; 2x2 -> 9px; 3x3 -> 6px), so
# a bigger sprite (#30) edits in the same on-screen footprint.
_PG_X0 = 14
_PG_Y0 = 32
_PG_CELL = 18                      # cell size at size 1 (8x8); _PG_SPAN derives from it
_PG_SPAN = 8 * _PG_CELL            # fixed grid footprint in px (144), all sizes
_PG_AREA = (_PG_X0, _PG_Y0, _PG_SPAN, _PG_SPAN)
_SW_X0 = 170
_SW_Y0 = 32
_SW = 18
_SW_COLS = 2
_SW_AREA = (_SW_X0, _SW_Y0, _SW_COLS * _SW, (16 // _SW_COLS) * _SW)
_SPR_PREV = (214, 40, 40, 24)
_SPR_NEXT = (262, 40, 40, 24)
_PAINT_SIZE = (214, 68, 88, 20)    # cycle sprite size 1x1 / 2x2 / 3x3 (#30)
_PAINT_SAVE = (14, 190, 88, 26)
_PAINT_CLOSE = (200, 190, 102, 26)
# Cross-cart sprite reuse (#18): GET imports the current tile FROM the shared sheet,
# PUT saves it TO the shared sheet. Hidden in the theme (icon) editor.
_PAINT_GET = (210, 130, 92, 20)
_PAINT_PUT = (210, 154, 92, 20)

_BASE_W = 320
_BASE_H = 240

# -- tool palette (#90) -------------------------------------------------------
# A compact row of single-glyph tool buttons drawn just below the pixel grid: the
# in-editor undo/redo, the bucket-fill toggle, and the whole-sprite transforms
# (flip / rotate 90 / shift-with-wrap in 4 directions / clear). Single-char labels
# so they fit the petme128 ASCII font AND the tiny baseline row (no chrome-glyph
# vocabulary change): Z/Y echo the host Ctrl+Z/Y undo shortcut, < > ^ v are the
# shift arrows, H/V mirror, R rotates, F is the fill bucket, X clears. Order is the
# hit-test/draw order.
_TOOLS = ("undo", "redo", "fill", "fliph", "flipv", "rot",
          "sleft", "sright", "sup", "sdown", "clear")
_TOOL_LABEL = {
    "undo": "Z", "redo": "Y", "fill": "F", "fliph": "H", "flipv": "V",
    "rot": "R", "sleft": "<", "sright": ">", "sup": "^", "sdown": "v",
    "clear": "X",
}
# Baseline (320x240) tool row: a full-width strip in the gap between the grid
# bottom (y176) and the SAVE/CLOSE row (y190). 11 buttons of 26px across x14..300.
_TOOL_X0 = 14
_TOOL_Y0 = 176
_TOOL_CW = 26
_TOOL_H = 13


def _tool_row(x0, y0, cw, h, n):
    """A row of n button rects (x, y, w, h), cw apart with a 1px gutter."""
    return [(x0 + i * cw, y0, cw - 1, h) for i in range(n)]


class PaintLayout:
    """Responsive paint-editor geometry (#39 step 3): the panel, the zoomed pixel
    grid, the 16-color swatch column, the sprite-selector / SIZE / GET / PUT buttons
    and the SAVE/CLOSE row, derived from the SYSTEM canvas size (w, h) + font scale.

    The single hard contract (mirrors Layout/CodeLayout): at (w, h, fs) ==
    (320, 240, 1) every field equals the frozen `_PG_*`/`_SW_*`/`_PAINT_*` module
    constant, byte for byte -- reproduced VERBATIM in the `_base` branch so no
    reflow formula's integer-floor can drift a pixel on the T-Deck. The responsive
    formulas only run on a larger canvas / bigger font.

    The grid span is the star of the reflow: it grows to the largest multiple of
    48px that fits (48 = lcm of the 8/16/24 sprite dims, so every zoom level keeps
    whole on-screen pixels), which is what "a larger editing app" means for paint --
    a hugely bigger drawing surface, not just scaled chrome."""

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
            self.pg_x0, self.pg_y0, self.pg_span = _PG_X0, _PG_Y0, _PG_SPAN
            self.pg_area = _PG_AREA
            self.sw_x0, self.sw_y0, self.sw, self.sw_cols = _SW_X0, _SW_Y0, _SW, _SW_COLS
            self.sw_area = _SW_AREA
            self.spr_prev = _SPR_PREV
            self.spr_next = _SPR_NEXT
            self.size_btn = _PAINT_SIZE
            self.prev_xy = (240, 92)
            self.prev_box = 32
            self.get_btn = _PAINT_GET
            self.put_btn = _PAINT_PUT
            self.save_btn = _PAINT_SAVE
            self.close_btn = _PAINT_CLOSE
            self.status_xy = (110, 196)
            self.status_maxc = 18
            # The #90 tool row: undo/redo/fill/transforms, in the grid->SAVE gap.
            self.tool_btns = _tool_row(_TOOL_X0, _TOOL_Y0, _TOOL_CW, _TOOL_H,
                                       len(_TOOLS))
            return
        # -- responsive: anchor the panel to the canvas, the swatch/button column to
        # the panel's right edge, and grow the grid to fill what's left ------------
        bar_h = 18 * fs
        px, py = 8 * fs, bar_h - 2 * fs
        pw, ph = self.w - 16 * fs, self.h - (bar_h - 2 * fs) - 20 * fs
        self.body_fill = (0, bar_h, self.w, self.h - bar_h)
        self.panel = (px, py, pw, ph)
        p_right = px + pw
        p_bottom = py + ph
        self.title_xy = (px + 6 * fs, py + 2 * fs)
        rc_x = p_right - 142 * fs                 # right column origin (base 170)
        row_y = p_bottom - 30 * fs                # SAVE/CLOSE row (base 190)
        toolh = 13 * fs                           # #90 tool row height
        self.pg_x0 = px + 6 * fs
        self.pg_y0 = py + 16 * fs
        # Reserve the tool-row band (its height + gaps) above the SAVE row so the
        # grid never grows over it -- otherwise the same as the shipped formula.
        avail = min(rc_x - self.pg_x0 - 8 * fs,
                    row_y - self.pg_y0 - toolh - 8 * fs)
        self.pg_span = max(48, 48 * (avail // 48))
        self.pg_area = (self.pg_x0, self.pg_y0, self.pg_span, self.pg_span)
        self.sw_x0, self.sw_y0 = rc_x, self.pg_y0
        self.sw = _SW * fs
        self.sw_cols = _SW_COLS
        self.sw_area = (self.sw_x0, self.sw_y0, self.sw_cols * self.sw,
                        (16 // self.sw_cols) * self.sw)
        self.spr_prev = (rc_x + 44 * fs, self.pg_y0 + 8 * fs, 40 * fs, 24 * fs)
        self.spr_next = (rc_x + 92 * fs, self.pg_y0 + 8 * fs, 40 * fs, 24 * fs)
        self.size_btn = (rc_x + 44 * fs, self.pg_y0 + 36 * fs, 88 * fs, 20 * fs)
        self.prev_xy = (rc_x + 70 * fs, self.pg_y0 + 60 * fs)
        self.prev_box = 32 * fs
        self.get_btn = (rc_x + 40 * fs, self.pg_y0 + 98 * fs, 92 * fs, 20 * fs)
        self.put_btn = (rc_x + 40 * fs, self.pg_y0 + 122 * fs, 92 * fs, 20 * fs)
        self.save_btn = (self.pg_x0, row_y, 88 * fs, 26 * fs)
        self.close_btn = (p_right - 112 * fs, row_y, 102 * fs, 26 * fs)
        self.status_xy = (self.pg_x0 + 96 * fs, row_y + 6 * fs)
        self.status_maxc = max(4, (self.close_btn[0] - self.status_xy[0]) // (8 * fs))
        # The #90 tool row: a full-width strip just above the SAVE/CLOSE row, growing
        # its button cells with the panel width. The reserved `avail` band above keeps
        # the (grown) grid clear of it.
        tool_y0 = row_y - toolh - 3 * fs
        tool_span = (p_right - 6 * fs) - self.pg_x0
        cw = max(10 * fs, tool_span // len(_TOOLS))
        self.tool_btns = _tool_row(self.pg_x0, tool_y0, cw, toolh, len(_TOOLS))


def _line_cells(x0, y0, x1, y1):
    """Integer cells on the line from (x0, y0) to (x1, y1), inclusive (Bresenham).
    Drag-to-draw fills these so a fast pointer move paints a continuous stroke
    instead of dotting only the frames it was sampled on (#30). Integer-only so it
    runs the same under CPython (host) and MicroPython (device)."""
    cells = []
    dx = x1 - x0 if x1 >= x0 else x0 - x1
    dy = y1 - y0 if y1 >= y0 else y0 - y1
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    x, y = x0, y0
    while True:
        cells.append((x, y))
        if x == x1 and y == y1:
            break
        e2 = err + err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy
    return cells


class PaintLayer:
    """The paint editor content Layer (SYSTEM domain, responsive #39 step 3): a
    full-screen panel on the reflowed system canvas (the frozen-cart backdrop is
    gone -- the panel always covered every pixel of it anyway). draw = the paint UI
    at the PaintLayout geometry; handle_pointer routes taps to the grid/palette/
    buttons in SYSTEM coords; keyboard is no-op (paint is pointer-driven). Reads
    ws.paint / ws.project.sheet / ws._editing_icons and dispatches SAVE/GET/PUT/
    CLOSE to Workstation."""

    id = "paint"
    domain = "system"

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect
        self._paint_drag = None       # last painted grid cell during a drag (#30)
        self._ekey_prev = 0           # last consumed keyboard byte (undo-shortcut edge, #90)
        sc = ws.sys_canvas
        self.layout = PaintLayout(sc.w, sc.h, getattr(sc, "font_scale", 1))

    def relayout(self, w, h, fs):
        """Rebuild the responsive geometry (#39) -- called by ws._relayout on a
        font-scale change (and, later, a window resize)."""
        self.layout = PaintLayout(w, h, fs)

    def reset_drag(self):
        """Clear the drag-stroke origin (called by ws lifecycle: open_theme /
        _leave_theme) so a new paint session's first stroke starts fresh."""
        self._paint_drag = None

    # -- Layer facets --------------------------------------------------------

    def draw(self, dt):
        # menu_view == "paint": a full-screen editor panel on the SYSTEM canvas (#39
        # step 3). The frozen-cart backdrop (_draw_menu_backdrop) is gone: the body
        # fill + the bar cover every pixel it ever painted, so the pixels are
        # identical and the cart's _draw() no longer runs on editor frames. The game
        # canvas still gets its state reset (degradation shares one canvas). (The
        # theme variant clears to a black field first in ThemeLayer.draw(), then
        # calls _draw_paint.)
        self.ws._reset_canvas_state()
        self._draw_paint()
        # The Editor's lent top-bar zone (Stage 4, #46 zoned bar): the tab ladder +
        # PLAY. ThemeLayer has its OWN draw() (doesn't call this one), so this is
        # unconditionally the cart-sprite "paint" tab, never the icon theme editor.
        self.ws.bar_layer._draw_status_strip("menu")

    def handle_input(self, i):
        self.handle_key(i)             # host Ctrl+Z / Ctrl+Y undo shortcut (#90)
        return True                    # otherwise paint is pointer/touch-driven

    def handle_key(self, i):
        """Host-convenience undo/redo shortcut: Ctrl+Z (0x1A) / Ctrl+Y (0x19). These
        control bytes never reach the paint logic otherwise (paint is pointer-driven),
        and on-screen Z/Y buttons give the SAME verbs everywhere touch reaches -- the
        keyboard path is a convenience only (spec / #90). Edge-tracked so a held key
        fires once."""
        pe = self.ws.paint
        k = getattr(i, "last_key", 0)
        if pe is not None and k and k != self._ekey_prev:
            if k == 0x1A:
                pe.undo()
            elif k == 0x19:
                pe.redo()
        self._ekey_prev = k

    def handle_pointer(self, px, py, click):
        ws = self.ws
        # SYSTEM coords (#39 step 3): the editor draws on the system canvas at
        # native size, so it hit-tests the raw pointer -- no _game_xy translation.
        # The Editor's lent zone (Stage 4): ONLY for the cart-sprite "paint" tab --
        # this handler is reused by ThemeLayer (EDIT ICONS) over the SAME grid/
        # buttons, which has no bar at all, so guard on menu_view rather than
        # unconditionally claiming the tap (a stray theme-editor tap must reach the
        # paint grid, not the Editor's tab ladder).
        if click and ws.menu_view == "paint" and ws.bar_layer.handle_bar_tap("menu", px, py):
            return True
        # A tap (click) routes through _paint_click (grid OR buttons). A held drag with
        # no fresh click keeps painting the grid stroke so press-and-move draws a
        # continuous line -- the same path for a host mouse drag and a device touch drag
        # (both = pointer.down + moving position). Releasing resets the stroke origin
        # (#30). The theme editor (EDIT ICONS) reuses this exact path over the icon sheet.
        if click:
            self._paint_click(px, py)
        elif ws.pointer.down:
            self._paint_stroke(px, py)
        else:
            # Pointer released (anywhere): close the brush stroke so the whole
            # press-drag-release commits ONE undo step (#90). Idempotent when idle.
            if ws.paint is not None:
                ws.paint.end_stroke()
            self._paint_drag = None
        return True

    # -- grid + taps ---------------------------------------------------------

    def _paint_grid_cell(self, px, py):
        """Grid-local pixel (lx, ly) under (px, py), or None when outside the grid.
        The cell size shrinks as the sprite grows so the size*8 region always fills
        the layout's grid footprint (#30; responsive span #39)."""
        pe = self.ws.paint
        lay = self.layout
        if pe is None or not self._in(px, py, lay.pg_area):
            return None
        cell = lay.pg_span // pe.dim
        if cell < 1:
            cell = 1
        lx = (px - lay.pg_x0) // cell
        ly = (py - lay.pg_y0) // cell
        if 0 <= lx < pe.dim and 0 <= ly < pe.dim:
            return (lx, ly)
        return None

    def _paint_stroke(self, px, py):
        """Drag-to-draw (#30): paint the grid cell under (px, py) AND fill the line
        from the last painted cell, so a fast drag leaves no gaps. Works the same
        for a host mouse drag and a device touch drag -- both arrive as pointer.down
        with the position updated each frame. Returns True if a cell was painted."""
        pe = self.ws.paint
        cell = self._paint_grid_cell(px, py)
        if cell is None:
            self._paint_drag = None        # left the grid -> next entry starts fresh
            return False
        # Bucket tool (#90): a tap floods the contiguous region ONCE per press (no
        # drag chaining). `fill` snapshots/records its own undo step, so the brush
        # stroke machinery below is bypassed.
        if pe.tool == pe.FILL:
            if self._paint_drag is None:
                pe.fill(cell[0], cell[1])
            self._paint_drag = cell
            return True
        last = self._paint_drag
        if last is None:
            pe.begin_stroke()              # snapshot before the first pixel (#90)
            pe.paint(cell[0], cell[1])
        else:
            for cx, cy in _line_cells(last[0], last[1], cell[0], cell[1]):
                pe.paint(cx, cy)
        self._paint_drag = cell
        return True

    def _paint_click(self, px, py):
        # A tap (press edge). Paint the grid cell, or hit a button/palette swatch.
        ws = self.ws
        pe = ws.paint
        lay = self.layout
        if pe is None:
            return
        if self._paint_stroke(px, py):         # paint a pixel in the zoomed grid
            return
        tid = self._tool_at(px, py)            # a #90 tool button (undo/fill/transform)?
        if tid is not None:
            self._do_tool(tid)
            return
        if self._in(px, py, lay.sw_area):           # pick a palette color
            idx = ((py - lay.sw_y0) // lay.sw) * lay.sw_cols + ((px - lay.sw_x0) // lay.sw)
            if 0 <= idx < 16:
                pe.color = idx
        elif self._in(px, py, lay.spr_prev):
            pe.select(-1)
        elif self._in(px, py, lay.spr_next):
            pe.select(1)
        elif self._in(px, py, lay.size_btn):        # cycle 1x1 / 2x2 / 3x3 (#30)
            pe.cycle_size()
        elif self._in(px, py, lay.get_btn) and not ws._editing_icons:
            ws.share_tile_get()              # import the tile from the shared sheet
        elif self._in(px, py, lay.put_btn) and not ws._editing_icons:
            ws.share_tile_put()              # save the tile to the shared sheet
        elif self._in(px, py, lay.save_btn):
            # SAVE persists the SYSTEM icon theme (EDIT ICONS) or the cart's sprites.
            ws.save_icons() if ws._editing_icons else ws.save_sprites()
        elif self._in(px, py, lay.close_btn):
            # CLOSE returns to Settings (theme editor) or runs+leaves to the cart (PAINT).
            ws._leave_theme() if ws._editing_icons else ws._leave_menu()

    # -- tool row (#90) ------------------------------------------------------

    def _tool_at(self, px, py):
        """The tool id under (px, py), or None -- hit-tested over the tool row."""
        btns = getattr(self.layout, "tool_btns", None)
        if btns is None:
            return None
        for i in range(len(btns)):
            if self._in(px, py, btns[i]):
                return _TOOLS[i]
        return None

    def _do_tool(self, tid):
        """Dispatch a tool-row tap to the PaintEditor verb (#90). Every action is
        touch-reachable here; the keyboard shortcut only doubles undo/redo."""
        pe = self.ws.paint
        if pe is None:
            return
        if tid == "undo":
            pe.undo()
        elif tid == "redo":
            pe.redo()
        elif tid == "fill":
            pe.toggle_fill()
        elif tid == "fliph":
            pe.flip_h()
        elif tid == "flipv":
            pe.flip_v()
        elif tid == "rot":
            pe.rotate()
        elif tid == "sleft":
            pe.shift(-1, 0)
        elif tid == "sright":
            pe.shift(1, 0)
        elif tid == "sup":
            pe.shift(0, -1)
        elif tid == "sdown":
            pe.shift(0, 1)
        elif tid == "clear":
            pe.clear()

    def _draw_tools(self):
        """Draw the compact tool row: undo/redo, the FILL toggle, and the whole-sprite
        transforms. Single-char labels centered at the canvas font scale (#39). The
        active FILL tool is accented; undo/redo dim when their ring is empty. Drawn on
        the panel surface directly with the indexed primitives (no chrome-glyph
        vocabulary change), so it renders identically on host and device."""
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        pe = ws.paint
        btns = getattr(self.layout, "tool_btns", None)
        if pe is None or btns is None:
            return
        fs = getattr(cv, "font_scale", 1)
        if fs < 1:
            fs = 1
        for i in range(len(btns)):
            tid = _TOOLS[i]
            x, y, w, h = btns[i]
            active = tid == "fill" and pe.tool == pe.FILL
            enabled = True
            if tid == "undo":
                enabled = pe.can_undo()
            elif tid == "redo":
                enabled = pe.can_redo()
            fill = NAMES["indigo"] if active else (
                NAMES["dark_grey"] if enabled else NAMES["black"])
            ink = NAMES["white"] if enabled else NAMES["dark_grey"]
            cv.rect(x, y, w, h, fill)
            cv.rectb(x, y, w, h, NAMES["light_grey"])
            lbl = _TOOL_LABEL[tid]
            cv.print(lbl, x + (w - 8 * fs) // 2, y + (h - 8 * fs) // 2, ink, fs)

    # -- draw ----------------------------------------------------------------

    def _draw_paint(self):
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        lay = self.layout
        pe = ws.paint
        # Edit the editor's OWN sheet -- the cart sprites for PAINT, the system icon
        # sheet for the theme editor (EDIT ICONS) -- so one renderer serves both.
        sheet = pe.sheet if pe is not None else ws.project.sheet
        # Cover the FULL content area below the bar first (Fix 3): the panel below
        # doesn't span edge to edge, so without this fill stale pixels would bleed
        # through the side/bottom strips. Match the cards tab (which fills the whole
        # area) so the editor is fully opaque. (Harmless on the EDIT-ICONS path,
        # which already cls()'d to black.)
        # Phase 3 (visual identity v1): the BODY joins the warm tool surface on
        # the shelf tiers; the PANEL stays the dark work canvas (the sprite art's
        # own field) on every tier. Baseline literals byte-identical.
        th = ws.theme_colors
        light = (not lay._base) and ws.light_chrome()
        cv.rect(*(lay.body_fill + ((th["surface"] if light else NAMES["black"]),)))
        # The panel joins the surface on the light tiers -- the pixel grid and
        # swatch/preview cells all back themselves, so only the frozen dark
        # baseline needs the black plate.
        cv.rect(*(lay.panel + ((th["surface"] if light else NAMES["black"]),)))
        cv.rectb(*(lay.panel + ((th["author"] if light else NAMES["orange"]),)))
        title = ("ICONS  TILE " if ws._editing_icons else "PAINT  SPR ") + str(pe.n if pe else 0)
        if sheet is not None and sheet.dirty:
            title = title + " *"
        cv.print(title, lay.title_xy[0], lay.title_xy[1],
                 th["ink"] if light else NAMES["orange"], 1)
        if pe is None or sheet is None:
            return
        # Zoomed pixel grid: a fixed lay.pg_span square, cells shrink as the sprite
        # grows so a 1x1/2x2/3x3 sprite (#30) all edit in the same footprint. Pixels
        # come from the sheet's flat buffer at the sprite's tile origin, so the grid
        # spans the constituent tiles for sizes > 1. Grid lines are drawn only when
        # the cell is big enough to read (skip them once cells get tiny).
        dim = pe.dim
        span = lay.pg_span
        gx0, gy0 = lay.pg_x0, lay.pg_y0
        cell = span // dim
        if cell < 1:
            cell = 1
        ox, oy = sheet.tile_origin(pe.n)
        lines = cell >= 6
        for ly in range(dim):
            for lx in range(dim):
                x = gx0 + lx * cell
                y = gy0 + ly * cell
                cv.rect(x, y, cell, cell, sheet.pget(ox + lx, oy + ly))
                if lines:
                    cv.rectb(x, y, cell, cell, NAMES["dark_grey"])
        # Outline the whole grid + the tile boundaries (so a 2x2/3x3 sprite shows
        # where its constituent sheet tiles divide).
        cv.rectb(gx0, gy0, span, span, NAMES["orange"])
        if pe.size > 1:
            tpx = span // pe.size
            for t in range(1, pe.size):
                cv.line(gx0 + t * tpx, gy0,
                        gx0 + t * tpx, gy0 + span - 1, NAMES["light_grey"])
                cv.line(gx0, gy0 + t * tpx,
                        gx0 + span - 1, gy0 + t * tpx, NAMES["light_grey"])
        # 16-color palette (2x8), the selected swatch outlined white.
        for idx in range(16):
            x = lay.sw_x0 + (idx % lay.sw_cols) * lay.sw
            y = lay.sw_y0 + (idx // lay.sw_cols) * lay.sw
            cv.rect(x, y, lay.sw, lay.sw, idx)
            cv.rectb(x, y, lay.sw, lay.sw,
                     (th["ink"] if light else NAMES["white"])
                     if idx == pe.color else NAMES["dark_grey"])
        # Sprite selector + a SIZE cycle button (#30) + a preview of the sprite,
        # scaled so the whole NxN span fits the layout's preview box.
        ws._btn("<", lay.spr_prev, NAMES["blue"], cv)
        ws._btn(">", lay.spr_next, NAMES["blue"], cv)
        ws._btn("SIZE %dx%d" % (pe.size, pe.size), lay.size_btn, NAMES["dark_purple"], cv)
        ppx, ppy = lay.prev_xy
        ps = max(1, lay.prev_box // dim)
        for ly in range(dim):
            for lx in range(dim):
                cv.rect(ppx + lx * ps, ppy + ly * ps, ps, ps,
                        sheet.pget(ox + lx, oy + ly))
        cv.rectb(ppx, ppy, dim * ps, dim * ps, NAMES["dark_grey"])
        # Cross-cart sprite reuse (#18): GET pulls this tile out of the shared sheet,
        # PUT pushes it in. Hidden in the theme editor -- the shared sheet is 8x8 cart
        # sprites, not the 16x16 icon theme, so GET/PUT don't apply there.
        if not ws._editing_icons:
            ws._icon_btn("get", "GET", lay.get_btn, NAMES["indigo"], cv)
            ws._icon_btn("put", "PUT", lay.put_btn, NAMES["dark_green"], cv)
        if ws.paint_status:
            cv.print(ws.paint_status[:lay.status_maxc],
                     lay.status_xy[0], lay.status_xy[1],
                     th["author"] if light else NAMES["yellow"], 1)
        ws._btn("SAVE", lay.save_btn, NAMES["green"], cv)
        ws._btn("CLOSE", lay.close_btn, NAMES["red"], cv)
        self._draw_tools()               # #90: undo/redo/fill/transform row


class ThemeLayer:
    """The icon-theme editor (EDIT ICONS, Settings -> #52): the SAME paint flow as the
    cart sprite editor, pointed at the SYSTEM icon sheet. It owns the theme LIFECYCLE
    (open/leave + the ws._editing_icons mode flag) and DELEGATES all the editing -- draw
    + taps -- to the shared PaintLayer (one _paint_drag, one renderer). System domain,
    like the paint tab it reuses (#39 step 3).

    The lifecycle stays reachable on Workstation as thin forwarders (ws.open_theme is
    device/test-pinned; ws._leave_theme is called by PaintLayer's CLOSE tap); the mode
    flag ws._editing_icons + the sheet/save methods (load_icon_sheet/set_icon_sheet/
    save_icons) stay on ws (the device backend calls them) -- ThemeLayer dispatches."""

    id = "theme"
    domain = "system"

    def __init__(self, ws, paint_layer, names):
        self.ws = ws
        self._paint = paint_layer
        self._NAMES = names

    def draw(self, dt):
        # EDIT ICONS (Stage 2): opened from Settings, NOT a running cart, so there's no
        # cart backdrop to draw -- clear to black and reuse the shared PAINT renderer
        # (over ws.icon_sheet, selected by ws._editing_icons), on the system canvas.
        ws = self.ws
        ws.sys_canvas.cls(self._NAMES["black"])
        ws._reset_canvas_state()
        self._paint._draw_paint()

    def handle_input(self, i):
        # EDIT ICONS is pointer/touch-driven like PAINT; B closes back to Settings.
        # Share the paint editor's host Ctrl+Z/Y undo shortcut here too (#90).
        self._paint.handle_key(i)
        self.ws._leave_or_home(self.leave)
        return True

    def handle_pointer(self, px, py, click):
        return self._paint.handle_pointer(px, py, click)

    def open(self):
        """Open the PAINT editor on the SYSTEM icon sheet (Settings -> EDIT ICONS,
        Stage 2 / #52). The same renderer/input as the cart PAINT flow, but pointed
        at ws.icon_sheet: SAVE persists system_icons.moygfx (not a cart) and CLOSE
        returns to Settings. Starts from the current theme (the baked default if no
        system_icons.moygfx exists yet); the first SAVE creates the file."""
        ws = self.ws
        ws._dirty = True                 # screen change repaints (#44)
        ws._editing_icons = True
        ws.paint_status = None
        ws.save_status = None
        ws.wm.goto("menu")               # Stage 6e: theme editor spawns on the back-stack
        ws.menu_view = "theme"
        # Build a PaintEditor over the icon sheet (PaintEditor is tile-size-agnostic,
        # so the 16x16 IconSheet edits natively). A fresh editor each open so the
        # brush/tile state doesn't leak in from a cart paint session.
        if ws.icon_sheet is not None:
            ws.paint = PaintEditor(ws.icon_sheet)
        self._paint.reset_drag()
        ws._set_text_mode(False)         # paint is pointer-driven, raw/game keyboard
        ws.ach.note("editor", "paint")   # repainting the chrome counts toward Toolbox

    def leave(self):
        """CLOSE/back from the theme editor: return to Settings (not a cart/desktop --
        the theme editor was opened from there). Drops the editor + clears the
        editing-icons flag so the cart PAINT flow is untouched next time."""
        ws = self.ws
        ws._dirty = True                 # screen change repaints (#44)
        ws._editing_icons = False
        ws.paint = None
        self._paint.reset_drag()
        ws.wm.goto("settings")           # Stage 6e: pop the theme editor, back to Settings
