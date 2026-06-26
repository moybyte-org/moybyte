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
    """Pixel-paint state over a SpriteSheet tile: current sprite + paint color.
    The shell maps taps on the zoomed grid/palette to these calls."""

    def __init__(self, sheet):
        self.sheet = sheet
        self.n = 0            # current sprite id
        self.color = 8        # current paint color (red, a friendly default)

    def paint(self, lx, ly):
        self.sheet.tset(self.n, lx, ly, self.color)

    def pick(self, lx, ly):
        self.color = self.sheet.tget(self.n, lx, ly)

    def select(self, d):
        self.n = (self.n + d) % self.sheet.count


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
