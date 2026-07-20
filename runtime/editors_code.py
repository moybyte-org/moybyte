"""CodeEditor -- editable text buffer + cursor (the on-device code editor, #3).
Split out of editors.py (which re-exports it); pure logic, dependency-free."""


def _is_word_char(ch):
    """A character that can appear inside an identifier (letters/digits/underscore).
    Hand-rolled ranges -- no str.isalnum dependency -- so it runs under MicroPython
    identically to the highlighter's _is_alpha (#89 completion/navigation)."""
    return (ch == "_" or ("a" <= ch <= "z") or ("A" <= ch <= "Z")
            or ("0" <= ch <= "9"))


def _def_kw_name(line):
    """The symbol a `def`/`class` (or Lua `function`/`local function`) line defines,
    or None. Used by def_symbols() for jump-to-symbol (#89). Dotted names
    (`obj.method`, Lua) are kept whole; the parse stops at the first char that can't
    be part of a name (`(`, `:`, whitespace)."""
    s = line.lstrip()
    for kw in ("def ", "class ", "local function ", "function "):
        if s[:len(kw)] == kw:
            rest = s[len(kw):].lstrip()
            j = 0
            n = len(rest)
            while j < n and (_is_word_char(rest[j]) or rest[j] == "."):
                j += 1
            name = rest[:j]
            return name if name else None
    return None


