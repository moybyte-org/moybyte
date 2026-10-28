#!/usr/bin/env python3
"""The 2026-10 look, modern rendering: the theme set, as data over one renderer.

Draws the handheld home (320x240) and the desk (1024x600) per theme, a Calc
sheet across every theme, and a type specimen, as an RGB565 panel would show
them. Every theme is a dict of fields over `BASE`; the renderer reads only
fields. What each pixel costs follows the pipeline in NOTES.md:

- text: glyphs pre-rasterised from the font at fixed sizes, their coverage
  at the face's bpp (1, 2 or 4), placed at whole pixels with a kern table,
  blended over the pixels below;
- shapes: corner coverage at 4 bpp (smooth) or thresholded (stepped); soft
  shadows as a blurred 4 bpp mask, hard shadows as an offset fill;
- a theme with `render.scale` 2 lays the desk out at 1024x600 and draws it
  at 512x300, doubled;
- paints: a colour, a gradient (one colour per row), a stepped gradient, or
  an ordered-dither blend of two colours;
- every blend lands in RGB565.

This is the renderer and the base scenes; `draw.py` layers the rounds over
it and draws every picture `index.txt` names. Running this file runs it.
The fonts download once into ~/.cache/moybyte-look-fonts (see FONT_FILES).
"""
import contextlib
import copy
import io
import os
import sys
import urllib.request
import zipfile

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
OUT = sys.argv[1] if len(sys.argv) > 1 else HERE
CACHE = os.environ.get("MOY_LOOK_FONTS", os.path.expanduser("~/.cache/moybyte-look-fonts"))
GF = "https://github.com/google/fonts/raw/main/ofl/"
FONT_FILES = {  # every one SIL OFL-1.1
    "inter": ("Inter.ttf", GF + "inter/Inter%5Bopsz,wght%5D.ttf"),
    "plex": ("IBMPlexSans.ttf", GF + "ibmplexsans/IBMPlexSans%5Bwdth,wght%5D.ttf"),
    "jbmono": ("JetBrainsMono.ttf", GF + "jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf"),
    "atkinson": ("AtkinsonHyperlegibleNext.ttf",
                 GF + "atkinsonhyperlegiblenext/AtkinsonHyperlegibleNext%5Bwght%5D.ttf"),
    "micro5": ("Micro5.ttf", GF + "micro5/Micro5-Regular.ttf"),
    "cantarell": ("Cantarell-Regular.ttf", GF + "cantarell/Cantarell-Regular.ttf"),
    "cantarell-bold": ("Cantarell-Bold.ttf", GF + "cantarell/Cantarell-Bold.ttf"),
}
DEJAVU = ("/usr/share/fonts/truetype/dejavu",
          "https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/"
          "dejavu-fonts-ttf-2.37.zip")
GALMURI = "https://github.com/quiple/galmuri/releases/download/v2.40.4/Galmuri-v2.40.4.zip"
INDEX = []


# ==========================================================================
# RGB565
# ==========================================================================
def q(c):
    if isinstance(c, str):
        c = (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))
    r, g, b = c[0] >> 3, c[1] >> 2, c[2] >> 3
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


LUT565 = ([q((v, 0, 0))[0] for v in range(256)] + [q((0, v, 0))[1] for v in range(256)]
          + [q((0, 0, v))[2] for v in range(256)])


def mix(a, b, t):
    a, b = q(a), q(b)
    return q(tuple(round(a[k] + (b[k] - a[k]) * t) for k in range(3)))


def _lin(v):
    v /= 255
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def lum(c):
    c = q(c)
    return 0.2126 * _lin(c[0]) + 0.7152 * _lin(c[1]) + 0.0722 * _lin(c[2])


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def levels(mask, n):
    """Quantise a coverage mask to n levels (n = 2**bpp)."""
    if n >= 256:
        return mask
    if n == 2:
        return mask.point(lambda a: 255 if a >= 112 else 0)
    return mask.point([round(round(a * (n - 1) / 255) * 255 / (n - 1)) for a in range(256)])


# ==========================================================================
# fonts: glyphs at fixed sizes, n-bpp coverage, kern pairs
# ==========================================================================
def _dejavu(name):
    """DejaVu (Bitstream Vera licence): the system copy, or the release zip."""
    for d in (DEJAVU[0], CACHE):
        if os.path.exists(os.path.join(d, name)):
            return os.path.join(d, name)
    os.makedirs(CACHE, exist_ok=True)
    z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(DEJAVU[1]).read()))
    for n in z.namelist():
        if n.endswith(".ttf"):
            with open(os.path.join(CACHE, os.path.basename(n)), "wb") as fh:
                fh.write(z.read(n))
    return os.path.join(CACHE, name)


def _galmuri(name):
    """Galmuri (SIL OFL-1.1, Lee Minseo): the release zip, unpacked once."""
    path = os.path.join(CACHE, name)
    if not os.path.exists(path):
        os.makedirs(CACHE, exist_ok=True)
        z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(GALMURI).read()))
        for n in z.namelist():
            if n.endswith(".ttf") and "Bitmap" not in n:
                with open(os.path.join(CACHE, os.path.basename(n)), "wb") as fh:
                    fh.write(z.read(n))
    return path


def font_path(key, weight=400, size=None):
    if key == "galmuri":  # each cut is crisp at its design size and its multiples
        if size % 12 == 0:
            return _galmuri("Galmuri11-Bold.ttf" if weight >= 600 else "Galmuri11.ttf")
        return _galmuri("Galmuri9.ttf" if size % 10 == 0 else "Galmuri7.ttf")
    if key == "dejavu":
        return _dejavu("DejaVuSansCondensed-Bold.ttf" if weight >= 600 else "DejaVuSansCondensed.ttf")
    if key == "dejavu-oblique":
        return _dejavu("DejaVuSans-BoldOblique.ttf")
    if key == "cantarell" and weight >= 600:
        key = "cantarell-bold"
    name, url = FONT_FILES[key]
    path = os.path.join(CACHE, name)
    if not os.path.exists(path):
        os.makedirs(CACHE, exist_ok=True)
        urllib.request.urlretrieve(url, path)
    return path


def _vary(f, size, weight):
    try:
        axes = f.get_variation_axes()
    except OSError:
        return
    vals = []
    for ax in axes:
        n = ax["name"].decode() if isinstance(ax["name"], bytes) else ax["name"]
        if "Weight" in n:
            vals.append(max(ax["minimum"], min(ax["maximum"], weight)))
        elif "Optical" in n:
            vals.append(max(ax["minimum"], min(ax["maximum"], size)))
        else:
            vals.append(ax["default"])
    f.set_variation_by_axes(vals)


class _FaceBase:
    """Metrics in layout pixels (the logical size times the canvas scale k);
    glyphs in logical pixels."""
    _all = {}

    def lwidth(self, s):
        w, prev = 0, None
        for ch in s:
            if prev:
                w += self.kerning(prev, ch)
            w += self.glyph(ch)[3]
            prev = ch
        return w

    def width(self, s):
        return self.lwidth(s) * self.k

    @property
    def cap(self):
        return self.lcap * self.k

    def mfnt_bytes(self, chars):
        """A v2 file: 16-byte header, per glyph 8 bytes (offset, box, bearing,
        advance) and its packed coverage, plus 3 bytes per kern pair."""
        body = 0
        for ch in chars:
            m = self.glyph(ch)[0]
            body += 8
            if m:
                body += (m.size[0] * m.size[1] * self.bpp + 7) // 8
        pairs = sum(1 for a in KERN_PAIRS for b in KERN_PAIRS if self.kerning(a, b))
        return 16 + body + 3 * pairs


