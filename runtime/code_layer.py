"""The Python code editor (#24/#39), extracted from Workstation (runtime/console.py)
as its own Layer -- docs/shell_layers_refactor_v1.md Phase 2 (the last surface).

The full-screen code view: the responsive text area (drawn on the SYSTEM canvas at the
CodeLayout geometry), the syntax-highlighted line rendering, the coding-symbol palette
(the T-Deck keyboard can't type `=[]{}<>%`, so a tappable palette supplies them), and
the touch/keyboard editing.

Stage-4 bar rollout (docs/shell_ux_technical_plan_v1.md): the code editor's OWN top
band -- the cart title + the RUN/SAVE/CLOSE icons -- is GONE. draw() now paints the
unified zoned bar (the tab ladder + PLAY + SAVE + X) over the top 18px like every other
editor tab, so the body below it is fullscreen text + the symbol palette with no chrome
of its own. PLAY runs (EditorApp.leave), SAVE persists (EditorApp.save_current ->
ws.save_code), X exits -- all in the bar. The text area already began at y0 == 18 (below
the bar), so nothing shifted.

Boundary (the anti-spaghetti line, per the doc): the shared CodeEditor handle stays on
Workstation -- `ws.editor` (device/test-pinned, ~38 refs, exactly like ws.paint), built
by ws.set_menu_view. The SAVE/RUN API + the code-error state stay on ws too:
ws.save_code / ws.run_code (device/test-pinned, now reached via the bar's SAVE/PLAY),
ws.code_err / code_err_row / crash_line + _set_code_error / _mark_code_error /
_cart_has_handwritten_code, and ws.code_layout (the CodeLayout). CodeLayer READS
ws.editor + code_layout + the error state and DISPATCHES to the bar; it owns only the
code-UI state (the keyboard edge tracker _ekey_prev, the drag-scroll origin _drag, the
highlight memo _hl_cache). ws.nav (the trackball-caret handler, called by both the host
+ device input drivers) stays on ws -- it just reads ws.editor. The code-only constants
+ the MicroPython-safe syntax highlighter live here (single source; console.py imports
the constants back for its CodeLayout + the crash panel + tests). `NAMES` / `_in`
injected.
"""
from editors import CodeEditor

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui


# -- code-editor geometry (single source; console.py imports these back) ------
# The area/line-height feed console's CodeLayout (responsive) + the crash panel; the
# button rects + symbol palette are the fixed 320x240 baseline CodeLayout starts from.
_CODE_X0 = 4
_CODE_Y0 = 18
_CODE_LH = 10
_CODE_AREA = (_CODE_X0, _CODE_Y0, CodeEditor.COLS * 8, CodeEditor.ROWS * _CODE_LH)
# The T-Deck keyboard has no `=`/`[]`/`{}`/`<>`/`%` keys, so the code editor shows a
# tappable palette of them along the bottom.
_CODE_SYMBOLS = "=()[]{}<>:;,.\"_%"
_SYM_Y = 220
_SYM_H = 20
_SYM_CELL = 20
_SYM_AREA = (0, _SYM_Y, _SYM_CELL * len(_CODE_SYMBOLS), _SYM_H)


# --- code-editor syntax highlighting (#24) ---------------------------------
# A tiny, MicroPython-safe tokenizer: scans one source line char-by-char and returns a
# per-character list of MOY64 palette indices, so the code view draws colored runs
# without any re/tokenize dependency (those are heavy/absent on the device). Token
# classes map to:
_HL_TEXT = 6        # light_grey -- identifiers, operators, punctuation (default)
_HL_KEYWORD = 12    # blue
_HL_STRING = 11     # green
_HL_NUMBER = 9      # orange
_HL_COMMENT = 5     # dark_grey
_HL_BUILTIN = 14    # pink -- the cart drawing verbs stand out

_HL_KEYWORDS = (
    "False", "None", "True", "and", "as", "assert", "break", "class",
    "continue", "def", "del", "elif", "else", "except", "finally", "for",
    "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", "not",
    "or", "pass", "raise", "return", "try", "while", "with", "yield",
)
# Cart-API verbs + the common builtins a kid actually types. Keep roughly in
# sync with make_api (host_app / moy_runtime); an extra name here is harmless.
_HL_BUILTINS = (
    "cls", "pix", "pset", "line", "rect", "rectb", "circ", "circb", "spr",
    "map", "mget", "mset",
    "print", "btn", "btnp", "touch", "mouse", "key", "keyp", "time", "pmem",
    "cfg", "col", "rnd", "flr", "abs", "min",
    "max", "sin", "cos", "range", "len", "int", "str", "float", "round", "sqrt",
)


