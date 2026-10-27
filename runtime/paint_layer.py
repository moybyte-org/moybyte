# Map (grep -n a name to jump there):
#   -- paint geometry  the paint tab's constants (console.py imports them back)
#   -- the tool row    PICO-8's tools, the more menu's operations, their keys
#   PaintLayout        responsive paint-editor geometry
#   PaintLayer         the paint editor content layer: draw, strokes, keys
"""The sprite PAINT editor (#4/#30), extracted from Workstation
(runtime/console.py) as its own Layer -- docs/history/shell_layers_refactor_v1.md Phase 2.

It edits the cart's SpriteSheet (ws.project.sheet) through `ws.paint` (a PaintEditor).
SAVE is GONE (#111, owner decision 2026-07-21): there is no SAVE button anywhere in the
editor -- CLOSE hard-commits the sheet on its way out (ws._leave_menu, which is
EditorApp.leave -- PLAY, a hard-commit trigger now), and the idle-typing autosave-commit +
every other tab-leaving exit path (a tab switch, PROJECTS, a window/context-X, a workspace
swap, going home) commit it too. A theme's icons are the Editor's theme mode's
(runtime/theme_editor.py).

Boundary (the anti-spaghetti line, per the doc): the SHEET + the current-editor handle
+ the persistence verbs stay on Workstation -- `ws.project.sheet` (single source of the
pixels), `ws.paint` (the PaintEditor handle, device/test-pinned like ws.editor),
`ws.paint_status` (status), and `ws.save_sprites` / `ws.share_tile_get` /
`ws.share_tile_put` (cart state the device + tests pin). PaintLayer READS those and
DISPATCHES to them; it
owns only the paint-UI: the DRAW, the grid/palette/button hit-testing, and the drag-
stroke continuity state (_paint_drag). The paint-only constants live here (single source;
console.py imports them back so tests + tools resolve console._PG_X0 / _PG_SPAN / ...).
`NAMES` (palette) is injected (the circular-import dodge); the rect hit-test is
`ui.rect_in`, imported directly.

Stage 4 (#46 zoned bar): PaintLayer.draw() calls ws.bar_layer._draw_status_strip("menu")
LAST (chrome over content).

Responsive (#39 step 3): the paint editor is SYSTEM-domain now -- it draws on the
reflowed system canvas at the panel's native size via `PaintLayout`, whose
formulas lay out every canvas, the T-Deck's 320x240 included. On a
big panel the zoomed pixel grid GROWS to fill the space (a multiple of 48px so the
1x1/2x2/3x3 sprite sizes all divide it into whole pixels) and the chrome scales with
the font. Hit-testing is in SYSTEM coords (no _game_xy translation).
"""

try:
    import ui as _ui              # frozen on device
    import ui_draw as _ui_draw
    from editor_map import checker as _checker, checker_quads as _checker_quads
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
    from runtime.editor_map import checker as _checker, checker_quads as _checker_quads
_in = _ui.rect_in   # one hit-test (ui.rect_in)
from array import array
from editors import PaintEditor, KeyEdge, _pe_line

try:
    from layout_base import (LayoutBase, BASE_W as _BASE_W, BASE_H as _BASE_H,
                             metric_class as _metric_class, DESK as _DESK,
                             render_scale as _render_scale, pane as _in_pane)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import (LayoutBase, BASE_W as _BASE_W,
                                     BASE_H as _BASE_H,
                                     metric_class as _metric_class, DESK as _DESK,
                                     render_scale as _render_scale, pane as _in_pane)


# -- paint geometry (single source; console.py imports these back) ------------
# The grid's origin and span and the swatch column at the 320x240 canvas, which
# PaintLayout derives on every canvas. Zoomed pixel grid: a fixed _PG_SPAN
# square; the per-pixel cell shrinks as the sprite size grows (1x1 -> 18px
# cells; 2x2 -> 9px; 3x3 -> 6px), so a bigger sprite (#30) edits in the same
# on-screen footprint.
_PG_X0 = 14
_PG_Y0 = 32
_PG_CELL = 18                      # cell size at size 1 (8x8); _PG_SPAN derives from it
_PG_SPAN = 8 * _PG_CELL            # fixed grid footprint in px (144), all sizes
_SW_X0 = 170
_SW_Y0 = 32
_SW = 18
_SW_COLS = 2

# -- the tool row (PICO-8's set, owner 2026-10-19) ---------------------------
# ONE row on every tier: the drawing tools -- pen, fill, shape, select and
# stamp -- then the more button, which opens the operations as a menu. Shape
# is one button over three tools: a tap selects the last shape used, a tap
# while a shape is in use moves to the next (oval -> rect -> line), and its
# icon is the shape in use. Stamp stamps the clipboard where the grid is
# tapped. The operations (copy, the flips, rotate, the shifts, mirror, clear)
# act on the selection, else the whole sprite, and each has a key on a
# keyboard (PICO-8's: F V R, the arrows, Del, Ctrl-C/X/V). UNDO/REDO are
# the bar's ONE pair (#111). Order within the row is the hit-test/draw order.
_TOOL_ROW = ("pen", "fill", "shape", "select", "stamp", "more")
_TOOLS = _TOOL_ROW                     # tool_btns follows it
_SHAPES = (PaintEditor.OVAL, PaintEditor.RECT, PaintEditor.LINE)
# The glyph table's kind for each tool (#89-#93 icon pass), where its icon
# set has no slot (`_TOOL_ICON`).
_TOOL_GLYPH = {
    "pen": "edit", "fill": "fill", "select": "select", "stamp": "paste",
    "more": "more",
}
# The 2026-10 look's icon for each tool: the icon set's slot where the sets
# have one, else the glyph table's (`ui.icon`'s order).
_TOOL_ICON = {
    "pen": "pencil", "fill": "bucket", "select": "select", "stamp": "stamp",
    "more": "more",
}
# A shape tool's (slot, glyph): the shape button wears the shape in use.
_SHAPE_ICON = {
    PaintEditor.OVAL: ("oval", "circle"), PaintEditor.RECT: ("rect", "rect_tool"),
    PaintEditor.LINE: ("line", "line"),
}
# The more menu's operations, in its order: (id, label, icon slot). Clear is
# a danger row, set apart at the end.
_OPS = (
    ("copy", "Copy", "copy"), ("fliph", "Flip across", "flip"),
    ("flipv", "Flip over", "flip_v"), ("rot", "Rotate", "rotate"),
    ("sleft", "Shift left", "arrow_left"), ("sright", "Shift right", "arrow_right"),
    ("sup", "Shift up", "arrow_up"), ("sdown", "Shift down", "arrow_down"),
    ("mirror", "Turn on mirror", "mirror"), ("clear", "Clear", "del"),
)
# The shifts, which a handheld's more menu folds into one row.
_SHIFT_OPS = ("sleft", "sright", "sup", "sdown")
# The key each operation answers to, per input (the hints' profiles,
# native/moy_ui/moy_ui_parts.c's ACT_KEYS): what the more menu shows at a
# row's right, an arrow named as the hints name it (`ui.menu` draws it as
# the hint strip's sign, never a face's arrow). The T-Deck's trackball moves
# the grid's cursor, so its shifts have no key; a gamepad and touch reach the
# operations through the menu.
_OP_KEYS = {
    "keys": {"copy": "Ctrl C", "fliph": "F", "flipv": "V", "rot": "R",
             "sleft": "left", "sright": "right", "sup": "up", "sdown": "down",
             "clear": "Del"},
    "tdeck": {"fliph": "F", "flipv": "V", "rot": "R", "clear": "Del"},
}
# The typed bytes the tab answers to, on a keyboard: PICO-8's sprite keys.
_KEY_OPS = {
    0x03: "copy", 0x18: "cut", 0x16: "paste", 0x08: "clear", 0x7F: "clear",
    ord("f"): "fliph", ord("F"): "fliph", ord("v"): "flipv", ord("V"): "flipv",
    ord("r"): "rot", ord("R"): "rot", ord("m"): "more", ord("M"): "more",
}
# The hint strip: the operations' actions (ui.hints draws each as the keys of
# the input in use, and drops one the input has no key for), the more
# menu's last. With a selection, clear takes shift's place.
_HINTS = ("flip", "Flip", "rotate", "Rotate", "shift", "Shift", "more", "More")
_HINTS_SEL = ("flip", "Flip", "rotate", "Rotate", "clear", "Clear", "more", "More")
# The desk Editor's Sprite pane: its sprite keys, the more menu, the next pane.
_HINTS_PANE = ("Q W", "Sprite", "more", "More", "F6", "Next pane")
# While a popover is open its keys alone: what closes it.
_HINTS_POP = ("back", "Close")
# The pickers' sizes, PaintEditor.SIZES's order.
_SIZE_NAMES = ("1x1", "2x2", "3x3")
# The drawing-mode tools (highlighted when active); shape is any of _SHAPES.
_TOOL_MODE = {
    "pen": PaintEditor.PEN, "fill": PaintEditor.FILL, "select": PaintEditor.SELECT,
    "stamp": PaintEditor.STAMP,
}
# The sheet the map draws is 16 sprites across (SPEC.md 3.2's 128 px).
_MAP_COLS = 16


