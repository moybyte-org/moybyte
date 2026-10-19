"""The refined 16x16 pixel icon set, drawn as a pixel artist would, by rule.

Each icon is a few filled parts on a 16-grid, each in a colour RAMP (shade,
base, light) of the 16-colour base palette. The rules:

- light comes from the top left: a part's top and left edge pixels take its
  light tone, its bottom and right edge pixels its shade, the rest its base
  (a part marked flat, too thin to shade, keeps its base);
- one outline: palette 1 (dark blue) on every pixel 4-adjacent to the
  silhouette;
- details (a door, a page's lines, a slash) are drawn after shading, inside
  the silhouette, and get no outline;
- content sits in rows and columns 1-14, so the outline fits in 16.

Moy is hand-drawn: the canonical Moy (docs/visual_identity_v1.md §6.5),
outlined in index 0. The sheet's clear pixel is CLEAR (16, a `.` in the
file under its `clear 16` header), so index 0 draws black. Redo is undo's
mirror.

Palette slots are roles, so a theme recolours an icon with a palette swap:
1 outline, 5/6/7 the grey ramp, 12/13 the blue ramp, 11/3 green, 10/9 yellow,
8/2 red, 13/2 Moy's lavender, 4 brown, 14 pink, 15 peach.
"""
from PIL import Image, ImageDraw

OUTLINE = 1
CLEAR = 16
RAMPS = {
    "grey": (5, 6, 7), "paper": (6, 7, 7), "blue": (13, 12, 6), "green": (3, 11, 10),
    "yellow": (9, 10, 7), "red": (2, 8, 14), "orange": (4, 9, 10), "lav": (2, 13, 6),
    "dark": (1, 5, 6), "brown": (2, 4, 9), "pink": (2, 14, 15), "peach": (4, 15, 7),
    "navy": (1, 1, 13),
}


def _mask(op):
    m = Image.new("1", (16, 16), 0)
    d = ImageDraw.Draw(m)
    k = op[0]
    if k == "rect":
        x, y, w, h = op[1]
        d.rectangle((x, y, x + w - 1, y + h - 1), fill=1)
    elif k == "poly":
        d.polygon(op[1], fill=1, outline=1)
    elif k == "ell":
        x0, y0, x1, y1 = op[1]
        d.ellipse((x0, y0, x1, y1), fill=1)
    elif k == "line":
        pts, w = op[1]
        d.line(pts, fill=1, width=w)
    elif k == "px":
        for p in op[1]:
            d.point(p, fill=1)
    elif k == "ring":
        x0, y0, x1, y1, t = op[1]
        d.ellipse((x0, y0, x1, y1), fill=1)
        d.ellipse((x0 + t, y0 + t, x1 - t, y1 - t), fill=0)
    return m


