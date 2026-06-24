"""Indexed software canvas for the v0.4 workstation (host reference impl).

A `Canvas` is a `width x height` buffer of palette indices (default 480x270, the
v0.4 logical workstation surface) with the PICO-8-style drawing API from the v0.4
plan (cls / pset / line / rect / rectfill / circ / circfill / spr / print).
`to_rgb888()` resolves indices through the palette for display (pygame) or export
(GIF). The same index-based API is what a future device backend maps onto the
native `kc_compositor` RGB565 framebuffer.
"""

from . import palette as _pal

# Compact 3x5 font: char -> 5 rows of 3 cells ('#'/space). Enough for titles and
# numbers; a richer native font is a later (device) concern.
_FONT_ROWS = {
    " ": ("   ", "   ", "   ", "   ", "   "),
    "0": ("###", "# #", "# #", "# #", "###"),
    "1": (" # ", "## ", " # ", " # ", "###"),
    "2": ("###", "  #", "###", "#  ", "###"),
    "3": ("###", "  #", "###", "  #", "###"),
    "4": ("# #", "# #", "###", "  #", "  #"),
    "5": ("###", "#  ", "###", "  #", "###"),
    "6": ("###", "#  ", "###", "# #", "###"),
    "7": ("###", "  #", "  #", "  #", "  #"),
    "8": ("###", "# #", "###", "# #", "###"),
    "9": ("###", "# #", "###", "  #", "###"),
    "A": ("###", "# #", "###", "# #", "# #"),
    "B": ("## ", "# #", "## ", "# #", "## "),
    "C": ("###", "#  ", "#  ", "#  ", "###"),
    "D": ("## ", "# #", "# #", "# #", "## "),
    "E": ("###", "#  ", "###", "#  ", "###"),
    "F": ("###", "#  ", "###", "#  ", "#  "),
    "G": ("###", "#  ", "# #", "# #", "###"),
    "H": ("# #", "# #", "###", "# #", "# #"),
    "I": ("###", " # ", " # ", " # ", "###"),
    "J": ("  #", "  #", "  #", "# #", "###"),
    "K": ("# #", "# #", "## ", "# #", "# #"),
    "L": ("#  ", "#  ", "#  ", "#  ", "###"),
    "M": ("# #", "###", "###", "# #", "# #"),
    "N": ("# #", "###", "###", "###", "# #"),
    "O": ("###", "# #", "# #", "# #", "###"),
    "P": ("###", "# #", "###", "#  ", "#  "),
    "Q": ("###", "# #", "# #", "###", "  #"),
    "R": ("## ", "# #", "## ", "# #", "# #"),
    "S": ("###", "#  ", "###", "  #", "###"),
    "T": ("###", " # ", " # ", " # ", " # "),
    "U": ("# #", "# #", "# #", "# #", "###"),
    "V": ("# #", "# #", "# #", "# #", " # "),
    "W": ("# #", "# #", "###", "###", "# #"),
    "X": ("# #", "# #", " # ", "# #", "# #"),
    "Y": ("# #", "# #", " # ", " # ", " # "),
    "Z": ("###", "  #", " # ", "#  ", "###"),
    ":": ("   ", " # ", "   ", " # ", "   "),
    ".": ("   ", "   ", "   ", "   ", " # "),
    "-": ("   ", "   ", "###", "   ", "   "),
    "!": (" # ", " # ", " # ", "   ", " # "),
    "/": ("  #", "  #", " # ", "#  ", "#  "),
    ",": ("   ", "   ", "   ", " # ", "#  "),
    "?": ("###", "  #", " # ", "   ", " # "),
    "'": (" # ", " # ", "   ", "   ", "   "),
    "=": ("   ", "###", "   ", "###", "   "),
    "_": ("   ", "   ", "   ", "   ", "###"),
    "(": (" ##", " # ", " # ", " # ", " ##"),
    ")": ("## ", " # ", " # ", " # ", "## "),
    "+": ("   ", " # ", "###", " # ", "   "),
    "%": ("# #", "  #", " # ", "#  ", "# #"),
}


