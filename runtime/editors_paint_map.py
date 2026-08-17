"""PaintEditor (#4) + MapEditor (#32) -- pixel-paint state over a sheet tile
and tile-placement state over a TileMap. Split out of editors.py (which
re-exports them).

Undo/redo runs on the shared #111 op-history core (runtime/op_history.py): each
editor keeps ONE `History` per open sheet/map over a small OpCodec, replacing the
per-editor UndoStack rings. The codecs record JSON-able ops (flat ints + hex
blobs -- MicroPython-safe, frozen on device) that INVERT in place carrying their
own pre-image, so the SAME batch a stroke records in RAM is the batch a project
commit embeds in its journal line (`History.flush()`), and the #88 bar UNDO/REDO
icons walk it before falling back to the coarse whole-commit journal (#111)."""

try:
    from op_history import History, OpHistoryMixin
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.op_history import History, OpHistoryMixin

try:                                              # binascii = MicroPython + CPython
    import ubinascii as _binascii
except ImportError:  # pragma: no cover
    import binascii as _binascii


def _b2hex(blob):
    """Bytes -> lowercase hex string (JSON-able map snapshot blob, #111)."""
    return _binascii.hexlify(bytes(blob)).decode()


def _hex2b(text):
    """Hex string -> bytes (the inverse of _b2hex, for a snapshot restore)."""
    return _binascii.unhexlify(text)


# -- shape rasterizers (#90) -------------------------------------------------
# Integer-only point generators for the RECT / LINE / OVAL tools: a shape is
# defined by its two drag endpoints and rasterized to a list of (x, y) cells the
# editor stamps in one undo step (and the layer previews live during the drag).
# Integer-only (no float / math import) so host CPython and the frozen device
# MicroPython produce byte-identical pixels; unclamped here (PaintEditor clips to
# the editable region).


def _pe_line(x0, y0, x1, y1):
    """Bresenham line points from (x0,y0) to (x1,y1) inclusive (mirrors the layer's
    drag-stroke _line_cells; kept in the core so shapes stay dependency-free)."""
    pts = []
    dx = x1 - x0 if x1 >= x0 else x0 - x1
    dy = y1 - y0 if y1 >= y0 else y0 - y1
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    x, y = x0, y0
    while True:
        pts.append((x, y))
        if x == x1 and y == y1:
            break
        e2 = err + err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy
    return pts


def _pe_rect(x0, y0, x1, y1):
    """The HOLLOW rectangle outline spanned by corners (x0,y0)..(x1,y1) inclusive
    (drag in any direction). Kids bucket-fill inside for a solid box -- an outline
    keeps the tool count down (one RECT mode, no fill variant)."""
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    pts = []
    for x in range(x0, x1 + 1):
        pts.append((x, y0))
        if y1 != y0:
            pts.append((x, y1))
    for y in range(y0 + 1, y1):
        pts.append((x0, y))
        if x1 != x0:
            pts.append((x1, y))
    return pts


def _pe_oval(x0, y0, x1, y1):
    """The HOLLOW ellipse inscribed in the bounding box (x0,y0)..(x1,y1) -- a circle
    when the box is square. Integer midpoint-ellipse over an integer centre/radii
    derived from the box (an even-sized box rounds the radius down by a pixel), so
    it needs no float. Degenerates to a line for a zero radius."""
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    rx = (x1 - x0) // 2
    ry = (y1 - y0) // 2
    if rx == 0 or ry == 0:
        return _pe_line(x0, y0, x1, y1)
    cx = x0 + rx
    cy = y0 + ry
    pts = []

    def plot(px, py):
        pts.append((cx + px, cy + py))
        pts.append((cx - px, cy + py))
        pts.append((cx + px, cy - py))
        pts.append((cx - px, cy - py))

    rx2 = rx * rx
    ry2 = ry * ry
    x = 0
    y = ry
    px = 0
    py = 2 * rx2 * y
    plot(x, y)
    p = ry2 - rx2 * ry + (rx2 + 2) // 4          # region 1 decision (~0.25*rx2)
    while px < py:
        x += 1
        px += 2 * ry2
        if p < 0:
            p += ry2 + px
        else:
            y -= 1
            py -= 2 * rx2
            p += ry2 + px - py
        plot(x, y)
    p = (ry2 * (2 * x + 1) * (2 * x + 1)) // 4 + rx2 * (y - 1) * (y - 1) - rx2 * ry2
    while y > 0:
        y -= 1
        py -= 2 * rx2
        if p > 0:
            p += rx2 - py
        else:
            x += 1
            px += 2 * ry2
            p += rx2 - py + px
        plot(x, y)
    return pts


