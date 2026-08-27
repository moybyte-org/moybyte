"""The sprite/icon PAINT editor (#4/#30), extracted from Workstation
(runtime/console.py) as its own Layer -- docs/history/shell_layers_refactor_v1.md Phase 2.

ONE renderer/input serves BOTH sheets (so the theme chunk reuses it, no duplication):
  * menu_view == "paint"  -> edits the cart's SpriteSheet   (ws.project.sheet)
  * menu_view == "theme"  -> edits the system IconSheet     (ws.look.icon_sheet, EDIT ICONS)
The active editor is `ws.paint` (a PaintEditor over whichever sheet); `ws._editing_icons`
selects the mode (which sheet, where CLOSE returns/commits, GET/PUT hidden). SAVE is GONE
(#111, owner decision 2026-07-21): there is no SAVE button anywhere in the editor -- CLOSE
hard-commits the open sheet on its way out (paint's cart-sprite CLOSE runs ws._leave_menu,
which is EditorApp.leave -- PLAY, a hard-commit trigger now; the theme's CLOSE is
ThemeLayer.leave, which commits directly since it has no PLAY of its own), and the idle-
typing autosave-commit + every other tab-leaving exit path (a tab switch, PROJECTS, a
window/context-X, a workspace swap, going home) commit it too.

Boundary (the anti-spaghetti line, per the doc): the SHEETS + the current-editor handle
+ the persistence verbs stay on Workstation -- `ws.project.sheet` / `ws.look.icon_sheet` (single
source of the pixels), `ws.paint` (the PaintEditor handle, device/test-pinned like
ws.editor), `ws._editing_icons` / `ws.paint_status` (lifecycle mode/status), and
`ws.save_sprites` / `ws.look.save_icons` / `ws.share_tile_get` / `ws.share_tile_put` (cart/
system state the device + tests pin). PaintLayer READS those and DISPATCHES to them; it
owns only the paint-UI: the DRAW, the grid/palette/button hit-testing, and the drag-
stroke continuity state (_paint_drag). The paint-only constants live here (single source;
console.py imports them back so tests + tools resolve console._PG_X0 / _PAINT_CLOSE / ...).
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

try:
    import ui as _ui              # frozen on device
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui
from editors import PaintEditor, KeyEdge, _pe_line

# The shared pre-literate glyph vocabulary (#89-#93 icon pass): the tool row draws a
# 12x12 chrome glyph per button instead of a one-char label. Imported for the
# membership check that keeps the letter as a fallback if a kind is ever missing.
try:
    from chrome import _GLYPHS
except ImportError:  # pragma: no cover - direct host import (chrome not yet aliased)
    from runtime.chrome import _GLYPHS

try:
    from layout_base import LayoutBase, BASE_W as _BASE_W, BASE_H as _BASE_H
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import (LayoutBase, BASE_W as _BASE_W,
                                     BASE_H as _BASE_H)


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
# CLOSE dropped below the TWO tool rows (#90 added a second row): a shorter button
# height buys the extra row on the 320x240 T-Deck without shrinking the pixel grid.
# Byte-identical-verbatim in PaintLayout._base. (SAVE lived at x14 here; #111 removed
# it -- the freed strip stays empty rather than widening CLOSE, so the status text at
# status_xy doesn't need to move too.)
_PAINT_CLOSE = (200, 202, 102, 16)
# Cross-cart sprite reuse (#18): GET imports the current tile FROM the shared sheet,
# PUT saves it TO the shared sheet. Hidden in the theme (icon) editor.
_PAINT_GET = (210, 130, 92, 20)
_PAINT_PUT = (210, 154, 92, 20)
# "Send to Files" (#108): export the whole sprite sheet to files/sprites/ as a
# named user file. Stacked below GET/PUT in the right column (#111: moved off
# y98 -- that slot sat flush against the sprite preview thumbnail at (240,92)-
# (272,124), a snug fit that read as an overlap; y178 clears it with room to
# spare, matching the responsive branch's already-correct sequencing below).
# Hidden in the theme editor, like GET/PUT.
_PAINT_FILES = (210, 178, 92, 20)

# -- tool palette (#90) -------------------------------------------------------
# TWO compact rows of single-glyph tool buttons drawn just below the pixel grid.
# Row 1 = the drawing MODES (the pen / bucket / rect / line / oval / select tools).
# Row 2 = the whole-sprite transforms (flip / rotate 90 / shift-with-wrap x4 / clear)
# plus the copy/paste/erase actions. UNDO/REDO left this row in #111: the ONE bar
# UNDO/REDO pair is now THE undo (routed to this editor's op-history, console.py), so
# the two freed slots just widen the remaining mode buttons (spacing).
# The drawing tools are direct mode buttons (touch-first, no chords): a tap selects
# that tool, and a grid drag then draws it with a live preview. Single-char labels
# are the fallback if a glyph is ever missing; the glyphs (chrome.py _GLYPHS) are
# the pre-literate primary cue. Order within each tuple is the hit-test/draw order.
_TOOL_ROW1 = ("pen", "fill", "rect", "line", "oval", "select")
_TOOL_ROW2 = ("copy", "paste", "erase", "fliph", "flipv", "rot",
              "sleft", "sright", "sup", "sdown", "clear")
_TOOLS = _TOOL_ROW1 + _TOOL_ROW2       # flat order; tool_btns follows it (row1, row2)
_TOOL_LABEL = {
    "pen": "P", "fill": "F", "rect": "R", "line": "L",
    "oval": "O", "select": "S", "copy": "C", "paste": "V", "erase": "E",
    "fliph": "H", "flipv": "M", "rot": "T", "sleft": "<", "sright": ">",
    "sup": "^", "sdown": "v", "clear": "X",
}
# The pre-literate glyph for each tool (#89-#93 icon pass): a 12x12 chrome glyph
# (runtime/chrome.py _GLYPHS) drawn centered instead of the single-char label, so
# the row reads as pictures. _blit_glyph draws NOTHING for an unknown kind, so the
# _TOOL_LABEL letter above stays the guaranteed fallback (see _draw_tools).
_TOOL_GLYPH = {
    "pen": "edit", "fill": "fill",
    "rect": "rect_tool", "line": "line", "oval": "circle", "select": "select",
    "copy": "copy", "paste": "paste", "erase": "eraser",
    "fliph": "flip_h", "flipv": "flip_v", "rot": "rotate",
    "sleft": "arr_l", "sright": "arr_r", "sup": "arr_u", "sdown": "arr_d",
    "clear": "clear",
}
# The drawing-mode tools (highlighted when active); the rest are one-shot actions.
_TOOL_MODE = {
    "pen": PaintEditor.PEN, "fill": PaintEditor.FILL, "rect": PaintEditor.RECT,
    "line": PaintEditor.LINE, "oval": PaintEditor.OVAL, "select": PaintEditor.SELECT,
}
# Baseline (320x240) tool rows: two full-width strips in the gap between the grid
# bottom (y176) and the SAVE/CLOSE row (y202). Row 1 = 8 buttons of 35px, row 2 =
# 11 buttons of 26px, both across x14..300.
_TOOL_X0 = 14
_TOOL_ROW1_Y = 176
_TOOL_ROW2_Y = 189
_TOOL_H = 12
_TOOL_SPAN = 286                       # x14..300 usable width
_TOOL_CW1 = _TOOL_SPAN // len(_TOOL_ROW1)   # 35
_TOOL_CW2 = _TOOL_SPAN // len(_TOOL_ROW2)   # 26


def _tool_row(x0, y0, cw, h, n):
    """A row of n button rects (x, y, w, h), cw apart with a 1px gutter."""
    return [(x0 + i * cw, y0, cw - 1, h) for i in range(n)]


class PaintLayout(LayoutBase):
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
        LayoutBase.__init__(self, w, h, font_scale)
        fs = self.fs
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
            self.files_btn = _PAINT_FILES
            self.close_btn = _PAINT_CLOSE
            self.status_xy = (110, 206)
            self.status_maxc = 11
            # The #90 tool rows: row 1 = drawing modes + undo/redo, row 2 =
            # copy/paste/erase + transforms, in the grid->CLOSE gap (verbatim).
            self.tool_btns = (
                _tool_row(_TOOL_X0, _TOOL_ROW1_Y, _TOOL_CW1, _TOOL_H, len(_TOOL_ROW1))
                + _tool_row(_TOOL_X0, _TOOL_ROW2_Y, _TOOL_CW2, _TOOL_H, len(_TOOL_ROW2)))
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
        row_y = p_bottom - 30 * fs                # CLOSE row (base 190; SAVE lived here too)
        toolh = 12 * fs                           # #90 tool row height (per row)
        tools_band = 2 * toolh + 2 * fs           # two rows + a gutter
        self.pg_x0 = px + 6 * fs
        self.pg_y0 = py + 16 * fs
        # Reserve BOTH tool rows' band (their height + gaps) above the CLOSE row so the
        # grid never grows over them -- otherwise the same as the shipped formula.
        avail = min(rc_x - self.pg_x0 - 8 * fs,
                    row_y - self.pg_y0 - tools_band - 6 * fs)
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
        self.files_btn = (rc_x + 40 * fs, self.pg_y0 + 146 * fs, 92 * fs, 20 * fs)
        self.close_btn = (p_right - 112 * fs, row_y, 102 * fs, 26 * fs)
        self.status_xy = (self.pg_x0 + 96 * fs, row_y + 6 * fs)
        self.status_maxc = max(4, (self.close_btn[0] - self.status_xy[0]) // (8 * fs))
        # The #90 tool rows: two full-width strips just above the CLOSE row,
        # growing their button cells with the panel width. The reserved `avail` band
        # above keeps the (grown) grid clear of them. Row 2 sits directly above the
        # CLOSE row; row 1 above it.
        row2_y = row_y - toolh - 3 * fs
        row1_y = row2_y - toolh - 1 * fs
        tool_span = (p_right - 6 * fs) - self.pg_x0
        cw1 = max(10 * fs, tool_span // len(_TOOL_ROW1))
        cw2 = max(10 * fs, tool_span // len(_TOOL_ROW2))
        self.tool_btns = (
            _tool_row(self.pg_x0, row1_y, cw1, toolh, len(_TOOL_ROW1))
            + _tool_row(self.pg_x0, row2_y, cw2, toolh, len(_TOOL_ROW2)))


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
        self._fill_fired = False      # FILL already fired this press (#90; see _paint_stroke)
        self._shape_start = None      # RECT/LINE/OVAL/SELECT drag origin cell (#90)
        self._shape_end = None        # ...and its current (clamped) end cell, for preview
        self._ekey = KeyEdge()        # Ctrl+Z/Y edge tracker (undo-shortcut, #90)
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
        self._fill_fired = False
        self._shape_start = None
        self._shape_end = None

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
        if pe is not None:
            self._ekey.undo_redo(k, pe.undo, pe.redo)
        else:
            self._ekey.hit(k)          # keep the edge in sync even with no editor

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
        # no fresh click keeps drawing on the grid so press-and-move draws a continuous
        # stroke / grows a shape's preview -- the same path for a host mouse drag and a
        # device touch drag (both = pointer.down + moving position). Releasing commits
        # the stroke/shape/selection (#30/#90). The theme editor (EDIT ICONS) reuses
        # this exact path over the icon sheet.
        if click:
            self._paint_click(px, py)
        elif ws.pointer.down:
            self._paint_drag_move(px, py)
        else:
            self._paint_release()
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

    def _grid_cell_clamped(self, px, py):
        """Grid-local cell under (px, py), CLAMPED into [0, dim) even when the pointer
        wandered off the grid -- used for the shape/select drag preview so dragging
        past an edge still tracks to the edge (#90). Returns None only with no editor."""
        pe = self.ws.paint
        lay = self.layout
        if pe is None:
            return None
        cell = lay.pg_span // pe.dim
        if cell < 1:
            cell = 1
        d = pe.dim
        lx = (px - lay.pg_x0) // cell
        ly = (py - lay.pg_y0) // cell
        lx = 0 if lx < 0 else (d - 1 if lx > d - 1 else lx)
        ly = 0 if ly < 0 else (d - 1 if ly > d - 1 else ly)
        return (lx, ly)

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
        # stroke machinery below is bypassed. The guard is a dedicated per-press
        # flag -- NOT _paint_drag, which resets whenever the held pointer wobbles
        # off the grid edge (above) and would re-fire a second flood (+ a surprise
        # extra undo step) on re-entry; _fill_fired only clears on pointer RELEASE.
        if pe.tool == pe.FILL:
            if not self._fill_fired:
                self._fill_fired = True
                pe.fill(cell[0], cell[1])
            self._paint_drag = cell
            return True
        last = self._paint_drag
        if last is None:
            pe.begin_stroke()              # snapshot before the first pixel (#90)
            pe.paint(cell[0], cell[1])
        else:
            for cx, cy in _pe_line(last[0], last[1], cell[0], cell[1]):
                pe.paint(cx, cy)
        self._paint_drag = cell
        return True

    def _grid_press(self, px, py):
        """Handle a press that lands inside the pixel grid. PEN/FILL go through the
        continuous _paint_stroke path (#30); RECT/LINE/OVAL/SELECT record a drag origin
        and defer the commit to release (#90). Returns True iff the press was in-grid."""
        pe = self.ws.paint
        if pe.tool in (pe.RECT, pe.LINE, pe.OVAL, pe.SELECT):
            cell = self._paint_grid_cell(px, py)   # a shape/select must START in-grid
            if cell is None:
                return False
            self._shape_start = cell
            self._shape_end = cell
            return True
        return self._paint_stroke(px, py)

    def _paint_drag_move(self, px, py):
        """A held drag with no fresh click. Grow the pending shape/selection preview
        (clamped to the grid), else keep painting the continuous pen/fill stroke."""
        pe = self.ws.paint
        if pe is None:
            return
        if self._shape_start is not None:
            cell = self._grid_cell_clamped(px, py)
            if cell is not None:
                self._shape_end = cell
            return
        self._paint_stroke(px, py)

    def _paint_release(self):
        """Pointer released (anywhere): commit the pending shape/selection, or close a
        brush stroke, so a whole press-drag-release is ONE undo step (#90). Idempotent
        when idle. Re-arms the once-per-press FILL guard (release is the ONLY place it
        clears -- a mid-press grid exit must not re-arm it)."""
        pe = self.ws.paint
        if pe is not None:
            if self._shape_start is not None:
                self._commit_shape_or_select(pe)
            else:
                pe.end_stroke()
        self._paint_drag = None
        self._fill_fired = False
        self._shape_start = None
        self._shape_end = None

    def _commit_shape_or_select(self, pe):
        """Turn a finished shape/select drag into an edit. RECT/LINE/OVAL stamp the
        shape; SELECT sets the selection box on a real drag, and a TAP (no drag) either
        stamps the clipboard there (move/copy) or clears the selection (#90)."""
        s = self._shape_start
        e = self._shape_end if self._shape_end is not None else s
        if pe.tool == pe.SELECT:
            if s == e:
                if pe.has_clip:
                    pe.paste(s[0], s[1])       # tap-to-stamp: move/copy the clip here
                else:
                    pe.clear_selection()       # tap on empty space deselects
            else:
                pe.set_selection(s[0], s[1], e[0], e[1])
        else:
            pe.stamp_shape(s[0], s[1], e[0], e[1])

    def _paint_click(self, px, py):
        # A tap (press edge). Draw in the grid cell, or hit a button/palette swatch.
        ws = self.ws
        pe = ws.paint
        lay = self.layout
        if pe is None:
            return
        if self._grid_press(px, py):           # draw/shape/select in the zoomed grid
            return
        tid = self._tool_at(px, py)            # a #90 tool button (mode/undo/transform)?
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
        elif (getattr(lay, "files_btn", None) is not None
              and self._in(px, py, lay.files_btn) and not ws._editing_icons):
            ws.send_sprites_to_files()       # export the sheet to files/sprites/ (#108)
        elif self._in(px, py, lay.close_btn):
            # CLOSE returns to Settings (theme editor, hard-committing on the way --
            # ThemeLayer.leave -- since it has no PLAY of its own) or runs+leaves to
            # the cart (PAINT: ws._leave_menu is EditorApp.leave, PLAY, itself now a
            # hard-commit trigger, #111 -- no SAVE tap exists anymore either way).
            # #184: deferred -- the commit+run happens behind the next paint.
            ws.defer(ws._leave_theme if ws._editing_icons else ws._leave_menu)

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
        touch-reachable here; undo/redo moved to the ONE bar pair (#111)."""
        pe = self.ws.paint
        if pe is None:
            return
        if tid in _TOOL_MODE:                  # pen/fill/rect/line/oval/select
            pe.set_tool(_TOOL_MODE[tid])
        elif tid == "copy":
            pe.copy_selection()
        elif tid == "paste":
            self._paste_default(pe)
        elif tid == "erase":
            pe.toggle_erase()
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

    def _paste_default(self, pe):
        """The PASTE button stamps the clipboard at the active selection's top-left (so
        copy->paste lands in place), or at (0, 0) with no selection. In SELECT mode a
        tap on the grid pastes at the tapped cell instead (the move/copy gesture)."""
        if not pe.has_clip:
            return
        if pe.sel is not None:
            pe.paste(pe.sel[0], pe.sel[1])
        else:
            pe.paste(0, 0)

    def _draw_tools(self):
        """Draw the compact tool row: the drawing MODES, the FILL toggle, and the
        whole-sprite transforms. Each button carries a centered 12x12 chrome GLYPH (the
        #89-#93 icon pass -- pen/fill/flip/rotate/shift-arrows/clear) instead of its
        one-char label, so the row reads as pictures on the pre-literate tiers. The
        active drawing tool is accented; copy/paste dim when unusable (undo/redo moved
        to the ONE bar pair, #111). Drawn on the panel
        surface directly (indexed primitives + ws._glyph), so host == device; the glyph
        follows the canvas font scale (#39). If a glyph kind were ever missing, ws._glyph
        draws nothing and the _TOOL_LABEL letter is the guaranteed fallback."""
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
            # The active drawing MODE is accented; so is the erase toggle when armed.
            active = (tid in _TOOL_MODE and pe.tool == _TOOL_MODE[tid]) or (
                tid == "erase" and pe.erase)
            enabled = True
            if tid in ("copy", "paste"):
                # copy needs a selection, paste needs a clip -- dim when unusable.
                enabled = (pe.sel is not None) if tid == "copy" else pe.has_clip
            fill = NAMES["indigo"] if active else (
                NAMES["dark_grey"] if enabled else NAMES["black"])
            ink = NAMES["white"] if enabled else NAMES["dark_grey"]
            cv.rect(x, y, w, h, fill)
            cv.rectb(x, y, w, h, NAMES["light_grey"])
            kind = _TOOL_GLYPH.get(tid)
            if kind is not None and kind in _GLYPHS:
                ws._glyph(kind, (x, y, w, h), ink, cv)
            else:                            # missing glyph -> the letter is the fallback
                cv.print(_TOOL_LABEL[tid],
                         x + (w - 8 * fs) // 2, y + (h - 8 * fs) // 2, ink, fs)

    def _draw_grid_overlay(self, cv, lay, pe, gx0, gy0, cell):
        """Overlay the active selection marquee and the live shape/select drag preview
        on the zoomed grid (#90). The committed selection is a solid white box; a live
        SELECT drag is a yellow box; a live RECT/LINE/OVAL drag stamps the shape's cells
        in the current ink with a white outline so it reads even when ink == background.
        Called after the grid pixels + tile boundaries; a no-selection / no-drag editor
        draws nothing (baseline parity)."""
        NAMES = self._NAMES

        def box(x0, y0, x1, y1, c):
            cv.rectb(gx0 + x0 * cell, gy0 + y0 * cell,
                     (x1 - x0 + 1) * cell, (y1 - y0 + 1) * cell, c)

        if pe.sel is not None:
            box(pe.sel[0], pe.sel[1], pe.sel[2], pe.sel[3], NAMES["white"])
        if self._shape_start is not None and self._shape_end is not None:
            s, e = self._shape_start, self._shape_end
            if pe.tool == pe.SELECT:
                x0 = s[0] if s[0] < e[0] else e[0]
                x1 = s[0] if s[0] > e[0] else e[0]
                y0 = s[1] if s[1] < e[1] else e[1]
                y1 = s[1] if s[1] > e[1] else e[1]
                box(x0, y0, x1, y1, NAMES["yellow"])
            else:
                ink = 0 if pe.erase else pe.color
                for (x, y) in pe.shape_points(s[0], s[1], e[0], e[1]):
                    cv.rect(gx0 + x * cell, gy0 + y * cell, cell, cell, ink)
                    if cell >= 6:
                        cv.rectb(gx0 + x * cell, gy0 + y * cell, cell, cell,
                                 NAMES["white"])

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
        light = (not lay._base) or ws.look.light_chrome()  # tokens on every responsive tier; _base stays frozen only in DARK chrome
        cv.rect(*(lay.body_fill + ((th["surface"] if light else NAMES["black"]),)))
        # The panel joins the surface on the light tiers -- the pixel grid and
        # swatch/preview cells all back themselves, so only the frozen dark
        # baseline needs the black plate.
        # Only the sliver of `panel` that `body_fill` did not already cover in
        # this exact colour -- the full re-fill rewrote ~94% of ~450K px for
        # nothing (see ui.fill_uncovered).
        _ui.fill_uncovered(cv, lay.panel, lay.body_fill,
                           th["surface"] if light else NAMES["black"])
        cv.rectb(*(lay.panel + ((th["author"] if light else NAMES["orange"]),)))
        # No dirty star: SAVE (the button and the concept) is gone (#111) -- autosave
        # commits pending edits within seconds, so "unsaved" is not a state a kid can
        # act on; write FAILURES surface loudly via ws.save_status instead.
        title = ("ICONS  TILE " if ws._editing_icons else "PAINT  SPR ") + str(pe.n if pe else 0)
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
        # The selection marquee + the live shape/select drag preview (#90). Draws
        # nothing in the default (no-selection, no-drag) state, so the frozen 320x240
        # baseline stays byte-identical (the #39 parity contract).
        self._draw_grid_overlay(cv, lay, pe, gx0, gy0, cell)
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
            fbtn = getattr(lay, "files_btn", None)
            if fbtn is not None:             # export the sheet to files/sprites/ (#108)
                ws._btn("FILE", fbtn, NAMES["dark_blue"], cv)
        if ws.paint_status:
            cv.print(ws.paint_status[:lay.status_maxc],
                     lay.status_xy[0], lay.status_xy[1],
                     th["author"] if light else NAMES["yellow"], 1)
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
    flag ws._editing_icons stays on ws (the device backend reads it) and the
    sheet/save trio is ws.look's (load_icon_sheet/set_icon_sheet/save_icons) --
    ThemeLayer dispatches."""

    id = "theme"
    domain = "system"

    def __init__(self, ws, paint_layer, names):
        self.ws = ws
        self._paint = paint_layer
        self._NAMES = names

    def draw(self, dt):
        # EDIT ICONS (Stage 2): opened from Settings, NOT a running cart, so there's no
        # cart backdrop to draw -- clear to black and reuse the shared PAINT renderer
        # (over ws.look.icon_sheet, selected by ws._editing_icons), on the system canvas.
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
        at ws.look.icon_sheet: CLOSE persists system_icons.moygfx (not a cart, #111: no
        SAVE tap -- leave() hard-commits) and returns to Settings. Starts from the
        current theme (the baked default if no system_icons.moygfx exists yet); the
        first commit creates the file."""
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
        if ws.look.icon_sheet is not None:
            ws.paint = PaintEditor(ws.look.icon_sheet)
        self._paint.reset_drag()
        ws._set_text_mode(False)         # paint is pointer-driven, raw/game keyboard
        ws.ach.note("editor", "paint")   # repainting the chrome counts toward Toolbox

    def leave(self):
        """CLOSE/back from the theme editor: hard-commit the icon sheet (#111 --
        there's no SAVE tap, and the theme editor has no PLAY of its own to double
        as one either, unlike the cart-sprite paint tab), then return to Settings
        (not a cart/desktop -- the theme editor was opened from there). Drops the
        editor + clears the editing-icons flag so the cart PAINT flow is untouched
        next time."""
        ws = self.ws
        ws._dirty = True                 # screen change repaints (#44)
        ws.look.save_icons()             # hard-commit BEFORE the editor drops (#111)
        ws._editing_icons = False
        ws.paint = None
        self._paint.reset_drag()
        ws.wm.goto("settings")           # Stage 6e: pop the theme editor, back to Settings
