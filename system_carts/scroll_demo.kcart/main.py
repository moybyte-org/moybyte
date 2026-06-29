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
# AUTORUN (in "Make it mine") is OFF -- YOU run. Flip it on to watch it auto-run.

LW = 800         # the world (layer) is 2.5 screens wide -> it scrolls
GY = 188         # ground top (y); the runner stands here
PW = 12          # runner collision box
PH = 22
GRAV = 900.0     # hop gravity (px/s^2)
HOP = 340.0      # hop launch speed

lay = None       # the pre-rendered world layer (built once per run in _init)
px = 0.0         # runner world x
py = 0.0         # runner world y (top of the box)
vy = 0.0         # vertical speed (hop)
on_ground = True
cam = 0.0        # camera world x (left edge of the visible window)
runf = 0.0       # run-cycle phase (animates the legs)
coins = []       # [world_x, world_y, taken]
got = 0
won = 0.0        # >0 = win banner countdown, then reset
t = 0.0


def _tree(x):
    lay.rect(x + 4, GY - 26, 4, 26, col("brown"))        # trunk
    lay.circ(x + 6, GY - 30, 11, col("dark_green"))      # canopy (back)
    lay.circ(x + 6, GY - 34, 8, col("green"))            # canopy (front)


def _build_world():
    # Paint the whole static world into the layer ONCE. The layer is opaque, so it
    # must cover the full height (sky included) -- draw_layer copies a screen-sized
    # window of it each frame.
    global coins
    lay.cls(col("dark_blue"))
    lay.rect(0, GY - 64, LW, 64, col("blue"))            # lower sky band
    for cx in (70, 230, 420, 600, 760):                  # clouds
        lay.circ(cx, 38, 12, col("white"))
        lay.circ(cx + 14, 42, 10, col("white"))
        lay.circ(cx - 12, 44, 9, col("white"))
    for hx, r, c in ((50, 70, "indigo"), (210, 92, "dark_purple"),
                     (410, 80, "indigo"), (620, 100, "dark_purple"),
                     (780, 75, "indigo")):               # distant hills
        lay.circ(hx, GY, r, col(c))
    lay.rect(0, GY, LW, H - GY, col("brown"))            # dirt
    lay.rect(0, GY, LW, 6, col("green"))                 # grass top
    x = 6
    while x < LW:                                        # grass tufts
        lay.line(x, GY - 1, x, GY - 4, col("green"))
        x += 11
    tx = 40
    while tx < LW - 60:                                  # trees across the level
        _tree(tx)
        tx += 70
    coins = []
    cx = 120
    while cx < LW - 110:                                 # coins along the path
        coins.append([cx, GY - 30, 0])
        cx += 95


def _init():
    global lay, px, py, vy, on_ground, cam, got, won, t, runf
    # Build the wide layer only once per run -- re-allocating ~375KB on every win
    # reset would fragment/exhaust the heap; _reset just repaints the same buffer.
    if lay is None:
        lay = make_layer(LW, H)
    _build_world()
    px = 40.0
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
        beep(440, 0.08)
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
            beep(660, 0.05)
    if px + PW > LW - 50:                                # reach the flag -> win
        won = 2.0


def _draw():
    cx = int(cam)
    draw_layer(lay, cx, 0)                               # the scrolling world (#54)
    for c in coins:                                      # coins (spin = radius pulse)
        if c[2]:
            continue
        sx = int(c[0]) - cx
        if sx < -8 or sx > W + 8:
            continue
        rr = 4 + int(2 * abs(((t * 3) % 2) - 1))
        circ(sx, int(c[1]), rr, col("yellow"))
        circb(sx, int(c[1]), rr, col("orange"))
    fx = (LW - 40) - cx                                  # goal flag
    if -20 < fx < W + 20:
        rect(fx, GY - 60, 2, 60, col("light_grey"))
        rect(fx + 2, GY - 60, 18, 12, col("red"))
        rect(fx + 2, GY - 56 + int(4 * ((t * 4) % 2)), 18, 3, col("white"))
    _draw_runner(int(px) - cx, int(py))                  # the runner, on top
    print("COINS " + str(got) + "/" + str(len(coins)), 6, 4, col("white"), 1)
    if won > 0.0:
        print("YOU MADE IT!", W // 2 - 48, H // 2 - 8, col("yellow"), 2)


def _draw_runner(x, y):
    c = col(cfg("hero", "red"))
    rect(x, y + 6, PW, PH - 6, c)                        # body
    circ(x + PW // 2, y + 3, 4, col("peach"))            # head
    if on_ground:                                        # legs alternate while running
        if int(runf) % 2 == 0:
            rect(x + 1, y + PH - 2, 3, 4, col("dark_grey"))
            rect(x + PW - 4, y + PH, 3, 2, col("dark_grey"))
        else:
            rect(x + 1, y + PH, 3, 2, col("dark_grey"))
            rect(x + PW - 4, y + PH - 2, 3, 4, col("dark_grey"))
    else:                                                # tucked while hopping
        rect(x + 1, y + PH - 1, 3, 3, col("dark_grey"))
        rect(x + PW - 4, y + PH - 1, 3, 3, col("dark_grey"))
