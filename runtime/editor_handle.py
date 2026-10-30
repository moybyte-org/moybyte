# Map (grep -n a name to jump there):
#   _Row               one buffer line, laid out
#   EditorHandle       the cart-facing editor over one document
#   EditorHandle.key   feed one byte
#   EditorHandle.save  write the document
#   EditorHandle.draw  render into a rect
#   EditorHandle.tap   route a pointer through the last layout
"""The EDITOR HANDLE -- the console's text editor, drawn by a CART (#181, #112).

Notes is the one text app and it is a cartridge
(docs/text_editing_2026-09.md). The engine it needs -- the `CodeEditor`
buffer, undo through the op-history, the clipboard, wrap, the per-mode
highlight and save gate, the Markdown rendering -- stays in the shell and
crosses to the cart as a HANDLE, exactly the way `make_layer` hands out the
layer engine: the kid edits the skin (the list, the buttons, the colours) and
never the engine.

Three seams, and each is chosen rather than defaulted:

**Identity is `(kind, name)`, never a path.** The handle is opened through the
`Files` role, so the same object edits a vault note today and a project's own
`config.json` once step 5 adds that door -- the only thing that changes is the
kind. The cart cannot NAME a kind: it gets the one its manifest was granted,
or the one the console was already asked to open.

**Keys arrive from the shell, taps from the cart.** The Player feeds the
FOCUSED handle the keyboard byte before the cart's `_update` runs, and the
cart's own `key()`/`keyp()` read nothing while a handle holds focus. That is
not symmetry for its own sake: taking the keyboard for a text surface is a
screen change, and the byte that caused it is still live on the next frame
(`b2ff7de`) -- so the seed that swallows it has to happen in the one place
that flips the T-Deck's keyboard, `Workstation._set_text_mode`, which no cart
can call. Taps have no mode switch and no stray edge, and only the cart knows
which rect it drew the editor into, so they stay the cart's to forward.

DIRECTIONAL input follows the keyboard for the same reason (`nav`): on the
T-Deck the trackball IS the arrow keys, and whether a roll is a caret or a
mouse cursor is a question about which surface holds the keyboard -- so
`Workstation.nav` answers it once, for the Code tab and for a cart's focused
handle alike, and reports whether it spent the pulses.

**The document draws as a text view** (`ui.text_view`, the Code tab's): the
monospace source, wrapped where the mode wraps prose, coloured by the C's
highlight where it is a program or data. A Markdown note shows its source
as typed; its checkboxes and `[[links]]` answer a tap, read off the line
under it (`tap`). The view keeps its own scroll, its fling and glide, and
the record of the rows it drew, so a frame draws the rows that changed.
"""

try:
    from editors_code import CodeEditor, TextMouse
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors_code import CodeEditor, TextMouse
try:
    from moy_input import PB_MIDDLE as _PB_MIDDLE, SHIFT as _SHIFT
except ImportError:  # pragma: no cover - host fallback
    from runtime.moy_input import PB_MIDDLE as _PB_MIDDLE, SHIFT as _SHIFT

try:
    from op_history import History, TextEditCodec, text_diff_op
except ImportError:  # pragma: no cover - direct host import
    from runtime.op_history import History, TextEditCodec, text_diff_op

try:
    import text_modes as _modes
except ImportError:  # pragma: no cover - direct host import
    from runtime import text_modes as _modes

try:
    import ui as _ui
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - direct host import
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw

try:
    from ticks import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - direct host import
    from runtime.ticks import _ticks_ms, _ticks_diff
from array import array as _array


MAX_CHARS = 8000       # per document -- bounds the SD write + device memory
AUTOSAVE_MS = 2500     # idle before a SOFT save (Writer's #108 debounce)
INVALID = "INVALID"    # the badge a mode's gate raises (#154's SYNTAX twin)

_BURST_BREAK = ".,!?;:"   # a typing burst closes here (#111 phase 3)

