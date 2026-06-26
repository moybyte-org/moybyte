"""Backend-agnostic editor cores shared by the host and device consoles.

These classes are pure logic -- no canvas, framebuf, input, or I/O -- so the
*same* file backs both the host reference (`runtime/`, imported as
`runtime.editors`) and the MicroPython device port (`kid_runtime.py`, which
imports it as the frozen top-level module `editors`). The build stages this file
into the firmware `modules/` tree; the host imports it directly. Each side adds
only its own rendering + input glue around these.

  CodeEditor   -- editable text buffer + cursor (the on-device code editor, #3)
  SpriteSheet  -- indexed 8x8 tile sheet + PICO-8 __gfx__-style hex (#4 storage)
  PaintEditor  -- pixel-paint state over a sheet tile (#4 editor)
  TileMap      -- grid of tile ids over a sheet + map.kmap hex (#32 storage)
  MapEditor    -- tile-placement state over a TileMap (#32 editor)
  BlockEditor  -- structured-outline block program + cursor (#29 Part 2 editor)

Keep this module dependency-free so it freezes cleanly and imports under both
CPython (tests/host) and MicroPython (device).
"""


class CodeEditor:
    """Editable text buffer for a cart's main.py: a list of lines plus a
    (row, col) cursor. The shell feeds it keyboard ASCII (key) and tap-to-place
    coordinates (place), and renders COLS x ROWS of it."""

    COLS = 38          # visible columns (8px font across the full 320px screen)
    ROWS = 20          # visible lines (full-screen code editor)

    def __init__(self, src=""):
        self.set_text(src)

    def set_text(self, src):
        self.lines = str(src).split("\n")
        if not self.lines:
            self.lines = [""]
        self.row = 0
        self.col = 0
        self.top = 0          # first visible line
        self.left = 0         # first visible column (horizontal scroll)
        self.dirty = False

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

    def move(self, dr, dc):
        # Move the caret by dr rows then dc columns (both honor magnitude, so a
        # multi-pulse trackball roll moves that many cells). Columns wrap across
        # line ends like a real caret.
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
        ln = self.lines[self.row]
        self.lines[self.row] = ln[:self.col] + ch + ln[self.col:]
        self.col += 1
        self.dirty = True
        self._scroll()                       # keep the caret on screen while typing

    def newline(self):
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
        elif code == 0x09:                   # tab -> two spaces
            self.insert(" ")
            self.insert(" ")
        elif 0x20 <= code <= 0x7E:           # printable ASCII
            self.insert(chr(code))
        else:
            return False
        return True

    def place(self, col, row):
        """Place the cursor at a visible (col, row-from-top) cell -- for tap."""
        self.row = max(0, min(len(self.lines) - 1, self.top + row))
        self.col = max(0, min(len(self.lines[self.row]), self.left + col))
        self._scroll()

    def visible_lines(self):
        return self.lines[self.top:min(len(self.lines), self.top + self.ROWS)]


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
    __gfx__-style hex blob (one nibble per pixel) stored as `sprites.kgfx`."""

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
        """Build an 8x8 blittable of sprite n for either backend's Canvas.spr."""
        if n < 0 or n >= self.count:
            return None
        ox, oy = self.tile_origin(n)
        w = self.w
        pix = []
        for ly in range(self.TILE):
            base = (oy + ly) * w + ox
            for lx in range(self.TILE):
                pix.append(self.pix[base + lx])
        return _SheetSprite(self.TILE, self.TILE, pix, transparent)

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


