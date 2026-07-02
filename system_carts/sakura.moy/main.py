import sys
_BG = (
    "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"
    "wfr+eEPur577CVF27fQmKsFj/lbJT6+Fd2997YokU/nv8Z34NpNo2vCIIZilkGsuFOpX5adTy1M7zy+vu3vvln/dkOTVPn7XOYc3"
    "le2PyY9wtJ3xixPE9PBpN8qv4kFwPl4uX7zpWCYWdqvrYMJjHFUB10/J7xmTvgB0X2rGO8cCfqEJ8/QVGVc8sOr59wsYn/9wyLWk"
    "dWeUpn3HN4+eDcLbmkIr3wFzPff/iaP1Ly/s973j/p6HsCU1FgX81vj7PAb09U3Cn/VGXz7q3LydrX769OT7L04Pdy5dba5Kw79G"
    "iosZsQ4aoWKJ2O15fhpfWftOxNy/Y57b8s2dnOqy+yZBBv+FW/uRsfudVSUfie6dmzi/8HpetSt79ereh258qmczor2xTaP8fHzp"
    "CGNfGp7towBj1H7Ae9m3O35TBcx9I/Fiv/sxvZNq2A8XOj0Ij5Ww/UBv/gKOhel8O4d2HaZ6WtZbXQ2lv/kuvvHVN1Ql7wooHpAe"
    "tQvfIr7OXe13ymj+Vsv82Ey/OAMOj5dYha05Mw42mLuAQRkrAqym7myutzHI88YAvGIWkdf8ID6007+4j6xnln7U9W0P+EE/QFeI"
    "i+/Ura/99je++qsnsm2kVdHhE0+sduw/q2v+5XFqhc3zVYc7qxorZEirDAJQljEWmLIO8z1s7t2oD/r35k31b5izhsb89QDyrXLs"
    "vrTC+VtNe58zTA8PhQXPoWRe1ksyVR1T+k38fsy5u5i6myMw1xSrnFiuEOkR4o1osgBIGTDlcYcXMx0gx2oGdYC70j9Gzu5tBew+"
    "aorv2vtbfJtvmz6ni+0HdyHZN95UQOs8FXiFKluU6FpDA5IVW63y2accp/P1aofUHrtE6aHBAI9RZdibdV2/DCCqmpRfke1HJMBj"
    "5PiQHv5D8ntTIrZwsI807PvPTtt7Z7LOD+7v3ALB5BqLsram6iQSn+OT6KxIoa7QgzZgbObVg7zzwyte7vbLwq8tVv05vrjrfmBQ"
    "/cAcv+Xl2z3AfhBYBC/XU7IItzCtabpgmauuqUgKDBcRxqDdLUgxxoKSTV9mPh5i8uUOoF8E5z8mlunHSPMTIPG930r2mYd68dM9"
    "mcD5UNjsae0ZizpEZnMn1o0tqe28DLdLg5M32FKgo4KXeImQ0j7/Wx7wsfLdPXywjTz9GK3J3ym474CRonydfeMy5vtn8pPnHh/M"
    "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"
)

