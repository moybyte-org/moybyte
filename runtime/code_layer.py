"""The Python code editor (#24/#39), extracted from Workstation (runtime/console.py)
as its own Layer -- docs/shell_layers_refactor_v1.md Phase 2 (the last surface).

The full-screen code view: the responsive text area (drawn on the SYSTEM canvas at the
CodeLayout geometry), the syntax-highlighted line rendering, the coding-symbol palette
(the T-Deck keyboard can't type `=[]{}<>%`, so a tappable palette supplies them), and
the touch/keyboard editing.

Stage-4 bar rollout (docs/shell_ux_technical_plan_v1.md): the code editor's OWN top
band -- the cart title + the RUN/SAVE/CLOSE icons -- is GONE. draw() now paints the
unified zoned bar (the tab ladder + PLAY + X, no SAVE -- #111) over the top 18px like
every other editor tab, so the body below it is fullscreen text + the symbol palette
with no chrome of its own. PLAY runs (EditorApp.leave, itself now a hard-commit
trigger) and persists (EditorApp.save_current -> ws.save_code, #111: a tab switch
and every other exit path commit too, not just PLAY), X exits -- all in the bar. The
text area already began at y0 == 18 (below the bar), so nothing shifted.

Boundary (the anti-spaghetti line, per the doc): the shared CodeEditor handle stays on
Workstation -- `ws.editor` (device/test-pinned, ~38 refs, exactly like ws.paint), built
by ws.set_menu_view. The SAVE/RUN API + the code-error state stay on ws too:
ws.save_code / ws.run_code (device/test-pinned, save_code now reached via every exit
path rather than a bar tap, #111; run_code stays PLAY's own explicit-run entry point),
ws.code_err / code_err_row / crash_line + _set_code_error / _mark_code_error /
_cart_has_handwritten_code, and ws.code_layout (the CodeLayout). CodeLayer READS
ws.editor + code_layout + the error state and DISPATCHES to the bar; it owns only the
code-UI state (the keyboard edge tracker _ekey, the drag-scroll origin _drag, the
highlight memo _hl_cache). ws.nav (the trackball-caret handler, called by both the host
+ device input drivers) stays on ws -- it just reads ws.editor. The code-only constants
+ the MicroPython-safe syntax highlighter live here (single source; console.py imports
the constants back for its CodeLayout + the crash panel + tests). `NAMES` / `_in`
injected.
"""
from editors import CodeEditor, KeyEdge

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui

# The shared pre-literate glyph vocabulary (#89 icon pass): the TLS toggle + the tool
# palette row draw a 12x12 chrome glyph per button instead of the terse 2-3 char
# label. Looked up LAZILY (not a top-level import): chrome.py imports THIS module for
# its code-layout constants before it defines _GLYPHS, so a module-level
# `from chrome import _GLYPHS` here would be a circular import. By draw time chrome is
# fully loaded, so the cached lookup below resolves the dict once.
_GLYPHS = None


def _glyph_known(kind):
    """True if `kind` is a known chrome glyph (so a button draws the icon; else the
    text label is the fallback). Caches the _GLYPHS dict on first draw-time call."""
    global _GLYPHS
    if _GLYPHS is None:
        try:
            from chrome import _GLYPHS as _G
        except ImportError:  # pragma: no cover - direct host import
            from runtime.chrome import _GLYPHS as _G
        _GLYPHS = _G
    return kind in _GLYPHS


