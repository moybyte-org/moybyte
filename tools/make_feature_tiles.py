#!/usr/bin/env python3
"""Draw the "What's in it" tiles: one-colour animated pixel art, one per feature.

Each tile is 64x48 pixels, 10 frames a second, and is written twice:

  <name>.png  a transparent mask sheet, frames side by side at 4x. The site
              colours it with CSS (so it follows the page's theme) and steps
              through it with an animation; site/build.py reads the frame count
              from the sheet's width.
  <name>.gif  the same frames at 3x, yellow on dark, for the README.

The outputs are committed because the Pages job has no Pillow.

    python tools/make_feature_tiles.py                 # all -> docs/media/features/
    python tools/make_feature_tiles.py --only updates
"""

import argparse
import math
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 64, 48
SHEET_SCALE = 4
GIF_SCALE = 3
FRAME_MS = 100
GIF_INK = (255, 236, 39)
GIF_GROUND = (13, 17, 32)


class Bmp:
    def __init__(self):
        self.p = [bytearray(W) for _ in range(H)]

    def copy(self):
        b = Bmp()
        b.p = [bytearray(r) for r in self.p]
        return b

    def px(self, x, y, v=1):
        x, y = int(round(x)), int(round(y))
        if 0 <= x < W and 0 <= y < H:
            self.p[y][x] = v

    def fill(self, x, y, w, h, v=1):
        x, y, w, h = (int(round(n)) for n in (x, y, w, h))
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.px(xx, yy, v)

    def rect(self, x, y, w, h, v=1):
        x, y, w, h = (int(round(n)) for n in (x, y, w, h))
        for xx in range(x, x + w):
            self.px(xx, y, v)
            self.px(xx, y + h - 1, v)
        for yy in range(y, y + h):
            self.px(x, yy, v)
            self.px(x + w - 1, yy, v)

    def line(self, x0, y0, x1, y1, v=1):
        x0, y0, x1, y1 = (int(round(n)) for n in (x0, y0, x1, y1))
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            self.px(x0, y0, v)
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def arc(self, cx, cy, r, a0, a1, v=1):
        """Degrees, 0 = right, 90 = up."""
        steps = max(8, int(r * 8))
        for i in range(steps + 1):
            a = math.radians(a0 + (a1 - a0) * i / steps)
            self.px(cx + r * math.cos(a), cy - r * math.sin(a), v)

    def art(self, rows, x, y):
        """`#` sets a pixel, `o` clears one, anything else leaves it."""
        for j, row in enumerate(rows):
            for i, ch in enumerate(row):
                if ch == "#":
                    self.px(x + i, y + j, 1)
                elif ch == "o":
                    self.px(x + i, y + j, 0)

    def dissolve(self, frac, rng):
        for row in self.p:
            for i, v in enumerate(row):
                if v and rng.random() < frac:
                    row[i] = 0
        return self


def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def lerp(a, b, t):
    return a + (b - a) * t


def hold(frames, n):
    frames.extend([frames[-1]] * n)


# --- windows: one window splits into three, they take turns in front ---------

def window(b, x, y, w, h):
    b.fill(x, y, w, h, 0)
    b.rect(x, y, w, h)
    b.fill(x, y, w, 3)
    b.px(round(x) + w - 3, round(y) + 1, 0)


def tile_windows():
    frames = []
    ww, wh = 30, 22
    home = (17, 13)
    spots = [(5, 5), (17, 13), (29, 21)]

    def draw(pos, order):
        b = Bmp()
        for i in order:
            window(b, pos[i][0], pos[i][1], ww, wh)
        return b

    frames.append(draw([home] * 3, [0, 1, 2]))
    hold(frames, 5)
    for k in range(8):
        t = ease((k + 1) / 8)
        frames.append(draw([(lerp(home[0], s[0], t), lerp(home[1], s[1], t))
                            for s in spots], [0, 1, 2]))
    for order in ([1, 2, 0], [2, 0, 1], [0, 1, 2]):
        frames.append(draw(spots, order))
        hold(frames, 4)
    for k in range(8):
        t = ease((k + 1) / 8)
        frames.append(draw([(lerp(s[0], home[0], t), lerp(s[1], home[1], t))
                            for s in spots], [0, 1, 2]))
    return frames


