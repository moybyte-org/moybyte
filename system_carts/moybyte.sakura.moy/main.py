# Sakura -- a living cherry-tree wallpaper (v0.4). The backdrop is an image supplied
# by the project owner (AI-generated; the project's own, no outside rights
# holder), converted to this cart's 320x240 MOY64 bitmap by
# tools/import_sakura_bg.py -- crop to 4:3, LANCZOS downscale, nearest-colour
# quantise. That script also writes scenes/blossoms.moyscene, since the shedding
# points have to sit on THIS image's canopy -- and because it is a SCENE, a kid
# who repaints the tree can drag the points onto their new canopy in the Scene
# tab, with no host tool involved. Blossoms shed from the canopy, drift on the
# breeze, and scatter from your cursor (touch on device). Petal count / fall /
# breeze / colour are editable in "Make it mine".
#
# The static scene is a PAINT-IMAGE ASSET (#63 Fold 3): a 320x240 MOY64 index bitmap
# stored as images/bg.moyimg (deflate-compressed data, NOT draw calls). At _init it is
# fetched with image("bg") and stamped into an off-screen LAYER (make_layer, #54) with
# ONE spr(bg, 0, 0) -- which the device bakes index->RGB565 in a single native
# blit_indices call (was ~32k rect() replays, seconds of load). draw_layer then copies
# the finished layer to the screen each frame as a flat blit -- no per-frame background
# work. The N petals are drawn with the naive per-petal spr() loop, which the engine
# AUTO-BATCHES into ONE native blit_batch (Fold 1, #63) -- so the kid-obvious loop is
# the fast one, and the draw-call count (the device's FPS ceiling) collapses to one for
# the whole flurry. The sway reads a 256-entry sine table built
# once, so the per-frame loop never calls math.*.

import math

EMIT = []           # canopy shed points (scenes/blossoms.moyscene), read at _init
SIN = []            # sine LUT (built once); the hot loop indexes it, never calls sin
lay = None          # the static scene, inflated + painted once, copied per frame (#54)
petals = []         # each: [x, y, fall_speed, sway_phase, sway_amp, shade(0 near..2 far)]
base = 0            # the run's blossom sheet column (base tile); each petal draws base + shade
t = 0.0

# The petal colours live in sprites.moygfx: 12 tiles, one column per blossom
# choice, three shades deep (0 near / 1 mid / 2 far, the near tile carrying a
# white glint). Recolour a blossom in the sprite editor -- nothing here to match.
BLOSSOM_ORDER = ("pink", "white", "peach", "mixed")   # sheet column order (base = i*3)


def _blossom_base():
    # A run's blossom colour fixes the sheet column; each petal's tile is base + shade.
    # Unknown names fall back to pink (base 0).
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
        a = EMIT[int(rnd(n)) % n]
        ex = a.x
        ey = a.y
    else:
        ex = rnd(W)
        ey = 0.0
    p[0] = ex + rnd(7.0) - 3.0
    p[1] = (ey - 2.0) if fresh else (ey + rnd(H - ey + 10.0))
    p[3] = rnd(1.0)


def _init():
    global lay, petals, base, t, EMIT
    EMIT = scene()                         # the canopy, as placed in the Scene tab
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
    # native blit_batch (Fold 1, #63) -- there is no faster way to write it, and no
    # manual batch verb to reach for (the API has none). Each petal's
    # tile is `base` (the run's blossom column) + its depth shade p[5]; the tile art
    # bakes the depth colour + the near-petal white glint.
    draw_layer(lay, 0, 0)
    for p in petals:
        spr(base + p[5], int(p[0]), int(p[1]), 0)
