"""The one slide renderer (docs/media/look_2026-10/APPS_RESTYLE.md,
Storybook, "One renderer"): a story's slide at any zoom, for editing, the
cards, the slides column and Present alike.

A zoom is a fraction `num / den` of the 1024 x 600 slide. Every position
and size goes to the canvas in 26.6 fixed point, worked out in integers
from the document's slide pixels, and the shapes and the words are filled
by native/moy_gfx/moy_path.c: so a slide at a zoom is the same pixels on
every board, the host and the browser. Text is wrapped at the slide's own
size (1:1), so its lines break in the same places at every zoom; it draws
in the shell's outline faces at its size times the zoom. Pictures and a
picture background are nearest-neighbour, read from their files a row at
a time into a buffer of the size they draw at (`Images`), so no whole
raster sits in the gc heap.
"""

import array

try:
    import io as _io
except ImportError:  # pragma: no cover - a port with only uio
    import uio as _io

try:
    import moybuf as _moybuf
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import moybuf as _moybuf

try:
    from moyimg import cover_stream as _cover_stream
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moyimg import cover_stream as _cover_stream

try:
    import story_doc as _doc
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import story_doc as _doc

SW, SH = _doc.SW, _doc.SH
FACE = {"sans": 0, "sans_bold": 1, "mono": 2}
ONE = 64

# sin(k * pi / 64), k = 0 .. 32, in 1/65536: a quarter turn in 32 steps.
_SIN = (0, 3216, 6424, 9616, 12785, 15924, 19024, 22078, 25080, 28020, 30893, 33692, 36410,
        39040, 41576, 44011, 46341, 48559, 50660, 52639, 54491, 56212, 57798, 59244, 60547,
        61705, 62714, 63572, 64277, 64827, 65220, 65457, 65536)
# A five-pointed star's points on a unit circle (its inner ones at 0.42),
# from the top, clockwise, in 1/65536.
_STAR = ((0, -65536), (16179, -22268), (62328, -20252), (26178, 8506), (38521, 53020),
         (0, 27525), (-38521, 53020), (-26178, 8506), (-62328, -20252), (-16179, -22268))