# --- editors: code types, then a sprite paints, then notes land ---------------

HEART = [".##.##.", "#######", "#######", "#######", ".#####.", "..###..", "...#..."]
CODE_LINES = [(0, 21), (4, 15), (4, 18), (8, 9), (0, 12)]


def editor_frame(b, tab):
    for i in range(3):
        x = 14 + i * 12
        if i == tab:
            b.fill(x, 4, 12, 6)
        else:
            b.rect(x, 5, 12, 5)
    b.rect(8, 10, 48, 34)


def tile_editors():
    frames = []
    total = sum(n for _, n in CODE_LINES)
    for k in range(18):
        b = Bmp()
        editor_frame(b, 0)
        typed = int(total * min(1.0, k / 14))
        left = typed
        cx, cy = 12, 14
        for row, (indent, n) in enumerate(CODE_LINES):
            y = 14 + row * 6
            shown = max(0, min(n, left))
            left -= n
            for i in range(shown):
                if i % 4 != 3:
                    b.px(12 + indent + i, y)
                    b.px(12 + indent + i, y + 1)
            if 0 < shown < n or (shown == n and left <= 0 and shown):
                cx, cy = 12 + indent + shown + 1, y
        if k % 4 < 2:
            b.fill(cx, cy - 1, 1, 4)
        frames.append(b)
    cells = [(c, r) for r in range(7) for c in range(7) if HEART[r][c] == "#"]
    for k in range(16):
        b = Bmp()
        editor_frame(b, 1)
        for r in range(8):
            for c in range(8):
                b.px(19 + c * 4, 13 + r * 4)
        for c, r in cells[:min(len(cells), k * 3)]:
            b.fill(20 + c * 4, 14 + r * 4, 3, 3)
        frames.append(b)
    notes = [(14, 3), (21, 1), (28, 2), (35, 0), (42, 2), (49, 1)]
    for k in range(16):
        b = Bmp()
        editor_frame(b, 2)
        for i in range(5):
            b.fill(11, 18 + i * 4, 42, 1)
        for i, (x, step) in enumerate(notes):
            age = k - i * 2
            if age < 0:
                continue
            y = 30 - step * 4 - (2 if age == 0 else 0)
            b.fill(x, y, 4, 3, 0)
            b.fill(x, y, 4, 3)
            b.fill(x + 3, y - 7, 1, 7)
        frames.append(b)
    return frames


# --- apps: one outline turns from a page into a calculator into a canvas ------

APP_SHAPES = {"page": (24, 32), "calc": (24, 32), "paint": (38, 28)}


