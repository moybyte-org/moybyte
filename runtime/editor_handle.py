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
    from editors_code import CodeEditor
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors_code import CodeEditor

try:
    from op_history import History, TextEditCodec, text_diff_op
except ImportError:  # pragma: no cover - direct host import
    from runtime.op_history import History, TextEditCodec, text_diff_op

try:
    import text_modes as _modes
except ImportError:  # pragma: no cover - direct host import
    from runtime import text_modes as _modes

try:
    from moy_image import Image, decode_moyimg
except ImportError:  # pragma: no cover - direct host import
    from runtime.moy_image import Image, decode_moyimg

try:
    from ticks import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - direct host import
    from runtime.ticks import _ticks_ms, _ticks_diff


MAX_CHARS = 8000       # per document -- bounds the SD write + device memory
AUTOSAVE_MS = 2500     # idle before a SOFT save (Writer's #108 debounce)
INVALID = "INVALID"    # the badge a mode's gate raises (#154's SYNTAX twin)

CELL = 8               # one glyph cell at scale 1
LH = 9                 # one text row: the 8px glyph plus a gap

# Laid-out lines held per generation of the `_row` memo (two generations live).
_MEMO = 120

_BURST_BREAK = ".,!?;:"   # a typing burst closes here (#111 phase 3)

# The drawings kind an `![[name]]` embed reads from. Named through the store's
# own table so nothing here spells a folder.
_IMAGE_KIND = "drawings"


class _Row:
    """One buffer line, laid out. Pure data: what `draw` paints and what `tap`
    hit-tests, cached by (text, cols) so an edit invalidates one line."""

    __slots__ = ("head", "check", "img", "segs", "links", "hl")

    def __init__(self, head, check, img, segs, links):
        self.head = head       # heading level 1..3, else 0
        self.check = check     # (box column, checked) for a `- [ ]` line, else None
        self.img = img         # the drawing name of a whole-line `![[name]]`
        self.segs = segs       # [(text, start column)] -- one per visual row
        self.links = links     # [(col0, col1, target)] for the `[[note]]` spans
        self.hl = None         # a code line's per-character colours, once


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
    """`text` laid out as MARKDOWN: heading, checkbox, embed, links, wrap."""
    head = 0
    while head < 3 and text[head:head + 1] == "#":
        head += 1
    if head and text[head:head + 1] not in (" ", ""):
        head = 0
    check = None
    lead = 0
    while text[lead:lead + 1] == " ":
        lead += 1
    mark = text[lead:lead + 6]
    if mark[:3] in ("- [", "* [") and mark[4:6] == "] ":
        check = (lead + 3, mark[3] in "xX")
    img = None
    links = []
    for c0, c1, target, is_img in _link_spans(text):
        if is_img:
            if img is None and text.strip() == text[c0:c1]:
                img = target
        else:
            links.append((c0, c1, target))
    return _Row(head, check, img, _wrap(text, cols, True), tuple(links))


