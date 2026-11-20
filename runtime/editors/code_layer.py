# Map (grep -n a name to jump there):
#   -- code-editor geometry           the code tab's constants (console.py imports them back)
#   symbols_for                       the tappable symbol palette for a source language
#   draw_symbol_keys                  the symbol palette renderer
#   -- code-editor syntax highlighting  the token colours
#   CodeLayer                         the host of the Code tab's page: the buffer, keys, pointer
"""The Editor's Code tab (#24/#39): the host of its `uitree` page
(runtime/editors/code_ui.py).

The page is the tab: the text editor over the cart's script, the find
strip, completion, Go to, the scripts menu, See why's popover, the status
line, and on a touch glass the tool row and the symbol keys (the T-Deck
keyboard cannot type `=[]{}<>%`). This layer hosts it in the Code tab's
content rect (`ws.code_layout`, which the desk's pane sets) under the
shell's zoned bar, and is the engine's side of what the page cannot own
yet:

- the document adopts the console's buffer, `ws.editor` (built by
  `ws.text.set_menu_view`), and its journal, the Editor's per-tab history
  (`ws.history`'s code bursts): an edit opens a burst, Enter closes it,
  and it re-checks the marked error (`_recheck_err`);
- the console's values the page shows (the error mark, See why's text,
  read-only, the input kind) are written into its state before each frame
  (`_sync_page`);
- the arrows reach the caret through the console (`ws.router.nav`), and an
  open popover's rows through `overlay_step`; a held key's repeats and the
  wheel come here;
- the pointer in the text: a tap places the caret, a mouse's press, drag
  and middle button select and autoscroll (`TextMouse`), select mode's drag
  extends, a drag scrolls the view.

There is no SAVE: every exit path commits (#111). The code-only constants
and the syntax highlighter's words live here (console.py and chrome.py
import the constants back)."""
from editors_code import CodeEditor
from array import array as _array

import ui as _ui
import ui_draw as _ui_draw
from layers import Layer
import layout_base as _lb
_in = _ui.rect_in   # one hit-test (ui.rect_in)


def _desk():
    return _lb.metric_class() == _lb.DESK
from moy_input import PB_MIDDLE as _PB_MIDDLE, SHIFT as _SHIFT
from editors_code import TextMouse
from ticks import _ticks_ms

# The cart API's names, once (see runtime/run/cart_verbs.py) -- the syntax
# highlighter's builtin class is derived from them below.
from cart_verbs import CART_VERBS as _CART_VERBS

# The console-wide editor MODE table (docs/theme_and_shell_2026-11.md §10): this tab
# edits a cart's MAIN file, so its language and its parse gate are the `code`
# mode's, asked here rather than re-derived.
import text_modes as _modes

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
# The token classes (their MOY64 palette indices name them) and the words each
# language colours, installed into the text view's C highlight
# (native/moy_ui/moy_ui_text.c) below:
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
# Cart-API verbs + the common builtins a kid actually types.
#
# The verb half is DERIVED (runtime/run/cart_verbs.CART_VERBS, pinned against
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
# The classes in the text view's tone order (moy_ui_text.h's MOY_UI_TT_*), and
# each language's words in it: the builtins, then the keywords over them.
_HL_CLASSES = (_HL_TEXT, _HL_KEYWORD, _HL_STRING, _HL_NUMBER, _HL_COMMENT, _HL_BUILTIN)
for _lang, _kw in (("python", _HL_KEYWORDS), ("lua", _HL_LUA_KEYWORDS)):
    _ui_draw.text_words(_lang, _HL_BUILTINS, _HL_CLASSES.index(_HL_BUILTIN))
    _ui_draw.text_words(_lang, _kw, _HL_CLASSES.index(_HL_KEYWORD))
# What a popover's shadow reaches past its card, at the font scale: the rows
# under it draw again the frame after.
_POP_PAD = 32


