# Sky Run -- a side-scroller that shows off the SCROLL ENGINE (#54). The world is
# WIDER than the screen: RUN with LEFT / RIGHT, HOP with A / UP. The camera follows
# you and the whole world slides past. Grab the coins and reach the checkered flag
# to win the round (it celebrates, then resets).
#
# How the scroll works (the #54 demo): the long decorated level -- sky, clouds,
# hills, ground, trees -- is drawn ONCE into an off-screen LAYER that is wider than
# the 320px screen (make_layer). Every frame draw_layer() window-copies just the
# visible slice at the camera offset -- a flat memory copy that replaces re-drawing
# dozens of trees per frame. Coins, the flag and the runner are drawn ON TOP as
# world actors at the same camera offset.
#
# The art is the cart's own: paint it in the PAINT editor (sprites.moygfx), dig the
# ground in the MAP editor (map.moymap) and move the clouds, trees, coins, flag and
# start in the SCENE editor (scenes/main.moyscene).
#
# AUTORUN (in "Make it mine") is OFF -- YOU run. Flip it on to watch it auto-run.

TS = 8           # map cell size -- the ground draws at scale 1, so the map, the
                 # editors' 8px grid and the scene's world pixels all agree
MW = 100         # tilemap width in cells (matches map.moymap)
MH = 30          # tilemap height in cells (the full 240px canvas)
GROUND = 23      # first solid map row; everything above it is sky
LW = MW * TS     # the world (layer) is 2.5 screens wide -> it scrolls
GY = GROUND * TS # ground top (y); the runner stands here
PW = 12          # runner collision box
PH = 22
GRAV = 900.0     # hop gravity (px/s^2)
HOP = 340.0      # hop launch speed

POSES = 3        # sheet: 3 poses per hero (run A, run B, hop), 1x2 spans at 2x
COIN = 6         # 2 spin frames
TREE = 32        # 2x3 span at 2x
CLOUD = 34       # 3x2 span at 2x
FLAG = 38        # 2x4 span at 2x; +2 = the wave frame

lay = None       # the pre-rendered world layer (built once per run in _init)
px = 0.0         # runner world x
py = 0.0         # runner world y (top of the box)
vy = 0.0         # vertical speed (hop)
on_ground = True
cam = 0.0        # camera world x (left edge of the visible window)
runf = 0.0       # run-cycle phase (animates the legs)
coins = []       # [world_x, world_y, taken]
startx = 40      # scene "spawn"
flagx = LW - 40  # scene "goal"
got = 0
won = 0.0        # >0 = win banner countdown, then reset
t = 0.0


def _hero():
    # base tile of the chosen hero's poses (tolerate a stale colour-name config)
    try:
        return int(cfg("hero", 0)) * POSES
    except (TypeError, ValueError):
        return 0