class EditorHandle:
    """The cart-facing editor over ONE document, identified by `(kind, name)`.

    Built by the shell (`Workstation.open_cart_editor`), published into a cart
    namespace by `system_api.make_system_api` as `open_editor`, and released
    with the cart's world -- flushed HARD on the way out (#154), so a kid who
    taps X never loses a note."""

    def __init__(self, files, kind, name, mode, canvas, theme,
                 clip=None, host=None):
        self._files = files          # the (kind, name)-taking Files role
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
        self._badge = ""             # the mode gate's refusal, "" when clean
        self._unsaved = False
        self._edit_ms = None         # when the idle autosave window opened
        self._focus = False
        self._view = (0, 0)          # (cols, rows) the last draw sized it to
        self._memo = {}
        self._memo_old = {}
        self._img = {}               # drawing name -> Image (None when absent)
        self._vis = ()               # the last drawn (buffer row, seg, y) map
        self._geom = (0, 0, CELL, LH, 1)   # x, y, cell, lh, scale
        self._drag = None            # last pointer cell-origin of a live drag
        self._caret_at = None        # the caret the last draw scrolled to see

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
        """The mode gate's refusal for the current text, "" when it parses."""
        return self._badge

    def caret(self):
        """The caret as `(row, col)`, zero-based, in the WHOLE document."""
        return (self.ed.row, self.ed.col)

    def text(self):
        return self.ed.text()

    def set_text(self, body):
        self.ed.set_text(str(body)[:MAX_CHARS])
        self._caret_at = None
        self._mark()

    def scroll(self, rows=0, cols=0):
        """Pan the view without moving the caret (a drag, a scrollbar).

        A pan STAYS: `draw` scrolls the caret back into view only when the
        caret itself moved, so reading the end of a long note does not fight
        the caret left at the top."""
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
        self._focus = False

    def focused(self):
        return self._focus

    def key(self, code):
        """Feed ONE ASCII byte -- an on-screen key, a soft keyboard, a handle
        the skin deliberately left blurred. The shell comes through the same
        door for the focused handle. True when the handle TOOK it (a shortcut
        counts), so a skin can fall through on a key it wants for itself."""
        code = int(code or 0)
        if not code:
            return False
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
        if len(ed.text()) >= MAX_CHARS and code not in (0x08, 0x7F):
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

    def nav(self, dx, dy):
        """Feed DIRECTIONAL input -- the T-Deck trackball, a host arrow key.

        It moves the CARET and the view follows, which is `ws.nav`'s contract
        for the Code tab held one rung down: on the writing board the ball IS
        the arrow keys, and a text surface that spent them on a mouse cursor
        would leave the caret unreachable. In SELECT mode the same motion
        EXTENDS the selection, because `select_sticky` is what `move` reads.
        True when it moved something."""
        dx = int(dx or 0)
        dy = int(dy or 0)
        if not (dx or dy):
            return False
        self.ed.move(dy, dx)
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
        self._close_burst()
        if self.history.undo() is None:
            return False
        self._mark()
        return True

    def redo(self):
        if self.history.redo() is None:
            return False
        self._mark()
        return True

    def select_all(self):
        self.ed.select_all()
        return True

    def select_mode(self, on=None):
        """Turn SELECT mode on or off (`None` toggles); returns the new state.

        The Code tab's `sel` tool, one rung down and for the same reason: the
        T-Deck has no shift-arrow and no Ctrl, so the only way a kid marks a
        range is a MODE in which the caret -- moved by a drag or the trackball
        -- extends the selection instead of collapsing it. Turning it on
        anchors at the caret, so the very next move already selects."""
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

    def can_paste(self):
        """True when there is clipboard text to paste (the system lane's when
        one is attached, else this handle's own)."""
        return bool(self.ed.paste_text())

    def copy(self):
        return self.ed.copy()

    def cut(self):
        if self._burst is None:
            self._burst = self.ed.text()
        if not self.ed.cut():
            self._burst = None
            return False
        self._mark()
        self._close_burst()               # a cut is its own burst edge
        return True

    def paste(self):
        if len(self.ed.text()) + len(self.ed.paste_text()) > MAX_CHARS:
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
        self._close_burst()
        body = self.ed.text()[:MAX_CHARS]
        good, why = _modes.check(self._mode, body)
        self._badge = "" if good else (INVALID + " " + why).rstrip()
        if not good and soft:
            self._edit_ms = _ticks_ms()   # re-check on the next window, not next frame
            return (False, self._badge)
        _, err = self._files.save(self.kind, self._name,
                                  self._files.encode_text(body))
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
        if self._focus:
            self.focus(False)
        if self.dirty():
            self.save()
        self._memo = {}
        self._memo_old = {}
        self._img = {}
        self._vis = ()
        self._drag = None

    # -- drawing --------------------------------------------------------------

    def draw(self, x, y, w, h, scale=1):
        """Render the document into `(x, y, w, h)` of the cart's canvas.

        Also where the idle autosave lands: the shipped notebook has always
        run its debounce off the draw pass (Writer's `#108` model), and a kid
        pressing save is the thing this console does not do."""
        cv = self._canvas
        th = self._theme()
        scale = int(scale) or 1
        cell = CELL * scale
        lh = LH * scale
        cols = max(1, int(w) // cell)
        rows = max(1, int(h) // lh)
        if (cols, rows) != self._view:
            self._view = (cols, rows)
            self.ed.set_view_size(cols, rows)
            self._memo = {}
            self._memo_old = {}
            self._caret_at = None      # a resize re-flows: show the caret again
        self._geom = (int(x), int(y), cell, lh, scale)
        if self._m.wrap:
            self.ed.left = 0              # wrapped prose never scrolls sideways
        self._autosave()
        self._keep_caret(cols, rows)
        # Clipped to the rect the skin gave: a long line, a tall embed and the
        # last part-row stop at the editor's edge instead of over the skin's
        # own chrome. Released to the full canvas afterwards, which is where
        # the Player leaves it for every cart frame anyway.
        cv.clip(x, y, w, h)
        cv.rect(x, y, w, h, th["surface"])
        self._paint(cv, th, x, y, w, cols, rows, cell, lh, scale)
        cv.clip()

    def tap(self, px, py, click=True):
        """Route a pointer at `(px, py)` through the LAST DRAWN frame -- the
        draw pass is the hit map, `ui.Hits`' rule.

        Answers what the tap meant, so the skin can act on it:
        `("link", name)` a `[[note]]` (open it), `("check", row)` a checkbox
        (already toggled), `("caret", None)` a plain place, or None when the
        point is outside the editor. Without `click` the answer is only
        whether the point is INSIDE -- nothing moves and nothing toggles."""
        got = self._cell_at(px, py)
        if got is None:
            return None
        brow, col, laid = got
        if not click:
            return ("caret", None)
        self._drag = (int(px), int(py))   # a drag pans/selects from HERE
        if self._mode == _modes.MD:
            if laid.check is not None and abs(col - laid.check[0]) <= 1:
                self._toggle_check(brow, laid)
                return ("check", brow)
            for c0, c1, target in laid.links:
                if c0 <= col < c1:
                    return ("link", target)
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
        not wrap -- `code_layer._code_drag`'s gesture, because it is the one a
        kid's hand already learned in the Code tab."""
        if not down:
            self._drag = None
            return False
        px = int(px)
        py = int(py)
        if self._drag is None:
            self._drag = (px, py)
            return False
        if self.ed.select_sticky:
            got = self._cell_at(px, py)
            if got is None:
                return False
            self._place(got[0], got[1], True)
            return True
        _x, _y, cell, lh, _scale = self._geom
        drows = (py - self._drag[1]) // lh
        dcols = 0 if self._m.wrap else (px - self._drag[0]) // cell
        if not (drows or dcols):
            return False
        self._drag = (px, py)
        self.ed.scroll(-drows, -dcols)
        return True

    # -- internals ------------------------------------------------------------

    def _cell_at(self, px, py):
        """`(buffer row, column, laid-out row)` under `(px, py)`, or None --
        the one place a pixel becomes a text cell, for both `tap` and
        `drag`."""
        x0, _y0, cell, lh, _scale = self._geom
        px = int(px)
        py = int(py)
        for brow, seg, yy in self._vis:
            if not (yy <= py < yy + lh):
                continue
            line = self.ed.lines[brow]
            laid = self._row(line, self._view[0])
            text, base = laid.segs[seg] if seg < len(laid.segs) else ("", 0)
            if not self._m.wrap:
                base = self.ed.left
                text = line[base:base + self._view[0]]
            col = base + max(0, (px - x0) // cell)
            if col > base + len(text):
                col = base + len(text)
            return (brow, min(col, len(line)), laid)
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
        """`line`'s laid-out form, memoized by (text, cols)."""
        key = (line, cols)
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
        here = (ed.row, ed.col)
        if here == self._caret_at:
            return
        self._caret_at = here
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
        col = laid.check[0]
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
        blob, err = self._files.load(_IMAGE_KIND, name)
        got = None
        if err is None and blob:
            dec = decode_moyimg(blob)
            if dec is not None:
                got = Image(dec[0], dec[1], dec[2], -1)
        self._img[name] = got
        return got

    # -- the paint pass -------------------------------------------------------

    def _paint(self, cv, th, x, y, w, cols, rows, cell, lh, scale):
        ed = self.ed
        ink = th["ink"]
        dim = th["ink_dim"]
        accent = th["accent"]
        link_c = th["focus"]
        vis = []
        drawn = 0
        brow = ed.top
        n = len(ed.lines)
        yy = y
        caret = None
        while drawn < rows and brow < n:
            line = ed.lines[brow]
            laid = self._row(line, cols)
            if laid.img is not None:
                drawn += self._paint_image(cv, th, laid.img, x, yy, w,
                                           (rows - drawn) * lh, scale)
                vis.append((brow, 0, yy))
                yy = y + drawn * lh
                brow += 1
                continue
            if self._m.lang and laid.hl is None:
                laid.hl = self._colors(line)     # once per LINE, not per frame
            hl = laid.hl
            for si in range(len(laid.segs)):
                if drawn >= rows:
                    break
                seg, base = laid.segs[si]
                if not self._m.wrap:
                    # An unwrapped line is ONE segment; the horizontal scroll
                    # is the window into it, so `base` is where it starts.
                    base = ed.left
                    seg = seg[base:base + cols]
                vis.append((brow, si, yy))
                self._paint_selection(cv, th, brow, base, seg, x, yy, cell, lh)
                self._paint_seg(cv, th, laid, seg, base, x, yy, cell, scale,
                                ink, dim, accent, link_c, hl)
                if brow == ed.row and base <= ed.col <= base + len(seg):
                    caret = (x + (ed.col - base) * cell, yy)
                drawn += 1
                yy += lh
            brow += 1
        self._vis = tuple(vis)
        if caret is not None and x <= caret[0] < x + w:
            cv.rect(caret[0], caret[1], max(1, scale), 8 * scale, accent)

    def _colors(self, line):
        """The code highlighter's per-character palette indices. Imported at
        the call because only a `code` document ever needs it, and this module
        is reached on every cart start."""
        try:
            from code_layer import _highlight
        except ImportError:  # pragma: no cover - direct host import
            from runtime.code_layer import _highlight
        return _highlight(line, self._m.lang == "lua")

    def _paint_seg(self, cv, th, laid, seg, base, x, yy, cell, scale,
                   ink, dim, accent, link_c, hl):
        if not seg and laid.check is None:
            return
        if hl is not None:
            self._paint_runs(cv, th, seg, hl[base:base + len(seg)], x, yy,
                             cell, scale, ink)
            return
        if laid.head:
            # A heading COLOURS, it does not resize: a wrapped caret has to map
            # onto a uniform row grid, and a taller first row would put every
            # tap on this page one line out.
            cut = laid.head + 1 if base == 0 else 0
            if cut:
                cv.print(seg[:cut], x, yy, dim, scale)
            cv.print(seg[cut:], x + cut * cell, yy, accent, scale)
        elif laid.check is not None and base == 0:
            # The box is a glyph pair the person taps; the rest is ordinary ink.
            bx = x + (laid.check[0] - 1) * cell
            cv.rectb(bx, yy, cell, 8 * scale, dim)
            if laid.check[1]:
                cv.rect(bx + scale * 2, yy + scale * 2,
                        cell - scale * 4, 8 * scale - scale * 4, accent)
            cv.print(seg[laid.check[0] + 3:],
                     x + (laid.check[0] + 3) * cell, yy, ink, scale)
        else:
            cv.print(seg, x, yy, ink, scale)
        # The link overlay runs over whatever was drawn above, so a checkbox
        # line may carry one -- `- [ ] read [[the other note]]` is a note a
        # person actually writes.
        for c0, c1, _target in laid.links:
            s = max(c0, base)
            e = min(c1, base + len(seg))
            if e <= s:
                continue
            cv.print(seg[s - base:e - base], x + (s - base) * cell, yy,
                     link_c, scale)
            cv.rect(x + (s - base) * cell, yy + 8 * scale - scale,
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

    def _paint_selection(self, cv, th, brow, base, seg, x, yy, cell, lh):
        ed = self.ed
        bounds = ed.selection_bounds()
        if bounds is None:
            return
        r0, c0, r1, c1 = bounds
        if brow < r0 or brow > r1:
            return
        s = c0 if brow == r0 else 0
        e = c1 if brow == r1 else base + len(seg)
        s = max(s, base)
        e = min(e, base + len(seg))
        if e <= s:
            return
        cv.rect(x + (s - base) * cell, yy, (e - s) * cell, lh, th["selection"])

    def _paint_image(self, cv, th, name, x, yy, w, h, scale):
        """An `![[drawing]]` embed. Returns the text rows it consumed."""
        img = self._image(name)
        if img is None:
            cv.print(("?" + name)[:max(1, w // (CELL * scale))], x, yy,
                     th["danger"], scale)
            return 1
        cv.spr(img, x, yy, scale)
        return max(1, min(h, img.h * scale) // (LH * scale))