def _tool_row(x0, y0, cw, h, n):
    """A row of n button rects (x, y, w, h), cw apart with a 1px gutter."""
    return [(x0 + i * cw, y0, cw - 1, h) for i in range(n)]


class PaintLayout(LayoutBase):
    """Responsive paint-editor geometry (#39 step 3): the zoomed pixel grid,
    the 16-color swatch column, the sprite steppers and its size, the sprite
    map, the load / save / to-Files buttons and the tool row, derived from the
    SYSTEM canvas size (w, h) + font scale (`_look_geometry`; the desk
    Editor's Sprite pane, `_pane_geometry`). The T-Deck's 320x240 is laid out
    by the same formulas as every canvas.

    The grid span is the star of the reflow: it grows to the largest multiple of
    48px that fits (48 = lcm of the 8/16/24 sprite dims, so every zoom level keeps
    whole on-screen pixels), which is what "a larger editing app" means for paint --
    a hugely bigger drawing surface, not just scaled chrome."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1,
                 chrome_scale=None, keys=True):
        LayoutBase.__init__(self, w, h, font_scale, chrome_scale=chrome_scale)
        self.keys = bool(keys)          # an input with keys: the hint strip shows
        self.desk = _metric_class() == _DESK
        self.pane = self.desk and _in_pane()
        self.flag_cells = ()
        self.preview = None
        if self.pane:
            self._pane_geometry()
        else:
            self._look_geometry()

    def _pane_geometry(self):
        """The desk Editor's Sprite pane (EDITOR_LAYOUT.md's Art, 1024 x 600:
        the pane's header names the sprite and holds flip and rotate): the
        tool row of 36 px cells at its head, the grid at the largest whole
        zoom for 8, 16 and 24 (a multiple of 48) with the palette's two
        columns at its right, then the sprite's steppers and its size, its
        eight flags, the sprite as the game draws it at 1x, 2x and 4x, and
        the hints at the foot. The sprite map and the load and save buttons
        are the Sheet's and the more menu's here."""
        cx, cy, cw, ch = self.cx, self.cy, self.cw, self.ch
        tall = ch >= 470                # 1024 x 600; a short pane (800 x 480) packs
        pad, gap, cell = 14, 8, (36 if tall else 24)
        self.body_fill = self.panel = (cx, cy, cw, ch)
        hint_h = max(34 // _render_scale(), 18 * self.fs) if self.keys else 0
        self.hints = (cx, cy + ch - hint_h, cw, hint_h) if hint_h else None
        self.trow, self.icon = cell, 24
        n = len(_TOOL_ROW)
        top = cy + 6
        self.tool_btns = _tool_row(cx + pad, top, cell + 4, cell, n)
        self.more_btn = self.tool_btns[-1]
        gx, gy = cx + pad, top + cell + 10
        below = cell + 10 + cell + 8 + (34 + 8 if tall else 0)
        wroom = (cw - 2 * pad - gap) * 4 // 5
        hroom = cy + ch - hint_h - gy - below
        span = max(48, 48 * (min(wroom, hroom) // 48))
        self.pg_x0, self.pg_y0, self.pg_span = gx, gy, span
        self.pg_area = (gx, gy, span, span)
        self.title_xy = (-4096, -4096)
        self.caption = (gx, gy, 0, 0)
        sw = span // 8
        self.sw, self.sw_cols = sw, _SW_COLS
        self.sw_x0, self.sw_y0 = gx + span + gap, gy
        self.sw_area = (self.sw_x0, gy, 2 * sw, 8 * sw)
        y = gy + span + 10
        line = _ui_draw.font("ui")[2] * self.fs
        self.spr_prev = (gx, y, cell, cell)
        nw = _ui_draw.print_width("000", self.fs) + 16
        self.num_rect = (gx + cell, y, nw, cell)
        self.spr_next = (gx + cell + nw, y, cell, cell)
        sx = gx + 2 * cell + nw + 12
        self.size_xy = (sx, y + (cell - line) // 2)
        self.size_btn = (sx - 4, y, 56, cell)
        self.pal_chip = (self.sw_x0, y, 2 * sw, cell)
        y += cell + 8
        fw = _ui_draw.print_width("Flags", self.fs) + 12
        fp = max(16, min(cell, (cw - 2 * pad - fw) // 8))
        self.flag_cells = tuple((gx + k * fp, y, fp, cell) for k in range(8))
        y += cell + 8
        self.preview = (gx, y, span + gap + 2 * sw, 34) if tall else None
        off = (-4096, -4096, 0, 0)
        self.get_btn = self.put_btn = self.files_btn = off
        self.btn_words = False
        self.status_xy, self.status_w = (-4096, -4096), 0
        self.map_rect = (gx, gy, 0, 0)

    def _look_geometry(self):
        """The 2026-10 look's arrangement (the `sprite-editor` targets, with the owner's D-15 of 2026-10-19):
        "Sprite N" over the grid, the palette's two columns beside it, then a
        column of compact parts from its left: ‹ › with the Size caption
        beside them, the sprite map (the sheet around the sprite, the sprite
        outlined) and the load, save and to-Files buttons at its foot. The
        tool row goes under the grid, or at the column's head where that
        leaves the grid bigger (the desk), at the mockup's spacing; the hint
        strip runs along the content's foot under an input with keys. The
        grid is the largest multiple of 48 that fits."""
        fs, desk = self.fs, self.desk
        pad, gap = (16, 20) if desk else (6 * fs, 6 * fs)
        trow = 40 if desk else 22 * fs
        bh = 40 if desk else 18 * fs
        sep = 12 if desk else 3 * fs              # between the column's parts
        g2 = 8 if desk else 3 * fs                # between two buttons
        # The caption and the Size line print in the `ui` face; the grid
        # clears its ascent, which holds a caption's ink (it has no
        # descenders).
        asc = _ui_draw.font("ui")[3] * fs
        line = _ui_draw.font("ui")[2] * fs
        cx, cy, cw, ch = self.cx, self.cy, self.cw, self.ch
        self.body_fill = self.panel = (cx, cy, cw, ch)
        hint_h = (max(34 // _render_scale(), 18 * fs) if desk else 20 * fs) if self.keys else 0
        self.hints = (cx, cy + ch - hint_h, cw, hint_h) if hint_h else None
        top = cy + (pad if desk else 3 * fs)
        self.title_xy = (cx + pad, top)
        self.trow = trow
        self.icon = 24 if desk else 16 * fs
        gx = cx + pad
        gy = top + asc + (10 if desk else 3 * fs)
        tgap = 14 if desk else 3 * fs
        bottom = cy + ch - hint_h - (pad // 2 if desk else 2 * fs)
        n = len(_TOOL_ROW)
        pitch = 48 if desk else 24 * fs           # the mockup's tool spacing
        col = 300 if desk else 120 * fs           # the column's least width

        def span_of(hroom, colw):
            wroom = (cw - 2 * pad - 2 * gap - colw) * 144 // 166
            return max(48, 48 * (min(hroom, wroom) // 48))
        under = span_of(bottom - gy - tgap - trow, col)
        head = span_of(bottom - gy, max(col, n * pitch))
        beside = head > under                     # the row at the column's head
        span = head if beside else under
        self.pg_x0, self.pg_y0, self.pg_span = gx, gy, span
        self.pg_area = (gx, gy, span, span)
        self.caption = (gx, top - 2 * fs, span, asc + 4 * fs)
        sw = max(6 * fs, span * 11 // 144)
        self.sw, self.sw_cols = sw, _SW_COLS
        self.sw_x0, self.sw_y0 = gx + span + gap, gy
        self.sw_area = (self.sw_x0, gy, 2 * sw, 8 * sw)
        rx = self.sw_x0 + 2 * sw + gap
        rw = cx + cw - pad - rx
        # ‹ n › (D-10's "‹ value ›"): square-ish icon buttons with the
        # sprite's number between them, in a slot that holds the sheet's
        # last, then the Size caption on its own (a tap on it opens the
        # pickers)
        sq = bh + (8 if desk else 4 * fs)
        self.spr_prev = (rx, gy, sq, bh)
        nw = _ui_draw.print_width("000", fs) + 2 * g2
        self.num_rect = (rx + sq, gy, nw, bh)
        self.spr_next = (rx + sq + nw, gy, sq, bh)
        sx = rx + 2 * sq + nw + (12 if desk else 5 * fs)
        self.size_xy = (sx, gy + (bh - line) // 2)
        self.size_btn = (sx - 2 * fs, gy, max(0, rx + rw - sx + 2 * fs), bh)
        y = gy + bh + sep
        if beside:
            pitch = min(pitch, rw // n)
            self.tool_btns = _tool_row(rx, y, pitch, trow, n)
            y += trow + sep
        # The load / save / to-Files row at the column's foot, from its
        # left: each its icon and its word where the three fit in the
        # column's width, else their icons alone. A tool row under the grid
        # stands on the same line (design review R4-17): its plates are the
        # buttons' height.
        fy = bottom - bh
        if not beside:
            pitch = min(pitch, span // n)
            self.tool_btns = _tool_row(gx, fy, pitch, trow, n)
        words = ("Load", "Save", "To Files")
        inner = self.icon + (8 if desk else 4 * fs) + 2 * (12 if desk else 5 * fs)
        ws_ = [inner + _ui_draw.print_width(wd, fs) for wd in words]
        self.btn_words = sum(ws_) + 2 * g2 <= rw
        if not self.btn_words:
            ws_ = [sq, sq, sq]
        x = rx
        rects = []
        for w_ in ws_:
            rects.append((x, fy, w_, bh))
            x += w_ + g2
        self.get_btn, self.put_btn, self.files_btn = rects
        self.status_xy = (x, fy + (bh - line) // 2)
        self.status_w = max(0, rx + rw - x)
        # The sprite map fills what the column has left.
        self.map_rect = (rx, y, rw, max(0, fy - sep - y))
        self.more_btn = self.tool_btns[-1]


class PaintLayer:
    """The paint editor content Layer (SYSTEM domain, responsive #39 step 3): a
    full-screen panel on the reflowed system canvas (the frozen-cart backdrop is
    gone -- the panel always covered every pixel of it anyway). draw = the paint UI
    at the PaintLayout geometry; handle_pointer routes taps to the grid/palette/
    buttons in SYSTEM coords; keyboard is no-op (paint is pointer-driven). Reads
    ws.paint / ws.project.sheet and dispatches SAVE/GET/PUT/
    CLOSE to Workstation."""

    id = "paint"
    domain = "system"

    def __init__(self, ws, names):
        self.ws = ws
        self._NAMES = names
        self._paint_drag = None       # last painted grid cell during a drag (#30)
        self._fill_fired = False      # FILL already fired this press (#90; see _paint_stroke)
        self._shape_start = None      # RECT/LINE/OVAL/SELECT drag origin cell (#90)
        self._shape_end = None        # ...and its current (clamped) end cell, for preview
        self._ekey = KeyEdge()        # Ctrl+Z/Y edge tracker (undo-shortcut, #90)
        # The grid's cursor (lx, ly): the cell the pointer last pressed or
        # dragged over, moved by the arrows; `a` acts there as a tap would.
        self._cur = [0, 0]
        # The pickers (the sprite and its size): a popover of the public UI
        # API's, its host side made at the first open; the release that
        # tapped inside it last.
        self._pop = False             # the popover open: "pickers", "more" or False
        self._host = None
        self._pop_tap = None
        self._shape = PaintEditor.OVAL   # the shape button's shape when not in use
        self._shifts = False          # the more menu shows its shifts' page
        self._grid_q = None           # the grid's coloured and clear cells, as quads
        sc = ws.sys_canvas
        self._lay_args = (sc.w, sc.h, getattr(sc, "font_scale", 1), None)
        self.layout = PaintLayout(sc.w, sc.h, getattr(sc, "font_scale", 1),
                                  keys=self._keys())

    @staticmethod
    def _keys():
        """Whether the hints have keys to name, and so the tab a hint strip."""
        return _ui_draw.hint_kind() != "touch"

    def relayout(self, w, h, fs, cs=None):
        """Rebuild the responsive geometry (#39) -- called by ws._relayout on a
        font-scale change (and, later, a window resize)."""
        self._lay_args = (w, h, fs, cs)
        self.layout = PaintLayout(w, h, fs, chrome_scale=cs, keys=self._keys())

    def _sync_layout(self):
        """A change between touch and an input with keys (the hint strip)
        lays the tab out again."""
        if self.layout.keys != self._keys():
            w, h, fs, cs = self._lay_args
            self.layout = PaintLayout(w, h, fs, chrome_scale=cs, keys=self._keys())

    # -- Layer facets --------------------------------------------------------

    def draw(self, dt):
        # menu_view == "paint": a full-screen editor panel on the SYSTEM canvas (#39
        # step 3). The frozen-cart backdrop (_draw_menu_backdrop) is gone: the body
        # fill + the bar cover every pixel it ever painted, so the pixels are
        # identical and the cart's _draw() no longer runs on editor frames. The game
        # canvas still gets its state reset (degradation shares one canvas).
        self.ws._reset_canvas_state()
        self._draw_paint()
        # The Editor's lent top-bar zone (Stage 4, #46 zoned bar): the tab ladder +
        # PLAY.
        self.ws.bar_layer._draw_status_strip("menu")

    def ball_steps(self):
        """The trackball steps an open popover's rows (the more menu, a
        picker) and points at the grid otherwise (Workstation.ball_steps)."""
        return bool(self._pop)

    def at_top(self):
        """Up goes on to the bar from the grid's top row, where the keys move
        its cursor (Workstation._bar_keys)."""
        return (not self._pop and self.ws.paint is not None and self._cur[1] == 0
                and _ui_draw.input_kind() != "keys")

    def handle_input(self, i):
        if self._pop:                  # the popover takes the keys while open
            if self._host.keys(i):
                self.ws.mark_dirty()
            return True
        self.handle_key(i)             # the typed keys: undo, redo, the operations
        if i.pressed("y") and self.ws.paint is not None:
            self._open_more()          # a gamepad's more menu
            return True
        self._cursor_keys(i)
        return True

    def _cursor_keys(self, i):
        """The arrows move the grid's cursor -- on a desk keyboard they shift
        the sprite, PICO-8's keys, and the pointer is the cursor there; `a`
        acts at it as a tap there does (a pen dot, a fill, a one-cell shape or
        selection, a stamp)."""
        ws = self.ws
        pe = ws.paint
        if pe is None:
            return
        dx = (1 if i.pressed("right") else 0) - (1 if i.pressed("left") else 0)
        dy = (1 if i.pressed("down") else 0) - (1 if i.pressed("up") else 0)
        c = self._cur
        if (dx or dy) and _ui_draw.input_kind() == "keys":
            pe.shift(dx, dy)
            ws.mark_dirty()
        elif dx or dy:
            d = pe.dim - 1
            c[0] = max(0, min(d, c[0] + dx))
            c[1] = max(0, min(d, c[1] + dy))
            ws.mark_dirty()
        if i.pressed("a"):
            lay = self.layout
            cell = max(1, lay.pg_span // pe.dim)
            px = lay.pg_x0 + min(c[0], pe.dim - 1) * cell + cell // 2
            py = lay.pg_y0 + min(c[1], pe.dim - 1) * cell + cell // 2
            if self._grid_press(px, py):
                self._paint_release()
            ws.mark_dirty()

    def _follow(self, px, py):
        """The cursor follows the pointer over the grid."""
        cell = self._paint_grid_cell(px, py)
        if cell is not None:
            self._cur[0], self._cur[1] = cell

    def handle_key(self, i):
        """The typed keys, edge-tracked so a held key fires once: Ctrl+Z (0x1A)
        / Ctrl+Y (0x19) undo and redo (#90), and PICO-8's sprite keys
        (`_KEY_OPS`): F and V flip, R rotates, Del and Backspace clear,
        Ctrl-C copies, Ctrl-X cuts, Ctrl-V stamps, M opens the more menu, and
        Q and W (PICO-8's) or E step to the sprite before and after -- Q and
        E on the T-Deck, where W moves the cursor (moy_kbd.c, THE MOVE KEYS).
        Each has its place on the screen too, so touch reaches every one."""
        pe = self.ws.paint
        k = getattr(i, "last_key", 0) or 0
        if pe is None:
            self._ekey.hit(k)          # keep the edge in sync even with no editor
            return
        if not self._ekey.hit(k):
            return
        if k == 0x1A:
            pe.undo()
            return
        if k == 0x19:
            pe.redo()
            return
        if k in (0x71, 0x51, 0x77, 0x57, 0x65, 0x45):   # Q, and W or E: the sprite before, after
            pe.select(-1 if k in (0x71, 0x51) else 1)
            self.ws.mark_dirty()
            return
        op = _KEY_OPS.get(k)
        if op == "more":
            self._open_more()
        elif op is not None:
            self._do_tool(op)
            self.ws.mark_dirty()

    def handle_pointer(self, px, py, click):
        ws = self.ws
        # SYSTEM coords (#39 step 3): the editor draws on the system canvas at
        # native size, so it hit-tests the raw pointer -- no _game_xy translation.
        # The Editor's lent zone (Stage 4) claims its taps first.
        if click and ws.bar_layer.handle_bar_tap("menu", px, py):
            self._pop = False
            return True
        if click and not self._pop:
            k = self._flag_at(px, py)
            if k is not None:
                self._toggle_flag(k)
                return True
        if self._pop:
            # The pickers hold the pointer while open: a press outside them
            # closes them (ui.popover answers it), and a release is the tap
            # their sheet reads its tile from.
            down = bool(ws.pointer.down)
            if self._host.point(px, py, click, down):
                if not click and not down:
                    self._pop_tap = (px, py)
                ws.mark_dirty()
            return True
        # A tap (click) routes through _paint_click (grid OR buttons). A held drag with
        # no fresh click keeps drawing on the grid so press-and-move draws a continuous
        # stroke / grows a shape's preview -- the same path for a host mouse drag and a
        # device touch drag (both = pointer.down + moving position). Releasing commits
        # the stroke/shape/selection (#30/#90).
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
        if pe is None or not _in(px, py, lay.pg_area):
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
        if pe.tool == pe.STAMP:
            cell = self._paint_grid_cell(px, py)   # one stamp a press, its top-left here
            if cell is None:
                return False
            pe.paste(cell[0], cell[1])
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
                self._cur[0], self._cur[1] = cell
            return
        if self._paint_stroke(px, py):
            self._follow(px, py)

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
                pe.clear_selection()           # a tap drops the selection
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
            self._follow(px, py)
            return
        tid = self._tool_at(px, py)            # a #90 tool button (mode/undo/transform)?
        if tid is not None:
            self._do_tool(tid)
            return
        if _in(px, py, lay.sw_area):           # pick a palette color
            idx = ((py - lay.sw_y0) // lay.sw) * lay.sw_cols + ((px - lay.sw_x0) // lay.sw)
            if 0 <= idx < 16:
                pe.color = idx
        elif _in(px, py, lay.spr_prev):
            pe.select(-1)
        elif _in(px, py, lay.spr_next):
            pe.select(1)
        elif lay.map_rect is not None and _in(px, py, lay.map_rect):
            self._map_pick(px, py)             # a sprite of the map becomes the sprite
        elif _in(px, py, lay.size_btn) or _in(px, py, lay.caption):
            self._open_pickers()               # the sprite and its size, a popover
        elif _in(px, py, lay.get_btn):
            ws.share_tile_get()              # import the tile from the shared sheet
        elif _in(px, py, lay.put_btn):
            ws.share_tile_put()              # save the tile to the shared sheet
        elif (getattr(lay, "files_btn", None) is not None
              and _in(px, py, lay.files_btn)):
            ws.send_sprites_to_files()       # export the sheet to files/sprites/ (#108)

    # -- tool row (#90) ------------------------------------------------------

    def _tool_at(self, px, py):
        """The tool id under (px, py), or None -- hit-tested over the tool row."""
        btns = getattr(self.layout, "tool_btns", None)
        if btns is None:
            return None
        for i in range(len(btns)):
            if _in(px, py, btns[i]):
                return _TOOLS[i]
        return None

    def _do_tool(self, tid):
        """Dispatch a tool-row tap to the PaintEditor verb (#90). Every action is
        touch-reachable here; undo/redo moved to the ONE bar pair (#111)."""
        pe = self.ws.paint
        if pe is None:
            return
        if tid in _TOOL_MODE:                  # pen/fill/select/stamp
            pe.set_tool(_TOOL_MODE[tid])
        elif tid == "shape":                   # the last shape, else the next one
            if pe.tool in _SHAPES:
                self._shape = _SHAPES[(_SHAPES.index(pe.tool) + 1) % len(_SHAPES)]
            pe.set_tool(self._shape)
        elif tid == "more":
            self._open_more()
        elif tid == "copy":
            pe.copy_selection()
        elif tid == "cut":
            pe.cut_selection()
        elif tid == "paste":
            self._paste_default(pe)
        elif tid == "erase":
            pe.toggle_erase()
        elif tid == "mirror":
            pe.toggle_mirror()
        elif tid == "fliph":
            pe.flip_h()
        elif tid == "flipv":
            pe.flip_v()
        elif tid == "rot":
            if pe.rotate() is False:
                self.ws.paint_status = "Rotate needs a square"
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

    def _draw_grid_overlay(self, cv, lay, pe, gx0, gy0, cell):
        """Overlay the active selection marquee and the live shape/select drag preview
        on the zoomed grid (#90). The committed selection and a live SELECT drag
        are one two-toned ring (design review R4-02): a line of `ink` on and
        outside the box's grid lines, then one of `surface` within, as the
        colour in use and the map's sprite are marked -- never a palette
        colour, so one tone reads against the grid's lines and any pixel in
        every theme. A live RECT/LINE/OVAL drag stamps the shape's cells in the
        current ink with a white outline so it reads even when ink == background.
        Called after the grid pixels + tile boundaries; a no-selection / no-drag editor
        draws nothing (baseline parity)."""
        NAMES = self._NAMES
        th = self.ws.theme_colors
        t = 2 if lay.desk else lay.fs
        lines = 1 if cell >= 6 else 0

        def ring(x0, y0, x1, y1):
            x = gx0 + x0 * cell
            y = gy0 + y0 * cell
            w = (x1 - x0 + 1) * cell
            h = (y1 - y0 + 1) * cell
            # on the box's own grid lines, where the grid draws them, and out
            for i in range(t):
                cv.rectb(x - 1 - i, y - 1 - i, w + 2 - lines + 2 * i,
                         h + 2 - lines + 2 * i, th["ink"])
            for i in range(t):
                cv.rectb(x + i, y + i, w - lines - 2 * i, h - lines - 2 * i,
                         th["surface"])

        if pe.sel is not None:
            ring(pe.sel[0], pe.sel[1], pe.sel[2], pe.sel[3])
        if self._shape_start is not None and self._shape_end is not None:
            s, e = self._shape_start, self._shape_end
            if pe.tool == pe.SELECT:
                x0 = s[0] if s[0] < e[0] else e[0]
                x1 = s[0] if s[0] > e[0] else e[0]
                y0 = s[1] if s[1] < e[1] else e[1]
                y1 = s[1] if s[1] > e[1] else e[1]
                ring(x0, y0, x1, y1)
            else:
                ink = 0 if pe.erase else pe.color
                for (x, y) in pe.shape_points(s[0], s[1], e[0], e[1]):
                    cv.rect(gx0 + x * cell, gy0 + y * cell, cell, cell, ink)
                    if cell >= 6:
                        cv.rectb(gx0 + x * cell, gy0 + y * cell, cell, cell,
                                 NAMES["white"])

    # -- draw ----------------------------------------------------------------

    def _draw_paint(self):
        ws = self.ws
        self._sync_layout()
        pe = ws.paint
        # Edit the editor's OWN sheet (the cart's sprites).
        sheet = pe.sheet if pe is not None else ws.project.sheet
        self._draw_look(sheet)
        if self._pop == "more" and pe is not None:
            self._draw_more()
        elif self._pop and pe is not None:
            self._draw_pickers()

    # -- the 2026-10 look ---------------------------------------------------------

    def _cursor_xy(self, pe, gx0, gy0, cell):
        c = self._cur
        d = pe.dim - 1
        c[0] = min(c[0], d)
        c[1] = min(c[1], d)
        return gx0 + c[0] * cell, gy0 + c[1] * cell

    def _icon_draw(self, kind, rect, ink, cv):
        """A glyph_draw for the look's buttons and tools: `kind` (an icon set
        slot, a glyph) from the theme's icon set where it has the slot, else
        the glyph table's -- `ui.icon`'s order."""
        slot, glyph = kind if isinstance(kind, tuple) else (kind, kind)
        if slot is not None and self.ws.draw_icon(slot, rect, ink, cv):
            return
        _ui_draw.glyph(cv, glyph, rect, ink, 2 if self.layout.desk else None)

    def _stepper(self, cv, th, rect, kind):
        """An icon-only button: the theme's button with icon `kind` (as
        `_icon_draw` takes it) centred on it, at the box and in the ink the
        button gives its glyph (a button lays its glyph out before a label,
        so with none the glyph would sit left of centre)."""
        got = []
        _ui_draw.button(cv, th, rect, "", "normal", glyph=kind,
                        glyph_draw=lambda k, r, ink, c: got.append((r, ink)))
        if got:
            (_x, y, w, h), ink = got[0]
            self._icon_draw(kind, (rect[0] + (rect[2] - w) // 2, y, w, h), ink, cv)

    def _checker(self, cv, x, y, w, h, s, greys):
        """Transparency's grey checker over (x, y, w, h) in squares of s."""
        _checker(cv, x, y, w, h, s, greys[0], greys[1])

    def _draw_look(self, sheet):
        """The sprite tab in the theme's roles (docs/look_2026-10.md step 24):
        the grid on the grey checker where a pixel is clear, the ring cursor,
        the current mark on the tool and the colour, and the column's parts as
        the theme's buttons with its icons."""
        ws = self.ws
        cv = ws.sys_canvas
        lay = self.layout
        pe = ws.paint
        th = ws.theme_colors
        fs = lay.fs
        greys = ws.look.live.fixed[:2]
        keyline, white = ws.look.live.fixed[2:]
        cv.rect(*(lay.body_fill + (th["surface"],)))
        n = pe.n if pe is not None else 0
        if not lay.pane:
            cv.print("Sprite " + str(n),
                     lay.title_xy[0], lay.title_xy[1], th["ink_dim"], 1)
        if pe is None or sheet is None:
            return
        # The grid: each pixel a cell, a clear one the checker, the cells
        # parted by the theme's border where they are big enough to part.
        dim, span = pe.dim, lay.pg_span
        gx0, gy0 = lay.pg_x0, lay.pg_y0
        cell = span // dim
        if cell < 1:
            cell = 1
        ox, oy = sheet.tile_origin(pe.n)
        lines = cell >= 6
        inner = cell - 1 if lines else cell
        cs = max(3 * fs, cell // 3)
        if lines:
            cv.rect(gx0 - 1, gy0 - 1, span + 1, span + 1, th["border"])
        # The cells are disjoint, so the coloured ones are the quads of one
        # fill_rects, the clear ones' light ground those of another, and the
        # clear ones' dark squares follow: the pixels of a rect or a checker a
        # cell. (A quad's own colour is a palette index; the ground may be the
        # theme's, which only a call's colour carries.)
        n2 = 5 * dim * dim
        q = self._grid_q
        if q is None or len(q[0]) < n2:
            q = self._grid_q = (array("h", bytes(2 * n2)), array("h", bytes(2 * n2)))
        cq, gq = q
        pix = sheet.pix
        sw_ = sheet.w
        inside = ox + dim <= sw_ and oy + dim <= sheet.h
        k = g = 0
        for ly in range(dim):
            y = gy0 + ly * cell
            base = (oy + ly) * sw_ + ox
            for lx in range(dim):
                x = gx0 + lx * cell
                v = pix[base + lx] if inside else sheet.pget(ox + lx, oy + ly)
                if v:
                    cq[k] = x
                    cq[k + 1] = y
                    cq[k + 2] = inner
                    cq[k + 3] = inner
                    cq[k + 4] = v
                    k += 5
                else:
                    gq[g] = x
                    gq[g + 1] = y
                    gq[g + 2] = inner
                    gq[g + 3] = inner
                    g += 5
        if k:
            cv.fill_rects(cq, k // 5)
        if g:
            cv.fill_rects(gq, g // 5, 0, 0, greys[1])
            dark = greys[0]
            sq = _checker_quads(inner, inner, cs)
            for i in range(0, g, 5):
                for a in sq:
                    cv.fill_rects(a, -1, gq[i], gq[i + 1], dark)
        if pe.size > 1:
            tpx = span // pe.size
            for t in range(1, pe.size):
                cv.rect(gx0 + t * tpx - 1, gy0, 1, span, th["ink_dim"])
                cv.rect(gx0, gy0 + t * tpx - 1, span, 1, th["ink_dim"])
        self._draw_grid_overlay(cv, lay, pe, gx0, gy0, cell)
        # The cursor, under an input with keys (touch shows no focus, and a
        # d-pad or trackball move brings it back): the theme's ring around
        # a black keyline, on a mat of `surface` from the keyline to a pixel
        # past the ring's reach, so it reads on a cell of its own colour.
        if self._keys():
            x, y = self._cursor_xy(pe, gx0, gy0, cell)
            box = (x - 1, y - 1, cell + 1, cell + 1)
            cv.rectb(box[0], box[1], box[2], box[3], keyline)
            for o in range(1, _ui_draw.mark_reach("part") + 2):
                cv.rectb(box[0] - o, box[1] - o, box[2] + 2 * o, box[3] + 2 * o,
                         th["surface"])
            _ui_draw.focus(cv, th, box, "part", ring=True)
        # The palette: colour 0 the checker under a white stroke, the
        # colour in use two-toned at its edge -- a line of `ink`, then one of
        # `surface` within -- never a palette colour, so one tone reads
        # against any swatch beside or under it, and the swatch shows.
        sw = lay.sw
        for idx in range(16):
            x = lay.sw_x0 + (idx % lay.sw_cols) * sw
            y = lay.sw_y0 + (idx // lay.sw_cols) * sw
            if idx:
                cv.rect(x, y, sw, sw, idx)
            else:
                self._checker(cv, x, y, sw, sw, max(3 * fs, sw // 4), greys)
                cv.line(x + sw - 1, y, x, y + sw - 1, white)
        x = lay.sw_x0 + (pe.color % lay.sw_cols) * sw
        y = lay.sw_y0 + (pe.color // lay.sw_cols) * sw
        for r in range(2 * fs):
            cv.rectb(x + r, y + r, sw - 2 * r, sw - 2 * r,
                     th["ink"] if r < fs else th["surface"])
        # The column: the sprite before and after (the icon set's chevrons,
        # never the font's less-than and greater-than signs) with the Size
        # caption beside them (a tap opens the pickers, as on the caption over
        # the grid), the sprite map, then load, save and to-Files.
        self._stepper(cv, th, lay.spr_prev, ("back", "arr_l"))
        self._stepper(cv, th, lay.spr_next, ("forward", "arr_r"))
        num = str(n)
        nx, ny, nw, nh = lay.num_rect
        cv.print(num, nx + (nw - _ui_draw.print_width(num, fs)) // 2, lay.size_xy[1],
                 th["ink"], 1)
        times = "\u00d7" if _ui.has_symbols() else "x"
        cv.print(("%d%s%d" if lay.pane else "Size %d%s%d") % (pe.size, times, pe.size),
                 lay.size_xy[0], lay.size_xy[1], th["ink_dim"], 1)
        if lay.pane:
            _ui_draw.button(cv, th, lay.pal_chip, "64", "normal")
            self._draw_flags(cv, lay, pe, th)
            self._draw_preview(cv, lay, pe, sheet, th)
            self._draw_tools_look()
            self._draw_hints(cv, lay, pe, th)
            return
        self._draw_map(cv, lay, pe, sheet, th, greys)
        draw = self._icon_draw
        words = lay.btn_words
        for rect, word, kind in ((lay.get_btn, "Load", ("folder_open", "get")),
                                 (lay.put_btn, "Save", ("save", "put")),
                                 (lay.files_btn, "To Files", ("export", "folder"))):
            if words:
                _ui_draw.button(cv, th, rect, word, "normal", glyph=kind,
                                glyph_draw=draw)
            else:
                self._stepper(cv, th, rect, kind)
        if ws.paint_status:
            cv.print(_ui_draw.print_cut(ws.paint_status, lay.status_w, fs),
                     lay.status_xy[0], lay.status_xy[1], th["ink_dim"], 1)
        self._draw_tools_look()
        self._draw_hints(cv, lay, pe, th)

    # PICO-8's flag colours, MOY64's first sixteen: red, orange, yellow,
    # green, blue, indigo, pink, peach.
    _FLAG_INKS = (8, 9, 10, 11, 12, 13, 14, 15)

    def _draw_flags(self, cv, lay, pe, th):
        """The sprite's eight flags as toggles (Q-03): a disc in its colour
        where it is set, a ring where it is clear, then the word."""
        flags = self.ws.project.flags
        v = flags[pe.n] if flags is not None and pe.n < len(flags) else 0
        for k, (x, y, w, h) in enumerate(lay.flag_cells):
            d = max(8, min(w, h) - (14 if h >= 36 else 8))
            ox, oy = x + (w - d) // 2, y + (h - d) // 2
            if v >> k & 1:
                cv.rrect(ox, oy, d, d, d // 2, self._FLAG_INKS[k])
            else:
                cv.rrect(ox, oy, d, d, d // 2, None, th["ink_dim"])
        if lay.flag_cells:
            x, y, w, h = lay.flag_cells[-1]
            line = _ui_draw.font("ui")[2] * lay.fs
            cv.print("Flags", x + w + 12, y + (h - line) // 2, th["ink_dim"], 1)

    def _flag_at(self, px, py):
        for k, r in enumerate(self.layout.flag_cells):
            if _in(px, py, r):
                return k
        return None

    def _toggle_flag(self, k):
        """A flag tapped: the sprite's bit flips, and reaches the running
        game at its next frame (`ProjectStore.flag_edits`); it is saved with
        the sheet (`Project.commit_sprites`)."""
        ws = self.ws
        flags = ws.project.flags
        pe = ws.paint
        if flags is None or pe is None or pe.n >= len(flags):
            return
        flags[pe.n] ^= 1 << k
        ws.project.flags_dirty = True
        if ws.sheet is not None:
            ws.sheet.dirty = True
        ws.mark_dirty()

    def _draw_preview(self, cv, lay, pe, sheet, th):
        """The sprite as the game draws it: at 1x, 2x and 4x, then the words."""
        if lay.preview is None:
            return
        x, y, w, h = lay.preview
        line = _ui_draw.font("ui")[2] * lay.fs
        dim = pe.dim
        # The sprite's span on the sheet (tile_span_image's, clamped at its
        # edge), each size one native stretch of it with colour 0 clear.
        ox, oy = sheet.tile_origin(pe.n)
        T = sheet.TILE
        pw = min(pe.size, (sheet.w - ox) // T) * T
        ph = min(pe.size, (sheet.h - oy) // T) * T
        for z in (1, 2, 4):
            side = dim * z
            if x + side > lay.preview[0] + w or side > h + dim * 2:
                break
            cv.sspr(sheet, ox, oy, pw, ph, x, y + h - side, pw * z, ph * z, 0)
            cv.print("%d\u00d7" % z if _ui.has_symbols() else "%dx" % z,
                     x + side + 4, y + h - line, th["ink_dim"], 1)
            x += side + 4 + _ui_draw.print_width("4x", lay.fs) + 14
        cv.print("in the game", x, y + h - line, th["ink_dim"], 1)

    @staticmethod
    def _map_ring(lay):
        """The width of each of the two lines that mark the map's sprite."""
        return 2 if lay.desk else lay.fs

    def _map_geom(self, lay, pe, sheet):
        """The sprite map's (x0, y0, k, first row, rows): the sheet's sprites at
        the largest whole scale `k` that holds a row of them across the map
        with the sprite's mark beside it, as many rows as fit, from the row
        that keeps the sprite (and its size's span) in view, from the map's
        left, on the column's edge (design review R4-17). The mark lies
        outside the sprite, in the column's gutters."""
        x, y, w, h = lay.map_rect
        cols = sheet.cols
        k = max(1, (w - 2 * self._map_ring(lay)) // (cols * sheet.TILE))
        tile = sheet.TILE * k
        rows = min(sheet.rows, max(1, h // tile))
        row = pe.n // cols
        first = row - (rows - pe.size) // 2
        first = max(0, min(first, sheet.rows - rows))
        return x, y, k, first, rows

    def _draw_map(self, cv, lay, pe, sheet, th, greys):
        """The sprite map (owner, 2026-10-19): the sheet around the sprite
        on the grey checker, a fade at an edge past which the sheet goes on,
        the sprite and its size's span outlined outside its pixels -- a line
        of `surface` around it, then one of `ink`, as the colour in use is
        marked, so the sprite shows whole (design review R4-03) -- and a tap
        on a sprite makes it the sprite."""
        if lay.map_rect is None or lay.map_rect[3] <= 0:
            return
        x0, y0, k, first, rows = self._map_geom(lay, pe, sheet)
        tile = sheet.TILE * k
        cols = sheet.cols
        self._checker(cv, x0, y0, cols * tile, rows * tile, max(2, 2 * k), greys)
        cv.sspr(sheet, 0, first * sheet.TILE, cols * sheet.TILE, rows * sheet.TILE,
                x0, y0, cols * tile, rows * tile, 0)
        fs = lay.fs
        fh = min(8 * fs, rows * tile // 2)
        if first > 0:
            _ui_draw.fade(cv, (x0, y0, cols * tile, fh), th["surface"], True)
        if first + rows < sheet.rows:
            _ui_draw.fade(cv, (x0, y0 + rows * tile - fh, cols * tile, fh),
                          th["surface"])
        n = pe.n
        sx = x0 + (n % cols) * tile
        sy = y0 + (n // cols - first) * tile
        span = pe.size * tile
        o = self._map_ring(lay)
        for i in range(1, 2 * o + 1):
            c = th["surface"] if i <= o else th["ink"]
            cv.rectb(sx - i, sy - i, span + 2 * i, span + 2 * i, c)

    def _map_pick(self, px, py):
        """A tap on the sprite map: the sprite under it becomes the sprite."""
        pe = self.ws.paint
        sheet = pe.sheet if pe is not None else None
        if sheet is None:
            return
        x0, y0, k, first, rows = self._map_geom(self.layout, pe, sheet)
        tile = sheet.TILE * k
        c = (px - x0) // tile
        r = (py - y0) // tile
        if 0 <= c < sheet.cols and 0 <= r < rows:
            pe.n = (first + r) * sheet.cols + c
            pe._clamp_size()

    def _draw_hints(self, cv, lay, pe, th):
        """The hint strip along the content's foot: the operations' keys of
        the input in use, the operations on a selection first while one is
        held, as many pairs as the strip holds (none under touch); while a
        popover is open, only the key that closes it."""
        if lay.hints is None:
            return
        if _ui_draw.on_bar():
            items = _ui_draw.BAR_KEYS
        elif self._pop:
            items = _HINTS_POP
        elif pe.sel is None and lay.pane:
            items = _HINTS_PANE
        else:
            items = _HINTS_SEL if pe.sel is not None else _HINTS
        x, y, w, h = lay.hints
        fs = lay.fs
        band = (x + (8 if lay.desk else 2 * fs), y, w - (16 if lay.desk else 4 * fs), h)
        spans = _ui_draw.hints(cv, th, band, items, lay.desk, draw=False)
        n = 0
        for x0, x1 in spans:
            if x1 > band[0] + band[2]:
                break
            n += 1
        if n:
            _ui_draw.hints(cv, th, band, items[:2 * n], lay.desk)

    @staticmethod
    def _shape_of(pe):
        return pe.tool if pe.tool in _SHAPES else None

    def _tool_on(self, pe, tid):
        """Whether tool button `tid` wears the current mark: the tool in use
        (shape: any of the shapes), the more button while its menu is open."""
        if tid == "shape":
            return pe.tool in _SHAPES
        if tid == "more":
            return self._pop == "more"
        return tid in _TOOL_MODE and pe.tool == _TOOL_MODE[tid]

    def _draw_tools_look(self):
        """The tool row as icons in the chrome's ink, one role for every
        enabled tool (design review R3-11): the tool in use and the more
        button while its menu is open on the current mark, the shape button
        wearing the shape in use (else the next it selects), and stamp in the
        disabled ink while there is nothing to stamp."""
        ws = self.ws
        cv = ws.sys_canvas
        pe = ws.paint
        lay = self.layout
        th = ws.theme_colors
        fs = lay.fs
        btns = lay.tool_btns
        r = 8 if lay.desk else 6 * fs
        for i in range(len(btns)):
            tid = _TOOLS[i]
            x, y, w, h = btns[i]
            plate = (x + fs, y, w - 2 * fs, h - 4 * fs)
            ink = th["chrome_ink"]
            if self._tool_on(pe, tid):
                got = _ui_draw.current(cv, th, plate, r)
                ink = th["ink"] if got is None else got
            elif tid == "stamp" and not pe.has_clip:
                ink = _ui_draw.disabled_ink(th)
            box = min(lay.icon, w, plate[3])
            rect = (x + (w - box) // 2, y + (plate[3] - box) // 2, box, box)
            if tid == "shape":
                kind = _SHAPE_ICON[self._shape_of(pe) or self._shape]
            else:
                kind = (_TOOL_ICON.get(tid), _TOOL_GLYPH[tid])
            self._icon_draw(kind, rect, ink, cv)

    def _open_more(self):
        """Open the more menu (the ⋯ button, M on a keyboard, Y on a
        gamepad), or close it when it is open."""
        if self._pop == "more":
            self._pop = False
            self.ws.mark_dirty()
            return
        self._open_pickers()
        self._pop = "more"
        self._shifts = False

    def _more_rows(self, desk):
        """The more menu's rows, [(id, label, slot)]: `_OPS` on a desk; on a
        handheld, whose screen holds seven rows under its bar, the four
        shifts fold into one "Shift" row that opens them as a page of their
        own (`_shifts`)."""
        if self._shifts:
            return [op for op in _OPS if op[0] in _SHIFT_OPS]
        if desk:
            return list(_OPS)
        out = []
        for op in _OPS:
            if op[0] == _SHIFT_OPS[0]:
                out.append(("shifts", "Shift", "arrow_right"))
            elif op[0] not in _SHIFT_OPS:
                out.append(op)
        return out

    def _draw_more(self):
        """The more menu, a popover of the public UI API's hung from the ⋯
        button: the operations as a menu, each with its icon and, under an
        input with keys, the key it answers to at the row's right; clear a
        danger row at its foot. A pick runs it and closes the menu (the
        handheld's Shift opens the shifts in its place); a press outside, or
        Esc, closes it."""
        ws = self.ws
        cv = ws.sys_canvas
        pe = ws.paint
        lay = self.layout
        th = ws.theme_colors
        fs = lay.fs
        settled = self._host.begin(cv, th, lay.body_fill, lay.desk)
        keys = _OP_KEYS.get(_ui_draw.hint_kind(), {})
        rows = self._more_rows(lay.desk)
        # the operations, then the danger rows set apart under a rule
        parts = ([], [])
        for i in range(len(rows)):
            oid, label, slot = rows[i]
            if oid == "mirror" and pe.mirror:
                label = "Turn off mirror"
            k = keys.get(oid)
            parts[oid == "clear"].append((i, label + ("\t" + k if k else ""), slot))
        _ui.at(*lay.more_btn)
        shown = _ui.popover("Shift" if self._shifts else "More",
                            260 if lay.desk else 150 * fs)
        pick = -1
        for part, kind in zip(parts, ("", "danger")):
            if part:
                got = _ui.menu("", [p[1] for p in part], [p[2] for p in part],
                               kinds=[kind] * len(part))
                if got >= 0:
                    pick = part[got][0]
        _ui.popover()
        if pick >= 0 and rows[pick][0] == "shifts":
            self._shifts = True
            self._host.reset()
            ws.request_frame()
            return
        if pick >= 0:
            self._do_tool(rows[pick][0])
            shown = False
        if not shown:
            self._pop = False
            ws.request_frame()
        elif not settled:
            ws.request_frame()

    def _open_pickers(self):
        if self._host is None:
            self._host = _ui_draw.AppHost()
        else:
            self._host.reset()
        self._pop = "pickers"
        self._pop_tap = None
        self.ws.mark_dirty()

    def _draw_pickers(self):
        """The pickers, a popover of the public UI API's hung from the
        preview: the size as a seg, and the sheet in a part, whose tapped
        tile becomes the sprite. A press outside, or Esc, closes it."""
        ws = self.ws
        cv = ws.sys_canvas
        pe = ws.paint
        lay = self.layout
        th = ws.theme_colors
        fs = lay.fs
        sheet = pe.sheet
        settled = self._host.begin(cv, th, lay.body_fill, lay.desk)
        k = 2 if lay.desk else fs
        m = 2 * fs
        tile = sheet.TILE * k
        pw = sheet.cols * tile + 2 * m
        ph = sheet.rows * tile + 2 * m
        _ui.at(*lay.size_btn)
        shown = _ui.popover("Sprites", max(pw + (48 if lay.desk else 24 * fs),
                                           300 if lay.desk else 200 * fs))
        i = _ui.seg("Size", _SIZE_NAMES, pe.size - 1)
        if 0 <= i < len(pe.SIZES) and pe.SIZES[i] != pe.size:
            pe.size = pe.SIZES[i]
            pe._clamp_size()
        x, y, w, h, st = _ui.part("Sheet", pw, ph)
        if w > 0 and h > 0:
            x0, y0 = x + m, y + m
            cv.sspr(sheet, 0, 0, sheet.w, sheet.h, x0, y0, sheet.w * k, sheet.h * k, 0)
            tx, ty = sheet.tile_origin(pe.n)
            span = pe.size * tile
            cv.rectb(x0 + tx * k - 1, y0 + ty * k - 1, span + 2, span + 2, th["value"])
            tap = self._pop_tap
            if st & _ui.ACTIVATED and tap is not None:
                col = (tap[0] - x0) // tile
                row = (tap[1] - y0) // tile
                if 0 <= col < sheet.cols and 0 <= row < sheet.rows:
                    pe.n = row * sheet.cols + col
                    pe._clamp_size()
                    shown = False
        _ui.popover()
        self._pop_tap = None
        if not shown:
            self._pop = False
            ws.request_frame()
        elif not settled:
            ws.request_frame()