def _build_world():
    # Paint the whole static world into the layer ONCE. The layer is opaque, so it
    # must cover the full height (sky included) -- draw_layer copies a screen-sized
    # window of it each frame. The ground is the cart's TILEMAP in one map() call;
    # the clouds, trees, coins, flag and start line are PLACED in
    # scenes/main.moyscene; the sky bands and hills stay parametric fills, which is
    # what a gradient and a big soft dome want to be.
    global coins, startx, flagx
    clouds = []
    trees = []
    coins = []
    startx = 40
    flagx = LW - 40
    for a in scene():
        if a.tag == "cloud":
            clouds.append(a)
        elif a.tag == "tree":
            trees.append(a)
        elif a.tag == "coin":
            coins.append([a.x + 4, a.y + 4, 0])
        elif a.tag == "spawn":
            startx = a.x
        elif a.tag == "goal":
            flagx = a.x
    lay.cls(col("dark_blue"))
    lay.rect(0, GY - 64, LW, 64, col("blue"))            # lower sky band
    for a in clouds:
        lay.spr(CLOUD, a.x, a.y, 0, 2, 0, 3, 2)
    for hx, r, c in ((50, 70, "indigo"), (210, 92, "dark_purple"),
                     (410, 80, "indigo"), (620, 100, "dark_purple"),
                     (780, 75, "indigo")):               # distant hills
        lay.circ(hx, GY, r, col(c))
    lay.map(0, 0, MW, MH, 0, 0)                          # the ground (map.moymap)
    x = 6
    while x < LW:                                        # tufts, only over ground
        if mget(x // TS, GROUND) >= 0:
            lay.line(x, GY - 1, x, GY - 4, col("green"))
        x += 11
    for a in trees:
        lay.spr(TREE, a.x, a.y, 0, 2, 0, 2, 3)


def _init():
    global lay, px, py, vy, on_ground, cam, got, won, t, runf
    # Build the wide layer only once per run -- re-allocating ~375KB on every win
    # reset would fragment/exhaust the heap; _reset just repaints the same buffer.
    if lay is None:
        lay = make_layer(LW, H)
    _build_world()
    px = float(startx)
    py = float(GY - PH)
    vy = 0.0
    on_ground = True
    cam = 0.0
    got = 0
    won = 0.0
    t = 0.0
    runf = 0.0


def _clampcam():
    global cam
    # Centre the camera on the runner, clamped so the window never falls off the
    # world. draw_layer clamps the SAME way internally, so the actors stay aligned.
    c = px + PW / 2 - W / 2
    if c < 0:
        c = 0.0
    elif c > LW - W:
        c = float(LW - W)
    cam = c


def _update(dt):
    global px, py, vy, on_ground, got, won, runf, t
    t += dt
    if won > 0.0:                                        # win banner -> reset
        won -= dt
        if won <= 0.0:
            _init()
        return
    spd = float(cfg("speed", 130))
    auto = int(cfg("autorun", 0))
    left = btn("left")
    right = btn("right") or auto
    if left and not right:
        px -= spd * dt
        runf += dt * 12
    elif right:
        px += spd * dt
        runf += dt * 12
    if px < 0:
        px = 0.0
    elif px > LW - PW:
        px = float(LW - PW)
    if on_ground and (btn("a") or btn("up") or (auto and rnd() < 0.02)):
        vy = -HOP
        on_ground = False
        sfx(0)
    if not on_ground:                                    # hop physics
        vy += GRAV * dt
        py += vy * dt
        if py >= GY - PH:
            py = float(GY - PH)
            vy = 0.0
            on_ground = True
    _clampcam()
    cxp = px + PW / 2
    cyp = py + PH / 2
    for c in coins:                                      # collect
        if c[2]:
            continue
        if abs(c[0] - cxp) < 14 and abs(c[1] - cyp) < 24:
            c[2] = 1
            got += 1
            sfx(1)
    if px + PW > flagx - 10:                             # reach the flag -> win
        won = 2.0


def _draw():
    cx = int(cam)
    draw_layer(lay, cx, 0)                               # the scrolling world (#54)
    spin = COIN + int(t * 4) % 2
    for c in coins:
        if c[2]:
            continue
        sx = int(c[0]) - cx
        if sx < -8 or sx > W + 8:
            continue
        spr(spin, sx - 4, int(c[1]) - 4, 0)
    fx = flagx - cx                                      # goal flag
    if -20 < fx < W + 20:
        spr(FLAG + 2 * (int(t * 4) % 2), fx, GY - 60, 0, 2, 0, 2, 4)
    _draw_runner(int(px) - cx, int(py))                  # the runner, on top
    print("COINS " + str(got) + "/" + str(len(coins)), 6, 4, col("white"), 1)
    if won > 0.0:
        print("YOU MADE IT!", W // 2 - 48, H // 2 - 8, col("yellow"), 2)


def _draw_runner(x, y):
    pose = 2 if not on_ground else int(runf) % 2         # tucked hop / alternating legs
    spr(_hero() + pose, x - 2, y - 1, 0, 2, 0, 1, 2)
