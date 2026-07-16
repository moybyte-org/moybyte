"""Backend-agnostic editor cores shared by the host and device consoles.

These classes are pure logic -- no canvas, framebuf, input, or I/O -- so the
*same* file backs both the host reference (`runtime/`, imported as
`runtime.editors`) and the MicroPython device port (`moy_runtime.py`, which
imports it as the frozen top-level module `editors`). The build stages this file
into the firmware `modules/` tree; the host imports it directly. Each side adds
only its own rendering + input glue around these.

  CodeEditor   -- editable text buffer + cursor (the on-device code editor, #3)
  SpriteSheet  -- indexed 8x8 tile sheet + PICO-8 __gfx__-style hex (#4 storage)
  IconSheet    -- SpriteSheet of 16x16 tiles: the editable top-bar icon theme
  PaintEditor  -- pixel-paint state over a sheet tile (#4 editor)
  TileMap      -- grid of tile ids over a sheet + map.moymap hex (#32 storage)
  MapEditor    -- tile-placement state over a TileMap (#32 editor)
  BlockEditor  -- structured-outline block program + cursor (#29 Part 2 editor)

Keep this module dependency-free so it freezes cleanly and imports under both
CPython (tests/host) and MicroPython (device).
"""


class UndoStack:
    """The bounded undo/redo stack discipline shared by every in-editor history
    (Paint #90, Map #91, Music #92, Blocks #93). It owns ONLY the bookkeeping --
    a bounded undo list + a redo list, the fresh-edit push (append, trim to
    `depth`, drop the redo branch), the undo/redo exchange, and clear. Each
    editor keeps its OWN snapshot/capture/restore semantics: it hands in the
    pushed entries and, on undo/redo, a `reverse(entry)` callable that builds the
    counterpart to stash on the opposite stack, so the three exchange shapes all
    fit --
      * Paint/Blocks push a freshly-captured CURRENT snapshot as the reverse;
      * Map moves the SAME popped rec across (reverse=None -- deltas replay both
        ways);
      * Music pushes _snapshot_of(popped) and skips the push when that is None.
    Plain Python (no deps) so it freezes onto the device."""

    def __init__(self, depth):
        self.depth = depth
        self.undo = []            # committed edits, oldest first
        self.redo = []

    def can_undo(self):
        return bool(self.undo)

    def can_redo(self):
        return bool(self.redo)

    def push(self, entry):
        """Record a pre-edit entry (a fresh edit): bounded-append it and drop the
        redo branch -- the classic fork."""
        self.undo.append(entry)
        if len(self.undo) > self.depth:
            del self.undo[0]
        self.redo = []

    def clear(self):
        """Drop both stacks (a structural change makes recorded steps meaningless)."""
        self.undo = []
        self.redo = []

    def _exchange(self, src, dst, reverse):
        """Pop the newest entry off `src`, push its counterpart (bounded) onto
        `dst`, and return the popped entry for the caller to apply. `reverse(entry)`
        builds what to stash -- None from the callable SKIPS the push (Music's
        stale object); reverse=None moves the SAME entry across (Map's replayable
        deltas). Returns None when `src` is empty (real entries are never None)."""
        if not src:
            return None
        entry = src.pop()
        rev = entry if reverse is None else reverse(entry)
        if rev is not None:
            dst.append(rev)
            if len(dst) > self.depth:
                del dst[0]
        return entry

    def take_undo(self, reverse=None):
        """Pop an undo step (stashing its reverse on redo); None if nothing to do."""
        return self._exchange(self.undo, self.redo, reverse)

    def take_redo(self, reverse=None):
        """Pop a redo step (stashing its reverse on undo); None if nothing to do."""
        return self._exchange(self.redo, self.undo, reverse)


class KeyEdge:
    """Single-fire keystroke edge detector shared by the editor surfaces (#89-#93).
    The keyboard streams last_key every frame with NO autorepeat, so a shortcut
    must act only on the 0->key (or key->new-key) EDGE -- a held Ctrl+Z would
    otherwise drain the whole undo stack, one step per frame. Tracks the previous
    byte. `undo_redo(k, on_undo, on_redo)` is the common Ctrl+Z/Y (0x1A/0x19) case
    the one-shot surfaces share; `hit(k)` exposes the raw edge for a richer
    dispatcher (the code editor folds undo/redo in with copy/cut/paste/find)."""

    def __init__(self):
        self.prev = 0

    def reset(self):
        """Forget the last byte (on entering a screen, so a stale key can't fire)."""
        self.prev = 0

    def hit(self, k):
        """True iff k is a fresh press (truthy and != the last byte). Records k as
        the new previous either way, so a caller reads the flag then dispatches."""
        fresh = bool(k) and k != self.prev
        self.prev = k
        return fresh

    def undo_redo(self, k, on_undo, on_redo):
        """The Ctrl+Z/Y shortcut: on a fresh edge fire on_undo (0x1A) / on_redo
        (0x19). Records the edge like hit(), so a held key fires once."""
        if self.hit(k):
            if k == 0x1A:
                on_undo()
            elif k == 0x19:
                on_redo()


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


class _SheetSprite:
    """Minimal blittable returned by SpriteSheet.tile_image: both canvas
    backends' spr() read only .w/.h/.pix/.transparent, so it needs nothing more
    (and avoids coupling this module to either backend's Image class)."""

    def __init__(self, w, h, pix, transparent):
        self.w = w
        self.h = h
        self.pix = pix
        self.transparent = transparent


