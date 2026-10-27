# Map (grep -n a name to jump there):
#   PaintDocument                 an indexed drawing: its marks, its undo by tiles
#   picture_size                  a drawing's (w, h) from its blob's head
#   read_cover                    a drawing streamed into a w x h screen, by rows
#   colour_name                   a MOY64 colour's number and name, as Paint says it
#   ArtworkService                Paint's document model and the wallpaper copy
#   ArtworkService.load           the open drawing as (w, h, index bytes)
#   ArtworkService.save           persist the canvas to its named drawing
#   ArtworkService.set_wallpaper  a drawing as the desktop wallpaper
#   ArtworkService.attach         copy a drawing into a cart
#   DrawingCovers                 the drawings at a card's size, read by rows
"""Paint's document model: a drawing, the open picture and its copies.

PaintDocument holds one drawing's indices and the marks that change them;
ArtworkService, which Paint builds over its own roles, owns the open picture
(persisting it) and the wallpaper copy (the ``Wallpaper`` slot). Publishing a
drawing through the slot (copied into the theme as its picture,
docs/theming_2026-09.md §4.9) or into a project as ``images/bg.moyimg`` is
runtime/picture_copies.py's, which Files and My Art use too. The app is
runtime/paint_app.py; the drawings' page is runtime/myart_app.py.

Drawings are USER FILES (#108): named ``files/drawings/*.moyimg`` items in the
store, auto-named (naming is never a gate), browsed by My Art and Files, and
autosaved. Publishing is copy-on-use everywhere: Use as wallpaper and Put in a
game copy the named file's pixels at that moment (editing the drawing
afterwards changes nothing until it is sent again -- the photo-roll model).

A desk board's drawings are 1024 x 600, a handheld's 320 x 240 (owner,
2026-10-21). A handheld opens a 1024 x 600 drawing to look at, never to draw
on, and reads it a row at a time (`read_cover`) into a screen of the size it
shows, so no whole raster of it is ever held on the small boards.

The Player hands the service only to the shipped Paint cart, by its identity
(``is_paint_app``). Ordinary kid cartridges keep the frozen cart API unchanged
and cannot reach the store through this object.
"""

try:
    from moy_store_base import builtin_name, COVER_FILE
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import builtin_name, COVER_FILE

try:
    from file_widgets import Bitmap
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.file_widgets import Bitmap

try:
    from app_context import NO_STORE
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.app_context import NO_STORE

try:
    from picture_copies import PictureCopies
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.picture_copies import PictureCopies

try:
    from moyimg import cover_stream
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moyimg import cover_stream

try:
    import moy_gfx as _gfx
except ImportError:  # pragma: no cover - host: the kernel through its binding
    from runtime import gfx_binding as _gfx

try:
    import io as _io
except ImportError:  # pragma: no cover - every tier has io
    import uio as _io

try:
    import moy_ufiles as _uf
except ImportError:  # pragma: no cover - a board without the layer
    _uf = None

# A cart's cover is this square, always (SPEC.md 3.6).
COVER_SIDE = 128
# The drawings a board makes (owner, 2026-10-21): a desk board's, and a
# handheld's and a game's.
DESK_W, DESK_H = 1024, 600
GAME_W, GAME_H = 320, 240

# MOY64's base sixteen by the names Paint says them in ("12 · Blue").
_NAMES = ("Black", "Dark blue", "Dark purple", "Dark green", "Brown", "Dark grey",
          "Light grey", "White", "Red", "Orange", "Yellow", "Green", "Blue", "Indigo",
          "Pink", "Peach")


def colour_name(c, dot=" · "):
    """Colour `c`'s number and, for the base sixteen, its name: "12 · Blue"."""
    c = int(c) & 63
    return str(c) + dot + _NAMES[c] if c < len(_NAMES) else str(c)


def picture_size(blob):
    """A drawing's (w, h) from its blob's head, without decoding it; None
    when the blob is not a picture's."""
    if not blob:
        return None
    head = blob[:160]
    if not isinstance(head, str):
        head = str(bytes(head), "utf-8", "ignore")
    out = []
    for key in ('"w"', '"h"'):
        i = head.find(key)
        if i < 0:
            return None
        i = head.find(":", i) + 1
        while i < len(head) and head[i] == " ":
            i += 1
        j = i
        while j < len(head) and "0" <= head[j] <= "9":
            j += 1
        if j == i:
            return None
        out.append(int(head[i:j]))
    return (out[0], out[1]) if out[0] > 0 and out[1] > 0 else None