def app_content(b, shape, k, x, y, w, h):
    if shape == "page":
        b.fill(x + w - 6, y, 6, 1, 0)
        b.line(x + w - 6, y, x + w - 1, y + 5)
        b.line(x + w - 6, y, x + w - 6, y + 5)
        b.line(x + w - 6, y + 5, x + w - 1, y + 5)
        for i in range(min(5, k)):
            b.fill(x + 4, y + 9 + i * 4, (14, 16, 11, 16, 8)[i], 1)
    elif shape == "calc":
        b.fill(x + 4, y + 4, w - 8, 6)
        for i in range(min(9, k * 2)):
            c, r = i % 3, i // 3
            kx, ky = x + 4 + c * 6, y + 13 + r * 6
            if i == (k // 2) % 9 and k > 5:
                b.rect(kx, ky, 4, 4)
            else:
                b.fill(kx, ky, 4, 4)
    else:
        pts = [(x + 4 + i, y + h // 2 + 7 * math.sin(i / 5.0)) for i in range(w - 8)]
        shown = pts[:int(len(pts) * min(1.0, k / 10))]
        for px_, py_ in shown:
            b.fill(px_, py_, 2, 2)
        if shown and k <= 10:
            b.fill(shown[-1][0] - 1, shown[-1][1] - 1, 4, 4)


def tile_apps():
    frames = []
    order = ["page", "calc", "paint"]
    holds = {"page": 10, "calc": 12, "paint": 14}
    for n, shape in enumerate(order):
        w, h = APP_SHAPES[shape]
        for k in range(holds[shape]):
            b = Bmp()
            x, y = 32 - w // 2, 24 - h // 2
            b.rect(x, y, w, h)
            app_content(b, shape, k, x, y, w, h)
            frames.append(b)
        nw, nh = APP_SHAPES[order[(n + 1) % 3]]
        for k in range(5):
            t = ease((k + 1) / 5)
            cw, ch = int(round(lerp(w, nw, t))), int(round(lerp(h, nh, t)))
            b = Bmp()
            b.rect(32 - cw // 2, 24 - ch // 2, cw, ch)
            frames.append(b)
    return frames


# --- python and lua: a snake and a moon meet and make one star ----------------

MOON = [
    "...#####.", ".####....", ".###.....", "####.....", "###......",
    "###......", "####.....", ".###.....", ".####....", "...#####.",
]
STAR = [
    ".....#.....", ".....#.....", "....###....", "###########", ".#########.",
    "..#######..", "..###.###..", ".##.....##.", ".#.......#.",
]


def snake(b, x, y):
    for i in range(16):
        b.fill(x + i, y + 3 + round(2.5 * math.sin(i * 0.62)), 1, 2)
    hy = y + 3 + round(2.5 * math.sin(16 * 0.62))
    b.fill(x + 16, hy - 1, 3, 3)
    b.px(x + 19, hy)
    b.px(x + 20, hy - 1)


def tile_languages():
    frames = []
    cy = 19
    for k in range(10):
        t = ease(k / 9)
        b = Bmp()
        snake(b, lerp(1, 12, t), cy + 1)
        b.art(MOON, int(lerp(50, 36, t)), cy)
        frames.append(b)
    for k in range(3):
        b = Bmp()
        b.arc(32, 24, 3 + k * 3, 0, 360)
        frames.append(b)
    for k in range(14):
        b = Bmp()
        b.art(STAR, 27, cy + 1)
        if k < 6:
            for r in (8 + k, 12 + k):
                for a in range(0, 360, 45):
                    b.px(32 + r * math.cos(math.radians(a)),
                         24 - r * math.sin(math.radians(a)))
        frames.append(b)
    for k in range(8):
        t = ease(k / 7)
        b = Bmp()
        snake(b, lerp(12, 1, t), cy + 1)
        b.art(MOON, int(lerp(36, 50, t)), cy)
        frames.append(b)
    return frames


# --- multiplayer: one ball crosses between two consoles, through the air ------

def console(b, x, y):
    b.rect(x, y, 26, 32)
    b.rect(x + 3, y + 3, 20, 16)
    b.fill(x + 5, y + 23, 2, 6)
    b.fill(x + 3, y + 25, 6, 2)
    b.px(x + 18, y + 24)
    b.px(x + 21, y + 26)


def tile_multiplayer():
    frames = []
    lx, rx, cy = 4, 34, 10
    n = 40
    for k in range(n):
        b = Bmp()
        console(b, lx, cy)
        console(b, rx, cy)
        u = (k / n) * 2
        u = u if u <= 1 else 2 - u
        tx = u * 37
        ty = 7 + 6 * math.sin(k / n * 2 * math.pi * 3)
        pl = round(lerp(4, 11, 0.5 + 0.5 * math.sin(k / n * 2 * math.pi * 2)))
        pr = round(lerp(4, 11, 0.5 + 0.5 * math.cos(k / n * 2 * math.pi * 2 + 1)))
        b.fill(lx + 5, cy + 1 + pl, 1, 5)
        b.fill(rx + 20, cy + 1 + pr, 1, 5)
        if tx < 18.5:
            b.fill(int(lx + 4 + tx), int(cy + 4 + ty), 2, 2)
        else:
            b.fill(int(rx + 4 + tx - 19), int(cy + 4 + ty), 2, 2)
        if abs(tx - 18.5) < 5:
            for r in (2, 4):
                b.arc(32, 8, r, 20, 160)
        frames.append(b)
    return frames


# --- pico-8: a cart lands, comes apart into its pieces, which become a folder -

def p8_cart(b, x, y):
    b.rect(x, y, 20, 24)
    b.fill(x + 16, y, 4, 1, 0)
    b.line(x + 15, y, x + 19, y + 4)
    b.fill(x + 19, y, 1, 4, 0)
    b.rect(x + 3, y + 3, 12, 10)
    b.art(["###.###", "#.#.#.#", "###.###", "#...#.#", "#...###"], x + 6, y + 5)
    for i in range(3):
        b.fill(x + 4 + i * 5, y + 18, 3, 3)


def piece(b, kind, x, y):
    if kind == "sprite":
        for r in range(4):
            for c in range(4):
                if (r + c) % 2 == 0:
                    b.fill(x + c * 2, y + r * 2, 2, 2)
    elif kind == "map":
        b.rect(x, y, 8, 8)
        b.fill(x + 2, y + 4, 4, 2)
        b.fill(x + 4, y + 2, 2, 2)
    elif kind == "sound":
        b.fill(x, y + 5, 3, 3)
        b.fill(x + 2, y, 1, 6)
        b.fill(x + 3, y, 3, 1)
        b.fill(x + 5, y + 1, 1, 2)
    else:
        for i, n in enumerate((8, 5, 7)):
            b.fill(x, y + i * 3, n, 1)


def folder(b, x, y, w=24, h=17):
    b.rect(x, y + 3, w, h - 3)
    b.fill(x, y, 9, 1)
    b.fill(x, y, 1, 3)
    b.px(x + 9, y + 1)
    b.px(x + 10, y + 2)


def tile_pico8():
    frames = []
    rng = random.Random(8)
    cx, cy = 22, 12
    for k in range(7):
        b = Bmp()
        p8_cart(b, cx, lerp(-26, cy, (k / 6) ** 2))
        frames.append(b)
    b = Bmp()
    p8_cart(b, cx, cy + 1)
    frames.append(b)
    b = Bmp()
    p8_cart(b, cx, cy)
    frames.append(b)
    hold(frames, 4)
    kinds = ["sprite", "map", "sound", "code"]
    corners = [(8, 6), (48, 6), (8, 34), (48, 34)]
    center = (28, 20)
    for k in range(7):
        t = ease((k + 1) / 7)
        b = Bmp()
        p8_cart(b, cx, cy)
        b.dissolve(t, rng)
        for kind, (ex, ey) in zip(kinds, corners):
            piece(b, kind, lerp(center[0], ex, t), lerp(center[1], ey, t))
        frames.append(b)
    hold(frames, 5)
    for k in range(7):
        t = ease((k + 1) / 7)
        b = Bmp()
        folder(b, 20, 15)
        b.dissolve(1 - t, rng)
        for kind, (sx, sy) in zip(kinds, corners):
            if t < 0.95:
                piece(b, kind, lerp(sx, 28, t), lerp(sy, 22, t))
        frames.append(b)
    b = Bmp()
    folder(b, 20, 15)
    frames.append(b)
    hold(frames, 9)
    for k in range(3):
        frames.append(frames[-1].copy().dissolve(0.4, rng))
    return frames


# --- carts are folders: a folder drops into a slot, the shelf gains a cart ----

def shelf_cart(b, x, y, grow=1.0):
    w, h = max(1, round(10 * grow)), max(1, round(12 * grow))
    x, y = x + (10 - w) // 2, y + (12 - h) // 2
    b.rect(x, y, w, h)
    if grow >= 1:
        b.fill(x + 2, y + 2, 6, 4)
        b.fill(x + 2, y + 8, 3, 1)


def tile_folders():
    frames = []
    rng = random.Random(4)
    slots = [8, 21, 34, 47]

    def base(filled, grow=1.0):
        b = Bmp()
        for i, x in enumerate(slots):
            if i < filled:
                shelf_cart(b, x, 5)
            elif i == filled and grow > 0:
                shelf_cart(b, x, 5, grow)
            else:
                for xx in range(x, x + 10, 2):
                    b.px(xx, 16)
        return b

    def slot(b):
        for y in range(41, H):
            b.p[y] = bytearray(W)
        b.fill(10, 41, 14, 1)
        b.fill(40, 41, 14, 1)
        b.fill(10, 41, 1, 4)
        b.fill(53, 41, 1, 4)

    for k in range(6):
        b = base(3, 0)
        folder(b, 20, 21)
        slot(b)
        frames.append(b)
    for k in range(10):
        b = base(3, 0)
        folder(b, 20, lerp(21, 44, ease((k + 1) / 10)))
        slot(b)
        frames.append(b)
    for grow in (0.3, 0.6, 1.0):
        b = base(3, grow)
        slot(b)
        frames.append(b)
    b = base(4)
    slot(b)
    for a in range(0, 360, 60):
        b.px(52 + 9 * math.cos(math.radians(a)), 11 - 9 * math.sin(math.radians(a)))
    frames.append(b)
    b = base(4)
    slot(b)
    frames.append(b)
    hold(frames, 12)
    for k in range(3):
        b = base(3, 0)
        shelf_cart(b, slots[3], 5)
        piece_ = b.copy()
        for y in range(H):
            for x in range(W):
                if x >= slots[3] and piece_.p[y][x] and rng.random() < (k + 1) / 3:
                    b.p[y][x] = 0
        slot(b)
        frames.append(b)
    return frames


# --- updates: signal fills a chip, the mark changes, a glitch rewinds it -------

MARK_OLD = ["######"] * 6
MARK_NEW = ["..##..", ".####.", "######", "######", ".####.", "..##.."]
REWIND = ["..#..#", ".##.##", "######", ".##.##", "..#..#"]


def chip(b, mark, x=22, y=18):
    b.rect(x, y, 20, 18)
    for i in range(3):
        b.fill(x - 3, y + 4 + i * 5, 2, 1)
        b.fill(x + 21, y + 4 + i * 5, 2, 1)
    b.art(mark, x + 7, y + 6)


def tile_updates():
    rng = random.Random(3)
    fwd = []
    total = 14
    for k in range(total):
        b = Bmp()
        lit = k % 4
        for r in range(1, 4):
            if r <= lit or k == total - 1:
                b.arc(32, 14, r * 4, 35, 145)
        b.rect(20, 41, 24, 3)
        b.fill(20, 41, int(24 * (k + 1) / total), 3)
        chip(b, MARK_OLD)
        fwd.append(b)
    for k in range(4):
        b = Bmp()
        b.fill(20, 41, 24, 3)
        chip(b, MARK_OLD if k < 2 else MARK_NEW)
        for _ in range(10 - k * 3):
            b.px(29 + rng.randrange(6), 24 + rng.randrange(6), rng.randrange(2))
        fwd.append(b)
    for _ in range(6):
        b = Bmp()
        chip(b, MARK_NEW)
        fwd.append(b)
    glitch = []
    for k in range(5):
        b = fwd[-1].copy()
        for y in range(H):
            if rng.random() < 0.35:
                s = rng.choice((-3, -2, 2, 3))
                b.p[y] = bytearray(b.p[y][-s:] + b.p[y][:-s])
        for _ in range(12):
            b.px(rng.randrange(W), rng.randrange(H))
        glitch.append(b)
    rewind = []
    for f in fwd[::-2]:
        b = f.copy()
        b.art(REWIND, 3, 3)
        rewind.append(b)
    return fwd + glitch + rewind + [fwd[0]] * 4


# --- in the browser: a board's screen lifts off into a browser window ---------

def browser(b, x, y, w, h):
    b.rect(x, y + 3, w, h - 3)
    b.fill(x, y, 11, 4)
    b.fill(x, y + 3, w, 1)
    b.rect(x + 3, y + 6, w - 6, 3)
    b.fill(x + w - 4, y + 7, 1, 1, 0)


def tile_browser():
    frames = []
    rng = random.Random(9)
    cons = (4, 12, 22, 28)
    small = (7, 15, 16, 12)
    big = (33, 17, 24, 20)

    def ball(k, rx, ry, rw, rh):
        u = (k % 16) / 16.0
        bx = rx + 1 + (rw - 4) * (1 - abs(1 - 2 * u))
        v = (k % 10) / 10.0
        by = ry + 1 + (rh - 4) * (1 - abs(1 - 2 * v))
        return bx, by

    def base(k):
        b = Bmp()
        x, y, w, h = cons
        b.rect(x, y, w, h)
        b.rect(*small)
        b.fill(x + 4, y + 21, 5, 1)
        b.fill(x + 6, y + 19, 1, 5)
        b.px(x + 15, y + 20)
        b.px(x + 17, y + 22)
        bx, by = ball(k, *small)
        b.fill(bx, by, 2, 2)
        return b

    k = 0
    for _ in range(8):
        frames.append(base(k))
        k += 1
    for i in range(10):
        t = ease((i + 1) / 10)
        b = base(k)
        r = [lerp(small[j], big[j], t) for j in range(4)]
        b.rect(*r)
        bx, by = ball(k, *r)
        b.fill(bx, by, 2, 2)
        if i >= 5:
            win = Bmp()
            browser(win, 30, 6, 30, 36)
            win.dissolve(1 - (i - 4) / 5, rng)
            for y in range(H):
                for x in range(W):
                    if win.p[y][x]:
                        b.p[y][x] = 1
        frames.append(b)
        k += 1
    for _ in range(18):
        b = base(k)
        browser(b, 30, 6, 30, 36)
        b.rect(*big)
        bx, by = ball(k, *big)
        b.fill(bx, by, 3, 3)
        frames.append(b)
        k += 1
    for i in range(4):
        b = base(k)
        win = Bmp()
        browser(win, 30, 6, 30, 36)
        win.rect(*big)
        win.dissolve((i + 1) / 4, rng)
        for y in range(H):
            for x in range(W):
                if win.p[y][x] and x >= 29:
                    b.p[y][x] = 1
        frames.append(b)
        k += 1
    return frames


TILES = {
    "windows": tile_windows,
    "editors": tile_editors,
    "apps": tile_apps,
    "languages": tile_languages,
    "multiplayer": tile_multiplayer,
    "pico8": tile_pico8,
    "folders": tile_folders,
    "updates": tile_updates,
    "browser": tile_browser,
}


def save(name, frames, out):
    from PIL import Image

    n = len(frames)
    sw, sh = W * SHEET_SCALE, H * SHEET_SCALE
    sheet = Image.new("LA", (sw * n, sh), (0, 0))
    gif = []
    for i, f in enumerate(frames):
        flat = [v for row in f.p for v in row]
        mask = Image.new("LA", (W, H))
        mask.putdata([(255, 255) if v else (0, 0) for v in flat])
        sheet.paste(mask.resize((sw, sh), Image.NEAREST), (i * sw, 0))
        im = Image.new("P", (W, H))
        im.putpalette(list(GIF_GROUND) + list(GIF_INK) + [0] * 762)
        im.putdata(flat)
        gif.append(im.resize((W * GIF_SCALE, H * GIF_SCALE), Image.NEAREST))
    sheet.save(os.path.join(out, name + ".png"), optimize=True)
    gif[0].save(os.path.join(out, name + ".gif"), save_all=True,
                append_images=gif[1:], duration=FRAME_MS, loop=0, optimize=False)
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "media", "features"))
    ap.add_argument("--only", choices=sorted(TILES), help="draw one tile")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    for name, draw in TILES.items():
        if args.only and name != args.only:
            continue
        print("%-12s %d frames" % (name, save(name, draw(), args.out)))


if __name__ == "__main__":
    main()