EMIT = [(20, 44), (21, 76), (21, 114), (24, 133), (28, 61), (28, 152), (30, 95), (36, 114), (36, 133), (36, 152), (38, 76), (40, 95), (44, 19), (46, 57), (53, 55), (54, 76), (54, 95), (54, 114), (54, 133), (54, 152), (59, 57), (65, 38), (67, 7), (70, 19), (72, 7), (72, 57), (72, 76), (72, 95), (72, 114), (73, 133), (73, 152), (75, 38), (77, 19), (89, 38), (89, 76), (89, 95), (89, 114), (89, 133), (90, 57), (91, 19), (100, 152), (101, 13), (107, 19), (108, 38), (108, 57), (108, 95), (110, 76), (112, 133), (113, 12), (117, 114), (119, 153), (125, 38), (125, 57), (125, 95), (125, 114), (126, 135), (127, 76), (134, 152), (135, 19), (139, 14), (143, 19), (143, 38), (143, 57), (143, 76), (143, 95), (143, 114), (143, 133), (149, 152), (156, 8), (160, 19), (160, 38), (160, 57), (160, 95), (161, 76), (161, 114), (161, 133), (170, 6), (173, 152), (178, 19), (178, 57), (178, 76), (178, 95), (178, 152), (179, 114), (180, 38), (180, 133), (184, 6), (196, 6), (196, 38), (196, 76), (196, 114), (196, 133), (197, 57), (197, 95), (197, 152), (198, 19), (214, 38), (214, 57), (214, 76), (214, 95), (214, 114), (214, 133), (218, 19), (218, 152), (229, 10), (232, 19), (232, 38), (232, 57), (232, 95), (232, 133), (233, 114), (235, 17), (238, 76), (241, 152), (249, 38), (249, 57), (249, 76), (249, 95), (249, 114), (249, 133), (249, 152), (260, 19), (266, 18), (267, 57), (267, 76), (267, 95), (267, 114), (267, 158), (269, 39), (282, 17), (283, 20), (283, 135), (285, 57), (285, 76), (285, 95), (285, 114), (288, 20), (290, 141), (291, 7), (293, 44), (298, 154), (303, 49), (303, 76), (303, 95), (303, 114), (303, 154), (304, 62)]

# Sakura -- a living cherry-tree wallpaper (v0.4). The backdrop is a real pixel-art
# cherry tree (by GenossinChloe, originally a Picotron wallpaper) quantised to the
# MOY64 palette; blossoms shed from the canopy, drift on the breeze, and scatter
# from your cursor (touch on device). Petal count / fall / breeze / colour are
# editable in "Make it mine".
#
# Authored to the device envelope: the static scene is stored ONCE as zlib-packed
# palette indices, inflated + painted into an off-screen LAYER at _init (make_layer,
# #54) and copied to the screen each frame as a flat blit (draw_layer) -- no
# per-frame background work. All N petals blit in ONE spr_batch call (#43) rather
# than up to two per-petal rect/pix calls -- the draw-call count is the device's
# FPS ceiling. The sway reads a 256-entry sine table built once, so the per-frame
# loop never calls math.*.

import math

SIN = []            # sine LUT (built once); the hot loop indexes it, never calls sin
lay = None          # the static scene, inflated + painted once, copied per frame (#54)
petals = []         # each: [x, y, fall_speed, sway_phase, sway_amp, shade(0 near..2 far)]
batch = []          # spr_batch items [tile, x, y], one per petal: the tile is fixed at
                    # _init (blossom + shade), only x/y refresh per frame -> one native call
t = 0.0

# Falling-petal palette by depth: (near, mid, far). Near gets a white glint. These
# colours are BAKED INTO sprites.moygfx: tile = BLOSSOM_ORDER.index(colour)*3 + shade
# (shade 0 near / 1 mid / 2 far), the near tile carrying the glint pixel. The petals
# draw from that sheet via spr_batch, so if you change a colour here, REGENERATE the
# sheet to match (12 tiles, painted with these indices, colorkey 0).
BLOSSOMS = {
    "pink":  (14, 14, 2),
    "white": (7, 6, 13),
    "peach": (15, 9, 4),
    "mixed": (14, 15, 7),
}
BLOSSOM_ORDER = ("pink", "white", "peach", "mixed")   # sheet column order (base = i*3)


def _blossom_base():
    # A run's blossom colour fixes the sheet column; each petal's tile is base + shade.
    # Unknown names fall back to pink (base 0), matching BLOSSOMS.get(...) below.
    name = cfg("blossom", "pink")
    for i in range(len(BLOSSOM_ORDER)):
        if BLOSSOM_ORDER[i] == name:
            return i * 3
    return 0


def _build_sin():
    global SIN
    if not SIN:
        SIN = [math.sin(i / 256.0 * 6.2831853) for i in range(256)]


