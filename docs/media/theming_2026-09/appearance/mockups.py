"""Draws the Appearance page's mockups (hand-drawn, not engine renders).

usage: .venv/bin/python docs/media/theming_2026-09/appearance/mockups.py [OUTDIR]

MOY64 colours only, Tiny5 (`native/moy_ui/themes/tiny5.fnt`) for the UI,
petme128 (`runtime/font.py`) for mono, the real 16x16 system icons
(`runtime/chrome.py`'s `_ICON_ART`) and the D1 parts. The seeds' rows come
from the host's own resolver (`chrome.theme_colors`, `chrome.theme_content`),
so the worktree's venv runs it. Two sizes: 320x240 (the T-Deck, console mode:
the page and its pickers are fullscreen pages) and 1024x600 (the Waveshare P4
as shipped, chrome 2x, font 1x: Appearance is a window, pickers are
popovers).

`derive()` below is the colour derivation of `docs/theming_2026-09.md` §4.10
written out in Python: every derived look in these pictures is computed by
it, not picked by hand, and it reports the readability pairs it was held to.
"""
import os
import sys

from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "runtime"))
from palette import MOY64          # noqa: E402
import font as petme               # noqa: E402
from runtime import chrome         # noqa: E402


def _icon_art():
    import ast
    tree = ast.parse(open(os.path.join(ROOT, "runtime", "chrome.py")).read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_ICON_ART":
            return ast.literal_eval(node.value)
    raise KeyError("_ICON_ART")


_ICON_ART = _icon_art()
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))


# ==========================================================================
# the derivation (docs/theming_2026-09.md §4.10)
# ==========================================================================
def _lin(v):
    v /= 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


# moy_theme_lum's units: relative luminance x 10000
LUM = [round(10000 * (0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)))
       for r, g, b in MOY64]
FLOOR = 30          # theme.contrast x 10


def clears(a, b):
    """The loader's predicate: the pair (a, b) reaches theme.contrast."""
    la, lb = LUM[a], LUM[b]
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 500) * 10 >= FLOOR * (lo + 500)


def ratio(a, b):
    la, lb = LUM[a], LUM[b]
    return (max(la, lb) + 500) / (min(la, lb) + 500)


# style.json's theme.accents: (accent, deep), the deep one for ink and the
# focus ring where the accent itself is too light against the surface
ACCENTS = [(10, 30, "YELLOW"), (9, 57, "ORANGE"), (36, 56, "CORAL"), (8, 56, "RED"),
           (14, 63, "PINK"), (45, 62, "MAGENTA"), (44, 61, "VIOLET"), (13, 61, "LAVENDER"),
           (12, 60, "BLUE"), (42, 60, "AZURE"), (41, 59, "CYAN"), (40, 58, "MINT"),
           (11, 58, "GREEN"), (47, 32, "LIME"), (15, 33, "PEACH"), (6, 5, "SILVER")]
# style.json's theme.tints: a ramp of backgrounds, dark to light, never 0 or 7
TINTS = [("NONE", None), ("GREY", [55, 54, 51, 50, 49, 6, 48]),
         ("SAND", [55, 5, 34, 53, 29, 31, 52]), ("NAVY", [1, 60, 51, 50, 22, 48]),
         ("PLUM", [61, 2, 13, 23, 24]), ("ROSE", [56, 63, 2, 46, 26, 17, 52]),
         ("FOREST", [58, 32, 3, 19, 20]), ("TEAL", [59, 58, 21, 27]),
         ("EARTH", [33, 57, 30, 28, 35, 31, 15])]
TINT = dict(TINTS)

ACCENT_FILLS = ("accent", "title", "title_active", "selection")
TIED = ("edge", "border", "hilite", "shade_hi", "bar_edge", "desktop_pattern")
BACKGROUNDS = ("panel", "surface", "surface_alt", "desktop", "desktop_pattern",
               "title_inactive", "bar", "dim", "hilite", "shade_hi", "shade_lo")
LINES = ("edge", "border", "bar_edge", "shade_dark")
INK_PAIRS = (("ink", ("surface", "surface_alt")), ("chrome_ink", ("panel", "bar")),
             ("title_ink", ("title", "title_active")), ("selection_ink", ("selection",)))
DIM_INKS = (("ink_dim", ("surface",)), ("chrome_ink_dim", ("panel", "bar", "title_inactive")))
ACCENT_INKS = ("focus", "ink_accent")
SYNTAX = ("code_text", "code_keyword", "code_string", "code_number", "code_comment",
          "code_builtin", "ink_note")
DIM_POOL = (5, 6, 13, 49, 50, 53)


def _other_set():
    """The syntax colours style.json's derive table gives each variant."""
    import json
    d = json.load(open(os.path.join(ROOT, "native", "moy_ui", "style.json")))["theme"]["derive"]
    both = dict((k, v) for k, v in d["both"] if isinstance(v, int) and not isinstance(v, bool))
    dark = dict(both, **dict((k, v) for k, v in d["dark"] if isinstance(v, int)))
    light = dict(both, **dict((k, v) for k, v in d["light"] if isinstance(v, int)))
    return dark, light


SYNTAX_DARK, SYNTAX_LIGHT = _other_set()


def light_bg(c):
    """Black beats white against it."""
    return ratio(c, 0) >= ratio(c, 7)


def tint_map(v, ramp):
    """The ramp entry nearest in luminance that clears, against black and
    against white, every floor the base value cleared; none: the base."""
    need = [x for x in (0, 7) if clears(v, x)]
    ok = [c for c in ramp if all(clears(c, x) for x in need)]
    if not ok:
        return v
    return min(ok, key=lambda c: (abs(LUM[c] - LUM[v]), c))


def pick_ink(base, bgs):
    if all(clears(base, b) for b in bgs):
        return base
    return max((0, 7), key=lambda x: (min(ratio(x, b) for b in bgs), x == 0))


def derive_variant(base, accent, tint):
    """One variant's row from the base row (every role resolved), an accent
    entry of ACCENTS (or None: keep) and a tint name. Returns (row, why)."""
    row = dict(base)
    why = dict((k, "kept") for k in base)
    ramp = TINT.get(tint)
    if ramp:
        for r in BACKGROUNDS:
            row[r], why[r] = tint_map(base[r], ramp), "tint"
        for r in LINES:
            if base[r] not in (0, 7):
                row[r], why[r] = tint_map(base[r], ramp), "tint"
    if accent is not None:
        a, deep = accent[0], accent[1]
        ident = (base["title"], base["accent"])
        for r in TIED:
            if base[r] in ident:
                row[r], why[r] = a, "accent"
        for r in ACCENT_FILLS:
            row[r], why[r] = a, "accent"
        for r in ACCENT_INKS:
            for c in (a, deep, row["ink"]):
                if clears(c, row["surface"]):
                    row[r], why[r] = c, "accent"
                    break
    for r, bgs in INK_PAIRS:
        v = pick_ink(base[r], [row[b] for b in bgs])
        if v != base[r]:
            why[r] = "ink"
        row[r] = v
    for r, bgs in DIM_INKS:
        bg = [row[b] for b in bgs]
        if not all(clears(base[r], b) for b in bg):
            pool = [c for c in set((ramp or []) + list(DIM_POOL))
                    if all(clears(c, b) for b in bg)]
            row[r] = min(pool, key=lambda c: (min(ratio(c, b) for b in bg), c)) if pool \
                else row["ink" if r == "ink_dim" else "chrome_ink"]
            why[r] = "ink"
    for r in SYNTAX:
        if r in row and not clears(row[r], row["surface"]):
            alt = (SYNTAX_LIGHT if light_bg(row["surface"]) else SYNTAX_DARK).get(r)
            row[r] = alt if alt is not None and clears(alt, row["surface"]) else row["ink"]
            why[r] = "ink"
    return row, why


PAIRS = (("title", "title_ink"), ("title_active", "title_ink"), ("surface", "ink"),
         ("surface_alt", "ink"), ("selection", "selection_ink"), ("panel", "chrome_ink"),
         ("bar", "chrome_ink"), ("title_inactive", "chrome_ink_dim"))


def readable(row):
    return [p for p in PAIRS if not clears(row[p[0]], row[p[1]])]


def base_row(name, variant):
    r = dict(chrome.theme_colors(name, variant))
    r.update(chrome.theme_content(name, variant))
    return r


def accent_of(name):
    for e in ACCENTS:
        if e[0] == name or e[2] == name:
            return e
    return None


def derived(name, variant, accent, tint):
    return derive_variant(base_row(name, variant), accent_of(accent) if accent else None, tint)[0]