def size(num, den):
    """A slide's (w, h) in pixels at zoom num / den, rounded half up (5/16
    is 320 x 188, 5/64 the slides column's 80 x 47)."""
    return ((SW * num + den // 2) // den, (SH * num + den // 2) // den)


def isqrt(n):
    """The integer square root of `n` (no float: every tier the same)."""
    if n <= 0:
        return 0
    x = n
    y = (x + 1) // 2
    while y < x:
        x = y
        y = (x + n // x) // 2
    return x


# -- the words ---------------------------------------------------------------------

class Words:
    """A text's lines, laid out at the slide's own size: (line, its width
    in 26.6) each, kept per text, face, size and width."""

    def __init__(self):
        self._kept = {}

    def lines(self, cv, text, font, size, w):
        key = (text, font, size, w)
        got = self._kept.get(key)
        if got is not None:
            return got
        if len(self._kept) > 96:
            self._kept = {}
        f = FACE[font]
        size64 = size * ONE
        w64 = max(ONE, w * ONE)
        out = []
        cur = ""
        cur_w = 0
        for word in text.split():
            cand = cur + " " + word if cur else word
            cw = cv.face_width(f, size64, cand)
            if cw <= w64:
                cur, cur_w = cand, cw
                continue
            if cur:
                out.append((cur, cur_w))
            # a word wider than the box breaks at its edge
            cur, cur_w = word, cv.face_width(f, size64, word)
            while cur_w > w64 and len(cur) > 1:
                n = len(cur) - 1
                while n > 1 and cv.face_width(f, size64, cur[:n]) > w64:
                    n -= 1
                out.append((cur[:n], cv.face_width(f, size64, cur[:n])))
                cur = cur[n:]
                cur_w = cv.face_width(f, size64, cur)
        if cur or not out:
            out.append((cur, cur_w))
        self._kept[key] = out
        return out


def line_h(size):
    """A line's height in 26.6 at the slide's size: 1.2 em."""
    return size * ONE * 12 // 10


def text_h(n, size):
    """A box of `n` lines' height in slide pixels: the lines and 0.2 em."""
    return (n * line_h(size) + size * ONE // 5 + ONE - 1) // ONE


# -- pictures ----------------------------------------------------------------------

class _Pic:
    """A picture read at one size: its RGB565 words (the canvas's order),
    `w` x `h`, and its corner's word (what a clear background leaves out)."""

    def __init__(self, rgb, w, h):
        self.rgb = rgb
        self.w = w
        self.h = h
        self.key = rgb[0] | (rgb[1] << 8)


def _blob_size(blob):
    """(w, h) a picture's blob says, or None."""
    head = blob[:256]
    if head.find('"data"') < 0:
        return None
    try:
        w = int(head[head.index('"w"') + 3:].lstrip(" :").split(",")[0].split("}")[0])
        h = int(head[head.index('"h"') + 3:].lstrip(" :").split(",")[0].split("}")[0])
    except (ValueError, IndexError):
        return None
    if not (0 < w <= _doc.IMAGE_W and 0 < h <= _doc.IMAGE_H):
        return None
    return (w, h)


def read_indices(blob, dw, dh, out):
    """The picture `blob` (moyimg-v1 text) at dw x dh into `out` (dw * dh
    MOY64 indices), read a row at a time (moy_ufiles.cover_image, else its
    reference moyimg.cover_stream). Whether it read."""
    data = blob.encode() if isinstance(blob, str) else blob
    try:
        try:
            import moy_ufiles
            got = moy_ufiles.cover_image(_io.BytesIO(data), out, dw, dh, _doc.IMAGE_W,
                                         _doc.IMAGE_H)
        except ImportError:
            got = _cover_stream(_io.BytesIO(data), out, dw, dh, _doc.IMAGE_W, _doc.IMAGE_H)
    except ValueError:
        got = None
    return got is not None


def read_picture(cv, blob, dw, dh):
    """The picture `blob` at dw x dh as `cv`'s RGB565 words: a _Pic over an
    off-heap buffer, or None."""
    idx = _moybuf.alloc(dw * dh)
    try:
        if not read_indices(blob, dw, dh, idx):
            return None
        rgb = _moybuf.alloc(2 * dw * dh)
        cv.bake565(idx, dw, dh, rgb)
        return _Pic(rgb, dw, dh)
    finally:
        _moybuf.free(idx)


class Images:
    """A story's pictures, read at the sizes the slides draw them: `load`
    (name -> its blob, or None) reaches the story's files. Kept up to
    `budget` bytes of pixels, the least recently drawn going first."""

    def __init__(self, load, budget):
        self.load = load
        self.budget = budget
        self._sizes = {}
        self._pics = {}                 # (name, w, h) -> _Pic
        self._order = []
        self._bytes = 0

    def size(self, name):
        """The picture's own (w, h), or None where it does not read."""
        got = self._sizes.get(name)
        if got is None and name not in self._sizes:
            blob = self.load(name)
            got = _blob_size(blob) if blob else None
            self._sizes[name] = got
        return got

    def get(self, cv, name, w, h):
        if w <= 0 or h <= 0:
            return None
        key = (name, w, h)
        pic = self._pics.get(key)
        if pic is not None:
            if self._order[-1] != key:
                self._order.remove(key)
                self._order.append(key)
            return pic
        blob = self.load(name)
        if not blob:
            return None
        pic = read_picture(cv, blob, w, h)
        blob = None
        if pic is None:
            return None
        self._pics[key] = pic
        self._order.append(key)
        self._bytes += 2 * w * h
        while self._bytes > self.budget and len(self._order) > 1:
            self._drop(self._order[0])
        return pic

    def _drop(self, key):
        pic = self._pics.pop(key)
        self._order.remove(key)
        self._bytes -= 2 * pic.w * pic.h
        _moybuf.free(pic.rgb)

    def forget(self, name=None):
        """Drop a picture (all of them with no name), its size too."""
        for key in list(self._order):
            if name is None or key[0] == name:
                self._drop(key)
        if name is None:
            self._sizes = {}
        else:
            self._sizes.pop(name, None)


# -- shapes as contours ------------------------------------------------------------

def _arc(out, cx, cy, rx, ry, q, step):
    """A quarter of an ellipse round (cx, cy), quadrant q (0 the top right,
    clockwise on screen), appended to `out` as x, y pairs."""
    for k in range(0, 33, step):
        s, c = _SIN[k], _SIN[32 - k]
        if q == 0:
            dx, dy = s, -c
        elif q == 1:
            dx, dy = c, s
        elif q == 2:
            dx, dy = -s, c
        else:
            dx, dy = -c, -s
        out.append(cx + rx * dx // 65536)
        out.append(cy + ry * dy // 65536)


def _step(r):
    r = r // ONE
    return 8 if r < 6 else 4 if r < 20 else 2 if r < 60 else 1


def _rect(x, y, w, h):
    return [x, y, x + w, y, x + w, y + h, x, y + h]


def _round(x, y, w, h, r):
    if r <= 0:
        return _rect(x, y, w, h)
    r = min(r, w // 2, h // 2)
    st = _step(r)
    out = []
    _arc(out, x + w - r, y + r, r, r, 0, st)
    _arc(out, x + w - r, y + h - r, r, r, 1, st)
    _arc(out, x + r, y + h - r, r, r, 2, st)
    _arc(out, x + r, y + r, r, r, 3, st)
    return out


def _ellipse(x, y, w, h):
    rx, ry = w // 2, h // 2
    st = _step(max(rx, ry))
    out = []
    for q in range(4):
        _arc(out, x + rx, y + ry, rx, ry, q, st)
    return out


def _star(x, y, w, h):
    r = min(w, h) // 2
    cx, cy = x + w // 2, y + h // 2 + h // 20
    out = []
    for sx, sy in _STAR:
        out.append(cx + r * sx // 65536)
        out.append(cy + r * sy // 65536)
    return out


def _area2(c):
    """Twice a contour's signed area (positive turning clockwise on screen)."""
    a = 0
    n = len(c) // 2
    for k in range(n):
        x0, y0 = c[2 * k], c[2 * k + 1]
        x1, y1 = c[2 * ((k + 1) % n)], c[2 * ((k + 1) % n) + 1]
        a += x0 * y1 - x1 * y0
    return a


def _clockwise(c):
    return c if _area2(c) >= 0 else _reverse(c)


def _inset_tri(t, i):
    """Triangle `t` (x, y pairs) with each edge moved `i` inward."""
    if i <= 0:
        return t
    cx = (t[0] + t[2] + t[4]) // 3
    cy = (t[1] + t[3] + t[5]) // 3
    lines = []
    for k in range(3):
        x0, y0 = t[2 * k], t[2 * k + 1]
        x1, y1 = t[2 * ((k + 1) % 3)], t[2 * ((k + 1) % 3) + 1]
        dx, dy = x1 - x0, y1 - y0
        n = max(1, isqrt(dx * dx + dy * dy))
        nx, ny = -dy * i // n, dx * i // n
        if (cx - x0) * nx + (cy - y0) * ny < 0:
            nx, ny = -nx, -ny
        lines.append((x0 + nx, y0 + ny, dx, dy))
    out = []
    for k in range(3):
        ax, ay, adx, ady = lines[k - 1]
        bx, by, bdx, bdy = lines[k]
        den = adx * bdy - ady * bdx
        if den == 0:
            return t
        u = ((bx - ax) * bdy - (by - ay) * bdx)
        out.append(ax + adx * u // den)
        out.append(ay + ady * u // den)
    return out


def _bubble(x, y, w, h, i):
    """The speech bubble's body and tail, `i` in from its edge, both turning
    clockwise (where they overlap they add up, never cancel)."""
    bh = h * 76 // 100
    r = max(ONE, min(w, bh) * 24 // 100 - i)
    body = _round(x + i, y + i, w - 2 * i, bh - 2 * i, r)
    tail = [x + w * 16 // 100, y + bh - 2 * ONE, x + w * 8 // 100, y + h,
            x + w * 34 // 100, y + bh - 2 * ONE]
    return [_clockwise(body), _clockwise(_inset_tri(tail, i))]


def _flip(c, x, y, w, h, flip):
    if flip & 1:
        for k in range(0, len(c), 2):
            c[k] = 2 * x + w - c[k]
    if flip & 2:
        for k in range(1, len(c), 2):
            c[k] = 2 * y + h - c[k]
    return c


def _reverse(c):
    out = []
    for k in range(len(c) - 2, -1, -2):
        out.append(c[k])
        out.append(c[k + 1])
    return out


def _thick(x0, y0, x1, y1, lw):
    """A line from (x0, y0) to (x1, y1), `lw` thick, as a quad."""
    dx, dy = x1 - x0, y1 - y0
    n = isqrt(dx * dx + dy * dy)
    if n == 0:
        return _rect(x0 - lw // 2, y0 - lw // 2, lw, lw)
    ox, oy = -dy * lw // (2 * n), dx * lw // (2 * n)
    return [x0 + ox, y0 + oy, x1 + ox, y1 + oy, x1 - ox, y1 - oy, x0 - ox, y0 - oy]


def shape_contours(kind, x, y, w, h, flip, i):
    """A closed shape's contours (lists of x, y in 26.6), `i` in from its
    edge: what its line is drawn as (i 0) and its fill inside the line."""
    if kind == "rect":
        cs = [_rect(x + i, y + i, w - 2 * i, h - 2 * i)]
    elif kind == "round":
        cs = [_round(x + i, y + i, w - 2 * i, h - 2 * i, min(w, h) * 22 // 100 - i)]
    elif kind == "ellipse":
        cs = [_ellipse(x + i, y + i, w - 2 * i, h - 2 * i)]
    elif kind == "star":
        cs = [_star(x + i * 2, y + i * 2, w - 4 * i, h - 4 * i)]
    else:
        cs = _bubble(x, y, w, h, i)
    return [_flip(c, x, y, w, h, flip) for c in cs]


def _fill(cv, contours, colour):
    pts = array.array("i")
    ends = array.array("i")
    for c in contours:
        for v in c:
            pts.append(v)
        ends.append(len(pts) // 2)
    if ends and ends[-1] >= 3:
        cv.fill_path(pts, ends, colour)


def _body_box(o):
    """Where a shape's words go: its body (a bubble's above its tail), less
    its line."""
    lw = o["width"] if o["line"] is not None else 0
    h = o["h"] * 76 // 100 if o["shape"] == "bubble" else o["h"]
    y = o["y"] + (o["h"] - h if o["shape"] == "bubble" and o["flip"] & 2 else 0)
    pad = 2 * lw if o["shape"] != "star" else o["w"] // 6
    return (o["x"] + pad, y, max(1, o["w"] - 2 * pad), h)


# -- the renderer --------------------------------------------------------------------

class Renderer:
    """Draws slides: `images` reads the story's pictures."""

    def __init__(self, images):
        self.images = images
        self.words = Words()

    def draw(self, cv, slide, ox, oy, num, den, step=-1, clip=None, edit=False):
        """Slide `slide` with its top left at (ox, oy) of `cv` at zoom
        num / den, inside `clip` (x, y, w, h) where given, its objects up to
        tap `step` (-1: all). `edit` draws what only the editor shows: a
        layout's empty frames."""
        sw, sh = size(num, den)
        x0, y0, x1, y1 = ox, oy, ox + sw, oy + sh
        if clip is not None:
            x0, y0 = max(x0, clip[0]), max(y0, clip[1])
            x1, y1 = min(x1, clip[0] + clip[2]), min(y1, clip[1] + clip[3])
        if x1 <= x0 or y1 <= y0:
            return
        cv.clip(x0, y0, x1 - x0, y1 - y0)
        try:
            bg = slide["bg"]
            if isinstance(bg, int):
                cv.rect(ox, oy, sw, sh, bg)
            else:
                cv.rect(ox, oy, sw, sh, 0)
                pic = self.images.get(cv, bg, sw, sh)
                if pic is not None:
                    cv.blit565(pic.rgb, sw, sh, ox, oy)
            for o in slide["objects"]:
                if step >= 0 and o["step"] > step:
                    continue
                t = o["type"]
                if t == "text":
                    self._text(cv, o, ox, oy, num, den, edit)
                elif t == "shape":
                    self._shape(cv, o, ox, oy, num, den)
                elif t == "picture":
                    self._picture(cv, o, ox, oy, num, den, edit)
        finally:
            cv.clip()

    # -- what each object draws ------------------------------------------------

    def box_h(self, cv, o):
        """A text box's height in slide pixels: its lines and 0.2 em."""
        n = len(self.words.lines(cv, o["text"], o["font"], o["size"], o["w"]))
        return text_h(n, o["size"])

    def _words(self, cv, text, font, size, colour, align, bx, by, bw, ox, oy, num, den,
               centre_h=None):
        """`text` wrapped in a box at (bx, by), bw wide (slide pixels); with
        `centre_h` its lines centred in a box that tall."""
        lines = self.words.lines(cv, text, font, size, bw)
        f = FACE[font]
        lh = line_h(size)
        top = by * ONE
        if centre_h is not None:
            top += (centre_h * ONE - len(lines) * lh) // 2 - size * ONE // 20
        base = top + size * ONE * 95 // 100
        size_z = size * ONE * num // den
        w64 = bw * ONE
        for line, lw in lines:
            if align == "centre":
                dx = (w64 - lw) // 2
            elif align == "right":
                dx = w64 - lw
            else:
                dx = 0
            gx = ox * ONE + (bx * ONE + dx) * num // den
            gy = oy * ONE + base * num // den
            if line:
                cv.face_text(f, size_z, gx, gy, line, colour)
            base += lh

    def _text(self, cv, o, ox, oy, num, den, edit):
        text = o["text"]
        if o["strip"] is not None:
            h = self.box_h(cv, o)
            x = 0 if o["w"] >= SW else o["x"]
            w = SW if o["w"] >= SW else o["w"]
            _fill(cv, [_rect(ox * ONE + x * ONE * num // den, oy * ONE + o["y"] * ONE * num // den,
                             w * ONE * num // den, h * ONE * num // den)], o["strip"])
        if not text and edit:
            self._words(cv, "Write here", o["font"], o["size"], 6, o["align"], o["x"], o["y"],
                        o["w"], ox, oy, num, den)
            return
        self._words(cv, text, o["font"], o["size"], o["colour"], o["align"], o["x"], o["y"],
                    o["w"], ox, oy, num, den)

    def _shape(self, cv, o, ox, oy, num, den):
        def q(v):
            return v * ONE * num // den
        x, y = ox * ONE + q(o["x"]), oy * ONE + q(o["y"])
        w, h = q(o["x"] + o["w"]) - q(o["x"]), q(o["y"] + o["h"]) - q(o["y"])
        kind = o["shape"]
        lw = max(ONE // 2, q(o["width"]))
        line, fill = o["line"], o["fill"]
        if kind in ("line", "arrow"):
            col = line if line is not None else fill
            if col is None:
                return
            x0, y0, x1, y1 = x, y, x + w, y + h
            if o["flip"] & 1:
                x0, x1 = x1, x0
            if o["flip"] & 2:
                y0, y1 = y1, y0
            if kind == "arrow":
                dx, dy = x1 - x0, y1 - y0
                n = max(1, isqrt(dx * dx + dy * dy))
                head = min(lw * 4, n)
                bx, by = x1 - dx * head // n, y1 - dy * head // n
                wx, wy = -dy * lw * 5 // (4 * n), dx * lw * 5 // (4 * n)
                _fill(cv, [_thick(x0, y0, bx, by, lw), [x1, y1, bx + wx, by + wy,
                                                         bx - wx, by - wy]], col)
            else:
                _fill(cv, [_thick(x0, y0, x1, y1, lw)], col)
        else:
            if line is not None:
                outer = shape_contours(kind, x, y, w, h, o["flip"], 0)
                if fill is None:
                    inner = shape_contours(kind, x, y, w, h, o["flip"], lw)
                    _fill(cv, outer + [_reverse(c) for c in inner], line)
                else:
                    _fill(cv, outer, line)
            if fill is not None:
                _fill(cv, shape_contours(kind, x, y, w, h, o["flip"],
                                         lw if line is not None else 0), fill)
        if o["text"]:
            bx, by, bw, bh = _body_box(o)
            self._words(cv, o["text"], o["font"], o["size"], o["colour"], o["align"], bx, by,
                        bw, ox, oy, num, den, centre_h=bh)

    def _picture(self, cv, o, ox, oy, num, den, edit):
        x = ox + o["x"] * num // den
        y = oy + o["y"] * num // den
        name = o["image"]
        if name is None:
            if edit:
                w, h = o["w"] * num // den, o["h"] * num // den
                ln = max(ONE, 2 * ONE * num // den)
                q = ONE
                c = _rect(x * q, y * q, w * q, h * q)
                i = _rect(x * q + ln, y * q + ln, w * q - 2 * ln, h * q - 2 * ln)
                _fill(cv, [c, _reverse(i)], 6)
                self._words(cv, "Add a picture", "sans", 40, 6, "centre", o["x"],
                            o["y"], o["w"], ox, oy, num, den, centre_h=o["h"])
            return
        src = self.images.size(name)
        if src is None:
            return
        sw, sh = src
        if o["scale"] == 0.5:
            name = name + "_h"
            sw, sh = sw // 2, sh // 2
        else:
            sw, sh = sw * o["scale"], sh * o["scale"]
        w, h = sw * num // den, sh * num // den
        pic = self.images.get(cv, name, w, h)
        if pic is None:
            return
        cv.blit565(pic.rgb, w, h, x, y, 1, pic.key if o["clear"] else -1)


def half(pix, w, h):
    """A picture's half (w // 2 x h // 2 indices) that keeps its lines: each
    pixel the first of its two-by-two that is not the picture's corner
    colour (its background), else that colour, so a 1 px line survives."""
    hw, hh = w // 2, h // 2
    key = pix[0]
    out = bytearray(hw * hh)
    for y in range(hh):
        r0 = 2 * y * w
        r1 = r0 + w
        o = y * hw
        for x in range(hw):
            a = 2 * x
            c = pix[r0 + a]
            if c == key:
                c = pix[r0 + a + 1]
                if c == key:
                    c = pix[r1 + a]
                    if c == key:
                        c = pix[r1 + a + 1]
            out[o + x] = c
    return out