class Face(_FaceBase):
    """One size of one weight of an outline font: what one MFNT v2 file holds."""

    def __new__(cls, fam, size, weight=400, bpp=4, k=1):
        key = (fam, size, weight, bpp, k)
        if key not in cls._all:
            self = super().__new__(cls)
            self.fam, self.size, self.weight, self.bpp, self.k = key
            p = font_path(fam, weight, size)
            self.f = ImageFont.truetype(p, size, layout_engine=ImageFont.Layout.BASIC)
            _vary(self.f, size, weight)
            try:
                self.fk = ImageFont.truetype(p, size, layout_engine=ImageFont.Layout.RAQM)
                _vary(self.fk, size, weight)
            except Exception:
                self.fk = None
            self.g, self.kern = {}, {}
            self.lcap = -self.f.getbbox("H", anchor="ls")[1]
            cls._all[key] = self
        return cls._all[key]

    SUBST = {"−": "-", "×": "x", "÷": "/", "…": "...", "·": "-"}

    def _ink(self, ch):
        im = Image.new("L", (4 * self.size, 2 * self.size))
        ImageDraw.Draw(im).text((self.size, self.size * 3 // 2), ch, font=self.f, anchor="ls", fill=255)
        return im.tobytes()

    def _missing(self, ch):
        if ch not in self.SUBST:
            return False
        if not hasattr(self, "_tofu"):
            self._tofu = self._ink("\uffff")
        return self._ink(ch) == self._tofu

    def glyph(self, ch):
        if ch not in self.g and self._missing(ch):
            sub = self.SUBST[ch]
            self.g[ch] = self.glyph(sub[0]) if len(sub) == 1 else self.glyph(sub[0])
        if ch not in self.g:
            x0, y0, x1, y1 = self.f.getbbox(ch, anchor="ls")
            adv = round(self.f.getlength(ch))
            if x1 <= x0 or y1 <= y0:
                self.g[ch] = (None, 0, 0, adv)
            else:
                m = Image.new("L", (x1 - x0, y1 - y0), 0)
                d = ImageDraw.Draw(m)
                if self.bpp == 1:
                    d.fontmode = "1"
                d.text((-x0, -y0), ch, font=self.f, anchor="ls", fill=255)
                self.g[ch] = (levels(m, 1 << self.bpp), x0, y0, adv)
        return self.g[ch]

    def kerning(self, a, b):
        if self.fk is None or self.bpp == 1:
            return 0
        if (a, b) not in self.kern:
            fk = self.fk
            self.kern[(a, b)] = round(fk.getlength(a + b) - fk.getlength(a) - fk.getlength(b))
        return self.kern[(a, b)]


class Petme(_FaceBase):
    """petme128, the engine's own 8x8 print font (runtime/font.py, MIT), at a
    whole-number scale: an MFNT v1 face, 1 bpp, fixed 8 px advance."""
    SUBST = {"×": "x", "÷": "/", "−": "-", "…": "~", "·": "-", "‹": "<", "›": ">"}

    def __new__(cls, scale=1, k=1):
        key = ("petme", scale, k)
        if key not in cls._all:
            sys.path.insert(0, os.path.join(ROOT, "runtime"))
            import font as petme
            self = super().__new__(cls)
            self.data, self.first = petme.DATA, petme.FIRST
            self.s, self.k, self.bpp, self.size = scale, k, 1, 8 * scale
            self.g = {}
            self.lcap = 7 * scale
            cls._all[key] = self
        return cls._all[key]

    def glyph(self, ch):
        if ch not in self.g:
            c = ord(self.SUBST.get(ch, ch))
            if not 0x20 <= c < 0x80:
                c = 0x3F
            m = Image.new("L", (8, 8), 0)
            for col in range(8):
                b = self.data[(c - self.first) * 8 + col]
                for row in range(8):
                    if b >> row & 1:
                        m.putpixel((col, row), 255)
            s = self.s
            if s != 1:
                m = m.resize((8 * s, 8 * s), Image.NEAREST)
            self.g[ch] = (m, 0, -7 * s, 8 * s)
        return self.g[ch]

    def kerning(self, a, b):
        return 0


CHARSET = ([chr(c) for c in range(0x20, 0x7F)] + [chr(c) for c in range(0xA0, 0x100)]
           + list("−←→↑↓…•“”‘’"))
KERN_PAIRS = "AFKLPRTVWYafkrtvwy.,-ceosa"


# ==========================================================================
# paints
# ==========================================================================
S = 4  # supersampling for shape coverage
BAYER = (0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5)


def grad(stops, y0=None, h=None, mode="smooth"):
    """A vertical gradient: stops [(t, colour)] over rows y0..y0+h (the
    shape's own rows when None). mode: smooth (one colour per row), steps:N
    (N flat bands) or dither (an ordered 4x4 blend of the two nearest stops)."""
    return ("grad", tuple(stops), y0, h, mode)


def grad_row(stops, t):
    t = min(1.0, max(0.0, t))
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            return mix(c0, c1, 0 if t1 == t0 else (t - t0) / (t1 - t0))
    return q(stops[-1][1])


def _pair(stops, t):
    t = min(1.0, max(0.0, t))
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            return q(c0), q(c1), 0 if t1 == t0 else (t - t0) / (t1 - t0)
    return q(stops[-1][1]), q(stops[-1][1]), 0


def _bayer(w, h, x0, y0):
    tile = Image.new("L", (4, 4))
    tile.putdata([v * 16 + 8 for v in BAYER])
    big = Image.new("L", (w + 4, h + 4))
    for yy in range(0, h + 4, 4):
        for xx in range(0, w + 4, 4):
            big.paste(tile, (xx, yy))
    return big.crop((x0 % 4, y0 % 4, x0 % 4 + w, y0 % 4 + h))


# ==========================================================================
# the canvas: layout pixels in, logical pixels drawn, RGB565 out
# ==========================================================================
def _rad(w, h, r, i):
    return max(0, min(r * S, (min(w, h) * S - 1 - 2 * i) / 2 - 1))


class Cv:
    """A surface laid out in W x H layout pixels and drawn at W/k x H/k.
    `aa` False thresholds every shape's coverage: stepped pixel corners."""

    def __init__(self, w, h, bg, k=1, aa=True):
        self.W, self.H, self.k, self.aa = w, h, k, aa
        self.img = Image.new("RGB", (w // k, h // k), q(bg))
        self.clip = None

    def L(self, v):
        return int(round(v / self.k))

    def box(self, x, y, w, h):
        x0, y0 = self.L(x), self.L(y)
        return x0, y0, max(1, self.L(x + w) - x0), max(1, self.L(y + h) - y0)

    def _src(self, c, box, gy, gh):
        bx0, by0, bx1, by1 = box
        w, h = bx1 - bx0, by1 - by0
        if isinstance(c, tuple) and c and c[0] == "grad":
            _, stops, sy0, sh, mode = c
            sy0 = gy if sy0 is None else self.L(sy0)
            sh = gh if sh is None else max(1, self.L(sh))
            ts = [(yy - sy0) / max(1, sh - 1) for yy in range(by0, by1)]
            if mode.startswith("steps"):
                n = int(mode.split(":")[1])
                ts = [min(n - 1, int(t * n)) / (n - 1) for t in ts]
            if mode == "dither":
                pairs = [_pair(stops, t) for t in ts]
                a = Image.new("RGB", (1, h))
                a.putdata([p[0] for p in pairs])
                b = Image.new("RGB", (1, h))
                b.putdata([p[1] for p in pairs])
                f = Image.new("L", (1, h))
                f.putdata([round(p[2] * 255) for p in pairs])
                a, b, f = (im.resize((w, h), Image.NEAREST) for im in (a, b, f))
                m = ImageChops.subtract(f, _bayer(w, h, bx0, by0)).point(lambda v: 255 if v else 0)
                return Image.composite(b, a, m)
            src = Image.new("RGB", (1, h))
            src.putdata([grad_row(stops, t) for t in ts])
            return src.resize((w, h), Image.NEAREST)
        return Image.new("RGB", (w, h), q(c))

    def _blend(self, x, y, mask, c, alpha=1.0, gy=None, gh=None):
        """Logical coordinates."""
        if alpha < 1:
            mask = mask.point([round(a * alpha) for a in range(256)])
        w, h = mask.size
        cx0, cy0, cx1, cy1 = self.clip or (0, 0, self.img.width, self.img.height)
        bx0, by0 = max(x, cx0, 0), max(y, cy0, 0)
        bx1, by1 = min(x + w, cx1, self.img.width), min(y + h, cy1, self.img.height)
        if bx1 <= bx0 or by1 <= by0:
            return
        mask = mask.crop((bx0 - x, by0 - y, bx1 - x, by1 - y))
        box = (bx0, by0, bx1, by1)
        dst = self.img.crop(box)
        src = self._src(c, box, y if gy is None else gy, h if gh is None else gh)
        self.img.paste(Image.composite(src, dst, mask).point(LUT565), box)

    def set_clip(self, x, y, w, h):
        x0, y0, wl, hl = self.box(x, y, w, h)
        self.clip = (x0, y0, x0 + wl, y0 + hl)

    def rect(self, x, y, w, h, c, alpha=1.0):
        x0, y0, wl, hl = self.box(x, y, w, h)
        self._blend(x0, y0, Image.new("L", (wl, hl), 255), c, alpha)

    def _rmask(self, w, h, r, inset=0.0, corners=None):
        m = Image.new("L", (w * S, h * S), 0)
        i = inset * S
        ImageDraw.Draw(m).rounded_rectangle((i, i, w * S - 1 - i, h * S - 1 - i),
                                            radius=_rad(w, h, r - inset, i), fill=255,
                                            corners=corners)
        return levels(m.resize((w, h), Image.BOX), 16 if self.aa else 2)

    def rmask(self, w, h, r, corners=None):
        """A mask in logical pixels for a w x h layout box."""
        _, _, wl, hl = self.box(0, 0, w, h)
        return self._rmask(wl, hl, r / self.k, corners=corners)

    def rrect(self, x, y, w, h, r, c, alpha=1.0, corners=None):
        x0, y0, wl, hl = self.box(x, y, w, h)
        self._blend(x0, y0, self._rmask(wl, hl, r / self.k, corners=corners), c, alpha)

    def ring(self, x, y, w, h, r, t, c, alpha=1.0, inset=0.0, corners=None):
        x0, y0, wl, hl = self.box(x, y, w, h)
        k = self.k
        r, t, inset = r / k, max(t / k, 1 if not self.aa else 0), inset / k
        m = Image.new("L", (wl * S, hl * S), 0)
        d = ImageDraw.Draw(m)
        o = inset * S
        d.rounded_rectangle((o, o, wl * S - 1 - o, hl * S - 1 - o), radius=_rad(wl, hl, r - inset, o),
                            fill=255, corners=corners)
        ti = (inset + t) * S
        d.rounded_rectangle((ti, ti, wl * S - 1 - ti, hl * S - 1 - ti),
                            radius=_rad(wl, hl, r - inset - t, ti), fill=0, corners=corners)
        self._blend(x0, y0, levels(m.resize((wl, hl), Image.BOX), 16 if self.aa else 2), c, alpha)

    def bevel(self, x, y, w, h, hi, lo, t=1):
        """A 1 logical px raised edge: light top and left, dark bottom and right."""
        x0, y0, wl, hl = self.box(x, y, w, h)
        for c, rects in ((hi, ((x0, y0, wl, t), (x0, y0, t, hl))),
                         (lo, ((x0, y0 + hl - t, wl, t), (x0 + wl - t, y0, t, hl)))):
            for rx, ry, rw, rh in rects:
                self._blend(rx, ry, Image.new("L", (rw, rh), 255), c)

    def frost(self, x, y, w, h, r, blur, tint, alpha, corners=None):
        """Glass over a static backdrop: blur the pixels below once, then tint."""
        x0, y0, wl, hl = self.box(x, y, w, h)
        blur = max(1, blur // self.k)
        box = (max(0, x0), max(0, y0), min(self.img.width, x0 + wl), min(self.img.height, y0 + hl))
        pad = blur * 2
        big = self.img.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                             min(self.img.width, box[2] + pad), min(self.img.height, box[3] + pad)))
        big = big.filter(ImageFilter.GaussianBlur(blur))
        ox, oy = box[0] - max(0, box[0] - pad), box[1] - max(0, box[1] - pad)
        bl = big.crop((ox, oy, ox + box[2] - box[0], oy + box[3] - box[1]))
        m = self._rmask(wl, hl, r / self.k, corners=corners).crop(
            (box[0] - x0, box[1] - y0, box[2] - x0, box[3] - y0))
        self.img.paste(Image.composite(bl, self.img.crop(box), m).point(LUT565), box)
        self.rrect(x, y, w, h, r, tint, alpha, corners)

    def shadow(self, x, y, w, h, r, spec, k=1.0, corners=None):
        """spec: ("soft", colour, alpha, blur, dy) or ("hard", colour, dx, dy)."""
        if spec[0] == "hard":
            _, c, dx, dy = spec
            self.rrect(x + dx, y + dy, w, h, r, c, corners=corners)
            return
        _, c, a, blur, dy = spec
        blur, dy, a = max(2, int(blur * k)), int(dy * k), a * k
        x0, y0, wl, hl = self.box(x, y + dy, w, h)
        bl = max(1, blur // self.k)
        pad = bl * 3
        m = Image.new("L", (wl + 2 * pad, hl + 2 * pad), 0)
        m.paste(self._rmask(wl, hl, r / self.k, corners=corners), (pad, pad))
        m = levels(m.filter(ImageFilter.GaussianBlur(bl)), 16)
        self._blend(x0 - pad, y0 - pad, m, c, a)

    def shape(self, x, y, w, h, fn, c, alpha=1.0, thin=False):
        """fn(draw, s) draws in layout units times s on a supersampled canvas."""
        x0, y0, wl, hl = self.box(x, y, w, h)
        m = Image.new("L", (wl * S, hl * S), 0)
        fn(ImageDraw.Draw(m), S / self.k)
        m = m.resize((wl, hl), Image.BOX)
        if self.aa:
            m = levels(m, 16)
        else:
            m = m.point(lambda a: 255 if a >= (64 if thin else 112) else 0)
        self._blend(x0, y0, m, c, alpha)

    def text(self, x, base, s, face, c, align="left", maxw=None, shadow=None):
        """shadow: (colour, alpha, dx, dy) in logical pixels, drawn first."""
        if maxw and face.width(s) > maxw:
            while s and face.width(s + "…") > maxw:
                s = s[:-1]
            s = s.rstrip() + "…"
        wl = face.lwidth(s)
        xl, bl = self.L(x), self.L(base)
        if align == "center":
            xl -= wl // 2
        elif align == "right":
            xl -= wl
        for sh in ([shadow] if shadow else []) + [None]:
            px, prev = xl, None
            for ch in s:
                if prev:
                    px += face.kerning(prev, ch)
                m, bx, by, adv = face.glyph(ch)
                if m:
                    if sh:
                        self._blend(px + bx + sh[2], bl + by + sh[3], m, sh[0], sh[1])
                    else:
                        self._blend(px + bx, bl + by, m, c)
                px += adv
                prev = ch
        return wl * self.k

    def paste(self, im, x, y, w=None, r=0):
        """An image laid out at w x w layout pixels (its own size when None)."""
        x0, y0, wl, hl = self.box(x, y, w or im.width * self.k, w or im.height * self.k)
        if im.size != (wl, hl):
            im = im.resize((wl, hl), Image.NEAREST)
        box = (x0, y0, x0 + wl, y0 + hl)
        m = self._rmask(wl, hl, r / self.k) if r else Image.new("L", (wl, hl), 255)
        dst = self.img.crop(box)
        self.img.paste(Image.composite(im, dst, m).point(LUT565), box)

    def image(self):
        im = self.img.point(LUT565)
        if self.k != 1:
            im = im.resize((self.W, self.H), Image.NEAREST)
        return im

    def save(self, name, what, x3=False):
        im = self.image()
        im.save(os.path.join(OUT, name + ".png"), optimize=True)
        INDEX.append(f"{name}.png — {what}; {self.W}x{self.H}")
        if x3:
            im.resize((self.W * 3, self.H * 3), Image.NEAREST).save(
                os.path.join(OUT, name + "@3x.png"), optimize=True)
            INDEX.append(f"{name}@3x.png — same, enlarged 3x (nearest)")


# ==========================================================================
# icons: 24-unit line glyphs, drawn at any pixel size
# ==========================================================================
def icon(cv, name, x, y, px, c, stroke=2.0, alpha=1.0):
    k = px / 24.0

    def fn(d, s):
        u = k * s
        sw = max(1, round(stroke * u))

        def P(*pts):
            return [(a * u, b * u) for a, b in pts]

        def line(*pts):
            d.line(P(*pts), fill=255, width=sw, joint="curve")
            for p in P(pts[0], pts[-1]):
                d.ellipse((p[0] - sw / 2, p[1] - sw / 2, p[0] + sw / 2, p[1] + sw / 2), fill=255)

        def rr(x0, y0, x1, y1, r, fill=False):
            if fill:
                d.rounded_rectangle((x0 * u, y0 * u, x1 * u, y1 * u), r * u, fill=255)
            else:
                d.rounded_rectangle((x0 * u, y0 * u, x1 * u, y1 * u), r * u, outline=255, width=sw)

        def arc(cx, cy, r, a0, a1):
            d.arc(((cx - r) * u, (cy - r) * u, (cx + r) * u, (cy + r) * u), a0, a1, fill=255, width=sw)

        def dot(cx, cy, r):
            d.ellipse(((cx - r) * u, (cy - r) * u, (cx + r) * u, (cy + r) * u), fill=255)

        if name == "wifi":
            for r in (10, 6.5):
                arc(12, 19, r, 225, 315)
            dot(12, 18.5, 1.9)
        elif name == "battery":
            rr(2, 7, 20, 17, 3)
            rr(5, 10, 14, 14, 1, fill=True)
            rr(21, 10, 23, 14, 1, fill=True)
        elif name == "play":
            d.polygon(P((7, 4.5), (19.5, 12), (7, 19.5)), fill=255)
        elif name == "projects":
            rr(3, 5, 19, 21, 3)
            line((10, 14), (20, 4))
            line((19, 3), (21, 5))
        elif name == "brush":
            line((20, 4), (11, 13))
            d.ellipse(P((5, 13), (12, 20)), fill=255)
            d.polygon(P((5, 17), (4, 21), (8, 20)), fill=255)
        elif name == "book":
            line((12, 6), (12, 20))
            d.line(P((12, 6), (8, 4.5), (3, 5), (3, 18), (8, 17.5), (12, 20)), fill=255, width=sw, joint="curve")
            d.line(P((12, 6), (16, 4.5), (21, 5), (21, 18), (16, 17.5), (12, 20)), fill=255, width=sw, joint="curve")
        elif name == "folder":
            d.line(P((3, 18), (3, 6), (4, 5), (9, 5), (11, 7.5), (20, 7.5), (21, 8.5), (21, 18), (20, 19), (4, 19), (3, 18)),
                   fill=255, width=sw, joint="curve")
        elif name == "calc":
            rr(4.5, 2.5, 19.5, 21.5, 3)
            rr(8, 6, 16, 9.5, 1, fill=True)
            for cx in (8.75, 12, 15.25):
                for cy in (13.5, 17.5):
                    dot(cx, cy, 1.25)
        elif name == "download":
            line((12, 3), (12, 15))
            line((7, 10), (12, 15), (17, 10))
            line((4, 15), (4, 20), (20, 20), (20, 15))
        elif name == "close":
            line((6, 6), (18, 18))
            line((18, 6), (6, 18))
        elif name == "cart":
            d.line(P((5, 21), (5, 3), (16, 3), (19, 6), (19, 21), (5, 21)), fill=255, width=sw, joint="curve")
            rr(8, 7, 16, 13, 1)
            line((9, 17), (15, 17))
        elif name == "note":
            line((9, 18), (9, 5), (20, 3), (20, 16))
            d.ellipse(P((3.5, 15), (9.5, 21)), fill=255)
            d.ellipse(P((14.5, 13), (20.5, 19)), fill=255)
        elif name == "trash":
            line((3, 6), (21, 6))
            line((9, 6), (9, 3), (15, 3), (15, 6))
            d.line(P((5.5, 6), (6.5, 21), (17.5, 21), (18.5, 6)), fill=255, width=sw, joint="curve")
        elif name == "enter":
            line((19, 5), (19, 14), (6, 14))
            line((10, 10), (6, 14), (10, 18))
        elif name == "backspace":
            d.line(P((8, 5), (21, 5), (21, 19), (8, 19), (2, 12), (8, 5)), fill=255, width=sw, joint="curve")
            line((11, 9), (17, 15))
            line((17, 9), (11, 15))
        elif name == "search":
            arc(10.5, 10.5, 6.5, 0, 360)
            line((15.5, 15.5), (20.5, 20.5))
        elif name == "chev":
            line((9, 5), (16, 12), (9, 19))
        elif name == "back":
            line((15, 5), (8, 12), (15, 19))

    cv.shape(x, y, px, px, fn, c, alpha, thin=True)


def moy(cv, x, y, px, body, eye):
    """Moy: a rounded ghost with two eyes and three feet, as a flat glyph."""
    def fn(d, s):
        u = px / 24.0 * s
        d.rounded_rectangle((3 * u, 2.5 * u, 21 * u, 20 * u), 8 * u, fill=255)
        for i in range(3):
            cx = (6.5 + i * 5.5) * u
            d.ellipse((cx - 3.0 * u, 16.5 * u, cx + 3.0 * u, 22 * u), fill=255)
    cv.shape(x, y, px, px, fn, body)

    def eyes(d, s):
        u = px / 24.0 * s
        for cx in (9, 15):
            d.rounded_rectangle(((cx - 1.4) * u, 8 * u, (cx + 1.4) * u, 13.5 * u), 1.4 * u, fill=255)
    cv.shape(x, y, px, px, eyes, eye)


# ==========================================================================


# ==========================================================================
# content: covers stay pixel art, scaled by whole numbers
# ==========================================================================
CROPS = {
    "star_catcher": {64: (43, 52)}, "sky_run": {64: (8, 44)}, "hop_quest": {64: (52, 36)},
    "pixel_pet": {64: (24, 50)}, "harpoon_pop": {64: (18, 64)}, "brick_siege": {64: (2, 20)},
    "bullet_storm": {64: (32, 8)}, "letter_blitz": {64: (2, 18)}, "tiny_runner": {64: (0, 60)},
}


def _src(name):
    return os.path.join(ROOT, "system_carts", f"moybyte.{name}.moy", "cover.png")


def cover(name, n=64, scale=1):
    img = Image.open(_src(name)).convert("RGB")
    x, y = CROPS.get(name, {}).get(n, ((128 - n) // 2, (128 - n) // 2))
    c = img.crop((x, y, x + n, y + n)).point(LUT565)
    return c.resize((n * scale, n * scale), Image.NEAREST) if scale != 1 else c


# ==========================================================================
# the theme model: every field the renderer reads, with its default
# ==========================================================================
BASE = dict(
    mode="light", danger=None, hi=None, lo=None, edges="flat",
    fam="inter", sizes={}, w_body=400, w_med=500, w_semi=600, w_bold=700,
    render=dict(text="aa", shapes="smooth", scale=1),
    focus=dict(style="ring", card=None, small=None, color="accent", ink="keep", outline=None,
               ring=dict(w=2.5, gap=3, alpha=1.0), plate=dict(alpha=(0.14, 0.22)),
               lift=dict(grow=5, rise=3, rim=None), edge=dict(w=4), underline=dict(w=3)),
    shadow=None, shadow_back=0.55,
    win_r=12, card_r=10, key_r=10, win_corners=None, dim_neighbours=0,
    home_bg=None, backdrop={},
    win=dict(header="plain", h=44, align="left", ink=None, ink_back=None, tshadow=None,
             controls="close", grad=None, grad_back=None, fill=None, fill_back=None,
             frame=None, frame_back=None, body=None, rule=1, icon=True),
    current=dict(style="bar", color="value", alpha=(0.14, 0.24)), sel=None, sel_ink=None, sel_back=None,
    bar=dict(edge="top", h=40, style="flat", fill=None, alpha=1.0, line=None, grad=None,
             sysbtn="tile", sys=None, chips="tab", chip_cur=None, app_title=None,
             ink=None, dim=None, tray=None, clock="right"),
    hhbar=dict(style="none", h=28, fill=None, line=None, ink=None, dim=None, sys=None),
    tabs=dict(style="bar", pill=None, pill_ink=None),
    launcher="column", dock=dict(fill="#ffffff", alpha=0.32, frost=True, ring=("#ffffff", 0.45), r=20),
    tiles=None, tile_r=12, tile_fill=None, tile_outline=None, tile_ink=None, tile_grad=True,
    label=dict(ink=None, shadow=None),
    on_backdrop=dict(ink=None, dim=None, shadow=None),
    sidebar=None, places_panel=None, list_bg=None, zebra=None, colhead=None,
    buttons={}, app_buttons={},
    lcd=None, lcd_border=None, lcd_bevel=None, lcd_ink=None, lcd_dim=None,
    keycap=dict(fill=None, alpha=1.0, border=None, grad=None, ink=None, r=4, bevel=False),
    layout=dict(files=(112, 54, 548, 534), calc=(648, 96, 344, 488)),
)
NESTED = ("render", "win", "bar", "hhbar", "tabs", "dock", "label", "on_backdrop", "keycap", "layout",
          "current")


def theme(**kw):
    T = copy.deepcopy(BASE)
    for k, v in kw.items():
        if k in NESTED:
            T[k].update(v)
        elif k == "focus":
            for fk, fv in v.items():
                if isinstance(fv, dict):
                    T[k][fk].update(fv)
                else:
                    T[k][fk] = fv
        else:
            T[k] = v
    T.setdefault("value", T["accent"])
    return T


# Drawn backdrops. Units: x and widths in thousandths of the surface's width,
# y, heights and radii in thousandths of its height. A paint is a colour or a
# list of colours spread top to bottom over the shape's own rows; "mode" is
# smooth (default), steps:N or dither.
BD_A = {"desk": [{"k": "circle", "at": [801, 850, 433], "p": "#191c3e"},
                 {"k": "circle", "at": [889, 733, 433], "p": "#0f1127"}]}
BD_B = {"desk": [{"k": "circle", "at": [918, 1000, 500], "p": "#e2e5ee"},
                 {"k": "circle", "at": [88, 983, 350], "p": "#e5e3f1"}]}
BD_C = {
    "home": [{"k": "fill", "p": "#ffd23f"},
             {"k": "circle", "at": [500, 470, 520], "p": "#ffe066"},
             {"k": "circle", "at": [0, 470, 230], "p": "#7be0b4"},
             {"k": "circle", "at": [1000, 470, 200], "p": "#ff8a6b"}],
    "desk": [{"k": "fill", "p": "#fff3dc"},
             {"k": "rrect", "at": [586, 73, 234, 150, 75], "p": "#ffd23f"},
             {"k": "pie", "at": [996, 433, 233, 180, 270], "p": "#7cc8ff"},
             {"k": "circle", "at": [889, 900, 350], "p": "#ff8a6b"},
             {"k": "circle", "at": [20, 1000, 283], "p": "#7be0b4"}],
}
_BLISS = [
    {"k": "band", "at": [0, 720], "p": ["#1b5ad3", "#3a80e2", "#86b9ef"]},
    {"k": "ellipse", "at": [270, 170, 120, 48], "p": "#ffffff", "a": 0.85},
    {"k": "ellipse", "at": [345, 140, 70, 60], "p": "#ffffff", "a": 0.85},
    {"k": "ellipse", "at": [760, 250, 150, 40], "p": "#ffffff", "a": 0.7},
    {"k": "ellipse", "at": [820, 225, 60, 45], "p": "#ffffff", "a": 0.7},
    {"k": "wave", "at": [[0, 600], [260, 560], [560, 610], [820, 540], [1000, 580]],
     "p": ["#8fd04e", "#5aa12a"]},
    {"k": "wave", "at": [[0, 800], [220, 690], [480, 650], [740, 720], [1000, 820]],
     "p": ["#52a52a", "#245f12"]},
]
_DUNES = [
    {"k": "band", "at": [0, 1000], "p": ["#2f3f9e", "#6b62cc", "#d99ac4", "#f4c3a2"]},
    {"k": "wave", "at": [[0, 560], [300, 470], [650, 560], [1000, 450]], "p": ["#9a8be4", "#6e5fc9"]},
    {"k": "wave", "at": [[0, 690], [330, 620], [700, 700], [1000, 610]], "p": ["#6252bd", "#43389c"]},
    {"k": "wave", "at": [[0, 840], [380, 760], [760, 840], [1000, 780]], "p": ["#382e86", "#231c5c"]},
]
_PICO = [
    {"k": "fill", "p": ["#1d2b53", "#1d2b53", "#7e2553"], "mode": "dither"},
    {"k": "circle", "at": [860, 820, 260], "p": "#7e2553"},
    {"k": "circle", "at": [860, 820, 200], "p": ["#ff77a8", "#7e2553"], "mode": "dither"},
]
_PICO_HOME = [
    {"k": "fill", "p": ["#1d2b53", "#1d2b53", "#7e2553"], "mode": "dither"},
]
_COPPER = [
    {"k": "fill", "p": ["#001a44", "#0055aa", "#5a4f9e", "#a8432a"], "mode": "steps:16"},
]
_ADW = [
    {"k": "fill", "p": "#1e1b2e"},
    {"k": "circle", "at": [930, 950, 520], "p": "#1a5fb4"},
    {"k": "circle", "at": [760, 1080, 380], "p": "#3584e4"},
    {"k": "circle", "at": [120, 120, 260], "p": "#26213a"},
    {"k": "circle", "at": [620, 200, 90], "p": "#62a0ea", "a": 0.9},
]
_ADW_HOME = [
    {"k": "fill", "p": "#1e1b2e"},
    {"k": "circle", "at": [1040, 1060, 330], "p": "#1a5fb4"},
    {"k": "circle", "at": [930, 1180, 220], "p": "#3584e4"},
    {"k": "circle", "at": [40, 60, 300], "p": "#26213a"},
]

P8 = dict(black="#000000", navy="#1d2b53", plum="#7e2553", green="#008751", brown="#ab5236",
          dgrey="#5f574f", lgrey="#c2c3c7", white="#fff1e8", red="#ff004d", orange="#ffa300",
          yellow="#ffec27", lime="#00e436", blue="#29adff", lav="#83769c", pink="#ff77a8",
          peach="#ffccaa")

A = theme(
    key="moy", name="Moy Night (dark)", mode="dark",
    bg="#0f1127", surface="#181b38", raised="#23274b", sunk="#0b0d1e", line="#2b2f57",
    dim="#a1a6cc", text="#eef0fb", accent="#b6a8f7", value_ink="#15123a",
    shadow=("soft", "#000000", 0.55, 14, 6), dim_neighbours=0.30, fam="inter", backdrop=BD_A,
    win=dict(header="bar", frame=(1, "#2b2f57"), frame_back=(1, "#2b2f57")),
    bar=dict(fill="#0b0d1e", alpha=0.85, sys=("#23274b", "#b6a8f7", "#23274b"), chip_cur="#23274b"),
    tile_fill="#23274b",
    buttons={"tonal": {"fill": "#2e3360"}},
    lcd="#0b0d1e", lcd_ink="#b6a8f7",
    focus=dict(style="edge", card="lift", small="underline", lift=dict(rim=("accent", 0.6))),
    current=dict(style="plate"),
)
B = theme(
    key="moy", name="Moy Day (light, the default)",
    bg="#eceef3", surface="#ffffff", raised="#f4f5f8", sunk="#e3e6ed", line="#e2e5ec",
    dim="#5f6478", text="#15161d", accent="#4c3fd6", value_ink="#ffffff",
    shadow=("soft", "#1b2040", 0.18, 16, 8), fam="plex", backdrop=BD_B,
    win=dict(header="bar"),
    bar=dict(fill="#ffffff", alpha=0.92, line=(1, "#e2e5ec"), sys=("#f4f5f8", "#4c3fd6", "#f4f5f8"),
             chip_cur="#f4f5f8"),
    hhbar=dict(sys=("#ffffff", "#4c3fd6", "#ffffff")),
    tile_fill="#ffffff",
    buttons={"tonal": {"fill": "#e3e6ed"}},
    lcd="#f1f2f6", lcd_ink="#4c3fd6",
    focus=dict(style="edge", card="lift", small="underline", lift=dict(rim=("#ffffff", 1.0))),
    current=dict(style="plate"),
)
C = theme(
    key="blocks", name="Blocks (light, colour-blocked, for kids and makers)",
    bg="#fff3dc", surface="#ffffff", raised="#fff8ec", sunk="#f6ead2", line="#eadcc0",
    dim="#5b5167", text="#1a1530", accent="#1a1530", value="#6a2cff", value_ink="#ffffff",
    win_r=16, card_r=14, key_r=12, fam="atkinson",
    focus=dict(style="invert", card="ring", small="invert", ring=dict(w=3)), w_semi=700, w_bold=800,
    home_bg="#ffd23f", backdrop=BD_C,
    win=dict(header="fill", frame=(2, "#1a1530"), frame_back=(2, "#5b5167"), rule=2),
    bar=dict(fill="#ffffff", line=(2, "#1a1530"), sys=("#6a2cff", "#ffffff", "#6a2cff"),
             chip_cur="#f6ead2"),
    tiles={"play": "#ff6b4a", "projects": "#b79cff", "brush": "#ffd23f", "book": "#7be0b4",
           "folder": "#7cc8ff", "calc": "#ff9a62", "download": "#9be36b",
           "cart": "#ff6b4a", "note": "#7be0b4", "trash": "#e8dcc6"},
    tile_outline=(2, "#1a1530"), tile_ink="#1a1530", tile_grad=False,
    buttons={"normal": {"fill": "#f4ecdc"}, "tonal": {"fill": "#ffd23f", "ink": "#1a1530"}},
    lcd="#c9f2dd", lcd_border=(2, "#1a1530"), lcd_ink="#1a1530",
    keycap=dict(fill="#ffffff", border=(1.25, "#1a1530")),
)
_XP_BTN = [(0, "#ffffff"), (0.55, "#f3f2ec"), (1, "#d9d5c7")]
LUNA = theme(
    key="luna", name="Luna (in the style of Windows XP)",
    bg="#3a6ea5", surface="#ece9d8", raised="#ffffff", sunk="#d6d2c2", line="#aca899",
    dim="#4d4b44", text="#000000", accent="#c86400", value="#2f63d6", value_ink="#ffffff",
    focus=dict(style="invert", card="ring", ring=dict(w=3, gap=0)),
    win_r=8, card_r=4, key_r=3, win_corners=(True, True, False, False),
    shadow=("soft", "#000000", 0.35, 6, 4), shadow_back=0.6, fam="dejavu", w_med=400, w_semi=700,
    w_bold=700, backdrop={"home": _BLISS, "desk": _BLISS},
    sizes={"hh": {"tab": 12, "status": 11, "meta": 11, "hint": 11, "key": 10, "title": 18},
           "dk": {"row": 14, "meta": 13, "chip": 14, "status": 14, "label": 13, "col": 12,
                  "wtitle": 16}},
    win=dict(header="grad", h=40, ink="#ffffff", ink_back="#e1e8fa",
             tshadow=("#0a1f6d", 0.85, 1, 1), controls="xp",
             grad=[(0, "#4192fa"), (0.12, "#2a76f0"), (0.35, "#0b5fe3"), (0.85, "#0554d6"),
                   (1, "#0846bb")],
             grad_back=[(0, "#b0c6f2"), (0.35, "#8ba8e8"), (1, "#7a96df")],
             frame=(4, "#0846bb"), frame_back=(4, "#7a96df"), rule=0),
    current=dict(style="fill"), sel="#316ac5", sel_ink="#ffffff", sel_back="#d6d2c2",
    bar=dict(edge="bottom", style="grad", sysbtn="start", chips="button",
             grad=[(0, "#3f8cf3"), (0.1, "#2b70e0"), (0.5, "#245edb"), (0.9, "#1f50c8"),
                   (1, "#1941a5")],
             ink="#ffffff", dim="#ffffff", tray=[(0, "#1aa0f0"), (1, "#0e78d6")]),
    hhbar=dict(style="grad", h=26, ink="#ffffff", dim="#dce8ff"),
    tabs=dict(style="pill", pill="#1b4ab0", pill_ink="#ffffff"),
    tiles={"play": "#3ca83a", "projects": "#e9a92b", "brush": "#d9473a", "book": "#3b7bd8",
           "folder": "#e9b83a", "calc": "#6f84a3", "download": "#2a9fd6",
           "cart": "#3ca83a", "note": "#d9473a", "trash": "#7b8ba3"},
    tile_r=8, label=dict(ink="#ffffff", shadow=("#000000", 0.85, 1, 1)),
    on_backdrop=dict(ink="#ffffff", dim="#eef4ff", shadow=("#00213f", 0.9, 1, 1)),
    sidebar=[(0, "#7ba2e7"), (1, "#6375d6")],
    places_panel=dict(fill="#ffffff", head=[(0, "#ffffff"), (1, "#c6d3f7")], head_ink="#215dc6"),
    list_bg="#ffffff", colhead=dict(fill="#ebeadb", line="#d6d2c2"),
    buttons={k: {"fill": _XP_BTN, "border": (1, "#003c74")}
             for k in ("normal", "tonal", "danger", "toggle")},
    app_buttons={"calc": {"normal": {"ink": "#1a3fcf"}, "tonal": {"ink": "#c4231b"},
                          "danger": {"ink": "#c4231b"}}},
    lcd="#ffffff", lcd_border=(1, "#7f9db9"), lcd_ink="#000000",
    keycap=dict(grad=_XP_BTN, border=(1, "#003c74"), ink="#000000", r=3),
    layout=dict(files=(112, 14, 548, 534), calc=(648, 50, 344, 488)),
)
COAST = theme(
    key="coast", name="Coast (in the style of macOS)",
    bg="#d9d4ee", surface="#ffffff", raised="#ffffff", sunk="#e9e9ee", line="#dcdce2",
    dim="#6e6e76", text="#1d1d1f", accent="#3a86ff", value="#0a6cff", value_ink="#ffffff",
    focus=dict(style="invert", card="ring", small="ring", ring=dict(w=3.5, gap=0, alpha=0.6)),
    win_r=12, card_r=12, key_r=99,
    shadow=("soft", "#0b0a2a", 0.38, 22, 14), shadow_back=0.5, fam="inter",
    backdrop={"home": _DUNES, "desk": _DUNES},
    sizes={"dk": {"result": 52}}, w_result=300,
    win=dict(align="center", controls="lights", body="#f6f6f8", icon=False, ink_back="#a1a1a8"),
    current=dict(style="fill"), sel="#0a6cff", sel_ink="#ffffff", sel_back="#dcdce1",
    bar=dict(h=30, style="glass", fill="#ffffff", alpha=0.55, sysbtn="glyph", chips="none",
             app_title="Calc"),
    hhbar=dict(style="glass", h=22, dim="#2c2c33"),
    tabs=dict(style="pill", pill="#0a6cff", pill_ink="#ffffff"),
    launcher="dock",
    tiles={"play": "#34c759", "projects": "#ff9f0a", "brush": "#ff375f", "book": "#5e5ce6",
           "folder": "#0a84ff", "calc": "#8e8e93", "download": "#32ade6",
           "cart": "#34c759", "note": "#ff375f", "trash": "#8e8e93"},
    on_backdrop=dict(ink="#ffffff", dim="#f1edff", shadow=("#120c3a", 0.55, 0, 1)),
    sidebar="vibrancy", list_bg="#ffffff", zebra="#f4f4f7",
    buttons={"normal": {"fill": "#ececf0"}, "tonal": {"fill": "#ececf0"}},
    app_buttons={"calc": {"normal": {"shape": "circle", "fill": "#e4e4e9"},
                          "danger": {"shape": "circle", "fill": "#cfcfd6"},
                          "tonal": {"shape": "circle", "fill": "#ff9f0a", "ink": "#ffffff"},
                          "primary": {"shape": "circle", "fill": "#ff9f0a"}}},
    lcd_ink="#1d1d1f",
    keycap=dict(fill="#ffffff", alpha=0.28, ink="#ffffff", r=5),
    layout=dict(files=(40, 50, 560, 460), calc=(630, 66, 344, 440)),
)
PICO = theme(
    key="pico", name="Pico (in the style of Picotron)",
    bg=P8["navy"], surface=P8["white"], raised=P8["lgrey"], sunk=P8["lgrey"], line=P8["black"],
    dim=P8["dgrey"], text=P8["black"], accent=P8["red"], value=P8["blue"], value_ink=P8["black"],
    danger=None, render=dict(text="crisp", shapes="stepped", scale=2), fam="galmuri",
    sizes={"hh": {"tab": 10, "status": 10, "title": 24, "meta": 10, "key": 8, "hint": 10,
                  "hkey": 12, "hop": 12, "hres": 24, "htape": 10, "htitle": 12},
           "dk": {r: 8 for r in ("chip", "status", "label", "wtitle", "row", "col", "meta", "tape")}
           | {"keypad": 12, "op": 12, "result": 24, "wtitle": 10}},
    focus=dict(style="invert", card="invert", ring=dict(w=2, gap=2)),
    win_r=4, card_r=4, key_r=4, shadow=("hard", "#000000", 2, 2),
    shadow_back=1.0, backdrop={"home": _PICO_HOME, "desk": _PICO},
    win=dict(header="fill", h=28, fill_back=P8["lgrey"], frame=(2, "#000000"),
             frame_back=(2, "#000000"), rule=2, ink="#000000", icon=False),
    bar=dict(h=24, fill="#000000", ink=P8["white"], dim=P8["lgrey"], sysbtn="glyph",
             chip_cur=P8["dgrey"]),
    hhbar=dict(style="flat", h=24, fill="#000000", ink=P8["white"], dim=P8["lgrey"]),
    tiles={"play": P8["lime"], "projects": P8["orange"], "brush": P8["pink"], "book": P8["peach"],
           "folder": P8["yellow"], "calc": P8["lgrey"], "download": P8["blue"],
           "cart": P8["lime"], "note": P8["pink"], "trash": P8["lgrey"]},
    tile_r=4, tile_outline=(2, "#000000"), tile_ink="#000000", tile_grad=False,
    label=dict(ink=P8["white"], shadow=("#000000", 1.0, 1, 1)),
    on_backdrop=dict(ink=P8["white"], dim=P8["lgrey"], shadow=("#000000", 1.0, 1, 1)),
    sidebar=P8["lgrey"], list_bg=P8["white"],
    buttons={"normal": {"fill": P8["lgrey"]}, "tonal": {"fill": P8["orange"], "ink": "#000000"},
             "danger": {"fill": P8["pink"], "ink": "#000000"}},
    lcd="#000000", lcd_ink=P8["lime"], lcd_dim=P8["lgrey"],
    keycap=dict(fill=P8["lgrey"], ink="#000000", r=2),
)
WB = theme(
    key="bench", name="Bench (in the style of Amiga Workbench 3.x)",
    bg="#aaaaaa", surface="#aaaaaa", raised="#aaaaaa", sunk="#999999", line="#000000",
    dim="#333333", text="#000000", accent="#000000", value="#6688bb", value_ink="#000000",
    hi="#ffffff", lo="#000000", edges="bevel",
    render=dict(text="crisp", shapes="stepped", scale=2), fam="petme",
    sizes={"hh": {"tab": 8, "status": 8, "title": 16, "meta": 8, "key": 8, "hint": 8,
                  "hkey": 8, "hop": 8, "hres": 16, "htape": 8, "htitle": 8},
           "dk": {r: 8 for r in ("chip", "status", "label", "wtitle", "row", "col", "meta", "tape")}
           | {"keypad": 16, "op": 16, "result": 24}},
    focus=dict(style="invert", card="invert", ring=dict(w=2, gap=2)),
    win_r=0, card_r=0, key_r=0, shadow=None,
    backdrop={"home": _COPPER, "desk": _COPPER},
    win=dict(header="fill", h=30, fill_back="#aaaaaa", ink="#000000", controls="amiga",
             frame=(6, "#6688bb"), frame_back=(6, "#aaaaaa"), rule=0, icon=False),
    current=dict(style="fill"), sel="#6688bb", sel_ink="#000000", sel_back="#6688bb",
    layout=dict(files=(176, 54, 500, 534), calc=(664, 96, 340, 488)),
    bar=dict(h=24, fill="#ffffff", line=(2, "#000000"), ink="#000000", dim="#333333",
             sysbtn="glyph", chips="none", app_title="Moybyte Workbench"),
    hhbar=dict(style="flat", h=24, fill="#ffffff", line=(2, "#000000"), ink="#000000",
               dim="#333333"),
    tiles={k: "#aaaaaa" for k in ("play", "projects", "brush", "book", "folder", "calc",
                                  "download", "cart", "note", "trash")},
    tile_r=0, tile_ink="#000000", tile_grad=False,
    label=dict(ink="#ffffff", shadow=("#000000", 1.0, 1, 1)),
    on_backdrop=dict(ink="#ffffff", dim="#ffffff", shadow=("#000000", 1.0, 1, 1)),
    list_bg="#aaaaaa",
    buttons={"tonal": {"fill": "#ffffff", "ink": "#000000"}},
    lcd="#ffffff", lcd_bevel="in", lcd_ink="#000000", lcd_dim="#333333",
    keycap=dict(fill="#aaaaaa", ink="#000000", r=0, bevel=True),
)
ADW = theme(
    key="adw", name="Adw (in the style of GNOME's Adwaita)",
    bg="#1e1b2e", surface="#ffffff", raised="#ffffff", sunk="#ebebed", line="#e0e0e2",
    dim="#5e5c64", text="#1f1f23", accent="#3584e4", value="#3584e4", value_ink="#ffffff",
    danger="#c01c28", fam="cantarell", w_med=400, w_semi=700, w_bold=700,
    sizes={"hh": {"tab": 12}},
    focus=dict(style="plate", card="ring", small="ring", ring=dict(w=2, gap=0, alpha=0.5)),
    win_r=12, card_r=12, key_r=8,
    shadow=("soft", "#000000", 0.30, 14, 4), shadow_back=0.5, backdrop={"home": _ADW_HOME, "desk": _ADW},
    win=dict(align="center", controls="adw", body="#fafafb", fill="#ffffff", fill_back="#f6f6f7",
             icon=False, frame=(1, "#d4d4d8"), frame_back=(1, "#d4d4d8")),
    current=dict(style="fill"), sel="#d8e5fa", sel_ink="#1f1f23", sel_back="#e3e3e6",
    bar=dict(h=32, fill="#000000", ink="#ffffff", dim="#c0bfbc", sysbtn="workspaces",
             chips="none", clock="center"),
    hhbar=dict(style="flat", h=24, fill="#000000", ink="#ffffff", dim="#bdbdc4"),
    tabs=dict(style="pill", pill="#3a3a40", pill_ink="#ffffff"),
    launcher="dock", dock=dict(fill="#2b2b30", alpha=0.94, frost=False, ring=None, r=22),
    tiles={"play": "#33d17a", "projects": "#f6d32d", "brush": "#e66100", "book": "#9141ac",
           "folder": "#3584e4", "calc": "#77767b", "download": "#26a269",
           "cart": "#33d17a", "note": "#e66100", "trash": "#77767b"},
    tile_r=14,
    on_backdrop=dict(ink="#ffffff", dim="#deddda", shadow=("#000000", 0.5, 0, 1)),
    sidebar="#ebebed", list_bg="#ffffff",
    buttons={"normal": {"fill": "#ececee"}, "tonal": {"fill": "#e1e1e5", "ink": "#1f1f23"}},
    lcd="#ffffff", lcd_border=(1, "#d4d4d8"), lcd_ink="#1f1f23",
    keycap=dict(fill="#ffffff", alpha=0.22, ink="#ffffff", r=5),
    layout=dict(files=(40, 50, 560, 460), calc=(630, 66, 344, 440)),
)
CONTRAST = theme(
    key="contrast", name="Contrast (dark)", mode="dark",
    bg="#000000", surface="#000000", raised="#000000", sunk="#1a1a1a", line="#ffffff",
    dim="#e6e6e6", text="#ffffff", accent="#ffe600", value="#ffffff", value_ink="#000000",
    danger="#ff8f8f", fam="atkinson", w_med=600, w_semi=700, w_bold=800,
    sizes={"dk": {"keypad": 26, "op": 28, "result": 50, "tape": 17, "wtitle": 17},
           "hh": {"hkey": 18, "hop": 19, "hres": 30, "htape": 13, "htitle": 15}},
    focus=dict(style="invert", card="ring", small="invert", ring=dict(w=3, gap=2)),
    current=dict(style="plate", alpha=(0.42, 0.40)),
    win_r=4, card_r=4, key_r=4,
    keycap=dict(border=(1.25, "#ffffff")),
    win=dict(header="fill", frame=(2, "#ffffff"), frame_back=(2, "#ffffff"), rule=2),
    bar=dict(fill="#000000", line=(2, "#ffffff"), chip_cur="#1a1a1a"),
    buttons={"normal": {"border": (2, "#ffffff")}, "tonal": {"border": (2, "#ffffff"), "fill": "#262626",
                                                              "ink": "#ffffff"},
             "danger": {"border": (2, "#ffffff")}},
    lcd="#000000", lcd_border=(2, "#ffffff"), lcd_ink="#ffffff",
)
CONTRAST_LIGHT = theme(
    key="contrast", name="Contrast (light)",
    bg="#ffffff", surface="#ffffff", raised="#ffffff", sunk="#e6e6e6", line="#000000",
    dim="#333333", text="#000000", accent="#ffe600", value="#000000", value_ink="#ffffff",
    danger="#b3261e", fam="atkinson", w_med=600, w_semi=700, w_bold=800,
    sizes=copy.deepcopy(CONTRAST["sizes"]),
    focus=dict(style="invert", card="ring", small="invert", ring=dict(w=3, gap=2),
               outline=(2, "#000000")),
    current=dict(style="plate", alpha=(0.42, 0.40)),
    win_r=4, card_r=4, key_r=4,
    keycap=dict(border=(1.25, "#000000")),
    win=dict(header="fill", frame=(2, "#000000"), frame_back=(2, "#000000"), rule=2),
    bar=dict(fill="#ffffff", line=(2, "#000000"), chip_cur="#e6e6e6"),
    buttons={"normal": {"border": (2, "#000000")},
             "tonal": {"border": (2, "#000000"), "fill": "#e6e6e6", "ink": "#000000"},
             "danger": {"border": (2, "#000000")}},
    lcd="#ffffff", lcd_border=(2, "#000000"), lcd_ink="#000000",
)
VARIANTS = (A, B, C)
RICED = (LUNA, COAST)
STYLES = (PICO, WB, ADW)
SHEET = (A, B, C, LUNA, COAST, PICO, WB, ADW, CONTRAST)

# role sizes (px), weight step and bpp. A theme's `sizes` overrides the size;
# a crisp theme draws every face at 1 bpp; a 13 px or smaller face is 2 bpp.
HH = {"tab": (13, "semi", 2), "status": (12, "med", 2), "title": (19, "bold", 4),
      "meta": (12, "body", 2), "key": (11, "semi", 2), "hint": (12, "med", 2),
      "hkey": (16, "med", 4), "hop": (17, "med", 4), "hres": (28, "semi", 4),
      "htape": (12, "body", 2), "htitle": (14, "semi", 4)}
DK = {"chip": (15, "med", 4), "status": (15, "med", 4), "label": (13, "med", 4),
      "wtitle": (15, "semi", 4), "row": (15, "body", 4), "col": (12, "semi", 4),
      "meta": (14, "body", 4), "tape": (15, "body", 4), "result": (44, "semi", 4),
      "keypad": (22, "med", 4), "op": (24, "med", 4)}


def F(T, role, table, k=1):
    size, w, bpp = table[role]
    size = T["sizes"].get("hh" if table is HH else "dk", {}).get(role, size)
    if T["fam"] == "petme":
        return Petme(max(1, size // 8), k)
    if T["render"]["text"] == "crisp":
        bpp = 1
    elif size <= 13:
        bpp = 2
    weight = T.get("w_" + role, T["w_" + w])
    return Face(T["fam"], size, weight, bpp, k)


def kscale(T, desk):
    return T["render"]["scale"] if desk else 1


def mid_base(face, y, h):
    """The baseline that centres a face's capitals in a box."""
    return y + (h + face.cap) // 2


def ring_mark(cv, T, x, y, w, h, r):
    """The theme's ring around a part: `focus.ring`'s width, gap and alpha."""
    rg = T["focus"]["ring"]
    g, t = rg["gap"], rg["w"]
    o = g + t
    ox = int(-(-o // 1))
    c = T[T["focus"]["color"]]
    cv.ring(x - ox, y - ox, w + 2 * ox, h + 2 * ox, r + ox if r else 0, t, c, rg["alpha"], inset=ox - o)
    ol = T["focus"]["outline"]
    if ol:
        e = ox + ol[0]
        cv.ring(x - e, y - e, w + 2 * e, h + 2 * e, r + e if r else 0, ol[0], ol[1], inset=ox - o)


FOCUS_ON = [True]


@contextlib.contextmanager
def focus_off():
    """Draw a scene with nothing focused (focus is elsewhere, or on a menu)."""
    FOCUS_ON[0] = False
    try:
        yield
    finally:
        FOCUS_ON[0] = True


def ink_on(c):
    return "#15161d" if ratio("#15161d", c) >= ratio("#ffffff", c) else "#ffffff"


def focus_mark(cv, T, box, size, image=None):
    """The focus mark: the engine's, by size class (`card`, `part` or
    `small`) from the theme's `focus`. Call it after the part's own fill and
    before its content; it returns the ink the content takes, or None to keep
    it. `image` is a card's picture, which the mark pastes (lifted or not)."""
    x, y, w, h, r = box
    fo = T["focus"]
    st = (fo[size] if size in ("card", "small") else None) or fo["style"]
    c = T[fo["color"]]
    if not FOCUS_ON[0]:
        if image is not None:
            cv.paste(image, x, y, w=w, r=r)
        return None
    ink = c if fo["ink"] == "accent" else None
    if st == "ring":
        if size == "small":
            a = fo["plate"]["alpha"][T["mode"] == "dark"]
            cv.rrect(x, y, w, h, r, c, a)
            cv.ring(x, y, w, h, r, 1.5, c)
        else:
            ring_mark(cv, T, x, y, w, h, r)
    elif st == "plate":
        a = fo["plate"]["alpha"][T["mode"] == "dark"]
        if image is not None:
            cv.rrect(x - 6, y - 6, w + 12, h + 12, r + 6, c, a)
        else:
            cv.rrect(x, y, w, h, r, c, a)
    elif st == "lift":
        lf = fo["lift"]
        d, up = (lf["grow"], lf["rise"]) if image is not None else (1, 1)
        dark = T["mode"] == "dark"
        bx, by, bw, bh = x - d, y - d - up, w + 2 * d, h + 2 * d
        cv.shadow(bx, by, bw, bh, r, ("soft", "#000000", 0.7 if dark else 0.32, 10, 5))
        if image is not None:
            cv.paste(image, bx, by, w=bw, r=r + 1)
        else:
            cv.rrect(bx, by, bw, bh, r, "#ffffff" if not dark else mix(T["raised"], "#ffffff", 0.08))
        if lf["rim"]:
            rc, ra = lf["rim"]
            cv.ring(bx - 1, by - 1, bw + 2, bh + 2, r + 2, 1, T.get(rc, rc), ra)
        if image is not None:
            return None
    elif st == "edge":
        t = fo["edge"]["w"]
        if w >= 3 * h:
            cv.rrect(x, y, t, h, t / 2, c)
        else:
            cv.rrect(x + 2, y + h - t, w - 4, t, t / 2, c)
    elif st == "underline":
        t = fo["underline"]["w"]
        cv.rrect(x + 3, y + h - t - 1, w - 6, t, t / 2, c)
    elif st == "invert":
        if image is not None:
            cv.rrect(x - 5, y - 5, w + 10, h + 10, r + 5, c)
        else:
            cv.rrect(x, y, w, h, r, c)
        if fo["outline"]:
            ow, oc = fo["outline"]
            g = 5 if image is not None else 0
            cv.ring(x - g, y - g, w + 2 * g, h + 2 * g, r + g, ow, oc)
        ink = ink_on(c)
    if image is not None:
        cv.paste(image, x, y, w=w, r=r)
    return ink


def cur_style(T):
    return T["current"]["style"]


def current_mark(cv, T, x, y, w, h, r, back=False):
    """The current mark under a part: a `plate` (the theme's `current.color`
    at `current.alpha`) or a `fill` (`sel`, `sel_back` in a window behind).
    Returns the ink the part takes, or None for a `bar` theme, whose caller
    draws its bar."""
    cu = T["current"]
    if cu["style"] == "fill":
        cv.rrect(x, y, w, h, r, T["sel_back"] if back else T["sel"])
        return T["text"] if back else T["sel_ink"]
    if cu["style"] == "plate":
        a = cu["alpha"][T["mode"] == "dark"]
        cv.rrect(x, y, w, h, r, T[cu["color"]], a * (0.6 if back else 1.0))
        return T["text"]
    return None


def soft(cv, T, x, y, w, h, r, k=1.0, corners=None):
    if T["shadow"]:
        cv.shadow(x, y, w, h, r, T["shadow"], k if T["shadow"][0] == "soft" else 1.0, corners)


def tile_col(T, ic):
    return (T["tiles"] or {}).get(ic)


def paint(p, y0=None, h=None, mode="smooth"):
    """A theme paint: a colour, or a list of colours or (t, colour) stops."""
    if isinstance(p, list):
        stops = p if isinstance(p[0], tuple) else [(i / (len(p) - 1), c) for i, c in enumerate(p)]
        return grad(stops, y0, h, mode)
    return p


def bevel_of(cv, T, x, y, w, h, inset=False):
    if T["hi"]:
        hi, lo = (T["lo"], T["hi"]) if inset else (T["hi"], T["lo"])
        cv.bevel(x, y, w, h, hi, lo)


# ==========================================================================
# drawn backdrops
# ==========================================================================
def _smooth(pts, n=24):
    out = []
    P = [pts[0]] + pts + [pts[-1]]
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for k in range(n):
            t = k / n
            out.append(tuple(0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t
                                    + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t * t
                                    + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t ** 3)
                             for j in range(2)))
    out.append(tuple(pts[-1]))
    return out


def backdrop(cv, T, surface):
    """Rasterise a theme's shape list into the surface: what the device lays
    into the one wallpaper bake (or redraws clipped to a damaged rect)."""
    W, H = cv.W, cv.H
    X = lambda v: v * W / 1000.0
    Y = lambda v: v * H / 1000.0
    for sh in T["backdrop"].get(surface, []):
        k, at, a = sh["k"], sh.get("at"), sh.get("a", 1.0)
        p = paint(sh["p"], mode=sh.get("mode", "smooth"))
        if k == "fill":
            cv.rect(0, 0, W, H, p, a)
        elif k == "band":
            y0, y1 = round(Y(at[0])), round(Y(at[1]))
            cv.rect(0, y0, W, y1 - y0, p, a)
        elif k in ("circle", "ellipse", "pie"):
            cx, cy = X(at[0]), Y(at[1])
            rx = Y(at[2]) if k != "ellipse" else X(at[2])
            ry = Y(at[2]) if k != "ellipse" else Y(at[3])
            x0, y0 = int(cx - rx), int(cy - ry)
            w, h = int(2 * rx) + 2, int(2 * ry) + 2
            box = lambda s: ((cx - rx - x0) * s, (cy - ry - y0) * s, (cx + rx - x0) * s, (cy + ry - y0) * s)
            if k == "pie":
                cv.shape(x0, y0, w, h, lambda d, s: d.pieslice(box(s), at[3], at[4], fill=255), p, a)
            else:
                cv.shape(x0, y0, w, h, lambda d, s: d.ellipse(box(s), fill=255), p, a)
        elif k == "rrect":
            cv.rrect(round(X(at[0])), round(Y(at[1])), round(X(at[2])), round(Y(at[3])),
                     Y(at[4]), p, a)
        elif k == "wave":
            pts = _smooth([(X(px), Y(py)) for px, py in at])
            top = int(min(y for _, y in pts))
            poly = lambda s: [(x * s, (y - top) * s) for x, y in pts] + [(W * s, (H - top) * s),
                                                                          (0, (H - top) * s)]
            cv.shape(0, top, W, H - top, lambda d, s: d.polygon(poly(s), fill=255), p, a)


def backdrop_bytes(T):
    """A binary shape record: kind and flags (2 bytes), int16 coordinates,
    2 bytes per paint colour, 1 byte alpha."""
    out = {}
    for surf, shapes in T["backdrop"].items():
        n = 0
        for sh in shapes:
            at = sh.get("at") or []
            coords = sum(len(c) if isinstance(c, list) else 1 for c in at)
            cols = len(sh["p"]) if isinstance(sh["p"], list) else 1
            n += 2 + 2 * coords + 2 * cols + 1
        out[surf] = (len(shapes), n)
    return out


def backdrop_mean(cv):
    return cv.img.resize((1, 1), Image.BOX).getpixel((0, 0))


# ==========================================================================
# buttons: an app tags each button with a kind; the theme styles the kind
# ==========================================================================
KINDS = ("normal", "primary", "tonal", "danger", "toggle")


def bstyle(T, app, kind):
    """Defaults derive from the base roles; the theme's `buttons` override per
    kind, then its `app_buttons[app]` per kind. Every other kind derives from
    the resolved `normal`."""
    def over(st, k):
        st.update(T["buttons"].get(k, {}))
        st.update(T["app_buttons"].get(app, {}).get(k, {}))
        return st
    n = over(dict(fill=T["raised"], ink=T["text"], border=None, shape="round", r=T["key_r"],
                  font="key", bevel=T["edges"] == "bevel"), "normal")
    derived = {
        "normal": n,
        "toggle": dict(n),
        "tonal": dict(n, ink=T["value"], font="op"),
        "primary": dict(n, fill=T["value"], ink=T["value_ink"], font="op", border=None),
        "danger": dict(n, ink=T["danger"] or n["ink"]),
    }
    return n if kind == "normal" else over(derived[kind], kind)


def button(cv, T, app, kind, x, y, w, h, label, fonts, focus=False):
    st = bstyle(T, app, kind)
    bx, by, bw, bh, r = x, y, w, h, min(st["r"], h / 2)
    if st["shape"] == "circle":
        d = min(h, w) if w < 1.5 * h else h
        bh = d
        bw = d if w < 1.5 * h else w - (h - d)
        bx, by, r = x + (w - bw) // 2, y + (h - d) // 2, d / 2
    elif st["shape"] == "square":
        r = 0
    cv.rrect(bx, by, bw, bh, r, paint(st["fill"], by, bh))
    if st["border"]:
        cv.ring(bx, by, bw, bh, r, st["border"][0], st["border"][1])
    if st["bevel"]:
        bevel_of(cv, T, bx, by, bw, bh)
    st = dict(st)
    if focus:
        st["ink"] = focus_mark(cv, T, (bx, by, bw, bh, r), "small" if min(bw, bh) < 28 else "part") or st["ink"]
    f = fonts[st["font"]]
    if label == "⌫":
        s = max(12, int(f.cap * 1.6))
        icon(cv, "backspace", bx + (bw - s) // 2, by + (bh - s) // 2, s, st["ink"])
    elif label.isalnum() or label == ".":
        cv.text(bx + bw // 2, mid_base(f, by, bh), label, f, st["ink"], align="center")
    else:
        gm, _, gy, _ = f.glyph(label)
        cv.text(bx + bw // 2, by + (bh - gm.size[1] * cv.k) // 2 - gy * cv.k, label, f, st["ink"],
                align="center")


# ==========================================================================
# bars
# ==========================================================================
TABS = ("Games", "Mine", "Apps", "Make")
SHELF = ("pixel_pet", "star_catcher", "sky_run", "hop_quest", "harpoon_pop")


def start_button(cv, T, x, y, w, h, label=None, face=None):
    """A start-style pill: rounded on the right, a green gradient."""
    g = [(0, "#6fd06a"), (0.45, "#3c9c37"), (1, "#2a7a26")]
    cv.rrect(x, y, w, h, h / 2, grad(g, y, h), corners=(False, True, True, False))
    px = h - 8
    moy(cv, x + 6, y + (h - px) // 2, px, "#ffffff", "#3c9c37")
    if label:
        cv.text(x + 8 + px, mid_base(face, y, h), label, face, "#ffffff", shadow=("#1c4a1a", 0.8, 1, 1))


def workspaces(cv, x, y, h, ink, dim):
    cv.rrect(x, y + h // 2 - 4, 26, 8, 4, ink)
    for i in range(2):
        cv.rrect(x + 32 + i * 12, y + h // 2 - 4, 8, 8, 4, dim)


def keycap(cv, T, x, base, label, face, bg):
    """A key hint: a small cap, its legend centred."""
    K = T["keycap"]
    h = 15
    y = base - h + 3
    w = 18 if label == "enter" else max(15, face.width(label) + 8)
    fill = K["fill"] or mix(bg, T["text"], 0.16 if T["mode"] == "dark" else 0.10)
    cv.rrect(x, y, w, h, K["r"], paint(K["grad"], y, h) if K["grad"] else fill, K["alpha"])
    if K["border"]:
        cv.ring(x, y, w, h, K["r"], K["border"][0], K["border"][1])
    if K["bevel"]:
        bevel_of(cv, T, x, y, w, h)
    ink = K["ink"] or T["text"]
    if label == "enter":
        icon(cv, "enter", x + 3, y + 1, 13, ink, stroke=2.2)
    else:
        cv.text(x + w // 2, mid_base(face, y, h), label, face, ink, align="center")
    return w


def hh_bar(cv, T, title=None):
    """The handheld's top bar: system button, tabs (or ‹ and a title), status."""
    W = cv.W
    hb = T["hhbar"]
    hh = hb["h"]
    ink, dim = hb["ink"] or T["text"], hb["dim"] or T["dim"]
    if hb["style"] == "grad":
        cv.rect(0, 0, W, hh, grad(T["bar"]["grad"], 0, hh))
    elif hb["style"] == "glass":
        cv.frost(0, 0, W, hh, 0, 8, T["bar"]["fill"], T["bar"]["alpha"])
    elif hb["style"] == "flat":
        cv.rect(0, 0, W, hh, hb["fill"])
    if hb["line"]:
        cv.rect(0, hh - hb["line"][0], W, hb["line"][0], hb["line"][1])
    sb = T["bar"]["sysbtn"]
    if sb == "start":
        start_button(cv, T, 0, 2, 32, hh - 4)
        x = 40
    elif sb == "glyph":
        moy(cv, 6, (hh - 16) // 2, 16, ink, hb["fill"] or T["bar"]["fill"] or T["surface"])
        x = 30
    elif sb == "workspaces":
        cv.rrect(8, hh // 2 - 4, 18, 8, 4, ink)
        x = 36
    else:
        sys_fill, body, eye = hb["sys"] or T["bar"]["sys"] or (T["raised"], T["value"], T["raised"])
        cv.rrect(5, 4, 22, 20, 7, sys_fill)
        moy(cv, 7, 5, 18, body, eye)
        x = 38
    framed = hb["style"] != "none"
    if title:
        ft = F(T, "htitle", HH)
        b = mid_base(ft, 0, hh) if framed else 18
        icon(cv, "back", x - 6, b - ft.cap // 2 - 8, 16, ink)
        cv.text(x + 14, b, title, ft, ink)
    else:
        ft = F(T, "tab", HH)
        base = mid_base(ft, 0, hh) if framed else 18
        for t in TABS:
            cur = t == "Games"
            tw = ft.width(t)
            if cur and cur_style(T) == "plate":
                cv.text(x, base, t, ft, current_mark(cv, T, x - 6, (hh - 18) // 2, tw + 12, 18, 5))
            elif cur and T["tabs"]["style"] == "pill":
                cv.rrect(x - 6, (hh - 18) // 2, tw + 12, 18, 5 if hb["style"] == "glass" else 3,
                         T["tabs"]["pill"])
                cv.text(x, base, t, ft, T["tabs"]["pill_ink"])
            else:
                cv.text(x, base, t, ft, ink if cur else dim)
                if cur:
                    uy = 22 if not framed else hh - 4 - (hb["line"][0] if hb["line"] else 0)
                    cv.rrect(x, uy, tw, 2 * cv.k if not cv.aa else 3, 1.5, T["value"])
            x += tw + (13 if T["tabs"]["style"] == "bar" else 14)
    fs = F(T, "status", HH)
    if T["bar"]["tray"]:
        cv.rect(W - 78, 0, 78, hh, grad(T["bar"]["tray"], 0, hh))
        cv.rect(W - 78, 0, 1, hh, "#0b4fae")
    w = cv.text(W - 7, mid_base(fs, 0, hh) if framed else 18, "14:32", fs, ink, align="right")
    iy = (hh - 16) // 2 if framed else 7
    icon(cv, "battery", W - 7 - w - 22, iy, 16, ink)
    icon(cv, "wifi", W - 7 - w - 41, iy - 1, 15, ink)
    return hh if framed else 28


# ==========================================================================
# handheld home, 320x240
# ==========================================================================
def new_cv(T, desk, bg=None):
    W, H = (1024, 600) if desk else (320, 240)
    return Cv(W, H, bg or T["bg"], k=kscale(T, desk), aa=T["render"]["shapes"] == "smooth")


def hh_home(T):
    W = 320
    bg = T["home_bg"] or T["bg"]
    cv = new_cv(T, False, bg)
    backdrop(cv, T, "home")
    ob = T["on_backdrop"]
    hh_bar(cv, T)
    n, hero, gap = 64, 128, 14
    hx, hy = (W - hero) // 2, 36
    step = n + gap
    cr = T["card_r"]
    for i, name in enumerate(SHELF):
        d = i - 2
        if d == 0:
            continue
        nx = (hx - gap - n) + (d + 1) * step if d < 0 else hx + hero + gap + (d - 1) * step
        ny = hy + (hero - n) // 2
        soft(cv, T, nx, ny, n, n, cr * 0.7, 0.4)
        cv.paste(cover(name, n), nx, ny, r=cr * 0.7)
        if T["dim_neighbours"]:
            cv._blend(nx, ny, cv.rmask(n, n, cr * 0.7), bg, T["dim_neighbours"])
    soft(cv, T, hx, hy, hero, hero, cr, 0.8)
    focus_mark(cv, T, (hx, hy, hero, hero, cr), "card", image=cover("sky_run", n, 2))
    ink, dim, sh = ob["ink"] or T["text"], ob["dim"] or T["dim"], ob["shadow"]
    T["_under_title"] = cv.img.crop((60, 176, 260, 236)).resize((1, 1), Image.BOX).getpixel((0, 0))
    cv.text(W // 2, 190, "Sky Run", F(T, "title", HH), ink, align="center", shadow=sh)
    fmeta = F(T, "meta", HH)
    meta = "Platformer  ·  Built in  ·  Played today"
    if fmeta.width(meta) > W - 12:
        meta = meta.replace("  ·  ", " · ")
    cv.text(W // 2, 207, meta, fmeta, dim, align="center", shadow=sh)
    fk, fh = F(T, "key", HH), F(T, "hint", HH)
    x, base = 8, 233
    for k, label in (("enter", "Play"), ("C", "Change"), ("/", "Search")):
        x += keycap(cv, T, x, base, k, fk, bg) + 4
        x += cv.text(x, base, label, fh, dim, shadow=sh) + 11
    x = W - 8 - fh.width("Tabs")
    cv.text(x, base, "Tabs", fh, dim, shadow=sh)
    x -= 4 + 15
    keycap(cv, T, x, base, "R", fk, bg)
    keycap(cv, T, x - 18, base, "L", fk, bg)
    return cv


# ==========================================================================
# the desk, 1024x600
# ==========================================================================
DESK = (("play", "Play"), ("projects", "Projects"), ("brush", "Paint"), ("book", "Storybook"),
        ("folder", "Files"), ("calc", "Calc"), ("download", "Get carts"))
FILES = (("brick_siege", "Game", "14 KB"), ("bullet_storm", "Game", "9 KB"),
         ("calc", "App", "6 KB"), ("coin_quest", "Game", "11 KB"),
         ("harpoon_pop", "Game", "12 KB"), ("hop_quest", "Game", "18 KB"),
         ("letter_blitz", "Game", "10 KB"), ("moy_night", "Wallpaper", "3 KB"),
         ("my_art", "Art", "22 KB"), ("notes", "App", "4 KB"),
         ("pin_light", "Game", "7 KB"), ("pixel_pet", "Game", "13 KB"),
         ("sky_run", "Game", "21 KB"), ("star_catcher", "Game", "16 KB"),
         ("storybook", "App", "9 KB"), ("tiny_runner", "Game", "8 KB"))
PLACES = (("Projects", "projects"), ("Carts", "cart"), ("Art", "brush"),
          ("Music", "note"), ("Trash", "trash"))


def desk_bar(cv, T):
    b = T["bar"]
    W = cv.W
    h = b["h"]
    y0 = 0 if b["edge"] == "top" else cv.H - h
    k = cv.k
    if b["style"] == "grad":
        cv.rect(0, y0, W, h, grad(b["grad"], y0, h))
    elif b["style"] == "glass":
        cv.frost(0, y0, W, h, 0, 14, b["fill"], b["alpha"])
    else:
        cv.rect(0, y0, W, h, b["fill"] or T["surface"], b["alpha"])
    if b["line"]:
        lw, lc = b["line"]
        cv.rect(0, y0 + h - lw if b["edge"] == "top" else y0, W, lw, lc)
    ink, dim = b["ink"] or T["text"], b["dim"] or T["dim"]
    fc = F(T, "chip", DK, k)
    if b["sysbtn"] == "start":
        start_button(cv, T, 0, y0, 112, h, "moy", Face("dejavu-oblique", 18, 700, 4))
        x = 124
    elif b["sysbtn"] == "glyph":
        moy(cv, 14, y0 + (h - 18) // 2, 18, ink, b["fill"] or T["surface"])
        x = 46
    elif b["sysbtn"] == "workspaces":
        workspaces(cv, 14, y0, h, ink, dim)
        x = 80
    else:
        sys_fill, body, eye = b["sys"] or (T["raised"], T["value"], T["raised"])
        cv.rrect(6, y0 + 3, 36, 34, 9, sys_fill)
        moy(cv, 8, y0 + 4, 32, body, eye)
        x = 52
    if b["app_title"]:
        fb = Face(T["fam"], 14, 700, 4, k) if T["fam"] not in ("petme", "micro5") else fc
        x += cv.text(x, mid_base(fc, y0, h), b["app_title"], fb, ink) + 22
    if b["chips"] == "tab":
        for ic, name, cur in (("calc", "Calc", True), ("folder", "Files", False)):
            w = 18 + 8 + fc.width(name) + 24
            ch = min(28, h - 8) if cv.aa else h - 4
            cy = y0 + (h - ch) // 2
            if cur and cur_style(T) == "plate":
                cv.rrect(x, cy, w, ch, 8 if cv.aa else 2 * k, b["chip_cur"])
                current_mark(cv, T, x, cy, w, ch, 8 if cv.aa else 2 * k)
            elif cur:
                cv.rrect(x, cy, w, ch, 8 if cv.aa else 2 * k, b["chip_cur"])
                uy = cy + ch - 3 if cv.aa else y0 + h - 2 * k
                cv.rrect(x + 10, uy, w - 20, 3 if cv.aa else 2 * k, 1.5, T["value"])
            if cv.k == 1:
                icon(cv, ic, x + 12, y0 + (h - 18) // 2, 18, ink if cur else dim)
            cv.text(x + (38 if cv.k == 1 else 14), mid_base(fc, y0, h), name, fc, ink if cur else dim)
            x += w + 6
    elif b["chips"] == "button":
        for ic, name, cur in (("calc", "Calc", True), ("folder", "Files", False)):
            w = 170
            fill = [(0, "#1a46a8"), (1, "#2156c4")] if cur else [(0, "#4f9af7"), (0.5, "#3c81f3"), (1, "#3270e0")]
            cv.rrect(x, y0 + 5, w, h - 9, 4, grad(fill, y0 + 5, h - 9))
            cv.ring(x, y0 + 5, w, h - 9, 4, 1, "#163f96" if cur else "#2a64cf")
            icon(cv, ic, x + 10, y0 + 11, 18, "#ffffff")
            cv.text(x + 36, mid_base(fc, y0 + 3, h - 6), name, fc, "#ffffff",
                    shadow=("#0a1f6d", 0.6, 1, 1))
            x += w + 4
    fs = F(T, "status", DK, k)
    if b["tray"]:
        cv.rect(W - 150, y0, 150, h, grad(b["tray"], y0, h))
        cv.rect(W - 150, y0, 1, h, "#0b4fae")
        cv.rect(W - 149, y0, 1, h, "#5cb8f7")
    if b["clock"] == "center":
        cv.text(W // 2, mid_base(fs, y0, h), "Thu 14:32", fs, ink, align="center")
        w = -8
    else:
        w = cv.text(W - 14, mid_base(fs, y0, h), "14:32", fs, ink, align="right")
    s = min(22, h - 10)
    icon(cv, "battery", W - 14 - w - s - 8, y0 + (h - s) // 2, s, ink)
    icon(cv, "wifi", W - 14 - w - 2 * s - 14, y0 + (h - s) // 2 - 1, s, ink)


def tile(cv, T, ic, x, y, s, r):
    tc = tile_col(T, ic)
    if not tc:
        soft(cv, T, x, y, s, s, r, 0.35)
        cv.rrect(x, y, s, s, r, T["tile_fill"] or T["raised"])
        return T["text"]
    if T["shadow"] and not T["tile_outline"]:
        soft(cv, T, x, y, s, s, r, 0.35)
    cv.rrect(x, y, s, s, r, grad([(0, mix(tc, "#ffffff", 0.25)), (1, tc)], y, s)
             if T["tile_grad"] else tc)
    if T["tile_outline"]:
        cv.ring(x, y, s, s, r, T["tile_outline"][0], T["tile_outline"][1])
    if T["edges"] == "bevel":
        bevel_of(cv, T, x, y, s, s)
    return T["tile_ink"] or "#ffffff"


def desk_icons(cv, T):
    if T["launcher"] == "dock":
        return dock(cv, T)
    fl = F(T, "label", DK, cv.k)
    lab = T["label"]
    top = T["bar"]["h"] + 18 if T["bar"]["edge"] == "top" else 18
    cx = max(54, max(fl.width(lb) for _, lb in DESK) // 2 + 10)
    for i, (ic, label) in enumerate(DESK):
        y = top + i * 74
        ink = tile(cv, T, ic, cx - 22, y, 44, T["tile_r"])
        icon(cv, ic, cx - 12, y + 10, 24, ink, stroke=2.2)
        cv.text(cx, y + 62, label, fl, lab["ink"] or T["text"], align="center", shadow=lab["shadow"])


def dock(cv, T):
    D = T["dock"]
    n, ts, g, pad = len(DESK), 50, 12, 12
    w = n * ts + (n - 1) * g + 2 * pad
    h = ts + 2 * pad
    x, y = (cv.W - w) // 2, cv.H - h - 8
    soft(cv, T, x, y, w, h, D["r"], 0.5)
    if D["frost"]:
        cv.frost(x, y, w, h, D["r"], 16, D["fill"], D["alpha"])
    else:
        cv.rrect(x, y, w, h, D["r"], D["fill"], D["alpha"])
    if D["ring"]:
        cv.ring(x, y, w, h, D["r"], 1, D["ring"][0], D["ring"][1])
    for i, (ic, label) in enumerate(DESK):
        tx = x + pad + i * (ts + g)
        ink = tile(cv, T, ic, tx, y + pad, ts, T["tile_r"])
        icon(cv, ic, tx + 11, y + pad + 11, 28, ink, stroke=2.2)
        if ic in ("calc", "folder"):
            cv.shape(tx + ts // 2 - 2, y + h - 7, 4, 4,
                     lambda d, s: d.ellipse((0, 0, 4 * s - 1, 4 * s - 1), fill=255), "#ffffff", 0.9)


def window(cv, T, x, y, w, h, title, ic, focus):
    Wn = T["win"]
    r = T["win_r"]
    corners = T["win_corners"]
    k = cv.k
    soft(cv, T, x, y, w, h, r, 1.0 if focus else T["shadow_back"], corners)
    cv.rrect(x, y, w, h, r, Wn["body"] or T["surface"], corners=corners)
    hh = Wn["h"]
    ft = F(T, "wtitle", DK, k)
    frame = Wn["frame"] if focus else Wn["frame_back"]
    fw = frame[0] if frame else 0
    inner = fw if fw >= 4 else 0
    ink = (Wn["ink"] or T["text"]) if focus else (Wn["ink_back"] or T["dim"])
    style = Wn["header"]
    hdr = None
    if style == "fill":
        hdr = T["value"] if focus else Wn["fill_back"]
        if focus and not Wn["ink"]:
            ink = T["value_ink"]
    elif style == "plain":
        hdr = Wn["fill"] if focus else (Wn["fill_back"] or Wn["fill"])
    elif style == "grad":
        hdr = grad(Wn["grad"] if focus else Wn["grad_back"], y, hh)
    if hdr:
        cv.set_clip(x, y, w, hh)
        cv.rrect(x, y, w, hh + r + 2, r, hdr, corners=corners)
        cv.clip = None
    if style == "bar" and focus:
        cv.set_clip(x, y, w, 4)
        cv.rrect(x, y, w, 2 * r, r, T["value"])
        cv.clip = None
    if Wn["rule"] and style != "grad":
        rc = frame[1] if frame and Wn["rule"] > 1 else T["line"]
        cv.rect(x + fw, y + hh - Wn["rule"], w - 2 * fw, Wn["rule"], rc)
    tsh = Wn["tshadow"] if focus else None
    c = Wn["controls"]
    tx = x + 16 if not fw or fw < 4 else x + fw + 10
    if c == "amiga":
        gs = hh - inner - 8
        gx, gy = x + fw + 4, y + inner + 4
        bevel_of(cv, T, gx, gy, gs, gs)
        cv.rect(gx + gs // 2 - 2 * k, gy + gs // 2 - 2 * k, 4 * k, 4 * k, "#000000")
        tx = gx + gs + 12
    if Wn["align"] == "center":
        cv.text(x + w // 2, mid_base(ft, y + inner, hh - inner), title, ft, ink, align="center")
    else:
        if Wn["icon"]:
            icon(cv, ic, tx, y + (hh - 20) // 2, 20, ink)
            tx += 30
        cv.text(tx, mid_base(ft, y + inner, hh - inner), title, ft, ink, shadow=tsh)
    if c == "close":
        s = min(20, hh - 8)
        icon(cv, "close", x + w - s - 14 - fw, y + (hh - s) // 2, s, ink)
    elif c == "lights":
        for i, col in enumerate(("#ff5f57", "#febc2e", "#28c840")):
            cc = col if focus else "#d4d4d8"
            cv.shape(x + 16 + i * 20, y + (hh - 12) // 2, 12, 12,
                     lambda d, s: d.ellipse((0, 0, 12 * s - 1, 12 * s - 1), fill=255), cc)
    elif c == "xp":
        bs = hh - 12
        bx, by = x + w - bs - 8, y + 6
        g = ([(0, "#f5a385"), (0.45, "#e2582e"), (1, "#c23c16")] if focus
             else [(0, "#f2c7b8"), (1, "#d8957e")])
        cv.rrect(bx, by, bs, bs, 4, grad(g, by, bs))
        cv.ring(bx, by, bs, bs, 4, 1, "#ffffff", 0.9)
        icon(cv, "close", bx + 4, by + 4, bs - 8, "#ffffff", stroke=3)
    elif c == "adw":
        bs = 24
        bx, by = x + w - bs - 12, y + (hh - bs) // 2
        cv.shape(bx, by, bs, bs, lambda d, s: d.ellipse((0, 0, bs * s - 1, bs * s - 1), fill=255),
                 "#e6e6e8" if focus else "#efeff1")
        icon(cv, "close", bx + 6, by + 6, 12, T["text"] if focus else T["dim"], stroke=2.6)
    elif c == "amiga":
        gs = hh - inner - 8
        gx, gy = x + w - fw - gs - 4, y + inner + 4
        bevel_of(cv, T, gx, gy, gs, gs)
        cv.rect(gx + 2 * k, gy + 2 * k, gs // 2 + k, gs // 2 + k, "#000000")
        cv.rect(gx + gs // 2 - k, gy + gs // 2 - k, gs // 2 - k, gs // 2 - k, "#ffffff")
        cv.ring(gx + gs // 2 - k, gy + gs // 2 - k, gs // 2 - k, gs // 2 - k, 0, 1, "#000000")
    if frame:
        cv.ring(x, y, w, h, r, fw, frame[1], corners=corners)
    if T["edges"] == "bevel":
        bevel_of(cv, T, x, y, w, h)
    return y + hh


def file_thumb(cv, T, name, x, y, n=22):
    if os.path.exists(_src(name)):
        nl = max(1, n // cv.k)
        cv.paste(cover(name, 64).resize((nl, nl), Image.BOX).point(LUT565), x, y, n,
                 r=5 if cv.aa else 0)
        return
    letters = {"coin_quest": ("#f2c230", "#3a2a00"), "pin_light": ("#ff3d6e", "#ffffff")}
    if name in letters:
        bgc, ink = letters[name]
        cv.rrect(x, y, n, n, 5, bgc)
        f = F(T, "row", DK, cv.k) if T["render"]["text"] == "crisp" else Face(T["fam"], 13, T["w_bold"], 4)
        cv.text(x + n // 2, mid_base(f, y, n), name[0].upper(), f, ink, align="center")
        return
    if name == "moy_night":
        cv.rrect(x, y, n, n, 5, "#1b1d4a")
        cv.shape(x + 9, y + 4, 9, 9, lambda d, s: d.ellipse((0, 0, 9 * s - 1, 9 * s - 1), fill=255), "#ffd56b")
        cv.shape(x + 9, y + 4, 9, 9, lambda d, s: d.ellipse((-3 * s, -2 * s, 6 * s, 7 * s), fill=255), "#1b1d4a")
        cv.rect(x + 1, y + 17, n - 2, 3, "#0b0d24")
        return
    ic = {"calc": "calc", "notes": "projects", "my_art": "brush", "storybook": "book"}.get(name, "projects")
    tc = tile_col(T, ic)
    if tc and T["tile_grad"]:
        cv.rrect(x, y, n, n, 5, grad([(0, mix(tc, "#ffffff", 0.25)), (1, tc)], y, n))
        icon(cv, ic, x + 3, y + 3, n - 6, "#ffffff")
        return
    cv.rrect(x, y, n, n, 5, tc or (T["raised"] if T["mode"] == "dark" else T["sunk"]))
    icon(cv, ic, x + 3, y + 3, n - 6, (T["tile_ink"] or T["text"]) if tc else T["dim"])


def current_row(cv, T, x, y, w, h, r, focus):
    """The current mark on a row: a plate or a fill, or a raised row with the
    value bar."""
    ink = current_mark(cv, T, x, y, w, h, r, back=not focus)
    if ink:
        return ink
    cv.rrect(x, y, w, h, r, T["raised"] if T["mode"] == "dark" else T["sunk"])
    cv.rrect(x, y + (h - 16) // 2, 3 if cv.aa else 2 * cv.k, 16, 1.5, T["value"])
    return T["text"]


def files_window(cv, T, x, y, w, h, vib=None):
    k = cv.k
    top = window(cv, T, x, y, w, h, "Files", "folder", False)
    fr_ = T["win"]["frame_back"]
    fw = fr_[0] if fr_ else 0
    side = 160 if cur_style(T) == "bar" else 176
    bottom = y + h - fw
    corners = (False, False, True, True) if T["win_r"] and not T["win_corners"] else None
    rr = T["win_r"] if corners else 0
    if T["list_bg"]:
        cv.set_clip(x + side, top, w - side - fw, bottom - top)
        cv.rrect(x + fw, top, w - 2 * fw, bottom - top, rr, T["list_bg"], corners=corners)
        cv.clip = None
    if T["sidebar"]:
        cv.set_clip(x + fw, top, side - fw, bottom - top)
        sp = vib if T["sidebar"] == "vibrancy" else paint(T["sidebar"], top, bottom - top)
        cv.rrect(x + fw, top, w - 2 * fw, bottom - top, rr, sp, corners=corners)
        cv.clip = None
    cv.rect(x + side, top, 1, bottom - top - T["win_r"] // 2, T["line"])
    fr, fcol, fm = F(T, "row", DK, k), F(T, "col", DK, k), F(T, "meta", DK, k)
    ink, dim = T["dim"], mix(T["dim"], T["surface"], 0.15)
    pp = T["places_panel"]
    px0, py0 = x + fw + 8, top + 36
    if pp:
        px0 = x + fw + 10
        cv.rrect(px0, top + 12, side - 20 - fw, 36 + 5 * 34 + 8, 4, pp["fill"])
        cv.rrect(px0, top + 12, side - 20 - fw, 28, 4, grad(pp["head"], top + 12, 28),
                 corners=(True, True, False, False))
        cv.text(px0 + 10, top + 31, "Places", Face(T["fam"], 13, 700, 4), pp["head_ink"])
        py0 = top + 46
        px0 += 2
    else:
        cv.text(x + fw + 16, top + 26, "Places", fcol, dim)
    for i, (label, ic) in enumerate(PLACES):
        ry = py0 + i * 34
        rw = (side - 16 - fw) if not pp else (side - 28 - fw)
        tink = T["text"]
        if i == 0:
            tink = current_row(cv, T, px0, ry, rw, 30, (8 if not pp else 3) if cv.aa else 0, False)
        icol = pp["head_ink"] if pp else (tink if i == 0 else ink)
        icon(cv, ic, px0 + 12, ry + 6, 18, icol)
        cv.text(px0 + 38, mid_base(fr, ry, 30), label, fr, (pp["head_ink"] if pp else ink) if i else tink)
    lx = x + side + 8
    sx = x + w - 18 - fw
    kx = sx - fm.width("22 KB") - 22
    name_w = kx - fm.width("Wallpaper") - 16 - lx - 36
    show_kind = name_w >= fr.width("harpoon_pop")
    if not show_kind:
        name_w = kx - 16 - lx - 36
    ch = T["colhead"]
    if ch:
        cv.rect(x + side, top, w - side - fw, 30, ch["fill"])
        cv.rect(x + side, top + 30, w - side - fw, 1, ch["line"])
        for sx_ in (kx - 70, kx + 8):
            cv.rect(sx_, top + 5, 1, 20, ch["line"])
        hb, hc = top + 20, T["text"]
    else:
        hb, hc = top + 26, dim
    cv.text(lx + 36, hb, "Name", fcol, hc)
    if show_kind:
        cv.text(kx, hb, "Kind", fcol, hc, align="right")
    cv.text(sx, hb, "Size", fcol, hc, align="right")
    for i, (name, kind, size) in enumerate(FILES):
        ry = top + 36 + i * 28
        if ry + 28 > bottom - 4:
            break
        if T["zebra"] and i % 2:
            cv.rect(x + side, ry, w - side - fw, 28, T["zebra"])
        tink = mix(T["text"], T["surface"], 0.12)
        if name == "harpoon_pop":
            tink = current_row(cv, T, lx - 2, ry, x + w - lx - 6 - fw, 28,
                               (7 if T["key_r"] > 4 else 2) if cv.aa else 0, False)
        file_thumb(cv, T, name, lx + 6, ry + 3)
        b = mid_base(fr, ry, 28)
        cv.text(lx + 36, b, name, fr, tink, maxw=name_w)
        if show_kind:
            cv.text(kx, b, kind, fm, ink, align="right")
        cv.text(sx, b, size, fm, ink, align="right")


# ==========================================================================
# Calc: one body, every theme, both sizes
# ==========================================================================
KEYS = (("C", "⌫", "%", "÷"), ("7", "8", "9", "×"), ("4", "5", "6", "−"),
        ("1", "2", "3", "+"), ("0", None, ".", "="))


def key_kind(label):
    """Calc's tagging: the only per-key decision, and it is the app's."""
    if label == "=":
        return "primary"
    if label == "C":
        return "danger"
    if label in "÷×−+%":
        return "tonal"
    return "normal"


def calc_body(cv, T, x, y, w, h, desk):
    k = cv.k
    tb = DK if desk else HH
    roles = ("tape", "result", "keypad", "op") if desk else ("htape", "hres", "hkey", "hop")
    ftape, fres, fkey, fop = (F(T, r, tb, k) for r in roles)
    pad, g = (16, 10) if desk else (8, 6)
    lh = 100 if desk else 46
    lx, lw = x + pad, w - 2 * pad
    lr = (10 if T["key_r"] > 4 else T["key_r"]) if desk else min(8, T["key_r"])
    ly = y + (pad if desk else 4)
    if T["lcd"]:
        cv.rrect(lx, ly, lw, lh, lr, T["lcd"])
        if T["lcd_border"]:
            cv.ring(lx, ly, lw, lh, lr, *T["lcd_border"])
        if T["lcd_bevel"]:
            bevel_of(cv, T, lx, ly, lw, lh, inset=True)
    tape_y, res_y = (30, 84) if desk else (14, 40)
    if T["fam"] == "petme" or T["render"]["text"] == "crisp":
        res_y -= 4 if desk else 2
    cv.text(lx + lw - 14, ly + tape_y, "12 × 7 =", ftape, T["lcd_dim"] or T["dim"], align="right")
    cv.text(lx + lw - 12, ly + res_y, "84", fres, T["lcd_ink"] or T["value"], align="right")
    ky = ly + lh + (pad if desk else 8)
    kw = (lw - 3 * g) // 4
    kh = (y + h - pad - ky - 4 * g) // 5 if desk else (y + h - 6 - ky - 4 * g) // 5
    fonts = {"key": fkey, "op": fop}
    for r, rowk in enumerate(KEYS):
        for c, lab in enumerate(rowk):
            if lab is None:
                continue
            ww = kw * 2 + g if lab == "0" else kw
            button(cv, T, "calc", key_kind(lab), lx + c * (kw + g), ky + r * (kh + g), ww, kh,
                   lab, fonts, focus=lab == "5")


def calc_window(cv, T, x, y, w, h):
    top = window(cv, T, x, y, w, h, "Calc", "calc", True)
    fw = (T["win"]["frame"] or (0,))[0]
    calc_body(cv, T, x + fw, top, w - 2 * fw, y + h - top - fw, True)


def hh_calc(T):
    cv = new_cv(T, False, T["win"]["body"] or T["surface"])
    if T["hhbar"]["style"] == "none":
        cv.rect(0, 0, 320, 28, T["bg"] if T["mode"] == "dark" else T["surface"])
    top = hh_bar(cv, T, title="Calc")
    calc_body(cv, T, 0, top, 320, 240 - top, False)
    return cv


def desktop(T):
    cv = new_cv(T, True)
    backdrop(cv, T, "desk")
    vib = mix(backdrop_mean(cv), "#ffffff", 0.8) if T["sidebar"] == "vibrancy" else None
    desk_icons(cv, T)
    files_window(cv, T, *T["layout"]["files"], vib=vib)
    calc_window(cv, T, *T["layout"]["calc"])
    desk_bar(cv, T)
    return cv


def calc_sheet():
    """Calc in every theme: one calc_body, one key_kind, nine themes."""
    cw, ch, cols = 400, 860, 3
    rows = -(-len(SHEET) // cols)
    sheet = Cv(cols * cw + 20, rows * ch + 20, "#d6d6dc")
    lab = Face("inter", 15, 600, 4)
    for i, T in enumerate(SHEET):
        ox, oy = 10 + (i % cols) * cw, 10 + (i // cols) * ch
        sheet.text(ox + 10, oy + 20, T["name"], lab, "#15161d", maxw=cw - 20)
        cv = new_cv(T, True)
        backdrop(cv, T, "desk")
        x, y, w, h = T["layout"]["calc"]
        calc_window(cv, T, x, y, w, h)
        crop = cv.image().crop((max(0, x - 24), max(0, y - 24), min(1024, x + w + 24), min(600, y + h + 30)))
        sheet.img.paste(crop, (ox + (cw - crop.width) // 2, oy + 34))
        hh = hh_calc(T).image()
        sheet.img.paste(hh, (ox + (cw - 320) // 2, oy + 34 + 548))
    sheet.save("calc_all_themes", "Calc in all nine themes through one body and one kind table: "
               "the desk window over the theme's backdrop, and the handheld page below it")


def specimen():
    W, H = 860, 336
    cv = Cv(W, H, "#0f1127")
    cols = ((1, "1 bpp (hinted)"), (2, "2 bpp"), (4, "4 bpp"), (8, "8 bpp ref"))
    lab = Face("inter", 11, 600, 4)
    rows = [("inter", 11), ("inter", 12), ("inter", 13), ("plex", 12), ("atkinson", 12),
            ("jbmono", 11), ("inter", 15)]
    sample = "Settings · Wi-Fi 14:32"
    cw = 186
    for i, (_, t) in enumerate(cols):
        cv.text(110 + i * cw, 14, t, lab, "#a1a6cc")
    y = 22
    for fam, size in rows:
        for half_i, (bg, ink) in enumerate((("#0f1127", "#eef0fb"), ("#ffffff", "#15161d"))):
            yy = y + half_i * (size + 6)
            cv.rect(0, yy, W, size + 6, bg)
            if half_i == 0:
                cv.text(6, yy + size + 1, f"{fam} {size}", lab, "#a1a6cc")
            for i, (bpp, _) in enumerate(cols):
                f = Face(fam, size, 400, bpp)
                cv.clip = (106 + i * cw, yy, 106 + (i + 1) * cw - 4, yy + size + 6)
                cv.text(110 + i * cw, yy + size + 1, sample, f, ink)
                cv.clip = None
        y += 2 * (size + 6) + 6
    f = Face("jbmono", 11, 400, 2)
    cv.text(110, y + 14, "jbmono 11 at 2 bpp, the Code tab: if x1 >= l1 then lI = (l1 | 0) end", f, "#eef0fb")
    cv.save("type_specimen", "Type specimen: handheld sizes, the Code tab's mono and the desk body "
            "at 1, 2, 4 bpp and an 8-bit reference, on dark and on light", x3=True)


# ==========================================================================
def contrast_report():
    for T in VARIANTS + RICED + STYLES:
        print(T["key"], "backdrop (shapes, bytes):", backdrop_bytes(T))
        bg = T["home_bg"] or T["bg"]
        pairs = {
            "text/surface": (T["text"], T["surface"]), "dim/surface": (T["dim"], T["surface"]),
            "text/home": (T["text"], bg), "dim/home": (T["dim"], bg),
            "ring/home": (T["accent"], bg), "ring/raised": (T["accent"], T["raised"]),
            "ring/surface": (T["accent"], T["surface"]),
            "value/surface": (T["value"], T["surface"]), "value/home": (T["value"], bg),
            "value_ink/value": (T["value_ink"], T["value"]),
        }
        if cur_style(T) == "plate":
            plate = mix(T["surface"], T[T["current"]["color"]], T["current"]["alpha"][T["mode"] == "dark"])
            pairs["focus/current plate"] = (T[T["focus"]["color"]], plate)
        if T["backdrop"].get("home") and "_under_title" in T:
            u = T["_under_title"]
            ob = T["on_backdrop"]
            pairs = {"title/backdrop": (ob["ink"] or T["text"], u), "meta/backdrop": (ob["dim"] or T["dim"], u),
                     "text/surface": pairs["text/surface"], "dim/surface": pairs["dim/surface"],
                     "ring/surface": pairs["ring/surface"], "value/surface": pairs["value/surface"],
                     "value_ink/value": pairs["value_ink/value"],
                     **{k: v for k, v in pairs.items() if k == "focus/current plate"}}
        print(T["key"], "  ".join(f"{k} {ratio(*v):.1f}" for k, v in pairs.items()))


def flash_report():
    print("MFNT v2 bytes for", len(CHARSET), "glyphs")
    for fam, size, wt in (("inter", 11, 600), ("inter", 12, 400), ("inter", 13, 600),
                          ("inter", 15, 400), ("inter", 15, 600), ("inter", 22, 500),
                          ("inter", 44, 600)):
        row = []
        for bpp in (1, 2, 4):
            row.append(f"{bpp}bpp {Face(fam, size, wt, bpp).mfnt_bytes(CHARSET)}")
        print(f"  {fam} {size}/{wt}: " + ", ".join(row))
    digits = list("0123456789.,−×÷+%=CE")
    for size, wt in ((22, 500), (44, 600)):
        print(f"  inter {size}/{wt}, the {len(digits)} keypad/display glyphs: "
              + ", ".join(f"{bpp}bpp {Face('inter', size, wt, bpp).mfnt_bytes(digits)}" for bpp in (2, 4)))


def main():
    for T in VARIANTS + (CONTRAST,) + RICED + STYLES:
        hh_home(T).save(f"{T['key']}-{T['mode']}_handheld_home",
                        f"Handheld home, Games tab, carousel on Sky Run; {T['name']}", x3=True)
        desktop(T).save(f"{T['key']}-{T['mode']}_desktop",
                        f"Desk: Calc focused, Files behind, bar; {T['name']}")
    calc_sheet()
    specimen()
    with open(os.path.join(OUT, "index.txt"), "w") as fh:
        fh.write("\n".join(INDEX) + "\n")
    contrast_report()
    flash_report()


if __name__ == "__main__":
    import runpy
    runpy.run_path(os.path.join(HERE, "draw.py"), run_name="__main__")
