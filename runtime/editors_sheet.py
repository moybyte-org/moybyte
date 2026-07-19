"""The indexed sprite/tile asset models, split out of editors.py (which
re-exports them): _SheetSprite (the blittable view), SpriteSheet (8x8 tile
sheet + PICO-8 __gfx__-style hex, #4 storage), IconSheet (16x16 tiles -- the
editable top-bar icon theme), TileMap (grid of tile ids + map.moymap hex,
#32 storage). Pure logic, dependency-free."""


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