def _is_alpha(ch):
    return ch == "_" or ("a" <= ch <= "z") or ("A" <= ch <= "Z")


def _highlight(line):
    """Return a list of palette indices, one per character of `line` (#24).
    Hand-rolled scanner -- no regex/tokenize, so it runs under MicroPython."""
    n = len(line)
    out = [_HL_TEXT] * n
    i = 0
    while i < n:
        ch = line[i]
        if ch == "#":                          # comment to end of line
            while i < n:
                out[i] = _HL_COMMENT
                i += 1
            break
        if ch == '"' or ch == "'":             # string literal (single line)
            q = ch
            out[i] = _HL_STRING
            i += 1
            while i < n:
                out[i] = _HL_STRING
                if line[i] == "\\" and i + 1 < n:   # escape: consume next char too
                    i += 1
                    out[i] = _HL_STRING
                    i += 1
                    continue
                if line[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if "0" <= ch <= "9":                   # number literal
            while i < n and (("0" <= line[i] <= "9") or line[i] == "." or line[i] == "x"):
                out[i] = _HL_NUMBER
                i += 1
            continue
        if _is_alpha(ch):                      # identifier / keyword / builtin
            j = i
            while j < n and (_is_alpha(line[j]) or "0" <= line[j] <= "9"):
                j += 1
            word = line[i:j]
            if word in _HL_KEYWORDS:
                cl = _HL_KEYWORD
            elif word in _HL_BUILTINS:
                cl = _HL_BUILTIN
            else:
                cl = _HL_TEXT
            while i < j:
                out[i] = cl
                i += 1
            continue
        i += 1                                 # operator / punctuation / space
    return out


class CodeLayer:
    """The code editor content Layer (system domain, responsive #39): a full-screen
    text editor on the SYSTEM canvas. Owns the drawing + the code-UI state (keyboard
    edge / drag origin / highlight memo); reads ws.editor + ws.code_layout + the code-
    error state and dispatches SAVE/RUN/CLOSE to Workstation."""

    id = "code"
    domain = "system"

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect
        self._ekey_prev = 0           # last consumed keyboard byte (editor edge detect)
        self._drag = None             # last pointer pos during a code-view drag-scroll
        self._hl_cache = {}           # per-line syntax-highlight memo (#24)
        self._t = None                # per-draw tone map (set by _draw_code)

    def reset(self):
        """Reset the keyboard edge tracker (called by ws.set_menu_view when the editor
        is (re)built) so the first key press after opening registers."""
        self._ekey_prev = 0

    # -- Layer facets --------------------------------------------------------

    def draw(self, dt):
        self._draw_code()
        # The unified zoned bar (Stage 4 rollout): the tab ladder + PLAY + SAVE + X,
        # drawn LAST (chrome over the full-screen text) so the code editor shows the
        # SAME bar every other tab does. System canvas + responsive layout, like the
        # launcher/Settings bar. This replaces the code editor's old title + RUN/SAVE/
        # CLOSE band: PLAY/SAVE/X all live in the bar now.
        self.ws.bar_layer._draw_status_strip("menu")

    def handle_input(self, i):
        self._editor_input()           # keyboard is in text mode here
        return True

    def handle_pointer(self, px, py, click):
        # The code editor is responsive (#39 step 2): it draws on the SYSTEM canvas at
        # native size, so it hit-tests in SYSTEM coords (the raw pointer), NOT the
        # 320x240 game viewport. That's also the coord space the system-canvas zoned bar
        # draws in, so the bar tap goes straight through (no _game_xy translation).
        ws = self.ws
        lay = ws.code_layout
        # The unified bar's tab ladder + PLAY + SAVE + X claims its slice FIRST (Stage 4
        # rollout), before any code-body tap -- SAVE here dispatches to ws.save_code.
        if click and ws.bar_layer.handle_bar_tap("menu", px, py):
            return True
        self._code_drag(px, py)        # touch/mouse drag pans the viewport
        if click:
            if self._in(px, py, lay.sym_area) and ws.editor is not None:
                i = (px - lay.sym_area[0]) // lay.sym_cell  # tap a coding symbol
                if 0 <= i < len(_CODE_SYMBOLS):
                    ws.editor.key(ord(_CODE_SYMBOLS[i]))
            elif ws.editor is not None and self._in(px, py, lay.code_area()):
                ws.editor.place((px - lay.x0) // lay.cell,
                                (py - lay.y0) // lay.lh)
        return True

    # -- input ---------------------------------------------------------------

    def _editor_input(self):
        # Feed the typed key to the editor, one insert per physical press: the
        # keyboard reports the byte for the frame it is down then 0, so acting on
        # the 0->key edge (key != previous) avoids autorepeat.
        ws = self.ws
        if ws.editor is None:
            return
        k = ws.input.last_key
        if k and k != self._ekey_prev:
            # Durable undo/redo (Stage 7): the code editor's keyboard shortcut for the
            # journal walk -- Ctrl+Z (0x1A) / Ctrl+Y (0x19). These control bytes are
            # never inserted as text (editor.key ignores them), so they can't corrupt
            # the buffer. DESIGN CALL FOR THE OWNER (spec Section 7): the on-DEVICE
            # affordance -- which T-Deck key combo, or an in-body control -- is
            # unresolved; this wires the host's Ctrl+Z/Y and ws.undo()/redo() are the
            # mechanism whatever device affordance the owner picks will drive.
            if k == 0x1A:
                ws.undo()
            elif k == 0x19:
                ws.redo()
            elif ws.editor.key(k):     # text changed -> drop the stale error marker
                ws.code_err = None
                ws.code_err_row = None
                ws.crash_line = None
        self._ekey_prev = k

    def _code_drag(self, px, py):
        # Touch/mouse drag inside the code area pans the viewport (content follows
        # the finger): drag down -> see earlier lines, drag right -> see left text.
        # SYSTEM coords + layout cell/line height (#39 step 2).
        ws = self.ws
        ed = ws.editor
        lay = ws.code_layout
        if ed is None or not ws.pointer.down or not self._in(px, py, lay.code_area()):
            self._drag = None
            return
        if self._drag is None:
            self._drag = (px, py)
            return
        drows = (py - self._drag[1]) // lay.lh
        dcols = (px - self._drag[0]) // lay.cell
        if drows or dcols:
            ed.scroll(-drows, -dcols)
            self._drag = (px, py)

    # -- draw ----------------------------------------------------------------

    # Light-surface syntax set (visual identity v1 Phase 3): the dark-background
    # highlight colors above don't read on the warm cream surface, so runs remap
    # through this at draw time (per RUN, not per char -- the _hl memo stays
    # untouched). Deep, §4.2-friendly hues: black text, deep teal keywords, dark
    # green strings, brown numbers, dim warm comments, dark purple cart verbs.
    _HL_LIGHT = {_HL_TEXT: 0, _HL_KEYWORD: 59, _HL_STRING: 3,
                 _HL_NUMBER: 4, _HL_COMMENT: 53, _HL_BUILTIN: 2}

    def _tones(self):
        """Per-draw color roles: frozen literals on the 320x240 baseline
        (byte-identical); theme tokens on the shelf tiers. The syntax remap only
        engages on a LIGHT surface (dark ink token), so the dark themes keep the
        shipped highlight set on their own panel color."""
        NAMES = self._NAMES
        if self.ws.code_layout._base:
            return {"bg": NAMES["black"], "caret": NAMES["yellow"],
                    "sym_bg": NAMES["dark_grey"], "sym_edge": NAMES["indigo"],
                    "sym_ink": NAMES["white"], "hl": None}
        th = self.ws.theme_colors
        if th.get("ink", NAMES["white"]) != 0:      # dark surface -> dark set
            return {"bg": th.get("surface", NAMES["black"]),
                    "caret": NAMES["yellow"],
                    "sym_bg": NAMES["dark_grey"], "sym_edge": NAMES["indigo"],
                    "sym_ink": NAMES["white"], "hl": None}
        return {"bg": th["surface"], "caret": th["ink"],
                "sym_bg": th.get("surface_alt", NAMES["dark_grey"]),
                "sym_edge": th["border"], "sym_ink": th["ink"],
                "hl": self._HL_LIGHT}

    def _draw_code(self):
        # Responsive (#39 step 2): the code editor draws on the SYSTEM canvas at
        # native size, so a bigger panel shows more lines + wider columns and a
        # bigger font scales the text. All positions come from CodeLayout (verbatim
        # baseline at 320x240/1x). The editor's COLS/ROWS already track the layout.
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        lay = ws.code_layout
        fs = lay.fs
        cell = lay.cell                          # on-screen char-cell width (8*fs)
        lh = lay.lh
        ed = ws.editor
        t = self._t = self._tones()
        cv.cls(t["bg"])                         # full-screen editor
        # The old title + RUN/SAVE/CLOSE top band is gone (Stage 4 rollout): the unified
        # bar (drawn after this in draw()) owns the top 18px -- PLAY runs, SAVE persists,
        # X exits, and the tab ladder switches views. The text area already starts at
        # y0 == 18 (below the bar), so the body is fullscreen with no chrome of its own.
        # code area (horizontal scroll: columns [left, left+COLS))
        if ed is not None:
            cols = ed.COLS
            vis = ed.visible_lines()
            errrow = ws.code_err_row
            for idx in range(len(vis)):
                y = lay.y0 + idx * lh
                full = vis[idx]
                on_err = errrow is not None and ed.top + idx == errrow
                if on_err:                      # inline error: gutter mark + underline (#24)
                    cv.rect(0, y, 3 * fs, 8 * fs, NAMES["red"])
                    cv.rect(lay.x0, y + 8 * fs, cols * cell, fs, NAMES["red"])
                seg = full[ed.left:ed.left + cols]
                segcols = self._hl(full)[ed.left:ed.left + cols]
                self._draw_code_runs(seg, segcols, y)
                if on_err and ws.code_err:      # short reason after the code, if it fits
                    mcol = len(seg) + 1
                    if mcol < cols - 2:
                        cv.print(ws.code_err[:cols - mcol],
                                 lay.x0 + mcol * cell, y, NAMES["red"], 1)
                if ed.top + idx == ed.row:      # caret on the cursor's line
                    vcol = ed.col - ed.left
                    if 0 <= vcol <= cols:
                        cv.rect(lay.x0 + vcol * cell, y, fs, 8 * fs, t["caret"])
        # Status band (Phase 3, shelf tiers): the mockup's "Ln 13, Col 1" strip.
        if lay.status_band is not None and ed is not None:
            issues = "1 ISSUE" if ws.code_err else "NO ISSUES"
            _ui.status_row(cv, ws.theme_colors, lay.status_band,
                           ("LN " + str(ed.row + 1) + ", COL " + str(ed.col + 1),
                            str(len(ed.lines)) + " LINES", issues))
        self._draw_symbols()

    def _hl(self, line):
        """Memoized per-line syntax highlight (#24). Lines recur every frame, so
        cache by text; bound the cache so a long edit session can't grow it."""
        cols = self._hl_cache.get(line)
        if cols is None:
            if len(self._hl_cache) > 400:
                self._hl_cache.clear()
            cols = _highlight(line)
            self._hl_cache[line] = cols
        return cols

    def _draw_code_runs(self, seg, segcols, y):
        """Draw one code line as runs of same-colored text (#24). On the SYSTEM
        canvas at the layout's char-cell width (8*fs), so it scales with the font."""
        cv = self.ws.sys_canvas
        lay = self.ws.code_layout
        x0, cell = lay.x0, lay.cell
        hl = (self._t or {}).get("hl")
        n = len(seg)
        i = 0
        while i < n:
            cl = segcols[i]
            j = i + 1
            while j < n and segcols[j] == cl:
                j += 1
            cv.print(seg[i:j], x0 + i * cell, y,
                     hl.get(cl, cl) if hl else cl, 1)
            i = j

    def _draw_symbols(self):
        # Tappable coding-symbol palette (supplies what the keyboard can't type). On
        # the SYSTEM canvas; cell + text scale with the layout/font (#39 step 2).
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        lay = self.ws.code_layout
        fs = lay.fs
        sc = lay.sym_cell
        sy = lay.sym_y
        sh = lay.sym_h
        t = self._t if self._t is not None else self._tones()
        for i in range(len(_CODE_SYMBOLS)):
            x = lay.sym_area[0] + i * sc
            cv.rect(x, sy, sc - 1, sh - 1, t["sym_bg"])
            cv.rectb(x, sy, sc - 1, sh - 1, t["sym_edge"])
            cv.print(_CODE_SYMBOLS[i], x + 6 * fs, sy + 6 * fs, t["sym_ink"], 1)
