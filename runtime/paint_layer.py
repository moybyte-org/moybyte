"""The sprite/icon PAINT editor (#4/#30), extracted from Workstation
(runtime/console.py) as its own Layer -- docs/shell_layers_refactor_v1.md Phase 2.

ONE renderer/input serves BOTH sheets (so the theme chunk reuses it, no duplication):
  * menu_view == "paint"  -> edits the cart's SpriteSheet   (ws.sheet)
  * menu_view == "theme"  -> edits the system IconSheet     (ws.icon_sheet, EDIT ICONS)
The active editor is `ws.paint` (a PaintEditor over whichever sheet); `ws._editing_icons`
selects the mode (which sheet, where SAVE persists, where CLOSE returns, GET/PUT hidden).

Boundary (the anti-spaghetti line, per the doc): the SHEETS + the current-editor handle
+ the SAVE persistence stay on Workstation -- `ws.sheet` / `ws.icon_sheet` (single
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
"""
from editors import PaintEditor


# -- paint geometry (single source; console.py imports these back) ------------
# Zoomed pixel grid: a fixed _PG_SPAN square; the per-pixel cell shrinks as the sprite
# size grows (1x1 -> 18px cells; 2x2 -> 9px; 3x3 -> 6px), so a bigger sprite (#30)
# edits in the same on-screen footprint.
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
    """The paint editor content Layer (game domain): a panel over the frozen cart
    frame (or a black field for the icon theme). draw = the shared backdrop then the
    paint UI; handle_pointer routes taps to the grid/palette/buttons; keyboard is
    no-op (paint is pointer-driven). Reads ws.paint / ws.sheet / ws._editing_icons and
    dispatches SAVE/GET/PUT/CLOSE to Workstation."""

    id = "paint"
    domain = "game"

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect
        self._paint_drag = None       # last painted grid cell during a drag (#30)

    def reset_drag(self):
        """Clear the drag-stroke origin (called by ws lifecycle: open_theme /
        _leave_theme) so a new paint session's first stroke starts fresh."""
        self._paint_drag = None

    # -- Layer facets --------------------------------------------------------

    def draw(self, dt):
        # menu_view == "paint": an editor panel over the frozen cart frame. (The theme
        # variant clears to black first via ws._draw_content_theme, then calls _draw_paint.)
        self.ws._draw_menu_backdrop()
        self._draw_paint()

    def handle_input(self, i):
        return True                    # paint is pointer/touch-driven

    def handle_pointer(self, px, py, click):
        ws = self.ws
        # paint lives in the 320x240 viewport, so translate to game coords.
        gx, gy = ws._game_xy(px, py)
        px, py = gx, gy
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
            self._paint_drag = None
        return True

    # -- grid + taps ---------------------------------------------------------

    def _paint_grid_cell(self, px, py):
        """Grid-local pixel (lx, ly) under (px, py), or None when outside the grid.
        The cell size shrinks as the sprite grows so the size*8 region always fills
        the fixed _PG_SPAN footprint (#30)."""
        pe = self.ws.paint
        if pe is None or not self._in(px, py, _PG_AREA):
            return None
        cell = _PG_SPAN // pe.dim
        if cell < 1:
            cell = 1
        lx = (px - _PG_X0) // cell
        ly = (py - _PG_Y0) // cell
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
        last = self._paint_drag
        if last is None:
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
        if pe is None:
            return
        if self._paint_stroke(px, py):         # paint a pixel in the zoomed grid
            return
        if self._in(px, py, _SW_AREA):              # pick a palette color
            idx = ((py - _SW_Y0) // _SW) * _SW_COLS + ((px - _SW_X0) // _SW)
            if 0 <= idx < 16:
                pe.color = idx
        elif self._in(px, py, _SPR_PREV):
            pe.select(-1)
        elif self._in(px, py, _SPR_NEXT):
            pe.select(1)
        elif self._in(px, py, _PAINT_SIZE):         # cycle 1x1 / 2x2 / 3x3 (#30)
            pe.cycle_size()
        elif self._in(px, py, _PAINT_GET) and not ws._editing_icons:
            ws.share_tile_get()              # import the tile from the shared sheet
        elif self._in(px, py, _PAINT_PUT) and not ws._editing_icons:
            ws.share_tile_put()              # save the tile to the shared sheet
        elif self._in(px, py, _PAINT_SAVE):
            # SAVE persists the SYSTEM icon theme (EDIT ICONS) or the cart's sprites.
            ws.save_icons() if ws._editing_icons else ws.save_sprites()
        elif self._in(px, py, _PAINT_CLOSE):
            # CLOSE returns to Settings (theme editor) or runs+leaves to the cart (PAINT).
            ws._leave_theme() if ws._editing_icons else ws._leave_menu()

    # -- draw ----------------------------------------------------------------

    def _draw_paint(self):
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.canvas
        pe = ws.paint
        # Edit the editor's OWN sheet -- the cart sprites for PAINT, the system icon
        # sheet for the theme editor (EDIT ICONS) -- so one renderer serves both.
        sheet = pe.sheet if pe is not None else ws.sheet
        cv.rect(8, 16, 304, 204, NAMES["black"])
        cv.rectb(8, 16, 304, 204, NAMES["orange"])
        title = ("ICONS  TILE " if ws._editing_icons else "PAINT  SPR ") + str(pe.n if pe else 0)
        if sheet is not None and sheet.dirty:
            title = title + " *"
        cv.print(title, 14, 18, NAMES["orange"], 1)
        if pe is None or sheet is None:
            return
        # Zoomed pixel grid: a fixed _PG_SPAN square, cells shrink as the sprite
        # grows so a 1x1/2x2/3x3 sprite (#30) all edit in the same footprint. Pixels
        # come from the sheet's flat buffer at the sprite's tile origin, so the grid
        # spans the constituent tiles for sizes > 1. Grid lines are drawn only when
        # the cell is big enough to read (skip them once cells get tiny).
        dim = pe.dim
        cell = _PG_SPAN // dim
        if cell < 1:
            cell = 1
        ox, oy = sheet.tile_origin(pe.n)
        lines = cell >= 6
        for ly in range(dim):
            for lx in range(dim):
                x = _PG_X0 + lx * cell
                y = _PG_Y0 + ly * cell
                cv.rect(x, y, cell, cell, sheet.pget(ox + lx, oy + ly))
                if lines:
                    cv.rectb(x, y, cell, cell, NAMES["dark_grey"])
        # Outline the whole grid + the tile boundaries (so a 2x2/3x3 sprite shows
        # where its constituent sheet tiles divide).
        cv.rectb(_PG_X0, _PG_Y0, _PG_SPAN, _PG_SPAN, NAMES["orange"])
        if pe.size > 1:
            tpx = _PG_SPAN // pe.size
            for t in range(1, pe.size):
                cv.line(_PG_X0 + t * tpx, _PG_Y0,
                        _PG_X0 + t * tpx, _PG_Y0 + _PG_SPAN - 1, NAMES["light_grey"])
                cv.line(_PG_X0, _PG_Y0 + t * tpx,
                        _PG_X0 + _PG_SPAN - 1, _PG_Y0 + t * tpx, NAMES["light_grey"])
        # 16-color palette (2x8), the selected swatch outlined white.
        for idx in range(16):
            x = _SW_X0 + (idx % _SW_COLS) * _SW
            y = _SW_Y0 + (idx // _SW_COLS) * _SW
            cv.rect(x, y, _SW, _SW, idx)
            cv.rectb(x, y, _SW, _SW,
                     NAMES["white"] if idx == pe.color else NAMES["dark_grey"])
        # Sprite selector + a SIZE cycle button (#30) + a preview of the sprite,
        # scaled so the whole NxN span fits a fixed ~32px box.
        ws._btn("<", _SPR_PREV, NAMES["blue"])
        ws._btn(">", _SPR_NEXT, NAMES["blue"])
        ws._btn("SIZE %dx%d" % (pe.size, pe.size), _PAINT_SIZE, NAMES["dark_purple"])
        ppx, ppy = 240, 92
        ps = max(1, 32 // dim)
        for ly in range(dim):
            for lx in range(dim):
                cv.rect(ppx + lx * ps, ppy + ly * ps, ps, ps,
                        sheet.pget(ox + lx, oy + ly))
        cv.rectb(ppx, ppy, dim * ps, dim * ps, NAMES["dark_grey"])
        # Cross-cart sprite reuse (#18): GET pulls this tile out of the shared sheet,
        # PUT pushes it in. Hidden in the theme editor -- the shared sheet is 8x8 cart
        # sprites, not the 16x16 icon theme, so GET/PUT don't apply there.
        if not ws._editing_icons:
            ws._icon_btn("get", "GET", _PAINT_GET, NAMES["indigo"])
            ws._icon_btn("put", "PUT", _PAINT_PUT, NAMES["dark_green"])
        if ws.paint_status:
            cv.print(ws.paint_status[:18], 110, 196, NAMES["yellow"], 1)
        ws._btn("SAVE", _PAINT_SAVE, NAMES["green"])
        ws._btn("CLOSE", _PAINT_CLOSE, NAMES["red"])


class ThemeLayer:
    """The icon-theme editor (EDIT ICONS, Settings -> #52): the SAME paint flow as the
    cart sprite editor, pointed at the SYSTEM icon sheet. It owns the theme LIFECYCLE
    (open/leave + the ws._editing_icons mode flag) and DELEGATES all the editing -- draw
    + taps -- to the shared PaintLayer (one _paint_drag, one renderer). Game domain.

    The lifecycle stays reachable on Workstation as thin forwarders (ws.open_theme is
    device/test-pinned; ws._leave_theme is called by PaintLayer's CLOSE tap); the mode
    flag ws._editing_icons + the sheet/save methods (load_icon_sheet/set_icon_sheet/
    save_icons) stay on ws (the device backend calls them) -- ThemeLayer dispatches."""

    id = "theme"
    domain = "game"

    def __init__(self, ws, paint_layer, names):
        self.ws = ws
        self._paint = paint_layer
        self._NAMES = names

    def draw(self, dt):
        # EDIT ICONS (Stage 2): opened from Settings, NOT a running cart, so there's no
        # cart backdrop to draw -- clear to black and reuse the shared PAINT renderer
        # (over ws.icon_sheet, selected by ws._editing_icons).
        ws = self.ws
        ws.canvas.cls(self._NAMES["black"])
        ws._reset_canvas_state()
        self._paint._draw_paint()

    def handle_input(self, i):
        # EDIT ICONS is pointer/touch-driven like PAINT; B closes back to Settings.
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
        ws.screen = "menu"
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
        ws.screen = "settings"