# The view's packed options' words the handle writes each frame
# (`ui.text_options`), and the text's inset from its left (moy_ui_api.c's
# TEXT_PAD) at the scale.
_TXO_CARET, _TXO_SEL, _TXO_LEFT, _TXO_GEN = _ui.TXO_CARET, _ui.TXO_SEL, _ui.TXO_LEFT, _ui.TXO_GEN
_TEXT_PAD = 8
_VIEWS = [0]           # the handles made: each view's id is its own


def _check_box(text):
    """The column of a `- [ ] ` line's box (its `[`), else -1. Read in place:
    a tap's frame makes no string."""
    lead = 0
    n = len(text)
    while lead < n and text.startswith(" ", lead):
        lead += 1
    if ((text.startswith("- [", lead) or text.startswith("* [", lead))
            and text.startswith("] ", lead + 4)):
        return lead + 2
    return -1


def _link_at(text, col):
    """The target of the `[[target]]` in `text` that column `col` lies in,
    its brackets included, else None; an embed's `![[name]]` is none."""
    i = 0
    while True:
        s = text.find("[[", i)
        if s < 0 or s > col:
            return None
        e = text.find("]]", s + 2)
        if e < 0:
            return None
        if col < e + 2:
            return None if s > 0 and text.startswith("!", s - 1) else text[s + 2:e]
        i = e + 2