def build(parts):
    """parts: [(op, ramp | palette index, flags)], flags: 'flat', 'cut', 'detail'."""
    pix = [[CLEAR] * 16 for _ in range(16)]
    solid = [[False] * 16 for _ in range(16)]
    for op, colour, *fl in parts:
        flags = fl[0] if fl else ""
        m = _mask(op)
        own = [[bool(m.getpixel((x, y))) for x in range(16)] for y in range(16)]
        if "cut" in flags:
            for y in range(16):
                for x in range(16):
                    if own[y][x]:
                        pix[y][x], solid[y][x] = CLEAR, False
            continue
        if "detail" in flags:
            for y in range(16):
                for x in range(16):
                    if own[y][x] and solid[y][x]:
                        pix[y][x] = colour
            continue
        shade, base, light = RAMPS[colour] if isinstance(colour, str) else (colour,) * 3
        for y in range(16):
            for x in range(16):
                if not own[y][x]:
                    continue
                c = base
                if "flat" not in flags:
                    up = y > 0 and own[y - 1][x]
                    lf = x > 0 and own[y][x - 1]
                    dn = y < 15 and own[y + 1][x]
                    rt = x < 15 and own[y][x + 1]
                    if not up or not lf:
                        c = light
                    if not dn or not rt:
                        c = shade
                    if not up and not rt and lf:
                        c = light
                pix[y][x] = c
                solid[y][x] = True
    out = [row[:] for row in pix]
    for y in range(16):
        for x in range(16):
            if solid[y][x]:
                continue
            if any(0 <= x + dx < 16 and 0 <= y + dy < 16 and solid[y + dy][x + dx]
                   for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                out[y][x] = OUTLINE
    return out


def hand(rows):
    """Rows of 16 hex digits, `.` the clear pixel."""
    return [[CLEAR if ch == "." else int(ch, 16) for ch in r.ljust(16, ".")[:16]] for r in rows]


MOY = hand([
    "................", "...0000000......", "..0ddddddd0.....", ".0d66ddddd0.....",
    ".0dddddddd0.....", ".0dddddddd0000..", ".0dd77d77ddddd0.", ".0dd70d70ddddd0.",
    ".0dddddddddddd0.", ".0dd0ddd0ddddd0.", ".0ddd000dddddd0.", ".0dddddddddddd0.",
    ".0dddddddddddd0.", "..022222222220..", "..02220002220...", "...000...000....",
])


def chev(pts):
    return [(("line", (pts, 3)), "dark", "flat")]


def arrow(shaft, head):
    return [(("line", (shaft, 3)), "dark", "flat"), (("line", (head, 3)), "dark", "flat")]


def cart(label, extra=()):
    return [(("poly", [(2, 1), (11, 1), (13, 3), (13, 14), (2, 14)]), "grey"),
            (("rect", (4, 3, 8, 7)), label),
            (("rect", (5, 11, 6, 2)), 5, "detail")] + list(extra)


TRASH = [(("rect", (3, 5, 10, 10)), "grey"), (("rect", (2, 3, 12, 2)), "grey"),
         (("rect", (6, 1, 4, 2)), "grey"),
         (("line", ([(5, 7), (5, 12)], 1)), 5, "detail"), (("line", ([(8, 7), (8, 12)], 1)), 5, "detail"),
         (("line", ([(11, 7), (11, 12)], 1)), 5, "detail")]
FOLDER = [(("poly", [(1, 3), (6, 3), (8, 5), (14, 5), (14, 13), (1, 13)]), "yellow"),
          (("line", ([(2, 6), (13, 6)], 1)), 9, "detail")]
PAPER = [(("poly", [(3, 1), (10, 1), (13, 4), (13, 14), (3, 14)]), "paper"),
         (("poly", [(10, 1), (10, 4), (13, 4)]), 6, "detail"),
         (("line", ([(5, 7), (11, 7)], 1)), 13, "detail"), (("line", ([(5, 9), (11, 9)], 1)), 13, "detail"),
         (("line", ([(5, 11), (9, 11)], 1)), 13, "detail")]
PLAY = [(("poly", [(4, 2), (13, 8), (4, 14)]), "green")]
STAR = [(7, 1), (8, 1), (10, 6), (14, 6), (11, 9), (12, 14), (8, 11), (7, 11), (3, 14),
        (4, 9), (1, 6), (5, 6)]

ICONS = {
    "moy": MOY,
    "home": [(("poly", [(1, 8), (8, 1), (14, 8)]), "red"), (("rect", (3, 8, 10, 6)), "paper"),
             (("rect", (7, 10, 3, 4)), 4, "detail"), (("rect", (11, 2, 2, 4)), "brown")],
    "wifi": [(("ring", (1, 3, 14, 16, 2)), "blue", "flat"), (("rect", (0, 9, 16, 7)), 0, "cut"),
             (("ring", (4, 6, 11, 13, 2)), "blue", "flat"), (("rect", (0, 9, 16, 7)), 0, "cut"),
             (("rect", (7, 11, 2, 2)), "blue", "flat")],
    "wifi_off": [(("ring", (1, 3, 14, 16, 2)), "grey", "flat"), (("rect", (0, 9, 16, 7)), 0, "cut"),
                 (("ring", (4, 6, 11, 13, 2)), "grey", "flat"), (("rect", (0, 9, 16, 7)), 0, "cut"),
                 (("rect", (7, 11, 2, 2)), "grey", "flat"), (("line", ([(2, 2), (13, 13)], 2)), "red", "flat")],
    "battery": [(("rect", (1, 4, 12, 8)), "dark"), (("rect", (13, 6, 2, 4)), "dark", "flat"),
                (("rect", (3, 6, 6, 4)), 11, "detail")],
    "search": [(("ring", (1, 1, 10, 10, 2)), "grey"), (("ell", (3, 3, 8, 8)), "blue"),
               (("line", ([(9, 9), (13, 13)], 3)), "brown", "flat")],
    "play": PLAY,
    "projects": [(("rect", (1, 3, 10, 11)), "paper"),
                 (("line", ([(4, 12), (13, 3)], 3)), "yellow", "flat"),
                 (("px", [(3, 13), (4, 13), (3, 12)]), 5, "detail"), (("rect", (12, 1, 3, 3)), "pink", "flat")],
    "paint": [(("line", ([(14, 1), (7, 8)], 3)), "brown", "flat"), (("rect", (6, 7, 3, 3)), "grey", "flat"),
              (("poly", [(2, 10), (6, 9), (7, 12), (4, 14), (1, 14)]), "red")],
    "storybook": [(("poly", [(1, 3), (7, 4), (7, 14), (1, 13)]), "paper"),
                  (("poly", [(8, 4), (14, 3), (14, 13), (8, 14)]), "paper"),
                  (("line", ([(2, 6), (5, 6)], 1)), 13, "detail"), (("line", ([(2, 8), (5, 8)], 1)), 13, "detail"),
                  (("line", ([(10, 6), (13, 6)], 1)), 13, "detail"),
                  (("line", ([(10, 8), (13, 8)], 1)), 13, "detail")],
    "files": FOLDER,
    "calc": [(("rect", (3, 1, 10, 14)), "grey"), (("rect", (5, 3, 6, 3)), 11, "detail"),
             (("px", [(5, 8), (8, 8), (11, 8), (5, 10), (8, 10), (11, 10), (5, 12), (8, 12)]), 5, "detail"),
             (("px", [(11, 12)]), 8, "detail")],
    "getcarts": [(("poly", [(6, 1), (10, 1), (10, 6), (13, 6), (8, 11), (3, 6), (6, 6)]), "green"),
                 (("rect", (1, 12, 14, 3)), "grey")],
    "settings": [(("ell", (2, 2, 13, 13)), "grey"), (("rect", (7, 0, 2, 16)), "grey", "flat"),
                 (("rect", (0, 7, 16, 2)), "grey", "flat"),
                 (("line", ([(2, 2), (13, 13)], 2)), "grey", "flat"),
                 (("line", ([(2, 13), (13, 2)], 2)), "grey", "flat"), (("ell", (4, 4, 11, 11)), "grey"),
                 (("ell", (6, 6, 9, 9)), 0, "cut")],
    "music": [(("ell", (1, 10, 5, 14)), "blue"), (("ell", (9, 8, 13, 12)), "blue"),
              (("rect", (4, 3, 2, 9)), "blue", "flat"), (("rect", (12, 1, 2, 9)), "blue", "flat"),
              (("poly", [(4, 2), (13, 0), (13, 3), (4, 5)]), "blue", "flat")],
    "notes": PAPER,
    "code": [(("line", ([(5, 3), (1, 8), (5, 13)], 2)), "blue", "flat"),
             (("line", ([(10, 3), (14, 8), (10, 13)], 2)), "blue", "flat"),
             (("line", ([(9, 2), (6, 14)], 2)), "grey", "flat")],
    "sprite": [(("rect", (1, 1, 14, 14)), "green"), (("rect", (4, 4, 2, 3)), 7, "detail"),
               (("rect", (10, 4, 2, 3)), 7, "detail"), (("px", [(5, 5), (11, 5)]), 1, "detail"),
               (("line", ([(4, 10), (5, 11), (10, 11), (11, 10)], 1)), 3, "detail")],
    "map": [(("rect", (1, 1, 14, 14)), "green"), (("rect", (2, 9, 12, 5)), 4, "detail"),
            (("rect", (2, 8, 12, 1)), 11, "detail"),
            (("px", [(4, 11), (9, 12), (12, 10)]), 2, "detail"),
            (("line", ([(5, 2), (5, 13)], 1)), 3, "detail"), (("line", ([(10, 2), (10, 13)], 1)), 3, "detail"),
            (("line", ([(2, 5), (13, 5)], 1)), 3, "detail")],
    "blocks": [(("rect", (1, 1, 6, 6)), "red"), (("rect", (9, 1, 6, 6)), "yellow"),
               (("rect", (1, 9, 6, 6)), "blue"), (("rect", (9, 9, 6, 6)), "green")],
    "scene": [(("rect", (1, 2, 14, 12)), "blue"), (("poly", [(2, 13), (6, 7), (9, 10), (11, 8), (14, 12), (14, 13)]), "green"),
              (("ell", (10, 3, 13, 6)), 10, "detail"), (("rect", (4, 9, 2, 3)), 8, "detail")],
    "undo": [(("line", ([(5, 6), (10, 6), (13, 9), (10, 12), (7, 12)], 2)), "blue", "flat"),
             (("poly", [(1, 6), (5, 2), (5, 10)]), "blue", "flat")],
    "run": PLAY,
    "save": [(("poly", [(1, 1), (12, 1), (14, 3), (14, 14), (1, 14)]), "blue"),
             (("rect", (4, 1, 7, 5)), "grey"), (("rect", (8, 2, 2, 3)), 5, "detail"),
             (("rect", (3, 8, 10, 6)), "paper")],
    "pencil": [(("line", ([(3, 12), (12, 3)], 4)), "yellow", "flat"),
               (("poly", [(1, 14), (2, 10), (5, 13)]), "peach", "flat"), (("px", [(1, 14), (2, 13)]), 1, "detail"),
               (("line", ([(11, 2), (13, 4)], 3)), "pink", "flat")],
    "bucket": [(("poly", [(6, 1), (13, 8), (8, 13), (1, 6)]), "grey"),
               (("line", ([(2, 7), (9, 7)], 1)), 5, "detail"),
               (("poly", [(13, 9), (15, 12), (14, 14), (12, 14), (11, 12)]), "blue", "flat")],
    "line": [(("line", ([(2, 13), (13, 2)], 2)), "dark", "flat")],
    "rect": [(("rect", (1, 3, 14, 10)), "dark", "flat"), (("rect", (3, 5, 10, 6)), 0, "cut")],
    "oval": [(("ring", (1, 3, 14, 12, 2)), "dark", "flat")],
    "select": [(("px", [(x, 2) for x in range(1, 15, 3)] + [(x, 13) for x in range(1, 15, 3)]
                + [(1, y) for y in range(2, 14, 3)] + [(14, y) for y in range(2, 14, 3)]), "dark", "flat")],
    "flip": [(("poly", [(7, 2), (7, 13), (1, 13)]), "blue"), (("poly", [(9, 2), (9, 13), (14, 13)]), "paper"),
             (("rect", (8, 0, 1, 16)), "dark", "flat")],
    "rotate": [(("ring", (2, 2, 13, 13, 2)), "blue", "flat"), (("rect", (8, 0, 8, 7)), 0, "cut"),
               (("poly", [(8, 0), (13, 4), (8, 7)]), "blue", "flat")],
    "game": [(("poly", [(3, 4), (12, 4), (14, 7), (14, 11), (12, 12), (10, 10), (5, 10), (3, 12), (1, 11), (1, 7)]), "grey"),
             (("px", [(4, 6), (3, 7), (4, 7), (5, 7), (4, 8)]), 1, "detail"),
             (("px", [(10, 6), (12, 8)]), 8, "detail"), (("px", [(11, 7)]), 12, "detail")],
    "app": [(("rect", (1, 1, 6, 6)), "blue"), (("rect", (9, 1, 6, 6)), "blue"),
            (("rect", (1, 9, 6, 6)), "blue"), (("rect", (9, 9, 6, 6)), "orange")],
    "art": [(("rect", (1, 2, 14, 12)), "brown"), (("rect", (3, 4, 10, 8)), 12, "detail"),
            (("poly", [(3, 11), (6, 7), (9, 11)]), 11, "detail"), (("poly", [(7, 11), (10, 8), (12, 11)]), 3, "detail"),
            (("px", [(10, 5), (11, 5)]), 10, "detail")],
    "wallpaper": [(("rect", (1, 1, 14, 10)), "dark"), (("rect", (3, 3, 10, 6)), 1, "detail"),
                  (("px", [(5, 4), (9, 5), (11, 3)]), 7, "detail"), (("rect", (3, 8, 10, 1)), 2, "detail"),
                  (("rect", (6, 11, 4, 2)), "dark", "flat"), (("rect", (4, 13, 8, 2)), "dark", "flat")],
    "doc": PAPER,
    "folder": FOLDER,
    "trash": TRASH,
    "close": [(("line", ([(3, 3), (12, 12)], 3)), "red", "flat"), (("line", ([(12, 3), (3, 12)], 3)), "red", "flat")],
    "back": chev([(10, 2), (4, 8), (10, 14)]),
    "forward": chev([(5, 2), (11, 8), (5, 14)]),
    "down": chev([(2, 5), (8, 11), (14, 5)]),
    "new": [(("rect", (6, 2, 4, 12)), "green"), (("rect", (2, 6, 12, 4)), "green")],
    "dup": [(("rect", (1, 1, 9, 10)), "paper"), (("rect", (6, 5, 9, 10)), "paper")],
    "del": TRASH[:3] + [(("line", ([(5, 7), (10, 12)], 1)), 8, "detail"), (("line", ([(10, 7), (5, 12)], 1)), 8, "detail")],
    "enter": [(("line", ([(13, 2), (13, 9), (5, 9)], 3)), "dark", "flat"),
              (("poly", [(1, 9), (6, 4), (6, 14)]), "dark", "flat")],
    "backspace": [(("poly", [(1, 8), (5, 3), (14, 3), (14, 13), (5, 13)]), "grey"),
                  (("line", ([(7, 6), (11, 10)], 1)), 1, "detail"), (("line", ([(11, 6), (7, 10)], 1)), 1, "detail")],
    "minimize": [(("rect", (2, 11, 12, 3)), "dark")],
    "maximize": [(("rect", (1, 2, 14, 12)), "paper"), (("rect", (2, 3, 12, 2)), 12, "detail")],
    "cart_py": cart("blue", [(("rect", (8, 6, 4, 4)), 10, "detail")]),
    "cart_lua": cart("navy", [(("ell", (5, 4, 9, 8)), 7, "detail"), (("ell", (7, 3, 10, 7)), 1, "detail")]),
    "cart_wasm": cart("lav", [(("rect", (6, 5, 4, 3)), 1, "detail")]),
    "cart_p8": cart("red", [(("px", [(5, 4), (7, 4), (9, 4), (6, 5), (8, 5), (10, 5), (5, 6), (7, 6), (9, 6)]), 7, "detail")]),
    "mute": [(("poly", [(1, 5), (3, 5), (7, 1), (7, 14), (3, 10), (1, 10)]), "grey"),
             (("line", ([(10, 5), (14, 9)], 1)), "red", "flat"),
             (("line", ([(14, 5), (10, 9)], 1)), "red", "flat")],
    "arrow_left": arrow([(13, 8), (3, 8)], [(8, 3), (3, 8), (8, 13)]),
    "arrow_right": arrow([(2, 8), (12, 8)], [(7, 3), (12, 8), (7, 13)]),
    "arrow_up": arrow([(8, 13), (8, 3)], [(3, 8), (8, 3), (13, 8)]),
    "arrow_down": arrow([(8, 2), (8, 12)], [(3, 7), (8, 12), (13, 7)]),
    "star": [(("poly", STAR), "grey")],
    "star_on": [(("poly", STAR), "yellow")],
    "eraser": [(("poly", [(1, 9), (7, 3), (12, 8), (6, 14)]), "pink"),
               (("poly", [(4, 6), (7, 3), (12, 8), (9, 11)]), "blue")],
    "mirror": [(("poly", [(1, 4), (6, 8), (1, 12)]), "blue"),
               (("poly", [(14, 4), (9, 8), (14, 12)]), "blue"),
               (("px", [(7, y) for y in (1, 2, 5, 6, 9, 10, 13, 14)]
                 + [(8, y) for y in (1, 2, 5, 6, 9, 10, 13, 14)]), "dark", "flat")],
    "flip_v": [(("poly", [(2, 7), (13, 7), (13, 1)]), "blue"),
               (("poly", [(2, 9), (13, 9), (13, 14)]), "paper"),
               (("rect", (0, 8, 16, 1)), "dark", "flat")],
    "folder_open": [(("poly", [(1, 3), (6, 3), (8, 5), (13, 5), (13, 7), (1, 7)]), "yellow"),
                    (("poly", [(1, 13), (4, 7), (15, 7), (12, 13)]), "yellow")],
    "more": [(("rect", (1, 7, 3, 3)), "dark", "flat"), (("rect", (6, 7, 3, 3)), "dark", "flat"),
             (("rect", (11, 7, 3, 3)), "dark", "flat")],
    "stamp": [(("ell", (5, 1, 10, 5)), "brown"), (("rect", (6, 6, 4, 3)), "brown"),
              (("rect", (2, 9, 12, 3)), "red"), (("rect", (2, 13, 12, 1)), "dark", "flat")],
}


def icon(slot):
    if slot == "redo":
        return [row[::-1] for row in icon("undo")]
    spec = ICONS[slot]
    return spec if isinstance(spec[0], list) else build(spec)


def image(pix, pal, scale=1):
    im = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for y, row in enumerate(pix):
        for x, c in enumerate(row):
            if c != CLEAR:
                im.putpixel((x, y), pal[c] + (255,))
    return im.resize((16 * scale, 16 * scale), Image.NEAREST) if scale != 1 else im


def moygfx(slots, cols=8):
    """The IconSheet's .moygfx: a `clear 16` header, then h lines of w hex
    nibbles, `.` the clear pixel, 16x16 tiles, row-major."""
    rows = -(-len(slots) // cols)
    w, h = cols * 16, rows * 16
    grid = [[CLEAR] * w for _ in range(h)]
    for n, slot in enumerate(slots):
        tx, ty = (n % cols) * 16, (n // cols) * 16
        for y, row in enumerate(icon(slot)):
            for x, c in enumerate(row):
                grid[ty + y][tx + x] = c
    body = "\n".join("".join("." if c == CLEAR else "%x" % c for c in row) for row in grid)
    return "clear %d\n" % CLEAR + body + "\n", cols, rows