class SpriteSheet:
    """An indexed sprite sheet: a grid of cols x rows 8x8 tiles, addressed by
    sprite id (row-major) for TIC-80-style spr(n, x, y). Pixels are 16-color
    indices (0-15, the shared base palette) and serialize to a PICO-8
    __gfx__-style hex blob (one nibble per pixel) stored as `sprites.moygfx`."""

    TILE = 8

    def __init__(self, cols=16, rows=16, pix=None):
        self.cols = cols
        self.rows = rows
        self.w = cols * self.TILE
        self.h = rows * self.TILE
        self.pix = pix if pix is not None else bytearray(self.w * self.h)
        self.dirty = False
        self.gen = 0          # bumps on every pset, so a running cart's tile cache
                              # can detect a sprite edit and rebuild (host/device parity)
        # tile_image() memo (keyed by (n, transparent), dropped when `gen` bumps): returns the
        # SAME _SheetSprite object for a tile across frames. Stable object identity is what lets
        # the web recorder's id()-keyed atlas dedup a UI tile (ship its defspr ONCE instead of
        # every frame -> the launcher/settings `unknown`-churn + payload-peak fix), and lets the
        # device's per-image RGB565 cache survive frames for the console's own tiles too.
        self._tile_cache = {}
        self._tile_cache_gen = 0

    @property
    def count(self):
        return self.cols * self.rows

    def pget(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.pix[y * self.w + x]
        return 0

    def pset(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.pix[y * self.w + x] = c & 15
            self.dirty = True
            self.gen += 1

    def tile_origin(self, n):
        return (n % self.cols) * self.TILE, (n // self.cols) * self.TILE

    def tget(self, n, lx, ly):
        ox, oy = self.tile_origin(n)
        return self.pget(ox + lx, oy + ly)

    def tset(self, n, lx, ly, c):
        ox, oy = self.tile_origin(n)
        self.pset(ox + lx, oy + ly, c)

    def tile_image(self, n, transparent=-1):
        """The 8x8 blittable of sprite n for either backend's Canvas.spr. Memoised per
        (n, transparent) and invalidated when `gen` bumps (a pset), so repeated per-frame
        calls return the SAME object -- see the _tile_cache note in __init__."""
        if n < 0 or n >= self.count:
            return None
        if self._tile_cache_gen != self.gen:
            self._tile_cache = {}
            self._tile_cache_gen = self.gen
        key = (n, transparent)
        img = self._tile_cache.get(key)
        if img is not None:
            return img
        ox, oy = self.tile_origin(n)
        w = self.w
        pix = []
        for ly in range(self.TILE):
            base = (oy + ly) * w + ox
            for lx in range(self.TILE):
                pix.append(self.pix[base + lx])
        img = _SheetSprite(self.TILE, self.TILE, pix, transparent)
        self._tile_cache[key] = img
        return img

    def tile_span_image(self, n, tw=1, th=1, transparent=-1):
        """Build a (tw x th)-tile blittable starting at sprite n -- a TIC-80-style
        multi-tile sprite (spr(n, x, y, w=2, h=2) draws the 16x16 block whose
        top-left tile is n). The span reads the contiguous sheet pixels covering
        tiles n, n+1, ..., n+cols, ... so a 2x2 sprite painted as one 16x16 block
        in the editor blits as one image. Spans are clamped to the sheet's right/
        bottom edge so a span starting near the edge never reads out of bounds."""
        if n < 0 or n >= self.count:
            return None
        if tw < 1:
            tw = 1
        if th < 1:
            th = 1
        ox, oy = self.tile_origin(n)
        # Clamp the span to what fits to the right of / below the start tile.
        max_tw = (self.w - ox) // self.TILE
        max_th = (self.h - oy) // self.TILE
        if tw > max_tw:
            tw = max_tw
        if th > max_th:
            th = max_th
        pw = tw * self.TILE
        ph = th * self.TILE
        w = self.w
        pix = []
        for sy in range(ph):
            base = (oy + sy) * w + ox
            for sx in range(pw):
                pix.append(self.pix[base + sx])
        return _SheetSprite(pw, ph, pix, transparent)

    def is_blank(self):
        for p in self.pix:
            if p:
                return False
        return True

    def copy_tile(self, src_sheet, src_n, dst_n=None):
        """Copy one 8x8 tile from another sheet into this one -- the cross-cart
        sprite-reuse primitive (#18). `src_sheet` is any SpriteSheet (another
        cart's sheet, or the shared sheet); `src_n` is the source sprite id and
        `dst_n` is where it lands here (defaults to the same id). Copies pixel by
        pixel through tget/tset so source and destination sheets may differ in
        size. Returns the destination id, or None if either id is out of range."""
        if dst_n is None:
            dst_n = src_n
        if (src_n < 0 or src_n >= src_sheet.count
                or dst_n < 0 or dst_n >= self.count):
            return None
        for ly in range(self.TILE):
            for lx in range(self.TILE):
                self.tset(dst_n, lx, ly, src_sheet.tget(src_n, lx, ly))
        return dst_n

    def to_hex(self):
        """Serialize to h lines of w hex nibbles (PICO-8 __gfx__ style)."""
        w = self.w
        return "\n".join(
            "".join("%x" % (self.pix[y * w + x] & 15) for x in range(w))
            for y in range(self.h)
        )

    @classmethod
    def from_hex(cls, text, cols=16, rows=16):
        sheet = cls(cols, rows)
        w = sheet.w
        y = 0
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if y >= sheet.h:
                break
            for x in range(min(w, len(line))):
                try:
                    sheet.pix[y * w + x] = int(line[x], 16)
                except ValueError:
                    pass
            y += 1
        sheet.dirty = False
        return sheet


class IconSheet(SpriteSheet):
    """An indexed sprite sheet of 16x16 tiles -- the editable icon theme behind the
    unified top bar (Stage 1). It IS a SpriteSheet: everything (tile_image, the flat
    .moygfx hex serialize/parse, copy_tile) drives off self.TILE, so a 16x16 tile is
    automatic. The only overrides are the bigger TILE and a smaller default geometry
    (8 cols x 4 rows = 32 icon slots, a 128x64 sheet). Colors are still the 16-color
    base palette (c & 15), so an icon reads on the dark bar and theme files round-trip
    through the same hex format as sprites.moygfx / shared.moygfx."""

    TILE = 16

    def __init__(self, cols=8, rows=4, pix=None):
        SpriteSheet.__init__(self, cols, rows, pix)

    @classmethod
    def from_hex(cls, text, cols=8, rows=4):
        # Same flat-grid parse as SpriteSheet (which is dimensioned off self.w/self.h,
        # so it already handles 16px tiles); only the default geometry differs. The
        # parse body is duplicated rather than delegating to SpriteSheet.from_hex so it
        # stays a plain classmethod under MicroPython (no __func__ rebinding).
        sheet = cls(cols, rows)
        w = sheet.w
        y = 0
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if y >= sheet.h:
                break
            for x in range(min(w, len(line))):
                try:
                    sheet.pix[y * w + x] = int(line[x], 16)
                except ValueError:
                    pass
            y += 1
        sheet.dirty = False
        return sheet


class TileMap:
    """A w x h grid of tile ids laid over a SpriteSheet -- the data a native
    map() blit walks (#32). Each cell holds a sprite id (0..count-1) or -1 for
    "empty" (no tile drawn there). Backend-agnostic like SpriteSheet, so the host
    Canvas.map and the device DeviceCanvas.map both consume the same grid.

    Storage is a flat bytearray of w*h cells where each byte is `tile_id + 1`
    (so 0 means empty); this keeps the on-disk blob compact and an all-zero map
    is genuinely blank. Serializes to a `map.moymap` text blob: a header line
    `w h` followed by `h` rows of `w * 2` hex digits (one byte per cell, "00"
    = empty), mirroring the PICO-8 __gfx__-style sprites.moygfx pattern. Tile ids
    are capped at 254 (254 distinct tiles is ample for a kid level; the 16x16
    sheet's id 255 simply can't be placed on the map)."""

    EMPTY = -1
    MAX_ID = 254          # a cell stores id+1 in one byte, so 255 is the ceiling

    def __init__(self, w=20, h=15, cells=None):
        self.w = w
        self.h = h
        self.cells = cells if cells is not None else bytearray(w * h)
        self.dirty = False
        self.gen = 0          # bumps on every mset, so a running cart's map cache
                              # can detect an edit and rebuild (host/device parity)

    def mget(self, x, y):
        """Tile id at cell (x, y), or -1 (EMPTY) for a blank/out-of-range cell."""
        x = int(x)
        y = int(y)
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.cells[y * self.w + x] - 1
        return self.EMPTY

    def mset(self, x, y, tile):
        """Set cell (x, y) to a tile id (a negative id clears it to EMPTY)."""
        x = int(x)
        y = int(y)
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        tile = int(tile)
        if tile < 0:
            v = 0
        else:
            if tile > self.MAX_ID:
                tile = self.MAX_ID
            v = tile + 1
        self.cells[y * self.w + x] = v
        self.dirty = True
        self.gen += 1

    def is_blank(self):
        for c in self.cells:
            if c:
                return False
        return True

    def resize(self, new_w, new_h):
        """Grow/shrink the grid to new_w x new_h in place (#91), preserving the
        overlapping top-left content (a grow adds empty cells on the right/bottom,
        a shrink drops the cells past the new edge). Anchored top-left so a running
        cart's existing tiles keep their coordinates. Clamped to >= 1; bumps
        dirty/gen so a cart's map cache rebuilds and SAVE persists the new dims via
        to_hex (the `w h` header already carries them, so from_hex round-trips)."""
        new_w = max(1, int(new_w))
        new_h = max(1, int(new_h))
        if new_w == self.w and new_h == self.h:
            return
        old = self.cells
        ow = self.w
        cw = ow if ow < new_w else new_w
        ch = self.h if self.h < new_h else new_h
        new = bytearray(new_w * new_h)
        for y in range(ch):
            src = y * ow
            dst = y * new_w
            new[dst:dst + cw] = old[src:src + cw]
        self.cells = new
        self.w = new_w
        self.h = new_h
        self.dirty = True
        self.gen += 1

    def to_hex(self):
        """Serialize to `w h` + h rows of w*2 hex digits (one byte/cell)."""
        rows = ["%d %d" % (self.w, self.h)]
        w = self.w
        for y in range(self.h):
            base = y * w
            rows.append("".join("%02x" % self.cells[base + x] for x in range(w)))
        return "\n".join(rows)

    @classmethod
    def from_hex(cls, text, default_w=20, default_h=15):
        """Parse a map.moymap blob (header `w h` + rows of hex byte pairs). Falls
        back to the default dims for a missing/blank header so a truncated blob
        still loads as an empty map rather than throwing."""
        lines = [ln.strip() for ln in str(text).split("\n")]
        lines = [ln for ln in lines if ln]
        w, h = default_w, default_h
        body = lines
        if lines:
            head = lines[0].split()
            if len(head) == 2:
                try:
                    w = int(head[0])
                    h = int(head[1])
                    body = lines[1:]
                except ValueError:
                    pass
        tm = cls(w, h)
        cells = tm.cells
        for y in range(min(h, len(body))):
            row = body[y]
            for x in range(w):
                i = x * 2
                if i + 2 <= len(row):
                    try:
                        cells[y * w + x] = int(row[i:i + 2], 16) & 0xFF
                    except ValueError:
                        pass
        tm.dirty = False
        return tm


class PaintEditor:
    """Pixel-paint state over a SpriteSheet tile: current sprite + paint color +
    sprite size. The shell maps taps on the zoomed grid/palette to these calls.

    `size` is the side length in 8x8 tiles (1 = an 8x8 sprite, 2 = a 16x16 sprite
    spanning a 2x2 block of sheet tiles, 3 = 24x24, TIC-80-style). The paint grid
    edits the size*8 x size*8 pixel region whose top-left is tile `n`'s origin --
    and because the constituent tiles (n, n+1, n+cols, ...) are contiguous in the
    sheet's flat pixel buffer, painting that region writes straight through to all
    of them, no special multi-tile bookkeeping. `paint`/`pick` take grid-local
    pixel coords (0..size*8-1) and offset them onto the tile origin."""

    SIZES = (1, 2, 3)     # selectable sprite sizes (side length in tiles)

    PEN = "pen"           # brush tool: tap/drag paints single pixels (#30)
    FILL = "fill"         # bucket tool: a tap flood-fills the contiguous region (#90)

    # In-editor undo depth (#90). An undo step snapshots ONLY the current editable
    # region (dim*dim palette bytes) -- 64B for an 8x8 sprite, 256B for a 16x16 icon,
    # up to ~2.3KB for a 3x3 icon block -- so the whole ring stays a few KB on the
    # device at the usual sizes. See the undo note in __init__.
    UNDO_DEPTH = 24

    def __init__(self, sheet):
        self.sheet = sheet
        self.n = 0            # current sprite id (top-left tile of the sprite)
        self.color = 8        # current paint color (red, a friendly default)
        self.size = 1         # sprite side length in tiles (1=8x8, 2=16x16, ...)
        self.tool = self.PEN  # active tool (PEN brush / FILL bucket, #90)
        # In-editor undo/redo (#90): two bounded stacks of region snapshots. Paint
        # edits are far too frequent to journal to SD per pixel (the durable journal
        # fires on a SAVE commit, project.py), so an in-RAM stroke-level ring gives
        # responsive undo; a SAVE still lands a durable step. Each entry is
        # (n, size, bytes) -- the region's tile origin + size + its flat pixels -- so
        # a restore targets the right tiles even after the sprite/size was changed.
        self._hist = UndoStack(self.UNDO_DEPTH)
        self._stroke_pre = None   # pending pre-stroke snapshot (set on press, #90)

    # `_undo`/`_redo` proxy the shared stack's lists (read-only; tests inspect them).
    @property
    def _undo(self):
        return self._hist.undo

    @property
    def _redo(self):
        return self._hist.redo

    @property
    def dim(self):
        """Editable region side length in pixels (size * 8)."""
        return self.size * self.sheet.TILE

    def _origin(self):
        return self.sheet.tile_origin(self.n)

    def paint(self, lx, ly):
        """Paint grid-local pixel (lx, ly) within the size*8 region at tile n."""
        if 0 <= lx < self.dim and 0 <= ly < self.dim:
            ox, oy = self._origin()
            self.sheet.pset(ox + lx, oy + ly, self.color)

    def pick(self, lx, ly):
        if 0 <= lx < self.dim and 0 <= ly < self.dim:
            ox, oy = self._origin()
            self.color = self.sheet.pget(ox + lx, oy + ly)

    def select(self, d):
        self.n = (self.n + d) % self.sheet.count
        self._clamp_size()   # a big sprite near the sheet edge shrinks to fit

    def cycle_size(self):
        """Step to the next selectable sprite size (1 -> 2 -> 3 -> 1), clamped so
        the span still fits the sheet from the current tile (a 3x3 sprite near the
        right/bottom edge falls back to a size that fits)."""
        i = self.SIZES.index(self.size) if self.size in self.SIZES else 0
        self.size = self.SIZES[(i + 1) % len(self.SIZES)]
        self._clamp_size()

    def _clamp_size(self):
        """Shrink size until the size*size tile block fits the sheet from tile n."""
        ox, oy = self._origin()
        max_tw = (self.sheet.w - ox) // self.sheet.TILE
        max_th = (self.sheet.h - oy) // self.sheet.TILE
        fit = max_tw if max_tw < max_th else max_th
        if self.size > fit:
            self.size = fit if fit >= 1 else 1

    # -- tool selection (#90) ------------------------------------------------

    def toggle_fill(self):
        """Flip between the PEN brush and the FILL bucket (one shared button)."""
        self.tool = self.PEN if self.tool == self.FILL else self.FILL

    # -- region snapshot / restore (undo primitive, #90) ---------------------

    def _capture(self, n=None, size=None):
        """Snapshot the dim(size) x dim(size) region at tile n as (n, size, bytes).
        Defaults to the current sprite. Reads pixel-by-pixel (like tile_span_image)
        so a multi-tile region -- whose tiles aren't contiguous in the flat buffer --
        captures correctly."""
        if n is None:
            n = self.n
        if size is None:
            size = self.size
        dim = size * self.sheet.TILE
        ox, oy = self.sheet.tile_origin(n)
        sh = self.sheet
        buf = bytearray(dim * dim)
        k = 0
        for ly in range(dim):
            for lx in range(dim):
                buf[k] = sh.pget(ox + lx, oy + ly)
                k += 1
        return (n, size, bytes(buf))

    def _restore(self, snap):
        """Write a snapshot back and re-select its sprite/size so the revert is
        visible (undo/redo). Goes through pset so `gen`/`dirty` bump exactly like a
        paint edit (a running cart preview picks it up)."""
        n, size, buf = snap
        self.n = n
        self.size = size
        self._clamp_size()
        dim = size * self.sheet.TILE
        ox, oy = self.sheet.tile_origin(n)
        sh = self.sheet
        k = 0
        for ly in range(dim):
            for lx in range(dim):
                sh.pset(ox + lx, oy + ly, buf[k])
                k += 1

    def _write_region(self, buf):
        """Write a dim*dim buffer back into the current region."""
        dim = self.dim
        ox, oy = self._origin()
        sh = self.sheet
        k = 0
        for ly in range(dim):
            for lx in range(dim):
                sh.pset(ox + lx, oy + ly, buf[k])
                k += 1

    def _record(self, build):
        """Run an atomic edit as a buffer transform: read the current region ONCE
        into a mutable pixel copy, hand it to `build(buf, dim)` to mutate in place,
        then -- ONLY if it actually changed -- write the region back and journal
        one undo step. A no-op fill / a flip that reproduces the region reads once,
        writes nothing, records nothing (so `gen`/`dirty` only bump on a real edit,
        matching the pset-driven paint path)."""
        pre = self._capture()              # (n, size, bytes) -- the ONE region read
        prebytes = pre[2]
        buf = bytearray(prebytes)
        build(buf, self.dim)
        if buf != prebytes:                # bytearray == bytes compares by content
            self._write_region(buf)
            self._hist.push(pre)

    # -- stroke boundaries (a drag = ONE undo step, #90) ---------------------

    def begin_stroke(self):
        """Mark the start of a brush stroke: snapshot the region ONCE (a fast drag
        calls paint() many times, but a whole press-drag-release is one undo step)."""
        if self._stroke_pre is None:
            self._stroke_pre = self._capture()

    def end_stroke(self):
        """Close a brush stroke: commit its pre-snapshot to the undo ring iff the
        stroke actually changed pixels. Idempotent (safe to call every idle frame)."""
        if self._stroke_pre is not None:
            pre = self._stroke_pre
            self._stroke_pre = None
            if self._capture(pre[0], pre[1])[2] != pre[2]:
                self._hist.push(pre)

    # -- undo / redo (over the shared UndoStack) -----------------------------

    def can_undo(self):
        return self._hist.can_undo()

    def can_redo(self):
        return self._hist.can_redo()

    def undo(self):
        """Revert the last recorded edit. Captures the current region onto the redo
        ring first, then restores. Returns True iff a step was taken."""
        snap = self._hist.take_undo(lambda s: self._capture(s[0], s[1]))
        if snap is None:
            return False
        self._restore(snap)
        return True

    def redo(self):
        """Re-apply the last undone edit (the inverse of undo)."""
        snap = self._hist.take_redo(lambda s: self._capture(s[0], s[1]))
        if snap is None:
            return False
        self._restore(snap)
        return True

    # -- bucket fill (#90) ---------------------------------------------------

    def fill(self, lx, ly):
        """Flood-fill the contiguous same-color run touching grid pixel (lx, ly)
        with the current color, bounded to the editable region. 4-connected and
        ITERATIVE (an explicit index stack, no recursion -- MicroPython has a small
        C stack). A fill onto its own color is a no-op (records no undo step)."""
        if not (0 <= lx < self.dim and 0 <= ly < self.dim):
            return

        def build(buf, dim):
            target = buf[ly * dim + lx]
            repl = self.color & 15
            if target == repl:
                return
            stack = [ly * dim + lx]
            while stack:
                i = stack.pop()
                if buf[i] != target:
                    continue
                buf[i] = repl
                x = i % dim
                if x > 0:
                    stack.append(i - 1)
                if x < dim - 1:
                    stack.append(i + 1)
                if i >= dim:
                    stack.append(i - dim)
                if i < dim * (dim - 1):
                    stack.append(i + dim)
        self._record(build)

    # -- transforms (respect the multi-tile SIZE selection, #90) -------------

    def _transform(self, fn):
        """Transform the region in one undo step: `_record` reads it ONCE into
        `buf`, we snapshot a stable `src` copy, then `fn(src, dst, dim)` writes the
        transformed pixels back into `buf`. Operates on the size*8 square, so a
        2x2/3x3 sprite transforms as a whole block."""
        def build(buf, dim):
            src = bytes(buf)               # a stable read-only source snapshot
            fn(src, buf, dim)
        self._record(build)

    def flip_h(self):
        """Mirror the sprite left<->right."""
        def fn(s, d, n):
            for y in range(n):
                b = y * n
                for x in range(n):
                    d[b + x] = s[b + (n - 1 - x)]
        self._transform(fn)

    def flip_v(self):
        """Mirror the sprite top<->bottom."""
        def fn(s, d, n):
            for y in range(n):
                b = y * n
                sb = (n - 1 - y) * n
                for x in range(n):
                    d[b + x] = s[sb + x]
        self._transform(fn)

    def rotate(self):
        """Rotate the sprite 90 degrees clockwise (the square region maps onto
        itself)."""
        def fn(s, d, n):
            for y in range(n):
                for x in range(n):
                    d[y * n + x] = s[(n - 1 - x) * n + y]
        self._transform(fn)

    def shift(self, dx, dy):
        """Scroll the sprite by (dx, dy) pixels with WRAP (pixels off one edge
        reappear on the opposite one)."""
        def fn(s, d, n):
            dxm = dx % n
            dym = dy % n
            for y in range(n):
                sy = (y - dym) % n
                sb = sy * n
                b = y * n
                for x in range(n):
                    d[b + x] = s[sb + ((x - dxm) % n)]
        self._transform(fn)

    def clear(self):
        """Clear the whole editable region to color 0 (the transparent/erase index)."""
        def build(buf, dim):
            for i in range(len(buf)):
                buf[i] = 0
        self._record(build)


class MapEditor:
    """Tile-placement state over a TileMap + its SpriteSheet -- the map analogue
    of PaintEditor (#32). PaintEditor places palette indices onto a sprite tile;
    this places sprite ids onto map cells. Pure logic: the shell maps taps on the
    visible map region / tile palette to these calls and renders the result.

    Holds the current tile id to stamp + a (cam_x, cam_y) view offset in cells, so
    a map larger than the on-screen window can be panned. `place` stamps the
    current tile, `erase` clears a cell, `pick` samples the cell under a tap into
    the brush, and `select(d)` steps the brush through the sheet's tile ids.

    `size` is the SIZE brush (#57), mirroring PaintEditor's: the stamp side length
    in tiles (1 = one 8x8 tile, 2 = a 16x16 sprite's 2x2 tile block, 3 = 24x24).
    `place` stamps the whole block of CONSECUTIVE tile ids in one tap -- the same
    contiguous layout spr(n, x, y, w, h)/tile_span_image read -- so a map-placed
    big sprite renders identical to the code-drawn one."""

    SIZES = (1, 2, 3)     # selectable stamp sizes (side length in tiles)
    UNDO_MAX = 32         # bounded in-editor undo depth (device RAM is scarce, #91)
    SNAP_DIV = 8          # a batch touching more than w*h/SNAP_DIV cells compacts
                          # to a whole-map snapshot step (per-cell tuples cost ~10x
                          # a raw byte on MicroPython; a full-map flood on a big map
                          # must not retain hundreds of KB per undo step, #91)

    def __init__(self, tilemap, sheet):
        self.tilemap = tilemap
        self.sheet = sheet
        self.n = 0            # current tile id to stamp (a sprite id in the sheet)
        self.size = 1         # stamp side length in tiles (#57; 1 = today's cell)
        self.cam_x = 0        # top-left visible cell (pan offset), in cells
        # In-editor undo/redo (#91): each COMPLETED edit gesture (a stamp, a rect
        # fill, a flood) is one step. Small steps record ONLY the changed cells as
        # (index, prev_byte, new_byte) triples (a LIST); a step that touched more
        # than w*h/SNAP_DIV cells is compacted by end_edit into a whole-map
        # before/after snapshot -- a ("snap", w, h, before, after) TUPLE of two
        # bytes() blobs (2 bytes/cell vs ~30+ per boxed tuple on MicroPython), so a
        # full-map flood/rect step is ~KBs, never hundreds of KB. `begin_edit`
        # opens the batch, `place`/`erase`/`fill_rect`/`flood` append to it via
        # `_set`, `end_edit` commits it (dropping the redo stack). Bounded to
        # UNDO_MAX steps so a long session can't grow without limit.
        self.cam_y = 0
        self._rec = None     # open edit batch (list of (idx, prev, new)) or None
        self._hist = UndoStack(self.UNDO_MAX)   # committed edits (shared discipline)

    # `_undo`/`_redo` proxy the shared stack's lists (read-only; tests inspect them).
    @property
    def _undo(self):
        return self._hist.undo

    @property
    def _redo(self):
        return self._hist.redo

    # -- in-editor undo/redo (#91) --------------------------------------------

    def begin_edit(self):
        """Open a new edit batch (a gesture). Any changes made through `_set` until
        `end_edit` are grouped into one undo step. Flushes a stray open batch first
        (a release always ends one, so this is only belt-and-braces)."""
        if self._rec:
            self.end_edit()
        self._rec = []

    def end_edit(self):
        """Commit the open batch as one undo step (no-op if it made no change or was
        aborted). A big batch (more than w*h/SNAP_DIV changed cells -- a full-map
        flood/rect) is compacted to a whole-map before/after snapshot so a step's
        retained size is bounded by 2*w*h bytes, never a per-cell tuple list (#91).
        Committing an edit drops the redo stack -- the classic branch."""
        rec = self._rec
        self._rec = None
        if rec:
            tm = self.tilemap
            if len(rec) > (tm.w * tm.h) // self.SNAP_DIV:
                after = bytes(tm.cells)            # the batch just finished: cells
                before = bytearray(after)          # ARE the post-edit state
                # Rebuild the pre-edit state by unwinding the recorded prevs newest
                # -> oldest (correct even if a cell was written twice in-gesture).
                for k in range(len(rec) - 1, -1, -1):
                    e = rec[k]
                    before[e[0]] = e[1]
                rec = ("snap", tm.w, tm.h, bytes(before), after)
            self._hist.push(rec)

    def abort_edit(self):
        """Discard the open batch without committing it (the cells are reverted by
        the caller). Used when a stamp gesture turns out to be a pan (#37)."""
        self._rec = None

    def _set(self, x, y, tile):
        """Write one cell through the TileMap AND, when a batch is open, record the
        before/after byte so undo/redo can replay it. Behaves exactly like a bare
        mset when no batch is open (so direct MapEditor use is unchanged)."""
        tm = self.tilemap
        x = int(x)
        y = int(y)
        if not (0 <= x < tm.w and 0 <= y < tm.h):
            return
        idx = y * tm.w + x
        prev = tm.cells[idx]
        tm.mset(x, y, tile)
        new = tm.cells[idx]
        if self._rec is not None and new != prev:
            self._rec.append((idx, prev, new))

    def _apply(self, rec, forward):
        """Replay (forward) or reverse a committed edit onto the live cells, then
        bump dirty/gen so a running cart's map cache rebuilds. Two step forms (#91):
        a LIST of (idx, prev, new) cell deltas, or a compacted ("snap", w, h,
        before, after) whole-map snapshot (undo restores `before`, redo `after`).
        A snapshot only applies at its recorded dims -- resize clears the history,
        so a mismatch can't happen; guarded anyway (a wrong-size blob must never
        be slammed over the live grid)."""
        tm = self.tilemap
        cells = tm.cells
        if type(rec) is tuple:                     # snapshot step
            _, w, h, before, after = rec
            if w == tm.w and h == tm.h:
                cells[:] = after if forward else before
        else:                                      # per-cell delta step
            n = len(cells)
            if forward:
                for idx, prev, new in rec:
                    if 0 <= idx < n:
                        cells[idx] = new
            else:
                # Unwind newest -> oldest so a cell written twice in one gesture
                # lands back on its ORIGINAL byte, not an intermediate one.
                for k in range(len(rec) - 1, -1, -1):
                    idx, prev, new = rec[k]
                    if 0 <= idx < n:
                        cells[idx] = prev
        tm.dirty = True
        tm.gen += 1

    def can_undo(self):
        return self._hist.can_undo()

    def can_redo(self):
        return self._hist.can_redo()

    def undo(self):
        """Revert the most recent committed edit; returns True iff a step was taken.
        Closes any open batch first so an in-flight gesture can't be half-undone. The
        SAME rec moves to the redo stack -- a delta/snapshot replays either way."""
        if self._rec:
            self.end_edit()
        rec = self._hist.take_undo()
        if rec is None:
            return False
        self._apply(rec, False)
        return True

    def redo(self):
        """Re-apply the next undone edit; returns True iff a step was taken."""
        if self._rec:
            self.end_edit()
        rec = self._hist.take_redo()
        if rec is None:
            return False
        self._apply(rec, True)
        return True

    def clear_history(self):
        """Drop the undo/redo stacks (a structural change -- a map resize -- makes
        the recorded cell indices meaningless, so history is reset, #91)."""
        self._rec = None
        self._hist.clear()

    def stamp_span(self):
        """The (tw, th) tile block the current brush stamps: `size` clamped
        independently to what fits the sheet right of / below tile n -- the SAME
        clamp tile_span_image applies, so the stamped block always matches what
        spr(n, x, y, w=size, h=size) draws. The EMPTY brush has no sheet
        footprint and spans size x size."""
        s = self.size
        if self.n < 0:
            return s, s
        max_tw = self.sheet.cols - (self.n % self.sheet.cols)
        max_th = self.sheet.rows - (self.n // self.sheet.cols)
        return (s if s < max_tw else max_tw), (s if s < max_th else max_th)

    def place(self, cell_x, cell_y):
        """Stamp the brush at map cell (cell_x, cell_y): the stamp_span() block of
        consecutive tile ids, n + dy*cols + dx per cell (#57; size 1 is exactly
        the old single mset). Cells past the map edge are dropped by mset; the
        EMPTY brush clears like erase (its pre-#57 behavior)."""
        if self.n < 0:
            self.erase(cell_x, cell_y)
            return
        tw, th = self.stamp_span()
        cols = self.sheet.cols
        for dy in range(th):
            for dx in range(tw):
                self._set(cell_x + dx, cell_y + dy,
                          self.n + dy * cols + dx)

    def erase(self, cell_x, cell_y):
        """Clear the size x size block at map cell (cell_x, cell_y) to empty (no
        tile). Always the full square -- an eraser should be predictable, so it
        ignores the stamp's sheet-edge clamp."""
        for dy in range(self.size):
            for dx in range(self.size):
                self._set(cell_x + dx, cell_y + dy, self.tilemap.EMPTY)

    def fill_rect(self, x0, y0, x1, y1, erase=False):
        """Fill the rectangle spanned by corners (x0,y0)..(x1,y1) inclusive with the
        current single-tile brush (#91): the RECT tool. `erase` (or the EMPTY brush,
        n<0) fills with EMPTY/sky, so a rect works with the eraser too. The corners
        are normalized + clamped to the map, so a drag in any direction fills the
        same region. Records into the open edit batch (one undo step per fill)."""
        tm = self.tilemap
        x0 = int(x0)
        y0 = int(y0)
        x1 = int(x1)
        y1 = int(y1)
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        x0 = 0 if x0 < 0 else x0
        y0 = 0 if y0 < 0 else y0
        x1 = (tm.w - 1) if x1 > tm.w - 1 else x1
        y1 = (tm.h - 1) if y1 > tm.h - 1 else y1
        tile = tm.EMPTY if (erase or self.n < 0) else self.n
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self._set(x, y, tile)

    def flood(self, cell_x, cell_y, erase=False):
        """Flood-fill the contiguous 4-connected region of cells sharing the tile
        under (cell_x, cell_y), replacing it with the current brush (#91): the FLOOD
        tool. Iterative (an explicit stack, never recursion -- MicroPython's frame
        depth is shallow), and bounded by the map: each cell flips at most once, so
        the whole map is the natural ceiling. A same-tile fill (target == brush) is
        a no-op. Records into the open edit batch (one undo step)."""
        tm = self.tilemap
        cell_x = int(cell_x)
        cell_y = int(cell_y)
        w = tm.w
        h = tm.h
        if not (0 <= cell_x < w and 0 <= cell_y < h):
            return
        cells = tm.cells
        target = cells[cell_y * w + cell_x]           # the raw byte to replace
        tile = tm.EMPTY if (erase or self.n < 0) else self.n
        # The byte the brush lays down (mirror TileMap.mset's id+1 / clamp).
        if tile < 0:
            new = 0
        else:
            new = (tile if tile <= tm.MAX_ID else tm.MAX_ID) + 1
        if new == target:
            return                                     # same-tile fill: nothing to do
        stack = [cell_y * w + cell_x]
        while stack:
            idx = stack.pop()
            if cells[idx] != target:
                continue                               # already flipped or a boundary
            cx = idx % w
            cy = idx // w
            self._set(cx, cy, tile)                    # writes `new` + records it
            if cx > 0:
                stack.append(idx - 1)
            if cx < w - 1:
                stack.append(idx + 1)
            if cy > 0:
                stack.append(idx - w)
            if cy < h - 1:
                stack.append(idx + w)

    def cycle_size(self):
        """Step to the next stamp size (1 -> 2 -> 3 -> 1), PaintEditor-style."""
        i = self.SIZES.index(self.size) if self.size in self.SIZES else 0
        self.size = self.SIZES[(i + 1) % len(self.SIZES)]

    def pick(self, cell_x, cell_y):
        """Sample the tile at a map cell into the brush (skip empty cells, so a
        tap on blank space doesn't reset the brush to a confusing -1)."""
        tid = self.tilemap.mget(cell_x, cell_y)
        if tid >= 0:
            self.n = tid

    def select(self, d):
        """Step the brush tile id by d, wrapping through the sheet's tile ids."""
        self.n = (self.n + d) % self.sheet.count

    def pan(self, dcx, dcy):
        """Pan the view by (dcx, dcy) cells, clamped so the top-left visible cell
        always stays inside the map (never scroll the whole map off the window)."""
        self.cam_x = max(0, min(self.tilemap.w - 1, self.cam_x + dcx))
        self.cam_y = max(0, min(self.tilemap.h - 1, self.cam_y + dcy))


# The block editor's in-session undo/redo depth (#93). Scripts are tiny JSON-ish
# trees, so a full-script snapshot per mutation is cheap; a bounded stack keeps
# the RAM footprint fixed on the device. In-session only (it never crosses a
# save / graduation -- the durable journal is Code's, spec Section 7).
_BLK_UNDO_MAX = 40


def _clone_tree(node):
    """A deep copy of a block / program tree -- dicts, lists, and json-safe scalars
    (int / float / str / bool / None). So a pasted/duplicated block or an undo
    snapshot shares NO mutable state with the original (#93). MicroPython-safe (no
    `copy`/`json` needed): the schema is exactly these three shapes."""
    if isinstance(node, dict):
        out = {}
        for k in node:
            out[k] = _clone_tree(node[k])
        return out
    if isinstance(node, list):
        return [_clone_tree(v) for v in node]
    return node


class BlockRow:
    """One visual line of the flattened block-outline (what the cursor moves over).

    Two flavors share this struct so the cursor is a single index over both:
      kind == "block"  -- an existing block; `block` is its dict, `parent` is the
                          list it lives in, `index` its position in that list.
      kind == "insert" -- an empty insert point (a `+` slot) where a NEW statement
                          can be added; `parent`/`index` say where it would land.
    `depth` is the indent level (for the renderer); `is_else` marks the synthetic
    "else" divider row of an if_else (a block row the kid can't delete directly)."""

    def __init__(self, kind, depth, parent, index, block=None, is_else=False):
        self.kind = kind          # "block" | "insert"
        self.depth = depth
        self.parent = parent      # the children list this row belongs to
        self.index = index        # position of the block (or the insert point) in it
        self.block = block        # the block dict for a "block" row, else None
        self.is_else = is_else    # the if_else divider (a non-deletable label row)


class BlockEditor:
    """The structured-outline block program + a cursor over its flattened script
    (issue #29 Part 2). Pure logic -- no rendering, no I/O -- so it backs both the
    host console and the frozen device console. The `blocks` module (Part 1) is the
    vocabulary/compiler; it's INJECTED (not imported) so this stays dependency-free
    and freezes cleanly: the host passes runtime.blocks, the device passes the
    frozen `blocks`.

    The program is the Part-1 `{vars, scripts}` tree. The kid navigates a flattened
    list of rows (events, statements, and the `+` insert points between them) with
    a single cursor; A inserts at an insert point or edits a block, and the edit ops
    (delete / move / set-slot) mutate the tree and re-flatten. No dragging -- exactly
    the decided device-friendly interaction."""

    def __init__(self, blocks, program=None):
        self.blocks = blocks
        self.program = program if program is not None else blocks.empty_program()
        self.cur = 0              # cursor index into self.rows
        self.rows = []
        self.dirty = False
        # -- #93 clipboard + cross-parent move + in-session undo/redo -----------
        self.clipboard = None     # a deep-copied block subtree (copy/paste/duplicate)
        self._move_src = None     # the block object marked for a cross-parent MOVE
        # bounded full-program snapshots (in-session), over the shared discipline
        self._hist = UndoStack(_BLK_UNDO_MAX)
        self.reflow()

    # `_undo`/`_redo` proxy the shared stack's lists (read-only; tests inspect them).
    @property
    def _undo(self):
        return self._hist.undo

    @property
    def _redo(self):
        return self._hist.redo

    # -- flattening ----------------------------------------------------------
    def reflow(self):
        """Rebuild the flat row list from the tree, then clamp the cursor. Called
        after every structural edit so rows/cursor stay in sync with the program."""
        rows = []
        scripts = self.program.get("scripts", []) or []
        for si in range(len(scripts)):
            self._flatten_block(rows, scripts, si, 0)
        # custom-block definitions (#48) render after the event scripts: each proc's
        # define-hat + its indented body, using the same flatten machinery as a hat.
        procs = self.program.get("procs", []) or []
        for pi in range(len(procs)):
            self._flatten_block(rows, procs, pi, 0)
        if not rows:                              # an empty program still needs a row
            rows.append(BlockRow("insert", 0, scripts, 0))
        self.rows = rows
        if self.cur >= len(rows):
            self.cur = len(rows) - 1
        if self.cur < 0:
            self.cur = 0

    def _flatten_block(self, rows, parent, index, depth):
        b = parent[index]
        tid = b.get("t")
        if tid == self.blocks.ELSE_MARKER:        # the if_else divider, drawn as a label
            rows.append(BlockRow("block", depth, parent, index, b, is_else=True))
            return
        rows.append(BlockRow("block", depth, parent, index, b))
        if self.blocks.is_cblock(tid) or self._is_hat(tid) or self.blocks.is_def(tid):
            # A block with a body (hat / c-block / proc definition) shows its children
            # indented, with
            # an insert point before each child and one trailing insert point so the
            # body can always be appended to even when empty. Ensure the body list
            # exists (make_block omits "c" on hats), so inserts have a real target.
            children = b.setdefault("c", [])
            cdepth = depth + 1
            for ci in range(len(children)):
                rows.append(BlockRow("insert", cdepth, children, ci))
                self._flatten_block(rows, children, ci, cdepth)
            rows.append(BlockRow("insert", cdepth, children, len(children)))

    def _is_hat(self, tid):
        d = self.blocks.block_def(tid)
        return bool(d) and d.get("shape") == self.blocks.SHAPE_HAT

    # -- cursor --------------------------------------------------------------
    def move(self, d):
        """Move the cursor by d rows (honors magnitude, like the code editor)."""
        n = len(self.rows)
        if n == 0:
            return
        self.cur = max(0, min(n - 1, self.cur + d))

    def row(self):
        """The row under the cursor (or None for an empty editor)."""
        if 0 <= self.cur < len(self.rows):
            return self.rows[self.cur]
        return None

    def selected_block(self):
        """The block dict under the cursor, or None if the cursor is on an insert
        point (or the synthetic else divider)."""
        r = self.row()
        if r is not None and r.kind == "block" and not r.is_else:
            return r.block
        return None

    def at_insert(self):
        r = self.row()
        return r is not None and r.kind == "insert"

    # -- in-session undo/redo (#93) ------------------------------------------
    # Every mutating edit snapshots the WHOLE program (a tiny tree) onto a bounded
    # stack BEFORE it changes anything, so undo restores the prior program verbatim.
    # In-session only: the stack lives on this editor instance and is dropped when a
    # different cart opens (BlockEditorUI rebuilds the editor). It never crosses a
    # save or graduation -- those are the durable code journal's job (spec Section 7).
    def _record(self):
        """Push a pre-edit snapshot for undo and clear the redo stack. Called at the
        top of every mutating op (after its guards, so a no-op records nothing)."""
        self._hist.push(_clone_tree(self.program))

    def can_undo(self):
        return self._hist.can_undo()

    def can_redo(self):
        return self._hist.can_redo()

    def undo(self):
        """Restore the program to the state before the last mutation. Returns True
        if a step was taken. The reverse pushed to redo is a fresh clone of the
        CURRENT program (captured before the restore)."""
        snap = self._hist.take_undo(lambda _e: _clone_tree(self.program))
        if snap is None:
            return False
        self.program = snap
        self._after_history()
        return True

    def redo(self):
        """Re-apply the mutation the last undo reverted. Returns True if it moved."""
        snap = self._hist.take_redo(lambda _e: _clone_tree(self.program))
        if snap is None:
            return False
        self.program = snap
        self._after_history()
        return True

    def _after_history(self):
        """Shared undo/redo tail: a restored program is a fresh tree, so any marked
        move source is stale -- drop it -- and the outline must re-flatten."""
        self._move_src = None
        self.dirty = True
        self.reflow()

    # -- structural edits ----------------------------------------------------
    def insert_block(self, type_id, params=None, children=None):
        """Insert a freshly-built block (make_block) at the cursor's insert point.
        No-op (returns None) if the cursor isn't on an insert point. The new block
        becomes the selection so editing/nesting flows continue on it. Returns the
        new block dict on success."""
        r = self.row()
        if r is None or r.kind != "insert":
            return None
        blk = self.blocks.make_block(type_id, params, children)
        self._record()
        r.parent.insert(r.index, blk)
        self.dirty = True
        self.reflow()
        self._select_block(blk)
        return blk

    def insert_else(self):
        """Add an else divider to the if_else c-block under the cursor (so the kid
        can build the else branch). No-op if the selected block isn't an if_else or
        already has an else. The else marker goes at the END of the children, after
        the if-body. Returns True on success."""
        b = self.selected_block()
        if b is None or b.get("t") != "if_else":
            return False
        children = b.setdefault("c", [])
        for c in children:
            if c.get("t") == self.blocks.ELSE_MARKER:
                return False                      # only one else per if_else
        self._record()
        children.append(self.blocks.make_block(self.blocks.ELSE_MARKER))
        self.dirty = True
        self.reflow()
        return True

    def delete(self):
        """Delete the selected block (and its whole subtree). Refuses to delete an
        event hat (a script must keep its lifecycle) and the synthetic else divider.
        Returns True if something was removed."""
        r = self.row()
        if r is None or r.kind != "block" or r.is_else:
            return False
        tid = r.block.get("t")
        if self._is_hat(tid):
            return False                          # never delete an event hat
        self._record()
        del r.parent[r.index]
        self.dirty = True
        self.reflow()
        # keep the cursor near where the block was (clamp handles the tail case)
        self.cur = max(0, min(len(self.rows) - 1, self.cur))
        return True

    def move_block(self, d):
        """Reorder the selected block up (d<0) / down (d>0) among its SIBLINGS in
        the same body. Won't move a hat (events stay ordered by kind) or the else
        divider, and won't move past the ends of its sibling list. Returns True if
        it moved."""
        r = self.row()
        if r is None or r.kind != "block" or r.is_else:
            return False
        if self._is_hat(r.block.get("t")):
            return False
        siblings = r.parent
        i = r.index
        j = i + (1 if d > 0 else -1)
        if j < 0 or j >= len(siblings):
            return False
        if siblings[j].get("t") == self.blocks.ELSE_MARKER:
            # Don't shuffle a statement across the else boundary by a single step --
            # that silently changes branches. The kid moves it explicitly instead
            # (the #93 MOVE flow crosses the divider on purpose).
            return False
        self._record()
        siblings[i], siblings[j] = siblings[j], siblings[i]
        self.dirty = True
        self.reflow()
        self._select_block(siblings[j])
        return True

    # -- copy / paste / duplicate + cross-parent move (#93) -------------------
    def copy_block(self):
        """Deep-copy the selected block (and its whole subtree: if/else arms, loop
        bodies, nested reporters) into the clipboard. Refuses an event hat (it can't
        live inside a body) and the synthetic else divider. Returns True on success."""
        r = self.row()
        if r is None or r.kind != "block" or r.is_else:
            return False
        tid = r.block.get("t")
        if self._is_hat(tid) or self.blocks.is_def(tid) or tid == self.blocks.ELSE_MARKER:
            return False
        self.clipboard = _clone_tree(r.block)
        return True

    def has_clipboard(self):
        return self.clipboard is not None

    def duplicate(self):
        """Insert a deep copy of the selected block immediately after it (same body).
        Same guards as copy (no hats, no else divider). The copy shares no mutable
        state with the original. Returns the new block, or None. Undoable."""
        r = self.row()
        if r is None or r.kind != "block" or r.is_else:
            return None
        tid = r.block.get("t")
        if self._is_hat(tid) or self.blocks.is_def(tid) or tid == self.blocks.ELSE_MARKER:
            return None
        clone = _clone_tree(r.block)
        self._record()
        r.parent.insert(r.index + 1, clone)
        self.dirty = True
        self.reflow()
        self._select_block(clone)
        return clone

    def paste(self):
        """Paste a deep copy of the clipboard at the cursor's insert point. Only at
        an insert point, only when the clipboard holds a paste-able block (never a
        hat / else divider -- copy already refuses those). Each paste is an
        independent deep copy, so repeated pastes never alias. Returns the new block,
        or None. Undoable."""
        if self.clipboard is None:
            return None
        r = self.row()
        if r is None or r.kind != "insert":
            return None
        tid = self.clipboard.get("t")
        if self._is_hat(tid) or self.blocks.is_def(tid) or tid == self.blocks.ELSE_MARKER:
            return None
        clone = _clone_tree(self.clipboard)
        self._record()
        r.parent.insert(r.index, clone)
        self.dirty = True
        self.reflow()
        self._select_block(clone)
        return clone

    def start_move(self):
        """Mark the selected block as the source of a cross-parent MOVE (the
        destination is then any insert point -- across the if/else divider or into a
        different body, which the single-step reorder can't reach). Refuses a hat /
        else divider. Returns True if the block can be moved."""
        r = self.row()
        if r is None or r.kind != "block" or r.is_else:
            return False
        if self._is_hat(r.block.get("t")) or self.blocks.is_def(r.block.get("t")):
            return False
        self._move_src = r.block
        return True

    def moving(self):
        return self._move_src is not None

    def cancel_move(self):
        self._move_src = None

    def complete_move(self):
        """Move the marked block to the cursor's insert point (across parents / the
        if-else divider) -- ONE undoable op that preserves the block's identity (the
        same object is re-parented, not cloned). Rejects a destination inside the
        block's own subtree (that would orphan it). Returns True on success."""
        src = self._move_src
        if src is None:
            return False
        r = self.row()
        if r is None or r.kind != "insert":
            return False
        dest_parent = r.parent
        dest_index = r.index
        if self._within(src, dest_parent):
            return False                          # can't drop a block inside itself
        loc = self._locate(src)
        if loc is None:                           # source vanished (e.g. undone away)
            self._move_src = None
            return False
        src_parent, src_index = loc
        self._record()
        del src_parent[src_index]
        # same body, dropping after the removed slot: the destination shifted up one.
        if dest_parent is src_parent and dest_index > src_index:
            dest_index -= 1
        dest_parent.insert(dest_index, src)
        self._move_src = None
        self.dirty = True
        self.reflow()
        self._select_block(src)
        return True

    def _locate(self, block):
        """Find (parent_list, index) of `block` by identity in the statement tree, or
        None. Walks child bodies only -- a movable block is always a body statement,
        which may live inside an event script OR a custom-block body (#48)."""
        roots = (self.program.get("scripts", []) or []) + \
                (self.program.get("procs", []) or [])
        return self._locate_in(roots, block)

    def _locate_in(self, lst, block):
        for i in range(len(lst)):
            c = lst[i]
            if c is block:
                return (lst, i)
            kids = c.get("c") if isinstance(c, dict) else None
            if kids:
                found = self._locate_in(kids, block)
                if found is not None:
                    return found
        return None

    def _within(self, block, target_list):
        """True if `target_list` is `block`'s own children list or any body nested
        under it (so a MOVE can't drop a block into itself)."""
        kids = block.get("c")
        if kids is target_list:
            return True
        if kids:
            for c in kids:
                if isinstance(c, dict) and self._within(c, target_list):
                    return True
        return False

    # -- slot editing --------------------------------------------------------
    def slots(self, block=None):
        """The slot descriptors for a block (defaults to the selection). Program-aware
        (#48): a `call` yields one expr slot per parameter of the proc it targets, a
        `proc_def` yields none (edited via the PROC menu). Empty for unknown/None."""
        b = block if block is not None else self.selected_block()
        if b is None:
            return []
        return self.blocks.block_slots(self.program, b)

    def slot_value(self, slot_name, block=None):
        b = block if block is not None else self.selected_block()
        if b is None:
            return None
        return (b.get("p", {}) or {}).get(slot_name)

    def set_slot(self, slot_name, value, block=None):
        """Write a slot value on a block (defaults to the selection). The caller is
        responsible for passing a value the slot's type accepts (a number/string
        literal, a variable name, a dropdown option, or an expression block dict for
        an expr slot). Returns True if the block exists."""
        b = block if block is not None else self.selected_block()
        if b is None:
            return False
        # A call's args (#48) live in a positional list p["args"], not as named slots;
        # the dynamic slot names are arg0/arg1/... -> write into the list, growing it
        # (padded with 0) so an arg can be set even before earlier ones were touched.
        if b.get("t") == self.blocks.CALL and slot_name[:3] == "arg":
            try:
                i = int(slot_name[3:])
            except ValueError:
                return False
            self._record()
            args = b.setdefault("p", {}).setdefault("args", [])
            while len(args) <= i:
                args.append(0)
            args[i] = value
            self.dirty = True
            return True
        self._record()
        p = b.setdefault("p", {})
        p[slot_name] = value
        self.dirty = True
        return True

    def cycle_dropdown(self, slot_name, d=1, block=None):
        """Step a dropdown slot to the next/previous option (wrapping). Convenience
        for the picker UI. Returns the new option, or None if the slot isn't a known
        dropdown. (number/text slots are edited via set_slot from the keyboard.)"""
        b = block if block is not None else self.selected_block()
        if b is None:
            return None
        for slot in self.slots(b):
            if slot["name"] == slot_name and slot["type"] == self.blocks.SLOT_DROPDOWN:
                opts = self.blocks.slot_options(slot)
                if not opts:
                    return None
                cur = (b.get("p", {}) or {}).get(slot_name)
                try:
                    i = opts.index(cur)
                except ValueError:
                    i = 0
                val = opts[(i + d) % len(opts)]
                # inline the write (don't call set_slot) so this records ONE undo
                # snapshot for the whole cycle, not a nested double.
                self._record()
                b.setdefault("p", {})[slot_name] = val
                self.dirty = True
                return val
        return None

    # -- variables -----------------------------------------------------------
    def add_var(self, name):
        """Declare a new variable (so variable slots can reference it). The name is
        sanitized into a safe identifier (kid free-text -> `my_score`), de-duplicated,
        and blanks / names already used by a list are ignored. Returns the variable
        list."""
        name = self.blocks.sanitize_var_name(name)
        vars_ = self.program.setdefault("vars", [])
        if name and name not in vars_ and name not in self.lists() \
                and name not in self.proc_names():
            self._record()
            vars_.append(name)
            self.dirty = True
        return vars_

    def new_var(self, base="var"):
        """Create a freshly-named variable with a sensible default (var, var2, ...)
        the kid can rename. The name is unique across BOTH variables and lists (they
        share the module-level global namespace). Returns the new variable's name."""
        taken = self._all_names()
        name = self.blocks.unique_var_name(taken, base)
        self._record()
        self.program.setdefault("vars", []).append(name)
        self.dirty = True
        return name

    def rename_var(self, old, new):
        """Rename a declared variable AND rewrite every variable-slot reference to it
        across the whole tree, so set/change/expr slots keep pointing at it. The new
        name is sanitized; a blank or duplicate (other than `old` itself) is rejected.
        Returns the applied name, or None if the rename didn't happen."""
        new = self.blocks.sanitize_var_name(new)
        vars_ = self.program.setdefault("vars", [])
        if not new or old not in vars_:
            return None
        if new != old and (new in vars_ or new in self.lists()
                           or new in self.proc_names()):
            return None                       # would collide with a var/list/proc
        self._record()
        vars_[vars_.index(old)] = new
        self._rewrite_name_refs(old, new, self.blocks.SLOT_VARIABLE)
        self.dirty = True
        return new

    def _rewrite_name_refs(self, old, new, slot_type):
        """Walk the tree and rewrite every slot of `slot_type` whose value equals `old`
        to `new` (statements' params, nested expression params, child bodies). Shared by
        the variable and list renamers (#48)."""
        def walk(node):
            if isinstance(node, list):            # a call's positional args (#48)
                for it in node:
                    walk(it)
                return
            if not isinstance(node, dict):
                return
            d = self.blocks.block_def(node.get("t"))
            params = node.get("p", {}) or {}
            if d is not None:
                for slot in d["slots"]:
                    nm = slot["name"]
                    if slot["type"] == slot_type and params.get(nm) == old:
                        params[nm] = new
            for v in params.values():
                walk(v)                       # nested expression blocks in expr slots
            for c in node.get("c", []) or []:
                walk(c)

        for s in (self.program.get("scripts", []) or []) + \
                 (self.program.get("procs", []) or []):
            walk(s)

    def variables(self):
        return list(self.program.get("vars", []) or [])

    # -- lists (#48) ---------------------------------------------------------
    # Lists mirror variables: declared at the program level, picked into list slots,
    # created + named through the same on-screen-keyboard flow. A list and a variable
    # can't share a name (both compile to module-level globals).
    def add_list(self, name):
        """Declare a new list. The name is sanitized into a safe identifier, blanks /
        duplicates / names already used by a variable are ignored. Returns the list."""
        name = self.blocks.sanitize_var_name(name)
        lists_ = self.program.setdefault("lists", [])
        if name and name not in lists_ and name not in self.variables() \
                and name not in self.proc_names():
            self._record()
            lists_.append(name)
            self.dirty = True
        return lists_

    def new_list(self, base="list"):
        """Create a freshly-named list (list, list2, ...) the kid can rename. The name
        is unique across BOTH lists and variables. Returns the new list's name."""
        taken = self._all_names()
        name = self.blocks.unique_var_name(taken, base)
        self._record()
        self.program.setdefault("lists", []).append(name)
        self.dirty = True
        return name

    def rename_list(self, old, new):
        """Rename a declared list AND rewrite every list-slot reference to it. Sanitized;
        a blank / duplicate / clash with a variable is rejected. Returns the applied
        name, or None."""
        new = self.blocks.sanitize_var_name(new)
        lists_ = self.program.setdefault("lists", [])
        if not new or old not in lists_:
            return None
        if new != old and (new in lists_ or new in self.variables()
                           or new in self.proc_names()):
            return None
        self._record()
        lists_[lists_.index(old)] = new
        self._rewrite_name_refs(old, new, self.blocks.SLOT_LIST)
        self.dirty = True
        return new

    def lists(self):
        return list(self.program.get("lists", []) or [])

    # -- custom blocks (#48: My Blocks / procedures) -------------------------
    # Procs mirror vars/lists at the program level: a definition list, created +
    # named through the same on-screen-keyboard flow, with a name unique across the
    # whole module-global namespace (vars + lists + procs) and NOT a reserved cart-API
    # verb. A proc's define-hat + body render in the outline after the event scripts.
    def procs(self):
        return list(self.program.get("procs", []) or [])

    def proc_names(self):
        return [self.blocks.proc_name(pd) for pd in self.procs()]

    def _all_names(self):
        return self.variables() + self.lists() + self.proc_names()

    def new_proc(self, base="block"):
        """Create a fresh, empty custom block with a unique default name the kid can
        rename, and select its define-hat. Returns the new proc_def block."""
        base = self.blocks.sanitize_var_name(base) or "block"
        if self.blocks.is_reserved_name(base):
            base = base + "_"                 # never seed a name that shadows the API
        name = self.blocks.unique_var_name(self._all_names(), base)
        self._record()
        pd = self.blocks.make_block(self.blocks.PROC_DEF, {"name": name, "params": []})
        self.program.setdefault("procs", []).append(pd)
        self.dirty = True
        self.reflow()
        self._select_block(pd)
        return pd

    def rename_proc(self, old, new):
        """Rename a custom block AND rewrite every call targeting it. The new name is
        sanitized; a blank / reserved / duplicate / clash with a var/list is rejected.
        Returns the applied name, or None."""
        new = self.blocks.sanitize_var_name(new)
        if not new or self.blocks.is_reserved_name(new):
            return None
        pd = self.blocks.find_proc(self.program, old)
        if pd is None:
            return None
        if new != old and new in self._all_names():
            return None
        self._record()
        pd.setdefault("p", {})["name"] = new
        self._rewrite_call_names(old, new)
        self.dirty = True
        return new

    def delete_proc(self, proc_def):
        """Remove a custom block entirely (stray calls to it then compile to `pass`).
        Returns True if it was removed."""
        procs = self.program.get("procs", []) or []
        for i in range(len(procs)):
            if procs[i] is proc_def:
                self._record()
                del procs[i]
                self.dirty = True
                self.reflow()
                self.cur = max(0, min(len(self.rows) - 1, self.cur))
                return True
        return False

    def add_param(self, proc_def, name):
        """Append a parameter to a custom block. The name is sanitized + made unique
        within the proc (blank/reserved/duplicate rejected). Returns the applied name
        or None; existing calls keep their args (a new trailing arg defaults to 0)."""
        if proc_def is None:
            return None
        name = self.blocks.sanitize_var_name(name)
        if not name or self.blocks.is_reserved_name(name):
            return None
        params = self.blocks.proc_params(proc_def)
        if name in params:
            return None
        self._record()
        params.append(name)
        proc_def.setdefault("p", {})["params"] = params
        self.dirty = True
        self.reflow()
        return name

    def remove_last_param(self, proc_def):
        """Drop the last parameter of a custom block. Returns True if one was removed."""
        if proc_def is None:
            return False
        params = self.blocks.proc_params(proc_def)
        if not params:
            return False
        self._record()
        params.pop()
        proc_def.setdefault("p", {})["params"] = params
        self.dirty = True
        self.reflow()
        return True

    def insert_call(self, name):
        """Insert a call to proc `name` at the cursor's insert point, pre-filling one
        default (0) arg per parameter. Returns the new call block, or None."""
        pd = self.blocks.find_proc(self.program, name)
        if pd is None:
            return None
        n = len(self.blocks.proc_params(pd))
        return self.insert_block(self.blocks.CALL, {"name": name, "args": [0] * n})

    def _rewrite_call_names(self, old, new):
        """Rewrite every call targeting `old` to target `new` (after a proc rename)."""
        def walk(node):
            if isinstance(node, list):
                for it in node:
                    walk(it)
                return
            if not isinstance(node, dict):
                return
            if node.get("t") == self.blocks.CALL and \
                    self.blocks.proc_name(node) == old:
                node.setdefault("p", {})["name"] = new
            for v in (node.get("p", {}) or {}).values():
                walk(v)
            for c in node.get("c", []) or []:
                walk(c)
        for s in (self.program.get("scripts", []) or []) + \
                 (self.program.get("procs", []) or []):
            walk(s)

    def enclosing_proc(self):
        """The proc_def whose body contains the cursor row (or None). Lets the editor
        offer a proc's PARAMETERS as variables while editing inside its body (#48)."""
        r = self.row()
        if r is None:
            return None
        for pd in self.procs():
            if self._within(pd, r.parent):
                return pd
        return None

    def current_params(self):
        """The parameter names in scope at the cursor (the enclosing proc's params),
        or [] outside any custom-block body."""
        pd = self.enclosing_proc()
        return self.blocks.proc_params(pd) if pd is not None else []

    # -- helpers -------------------------------------------------------------
    def _select_block(self, blk):
        """Park the cursor on the row that holds `blk` after a reflow (so an insert/
        move leaves the new/moved block selected)."""
        for i in range(len(self.rows)):
            r = self.rows[i]
            if r.kind == "block" and r.block is blk:
                self.cur = i
                return


# -- music / sound editor (#50) ----------------------------------------------

# Editable bounds for an SFX step, mirrored from runtime/audio.py so this core
# stays dependency-free (the docstring contract): a step is [pitch, wave, vol].
# pitch is a semitone index 0..95 (C0..B7) or -1 for a rest; wave is 0..3
# (square/triangle/saw/noise); vol is 0..7. The console renders pitch as a note
# name; this core only ever stores/clamps the integers, so it never imports audio.
_ME_REST = -1
_ME_PITCH_MIN = 0
_ME_PITCH_MAX = 95
_ME_WAVE_MIN = 0
_ME_WAVE_MAX = 3
_ME_VOL_MIN = 0
_ME_VOL_MAX = 7
_ME_SPEED_MIN = 1
_ME_SPEED_MAX = 30          # steps/slots per second (kid-sane upper bound)
_ME_STEPS_MAX = 32          # most steps a single SFX may hold
_ME_PATTERN_MAX = 32        # most slots a music track may hold
_ME_BANK_MAX = 64           # most SFX/tracks duplicate_sfx/duplicate_track may grow the bank to
_ME_UNDO_MAX = 30           # bounded in-editor undo/redo depth (#92)


def _me_clamp(v, lo, hi):
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


class MusicEditor:
    """Tracker/step-editor state over a cart's AudioBank (#50) -- the sound analogue
    of MapEditor/PaintEditor. Pure logic: no canvas, no synth, no I/O, so the *same*
    file backs the host console and the frozen device console. The console wraps it
    with rendering + input + live preview (it drives the injected AudioEngine; this
    core never makes sound itself).

    It edits the bank IN PLACE through two views the kid flips between:

      view == "sfx"  -- a column of STEPS for one SFX. Each step is [pitch, wave,
                        vol]; the cursor picks a step, and nudge_pitch/cycle_wave/
                        nudge_vol/toggle_rest edit it. select_sfx walks the bank's
                        SFX list (creating a fresh empty SFX past the end so the kid
                        can author new effects), and nudge_speed sets playback speed.

      view == "song" -- the looping PHRASE: a row of SLOTS, each an SFX id. The
                        cursor picks a slot; set_slot / nudge_slot point it at an SFX,
                        add_slot/del_slot grow/shrink the phrase, nudge_speed sets the
                        phrase tempo. select_track walks the bank's music tracks.

    The bank is guaranteed non-empty on construction (a default SFX + track are
    created if missing) so the grid is never blank. `dirty` tracks unsaved edits so
    the console can show a `*` and SAVE; the bank's own to_dict drives sounds.json.

    #92 adds copy/paste, whole-object duplicate, step/slot reorder, and a bounded
    in-editor undo/redo, all pure in-RAM state (no OS clipboard, no disk journal --
    that's moy_carts' separate durable per-project journal for code):

      copy/paste -- `copy()` snapshots the item under the cursor (a step in the sfx
                    view, a slot's SFX id in the song view) into `self._clip`;
                    `paste()` writes it back at the (possibly moved) cursor. A
                    mismatched clipboard kind for the active view is a no-op.

      duplicate  -- `duplicate_sfx`/`duplicate_track` deep-copy the WHOLE active
                    object into a fresh bank slot APPENDED at the end and select
                    the copy -- appended because bank indices are cross-referenced
                    ids (song slots + cart sfx(n)/music(n) calls), so a mid-bank
                    insert would retarget every later reference (the per-item
                    "duplicate" -- a new step/slot seeded from the current one --
                    already exists as add_step/add_slot).

      reorder    -- `move_step`/`move_slot` swap the cursor item with its neighbor
                    (d = +-1) and move the cursor along with it; a no-op at either
                    edge (mirrors add/del's "keep at least one" edge behavior).

      undo/redo  -- a bounded stack (depth _ME_UNDO_MAX) of snapshots of just the
                    ACTIVE object's editable fields (steps/pattern + speed + loop),
                    pushed before every content-mutating call. Switching which
                    SFX/track is active, or duplicating one, is navigation, not a
                    tracked edit -- undo only walks back through what was DONE to
                    the object currently open, restoring the object + cursor + view
                    the edit happened in (so an undo after `select_sfx` still finds
                    its way back). A fresh edit after an undo drops the redo tail,
                    same rule as moy_carts' durable journal."""

    SFX_VIEW = "sfx"
    SONG_VIEW = "song"

    def __init__(self, bank, sfx_factory=None, track_factory=None):
        # `bank` is an audio.AudioBank. The factories build a fresh SFX / MusicTrack
        # WITHOUT importing audio here (dependency-free): the console passes them, or
        # we fall back to cloning the type of an existing entry. They take no args and
        # return an empty-ish SFX / MusicTrack.
        self.bank = bank
        self._sfx_factory = sfx_factory
        self._track_factory = track_factory
        self.view = self.SFX_VIEW
        self.sfx_idx = 0          # which SFX is being edited (sfx view)
        self.step = 0             # selected step within that SFX (sfx view)
        self.track_idx = 0        # which music track is being edited (song view)
        self.slot = 0             # selected slot within that track (song view)
        self.dirty = False
        self._clip = None         # internal clipboard: ("step", [p, w, v]) | ("slot", id)
        # bounded snapshot stacks (#92) over the shared discipline -- see docstring
        self._hist = UndoStack(_ME_UNDO_MAX)
        self._ensure_nonempty()

    # `_undo`/`_redo` proxy the shared stack's lists (read-only; tests inspect them).
    @property
    def _undo(self):
        return self._hist.undo

    @property
    def _redo(self):
        return self._hist.redo

    # -- bank bootstrapping --------------------------------------------------
    def _new_sfx(self):
        """A fresh, single-rest SFX (so a new effect has one editable step)."""
        if self._sfx_factory is not None:
            s = self._sfx_factory()
        elif self.bank.sfx:
            s = type(self.bank.sfx[0])()      # clone the concrete SFX class, empty
        else:
            return None
        if not s.steps:
            s.steps = [[_ME_REST, _ME_WAVE_MIN, _ME_VOL_MAX - 1]]
        return s

    def _new_track(self):
        """A fresh music track with one slot pointing at SFX 0."""
        if self._track_factory is not None:
            t = self._track_factory()
        elif self.bank.music:
            t = type(self.bank.music[0])()
        else:
            return None
        if not t.pattern:
            t.pattern = [0]
        return t

    def _ensure_nonempty(self):
        """The editor must never face an empty bank/SFX/track -- seed minimal ones."""
        if not self.bank.sfx:
            s = self._new_sfx()
            if s is not None:
                self.bank.sfx.append(s)
        if not self.bank.music:
            t = self._new_track()
            if t is not None:
                self.bank.music.append(t)
        self._clamp()
        # A loaded SFX could carry zero steps; give the cursor a real step to land on.
        s = self.cur_sfx()
        if s is not None and not s.steps:
            s.steps.append([_ME_REST, _ME_WAVE_MIN, _ME_VOL_MAX - 1])

    # -- current selection ---------------------------------------------------
    def cur_sfx(self):
        if 0 <= self.sfx_idx < len(self.bank.sfx):
            return self.bank.sfx[self.sfx_idx]
        return None

    def cur_track(self):
        if 0 <= self.track_idx < len(self.bank.music):
            return self.bank.music[self.track_idx]
        return None

    def cur_step(self):
        """The [pitch, wave, vol] list under the cursor in the sfx view, or None."""
        s = self.cur_sfx()
        if s is not None and 0 <= self.step < len(s.steps):
            return s.steps[self.step]
        return None

    def step_count(self):
        s = self.cur_sfx()
        return len(s.steps) if s is not None else 0

    def slot_count(self):
        t = self.cur_track()
        return len(t.pattern) if t is not None else 0

    def _clamp(self):
        self.sfx_idx = _me_clamp(self.sfx_idx, 0, max(0, len(self.bank.sfx) - 1))
        self.track_idx = _me_clamp(self.track_idx, 0, max(0, len(self.bank.music) - 1))
        n = self.step_count()
        self.step = _me_clamp(self.step, 0, max(0, n - 1)) if n else 0
        m = self.slot_count()
        self.slot = _me_clamp(self.slot, 0, max(0, m - 1)) if m else 0

    # -- view + cursor -------------------------------------------------------
    def toggle_view(self):
        """Flip between the SFX step grid and the SONG phrase."""
        self.view = self.SONG_VIEW if self.view == self.SFX_VIEW else self.SFX_VIEW

    def select_cursor(self, i):
        """Place the cursor on step / slot index `i` (clamped) for the active view."""
        if self.view == self.SFX_VIEW:
            n = self.step_count()
            if n:
                self.step = _me_clamp(int(i), 0, n - 1)
        else:
            m = self.slot_count()
            if m:
                self.slot = _me_clamp(int(i), 0, m - 1)

    def move_cursor(self, d):
        """Move the step/slot cursor by d (honors magnitude, clamped to the ends)."""
        if self.view == self.SFX_VIEW:
            self.select_cursor(self.step + d)
        else:
            self.select_cursor(self.slot + d)

    # -- SFX selection -------------------------------------------------------
    def select_sfx(self, d):
        """Step the edited-SFX index by d. Walking PAST the last SFX appends a fresh
        empty one (so the kid grows the bank just by pressing >), then clamps. Going
        before 0 clamps at 0. Resets the step cursor to the start of the new SFX."""
        target = self.sfx_idx + d
        if target >= len(self.bank.sfx):
            s = self._new_sfx()
            if s is not None:
                self.bank.sfx.append(s)
                self.dirty = True
        self.sfx_idx = _me_clamp(target, 0, max(0, len(self.bank.sfx) - 1))
        self.step = 0
        self._clamp()

    def select_track(self, d):
        """Step the edited-track index by d; past the end appends a fresh track."""
        target = self.track_idx + d
        if target >= len(self.bank.music):
            t = self._new_track()
            if t is not None:
                self.bank.music.append(t)
                self.dirty = True
        self.track_idx = _me_clamp(target, 0, max(0, len(self.bank.music) - 1))
        self.slot = 0
        self._clamp()

    # -- SFX step edits ------------------------------------------------------
    def nudge_pitch(self, d):
        """Raise/lower the current step's pitch by d semitones. A rest stays a rest
        until toggle_rest gives it a real pitch (so nudging a rest is a no-op)."""
        st = self.cur_step()
        if st is None or st[0] < 0:
            return
        self._push_undo()
        st[0] = _me_clamp(st[0] + d, _ME_PITCH_MIN, _ME_PITCH_MAX)
        self.dirty = True

    def set_pitch(self, pitch):
        """Set the current step to an explicit pitch (a semitone index, or <0 rest)."""
        st = self.cur_step()
        if st is None:
            return
        self._push_undo()
        st[0] = _ME_REST if pitch < 0 else _me_clamp(int(pitch), _ME_PITCH_MIN, _ME_PITCH_MAX)
        self.dirty = True

    def toggle_rest(self, default_pitch=57):
        """Toggle the current step between a rest and a real note. Leaving a rest
        restores `default_pitch` (A4=57 by default) so the kid hears something."""
        st = self.cur_step()
        if st is None:
            return
        self._push_undo()
        if st[0] < 0:
            st[0] = _me_clamp(int(default_pitch), _ME_PITCH_MIN, _ME_PITCH_MAX)
        else:
            st[0] = _ME_REST
        self.dirty = True

    def cycle_wave(self, d=1):
        """Step the current step's waveform (square/triangle/saw/noise), wrapping."""
        st = self.cur_step()
        if st is None:
            return
        self._push_undo()
        span = _ME_WAVE_MAX - _ME_WAVE_MIN + 1
        st[1] = _ME_WAVE_MIN + (st[1] - _ME_WAVE_MIN + d) % span
        self.dirty = True

    def nudge_vol(self, d):
        """Raise/lower the current step's volume (0=silent .. 7=loud), clamped."""
        st = self.cur_step()
        if st is None:
            return
        self._push_undo()
        st[2] = _me_clamp(st[2] + d, _ME_VOL_MIN, _ME_VOL_MAX)
        self.dirty = True

    def cycle_vol(self, d=1):
        """Step the current step's volume with wraparound (7 -> 0), so a single
        tap-only button can cycle through every level (touch UI convenience)."""
        st = self.cur_step()
        if st is None:
            return
        self._push_undo()
        span = _ME_VOL_MAX - _ME_VOL_MIN + 1
        st[2] = _ME_VOL_MIN + (st[2] - _ME_VOL_MIN + d) % span
        self.dirty = True

    def add_step(self):
        """Append a step (a copy of the current one, or a default) to the current SFX
        and move the cursor to it. Capped at _ME_STEPS_MAX."""
        s = self.cur_sfx()
        if s is None or len(s.steps) >= _ME_STEPS_MAX:
            return
        self._push_undo()
        src = self.cur_step()
        new = list(src) if src is not None else [_ME_REST, _ME_WAVE_MIN, _ME_VOL_MAX - 1]
        s.steps.insert(self.step + 1, new)
        self.step += 1
        self.dirty = True

    def del_step(self):
        """Remove the current step (keeps at least one step so the grid never empties)."""
        s = self.cur_sfx()
        if s is None or len(s.steps) <= 1:
            return
        self._push_undo()
        del s.steps[self.step]
        if self.step >= len(s.steps):
            self.step = len(s.steps) - 1
        self.dirty = True

    def move_step(self, d):
        """Reorder (#92): swap the current step with its neighbor d away (+1 right/
        later, -1 left/earlier) and move the cursor along with it. A no-op past
        either edge, mirroring add/del's "keep at least one" boundary style."""
        s = self.cur_sfx()
        if s is None:
            return
        j = self.step + d
        if not (0 <= j < len(s.steps)):
            return
        self._push_undo()
        s.steps[self.step], s.steps[j] = s.steps[j], s.steps[self.step]
        self.step = j
        self.dirty = True

    # -- tempo / length ------------------------------------------------------
    def nudge_speed(self, d):
        """Change the playback speed of the ACTIVE object: the current SFX in the sfx
        view (steps/sec), the current track in the song view (slots/sec). Clamped."""
        obj = self.cur_sfx() if self.view == self.SFX_VIEW else self.cur_track()
        if obj is None:
            return
        self._push_undo()
        obj.speed = _me_clamp(obj.speed + d, _ME_SPEED_MIN, _ME_SPEED_MAX)
        self.dirty = True

    def toggle_loop(self):
        """Flip the loop flag of the active object (SFX in sfx view, track in song)."""
        obj = self.cur_sfx() if self.view == self.SFX_VIEW else self.cur_track()
        if obj is None:
            return
        self._push_undo()
        obj.loop = not obj.loop
        self.dirty = True

    # -- song (phrase) edits -------------------------------------------------
    def cur_slot_value(self):
        """The SFX id at the cursor slot in the song view, or None."""
        t = self.cur_track()
        if t is not None and 0 <= self.slot < len(t.pattern):
            return t.pattern[self.slot]
        return None

    def nudge_slot(self, d):
        """Point the current phrase slot at the next/previous SFX id, clamped to the
        bank's SFX range (you can only sequence effects that exist)."""
        t = self.cur_track()
        if t is None or not (0 <= self.slot < len(t.pattern)):
            return
        self._push_undo()
        hi = max(0, len(self.bank.sfx) - 1)
        t.pattern[self.slot] = _me_clamp(t.pattern[self.slot] + d, 0, hi)
        self.dirty = True

    def set_slot(self, sfx_id):
        """Set the current phrase slot to a specific SFX id (clamped to the bank)."""
        t = self.cur_track()
        if t is None or not (0 <= self.slot < len(t.pattern)):
            return
        self._push_undo()
        hi = max(0, len(self.bank.sfx) - 1)
        t.pattern[self.slot] = _me_clamp(int(sfx_id), 0, hi)
        self.dirty = True

    def add_slot(self):
        """Append a phrase slot (copying the current slot's SFX id) and move to it."""
        t = self.cur_track()
        if t is None or len(t.pattern) >= _ME_PATTERN_MAX:
            return
        self._push_undo()
        val = t.pattern[self.slot] if 0 <= self.slot < len(t.pattern) else 0
        t.pattern.insert(self.slot + 1, val)
        self.slot += 1
        self.dirty = True

    def del_slot(self):
        """Remove the current phrase slot (keeps at least one so a track always plays)."""
        t = self.cur_track()
        if t is None or len(t.pattern) <= 1:
            return
        self._push_undo()
        del t.pattern[self.slot]
        if self.slot >= len(t.pattern):
            self.slot = len(t.pattern) - 1
        self.dirty = True

    def move_slot(self, d):
        """Reorder (#92): swap the current phrase slot with its neighbor d away
        (+1 later, -1 earlier), moving the cursor along. Mirrors move_step."""
        t = self.cur_track()
        if t is None:
            return
        j = self.slot + d
        if not (0 <= j < len(t.pattern)):
            return
        self._push_undo()
        t.pattern[self.slot], t.pattern[j] = t.pattern[j], t.pattern[self.slot]
        self.slot = j
        self.dirty = True

    # -- copy / paste (#92) ---------------------------------------------------
    def copy(self):
        """Copy the item under the cursor to the internal clipboard: the current
        step (a [pitch, wave, vol] list) in the sfx view, the current slot's SFX id
        in the song view. In-RAM only, device-identical -- no OS clipboard."""
        if self.view == self.SFX_VIEW:
            st = self.cur_step()
            if st is not None:
                self._clip = ("step", list(st))
        else:
            v = self.cur_slot_value()
            if v is not None:
                self._clip = ("slot", v)

    def paste(self):
        """Paste the clipboard over the item under the cursor. A no-op if nothing
        was copied yet, or the clipboard holds the other view's kind of item
        (a copied step can't paste into a song slot and vice versa)."""
        if self._clip is None:
            return
        kind, val = self._clip
        if self.view == self.SFX_VIEW and kind == "step":
            st = self.cur_step()
            if st is None:
                return
            self._push_undo()
            st[0], st[1], st[2] = val
            self.dirty = True
        elif self.view == self.SONG_VIEW and kind == "slot":
            t = self.cur_track()
            if t is None or not (0 <= self.slot < len(t.pattern)):
                return
            self._push_undo()
            hi = max(0, len(self.bank.sfx) - 1)
            t.pattern[self.slot] = _me_clamp(int(val), 0, hi)
            self.dirty = True

    # -- whole-object duplicate (#92) ------------------------------------------
    def duplicate_sfx(self):
        """Duplicate the WHOLE current SFX (steps/speed/loop) into a fresh bank
        slot APPENDED at the end, and select the copy. Appended -- never inserted
        mid-bank -- because a bank index is a cross-referenced id: song pattern
        slots store raw SFX indices and cart code calls sfx(n) by the same
        integer, so an insert would silently retarget every later reference
        (every other grow path here appends for the same reason). The per-item
        duplicate (a new step seeded from the current one) already exists as
        add_step; this is the bank-level "clone this effect"."""
        s = self.cur_sfx()
        if s is None or len(self.bank.sfx) >= _ME_BANK_MAX:
            return
        dup = self._new_sfx()
        if dup is None:
            return
        dup.steps = [list(st) for st in s.steps]
        dup.speed = s.speed
        dup.loop = s.loop
        self.bank.sfx.append(dup)
        self.sfx_idx = len(self.bank.sfx) - 1
        self.step = 0
        self.dirty = True
        self._clamp()

    def duplicate_track(self):
        """Duplicate the WHOLE current SONG (track: pattern/speed/loop) into a
        fresh bank slot APPENDED at the end, and select the copy. Mirrors
        duplicate_sfx (same append-only rule: music(n) calls index the bank)."""
        t = self.cur_track()
        if t is None or len(self.bank.music) >= _ME_BANK_MAX:
            return
        dup = self._new_track()
        if dup is None:
            return
        dup.pattern = list(t.pattern)
        dup.speed = t.speed
        dup.loop = t.loop
        self.bank.music.append(dup)
        self.track_idx = len(self.bank.music) - 1
        self.slot = 0
        self.dirty = True
        self._clamp()

    # -- bounded in-editor undo/redo (#92) -------------------------------------
    # Scoped to the ACTIVE object's own editable fields (steps/pattern + speed +
    # loop) -- a snapshot per content edit, depth _ME_UNDO_MAX. Navigation (which
    # SFX/track is selected) and whole-object duplicate are NOT tracked; only what
    # was actually done to an object is undoable, and undoing restores the view +
    # object + cursor the edit happened in.
    def _snapshot(self):
        """Capture the active object's mutable fields + the cursor pointing at it,
        or None if there is nothing to snapshot (an empty bank)."""
        if self.view == self.SFX_VIEW:
            obj = self.cur_sfx()
            if obj is None:
                return None
            return ("sfx", self.sfx_idx, [list(st) for st in obj.steps],
                    obj.speed, obj.loop, self.step)
        obj = self.cur_track()
        if obj is None:
            return None
        return ("song", self.track_idx, list(obj.pattern),
                obj.speed, obj.loop, self.slot)

    def _snapshot_of(self, snap):
        """Snapshot the CURRENT state of the object a popped undo/redo entry names
        (its kind + bank idx) -- NOT whatever object the editor happens to be
        showing. undo/redo push this onto the opposite stack, so the pair is
        always a true before/after of the SAME object even when the kid walked
        the selection elsewhere between the edit and the undo. The cursor
        recorded is the live one when that object is still active, else the
        popped entry's (best available -- _restore clamps it anyway)."""
        kind, idx, _data, _speed, _loop, cursor = snap
        if kind == "sfx":
            if not (0 <= idx < len(self.bank.sfx)):
                return None
            obj = self.bank.sfx[idx]
            if self.view == self.SFX_VIEW and self.sfx_idx == idx:
                cursor = self.step
            return ("sfx", idx, [list(st) for st in obj.steps],
                    obj.speed, obj.loop, cursor)
        if not (0 <= idx < len(self.bank.music)):
            return None
        obj = self.bank.music[idx]
        if self.view == self.SONG_VIEW and self.track_idx == idx:
            cursor = self.slot
        return ("song", idx, list(obj.pattern), obj.speed, obj.loop, cursor)

    def _restore(self, snap):
        """Write a _snapshot() tuple back over the bank + re-point the cursor."""
        kind, idx, data, speed, loop, cursor = snap
        if kind == "sfx":
            if not (0 <= idx < len(self.bank.sfx)):
                return
            obj = self.bank.sfx[idx]
            obj.steps = [list(st) for st in data]
            obj.speed = speed
            obj.loop = loop
            self.view = self.SFX_VIEW
            self.sfx_idx = idx
            self.step = cursor
        else:
            if not (0 <= idx < len(self.bank.music)):
                return
            obj = self.bank.music[idx]
            obj.pattern = list(data)
            obj.speed = speed
            obj.loop = loop
            self.view = self.SONG_VIEW
            self.track_idx = idx
            self.slot = cursor
        self._clamp()

    def _push_undo(self):
        """Record the active object's pre-edit state. Called by every content-
        mutating method BEFORE it changes anything. A fresh edit always drops the
        redo tail (same rule as moy_carts' durable journal)."""
        snap = self._snapshot()
        if snap is None:
            return
        self._hist.push(snap)

    def can_undo(self):
        return self._hist.can_undo()

    def can_redo(self):
        return self._hist.can_redo()

    def undo(self):
        """Step back one content edit (a no-op at the floor). The reverse pushed to
        redo is a fresh _snapshot_of the POPPED entry's object (pre-restore) -- so
        the pair is a true before/after even if the selection walked elsewhere; a
        stale object (None) simply isn't stashed."""
        snap = self._hist.take_undo(self._snapshot_of)
        if snap is None:
            return
        self._restore(snap)
        self.dirty = True

    def redo(self):
        """Re-apply the next edit undo() walked back past (a no-op at the top)."""
        snap = self._hist.take_redo(self._snapshot_of)
        if snap is None:
            return
        self._restore(snap)
        self.dirty = True