class TileMap:
    """A w x h grid of tile ids laid over a SpriteSheet -- the data a native
    map() blit walks (#32). Each cell holds a sprite id (0..count-1) or -1 for
    "empty" (no tile drawn there). Backend-agnostic like SpriteSheet, so the host
    Canvas.map and the device DeviceCanvas.map both consume the same grid.

    Storage is a flat bytearray of w*h cells where each byte is `tile_id + 1`
    (so 0 means empty); this keeps the on-disk blob compact and an all-zero map
    is genuinely blank. Serializes to a `map.kmap` text blob: a header line
    `w h` followed by `h` rows of `w * 2` hex digits (one byte per cell, "00"
    = empty), mirroring the PICO-8 __gfx__-style sprites.kgfx pattern. Tile ids
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
        """Parse a map.kmap blob (header `w h` + rows of hex byte pairs). Falls
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

    def __init__(self, sheet):
        self.sheet = sheet
        self.n = 0            # current sprite id (top-left tile of the sprite)
        self.color = 8        # current paint color (red, a friendly default)
        self.size = 1         # sprite side length in tiles (1=8x8, 2=16x16, ...)

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


class MapEditor:
    """Tile-placement state over a TileMap + its SpriteSheet -- the map analogue
    of PaintEditor (#32). PaintEditor places palette indices onto a sprite tile;
    this places sprite ids onto map cells. Pure logic: the shell maps taps on the
    visible map region / tile palette to these calls and renders the result.

    Holds the current tile id to stamp + a (cam_x, cam_y) view offset in cells, so
    a map larger than the on-screen window can be panned. `place` stamps the
    current tile, `erase` clears a cell, `pick` samples the cell under a tap into
    the brush, and `select(d)` steps the brush through the sheet's tile ids."""

    def __init__(self, tilemap, sheet):
        self.tilemap = tilemap
        self.sheet = sheet
        self.n = 0            # current tile id to stamp (a sprite id in the sheet)
        self.cam_x = 0        # top-left visible cell (pan offset), in cells
        self.cam_y = 0

    def place(self, cell_x, cell_y):
        """Stamp the current tile id at map cell (cell_x, cell_y)."""
        self.tilemap.mset(cell_x, cell_y, self.n)

    def erase(self, cell_x, cell_y):
        """Clear map cell (cell_x, cell_y) to empty (no tile)."""
        self.tilemap.mset(cell_x, cell_y, self.tilemap.EMPTY)

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
        self.reflow()

    # -- flattening ----------------------------------------------------------
    def reflow(self):
        """Rebuild the flat row list from the tree, then clamp the cursor. Called
        after every structural edit so rows/cursor stay in sync with the program."""
        rows = []
        scripts = self.program.get("scripts", []) or []
        for si in range(len(scripts)):
            self._flatten_block(rows, scripts, si, 0)
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
        if self.blocks.is_cblock(tid) or self._is_hat(tid):
            # A block with a body (hat / c-block) shows its children indented, with
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
            # that silently changes branches. The kid moves it explicitly instead.
            return False
        siblings[i], siblings[j] = siblings[j], siblings[i]
        self.dirty = True
        self.reflow()
        self._select_block(siblings[j])
        return True

    # -- slot editing --------------------------------------------------------
    def slots(self, block=None):
        """The catalog slot descriptors for a block (defaults to the selection).
        Empty list for an unknown/None block."""
        b = block if block is not None else self.selected_block()
        if b is None:
            return []
        d = self.blocks.block_def(b.get("t"))
        return list(d["slots"]) if d else []

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
                self.set_slot(slot_name, val, b)
                return val
        return None

    # -- variables -----------------------------------------------------------
    def add_var(self, name):
        """Declare a new variable (so variable slots can reference it). De-duplicates
        and ignores blanks. Returns the variable list."""
        name = str(name).strip()
        vars_ = self.program.setdefault("vars", [])
        if name and name not in vars_:
            vars_.append(name)
            self.dirty = True
        return vars_

    def variables(self):
        return list(self.program.get("vars", []) or [])

    # -- helpers -------------------------------------------------------------
    def _select_block(self, blk):
        """Park the cursor on the row that holds `blk` after a reflow (so an insert/
        move leaves the new/moved block selected)."""
        for i in range(len(self.rows)):
            r = self.rows[i]
            if r.kind == "block" and r.block is blk:
                self.cur = i
                return
