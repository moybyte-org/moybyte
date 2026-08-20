#!/usr/bin/env python3
"""A procedurally drawn cherry-blossom backdrop -- NOT the one the carts ship.

READ THIS FIRST: this script is not the source of
`system_carts/sakura*.moy/images/bg.moyimg`. It generated a *candidate*
backdrop, which the project owner then replaced with a supplied image; the
shipped bitmap and the carts' `EMIT` tables now come from
`tools/import_sakura_bg.py`. Running this script OVERWRITES both carts' bg with
this scene instead, so don't run it unless that is what you want. It is kept in
the tree because a fixed-seed generator is a useful fallback and a working
reference for drawing directly in MOY64 indices.

What it draws: a dawn sky over still water, a far shore of misty blossom trees,
and one big cherry tree leaning in from the bottom-left whose canopy fills the
top of the frame. A sun sits in an open window of sky on the right; the water
gives the canopy back as a soft blush. Nothing is traced, filtered or sampled
from any other picture -- every pixel comes out of the drawing code below, from
a fixed seed, so the result is reproducible.

Run from the repo root:

    .venv/bin/python tools/make_sakura_bg.py --dry-run --png /tmp/sakura.png
    .venv/bin/python tools/make_sakura_bg.py --emit     # print the EMIT tables
    .venv/bin/python tools/make_sakura_bg.py            # OVERWRITE both carts' bg

The canopy DEFINES the `EMIT` table that would go with this scene -- the
petal-shedding points must sit on real blossom clusters -- so `--emit` prints
the Python and Lua literals for main.py / main.lua (they must stay identical:
tests/test_lua_sakura_parity.py compares the two runtimes bit-for-bit). Adopting
this scene means taking its art AND its table together.

If you do adopt it, bump `"version"` in BOTH carts' manifest.json (#47) or an
already-seeded device keeps the old art, and refresh sakura_lua's cover with
`tools/gen_covers.py sakura_lua`.

Stdlib only, apart from the repo's own MOY64 palette table (used to pick the
ramps' true colours for the PNG preview).
"""

import argparse
import json
import math
import os
import random
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from runtime.palette import MOY64  # noqa: E402  (preview colours only)
from tools import pngwrite  # noqa: E402

W, H = 320, 240
SEED = 20260729

# --- scene geometry ----------------------------------------------------------
HORIZON = 156         # far shore waterline
WATER_BOT = 194       # near shore
SUN = (259, 86, 13)   # x, y, radius of the disc
SUN_CLEAR = 33        # blossom clumps keep this far from the sun's centre

# --- palette ramps (dark -> light), all MOY64 indices -------------------------
# Dawn sky: a hue sweep down the pastel family, periwinkle -> violet -> orchid
# -> rose -> peach, with the warm glow (15) reserved for the horizon.
SKY = (22, 23, 24, 25, 26, 16, 17, 18, 15)
GLOW = (18, 15, 7)                       # the sun's halo -> its core
CANOPY = (62, 13, 24, 25, 26, 15, 7)     # plum -> mauve -> orchid -> rose -> peach -> white
CANOPY_POP = 14                          # a few saturated blossoms for sparkle
FAR_TREES = (61, 62, 13, 24, 23)         # aerial perspective: the far shore's trees
BARK = (55, 56, 33, 28, 29, 31)          # cherry bark: dark, with one lit edge
WATER = (60, 51, 13, 22, 23, 21, 48)     # deep -> pale, the cool half of the pond
WATER_ROSE = (61, 62, 13, 24, 25, 26, 52)  # matched rungs, blushed by the canopy
GLINT = (18, 15, 7)                      # the sun's path across the ripples
GRASS = (30, 32, 35, 19)                 # dark olive -> meadow green -> khaki -> mint

# 8x8 ordered-dither matrix: every gradient in here is two ramp entries mixed by
# this threshold, which is what keeps 64 colours looking like a smooth sky.
_BAYER = (
    0, 32, 8, 40, 2, 34, 10, 42,
    48, 16, 56, 24, 50, 18, 58, 26,
    12, 44, 4, 36, 14, 46, 6, 38,
    60, 28, 52, 20, 62, 30, 54, 22,
    3, 35, 11, 43, 1, 33, 9, 41,
    51, 19, 59, 27, 49, 17, 57, 25,
    15, 47, 7, 39, 13, 45, 5, 37,
    63, 31, 55, 23, 61, 29, 53, 21,
)


