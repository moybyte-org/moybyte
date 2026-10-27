# Map (grep -n a name to jump there):
#   -- code-editor geometry           the code tab's constants (console.py imports them back)
#   symbols_for                       the tappable symbol palette for a source language
#   draw_symbol_keys                  the symbol palette renderer
#   -- code-editor syntax highlighting  the token colours
#   CodeLayer                         the code editor content layer: draw, keys, pointer
"""The Python code editor (#24/#39), extracted from Workstation (runtime/console.py)
as its own Layer -- docs/history/shell_layers_refactor_v1.md Phase 2 (the last surface).

The full-screen code view: the responsive text area (drawn on the SYSTEM canvas at the
CodeLayout geometry), the syntax-highlighted line rendering, the coding-symbol palette
(the T-Deck keyboard can't type `=[]{}<>%`, so a tappable palette supplies them), and
the touch/keyboard editing.

Stage-4 bar rollout (docs/history/shell_ux_technical_plan_v1.md): the code editor's OWN top
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
from editors_code import CodeEditor
from editors_base import KeyEdge

try:
    import ui as _ui
    import ui_draw as _ui_draw
    import layout_base as _lb
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
    from runtime import layout_base as _lb
_in = _ui.rect_in   # one hit-test (ui.rect_in)
# The overlays' fields, lines and keys (`_draw_tools`): the keys every
# board has, by action, Shift+Enter its own.
_FIND_FIELD = {"id": "find", "hint": "Find"}
_JUMP_FIELD = {"id": "goto", "hint": "Go to a definition"}
_DIM = {"tone": "dim"}
_MATCH_ALPHA = 96                 # a find match's tint of `value` over the code
_NEW_SCRIPT = "New script"
_FIND_KEYS = ("confirm", "Next", "Shift+enter", "Back", "cancel", "Close")
_CMP_KEYS = ("scroll", "Pick", "confirm", "Insert", "cancel", "Close")
_JUMP_KEYS = ("scroll", "Pick", "confirm", "Go", "cancel", "Close")
_FILES_KEYS = ("scroll", "Pick", "confirm", "Open", "cancel", "Close")
# See why's popover's lines (`_draw_crash_popup`).
_STOP_HEAD = {"face": "title"}
_STOP_TEXT = {"face": "mono", "wrap": True, "lines": 4}
_STOP_DIM = {"tone": "dim"}


def _desk():
    return _lb.metric_class() == _lb.DESK
try:
    from moy_input import PB_MIDDLE as _PB_MIDDLE, SHIFT as _SHIFT
except ImportError:  # pragma: no cover - host fallback
    from runtime.moy_input import PB_MIDDLE as _PB_MIDDLE, SHIFT as _SHIFT
try:
    from editors_code import TextMouse
    from ticks import _ticks_ms
except ImportError:  # pragma: no cover - host fallback
    from runtime.editors_code import TextMouse
    from runtime.ticks import _ticks_ms

# The cart API's names, once (see runtime/cart_verbs.py) -- the syntax
# highlighter's builtin class is derived from them below.
try:
    from cart_verbs import CART_VERBS as _CART_VERBS
except ImportError:  # pragma: no cover - host fallback
    from runtime.cart_verbs import CART_VERBS as _CART_VERBS

# The console-wide editor MODE table (docs/text_editing_2026-09.md): this tab
# edits a cart's MAIN file, so its language and its parse gate are the `code`
# mode's, asked here rather than re-derived.
try:
    import text_modes as _modes
except ImportError:  # pragma: no cover - host fallback
    from runtime import text_modes as _modes

# The shared pre-literate glyph vocabulary (#89 icon pass): the TLS toggle + the tool
# palette row draw a 12x12 glyph per button instead of the terse 2-3 char label.


def _glyph_known(kind):
    """True if `kind` is a known glyph (so a button draws the icon; else the
    text label is the fallback)."""
    return _ui_draw.glyph_id(kind) >= 0


# The code editor draws its chrome in its OWN palette (`_tones`), not in the
# theme's. The tone map therefore doubles as the `th` dict the toolkit reads, by
# carrying the token names it looks up. Nothing is invented here: every alias
# points at a role this surface already had, which is what lets the chips, the
# find field, the symbol keys and the popup rows all draw as ordinary widget
# kinds -- reachable by a theme -- instead of through `colors=` (#207).
def _as_theme(t):
    """Add the ui-toolkit token aliases to a tone map, in place."""
    edge = t["sym_edge"]
    t["panel"] = t["sym_bg"]        # chip/row/cell REST field
    t["dim"] = edge                 # chip/row/cell REST edge
    t["accent"] = edge              # chip ON field  (the `active` look)
    t["edge"] = edge                # chip ON edge
    t["title_ink"] = t["sym_ink"]   # chip/row/cell REST ink
    return t


def _chip_rect(r):
    """The FIELD of a code-chrome chip: one pixel narrower and shorter than the
    cell it is laid out in, so adjacent chips keep a 1px gutter. Frozen geometry
    -- and the reason a chip's text label centres on the CELL (see `_btn_face`),
    which is one pixel wider than the field it sits in."""
    return (r[0], r[1], r[2] - 1, r[3] - 1)


# -- code-editor geometry (single source; console.py imports these back) ------
# The area/line-height feed console's CodeLayout (responsive) + the crash panel; the
# button rects + symbol palette are the fixed 320x240 baseline CodeLayout starts from.
_CODE_X0 = 4
_CODE_Y0 = 18
_CODE_LEAD = 2          # rows between two code lines, under the font's line
_CODE_LH = 8 + _CODE_LEAD   # petme128's line and the lead: the baseline grid
_CODE_AREA = (_CODE_X0, _CODE_Y0, CodeEditor.COLS * 8, CodeEditor.ROWS * _CODE_LH)
# The T-Deck keyboard has no `=`/`[]`/`{}`/`<>`/`%` keys, so the code editor shows a
# tappable palette of them along the bottom. A lua cart (#67 Phase 5) swaps in the
# Lua set: `~` joins (for `~=`; `..` is two taps of `.`) and the optional `;` --
# which Lua never needs -- makes room. SAME length, so the layout geometry is
# untouched.
_CODE_SYMBOLS = "=()[]{}<>:;,.\"_%"
_LUA_SYMBOLS = "=~()[]{}<>:,.\"_%"
_SYM_Y = 220
_SYM_H = 20
_SYM_CELL = 20
# The responsive strip (CodeLayout): its keys share out the content width
# less _SYM_MARGIN each side, never narrower than _SYM_MIN, and it sits
# _SYM_MARGIN less a key's 1px inset above the content's bottom edge.
_SYM_MIN = 18
_SYM_MARGIN = 3
# The Code tab's one content inset at the font scale: the code, the status
# line, the symbol keys and the Editor's tab strip start this far in from the
# content's left edge, and the current line's mark sits _CODE_MARK in, inside
# that inset and clear of the frame (design review R3-10).
_CODE_INSET = 8
_CODE_MARK = 3
# The status band's height (CodeLayout) at the font scale under a theme's
# own `ui` face, the same in every theme and mode, and never under that
# face's line and 1px above and below; petme128's band is 12.
_CODE_STATUS_H = 20
_SYM_AREA = (0, _SYM_Y, _SYM_CELL * len(_CODE_SYMBOLS), _SYM_H)
# The 2026-10 look's overflow fade: a line longer than the view fades out over
# its last _FADE pixels (at the font scale), in bands of `bg` at these alphas.
_FADE = 12
_FADE_ALPHA = (89, 153, 217)


def _status_line(fields, room, fs):
    """The status band's one line: `fields` joined by the home's separator,
    the middle dot (a hyphen in a `ui` face without it), as many as fit
    `room` less the band's pads at the `ui` face."""
    fid = _ui_draw.font("ui")[0]
    info = _ui_draw.font_info(fid) if fid else None
    sep = "  \u00b7  " if info is not None and info[4] == 0 else " - "
    was = _ui_draw.print_font(fid)
    try:
        room -= 2 * _ui_draw.metrics("status")[_ui_draw.ST_PAD] * fs
        line = fields[0]
        for f in fields[1:]:
            if _ui_draw.print_width(line + sep + f, fs) > room:
                break
            line += sep + f
    finally:
        _ui_draw.print_font(was)
    return line


