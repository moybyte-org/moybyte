#!/usr/bin/env python3
"""Round 7 of the 2026-10 look: icon sets and the editors.

Reuses the theme set and renderer in docs/media/look_2026-10/mockups.py and
adds:
- icon sets: the smooth set as SVG sources (icons/<set>/*.svg here),
  rasterised on the host per size to 2/4 bpp alpha masks and tinted by theme
  roles at draw time; the pixel set as the existing 16x16 IconSheet art
  (runtime/chrome.py), palette-coloured;
- the code editor and the sprite editor, with the editor's tab row.

A layer of the target, not an entry point: `../draw.py` draws every
picture, with rounds 8-10 layered over this one. The Studio scenes this
round drew (`main`) are the later Studio project's, not targets.
"""
import importlib.util
import io
import keyword
import os
import sys
import tokenize

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
LOOK = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(LOOK)))
BASE = os.path.join(LOOK, "mockups.py")
OUT = os.environ.get("LOOK_OUT", LOOK)
sys.argv = [sys.argv[0], OUT]
spec = importlib.util.spec_from_file_location("look", BASE)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
sys.path.insert(0, os.path.join(REPO, "runtime"))
from palette import MOY64, NAMES  # noqa: E402


def _chrome_tables():
    """runtime/chrome.py's icon slot map and 16x16 art, read without
    importing the toolkit (which needs the native moy_ui)."""
    import ast
    out = {}
    for n in ast.parse(open(os.path.join(REPO, "runtime/chrome.py")).read()).body:
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in ("_ICON", "_ICON_ART"):
            out[n.targets[0].id] = ast.literal_eval(n.value)
    return out["_ICON"], out["_ICON_ART"]


ICON_SLOT, ICON_ART = _chrome_tables()

import resvg_py  # noqa: E402

_fp = M.font_path


def font_path(key, weight=400, size=None):
    if key == "galmono":
        return M._galmuri({8: "GalmuriMono7.ttf", 10: "GalmuriMono9.ttf", 12: "GalmuriMono11.ttf"}[size])
    return _fp(key, weight, size)


M.font_path = font_path
Face, F, HH, DK, mix, q, ratio = M.Face, M.F, M.HH, M.DK, M.mix, M.q, M.ratio
INDEX = M.INDEX
PAL = [q(c) for c in MOY64[:16]]
COLOUR_NAME = {v: k for k, v in NAMES.items() if v < 16}

# One theme, two modes (owner, 2026-10-15): Daylight is Moy's light mode,
# Moy Night its dark mode.
LIGHT, DARK, PICO = M.B, M.A, M.PICO
LIGHT["name"], DARK["name"] = "Moy, light mode", "Moy, dark mode"

# ==========================================================================
# icon slots and their sources
# ==========================================================================
SLOTS = [
    ("bar", ["moy", "home", "wifi", "wifi_off", "battery", "search"]),
    ("apps", ["play", "projects", "paint", "storybook", "files", "calc", "getcarts", "settings",
              "music", "notes"]),
    ("editor", ["code", "sprite", "map", "blocks", "scene", "undo", "redo", "run", "save"]),
    ("tools", ["pencil", "bucket", "line", "rect", "oval", "select", "flip", "rotate"]),
    ("files", ["game", "app", "art", "wallpaper", "doc", "folder", "trash"]),
    ("controls", ["close", "back", "forward", "down", "new", "dup", "del", "enter", "backspace"]),
    ("carts", ["cart_py", "cart_lua", "cart_wasm", "cart_p8"]),
]
ALL = [s for _, g in SLOTS for s in g]


def _gear():
    import math
    pts = []
    for i in range(16):
        a = math.pi * 2 * i / 16 - math.pi / 16
        r = 8.6 if i % 2 == 0 else 6.6
        for da in (-0.13, 0.13):
            pts.append((12 + r * math.cos(a + da), 12 + r * math.sin(a + da)))
    return "M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in pts) + " Z"


CART = "M6 3.5h9l3.5 3.5v12a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 19V5A1.5 1.5 0 0 1 6 3.5z"
TRASH = [("line", "M4 7h16"), ("line", "M9.5 7V4.5h5V7"),
         ("shape", "M6 7l1 12.5A1.5 1.5 0 0 0 8.5 21h7a1.5 1.5 0 0 0 1.5-1.5L18 7"),
         ("line", "M10 11v6"), ("line", "M14 11v6")]
DOC = [("shape", "M6 3.5h8l4.5 4.5v11a1.5 1.5 0 0 1-1.5 1.5H6a1.5 1.5 0 0 1-1.5-1.5V5A1.5 1.5 0 0 1 6 3.5z"),
       ("line", "M14 3.5V8h4.5"), ("line", "M8 12.5h8"), ("line", "M8 16h6")]
FOLDER = [("shape", "M3.5 7a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z")]
PLAY = [("shape", "M7.5 5v14l11-7z")]


def R(x, y, w, h, r):
    return f"M{x + r} {y}h{w - 2 * r}a{r} {r} 0 0 1 {r} {r}v{h - 2 * r}a{r} {r} 0 0 1 -{r} {r}" \
           f"h-{w - 2 * r}a{r} {r} 0 0 1 -{r} -{r}v-{h - 2 * r}a{r} {r} 0 0 1 {r} -{r}z"


def C(cx, cy, r):
    return f"M{cx - r} {cy}a{r} {r} 0 1 0 {2 * r} 0a{r} {r} 0 1 0 -{2 * r} 0z"