class _PaintOps:
    """OpCodec for PaintEditor (#111): each op is a SPARSE pre/post pixel diff of
    one edited region, `["p", n, size, spans]` where `spans` is a flat int list
    `[idx, pre, post, idx, pre, post, ...]` (region-local pixel index + its old and
    new palette values). JSON-able by construction, so the same op undoes in RAM
    and replays out of a journal line unchanged. INVERT-only (undo writes `pre`,
    redo writes `post`); the doc is the PaintEditor -- the codec re-selects the
    op's sprite/size (so the revert is visible) and psets through the sheet exactly
    like a paint edit, bumping gen/dirty for a running cart preview."""

    def apply(self, doc, op):          # redo -> lay the POST pixels
        self._write(doc, op, 2)

    def invert(self, doc, op):         # undo -> lay the PRE pixels
        self._write(doc, op, 1)

    def _write(self, doc, op, which):
        n, size, spans = int(op[1]), int(op[2]), op[3]
        doc.n = n
        doc.size = size
        doc._clamp_size()
        sh = doc.sheet
        dim = size * sh.TILE            # the STORED size (the region fit when recorded)
        ox, oy = sh.tile_origin(n)
        i = 0
        L = len(spans)
        while i < L:
            idx = spans[i]
            sh.pset(ox + (idx % dim), oy + (idx // dim), spans[i + which])
            i += 3


class PaintEditor(OpHistoryMixin):
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
    RECT = "rect"         # shape tool: drag two corners -> hollow rectangle (#90)
    LINE = "line"         # shape tool: drag endpoints -> straight line (#90)
    OVAL = "oval"         # shape tool: drag a bbox -> hollow ellipse/circle (#90)
    SELECT = "select"     # region tool: drag a box -> selection; tap stamps the clip (#90)
    TOOLS = (PEN, FILL, RECT, LINE, OVAL, SELECT)

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
        self.tool = self.PEN  # active tool (PEN / FILL / RECT / LINE / OVAL / SELECT, #90)
        self.erase = False    # color-erase toggle (#90): paint/fill/shapes write index 0
        # Region select / copy / paste (#90). `sel` is the active grid-local selection
        # rectangle (x0,y0,x1,y1 inclusive) or None; `clip` is the copied region as
        # (w, h, bytes) or None. Both default off so a freshly-opened editor renders
        # byte-identically to before (the #39 parity contract).
        self.sel = None
        self.clip = None
        # In-editor undo/redo (#90/#111): ONE op-history over the paint OpCodec,
        # bounded to UNDO_DEPTH RAM steps (paint edits are far too frequent to
        # journal to SD per pixel -- the durable journal fires on a commit,
        # project.py, which also drains this History's op batch into its line). Each
        # op is a sparse (n, size, changed-pixel) diff, so undo/redo target the
        # right tiles even after the sprite/size was changed, and the batch survives
        # the JSON round-trip through the journal unchanged.
        self._hist = History(self, _PaintOps(), max_undo=self.UNDO_DEPTH)
        self._stroke_pre = None   # pending pre-stroke snapshot (set on press, #90)

    @property
    def dim(self):
        """Editable region side length in pixels (size * 8)."""
        return self.size * self.sheet.TILE

    def _origin(self):
        return self.sheet.tile_origin(self.n)

    def _ink(self):
        """The palette index the current op writes: the erase toggle forces index 0
        (the transparent/erase color), else the selected paint color (#90)."""
        return 0 if self.erase else (self.color & 15)

    def paint(self, lx, ly):
        """Paint grid-local pixel (lx, ly) within the size*8 region at tile n."""
        if 0 <= lx < self.dim and 0 <= ly < self.dim:
            ox, oy = self._origin()
            self.sheet.pset(ox + lx, oy + ly, self._ink())

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
        """Flip between the PEN brush and the FILL bucket (legacy one-button toggle;
        the tool row now selects modes directly via set_tool -- kept for API stability)."""
        self.tool = self.PEN if self.tool == self.FILL else self.FILL

    def set_tool(self, tool):
        """Select the active drawing tool (PEN / FILL / RECT / LINE / OVAL / SELECT).
        Direct mode buttons on the tool row call this -- touch-first, no chords (#90)."""
        if tool in self.TOOLS:
            self.tool = tool

    def toggle_erase(self):
        """Flip the color-erase toggle: when on, PEN / FILL / shapes lay index 0 (the
        transparent color) so a kid can carve holes in a sprite without hunting for the
        color-0 swatch (#90). A display flag only in spirit -- the pixels really change."""
        self.erase = not self.erase

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

    def _push_diff(self, n, size, prebytes, postbytes):
        """Record ONE undo op = the sparse pre/post pixel diff of the (n, size)
        region (#111). `spans` is a flat int list [idx, pre, post, ...] over just
        the pixels that changed, so a small stroke is a small op and the batch
        JSON-round-trips through the journal unchanged. A no-diff (nothing changed)
        records nothing -- the callers already gate on `buf != prebytes`, this is
        belt-and-braces so gen/dirty and the undo ring stay in lock-step."""
        spans = []
        ap = spans.append
        for idx in range(len(prebytes)):
            p = prebytes[idx]
            q = postbytes[idx]
            if p != q:
                ap(idx)
                ap(p)
                ap(q)
        if spans:
            self._hist.record(["p", n, size, spans])

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
            self._push_diff(pre[0], pre[1], prebytes, buf)

    # -- stroke boundaries (a drag = ONE undo step, #90) ---------------------

    def begin_stroke(self):
        """Mark the start of a brush stroke: snapshot the region ONCE (a fast drag
        calls paint() many times, but a whole press-drag-release is one undo step)."""
        if self._stroke_pre is None:
            self._stroke_pre = self._capture()

    def end_stroke(self):
        """Close a brush stroke: record its pre/post diff as ONE undo op iff the
        stroke actually changed pixels. Idempotent (safe to call every idle frame)."""
        if self._stroke_pre is not None:
            pre = self._stroke_pre
            self._stroke_pre = None
            post = self._capture(pre[0], pre[1])[2]
            if post != pre[2]:
                self._push_diff(pre[0], pre[1], pre[2], post)

    # undo/redo/can_* are OpHistoryMixin's over self._hist (#111).

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
            repl = self._ink()
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

    # -- shape tools (RECT / LINE / OVAL, #90) -------------------------------

    def shape_points(self, x0, y0, x1, y1):
        """The grid-local cells the active shape tool would draw between drag
        endpoints (x0,y0)..(x1,y1), CLIPPED to the editable region. Returns [] for a
        non-shape tool. Shared by the live drag preview (paint_layer) and stamp_shape,
        so the preview matches the committed pixels exactly."""
        t = self.tool
        if t == self.LINE:
            pts = _pe_line(x0, y0, x1, y1)
        elif t == self.RECT:
            pts = _pe_rect(x0, y0, x1, y1)
        elif t == self.OVAL:
            pts = _pe_oval(x0, y0, x1, y1)
        else:
            return []
        d = self.dim
        return [(x, y) for (x, y) in pts if 0 <= x < d and 0 <= y < d]

    def stamp_shape(self, x0, y0, x1, y1):
        """Commit the active shape (RECT / LINE / OVAL) between drag endpoints as ONE
        undo step, in the current ink (erase toggle honored). A shape wholly off-grid
        or that changes nothing records nothing (like the pen/fill paths)."""
        pts = self.shape_points(x0, y0, x1, y1)
        if not pts:
            return
        c = self._ink()

        def build(buf, dim):
            for (x, y) in pts:
                buf[y * dim + x] = c
        self._record(build)

    # -- region select / copy / paste (#90) ----------------------------------

    def set_selection(self, x0, y0, x1, y1):
        """Set the active selection rectangle (grid-local, inclusive), normalized so a
        drag in any direction works and clamped inside the editable region."""
        d = self.dim
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        x0 = 0 if x0 < 0 else (d - 1 if x0 > d - 1 else x0)
        y0 = 0 if y0 < 0 else (d - 1 if y0 > d - 1 else y0)
        x1 = 0 if x1 < 0 else (d - 1 if x1 > d - 1 else x1)
        y1 = 0 if y1 < 0 else (d - 1 if y1 > d - 1 else y1)
        self.sel = (x0, y0, x1, y1)

    def clear_selection(self):
        """Drop the active selection (the copied clip stays available for paste)."""
        self.sel = None

    @property
    def has_clip(self):
        return self.clip is not None

    def copy_selection(self):
        """Copy the selected region's pixels into the clipboard as (w, h, bytes).
        No-op (returns False) with no active selection. Read-only -- records no undo
        step. Survives a sprite/size switch, so a kid can copy from one tile and paste
        onto another."""
        if self.sel is None:
            return False
        x0, y0, x1, y1 = self.sel
        w = x1 - x0 + 1
        h = y1 - y0 + 1
        ox, oy = self._origin()
        buf = bytearray(w * h)
        k = 0
        for yy in range(y0, y1 + 1):
            for xx in range(x0, x1 + 1):
                buf[k] = self.sheet.pget(ox + xx, oy + yy)
                k += 1
        self.clip = (w, h, bytes(buf))
        return True

    def paste(self, lx, ly, transparent=True):
        """Stamp the clipboard with its top-left at grid cell (lx, ly), clipped to the
        editable region, as ONE undo step. `transparent` (default) skips index-0 clip
        pixels so a paste overlays the destination (the sprite-editor idiom where 0 is
        transparent); pass False for an opaque block copy. Returns False with no clip."""
        if self.clip is None:
            return False
        w, h, buf = self.clip

        def build(bbuf, dim):
            for yy in range(h):
                ty = ly + yy
                if ty < 0 or ty >= dim:
                    continue
                for xx in range(w):
                    tx = lx + xx
                    if tx < 0 or tx >= dim:
                        continue
                    v = buf[yy * w + xx]
                    if transparent and v == 0:
                        continue
                    bbuf[ty * dim + tx] = v
        self._record(build)
        return True

    def cut_selection(self):
        """Copy the selection to the clipboard AND clear it to index 0 in one undo step
        -- the move primitive (cut here, paste elsewhere). No-op with no selection."""
        if not self.copy_selection():
            return False
        x0, y0, x1, y1 = self.sel

        def build(buf, dim):
            for yy in range(y0, y1 + 1):
                for xx in range(x0, x1 + 1):
                    buf[yy * dim + xx] = 0
        self._record(build)
        return True


class _MapOps:
    """OpCodec for MapEditor (#111): two JSON-able op forms, both replayed against
    the live TileMap cells (mirrors the old MapEditor._apply exactly, just over
    device-safe payloads).

      * DELTA: `[(idx, prev, new), ...]` -- a list of per-cell (index, old, new)
        triples, the cheap form for a small gesture. Serializes to a list-of-lists
        and reads back the same; the codec indexes by position, so tuple/list both
        work.
      * SNAPSHOT: `("snap", w, h, before_hex, after_hex)` -- a whole-map before/after
        pair (hex strings) for a gesture that touched a large share of the map (a
        full-map flood/rect), so a step's retained size stays bounded by the map,
        not one boxed tuple per cell.

    INVERT-only: undo reverses the deltas / restores `before`; redo re-applies /
    restores `after`. The doc is the MapEditor; `tm.dirty`/`gen` bump so a running
    cart's map cache rebuilds."""

    def apply(self, doc, op):          # redo -> forward
        self._replay(doc, op, True)

    def invert(self, doc, op):         # undo -> reverse
        self._replay(doc, op, False)

    def _replay(self, doc, op, forward):
        tm = doc.tilemap
        cells = tm.cells
        if op and op[0] == "snap":                 # whole-map snapshot step
            w, h = op[1], op[2]
            if w == tm.w and h == tm.h:            # a resize clears history -> guarded
                cells[:] = _hex2b(op[4] if forward else op[3])
        else:                                      # per-cell delta step
            n = len(cells)
            if forward:
                for e in op:
                    idx = e[0]
                    if 0 <= idx < n:
                        cells[idx] = e[2]
            else:
                # Unwind newest -> oldest so a cell written twice in one gesture
                # lands back on its ORIGINAL byte, not an intermediate one.
                for k in range(len(op) - 1, -1, -1):
                    e = op[k]
                    idx = e[0]
                    if 0 <= idx < n:
                        cells[idx] = e[1]
        tm.dirty = True
        tm.gen += 1


class MapEditor(OpHistoryMixin):
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
        # before/after snapshot -- a ("snap", w, h, before_hex, after_hex) tuple of
        # two hex blobs (2 bytes/cell), so a full-map flood/rect step is ~KBs, never
        # hundreds of KB. `begin_edit` opens the batch, `place`/`erase`/`fill_rect`/
        # `flood` append to it via `_set`, `end_edit` records it as one op into the
        # shared #111 op-history (dropping the redo stack). Bounded to UNDO_MAX RAM
        # steps; a project commit drains the batch into its journal line.
        self.cam_y = 0
        self._rec = None     # open edit batch (list of (idx, prev, new)) or None
        self._hist = History(self, _MapOps(), max_undo=self.UNDO_MAX)
        # Region select / copy / paste / move (#91), mirroring PaintEditor's model
        # (editors_paint_map, #90) so the two editors feel identical. `sel` is the
        # active selection rectangle in MAP CELLS (x0,y0,x1,y1 inclusive) or None;
        # `clip` is the copied region as (w, h, bytes) where each byte is the TileMap
        # STORAGE form (tile_id+1, 0 = EMPTY) so a paste round-trips a cell exactly.
        # Both default off so a freshly built editor behaves byte-identically to
        # before (the #39 parity contract -- the SELECT overlay only draws when the
        # select tool is active, never at open).
        self.sel = None
        self.clip = None

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
        flood/rect) is compacted to a whole-map before/after HEX snapshot so a
        step's retained size is bounded by ~2*w*h bytes, never a per-cell tuple list
        (#91). The cells are already edited (via _set), so `record` just logs the op
        -- which also drops the redo stack, the classic branch (#111)."""
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
                rec = ("snap", tm.w, tm.h, _b2hex(before), _b2hex(after))
            self._hist.record(rec)

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

    # undo/redo/can_* are OpHistoryMixin's over self._hist (#111); the _MapOps
    # codec (module top) replays a delta/snapshot either way.

    def _hist_before(self):
        # An in-flight gesture can't be half-undone: close any open batch so it
        # becomes the step's target.
        if self._rec:
            self.end_edit()

    def clear_history(self):
        """Drop the undo/redo stacks (a structural change -- a map resize -- makes
        the recorded cell indices meaningless, so history is reset, #91). The active
        selection rectangle is dropped too (its cell coords are stale on a resized
        grid); the coordinate-free clip survives so a copy can outlive a resize."""
        self._rec = None
        self._hist.clear()
        self.sel = None

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

    # -- region select / copy / paste / move (#91) ----------------------------
    # The map analogue of PaintEditor's #90 clipboard: PaintEditor copies palette
    # indices out of a sprite tile; this copies TileMap cell bytes out of the grid.
    # Same verbs, same shapes (sel / clip / copy_selection / paste / cut_selection),
    # so the two editors read identically -- the clip byte is the storage form
    # (tile_id+1, 0 = EMPTY), converted back to a tile id on paste so it round-trips.

    def set_selection(self, x0, y0, x1, y1):
        """Set the active selection rectangle (map cells, inclusive), normalized so a
        drag in any direction works and clamped inside the map bounds. Mirrors
        PaintEditor.set_selection."""
        tm = self.tilemap
        x0 = int(x0)
        y0 = int(y0)
        x1 = int(x1)
        y1 = int(y1)
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        mw = tm.w - 1
        mh = tm.h - 1
        x0 = 0 if x0 < 0 else (mw if x0 > mw else x0)
        y0 = 0 if y0 < 0 else (mh if y0 > mh else y0)
        x1 = 0 if x1 < 0 else (mw if x1 > mw else x1)
        y1 = 0 if y1 < 0 else (mh if y1 > mh else y1)
        self.sel = (x0, y0, x1, y1)

    def clear_selection(self):
        """Drop the active selection (the copied clip stays available for paste)."""
        self.sel = None

    @property
    def has_clip(self):
        return self.clip is not None

    def copy_selection(self):
        """Copy the selected region's RAW cell bytes into the clipboard as
        (w, h, bytes) (each byte tile_id+1, 0 = EMPTY -- the TileMap storage form, so a
        paste round-trips exactly). No-op (returns False) with no active selection;
        read-only, records no undo step. Survives a brush/size switch and a resize, so
        a kid can copy one area and paste it elsewhere."""
        if self.sel is None:
            return False
        x0, y0, x1, y1 = self.sel
        tm = self.tilemap
        w = x1 - x0 + 1
        h = y1 - y0 + 1
        cells = tm.cells
        buf = bytearray(w * h)
        k = 0
        for yy in range(y0, y1 + 1):
            base = yy * tm.w
            for xx in range(x0, x1 + 1):
                buf[k] = cells[base + xx]
                k += 1
        self.clip = (w, h, bytes(buf))
        return True

    def paste(self, cell_x, cell_y, transparent=True):
        """Stamp the clipboard with its top-left at map cell (cell_x, cell_y), clipped
        to the map, as ONE undo step. `transparent` (default) skips EMPTY (byte 0) clip
        cells so the paste overlays the destination (an empty clip cell doesn't punch a
        hole); pass False to lay the clip opaquely (its empties then erase). Returns
        False with no clip. Self-contained: opens/commits its own edit batch so it's a
        single undo step regardless of caller (begin_edit flushes any stray batch)."""
        if self.clip is None:
            return False
        w, h, buf = self.clip
        tm = self.tilemap
        cell_x = int(cell_x)
        cell_y = int(cell_y)
        self.begin_edit()
        for yy in range(h):
            ty = cell_y + yy
            if ty < 0 or ty >= tm.h:
                continue
            row = yy * w
            for xx in range(w):
                tx = cell_x + xx
                if tx < 0 or tx >= tm.w:
                    continue
                v = buf[row + xx]
                if transparent and v == 0:
                    continue
                self._set(tx, ty, v - 1)       # storage byte -> tile id (0 -> EMPTY)
        self.end_edit()
        return True

    def cut_selection(self):
        """Copy the selection to the clipboard AND clear it to EMPTY in one undo step
        -- the move primitive (cut here, paste elsewhere). No-op (False) with no
        selection. The copy is read-only; only the clear journals, so a single undo
        restores the cut region (mirrors PaintEditor.cut_selection)."""
        if not self.copy_selection():
            return False
        x0, y0, x1, y1 = self.sel
        tm = self.tilemap
        self.begin_edit()
        for yy in range(y0, y1 + 1):
            for xx in range(x0, x1 + 1):
                self._set(xx, yy, tm.EMPTY)
        self.end_edit()
        return True


# The block editor's in-session undo/redo depth (#93). Scripts are tiny JSON-ish
# trees, so a full-script snapshot per mutation is cheap; a bounded stack keeps
# the RAM footprint fixed on the device. In-session only (it never crosses a
# save / graduation -- the durable journal is Code's, spec Section 7).