class CodeEditor:
    """Editable text buffer for a cart's main.py: a list of lines plus a
    (row, col) cursor. The shell feeds it keyboard ASCII (key) and tap-to-place
    coordinates (place), and renders COLS x ROWS of it."""

    COLS = 38          # visible columns (8px font across the full 320px screen)
    ROWS = 20          # visible lines (full-screen code editor)

    INDENT = "  "      # block indent/outdent step: two spaces (matches Tab->2 spaces)

    def __init__(self, src="", cols=None, rows=None):
        # COLS/ROWS default to the 320x240 baseline class attrs; a responsive shell
        # (#39 step 2) passes the layout-derived window so a bigger system canvas
        # shows more lines + wider columns. set_view_size() re-clamps on a resize.
        if cols is not None:
            self.COLS = int(cols)
        if rows is not None:
            self.ROWS = int(rows)
        # The internal clipboard (#89): a plain string, NOT the OS clipboard, so
        # copy/cut/paste behave identically on the host and the device. Kept across
        # set_text() (a reload) so a copy survives switching what you view.
        self.clipboard = ""
        self.set_text(src)

    def set_view_size(self, cols, rows):
        """Adopt a new visible window (size/font-scale change) and re-clamp scroll
        so the caret stays in view. Instance COLS/ROWS shadow the class baseline."""
        self.COLS = max(1, int(cols))
        self.ROWS = max(1, int(rows))
        self._scroll()

    def set_text(self, src):
        self.lines = str(src).split("\n")
        if not self.lines:
            self.lines = [""]
        self.row = 0
        self.col = 0
        self.top = 0          # first visible line
        self.left = 0         # first visible column (horizontal scroll)
        self.dirty = False
        self.sel = None            # selection anchor (row, col) or None (#89)
        self.select_sticky = False # SELECT mode: moves/arrows extend the selection

    def text(self):
        return "\n".join(self.lines)

    def _clamp_col(self):
        n = len(self.lines[self.row])
        if self.col > n:
            self.col = n
        elif self.col < 0:
            self.col = 0

    MARGIN = 1            # scrolloff: keep the caret this many cells in from the edge

    def _scroll(self):
        # Keep the caret in view with a 1-cell margin (so there's always a line/col
        # of context ahead), then clamp so we never scroll past the file end.
        m = self.MARGIN
        if self.row < self.top + m:
            self.top = self.row - m
        elif self.row > self.top + self.ROWS - 1 - m:
            self.top = self.row - self.ROWS + 1 + m
        maxtop = len(self.lines) - self.ROWS
        if maxtop < 0:
            maxtop = 0
        self.top = max(0, min(maxtop, self.top))
        if self.col < self.left + m:
            self.left = self.col - m
        elif self.col > self.left + self.COLS - 1 - m:
            self.left = self.col - self.COLS + 1 + m
        if self.left < 0:
            self.left = 0

    def scroll(self, dr, dc):
        """Pan the viewport by (rows, cols) WITHOUT moving the caret (drag-scroll)."""
        maxtop = len(self.lines) - self.ROWS
        if maxtop < 0:
            maxtop = 0
        self.top = max(0, min(maxtop, self.top + dr))
        maxlen = 0
        for ln in self.lines:
            if len(ln) > maxlen:
                maxlen = len(ln)
        maxleft = maxlen - self.COLS
        if maxleft < 0:
            maxleft = 0
        self.left = max(0, min(maxleft, self.left + dc))

    def move(self, dr, dc, select=None):
        # Move the caret by dr rows then dc columns (both honor magnitude, so a
        # multi-pulse trackball roll moves that many cells). Columns wrap across
        # line ends like a real caret. `select` (#89): True extends the selection
        # (shift+arrow / SELECT mode), False collapses it; None defers to the
        # sticky SELECT-mode flag so the trackball/arrows extend while it's on.
        if select is None:
            select = self.select_sticky
        if select:
            self.begin_select()
        else:
            self.sel = None
        if dr:
            self.row = max(0, min(len(self.lines) - 1, self.row + dr))
            self._clamp_col()
        back = dc < 0
        for _ in range(abs(dc)):
            if back:
                if self.col > 0:
                    self.col -= 1
                elif self.row > 0:
                    self.row -= 1
                    self.col = len(self.lines[self.row])
                else:
                    break
            else:
                if self.col < len(self.lines[self.row]):
                    self.col += 1
                elif self.row < len(self.lines) - 1:
                    self.row += 1
                    self.col = 0
                else:
                    break
        self._scroll()

    def insert(self, ch):
        if self.has_selection():             # typing over a selection replaces it (#89)
            self.delete_selection()
        else:
            # Drop a zero-width anchor: the caret is about to advance past it, and
            # a dangling anchor would turn the NEXT edit into a bogus 1-char
            # "selection" that delete_selection() eats (the SEL-then-type bug).
            self.sel = None
        ln = self.lines[self.row]
        self.lines[self.row] = ln[:self.col] + ch + ln[self.col:]
        self.col += 1
        self.dirty = True
        self._scroll()                       # keep the caret on screen while typing

    def newline(self):
        if self.has_selection():             # enter over a selection replaces it (#89)
            self.delete_selection()
        else:
            self.sel = None                  # drop a zero-width anchor (see insert)
        ln = self.lines[self.row]
        head, tail = ln[:self.col], ln[self.col:]
        indent = ""                          # carry indentation (kid-friendly Python)
        for c in head:
            if c == " ":
                indent += " "
            else:
                break
        self.lines[self.row] = head
        self.lines.insert(self.row + 1, indent + tail)
        self.row += 1
        self.col = len(indent)
        self.dirty = True
        self._scroll()

    def backspace(self):
        if self.has_selection():             # backspace over a selection deletes it (#89)
            self.delete_selection()
            return
        self.sel = None                      # drop a zero-width anchor (see insert)
        if self.col > 0:
            ln = self.lines[self.row]
            self.lines[self.row] = ln[:self.col - 1] + ln[self.col:]
            self.col -= 1
            self.dirty = True
            self._scroll()                   # follow the caret back into view
        elif self.row > 0:
            prev = self.lines[self.row - 1]
            self.col = len(prev)
            self.lines[self.row - 1] = prev + self.lines[self.row]
            del self.lines[self.row]
            self.row -= 1
            self.dirty = True
            self._scroll()

    def key(self, code):
        """Feed one keyboard ASCII byte. Returns True if it changed the text."""
        if not code:
            return False
        if code in (0x0D, 0x0A):             # enter / return
            self.newline()
        elif code in (0x08, 0x7F):           # backspace / delete
            self.backspace()
        elif code == 0x09:                   # tab: indent the selection, else 2 spaces
            if self.has_selection():
                self.indent_selection()
            else:
                self.insert(" ")
                self.insert(" ")
        elif 0x20 <= code <= 0x7E:           # printable ASCII
            self.insert(chr(code))
        else:
            return False
        return True

    def place(self, col, row, select=False):
        """Place the cursor at a visible (col, row-from-top) cell -- for tap.
        `select` (#89): True extends the current selection to here (a select-mode
        drag), False collapses it (a plain tap)."""
        if select:
            self.begin_select()
        else:
            self.sel = None
        self.row = max(0, min(len(self.lines) - 1, self.top + row))
        self.col = max(0, min(len(self.lines[self.row]), self.left + col))
        self._scroll()

    def visible_lines(self):
        return self.lines[self.top:min(len(self.lines), self.top + self.ROWS)]

    # -- text selection + clipboard (#89) ------------------------------------
    # A selection is the span between the anchor (self.sel = (row, col), set when a
    # range op begins) and the live caret; None means nothing is selected. The
    # clipboard (self.clipboard) is an INTERNAL string -- deliberately not the OS
    # clipboard -- so copy/cut/paste behave identically on the host and the device.

    def begin_select(self):
        """Anchor a selection at the caret (idempotent: keeps an existing anchor)."""
        if self.sel is None:
            self.sel = (self.row, self.col)

    def clear_select(self):
        self.sel = None

    def has_selection(self):
        return self.sel is not None and self.sel != (self.row, self.col)

    def selection_bounds(self):
        """The selection as a normalized (r0, c0, r1, c1) with (r0, c0) <= (r1, c1),
        or None when nothing is selected."""
        if not self.has_selection():
            return None
        ar, ac = self.sel
        br, bc = self.row, self.col
        if (ar, ac) <= (br, bc):
            return (ar, ac, br, bc)
        return (br, bc, ar, ac)

    def selected_text(self):
        """The selected span joined with newlines ('' when nothing is selected)."""
        b = self.selection_bounds()
        if b is None:
            return ""
        r0, c0, r1, c1 = b
        if r0 == r1:
            return self.lines[r0][c0:c1]
        parts = [self.lines[r0][c0:]]
        for r in range(r0 + 1, r1):
            parts.append(self.lines[r])
        parts.append(self.lines[r1][:c1])
        return "\n".join(parts)

    def delete_selection(self):
        """Remove the selected span, leaving the caret at its start. Returns True
        iff something was deleted (so callers can fall through to a plain edit)."""
        b = self.selection_bounds()
        if b is None:
            return False
        r0, c0, r1, c1 = b
        joined = self.lines[r0][:c0] + self.lines[r1][c1:]
        self.lines[r0:r1 + 1] = [joined]
        self.row, self.col = r0, c0
        self.sel = None
        self.dirty = True
        self._scroll()
        return True

    def copy(self):
        """Copy the selection to the internal clipboard (text unchanged). Returns
        True iff there was a selection to copy."""
        if not self.has_selection():
            return False
        self.clipboard = self.selected_text()
        return True

    def cut(self):
        """Copy the selection then delete it. Returns True iff the text changed."""
        if not self.has_selection():
            return False
        self.clipboard = self.selected_text()
        return self.delete_selection()

    def paste(self):
        """Insert the clipboard at the caret (replacing any selection). Returns True
        iff the text changed."""
        if not self.clipboard:
            return self.delete_selection()   # empty clipboard: paste still drops a selection
        self.delete_selection()
        self.insert_text(self.clipboard)
        return True

    def insert_text(self, s):
        """Insert a (possibly multi-line) string at the caret, advancing it to the
        end of the inserted text. dirty + scroll like a normal edit."""
        if not s:
            return
        if self.has_selection():
            self.delete_selection()
        chunks = str(s).split("\n")
        ln = self.lines[self.row]
        head, tail = ln[:self.col], ln[self.col:]
        if len(chunks) == 1:
            self.lines[self.row] = head + chunks[0] + tail
            self.col = len(head) + len(chunks[0])
        else:
            self.lines[self.row] = head + chunks[0]
            for k in range(1, len(chunks)):
                self.lines.insert(self.row + k, chunks[k])
            self.row += len(chunks) - 1
            self.col = len(chunks[-1])
            self.lines[self.row] = self.lines[self.row] + tail
        self.dirty = True
        self._scroll()

    # -- block indent / outdent (#89) ----------------------------------------
    # Tab / Shift-Tab (and the tool-palette >> / << buttons) shift every line the
    # selection touches by one INDENT step, keeping the caret + anchor over the
    # same text. With no selection they act on the caret's own line.

    def _block_rows(self):
        """The inclusive line range a block op spans: the selection's rows (a
        selection ending at column 0 doesn't include that trailing line), or the
        caret's line when nothing is selected."""
        b = self.selection_bounds()
        if b is None:
            return self.row, self.row
        r0, c0, r1, c1 = b
        if r1 > r0 and c1 == 0:
            r1 -= 1
        return r0, r1

    def indent_selection(self):
        """Prepend one INDENT step to every line in the block. Returns True."""
        r0, r1 = self._block_rows()
        d = len(self.INDENT)
        for r in range(r0, r1 + 1):
            self.lines[r] = self.INDENT + self.lines[r]
        if self.sel is not None and r0 <= self.sel[0] <= r1:
            self.sel = (self.sel[0], self.sel[1] + d)
        if r0 <= self.row <= r1:
            self.col += d
        self.dirty = True
        self._scroll()
        return True

    def outdent_selection(self):
        """Strip up to one INDENT step of leading spaces from every line in the
        block. Returns True iff any line changed."""
        r0, r1 = self._block_rows()
        changed = False
        for r in range(r0, r1 + 1):
            ln = self.lines[r]
            s = 0
            while s < len(self.INDENT) and s < len(ln) and ln[s] == " ":
                s += 1
            if not s:
                continue
            self.lines[r] = ln[s:]
            changed = True
            if self.sel is not None and self.sel[0] == r:
                self.sel = (r, max(0, self.sel[1] - s))
            if self.row == r:
                self.col = max(0, self.col - s)
        if changed:
            self.dirty = True
            self._scroll()
        return changed

    # -- find / search (#89) -------------------------------------------------

    def _offset(self, row, col):
        """Absolute character offset of (row, col) in the newline-joined buffer.
        Defensively clamps a stale position (the buffer can shrink under a caller
        holding an old (row, col) -- e.g. a cut while the find bar is open), so a
        stale row can never index past the buffer."""
        if row > len(self.lines) - 1:
            row = len(self.lines) - 1
            col = len(self.lines[row])
        elif col > len(self.lines[row]):
            col = len(self.lines[row])
        off = 0
        for r in range(row):
            off += len(self.lines[r]) + 1        # +1 for the joining newline
        return off + col

    def _row_col(self, off):
        """Inverse of _offset: the (row, col) at an absolute buffer offset."""
        r = 0
        while r < len(self.lines) and off > len(self.lines[r]):
            off -= len(self.lines[r]) + 1
            r += 1
        if r >= len(self.lines):
            r = len(self.lines) - 1
            off = len(self.lines[r])
        return r, off

    def find(self, query, forward=True, ci=True, include_current=False):
        """Search for `query` from the caret, wrapping around the whole buffer. On a
        hit, move the caret to the match START and select the match (so it renders
        highlighted); returns True. An empty query or no match leaves the caret put
        and returns False. Case-insensitive by default (`ci`). `include_current`
        (the incremental-typing path) accepts a match starting AT the caret;
        explicit next keeps the move-past pos+1 semantics. The query never contains
        a newline (it comes from the one-line find field), so a match always lies
        within a single line."""
        if not query:
            return False
        if self.row > len(self.lines) - 1:       # defensive: a stale caret from a
            self.row = len(self.lines) - 1       # shrunk buffer must never crash
        if self.col > len(self.lines[self.row]):
            self.col = len(self.lines[self.row])
        text = "\n".join(self.lines)
        hay = text.lower() if ci else text
        q = query.lower() if ci else query
        pos = self._offset(self.row, self.col)
        if forward:
            idx = hay.find(q, pos if include_current else pos + 1)
            if idx < 0:
                idx = hay.find(q, 0)             # wrap to the top
        else:
            idx = hay.rfind(q, 0, pos)
            if idx < 0:
                idx = hay.rfind(q)               # wrap to the bottom
        if idx < 0:
            return False
        r, c = self._row_col(idx)
        self.row, self.col = r, c
        self.sel = (r, c + len(query))           # select the match (anchor past its end)
        self._scroll()
        return True

    # -- autocomplete + jump-to-symbol (#89) ---------------------------------
    # Pure buffer logic; the CodeLayer surface drives the popups. Autocomplete
    # completes the identifier left of the caret from a supplied name pool (the
    # cart-API verbs + language keywords) plus the words already in the buffer.
    # Jump-to-symbol lists the `def`/`class` (Lua `function`) lines.

    def word_prefix(self):
        """The identifier immediately left of the caret ('' when there is none, e.g.
        the caret sits after a space/operator, or the run starts with a digit -- a
        number, not a name). This is the text autocomplete replaces."""
        ln = self.lines[self.row]
        i = self.col
        while i > 0 and _is_word_char(ln[i - 1]):
            i -= 1
        pre = ln[i:self.col]
        if pre and ("0" <= pre[0] <= "9"):       # a number, not an identifier prefix
            return ""
        return pre

    def buffer_words(self, prefix):
        """Distinct identifier-shaped words in the whole buffer that start with
        `prefix` (case-sensitive), in first-appearance order, with an exact-match of
        `prefix` itself excluded. `prefix` == '' returns every identifier word."""
        out = []
        seen = {}
        for ln in self.lines:
            i = 0
            n = len(ln)
            while i < n:
                ch = ln[i]
                if ch == "_" or ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
                    j = i + 1
                    while j < n and _is_word_char(ln[j]):
                        j += 1
                    w = ln[i:j]
                    if w not in seen:
                        seen[w] = True
                        out.append(w)
                    i = j
                else:
                    i += 1
        if prefix:
            return [w for w in out if w != prefix and w[:len(prefix)] == prefix]
        return out

    def completions(self, names, limit=12):
        """Autocomplete candidates for the word left of the caret: first the supplied
        `names` (cart-API verbs + language keywords) that start with the prefix, then
        the buffer's own identifiers that start with it -- de-duplicated, the prefix
        itself excluded, capped at `limit`. An empty prefix yields no candidates
        (autocomplete only fires mid-word)."""
        p = self.word_prefix()
        if not p:
            return []
        out = []
        seen = {}
        lp = len(p)
        for nm in names:
            if nm != p and nm[:lp] == p and nm not in seen:
                seen[nm] = True
                out.append(nm)
        for w in self.buffer_words(p):
            if w not in seen:
                seen[w] = True
                out.append(w)
        if limit and len(out) > limit:
            out = out[:limit]
        return out

    def complete(self, word):
        """Accept an autocomplete candidate: replace the identifier prefix left of the
        caret with `word`, leaving the caret at its end. Returns True."""
        p = self.word_prefix()
        ln = self.lines[self.row]
        start = self.col - len(p)
        self.lines[self.row] = ln[:start] + word + ln[self.col:]
        self.col = start + len(word)
        self.sel = None
        self.dirty = True
        self._scroll()
        return True

    def def_symbols(self):
        """(name, row) for every `def`/`class` (or Lua `function`/`local function`)
        line, in file order -- the jump-to-symbol source (#89)."""
        out = []
        for r in range(len(self.lines)):
            name = _def_kw_name(self.lines[r])
            if name:
                out.append((name, r))
        return out

    def goto_row(self, row, col=0):
        """Move the caret to an absolute (row, col), clamped, and scroll it into view
        (jump-to-symbol lands here). Collapses any selection."""
        self.row = max(0, min(len(self.lines) - 1, int(row)))
        self.col = max(0, min(len(self.lines[self.row]), int(col)))
        self.sel = None
        self._scroll()