GLYPHS = {
    "moy": [("shape", "M5 20V10.5a7 7 0 0 1 14 0V20l-2.4-1.7-2.3 1.7-2.3-1.7-2.3 1.7-2.3-1.7z"),
            ("dot", R(8.6, 9, 2.2, 3.6, 1.1)), ("dot", R(13.2, 9, 2.2, 3.6, 1.1))],
    "home": [("shape", "M4 11l8-7 8 7v8a1.5 1.5 0 0 1-1.5 1.5H15v-6H9v6H5.5A1.5 1.5 0 0 1 4 19z")],
    "wifi": [("line", "M3 9.5a13 13 0 0 1 18 0"), ("line", "M6.5 13a8 8 0 0 1 11 0"),
             ("dot", C(12, 17.2, 1.7))],
    "wifi_off": [("line", "M3 9.5a13 13 0 0 1 18 0"), ("line", "M6.5 13a8 8 0 0 1 11 0"),
                 ("dot", C(12, 17.2, 1.7)), ("line", "M4 4l16 16")],
    "battery": [("shape", R(2.5, 7, 17, 10, 2.5)), ("line", "M22 10.5v3"), ("dot", R(5, 9.5, 9, 5, 1))],
    "search": [("shape", C(10.5, 10.5, 6.5)), ("line", "M15.5 15.5L20 20")],
    "play": PLAY,
    "projects": [("shape", "M17.5 13.5V19a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19V8A1.5 1.5 0 0 1 5 6.5h5.5"),
                 ("shape", "M17 3.5l3.5 3.5-8 8H9v-3.5z")],
    "paint": [("line", "M19.5 4.5L11.5 12.5"),
              ("shape", "M10.5 13c-2.6 0-4.2 1.7-4.2 4 0 1.3-.8 2.1-2.3 2.6 3.6 1.2 8.6.2 9.1-3.8z")],
    "storybook": [("shape", "M12 6.5C10 5 7 4.5 3.5 5v13c3.5-.5 6.5 0 8.5 1.5 2-1.5 5-2 8.5-1.5V5C17 4.5 14 5 12 6.5z"),
                  ("line", "M12 6.5v13")],
    "files": FOLDER,
    "calc": [("shape", R(5, 3, 14, 18, 2.5)), ("dot", R(8, 6, 8, 3.5, .8))]
            + [("dot", C(x, y, 1.15)) for y in (13, 16.6) for x in (8.8, 12, 15.2)],
    "getcarts": [("line", "M12 4v10"), ("line", "M7.5 10l4.5 4.5 4.5-4.5"),
                 ("line", "M4.5 15.5v3A1.5 1.5 0 0 0 6 20h12a1.5 1.5 0 0 0 1.5-1.5v-3")],
    "settings": [("shape", _gear()), ("shape", C(12, 12, 2.8))],
    "music": [("line", "M9 18V6l10-2v12"), ("shape", C(6.5, 18, 2.5)), ("shape", C(16.5, 16, 2.5))],
    "notes": DOC,
    "code": [("line", "M8.5 7L3.5 12l5 5"), ("line", "M15.5 7l5 5-5 5"), ("line", "M13.5 5l-3 14")],
    "sprite": [("shape", R(3.5, 3.5, 17, 17, 3.5)), ("dot", R(8, 8.5, 2.2, 3.4, .6)),
               ("dot", R(13.8, 8.5, 2.2, 3.4, .6)), ("line", "M8 15c2.3 2 5.7 2 8 0")],
    "map": [("shape", "M3.5 6.5l5.5-2 6 2 5.5-2v13l-5.5 2-6-2-5.5 2z"), ("line", "M9 4.5v13"),
            ("line", "M15 6.5v13")],
    "blocks": [("shape", R(3.5, 3.5, 7.5, 7.5, 1.6)), ("shape", R(13, 3.5, 7.5, 7.5, 1.6)),
               ("shape", R(3.5, 13, 7.5, 7.5, 1.6)), ("dot", R(13, 13, 7.5, 7.5, 1.6))],
    "scene": [("shape", R(3.5, 5, 17, 14, 2.2)), ("line", "M3.5 16l5-5 4 4 2.5-2.5 5.5 5.5"),
              ("dot", C(16, 9.2, 1.6))],
    "undo": [("line", "M9 14L4.5 9.5 9 5"), ("line", "M4.5 9.5H15a5 5 0 0 1 0 10h-3")],
    "redo": [("line", "M15 14l4.5-4.5L15 5"), ("line", "M19.5 9.5H9a5 5 0 0 0 0 10h3")],
    "run": PLAY,
    "save": [("shape", "M5.5 4h10.5l3.5 3.5v11a1.5 1.5 0 0 1-1.5 1.5h-12A1.5 1.5 0 0 1 4.5 18.5v-13A1.5 1.5 0 0 1 5.5 4z"),
             ("line", "M8 4v4.5h7V4"), ("line", "M8 20v-6h8v6")],
    "pencil": [("shape", "M15 4.5l4.5 4.5L9 19.5H4.5V15z"), ("line", "M12.5 7l4.5 4.5")],
    "bucket": [("shape", "M10 4.5l7.5 7.5-6 6a2 2 0 0 1-2.8 0L4 13.3a2 2 0 0 1 0-2.8z"),
               ("line", "M4.5 12h12.5"),
               ("dot", "M19.6 14.6s1.9 2.3 1.9 3.5a1.9 1.9 0 0 1-3.8 0c0-1.2 1.9-3.5 1.9-3.5z")],
    "line": [("line", "M5 19L19 5")],
    "rect": [("shape", R(4.5, 6, 15, 12, 1.5))],
    "oval": [("shape", "M4 12a8 6 0 1 0 16 0a8 6 0 1 0-16 0z")],
    "select": [("line", d) for d in ("M4.5 8V4.5H8", "M16 4.5h3.5V8", "M19.5 16v3.5H16", "M8 19.5H4.5V16",
                                     "M11 4.5h2", "M11 19.5h2", "M4.5 11v2", "M19.5 11v2")],
    "flip": [("line", "M12 3.5v17"), ("shape", "M9.5 7L3.5 17h6z"), ("dot", "M14.5 7l6 10h-6z")],
    "rotate": [("line", "M19 12a7 7 0 1 1-2.1-5"), ("line", "M17.5 3v4.5H13")],
    "game": [("shape", "M7 8h10a4.5 4.5 0 0 1 4.5 4.5v1a3 3 0 0 1-5.3 1.9L14.5 14h-5l-1.7 1.4a3 3 0 0 1-5.3-1.9v-1A4.5 4.5 0 0 1 7 8z"),
             ("line", "M7 10.5v3.5"), ("line", "M5.25 12.25h3.5"), ("dot", C(15.5, 11, 1.1)),
             ("dot", C(17.8, 13.2, 1.1))],
    "app": [("shape", R(4, 4, 7, 7, 2)), ("shape", R(13, 4, 7, 7, 2)), ("shape", R(4, 13, 7, 7, 2)),
            ("shape", R(13, 13, 7, 7, 2))],
    "art": [("shape", R(3.5, 4.5, 17, 15, 2.5)), ("line", "M3.5 16.5l5-5 5 5"),
            ("line", "M13 15l2.5-2.5 5 5"), ("dot", C(15.5, 9, 1.6))],
    "wallpaper": [("shape", R(3, 4, 18, 12, 2)), ("line", "M9 20h6"), ("line", "M12 16v4"),
                  ("line", "M3 13l5-4 4 3.5 3-2.5 6 4.5")],
    "doc": DOC,
    "folder": FOLDER,
    "trash": TRASH,
    "close": [("line", "M6 6l12 12"), ("line", "M18 6L6 18")],
    "back": [("line", "M15 5l-7 7 7 7")],
    "forward": [("line", "M9 5l7 7-7 7")],
    "down": [("line", "M6 9l6 6 6-6")],
    "new": [("line", "M12 5v14"), ("line", "M5 12h14")],
    "dup": [("shape", R(8, 8, 12, 12, 2)), ("line", "M16 4.5H6A1.5 1.5 0 0 0 4.5 6v10")],
    "del": TRASH,
    "enter": [("line", "M19 5v7a2 2 0 0 1-2 2H6"), ("line", "M10 10l-4 4 4 4")],
    "backspace": [("shape", "M8.5 5H19a1.5 1.5 0 0 1 1.5 1.5v11A1.5 1.5 0 0 1 19 19H8.5L3 12z"),
                  ("line", "M11.5 9.5l5 5"), ("line", "M16.5 9.5l-5 5")],
    "cart_py": [("shape", CART),
                ("line", "M10 8.5c-1 0-1.5.5-1.5 1.5v.6c0 .8-.4 1.2-1 1.4.6.2 1 .6 1 1.4v.6c0 1 .5 1.5 1.5 1.5"),
                ("line", "M14 8.5c1 0 1.5.5 1.5 1.5v.6c0 .8.4 1.2 1 1.4-.6.2-1 .6-1 1.4v.6c0 1-.5 1.5-1.5 1.5")],
    "cart_lua": [("shape", CART), ("dot", "M14 8.6a4 4 0 1 0 0 7.2 3.2 3.2 0 1 1 0-7.2z"),
                 ("dot", C(15.6, 7.6, 1))],
    "cart_wasm": [("shape", CART), ("shape", R(9, 9.5, 6, 6, 1))]
                 + [("line", d) for d in ("M10.5 7.5v2", "M13.5 7.5v2", "M10.5 15.5v2", "M13.5 15.5v2")],
    "cart_p8": [("shape", CART), ("dot", R(8, 8, 4, 4, .3)), ("dot", R(12, 12, 4, 4, .3)),
                ("shape", R(12, 8, 4, 4, .3)), ("shape", R(8, 12, 4, 4, .3))],
}

