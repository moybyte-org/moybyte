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

**The layout memo is per LINE, keyed by its text.** A line's laid-out form --
its wrap segments, its heading level, its checkbox, its `[[link]]` spans -- is
a pure function of the text and the column count, so an edit invalidates
exactly the line it touched and nothing re-parses per frame. Two generations
retire rather than one clearing, for `code_layer._hl`'s reason: emptying the
memo mid-scroll re-lays the whole visible window inside one frame.
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
    from moy_image import Image
    from moyimg import decode_moyimg
except ImportError:  # pragma: no cover - direct host import
    from runtime.moy_image import Image
    from runtime.moyimg import decode_moyimg

try:
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - direct host import
    from runtime import ui_draw as _ui_draw

try:
    from ticks import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - direct host import
    from runtime.ticks import _ticks_ms, _ticks_diff
from array import array as _array


MAX_CHARS = 8000       # per document -- bounds the SD write + device memory
_VIS_ROW = (0, 0, 0)
_SIG_ROW = (0, -1, -1, -1)
_ST_N_FILL, _ST_N_TEXT = 10, 11   # the canvas's fill and text counters (moy_canvas.h's MOY_ST_N_*)
AUTOSAVE_MS = 2500     # idle before a SOFT save (Writer's #108 debounce)
INVALID = "INVALID"    # the badge a mode's gate raises (#154's SYNTAX twin)

CELL = 8               # one glyph cell at scale 1
LH = 9                 # one text row: the 8px glyph plus a gap
# In the look (a built-in app's note): a row's pitch and the paper's pad on
# the handheld and on the desk, and an embedded drawing's height.
LOOK_LH = (15, 22)
LOOK_PAD = ((10, 6), (16, 12))
IMAGE_H = (48, 144)
DESK_W = 600           # a canvas this wide is a desk's

# Laid-out lines held per generation of the `_row` memo (two generations live).
_MEMO = 120
_BANDS = 6             # the bands of rows a draw paints alone, at most
_FLOOR = 24            # the floor's rects the frame's pass reads back

_BURST_BREAK = ".,!?;:"   # a typing burst closes here (#111 phase 3)

# The drawings kind an `![[name]]` embed reads from. Named through the store's
# own table so nothing here spells a folder.
_IMAGE_KIND = "drawings"


class _Row:
    """One buffer line, laid out. Pure data: what `draw` paints and what `tap`
    hit-tests, cached by (text, cols) so an edit invalidates one line.

    A Markdown line SHOWS other text than it holds: a checkbox's `- [ ] ` is a
    box in three cells, a link's brackets are not drawn. `cols` maps each
    shown cell to the buffer column it stands for (one past the last, the
    line's end), so the caret, a tap and a selection cross between the two;
    None where the two are the same. Columns in `segs`, `check` and `links`
    are SHOWN ones."""

    __slots__ = ("head", "check", "img", "segs", "links", "hl", "cols")

    def __init__(self, head, check, img, segs, links, cols=None):
        self.head = head       # heading level 1..3, else 0
        self.check = check     # (box column, checked) for a `- [ ]` line, else None
        self.img = img         # the drawing name of a whole-line `![[name]]`
        self.segs = segs       # [(text, start column)] -- one per visual row
        self.links = links     # [(col0, col1, target)] for the `[[note]]` spans
        self.hl = None         # a code line's per-character colours, once
        self.cols = cols       # shown cell -> buffer column, or None (the same)

    def shown(self, col):
        """The shown cell buffer column `col` stands at: the first cell at or
        past it (a hidden bracket's caret stands before the word it opens)."""
        m = self.cols
        if m is None:
            return col
        lo, hi = 0, len(m) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if m[mid] < col:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def held(self, cell):
        """The buffer column shown cell `cell` stands for."""
        m = self.cols
        if m is None:
            return cell
        return m[max(0, min(len(m) - 1, cell))]


def _wrap(text, cols, on):
    """`text` as visual segments `[(text, start column)]`, broken at spaces."""
    if not on or len(text) <= cols:
        return ((text, 0),)
    out = []
    i = 0
    n = len(text)
    while i < n:
        end = i + cols
        if end >= n:
            out.append((text[i:], i))
            break
        brk = text.rfind(" ", i, end + 1)
        if brk <= i:
            brk = end                    # one unbroken word: cut it
            out.append((text[i:brk], i))
            i = brk
        else:
            out.append((text[i:brk], i))
            i = brk + 1                  # the break space belongs to neither row
    return tuple(out) if out else (("", 0),)


def _link_spans(text):
    """Every `[[target]]` / `![[target]]` in `text` as `(col0, col1, target,
    is_image)`; col0 includes the `!` of an embed."""
    out = []
    i = 0
    while True:
        s = text.find("[[", i)
        if s < 0:
            return out
        e = text.find("]]", s + 2)
        if e < 0:
            return out
        img = s > 0 and text[s - 1] == "!"
        out.append((s - 1 if img else s, e + 2, text[s + 2:e], img))
        i = e + 2


def _md_row(text, cols):
    """`text` laid out as MARKDOWN: heading, checkbox, embed, links, wrap --
    as it shows: a checkbox's `- [ ] ` a box and a space in three cells, a
    link its target's words without the brackets."""
    head = 0
    while head < 3 and text[head:head + 1] == "#":
        head += 1
    if head and text[head:head + 1] not in (" ", ""):
        head = 0
    out = []
    cmap = []
    i = 0
    check = None
    lead = 0
    while text[lead:lead + 1] == " ":
        lead += 1
    mark = text[lead:lead + 6]
    if mark[:3] in ("- [", "* [") and mark[4:6] == "] ":
        check = (lead, mark[3] in "xX")
        out.append(text[:lead] + "   ")
        cmap.extend(range(lead))
        cmap.extend((lead, lead + 3, lead + 5))
        i = lead + 6
    img = None
    links = []
    for c0, c1, target, is_img in _link_spans(text):
        if is_img:
            if img is None and text.strip() == text[c0:c1]:
                img = target
            continue
        if c0 < i:
            continue
        out.append(text[i:c0])
        cmap.extend(range(i, c0))
        d0 = len(cmap)
        out.append(text[c0 + 2:c1 - 2])
        cmap.extend(range(c0 + 2, c1 - 2))
        links.append((d0, len(cmap), target))
        i = c1
    if i == 0 and not links:
        return _Row(head, check, img, _wrap(text, cols, True), ())
    out.append(text[i:])
    cmap.extend(range(i, len(text)))
    cmap.append(len(text))
    shown = "".join(out)
    return _Row(head, check, img, _wrap(shown, cols, True), tuple(links), tuple(cmap))