def _thr(x, y):
    return (_BAYER[(y & 7) * 8 + (x & 7)] + 0.5) / 64.0


def _clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def _pick(ramp, t, x, y):
    """Ramp entry for `t` in 0..1, dithered between the two nearest steps."""
    f = _clamp(t) * (len(ramp) - 1)
    i = int(f)
    if i >= len(ramp) - 1:
        return ramp[-1]
    return ramp[i + 1] if (f - i) > _thr(x, y) else ramp[i]


def _hash01(x, y, salt=0):
    """Cheap deterministic per-pixel noise in 0..1 (no RNG state, so painting
    order never changes the texture)."""
    n = (x * 374761393 + y * 668265263 + salt * 1274126177) & 0x7FFFFFFF
    n = (n ^ (n >> 13)) * 1274126177 & 0x7FFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFF) / 65535.0


class Scene:
    def __init__(self):
        self.buf = bytearray(W * H)
        self.rng = random.Random(SEED)
        self.spans = []       # limb cross-sections, kept for the re-stroke pass

    def put(self, x, y, idx):
        if 0 <= x < W and 0 <= y < H:
            self.buf[y * W + x] = idx

    def get(self, x, y):
        return self.buf[y * W + x]

    # -- sky ---------------------------------------------------------------
    def sky(self):
        sx, sy, sr = SUN
        for y in range(HORIZON + 2):
            base = (y / float(HORIZON)) ** 1.25
            for x in range(W):
                t = base
                dx = (x - sx) * 0.85
                dy = y - sy
                d = math.sqrt(dx * dx + dy * dy)
                if d < 120.0:                       # warm bloom around the sun
                    t += (1.0 - d / 120.0) ** 2 * 0.55
                self.put(x, y, _pick(SKY, t, x, y))
        # the disc itself, with a tight halo
        for y in range(sy - sr - 8, sy + sr + 9):
            for x in range(sx - sr - 10, sx + sr + 11):
                dx, dy = x - sx, y - sy
                d = math.sqrt(dx * dx + dy * dy)
                if d > sr + 8:
                    continue
                if d <= sr - 1:
                    self.put(x, y, GLOW[2])
                else:
                    t = 1.0 - (d - sr + 1) / 9.0
                    if t > _thr(x, y) * 0.9:
                        self.put(x, y, _pick(GLOW, t, x, y))

    # -- far shore ---------------------------------------------------------
    def far_shore(self):
        """A low ridge of hazy blossom trees behind the water -- the same tree
        idea at a twelfth of the scale, washed toward the sky and kept in the
        dark half of the ramp so it reads as a distance, not a subject."""
        rng = self.rng
        crowns = []
        x = -16
        while x < W + 16:
            r = rng.uniform(7.0, 15.0)
            crowns.append((x, HORIZON - 5 - r * rng.uniform(0.45, 0.95), r))
            x += rng.uniform(5.0, 11.0)
        for cx, cy, r in crowns:
            for y in range(int(cy - r), HORIZON):
                for x in range(int(cx - r - 1), int(cx + r + 2)):
                    if not (0 <= x < W and 0 <= y < HORIZON):
                        continue
                    dx = (x - cx) / r
                    dy = (y - cy) / (r * 0.80)
                    d = math.sqrt(dx * dx + dy * dy)
                    if d > 1.0 and y < HORIZON - 4:
                        continue
                    if d > 0.70 and _hash01(x, y, 3) > (1.05 - d) * 2.4:
                        continue
                    t = 0.14 + 0.34 * (0.4 - dy) + _hash01(x // 2, y // 2, 4) * 0.22
                    self.put(x, y, _pick(FAR_TREES, t, x, y))
        # the shore itself: a thin band of shadow under the trees
        for x in range(W):
            wob = int(2.0 + 1.5 * math.sin(x * 0.11) + math.sin(x * 0.37))
            for y in range(HORIZON - 3, HORIZON + wob):
                self.put(x, y, _pick((61, 62, 13), _hash01(x, y, 5) * 0.8, x, y))

    # -- water -------------------------------------------------------------
    def water(self, mirror):
        """Still water. A literal fold of the scene reads as noise at this size,
        so the pond is painted instead: a vertical brightness ramp, a per-column
        BLUSH wherever the canopy stands above it (the two water ramps are the
        same rungs, one plain and one rose, so the blend is a plain dither), and
        the sun's glitter path broken into ripples. `mirror` is the buffer
        snapshot taken before the foreground went down."""
        rng = self.rng
        blossom = set(CANOPY) | {CANOPY_POP}
        blush = []
        for x in range(W):
            hit = 0
            for y in range(HORIZON - 1, -1, -1):
                if mirror[y * W + x] in blossom:
                    hit += 1
            blush.append(_clamp(hit / 90.0))
        smooth = [sum(blush[max(0, x - 5):x + 6]) / len(blush[max(0, x - 5):x + 6])
                  for x in range(W)]
        sx = SUN[0]
        span = float(WATER_BOT - HORIZON)
        for y in range(HORIZON, WATER_BOT + 1):
            depth = (y - HORIZON) / span
            t = 0.94 - depth * 0.58
            ripple = 0.05 * math.sin(y * 0.9) + 0.04 * math.sin(y * 2.3 + 1.1)
            for x in range(W):
                lvl = t + ripple + _hash01(x // 3, y, 6) * 0.05
                rose = smooth[x] * 1.9 * (1.0 - depth * 0.75)
                ramp = WATER_ROSE if _thr(x + 3, y + 1) < rose else WATER
                self.put(x, y, _pick(ramp, lvl, x, y))
            # the sun's path: a widening column of broken glitter
            half = 3.0 + depth * 30.0
            for x in range(int(sx - half), int(sx + half) + 1):
                if not (0 <= x < W):
                    continue
                edge = 1.0 - abs(x - sx) / half
                q = edge * (0.85 - depth * 0.45)
                if _hash01(x // 2, y, 8) < q * 0.75 and (y - HORIZON) % 2 == 0:
                    self.put(x, y, _pick(GLINT, edge * 1.3 - depth * 0.4, x, y))
        # a few calm ripple lines catching the sky
        for k in range(7):
            y = HORIZON + 4 + int((k / 6.0) ** 1.45 * (span - 8)) + (k % 2)
            depth = (y - HORIZON) / span
            cx = int((math.sin(k * 2.7) * 0.5 + 0.5) * W)
            wide = 20 + int(40 * depth) + (k * 7) % 19
            for x in range(cx - wide, cx + wide):
                if 0 <= x < W and _hash01(x // 4, k, 9) < 0.5:
                    self.put(x, y, 48 if depth < 0.30 else 49)
        # petals already on the water, thickest where the canopy overhangs
        for _ in range(80):
            x = rng.randrange(W)
            y = rng.randint(HORIZON + 3, WATER_BOT - 4)
            depth = (y - HORIZON) / span
            if rng.random() > smooth[x] * 2.2 * (1.0 - depth * 0.5) + 0.06:
                continue
            c = rng.choice((25, 26, 15, 7))
            self.put(x, y, c)
            if depth > 0.5 and rng.random() < 0.5:
                self.put(x + 1, y, c)
        # the near shore's own shadow, where the bank leans over the water
        for y in range(WATER_BOT - 6, WATER_BOT + 1):
            f = (y - (WATER_BOT - 6)) / 6.0
            for x in range(W):
                if _thr(x, y) < f * 0.85:
                    self.put(x, y, _pick((60, 61, 51), 1.0 - f, x, y))

    def birds(self):
        """Three gulls out over the water. Drawn last of the sky elements and
        only onto sky pixels, so a bird never lands inside the canopy."""
        sky = set(SKY) | set(GLOW)
        for bx, by, ink in ((191, 63, 13), (207, 52, 51), (222, 70, 13)):
            for dx, dy in ((-3, -1), (-2, 0), (-1, 0), (0, 1),
                           (1, 0), (2, 0), (3, -1)):
                x, y = bx + dx, by + dy
                if 0 <= x < W and 0 <= y < H and self.get(x, y) in sky:
                    self.put(x, y, ink)

    # -- foreground bank ---------------------------------------------------
    def bank(self):
        rng = self.rng
        for y in range(WATER_BOT - 4, H):
            near = _clamp((y - WATER_BOT) / float(H - WATER_BOT))
            for x in range(W):
                edge = (2.0 * math.sin(x * 0.07 + 1.1) + 1.5 * math.sin(x * 0.23)
                        + 1.6 * _hash01(x // 3, 0, 12) - 1.0)
                if y < WATER_BOT + edge:
                    continue
                # low-frequency patches keep the meadow from reading as a slab
                patch = (0.09 * math.sin(x * 0.045 + y * 0.11)
                         + 0.07 * math.sin(x * 0.017 - y * 0.05 + 2.0))
                t = (0.62 - near * 0.46 + patch
                     + _hash01(x // 2, y // 2, 11) * 0.16)
                self.put(x, y, _pick(GRASS, t, x, y))
            # a warm strip of petal-strewn sand right at the waterline
            if -1 <= y - WATER_BOT <= 2:
                for x in range(W):
                    if _hash01(x, y, 15) < 0.30:
                        self.put(x, y, _pick((13, 29, 31), _hash01(x, y, 16),
                                             x, y))
        # blades along the water's edge
        for _ in range(170):
            x = rng.randrange(W)
            y = WATER_BOT + rng.randint(0, 7)
            h = rng.randint(3, 9)
            lean = rng.choice((-1, 0, 0, 1))
            for k in range(h):
                self.put(x + (k * lean) // 3, y - k,
                         GRASS[2] if k > h - 3 else GRASS[1])
        # fallen petals settled on the grass, warmer and larger as they near
        for _ in range(105):
            x = rng.randrange(W)
            y = rng.randint(WATER_BOT + 2, H - 1)
            near = (y - WATER_BOT) / float(H - WATER_BOT)
            c = rng.choice((25, 26, 15) if near < 0.5 else (26, 15, 14, 7))
            self.put(x, y, c)
            if rng.random() < 0.45 + near * 0.4:
                self.put(x + 1, y, c)
            if near > 0.6 and rng.random() < 0.35:
                self.put(x, y + 1, 25)

    # -- the tree ----------------------------------------------------------
    def branch(self, x, y, ang, length, width, depth, tips, limbs):
        """One tapered limb, drawn as a swept span; recurses into a fork and
        collects blossom anchor points along the way."""
        rng = self.rng
        steps = max(2, int(length))
        curl = rng.uniform(-0.010, 0.010)
        for s in range(steps):
            ang += curl
            x += math.cos(ang)
            y += math.sin(ang)
            w = width * (1.0 - 0.42 * s / steps)
            if w < 0.7:
                w = 0.7
            self.spans.append((x, y, ang, w))
            self._stroke(x, y, ang, w, 1.0)
            if depth <= 2 and s % 7 == 3 and width < 4.5:
                tips.append((x, y, 0.55))
        limbs.append((x, y))
        if depth <= 0:
            tips.append((x, y, 1.0))
            return
        spread = 0.42 + 0.16 * depth
        n = 2 if (depth > 1 or rng.random() < 0.65) else 3
        for i in range(n):
            f = (i / float(n - 1) - 0.5) * 2.0 if n > 1 else 0.0
            a = ang + f * spread + rng.uniform(-0.20, 0.20)
            a = self._steer(a, x, y)
            self.branch(x, y, a,
                        length * rng.uniform(0.58, 0.78),
                        width * rng.uniform(0.55, 0.70),
                        depth - 1, tips, limbs)

    def _stroke(self, x, y, ang, w, cover):
        """One cross-section of a limb, lit on its upper-right flank. `cover`
        below 1 leaves gaps -- that is how the re-stroke pass threads the main
        limbs back THROUGH the blossoms instead of over them."""
        nx, ny = -math.sin(ang), math.cos(ang)
        half = w * 0.5
        k = -half
        while k <= half:
            px = int(round(x + nx * k))
            py = int(round(y + ny * k))
            u = 0.5 - 0.5 * (k / half if half > 0.6 else 0.0)
            if nx < 0:
                u = 1.0 - u
            if cover >= 1.0 or _hash01(px, py, 37) < cover:
                t = 0.04 + (u ** 2.0) * 0.90 + _hash01(px, py, 13) * 0.16
                self.put(px, py, _pick(BARK, t, px, py))
            k += 0.5

    def restroke(self, min_w, cover):
        """Re-lay the limbs at least `min_w` thick over the finished canopy."""
        for x, y, ang, w in self.spans:
            if w >= min_w:
                self._stroke(x, y, ang, w * 0.80, cover)

    @staticmethod
    def _steer(ang, x, y):
        """Keep limbs inside the frame and reaching up-and-out: near an edge
        the angle is nudged back toward the middle of the canvas."""
        if x < 40:
            ang -= 0.30 * (1.0 - x / 40.0) * math.copysign(1.0, math.sin(ang))
        if x > W - 40:
            ang += 0.30 * (1.0 - (W - x) / 40.0) * math.copysign(1.0, math.sin(ang))
        if y < 26:
            ang = ang * 0.55                     # flatten out under the top edge
        return ang

    def trunk(self, x0, y0, x1, y1, w0, w1, y_from=None):
        """The main stem, a tapered column with bark texture; `y_from` clips it
        so the lower half can be re-laid over water and grass."""
        n = int(math.hypot(x1 - x0, y1 - y0)) + 1
        for s in range(n + 1):
            f = s / float(n)
            x = x0 + (x1 - x0) * f + math.sin(f * 2.6) * 3.0
            y = y0 + (y1 - y0) * f
            w = w0 + (w1 - w0) * f
            half = w * 0.5
            k = -half
            while k <= half:
                px, py = int(round(x + k)), int(round(y))
                if y_from is not None and py < y_from:
                    k += 0.5
                    continue
                u = 0.5 + 0.5 * (k / half)
                t = 0.02 + (u ** 2.4) * 0.94 + _hash01(px, py, 17) * 0.16
                if _hash01(px // 2, py // 3, 19) < 0.16:   # lenticel scars
                    t *= 0.35
                self.put(px, py, _pick(BARK, t, px, py))
                k += 0.5

    def clump(self, cx, cy, r, lit):
        """One blossom cluster: overlapping lobes, speckled at the rim so the
        canopy edge reads as separate flowers rather than a painted blob."""
        rng = self.rng
        lobes = [(cx, cy, r)]
        for _ in range(rng.randint(3, 6)):
            a = rng.uniform(0, 6.2832)
            d = rng.uniform(0.25, 0.85) * r
            lobes.append((cx + math.cos(a) * d, cy + math.sin(a) * d * 0.8,
                          r * rng.uniform(0.45, 0.85)))
        x0 = int(min(l[0] - l[2] for l in lobes)) - 1
        x1 = int(max(l[0] + l[2] for l in lobes)) + 2
        y0 = int(min(l[1] - l[2] for l in lobes)) - 1
        y1 = int(max(l[1] + l[2] for l in lobes)) + 2
        for y in range(max(0, y0), min(H, y1)):
            for x in range(max(0, x0), min(W, x1)):
                cov = 0.0
                for lx, ly, lr in lobes:
                    d = math.hypot(x - lx, (y - ly) / 0.86)
                    c = 1.0 - d / lr
                    if c > cov:
                        cov = c
                if cov <= 0.0:
                    continue
                if cov < 0.30 and _hash01(x, y, 23) > cov * 2.6:
                    continue
                # light from the upper right, plus flower-sized mottling
                u = 0.42 + 0.30 * ((x - cx) / r) - 0.42 * ((y - cy) / r)
                mott = (_hash01(x // 2, y // 2, 29) - 0.5) * 0.42
                t = _clamp(0.06 + lit * 0.36 + u * 0.50 + mott + cov * 0.16)
                if t > 0.86 and _hash01(x, y, 31) < 0.16:
                    self.put(x, y, CANOPY_POP)
                else:
                    self.put(x, y, _pick(CANOPY, t, x, y))

    def tree(self):
        rng = self.rng
        tips, limbs = [], []
        self.trunk(58, H + 6, 74, 128, 27.0, 12.0)
        # three main limbs off the fork, fanning up and to the right
        for ang, ln, wd in ((-2.05, 46, 10.5), (-1.42, 52, 11.5), (-0.72, 58, 10.0)):
            self.branch(74, 128, ang + rng.uniform(-0.06, 0.06), ln, wd, 3,
                        tips, limbs)
        # a second, smaller stem leaning in along the top-right edge so the
        # canopy closes across the frame instead of trailing off
        self.branch(W + 10, 15, 3.02, 38, 7.0, 2, tips, limbs)
        clumps = []
        sx, sy, _sr = SUN
        for x, y, lit in tips:
            if math.hypot(x - sx, (y - sy) * 1.15) < SUN_CLEAR:
                continue
            if y > 168:
                continue
            r = rng.uniform(9.0, 17.0) * (0.72 + 0.28 * lit)
            clumps.append((x, y, r, lit))
        # fill the crown out with free-floating clusters so the canopy has a
        # body, not just beads on the branch tips
        for _ in range(150):
            x = rng.uniform(-8, W + 8)
            ceiling = 118.0 - 62.0 * _clamp((x - 30.0) / 250.0)
            y = rng.uniform(-10, ceiling)
            if math.hypot(x - sx, (y - sy) * 1.15) < SUN_CLEAR:
                continue
            if x > 214 and rng.random() < 0.62:      # keep the right side airy
                continue
            r = rng.uniform(7.0, 15.0)
            clumps.append((x, y, r, _clamp(0.30 + (140.0 - y) / 150.0)))
        # danglers: a few clusters hanging below the canopy line, so its
        # silhouette against the water is ragged rather than ruled
        for _ in range(22):
            x = rng.uniform(-6, 236)
            ceiling = 118.0 - 62.0 * _clamp((x - 30.0) / 250.0)
            y = ceiling + rng.uniform(4.0, 34.0)
            if math.hypot(x - sx, (y - sy) * 1.15) < SUN_CLEAR:
                continue
            clumps.append((x, y, rng.uniform(5.0, 10.0),
                           _clamp(0.45 + (140.0 - y) / 200.0)))
        clumps.sort(key=lambda c: c[1])              # back (high) to front
        for x, y, r, lit in clumps:
            self.clump(x, y, r, lit)
        # the main limbs come back over the blossoms, gapped: a cherry tree's
        # structure reads THROUGH its canopy, and without this the crown eats
        # the branches and the tree becomes a trunk with two stubs.
        self.restroke(4.2, 0.80)
        self.restroke(2.2, 0.42)
        return clumps


def _emit_points(clumps, rng):
    """Petal-shedding anchors for the carts' EMIT table: the underside of real
    blossom clusters, thinned onto a grid so the flurry stays even."""
    seen = set()
    pts = []
    for x, y, r, _lit in sorted(clumps, key=lambda c: -c[2]):
        px = int(round(x))
        py = int(round(y + r * 0.55))
        if not (2 <= px <= W - 3) or not (2 <= py <= 172):
            continue
        cell = (px // 17, py // 15)
        if cell in seen:
            continue
        seen.add(cell)
        pts.append((px, py))
    rng.shuffle(pts)
    pts = pts[:145]
    pts.sort()
    return pts


def _wrap(text, width=86, indent=""):
    out, line = [], indent
    for word in text.split(" "):
        if line != indent and len(line) + len(word) + 1 > width:
            out.append(line)
            line = indent
        line += ("" if line == indent else " ") + word
    out.append(line)
    return "\n".join(out)


def emit_tables(pts):
    py = "EMIT = [" + ", ".join("(%d, %d)" % p for p in pts) + "]"
    lua = ("EMIT = { " + ", ".join("{%d, %d}" % p for p in pts) + " }")
    return py, _wrap(lua)


def write_png(path, buf):
    rows = []
    for y in range(H):
        row = bytearray()
        for x in range(W):
            row += bytes(MOY64[buf[y * W + x]])
        rows.append(row)
    pngwrite.write_png(path, rows, W, H)


def encode_bg(buf):
    """The legacy zlib .moyimg envelope -- what both carts' bg has always used
    (a dense 320x240 scene compresses far better than the RLE codec, and every
    decoder dispatches on the absent `codec` field)."""
    import base64
    return json.dumps({
        "format": "moyimg-v1", "w": W, "h": H,
        "data": base64.b64encode(zlib.compress(bytes(buf), 9)).decode("ascii"),
    })


def render():
    sc = Scene()
    sc.sky()
    sc.far_shore()
    clumps = sc.tree()
    sc.birds()
    mirror = bytes(sc.buf)
    sc.water(mirror)
    sc.bank()
    sc.trunk(58, H + 6, 74, 128, 27.0, 12.0, y_from=HORIZON - 2)
    return sc, clumps


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--png", help="also write a preview PNG here")
    ap.add_argument("--emit", action="store_true",
                    help="print the EMIT literals for main.py / main.lua")
    ap.add_argument("--dry-run", action="store_true",
                    help="render (and preview) but do not touch the carts")
    args = ap.parse_args(argv)

    sc, clumps = render()
    blob = encode_bg(sc.buf)
    if args.png:
        write_png(args.png, sc.buf)
        print("preview ->", args.png)
    if not args.dry_run:
        for slug in ("sakura", "sakura_lua"):
            d = os.path.join(ROOT, "system_carts", slug + ".moy", "images")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "bg.moyimg"), "w") as f:
                f.write(blob)
            print("wrote", os.path.join(d, "bg.moyimg"), len(blob), "bytes")
    if args.emit:
        py, lua = emit_tables(_emit_points(clumps, random.Random(SEED + 1)))
        print("\n--- main.py ---\n" + py)
        print("\n--- main.lua ---\n" + lua)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