def symbols_for(lang):
    """The tappable symbol palette for a source language: the keys the
    keyboard lacks, in the order they are laid out."""
    return _LUA_SYMBOLS if lang == "lua" else _CODE_SYMBOLS


def draw_symbol_keys(cv, th, syms, x0, y, cell, h, fs, rects=None, w=None):
    """The symbol palette renderer, ONE body for the Code tab and the script
    console: each key a normal button of theme `th` in a cell `cell` apart
    from `x0`, inset by fs (2*fs apart, fs off the strip's edges), its
    picture the symbol itself. `w` (the strip's width) shares it out whole
    instead, cell i starting at x0 + i * w // n. Appends each key's rect to
    `rects` when a list is given."""
    n = len(syms)
    for i in range(n):
        if w is None:
            kx, kw = x0 + i * cell, cell
        else:
            kx = x0 + i * w // n
            kw = x0 + (i + 1) * w // n - kx
        if rects is not None:
            rects.append((kx, y, kw - 1, h - 1))
        _ui_draw.button(cv, th, (kx + fs, y + fs, kw - 2 * fs, h - 2 * fs),
                        syms[i], kind="normal", app="code")


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

# Lines held per generation of the `_hl` memo. TWO generations live at a time,
# so this is half the single bound it replaces and the memory ceiling is the
# same; see _hl for why one generation was the wrong shape.
_HL_MEMO = 200

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
# Cart-API verbs + the common builtins a kid actually types.
#
# The verb half is DERIVED (runtime/cart_verbs.CART_VERBS, pinned against
# make_api's real namespace by a test) rather than retyped. It used to be a
# hand-kept copy under a comment reading "keep roughly in sync with make_api";
# it had drifted to 37 names against make_api's 62, and still highlighted
# `pset`, which was renamed `pix` long enough ago that no cart uses it.
#
# The LANGUAGE builtins below stay local -- they are what a kid types that the
# cart API does not define, so they are this module's own vocabulary and have
# no business in a cart-verb list.
_HL_LANG_BUILTINS = (
    "abs", "min", "max", "sin", "cos", "range", "len", "int", "str", "float",
    "round", "sqrt", "bool",
)
_HL_BUILTINS = tuple(_CART_VERBS) + _HL_LANG_BUILTINS


# What a byte starts in `_highlight_runs`: 1 a name (letter or _), 2 a digit,
# 3 a quote, 4 `#`, 5 `-`, 0 anything else (a run of those is one text run).
_HL_CLASS = bytes((1 if (c == 95 or 65 <= c <= 90 or 97 <= c <= 122) else
                   2 if 48 <= c <= 57 else 3 if c in (34, 39) else
                   4 if c == 35 else 5 if c == 45 else 0) for c in range(128))
# Each language's word -> its colour, one lookup a name.
_HL_WORDS = ({}, {})
for _w in _HL_BUILTINS:
    _HL_WORDS[0][_w] = _HL_BUILTIN
    _HL_WORDS[1][_w] = _HL_BUILTIN
for _w in _HL_KEYWORDS:
    _HL_WORDS[0][_w] = _HL_KEYWORD
for _w in _HL_LUA_KEYWORDS:
    _HL_WORDS[1][_w] = _HL_KEYWORD


def _highlight_runs(line, lua=False):
    """`_highlight` as the memo keeps it: a flat list of (start, end, colour,
    text) for each stretch of one colour, scanned a token at a time over the
    line's bytes (`_highlight` walks it a character at a time and a second
    pass groups the colours). A line with a non-ASCII character takes that
    walk."""
    b = line.encode()
    n = len(b)
    if n != len(line):
        return _runs_of(line, _highlight(line, lua))
    cls = _HL_CLASS
    words = _HL_WORDS[1 if lua else 0]
    out = []
    pc = -1
    ps = 0
    i = 0
    while i < n:
        k = cls[b[i]] if b[i] < 128 else 0
        if k == 1:
            j = i + 1
            while j < n and b[j] < 128 and 0 < cls[b[j]] < 3:
                j += 1
            c = words.get(line[i:j], _HL_TEXT)
        elif k == 2:
            j = i + 1
            while j < n and (48 <= b[j] <= 57 or b[j] == 46 or b[j] == 120):
                j += 1
            c = _HL_NUMBER
        elif k == 3:
            q = b[i]
            j = i + 1
            while j < n:
                x = b[j]
                if x == 92 and j + 1 < n:
                    j += 2
                    continue
                j += 1
                if x == q:
                    break
            c = _HL_STRING
        elif (k == 4 and not lua) or (k == 5 and lua and i + 1 < n and b[i + 1] == 45):
            j = n
            c = _HL_COMMENT
        else:
            j = i + 1
            while j < n and (b[j] >= 128 or cls[b[j]] == 0):
                j += 1
            c = _HL_TEXT
        if c != pc:
            if pc >= 0:
                out += (ps, i, pc, line[ps:i])
            pc = c
            ps = i
        i = j
    if pc >= 0:
        out += (ps, n, pc, line[ps:n])
    return out


def _runs_of(line, cols):
    """Per-character colours as `_highlight_runs`' flat runs."""
    runs = []
    n = len(line)
    i = 0
    while i < n:
        c = cols[i]
        j = i + 1
        while j < n and cols[j] == c:
            j += 1
        runs += (i, j, c, line[i:j])
        i = j
    return runs


def _is_alpha(ch):
    return ch == "_" or ("a" <= ch <= "z") or ("A" <= ch <= "Z")