def read_cover(blob, dst, dw, dh):
    """Drawing `blob` read into `dst` (dw * dh indices) a row at a time,
    cropped about its centre to dw:dh and sampled nearest -- the picture
    decoder the backdrop reads with (native/moy_store/moy_img.h, whose
    reference is `moyimg.cover_stream`), so a 1024 x 600 drawing never stands
    whole in memory. Its (w, h), or None when it is not a picture."""
    if not blob:
        return None
    raw = blob.encode() if isinstance(blob, str) else blob
    f = _io.BytesIO(raw)
    if _uf is not None:
        try:
            return _uf.cover_image(f, dst, dw, dh, DESK_W, DESK_H)
        except (AttributeError, ImportError):
            f.seek(0)
    return cover_stream(f, dst, dw, dh, DESK_W, DESK_H)


def _invalidate_bitmap(img):
    """Drop every bake a canvas keeps on `img`: its pixels all changed."""
    if img is None:
        return
    for name, value in (("_rgb_i", None), ("_rgb", None),
                        ("_rgb_variants", {})):
        try:
            setattr(img, name, value)
        except Exception:  # noqa: BLE001 -- host bitmap has no device cache
            pass


class PaintDocument:
    """An indexed drawing: its pixels, the marks that change them, and their
    undo. A mark records the TILES it is about to change before it changes
    them (`_touch`), so an undo step holds the parts a stroke reached, never
    the whole drawing -- on a 1024 x 600 desk drawing a whole copy is 600 KB a
    step. `dirty` is the rect the marks changed since the canvas last mended
    its bake (`take_dirty`)."""

    PAPER = 7
    # The off-heap loan key for this document's bake (#186). The app id, so a
    # reader of moy_alloc's ledger can name what is holding the memory.
    # Released by PaintAppLayer._return_bakes.
    OWNER = "artwork"
    TILE = 32
    UNDO_STEPS = 24
    UNDO_BYTES = 384 * 1024          # the tiles every step holds, together
    _MARKER = 255                    # a flood's region while it is found: no colour
    _FLOOD_STACK = 4096              # the flood's (x, y) scratch, pairs

    def __init__(self, width=GAME_W, height=GAME_H, seed=False):
        self.W = int(width)
        self.H = int(height)
        self.pix = bytearray(self.W * self.H)
        _gfx.ifill(self.pix, self.W, self.H, 0, 0, self.W, self.H, self.PAPER)
        self.image = Bitmap(self.W, self.H, self.pix, self.OWNER)
        self.tiles_x = (self.W + self.TILE - 1) // self.TILE
        self.history = []
        self.future = []
        self._act = None              # the mark in hand: {tile: its pixels before}
        self._held = 0                # the bytes it holds
        self._lost = False            # it outgrew UNDO_BYTES: it records nothing more
        self.dirty = None             # [x0, y0, x1, y1) the marks changed
        if seed and self.W * 300 == self.H * 512:
            self.seed_desktop()

    @property
    def action_live(self):
        return self._act is not None

    def load(self, data):
        if data is None or data[0] < 1 or data[1] < 1:
            return False
        if data[0] != self.W or data[1] != self.H:
            self.__init__(data[0], data[1])
        self.pix[:] = data[2]
        self.history = []
        self.future = []
        self.invalidate()
        return True

    def invalidate(self):
        """Every pixel changed: the whole bake goes."""
        _invalidate_bitmap(self.image)
        self.dirty = None

    def take_dirty(self):
        """The rect the marks changed since the last call, (x, y, w, h), or
        None; the next call starts afresh."""
        d = self.dirty
        self.dirty = None
        if d is None:
            return None
        return (d[0], d[1], d[2] - d[0], d[3] - d[1])

    # -- undo by tiles --------------------------------------------------------

    def snapshot(self):
        """A mark begins: what it changes is recorded until `finish`."""
        if self._act is None:
            self._act = {}
            self._held = 0
            self._lost = False

    def finish(self):
        """The mark ends: the tiles it changed are one undo step. A step
        larger than every step may hold together leaves no undo at all, as
        trimming it would."""
        act = self._act
        self._act = None
        if self._lost:
            self._lost = False
            self.history = []
            self.future = []
            return
        if not act:
            return
        self.history.append(tuple(act.items()))
        self.future = []
        self._trim(self.history)

    def _trim(self, steps):
        size = 0
        for st in steps:
            for _t, data in st:
                size += len(data)
        while steps and (len(steps) > self.UNDO_STEPS or size > self.UNDO_BYTES):
            for _t, data in steps.pop(0):
                size -= len(data)

    def _tile_rect(self, t):
        T = self.TILE
        x = (t % self.tiles_x) * T
        y = (t // self.tiles_x) * T
        return x, y, min(T, self.W - x), min(T, self.H - y)

    def _tile(self, t):
        x, y, w, h = self._tile_rect(t)
        out = bytearray(w * h)
        _gfx.icopy(out, w, h, 0, 0, self.pix, self.W, self.H, x, y, w, h, -1, 0)
        return out

    def _put_tile(self, t, data):
        x, y, w, h = self._tile_rect(t)
        _gfx.icopy(self.pix, self.W, self.H, x, y, data, w, h, 0, 0, w, h, -1, 0)
        self._grow(x, y, x + w, y + h)

    def _swap(self, src, dst):
        if not src:
            return False
        step = src.pop()
        back = []
        for t, data in step:
            back.append((t, self._tile(t)))
            self._put_tile(t, data)
        dst.append(tuple(back))
        self._trim(dst)
        return True

    def undo(self):
        return self._swap(self.history, self.future)

    def redo(self):
        return self._swap(self.future, self.history)

    def _grow(self, x0, y0, x1, y1):
        d = self.dirty
        if d is None:
            self.dirty = [x0, y0, x1, y1]
            return
        if x0 < d[0]:
            d[0] = x0
        if y0 < d[1]:
            d[1] = y0
        if x1 > d[2]:
            d[2] = x1
        if y1 > d[3]:
            d[3] = y1

    def _touch(self, x0, y0, x1, y1):
        """Pixels x0..x1, y0..y1 (inclusive) are about to change: the mark in
        hand records the tiles they lie in, and the dirty rect grows. False
        where none of them is on the drawing."""
        if x0 < 0:
            x0 = 0
        if y0 < 0:
            y0 = 0
        if x1 >= self.W:
            x1 = self.W - 1
        if y1 >= self.H:
            y1 = self.H - 1
        if x1 < x0 or y1 < y0:
            return False
        self._grow(x0, y0, x1 + 1, y1 + 1)
        if self._act is not None:
            self._keep(x0 // self.TILE, y0 // self.TILE, x1 // self.TILE, y1 // self.TILE)
        return True

    def _keep(self, tx0, ty0, tx1, ty1):
        """The mark in hand records tiles tx0..tx1 by ty0..ty1 it has not."""
        act = self._act
        tx = self.tiles_x
        ts = None
        for ty in range(ty0, ty1 + 1):
            for t in range(ty * tx + tx0, ty * tx + tx1 + 1):
                if t not in act:
                    if ts is None:
                        ts = []
                    ts.append(t)
        if ts is not None:
            self._hold(ts)

    def _hold(self, ts, frm=-1, to=0):
        """The mark in hand records tiles `ts` as they are (a value `frm` read
        as `to`), into one buffer: an allocation a call, not one a tile."""
        if self._lost:
            return
        n = 0
        for t in ts:
            _x, _y, w, h = self._tile_rect(t)
            n += w * h
        if self._held + n > self.UNDO_BYTES:
            self._lost = True
            self._act.clear()
            return
        self._held += n
        buf = memoryview(bytearray(n))
        act = self._act
        W, H, pix = self.W, self.H, self.pix
        o = 0
        for t in ts:
            x, y, w, h = self._tile_rect(t)
            k = buf[o:o + w * h]
            _gfx.icopy(k, w, h, 0, 0, pix, W, H, x, y, w, h, frm, to)
            act[t] = k
            o += w * h

    def _reach(self, x0, y0, x1, y1, d):
        """A line of dots `d` across, top lefts (x0, y0) to (x1, y1), is about
        to be drawn: the dirty rect grows over it, and the mark in hand records
        the tiles each dot on the drawing lies in, walked as the line steps --
        a long diagonal's undo holds the strip it crossed, never its box."""
        W = self.W
        H = self.H
        lx = x0 if x0 < x1 else x1
        ly = y0 if y0 < y1 else y1
        hx = (x1 if x0 < x1 else x0) + d - 1
        hy = (y1 if y0 < y1 else y0) + d - 1
        if hx < 0 or hy < 0 or lx >= W or ly >= H:
            return
        self._grow(max(0, lx), max(0, ly), min(W, hx + 1), min(H, hy + 1))
        if self._act is None:
            return
        T = self.TILE
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        last = -1
        while True:
            ax = x0 if x0 > 0 else 0
            ay = y0 if y0 > 0 else 0
            bx = x0 + d - 1
            by = y0 + d - 1
            if bx >= W:
                bx = W - 1
            if by >= H:
                by = H - 1
            if bx >= ax and by >= ay:
                k = ax // T | (ay // T) << 7 | (bx // T) << 14 | (by // T) << 21
                if k != last:
                    last = k
                    self._keep(ax // T, ay // T, bx // T, by // T)
            if x0 == x1 and y0 == y1:
                return
            e2 = err + err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    # -- marks ------------------------------------------------------------------

    def get(self, x, y):
        return self.pix[int(y) * self.W + int(x)]

    def put(self, x, y, color):
        x = int(x)
        y = int(y)
        if x < 0 or y < 0 or x >= self.W or y >= self.H:
            return
        if self._touch(x, y, x, y):
            self.pix[y * self.W + x] = int(color) & 63

    def _span(self, x0, x1, y, c):
        """Row y from x0 to x1 inclusive in colour `c`, clipped; the caller
        has touched it."""
        _gfx.ifill(self.pix, self.W, self.H, x0, y, x1 - x0 + 1, 1, c)

    def stamp(self, x, y, color, d=1, round_=True):
        self.line(x, y, x, y, color, d, round_)

    def line(self, x0, y0, x1, y1, color, d=1, round_=True):
        d = max(1, int(d))
        a = d // 2
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        self._reach(x0 - a, y0 - a, x1 - a, y1 - a, d)
        _gfx.iline(self.pix, self.W, self.H, x0, y0, x1, y1, d, round_, int(color) & 63)

    def box(self, a, b, color, filled=False, d=1):
        x0, x1 = min(a[0], b[0]), max(a[0], b[0])
        y0, y1 = min(a[1], b[1]), max(a[1], b[1])
        d = max(1, int(d))
        if not self._touch(x0, y0, x1, y1):
            return
        c = int(color) & 63
        W, H, pix = self.W, self.H, self.pix
        w = x1 - x0 + 1
        h = y1 - y0 + 1
        if filled or 2 * d >= min(x1 - x0, y1 - y0) + 1:
            _gfx.ifill(pix, W, H, x0, y0, w, h, c)
            return
        _gfx.ifill(pix, W, H, x0, y0, w, d, c)
        _gfx.ifill(pix, W, H, x0, y1 - d + 1, w, d, c)
        _gfx.ifill(pix, W, H, x0, y0 + d, d, h - 2 * d, c)
        _gfx.ifill(pix, W, H, x1 - d + 1, y0 + d, d, h - 2 * d, c)

    def oval(self, a, b, color, filled=False, d=1):
        """The ellipse inscribed in the box from `a` to `b`: filled, or its
        rim `d` pixels thick."""
        x0, x1 = min(a[0], b[0]), max(a[0], b[0])
        y0, y1 = min(a[1], b[1]), max(a[1], b[1])
        if not self._touch(x0, y0, x1, y1):
            return
        c = int(color) & 63
        cx = (x0 + x1 + 1) / 2
        cy = (y0 + y1 + 1) / 2
        rx = (x1 - x0 + 1) / 2
        ry = (y1 - y0 + 1) / 2
        d = max(1, int(d))
        ix = rx - d
        iy = ry - d
        for y in range(y0, y1 + 1):
            t = (y + 0.5 - cy) / ry
            k = 1 - t * t
            if k < 0:
                continue
            half = rx * k ** 0.5
            l = int(cx - half + 0.5)
            r = int(cx + half - 0.5)
            if filled or ix <= 0 or iy <= 0:
                self._span(l, r, y, c)
                continue
            t = (y + 0.5 - cy) / iy
            k = 1 - t * t
            if k <= 0:
                self._span(l, r, y, c)
                continue
            inner = ix * k ** 0.5
            il = int(cx - inner + 0.5)
            ir = int(cx + inner - 0.5)
            self._span(l, max(l, il - 1), y, c)
            self._span(min(r, ir + 1), r, y, c)

    def circle(self, center, edge, color, filled=False, d=1):
        """The circle about `center` through `edge` (the starter garden's)."""
        cx, cy = center
        dx, dy = edge[0] - cx, edge[1] - cy
        r = int((dx * dx + dy * dy) ** 0.5)
        self.oval((cx - r, cy - r), (cx + r, cy + r), color, filled, d)

    def spray(self, x, y, color, d, seed):
        """A puff of dots about (x, y), wider with the size; `seed` is the
        caller's generator state, returned moved on."""
        spread = 2 * d + 2
        if not self._touch(x - spread, y - spread, x + spread, y + spread):
            return seed
        c = int(color) & 63
        W = self.W
        for _i in range(4 + 2 * d):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            ox = seed % (spread * 2 + 1) - spread
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            oy = seed % (spread * 2 + 1) - spread
            px, py = x + ox, y + oy
            if ox * ox + oy * oy <= spread * spread and 0 <= px < W and 0 <= py < self.H:
                self.pix[py * W + px] = c
        return seed

    def flood(self, x, y, color):
        """The region of (x, y)'s colour around it, in `color`: filled with a
        marker first, so the tiles it reached are recorded as they were (the
        marker read as the old colour), then the marker becomes the colour."""
        old = self.get(x, y)
        color = int(color) & 63
        if old == color:
            return
        W, H, T = self.W, self.H, self.TILE
        tx = self.tiles_x
        marks = bytearray(tx * ((H + T - 1) // T))
        n, x0, y0, x1, y1 = _gfx.iflood(self.pix, W, H, x, y, self._MARKER, marks, T, tx,
                                        bytearray(self._FLOOD_STACK * 4))
        if not n:
            return
        act = self._act
        if act is not None:
            self._hold([t for t in range(len(marks)) if marks[t] and t not in act],
                       self._MARKER, old)
        _gfx.icopy(self.pix, W, H, x0, y0, self.pix, W, H, x0, y0, x1 - x0, y1 - y0,
                   self._MARKER, color)
        self._grow(x0, y0, x1, y1)

    def seed_desktop(self):
        """Editable starter art: a designed retro garden wallpaper, drawn at
        512 x 300 and at twice that on a desk board's 1024 x 600."""
        k = self.W // 512
        W = self.W
        H = self.H
        pix = self.pix

        def s(v):
            return v * k

        bands = ((0, 70, 22), (70, 138, 21), (138, 202, 20), (202, 240, 19))
        for y0, y1, c in bands:
            _gfx.ifill(pix, W, H, 0, s(y0), W, s(y1) - s(y0), c)
        self.circle((s(423), s(70)), (s(463), s(70)), 18, True)
        for x in range(W):
            xs = x // k
            ridge = 186 - abs((xs % 210) - 105) // 3
            top = s(max(145, ridge))
            _gfx.ifill(pix, W, H, x, top, 1, s(220) - top, 23 if xs < 300 else 13)
        for y in range(s(205), s(260)):
            self._span(0, W - 1, y, 22 if (y // k) & 4 else 21)
        for y in range(s(255), self.H):
            self._span(0, W - 1, y, 32 if y // k < 278 else 3)
        self.line(s(102), s(274), s(126), s(128), 28, s(14))
        self.line(s(116), s(185), s(67), s(115), 28, s(8))
        self.line(s(119), s(176), s(180), s(104), 28, s(8))
        self.line(s(105), s(205), s(54), s(176), 33, s(6))
        clusters = ((48, 96, 32), (82, 80, 38), (122, 89, 42),
                    (166, 78, 34), (202, 105, 30), (73, 130, 36),
                    (132, 132, 40), (178, 132, 34))
        shades = (25, 14, 16, 15)
        for i in range(len(clusters)):
            cx, cy, r = clusters[i]
            self.circle((s(cx), s(cy)), (s(cx + r), s(cy)), shades[i & 3], True)
            self.circle((s(cx - r // 3), s(cy - r // 4)),
                        (s(cx - r // 3 + r // 2), s(cy - r // 4)), 7 if i & 1 else 15, True)
        for x in range(260, 500, 12):
            self._span(s(x), s(min(511, x + 7)), s(232 + (x % 5)), 48)
        for i in range(38):
            x = (i * 73 + 41) % 512
            y = 34 + ((i * 47) % 210)
            self.stamp(s(x), s(y), 14 if i % 3 else 7, k, False)
            if i % 5 == 0:
                self.stamp(s(x + 1), s(y), 15, k, False)
        self.dirty = None


class DrawingCovers:
    """The drawings' pictures at a card's size, read a row at a time from
    their files (`read_cover`) and kept by (name, w, h) until the drawing
    changes: My Art's cards, Paint's stories. A broken file keeps None.
    A picture outlives `recheck` where its file's bytes are kept beside it
    (a file of up to KEEP_BYTES): the first ask after it reads the file
    again and decodes it only where the bytes moved -- a read is a tenth of
    a decode on the T-Deck."""

    KEEP = 24
    KEEP_BYTES = 16384

    def __init__(self, files):
        self._files = files
        self._got = {}               # name -> [[w, h, bitmap or None, kind, bytes or None], ...]
        self._order = []             # (name, its entry), the oldest first
        self._checked = {}           # names whose pictures are the file's as it is

    def get(self, name, w, h, kind="drawings"):
        # Looked up by name, then compared by size, so a frame that asks for
        # pictures it has makes no key.
        got = self._got.get(name)
        if got is not None:
            for e in got:
                if e[0] == w and e[1] == h and e[3] == kind:
                    if name in self._checked:
                        return e[2]
                    blob, err = self._files.load(kind, name)
                    if err is None and blob is not None and blob == e[4]:
                        self._checked[name] = True
                        return e[2]
                    self.forget(name)
                    got = None
                    break
        bmp = None
        keep = None
        try:
            blob, err = self._files.load(kind, name)
            if err is None and blob:
                pix = bytearray(w * h)
                if read_cover(blob, pix, w, h) is not None:
                    bmp = Bitmap(w, h, pix)
                if len(blob) <= self.KEEP_BYTES:
                    keep = blob if isinstance(blob, (str, bytes)) else bytes(blob)
            blob = None
        except MemoryError:
            raise
        except Exception:  # noqa: BLE001 -- a broken file draws no picture
            bmp = None
        e = [w, h, bmp, kind, keep]
        if got is None:
            got = self._got[name] = []
        got.append(e)
        self._checked[name] = True
        self._order.append((name, e))
        if len(self._order) > self.KEEP:
            old, oe = self._order.pop(0)
            sizes = self._got.get(old)
            if sizes is not None:
                sizes.remove(oe)
                if not sizes:
                    del self._got[old]
                    self._checked.pop(old, None)
        return bmp

    def forget(self, name=None):
        """Drop the pictures of `name` (all, with none): its file changed."""
        if name is None:
            self._got.clear()
            del self._order[:]
            self._checked.clear()
            return
        self._got.pop(name, None)
        self._checked.pop(name, None)
        self._order = [o for o in self._order if o[0] != name]

    def recheck(self):
        """The files may have changed since the pictures were made (another
        app wrote them): each picture kept with its file's bytes is checked
        against the file at its next ask, the rest dropped."""
        self._checked.clear()
        for name in list(self._got):
            for e in self._got[name]:
                if e[4] is None:
                    self.forget(name)
                    break


class ArtworkService:
    """Paint's DOCUMENT model: the open picture, its load and save, and the
    wallpaper copy (the Wallpaper slot), which a pick copies into the theme. Paint builds it
    over Paint's own context and keeps it as `PaintAppLayer.service`, which
    is how the console reaches it; the copy-on-use verbs are Paint's
    `PictureCopies` (runtime/picture_copies.py), over the same roles. The open
    picture lives in Paint's prefs, whose namespace is "paint" (`paint_doc` has
    been the key in real cards' system.json since #108), which the `artwork`
    role's C rows read for every other app."""

    # The largest document Paint holds -- and it is Paint's OWN largest, the
    # desk boards' 1024 x 600 drawing, not a number picked for the gate below.
    # Smaller pictures are editable at every size, because PaintDocument.load
    # re-sizes to what it is given. So "Too big to edit" is about a picture
    # bigger than anything this system makes. A handheld never decodes one
    # larger than its own drawings: it views it (`peek`, `read_cover`).
    MAX_W = DESK_W
    MAX_H = DESK_H
    PAINT_TITLE = "Paint"

    def __init__(self, ctx):
        self.ctx = ctx
        self._files = ctx.files
        self._carts = ctx.carts
        self._wall = ctx.wallpaper
        self._prefs = ctx.prefs
        self._notify = ctx.notify
        self.copies = PictureCopies(ctx)
        self.last_error = ""
        self._cached = None            # the OPEN doc's decoded (w, h, bytes)
        self._thumb_bitmap = None
        self._thumb_key = None
        # The copy's generation (the wallpaper role's save_copy bumps it, from
        # any app): the decode above is the copy of the generation last seen.
        self._copy_gen = getattr(ctx, "copy_gen", None)
        self._copy_seen = None
        self._read_only = False        # the open picture is show-only
        self._why = ""
        self.on_leave = None           # called before the pointer moves to another picture
        self.moves = 0                 # counts the pointer's sets: a frame compares one int

    def _ready(self):
        return self._files.ready()

    @classmethod
    def is_paint_app(cls, cart):
        """True only for the shipped Paint identity, not a renamed/copied cart."""
        if (not cart or cart.get("title") != cls.PAINT_TITLE
                or "artwork" not in (cart.get("permissions") or ())):
            return False
        path = cart.get("path")
        if not path:                 # embedded fallback cart (no writable store)
            return int(cart.get("version", 0)) >= 1
        return builtin_name(path) == "moybyte.paint.moy"

    def available(self):
        return self._ready()

    # -- the open document (a named files item) -------------------------------
    #
    # The doc pointer is a `(kind, name)` PAIR, because a picture is not only a
    # drawing: a cart's own images (`images/<name>.moyimg`, #108) and its cover
    # (`cover.png`, SPEC.md 3.6) open here too, on the project kind
    # (`moy_carts.PROJECT_KIND`), and are written back to the cart's folder
    # rather than into the gallery. `drawings` is the default and the only kind
    # the auto-naming, trash and copy-on-use verbs know -- a project image was
    # NAMED by the format, so there is nothing to auto-name.
    #
    # THE COVER is a 128 x 128 canvas in the console palette, saved as an
    # indexed cover.png. A cover in direct colour opens with each pixel mapped
    # to its nearest palette colour, and is rewritten only once the kid draws
    # on it -- opening and leaving writes nothing. A cover.png outside the
    # profile (or one that does not decode) opens as a BLANK 128 x 128 cover,
    # editable, that replaces the bad file the first time it is saved.

    DRAWINGS = "drawings"

    def doc_name(self):
        """The open picture's file name, or None before the first save."""
        return self._prefs.get("doc")

    def doc_kind(self):
        """The files kind the open picture lives in ("drawings" by default; a
        `project:<folder>` kind for a cart's own image)."""
        return self._prefs.get("doc_kind") or self.DRAWINGS

    def editable(self):
        """False while the open picture is one Paint can SHOW but not change --
        a shape it has no editor for. Never a refusal to open: a picture always
        opens somewhere a kid can look at it."""
        return not self._read_only

    def why_read_only(self):
        return self._why

    def _cover_doc(self):
        """True while the open picture is a cart's cover.png."""
        return self.doc_kind() != self.DRAWINGS and self.doc_name() == COVER_FILE

    def _open_drawing(self):
        """The open picture's name IF it is a gallery drawing, else None. The
        copy-on-use verbs (WALL / GAME) are about the gallery: a cart's own
        image was never copied FROM anywhere, so it is not a source."""
        return self.doc_name() if self.doc_kind() == self.DRAWINGS else None

    def _set_doc_name(self, name, kind=None):
        self.moves += 1
        prefs = self._prefs
        kind = kind or self.DRAWINGS
        if prefs.get("doc_kind") != (None if kind == self.DRAWINGS else kind):
            if kind == self.DRAWINGS:
                prefs.clear("doc_kind")
            else:
                prefs.set("doc_kind", kind)
        if prefs.get("doc") == name:
            return
        if name is None:
            prefs.clear("doc")
        else:
            prefs.set("doc", name)

    def open_named(self, name, kind=None):
        """Point Paint at another picture (Files' and My Art's Open, and the
        cart-image door). The next load() reads it; `on_leave`, where Paint
        set it, writes the open one first."""
        if self.on_leave is not None and (kind or self.DRAWINGS, name) != \
                (self.doc_kind(), self.doc_name()):
            self.on_leave()
        self._set_doc_name(name, kind)
        self._cached = None
        self._read_only = False
        self._why = ""

    def peek(self):
        """The open picture as its file: (w, h, blob) of a gallery drawing,
        read but not decoded -- what a handheld views a desk's drawing from
        -- or None for no drawing, a cart's own image or one that is not a
        picture. Resolves the doc pointer as `load` does."""
        if self.doc_kind() != self.DRAWINGS:
            return None
        files = self._files
        name = self.doc_name()
        if not name:
            names, err = files.list(self.DRAWINGS)
            if err is not None or not names:
                return None
            name = names[0]
        blob, err = files.load(self.DRAWINGS, name)
        if err is not None or not blob:
            return None
        size = picture_size(blob)
        if size is None:
            return None
        self._set_doc_name(name, self.DRAWINGS)
        return (size[0], size[1], blob)

    def load(self):
        """Return ``(w, h, index_bytes)`` for the open picture, or ``None``.
        Resolves the doc pointer (running the one-shot #108 migration first):
        no pointer -> the newest drawing; an empty kind -> fresh canvas; a
        pointer at a since-deleted file -> a fresh canvas under that name.

        A picture Paint cannot EDIT still loads -- it comes back read-only with
        a reason, and only a blob that will not decode at all comes back None.
        A cart's own image is asked for by name and never falls back to the
        gallery: there is exactly one file behind that door."""
        files = self._files
        kind = self.doc_kind()
        self._read_only = False
        self._why = ""

        name = self.doc_name()
        blob, err = None, None
        if kind == self.DRAWINGS and not name:
            names, err = files.list(kind)
            name = names[0] if names else None
        if name is not None and err is None:
            blob, err = files.load(kind, name)
        if err is not None:      # a bad/missing drawing is non-fatal
            if err is not NO_STORE:
                self.last_error = str(err)
            return None
        if name is not None:
            self._set_doc_name(name, kind)
        if self._cover_doc():
            self._cached = files.decode_cover(blob)
            if self._cached is None:
                self._cached = (COVER_SIDE, COVER_SIDE,
                                bytes((PaintDocument.PAPER,))
                                * (COVER_SIDE * COVER_SIDE))
            return self._cached
        self._cached = files.decode_image(blob)
        if self._cached is None:
            if blob:
                self._read_only = True
                self._why = "Can't read this picture"
        elif self._cached[0] > self.MAX_W or self._cached[1] > self.MAX_H:
            # Bigger than Paint's largest document. It still OPENS -- refusing a
            # picture outright is what "no editor for this" was, and a kid with a
            # picture they cannot look at learns nothing.
            self._read_only = True
            self._why = "Too big to edit"
        return self._cached

    def save(self, indices, width=320, height=240):
        """Persist Paint's canvas to its named drawing file (auto-naming a
        first save). Publishing is copy-on-use: saving never touches the
        wallpaper or any project copy (#108)."""
        if not self._ready():
            self.last_error = "Storage is off"
            return False
        if self._read_only:
            self.last_error = self._why or "Only to look at"
            return False
        w = int(width)
        h = int(height)
        if w <= 0 or h <= 0 or w > self.MAX_W or h > self.MAX_H:
            self.last_error = "Can't save this size"
            return False
        files = self._files
        kind = self.doc_kind()
        if self._cover_doc():
            if w != COVER_SIDE or h != COVER_SIDE:
                self.last_error = "Can't save this size"
                return False
            blob = files.encode_cover(indices)
        else:
            blob = files.encode_image(w, h, indices)
        # A cart's own image keeps its name: the FORMAT chose it, and an
        # auto-name would write a file the cart does not look for.
        got, err = self.doc_name(), None
        if not got:
            got, err = files.new_name(kind)
        if err is None:
            _v, err = files.save(kind, got, blob)
        if err is not None:      # surface failure in the app
            self.last_error = str(err)
            return False
        self._set_doc_name(got, kind)
        self._cached = (w, h, bytes(indices))
        self.last_error = ""
        self._notify.achieve("paint_save")
        return True

    def new_doc(self, width, height):
        """Start a fresh auto-named drawing (the old one keeps its file). The
        name is assigned now but written only by the first save, so an
        untouched new canvas never litters the gallery."""
        if not self._ready():
            self._set_doc_name(None)
            return None
        name, err = self._files.new_name(self.DRAWINGS)
        if err is not None:
            self.last_error = str(err)
            return None
        # NEW always lands in the gallery, so it also LEAVES a cart's image --
        # a fresh canvas is not a new cover.
        self._set_doc_name(name, self.DRAWINGS)
        self._read_only = False
        self._why = ""
        self._cached = None
        return name

    # -- the wallpaper copy (#108 copy-on-set) --------------------------------
    #
    # The artwork.moyimg sibling is the WALLPAPER COPY: set_wallpaper snapshots
    # a named drawing into it (and into My Art's images/bg for the launcher
    # shelf), and picking My Art copies it into the theme as its picture
    # (`Appearance.change`). Editing the drawing afterwards changes nothing
    # until it is set again -- so deleting/renaming drawings can never break
    # the desktop.

    def sync_wallpaper(self):
        """Restore ``My Art/bg`` from the re-seed-proof wallpaper-copy file."""
        if not self._ready():
            return False
        wall = self.copies.wall_cart()
        if wall is None or not wall.get("path"):
            return False
        blob, err = self._wall.load_copy()
        if err is not None:
            self.last_error = str(err)
            return False
        if not blob:
            return False
        images, err = self._carts.images(wall)
        if err is None and (images or {}).get("bg") != blob:
            _v, err = self._carts.save_image(wall, "bg", blob)
        if err is not None:
            self.last_error = str(err)
            return False
        return True

    def set_wallpaper(self, name=None):
        """Copy the named drawing (default: the open one) into the wallpaper
        slot and make it the theme's picture."""
        ok = self.copies.set_wallpaper(name or self._open_drawing())
        self.last_error = self.copies.last_error
        self._copy_check()
        return ok

    def copy_changed(self):
        """The wallpaper copy was written (the wallpaper role's `save_copy`,
        from any app, which moves the copy's generation): the thumbnail goes,
        read again at the next ask."""
        self._thumb_bitmap = None
        self._thumb_key = None

    def _copy_check(self):
        """Drop the thumbnail when the copy's generation moved since it was
        read."""
        cg = self._copy_gen
        if cg is not None:
            gen = cg()
            if gen != self._copy_seen:
                if self._copy_seen is not None:
                    self.copy_changed()
                self._copy_seen = gen

    def thumbnail(self, width, height):
        """The wallpaper copy at `width` x `height` for the visual Appearance
        picker, read a row at a time from the copy (`read_cover`) and kept
        until the copy changes; None with no copy."""
        self._copy_check()
        w = max(1, int(width))
        h = max(1, int(height))
        key = (w, h)
        if self._thumb_key == key:
            return self._thumb_bitmap
        bmp = None
        if self._ready():
            blob, err = self._wall.load_copy()
            if err is not None:
                self.last_error = str(err)
                return None
            if blob:
                pix = bytearray(w * h)
                try:
                    if read_cover(blob, pix, w, h) is not None:
                        bmp = Bitmap(w, h, pix)
                except MemoryError:
                    raise
                except Exception:  # noqa: BLE001 -- a bad copy draws nothing
                    bmp = None
        self._thumb_bitmap = bmp
        self._thumb_key = key
        return bmp

    def targets(self):
        """Project titles Paint can offer in Put in a game."""
        return self.copies.targets()

    def attach(self, index, name=None):
        """Copy the named drawing (default: the open one) into one project as
        ``images/bg.moyimg``: the project's title, or None."""
        got = self.copies.attach(index, name or self._open_drawing())
        self.last_error = self.copies.last_error
        return got