class EditorHandle:
    """The cart-facing editor over ONE document, identified by `(kind, name)`.

    Built by the shell (`Workstation.open_cart_editor`), published into a cart
    namespace by `system_api.make_system_api` as `open_editor`, and released
    with the cart's world -- flushed HARD on the way out (#154), so a kid who
    taps X never loses a note."""

    def __init__(self, files, kind, name, mode, canvas, theme,
                 clip=None, host=None, frame=None):
        self._files = files          # the (kind, name)-taking Files role
        self._max = MAX_CHARS        # the document's size cap
        self._canvas = canvas
        self._theme = theme          # () -> the live token dict
        self._host = host            # the shell, for the keyboard-focus flip
        self.kind = kind
        self._name = str(name)
        self._mode = mode or _modes.mode_for_kind(kind, name)
        self._m = _modes.MODES[self._mode]
        blob, err = files.load(kind, self._name)
        body = "\n".join(files.decode_text(blob)) if err is None else ""
        # The mode's own reading layout, applied to the document as OPENED and
        # not as an edit: JSON arrives from a program as one long line, and a
        # kid cannot read or repair that on 320px. Nothing is dirty yet, so a
        # note only opened is never rewritten -- the indented form is what the
        # next SAVE writes, which is the form the kid was editing.
        body = _modes.pretty(self._mode, body)
        self.ed = CodeEditor(body, 32, 12, clip=clip)
        self.history = History(self.ed, TextEditCodec())
        self.history.seed(files.history_ops(kind, self._name)[0] or [])
        self._burst = None           # text() at the live typing burst's start
        self._badge = ""             # the gate's refusal, "" when clean
        self._unsaved = False
        self._edit_ms = None         # when the idle autosave window opened
        self._focus = False
        self._mouse = TextMouse()    # a mouse's clicks, selection and autoscroll
        self._mid = False            # the middle button was held last frame
        self._held = False           # a press on the view is held
        self._caret_at = None        # the caret's line the view last moved to show
        self._caret_col = 0          # ...and its column
        self._gen = 0                # bumped by every verb that changes what it draws
        self._drawn = -1             # the _gen the last draw showed
        # The view (`ui.text_view`): its id, its options packed once and
        # written in place, its answer, the rect it drew in, the first line
        # it showed and the columns it was panned to; the host whose pass
        # drew it last, and the handle's own for a draw in none. `frame` is
        # the run's (the Player): the host whose pass draws now.
        _VIEWS[0] += 1
        self._vid = "editor" + str(_VIEWS[0])
        syn = "plain"
        if self._mode == _modes.JSON:
            syn = "json"
        elif self._m.lang:
            syn = _modes.lang_for(self._name) or self._m.lang
        self._opt = _ui.text_options({"wrap": self._m.wrap, "syntax": syn})
        self._into = _array("i", [0, 0])
        self._vr = [0, 0, 0, 0]
        self._top = -1
        self._left = 0
        self._vh = None
        self._own = None
        self._frame = frame
        self.live = True             # not closed since its last draw: it draws the run's rows

    # -- what the skin asks ---------------------------------------------------

    def name(self):
        """The document's name inside its kind."""
        return self._name

    def mode(self):
        """The editing mode: "md" / "code" / "json" / "text"."""
        return self._mode

    def dirty(self):
        """True while there are edits this document has not been saved with."""
        return bool(self._unsaved or self.ed.dirty or self._burst is not None)

    def badge(self):
        """The gate's refusal for the text last saved; "" when there is
        nothing to say."""
        return self._badge

    def caret(self):
        """The caret as `(row, col)`, zero-based, in the WHOLE document."""
        return (self.ed.row, self.ed.col)

    def text(self):
        return self.ed.text()

    def set_text(self, body):
        self._gen += 1
        self.ed.set_text(str(body)[:self._max])
        self._caret_at = None
        self._mark()

    def scroll(self, rows=0, cols=0):
        """Pan the view without moving the caret (a drag, a scrollbar).

        A pan STAYS: `draw` scrolls the caret back into view only when the
        caret itself moved, so reading the end of a long note does not fight
        the caret left at the top."""
        self._gen += 1
        self.ed.scroll(int(rows), int(cols))

    def wraps(self):
        """True when this mode soft-wraps prose; False when a long line pans
        sideways instead (`text_modes.Mode.wrap`)."""
        return bool(self._m.wrap)

    # -- the keyboard ---------------------------------------------------------

    def focus(self, on=True):
        """Take (or release) the keyboard.

        A focused handle is fed by the shell before the cart's `_update` runs,
        and the cart's own `key()`/`keyp()` go quiet for as long as it holds
        focus. Taking it flips the T-Deck to clean ASCII and swallows the byte
        that caused the flip (`b2ff7de`), which is the whole reason this is a
        shell call and not a flag the cart sets."""
        self._gen += 1
        on = bool(on)
        if on == self._focus:
            return on
        self._focus = on
        host = self._host
        if host is not None:
            host.cart_editor_focus(self if on else None)
        return on

    def _blur(self):
        """Drop the focus flag without touching the keyboard -- what the shell
        calls on the PREVIOUS handle when a second one takes it, so two
        handles can never both believe they own the keys."""
        self._gen += 1
        self._focus = False

    def focused(self):
        return self._focus

    def key(self, code):
        """Feed ONE ASCII byte -- an on-screen key, a soft keyboard, a handle
        the skin deliberately left blurred. The shell comes through the same
        door for the focused handle. True when the handle TOOK it (a shortcut
        counts), so a skin can fall through on a key it wants for itself."""
        self._gen += 1
        code = int(code or 0)
        if not code:
            return False
        self._mouse.stop()                   # any key ends a latched autoscroll
        ed = self.ed
        if code == 0x1A:                     # Ctrl+Z
            self.undo()
            return True
        if code == 0x19:                     # Ctrl+Y
            self.redo()
            return True
        if code == 0x01:                     # Ctrl+A
            ed.select_all()
            return True
        if code == 0x03:                     # Ctrl+C
            return ed.copy()
        if code in (0x18, 0x16):             # Ctrl+X / Ctrl+V
            changed = self.cut() if code == 0x18 else self.paste()
            return changed
        if code not in (0x08, 0x7F):
            n = len(ed.lines) - 1           # the text's length, not joined to measure it
            for line in ed.lines:
                n += len(line)
            if n >= self._max:
                return False
        if self._burst is None:
            self._burst = ed.text()
        if not ed.key(code):
            return False
        self._mark()
        if code in (0x0D, 0x0A) or (0x20 <= code <= 0x7E
                                    and chr(code) in _BURST_BREAK):
            self._close_burst()
        return True

    def nav(self, dx, dy, select=None):
        """Feed DIRECTIONAL input -- the T-Deck trackball, a host arrow key.

        It moves the CARET and the view follows, which is `ws.nav`'s contract
        for the Code tab held one rung down: on the writing board the ball IS
        the arrow keys, and a text surface that spent them on a mouse cursor
        would leave the caret unreachable. In SELECT mode the same motion
        EXTENDS the selection, because `select_sticky` is what `move` reads,
        and so does `select` (Shift held). True when it moved something."""
        self._gen += 1
        dx = int(dx or 0)
        dy = int(dy or 0)
        if not (dx or dy):
            return False
        self._mouse.stop()
        self.ed.move(dy, dx, select)
        return True

    # -- undo / clipboard -----------------------------------------------------

    def can_undo(self):
        # A still-open burst is undoable too: undo() closes it first, so the
        # skin's dimmed/lit UNDO has to agree with what a press actually does.
        if self.history.can_undo():
            return True
        return self._burst is not None and self.ed.text() != self._burst

    def can_redo(self):
        return self.history.can_redo()

    def undo(self):
        self._gen += 1
        self._close_burst()
        if self.history.undo() is None:
            return False
        self._mark()
        return True

    def redo(self):
        self._gen += 1
        if self.history.redo() is None:
            return False
        self._mark()
        return True

    def select_all(self):
        self._gen += 1
        self.ed.select_all()
        return True

    def select_mode(self, on=None):
        """Turn SELECT mode on or off (`None` toggles); returns the new state.

        The Code tab's `sel` tool, one rung down and for the same reason: the
        T-Deck has no shift-arrow and no Ctrl, so the only way a kid marks a
        range is a MODE in which the caret -- moved by a drag or the trackball
        -- extends the selection instead of collapsing it. Turning it on
        anchors at the caret, so the very next move already selects."""
        self._gen += 1
        on = (not self.ed.select_sticky) if on is None else bool(on)
        self.ed.select_sticky = on
        if on:
            self.ed.begin_select()
        else:
            self.ed.clear_select()
        return on

    def selecting(self):
        """True while SELECT mode is on -- what a skin lights its chip on."""
        return bool(self.ed.select_sticky)

    def has_selection(self):
        """True when COPY and CUT have something to act on."""
        return self.ed.has_selection()

    def selected_count(self):
        """The letters the selection holds (a line's break counts as one)."""
        return len(self.ed.selected_text())

    def can_paste(self):
        """True when there is clipboard text to paste (the system lane's when
        one is attached, else this handle's own)."""
        return bool(self.ed.paste_text())

    def copy(self):
        return self.ed.copy()

    def cut(self):
        self._gen += 1
        if self._burst is None:
            self._burst = self.ed.text()
        if not self.ed.cut():
            self._burst = None
            return False
        self._mark()
        self._close_burst()               # a cut is its own burst edge
        return True

    def paste(self):
        self._gen += 1
        if len(self.ed.text()) + len(self.ed.paste_text()) > self._max:
            return False
        if self._burst is None:
            self._burst = self.ed.text()
        if not self.ed.paste():
            self._burst = None
            return False
        self._mark()
        self._close_burst()
        return True

    # -- saving ---------------------------------------------------------------

    def save(self, soft=False):
        """Write the document. `(ok, badge)`.

        `soft` is the #154 split and belongs to the idle debounce alone: a
        document its MODE cannot parse is refused there, never published
        half-typed, and badged. Every other caller is an EXIT -- the skin's
        own back, `close()`, the hard-exit ladder -- and writes anyway, keeping
        the badge, because a kid's text is theirs and a broken document is
        caught the next time something loads it."""
        self._gen += 1
        self._close_burst()
        body = self.ed.text()[:self._max]
        good, why = _modes.check(self._mode, body)
        self._badge = why if good else (INVALID + " " + why).rstrip()
        if not good and soft:
            self._edit_ms = _ticks_ms()   # re-check on the next window, not next frame
            return (False, self._badge)
        _, err = self._files.save(self.kind, self._name, self._files.encode_text(body))
        if err is not None:
            return (False, str(err))
        self.ed.dirty = False
        self._unsaved = False
        self._edit_ms = None
        self._commit_history()
        return (True, self._badge)

    def close(self):
        """Flush (hard) and release. Idempotent; the shell calls it for any
        handle still open when the cart's world drops."""
        self.live = False
        if self._focus:
            self.focus(False)
        if self.dirty():
            self.save()
        self._held = False

    # -- drawing --------------------------------------------------------------

    def owes(self):
        """Whether the handle has a frame to draw: a verb changed it since its
        last draw (a key handed to it outside a drawn frame), its caret moved
        where the view has not shown it (`aim`) or its idle save is due --
        what an app drawn on change (`Player.animates`) asks of every handle
        it opened."""
        ed = self.ed
        if self._gen != self._drawn or (self.live and (self._caret_at != ed.row
                                                        or self._caret_col != ed.col)):
            return True
        if self.dirty():
            at = self._edit_ms
            return at is None or _ticks_diff(_ticks_ms(), at) >= AUTOSAVE_MS
        return False

    def draw(self, x, y, w, h, scale=1, r=0):
        """Render the document into `(x, y, w, h)` of the cart's canvas, on
        its paper (corners of radius `r`): one `ui.text_view` in the
        monospace face, wrapped where the mode wraps prose, coloured where
        the mode is a program's or data's. `scale` is the canvas's font
        scale's, which the view takes itself.

        In the run's pass (`Player.ui_host`) the view is a stop of the
        app's page, whose frames the engine plans: a fling, a glide or the
        caret's step moves the rows the frame before drew and draws the ones
        it brings, and a typed key draws the rows it changed
        (docs/scroll_and_text_2026-10.md §5). Drawn outside one (a Lua
        cart's `_draw`), the handle's own host draws it whole.

        Also where the idle autosave lands: the shipped notebook has always
        run its debounce off the draw pass (Writer's `#108` model), and a kid
        pressing save is the thing this console does not do."""
        cv = self._canvas
        self.live = True
        x = int(x)
        y = int(y)
        w = int(w)
        h = int(h)
        r = int(r)
        self._autosave()
        self._drawn = self._gen
        fr = self._frame
        host = fr.ui_host(cv) if fr is not None else None
        th = self._theme()
        if host is None:
            # no pass of the run's: the handle's own, its every row drawn
            host = self._own
            if host is None:
                host = self._own = _ui_draw.AppHost()
            self.aim(host)
            t = _ui_draw.UI_TARGET
            was = (t[0], t[1], t[2], t[3])
            host.whole()
            host.begin(cv, th, (x, y, w, h))
            try:
                self._view_in(host, cv, th, x, y, w, h, r)
            finally:
                t[0], t[1], t[2], t[3] = was
            return
        self._view_in(host, cv, th, x, y, w, h, r)

    def _view_in(self, host, cv, th, x, y, w, h, r):
        """The paper and the view on it, in `host`'s pass: the paper's
        bands above and under the view, as tall as the text's inset from
        its sides (or its round corners, where taller), which they leave
        whole (a shift frame moves the view's rows alone)."""
        self._vh = host
        s = getattr(cv, "font_scale", 1) or 1
        band = r if r > _TEXT_PAD * s else _TEXT_PAD * s
        if 2 * band < h:
            bg = th["surface"]
            cv.clip(x, y, w, band)
            cv.rrect(x, y, w, h, r, bg)
            cv.clip(x, y + h - band, w, band)
            cv.rrect(x, y, w, h, r, bg)
            cv.clip()
            y += band
            h -= 2 * band
        v = self._vr
        v[0] = x
        v[1] = y
        v[2] = w
        v[3] = h
        ed = self.ed
        o = self._opt
        o[_TXO_CARET] = ed.row
        o[_TXO_CARET + 1] = ed.col
        b = ed.selection_bounds() if ed.sel is not None else None
        k = _TXO_SEL
        if b is None:
            o[k] = o[k + 2] = -1
            o[k + 1] = o[k + 3] = 0
        else:
            o[k] = b[0]
            o[k + 1] = b[1]
            o[k + 2] = b[2]
            o[k + 3] = b[3]
        if self._m.wrap:
            ed.left = 0                 # wrapped prose never scrolls sideways
        o[_TXO_LEFT] = ed.left
        o[_TXO_GEN] = ed.edits + 1
        if ed.left != self._left:
            # the columns scrolled across: every row changes
            self._left = ed.left
            host.whole()
        _ui.at(x, y, w, h)
        got = _ui.text_view(self._vid, ed.lines, o, None, None, self._into)
        ed.top = self._top = got[0]
        rows = got[1] - 1 if got[1] > 1 else 1
        if ed.ROWS != rows or ed.COLS != self._cols(cv, w):
            ed.set_view_size(self._cols(cv, w), rows)
            ed.top = self._top
        a = self.autoscrolling()
        if a is not None:
            s = getattr(cv, "font_scale", 1) or 1
            _ui_draw.autoscroll_mark(cv, th, a[0], a[1], s)
            _ui_draw.text_cover(host, self._vid, a[1] - 7 * s, a[1] + 7 * s)

    def _cols(self, cv, w):
        """The columns a row of the view shows (`ui.text_view`'s count:
        the mono face's cell at the canvas's scale, the text's inset from
        the view's left and, wrapped, from its right): where a line that
        does not wrap pans sideways to keep the caret (`CodeEditor`)."""
        s = getattr(cv, "font_scale", 1) or 1
        cell = _ui_draw.font("mono")[1] * s
        pad = _TEXT_PAD * s * (2 if self._m.wrap else 1)
        return max(1, (w - pad) // max(1, cell))

    def aim(self, host):
        """Before `host` begins the frame the view draws in: where the caret
        moved since the view last showed it (a key, the ball, an edit, a
        tap), the view moves only as far as it needs to show the caret's row;
        where the editor moved its top line (`scroll`, an autoscroll), it
        shows that line first. The move is the frame's shift
        (`ui_draw.text_reveal`). A caret left alone asks for nothing, so a
        drag or a fling takes the view anywhere and it stays there."""
        ed = self.ed
        if self._caret_at != ed.row or self._caret_col != ed.col:
            self._caret_at = ed.row
            self._caret_col = ed.col
            _ui_draw.text_reveal(host, self._vid, ed.lines, ed.row, ed.col)
        elif ed.top != self._top and self._top >= 0:
            _ui_draw.text_reveal(host, self._vid, ed.lines, ed.top, -1)

    def drawn_in(self, host):
        """Whether the handle's last draw was a view in `host`'s pass."""
        return self.live and self._vh is host

    def meets(self, r):
        """Whether rect `r` (x, y, w, h) meets the view as it drew last."""
        v = self._vr
        return (r[0] < v[0] + v[2] and v[0] < r[0] + r[2] and r[1] < v[1] + v[3]
                and v[1] < r[1] + r[3])

    def claims(self, px, py):
        """Whether the pointer's gesture at (px, py) is the handle's own, not
        a drag of its view: a mouse's (its press places the caret and its
        drag selects, the middle button autoscrolls) and, in SELECT mode, a
        finger's, over the view or held since a press on it."""
        mine = self._is_mouse() or self.ed.select_sticky
        if self._held:
            return mine or self._mouse.armed
        v = self._vr
        return ((mine or self._mouse.armed) and v[0] <= px < v[0] + v[2]
                and v[1] <= py < v[1] + v[3])

    def tap(self, px, py, click=True):
        """Route a pointer at `(px, py)` through the LAST DRAWN frame -- the
        view's rows are the hit map (`ui_draw.text_at`).

        Answers what the tap meant, so the skin can act on it:
        `("link", name)` a `[[note]]` (open it), `("check", row)` a
        checkbox's `[ ]` (already ticked or cleared), `("caret", None)` a
        plain place, or None when the point is outside the editor. Without
        `click` the answer is only whether the point is INSIDE -- nothing
        moves and nothing toggles."""
        self._gen += 1
        got = self._at(px, py)
        if got is None:
            return None
        row, col = got
        if not click:
            return ("caret", None)
        self._held = True
        own = self._own
        if own is not None and self._vh is own and not self.claims(px, py):
            own.point(px, py, True, True)       # the view's own drag starts here
        if self._mode == _modes.MD:
            line = self.ed.lines[row]
            box = _check_box(line)
            if box >= 0 and box <= col <= box + 2:
                self._toggle_check(row, box + 1)
                return ("check", row)
            target = _link_at(line, col)
            if target is not None:
                return ("link", target)
        if self._is_mouse():
            # A mouse's press: the caret, Shift extending, a second and a
            # third press the word and the line; its drag selects.
            m = getattr(getattr(self._host, "input", None), "mods", 0)
            self._mouse.press(self.ed, row, col, bool(m & _SHIFT), _ticks_ms())
            return ("caret", None)
        # The press edge COLLAPSES even in SELECT mode and re-anchors here --
        # the Code tab's `_select_pointer` rule, so a fresh drag selects a
        # fresh range instead of growing the last one.
        self._place(row, col, False)
        if self.ed.select_sticky:
            self.ed.begin_select()
        return ("caret", None)

    def drag(self, px, py, down):
        """One pointer frame that is NOT the press edge -- what happens while
        the finger moves. True when the selection changed.

        A mouse's drag and, in SELECT mode, a finger's extend the selection
        to the pointer (`claims`). Otherwise the drag is the view's: it
        scrolls by the finger's pixels and its release coasts, as the Code
        tab's does."""
        self._gen += 1
        own = self._own
        if own is not None and self._vh is own and own.hold:
            own.point(px, py, False, down)      # the view's own drag, a finger's
        if not down:
            self._held = False
            self._mouse.release(self.ed)
            return False
        if self._mouse.dragging:
            got = self._at(px, py, True)
            return got is not None and self._mouse.drag(self.ed, got[0], got[1])
        if self.ed.select_sticky and self._held:
            got = self._at(px, py, True)
            if got is None:
                return False
            self._place(got[0], got[1], True)
            return True
        return False

    def mouse_frame(self, inp, x, y, dt):
        """One frame of a mouse over the handle, the pointer at (x, y) in
        the canvas: the middle button pressed over it autoscrolls
        (`TextMouse`), and Shift's wheel moves its columns where it does not
        wrap. The wheel's rows are the view's: the run's pass hands them to
        it, and the handle's own host takes them here. True when the view
        moved or will."""
        ed = self.ed
        v = self._vr
        over = v[0] <= x < v[0] + v[2] and v[1] <= y < v[1] + v[3]
        moved = False
        wy = getattr(inp, "wheel_y", 0)
        if over and wy and getattr(inp, "mods", 0) & _SHIFT:
            if not self._m.wrap:
                left = ed.left
                ed.scroll(0, 3 * wy)
                moved = left != ed.left
        elif over and wy:
            own = self._own
            if own is not None and self._vh is own:
                own.point(x, y, False)
                moved = own.wheel(0, wy)
        mid = bool(getattr(inp, "pointer_buttons", 0) & _PB_MIDDLE)
        if mid != self._mid and (over or not mid):
            self._mouse.middle(x, y, mid)
        self._mid = mid
        if self._mouse.armed and self._mouse.autoscroll(ed, x, y, dt, self._lh()):
            moved = True
        if moved:
            self._gen += 1
        return moved

    def _lh(self):
        """A row's pitch as the view last drew: what an autoscroll's speed
        counts in."""
        fid, _cell, line, _asc = _ui_draw.font("mono")
        s = getattr(self._canvas, "font_scale", 1) or 1
        return line * s if fid else 10 * s

    def autoscrolling(self):
        """Whether a middle-button autoscroll runs, and where its anchor is."""
        m = self._mouse
        return (m.anchor_x, m.anchor_y) if m.armed else None

    # -- internals ------------------------------------------------------------

    def _is_mouse(self):
        """The pointer is a mouse: it hovers, as a finger never does."""
        p = getattr(self._host, "pointer", None)
        return bool(getattr(p, "hovers", False))

    def _at(self, px, py, edge=False):
        """`(line, column)` under `(px, py)` in the view as it drew last,
        or None off its rows; with `edge`, a point above or under the view
        reads its top or foot row (a drag carried past it)."""
        h = self._vh
        if h is None:
            return None
        px = int(px)
        py = int(py)
        if edge:
            v = self._vr
            if py < v[1]:
                py = v[1]
            elif py >= v[1] + v[3]:
                py = v[1] + v[3] - 1
            if px < v[0]:
                px = v[0]
            elif px >= v[0] + v[2]:
                px = v[0] + v[2] - 1
        got = _ui_draw.text_at(h, self._vid, px, py)
        if got is None or got[0] >= len(self.ed.lines):
            return None
        return got

    def _place(self, row, col, select):
        """Put the caret on a document cell: the view's (`_at`), clamped
        by the editor."""
        ed = self.ed
        if select:
            ed.begin_select()
        else:
            ed.sel = None
        ed.row = max(0, min(len(ed.lines) - 1, int(row)))
        ed.col = max(0, min(len(ed.lines[ed.row]), int(col)))
        if not self._m.wrap:
            ed._scroll()          # the sideways window follows the caret

    def _mark(self):
        self._unsaved = True
        self._edit_ms = _ticks_ms()
        # An EDIT always re-shows the caret, wherever the reader had panned to:
        # the text just changed under it, and an undo can drop lines out from
        # under the view entirely.
        self._caret_at = None

    def _autosave(self):
        if not self.dirty():
            return
        at = self._edit_ms
        if at is None:
            self._edit_ms = _ticks_ms()
            return
        if _ticks_diff(_ticks_ms(), at) >= AUTOSAVE_MS:
            self.save(soft=True)

    def _close_burst(self):
        """Finalize the in-progress typing/delete burst into ONE undo op (#111):
        the net diff since it started. The edges are Enter, punctuation, a
        cut/paste, an undo press and the idle save."""
        before = self._burst
        self._burst = None
        if before is None:
            return
        after = self.ed.text()
        if before == after:
            return
        op = text_diff_op(before, after)
        if op is not None:
            self.history.record(op)

    def _commit_history(self):
        """Drain the History's pending batch (plus a keyframe when the segment
        cap trips) into the sidecar, at the cadence `save` already writes on."""
        hist = self.history
        kf = hist.keyframe() if hist.needs_keyframe() else None
        ops = hist.flush()
        if not ops and kf is None:
            return
        _, err = self._files.history_commit(self.kind, self._name, ops,
                                            keyframe=kf)
        if err is None and kf is not None:
            hist.mark_keyframe()

    def _toggle_check(self, row, col):
        """Tick or clear the box of line `row` whose mark is at `col`."""
        line = self.ed.lines[row]
        self._burst = self.ed.text()
        self.ed.lines[row] = line[:col] + (" " if line[col:col + 1] in ("x", "X") else "x") \
            + line[col + 1:]
        self.ed.dirty = True
        self._mark()
        self._close_burst()               # a tick is one undo step of its own