def _highlight_json(line):
    """`_highlight`'s answer for a line of JSON (the theme mode's Config): a
    key (a string a colon follows) as a builtin, a value's string as a
    string, a number as a number, true, false and null as keywords."""
    n = len(line)
    out = [_HL_TEXT] * n
    i = 0
    while i < n:
        ch = line[i]
        if ch == '"':
            j = i + 1
            while j < n and line[j] != '"':
                j += 2 if line[j] == "\\" else 1
            j = min(n, j + 1)
            k = j
            while k < n and line[k] == " ":
                k += 1
            cl = _HL_BUILTIN if k < n and line[k] == ":" else _HL_STRING
            while i < j:
                out[i] = cl
                i += 1
            continue
        if "0" <= ch <= "9" or (ch == "-" and i + 1 < n and "0" <= line[i + 1] <= "9"):
            while i < n and (("0" <= line[i] <= "9") or line[i] in "-+.eE"):
                out[i] = _HL_NUMBER
                i += 1
            continue
        if _is_alpha(ch):
            j = i
            while j < n and _is_alpha(line[j]):
                j += 1
            if line[i:j] in ("true", "false", "null"):
                while i < j:
                    out[i] = _HL_KEYWORD
                    i += 1
            i = j
            continue
        i += 1
    return out


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
    ball_steps = False              # the trackball is the caret's arrows (`nav`)

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
    # label. `ui.glyph` draws nothing for an unknown kind, so _TOOL_LABEL stays the
    # guaranteed fallback (gutter -> the numbered-lines glyph; auto/goto have no glyph
    # yet -> the AC/DEF labels).
    _TOOL_GLYPH = {"sel": "select", "copy": "copy", "cut": "cut", "paste": "paste",
                   "find": "find", "indent": "indent", "outdent": "outdent",
                   "gutter": "linenums", "undo": "undo", "redo": "redo"}
    _TLS_COLS = 3                 # cells wide for the always-visible tools toggle
    _POPUP_MAX = 8               # visible rows in the autocomplete / jump popups

    def __init__(self, ws, names):
        self.ws = ws
        self._NAMES = names
        self._ekey = KeyEdge()        # keyboard edge tracker (editor edge detect)
        self._drag = None             # last pointer pos during a code-view drag-scroll
        self._hl_cache = {}           # per-line syntax-highlight memo (#24)
        self._hl_old = {}             # ...and the generation it retires (see _hl)
        self._hl_lua = None           # ...the language both were built for
        self._t = None                # per-draw tone map (set by _draw_code)
        self._stop_host = None        # the stopped line's popover's UI context
        # -- #89 additions: selection / tools / find / gutter -----------------
        self._tools_open = False      # the tool palette row is shown
        self._select_mode = False     # SELECT mode: a code drag extends the selection
        self._sel_drag = False        # a selection drag is in progress this gesture
        self._mouse = TextMouse()     # a mouse's clicks, selection and autoscroll
        self._mid = False             # the middle button was held last frame
        self._px = self._py = 0       # the pointer as handle_pointer last saw it
        self._gutter = True           # the line-number gutter, on by default (look 2026-10, D-03.1)
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
        self._jump_q = ""             # what Go to's field filters them by
        self.jump_anchor = None       # the desk pane's jump tool, Go to hung from it
        # -- the cart's scripts, a menu hung from the bar's Code tab -----------
        self._files_open = False      # the scripts menu is shown
        self._files_sel = 0           # the highlighted row (opens on the current file)
        self._ov_host = _ui_draw.AppHost()   # the overlays' UI context (look step 33)

    def seed_key(self, k):
        """The byte that was live when this surface took the keyboard is NOT a
        keystroke of its own. Called by ws._set_text_mode on the switch into
        text mode -- the key that entered the Code tab is still in last_key when
        the editor's first frame reads it, and a zeroed edge tracker types it.
        Seeding, not resetting: an already-down byte has to look like the
        PREVIOUS one, so releasing and pressing it again still types."""
        self._ekey.prev = k

    def reset(self):
        """Reset the keyboard edge tracker (called by ws.set_menu_view when the editor
        is (re)built) so the first key press after opening registers. Also drops the
        transient #89 modes so a freshly-opened editor is in a clean state.

        set_tab runs this BEFORE ws._set_text_mode, whose seed_key then supplies
        the live byte -- so a fresh editor still starts clean and still does not
        type the key that opened it."""
        self._ekey.reset()
        self._sel_drag = False
        self._find_open = False
        self._select_mode = False
        self._cmp_open = False
        self._jump_open = False
        self._files_open = False
        if self.ws.editor is not None:
            self.ws.editor.select_sticky = False

    def _is_lua(self):
        """The open project's cart language (#67 Phase 5): drives the symbol
        palette + the highlighter's comment/keyword rules. Asked of the mode
        table, which is where the shell's other text surfaces ask too."""
        proj = self.ws.project
        cart = proj.cart if proj is not None else None
        return cart is not None and _modes.cart_lang(cart) == "lua"

    def _lang(self):
        """The highlighter's language: "lua" or "python" (a subclass's own)."""
        return "lua" if self._is_lua() else "python"

    def _symbols(self):
        return symbols_for("lua" if self._is_lua() else "python")

    def wants_symbols(self):
        """Whether the Code tab lays out its symbol keys: the last input was
        a touch, which leaves a keyboard's symbols to type (D-17)."""
        return _ui_draw.input_kind() == "touch"

    def wants_tools(self):
        """Whether the Code tab shows its tools toggle and row: a touch
        glass's; a keyboard reaches each tool by its key (D-17)."""
        return _ui_draw.input_kind() == "touch"

    # -- Layer facets --------------------------------------------------------

    def draw(self, dt):
        ws = self.ws
        if self._mouse.armed and ws.editor is not None:
            self._mouse.autoscroll(ws.editor, self._px, self._py, dt, ws.code_layout.lh)
        # The code and its popup are laid out in the `mono` font's cells, so
        # they print in it (docs/theming_2026-09.md §4.7).
        was = _ui_draw.print_font(_ui_draw.font("mono")[0])
        try:
            self._draw_code()
            if self._mouse.armed:
                _ui_draw.autoscroll_mark(ws.sys_canvas, ws.theme_colors, self._mouse.anchor_x,
                                         self._mouse.anchor_y, ws.look.effective_font_scale())
        finally:
            _ui_draw.print_font(was)
        # The unified zoned bar (Stage 4 rollout): the tab ladder + PLAY + X (no SAVE,
        # #111), drawn LAST (chrome over the full-screen text) so the code editor shows
        # the SAME bar every other tab does. System canvas + responsive layout, like the
        # launcher/Settings bar. This replaces the code editor's old title + RUN/SAVE/
        # CLOSE band: PLAY/X live in the bar now, SAVE is an automatic exit-path commit.
        self.ws.bar_layer._draw_status_strip("menu")
        if self.ws.crash_popup:
            self._draw_crash_popup()       # the stop card's See why: what stopped
                                           # the run, hung under its line

    def _draw_crash_popup(self):
        """See why's popover (docs/look_2026-10.md step 33): hung under the
        line that stopped the run, after the gutter (the caret already sits
        on it), "Stopped on this line", the error in the code face, and that
        the first edit closes it; a tap anywhere, Esc (Mic on the T-Deck)
        or the first edit dismisses it (handle_pointer / _clear_err)."""
        ws = self.ws
        cv = ws.sys_canvas
        lay = ws.code_layout
        ed = ws.editor
        fs = lay.fs
        desk = _desk()
        host = self._stop_host
        if host is None:
            host = self._stop_host = _ui_draw.AppHost()
        host.begin(cv, ws.theme_colors, (0, 0, cv.w, cv.h), desk)
        x = lay.x0
        y, h = lay.y0, 0
        row = ws.code_err_row
        if ed is not None:
            x += self._gutter_cols(ed) * lay.cell
            if row is not None and ed.top <= row < ed.top + ed.ROWS:
                y, h = self._y0(lay) + (row - ed.top) * lay.lh, lay.lh
        e = 8 if desk else 4 * fs
        w = min(cv.w - x - e, 560 if desk else cv.w)
        _ui.at(x, y, w, h)
        _ui.popover("Stopped", w)
        _ui.icon("warn")
        _ui.label("Stopped on this line", _STOP_HEAD)
        _ui.label(str(ws.crash_popup).strip(), _STOP_TEXT)
        _ui.label("Your first edit closes this.", _STOP_DIM)
        _ui.hints(("cancel", "Close"))
        _ui.popover()

    def handle_input(self, i):
        if self.ws.crash_popup is not None and i.last_key == 27:
            self.ws.crash_popup = None     # Esc closes See why's popover
            self.ws.mark_dirty()
            return True
        if i.last_key or i.any_pressed():
            self._mouse.stop()         # any key ends a latched autoscroll
        if self._mouse.armed:
            self.ws.mark_dirty()       # an autoscroll moves the code on its own
        self._wheel(i)
        self._editor_input()           # keyboard is in text mode here
        return True

    def _wheel(self, i):
        """The wheel scrolls the code three lines a notch, Shift's sideways."""
        wy, wx = getattr(i, "wheel_y", 0), getattr(i, "wheel_x", 0)
        ed = self.ws.editor
        if not (wx or wy) or ed is None:
            return
        if getattr(i, "mods", 0) & _SHIFT:
            wx, wy = wx + wy, 0
        ed.scroll(3 * wy, 3 * wx)
        self.ws.mark_dirty()

    def stop_autoscroll(self):
        self._mouse.stop()

    def autoscrolling(self):
        """A middle-button autoscroll runs: the frames must keep coming."""
        return self._mouse.armed

    def _middle(self, px, py, lay):
        """The middle button's edges over the code (`TextMouse.middle`)."""
        mid = bool(getattr(self.ws.input, "pointer_buttons", 0) & _PB_MIDDLE)
        if mid != self._mid and (not mid or _in(px, py, lay.code_area())):
            self._mouse.middle(px, py, mid)
            self.ws.mark_dirty()
        self._mid = mid

    def _cell(self, px, py, lay, ed):
        """The document cell (row, col) under (px, py) in the code area."""
        col = (px - self._text_x0(lay, ed)) // lay.cell
        return ed.top + (py - self._y0(lay)) // lay.lh, ed.left + max(0, col)

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
        # The crash-to-code popup is dismissed by a tap ANYWHERE (consumed --
        # the tap under it must not edit/re-place the caret), before any other
        # claim, the bar included.
        if click and ws.crash_popup is not None:
            ws.crash_popup = None
            ws.mark_dirty()
            return True
        if click and ws.bar_layer.handle_bar_tap("menu", px, py):
            return True
        # #89 chrome, in overlay order: the tools toggle (a touch glass's, D-17),
        # then (when open) an overlay and the tool palette row, before the code.
        touch = self.wants_tools()
        if click and touch and _in(px, py, self._tls_btn(lay)):
            self._tools_open = not self._tools_open
            ws.mark_dirty()
            return True
        # An open overlay (completion, Go to, the scripts) is modal over the code
        # body: its UI context takes the tap, picking a row as it draws, and a
        # press outside its card dismisses it; the find strip takes taps on it.
        if self._cmp_open or self._jump_open or self._files_open:
            if self._ov_host.point(px, py, click, ws.pointer.down):
                ws.mark_dirty()
            return True
        if self._find_open and (self._ov_host.hold or _in(px, py, self._find_rect(lay))):
            if self._ov_host.point(px, py, click, ws.pointer.down):
                ws.mark_dirty()
            return True
        if click and touch and self._tools_open and _in(px, py, self._toolbar_rect(lay)):
            self._tool_tap(px, py, lay, ed)
            return True
        if click and _in(px, py, lay.sym_area) and ed is not None:
            syms = self._symbols()
            i = lay.sym_key_at(px)                       # tap a coding symbol
            if 0 <= i < len(syms):
                self._feed_char(ord(syms[i]))            # routes to find field or editor
            return True
        self._px, self._py = px, py    # the pointer in this layer's space
        if ed is not None:
            self._middle(px, py, lay)
        if ed is not None and ws.pointer.hovers and (self._mouse.dragging or (
                click and _in(px, py, lay.code_area()))):
            # A mouse: the press places the caret (Shift extends, a second
            # and third press take the word and the line) and its drag selects.
            row, col = self._cell(px, py, lay, ed)
            if click:
                self._mouse.press(ed, row, col, bool(getattr(ws.input, "mods", 0) & _SHIFT),
                                  _ticks_ms())
            elif ws.pointer.down:
                self._mouse.drag(ed, row, col)
                ed._scroll()
            else:
                self._mouse.release(ed)
            ws.mark_dirty()
        elif ed is not None and _in(px, py, lay.code_area()):
            if self._select_mode:
                self._select_pointer(px, py, click, lay, ed)   # drag = extend selection
            else:
                self._code_drag(px, py)                  # drag pans the viewport
                if click:
                    tx0 = self._text_x0(lay, ed)
                    col = (px - tx0) // lay.cell
                    if col < 0:
                        col = 0                           # a tap in the gutter -> column 0
                    ed.place(col, (py - self._y0(lay)) // lay.lh)
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
            if self._cmp_open or self._jump_open or self._files_open:
                if k in (0x0D, 0x0A):
                    self._popup_accept()
                    return
                if k == 27:
                    self.close_overlay()
                    return
                if self._jump_open and (32 <= k < 127 or k in (8, 127)):
                    self._jump_type(k)         # Go to's field filters the definitions
                    return
                self.close_overlay()
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
            elif self._find_open and k == 27:
                self.close_overlay()
            elif self._find_open:
                self._find_key(k)              # find field owns the keyboard while open
            elif k == 0x1A:
                ws.history.undo()
            elif k == 0x19:
                ws.history.redo()
            elif k == 0x01:                    # Ctrl+A: select all (#132 parity)
                ed.select_all()
            elif k == 0x03:
                ed.copy()
            elif k == 0x18:
                ws.history.code_burst_open()   # #111: a cut joins the typing burst
                if ed.cut():
                    self._recheck_err()
            elif k == 0x16:
                ws.history.code_burst_open()   # #111: a paste joins the typing burst
                if ed.paste():
                    self._recheck_err()
            elif k == 0x09 and getattr(ws.input, "mods", 0) & _SHIFT:
                ws.history.code_burst_open()   # Shift+Tab outdents, as << does
                if ed.outdent_selection():
                    self._recheck_err()
            else:
                # A text-editing key (printable / backspace / tab / enter). Open the
                # typing burst BEFORE the edit lands (#111 phase 4), then close it on
                # Enter -- the burst edge the spec names, alongside the autosave
                # debounce + an undo press (both handled by ws.history). ed.key
                # ignores control bytes it doesn't know, so a no-op key just leaves the
                # burst open with an unchanged pre-image (a harmless later net no-op).
                ws.history.code_burst_open()
                if ed.key(k):                  # text changed -> re-check the marker
                    self._recheck_err()
                if k in (0x0D, 0x0A):
                    ws.history.close_code_burst()
        # (self._ekey.hit above already recorded k as the new previous byte.)

    # -- #89 helpers: clipboard/find/tool/select routing ---------------------

    def _clear_err(self):
        # Hard-clear the error marker (the save-OK path; _recheck_err below is
        # what text edits call).
        ws = self.ws
        ws.code_err = None
        ws.code_err_row = None
        ws.crash_line = None
        ws.crash_popup = None

    def _recheck_err(self):
        """A text change RE-VALIDATES the marked error instead of blindly
        clearing it (owner 2026-07-23): the red underline retires only when the
        source actually parses again; while it still doesn't, the marker
        FOLLOWS the live syntax error (without moving the caret). A runtime
        crash marker can't be re-proven without a run, so a parsing source
        retires it too -- the closest static answer. The crash popup is
        transient either way: the first edit/undo dismisses it. No marker up ->
        free (typing never pays a compile).

        The gate is the cart's runtime's, asked through the same store verb the
        commit paths ask: a lua cart has no parse gate on either tier, answers
        ok, and so keeps the clear-on-edit rule it always had."""
        ws = self.ws
        ws.crash_popup = None          # typing starts the fix -- the popup is done
        if ws.code_err is None and ws.code_err_row is None:
            return
        ed = ws.editor
        check = getattr(ws.carts_store, "runtime_compile_check", None) \
            if ws.carts_store is not None else None
        if ed is None or check is None:
            self._clear_err()
            return
        ok, msg = check(ws.cart, ed.text())
        if ok:
            self._clear_err()
        else:
            ws.crash_line = None       # superseded by the live syntax position
            ws._set_code_error(msg, move=False)

    def _feed_char(self, code):
        # One typed/tapped character: into the find field while it's focused, else
        # inserted into the buffer (the symbol-palette path).
        if self._find_open:
            self._find_key(code)
        elif self.ws.editor is not None:
            self.ws.history.code_burst_open()   # #111: a palette char joins the burst
            if self.ws.editor.key(code):
                self._recheck_err()

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
            ws.history.code_burst_open()     # #111: tool-row edits join the burst
            if ed.cut():
                self._clear_err()
        elif name == "paste":
            ws.history.code_burst_open()
            if ed.paste():
                self._clear_err()
        elif name == "find":
            self._toggle_find()
        elif name == "indent":
            ws.history.code_burst_open()
            ed.indent_selection()
            self._clear_err()
        elif name == "outdent":
            ws.history.code_burst_open()
            if ed.outdent_selection():
                self._clear_err()
        elif name == "gutter":
            self._gutter = not self._gutter
        elif name == "auto":
            self._open_completion(ed)
        elif name == "goto":
            self._open_jump(ed)
        elif name == "undo":
            ws.history.undo()
        elif name == "redo":
            ws.history.redo()
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
        row = (py - self._y0(lay)) // lay.lh
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
        if ed is not None:
            self._apply_gutter(self.ws.code_layout, ed)   # the strip's rows
            ed._scroll()
        self._ov_host.reset()
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
        if code in (0x0D, 0x0A):               # Enter the next match, Shift+Enter the last
            self._find_run(not getattr(self.ws.input, "mods", 0) & _SHIFT)
        elif code in (0x08, 0x7F):             # backspace -> trim + re-search
            self._find_q = self._find_q[:-1]
            self._find_run(True, reset=True)
            self.ws.mark_dirty()
        elif 0x20 <= code <= 0x7E:             # printable -> extend + re-search
            self._find_q += chr(code)
            self._find_run(True, reset=True)
            self.ws.mark_dirty()

    def find_count(self):
        """(the current match from 1, the matches) of the find's query in the
        buffer, the current one the match at the caret or the last before
        it; (0, 0) with no query or no match."""
        ed = self.ws.editor
        q = self._find_q
        if ed is None or not q:
            return 0, 0
        if self._find_ci:
            q = q.lower()
        n = cur = 0
        for r in range(len(ed.lines)):
            line = ed.lines[r].lower() if self._find_ci else ed.lines[r]
            at = line.find(q)
            while at >= 0:
                n += 1
                if (r, at) <= (ed.row, ed.col):
                    cur = n
                at = line.find(q, at + 1)
        return (cur or (1 if n else 0)), n

    def overlay_step(self, d):
        """Up or down (the arrows, the T-Deck's trackball) in an open
        completion, Go to or the scripts: the row `d` away gets the focus.
        True where one was open."""
        if self._cmp_open:
            n = min(len(self._cmp_items), self._POPUP_MAX)
            self._cmp_sel = max(0, min(n - 1, self._cmp_sel + d))
        elif self._jump_open:
            n = min(len(self._jump_shown()), self._jump_rows())
            self._jump_sel = max(0, min(n - 1, self._jump_sel + d))
        elif self._files_open:
            n = len(self.ws.code_sources()) + (1 if self.can_add_script() else 0)
            self._files_sel = max(0, min(n - 1, self._files_sel + d))
        else:
            return False
        self._ov_host.reset()              # the next draw focuses the row picked
        return True

    def close_overlay(self):
        """Close whichever overlay is open (the find strip, completion, Go to,
        the scripts): Esc, the T-Deck's Mic (`Workstation.cancel_field`) or a
        press outside it. True where one was open."""
        was = self._find_open or self._cmp_open or self._jump_open or self._files_open
        self._find_open = self._cmp_open = self._jump_open = self._files_open = False
        if was:
            self._ov_host.reset()
            self.ws.mark_dirty()
        return was

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
        self._jump_open = self._files_open = False
        self._cmp_items = ed.completions(self._api_names())
        self._cmp_sel = 0
        self._cmp_open = bool(self._cmp_items)     # nothing to offer -> stay closed
        self._ov_host.reset()
        self.ws.mark_dirty()

    def _open_jump(self, ed):
        if ed is None:
            return
        self._cmp_open = self._files_open = False
        self._jump_items = ed.def_symbols()
        self._jump_sel = 0
        self._jump_q = ""
        self._jump_open = bool(self._jump_items)
        self._ov_host.reset()
        self.ws.mark_dirty()

    def _jump_shown(self):
        """Go to's definitions its query keeps, (name, row) in file order."""
        q = self._jump_q.lower()
        return [it for it in self._jump_items if q in it[0].lower()]

    def _jump_rows(self):
        """How many definitions Go to lists: what its card holds under its
        field on the glass."""
        return self._POPUP_MAX if _desk() else 6

    def _jump_type(self, k):
        if k in (8, 127):
            self._jump_q = self._jump_q[:-1]
        else:
            self._jump_q += chr(k)
        self._jump_sel = 0
        self.ws.mark_dirty()

    def _cmp_kind(self, word, defs):
        """What a completion is, as its row says it."""
        if word in defs:
            return "def"
        if word in _HL_BUILTINS:
            return "verb"
        if word in _HL_KEYWORDS or word in _HL_LUA_KEYWORDS:
            return "word"
        return "var"

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
            shown = self._jump_shown()
            if ed is not None and self._jump_sel < len(shown):
                _name, row = shown[self._jump_sel]
                ed.goto_row(row, self._leading_spaces(ed.lines[row]))
            self._jump_open = False
        elif self._files_open:
            # Close BEFORE the switch: open_code_file hard-commits this buffer and
            # rebuilds the editor, and a popup left open would be drawn over a
            # file it no longer describes.
            self._files_open = False
            names = self.ws.code_sources()
            if self._files_sel < len(names):
                self.ws.open_code_file(names[self._files_sel])
            elif self._files_sel == len(names) and self.can_add_script():
                self.new_script()
        self._ov_host.reset()
        self.ws.mark_dirty()

    def _leading_spaces(self, line):
        n = 0
        while n < len(line) and line[n] == " ":
            n += 1
        return n

    # -- #89 geometry (derived from CodeLayout; kept off the frozen constants) -

    def _tls_btn(self, lay):
        # The always-visible tools toggle (the pencil): at the status line's
        # right end, on the inset (the owner's D-17, 2026-10-19), never over the
        # code; the code body's top right where a layout has no status band.
        w = self._TLS_COLS * lay.cell
        band = lay.status_band
        if band is None:
            return (lay.cx + lay.cw - w, lay.y0, w, lay.lh)
        h = min(band[3], lay.lh)
        return (lay.cx + lay.cw - (lay.x0 - lay.cx) - w, band[1] + (band[3] - h) // 2, w, h)

    # -- the cart's scripts (SPEC.md 4, #89) ----------------------------------
    #
    # A menu hung from the bar's Code tab (look step 33): a tap on that tab
    # while the Code tab is open shows the cart's scripts, the open one
    # marked, and New script last where a second script would run -- the
    # door Make it mine's Files page holds too. A PICO-8 port is the cart that
    # has several (p8.lua, main.lua and a file per PICO-8 tab).

    def open_scripts(self):
        """The scripts menu, on the file the tab is already showing, so the
        focused row means "here" rather than "row 0"; the Code tab tapped
        again closes it."""
        if self._files_open:
            self.close_overlay()
            return
        self._cmp_open = self._jump_open = self._find_open = False
        names = self.ws.code_sources()
        cur = self.ws.code_file_name()
        self._files_sel = names.index(cur) if cur in names else 0
        self._files_open = bool(names)
        self._ov_host.reset()
        self.ws.mark_dirty()

    def can_add_script(self):
        """Whether the open cart may gain a script (Make it mine's rule)."""
        proj = self.ws.project
        cart = proj.cart if proj is not None else None
        return cart is not None and self.ws.cards_layer._can_add_source(cart)

    def new_script(self):
        """New script, the scripts menu's last row: Make it mine's dialog."""
        ws = self.ws
        ws.editor_app.set_tab("cards")
        ws.cards_layer._open_newf()

    def _toolbar_rect(self, lay):
        # The tool palette row, just above the status band / symbol palette.
        h = lay.lh
        top = lay.status_band[1] if lay.status_band is not None else lay.sym_y
        return (lay.cx, top - h, lay.cw, h)

    def _find_rect(self, lay):
        """The find strip over the code's top: a field's row and its ring."""
        return (lay.cx, lay.y0, lay.cw, 38 if _desk() else 28 * lay.fs)

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
        # The find strip takes the rows at the top it covers (`_top_rows`).
        if ed is None:
            return
        target = max(4, lay.cols - self._gutter_cols(ed))
        rows = max(1, lay.rows - self._top_rows(lay))
        if (ed.COLS, ed.ROWS) != (target, rows):
            ed.set_view_size(target, rows)

    def _top_rows(self, lay):
        """The lines the find strip takes from the code's top, 0 closed."""
        if not self._find_open:
            return 0
        return -(-self._find_rect(lay)[3] // lay.lh)

    def _y0(self, lay):
        """Where the code's first line draws: under the find strip, open."""
        return lay.y0 + self._top_rows(lay) * lay.lh

    def _code_drag(self, px, py):
        # Touch/mouse drag inside the code area pans the viewport (content follows
        # the finger): drag down -> see earlier lines, drag right -> see left text.
        # SYSTEM coords + layout cell/line height (#39 step 2).
        ws = self.ws
        ed = ws.editor
        lay = ws.code_layout
        if ed is None or not ws.pointer.down or not _in(px, py, lay.code_area()):
            self._drag = None
            return
        if self._drag is None:
            self._drag = (px, py, 0, 0)
            return
        self._drag = _ui_draw.pan_drag(self._drag, ed, px, py, lay.cell, lay.lh)

    # -- draw ----------------------------------------------------------------

    # The syntax colours are the theme's CONTENT roles (`code_*`, style.json's
    # theme section): the highlighter tags each char with its class (the _HL_*
    # indices above), and runs remap through the theme's roles at draw time
    # (per RUN, not per char -- the _hl memo stays untouched). The default
    # derivation gives a light surface deep, §4.2-friendly hues (black text,
    # deep teal keywords, dark green strings, brown numbers, dim warm comments,
    # dark purple cart verbs) and a dark one the classes' own colours.
    # The map is rebuilt only when the content dict changes (one shared dict
    # per theme and variant), so a draw allocates nothing.
    _hl_src = None
    _hl_val = None

    def _hl_map(self):
        c = self.ws.look.content_colors()
        if c is not self._hl_src:
            m = {_HL_TEXT: c["code_text"], _HL_KEYWORD: c["code_keyword"],
                 _HL_STRING: c["code_string"], _HL_NUMBER: c["code_number"],
                 _HL_COMMENT: c["code_comment"], _HL_BUILTIN: c["code_builtin"]}
            self._hl_val = None
            for k, v in m.items():
                if k != v:
                    self._hl_val = m
                    break
            self._hl_src = c
        return self._hl_val

    def _tones(self):
        """Per-draw color roles from the theme tokens, on every tier: the
        code surface and its chrome in the theme's roles, the caret in its
        accent as an ink (`ink_accent`, held at 3:1 on the code surface).
        The syntax remap is the theme's `code_*` content roles (`_hl_map`).
        sel (#89): the selection tint (drawn BEHIND the text), and the dim
        gutter number ink."""
        th = self.ws.theme_colors
        return _as_theme({"bg": th["surface"], "caret": th["ink_accent"],
                          "sym_bg": th["surface_alt"], "sym_edge": th["border"],
                          "sym_ink": th["ink"], "hl": self._hl_map(),
                          "sel": th["hilite"],
                          "gutter": th["ink_dim"], "gutter_cur": th["ink"]})

    def draw_lines(self, lines, first, rect):
        """Lines of code read only in `rect` from line `first`, numbered, in
        the Code tab's mono font and colours: the desk's made code
        (`editor_parts.made`). The rows it shows."""
        ws = self.ws
        cv = ws.sys_canvas
        th = ws.theme_colors
        hl = self._hl_map()
        lay = ws.code_layout
        cell, lh = lay.cell, lay.lh
        x, y, w, h = rect
        gut = (len(str(len(lines))) + 1) * cell
        rows = max(1, h // lh)
        cols = max(1, (w - gut) // cell)
        was = _ui_draw.print_font(_ui_draw.font("mono")[0])
        cv.clip(x, y, w, h)
        try:
            for k in range(rows):
                n = first + k
                if n >= len(lines):
                    break
                ly = y + k * lh
                num = str(n + 1)
                cv.print(num, x + gut - (len(num) + 1) * cell, ly, th["ink_dim"], 1)
                self._draw_code_runs(lines[n][:cols], 0, cols, ly, x + gut, hl)
        finally:
            cv.clip()
            _ui_draw.print_font(was)
        return rows

    def _draw_code(self):
        # Responsive (#39 step 2): the code editor draws on the SYSTEM canvas at
        # native size, so a bigger panel shows more lines + wider columns and a
        # bigger font scales the text. All positions come from CodeLayout (verbatim
        # baseline at 320x240/1x). The editor's COLS/ROWS already track the layout.
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        if ws.code_layout.symbols != self.wants_symbols():
            # The last input moved between touch and keys: the symbol keys
            # come or go, and the code takes their rows (D-17).
            ws._relayout_code()
        lay = ws.code_layout
        fs = lay.fs
        cell = lay.cell                          # on-screen char-cell width
        lh = lay.lh
        th = lay.text_h                          # the glyph box: caret, selection
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
            # The current line on the theme's current mark with a `value`
            # bar at its left, and a line longer than the view fading out
            # over FADE pixels at its right.
            fx = tx0 + cols * cell - _FADE * fs
            for idx in range(len(vis)):
                y = self._y0(lay) + idx * lh
                absrow = ed.top + idx
                full = vis[idx]
                on_err = errrow is not None and absrow == errrow
                cur = absrow == ed.row
                fade = len(full) - ed.left > cols
                if cur:
                    self._current_line(cv, lay, y, lay.mark_x, fx if fade else None)
                if gc:                           # #89: line-number gutter (crash mark integrated)
                    num = str(absrow + 1)[-(gc - 1):]
                    cv.print(num, lay.x0 + (gc - 1 - len(num)) * cell, y,
                             NAMES["red"] if on_err else
                             (t["gutter_cur"] if cur else t["gutter"]), 1)
                elif on_err:                     # no gutter: the crash tick, on the mark's place
                    cv.rect(lay.mark_x, y, 2 * fs, th, NAMES["red"])
                if on_err:                       # inline error underline (#24)
                    cv.rect(tx0, y + th, cols * cell, fs, NAMES["red"])
                self._draw_selection(ed, absrow, y, tx0, cell, cols, th, t["sel"])
                self._draw_find_matches(ed, absrow, full, y, tx0, cell, cols, th)
                self._draw_code_runs(full, ed.left, cols, y, tx0, t.get("hl"))
                if on_err and ws.code_err:      # short reason after the code, if it fits
                    mcol = max(0, min(cols, len(full) - ed.left)) + 1
                    if mcol < cols - 2:
                        cv.print(ws.code_err[:cols - mcol],
                                 tx0 + mcol * cell, y, NAMES["red"], 1)
                if fade:
                    self._fade(cv, fx, y, lh, t["bg"], fs)
                    if cur:
                        self._current_line(cv, lay, y, fx, None)
                if absrow == ed.row:            # caret on the cursor's line
                    vcol = ed.col - ed.left
                    if 0 <= vcol <= cols:
                        # One scale wide, in the column before the cell: a
                        # mono face leaves its cells two blank columns between
                        # glyphs, and a caret filling both read as the next
                        # stem's bold (design review R3-24).
                        cv.rect(tx0 + vcol * cell - fs, y + fs, fs, th - 2 * fs, t["caret"])
        if lay.status_band is not None and ed is not None:
            self._draw_status(cv, lay, ed)
        self._draw_tools(lay, ed, t)             # #89: tools toggle + palette + find bar
        self._draw_symbols()

    def _draw_status(self, cv, lay, ed):
        """Status band (Phase 3, shelf tiers): the mockup's "Ln 13, Col 1"
        strip, the band across the content and its text on the code's
        column."""
        ws = self.ws
        issues = "1 issue" if ws.code_err else "No issues"
        _ui_draw.status_row(cv, ws.theme_colors, lay.status_band, ())
        _ui_draw.status_row(cv, ws.theme_colors, lay.status_text, (_status_line(
            ("Ln " + str(ed.row + 1) + ", Col " + str(ed.col + 1),
             str(len(ed.lines)) + " lines", issues), lay.status_text[2], lay.fs),))

    def _current_line(self, cv, lay, y, x0, x1):
        """The current line's mark, the columns [x0, x1) of it (x1 None: to
        the area's right): the theme's current mark (a `bar` beside the line,
        as beside a row) laid over the WHOLE line from `mark_x` (inside the
        content inset, clear of the frame) and clipped to those columns, so
        a line drawn in two parts around its fade is one mark -- one outline,
        one plate -- and the `value` bar where the part starts at `mark_x`."""
        right = lay.cx + lay.cw
        left = lay.mark_x
        x1 = right if x1 is None else x1
        if x1 <= x0:
            return
        th = self.ws.theme_colors
        whole = x0 == left and x1 == right
        if not whole:
            cv.clip(x0, y, x1 - x0, lay.lh)
        _ui_draw.current(cv, th, (left, y, right - left, lay.lh), side=True, keep=True)
        if not whole:
            cv.clip()
        if x0 == left:
            cv.rect(x0, y, 2 * lay.fs, lay.lh, th["value"])

    def _fade(self, cv, x, y, h, bg, fs):
        """A line longer than the view fades into `bg` over the FADE pixels
        from x: three steps, each a band of `bg` at a rising alpha."""
        step = _FADE * fs // len(_FADE_ALPHA)
        for i in range(len(_FADE_ALPHA)):
            cv.shadow(x + i * step, y, step, h, 0, bg, _FADE_ALPHA[i], 0)

    def _runs(self, line):
        """Memoized per-line syntax highlight (#24) as its runs: a flat list
        of (start, end, colour, text) for each stretch of one colour, the
        stretch's own text kept with it so a frame prints the memo's strings
        and slices none (a slice is a new string on the heap, per stretch per
        line per frame). Lines recur every frame, so cache by text; a memo
        holds one language's colours, and a switch of language (a python
        project for a lua one) empties it.

        The bound is TWO generations, not a clear. Dragging through a long file
        walks past the bound, and emptying the memo there re-highlights the whole
        visible window inside ONE frame -- a felt hitch, mid-drag, on exactly the
        gesture the memo exists to make cheap. Retiring the older generation
        leaves the lines still on screen one lookup away and caps the memory at
        what the single larger bound it replaces held."""
        lang = self._lang()
        if lang != self._hl_lua:
            self._hl_lua = lang
            self._hl_cache = {}
            self._hl_old = {}
        runs = self._hl_cache.get(line)
        if runs is None:
            runs = self._hl_old.get(line)
            if runs is None:
                runs = (_runs_of(line, _highlight_json(line)) if lang == "json"
                        else _highlight_runs(line, lang == "lua"))
            if len(self._hl_cache) >= _HL_MEMO:
                self._hl_old = self._hl_cache
                self._hl_cache = {}
            self._hl_cache[line] = runs
        return runs

    def _draw_code_runs(self, line, left, cols, y, x0, hl):
        """Draw the columns [left, left+cols) of one code line as runs of
        same-colored text (#24), each through `hl` (the theme's tones) when
        it has one. On the SYSTEM canvas at the layout's char-cell width
        (8*fs), so it scales with the font. `x0` is the text origin (shifted
        right by the #89 line-number gutter). A run the window cuts is sliced;
        a whole one prints its memoized text."""
        cv = self.ws.sys_canvas
        cell = self.ws.code_layout.cell
        runs = self._runs(line)
        right = left + cols
        for k in range(0, len(runs), 4):
            a = runs[k]
            b = runs[k + 1]
            if b <= left:
                continue
            if a >= right:
                break
            text = runs[k + 3]
            if a < left or b > right:
                if a < left:
                    a = left
                if b > right:
                    b = right
                text = line[a:b]
            cl = runs[k + 2]
            cv.print(text, x0 + (a - left) * cell, y,
                     hl.get(cl, cl) if hl else cl, 1)

    def _visible_span(self, ed, s, e, cols):
        """Clip a column span [s, e) to the horizontally-scrolled visible window,
        returning the visible (start_col, end_col) or None when it's off-screen."""
        vs = s if s > ed.left else ed.left
        we = ed.left + cols
        ve = e if e < we else we
        if ve <= vs:
            return None
        return vs, ve

    def _draw_selection(self, ed, absrow, y, tx0, cell, cols, h, color):
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
        cv.rect(tx0 + (vs - ed.left) * cell, y, (ve - vs) * cell, h, color)

    def _draw_find_matches(self, ed, absrow, line, y, tx0, cell, cols, h):
        """Tint every occurrence of the find query on one visible row (#89),
        under the code: the current one, at the caret, gets a bar beneath."""
        q = self._find_q
        if not (self._find_open and q):
            return
        hay = line.lower() if self._find_ci else line
        qq = q.lower() if self._find_ci else q
        L = len(qq)
        cv = self.ws.sys_canvas
        value = self.ws.theme_colors["value"]
        bar = max(1, h // 7)
        start = 0
        while True:
            i = hay.find(qq, start)
            if i < 0:
                break
            span = self._visible_span(ed, i, i + L, cols)
            if span is not None:
                vs, ve = span
                mx, mw = tx0 + (vs - ed.left) * cell, (ve - vs) * cell
                cv.shadow(mx, y, mw, h, 0, value, _MATCH_ALPHA, 0)
                if absrow == ed.row and i == ed.col:
                    cv.rect(mx, y + h - bar, mw, bar, value)
            start = i + (L if L > 0 else 1)

    # The tool/find buttons ARE `ui.chip`s (Phase 3d): the private `_panel_btn`
    # copy is gone. What stays here is only the chip's FACE -- the part ui.py is
    # deliberately blind to -- handed to `chip` as its `glyph_draw` callback, the
    # same seam `game_icon_btn` already uses to keep the glyph vocabulary out of
    # the leaf.

    def _btn_spec(self, label, glyph):
        """The face spec for one chip: the chrome glyph KIND when this button has
        a known one (#89 icon pass), else its text label. One value, so the chip
        call site passes one argument and `_btn_face` needs no unpacking."""
        return glyph if (glyph is not None and _glyph_known(glyph)) else label

    def _btn_face(self, spec, rect, ink, cv):
        """Draw a chip's face -- `ui.chip`'s `glyph_draw` callback.

        Two things are preserved verbatim from the `_panel_btn` this replaced,
        because both are frozen pixels:

          * the ink is the chrome's ONE `sym_ink` in BOTH looks (the field flips
            for `active`, the ink does not), where chip's own `on` ink is black;
          * a text label centres on the button CELL, which `_chip_rect` made one
            pixel wider than the field the chip filled.
        """
        t = self._t
        if t is not None:
            ink = t["sym_ink"]
        if _glyph_known(spec):
            self.ws._glyph(spec, rect, ink, cv)
            return
        if not spec:
            return
        lay = self.ws.code_layout
        fs = lay.fs
        gx = rect[0] + (rect[2] + 1 - len(spec) * lay.cell) // 2
        if gx < rect[0] + fs:
            gx = rect[0] + fs
        cv.print(spec, gx, rect[1] + (rect[3] + 1 - lay.text_h) // 2, ink, 1)

    def _draw_tools(self, lay, ed, t):
        """The overlays over the code body, drawn AFTER the text so they sit
        on top and never move the layout: a touch glass's tools toggle and
        tool row (#89; a keyboard has the keys, D-17), the find strip, and
        completion, Go to and the scripts as popovers (look step 33). Their
        keys sit in the status line."""
        cv = self.ws.sys_canvas
        fs = lay.fs
        if self.wants_tools():
            # `glyph` carries the FACE spec (a glyph kind or the label text) and
            # `_btn_face` draws it -- ui.py stays blind to the chrome vocabulary.
            _ui_draw.chip(cv, t, _chip_rect(self._tls_btn(lay)), "TLS",
                          on=self._tools_open, glyph=self._btn_spec("TLS", "tools"),
                          glyph_draw=self._btn_face, fs=fs)
            if self._tools_open:
                r = self._toolbar_rect(lay)
                n = len(self._TOOLS)
                bw = r[2] // n
                for i in range(n):
                    name = self._TOOLS[i]
                    active = (name == "sel" and self._select_mode) or \
                             (name == "find" and self._find_open) or \
                             (name == "gutter" and self._gutter)
                    label = self._TOOL_LABEL[name]
                    _ui_draw.chip(cv, t, _chip_rect((r[0] + i * bw, r[1], bw, r[3])),
                                  label, on=active,
                                  glyph=self._btn_spec(label, self._TOOL_GLYPH.get(name)),
                                  glyph_draw=self._btn_face, fs=fs)
        if not (self._find_open or self._cmp_open or self._jump_open or self._files_open):
            return
        ws = self.ws
        host = self._ov_host
        host.begin(cv, ws.theme_colors, (lay.cx, lay.y0, lay.cw, lay.ch - (lay.y0 - lay.cy)),
                   _desk())
        if self._find_open:
            self._draw_find(lay, ed, t)
        elif self._cmp_open:
            self._draw_completion(lay, ed)
        elif self._jump_open:
            self._draw_jump(lay)
        else:
            self._draw_files(lay)
        keys = (_FIND_KEYS if self._find_open else _CMP_KEYS if self._cmp_open
                else _JUMP_KEYS if self._jump_open else _FILES_KEYS)
        band = lay.status_band
        if band is not None and _ui_draw.hint_kind() != "touch":
            _ui_draw.status_row(cv, ws.theme_colors, band, ())
            _ui.at(band[0], band[1], band[2], band[3])
            _ui.hints(keys)
        if host.owed or host.tap == 2:
            ws.request_frame()

    def _draw_find(self, lay, ed, t):
        """The find strip (look step 33): a field holding the query in the
        code face, the count ("2 of 5") and Aa, its state shown, under the bar;
        on the desk the arrows and the close too, a pointer's targets. Enter
        moves from the caret, Shift+Enter back."""
        cv = self.ws.sys_canvas
        fs = lay.fs
        desk = _desk()
        x, y, w, h = self._find_rect(lay)
        cv.rect(x, y, w, h, t["bg"])
        pad = 3 * fs
        cur, n = self.find_count()
        count = ("%d of %d" % (cur, n)) if self._find_q else ""
        cw = _ui_draw.text_width(count, fs, "ui") + 2 * pad if count else 0
        bw = 28 * fs
        tools = 3 if desk else 0
        fw = w - 2 * pad - cw - bw - tools * (bw + pad) - pad
        _ui.at(x + pad, y + pad, fw, h - 2 * pad)
        _ui.field("", self._find_q, 64, _FIND_FIELD)
        bx = x + pad + fw + pad
        if count:
            _ui.at(bx, y, cw, h)
            _ui.label(count, _DIM)
            bx += cw
        _ui.at(bx, y + pad, bw, h - 2 * pad)
        if _ui.button("Aa", "toggle", {"current": not self._find_ci, "id": "case"}):
            self._find_ci = not self._find_ci
            self._find_run(True, reset=True)
        bx += bw + pad
        for icon, word in (("arrow_up", "Back"), ("arrow_down", "Next"), ("close", "Close")) \
                if desk else ():
            _ui.at(bx, y + pad, bw, h - 2 * pad)
            if _ui.tool(icon, word):
                if icon == "close":
                    self.close_overlay()
                else:
                    self._find_run(icon == "arrow_down")
            bx += bw + pad

    def _word_rect(self, lay, ed):
        """The word being typed on the glass, (x, y, w, h): what completion
        hangs under."""
        plen = len(ed.word_prefix()) if ed is not None else 0
        x = self._text_x0(lay, ed) + (ed.col - ed.left - plen) * lay.cell
        y = self._y0(lay) + (ed.row - ed.top) * lay.lh
        return (x, y, max(lay.cell, plen * lay.cell), lay.lh)

    def _overlay_menu(self, label, items, sel, icons=()):
        """`ui.menu` of an overlay's rows ("name\tvalue"), the row `sel` its
        focus where nothing else holds it: the row picked, else -1."""
        if sel < len(items) and _ui.focus() not in items:
            _ui.focus(items[sel])
        return _ui.menu(label, items, icons)

    def _draw_completion(self, lay, ed):
        """Completion (look step 33): `ui.menu`'s card under the word being
        typed, each name and what it is."""
        fs = lay.fs
        defs = [d[0] for d in ed.def_symbols()] if ed is not None else []
        words = self._cmp_items[:self._POPUP_MAX]
        items = [w + "\t" + self._cmp_kind(w, defs) for w in words]
        _ui.at(*self._word_rect(lay, ed))
        shown = _ui.popover("complete", 220 if _desk() else 150 * fs)
        got = self._overlay_menu("", items, self._cmp_sel)
        f = _ui.focus()
        if f in items:
            self._cmp_sel = items.index(f)
        if got >= 0:
            self._cmp_sel = got
            self._popup_accept()
        _ui.popover()
        if not shown:
            self.close_overlay()

    def _draw_jump(self, lay):
        """Go to (look step 33): a popover with its own field over the cart's
        definitions and their lines; what is typed filters them."""
        fs = lay.fs
        w = min(lay.cw - 16 * fs, 420 if _desk() else 228 * fs)
        a = self.jump_anchor if _desk() else None
        items = ["%s\tline %d" % (n, r + 1) for n, r in self._jump_shown()[:self._jump_rows()]]
        if a is not None:
            _ui.at(*a)
        else:
            _ui.at(lay.cx + (lay.cw - w) // 2, lay.y0 + 4 * fs, w, 0)
        shown = _ui.popover("goto", w)
        _ui.field("", self._jump_q, 32, _JUMP_FIELD)
        got = self._overlay_menu("", items, self._jump_sel)
        f = _ui.focus()
        if f in items:
            self._jump_sel = items.index(f)
        if got >= 0:
            self._jump_sel = got
            self._popup_accept()
        _ui.popover()
        if not shown:
            self.close_overlay()

    def _draw_files(self, lay):
        """The cart's scripts (look step 33): a menu hung from the bar's
        Code tab, each script and its size, the open one marked, and New
        script last where a second script would run."""
        ws = self.ws
        fs = lay.fs
        names = ws.code_sources()
        items = []
        icons = []
        for n in names:
            size = ws.code_source_size(n)
            kb = ("%.1f KB" % (size / 1024.0)) if size is not None else ""
            items.append(n + "\t" + kb if kb else n)
            icons.append("code")
        if self.can_add_script():
            items.append(_NEW_SCRIPT)
            icons.append("new")
        anchor = ws.editor_app.tab_box("code") or (lay.cx, lay.y0, lay.cw // 2, 0)
        _ui.at(*anchor)
        shown = _ui.popover("scripts", 260 if _desk() else 200 * fs)
        got = self._overlay_menu("Scripts", items, self._files_sel, icons)
        f = _ui.focus()
        if f in items:
            self._files_sel = items.index(f)
        if got >= 0:
            self._files_sel = got
            self._popup_accept()
        _ui.popover()
        if not shown:
            self.close_overlay()

    def _draw_symbols(self):
        # Tappable coding-symbol palette (supplies what the keyboard can't type). On
        # the SYSTEM canvas; cell + text scale with the layout/font (#39 step 2).
        cv = self.ws.sys_canvas
        lay = self.ws.code_layout
        if not lay.symbols:
            return                       # a keyboard types them (D-17)
        fs = lay.fs
        sc = lay.sym_cell
        sy = lay.sym_y
        sh = lay.sym_h
        draw_symbol_keys(cv, self.ws.theme_colors, self._symbols(), lay.sym_area[0], sy, sc,
                         sh, fs, w=lay.sym_area[2])
