"""Draws the 2026-10 look's mockups (hand-drawn, not engine renders).

usage: .venv/bin/python docs/media/look_2026-10/pixel_round3/mockups.py [OUTDIR]

Every screen is drawn at its LOGICAL size: 320x240 for the T-Deck, 512x300 for
the 1024x600 boards (chrome AND text at 2x, nearest-neighbour). Colours are the
design's RGB targets quantised to RGB565, as the panels show them; the carts'
art (covers, sprites, syntax colours, the wallpaper) stays MOY64. Fonts:
Galmuri 7/9/11/11-Bold and GalmuriMono9 (OFL-1.1, sampled into
galmuri_glyphs.json). The seed carts' real cover.png files are the art.
"""
import json
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "runtime"))
from palette import MOY64          # noqa: E402
import font as petme               # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else HERE
INDEX = []


# ==========================================================================
# colour
# ==========================================================================
def q(c):
    """RGB565 as the panel shows it."""
    if isinstance(c, str):
        c = (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))
    r, g, b = c[0] >> 3, c[1] >> 2, c[2] >> 3
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def P(i):
    return q(MOY64[i])


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


# ==========================================================================
# fonts
# ==========================================================================
GAL = json.load(open(os.path.join(HERE, "galmuri_glyphs.json")))


def _tiny5():
    d = open(os.path.join(ROOT, "native", "moy_ui", "themes", "tiny5.fnt"), "rb").read()
    w, h, first, count = d[5], d[6], d[9], d[10]
    adv = d[16:16 + count]
    off = 16 + ((count + 3) // 4) * 4
    cb = (h + 7) // 8
    return {first + i: (d[off + i * w * cb: off + (i + 1) * w * cb], adv[i], w, h)
            for i in range(count)}


TINY5 = _tiny5()
FONTS = {"cap": "Galmuri7", "body": "Galmuri9", "body11": "Galmuri11",
         "title": "Galmuri11-Bold", "code": "GalmuriMono9"}
LINE = {"cap": 9, "body": 11, "body11": 14, "title": 14, "key": 7, "mono": 8}
EXTRA = {  # glyphs Galmuri lacks
    "⏎": ("....#", "....#", ".#..#", "#####", ".#...", "....."),
    "✓": (".....", "....#", "...#.", "#.#..", ".#...", "....."),
    "⌫": ("..###", ".#..#", "#.#.#", ".#..#", "..###", "....."),
}


# ==========================================================================
# icons: 12x12, '#' ink, '+' second tone, 'a' accent
# ==========================================================================
ICONS = {
    "wifi": """
............
............
...######...
.##......##.
#...####...#
..##....##..
.#...##...#.
....#..#....
............
.....##.....
.....##.....
............""",
    "batt": """
............
............
............
##########..
#........#..
#.######.##.
#.######.##.
#.######.##.
#........#..
##########..
............
............""",
    "back": """
............
............
......#.....
.....##.....
....##......
...##.......
...##.......
....##......
.....##.....
......#.....
............
............""",
    "close": """
............
............
..#......#..
..##....##..
...##..##...
....####....
.....##.....
....####....
...##..##...
..##....##..
..#......#..
............""",
    "more": """
............
............
............
............
............
.##..##..##.
.##..##..##.
............
............
............
............
............""",
    "moy": """
............
..#######...
.#+++++++#..
.#+#+++#+#..
.#+#+++#+##.
.#+++++++++#
.#++###++++#
.#+++++++++#
.#+++++++++#
..#########.
..#.#...#.#.
............""",
    "play": """
............
............
...#........
...###......
...#####....
...#######..
...#######..
...#####....
...###......
...#........
............
............""",
    "folder": """
............
............
.####.......
#++++#......
#+++++######
############
#..........#
#..........#
#..........#
#..........#
############
............""",
    "projects": """
............
.........##.
........#aa#
.####..#aa#.
#++++##aa#..
#+++++#a#.##
#######+##.#
#.....#.#..#
#.....##...#
#..........#
############
............""",
    "brush": """
............
.........###
........##a#
.......##a#.
......##a#..
.....##a#...
....#.##....
...###......
..####......
.####.......
.##.........
............""",
    "book": """
............
.####..####.
#+++#..#+++#
#+++.##.+++#
#+++.##.+++#
#+++.##.+++#
#+++.##.+++#
#+++.##.+++#
#####..#####
.....##.....
............
............""",
    "calc": """
............
.##########.
.#++++++++#.
.#+######+#.
.#++++++++#.
.#+##+##a+#.
.#++++++++#.
.#+##+##a+#.
.#++++++++#.
.#+##+##a+#.
.##########.
............""",
    "download": """
............
.....##.....
.....##.....
.....##.....
..#..##..#..
...#.##.#...
....####....
.....##.....
#..........#
#..........#
############
............""",
    "code": """
............
............
...#....#...
..#.....#...
.#.....#..#.
#......#...#
#.....#....#
.#....#...#.
..#..#...#..
...#.#..#...
............
............""",
    "sprite": """
............
.##########.
.#........#.
.#.##..##.#.
.#.##..##.#.
.#........#.
.#.#....#.#.
.#..####..#.
.#........#.
.##########.
............
............""",
    "map": """
............
############
#...#...#..#
#.+.#...#..#
#+++#.a.#+.#
#####.#.####
#...#...#..#
#..+#...#+.#
#.+++...+++#
#####...####
############
............""",
    "note": """
............
......#####.
......#...#.
......#...#.
......#...#.
......#...#.
......#...#.
...####.###.
..#####.###.
..####......
............
............""",
    "undo": """
............
............
...#........
..##........
.#########..
..##......#.
...#.......#
...........#
..........#.
......####..
............
............""",
    "redo": """
............
............
........#...
........##..
..#########.
.#......##..
#.......#...
#...........
.#..........
..####......
............
............""",
    "pencil": """
............
.........##.
........#..#
.......#..#.
......#..#..
.....#..#...
....#..#....
...#..#.....
..#..#......
.###.#......
.####.......
............""",
    "bucket": """
............
....#.......
...#.#......
..#...#.....
.#.....#....
#.......#...
.#.....##...
..#...#.#...
...#.#..#.#.
....#...###.
.........#..
............""",
    "line": """
............
..........#.
.........#..
........#...
.......#....
......#.....
.....#......
....#.......
...#........
..#.........
.#..........
............""",
    "rect": """
............
............
.##########.
.#........#.
.#........#.
.#........#.
.#........#.
.#........#.
.#........#.
.##########.
............
............""",
    "oval": """
............
............
...######...
..#......#..
.#........#.
.#........#.
.#........#.
.#........#.
..#......#..
...######...
............
............""",
    "select": """
............
............
.##.##.##.#.
.#........#.
..........#.
.#..........
.#........#.
..........#.
.#..........
.#.##.##.##.
............
............""",
    "flip": """
............
.....##.....
..#..##..#..
.##..##..##.
###.....###.
###..##.###.
.##..##..##.
..#.....##..
.....##.....
.....##.....
............
............""",
    "rotate": """
............
....####....
...#....#...
..#......#..
..#......#..
#####....#..
.###.....#..
..#......#..
.........#..
....#####...
............
............""",
    "search": """
............
...####.....
..#....#....
.#......#...
.#......#...
.#......#...
..#....#....
...#####....
.......###..
........###.
.........##.
............""",
    "gear": """
............
.....##.....
..#.####.#..
..########..
...##..##...
.###....###.
.###....###.
...##..##...
..########..
..#.####.#..
.....##.....
............""",
    "get": """
............
.....##.....
.....##.....
...######...
....####....
.....##.....
............
.##########.
.#........#.
.##########.
............
............""",
    "doc": """
............
.#######....
.#.....##...
.#.###.#.#..
.#.....####.
.#.#####..#.
.#........#.
.#.######.#.
.#........#.
.#.####...#.
.#........#.
.##########.""",
    "trash": """
............
....####....
.##########.
............
..########..
..#.#..#.#..
..#.#..#.#..
..#.#..#.#..
..#.#..#.#..
..#.#..#.#..
...######...
............""",
    "put": """
............
.....##.....
....####....
...######...
.....##.....
.....##.....
............
.##########.
.#........#.
.##########.
............
............""",
}
ICONS = {k: [r for r in v.strip("\n").split("\n")] for k, v in ICONS.items()}


# ==========================================================================
# the canvas
# ==========================================================================
class Cv:
    def __init__(self, w, h, bg):
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w, h), q(bg))
        self.px = self.img.load()

    def put(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[x, y] = q(c)

    def get(self, x, y):
        return self.px[x, y]

    def rect(self, x, y, w, h, c):
        x0, y0, x1, y1 = max(0, x), max(0, y), min(self.w, x + w), min(self.h, y + h)
        if x1 > x0 and y1 > y0:
            self.img.paste(q(c), (x0, y0, x1, y1))

    def rrect(self, x, y, w, h, c, r=2):
        """A filled box with r pixels cut from each corner (the C corner mask)."""
        cut = {0: [], 1: [1], 2: [2, 1], 3: [3, 1, 1], 4: [4, 2, 1, 1]}[min(r, 4)]
        for i in range(h):
            k = 0
            if i < len(cut):
                k = cut[i]
            elif h - 1 - i < len(cut):
                k = cut[h - 1 - i]
            self.rect(x + k, y + i, w - 2 * k, 1, c)

    def rbox(self, x, y, w, h, c, r=2, t=1):
        """A ring the same shape as rrect, t pixels thick."""
        tmp = Cv(w, h, (1, 2, 3))
        tmp.rrect(0, 0, w, h, (250, 0, 250), r)
        tmp.rrect(t, t, w - 2 * t, h - 2 * t, (1, 2, 3), max(0, r - t))
        for yy in range(h):
            for xx in range(w):
                if tmp.px[xx, yy] == q((250, 0, 250)):
                    self.put(x + xx, y + yy, c)

    def shade(self, x, y, w, h, f, toward=(0, 0, 0)):
        """Blend a region toward a colour (the darken op GAPS.md names)."""
        for yy in range(max(0, y), min(self.h, y + h)):
            for xx in range(max(0, x), min(self.w, x + w)):
                self.px[xx, yy] = mix(self.px[xx, yy], toward, f)

    def dither(self, x, y, w, h, c, phase=0):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                if (xx + yy + phase) & 1 == 0:
                    self.put(xx, yy, c)

    # -- text ----------------------------------------------------------------
    def text(self, x, y, s, c, font="body", scale=1, upper=False):
        if upper:
            s = s.upper()
        for ch in s:
            x += self._glyph(x, y, ch, c, font, scale)
        return x

    def tw(self, s, font="body", scale=1):
        return sum(self._adv(ch, font) for ch in s) * scale

    def _adv(self, ch, font):
        if ch in EXTRA:
            return 6 if font != "code" else 5
        if font == "mono":
            return 8
        if font == "key":
            g = TINY5.get(ord(ch))
            return (g[1] if g else 3) + 1
        g = GAL[FONTS[font]]["glyphs"].get(ch)
        return g[0] if g else 4

    def _glyph(self, x, y, ch, c, font, s):
        if ch in EXTRA:
            base = {"cap": 2, "body": 3, "body11": 4, "title": 4, "key": 0, "code": 3}.get(font, 1)
            for yy, r in enumerate(EXTRA[ch]):
                for xx, b in enumerate(r):
                    if b == "#":
                        self.rect(x + xx * s, y + (yy + base) * s, s, s, c)
            return 6 * s
        if font == "mono":
            cols = petme.glyph(ch)
            for j in range(8):
                for i in range(8):
                    if (cols[j] >> i) & 1:
                        self.rect(x + j * s, y + i * s, s, s, c)
            return 8 * s
        if font == "key":
            g = TINY5.get(ord(ch))
            if not g:
                return 4 * s
            cols, adv, w, h = g
            for j in range(w):
                for i in range(h):
                    if (cols[j] >> i) & 1:
                        self.rect(x + j * s, y + (i - 1) * s, s, s, c)
            return (adv + 1) * s
        g = GAL[FONTS[font]]["glyphs"].get(ch)
        if not g:
            return 4 * s
        adv, x0, y0, rows = g
        for yy, r in enumerate(rows):
            for xx, b in enumerate(r):
                if b == "#":
                    self.rect(x + (x0 + xx) * s, y + (y0 + yy) * s, s, s, c)
        return adv * s

    def ctext(self, x, y, w, s, c, font="body", scale=1):
        return self.text(x + (w - self.tw(s, font, scale)) // 2, y, s, c, font, scale)

    def rtext(self, x, y, s, c, font="body", scale=1):
        return self.text(x - self.tw(s, font, scale), y, s, c, font, scale)

    def icon(self, name, x, y, ink, second=None, accent=None, s=1):
        for yy, row in enumerate(ICONS[name]):
            for xx, ch in enumerate(row):
                c = {"#": ink, "+": second, "a": accent if accent is not None else ink}.get(ch)
                if c is not None:
                    self.rect(x + xx * s, y + yy * s, s, s, c)

    def paste(self, img, x, y):
        self.img.paste(img, (x, y))

    def save(self, name, what, theme, size, x3=False, up=1):
        img = self.img
        if up > 1:
            img = img.resize((self.w * up, self.h * up), Image.NEAREST)
        path = os.path.join(OUT, name + ".png")
        img.save(path)
        INDEX.append(f"{name}.png — {what}; {theme}; {img.width}x{img.height}")
        if x3:
            img.resize((img.width * 3, img.height * 3), Image.NEAREST).save(
                os.path.join(OUT, name + "@3x.png"))
            INDEX.append(f"{name}@3x.png — same, enlarged 3x (nearest)")
        print("wrote", path)


# ==========================================================================
# themes: six neutral steps, one accent (focus and the primary action) and a
# value colour (sliders, toggles, the current tab, the calculator's display)
# ==========================================================================
def theme(name, mode, **kw):
    T = dict(name=name, mode=mode, radius=3, shadow="hard", outline=0, frame="soft",
             tabs="underline", body="body", cap="cap", title="title", sections=None,
             upper=False, desk_pattern=None, edge=None, modes=("dark", "light"),
             syn=None, dim_cards=0.4, halo=False)
    T.update(kw)
    T.setdefault("desk", T["bg"])
    T.setdefault("inset", T["bg"])
    T.setdefault("on_accent", T["bg"] if mode == "dark" else T["text"])
    T.setdefault("accent_text", T["accent"])
    T.setdefault("value", T["accent"])
    T.setdefault("value_ink", T["bg"])
    T.setdefault("sel_rest", T["raised"])
    T.setdefault("lcd", T["inset"])
    T.setdefault("lcd_ink", T["value"])
    T.setdefault("strip_on", T["value"])
    T.setdefault("strip_on_ink", T["value_ink"])
    T.setdefault("under", mix(T["surface"], "#000000", 0.45) if mode == "dark" else T["line"])
    T.setdefault("group_edge", None)
    T.setdefault("cur_line", T["sel_rest"])
    return T


SYN_DARK = {"t": "#eceaf8", "c": "#8a90c0", "k": "#c9a2ff", "f": "#7fd0ff", "n": "#ffa64d",
            "s": "#8ee08e"}
SYN_LIGHT = {"t": "#1b1934", "c": "#5f6385", "k": "#6a35c4", "f": "#1662a8", "n": "#a8510a",
             "s": "#23793a"}

MOY_DARK = theme(
    "Moy", "dark", bg="#11122a", surface="#1c1e40", raised="#2a2d55", edge="#4a4e86",
    line="#30335c", dim="#9196c2", text="#eceaf8", accent="#ffc53d", value="#b3a6f2",
    value_ink="#11122a", shadow_c="#06071a", lcd="#0b0c20", syn=SYN_DARK,
    group_edge="#5c5f96", sel_rest="#2c2f5c", desk_pattern="stars", halo=True,
    stars=("#30335c", "#5c5f96"))
MOY_LIGHT = theme(
    "Moy", "light", bg="#efecf8", surface="#faf9fd", raised="#ffffff", line="#dcd7ee",
    dim="#5a5487", text="#1d1747", accent="#d45f00", on_accent="#ffffff",
    accent_text="#a34800", value="#4b3bb8", value_ink="#ffffff", shadow_c="#cdc6ea",
    lcd="#e6e1f7", lcd_ink="#2b2470", syn=SYN_LIGHT, under="#8a80c4",
    group_edge="#8a80c4", sel_rest="#e6e1f8", desk_pattern="stars", halo=True,
    stars=("#d6cff0", "#a99de0"), dim_cards=0)
PAPER = theme(
    "Paper", "light", bg="#c4c6c0", surface="#e9eae6", raised="#f6f6f3", line="#1a1a1a",
    dim="#4f514a", text="#121212", accent="#121212", on_accent="#f6f6f3", value="#121212",
    value_ink="#f6f6f3", shadow_c="#121212", shadow="dither", outline=1, frame="paper",
    desk_pattern="dots", sel_rest="#d3d4ce", under="#121212", syn=SYN_LIGHT, dim_cards=0,
    lcd="#d3d5cd", lcd_ink="#121212", modes=("light",))
STUDIO = theme(
    "Studio", "dark", bg="#000000", surface="#101010", raised="#1e1e1e", line="#555555",
    dim="#8f8f8f", text="#f2f2f2", accent="#ff5a1f", on_accent="#000000", value="#f2f2f2",
    value_ink="#000000", shadow="none", radius=0, frame="studio", upper=True,
    modes=("dark",), syn=SYN_DARK, sel_rest="#262626", dim_cards=0.45)
CONTRAST = theme(
    "Contrast", "dark", bg="#000000", surface="#000000", raised="#000000", line="#ffffff",
    dim="#d6d6d6", text="#ffffff", accent="#ffe500", on_accent="#000000", value="#ffffff",
    value_ink="#000000", shadow="none", outline=2, radius=2, frame="contrast",
    body="body11", cap="body", sel_rest="#3a3a3a", syn=SYN_DARK, strip_on="#ffffff",
    strip_on_ink="#000000", under="#ffffff", dim_cards=0)
CONTRAST_LIGHT = theme(
    "Contrast", "light", bg="#ffffff", surface="#ffffff", raised="#ffffff", line="#000000",
    dim="#2a2a2a", text="#000000", accent="#ffe500", on_accent="#000000", value="#000000",
    value_ink="#ffffff", shadow="none", outline=2, radius=2, frame="contrast",
    body="body11", cap="body", sel_rest="#d8d8d8", syn=SYN_LIGHT, strip_on="#000000",
    strip_on_ink="#ffffff", under="#000000", dim_cards=0)
KEPT = (MOY_DARK, PAPER, STUDIO, CONTRAST)
VARIANTS = {("Moy", "dark"): MOY_DARK, ("Moy", "light"): MOY_LIGHT,
            ("Paper", "light"): PAPER, ("Studio", "dark"): STUDIO,
            ("Contrast", "dark"): CONTRAST, ("Contrast", "light"): CONTRAST_LIGHT}


# ==========================================================================
# components
# ==========================================================================
def top_edge(cv, x, y, w, c, r):
    k = {0: 0, 1: 1, 2: 2, 3: 3}[min(r, 3)]
    cv.rect(x + k, y, w - 2 * k, 1, c)


def drop(cv, x, y, w, h, T, r=3, depth=2):
    """Elevation: a hard pixel drop (Moy), a dithered one (Paper); Studio and
    Contrast are flat and carry an edge instead."""
    if T["shadow"] == "hard":
        cv.rrect(x + depth, y + depth, w, h, T["shadow_c"], r)
    elif T["shadow"] == "dither":
        cv.dither(x + depth, y + h, w, depth, T["shadow_c"])
        cv.dither(x + w, y + depth, depth, h, T["shadow_c"])


def raised_box(cv, x, y, w, h, T, fill=None, r=None):
    r = max(0, T["radius"] - 1) if r is None else r
    fill = fill or T["raised"]
    if not T["outline"] and T["frame"] != "studio":
        cv.rrect(x, y + 1, w, h, T["under"], r)
    cv.rrect(x, y, w, h, fill, r)
    if T["edge"] and fill == T["raised"]:
        top_edge(cv, x, y, w, T["edge"], r)
    if T["outline"]:
        cv.rbox(x, y, w, h, T["line"], r, T["outline"] if T["frame"] != "paper" else 1)
    elif T["frame"] == "studio":
        cv.rbox(x, y, w, h, T["line"], 0, 1)


def group(cv, x, y, w, h, T):
    """A group of rows: one surface. Moy dark catches the moon on its top edge,
    Moy light sits on a shadow line: either edge is 3:1 against the screen."""
    cv.rrect(x, y, w, h, T["surface"], T["radius"])
    if T["outline"]:
        cv.rbox(x, y, w, h, T["line"], T["radius"], T["outline"] if T["frame"] != "paper" else 1)
    elif T["frame"] == "studio":
        cv.rbox(x, y, w, h, T["line"], 0, 1)
    elif T["group_edge"]:
        if T["mode"] == "dark":
            top_edge(cv, x, y, w, T["group_edge"], T["radius"])
        else:
            top_edge(cv, x, y + h - 1, w, T["group_edge"], T["radius"])


def keycap(cv, x, y, label, T):
    """A key legend: an outlined cap (its edge 3:1 or better) with a heavier
    bottom, grown to the legend's font."""
    f = T["cap"]
    h = 11 if f == "cap" else 15
    w = max(h, cv.tw(label, f) + 6)
    ring = T["dim"] if not T["outline"] else T["line"]
    cv.rbox(x, y, w, h, ring, 1, 1)
    cv.rect(x + 1, y + h - 1, w - 2, 1, ring)
    cv.rect(x + 1, y + h - 2, w - 2, 1, ring)
    cv.ctext(x, y + (0 if f == "cap" else 1), w, label, T["text"], f)
    return w


def hints(cv, x, y, items, T, right=()):
    f = T["cap"]
    h = 11 if f == "cap" else 15
    if f != "cap":
        y -= 4

    def run(x, items, draw=True):
        for k, label in items:
            k, label = U(T, k), U(T, label)
            x += (keycap(cv, x, y, k, T) if draw else max(h, cv.tw(k, f) + 6)) + 3
            if label:
                if draw:
                    cv.text(x, y + (0 if f == "cap" else 2), label, T["dim"], f)
                x += cv.tw(label, f) + 8
        return x
    run(x, items)
    if right:
        w = run(0, right, draw=False)
        run(cv.w - 8 - w + 8, right)


def status(cv, T, right_x, y=4, ink=None):
    ink = ink or T["text"]
    x = cv.rtext(right_x, y, "14:32", ink, "body") - cv.tw("14:32", "body") - 6
    cv.icon("batt", x - 12, y, ink, ink)
    cv.icon("wifi", x - 28, y, ink)


def tab_row(cv, x, y, names, cur, T, gap=12):
    for n in names:
        on = n == cur
        label = U(T, n)
        w = cv.tw(label, "body")
        cv.text(x, y, label, T["text"] if on else T["dim"], "body")
        if on:
            cur_bar(cv, x, y + 13, w, T)
        x += w + gap
    return x


def _chev(cv, x, y, c, left=False):
    for dx, dy in ((0, 0), (1, 1), (2, 2), (3, 3), (2, 4), (1, 5), (0, 6)):
        cv.put(x + (3 - dx if left else dx), y + dy, c)


def toggle(cv, x, y, on, T, inv=False):
    track = T["value"] if on else T["line"]
    knob = T["value_ink"] if on else T["text"]
    if inv:
        track, knob = (T["on_accent"], T["accent"]) if on else (mix(T["accent"], T["on_accent"], 0.4), T["on_accent"])
    cv.rrect(x, y, 20, 10, track, 4)
    if T["mode"] == "light" and not on and not inv:
        knob = "#ffffff"
    cv.rrect(x + (11 if on else 1), y + 1, 8, 8, knob, 3)


def slider(cv, x, y, w, v, T, inv=False):
    base, fill, knob = T["line"] if T["mode"] == "dark" else "#b9b3d3", T["value"], T["text"]
    if inv:
        base, fill, knob = mix(T["accent"], T["on_accent"], 0.35), T["on_accent"], T["on_accent"]
    cv.rrect(x, y, w, 3, base, 1)
    cv.rrect(x, y, int(w * v), 3, fill, 1)
    kx = x + int(w * v) - 3
    cv.rrect(kx, y - 3, 7, 9, knob if T["mode"] == "dark" or inv else T["value"], 2)


def seg(cv, rx, y, opts, cur, T):
    ws = [cv.tw(o, T["body"]) + 14 for o in opts]
    x = rx - sum(ws)
    for o, w in zip(opts, ws):
        on = o == cur
        cv.ctext(x, y + 2, w, o, T["text"] if on else T["dim"], T["body"])
        if on:
            cur_bar(cv, x + 5, y + 14, w - 10, T)
        x += w


def soon_tag(cv, x, y, T):
    w = cv.tw("Soon", "cap") + 8
    cv.rbox(x, y, w, 11, T["dim"], 1)
    cv.text(x + 4, y, "Soon", T["dim"], "cap")


def row(cv, x, y, w, label, T, value=None, kind="link", focus=False, h=20, last=False):
    """FOCUS on a row: the row is raised and ringed in the accent; ink stays ink."""
    ink, dimk = T["text"], T["dim"]
    if focus:
        cv.rrect(x + 3, y + 3, w - 6, h - 6, T["raised"] if T["frame"] != "paper"
                 else T["raised"], max(0, T["radius"] - 1))
        cv.rbox(x + 1, y + 1, w - 2, h - 2, T["accent"], max(0, T["radius"]), 2)
        if T["frame"] == "contrast" and T["mode"] == "light":
            cv.rbox(x, y, w, h, "#000000", max(0, T["radius"]) + 1, 1)
    elif not last:
        cv.rect(x + 8, y + h - 1, w - 16, 1, T["line"] if T["frame"] not in ("paper",)
                else "#9d9e98")
    ty = y + (h - 11) // 2 + (0 if T["body"] == "body" else -1)
    cv.text(x + 8, ty, U(T, label), ink, T["body"])
    rx = x + w - 8
    if kind == "link":
        _chev(cv, rx - 4, ty + 2, dimk)
        if value:
            cv.rtext(rx - 10, ty, value, dimk, T["body"])
    elif kind == "info":
        cv.rtext(rx, ty, value, dimk, T["body"])
    elif kind == "cycle":
        vw = cv.tw(value, T["body"])
        _chev(cv, rx - 4, ty + 2, ink)
        cv.text(rx - 12 - vw, ty, value, ink, T["body"])
        _chev(cv, rx - 22 - vw, ty + 2, ink, left=True)
    elif kind == "toggle":
        cv.rtext(rx - 26, ty, "On" if value else "Off", dimk, T["body"])
        toggle(cv, rx - 20, ty + 1, value, T)
    elif kind == "slider":
        slider(cv, rx - 84, ty + 4, 84, value, T)
    elif kind == "seg":
        seg(cv, rx, ty - 2, value[0], value[1], T)
    elif kind == "swatches":
        sw, cur = value
        xx = rx - len(sw) * 14 + 4
        for i, c in enumerate(sw):
            cv.rrect(xx, ty - 1, 11, 11, c, 2)
            if ratio(c, T["surface"]) < 3:
                cv.rbox(xx, ty - 1, 11, 11, T["dim"], 2)
            if i == cur:
                cur_bar(cv, xx, ty + 12, 11, T)
            xx += 14


def button(cv, x, y, w, h, label, T, kind="secondary", icon=None):
    """Primary is the value colour, never amber: amber is focus alone."""
    if kind == "primary":
        fill, ink = T["value"], T["value_ink"]
        if not T["outline"] and T["frame"] != "studio":
            cv.rrect(x, y + 1, w, h, mix(fill, "#000000", 0.4), max(0, T["radius"] - 1))
        cv.rrect(x, y, w, h, fill, max(0, T["radius"] - 1))
        if T["outline"]:
            cv.rbox(x, y, w, h, T["line"], max(0, T["radius"] - 1), T["outline"])
    else:
        raised_box(cv, x, y, w, h, T)
        ink = T["text"]
    tw = cv.tw(label, T["body"]) + (14 if icon else 0)
    tx = x + (w - tw) // 2
    if icon:
        cv.icon(icon, tx, y + (h - 12) // 2, ink)
        tx += 14
    cv.text(tx, y + (h - 11) // 2 - (1 if T["body"] != "body" else 0), label, ink, T["body"])


# ==========================================================================
# covers, thumbnails, file icons
# ==========================================================================
def _src(name):
    return os.path.join(ROOT, "system_carts", f"moybyte.{name}.moy", "cover.png")


def cover(name, n=64):
    img = Image.open(_src(name)).convert("RGB")
    x, y = CROPS.get(name, {}).get(n, ((128 - n) // 2, (128 - n) // 2))
    c = img.crop((x, y, x + n, y + n))
    out = Image.new("RGB", c.size)
    out.putdata([q(p) for p in list(c.getdata())])
    return out


# the curated window of each cover, per crop size (no HUD, the subject centred)
CROPS = {
    "star_catcher": {64: (43, 52), 48: (48, 64)},
    "sky_run": {64: (8, 44), 48: (24, 60)},
    "hop_quest": {64: (52, 36), 48: (72, 36)},
    "pixel_pet": {64: (24, 50), 48: (32, 56)},
    "harpoon_pop": {64: (18, 64), 48: (16, 80)},
    "brick_siege": {64: (2, 20), 48: (10, 24)},
    "bullet_storm": {64: (32, 8), 48: (40, 12)},
    "letter_blitz": {64: (2, 18), 48: (10, 24)},
    "tiny_runner": {64: (0, 60), 48: (0, 76)},
}


def card(cv, name, x, y, size, T, focus=False, dimmed=False, n=64):
    """Neighbours keep their colours: dark themes darken them a step, light
    themes leave the art alone and let size and position carry depth."""
    img = cover(name, n)
    if size != n:
        img = img.resize((size, size), Image.NEAREST)
    r = 3 if T["radius"] else 0
    if T["frame"] == "paper":
        m = 4 if focus else 3
        d = 3 if focus else 2
        cv.dither(x - m + d, y + size + m, size + 2 * m, d, T["shadow_c"])
        cv.dither(x + size + m, y - m + d, d, size + 2 * m, T["shadow_c"])
        cv.rrect(x - m, y - m, size + 2 * m, size + 2 * m, T["raised"], 3)
        cv.rbox(x - m, y - m, size + 2 * m, size + 2 * m, T["line"], 3, 1)
        r = 0
    elif focus:
        drop(cv, x, y, size, size, T, r, 3)
    mask = Image.new("L", (size, size), 255)
    for i, k in enumerate({0: [], 1: [1], 2: [2, 1], 3: [3, 1, 1]}[r]):
        for xx in list(range(k)) + list(range(size - k, size)):
            mask.putpixel((xx, i), 0)
            mask.putpixel((xx, size - 1 - i), 0)
    cv.img.paste(img, (x, y), mask)
    if dimmed and T["dim_cards"] and T["mode"] == "dark":
        cv.shade(x, y, size, size, T["dim_cards"], "#000000")
    if T["frame"] == "studio":
        cv.rbox(x, y, size, size, T["line"], 0, 1)
    if T["outline"] and T["frame"] != "paper":
        cv.rbox(x, y, size, size, T["line"], r, 1)
    if focus:
        m = 4 if T["frame"] == "paper" else 0
        focus_ring(cv, x - m, y - m, size + 2 * m, size + 2 * m, T, 3 if r or m else 0)


FALLBACK = {"coin_quest": (10, 0), "pin_light": (8, 7)}


def file_icon(cv, name, x, y, T):
    """Each file's own picture: its cover, its app icon, or a title tile."""
    if os.path.exists(_src(name)):
        img = cover(name, 64).resize((12, 12), Image.BOX)
        img.putdata([q(p) for p in list(img.getdata())])
        cv.paste(img, x, y)
        for xx, yy in ((0, 0), (11, 0), (0, 11), (11, 11)):
            cv.put(x + xx, y + yy, cv.get(x - 1, y))
        return
    if name in FALLBACK:
        bgc, ink = FALLBACK[name]
        cv.rrect(x, y, 12, 12, P(bgc), 1)
        cv.ctext(x, y + 1, 12, name[0].upper(), P(ink), "cap")
        return
    if name == "moy_night":
        cv.rrect(x, y, 12, 12, P(1), 1)
        for dx, dy in ((7, 2), (8, 2), (6, 3), (6, 4), (6, 5), (7, 6), (8, 6)):
            cv.put(x + dx, y + dy, P(10))
        cv.rect(x, y + 9, 12, 3, P(0))
        cv.put(x + 2, y + 3, P(7))
        return
    ic = {"calc": "calc", "notes": "doc", "my_art": "brush"}.get(name, "doc")
    raised_box(cv, x, y, 12, 12, T, r=1)
    cv.icon(ic, x, y, T["text"], T["dim"], T["accent"])


# ==========================================================================
# the handheld home
# ==========================================================================
TABS = ("Games", "Mine", "Apps", "Make")
SHELF = ("pixel_pet", "star_catcher", "sky_run", "hop_quest", "harpoon_pop")


def bar_line(cv, T, W):
    if T["frame"] == "paper":
        cv.rect(0, 19, W, 1, T["line"])
    elif T["outline"]:
        cv.rect(0, 18, W, 2, T["line"])




def meta(cv, x, y, w, T):
    t = "Platformer  ·  Built in  ·  Played today"
    cv.ctext(x, y, w, t.upper().replace("·", "/") if T["upper"] else t, T["dim"], T["cap"])


HOME_HINTS = (("⏎", "Play"), ("C", "Change"), ("/", "Search"))


def home(cv, T, W, H, wall=False):
    if T["desk_pattern"] and not wall:
        desk_bg(cv, T, W, H)
    if wall:
        cv.rect(0, 0, W, 20, T["bg"])
    bar_line(cv, T, W)
    moy_button(cv, T)
    tab_row(cv, 34, 4, TABS, "Games", T)
    status(cv, T, W - 8)
    n, hero, gap = 64, 128, 12
    cx = W // 2
    hy = 34
    hx = cx - hero // 2
    step = n + gap
    for i, name in enumerate(SHELF):
        d = i - 2
        if d == 0:
            continue
        nx = (hx - gap - n) + (d + 1) * step if d < 0 else hx + hero + gap + (d - 1) * step
        card(cv, name, nx, hy + n // 2, n, T, dimmed=True, n=n)
    card(cv, "sky_run", hx, hy, hero, T, focus=True, n=n)
    ty = hy + hero + 12
    cv.ctext(0, ty, W, U(T, "Sky Run"), T["text"], T["title"])
    meta(cv, 0, ty + 16, W, T)
    hints(cv, 8, H - 14, HOME_HINTS, T, right=(("L", ""), ("R", "Tabs")))


# ==========================================================================
# the 1024x600 home: hero, details, the shelf
# ==========================================================================
def big_home(cv, T, W, H):
    if T["desk_pattern"]:
        desk_bg(cv, T, W, H)
    bar_line(cv, T, W)
    moy_button(cv, T)
    tab_row(cv, 34, 4, TABS, "Games", T)
    status(cv, T, W - 28)
    cv.icon("close", W - 18, 4, T["text"])
    hx, hy = 24, 32
    card(cv, "sky_run", hx, hy, 128, T, n=64)
    dx = 172
    cv.text(dx, hy - 2, U(T, "Sky Run"), T["text"], T["title"], 2)
    t = "Platformer  ·  Built in  ·  Played today"
    cv.text(dx, hy + 28, t.upper().replace("·", "/") if T["upper"] else t, T["dim"], T["cap"])
    desc = ("Run and jump across the hills, grab every coin before the",
            "clouds roll in, and find the flag at the end of the day.")
    for i, line in enumerate(desc):
        cv.text(dx, hy + 44 + i * 13, line, T["text"], T["body"])
    sy = hy + 78
    for i, (k, v) in enumerate((("Played", "12 times"), ("Last", "Today"), ("Size", "21 KB"),
                                ("By", "Moybyte"))):
        sx = dx + i * 78
        cv.text(sx, sy, U(T, k), T["dim"], T["cap"])
        cv.text(sx, sy + 10, v, T["text"], T["body"])
    by = hy + 108
    button(cv, dx, by, 76, 22, U(T, "Play"), T, "primary", icon="play")
    button(cv, dx + 82, by, 76, 22, U(T, "Change"), T)
    button(cv, dx + 164, by, 64, 22, U(T, "Copy"), T)
    sy = 196
    t = "Games  ·  11"
    cv.text(24, sy - 15, t.upper().replace("·", "/") if T["upper"] else t, T["dim"], T["cap"])
    names = ("pixel_pet", "star_catcher", "sky_run", "hop_quest", "harpoon_pop",
             "brick_siege", "bullet_storm", "letter_blitz")
    x = 24
    for nm in names:
        card(cv, nm, x, sy, 64, T, focus=nm == "sky_run", dimmed=nm != "sky_run", n=64)
        x += 74
    hints(cv, 12, H - 14, HOME_HINTS, T, right=(("L", ""), ("R", "Tabs")))


# ==========================================================================
# pages
# ==========================================================================
def topbar_page(cv, T, W, title, H=240):
    if T["desk_pattern"]:
        desk_bg(cv, T, W, H)
    bar_line(cv, T, W)
    cv.icon("back", 6, 4, T["dim"])
    cv.text(20, 4, title.upper() if T["upper"] else title, T["text"], "body")
    status(cv, T, W - 8)


def section(cv, x, y, label, T):
    cv.text(x + 8, y, label.upper() if T["upper"] else label, T["dim"], T["cap"])


def settings(cv, T, W, H):
    topbar_page(cv, T, W, "Settings", H)
    x, w = 8, W - 16
    y = 24
    section(cv, x, y, "General", T)
    y += 11
    group(cv, x, y, w, 60, T)
    row(cv, x, y, w, "Wi-Fi", T, True, "toggle")
    row(cv, x, y + 20, w, "Appearance", T, "Moy", "link", focus=True)
    row(cv, x, y + 40, w, "Text size", T, "Medium", "cycle", last=True)
    y += 66
    section(cv, x, y, "About", T)
    y += 11
    group(cv, x, y, w, 80, T)
    for i, (k, v) in enumerate((("Moybyte", "0.9.4"), ("Board", "T-Deck Plus"),
                                ("Storage", "1.2 MB free"), ("Battery", "82%"))):
        row(cv, x, y + i * 20, w, k, T, v, "info", last=i == 3)
    hints(cv, 8, H - 14, (("⏎", "Open"), ("Esc", "Back")), T)


def mini_home(cv, x, y, V, line):
    """A theme's preview: one 64x48 drawing at every size, in the variant the
    Mode row picks."""
    w, h = 64, 48
    cv.rrect(x, y, w, h, V["bg"], 2)
    if V["desk_pattern"] == "dots":
        for yy in range(y + 12, y + h - 2, 4):
            for xx in range(x + 2, x + w - 2, 4):
                cv.put(xx, yy, mix(V["bg"], V["text"], 0.25))
    elif V["desk_pattern"] == "stars":
        for sx, sy in ((6, 14), (55, 12), (12, 40), (52, 38), (30, 44)):
            cv.put(x + sx, y + sy, V["stars"][1])
    tab = V["sections"]["Games"] if V["sections"] else (V["value"] if V["frame"] != "contrast"
                                                         else V["text"])
    cv.rect(x + 4, y + 4, 10, 2, V["text"])
    cv.rect(x + 4, y + 7, 10, 1, tab)
    cv.rect(x + 17, y + 4, 7, 2, V["dim"])
    cv.rect(x + 27, y + 4, 7, 2, V["dim"])
    cv.rect(x + w - 9, y + 4, 5, 2, V["dim"])
    cs = 24
    cxx = x + (w - cs) // 2
    side = mix(V["bg"], "#29adff", 0.5) if V["dim_cards"] else "#29adff"
    cv.rect(x + 4, y + 18, 12, 12, side)
    cv.rect(x + w - 16, y + 18, 12, 12, side)
    if V["frame"] == "paper":
        cv.rect(cxx - 2, y + 11, cs + 4, cs + 4, V["line"])
        cv.rect(cxx - 1, y + 12, cs + 2, cs + 2, V["raised"])
    else:
        cv.rect(cxx - 2, y + 11, cs + 4, cs + 4, V["accent"])
    cv.rect(cxx, y + 13, cs, cs, "#29adff")
    cv.rect(cxx, y + 13 + 16, cs, 8, "#008751")
    cv.rect(cxx + 10, y + 13 + 11, 4, 5, "#ff004d")
    cv.rect(x + w // 2 - 8, y + h - 7, 16, 2, V["text"])
    cv.rbox(x, y, w, h, line, 2, 1)


def accents(T):
    if T["mode"] == "light":
        return ("#d45f00", "#d13f2a", "#c2306f", "#6f4fd8", "#0a72bd", "#0d8160", "#4c8417",
                T["text"])
    return ("#ffc53d", "#ff7a59", "#f2719f", "#a98bff", "#5cc4ff", "#55d6a6", "#9fd36a",
            T["text"])


def appearance(cv, T, x, y, w, desk=False):
    section(cv, x, y, "Theme", T)
    y += 14
    n = len(KEPT)
    tw, th = 64, 48
    g = min((w - 16 - n * tw) // (n - 1), 24)
    x0 = x + 8 + (w - 16 - (n * tw + (n - 1) * g)) // 2
    edge = T["group_edge"] or T["line"]
    if T["frame"] == "studio" or T["outline"]:
        edge = T["line"]
    for i, Tk in enumerate(KEPT):
        nm = Tk["name"]
        tx = x0 + i * (tw + g)
        V = VARIANTS.get((nm, T["mode"])) or VARIANTS[(nm, Tk["modes"][0])]
        mini_home(cv, tx, y, V, edge)
        on = nm == T["name"]
        cv.ctext(tx - 8, y + th + 5, tw + 16, nm, T["text"] if on else T["dim"], T["body"])
        if on:
            cur_bar(cv, tx, y + th + 17, tw, T)
        modes = [m for m in ("dark", "light") if (nm, m) in VARIANTS]
        cap = "Dark · Light" if len(modes) == 2 else f"{modes[0].title()} only"
        cv.ctext(tx - 8, y + th + 20, tw + 16, cap, T["dim"], T["cap"])
    y += th + 33
    rows = [("Mode", (("Dark", "Light"), T["mode"].title()), "seg"),
            ("Accent", (accents(T), 0), "swatches"),
            ("Tint", "Night", "cycle"),
            ("Wallpaper", "Moy's night", "link")]
    if desk:
        rows.append(("Window style", "Soft", "cycle"))
    rows.append(("Code font", "Galmuri Mono", "cycle"))
    rh = 20 if not desk else 17
    group(cv, x + 8, y, w - 16, rh * len(rows), T)
    for i, (label, value, kind) in enumerate(rows):
        row(cv, x + 8, y + i * rh, w - 16, label, T, value, kind, focus=label == "Tint",
            last=i == len(rows) - 1, h=rh)
    return y + rh * len(rows)


def appearance_page(cv, T, W, H):
    topbar_page(cv, T, W, "Appearance", H)
    appearance(cv, T, 0, 24, W)
    hints(cv, 8, H - 14, (("⏎", "Open"), ("Esc", "Back")), T)


# ==========================================================================
# the editors
# ==========================================================================
CODE = [
    ("c", "# Star Catcher: catch the falling stars before they reach the ground"),
    ("", ""),
    ("k", "def ", "f", "_spawn", "t", "(s):"),
    ("t", "    s[", "n", "0", "t", "] = rnd(W - ", "n", "8", "t", ")"),
    ("t", "    s[", "n", "1", "t", "] = -rnd(H * ", "n", "0.5", "t", ") - ", "n", "8"),
    ("c", "    # speed ramps with score: harder the better you do"),
    ("t", "    s[", "n", "2", "t", "] = _base_speed() * (", "n", "0.7", "t", " + rnd(",
     "n", "0.6", "t", ")) * (", "n", "1.0", "t", " + score * ", "n", "0.02", "t", ")"),
    ("", ""),
    ("k", "def ", "f", "_init", "t", "():"),
    ("t", "    background(col(", "s", "\"black\"", "t", "))  ", "c", "# the night sky"),
    ("k", "    global ", "t", "score, combo, lives, bx, stars, catcher"),
    ("t", "    score = ", "n", "0"),
    ("t", "    combo = ", "n", "0"),
    ("t", "    lives = LIVES  ", "c", "# l 1 I | ( ) all differ"),
    ("t", "    bx = W / ", "n", "2", "t", " - BW / ", "n", "2"),
    ("k", "    for ", "t", "_i ", "k", "in ", "t", "range(int(cfg(", "s", "\"star_count\"",
     "t", ", ", "n", "5", "t", "))):"),
    ("t", "        s = [", "n", "0", "t", ", ", "n", "0", "t", ", ", "n", "1", "t", "]"),
]


def editor_bar(cv, T, W, cur):
    bar_line(cv, T, W)
    cv.icon("back", 4, 4, T["dim"])
    cv.text(17, 4, "Star Catcher", T["text"], "body")
    x = 100
    for name, ic in (("Code", "code"), ("Sprites", "sprite"), ("Map", "map"), ("Sound", "note")):
        if name == cur:
            w = cv.tw(name, "body") + 16
            cv.icon(ic, x, 4, T["text"])
            cv.text(x + 14, 4, name, T["text"], "body")
            cur_bar(cv, x, 17, w - 2, T)
            x += w + 4
        else:
            cv.icon(ic, x, 4, T["dim"])
            x += 17
    x += 4
    cv.icon("undo", x, 4, T["dim"])
    cv.icon("redo", x + 16, 4, T["line"] if T["mode"] == "dark" else "#b3aec8")
    button(cv, W - 46, 2, 40, 16, "Run", T, "primary", icon="play")


def code_editor(cv, T, W, H):
    editor_bar(cv, T, W, "Code")
    syn = T["syn"]
    cv.rect(0, 20, W, 184, T["surface"])
    y0, lh, cw = 23, 11, 5
    cur = 13
    gut = 20
    for i, line in enumerate(CODE):
        y = y0 + i * lh
        if y + 10 > 203:
            break
        bgc = T["sel_rest"] if i == cur else T["surface"]
        if i == cur:
            cv.rect(0, y - 1, W, lh, bgc)
            cv.rect(0, y - 1, 2, lh, T["value"])
        cv.rtext(gut - 3, y, str(i + 12), T["text"] if i == cur else T["dim"], "code")
        x = gut + 4
        for k in range(0, len(line) - 1, 2):
            for ch in line[k + 1]:
                if x + cw > W - 4:
                    break
                x = cv.text(x, y, ch, syn[line[k] or "t"], "code")
        if x + cw > W - 4:
            for j, f in enumerate((0.35, 0.6, 0.85)):
                cv.shade(W - 4 - (3 - j) * 4, y - 1, 4, lh - 1, f, bgc)
            cv.rect(W - 4, y - 1, 4, lh - 1, bgc)
        if i == cur:
            cv.rect(gut + 4 + cw * 12, y - 1, 2, 10, T["accent"])
    cv.rect(0, 204, W, 1, T["line"])
    cv.text(8, 206, "Ln 25, Col 13", T["dim"], "cap")
    cv.text(78, 206, "178 lines", T["dim"], "cap")
    cv.rrect(132, 209, 4, 4, "#5cd08a" if T["mode"] == "dark" else "#22985a", 1)
    cv.text(140, 206, "No issues", T["dim"], "cap")
    cv.rtext(W - 8, 206, "Python", T["dim"], "cap")
    keys = "=()[]{}<>:;,.\"_%"
    kw = (W - 4) // len(keys)
    for i, ch in enumerate(keys):
        x = 3 + i * kw
        raised_box(cv, x, 220, kw - 2, 17, T)
        cv.ctext(x, 222, kw - 2, ch, T["text"], "title")


FROG = ["00bb00bb", "0b7bb7b0", "0bbbbbb0", "bb0bb0bb", "bbbbbbbb", "bbbbbbbb",
        "0bbbbbb0", "00b00b00"]


def checker(cv, x, y, w, h, T, s=4):
    """Transparent: a mid-grey checker with a hairline diagonal; neither grey
    is a palette colour, and the pattern is never a solid."""
    a, b = "#6e6e78", "#9a9aa4"
    for yy in range(0, h, s):
        for xx in range(0, w, s):
            cv.rect(x + xx, y + yy, min(s, w - xx), min(s, h - yy),
                    a if ((xx // s) + (yy // s)) & 1 else b)


def sprite_editor(cv, T, W, H):
    editor_bar(cv, T, W, "Sprites")
    tools = ("pencil", "bucket", "line", "rect", "oval", "select")
    for i, t in enumerate(tools):
        y = 26 + i * 21
        if i == 0:
            cv.rect(4, y + 1, 2, 16, T["value"])
            cv.icon(t, 9, y + 3, T["text"])
        else:
            cv.icon(t, 9, y + 3, T["dim"])
    cv.rect(8, 26 + 6 * 21 + 1, 12, 1, T["line"])
    for i, t in enumerate(("flip", "rotate")):
        cv.icon(t, 9, 26 + 6 * 21 + 6 + i * 21, T["dim"])
    cx, cy, cs = 30, 24, 22
    cv.rrect(cx - 1, cy - 1, 8 * cs + 1, 8 * cs + 1, T["line"], 1)
    for r in range(8):
        for c in range(8):
            v = int(FROG[r][c], 16)
            if v == 0:
                checker(cv, cx + c * cs, cy + r * cs, cs - 1, cs - 1, T, 7)
            else:
                cv.rect(cx + c * cs, cy + r * cs, cs - 1, cs - 1, P(v))
    fx, fy = cx + 3 * cs, cy + 2 * cs
    cv.rbox(fx - 1, fy - 1, cs + 1, cs + 1, "#000000", 0, 1)
    cv.rbox(fx - 3, fy - 3, cs + 5, cs + 5, T["accent"], 0, 2)
    rx, rw = 214, W - 214 - 6
    cv.text(rx, 24, "Sprite", T["dim"], "cap")
    _chev(cv, rx, 39, T["dim"], left=True)
    cv.text(rx + 8, 36, "12", T["text"], "body")
    _chev(cv, rx + 22, 39, T["dim"])
    cv.rtext(rx + rw - 10, 36, "8×8", T["text"], "body")
    _chev(cv, rx + rw - 4, 39, T["dim"])
    _chev(cv, rx + rw - 18 - cv.tw("8×8", "body"), 39, T["dim"], left=True)
    checker(cv, rx, 52, rw, 30, T, 3)
    for r in range(8):
        for c in range(8):
            v = int(FROG[r][c], 16)
            if v:
                cv.put(rx + 8 + c, 63 + r, P(v))
                cv.rect(rx + 26 + c * 3, 55 + r * 3, 3, 3, P(v))
    cv.rrect(rx + rw - 30, 61, 26, 12, T["surface"], 2)
    cv.ctext(rx + rw - 30, 62, 26, "1/2", T["text"], "cap")
    cv.text(rx, 88, "Colour", T["dim"], "cap")
    sw = 11
    for i in range(16):
        px, py = rx + (i % 8) * (sw + 1), 100 + (i // 8) * (sw + 4)
        if i == 0:
            checker(cv, px, py, sw, sw, T, 3)
            for k in range(sw):
                cv.put(px + sw - 1 - k, py + k, "#ffffff")
        else:
            cv.rect(px, py, sw, sw, P(i))
    sel = 11
    px, py = rx + (sel % 8) * (sw + 1), 100 + (sel // 8) * (sw + 4)
    cur_bar(cv, px, py + sw + 1, sw, T)
    cv.text(rx, 130, "11  Green", T["text"], "cap")
    cv.rtext(rx + rw, 130, "0 = clear", T["dim"], "cap")
    button(cv, rx, 144, rw // 2 - 2, 18, "Get", T, icon="get")
    button(cv, rx + rw // 2 + 2, 144, rw // 2 - 2, 18, "Put", T, icon="put")
    button(cv, rx, 167, rw, 18, "Whole sheet", T)
    hints(cv, 8, H - 14, (("Space", "Draw"), ("[ ]", "Colour"), ("Z", "Undo")), T,
          right=(("Tab", "Tools"),))


# ==========================================================================
# Calc: an object -- a paper tape, a display, a keypad
# ==========================================================================
KEYS = (("C", "⌫", "%", "÷"), ("7", "8", "9", "×"), ("4", "5", "6", "−"),
        ("1", "2", "3", "+"), ("0", None, ".", "="))


def calc_key(cv, x, y, w, h, lab, T, font, s, focus=False, big=False):
    if lab == "=":
        if T["frame"] not in ("contrast", "studio"):
            cv.rrect(x, y + 1, w, h, mix(T["value"], "#000000", 0.4), max(0, T["radius"] - 1))
        cv.rrect(x, y, w, h, T["value"], max(0, T["radius"] - 1))
        if T["outline"]:
            cv.rbox(x, y, w, h, T["line"], max(0, T["radius"] - 1), T["outline"])
        ink = T["value_ink"]
    else:
        raised_box(cv, x, y, w, h, T)
        ink = T["value"] if lab in "÷×−+%" else T["text"]
        if T["frame"] in ("paper", "contrast"):
            ink = T["text"]
    if lab == "⌫":
        k = 4 if big else 2
        for yy, rr in enumerate(EXTRA["⌫"][:5]):
            for xx, b in enumerate(rr):
                if b == "#":
                    cv.rect(x + (w - 5 * k) // 2 + xx * k, y + (h - 5 * k) // 2 + yy * k, k, k, ink)
    elif lab == "." and big:
        cv.rect(x + w // 2 - 3, y + h // 2 + 3, 6, 6, ink)
    elif big:
        big_glyph(cv, x, y, w, h, lab, ink)
    else:
        lh = {"body": 9, "title": 11}[font] * s
        cv.ctext(x, y + (h - lh) // 2 - s, w, lab, ink, font, s)
    if focus:
        focus_ring(cv, x, y, w, h, T, max(0, T["radius"] - 1), gap=0, halo=False)


def big_glyph(cv, x, y, w, h, lab, ink):
    """A key legend in Galmuri11 Bold with every stroke doubled in height:
    the 2x numerals the handheld keypad uses (cap 18 in a 37-pixel key)."""
    g = GAL["Galmuri11-Bold"]["glyphs"].get(lab)
    if not g:
        return
    adv, x0, y0, rows = g
    gw = max(len(r) for r in rows) * 2
    gh = len(rows) * 2
    ox, oy = x + (w - gw) // 2, y + (h - gh) // 2
    for yy, r in enumerate(rows):
        for xx, b in enumerate(r):
            if b == "#":
                cv.rect(ox + xx * 2, oy + yy * 2, 2, 2, ink)


def keypad(cv, x, y, w, h, T, font, s, g=6, focus="5", big=False):
    kw = (w - 3 * g) // 4
    kh = (h - 4 * g) // 5
    for r, rr in enumerate(KEYS):
        for c, lab in enumerate(rr):
            if lab is None:
                continue
            ww = kw * 2 + g if lab == "0" else kw
            calc_key(cv, x + c * (kw + g), y + r * (kh + g), ww, kh, lab, T, font, s,
                     focus=lab == focus, big=big)


def lcd(cv, x, y, w, h, T):
    cv.rrect(x, y, w, h, T["lcd"], max(0, T["radius"] - 1))
    if T["mode"] == "dark" and not T["outline"]:
        cv.rect(x + 2, y, w - 4, 1, mix(T["lcd"], "#000000", 0.6))
    if T["outline"]:
        cv.rbox(x, y, w, h, T["line"], max(0, T["radius"] - 1), T["outline"])
    elif T["frame"] == "studio":
        cv.rbox(x, y, w, h, T["line"], 0, 1)


def calc_page(cv, T, W, H):
    topbar_page(cv, T, W, "Calc", H)
    tx, ty, tw, th = 8, 26, 112, 206
    lcd(cv, tx, ty, tw, th, T)
    ink = T["lcd_ink"]
    tape = (("120 − 36", "84"), ("36 + 48", "84"), ("84 ÷ 7", "12"), ("12 × 12", "144"))
    yy = ty + 8
    for e, r in tape:
        cv.rtext(tx + tw - 8, yy, e, mix(ink, T["lcd"], 0.45), T["cap"])
        cv.rtext(tx + tw - 8, yy + 10, "= " + r, mix(ink, T["lcd"], 0.3), T["body"])
        yy += 26
        cv.rect(tx + 8, yy - 5, tw - 16, 1, mix(ink, T["lcd"], 0.8))
    cv.rtext(tx + tw - 8, ty + th - 52, "12 × 7", mix(ink, T["lcd"], 0.3), T["body"])
    cv.rtext(tx + tw - 8, ty + th - 40, "84", ink, "title", 3)
    keypad(cv, 130, ty + 2, W - 130 - 10, th - 4, T, "title", 1, big=True)


# ==========================================================================
# the desk
# ==========================================================================
def desk_bg(cv, T, W, H, top=0):
    cv.rect(0, top, W, H - top, T["desk"])
    if T["desk_pattern"] == "dots":
        for yy in range(24, H, 8):
            for xx in range(4, W, 8):
                cv.put(xx, yy, mix(T["desk"], "#000000", 0.18))
    elif T["desk_pattern"] == "stars":
        dim, bright = T["stars"]
        seed = [7]

        def rnd(n):
            seed[0] = (seed[0] * 1103515245 + 12345) & 0x7FFFFFFF
            return seed[0] % n
        tile = [(rnd(96), rnd(96), rnd(9)) for _ in range(14)]
        for oy in range(0, H, 96):
            for ox in range(0, W, 96):
                for sx, sy, k in tile:
                    x, y = ox + sx, oy + sy
                    if y < max(22, top) or y >= H:
                        continue
                    if k == 0:
                        cv.put(x, y, bright)
                        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                            cv.put(x + dx, y + dy, dim)
                    else:
                        cv.put(x, y, dim if k > 2 else bright)


def window(cv, x, y, w, h, title, icon, T, focus=True):
    """Current window: the strip in the value colour (Studio: white, inverted)
    and a 3-pixel drop. Behind: the strip sunk to the body, a dim title, the
    body dimmed, and no drop in the flat or 1-bit themes."""
    fr, r, th = T["frame"], T["radius"], 20
    if focus or fr == "soft":
        drop(cv, x, y, w, h, T, r, 3 if focus else 2)
    label = U(T, title)
    if fr == "studio":
        cv.rect(x, y, w, h, T["surface"])
        fill, ink = (T["value"], T["value_ink"]) if focus else (T["surface"], T["dim"])
        cv.rect(x, y, w, th, fill)
        cv.rbox(x, y, w, h, T["line"] if focus else "#333333", 0, 1)
        cv.rect(x, y + th - 1, w, 1, T["line"] if focus else "#333333")
        cv.icon(icon, x + 8, y + 4, ink, fill)
        cv.text(x + 24, y + 4, label, ink, "body")
        cv.icon("close", x + w - 17, y + 4, ink)
        return y + th
    if fr == "paper":
        cv.rrect(x, y, w, h, T["surface"], r)
        if focus:
            cv.rrect(x, y, w, th, T["text"], r)
            cv.rect(x, y + th - 3, w, 3, T["text"])
            ink = T["raised"]
        else:
            ink = T["text"]
        cv.rbox(x, y, w, h, T["line"], r, 1)
        cv.rect(x, y + th - 1, w, 1, T["line"])
        cv.icon(icon, x + 7, y + 4, ink, T["text"] if focus else T["surface"])
        cv.text(x + 23, y + 4, label, ink, "body")
        cv.icon("close", x + w - 18, y + 4, ink)
        return y + th
    cv.rrect(x, y, w, h, T["surface"], r)
    if focus:
        fill, ink = T["strip_on"], T["strip_on_ink"]
    else:
        fill, ink = T["surface"], T["dim"]
    cv.rrect(x, y, w, th, fill, r)
    cv.rect(x, y + th - r, w, r, fill)
    if fr == "contrast":
        cv.rbox(x, y, w, h, T["line"] if focus else T["dim"], r, 2)
        cv.rect(x, y + th - 1, w, 2, T["line"] if focus else T["dim"])
        cv.icon(icon, x + 7, y + 5, ink, fill)
        cv.text(x + 23, y + 3, fit(cv, label, w - 50, T["body"]), ink, T["body"])
        cv.icon("close", x + w - 18, y + 5, ink)
        return y + th + 1
    if focus and T["mode"] == "dark":
        top_edge(cv, x, y, w, mix(fill, "#ffffff", 0.35), r)
    elif not focus and T["group_edge"] and T["mode"] == "dark":
        top_edge(cv, x, y, w, T["group_edge"], r)
    if not focus:
        cv.rect(x, y + th - 1, w, 1, T["line"])
    cv.icon(icon, x + 7, y + 4, ink, fill if focus else T["dim"])
    cv.text(x + 23, y + 4, label, ink, "body")
    cv.icon("close", x + w - 18, y + 4, ink)
    return y + th


FILES = (("brick_siege", "Game", "14 KB"), ("bullet_storm", "Game", "9 KB"),
         ("calc", "App", "6 KB"), ("coin_quest", "Game", "11 KB"),
         ("harpoon_pop", "Game", "12 KB"), ("hop_quest", "Game", "18 KB"),
         ("letter_blitz", "Game", "10 KB"), ("moy_night", "Wallpaper", "3 KB"),
         ("my_art", "Art", "22 KB"), ("notes", "App", "4 KB"),
         ("pin_light", "Game", "7 KB"), ("pixel_pet", "Game", "13 KB"),
         ("sky_run", "Game", "21 KB"), ("star_catcher", "Game", "16 KB"),
         ("storybook", "App", "9 KB"), ("tiny_runner", "Game", "8 KB"))
PLACES = (("Projects", "projects", 16), ("Carts", "play", 11), ("Art", "brush", 4),
          ("Music", "note", 2), ("Trash", "trash", 0))


def files_window(cv, x, y, w, h, T, focus=False):
    by = window(cv, x, y, w, h, "Files", "folder", T, focus)
    big = T["body"] != "body"
    rh = 14 if not big else 18
    sw = 80 if not big else 100
    section(cv, x, by + 5, "Places", T)
    for i, (n, ic, cnt) in enumerate(PLACES):
        yy = by + 18 + i * (rh + 2)
        on = i == 0
        if on:
            cv.rrect(x + 4, yy - 2, sw - 6, rh + 1, T["sel_rest"], 2)
            cv.rect(x + 4, yy - 2, 2, rh + 1, T["value"])
        cv.icon(ic, x + 9, yy, T["text"] if on else T["dim"], T["dim"], T["value"])
        cv.text(x + 24, yy + (0 if not big else 1), fit(cv, U(T, n), sw - 30, T["body"]),
                T["text"] if on else T["dim"], T["body"])
    cv.rect(x + sw, by, 1, y + h - by, T["line"] if T["frame"] != "studio" else "#333333")
    lx, lw = x + sw + 1, w - sw - 1
    kind_r, size_r = (lx + lw - 40, lx + lw - 8) if not big else (None, lx + lw - 8)
    cv.text(lx + 8, by + 5, U(T, "Name"), T["dim"], T["cap"])
    if kind_r:
        cv.rtext(kind_r, by + 5, U(T, "Kind"), T["dim"], T["cap"])
    cv.rtext(size_r, by + 5, U(T, "Size"), T["dim"], T["cap"])
    if kind_r:
        kind_w = max(cv.tw(U(T, k), T["cap"]) for _, k, _ in FILES)
        name_w = kind_r - kind_w - 6 - (lx + 24)
    else:
        name_w = size_r - cv.tw("22 KB", T["cap"]) - 6 - (lx + 24)
    for i, (n, kind, size) in enumerate(FILES):
        yy = by + 17 + i * rh
        if yy + rh > y + h - 2:
            break
        if i == 4:
            cv.rect(lx + 2, yy, lw - 4, rh, T["sel_rest"])
            cv.rect(lx + 2, yy, 2, rh, T["value"])
        file_icon(cv, n, lx + 8, yy + (rh - 12) // 2, T)
        cv.text(lx + 24, yy + (rh - 11) // 2 + (0 if not big else -1),
                fit(cv, n, name_w, T["body"]), T["text"], T["body"])
        if kind_r:
            cv.rtext(kind_r, yy + (rh - 9) // 2, U(T, kind), T["dim"], T["cap"])
        cv.rtext(size_r, yy + (rh - 9) // 2, size, T["dim"], T["cap"])
    if not focus and T["frame"] == "soft":
        cv.shade(x, by, w, y + h - by, 0.25, T["surface"])


DESK = (("play", "Play"), ("projects", "Projects"), ("brush", "Paint"), ("book", "Storybook"),
        ("folder", "Files"), ("calc", "Calc"), ("download", "Get carts"))


def desktop_bar(cv, T, W, chips):
    bar_line(cv, T, W)
    moy_button(cv, T)
    x = 34
    for title, ic, on in chips:
        label = title.upper() if T["upper"] else title
        w = cv.tw(label, "body") + 22
        ink = T["text"] if on else T["dim"]
        if on:
            if T["frame"] == "paper":
                cv.rrect(x, 2, w, 16, T["text"], 2)
                ink = T["raised"]
            elif T["frame"] == "contrast":
                cv.rrect(x, 2, w, 16, T["text"], 2)
                ink = T["bg"]
            else:
                raised_box(cv, x, 2, w, 16, T, r=2)
                cv.rect(x + 4, 16, w - 8, 2, T["value"])
        cv.icon(ic, x + 4, 4, ink)
        cv.text(x + 18, 4, label, ink, "body")
        x += w + 4
    status(cv, T, W - 8)


def desk_icons(cv, T, wall=False):
    y = 30
    for ic, label in DESK:
        cx = 28
        raised_box(cv, cx - 13, y, 26, 20, T, r=max(0, T["radius"]))
        cv.icon(ic, cx - 6, y + 4, T["text"], T["dim"], T["value"]
                if T["frame"] not in ("paper", "contrast") else T["text"])
        lab = label.upper() if T["upper"] else label
        f = T["cap"] if T["cap"] == "cap" else "cap"
        lw = cv.tw(lab, f)
        if wall:
            cv.rrect(cx - lw // 2 - 3, y + 22, lw + 6, 10, T["bg"], 2)
        cv.text(cx - lw // 2, y + 22, lab, T["text"], f)
        y += 37


def desktop(cv, T, W, H, wall=False, calc_x=330):
    if not wall:
        desk_bg(cv, T, W, H)
    else:
        cv.rect(0, 0, W, 20, T["bg"])
    desktop_bar(cv, T, W, (("Calc", "calc", True), ("Files", "folder", False)))
    desk_icons(cv, T, wall)
    files_window(cv, 54, 26, 282, 268, T, focus=False)
    x, y, w, h = calc_x, 70, 166, 214
    by = window(cv, x, y, w, h, "Calc", "calc", T, focus=True)
    lcd(cv, x + 8, by + 8, w - 16, 42, T)
    ink = T["lcd_ink"]
    cv.rtext(x + w - 16, by + 12, "12 × 7", mix(ink, T["lcd"], 0.3), T["cap"])
    cv.rtext(x + w - 16, by + 22, "84", ink, "title", 2)
    keypad(cv, x + 10, by + 60, w - 20, y + h - by - 70, T, "title", 1, g=6)


def appearance_window(cv, T, W, H):
    desk_bg(cv, T, W, H)
    desktop_bar(cv, T, W, (("Appearance", "gear", True), ("Files", "folder", False)))
    desk_icons(cv, T)
    x, y, w, h = 76, 26, 340, 262
    by = window(cv, x, y, w, h, "Appearance", "gear", T, focus=True)
    end = appearance(cv, T, x, by + 6, w, desk=True)
    cv.text(x + 16, end + 8, "Moy  ·  built in", T["dim"], T["cap"])
    hints(cv, x + w - 120, end + 8, (("⏎", "Open"), ("Esc", "Close")), T)


# ==========================================================================
# the default wallpaper: a cart, MOY64 only, laid out per glass
# ==========================================================================
MOY_ART = ("................", "...0000000......", "..0ddddddd0.....", ".0d66ddddd0.....",
           ".0dddddddd0.....", ".0dddddddd0000..", ".0dd00d00ddddd0.", ".0dddddddddddd0.",
           ".0dddddddddddd0.", ".0dd0ddd0ddddd0.", ".0ddd000dddddd0.", ".0dddddddddddd0.",
           ".0dddddddddddd0.", "..022222222220..", "..02220002220...", "...000...000....")


def wallpaper(cv, W, H, moon, moy, ridge, quiet, band=24, zz_above=False):
    """Moy's night as a cart: black sky with sparse stars, a narrow dithered
    band down to dark blue at the horizon, a black hill under the hint strip.
    Stars keep out of the bar and the quiet rectangles; Moy sleeps on the
    ridge where no card or default window sits."""
    import math
    bayer = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
    for yy in range(H):
        t = max(0.0, min(1.0, (yy - (ridge - band)) / band))
        for xx in range(W):
            cv.px[xx, yy] = P(1) if t * 15 > bayer[yy & 3][xx & 3] else P(0)
    seed = [20261015]

    def rnd(n):
        seed[0] = (seed[0] * 1103515245 + 12345) & 0x7FFFFFFF
        return seed[0] % n
    for _ in range(W * H // 700):
        x, y = rnd(W), 24 + rnd(max(1, ridge - 32))
        if any(qx <= x < qx + qw and qy <= y < qy + qh for qx, qy, qw, qh in quiet):
            continue
        k = rnd(14)
        if k == 0:
            cv.put(x, y, P(7))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                cv.put(x + dx, y + dy, P(13))
        else:
            cv.put(x, y, P(7) if k < 4 else P(13))
    mx, my, mr = moon
    for yy in range(-mr - 3, mr + 4):
        for xx in range(-mr - 3, mr + 4):
            d = (xx * xx + yy * yy) ** 0.5
            bite = ((xx + mr * 0.45) ** 2 + (yy + mr * 0.35) ** 2) ** 0.5
            if d <= mr and bite > mr * 0.85:
                cv.put(mx + xx, my + yy, P(10))
            elif mr < d <= mr + 2 and bite > mr * 0.85 and (xx + yy) & 1:
                cv.put(mx + xx, my + yy, P(9) if d <= mr + 1 else P(60))
    ox, oy, s = moy
    for xx in range(W):
        bump = int(4 * math.cos((xx - ox - 8 * s) / (W * 0.12)))
        h1 = ridge - max(0, bump)
        for yy in range(h1, H):
            cv.px[xx, yy] = P(0)
        cv.put(xx, h1, P(60))
        cv.put(xx, h1 + 1, P(1) if xx & 1 else P(0))
    oy = ridge - 4 - 16 * s + 2
    for r, line in enumerate(MOY_ART):
        for c, ch in enumerate(line):
            if ch != ".":
                cv.rect(ox + c * s, oy + r * s, s, s, P(int(ch, 16)))
    zx, zy = (ox + 9 * s, oy - 12) if zz_above else (ox + 16 * s, oy - 2)
    cv.text(zx, zy, "z", P(6), "body")
    cv.text(zx + 6, zy - 8, "z", P(13), "cap")


# ==========================================================================
# compose and save
# ==========================================================================
def slug(T):
    return f"{T['name'].lower()}-{T['mode']}"


def shot(fn, T, W, H, name, what, up=1, x3=False):
    cv = Cv(W, H, T["bg"])
    fn(cv, T, W, H)
    cv.save(name, what, f"{T['name']} theme, {T['mode']}", "", x3=x3, up=up)



def _patch_glyphs():
    """The Moybyte cut of two Galmuri glyphs (OFL-1.1 allows it): Mono's `l`
    gains a flag and a tail and `1` a base, so `l 1 I | ( )` never read alike;
    Bold's minus is the plus's bar, as wide as the plus."""
    m = GAL["GalmuriMono9"]["glyphs"]
    m["l"] = [5, 1, 1, ["##.", ".#.", ".#.", ".#.", ".#.", ".#.", ".#.", "..#"]]
    m["1"] = [5, 1, 1, [".#.", "##.", ".#.", ".#.", ".#.", ".#.", ".#.", "###"]]
    b = GAL["Galmuri11-Bold"]["glyphs"]
    adv, x0, y0, rows = b["+"]
    mid = [i for i, r in enumerate(rows) if r.count("#") == len(r)]
    b["−"] = [adv, x0, y0 + mid[0], [rows[i] for i in mid]]
    return True


GLYPH_PATCH = _patch_glyphs()


def fit(cv, s, maxw, font):
    """The text, cut with an ellipsis to fit maxw."""
    if cv.tw(s, font) <= maxw:
        return s
    while s and cv.tw(s + "…", font) > maxw:
        s = s[:-1]
    return s + "…"


def focus_ring(cv, x, y, w, h, T, r=3, gap=1, halo=True):
    """FOCUS: a 2-pixel accent ring with a 1-pixel gap. Moy adds its dithered
    moon-glow outside it; Contrast light adds a black keyline."""
    o = gap + 2
    if T["frame"] == "contrast" and T["mode"] == "light":
        cv.rbox(x - o - 1, y - o - 1, w + 2 * o + 2, h + 2 * o + 2, "#000000",
                r + o + 1 if r else 0, 1)
    cv.rbox(x - o, y - o, w + 2 * o, h + 2 * o, T["accent"], r + o if r else 0, 2)
    if T["halo"] and halo:
        k = o + 1
        tmp = Cv(w + 2 * k, h + 2 * k, (1, 2, 3))
        tmp.rbox(0, 0, w + 2 * k, h + 2 * k, (250, 0, 250), r + k if r else 0, 1)
        for yy in range(h + 2 * k):
            for xx in range(w + 2 * k):
                if tmp.px[xx, yy] == q((250, 0, 250)) and (xx + yy) & 1:
                    cv.put(x - k + xx, y - k + yy, mix(T["accent"], T["bg"], 0.35))




def moy_button(cv, T, x=6):
    raised_box(cv, x, 2, 20, 16, T, r=2)
    cv.icon("moy", x + 4, 4, T["text"], T["value"] if T["mode"] == "dark" and T["frame"] == "soft"
            else T["surface"])



def U(T, s):
    return s.upper() if T["upper"] else s


def cur_bar(cv, x, y, w, T):
    """CURRENT / SELECTED: a 2-pixel bar in the value colour (under a tab, a
    swatch, a theme or a tool; left of a row)."""
    cv.rect(x, y, w, 2, T["value"])


def main():
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        if f.endswith(".png"):
            os.remove(os.path.join(OUT, f))
    for T in (MOY_DARK, MOY_LIGHT):
        s = slug(T)
        shot(home, T, 320, 240, f"{s}_handheld_home", "Handheld home (console mode), Games "
             "tab, carousel on Sky Run, 320x240 T-Deck", x3=True)
        shot(desktop, T, 512, 300, f"{s}_desktop", "Desktop, 1024x600 board at 2x: Calc "
             "current, Files behind (desk density)", up=2)
        shot(big_home, T, 512, 300, f"{s}_desktop_home", "Home on the 1024x600 board in "
             "console mode (2x): hero, details, shelf; the X returns to the desk", up=2)
        shot(settings, T, 320, 240, f"{s}_handheld_settings", "Settings page, 320x240", x3=True)
        shot(appearance_page, T, 320, 240, f"{s}_handheld_appearance",
             "Appearance page, 320x240", x3=True)
        shot(appearance_window, T, 512, 300, f"{s}_desktop_appearance",
             "Appearance as a window on the desk, 1024x600 at 2x", up=2)
        shot(code_editor, T, 320, 240, f"{s}_handheld_code-editor",
             "Code editor on Star Catcher, 320x240", x3=True)
        shot(sprite_editor, T, 320, 240, f"{s}_handheld_sprite-editor",
             "Sprite editor on Star Catcher's frog, 320x240", x3=True)
        shot(calc_page, T, 320, 240, f"{s}_handheld_calc", "Calc fullscreen, 320x240",
             x3=True)
    for T in (PAPER, STUDIO, CONTRAST, CONTRAST_LIGHT):
        s = slug(T)
        shot(home, T, 320, 240, f"{s}_handheld_home", "Handheld home, 320x240", x3=True)
        shot(desktop, T, 512, 300, f"{s}_desktop", "Desktop, Calc current, Files behind, "
             "1024x600 at 2x", up=2)
    T = MOY_DARK
    cv = Cv(320, 240, T["bg"])
    wallpaper(cv, 320, 240, moon=(296, 44, 8), moy=(10, 0, 2), ridge=216, band=18,
              quiet=((0, 0, 320, 24), (24, 158, 272, 40)), zz_above=True)
    home(cv, T, 320, 240, wall=True)
    cv.save("moy-dark_handheld_home_wallpaper", "Handheld home over the reworked default "
            "wallpaper (Moy's night), 320x240", "Moy theme, dark", "", x3=True)
    cv = Cv(512, 300, T["bg"])
    wallpaper(cv, 512, 300, moon=(486, 44, 12), moy=(472, 0, 2), ridge=284, band=40,
              quiet=((0, 0, 512, 24), (0, 24, 52, 270)), zz_above=True)
    desktop(cv, T, 512, 300, wall=True, calc_x=296)
    cv.save("moy-dark_desktop_wallpaper", "Desktop over the reworked default wallpaper, "
            "1024x600 at 2x", "Moy theme, dark", "", up=2)
    sheet()
    with open(os.path.join(OUT, "index.txt"), "w") as f:
        f.write("\n".join(INDEX) + "\n")
    contrast_report()


def sheet():
    cv = Cv(512, 300, "#22232e")
    y = 6
    cv.text(8, y, "bg surface raised edge line dim text | accent: FOCUS ring | value: CURRENT bar, "
            "PRIMARY fill", "#eceaf8", "cap")
    y += 13
    for T in (MOY_DARK, MOY_LIGHT, PAPER, STUDIO, CONTRAST, CONTRAST_LIGHT):
        cv.text(8, y + 2, f"{T['name']} {T['mode']}", "#eceaf8", "body")
        x = 100
        for k in ("bg", "surface", "raised"):
            cv.rect(x, y, 30, 14, T[k])
            x += 32
        cv.rect(x, y, 30, 14, T["group_edge"] or T["edge"] or T["under"])
        x += 32
        for k in ("line", "dim", "text"):
            cv.rect(x, y, 30, 14, T[k])
            x += 32
        x += 8
        cv.rect(x, y, 30, 14, T["accent"])
        cv.text(x + 6, y + 2, "Aa", T["on_accent"], "body")
        cv.rect(x + 34, y, 30, 14, T["value"])
        cv.text(x + 40, y + 2, "Aa", T["value_ink"], "body")
        if T["sections"]:
            for i, c in enumerate(T["sections"].values()):
                cv.rect(x + 70 + i * 9, y, 7, 14, c)
        y += 16
    T = MOY_DARK
    cv.rrect(8, y + 4, 496, 300 - y - 12, T["bg"], 3)
    desk_bg(cv, T, 504, 292, top=y + 6)
    yy = y + 10
    cv.text(16, yy, "Sky Run", T["text"], "title")
    cv.text(200, yy + 2, "title: Galmuri11 Bold", T["dim"], "cap")
    yy += 16
    cv.text(16, yy, "Appearance  Wi-Fi  14:32", T["text"], "body")
    cv.text(200, yy + 1, "body: Galmuri9", T["dim"], "cap")
    yy += 14
    cv.text(16, yy, "Platformer · Built in · Played today", T["dim"], "cap")
    cv.text(200, yy, "caption: Galmuri7, dim", T["dim"], "cap")
    yy += 13
    cv.text(16, yy, "lives = 1 + I | (l)", T["text"], "code")
    cv.text(200, yy, "code: GalmuriMono9, Moybyte cut of l and 1", T["dim"], "cap")
    yy += 16
    x = 16
    for k in ("⏎", "C", "Space", "↑↓"):
        x += keycap(cv, x, yy, k, T) + 4
    toggle(cv, x + 6, yy, True, T)
    toggle(cv, x + 30, yy, False, T)
    slider(cv, x + 58, yy + 4, 50, 0.6, T)
    cv.text(x + 112, yy, "Now", T["text"], "body")
    cur_bar(cv, x + 112, yy + 13, 20, T)
    button(cv, x + 136, yy - 3, 50, 16, "Play", T, "primary")
    button(cv, x + 190, yy - 3, 50, 16, "Copy", T)
    cv.rrect(x + 254, yy - 3, 50, 16, T["raised"], 2)
    focus_ring(cv, x + 254, yy - 3, 50, 16, T, 2)
    cv.ctext(x + 254, yy - 1, 50, "Focus", T["text"], "body")
    yy += 20
    x = 16
    for ic in ("wifi", "batt", "back", "close", "moy", "play", "folder", "projects", "brush",
               "book", "calc", "download", "code", "sprite", "map", "note", "undo", "redo",
               "pencil", "bucket", "line", "rect", "oval", "select", "flip", "rotate",
               "search", "gear", "get", "put", "doc", "trash"):
        cv.icon(ic, x, yy, T["text"], T["dim"], T["value"])
        x += 15
    yy += 22
    window(cv, 24, yy, 140, 56, "Current", "calc", T, True)
    window(cv, 184, yy, 140, 56, "Behind", "folder", T, False)
    group(cv, 344, yy, 150, 40, T)
    row(cv, 344, yy, 150, "Focused", T, "On", "link", focus=True)
    row(cv, 344, yy + 20, 150, "Row", T, True, "toggle", last=True)
    cv.save("system_sheet", "The system on one page: every theme's ramp, focus and value "
            "colours; type; components; the 12px icons; window states", "all themes", "",
            up=2)


def contrast_report():
    print("contrast:")
    for T in (MOY_DARK, MOY_LIGHT, PAPER, STUDIO, CONTRAST, CONTRAST_LIGHT):
        out = [f"{ink}/{bg} {ratio(T[ink], T[bg]):.1f}" for ink in ("text", "dim")
               for bg in ("bg", "surface", "raised")]
        out.append(f"on_accent/accent {ratio(T['on_accent'], T['accent']):.1f}")
        out.append(f"accent/bg {ratio(T['accent'], T['bg']):.1f}")
        out.append(f"accent/surface {ratio(T['accent'], T['surface']):.1f}")
        out.append(f"value_ink/value {ratio(T['value_ink'], T['value']):.1f}")
        out.append(f"value/surface {ratio(T['value'], T['surface']):.1f}")
        if T["group_edge"]:
            out.append(f"group_edge/bg {ratio(T['group_edge'], T['bg']):.1f}")
        out.append(f"sel_bar(value)/sel {ratio(T['value'], T['sel_rest']):.1f}")
        if T["syn"]:
            out.append(f"comment {ratio(T['syn']['c'], T['surface']):.1f}")
        print(f"  {T['name']} {T['mode']}: " + ", ".join(out))


if __name__ == "__main__":
    main()