class EditorHandle:
    """The cart-facing editor over ONE document, identified by `(kind, name)`.

    Built by the shell (`Workstation.open_cart_editor`), published into a cart
    namespace by `system_api.make_system_api` as `open_editor`, and released
    with the cart's world -- flushed HARD on the way out (#154), so a kid who
    taps X never loses a note."""

    def __init__(self, files, kind, name, mode, canvas, theme,
                 clip=None, host=None, images=None, look=False, frame=None):
        self._files = files          # the (kind, name)-taking Files role
        self._max = MAX_CHARS        # the document's size cap
        self._images = images or files   # the role a note's embedded drawings load through
        self._canvas = canvas
        self._theme = theme          # () -> the live token dict
        # A built-in app's note draws in the look (system_api.draws_in_look):
        # the mono faces and their bold, the look's marks; else petme128's cells.
        self._look = bool(look)
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
        self._view = (0, 0)          # (cols, rows) the last draw sized it to
        self._memo = {}
        self._memo_old = {}
        self._img = {}               # drawing name -> Image (None when absent)
        self._vis = []               # the last drawn (buffer row, seg, y) map, flat
        self._nvis = 0               # the rows of it the last draw wrote
        self._geom = (0, 0, CELL, LH, 1)   # x, y, cell, lh, scale
        self._faces_in = (0, 0, 8)   # the faces the last draw took, and their line
        self._drag = None            # last pointer cell-origin of a live drag
        self._mouse = TextMouse()    # a mouse's clicks, selection and autoscroll
        self._mid = False            # the middle button was held last frame
        self._rect = (0, 0, 0, 0)    # where the last draw put it
        self._caret_at = None        # the caret's row the last draw scrolled to see
        self._caret_col = 0          # ...and its column
        self._fmemo = [None, None, None, None]   # look_gen, desk, its faces, font_info
        self._gen = 0                # bumped by every verb that changes what it draws
        self._drawn = -1             # the _gen the last draw showed
        # The rows: what each visual row of the last draw showed (its laid
        # line `_sl`; its segment, caret cell and selection's cells, four a
        # row in `_ss`), what every row of it is a function of besides its
        # own (`_key`) and the draw's arguments; the walk of the next draw
        # beside them (`_nl`, `_ns`); the bands, y0 and y1 a band, the rows
        # that differ; what the last draw changed of the canvas (`drew`: x0,
        # y0, x1, y1) and the canvas's counters after it (`_seen`). `frame`
        # is the run's frame (the Player): whether its pass draws a floor
        # this frame, which the rows draw into (docs/scroll_and_text_2026-10.md §5).
        self._frame = frame
        self._args = None
        self._key = None
        self._nr = -1
        self._walked = -1
        self._spill = False
        self._lt = [0] * 8
        self._sl = []
        self._ss = []
        self._nl = []
        self._ns = []
        self._band = [0] * (2 * _BANDS)
        self._fr = _array("i", [0] * (4 * _FLOOR))
        self.drew = [0, 0, 0, 0]
        self.rows_only = False
        self._seen = [None, 0, 0, 0]
        # A view moved by whole visual rows: the first line the last draw
        # showed (`_top`) and this walk's (`_wtop`), the rows the next draw's
        # rows lie below the last draw's (`_k`, the plan's, 0 none) and the
        # rect of the canvas that move shifts, x, y, w, h and its dy (`_mv`).
        self._top = -1
        self._wtop = -1
        self._k = 0
        self._mv = [0, 0, 0, 0, 0]
        self.live = True             # not closed since its last draw: it draws the run's rows
        # The plan this frame made: its bands' count (-1 none), the gen, the
        # move and the look's gen it saw, and the theme it read (`_pth`): a draw that finds
        # them all still so takes its walk and bands as they are.
        self._pl = [-1, 0, 0, 0]
        self._ab = [0, 0]             # `_span`'s rows, a..b
        self._pth = None

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
        cols = self._view[0]
        if cols:
            # The edit lays out the line it changed; the frame that draws it
            # finds it laid out.
            self._row(ed.lines[ed.row], cols)
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
        self._memo = {}
        self._memo_old = {}
        self._img = {}
        self._nvis = 0
        self._drag = None

    # -- drawing --------------------------------------------------------------

    def owes(self):
        """Whether the handle has a frame to draw: a verb changed it since its
        last draw (a key handed to it outside a drawn frame) or its idle save
        is due -- what an app drawn on change (`Player.animates`) asks of
        every handle it opened."""
        if self._gen != self._drawn:
            return True
        if self.dirty():
            at = self._edit_ms
            return at is None or _ticks_diff(_ticks_ms(), at) >= AUTOSAVE_MS
        return False

    def draw(self, x, y, w, h, scale=1, r=0):
        """Render the document into `(x, y, w, h)` of the cart's canvas, on
        its paper (corners of radius `r`).

        Where the canvas holds the last draw but for what this frame's pass
        put over the page's ground under it (its run's frame draws a floor),
        or holds it untouched (nothing wrote the canvas since: its counters),
        only the rows that differ from the last draw's are drawn
        (docs/scroll_and_text_2026-10.md §5); otherwise the document whole.

        Also where the idle autosave lands: the shipped notebook has always
        run its debounce off the draw pass (Writer's `#108` model), and a kid
        pressing save is the thing this console does not do."""
        cv = self._canvas
        th = self._theme()
        self.live = True
        g = self._lay(x, y, w, h, scale)
        # one name at a time: a wider unpack builds a tuple every draw
        tx = g[0]
        ty = g[1]
        cols = g[2]
        rows = g[3]
        cell = g[4]
        lh = g[5]
        scale = g[6]
        px = g[7]
        x = int(x)
        y = int(y)
        w = int(w)
        h = int(h)
        r = int(r)
        self._autosave()
        self._drawn = self._gen
        self._keep_caret(cols, rows)
        nb = self._rows(cv, th, x, y, w, h, r, ty, rows, lh)
        reg = self._faces_in[0]
        was = _ui_draw.print_font(reg) if reg else None
        d = self.drew
        try:
            if nb < 0:
                # Clipped to the rect the skin gave: a long line, a tall embed
                # and the last part-row stop at the editor's edge instead of
                # over the skin's own chrome. Released to the full canvas
                # afterwards, which is where the Player leaves it for every
                # cart frame anyway.
                cv.clip(x, y, w, h)
                self._paper(cv, th, x, y, w, h, r)
                self._paint(cv, th, tx, ty, w - 2 * px, cols, rows, cell, lh, scale)
                a = self.autoscrolling()
                if a is not None:
                    _ui_draw.autoscroll_mark(cv, th, a[0], a[1], max(1, cell // 7))
                d[0] = x
                d[1] = y
                d[2] = x + w
                d[3] = y + h
            else:
                # Each band on fresh paper; its rows drawn into it, and the
                # row above it where that row's underline reaches into it.
                b = self._band
                d[0] = x
                d[2] = x + w
                d[1] = d[3] = y
                up = lh if self._spill else 0
                for i in range(0, 2 * nb, 2):
                    cv.clip(x, b[i], w, b[i + 1] - b[i])
                    self._paper(cv, th, x, y, w, h, r)
                    self._paint(cv, th, tx, ty, w - 2 * px, cols, rows, cell, lh, scale,
                                b[i] - up, b[i + 1])
                    if i == 0 or b[i] < d[1]:
                        d[1] = b[i]
                    if b[i + 1] > d[3]:
                        d[3] = b[i + 1]
        finally:
            if was is not None:
                _ui_draw.print_font(was)
            cv.clip()
        self.rows_only = nb >= 0
        self._note_seen(cv)

    def _paper(self, cv, th, x, y, w, h, r):
        if r:
            cv.rrect(x, y, w, h, r, th["surface"])
        else:
            cv.rect(x, y, w, h, th["surface"])

    def _lay(self, x, y, w, h, scale):
        """Size the view for a draw at `(x, y, w, h)`, as the draw keeps it:
        `_lt`, [text x, text y, cols, rows, cell, line height, scale, pad x]."""
        cv = self._canvas
        desk = getattr(cv, "w", 0) >= DESK_W
        reg, bold = self._faces(desk)
        if reg:
            info = self._fmemo[3]
            cell, line = info[0], info[2]
            lh = LOOK_LH[desk]
            px, py = LOOK_PAD[desk]
            scale = 1
        else:
            scale = int(scale) or 1
            cell, line, lh = CELL * scale, 8 * scale, LH * scale
            px = py = 0
        tx, ty = int(x) + px, int(y) + py
        cols = max(1, (int(w) - 2 * px) // cell)
        rows = max(1, (int(h) - py) // lh)
        v = self._view
        if v[0] != cols or v[1] != rows:
            self._view = (cols, rows)
            self.ed.set_view_size(cols, rows)
            self._memo = {}
            self._memo_old = {}
            self._caret_at = None      # a resize re-flows: show the caret again
        # Kept while they hold: a frame drawn as the last one allocates none.
        g = self._geom
        if g[0] != tx or g[1] != ty or g[2] != cell or g[3] != lh or g[4] != scale:
            self._geom = (tx, ty, cell, lh, scale)
        g = self._rect
        if g[0] != x or g[1] != y or g[2] != w or g[3] != h:
            self._rect = (int(x), int(y), int(w), int(h))
        g = self._faces_in
        if g[0] is not reg or g[1] is not bold or g[2] != line:
            self._faces_in = (reg, bold, line)
        # A link's underline lies a line under the text's top: where that is
        # the row's foot, it is the next row's first line.
        self._spill = bool(reg) and (lh - line) // 2 + line >= lh
        if self._m.wrap:
            self.ed.left = 0              # wrapped prose never scrolls sideways
        t = self._lt
        t[0] = tx
        t[1] = ty
        t[2] = cols
        t[3] = rows
        t[4] = cell
        t[5] = lh
        t[6] = scale
        t[7] = px
        return t

    # -- the rows a draw changes (docs/scroll_and_text_2026-10.md §5) ----------

    def rows_plan(self, out, at):
        """At a frame's begin, before the cart's pass: the rects of the
        canvas the next draw changes -- the bands of rows that differ from
        the last draw's, across its rect -- into `out` from slot `at`, x, y,
        w, h each. Their count, or -1 where that draw is the document whole
        (nothing drawn yet, its rect, look or size changed, an embed
        shows). Where the view moved by whole visual rows, the rows the move
        keeps are not among them: `rows_move` names the rect the host shifts
        them in before the pass. The idle save lands here, before the page
        reads the badge it leaves."""
        self._k = 0
        a = self._args
        if a is None or self._nr < 0:
            return -1
        self._autosave()
        x, y, w, h, scale, r = a
        g = self._lay(x, y, w, h, scale)
        ty = g[1]
        cols = g[2]
        rows = g[3]
        lh = g[5]
        self._keep_caret(cols, rows)
        th = self._theme()
        nb = self._diff(th, x, y, w, h, r, ty, rows, lh, False, None)
        p = self._pl
        p[0] = nb
        p[1] = self._gen
        p[2] = self._k
        p[3] = _ui_draw._GEN
        self._pth = th
        if nb < 0 or at + nb > len(out) // 4:
            self._k = 0
            p[0] = -1
            return -1
        b = self._band
        for i in range(nb):
            k = 4 * (at + i)
            out[k] = x
            out[k + 1] = b[2 * i]
            out[k + 2] = w
            out[k + 3] = b[2 * i + 1] - b[2 * i]
        return nb

    def rows_move(self):
        """The move the plan `rows_plan` just made takes: x, y, w, h, dy, the
        rect of the canvas whose last draw's rows shift by dy to where this
        one shows them (inside the paper's round corners); None where its
        rows stay where they were. The host shifts it before the pass, and
        the frame's draw then draws the rows the plan named."""
        return self._mv if self._k else None

    def _rows(self, cv, th, x, y, w, h, r, ty, rows, lh):
        """The bands this draw paints into `_band`, their count; -1 for the
        whole document. Each band is a run of rows that differ from the last
        draw's, across the rect; where the run's frame draws a floor, every
        row its floor reaches joins them and each band is taken into the
        floor over the page's ground. A frame whose host shifted the rows
        the plan moved (`rows_move`) compares them where they now lie."""
        fr = self._frame
        on = fr is not None and fr.floor_on()
        k = self._k if on else 0
        self._k = 0
        p = self._pl
        a = self._args
        if (p[0] >= 0 and p[1] == self._gen and p[2] == k and self._pth is th
                and p[3] == _ui_draw._GEN
                and a[0] == x and a[1] == y and a[2] == w and a[3] == h and a[5] == r
                and a[4] == self._geom[4]):
            nb = p[0]                   # the plan's walk and bands hold: nothing moved since
        else:
            nb = self._diff(th, x, y, w, h, r, ty, rows, lh, True, k)
        p[0] = -1
        self._sl, self._nl = self._nl, self._sl
        self._ss, self._ns = self._ns, self._ss
        self._nr = self._walked
        self._top = self._wtop
        if nb < 0 or fr is None:
            if on and not fr.floor_take(cv, x, y, w, h):
                fr.floor_break()        # the note draws whole, on the page's ground
            return -1
        if on:
            f = self._fr
            n = fr.floor_rects(f)
            for i in range(0, 4 * n, 4):
                y0 = f[i + 1]
                y1 = y0 + f[i + 3]
                if f[i] >= x + w or f[i] + f[i + 2] <= x or y1 <= y or y0 >= y + h:
                    continue
                nb = self._band_add(nb, y0, y1, y, h, ty, rows, lh)
                if nb < 0:
                    return -1
            b = self._band
            for i in range(0, 2 * nb, 2):
                if not fr.floor_take(cv, x, b[i], w, b[i + 1] - b[i]):
                    fr.floor_break()
                    return -1
            return nb
        # Nothing wrote the canvas since the last draw, which it still
        # holds: the bands over the rows the last draw left, where no band
        # crosses the paper's round corners (their edge blends over itself).
        if not fr.holds(cv) or not self._unseen(cv):
            return -1
        if r:
            b = self._band
            for i in range(0, 2 * nb, 2):
                if b[i] < y + r or b[i + 1] > y + h - r:
                    return -1
        return nb

    def _band_add(self, nb, y0, y1, y, h, ty, rows, lh):
        """Rows y0..y1 of the canvas join the bands, widened to whole rows
        (the pad above the first and the part-row under the last are rows
        of their own); runs that meet merge. The count, -1 past _BANDS."""
        foot = ty + rows * lh
        a = y if y0 < ty else ty + (y0 - ty) // lh * lh
        e = y + h if y1 > foot else ty + (y1 - ty + lh - 1) // lh * lh
        if a < y:
            a = y
        if e > y + h:
            e = y + h
        if e <= a:
            return nb
        b = self._band
        i = 0
        while i < 2 * nb:
            if b[i] <= e and b[i + 1] >= a:
                a = b[i] if b[i] < a else a
                e = b[i + 1] if b[i + 1] > e else e
                nb -= 1
                b[i] = b[2 * nb]
                b[i + 1] = b[2 * nb + 1]
                i = 0
                continue
            i += 2
        if nb >= _BANDS:
            return -1
        b[2 * nb] = a
        b[2 * nb + 1] = e
        return nb + 1

    def _diff(self, th, x, y, w, h, r, ty, rows, lh, draw, move):
        """Walk the view as this draw lays it out into `_nl`/`_ns` and
        compare it with the last draw's: the bands of rows that differ, into
        `_band`, their count; -1 where it is the whole document. A `draw`
        keeps what its rows are a function of for the next. Row i is
        compared with the last draw's row i + `move`; a `move` of None finds
        the one the view's move makes (`_moved_by`) and keeps it with its
        rect."""
        reg, bold, line = self._faces_in
        g = self._geom
        k = self._key
        a = self._args
        same = (k is not None and k[0] is th and k[1] == _ui_draw._GEN and k[2] == g[2]
                and k[3] == lh and k[4] == g[4] and k[5] is reg and k[6] is bold
                and k[7] == line and k[8] == self.ed.left and a is not None and a[0] == x
                and a[1] == y and a[2] == w and a[3] == h and a[5] == r and g[1] == ty)
        if not same and draw:
            self._key = (th, _ui_draw._GEN, g[2], lh, g[4], reg, bold, line, self.ed.left)
            self._args = (x, y, w, h, g[4], r)
        n = self._walk(rows)
        self._walked = n
        if not same or n < 0 or self._nr < 0 or self.autoscrolling() is not None:
            return -1
        if move is None:
            move = self._k = self._moved_by(n, x, w, y, h, r, ty, rows, lh)
        if move:
            return self._diff_moved(move, n, y, h, r, ty, rows, lh)
        sl = self._sl
        ss = self._ss
        nl = self._nl
        ns = self._ns
        m = self._nr if self._nr > n else n
        nb = 0
        run = -1
        for i in range(m + 1):
            differs = False
            if i < m:
                if i >= n or i >= self._nr or sl[i] is not nl[i]:
                    differs = True
                else:
                    j = 4 * i
                    differs = (ss[j] != ns[j] or ss[j + 1] != ns[j + 1]
                               or ss[j + 2] != ns[j + 2] or ss[j + 3] != ns[j + 3])
            if differs and run < 0:
                run = i
            elif not differs and run >= 0:
                # a row's underline that reaches the next row moved with it
                e = i + 1 if self._spill else i
                nb = self._band_add(nb, ty + run * lh, ty + e * lh, y, h, ty, rows, lh)
                if nb < 0:
                    return -1
                run = -1
        return nb

    def _span(self, k, y, h, r, ty, rows, lh):
        """The rows a move by k keeps, a..b: each lies, where this draw
        shows it and where the last showed it, inside the paper's straight
        sides (clear of corners of radius r), into `_ab`; a == b where none
        does."""
        lo = y + r
        i0 = 0 if ty >= lo else (lo - ty + lh - 1) // lh
        i1 = (y + h - r - ty) // lh
        if i1 > rows:
            i1 = rows
        a = i0 if k > 0 else i0 - k
        b = i1 - k if k > 0 else i1
        ab = self._ab
        ab[0] = a
        ab[1] = b if b > a else a

    def _moved_by(self, n, x, w, y, h, r, ty, rows, lh):
        """The visual rows this walk's view lies below the last draw's
        (negative above), where its first line moved by whole lines the last
        draw's walk or this one shows: 0 where it did not, or by a view or
        more. Its rect into `_mv`."""
        top = self.ed.top
        t0 = self._top
        if t0 < 0 or top == t0:
            return 0
        if top > t0:
            d = top - t0
            s = self._ss
            m = self._nr
        else:
            d = t0 - top
            s = self._ns
            m = n
        c = 0
        k = 0
        for i in range(1, m):
            if s[4 * i] == 0:
                c += 1
                if c == d:
                    k = i if top > t0 else -i
                    break
        if not k:
            return 0
        self._span(k, y, h, r, ty, rows, lh)
        a = self._ab[0]
        b = self._ab[1]
        if b <= a:
            return 0
        t = a if k > 0 else a + k
        e = b + k if k > 0 else b
        mv = self._mv
        mv[0] = x
        mv[1] = ty + t * lh
        mv[2] = w
        mv[3] = (e - t) * lh
        mv[4] = -k * lh
        return k

    def _diff_moved(self, k, n, y, h, r, ty, rows, lh):
        """`_diff` for a view moved by k visual rows: row i against the last
        draw's row i + k (a blank row matches a blank one), and the rows the
        move does not keep (`_span`) drawn. Where an underline reaches the
        next row, the first row draws (nothing is above it now), and so does
        the row under a row whose own text or marks changed."""
        self._span(k, y, h, r, ty, rows, lh)
        a = self._ab[0]
        b = self._ab[1]
        sl = self._sl
        ss = self._ss
        nl = self._nl
        ns = self._ns
        nr = self._nr
        spill = self._spill
        nb = 0
        run = -1
        was = False
        for i in range(rows + 1):
            differs = chg = False
            if i < rows:
                j = i + k
                if j < 0 or j >= rows:
                    chg = True
                elif i < n:
                    if j >= nr or sl[j] is not nl[i]:
                        chg = True
                    else:
                        p = 4 * i
                        q = 4 * j
                        chg = (ss[q] != ns[p] or ss[q + 1] != ns[p + 1]
                               or ss[q + 2] != ns[p + 2] or ss[q + 3] != ns[p + 3])
                else:
                    chg = j < nr
                differs = chg or i < a or i >= b or (spill and i == 0)
            if differs and run < 0:
                run = i
            elif not differs and run >= 0:
                e = i + 1 if spill and was else i
                nb = self._band_add(nb, ty + run * lh, ty + e * lh, y, h, ty, rows, lh)
                if nb < 0:
                    return -1
                run = -1
            was = chg
        return nb

    def _walk(self, rows):
        """Lay out the view's visual rows into `_nl` (each row's laid line)
        and `_ns` (its segment, the caret's cell in it or -1, the
        selection's cells or -1, -1): how many; -1 where an embed shows,
        whose picture is no row's."""
        ed = self.ed
        cols = self._view[0]
        nl, ns = self._nl, self._ns
        bounds = ed.selection_bounds()
        wrap = self._m.wrap
        k = 0
        brow = ed.top
        self._wtop = brow
        n = len(ed.lines)
        while k < rows and brow < n:
            laid = self._row(ed.lines[brow], cols)
            if laid.img is not None:
                return -1
            here = laid.shown(ed.col) if brow == ed.row else -1
            segs = laid.segs
            for si in range(len(segs)):
                if k >= rows:
                    break
                seg, base = segs[si]
                if wrap:
                    ln = len(seg)
                else:
                    base = ed.left
                    ln = min(cols, max(0, len(seg) - base))
                c = here - base if here >= 0 and base <= here <= base + ln else -1
                s0 = e0 = -1
                if bounds is not None and bounds[0] <= brow <= bounds[2]:
                    s = laid.shown(bounds[1]) if brow == bounds[0] else 0
                    e = laid.shown(bounds[3]) if brow == bounds[2] else base + ln
                    s = s if s > base else base
                    e = e if e < base + ln else base + ln
                    if e > s:
                        s0, e0 = s, e
                if len(nl) <= k:
                    nl.append(None)
                    ns.extend(_SIG_ROW)
                nl[k] = laid
                j = 4 * k
                ns[j] = si
                ns[j + 1] = c
                ns[j + 2] = s0
                ns[j + 3] = e0
                k += 1
            brow += 1
        return k

    def _note_seen(self, cv):
        """The canvas's counters as this draw left them (`_unseen`)."""
        st = getattr(cv, "_gate_state", None)
        s = self._seen
        s[0] = cv if st is not None else None
        if st is not None:
            s[1] = st[_ST_N_FILL]
            s[2] = st[_ST_N_TEXT]
            s[3] = getattr(cv, "clears", 0)

    def _unseen(self, cv):
        """Nothing wrote the canvas since the last draw: its fill and text
        counters and its clears are where that draw left them."""
        st = getattr(cv, "_gate_state", None)
        s = self._seen
        return (st is not None and s[0] is cv and st[_ST_N_FILL] == s[1]
                and st[_ST_N_TEXT] == s[2] and getattr(cv, "clears", 0) == s[3])

    def _faces(self, desk):
        """The note's (regular, bold) faces, (0, 0) for petme128's cells;
        asked of the look once a look (`look_gen`) and size class, the
        regular face's `font_info` kept beside them (`_fmemo`)."""
        if not self._look:
            return (0, 0)
        look = getattr(self._host, "look", None)
        gen = getattr(look, "look_gen", None)
        if gen is None:
            return (0, 0)
        m = self._fmemo
        if m[0] != gen or m[1] is not desk:
            got = look.note_faces(desk)
            m[0] = gen
            m[1] = desk
            m[2] = got
            m[3] = _ui_draw.font_info(got[0]) if got[0] else None
        return m[2]

    def tap(self, px, py, click=True):
        """Route a pointer at `(px, py)` through the LAST DRAWN frame -- the
        draw pass is the hit map.

        Answers what the tap meant, so the skin can act on it:
        `("link", name)` a `[[note]]` (open it), `("check", row)` a checkbox
        (already toggled), `("caret", None)` a plain place, or None when the
        point is outside the editor. Without `click` the answer is only
        whether the point is INSIDE -- nothing moves and nothing toggles."""
        self._gen += 1
        got = self._cell_at(px, py)
        if got is None:
            return None
        brow, col, laid, cell = got
        if not click:
            return ("caret", None)
        self._drag = (int(px), int(py), 0, 0)   # a drag pans/selects from HERE
        if self._mode == _modes.MD:
            if laid.check is not None and abs(cell - laid.check[0] - 1) <= 1:
                self._toggle_check(brow, laid)
                return ("check", brow)
            for c0, c1, target in laid.links:
                if c0 <= cell < c1:
                    return ("link", target)
        if self._is_mouse():
            # A mouse's press: the caret, Shift extending, a second and a
            # third press the word and the line; its drag selects.
            m = getattr(getattr(self._host, "input", None), "mods", 0)
            self._mouse.press(self.ed, brow, col, bool(m & _SHIFT), _ticks_ms())
            return ("caret", None)
        # The press edge COLLAPSES even in SELECT mode and re-anchors here --
        # the Code tab's `_select_pointer` rule, so a fresh drag selects a
        # fresh range instead of growing the last one.
        self._place(brow, col, False)
        if self.ed.select_sticky:
            self.ed.begin_select()
        return ("caret", None)

    def drag(self, px, py, down):
        """One pointer frame that is NOT the press edge -- what happens while
        the finger moves. True when the view or the selection changed.

        In SELECT mode it extends the selection to the finger. Otherwise it
        PANS, content following the finger, sideways too in the modes that do
        not wrap -- the Code tab's gesture (its text view scrolls by the
        finger's pixels), because it is the one a kid's hand already learned
        there."""
        self._gen += 1
        if not down:
            self._drag = None
            self._mouse.release(self.ed)
            return False
        px = int(px)
        py = int(py)
        if self._mouse.dragging:
            got = self._cell_at(px, py)
            return got is not None and self._mouse.drag(self.ed, got[0], got[1])
        if self._drag is None:
            self._drag = (px, py, 0, 0)
            return False
        if self.ed.select_sticky:
            got = self._cell_at(px, py)
            if got is None:
                return False
            self._place(got[0], got[1], True)
            return True
        _x, _y, cell, lh, _scale = self._geom
        drag = self._drag
        self._drag = _ui_draw.pan_drag(drag, self.ed, px, py,
                                       0 if self._m.wrap else cell, lh)
        return self._drag is not drag

    def mouse_frame(self, inp, x, y, dt):
        """One frame of a mouse over the handle, the pointer at (x, y) in
        the canvas: the wheel scrolls it (Shift's sideways where it does not
        wrap), and the middle button pressed over it autoscrolls
        (`TextMouse`). True when the view moved."""
        ed = self.ed
        rx, ry, rw, rh = self._rect
        over = rx <= x < rx + rw and ry <= y < ry + rh
        moved = False
        wy = getattr(inp, "wheel_y", 0)
        wx = getattr(inp, "wheel_x", 0)
        if getattr(inp, "mods", 0) & _SHIFT:
            wx, wy = wx + wy, 0
        if over and (wy or (wx and not self._m.wrap)):
            top, left = ed.top, ed.left
            ed.scroll(3 * wy, 0 if self._m.wrap else 3 * wx)
            moved = (top, left) != (ed.top, ed.left)
        mid = bool(getattr(inp, "pointer_buttons", 0) & _PB_MIDDLE)
        if mid != self._mid and (over or not mid):
            self._mouse.middle(x, y, mid)
        self._mid = mid
        if self._mouse.armed and self._mouse.autoscroll(ed, x, y, dt, self._geom[3]):
            moved = True
        if moved:
            self._gen += 1
            self._caret_at = ed.row             # the view is the reader's now
            self._caret_col = ed.col
        return moved

    def autoscrolling(self):
        """Whether a middle-button autoscroll runs, and where its anchor is."""
        m = self._mouse
        return (m.anchor_x, m.anchor_y) if m.armed else None

    # -- internals ------------------------------------------------------------

    def _is_mouse(self):
        """The pointer is a mouse: it hovers, as a finger never does."""
        p = getattr(self._host, "pointer", None)
        return bool(getattr(p, "hovers", False))

    def _visible(self):
        """The rows the last draw put on screen, as (buffer row, seg, y)."""
        v = self._vis
        return [(v[i], v[i + 1], v[i + 2]) for i in range(0, 3 * self._nvis, 3)]

    def _cell_at(self, px, py):
        """`(buffer row, column, laid-out row, shown cell)` under `(px, py)`,
        or None -- the one place a pixel becomes a text cell, for both `tap`
        and `drag`."""
        x0, _y0, cell, lh, _scale = self._geom
        px = int(px)
        py = int(py)
        vis = self._vis
        for i in range(0, 3 * self._nvis, 3):
            brow = vis[i]
            seg = vis[i + 1]
            yy = vis[i + 2]
            if not (yy <= py < yy + lh):
                continue
            line = self.ed.lines[brow]
            laid = self._row(line, self._view[0])
            text, base = laid.segs[seg] if seg < len(laid.segs) else ("", 0)
            if not self._m.wrap:
                base = self.ed.left
                text = line[base:base + self._view[0]]
            c = base + max(0, (px - x0) // cell)
            if c > base + len(text):
                c = base + len(text)
            return (brow, min(laid.held(c), len(line)), laid, c)
        return None

    def _place(self, brow, col, select):
        """Put the caret on a WHOLE-document cell. `CodeEditor.place` cannot:
        it takes a row from the top of the view, and a wrapped line is several
        visual rows, so the mapping is the draw pass's (`_cell_at`) and only
        the clamping is the editor's."""
        ed = self.ed
        if select:
            ed.begin_select()
        else:
            ed.sel = None
        ed.row = max(0, min(len(ed.lines) - 1, int(brow)))
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

    def _row(self, line, cols):
        """`line`'s laid-out form, memoized by its text: every caller lays out
        at the drawn view's `cols`, and a view of other cols clears the memo
        (`draw`)."""
        key = line
        row = self._memo.get(key)
        if row is None:
            row = self._memo_old.get(key)
            if row is None:
                if self._mode == _modes.MD:
                    row = _md_row(line, cols)
                else:
                    row = _Row(0, None, None, _wrap(line, cols, self._m.wrap),
                               ())
            if len(self._memo) >= _MEMO:
                self._memo_old = self._memo
                self._memo = {}
            self._memo[key] = row
        return row

    def _keep_caret(self, cols, rows):
        """Scroll so the caret's VISUAL row is on screen. A wrapped line is
        several rows, so `CodeEditor.top` (a buffer line) cannot answer this
        on its own.

        FOLLOWS the caret rather than pinning it: a caret that has not moved
        since the last draw asks for nothing, so a drag or a scrollbar can
        take the view anywhere and it stays there. Pinning it every frame
        meant a long note could not be read past its first screen -- the pan
        landed and the next frame undid it."""
        ed = self.ed
        if self._caret_at == ed.row and self._caret_col == ed.col:
            return
        self._caret_at = ed.row
        self._caret_col = ed.col
        if ed.row < ed.top:
            ed.top = ed.row
            return
        if ed.row - ed.top > rows:
            ed.top = ed.row - rows + 1        # a jump: land near, then trim
        used = 0
        r = ed.top
        while r <= ed.row:
            used += len(self._row(ed.lines[r], cols).segs)
            r += 1
        while used > rows and ed.top < ed.row:
            used -= len(self._row(ed.lines[ed.top], cols).segs)
            ed.top += 1

    def _toggle_check(self, row, laid):
        col = laid.held(laid.check[0] + 1)      # the box's `x` or space
        line = self.ed.lines[row]
        self._burst = self.ed.text()
        self.ed.lines[row] = line[:col] + (" " if laid.check[1] else "x") \
            + line[col + 1:]
        self.ed.dirty = True
        self._mark()
        self._close_burst()               # a tick is one undo step of its own

    def _image(self, name):
        img = self._img.get(name, 0)
        if img != 0:
            return img
        blob, err = self._images.load(_IMAGE_KIND, name)
        got = None
        if err is None and blob:
            dec = decode_moyimg(blob)
            if dec is not None:
                got = Image(dec[0], dec[1], dec[2], -1)
        self._img[name] = got
        return got

    # -- the paint pass -------------------------------------------------------

    def _paint(self, cv, th, x, y, w, cols, rows, cell, lh, scale, lo=None, hi=0):
        """The view's rows, every one, or those whose top lies in lo..hi
        (the hit map `_vis` is every row's)."""
        ed = self.ed
        reg, _bold, line = self._faces_in
        dy = (lh - line) // 2 if reg else 0
        vis = self._vis                 # (brow, seg, y) a row, flat and reused
        nv = 0
        drawn = 0
        brow = ed.top
        n = len(ed.lines)
        yy = y
        cx = cy = None
        while drawn < rows and brow < n:
            text = ed.lines[brow]
            laid = self._row(text, cols)
            if laid.img is not None:
                drawn += self._paint_image(cv, th, laid.img, x, yy, w,
                                           (rows - drawn) * lh, lh, scale)
                if len(vis) <= nv:
                    vis.extend(_VIS_ROW)
                vis[nv] = brow
                vis[nv + 1] = 0
                vis[nv + 2] = yy
                nv += 3
                yy = y + drawn * lh
                brow += 1
                continue
            if self._m.lang and laid.hl is None:
                laid.hl = self._colors(text)     # once per LINE, not per frame
            hl = laid.hl
            here = laid.shown(ed.col) if brow == ed.row else -1
            for si in range(len(laid.segs)):
                if drawn >= rows:
                    break
                seg, base = laid.segs[si]
                if not self._m.wrap:
                    # An unwrapped line is ONE segment; the horizontal scroll
                    # is the window into it, so `base` is where it starts.
                    base = ed.left
                    seg = seg[base:base + cols]
                if len(vis) <= nv:
                    vis.extend(_VIS_ROW)
                vis[nv] = brow
                vis[nv + 1] = si
                vis[nv + 2] = yy
                nv += 3
                if lo is None or lo <= yy < hi:
                    sel = self._paint_selection(cv, th, brow, laid, base, seg, x, yy, cell, lh)
                    self._paint_seg(cv, th, laid, seg, base, x, yy + dy, cell, lh, scale, hl)
                    if sel is not None:
                        s0, s1 = sel
                        cv.print(seg[s0 - base:s1 - base], x + (s0 - base) * cell, yy + dy,
                                 th["selection_ink"], scale)
                if base <= here <= base + len(seg) and here >= 0:
                    cx = x + (here - base) * cell
                    cy = yy
                drawn += 1
                yy += lh
            brow += 1
        self._nvis = nv // 3
        if cx is not None and x <= cx < x + w and (lo is None or lo <= cy < hi):
            if reg:
                cv.rect(cx, cy + 2, 2, lh - 4, th["accent"])
            else:
                cv.rect(cx, cy, max(1, scale), 8 * scale, th["accent"])

    def _colors(self, line):
        """The code highlighter's per-character palette indices. Imported at
        the call because only a `code` document ever needs it, and this module
        is reached on every cart start."""
        try:
            from code_layer import _highlight
        except ImportError:  # pragma: no cover - direct host import
            from runtime.code_layer import _highlight
        return _highlight(line, self._m.lang == "lua")

    def _paint_seg(self, cv, th, laid, seg, base, x, yy, cell, lh, scale, hl):
        """One visual row: a heading after its `#` in the disabled ink and
        in the bold, a checkbox as a square (done: filled in `value`, its
        words `dim`), a link in `value` underlined (APPS_RESTYLE.md, Notes)."""
        ink = th["ink"]
        if not seg and laid.check is None:
            return
        if hl is not None:
            self._paint_runs(cv, th, seg, hl[base:base + len(seg)], x, yy, cell, scale, ink)
            return
        reg, bold, line = self._faces_in
        if laid.head:
            # A heading takes the bold, not a size: a wrapped caret has to map
            # onto a uniform row grid, and a taller first row would put every
            # tap on this page one line out.
            cut = laid.head + 1 if base == 0 else 0
            if cut:
                cv.print(seg[:cut], x, yy, _ui_draw.disabled_ink(th), scale)
            if bold and bold != reg:
                _ui_draw.print_font(bold)
            cv.print(seg[cut:], x + cut * cell, yy, ink if reg else th["accent"], scale)
            if bold and bold != reg:
                _ui_draw.print_font(reg)
        elif laid.check is not None and base == 0:
            bx = x + laid.check[0] * cell
            done = laid.check[1]
            if reg:
                box = 13 if cell > 8 else 9
                bx += 2
                by = yy + (line - box) // 2
                if done:
                    cv.rrect(bx, by, box, box, 2, th.get("value", th["accent"]))
                    ink_v = th.get("value_ink", th["surface"])
                    host = self._host
                    r = (bx - 1, by - 1, box + 2, box + 2)
                    if host is None or not host.draw_icon("check", r, ink_v, cv):
                        cv.rect(bx + box // 4, by + box // 4, box // 2, box // 2, ink_v)
                else:
                    cv.rrect(bx, by, box, box, 2, None, ring=th["ink_dim"])
            else:
                # petme128's cells: a box the glyph's size, filled when done
                cv.rectb(bx, yy, cell, 8 * scale, th["ink_dim"])
                if done:
                    cv.rect(bx + scale * 2, yy + scale * 2, cell - scale * 4,
                            8 * scale - scale * 4, th["accent"])
            k = laid.check[0] + 3
            cv.print(seg[k:], x + k * cell, yy, th["ink_dim"] if done else ink, scale)
        else:
            cv.print(seg, x, yy, ink, scale)
        # The link overlay runs over whatever was drawn above, so a checkbox
        # line may carry one -- `- [ ] read [[the other note]]` is a note a
        # person actually writes.
        link_c = th.get("value", th["focus"])
        under = (line + 2) if reg else 8 * scale - scale
        for c0, c1, _target in laid.links:
            s = max(c0, base)
            e = min(c1, base + len(seg))
            if e <= s:
                continue
            cv.print(seg[s - base:e - base], x + (s - base) * cell, yy, link_c, scale)
            cv.rect(x + (s - base) * cell, yy + under - (2 if reg else 0),
                    (e - s) * cell, max(1, scale), link_c)

    def _paint_runs(self, cv, th, seg, cols_, x, yy, cell, scale, ink):
        """One highlighted line as runs of one colour (code_layer's idiom)."""
        hl = th.get("hl")
        n = len(seg)
        i = 0
        while i < n:
            c = cols_[i] if i < len(cols_) else ink
            j = i + 1
            while j < n and (cols_[j] if j < len(cols_) else ink) == c:
                j += 1
            cv.print(seg[i:j], x + i * cell, yy,
                     hl.get(c, c) if hl else c, scale)
            i = j

    def _paint_selection(self, cv, th, brow, laid, base, seg, x, yy, cell, lh):
        """The selection's part of one visual row, in the `selection` colour:
        its shown cells (s, e), or None."""
        ed = self.ed
        bounds = ed.selection_bounds()
        if bounds is None:
            return None
        r0, c0, r1, c1 = bounds
        if brow < r0 or brow > r1:
            return None
        s = laid.shown(c0) if brow == r0 else 0
        e = laid.shown(c1) if brow == r1 else base + len(seg)
        s = max(s, base)
        e = min(e, base + len(seg))
        if e <= s:
            return None
        inset = 1 if self._faces_in[0] else 0
        cv.rect(x + (s - base) * cell, yy + inset, (e - s) * cell, lh - 2 * inset,
                th["selection"])
        return (s, e)

    def _paint_image(self, cv, th, name, x, yy, w, h, lh, scale):
        """An `![[drawing]]` embed, its picture at the note's size with the
        edge (the look's: 48 pixels tall on the handheld, 144 on the desk).
        Returns the text rows it consumed."""
        img = self._image(name)
        if img is None:
            cv.print(("?" + name)[:max(1, w // max(1, self._geom[2]))], x, yy,
                     th["danger"], scale)
            return 1
        if not self._faces_in[0] or not hasattr(cv, "picture"):
            cv.spr(img, x, yy, scale)
            return max(1, min(h, img.h * scale) // lh)
        ph = IMAGE_H[lh > LOOK_LH[0]]
        pw = img.w * ph // max(1, img.h)
        top = yy + 4
        cv.picture(img, x, top, pw, ph, 4)
        cv.rrect(x, top, pw, ph, 4, None, ring=th["border"])
        return max(1, -(-(ph + 4) // lh))