class Image:
    """A small indexed sprite. `pix` is a flat list/bytes of palette indices."""

    def __init__(self, width, height, pix, transparent=None):
        self.w = width
        self.h = height
        self.pix = pix
        self.transparent = transparent

    @classmethod
    def from_ascii(cls, rows, mapping, transparent="."):
        """Build from ['..##..', ...] using {char: index}; `transparent` char skipped."""
        h = len(rows)
        w = max(len(r) for r in rows) if rows else 0
        t_index = -1
        pix = []
        for y in range(h):
            row = rows[y]
            for x in range(w):
                ch = row[x] if x < len(row) else transparent
                if ch == transparent:
                    pix.append(t_index)
                else:
                    pix.append(mapping[ch] & 63)
        return cls(w, h, pix, transparent=t_index)


class Canvas:
    def __init__(self, width=480, height=270, palette=None):
        self.w = width
        self.h = height
        self.palette = palette or _pal.KID64
        self.buf = bytearray(width * height)

    # -- primitives ----------------------------------------------------------

    def cls(self, c=0):
        self.buf[:] = bytes((c & 63,)) * (self.w * self.h)

    def pset(self, x, y, c):
        x = int(x)
        y = int(y)
        if 0 <= x < self.w and 0 <= y < self.h:
            self.buf[y * self.w + x] = c & 63

    def pget(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.buf[y * self.w + x]
        return 0

    def rectfill(self, x, y, w, h, c):
        x = int(x)
        y = int(y)
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(self.w, x + int(w))
        y1 = min(self.h, y + int(h))
        if x1 <= x0 or y1 <= y0:
            return
        ci = c & 63
        row = bytes((ci,)) * (x1 - x0)
        buf = self.buf
        width = self.w
        for yy in range(y0, y1):
            base = yy * width + x0
            buf[base:base + (x1 - x0)] = row

    def rect(self, x, y, w, h, c):
        x = int(x)
        y = int(y)
        w = int(w)
        h = int(h)
        self.rectfill(x, y, w, 1, c)
        self.rectfill(x, y + h - 1, w, 1, c)
        self.rectfill(x, y, 1, h, c)
        self.rectfill(x + w - 1, y, 1, h, c)

    def line(self, x0, y0, x1, y1, c):
        x0 = int(x0)
        y0 = int(y0)
        x1 = int(x1)
        y1 = int(y1)
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.pset(x0, y0, c)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def circfill(self, cx, cy, r, c):
        cx = int(cx)
        cy = int(cy)
        r = int(r)
        for dy in range(-r, r + 1):
            span = int((r * r - dy * dy) ** 0.5)
            self.rectfill(cx - span, cy + dy, 2 * span + 1, 1, c)

    def circ(self, cx, cy, r, c):
        cx = int(cx)
        cy = int(cy)
        r = int(r)
        x = r
        y = 0
        err = 0
        while x >= y:
            for px, py in ((x, y), (y, x), (-y, x), (-x, y), (-x, -y), (-y, -x), (y, -x), (x, -y)):
                self.pset(cx + px, cy + py, c)
            y += 1
            if err <= 0:
                err += 2 * y + 1
            else:
                x -= 1
                err -= 2 * x + 1

    def spr(self, img, x, y, scale=1):
        x = int(x)
        y = int(y)
        scale = int(scale)
        t = img.transparent
        if scale <= 1:
            buf = self.buf
            width = self.w
            for sy in range(img.h):
                ty = y + sy
                if ty < 0 or ty >= self.h:
                    continue
                base_s = sy * img.w
                base_d = ty * width
                for sx in range(img.w):
                    tx = x + sx
                    if tx < 0 or tx >= width:
                        continue
                    p = img.pix[base_s + sx]
                    if p == t or p < 0:
                        continue
                    buf[base_d + tx] = p & 63
            return
        # Scaled blit: each source pixel becomes a scale x scale block.
        for sy in range(img.h):
            base_s = sy * img.w
            for sx in range(img.w):
                p = img.pix[base_s + sx]
                if p == t or p < 0:
                    continue
                self.rectfill(x + sx * scale, y + sy * scale, scale, scale, p)

    def print(self, s, x, y, c, scale=2):
        x = int(x)
        y = int(y)
        cx = x
        for ch in str(s).upper():
            glyph = _FONT_ROWS.get(ch)
            if glyph is not None:
                for gy in range(5):
                    rowbits = glyph[gy]
                    for gx in range(3):
                        if rowbits[gx] == "#":
                            self.rectfill(cx + gx * scale, y + gy * scale, scale, scale, c)
            cx += 4 * scale  # 3 wide + 1 spacing

    # -- output --------------------------------------------------------------

    def to_rgb888(self):
        pal3 = [bytes(rgb) for rgb in self.palette]
        return b"".join(pal3[i] for i in self.buf)
