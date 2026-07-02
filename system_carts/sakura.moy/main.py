EMIT = [(20, 44), (21, 76), (21, 114), (24, 133), (28, 61), (28, 152), (30, 95), (36, 114), (36, 133), (36, 152), (38, 76), (40, 95), (44, 19), (46, 57), (53, 55), (54, 76), (54, 95), (54, 114), (54, 133), (54, 152), (59, 57), (65, 38), (67, 7), (70, 19), (72, 7), (72, 57), (72, 76), (72, 95), (72, 114), (73, 133), (73, 152), (75, 38), (77, 19), (89, 38), (89, 76), (89, 95), (89, 114), (89, 133), (90, 57), (91, 19), (100, 152), (101, 13), (107, 19), (108, 38), (108, 57), (108, 95), (110, 76), (112, 133), (113, 12), (117, 114), (119, 153), (125, 38), (125, 57), (125, 95), (125, 114), (126, 135), (127, 76), (134, 152), (135, 19), (139, 14), (143, 19), (143, 38), (143, 57), (143, 76), (143, 95), (143, 114), (143, 133), (149, 152), (156, 8), (160, 19), (160, 38), (160, 57), (160, 95), (161, 76), (161, 114), (161, 133), (170, 6), (173, 152), (178, 19), (178, 57), (178, 76), (178, 95), (178, 152), (179, 114), (180, 38), (180, 133), (184, 6), (196, 6), (196, 38), (196, 76), (196, 114), (196, 133), (197, 57), (197, 95), (197, 152), (198, 19), (214, 38), (214, 57), (214, 76), (214, 95), (214, 114), (214, 133), (218, 19), (218, 152), (229, 10), (232, 19), (232, 38), (232, 57), (232, 95), (232, 133), (233, 114), (235, 17), (238, 76), (241, 152), (249, 38), (249, 57), (249, 76), (249, 95), (249, 114), (249, 133), (249, 152), (260, 19), (266, 18), (267, 57), (267, 76), (267, 95), (267, 114), (267, 158), (269, 39), (282, 17), (283, 20), (283, 135), (285, 57), (285, 76), (285, 95), (285, 114), (288, 20), (290, 141), (291, 7), (293, 44), (298, 154), (303, 49), (303, 76), (303, 95), (303, 114), (303, 154), (304, 62)]

# Sakura -- a living cherry-tree wallpaper (v0.4). The backdrop is a real pixel-art
# cherry tree (by GenossinChloe, originally a Picotron wallpaper) quantised to the
# MOY64 palette; blossoms shed from the canopy, drift on the breeze, and scatter
# from your cursor (touch on device). Petal count / fall / breeze / colour are
# editable in "Make it mine".
#
# The static scene is a PAINT-IMAGE ASSET (#63 Fold 3): a 320x240 MOY64 index bitmap
# stored as images/bg.moyimg (deflate-compressed data, NOT draw calls). At _init it is
# fetched with image("bg") and stamped into an off-screen LAYER (make_layer, #54) with
# ONE spr(bg, 0, 0) -- which the device bakes index->RGB565 in a single native
# blit_indices call (was ~32k rect() replays, seconds of load). draw_layer then copies
# the finished layer to the screen each frame as a flat blit -- no per-frame background
# work. The N petals are drawn with the naive per-petal spr() loop, which the engine
# AUTO-BATCHES into ONE native blit_batch (Fold 1, #63) -- so the kid-obvious loop is as
# fast as a hand-rolled spr_batch, and the draw-call count (the device's FPS ceiling)
# collapses to one for the whole flurry. The sway reads a 256-entry sine table built
# once, so the per-frame loop never calls math.*.

import math

SIN = []            # sine LUT (built once); the hot loop indexes it, never calls sin
lay = None          # the static scene, inflated + painted once, copied per frame (#54)
petals = []         # each: [x, y, fall_speed, sway_phase, sway_amp, shade(0 near..2 far)]
base = 0            # the run's blossom sheet column (base tile); each petal draws base + shade
t = 0.0

# Falling-petal palette by depth: (near, mid, far). Near gets a white glint. These
# colours are BAKED INTO sprites.moygfx: tile = BLOSSOM_ORDER.index(colour)*3 + shade
# (shade 0 near / 1 mid / 2 far), the near tile carrying the glint pixel. The petals
# draw from that sheet via spr(), so if you change a colour here, REGENERATE the
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
    global lay, petals, base, t
    _build_sin()
    if lay is None:                        # allocate the scene buffer only once
        lay = make_layer(W, H)
    bg = image("bg")                       # the painted cherry-tree scene (images/bg.moyimg)
    if bg is not None:
        lay.spr(bg, 0, 0)                  # ONE native blit_indices bake (was 32k rect() replays)
    n = int(cfg("petal_count", 120))
    fall = float(cfg("fall_speed", 30))
    base = _blossom_base()                 # blossom fixes the tile column for the run
    petals = []
    for i in range(n):
        shade = i % 3
        spd = fall * (1.0 - 0.18 * shade) * (0.7 + rnd(0.6))
        p = [0.0, 0.0, spd, 0.0, 4.0 + rnd(9.0), shade]
        _shed(p, False)
        petals.append(p)
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
    # Background: one flat blit. Petals: the naive per-petal spr() loop -- one call per
    # petal at (x, y), colorkey 0 (index 0 is transparent in the tile art). This
    # contiguous run of 1x1 sheet-tile spr()s is AUTO-BATCHED by the engine into ONE
    # native blit_batch (Fold 1, #63), so it costs the same as a hand-rolled spr_batch
    # and is pixel-identical -- the kid never has to know spr_batch exists. Each petal's
    # tile is `base` (the run's blossom column) + its depth shade p[5]; the tile art
    # bakes the depth colour + the near-petal white glint.
    draw_layer(lay, 0, 0)
    for p in petals:
        spr(base + p[5], int(p[0]), int(p[1]), 0)