def _sin(turn):
    return SIN[int(turn * 256.0) & 255]


def _paint_bg():
    # Inflate the zlib-packed 320x240 indices and paint them into the layer as
    # horizontal run rects (portable: rect() maps index->RGB on host AND device).
    # Runs once at _init. zlib on the host (CPython), deflate on the device (MP).
    import binascii
    data = binascii.a2b_base64(_BG)
    if sys.implementation.name == "cpython":
        import zlib
        raw = zlib.decompress(data)
    else:
        import deflate, io
        raw = deflate.DeflateIO(io.BytesIO(data), deflate.ZLIB).read()
    rect_ = lay.rect
    i = 0
    for y in range(H):
        x = 0
        while x < W:
            c = raw[i + x]
            x2 = x + 1
            while x2 < W and raw[i + x2] == c:
                x2 += 1
            rect_(x, y, x2 - x, 1, c)
            x = x2
        i += W


def _shed(p, fresh):
    # Place a petal at a random canopy blossom -- the tree shedding it. fresh=True
    # starts it right at the cluster; else scatter it down the column so the air
    # starts full.
    n = len(EMIT)
    if n:
        ex, ey = EMIT[int(rnd(n)) % n]
    else:
        ex = rnd(W)
        ey = 0.0
    p[0] = ex + rnd(7.0) - 3.0
    p[1] = (ey - 2.0) if fresh else (ey + rnd(H - ey + 10.0))
    p[3] = rnd(1.0)


def _init():
    global lay, petals, batch, t
    _build_sin()
    if lay is None:                        # allocate the scene buffer only once
        lay = make_layer(W, H)
    _paint_bg()
    n = int(cfg("petal_count", 120))
    fall = float(cfg("fall_speed", 30))
    base = _blossom_base()                 # blossom fixes the tile column for the run
    petals = []
    batch = []
    for i in range(n):
        shade = i % 3
        spd = fall * (1.0 - 0.18 * shade) * (0.7 + rnd(0.6))
        p = [0.0, 0.0, spd, 0.0, 4.0 + rnd(9.0), shade]
        _shed(p, False)
        petals.append(p)
        batch.append([base + shade, 0, 0])   # tile fixed; x/y refreshed each frame in _draw
    t = 0.0


def _update(dt):
    global t
    if dt > 0.1:
        dt = 0.1
    t += dt
    breeze = float(cfg("breeze", 18))
    tp = touch()
    cx = -999.0
    cy = -999.0
    if tp is not None:
        cx = tp[0]
        cy = tp[1]
    R = 52.0
    for p in petals:
        p[3] += dt * (0.32 + 0.06 * p[5])
        sway = _sin(p[3]) * p[4]
        p[0] += (breeze * (1.0 - 0.15 * p[5]) + sway) * dt
        p[1] += p[2] * dt
        dx = p[0] - cx
        dy = p[1] - cy
        if -R < dx < R and -R < dy < R:
            far = dx if dx >= 0 else -dx
            ady = dy if dy >= 0 else -dy
            if ady > far:
                far = ady
            k = (R - far) / R * 130.0
            inv = 1.0 / (far + 4.0)
            p[0] += dx * inv * k * dt
            p[1] += dy * inv * k * dt
        if p[1] > H + 4.0:
            _shed(p, True)
        elif p[0] < -8.0:
            p[0] += W + 16.0
        elif p[0] > W + 8.0:
            p[0] -= W + 16.0


def _draw():
    # Background: one flat blit. Petals: refresh each batch item's x/y (its tile was
    # fixed at _init) and hand the whole list to spr_batch -- ONE native draw call for
    # all N petals. The tile art bakes the depth colour + the near-petal white glint,
    # with index 0 as the transparent colorkey (no petal colour is 0).
    draw_layer(lay, 0, 0)
    b = batch
    for i, p in enumerate(petals):
        e = b[i]
        e[1] = int(p[0])
        e[2] = int(p[1])
    spr_batch(b, 0)