class _Journal:
    """The document's bursts, undo and edits: the Editor's per-tab history
    (`ws.history`), and the error mark the host re-checks on an edit."""

    def __init__(self, layer):
        self.layer = layer

    def open(self):
        self.layer.ws.history.code_burst_open()

    def close(self):
        self.layer.ws.history.close_code_burst()

    def undo(self):
        return bool(self.layer.ws.history.undo())

    def redo(self):
        return bool(self.layer.ws.history.redo())

    def can_undo(self):
        return bool(self.layer.ws.history.can_undo())

    def can_redo(self):
        return bool(self.layer.ws.history.can_redo())

    def edited(self):
        self.layer._recheck_err()


class _Damage:
    def __init__(self, ws):
        self.ws = ws

    def all(self):
        self.ws.mark_dirty()


# a tool of the touch row (and the desk pane's `_CODE_MORE`) -> its action
_TOOL_ACTION = {"sel": "select_mode", "copy": "copy", "cut": "cut", "paste": "paste",
                "find": "find", "indent": "indent", "outdent": "outdent", "gutter": "gutter",
                "auto": "complete_open", "goto": "goto_open", "undo": "undo", "redo": "redo"}


class CodeLayer(Layer):
    """The Code tab's content Layer (system domain): hosts the tab's page
    (`code_ui.code_page`) over the console's buffer; see the module's
    docstring for what is the host's."""

    id = "code"
    domain = "system"
    PAGE = "code_page"

    def ball_steps(self):
        """The trackball is the caret's arrows (`nav`)."""
        return False

    def typing(self):
        """The code editor types, always."""
        return True

    def __init__(self, ws, names):
        import code_ui
        import uitree_doc
        import uitree_editor
        self.ws = ws
        self._NAMES = names
        self._ui_mod = code_ui
        code_ui._ws[0] = ws
        self._host = _ui_draw.AppHost()
        self.page = uitree_editor.Page(getattr(code_ui, self.PAGE), self._host, _Damage(ws))
        self.page.host_arrows = True
        self.page.pointer_down = lambda: bool(ws.pointer.down)
        self._journal = _Journal(self)
        self._docb = uitree_doc.DocBuf("", "python")    # this tab's side of `s.code`
        self._opened = False
        self._pop = _array("i", [0, 0, 0, 0])   # a popover's card, as the page's pass drew it
        self._left = 0                # the first column the view drew
        self._sk = [None, None, None, None, None, None]   # what _sync wrote last
        self._mouse = TextMouse()     # a mouse's clicks, selection and autoscroll
        self._mid = False             # the middle button was held last frame
        self._sel_drag = False        # a select-mode drag is in progress this gesture
        self._px = self._py = 0       # the pointer as handle_pointer last saw it
        self.page_held = False        # a press on the page's parts holds to its release
        self.run("fresh")

    # -- what the page's handlers ask of the host --------------------------------

    def _ctx(self):
        """This tab's page is the one the module's handlers and state are
        for: the engine binds its own values (two hosts in one process, the
        Code tab and Config, two consoles in a test, each keep theirs), and
        its document is this tab's, over the console's buffer."""
        mod = self._ui_mod
        mod._host[0] = self
        mod._ws[0] = self.ws
        self.page._bind()
        st = mod.s
        st._v["code"] = self._docb
        ed = self.ws.editor
        if ed is not None:
            self._docb.adopt(ed, self._journal, self._lang())

    @property
    def s(self):
        """The page's state, this tab's values in it."""
        self._ctx()
        return self._ui_mod.s

    def _is_lua(self):
        """The open project's cart language (#67 Phase 5): the symbol keys
        and the highlighter's rules. Asked of the mode table, where the
        shell's other text surfaces ask too."""
        proj = self.ws.run.project
        cart = proj.cart if proj is not None else None
        return cart is not None and _modes.cart_lang(cart) == "lua"

    def _lang(self):
        """The highlighter's language: "lua" or "python" (a subclass's own)."""
        return "lua" if self._is_lua() else "python"

    def _symbols(self):
        return symbols_for("lua" if self._is_lua() else "python")

    symbols = _symbols

    def api_names(self):
        """Completion's names for the open project's language (#67): the
        cart-API verbs and the language's keywords."""
        if self._is_lua():
            return _HL_LUA_KEYWORDS + _HL_BUILTINS
        return _HL_KEYWORDS + _HL_BUILTINS

    def wants_symbols(self):
        """Whether the Code tab shows its symbol keys: the last input was a
        touch, which leaves a keyboard's symbols to type (D-17)."""
        return _ui_draw.input_kind() == "touch"

    def wants_tools(self):
        """Whether the Code tab shows its tools toggle and row: a touch
        glass's; a keyboard reaches each tool by its key (D-17)."""
        return _ui_draw.input_kind() == "touch"

    def can_add_script(self):
        """Whether the open cart may gain a script (Make it mine's rule)."""
        proj = self.ws.run.project
        cart = proj.cart if proj is not None else None
        return cart is not None and self.ws.cards_layer._can_add_source(cart)

    def new_script(self):
        """New script, the scripts menu's last row: Make it mine's dialog."""
        ws = self.ws
        ws.editor_app.set_tab("cards")
        ws.cards_layer._open_newf()

    def symbol_at(self, x):
        """The symbol key at x across the strip (`draw_symbols`'s cells), or -1."""
        got = self.page.canvases.get("symbols") if getattr(self.page, "canvases", None) else None
        if not got:
            return -1
        fs = max(1, getattr(self.ws.sys_canvas, "font_scale", 1))
        ins = _CODE_INSET * fs - fs
        w = got[1][2] - 2 * ins
        n = len(self._symbols())
        x -= ins
        if not 0 <= x < w:
            return -1
        return ((x + 1) * n - 1) // w

    def draw_symbols(self, cv, x, y, w, h):
        """The symbol keys across the strip, the first key's edge and the
        last's on the page's text column inset (design review R3-10)."""
        fs = max(1, getattr(cv, "font_scale", 1))
        ins = _CODE_INSET * fs - fs
        draw_symbol_keys(cv, self.ws.theme_colors, self._symbols(), x + ins, y, 0, h, fs,
                         w=w - 2 * ins)

    def strip_moved(self):
        """The find strip came or went: the code's rows move under it."""
        self._host.whole()
        self.ws.mark_dirty()

    # -- the page's state the host reads and writes (the Editor and the desk) -----

    def run(self, name, *args):
        """Run the page's action or hook `name` now (a bar's, a pane's tool)."""
        self._ctx()
        act = self.page.actions[name]
        self.page.run_action(act, args, "host")
        self.ws.mark_dirty()

    @property
    def _find_open(self):
        return self.s.finding

    @property
    def _find_q(self):
        return self.s.find_q

    @_find_q.setter
    def _find_q(self, q):
        self.run("set_find", q)

    @property
    def _find_ci(self):
        return self.s.find_ci

    @_find_ci.setter
    def _find_ci(self, ci):
        self.run("set_case", bool(ci))

    @property
    def _cmp_open(self):
        return self.s.over == "complete"

    @property
    def _jump_open(self):
        return self.s.over == "goto"

    @property
    def _files_open(self):
        return self.s.over == "scripts"

    @property
    def _gutter(self):
        return self.s.gutter_on

    @property
    def _tools_open(self):
        return self.s.tools_open

    @property
    def _select_mode(self):
        return self.s.select

    def _toggle_find(self):
        self.run("find")

    def _run_tool(self, name, ed):
        """A tool of the touch row or the desk pane's menu, by its name."""
        if ed is not None:
            self.run(_TOOL_ACTION[name])

    def _open_completion(self, ed):
        if ed is not None:
            self.run("complete_open")

    def _open_jump(self, ed):
        if ed is not None:
            self.run("goto_open")

    def open_scripts(self):
        """The scripts menu (the bar's Code tab tapped again); a second tap
        closes it."""
        self.run("scripts_open")

    def find_count(self):
        """(the current match from 1, the matches) of the find's query, the
        current one the match at the caret or the last before it."""
        b = self._buf()
        if self.ws.editor is not None:
            b.adopt(self.ws.editor, self._journal, self._lang())
        b.find_q = self.s.find_q
        b.find_ci = self.s.find_ci
        return b.count()

    def box(self, id):
        """Where the page drew stop `id` (an action's button, a field, a
        popover's row "ROWKEY/ACTION") last frame: (x, y, w, h), or None."""
        return self._host.box(id)

    def row_box(self, cur, key):
        """Where row `key` of the popover list whose cur= is `cur` drew last
        frame: (x, y, w, h), or None."""
        lst = next((l for k, l in self.page.curs if k == cur), None)
        sid = self.page._row_stop_id(lst, key) if lst is not None else None
        return self._host.box(sid) if sid else None

    def view(self):
        """The code's rows as the page drew them last: (their rect, the first
        line shown, the cell's width, the line pitch, the text's x)."""
        v = self.page.views.get("code")
        return tuple(v[:5]) if v is not None else None

    def overlay_step(self, d):
        """Up or down (the arrows, the T-Deck's trackball) in an open
        completion, Go to or the scripts: the row `d` away. True where one
        was open."""
        self._ctx()
        return self.page.step_rows(d)

    def close_overlay(self):
        """Close whichever overlay is open (the find strip, completion, Go
        to, the scripts). True where one was open."""
        s = self.s
        if s.over != "none":
            self.run("overlay_close")
            return True
        if s.finding:
            self.run("find_close")
            return True
        return False

    def cancel(self):
        """Mic, Esc or B over the editor: See why's popover closes, else an
        open overlay."""
        ws = self.ws
        if ws.crash_popup is not None:
            ws.crash_popup = None
            ws.mark_dirty()
            return True
        return self.close_overlay()

    back = cancel

    def reset(self):
        """A fresh editor (`ws.text.set_menu_view`): no overlay, no select
        mode, focus on the code. The key down as it opens is spent
        (`spend_key`), so a fresh editor does not type the key that opened
        it."""
        self.ws.input.spend_key()
        self._sel_drag = False
        self._ctx()
        s = self.s
        if s.finding or s.over != "none" or s.select:
            self.run("reset")
        self.page.open(hook="none")
        if self.ws.editor is not None:
            self.ws.editor.select_sticky = False

    # -- the error mark ---------------------------------------------------------

    def _clear_err(self):
        ws = self.ws
        ws.code_err = None
        ws.code_err_row = None
        ws.run.player.crash_line = None
        ws.crash_popup = None

    def _recheck_err(self):
        """A text change RE-VALIDATES the marked error instead of blindly
        clearing it (owner 2026-07-23): the mark retires only when the
        source parses again; while it does not, it follows the live syntax
        error (without moving the caret). A runtime crash's mark cannot be
        re-proven without a run, so a parsing source retires it too. See
        why's popover is done at the first edit either way. With no mark up
        typing never pays a compile. The gate is the cart's runtime's: a lua
        cart has none, answers ok, and so keeps clear-on-edit."""
        ws = self.ws
        ws.crash_popup = None
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
            ws.run.player.crash_line = None
            ws.saves.set_code_error(msg, move=False)

    # -- the frame ----------------------------------------------------------------

    def _buf(self):
        """The page's document (the engine's side of `s.code`)."""
        return self.s._v["code"]

    def _sync_page(self):
        """The console's values the page shows, handed to its `console` hook
        where they moved: the buffer adopted, the error mark, See why's text,
        read-only, the input kind (and Config's note, `_note`)."""
        ws = self.ws
        ed = ws.editor
        k = self._sk
        stop = ws.crash_popup
        ro = bool(ed is not None and ed.read_only)
        touch = self.wants_tools()
        note = self._note()
        if (k[0] != ws.code_err_row or k[1] != ws.code_err or k[2] is not stop or k[3] != ro
                or k[4] != touch or k[5] != note):
            k[0], k[1], k[2], k[3], k[4], k[5] = ws.code_err_row, ws.code_err, stop, ro, touch, note
            self.run("console", (ws.code_err_row, ws.code_err, str(stop).strip() if stop else "", ro,
                                 _ui_draw.s("code.read_only") if ro else "", touch,
                                 self.PAGE == "code_page", note))

    def _note(self):
        """Config's status line: (its words, whether they are a note)."""
        return ("", False)

    def _rect(self):
        lay = self.ws.code_layout
        return (lay.cx, lay.cy, lay.cw, lay.ch)

    def _frame_keys(self):
        """The Editor's keys the page's band names while the text holds the
        keys (Config's), else none."""
        return ()

    def draw(self, dt):
        ws = self.ws
        self._ctx()
        if not self._opened:
            self._opened = True
            self.page.open(hook="none")
        if ws.code_layout.symbols != self.wants_symbols():
            ws.relayout_code()           # the last input moved between touch and keys
        ed = ws.editor
        v = self.page.views.get("code")
        if self._mouse.armed and ed is not None and v is not None:
            self._mouse.autoscroll(ed, self._px, self._py, dt, v[3])
        self._sync_page()
        host = self._host
        if ed is not None and v is not None:
            if ed.top != v[1]:
                # the editor moved its top (a key, a typed line, Go to): the
                # view lands there as this frame's shift
                _ui_draw.text_aim(host, "code", ed.top, len(ed.lines))
            if ed.left != self._left:
                # the columns scrolled across: every row changes, so the frame
                # draws whole rather than first moving the last frame's pixels
                self._left = ed.left
                host.whole()
        cv = ws.sys_canvas
        desk = _desk()
        r = self._rect()
        self.page.text_keys = self._frame_keys()
        # the page grounds the canvas round its rect too: a handheld's bar
        # band, whose strip the bar draws over it, is never the last frame's
        self.page.ground = None if desk else (0, 0, cv.w, cv.h)
        host.begin(cv, ws.theme_colors, r, desk)
        self.page.paint(dt, cv, ws.theme_colors, r, desk)
        _ui.end()
        b = self._pop
        if host.popped(b) and b[2] > 0 and b[3] > 0:
            pad = _POP_PAD * max(1, getattr(cv, "font_scale", 1))
            _ui_draw.text_cover(host, "code", b[1] - pad, b[1] + b[3] + pad)
        if not host.still():
            ws.request_frame()
        if self._mouse.armed and v is not None:
            s = ws.look.effective_font_scale()
            ay = self._mouse.anchor_y
            _ui_draw.autoscroll_mark(cv, ws.theme_colors, self._mouse.anchor_x, ay, s)
            _ui_draw.text_cover(host, "code", ay - 7 * s, ay + 7 * s)
        # the zoned bar, drawn last (chrome over the text): the tab ladder,
        # undo, redo and PLAY, as on every Editor tab
        ws.bar_layer.draw_status_strip("menu")

    # -- input --------------------------------------------------------------------

    def handle_input(self, i):
        ws = self.ws
        self._ctx()
        if ws.crash_popup is not None and i.last_key == 27:
            ws.crash_popup = None          # Esc closes See why's popover
            ws.mark_dirty()
            return True
        if i.last_key or i.any_pressed():
            self._mouse.stop()             # any key ends a latched autoscroll
        if self._mouse.armed:
            ws.mark_dirty()                # an autoscroll moves the code on its own
        self._wheel(i)
        k = i.typed
        s = self.s
        if (s.finding and k and s.over == "none" and self.page.focus_key != "find_q"
                and (k in (8, 13) or 32 <= k < 127)):
            # the find strip has the keyboard while it is open, whatever was
            # tapped since (a tool, the code)
            if k == 13:
                self.run("find_prev" if getattr(i, "mods", 0) & _SHIFT else "find_next")
            else:
                self.run("set_find", s.find_q[:-1] if k == 8 else s.find_q + chr(k))
                self.run("find_live")
            return True
        self.page.keys(i)
        # a held key types again at the input table's repeat (a count beside
        # the frame, never a second edge): text keys only, never into a popover
        n = getattr(ws.input, "key_repeats", 0)
        if n and self.s.over == "none":
            r = ws.input.repeat_key
            if r in (8, 9, 10, 13, 127) or 32 <= r < 127:
                shift = bool(getattr(ws.input, "mods", 0) & _SHIFT)
                for _ in range(n):
                    if self.s.finding and r != 13:
                        self.s.find_q = self.s.find_q[:-1] if r == 8 else self.s.find_q + chr(r)
                        self.run("find_live")
                    else:
                        self.page.type_byte(r, shift)
        return True

    def _wheel(self, i):
        """The wheel scrolls the code three lines a notch, wherever over the
        tab the pointer rests, and glides; Shift's, or a wheel across, moves
        the editor's columns."""
        wx = getattr(i, "wheel_x", 0)
        wy = getattr(i, "wheel_y", 0)
        if not (wx or wy):
            return
        ed = self.ws.editor
        if getattr(i, "mods", 0) & _SHIFT:
            wx, wy = wx + wy, 0
        if wx and ed is not None:
            ed.scroll(0, 3 * wx)
        v = self.page.views.get("code")
        if wy and v is not None:
            r = v[0]
            self._host.point(r[0] + r[2] // 2, r[1] + r[3] // 2, False)
            self._host.wheel(0, wy)
        self.ws.mark_dirty()

    def stop_autoscroll(self):
        self._mouse.stop()

    def autoscrolling(self):
        """A middle-button autoscroll runs: the frames must keep coming."""
        return self._mouse.armed

    def _code_rect(self):
        v = self.page.views.get("code")
        return v[0] if v is not None else (0, 0, 0, 0)

    def _cell(self, px, py, ed):
        """The document cell (row, col) under (px, py): the text view's,
        and off its rows (a drag past its edge) the lines on from them."""
        got = _ui_draw.text_at(self._host, "code", px, py)
        if got is not None:
            return got
        rect, _top, cell, lh, x0 = self.page.views["code"][:5]
        col = (px - x0) // max(1, cell)
        return ed.top + (py - rect[1]) // max(1, lh), ed.left + max(0, col)

    def _place(self, px, py, ed, select=False):
        """The caret to the cell under (px, py) (`CodeEditor.place`)."""
        row, col = self._cell(px, py, ed)
        ed.place(col - ed.left, row - ed.top, select)

    def handle_pointer(self, px, py, click):
        """The pointer over the tab: See why's popover goes at any tap; the
        bar takes its slice; an open popover is modal, and the page's other
        parts (the find strip, the tools, the status line, the symbol keys)
        take what lands on them; in the text a tap places the caret, a drag
        scrolls (select mode: extends), and a mouse selects and autoscrolls."""
        ws = self.ws
        ed = ws.editor
        self._ctx()
        if click and ws.crash_popup is not None:
            ws.crash_popup = None
            ws.mark_dirty()
            return True
        if click and ws.bar_layer.handle_bar_tap("menu", px, py):
            return True
        page = self.page
        area = self._code_rect()
        inside = _in(px, py, area)
        if click:
            self.page_held = not inside or page.popover_shown()
        if self.page_held or page.popover_shown() or not (
                inside or self._mouse.dragging or self._host.hold or self._sel_drag):
            # a press on the page's parts holds the pointer to its release
            page.point(px, py, click)
            if not ws.pointer.down:
                self.page_held = False
            ws.mark_dirty()
            return True
        self._px, self._py = px, py
        if ed is None:
            return True
        mid = bool(getattr(ws.input, "pointer_buttons", 0) & _PB_MIDDLE)
        if mid != self._mid and (not mid or inside):
            self._mouse.middle(px, py, mid)
            ws.mark_dirty()
        self._mid = mid
        if ws.pointer.hovers and (self._mouse.dragging or (click and inside)):
            # a mouse: the press places the caret (Shift extends, a second
            # and third press take the word and the line) and its drag selects
            row, col = self._cell(px, py, ed)
            if click:
                self._mouse.press(ed, row, col, bool(getattr(ws.input, "mods", 0) & _SHIFT),
                                  _ticks_ms())
            elif ws.pointer.down:
                self._mouse.drag(ed, row, col)
                ed._scroll()
            else:
                self._mouse.release(ed)
            ws.mark_dirty()
        elif self.s.select:
            # select mode: the press drops a fresh anchor at the cell, a drag
            # extends the selection to the finger, the release ends it
            if click:
                self._place(px, py, ed)
                ed.begin_select()
                self._sel_drag = True
            elif self._sel_drag and ws.pointer.down:
                self._place(px, py, ed, True)
            elif not ws.pointer.down:
                self._sel_drag = False
            ws.mark_dirty()
        else:
            # a drag scrolls the view and its release coasts (the text view's
            # region); a tap places the caret
            if self._host.point(px, py, click, ws.pointer.down):
                ws.mark_dirty()
            if click:
                self._place(px, py, ed)
        return True