# -- code-editor geometry (single source; console.py imports these back) ------
# The area/line-height feed console's CodeLayout (responsive) + the crash panel; the
# button rects + symbol palette are the fixed 320x240 baseline CodeLayout starts from.
_CODE_X0 = 4
_CODE_Y0 = 18
_CODE_LH = 10
_CODE_AREA = (_CODE_X0, _CODE_Y0, CodeEditor.COLS * 8, CodeEditor.ROWS * _CODE_LH)
# The T-Deck keyboard has no `=`/`[]`/`{}`/`<>`/`%` keys, so the code editor shows a
# tappable palette of them along the bottom. A lua cart (#67 Phase 5) swaps in the
# Lua set: `~` joins (for `~=`; `..` is two taps of `.`) and the optional `;` --
# which Lua never needs -- makes room. SAME length, so the layout geometry (and
# the frozen 320x240 baseline for python carts) is untouched.
_CODE_SYMBOLS = "=()[]{}<>:;,.\"_%"
_LUA_SYMBOLS = "=~()[]{}<>:,.\"_%"
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
_HL_LUA_KEYWORDS = (
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return",
    "then", "true", "until", "while",
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


def _highlight(line, lua=False):
    """Return a list of palette indices, one per character of `line` (#24).
    Hand-rolled scanner -- no regex/tokenize, so it runs under MicroPython.
    `lua` (#67 Phase 5) switches the comment marker (`--`; `#` is Lua's length
    operator, NOT a comment) and the keyword set; strings/numbers/cart-verb
    builtins scan identically in both languages."""
    n = len(line)
    out = [_HL_TEXT] * n
    i = 0
    keywords = _HL_LUA_KEYWORDS if lua else _HL_KEYWORDS
    while i < n:
        ch = line[i]
        if (lua and ch == "-" and i + 1 < n and line[i + 1] == "-") or \
                (not lua and ch == "#"):       # comment to end of line
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
            if word in keywords:
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

    # The tool palette (#89): a tappable row (opened by the always-visible TLS
    # toggle) that reaches every range op the T-Deck keyboard has no key/combo for.
    # 2-3 char labels so ten fit across the 320px baseline. Host keyboard shortcuts
    # (Ctrl+C/X/V/F, shift+arrow, Tab) are conveniences layered on top -- these
    # buttons are the touch (mouse-on-web) path that also works on the device.
    _TOOLS = ("sel", "copy", "cut", "paste", "find",
              "indent", "outdent", "gutter", "auto", "goto", "undo", "redo")
    _TOOL_LABEL = {"sel": "SEL", "copy": "CPY", "cut": "CUT", "paste": "PST",
                   "find": "FND", "indent": ">>", "outdent": "<<", "gutter": "#",
                   "auto": "AC", "goto": "DEF", "undo": "UN", "redo": "RE"}
    # The pre-literate glyph per tool (#89 icon pass): drawn centered instead of the
    # label. _blit_glyph draws nothing for an unknown kind, so _TOOL_LABEL stays the
    # guaranteed fallback (gutter -> the numbered-lines glyph; auto/goto have no glyph
    # yet -> the AC/DEF labels).
    _TOOL_GLYPH = {"sel": "select", "copy": "copy", "cut": "cut", "paste": "paste",
                   "find": "find", "indent": "indent", "outdent": "outdent",
                   "gutter": "linenums", "undo": "undo", "redo": "redo"}
    _TLS_COLS = 3                 # cells wide for the always-visible tools toggle
    _POPUP_MAX = 8               # visible rows in the autocomplete / jump popups

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect
        self._ekey = KeyEdge()        # keyboard edge tracker (editor edge detect)
        self._drag = None             # last pointer pos during a code-view drag-scroll
        self._hl_cache = {}           # per-line syntax-highlight memo (#24)
        self._t = None                # per-draw tone map (set by _draw_code)
        # -- #89 additions: selection / tools / find / gutter -----------------
        self._tools_open = False      # the tool palette row is shown
        self._select_mode = False     # SELECT mode: a code drag extends the selection
        self._sel_drag = False        # a selection drag is in progress this gesture
        self._gutter = False          # optional line-number gutter (off == baseline)
        self._find_open = False       # the find bar is shown + focused
        self._find_q = ""             # the find query being typed
        self._find_ci = True          # case-insensitive find (the default)
        self._find_anchor = None      # where the incremental search re-runs from
        # -- #89 autocomplete + jump-to-symbol popups -------------------------
        self._cmp_open = False        # the autocomplete popup is shown
        self._cmp_items = []          # the candidate words (API/keyword + buffer)
        self._cmp_sel = 0             # the highlighted candidate row
        self._jump_open = False       # the jump-to-symbol popup is shown
        self._jump_items = []         # (name, row) for every def/class line
        self._jump_sel = 0            # the highlighted symbol row

    def reset(self):
        """Reset the keyboard edge tracker (called by ws.set_menu_view when the editor
        is (re)built) so the first key press after opening registers. Also drops the
        transient #89 modes so a freshly-opened editor is in a clean state."""
        self._ekey.reset()
        self._sel_drag = False
        self._find_open = False
        self._select_mode = False
        self._cmp_open = False
        self._jump_open = False
        if self.ws.editor is not None:
            self.ws.editor.select_sticky = False

    def _is_lua(self):
        """The open project's cart language (#67 Phase 5): drives the symbol
        palette + the highlighter's comment/keyword rules."""
        proj = self.ws.project
        cart = proj.cart if proj is not None else None
        return cart is not None and cart.get("runtime") == "lua"

    def _symbols(self):
        return _LUA_SYMBOLS if self._is_lua() else _CODE_SYMBOLS

    # -- Layer facets --------------------------------------------------------

    def draw(self, dt):
        self._draw_code()
        # The unified zoned bar (Stage 4 rollout): the tab ladder + PLAY + X (no SAVE,
        # #111), drawn LAST (chrome over the full-screen text) so the code editor shows
        # the SAME bar every other tab does. System canvas + responsive layout, like the
        # launcher/Settings bar. This replaces the code editor's old title + RUN/SAVE/
        # CLOSE band: PLAY/X live in the bar now, SAVE is an automatic exit-path commit.
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
        ed = ws.editor
        # The unified bar's tab ladder + PLAY + X claims its slice FIRST (Stage 4
        # rollout), before any code-body tap -- there's no SAVE tap to dispatch (#111);
        # ws.save_code fires automatically from set_tab/leave instead.
        if click and ws.bar_layer.handle_bar_tap("menu", px, py):
            return True
        # #89 chrome, in overlay order: the always-visible tools toggle, then (when
        # open) the find bar + the tool palette row, all before the code body.
        if click and self._in(px, py, self._tls_btn(lay)):
            self._tools_open = not self._tools_open
            ws.mark_dirty()
            return True
        # An open autocomplete / jump popup is modal over the code body: a tap picks a
        # row, a tap anywhere else dismisses it (the small-screen "escape").
        if click and (self._cmp_open or self._jump_open) and \
                self._popup_tap(px, py, lay, ed):
            return True
        if click and self._find_open and self._find_tap(px, py, lay):
            return True
        if click and self._tools_open and self._in(px, py, self._toolbar_rect(lay)):
            self._tool_tap(px, py, lay, ed)
            return True
        if click and self._in(px, py, lay.sym_area) and ed is not None:
            syms = self._symbols()
            i = (px - lay.sym_area[0]) // lay.sym_cell   # tap a coding symbol
            if 0 <= i < len(syms):
                self._feed_char(ord(syms[i]))            # routes to find field or editor
            return True
        if ed is not None and self._in(px, py, lay.code_area()):
            if self._select_mode:
                self._select_pointer(px, py, click, lay, ed)   # drag = extend selection
            else:
                self._code_drag(px, py)                  # drag pans the viewport
                if click:
                    tx0 = self._text_x0(lay, ed)
                    col = (px - tx0) // lay.cell
                    if col < 0:
                        col = 0                           # a tap in the gutter -> column 0
                    ed.place(col, (py - lay.y0) // lay.lh)
        else:
            self._drag = None
        return True

    # -- input ---------------------------------------------------------------

    def _editor_input(self):
        # Feed the typed key to the editor, one insert per physical press: the
        # keyboard reports the byte for the frame it is down then 0, so acting on
        # the 0->key edge (key != previous) avoids autorepeat.
        ws = self.ws
        ed = ws.editor
        if ed is None:
            return
        k = ws.input.last_key
        if self._ekey.hit(k):
            # An open popup (autocomplete / jump-to-symbol) owns the keyboard: Enter
            # accepts the highlighted row; any other key dismisses it and then edits
            # normally (the small-screen "escape" -- the T-Deck has no Esc key).
            if self._cmp_open or self._jump_open:
                if k in (0x0D, 0x0A):
                    self._popup_accept()
                    return
                self._cmp_open = False
                self._jump_open = False
            # Host keyboard shortcuts (#89) layered over the touch tool palette: the
            # control bytes below are NEVER inserted as text (editor.key ignores them),
            # so they can't corrupt the buffer -- and each maps to a tool-palette button
            # so touch-only devices reach the same feature. Ctrl+Z/Y (0x1A/0x19) are the
            # Stage-7 journal walk; Ctrl+C/X/V (0x03/0x18/0x16) the clipboard; Ctrl+F
            # (0x06) the find bar; Ctrl+E (0x05) autocomplete, Ctrl+G (0x07) jump-to-
            # symbol. While the find bar is focused, typing feeds the query.
            if k == 0x05:                      # Ctrl+E opens autocomplete
                self._open_completion(ed)
            elif k == 0x07:                    # Ctrl+G opens jump-to-symbol
                self._open_jump(ed)
            elif k == 0x06:                    # Ctrl+F toggles the find bar
                self._toggle_find()
            elif self._find_open:
                self._find_key(k)              # find field owns the keyboard while open
            elif k == 0x1A:
                ws.undo()
            elif k == 0x19:
                ws.redo()
            elif k == 0x03:
                ed.copy()
            elif k == 0x18:
                if ed.cut():
                    self._clear_err()
            elif k == 0x16:
                if ed.paste():
                    self._clear_err()
            elif ed.key(k):                    # text changed -> drop the stale error marker
                self._clear_err()
        # (self._ekey.hit above already recorded k as the new previous byte.)

    # -- #89 helpers: clipboard/find/tool/select routing ---------------------

    def _clear_err(self):
        # A text edit invalidates a stale runtime-error marker (same as the old
        # inline drop in _editor_input; shared now that several ops mutate the text).
        ws = self.ws
        ws.code_err = None
        ws.code_err_row = None
        ws.crash_line = None

    def _feed_char(self, code):
        # One typed/tapped character: into the find field while it's focused, else
        # inserted into the buffer (the symbol-palette path).
        if self._find_open:
            self._find_key(code)
        elif self.ws.editor is not None and self.ws.editor.key(code):
            self._clear_err()

    def _run_tool(self, name, ed):
        # Dispatch a tool-palette button (also the keyboard-shortcut targets).
        ws = self.ws
        if ed is None:
            return
        if name == "sel":
            self._select_mode = not self._select_mode
            ed.select_sticky = self._select_mode
            if self._select_mode:
                ed.begin_select()              # anchor here so arrows/drag extend
        elif name == "copy":
            ed.copy()
        elif name == "cut":
            if ed.cut():
                self._clear_err()
        elif name == "paste":
            if ed.paste():
                self._clear_err()
        elif name == "find":
            self._toggle_find()
        elif name == "indent":
            ed.indent_selection()
            self._clear_err()
        elif name == "outdent":
            if ed.outdent_selection():
                self._clear_err()
        elif name == "gutter":
            self._gutter = not self._gutter
        elif name == "auto":
            self._open_completion(ed)
        elif name == "goto":
            self._open_jump(ed)
        elif name == "undo":
            ws.undo()
        elif name == "redo":
            ws.redo()
        ws.mark_dirty()

    def _tool_tap(self, px, py, lay, ed):
        r = self._toolbar_rect(lay)
        n = len(self._TOOLS)
        bw = r[2] // n
        if bw <= 0:
            return
        i = (px - r[0]) // bw
        if 0 <= i < n:
            self._run_tool(self._TOOLS[i], ed)

    def _select_pointer(self, px, py, click, lay, ed):
        # SELECT mode: the press edge drops a fresh anchor at the tapped cell, then a
        # drag extends the selection to the finger (place(select=True) keeps the
        # anchor). Release ends the gesture. A plain tap (press+release, no move)
        # just moves the caret -- exactly a non-select tap.
        ws = self.ws
        tx0 = self._text_x0(lay, ed)
        col = (px - tx0) // lay.cell
        if col < 0:
            col = 0
        row = (py - lay.y0) // lay.lh
        if click:
            ed.place(col, row)                 # collapse + move the caret here
            ed.begin_select()
            self._sel_drag = True
        elif self._sel_drag and ws.pointer.down:
            ed.place(col, row, select=True)    # extend to the finger
        elif not ws.pointer.down:
            self._sel_drag = False
        ws.mark_dirty()

    # -- find bar ------------------------------------------------------------

    def _toggle_find(self):
        self._find_open = not self._find_open
        ed = self.ws.editor
        if self._find_open and ed is not None:
            self._find_anchor = (ed.row, ed.col)   # incremental search re-runs from here
        self.ws.mark_dirty()

    def _find_run(self, forward, reset=False):
        ed = self.ws.editor
        if ed is None or not self._find_q:
            return
        if reset and self._find_anchor is not None:
            # Incremental (query changed): restart from where find opened so the
            # highlight doesn't march away as you type, and ACCEPT a match starting
            # exactly there (include_current -- explicit next keeps move-past). The
            # buffer may have SHRUNK since the anchor was recorded (the tool
            # palette's CUT/UNDO stay tappable behind the find bar), so clamp the
            # restored position or the next search indexes past the buffer.
            r, c = self._find_anchor
            if r > len(ed.lines) - 1:
                r = len(ed.lines) - 1
            ed.row = r
            ed.col = min(c, len(ed.lines[r]))
            ed.sel = None
        ed.find(self._find_q, forward, self._find_ci, include_current=reset)
        self.ws.mark_dirty()

    def _find_key(self, code):
        # Edit the find query (the find field has the keyboard while it's open).
        if code in (0x0D, 0x0A):               # enter -> next match
            self._find_run(True)
        elif code in (0x08, 0x7F):             # backspace -> trim + re-search
            self._find_q = self._find_q[:-1]
            self._find_run(True, reset=True)
            self.ws.mark_dirty()
        elif 0x20 <= code <= 0x7E:             # printable -> extend + re-search
            self._find_q += chr(code)
            self._find_run(True, reset=True)
            self.ws.mark_dirty()

    def _find_tap(self, px, py, lay):
        btns = self._find_btns(lay)
        if self._in(px, py, btns["prev"]):
            self._find_run(False)
        elif self._in(px, py, btns["next"]):
            self._find_run(True)
        elif self._in(px, py, btns["case"]):
            self._find_ci = not self._find_ci
            self._find_run(True, reset=True)
        elif self._in(px, py, btns["close"]):
            self._find_open = False
            self.ws.mark_dirty()
        elif not self._in(px, py, self._find_rect(lay)):
            return False
        return True                            # a tap anywhere on the bar is consumed

    # -- autocomplete + jump-to-symbol popups (#89) --------------------------
    # Both are small overlay lists the CodeEditor core populates: autocomplete
    # completes the identifier left of the caret from the cart-API verbs + language
    # keywords + the words already in the buffer; jump lists the def/class lines and
    # moves the caret to the picked one. Pointer-pick is the universal path (touch on
    # the device, mouse on the web); Ctrl+E/Ctrl+G + Enter layer on for the host.

    def _api_names(self):
        """The completion name pool for the open project's language (#67): the cart-
        API verbs (shared) + the language keywords. Tuples concatenate fine."""
        if self._is_lua():
            return _HL_LUA_KEYWORDS + _HL_BUILTINS
        return _HL_KEYWORDS + _HL_BUILTINS

    def _open_completion(self, ed):
        if ed is None:
            return
        self._jump_open = False
        self._cmp_items = ed.completions(self._api_names())
        self._cmp_sel = 0
        self._cmp_open = bool(self._cmp_items)     # nothing to offer -> stay closed
        self.ws.mark_dirty()

    def _open_jump(self, ed):
        if ed is None:
            return
        self._cmp_open = False
        self._jump_items = ed.def_symbols()
        self._jump_sel = 0
        self._jump_open = bool(self._jump_items)
        self.ws.mark_dirty()

    def _popup_accept(self):
        """Accept the highlighted row of whichever popup is open (Enter / the pointer
        pick both land here)."""
        ed = self.ws.editor
        if self._cmp_open:
            if ed is not None and self._cmp_items:
                ed.complete(self._cmp_items[self._cmp_sel])
                self._clear_err()
            self._cmp_open = False
        elif self._jump_open:
            if ed is not None and self._jump_items:
                _name, row = self._jump_items[self._jump_sel]
                ed.goto_row(row, self._leading_spaces(ed.lines[row]))
            self._jump_open = False
        self.ws.mark_dirty()

    def _leading_spaces(self, line):
        n = 0
        while n < len(line) and line[n] == " ":
            n += 1
        return n

    def _popup_tap(self, px, py, lay, ed):
        """A tap while a popup is open: pick the tapped row, else dismiss. Always
        consumes the tap (the popup is modal over the code body)."""
        if self._cmp_open:
            _panel, rects = self._cmp_geom(lay, ed)
            for i in range(len(rects)):
                if self._in(px, py, rects[i]):
                    self._cmp_sel = i
                    self._popup_accept()
                    return True
            self._cmp_open = False
        elif self._jump_open:
            _panel, rects = self._jump_geom(lay)
            for i in range(len(rects)):
                if self._in(px, py, rects[i]):
                    self._jump_sel = i
                    self._popup_accept()
                    return True
            self._jump_open = False
        self.ws.mark_dirty()
        return True

    def _cmp_geom(self, lay, ed):
        """(panel_rect, [row_rects]) for the autocomplete popup, anchored at the word
        start just below the caret line and clamped inside the canvas. Flips above the
        caret when it would cover the symbol palette."""
        items = self._cmp_items
        n = min(len(items), self._POPUP_MAX)
        lh = lay.lh
        cell = lay.cell
        w = 1
        for i in range(n):
            if len(items[i]) > w:
                w = len(items[i])
        pw = (w + 1) * cell
        plen = len(ed.word_prefix()) if ed is not None else 0
        x = self._text_x0(lay, ed) + (ed.col - ed.left - plen) * cell
        if x + pw > lay.w:
            x = lay.w - pw
        if x < 0:
            x = 0
        ph = n * lh
        y = lay.y0 + (ed.row - ed.top + 1) * lh
        if y + ph > lay.sym_y:                     # would cover the palette -> flip up
            y = lay.y0 + (ed.row - ed.top) * lh - ph
            if y < lay.y0:
                y = lay.y0
        panel = (x, y, pw, ph)
        rects = [(x, y + i * lh, pw, lh) for i in range(n)]
        return panel, rects

    def _jump_geom(self, lay):
        """(panel_rect, [row_rects]) for the jump-to-symbol popup: a centered list
        with a one-row header. (More than _POPUP_MAX symbols show the first page --
        kid carts rarely have that many defs.)"""
        items = self._jump_items
        n = min(len(items), self._POPUP_MAX)
        lh = lay.lh
        cell = lay.cell
        w = 4
        for i in range(n):
            L = len(items[i][0]) + 5               # room for the " 12" line-number tail
            if L > w:
                w = L
        pw = (w + 1) * cell
        if pw > lay.w:
            pw = lay.w
        ph = (n + 1) * lh                          # +1 header row
        x = (lay.w - pw) // 2
        y = lay.y0 + lh
        panel = (x, y, pw, ph)
        rects = [(x, y + (i + 1) * lh, pw, lh) for i in range(n)]
        return panel, rects

    # -- #89 geometry (derived from CodeLayout; kept off the frozen constants) -

    def _tls_btn(self, lay):
        # The always-visible tools toggle, top-right of the code body (overlays only
        # the far-right of line 0, which is usually blank). Off the frozen constants,
        # so the baseline code_area()/sym_area geometry is untouched.
        w = self._TLS_COLS * lay.cell
        return (lay.w - w, lay.y0, w, lay.lh)

    def _toolbar_rect(self, lay):
        # The tool palette row, just above the status band / symbol palette.
        h = lay.lh
        top = lay.status_band[1] if lay.status_band is not None else lay.sym_y
        return (0, top - h, lay.w, h)

    def _find_rect(self, lay):
        # The SECOND visible row, so the find bar's right-edge buttons never sit
        # under the always-visible TLS toggle (which owns the top-right of row 0).
        return (0, lay.y0 + lay.lh, lay.w, lay.lh)

    def _find_btns(self, lay):
        # prev / next / case / close, packed against the right edge of the find bar.
        r = self._find_rect(lay)
        bw = 2 * lay.cell
        x = r[0] + r[2]
        out = {}
        for name in ("close", "case", "next", "prev"):
            x -= bw
            out[name] = (x, r[1], bw, r[3])
        return out

    def _gutter_cols(self, ed):
        # Line-number gutter width in cells (0 == off). Narrow: the digits of the
        # largest line number + one separating cell, so it stays readable at 320x240.
        if not self._gutter or ed is None:
            return 0
        return len(str(len(ed.lines))) + 1

    def _text_x0(self, lay, ed):
        return lay.x0 + self._gutter_cols(ed) * lay.cell

    def _apply_gutter(self, lay, ed):
        # Re-flow the editor's visible columns so long lines still fit beside the
        # gutter. Self-correcting every draw (a resize relayout resets ed.COLS to
        # lay.cols; this narrows it again while the gutter is on).
        if ed is None:
            return
        target = max(4, lay.cols - self._gutter_cols(ed))
        if ed.COLS != target:
            ed.set_view_size(target, lay.rows)

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
        # sel/find (#89): the selection tint (drawn BEHIND the text) + the find-match
        # outline + the dim gutter number ink. indigo/orange read on every surface the
        # editor uses; on a light theme the selection uses the theme hilite token.
        if self.ws.code_layout._base:
            return {"bg": NAMES["black"], "caret": NAMES["yellow"],
                    "sym_bg": NAMES["dark_grey"], "sym_edge": NAMES["indigo"],
                    "sym_ink": NAMES["white"], "hl": None,
                    "sel": NAMES["indigo"], "find": NAMES["orange"],
                    "gutter": NAMES["dark_grey"]}
        th = self.ws.theme_colors
        if th.get("ink", NAMES["white"]) != 0:      # dark surface -> dark set
            return {"bg": th.get("surface", NAMES["black"]),
                    "caret": NAMES["yellow"],
                    "sym_bg": NAMES["dark_grey"], "sym_edge": NAMES["indigo"],
                    "sym_ink": NAMES["white"], "hl": None,
                    "sel": NAMES["indigo"], "find": NAMES["orange"],
                    "gutter": NAMES["dark_grey"]}
        return {"bg": th["surface"], "caret": th["ink"],
                "sym_bg": th.get("surface_alt", NAMES["dark_grey"]),
                "sym_edge": th["border"], "sym_ink": th["ink"],
                "hl": self._HL_LIGHT,
                "sel": th.get("hilite", NAMES["indigo"]), "find": NAMES["orange"],
                "gutter": th.get("border", NAMES["dark_grey"])}

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
        # bar (drawn after this in draw()) owns the top 18px -- PLAY runs (and persists,
        # #111), X exits, and the tab ladder switches views (persisting the outgoing tab
        # too). The text area already starts at y0 == 18 (below the bar), so the body is
        # fullscreen with no chrome of its own.
        # code area (horizontal scroll: columns [left, left+COLS))
        if ed is not None:
            self._apply_gutter(lay, ed)          # #89: narrow ed.COLS while the gutter is on
            gc = self._gutter_cols(ed)           # gutter width in cells (0 == off)
            tx0 = lay.x0 + gc * cell             # text origin (shifted right by the gutter)
            cols = ed.COLS
            vis = ed.visible_lines()
            errrow = ws.code_err_row
            for idx in range(len(vis)):
                y = lay.y0 + idx * lh
                absrow = ed.top + idx
                full = vis[idx]
                on_err = errrow is not None and absrow == errrow
                if gc:                           # #89: line-number gutter (crash mark integrated)
                    num = str(absrow + 1)
                    cv.print(num[-gc:], lay.x0, y,
                             NAMES["red"] if on_err else t["gutter"], 1)
                elif on_err:                     # no gutter: the original far-left crash tick
                    cv.rect(0, y, 3 * fs, 8 * fs, NAMES["red"])
                if on_err:                       # inline error underline (#24)
                    cv.rect(tx0, y + 8 * fs, cols * cell, fs, NAMES["red"])
                self._draw_selection(ed, absrow, y, tx0, cell, cols, fs, t["sel"])
                self._draw_find_matches(ed, full, y, tx0, cell, cols, fs, t["find"])
                seg = full[ed.left:ed.left + cols]
                segcols = self._hl(full)[ed.left:ed.left + cols]
                self._draw_code_runs(seg, segcols, y, tx0)
                if on_err and ws.code_err:      # short reason after the code, if it fits
                    mcol = len(seg) + 1
                    if mcol < cols - 2:
                        cv.print(ws.code_err[:cols - mcol],
                                 tx0 + mcol * cell, y, NAMES["red"], 1)
                if absrow == ed.row:            # caret on the cursor's line
                    vcol = ed.col - ed.left
                    if 0 <= vcol <= cols:
                        cv.rect(tx0 + vcol * cell, y, fs, 8 * fs, t["caret"])
        # Status band (Phase 3, shelf tiers): the mockup's "Ln 13, Col 1" strip.
        if lay.status_band is not None and ed is not None:
            issues = "1 ISSUE" if ws.code_err else "NO ISSUES"
            _ui.status_row(cv, ws.theme_colors, lay.status_band,
                           ("LN " + str(ed.row + 1) + ", COL " + str(ed.col + 1),
                            str(len(ed.lines)) + " LINES", issues))
        self._draw_tools(lay, ed, t)             # #89: tools toggle + palette + find bar
        self._draw_symbols()

    def _hl(self, line):
        """Memoized per-line syntax highlight (#24). Lines recur every frame, so
        cache by text (keyed with the language, so switching a python project for
        a lua one never replays stale colors); bound the cache so a long edit
        session can't grow it."""
        lua = self._is_lua()
        key = (lua, line)
        cols = self._hl_cache.get(key)
        if cols is None:
            if len(self._hl_cache) > 400:
                self._hl_cache.clear()
            cols = _highlight(line, lua)
            self._hl_cache[key] = cols
        return cols

    def _draw_code_runs(self, seg, segcols, y, x0):
        """Draw one code line as runs of same-colored text (#24). On the SYSTEM
        canvas at the layout's char-cell width (8*fs), so it scales with the font.
        `x0` is the text origin (shifted right by the #89 line-number gutter)."""
        cv = self.ws.sys_canvas
        cell = self.ws.code_layout.cell
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

    def _visible_span(self, ed, s, e, cols):
        """Clip a column span [s, e) to the horizontally-scrolled visible window,
        returning the visible (start_col, end_col) or None when it's off-screen."""
        vs = s if s > ed.left else ed.left
        we = ed.left + cols
        ve = e if e < we else we
        if ve <= vs:
            return None
        return vs, ve

    def _draw_selection(self, ed, absrow, y, tx0, cell, cols, fs, color):
        """Highlight the selected columns on one visible row (#89): a filled tint
        behind the text (the runs redraw on top). Multi-row selections fill full
        lines between the endpoints."""
        b = ed.selection_bounds()
        if b is None:
            return
        r0, c0, r1, c1 = b
        if absrow < r0 or absrow > r1:
            return
        s = c0 if absrow == r0 else 0
        e = c1 if absrow == r1 else len(ed.lines[absrow])
        span = self._visible_span(ed, s, e, cols)
        if span is None:
            return
        vs, ve = span
        cv = self.ws.sys_canvas
        cv.rect(tx0 + (vs - ed.left) * cell, y, (ve - vs) * cell, 8 * fs, color)

    def _draw_find_matches(self, ed, line, y, tx0, cell, cols, fs, color):
        """Outline every occurrence of the find query on one visible row (#89): the
        incremental "all matches" highlight the find bar drives."""
        q = self._find_q
        if not (self._find_open and q):
            return
        hay = line.lower() if self._find_ci else line
        qq = q.lower() if self._find_ci else q
        L = len(qq)
        cv = self.ws.sys_canvas
        start = 0
        while True:
            i = hay.find(qq, start)
            if i < 0:
                break
            span = self._visible_span(ed, i, i + L, cols)
            if span is not None:
                vs, ve = span
                cv.rectb(tx0 + (vs - ed.left) * cell, y, (ve - vs) * cell, 8 * fs, color)
            start = i + (L if L > 0 else 1)

    def _panel_btn(self, cv, lay, r, label, t, active=False, ink=None, glyph=None):
        """One tool/find button: a filled, bordered cell with a centered label -- or,
        when `glyph` names a known chrome glyph (#89 icon pass), a centered 12x12 icon
        instead of the label. Uses the same sym_bg/sym_edge palette as the symbol strip
        so it reads as chrome. A missing glyph kind falls through to the `label`."""
        fs = lay.fs
        cv.rect(r[0], r[1], r[2] - 1, r[3] - 1, t["sym_edge"] if active else t["sym_bg"])
        cv.rectb(r[0], r[1], r[2] - 1, r[3] - 1, t["sym_edge"])
        gink = ink if ink is not None else t["sym_ink"]
        if glyph is not None and _glyph_known(glyph):
            self.ws._glyph(glyph, (r[0], r[1], r[2] - 1, r[3] - 1), gink, cv)
        elif label:
            gx = r[0] + (r[2] - len(label) * 8 * fs) // 2
            if gx < r[0] + fs:
                gx = r[0] + fs
            gy = r[1] + (r[3] - 8 * fs) // 2
            cv.print(label, gx, gy, gink, 1)

    def _draw_tools(self, lay, ed, t):
        """The #89 chrome: the always-visible TLS toggle, the tool palette row (when
        open) and the find bar (when open). All overlay the code body -- drawn AFTER
        the text so they sit on top -- and never touch the frozen baseline geometry."""
        cv = self.ws.sys_canvas
        self._panel_btn(cv, lay, self._tls_btn(lay), "TLS", t,
                        active=self._tools_open, glyph="tools")
        if self._tools_open:
            r = self._toolbar_rect(lay)
            n = len(self._TOOLS)
            bw = r[2] // n
            for i in range(n):
                name = self._TOOLS[i]
                active = (name == "sel" and self._select_mode) or \
                         (name == "find" and self._find_open) or \
                         (name == "gutter" and self._gutter)
                self._panel_btn(cv, lay, (r[0] + i * bw, r[1], bw, r[3]),
                                self._TOOL_LABEL[name], t, active=active,
                                glyph=self._TOOL_GLYPH.get(name))
        if self._find_open:
            self._draw_find(lay, ed, t)
        if self._cmp_open:
            self._draw_completion(lay, ed, t)
        if self._jump_open:
            self._draw_jump(lay, t)

    def _draw_listbox(self, cv, lay, t, panel, rects, labels, sel, title):
        """A bordered overlay list (the autocomplete + jump popups): the sym_bg/edge
        chrome palette, an optional accent title row, and the selected row tinted with
        the selection color. Labels are pre-clipped by the caller's width."""
        fs = lay.fs
        cv.rect(panel[0], panel[1], panel[2] - 1, panel[3] - 1, t["sym_bg"])
        cv.rectb(panel[0], panel[1], panel[2] - 1, panel[3] - 1, t["sym_edge"])
        if title is not None:
            cv.print(title, panel[0] + fs, panel[1] + fs, t["find"], 1)
        maxch = max(1, (panel[2] - 2 * fs) // lay.cell)
        for i in range(len(rects)):
            r = rects[i]
            if i == sel:
                cv.rect(r[0], r[1], r[2] - 1, r[3] - 1, t["sel"])
            cv.print(labels[i][:maxch], r[0] + fs, r[1] + fs, t["sym_ink"], 1)

    def _draw_completion(self, lay, ed, t):
        cv = self.ws.sys_canvas
        panel, rects = self._cmp_geom(lay, ed)
        self._draw_listbox(cv, lay, t, panel, rects,
                           self._cmp_items[:len(rects)], self._cmp_sel, None)

    def _draw_jump(self, lay, t):
        cv = self.ws.sys_canvas
        panel, rects = self._jump_geom(lay)
        labels = [self._jump_items[i][0] + " " + str(self._jump_items[i][1] + 1)
                  for i in range(len(rects))]
        self._draw_listbox(cv, lay, t, panel, rects, labels, self._jump_sel, "DEFS")

    def _draw_find(self, lay, ed, t):
        """The find bar: the typed query on the left + prev/next/case/close buttons
        packed against the right (#89)."""
        cv = self.ws.sys_canvas
        fs = lay.fs
        r = self._find_rect(lay)
        btns = self._find_btns(lay)
        qright = btns["prev"][0]                  # the query field ends where the buttons start
        cv.rect(r[0], r[1], qright - r[0] - 1, r[3] - 1, t["sym_bg"])
        cv.rectb(r[0], r[1], qright - r[0] - 1, r[3] - 1, t["sym_edge"])
        # As much of the tail of the query as fits, then a caret block.
        avail = max(1, (qright - r[0]) // lay.cell - 2)
        shown = self._find_q[-avail:] if len(self._find_q) > avail else self._find_q
        cv.print(("F:" + shown)[-avail - 2:], r[0] + fs, r[1] + fs, t["sym_ink"], 1)
        cx = r[0] + fs + (len(shown) + 2) * lay.cell
        cv.rect(cx, r[1] + fs, fs, 8 * fs, t["caret"])
        self._panel_btn(cv, lay, btns["prev"], "<", t)
        self._panel_btn(cv, lay, btns["next"], ">", t)
        self._panel_btn(cv, lay, btns["case"], "Aa", t, active=not self._find_ci)
        self._panel_btn(cv, lay, btns["close"], "X", t)

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
        syms = self._symbols()
        for i in range(len(syms)):
            x = lay.sym_area[0] + i * sc
            cv.rect(x, sy, sc - 1, sh - 1, t["sym_bg"])
            cv.rectb(x, sy, sc - 1, sh - 1, t["sym_edge"])
            cv.print(syms[i], x + 6 * fs, sy + 6 * fs, t["sym_ink"], 1)