# a set is a drawing style over the same geometry, or its own sources
SETS = {
    "default": dict(stroke=2.0, shape_fill=0.0, layers=("ink",)),
    "coast": dict(stroke=1.5, shape_fill=0.22, layers=("ink",)),
    "luna": dict(stroke=1.6, shape_fill=1.0, layers=("fill", "ink")),
}


def svg(slot, set_name, layer):
    st = SETS[set_name]
    out = []
    for kind, d in GLYPHS[slot]:
        if layer == "fill":
            if kind == "shape":
                out.append(f'<path d="{d}" fill="#000"/>')
            continue
        if kind == "dot":
            out.append(f'<path d="{d}" fill="#000"/>')
            continue
        fill = ""
        if kind == "shape" and st["shape_fill"] and set_name != "luna":
            fill = f' fill="#000" fill-opacity="{st["shape_fill"]}"'
        else:
            fill = ' fill="none"'
        out.append(f'<path d="{d}"{fill} stroke="#000" stroke-width="{st["stroke"]}" '
                   f'stroke-linecap="round" stroke-linejoin="round"/>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            + "".join(out) + "</svg>")


_MASKS = {}


def mask(slot, set_name, layer, px, bpp):
    key = (slot, set_name, layer, px, bpp)
    if key not in _MASKS:
        src = svg(slot, set_name, layer)
        im = Image.open(io.BytesIO(bytes(resvg_py.svg_to_bytes(svg_string=src, width=px, height=px))))
        a = im.convert("RGBA").split()[3]
        _MASKS[key] = M.levels(a, 1 << bpp)
    return _MASKS[key]


def write_sources():
    total = {}
    for set_name in SETS:
        d = os.path.join(LOOK, "icons", "smooth", set_name)
        os.makedirs(d, exist_ok=True)
        n = 0
        for slot in ALL:
            for layer in SETS[set_name]["layers"]:
                s = svg(slot, set_name, layer)
                name = slot if layer == "ink" else f"{slot}.{layer}"
                with open(os.path.join(d, name + ".svg"), "w") as fh:
                    fh.write(s)
                n += len(s)
        total[set_name] = n
    return total


PIXEL_MAP = {"moy": "moy", "home": "home", "wifi": "wifi", "wifi_off": "wifi_off", "battery": "batt",
             "settings": "gear", "music": "music", "code": "code", "sprite": "paint", "paint": "paint",
             "projects": "edit", "map": "map", "blocks": "blocks", "scene": "scene", "new": "new",
             "dup": "dup", "del": "del", "trash": "del", "close": "close", "run": "run", "play": "run",
             "save": "save", "undo": "undo", "redo": "redo"}


def pixel_icon(slot):
    """The 16x16 IconSheet art for a slot (palette-coloured, 0 = clear), or
    None when the pixel set has no art for it yet."""
    name = PIXEL_MAP.get(slot)
    if name is None or name not in ICON_ART:
        return None
    im = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for y, row in enumerate(ICON_ART[name][:16]):
        for x, ch in enumerate(row[:16]):
            if ch != "." and ch in "0123456789abcdefABCDEF":
                c = int(ch, 16) & 15
                if c:
                    im.putpixel((x, y), PAL[c] + (255,))
    return im


def pixel_fallback(slot, ink):
    """A slot the pixel set lacks: the smooth source thresholded at 16 px, 1 bpp."""
    m = mask(slot, "default", "ink", 16, 1)
    im = Image.new("RGBA", (16, 16), q(ink) + (255,))
    im.putalpha(m)
    return im


def theme_icons(T):
    return {"pico": ("pixel", None), "bench": ("pixel", None), "luna": ("smooth", "luna"),
            "coast": ("smooth", "coast")}.get(T["key"], ("smooth", "default"))


LUNA_FILL = {"files": "#f5c542", "folder": "#f5c542", "notes": "#ffffff", "doc": "#ffffff",
             "art": "#ffffff", "wallpaper": "#9fd0ff", "play": "#46b843", "run": "#46b843",
             "game": "#c9d3e6", "settings": "#b8c0cc", "trash": "#c9d3e6", "del": "#c9d3e6",
             "moy": "#c9b8ff", "home": "#f2a65a", "projects": "#f5c542", "paint": "#e2582e",
             "storybook": "#e2582e", "music": "#46b843", "battery": "#46b843", "calc": "#c9d3e6",
             "getcarts": "#46b843", "save": "#7da7ea", "sprite": "#9be36b", "map": "#9fd0ff"}


def draw_icon(cv, T, slot, x, y, px, ink, fill=None):
    kind, set_name = theme_icons(T)
    if kind == "pixel":
        im = pixel_icon(slot) or pixel_fallback(slot, ink)
        s = max(1, px // 16)
        im = im.resize((16 * s, 16 * s), Image.NEAREST)
        cv.img.paste(im.convert("RGB"), (cv.L(x), cv.L(y)), im.split()[3])
        return
    bpp = 2 if px <= 16 else 4
    for layer in SETS[set_name]["layers"]:
        m = mask(slot, set_name, layer, px, bpp)
        c = ink if layer == "ink" else (fill or LUNA_FILL.get(slot) or "#7da7ea")
        cv._blend(cv.L(x), cv.L(y), m, c)


# ==========================================================================
# icons_sheet.png
# ==========================================================================
def icons_sheet(src_bytes):
    cols = [("16 light", LIGHT, 16, "#ffffff", LIGHT["text"]),
            ("16 dark", DARK, 16, DARK["surface"], DARK["text"]),
            ("24 light", LIGHT, 24, "#ffffff", LIGHT["text"]),
            ("24 dark", DARK, 24, DARK["surface"], DARK["text"]),
            ("pixel 16", PICO, 16, M.P8["navy"], M.P8["white"]),
            ("pixel ×2", PICO, 32, M.P8["navy"], M.P8["white"]),
            ("Luna, 24", M.LUNA, 24, M.LUNA["surface"], "#1d3f8f"),
            ("Coast, 24", M.COAST, 24, "#f6f6f8", M.COAST["text"])]
    cw, rh, lw = 84, 40, 120
    groups = [(g, s) for g, s in SLOTS]
    H = 70 + sum(28 + len(s) * rh for _, s in groups) + 26
    W = lw + cw * len(cols) + 20
    cv = M.Cv(W, H, "#e9eaee")
    f = Face("inter", 13, 500, 4)
    fb = Face("inter", 13, 700, 4)
    fs = Face("inter", 11, 500, 2)
    cv.text(10, 24, "Icon sets: every slot in the default smooth set (Moy, light and dark),", fb, "#15161d")
    cv.text(10, 42, "the pixel set (Pico, Bench), and the sets Luna and Coast bring", fb, "#15161d")
    for i, (name, *_r) in enumerate(cols):
        cv.text(lw + i * cw + cw // 2, 62, name, fs, "#4a4d5c", align="center")
    y = 70
    for g, slots in groups:
        cv.text(10, y + 18, g, fb, "#15161d")
        y += 28
        for slot in slots:
            cv.text(18, y + 25, slot, f, "#33364a")
            for i, (_, T, px, bg, ink) in enumerate(cols):
                x0 = lw + i * cw
                cv.rect(x0 + 4, y + 2, cw - 8, rh - 4, bg)
                ix, iy = x0 + (cw - px) // 2, y + (rh - px) // 2
                if T is PICO:
                    im = pixel_icon(slot)
                    fallback = im is None
                    im = (im or pixel_fallback(slot, ink)).resize((px, px), Image.NEAREST)
                    cv.img.paste(im.convert("RGB"), (ix, iy), im.split()[3])
                    if fallback:
                        cv.rrect(x0 + cw - 13, y + 5, 6, 6, 3, "#e07b00")
                else:
                    draw_icon(cv, T, slot, ix, iy, px, ink)
            y += rh
    cv.text(10, H - 6, "orange dot: no pixel art yet; drawn from the smooth source at 1 bpp", fs,
            "#4a4d5c")
    cv.save("icons_sheet", "Icon sets: the default smooth set at 16 and 24 px on light and dark, "
            "the pixel set at 1x and 2x, and Luna's and Coast's own sets")
    big = cv.image().resize((W * 2, H * 2), Image.NEAREST)
    big.save(os.path.join(OUT, "icons_sheet@2x.png"), optimize=True)
    INDEX.append("icons_sheet@2x.png — same, enlarged 2x (nearest)")


def icon_flash():
    """Bytes per set: trimmed masks at each size (8-byte record each)."""
    hh, dk = (12, 16), (18, 20, 24, 28)
    out = {}
    for set_name in SETS:
        for label, sizes in (("handheld", hh), ("desk", dk)):
            n = 0
            for slot in ALL:
                for layer in SETS[set_name]["layers"]:
                    for px in sizes:
                        bpp = 2 if px <= 16 else 4
                        m = mask(slot, set_name, layer, px, bpp)
                        bb = m.getbbox() or (0, 0, 0, 0)
                        n += 8 + ((bb[2] - bb[0]) * (bb[3] - bb[1]) * bpp + 7) // 8
            out[(set_name, label)] = n
    return out


# ==========================================================================
# the Studio (docs/studio_2026-09.md's layout, drawn in this look)
# ==========================================================================
SYN = {
    "moy-light": dict(bg="#ffffff", line="#f1effc", gutter="#6b7085", text="#15161d", com="#5f6478",
                      kw="#6a35c4", fn="#1662a8", num="#a8510a", str="#1f7a3a"),
    "moy-dark": dict(bg="#181b38", line="#23274b", gutter="#8d92bd", text="#eef0fb", com="#9aa0cc",
                     kw="#c9a2ff", fn="#7fd0ff", num="#ffb070", str="#9be3a5"),
    "pico": dict(bg=M.P8["navy"], line="#000000", gutter=M.P8["lgrey"], text=M.P8["white"],
                 com=M.P8["peach"], kw=M.P8["pink"], fn=M.P8["blue"], num=M.P8["orange"],
                 str=M.P8["lime"]),
}


def syn_of(T):
    return SYN["pico" if T is PICO else ("moy-dark" if T is DARK else "moy-light")]


CART_DIR = os.path.join(REPO, "system_carts/moybyte.star_catcher.moy")
SRC = os.path.join(CART_DIR, "main.py")


def tokens_by_line():
    """Each source line as [(role, text)] from Python's own tokenizer."""
    text = open(SRC).read()
    lines = text.split("\n")
    found = [[] for _ in lines]
    toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    for i, t in enumerate(toks):
        if t.start[0] != t.end[0]:
            continue
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if t.type == tokenize.COMMENT:
            role = "com"
        elif t.type == tokenize.STRING:
            role = "str"
        elif t.type == tokenize.NUMBER:
            role = "num"
        elif t.type == tokenize.NAME and keyword.iskeyword(t.string):
            role = "kw"
        elif t.type == tokenize.NAME and nxt is not None and nxt.string == "(":
            role = "fn"
        else:
            role = "text"
        found[t.start[0] - 1].append((t.start[1], role, t.string))
    rows = []
    for ln, toks_ in zip(lines, found):
        row, col = [], 0
        for c0, role, s in toks_:
            if c0 > col:
                row.append(("text", ln[col:c0]))
            row.append((role, s))
            col = c0 + len(s)
        rows.append(row)
    return rows


ROWS = tokens_by_line()
CUR_LINE, CUR_COL = 42, 13


def code_face(T, desk):
    if T is PICO:
        return Face("galmono", 10, 400, 1)
    return Face("jbmono", 15 if desk else 11, 400, 4 if desk else 2)


def draw_code(cv, T, x, y, w, h, desk, first=34):
    S = syn_of(T)
    f = code_face(T, desk)
    lh = 22 if desk else (14 if T is not PICO else 13)
    gut = 46 if desk else 26
    cv.rect(x, y, w, h, S["bg"])
    adv = f.glyph("m")[3] * cv.k
    for i in range((h - 4) // lh):
        ln = first + i
        if ln > len(ROWS):
            break
        yy = y + 4 + i * lh
        base = yy + (lh + f.cap) // 2 - 1
        cur = ln == CUR_LINE
        if cur:
            cv.rect(x, yy, w, lh, S["line"])
            cv.rect(x, yy, 2, lh, T["value"])
        cv.text(x + gut - 8, base, str(ln), f, S["text"] if cur else S["gutter"], align="right")
        cx = x + gut
        cv.set_clip(x + gut, yy, w - gut - 6, lh)
        for role, s in ROWS[ln - 1]:
            cv.text(cx, base, s, f, S[role])
            cx += len(s) * adv
        cv.clip = None
        if cx > x + w - 6:
            bg = S["line"] if cur else S["bg"]
            for j, a in enumerate((0.35, 0.6, 0.85)):
                cv.rect(x + w - 6 - (3 - j) * 4, yy, 4, lh, bg, a)
            cv.rect(x + w - 6, yy, 6, lh, bg)
        if cur:
            cv.rect(x + gut + CUR_COL * adv - 1, yy + 2, 2, lh - 4, T["accent"])


# ---- Star Catcher's own sheet and a frame of the running game ------------
def sheet_pixels():
    rows = [ln.strip() for ln in open(os.path.join(CART_DIR, "sprites.moygfx")) if ln.strip()]
    return [[int(ch, 16) for ch in r[:128]] for r in rows[:128]]


SHEET = sheet_pixels()


def tile_pix(t):
    tx, ty = (t % 16) * 8, (t // 16) * 8
    return [[SHEET[ty + r][tx + c] for c in range(8)] for r in range(8)]


def sprite_img(pix, scale):
    im = Image.new("RGBA", (len(pix[0]), len(pix)), (0, 0, 0, 0))
    for r, row in enumerate(pix):
        for c, v in enumerate(row):
            if v:
                im.putpixel((c, r), PAL[v] + (255,))
    return im.resize((im.width * scale, im.height * scale), Image.NEAREST)


def paste_rgba(cv, im, x, y):
    cv.img.paste(im.convert("RGB"), (cv.L(x), cv.L(y)), im.split()[3])


def game_frame():
    """A frame of Star Catcher, drawn from its own sheet: what the Run pane shows."""
    import random
    rnd = random.Random(7)
    g = M.Cv(320, 240, "#000000")
    for _ in range(40):
        g.rect(rnd.randrange(320), rnd.randrange(200), 1, 1, PAL[rnd.choice((5, 6, 7, 13))])
    for x, y in ((40, 30), (120, 80), (210, 50), (270, 120), (90, 140), (180, 160)):
        paste_rgba(g, sprite_img(tile_pix(2), 2), x, y)
    for i in range(3):
        paste_rgba(g, sprite_img(tile_pix(6 if i < 2 else 7), 2), 6 + i * 18, 6)
    pf = M.Petme(1, 1)
    g.text(314, 18, "SCORE 12", pf, PAL[7], align="right")
    g.text(314, 30, "COMBO x3", pf, PAL[10], align="right")
    paste_rgba(g, sprite_img(tile_pix(0), 4), 132, 186)
    span = [[SHEET[r][c] for c in range(24, 48)] for r in range(8)]
    paste_rgba(g, sprite_img(span, 2), 124, 214)
    g.rect(0, 232, 320, 8, PAL[1])
    return g.image()


GAME = game_frame()


# ---- the Sprites panel: PICO-8's layout at any size ----------------------
def checker(cv, x, y, w, h, s):
    a, b = "#6e6e78", "#9a9aa4"
    for yy in range(0, h, s):
        for xx in range(0, w, s):
            cv.rect(x + xx, y + yy, min(s, w - xx), min(s, h - yy),
                    a if ((xx // s) + (yy // s)) & 1 else b)


TOOLS = ("pencil", "bucket", "line", "rect", "oval", "select", "flip", "rotate")
FLAGS = (8, 9, 10, 11, 12, 13, 14, 15)


def sprites_panel(cv, T, x, y, w, h, desk):
    m = dict(pad=16, cell=36, pal=32, tool=40, flag=14, sheet=2, page=(30, 24), gap=24) if desk else \
        dict(pad=6, cell=17, pal=15, tool=22, flag=9, sheet=1, page=(17, 14), gap=8)
    fr = F(T, "row", DK) if desk else F(T, "meta", HH)
    fc = F(T, "col", DK) if desk else F(T, "meta", HH)
    cv.rect(x, y, w, h, T["surface"])
    pad, cell, gap = m["pad"], m["cell"], m["gap"]
    pix = tile_pix(0)
    cx, cy = x + pad, y + pad
    cv.rect(cx - 1, cy - 1, 8 * cell + 1, 8 * cell + 1, T["line"])
    for r in range(8):
        for c in range(8):
            v = pix[r][c]
            if v == 0:
                checker(cv, cx + c * cell, cy + r * cell, cell - 1, cell - 1, max(3, cell // 3))
            else:
                cv.rect(cx + c * cell, cy + r * cell, cell - 1, cell - 1, PAL[v])
    fx, fy = cx + 3 * cell, cy + 2 * cell
    cv.ring(fx - 1, fy - 1, cell + 1, cell + 1, 0, 1, "#000000")
    M.ring_mark(cv, T, fx - 1, fy - 1, cell + 1, cell + 1, 0)
    # palette: the first 16, true colours, 0 is clear
    px0, py0 = cx + 8 * cell + gap, cy
    p = m["pal"]
    for i in range(16):
        sx, sy = px0 + (i % 4) * p, py0 + (i // 4) * p
        if i == 0:
            checker(cv, sx, sy, p, p, max(3, p // 4))
            cv.shape(sx, sy, p, p, lambda d, s: d.line((p * s, 0, 0, p * s), fill=255, width=max(1, int(s))),
                     "#ffffff")
        else:
            cv.rect(sx, sy, p, p, PAL[i])
    sel = 11
    sx, sy = px0 + (sel % 4) * p, py0 + (sel // 4) * p
    cv.ring(sx, sy, p, p, 0, 2 if desk else 1, "#ffffff")
    cv.rect(sx, sy + p - 3, p, 3, T["value"])
    # tools: two rows of four, the current one raised with the current mark;
    # beside the palette on the handheld, under the sprite's name on the desk
    tx0, ty0 = (px0, py0 + 4 * p + 66) if desk else (px0 + 4 * p + gap, cy)
    t = m["tool"]
    s = 20 if desk else 14
    for i, name in enumerate(TOOLS):
        bx, by = tx0 + (i % 4) * t, ty0 + (i // 4) * t
        on = i == 0
        if on:
            if M.cur_style(T) != "bar":
                M.current_mark(cv, T, bx + 1, by + 1, t - 2, t - 2, 6)
            else:
                cv.rrect(bx + 1, by + 1, t - 2, t - 2, 6 if cv.aa else 0, T["sunk"] if T["mode"] == "light" else T["raised"])
                cv.rect(bx + 4, by + t - 4, t - 8, 2, T["value"])
        draw_icon(cv, T, name, bx + (t - s) // 2, by + (t - s) // 2, s, T["text"] if on else T["dim"])
    # flags
    fy0 = ty0 + 2 * t + (10 if desk else 6)
    fl = m["flag"]
    cv.text(tx0, fy0 + fc.cap, "Flags", fc, T["dim"])
    fy0 += fc.cap + (8 if desk else 4)
    for i, c in enumerate(FLAGS):
        bx = tx0 + i * (fl + (5 if desk else 3))
        if i in (0, 3):
            cv.rrect(bx, fy0, fl, fl, 3 if cv.aa else 0, PAL[c])
        else:
            cv.ring(bx, fy0, fl, fl, 3 if cv.aa else 0, 1.5 if desk else 1, PAL[c])
    # sprite number, size, colour name, preview
    ny = py0 + 4 * p + (24 if desk else 14)
    draw_icon(cv, T, "back", px0 - 3, ny - fr.cap - (5 if desk else 4), s, T["dim"])
    cv.text(px0 + s, ny, "0", fr, T["text"])
    nx = px0 + s + fr.width("0") + 2
    draw_icon(cv, T, "forward", nx, ny - fr.cap - (5 if desk else 4), s, T["dim"])
    cv.text(nx + s + (10 if desk else 6), ny, "8×8", fr, T["text"])
    cy2 = ny + (26 if desk else 14)
    cv.text(px0, cy2, f"{sel}  {COLOUR_NAME[sel].replace('_', ' ')}", fr, T["text"])
    pvy = (fy0 + fl + 16) if desk else cy2 + 6
    pw = 4 * t if desk else (4 * p + gap + 4 * t)
    ph = 32 if desk else 22
    checker(cv, px0, pvy, pw, ph, 4)
    paste_rgba(cv, sprite_img(pix, 1), px0 + 8, pvy + (ph - 8) // 2)
    paste_rgba(cv, sprite_img(pix, 2 if not desk else 4), px0 + 24, pvy + (ph - (16 if not desk else 32)) // 2)
    # the sheet, always along the bottom, with its page buttons
    sc = m["sheet"]
    hints = 0 if desk else 16
    sy0 = y + h - pad - 32 * sc - hints
    cv.rect(x, sy0 - (10 if desk else 5), w, 1, T["line"])
    cv.rect(cx, sy0, 128 * sc, 32 * sc, "#000000")
    paste_rgba(cv, sprite_img([row[:128] for row in SHEET[:32]], sc), cx, sy0)
    cv.ring(cx - 1, sy0 - 1, 8 * sc + 2, 8 * sc + 2, 0, 2 if desk else 1, T["value"])
    pw_, ph_ = m["page"]
    bx0 = cx + 128 * sc + gap
    fpg = fc
    for i in range(8):
        bx, by = bx0 + (i % 4) * (pw_ + 4), sy0 + (i // 4) * (ph_ + 4)
        st = M.bstyle(T, "editor", "toggle")
        on = i == 0
        if on and M.cur_style(T) != "bar":
            cv.rrect(bx, by, pw_, ph_, 5 if cv.aa else 0, M.paint(st["fill"], by, ph_))
            ink = M.current_mark(cv, T, bx, by, pw_, ph_, 5 if cv.aa else 0)
        else:
            cv.rrect(bx, by, pw_, ph_, 5 if cv.aa else 0, M.paint(st["fill"], by, ph_))
            if st["border"]:
                cv.ring(bx, by, pw_, ph_, 5, st["border"][0], st["border"][1])
            ink = st["ink"]
            if on:
                cv.rect(bx + 3, by + ph_ - 3, pw_ - 6, 2, T["value"])
        cv.text(bx + pw_ // 2, M.mid_base(fpg, by, ph_), str(i), fpg, ink, align="center")
    if not desk:
        fk, fh = F(T, "key", HH), F(T, "hint", HH)
        hx, base = 8, y + h - 3
        for keys, label in ((("Q", "W"), "sprite"), (("H", "V"), "flip"), (("1", "4"), "zoom")):
            for k in keys:
                hx += M.keycap(cv, T, hx, base, k, fk, T["surface"]) + 2
            hx += cv.text(hx + 2, base, label, fh, T["dim"]) + 12
        cv.text(320 - 8, base, "hold: pick", fh, T["dim"], align="right")


# ---- the handheld: the fullscreen tier (Studio §7) -----------------------
FILES = (("settings", "Make it mine"), ("code", "main.py"), ("sprite", "sprites"), ("art", "cover"))


def files_bar(cv, T, open_name, h=26):
    """One bar: back, a tile per project file (the open one named), undo,
    redo, Play; the clock and the OS's X keep the right zone."""
    hb = T["hhbar"]
    ink, dim = hb["ink"] or T["text"], hb["dim"] or T["dim"]
    if hb["style"] == "flat":
        cv.rect(0, 0, 320, h, hb["fill"])
    else:
        cv.rect(0, 0, 320, h, T["bg"])
        cv.rect(0, h - 1, 320, 1, T["line"])
    s = 16
    mid = h // 2
    draw_icon(cv, T, "back", 2, mid - s // 2, s, dim)
    x = 22
    lf = F(T, "tab", HH)
    for slot, name in FILES:
        if name == open_name:
            tw = s + 4 + lf.width(name)
            if M.cur_style(T) == "plate":
                c = M.current_mark(cv, T, x - 4, mid - 10, tw + 8, 20, 5)
            elif T["tabs"]["style"] == "pill":
                cv.rrect(x - 4, mid - 10, tw + 8, 20, 5, T["tabs"]["pill"])
                c = T["tabs"]["pill_ink"]
            else:
                cv.rrect(x - 4, 3, tw + 8, h - 3, 5 if cv.aa else 0, T["surface"] if T is not PICO else M.P8["dgrey"],
                         corners=(True, True, False, False))
                cv.rect(x - 1, 3, tw + 2, 2, T["value"])
                c = ink
            draw_icon(cv, T, slot, x, mid - s // 2, s, c)
            cv.text(x + s + 4, M.mid_base(lf, 0, h), name, lf, c)
            x += tw + 10
        else:
            draw_icon(cv, T, slot, x, mid - s // 2, s, dim)
            x += s + 8
    x += 2
    draw_icon(cv, T, "undo", x, mid - s // 2, s, dim)
    draw_icon(cv, T, "redo", x + 20, mid - s // 2, s, mix(dim, hb["fill"] or T["bg"], 0.55))
    st = M.bstyle(T, "editor", "primary")
    bx = x + 42
    cv.rrect(bx, mid - 9, 22, 18, min(6, st["r"]) if cv.aa else 0, M.paint(st["fill"], mid - 9, 18))
    draw_icon(cv, T, "play", bx + 4, mid - 7, 14, st["ink"])
    fs = F(T, "status", HH)
    cv.text(320 - 24, M.mid_base(fs, 0, h), "14:32", fs, ink, align="right")
    draw_icon(cv, T, "close", 320 - 19, mid - 8, 16, dim)
    return h


def hh_code(T):
    cv = M.new_cv(T, False, T["bg"])
    top = files_bar(cv, T, "main.py")
    draw_code(cv, T, 0, top, 320, 204 - top, False)
    cv.rect(0, 204, 320, 1, T["line"])
    fm = F(T, "meta", HH)
    cv.text(8, 216, f"Ln {CUR_LINE}, Col {CUR_COL}", fm, T["dim"])
    cv.text(160, 216, "177 lines", fm, T["dim"], align="center")
    cv.text(312, 216, "Python", fm, T["dim"], align="right")
    keys = "()[]{}=:,.\"_+-*"
    kw = (320 - 4) // len(keys)
    f = code_face(T, False)
    for i, ch in enumerate(keys):
        M.button(cv, T, "editor", "normal", 3 + i * kw, 221, kw - 2, 17, ch, {"key": f, "op": f})
    return cv


def hh_sprites(T):
    cv = M.new_cv(T, False, T["surface"])
    top = files_bar(cv, T, "sprites")
    sprites_panel(cv, T, 0, top, 320, 240 - top, False)
    return cv


# ---- the desk: one Studio window, panes in a preset layout ----------------
def os_bar(cv, T):
    b = T["bar"]
    cv.rect(0, 0, 1024, b["h"], b["fill"] or T["surface"], b["alpha"])
    if b["line"]:
        cv.rect(0, b["h"] - b["line"][0], 1024, b["line"][0], b["line"][1])
    sys_fill, body, eye = b["sys"] or (T["raised"], T["value"], T["raised"])
    cv.rrect(8, 6, 32, 28, 9, sys_fill)
    M.moy(cv, 12, 8, 24, body, eye)
    fc = F(T, "chip", DK)
    w = 18 + 8 + fc.width("Studio") + 24
    cv.rrect(52, 6, w, 28, 8, b["chip_cur"])
    if M.current_mark(cv, T, 52, 6, w, 28, 8) is None:
        cv.rrect(62, 31, w - 20, 3, 1.5, T["value"])
    draw_icon(cv, T, "code", 64, 11, 18, T["text"])
    cv.text(90, 25, "Studio", fc, T["text"])
    fs = F(T, "status", DK)
    tw = cv.text(1010, 26, "14:32", fs, T["text"], align="right")
    draw_icon(cv, T, "battery", 1010 - tw - 30, 9, 22, T["text"])
    draw_icon(cv, T, "wifi", 1010 - tw - 58, 8, 21, T["text"])


def pane_bar(cv, T, x, y, w, h, tabs, focused, lead=False, trail=False):
    """A pane's own tab bar. The leftmost starts with the project menu; the
    rightmost top one ends with undo, redo and Play."""
    bar_bg = T["sunk"] if T["mode"] == "light" else T["bg"]
    cv.rect(x, y, w, h, bar_bg)
    cv.rect(x, y + h - 1, w, 1, T["line"])
    lf, bf = F(T, "chip", DK), Face(T["fam"], 15, T["w_semi"], 4)
    s = 18
    cx = x + 10
    mid = y + h // 2
    if lead:
        draw_icon(cv, T, "cart_py", cx, mid - s // 2, s, T["text"])
        cx += s + 6
        cx += cv.text(cx, M.mid_base(bf, y, h), "Star Catcher", bf, T["text"]) + 4
        draw_icon(cv, T, "down", cx, mid - 8, 16, T["dim"])
        cx += 26
        cv.rect(cx - 6, y + 9, 1, h - 18, T["line"])
        cx += 6
    for slot, label, active in tabs:
        tw = s + 6 + lf.width(label) + 8 + 12
        if active:
            cv.rrect(cx - 8, y + 5, tw + 16, h - 5, 7, T["surface"], corners=(True, True, False, False))
            if focused:
                cv.set_clip(cx - 8, y + 5, tw + 16, 3)
                cv.rrect(cx - 8, y + 5, tw + 16, 10, 7, T["value"])
                cv.clip = None
        c = T["text"] if active else T["dim"]
        draw_icon(cv, T, slot, cx, mid - s // 2 + 2, s, c)
        cv.text(cx + s + 6, M.mid_base(lf, y + 2, h), label, lf, c)
        draw_icon(cv, T, "close", cx + tw - 12, mid - 5, 12, T["dim"])
        cx += tw + 24
    draw_icon(cv, T, "new", cx - 4, mid - 8, 16, T["dim"])
    if trail:
        st = M.bstyle(T, "editor", "primary")
        bw, bh = 92, 28
        bx = x + w - bw - 8
        cv.rrect(bx, mid - bh // 2, bw, bh, min(st["r"], 8), M.paint(st["fill"], mid - bh // 2, bh))
        draw_icon(cv, T, "play", bx + 14, mid - 8, 16, st["ink"])
        cv.text(bx + 38, M.mid_base(lf, mid - bh // 2, bh), "Play", lf, st["ink"])
        draw_icon(cv, T, "redo", bx - 34, mid - 9, 18, mix(T["dim"], bar_bg, 0.55))
        draw_icon(cv, T, "undo", bx - 64, mid - 9, 18, T["dim"])


def run_pane(cv, T, x, y, w, h):
    cv.rect(x, y, w, h, "#000000" if T["mode"] == "dark" else "#14151c")
    k = max(1, min(w // 320, h // 240))
    im = GAME.resize((320 * k, 240 * k), Image.NEAREST)
    cv.img.paste(im, (cv.L(x + (w - 320 * k) // 2), cv.L(y + (h - 240 * k) // 2)))
    return k


PRESETS = (("Code + Run", "cr"), ("Code + Sprites + Run", "csr"), ("One panel", "one"),
           ("Blocks + Code", "bc"))


def preset_glyph(cv, T, x, y, w, h, kind, ink):
    cv.ring(x, y, w, h, 3, 1.5, ink)
    g = 2

    def box(a, b, c, d):
        cv.rrect(x + a * w + g, y + b * h + g, c * w - 2 * g, d * h - 2 * g, 2, ink, 0.35)
    if kind == "cr":
        box(0, 0, .38, 1); box(.38, 0, .62, 1)
    elif kind == "csr":
        box(0, 0, .47, .52); box(0, .52, .47, .48); box(.47, 0, .53, 1)
    elif kind == "one":
        box(0, 0, 1, 1)
    else:
        box(0, 0, .5, 1); box(.5, 0, .5, 1)


def preset_picker(cv, T, x, y):
    """The project menu, open: switch project, cart info, and the layouts."""
    w, rh = 330, 50
    rows = [("projects", "Switch project…", None), ("notes", "Cart info", None)]
    h = 16 + 2 * 38 + 14 + 26 + len(PRESETS) * rh + 36
    M.soft(cv, T, x, y, w, h, 12, 1.0)
    cv.rrect(x, y, w, h, 12, T["surface"])
    if T["mode"] == "dark":
        cv.ring(x, y, w, h, 12, 1, T["line"])
    fr, fc, fm = F(T, "row", DK), F(T, "col", DK), F(T, "meta", DK)
    yy = y + 8
    for slot, label, _ in rows:
        draw_icon(cv, T, slot, x + 16, yy + 9, 20, T["dim"])
        cv.text(x + 48, M.mid_base(fr, yy, 38), label, fr, T["text"])
        yy += 38
    cv.rect(x + 12, yy + 6, w - 24, 1, T["line"])
    yy += 14
    cv.text(x + 16, yy + 16, "Layout", fc, T["dim"])
    yy += 26
    for i, (name, kind) in enumerate(PRESETS):
        cur = kind == "csr"
        if cur:
            M.current_row(cv, T, x + 8, yy + 3, w - 16, rh - 6, 8, True)
        ink = M.focus_mark(cv, T, (x + 8, yy + 3, w - 16, rh - 6, 8), "part") if kind == "cr" else None
        preset_glyph(cv, T, x + 18, yy + 11, 44, 28, kind, ink or (T["text"] if cur else T["dim"]))
        cv.text(x + 76, M.mid_base(fr, yy, rh), name, fr, ink or T["text"])
        M.keycap(cv, T, x + w - 36, M.mid_base(fm, yy, rh) + 2, str(i + 1), F(T, "key", HH), T["surface"])
        yy += rh
    cv.text(x + 16, yy + 22, "Ctrl+L cycles the layouts", fm, T["dim"])


def studio_desk(T, preset="csr", picker=False):
    cv = M.new_cv(T, True)
    M.backdrop(cv, T, "desk")
    os_bar(cv, T)
    top = M.window(cv, T, 0, 40, 1024, 560, "Star Catcher · Studio", "code", True)
    bh = 36
    y0 = top + bh
    if preset == "csr":
        lw = 480
        run_h = 248
        pane_bar(cv, T, 0, top, lw, bh, [("run", "Run", True)], False, lead=True)
        pane_bar(cv, T, lw + 1, top, 1024 - lw - 1, bh, [("sprite", "sprites", True)], False, trail=True)
        cv.rect(lw, top, 1, 600 - top, T["line"])
        k = run_pane(cv, T, 0, y0, lw, run_h)
        pane_bar(cv, T, 0, y0 + run_h, lw, 34, [("code", "main.py", True)], True)
        draw_code(cv, T, 0, y0 + run_h + 34, lw, 600 - (y0 + run_h + 34), True, first=36)
        sprites_panel(cv, T, lw + 1, y0, 1024 - lw - 1, 600 - y0, True)
    else:
        rw = 640
        lw = 1024 - rw - 1
        pane_bar(cv, T, 0, top, lw, bh, [("code", "main.py", True)], True, lead=True)
        pane_bar(cv, T, lw + 1, top, rw, bh, [("run", "Run", True)], False, trail=True)
        cv.rect(lw, top, 1, 600 - top, T["line"])
        draw_code(cv, T, 0, y0, lw, 600 - y0, True, first=34)
        k = run_pane(cv, T, lw + 1, y0, rw, 600 - y0)
    if picker:
        preset_picker(cv, T, 10, top + bh - 2)
    return cv, k


# ==========================================================================
def contrast_report():
    for k, S in SYN.items():
        print(k, "  ".join(f"{r} {ratio(S[r], S['bg']):.1f}" for r in
                           ("text", "com", "kw", "fn", "num", "str", "gutter")),
              f"| on the current line: com {ratio(S['com'], S['line']):.1f}")


def main():
    """The Studio scenes (the later Studio project's), into OUT."""
    src = write_sources()
    icons_sheet(src)
    for T, tag in ((LIGHT, "moy-light"), (DARK, "moy-dark"), (PICO, "pico")):
        hh_code(T).save(f"{tag}_handheld_studio-code",
                        f"Studio, fullscreen tier: the Code panel on Star Catcher's main.py, "
                        f"the project's files in the bar; {T['name']}", x3=True)
        hh_sprites(T).save(f"{tag}_handheld_studio-sprites",
                           f"Studio, fullscreen tier: the Sprites panel on Star Catcher's frog; {T['name']}",
                           x3=True)
    for T, tag, pick in ((LIGHT, "moy-light", True), (DARK, "moy-dark", False)):
        cv, k = studio_desk(T, "csr", pick)
        what = "Studio window, preset Code + Sprites + Run: Run (1x) over Code, Sprites beside"
        cv.save(f"{tag}_desktop_studio", what + ("; the project menu open on the layout presets" if pick else "")
                + f"; {T['name']}")
    cv, k = studio_desk(LIGHT, "cr")
    cv.save("moy-light_desktop_studio-code-run",
            f"Studio window, preset Code + Run: Run integer-scaled to {k}x beside Code; Moy, light mode")
    print("SVG source bytes per set:", src)
    for kk, v in icon_flash().items():
        print("mask bytes", kk, v)
    print("pixel set: 128 bytes per slot;", len(ALL), "slots =", 128 * len(ALL))
    print("pixel slots with art:", sum(1 for s_ in ALL if pixel_icon(s_) is not None), "of", len(ALL))
    contrast_report()


if __name__ == "__main__":
    main()