# ==========================================================================
# drawing
# ==========================================================================
def _load_tiny5():
    d = open(os.path.join(ROOT, "native", "moy_ui", "themes", "tiny5.fnt"), "rb").read()
    w, h, first, count = d[5], d[6], d[9], d[10]
    adv = d[16:16 + count]
    off = 16 + ((count + 3) // 4) * 4
    cb = (h + 7) // 8
    glyphs = {}
    for i in range(count):
        g = d[off + i * w * cb: off + (i + 1) * w * cb]
        glyphs[first + i] = (g, adv[i])
    return dict(w=w, h=h, glyphs=glyphs)


TINY5 = _load_tiny5()
GLYPHS = {
    "←": ("..1..", ".1...", "11111", ".1...", "..1.."),
    "→": ("..1..", "...1.", "11111", "...1.", "..1.."),
    "↑": ("..1..", ".111.", "1.1.1", "..1..", "..1.."),
    "↓": ("..1..", "..1..", "1.1.1", ".111.", "..1.."),
    "•": (".....", ".11..", ".11..", ".....", "....."),
    "✓": (".....", "....1", "...1.", "1.1..", ".1..."),
    "‹": ("...1.", "..1..", ".1...", "..1..", "...1."),
    "›": (".1...", "..1..", "...1.", "..1..", ".1..."),
    "…": (".....", ".....", ".....", ".....", "1.1.1"),
    "⌫": ("..111", ".1..1", "1.1.1", ".1..1", "..111"),
    "×": (".....", "1...1", ".1.1.", "..1..", ".1.1."),
}


class Cv:
    def __init__(self, w, h, bg=0):
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w, h), tuple(MOY64[bg]))
        self.px = self.img.load()

    def put(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[x, y] = tuple(MOY64[c])

    def rect(self, x, y, w, h, c):
        x0, y0, x1, y1 = max(0, x), max(0, y), min(self.w, x + w), min(self.h, y + h)
        if x1 > x0 and y1 > y0:
            self.img.paste(tuple(MOY64[c]), (x0, y0, x1, y1))

    def box(self, x, y, w, h, c):
        self.rect(x, y, w, 1, c)
        self.rect(x, y + h - 1, w, 1, c)
        self.rect(x, y, 1, h, c)
        self.rect(x + w - 1, y, 1, h, c)

    def dotbox(self, x, y, w, h, c):
        for i in range(0, w, 2):
            self.put(x + i, y, c)
            self.put(x + i, y + h - 1, c)
        for i in range(0, h, 2):
            self.put(x, y + i, c)
            self.put(x + w - 1, y + i, c)

    def dither(self, x, y, w, h, a, b):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.put(xx, yy, a if (xx + yy) & 1 else b)

    def pinstripe(self, x, y, w, h, a, b):
        for yy in range(y, y + h):
            self.rect(x, yy, w, 1, a if yy & 1 else b)

    def text(self, x, y, s, c, font="ui", scale=1):
        for ch in s:
            x += self._glyph(x, y, ch, c, font, scale)
        return x

    def tw(self, s, font="ui", scale=1):
        return sum(self._gadv(ch, font) for ch in s) * scale

    def _gadv(self, ch, font):
        if ch in GLYPHS:
            return 6
        if font == "mono":
            return 8
        g = TINY5["glyphs"].get(ord(ch))
        return (g[1] if g else 3) + 1

    def _glyph(self, x, y, ch, c, font, scale):
        if ch in GLYPHS:
            for yy, r in enumerate(GLYPHS[ch]):
                for xx, b in enumerate(r):
                    if b == "1":
                        self.rect(x + xx * scale, y + (yy + 1) * scale, scale, scale, c)
            return 6 * scale
        if font == "mono":
            cols = petme.glyph(ch)
            for j in range(8):
                for i in range(8):
                    if (cols[j] >> i) & 1:
                        self.rect(x + j * scale, y + i * scale, scale, scale, c)
            return 8 * scale
        g = TINY5["glyphs"].get(ord(ch))
        if not g:
            return 4 * scale
        cols, adv = g
        for j in range(TINY5["w"]):
            for i in range(TINY5["h"]):
                if (cols[j] >> i) & 1:
                    self.rect(x + j * scale, y + i * scale, scale, scale, c)
        return (adv + 1) * scale

    def ctext(self, x, y, w, s, c, font="ui", scale=1):
        self.text(x + (w - self.tw(s, font, scale)) // 2, y, s, c, font, scale)

    def icon(self, name, x, y, scale=1, tint=None):
        for yy, row in enumerate(_ICON_ART[name]):
            for xx, ch in enumerate(row):
                if ch != ".":
                    self.rect(x + xx * scale, y + yy * scale, scale, scale,
                              tint if tint is not None else int(ch, 16))

    def paste(self, other, x, y):
        self.img.paste(other.img, (x, y))

    def save(self, name):
        path = os.path.join(OUT, name)
        self.img.save(path)
        print("wrote", path)


class Look:
    def __init__(self, row, name="night", variant="dark", style="classic", font="TINY5",
                 accent=None, tint="NONE", custom=False):
        self.t = row
        self.name, self.variant, self.style, self.font = name, variant, style, font
        self.acc, self.tnt, self.custom = accent, tint, custom

    def __getattr__(self, k):
        return self.t[k]


def look(name="night", variant="dark", accent=None, tint="NONE", **kw):
    if accent is None and tint == "NONE":
        return Look(base_row(name, variant), name, variant, **kw)
    return Look(derived(name, variant, accent, tint), name, variant,
                accent=accent, tint=tint, **kw)


# --- the D1 parts ---------------------------------------------------------
def surface(cv, x, y, w, h, T, fill=None, border=None, shadow=True, focus=False):
    bw, bh = (w - 2, h - 2) if shadow else (w, h)
    if shadow:
        cv.rect(x + 2, y + bh, bw, 2, T.shade_dark)
        cv.rect(x + bw, y + 2, 2, bh, T.shade_dark)
    cv.rect(x, y, bw, bh, T.panel if fill is None else fill)
    cv.box(x, y, bw, bh, T.shade_dark if border is None else border)
    if focus:
        cv.box(x - 2, y - 2, bw + 4, bh + 4, T.focus)


def button(cv, x, y, w, h, label, T, state="rest"):
    fill = {"on": T.accent, "disabled": T.panel}.get(state, T.surface_alt)
    ink = {"on": 0, "disabled": T.ink_dim}.get(state, T.ink)
    surface(cv, x, y, w, h, T, fill=fill, focus=(state == "hover"))
    cv.ctext(x, y + (h - 2 - 6) // 2, w - 2, label, ink)


def chip(cv, x, y, w, h, label, T, on=False, focus=False, ink=None):
    cv.rect(x, y, w, h, T.accent if on else T.panel)
    cv.box(x, y, w, h, T.shade_dark)
    if focus:
        cv.box(x - 2, y - 2, w + 4, h + 4, T.focus)
    cv.ctext(x, y + (h - 6) // 2, w, label, 0 if on else (T.ink if ink is None else ink))
    return x + w


def swatch(cv, x, y, s, idx, T, focus=False, tick=False):
    cv.rect(x, y, s, s, idx)
    cv.box(x, y, s, s, T.shade_dark)
    if focus:
        cv.box(x - 2, y - 2, s + 4, s + 4, T.focus)
        cv.box(x - 3, y - 3, s + 6, s + 6, T.shade_dark)
    if tick:
        cv.text(x + s // 2 - 3, y + s // 2 - 4, "✓", 0 if light_bg(idx) else 7)


def ramp_tile(cv, x, y, w, h, ramp, T, base=None):
    """A tint as its ramp: vertical bands, dark to light."""
    cols = ramp if ramp else [base.dim, base.panel, base.surface_alt, base.hilite]
    bw = w / len(cols)
    for i, c in enumerate(cols):
        cv.rect(x + int(i * bw), y, int((i + 1) * bw) - int(i * bw), h, c)
    cv.box(x, y, w, h, T.shade_dark)
    if not ramp:
        for i in range(0, w, 3):
            cv.put(x + i, y + h - 1 - (i * h // max(1, w)), T.ink)


def scrollbar(cv, x, y, h, T, at=0.0, span=0.6):
    cv.rect(x, y, 3, h, T.shade_lo)
    cv.rect(x, y + int(h * at), 3, max(6, int(h * span)), T.ink_dim)


def toast(cv, x, y, s, T, bad=False):
    w = cv.tw(s) + 14
    fill = T.danger if bad else T.surface_alt
    surface(cv, x, y, w, 15, T, fill=fill)
    cv.text(x + 6, y + 4, s, 7 if bad else T.ink)
    return w


# --- the bar ------------------------------------------------------------------
def menu_glyph(cv, x, y, cs, c):
    for i in range(3):
        cv.rect(x + 2 * cs, y + (3 + i * 5) * cs, 12 * cs, 2 * cs, c)


def osbar(cv, T, cs, zone, chips=(), console=True, focus_chip=None, clock="10:24"):
    h = 18 * cs
    cv.rect(0, 0, cv.w, h, T.bar)
    cv.rect(0, h - 1, cv.w, 1, T.bar_edge)
    x = 4 * cs
    if console:
        cv.icon("moy", x, (h - 16 * cs) // 2, cs)
        x += 18 * cs
    cv.text(x, (h - 6) // 2, zone, T.chrome_ink)
    cx = cv.w // 2 - sum(cv.tw(c) + 10 + 6 for c in chips) // 2
    for c in chips:
        w = cv.tw(c) + 10
        on = c == focus_chip
        cv.rect(cx, 3 * cs, w, h - 6 * cs, T.accent if on else T.panel)
        cv.box(cx, 3 * cs, w, h - 6 * cs, T.shade_dark)
        cv.ctext(cx, (h - 6) // 2, w, c, 0 if on else T.chrome_ink)
        cx += w + 6
    rx = cv.w - 4 * cs
    iy = (h - 16 * cs) // 2
    ink = T.chrome_ink if T.bar_light else None
    for name in ("batt", "wifi"):
        rx -= 18 * cs
        cv.icon(name, rx, iy, cs, tint=ink)
    rx -= 18 * cs
    menu_glyph(cv, rx, iy, cs, T.chrome_ink)
    rx -= 18 * cs
    cv.icon("close", rx, iy, cs)
    rx -= cv.tw(clock) + 6 * cs
    cv.text(rx, (h - 6) // 2, clock, T.chrome_ink)
    return h


# --- windows --------------------------------------------------------------------
def window(cv, x, y, w, h, title, T, active=True, stripe=12, cs=2, style=None):
    style = style or T.style
    th = 8 * cs
    cv.rect(x + 3, y + 3, w, h, 0 if style == "classic" else T.shade_dark)
    cv.rect(x, y, w, h, T.panel)
    cv.box(x, y, w, h, T.shade_dark)
    strip = T.title if active else T.title_inactive
    ty = y + 1 + (th - 6) // 2
    bs = th - 4
    if style == "platinum":
        cv.rect(x + 1, y + 1, w - 2, th, T.panel)
        tw_ = cv.tw(title)
        if active:
            cv.pinstripe(x + 22, y + 3, (w - tw_) // 2 - 28, th - 4, T.shade_lo, T.panel)
            cv.pinstripe(x + (w + tw_) // 2 + 6, y + 3, (w - tw_) // 2 - 28, th - 4, T.shade_lo, T.panel)
        cv.rect(x + 1, y + th + 1, w - 2, 1, T.shade_dark)
        cv.ctext(x, ty, w, title, T.ink if T.surface_light else T.chrome_ink)
        for bx in (x + 4, x + w - 4 - bs):
            cv.rect(bx, y + 3, bs, bs, T.panel)
            cv.box(bx, y + 3, bs, bs, T.shade_dark)
            cv.rect(bx + 1, y + 4, bs - 2, 1, T.shade_hi)
    elif style == "win9x":
        cv.rect(x + 1, y + 1, w - 2, 1, T.shade_hi)
        cv.rect(x + 1, y + 1, 1, h - 2, T.shade_hi)
        cv.rect(x + 3, y + 3, w - 6, th - 2, strip)
        cv.text(x + 6, ty + 1, title, T.title_ink)
        bx = x + w - 5 - bs
        for i in range(3):
            cv.rect(bx, y + 4, bs - 1, bs - 2, T.panel)
            cv.box(bx, y + 4, bs - 1, bs - 2, T.shade_dark)
            cv.rect(bx, y + 4, bs - 2, 1, T.shade_hi)
            cv.text(bx + 3, y + 5, "×_o"[i], T.chrome_ink if not T.surface_light else 0)
            bx -= bs + 1
    elif style == "tab":
        tw_ = cv.tw(title) + 14 + 3 * cs
        cv.rect(x, y, w, th + 2, T.desktop)
        cv.rect(x + 1, y + 1, tw_, th + 1, strip)
        cv.box(x, y, tw_ + 2, th + 2, T.shade_dark)
        cv.rect(x, y + th + 1, w, 1, T.shade_dark)
        cv.rect(x + 1, y + 1, 3 * cs, th, stripe)
        cv.text(x + 3 * cs + 6, ty, title, T.title_ink)
    elif style == "flat":
        cv.rect(x + 1, y + 1, w - 2, th, T.panel)
        cv.rect(x + 1, y + th + 1, w - 2, 1, T.dim)
        cv.text(x + 8, ty, title, T.chrome_ink)
        cv.text(x + w - 14, ty, "×", T.chrome_ink_dim)
    else:
        cv.rect(x + 1, y + 1, w - 2, th, strip)
        cv.rect(x + 1, y + th + 1, w - 2, 1, T.shade_dark)
        cv.rect(x + 1, y + 1, 3 * cs, th, stripe)
        cv.text(x + 1 + 3 * cs + 4, ty, title, T.title_ink)
        bx = x + w - 2 - bs - 1
        cv.box(bx, y + 3, bs, bs, T.title_ink)
        cv.text(bx + (bs - 6) // 2 + 1, y + 3 + (bs - 8) // 2, "×", T.title_ink)
    return x + 1, y + th + 2, w - 2, h - th - 3


def popover(cv, x, y, w, h, title, T):
    cv.rect(x + 3, y + 3, w, h, T.shade_dark)
    cv.rect(x, y, w, h, T.panel)
    cv.box(x, y, w, h, T.shade_dark)
    cv.rect(x + 1, y + 1, w - 2, 13, T.surface_alt)
    cv.rect(x + 1, y + 14, w - 2, 1, T.shade_dark)
    cv.text(x + 6, y + 5, title, T.ink)
    return x + 1, y + 15, w - 2, h - 16


# --- a theme preview card (colours only, as ui.theme_resolve answers) -----------
def theme_card(cv, x, y, w, h, P, label, T, selected=False, focus=False, tag=None):
    """A little desk in the package's colours: its desktop, its bar, an
    inactive window behind an active one with its title and accent."""
    surface(cv, x, y, w, h, T, fill=P.desktop, focus=focus,
            border=T.accent if selected else None)
    iw, ih = w - 2, h - 2 - 10
    cv.rect(x + 1, y + 1, iw - 1, 3, P.bar)
    wx, wy, ww, wh = x + 4, y + 7, iw * 5 // 9, ih - 11
    cv.rect(wx, wy, ww, wh, P.panel)
    cv.box(wx, wy, ww, wh, P.shade_dark)
    cv.rect(wx + 1, wy + 1, ww - 2, 3, P.title_inactive)
    wx2, wy2, ww2, wh2 = x + iw * 3 // 8, y + 10, iw * 5 // 9, ih - 11
    cv.rect(wx2 + 1, wy2 + 1, ww2, wh2, 0)
    cv.rect(wx2, wy2, ww2, wh2, P.surface)
    cv.box(wx2, wy2, ww2, wh2, P.shade_dark)
    cv.rect(wx2 + 1, wy2 + 1, ww2 - 2, 3, P.title)
    cv.rect(wx2 + 3, wy2 + 6, ww2 // 3, 3, P.accent)
    cv.rect(wx2 + 3, wy2 + 11, ww2 - 6, 1, P.ink_dim)
    cv.rect(wx2 + 3, wy2 + 14, ww2 * 2 // 3, 1, P.ink_dim)
    ly = y + h - 2 - 10
    cv.rect(x + 1, ly, iw - 1, 9, T.accent if selected else T.bar)
    cv.text(x + 4, ly + 2, label, 0 if selected else T.chrome_ink)
    if tag:
        tw_ = cv.tw(tag) + 4
        cv.rect(x + iw - tw_ - 1, ly, tw_, 9, T.play if tag == "CUSTOM" else T.dim)
        cv.text(x + iw - tw_ + 1, ly + 2, tag, 0 if tag == "CUSTOM" else T.chrome_ink)


def window_sample(cv, x, y, w, h, T, style):
    """A tiny window frame in a WINDOW STYLE preset, for the row."""
    sub = Look(T.t, style=style)
    window(cv, x, y, w, h, "", sub, cs=1)


# ==========================================================================
# the Appearance page: one list of rows, every board
# ==========================================================================
WALL = "MOY'S NIGHT"


def page_rows(T, focus, cs_choices=True, on_custom=False, compact=False, taskbar=False):
    """The page's rows in order. Each is (key, label, draw-kind). The THEME
    grid is its own block above them on a wide page and a row of its own on
    the 320x240 page."""
    out = []
    if compact:
        out.append(("theme", "THEME"))
    out += [("mode", "MODE"), ("accent", "ACCENT"), ("tint", "TINT"),
            ("wallpaper", "WALLPAPER"), ("style", "WINDOW STYLE"), ("font", "FONT")]
    if cs_choices:
        out.append(("chrome", "CHROME SIZE"))
    if taskbar:
        out.append(("taskbar", "EDIT TASKBAR…"))
    out.append(("file", "EDIT FILE…"))
    if on_custom:
        out.append(("name", "NAME…"))
    return out


def draw_value(cv, key, x, y, w, rh, T, focused):
    """A row's value, right-aligned in [x, x + w)."""
    ly = y + (rh - 6) // 2
    r = x + w
    if key == "mode":
        cx = r - 76
        chip(cv, cx, y + (rh - 13) // 2, 36, 13, "DARK", T, on=T.variant == "dark",
             focus=focused and T.variant == "dark")
        chip(cv, cx + 40, y + (rh - 13) // 2, 36, 13, "LIGHT", T, on=T.variant == "light",
             focus=focused and T.variant == "light")
        return
    if key == "accent":
        a = accent_of(T.acc) if T.acc else accent_of(T.t["accent"])
        name = a[2] if a else "THEME'S OWN"
        s = rh - 8
        cv.text(r - 12, ly, "›", T.ink_dim)
        nx = r - 16 - cv.tw(name)
        cv.text(nx, ly, name, T.ink)
        swatch(cv, nx - s - 6, y + 4, s, T.t["accent"], T, focus=focused)
        return
    if key == "tint":
        name = T.tnt
        cv.text(r - 12, ly, "›", T.ink_dim)
        nx = r - 16 - cv.tw(name)
        cv.text(nx, ly, name, T.ink)
        ramp_tile(cv, nx - 34, y + 4, 28, rh - 8, TINT[name], T, base=T)
        if focused:
            cv.box(nx - 36, y + 2, 32, rh - 4, T.focus)
        return
    if key == "wallpaper":
        cv.text(r - 12, ly, "›", T.ink_dim)
        nx = r - 16 - cv.tw(WALL)
        cv.text(nx, ly, WALL, T.ink)
        tx, tw_, th_ = nx - 34, 28, rh - 8
        cv.rect(tx, y + 4, tw_, th_, 60)
        for i in range(9):
            cv.put(tx + 2 + (i * 7) % (tw_ - 3), y + 5 + (i * 5) % (th_ - 5), 7)
        cv.rect(tx, y + 4 + th_ - 4, tw_, 4, 1)
        cv.rect(tx + tw_ - 8, y + 6, 4, 4, 10)
        cv.box(tx, y + 4, tw_, th_, T.shade_dark)
        return
    if key == "style":
        val = {"classic": "CLASSIC", "platinum": "PLATINUM", "win9x": "WIN9X",
               "tab": "TABS", "flat": "FLAT"}[T.style]
        cycle_value(cv, r - 92, ly, 92, val, T, focused)
        window_sample(cv, r - 92 - 44, y + 3, 38, rh - 6, T, T.style)
        return
    if key == "font":
        cycle_value(cv, r - 92, ly, 92, T.font, T, focused)
        f = "mono" if T.font == "PETME" else "ui"
        cv.text(r - 92 - 30, ly - (1 if f == "mono" else 0), "Aa", T.ink_dim, font=f)
        return
    if key == "chrome":
        cycle_value(cv, r - 60, ly, 60, "2x", T, focused)
        return
    if key == "file":
        s = "theme.json"
        cv.text(r - 12, ly, "›", T.ink_dim)
        cv.text(r - 16 - cv.tw(s, "mono"), ly - 1, s, T.ink_dim, font="mono")
        return
    if key == "taskbar":
        s = "the bar's own edit mode"
        cv.text(r - 12, ly, "›", T.ink_dim)
        cv.text(r - 16 - cv.tw(s), ly, s, T.ink_dim)
        return
    if key == "name":
        s = "keep it as its own theme"
        cv.text(r - 12, ly, "›", T.ink_dim)
        cv.text(r - 16 - cv.tw(s), ly, s, T.ink_dim)
        return
    if key == "theme":
        s = T.name
        cv.text(r - 12, ly, "›", T.ink_dim)
        nx = r - 16 - cv.tw(s)
        cv.text(nx, ly, s, T.ink)
        if T.custom:
            tag = "from night"
            cv.text(nx - cv.tw(tag) - 8, ly, tag, T.ink_dim)
        return


def cycle_value(cv, x, y, w, value, T, focused):
    cv.text(x, y, "‹", T.accent if focused else T.ink_dim)
    cv.text(x + w - 6, y, "›", T.accent if focused else T.ink_dim)
    cv.ctext(x + 6, y, w - 12, value, T.ink)
    if focused:
        cv.box(x - 3, y - 4, w + 6, 14, T.focus)


def draw_rows(cv, x, y, w, rh, rows, T, focus):
    for key, label in rows:
        foc = key == focus
        if foc:
            cv.rect(x, y, w, rh, T.surface_alt)
            cv.rect(x, y, 2, rh, T.focus)
        cv.rect(x, y + rh - 1, w, 1, T.shade_lo)
        cv.text(x + 8, y + (rh - 6) // 2, label, T.ink)
        draw_value(cv, key, x + 8, y, w - 16, rh, T, foc)
        y += rh
    return y


def keys_strip(cv, x, y, w, h, T, left, keys, dot=False):
    cv.rect(x, y, w, h, T.bar)
    cv.rect(x, y, w, 1, T.bar_edge)
    tx = cv.text(x + 5, y + (h - 6) // 2, left, T.chrome_ink)
    if dot:
        cv.text(tx + 3, y + (h - 6) // 2, "•", T.accent)
    cv.text(x + w - cv.tw(keys) - 5, y + (h - 6) // 2, keys, T.chrome_ink_dim)


def status_left(T):
    if T.custom:
        return T.name + "  -  saved as you go"
    return T.name + "  -  built in"


# --- 320x240 ------------------------------------------------------------------
TD_KEYS = "↑↓ row  ←→ change  ENTER open"


def td_page(T, focus="accent", dot=False):
    cv = Cv(320, 240, T.panel)
    osbar(cv, T, 1, "Appearance")
    rows = page_rows(T, focus, cs_choices=False, on_custom=T.custom, compact=True)
    y = 18
    # the THEME row is taller: the current theme's card
    rh0 = 34
    foc = focus == "theme"
    if foc:
        cv.rect(0, y, 320, rh0, T.surface_alt)
        cv.rect(0, y, 2, rh0, T.focus)
    cv.text(8, y + 14, "THEME", T.ink)
    theme_card(cv, 70, y + 3, 52, rh0 - 4, T, "", T, focus=foc)
    draw_value(cv, "theme", 130, y, 182, rh0, T, foc)
    cv.rect(0, y + rh0 - 1, 320, 1, T.shade_lo)
    draw_rows(cv, 0, y + rh0, 320, 20, rows[1:], T, focus)
    keys_strip(cv, 0, 228, 320, 12, T, status_left(T), TD_KEYS, dot=dot)
    return cv


def td_theme_grid(T, names, selected, focus, custom_from=None, toast_s=None):
    cv = Cv(320, 240, T.panel)
    osbar(cv, T, 1, "Appearance › Theme")
    cw, ch, gap = 98, 58, 6
    for i, (name, P, tag) in enumerate(names):
        cx = 8 + (i % 3) * (cw + gap)
        cy = 24 + (i // 3) * (ch + gap)
        if cy + ch > 226:
            break
        theme_card(cv, cx, cy, cw, ch, P, name, T, selected=(name == selected),
                   focus=(name == focus), tag=tag)
    scrollbar(cv, 314, 24, 198, T, 0.0, 0.62)
    keys_strip(cv, 0, 228, 320, 12, T, toast_s or "%d themes" % len(names), "←→↑↓  ENTER use  ESC back")
    return cv


def td_accent(T, cursor):
    cv = Cv(320, 240, T.panel)
    osbar(cv, T, 1, "Appearance › Accent")
    cv.text(8, 24, "ONE COLOUR FOR TITLES, SELECTION AND FOCUS.", T.ink_dim)
    s, gap = 28, 8
    for i, a in enumerate(ACCENTS):
        sx = 12 + (i % 8) * (s + gap)
        sy = 38 + (i // 8) * (s + 16)
        swatch(cv, sx, sy, s, a[0], T, focus=(a[0] == cursor),
               tick=(a[0] == T.t["accent"]))
    name = accent_of(cursor)[2]
    cv.text(12, 124, name, T.ink)
    # what the accent does, drawn in the derived row
    px, py = 12, 136
    window(cv, px, py, 180, 80, "Calc", T, cs=1, stripe=11)
    chip(cv, px + 8, py + 18, 40, 12, "ON", T, on=True)
    chip(cv, px + 54, py + 18, 40, 12, "OFF", T)
    surface(cv, px + 100, py + 16, 70, 16, T, fill=T.surface_alt, focus=True)
    cv.text(px + 106, py + 21, "FOCUS", T.ink)
    cv.rect(px + 8, py + 40, 162, 10, T.selection)
    cv.text(px + 12, py + 42, "a selected row", T.selection_ink)
    cv.text(px + 8, py + 58, "emphasis:", T.ink_dim)
    cv.text(px + 50, py + 58, "1024", T.ink_accent)
    cv.text(204, 140, "THE PAGE IS", T.ink_dim)
    cv.text(204, 150, "ALREADY IN IT.", T.ink_dim)
    cv.text(204, 168, "EVERY OTHER", T.ink_dim)
    cv.text(204, 178, "ROLE FOLLOWS,", T.ink_dim)
    cv.text(204, 188, "READABLE BY", T.ink_dim)
    cv.text(204, 198, "CONSTRUCTION.", T.ink_dim)
    keys_strip(cv, 0, 228, 320, 12, T, "accent", "←→↑↓ try  ENTER keep  ESC undo")
    return cv


def td_tint(T, cursor):
    cv = Cv(320, 240, T.panel)
    osbar(cv, T, 1, "Appearance › Tint")
    cv.text(8, 24, "ONE TINT FOR PANELS, DESK AND BAR.", T.ink_dim)
    tw_, th_, gap = 92, 40, 8
    for i, (name, ramp) in enumerate(TINTS):
        x = 12 + (i % 3) * (tw_ + gap)
        y = 38 + (i // 3) * (th_ + 20)
        ramp_tile(cv, x, y, tw_, th_, ramp, T, base=look(T.name, T.variant))
        if name == cursor:
            cv.box(x - 2, y - 2, tw_ + 4, th_ + 4, T.focus)
            cv.box(x - 3, y - 3, tw_ + 6, th_ + 6, T.shade_dark)
        cv.text(x, y + th_ + 4, name, T.ink if name == cursor else T.ink_dim)
        if name == T.tnt:
            cv.text(x + tw_ - 8, y + th_ + 4, "✓", T.ink)
    keys_strip(cv, 0, 228, 320, 12, T, "tint", "←→↑↓ try  ENTER keep  ESC undo")
    return cv


def wall_thumb(cv, x, y, w, h, kind, T):
    if kind == "night":
        cv.rect(x, y, w, h, 60)
        for i in range(14):
            cv.put(x + 3 + (i * 13) % (w - 6), y + 3 + (i * 7) % (h - 12), 7)
        cv.rect(x, y + h - 9, w, 9, 1)
        disc(cv, x + w - 14, y + 10, 5, 10)
        disc(cv, x + w - 11, y + 8, 4, 60)
    elif kind == "sakura":
        cv.rect(x, y, w, h, 22)
        cv.rect(x, y + h - 12, w, 12, 19)
        cv.rect(x + 20, y + 14, 4, h - 22, 4)
        for i in range(22):
            cv.rect(x + 8 + (i * 11) % 36, y + 6 + (i * 5) % 14, 3, 3, 14 if i % 3 else 16)
    elif kind == "space":
        cv.rect(x, y, w, h, 0)
        for i in range(20):
            cv.put(x + 2 + (i * 17) % (w - 4), y + 2 + (i * 11) % (h - 4), 6)
        disc(cv, x + 26, y + 22, 10, 12)
        cv.text(x + 6, y + h - 10, "SPACE", 10)
    elif kind == "art":
        cv.rect(x, y, w, h, 7)
        disc(cv, x + 18, y + 18, 9, 9)
        cv.rect(x + 6, y + h - 14, w - 12, 6, 11)
        cv.rect(x + 40, y + 10, 10, 16, 8)
    else:
        cv.rect(x, y, w, h, T.desktop)
        cv.dither(x + 1, y + 1, w - 2, h - 2, T.desktop, T.desktop_pattern)
    cv.box(x, y, w, h, T.shade_dark)


def td_wallpaper(T, cursor="sakura"):
    cv = Cv(320, 240, T.panel)
    osbar(cv, T, 1, "Appearance › Wallpaper")
    cv.text(8, 23, "THE WALLPAPER IS PART OF THE THEME: A PICK", T.ink_dim)
    cv.text(8, 32, "CHANGES IT (INTO CUSTOM); A THEME BRINGS ITS OWN.", T.ink_dim)
    items = (("night", "MOY'S NIGHT", "cart"), ("sakura", "SAKURA", "cart"),
             ("space", "SPACE DESKTOP", "cart"), ("art", "MY DRAWING 3", "my art"),
             ("none", "NONE", "the desk colour"))
    tw_, th_ = 92, 52
    for i, (k, name, sub) in enumerate(items):
        x = 10 + (i % 3) * (tw_ + 12)
        y = 46 + (i // 3) * (th_ + 32)
        wall_thumb(cv, x, y, tw_, th_, k, T)
        if k == cursor:
            cv.box(x - 2, y - 2, tw_ + 4, th_ + 4, T.focus)
            cv.box(x - 3, y - 3, tw_ + 6, th_ + 6, T.shade_dark)
        cv.text(x, y + th_ + 4, name, T.ink if k == cursor else T.ink_dim)
        cv.text(x, y + th_ + 13, sub, T.ink_dim)
        if k == "night":
            cv.text(x + tw_ - 8, y + th_ + 4, "✓", T.ink)
    keys_strip(cv, 0, 228, 320, 12, T, "night  -  built in", "←→↑↓ try  ENTER keep  ESC undo")
    return cv


def td_name(T, typed, note=None):
    cv = Cv(320, 240, T.panel)
    osbar(cv, T, 1, "Appearance › Name")
    cv.text(12, 30, "NAME YOUR THEME", T.ink)
    cv.text(12, 42, "it joins the THEME grid as its own theme;", T.ink_dim)
    cv.text(12, 52, "CUSTOM is free again for the next change.", T.ink_dim)
    fx, fy, fw, fh = 12, 70, 296, 20
    cv.rect(fx, fy, fw, fh, T.surface_alt)
    cv.box(fx, fy, fw, fh, T.shade_dark)
    cv.box(fx - 2, fy - 2, fw + 4, fh + 4, T.focus)
    tx = cv.text(fx + 6, fy + 7, typed, T.ink)
    cv.rect(tx + 1, fy + 4, 1, fh - 8, T.ink)
    cv.text(12, 98, "a-z 0-9 _ -, up to 24", T.ink_dim)
    if note:
        toast(cv, 12, 114, note, T, bad=True)
    theme_card(cv, 12, 150, 110, 64, T, typed, T, selected=True)
    cv.text(132, 160, "the theme folder", T.ink_dim)
    cv.text(132, 172, typed + ".moy", T.ink, font="mono")
    cv.text(132, 188, "travels like a cart:", T.ink_dim)
    cv.text(132, 198, "Files, the browser's", T.ink_dim)
    cv.text(132, 208, "export, the store.", T.ink_dim)
    keys_strip(cv, 0, 228, 320, 12, T, "custom", "type  ENTER keep  ESC back")
    return cv


# --- 1024x600 -----------------------------------------------------------------
DESK_ICONS = (("run", "PLAY"), ("edit", "PROJECTS"), ("paint", "PAINT"),
              ("scene", "STORYBOOK"), ("dup", "FILES"), ("blocks", "CALC"),
              ("save", "GET CARTS"))
P4_KEYS = "↑↓ row   ←→ change   ENTER open   ESC close"


def night_wallpaper(cv, y0=0):
    cv.rect(0, y0, cv.w, cv.h - y0, 60)
    for i in range(170):
        cv.put((i * 97 + 13) % cv.w, y0 + 10 + (i * 61) % 420, 7 if i % 5 else 6)
    cv.rect(0, 480, cv.w, 120, 1)
    for i in range(0, cv.w, 3):
        cv.put(i, 478 + (i // 40) % 3, 1)
    disc(cv, 890, 110, 18, 10)
    disc(cv, 901, 103, 16, 60)


def disc(cv, cx, cy, r, c):
    for dy in range(-r, r + 1):
        dx = int((r * r - dy * dy) ** 0.5)
        cv.rect(cx - dx, cy + dy, 2 * dx + 1, 1, c)


def p4_desk(T, chips=("EDITOR", "CALC", "APPEARANCE"), focus_chip="APPEARANCE", bar=True,
            icons_x=20):
    cv = Cv(1024, 600, T.desktop)
    night_wallpaper(cv)
    if bar:
        osbar(cv, T, 2, "", chips=chips, console=False, focus_chip=focus_chip)
    for i, (ic, label) in enumerate(DESK_ICONS):
        x, y = icons_x, 50 + i * 62
        surface(cv, x, y, 42, 42, T, fill=T.panel)
        cv.icon(ic, x + 4, y + 4, 2)
        lw = cv.tw(label) + 8
        cv.rect(x + 20 - lw // 2, y + 44, lw, 9, T.bar)
        cv.ctext(x + 20 - lw // 2, y + 45, lw, label, T.chrome_ink)
    return cv


def p4_calc(cv, T, x=110, y=300):
    cx, cy, cw, ch = window(cv, x, y, 150, 170, "Calc", T, active=False, stripe=11)
    surface(cv, cx + 6, cy + 6, cw - 12, 18, T, fill=T.surface_alt)
    cv.text(cx + cw - 40, cy + 11, "1024", T.ink_accent)
    for r in range(4):
        for c in range(4):
            lab = "789/456*123-C0=+"[r * 4 + c]
            fill = T.surface_alt
            if lab == "=":
                fill = T.play
            if lab == "C":
                fill = T.danger
            bx, by = cx + 6 + c * 34, cy + 30 + r * 32
            surface(cv, bx, by, 30, 28, T, fill=fill)
            cv.ctext(bx, by + 10, 28, lab, T.ink if fill == T.surface_alt else 0)


def p4_code(cv, T, x=110, y=86):
    """An Editor window on its Code tab: the content roles."""
    cx, cy, cw, ch = window(cv, x, y, 196, 196, "Editor", T, active=False, stripe=12)
    tabs = ("CODE", "SPR", "MAP")
    tx = cx + 4
    for i, t in enumerate(tabs):
        w = cv.tw(t) + 10
        chip(cv, tx, cy + 4, w, 12, t, T, on=(i == 0))
        tx += w + 4
    cv.rect(cx + 2, cy + 20, cw - 4, ch - 22, T.surface)
    lines = [[("code_keyword", "function "), ("code_text", "_draw()")],
             [("code_text", "  "), ("code_builtin", "cls"), ("code_text", "("), ("code_number", "1"), ("code_text", ")")],
             [("code_comment", "  -- the moon")],
             [("code_text", "  "), ("code_builtin", "circ"), ("code_text", "(x,"), ("code_number", "40"), ("code_text", ")")],
             [("code_text", "  "), ("code_builtin", "print"), ("code_text", "("), ("code_string", "\"hi\""), ("code_text", ")")],
             [("code_keyword", "end")]]
    for i, ln in enumerate(lines):
        lx = cx + 6
        for role, s in ln:
            for chx in s:
                cv.text(lx, cy + 26 + i * 11, chx, T.t[role], font="mono")
                lx += 6
    blocks = (("block_events", "when go"), ("block_actors", "move 10"), ("block_looks", "say hi"))
    for i, (role, s) in enumerate(blocks):
        bx, by = cx + 8 + i * 4, cy + 102 + i * 20
        w = cv.tw(s) + 14
        cv.rect(bx, by, w, 16, T.t[role])
        cv.box(bx, by, w, 16, T.shade_dark)
        cv.text(bx + 7, by + 5, s, 7 if not light_bg(T.t[role]) else 0)


def p4_appearance(T, focus="accent", focus_card=None, names=None, dot=False,
                  x=330, y=52, w=580, h=500, bar=True, icons_x=20, taskbar=False, active=True):
    cv = p4_desk(T, bar=bar, icons_x=icons_x)
    p4_code(cv, T, x=icons_x + 90)
    p4_calc(cv, T, x=icons_x + 90)
    cx, cy, cw, ch = window(cv, x, y, w, h, "Appearance", T, stripe=14, active=active)
    # THEME: the grid of previews, seeds + installed + custom
    cv.text(cx + 10, cy + 8, "THEME", T.ink)
    names = names or theme_names(T)
    gcw, gch, gap = 86, 54, 7
    for i, (name, P, tag) in enumerate(names[:12]):
        gx = cx + 10 + (i % 6) * (gcw + gap)
        gy = cy + 20 + (i // 6) * (gch + gap)
        theme_card(cv, gx, gy, gcw, gch, P, name, T, selected=(name == T.name),
                   focus=(name == focus_card), tag=tag)
    if len(names) > 12:
        cv.text(cx + cw - 70, cy + 8, "%d more  ↓" % (len(names) - 12), T.ink_dim)
    ry = cy + 20 + 2 * (gch + gap) + 4
    rows = page_rows(T, focus, cs_choices=True, on_custom=T.custom, taskbar=taskbar)
    draw_rows(cv, cx + 4, ry, cw - 8, 26, rows, T, focus)
    keys_strip(cv, cx, cy + ch - 16, cw, 16, T, status_left(T), P4_KEYS, dot=dot)
    return cv, (cx, cy, cw, ch, ry)


def theme_names(T, custom=None, installed=True, named=None):
    out = []
    if custom is not None:
        out.append(("custom", custom, None))
    for n in chrome.THEME_NAMES:
        out.append((n, look(n, T.variant), None))
    if installed:
        out.append(("sunset", look("night", T.variant, accent="CORAL", tint="EARTH"), "STORE"))
    if named is not None:
        out.insert(0, named)
    return out


def p4_accent_popover(T, cursor):
    cv, (cx, cy, cw, ch, ry) = p4_appearance(T, focus="accent", dot=True)
    rowy = ry + 26
    qx, qy, qw, qh = popover(cv, cx + cw - 300, rowy + 26, 296, 196, "ACCENT", T)
    s, gap = 26, 8
    for i, a in enumerate(ACCENTS):
        sx = qx + 12 + (i % 8) * (s + gap)
        sy = qy + 12 + (i // 8) * (s + 18)
        swatch(cv, sx, sy, s, a[0], T, focus=(a[0] == cursor), tick=(a[0] == T.t["accent"]))
        if a[0] == cursor:
            cv.ctext(sx - 10, sy + s + 4, s + 20, a[2], T.ink)
    cv.text(qx + 12, qy + 110, "titles, selection, focus and the active chip take it;", T.ink_dim)
    cv.text(qx + 12, qy + 120, "every ink is re-picked so each pair stays readable.", T.ink_dim)
    cv.text(qx + 12, qy + 136, "the desk behind is already the new look.", T.ink_dim)
    cv.text(qx + 12, qy + qh - 14, "←→↑↓ try   ENTER keep   ESC undo", T.chrome_ink_dim
            if not T.surface_light else T.ink_dim)
    return cv


def p4_tint_popover(T, cursor):
    cv, (cx, cy, cw, ch, ry) = p4_appearance(T, focus="tint", dot=True)
    rowy = ry + 52
    qx, qy, qw, qh = popover(cv, cx + cw - 330, rowy + 26, 326, 210, "TINT", T)
    tw_, th_, gap = 94, 34, 8
    for i, (name, ramp) in enumerate(TINTS):
        x = qx + 12 + (i % 3) * (tw_ + gap)
        y = qy + 10 + (i // 3) * (th_ + 20)
        ramp_tile(cv, x, y, tw_, th_, ramp, T, base=look(T.name, T.variant))
        if name == cursor:
            cv.box(x - 2, y - 2, tw_ + 4, th_ + 4, T.focus)
            cv.box(x - 3, y - 3, tw_ + 6, th_ + 6, T.shade_dark)
        cv.text(x, y + th_ + 4, name, T.ink if name == cursor else T.ink_dim)
        if name == T.tnt:
            cv.text(x + tw_ - 8, y + th_ + 4, "✓", T.ink)
    cv.text(qx + 12, qy + qh - 14, "←→↑↓ try   ENTER keep   ESC undo", T.ink_dim)
    return cv


# ==========================================================================
# EDIT TASKBAR
# ==========================================================================
def bar_items_h(cv, T, cs, items, sel=None, held=False, y=0, h=None, x0=None, edit=True):
    """A horizontal bar's items in edit mode: each item boxed, the selected
    one ringed (raised when held). Returns the item rects."""
    h = h or 18 * cs
    rects = {}
    for slot, names in items.items():
        widths = [item_w(cv, n, cs) for n in names]
        total = sum(widths) + 4 * cs * max(0, len(names) - 1)
        if slot == "left":
            x = 4 * cs if x0 is None else x0
        elif slot == "center":
            x = (cv.w - total) // 2
        else:
            x = cv.w - 4 * cs - total
        for n, w in zip(names, widths):
            iy = y + 2 * cs
            ih = h - 4 * cs
            lift = -2 if (held and n == sel) else 0
            draw_item(cv, T, n, x, iy + lift, w, ih, cs)
            if edit:
                cv.dotbox(x - 1, iy - 1 + lift, w + 2, ih + 2, T.chrome_ink_dim)
            if n == sel:
                if held:
                    cv.rect(x + 2, iy + ih + 1, w, 2, 0)
                cv.box(x - 3, iy - 3 + lift, w + 6, ih + 6, T.focus)
                cv.box(x - 4, iy - 4 + lift, w + 8, ih + 8, 0)
            rects[n] = (x, iy, w, ih)
            x += w + 4 * cs
    return rects


def item_w(cv, n, cs):
    if n == "zone":
        return cv.tw("Appearance") + 8
    if n == "chips":
        return 3 * (cv.tw("APPEARANCE") + 10) // 2 + 60
    if n == "clock":
        return cv.tw("10:24") + 4
    if n == "title":
        return cv.tw("Appearance") + 6
    if n == "text":
        return cv.tw("hello") + 6
    if n == "spacer":
        return 12 * cs
    if n == "separator":
        return 4 * cs
    return 16 * cs


def draw_item(cv, T, n, x, y, w, h, cs, vertical=False):
    cy = y + (h - 6) // 2
    iy = y + (h - 16 * cs) // 2
    if n == "zone":
        cv.text(x + 4, cy, "Appearance", T.chrome_ink)
    elif n == "title":
        cv.text(x + 3, cy, "Appearance", T.chrome_ink_dim)
    elif n == "text":
        cv.text(x + 3, cy, "hello", T.chrome_ink)
    elif n == "clock":
        if vertical:
            cv.ctext(x, y + 2, w, "10", T.chrome_ink)
            cv.ctext(x, y + 10, w, "24", T.chrome_ink)
        else:
            cv.text(x + 2, cy, "10:24", T.chrome_ink)
    elif n == "close":
        cv.icon("close", x + (w - 16 * cs) // 2, iy, cs)
    elif n == "menu":
        menu_glyph(cv, x + (w - 16 * cs) // 2, iy, cs, T.chrome_ink)
    elif n == "wifi":
        cv.icon("wifi", x + (w - 16 * cs) // 2, iy, cs)
    elif n == "battery":
        cv.icon("batt", x + (w - 16 * cs) // 2, iy, cs)
    elif n == "separator":
        if vertical:
            cv.rect(x + 2, y + h // 2, w - 4, 1, T.chrome_ink_dim)
        else:
            cv.rect(x + w // 2, y + 2, 1, h - 4, T.chrome_ink_dim)
    elif n == "spacer":
        for i in range(0, w, 4):
            cv.put(x + i, y + h // 2, T.chrome_ink_dim)
    elif n == "chips":
        if vertical:
            for i, ic in enumerate(("edit", "blocks", "gear")):
                cv.rect(x, y + i * (17 * cs), w, 16 * cs, T.accent if i == 2 else T.panel)
                cv.box(x, y + i * (17 * cs), w, 16 * cs, T.shade_dark)
                cv.icon(ic, x + (w - 16 * cs) // 2 + cs, y + i * (17 * cs), cs - 1 if cs > 1 else 1)
        else:
            cx = x
            for i, c in enumerate(("EDITOR", "CALC", "APPEARANCE")):
                cw = cv.tw(c) + 10
                cv.rect(cx, y + 1, cw, h - 2, T.accent if i == 2 else T.panel)
                cv.box(cx, y + 1, cw, h - 2, T.shade_dark)
                cv.ctext(cx, cy, cw, c, 0 if i == 2 else T.chrome_ink)
                cx += cw + 6


def edit_banner(cv, x, y, w, T, keys, title="EDIT TASKBAR"):
    cv.rect(x + 3, y + 3, w, 18, T.shade_dark)
    cv.rect(x, y, w, 18, T.accent)
    cv.box(x, y, w, 18, T.shade_dark)
    tx = cv.text(x + 8, y + 6, title, 0)
    cv.text(tx + 12, y + 6, keys, 0)


def tray(cv, x, y, w, T, items, sel=None, cs=2, title="ADD TO THE BAR"):
    rows = (len(items) + 3) // 4
    h = 22 + rows * (20 * cs // 2 + 18) + 18
    qx, qy, qw, qh = popover(cv, x, y, w, h, title, T)
    for i, n in enumerate(items):
        ix = qx + 10 + (i % 4) * ((qw - 20) // 4)
        iy = qy + 8 + (i // 4) * (20 * cs // 2 + 18)
        iw, ih = (qw - 20) // 4 - 8, 20 * cs // 2 + 6
        cv.rect(ix, iy, iw, ih, T.bar)
        cv.box(ix, iy, iw, ih, T.shade_dark)
        dw = min(iw - 4, item_w(cv, n, cs))
        draw_item(cv, T, n, ix + (iw - dw) // 2, iy + 1, dw, ih - 2, cs)
        if n == sel:
            cv.box(ix - 2, iy - 2, iw + 4, ih + 4, T.focus)
        cv.ctext(ix, iy + ih + 3, iw, n.upper(), T.ink if n == sel else T.ink_dim)
    cv.text(qx + 10, qy + qh - 14, "ENTER adds it where the bar was selected", T.ink_dim)
    return qy + qh


P4_TB_KEYS = "←→ select   SPACE grab/drop   ⌫ remove   TAB add   ENTER done"
TOP_ITEMS = {"left": ["zone"], "center": ["chips"],
             "right": ["clock", "close", "menu", "wifi", "battery"]}


def p4_taskbar_top(T):
    """Edit mode on the live top bar: MENU selected, its remove refused."""
    cv = p4_appearance(T, focus="taskbar", bar=False, taskbar=True, y=84, h=470)[0]
    cs = 2
    cv.rect(0, 0, 1024, 36, T.bar)
    cv.rect(0, 35, 1024, 1, T.bar_edge)
    rects = bar_items_h(cv, T, cs, TOP_ITEMS, sel="menu")
    # the bar's own handle: the whole bar moves with it
    cv.rect(0, 36, 1024, 2, T.focus)
    edit_banner(cv, 262, 48, 500, T, P4_TB_KEYS)
    mx, my, mw, mh = rects["menu"]
    toast(cv, mx - 250, my + mh + 40, "⌫ refused: MENU opens the system menu. it can move, not go.",
          T, bad=True)
    cv.rect(mx + mw // 2, my + mh + 6, 1, 34, T.danger)
    return cv


def p4_taskbar_left(T):
    """The whole bar moved to the left edge; ZONE kept on a slim top bar
    (it is horizontal only); the add tray open."""
    cs = 2
    cv = p4_appearance(T, focus="taskbar", bar=False, taskbar=True, icons_x=56, x=420, y=96,
                       w=580, h=496)[0]
    bw = 18 * cs
    # the slim top bar that keeps ZONE
    cv.rect(bw, 0, 1024 - bw, 32, T.bar)
    cv.rect(bw, 31, 1024 - bw, 1, T.bar_edge)
    draw_item(cv, T, "zone", bw + 8, 2, 80, 28, cs)
    cv.dotbox(bw + 7, 5, cv.tw("Appearance") + 10, 22, T.chrome_ink_dim)
    cv.text(bw + 120, 13, "ZONE is horizontal: it keeps a top bar of its own", T.chrome_ink_dim)
    # the left bar
    cv.rect(0, 0, bw, 600, T.bar)
    cv.rect(bw - 1, 0, 1, 600, T.bar_edge)
    y = 6
    order = [("menu", 32), ("close", 32), ("chips", 3 * 34), ("spacer", 0),
             ("clock", 24), ("wifi", 32), ("battery", 32)]
    sel = "clock"
    for n, h in order:
        if n == "spacer":
            y = 600 - 6 - (24 + 32 + 32 + 8)
            continue
        draw_item(cv, T, n, 2, y, bw - 4, h, cs, vertical=True)
        cv.dotbox(1, y - 1, bw - 2, h + 2, T.chrome_ink_dim)
        if n == sel:
            cv.box(-1, y - 3, bw + 2, h + 6, T.focus)
        y += h + 4
    edit_banner(cv, 60, 42, 560, T, "↑↓ select   SPACE grab/drop   ⌫ remove   TAB add   ENTER done")
    cv.text(60, 68, "the bar was grabbed (SPACE on its edge) and dropped with ←: every item kept its order.",
            T.chrome_ink if not T.surface_light else T.ink)
    tray(cv, 120, 96, 420, T, ["title", "text", "spacer", "separator"], sel="separator")
    return cv


def td_taskbar(T):
    """The T-Deck in console mode: the same mode on its one bar; the tray is
    the page under the bar (console mode opens components as pages)."""
    cv = Cv(320, 240, T.panel)
    cs = 1
    cv.rect(0, 0, 320, 18, T.bar)
    cv.rect(0, 17, 320, 1, T.bar_edge)
    items = {"left": ["zone"], "center": [], "right": ["close", "clock", "menu", "wifi", "battery"]}
    bar_items_h(cv, T, cs, items, sel="clock", held=True, x0=4)
    cv.rect(0, 18, 320, 14, T.accent)
    cv.text(6, 22, "EDIT TASKBAR", 0)
    cv.text(84, 22, "CLOCK held: ←→ move, SPACE drop", 0)
    y = 40
    cv.text(8, y, "ON THE BAR (TOP)", T.ink)
    cv.text(8, y + 12, "ZONE  |  CLOSE  CLOCK  MENU  WIFI  BATTERY", T.ink_dim)
    cv.text(8, y + 22, "MENU, ZONE and CLOSE move but stay.", T.ink_dim)
    y = 78
    cv.text(8, y, "ADD (TAB)", T.ink)
    for i, n in enumerate(["title", "text", "spacer", "separator"]):
        ix, iy = 8 + i * 76, y + 12
        cv.rect(ix, iy, 68, 18, T.bar)
        cv.box(ix, iy, 68, 18, T.shade_dark)
        dw = min(64, item_w(cv, n, cs))
        draw_item(cv, T, n, ix + (68 - dw) // 2, iy + 1, dw, 16, cs)
        cv.ctext(ix, iy + 22, 68, n.upper(), T.ink_dim)
    y = 132
    cv.text(8, y, "MOVE THE WHOLE BAR", T.ink)
    for i, e in enumerate(("TOP", "BOTTOM", "LEFT", "RIGHT")):
        chip(cv, 8 + i * 58, y + 12, 52, 14, e, T, on=(i == 0))
    cv.text(8, y + 34, "a side bar keeps ZONE on a top bar of its own.", T.ink_dim)
    cv.text(8, y + 44, "no CHIPS here: console mode has no windows.", T.ink_dim)
    keys_strip(cv, 0, 228, 320, 12, T, "custom", "⌫ remove  TAB add  ENTER done")
    return cv


# ==========================================================================
# the derivation, as a table of every role
# ==========================================================================
def roles_table(name, accent, tint):
    rows_d, why_d = derive_variant(base_row(name, "dark"), accent_of(accent), tint)
    rows_l, why_l = derive_variant(base_row(name, "light"), accent_of(accent), tint)
    bd, bl = base_row(name, "dark"), base_row(name, "light")
    roles = [k for k in bd if not isinstance(bd[k], bool)]
    T = look("night", "dark")
    half = (len(roles) + 1) // 2
    rh = 11
    w = 2 * 470 + 30
    h = 40 + half * rh + 50
    cv = Cv(w, h, 55)
    cv.text(10, 8, "%s  +  ACCENT %s  +  TINT %s   ->   custom   (every role, both variants)"
            % (name, accent, tint), 7)
    cv.text(10, 20, "role                 dark: base  ->  derived            light: base  ->  derived", 6)
    colour = {"tint": 22, "accent": 10, "ink": 11, "kept": 5}
    for i, r in enumerate(roles):
        col = i // half
        x = 10 + col * 485
        y = 34 + (i % half) * rh
        cv.text(x, y + 1, r, 7)
        for j, (b, d, wy) in enumerate(((bd, rows_d, why_d), (bl, rows_l, why_l))):
            sx = x + 120 + j * 180
            swatch(cv, sx, y, 9, b[r], T)
            cv.text(sx + 12, y + 1, str(b[r]), 6)
            cv.text(sx + 30, y + 1, "→", 5)
            swatch(cv, sx + 40, y, 9, d[r], T)
            cv.text(sx + 52, y + 1, str(d[r]), 7)
            tag = wy[r]
            cv.text(sx + 72, y + 1, tag, colour[tag])
    fy = 34 + half * rh + 8
    bad = readable(rows_d) + readable(rows_l)
    cv.text(10, fy, "tint = the background follows the tint's ramp (nearest luminance, same side of black/white)."
            "  accent = the accent or a role the base tied to it.", 6)
    cv.text(10, fy + 10, "ink = re-picked against its backgrounds.  kept = the base's value"
            " (play, author, danger, the blocks: meanings, not looks).", 6)
    cv.text(10, fy + 22, "readability pairs (theme.pairs) below theme.contrast: %s"
            % ("none" if not bad else bad), 11 if not bad else 8)
    return cv


# ==========================================================================
# the flow strip
# ==========================================================================
def strip(frames, captions, name):
    gap = 10
    w = sum(f.w for f in frames) + gap * (len(frames) + 1)
    h = max(f.h for f in frames) + 44
    cv = Cv(w, h, 55)
    x = gap
    for i, (f, cap) in enumerate(zip(frames, captions)):
        cv.paste(f, x, gap)
        cv.box(x - 1, gap - 1, f.w + 2, f.h + 2, 50)
        cv.text(x, gap + f.h + 6, "%d." % (i + 1), 10)
        yy = gap + f.h + 6
        for line in cap.split("\n"):
            cv.text(x + 14, yy, line, 7)
            yy += 9
        x += f.w + gap
    cv.save(name)


def check_all():
    """Every seed x accent x tint x variant: no failing pair."""
    bad = 0
    for n in chrome.THEME_NAMES:
        for v in ("dark", "light"):
            b = base_row(n, v)
            for a in ACCENTS:
                for t, _ in TINTS:
                    row, _ = derive_variant(b, a, t)
                    if readable(row):
                        bad += 1
                        print("unreadable", n, v, a[2], t, readable(row))
    return bad


def main():
    os.makedirs(OUT, exist_ok=True)
    print("derivation check, failing combinations:", check_all())

    night = look("night", "dark")
    night_l = look("night", "light")
    plat_l = look("platinum", "light", style="platinum")

    # --- the page ---------------------------------------------------------
    td_page(night, focus="accent").save("appearance_320x240_dark.png")
    td_page(night_l, focus="tint").save("appearance_320x240_light.png")
    p4_appearance(night, focus="accent")[0].save("appearance_1024x600_dark.png")
    p4_appearance(plat_l, focus="style")[0].save("appearance_1024x600_light.png")

    # --- the THEME grid -----------------------------------------------------
    pink = look("night", "dark", accent="PINK", tint="PLUM", custom=True)
    pink.name = "custom"
    td_theme_grid(night, theme_names(night), "night", "night").save("theme_grid_320x240.png")
    names = theme_names(pink, custom=pink)
    p4_appearance(pink, focus=None, focus_card="custom", names=names)[0].save("theme_grid_1024x600.png")

    # --- ACCENT and TINT ------------------------------------------------------
    blue = look("night", "dark", accent="BLUE")
    p4_accent_popover(blue, 12).save("accent_popover_1024x600.png")
    plum = look("night", "dark", tint="PLUM")
    p4_tint_popover(plum, "PLUM").save("tint_popover_1024x600.png")
    td_accent(look("night", "dark", accent="PINK"), 14).save("accent_320x240.png")
    td_tint(look("night", "light", tint="SAND"), "SAND").save("tint_320x240.png")
    td_wallpaper(night).save("wallpaper_320x240.png")

    # --- before / after: one accent, the whole shell --------------------------
    after = look("night", "dark", accent="MINT", tint="TEAL", custom=True)
    after.name = "custom"
    p4_appearance(after, focus="tint", dot=True,
                  names=theme_names(after, custom=after))[0].save("accent_after_1024x600.png")
    roles_table("night", "MINT", "TEAL").save("derived_roles.png")

    # --- EDIT TASKBAR -------------------------------------------------------------
    p4_taskbar_top(pink).save("taskbar_top_1024x600.png")
    p4_taskbar_left(pink).save("taskbar_left_1024x600.png")
    td_taskbar(pink).save("taskbar_320x240.png")

    # --- the flow (T-Deck) ------------------------------------------------------
    f1 = td_page(night, focus="accent")
    f2 = td_accent(look("night", "dark", accent="PINK"), 14)
    c = look("night", "dark", accent="PINK", custom=True)
    c.name = "custom"
    f3 = td_page(c, focus="name", dot=True)
    f4 = td_name(c, "bubblegum")
    named = look("night", "dark", accent="PINK")
    named.name = "bubblegum"
    f5 = td_theme_grid(named, theme_names(named, named=("bubblegum", named, None)),
                       "bubblegum", "bubblegum", toast_s="bubblegum.moy is a theme now")
    strip([f1, f2, f3, f4, f5],
          ["night is built in.\nACCENT, ENTER",
           "PINK: the page is in it\nalready. ENTER keeps it",
           "the change made CUSTOM\n(from night), saved as\nyou go. NAME… appears",
           "NAME… bubblegum,\nENTER",
           "bubblegum is in the grid;\nCUSTOM is free again"],
          "flow_320x240.png")


if __name__ == "__main__":
    main()
