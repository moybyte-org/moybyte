# Scroll Demo (#54) -- proof of the scroll engine.
#
# A world BIGGER than the screen is pre-rendered ONCE into a wide off-screen layer
# (make_layer), then each frame the visible window is COPIED to the screen
# (draw_layer) instead of re-drawing the whole background. Actors (coins + the player
# crosshair) are drawn on TOP of the copy. It scrolls in ANY direction.
#
# The win: the per-frame background cost is a flat memory copy (~7ms on device),
# regardless of how detailed the world is -- so a scroller hits ~60fps where a
# per-frame map() re-render (~12-14ms) could not. The HUD shows live FPS.
#
# MODE auto  = the camera drifts and bounces around the world, so the FPS reading is
#              the engine's steady full-window-copy cost.
# MODE drive = steer with the arrows / WASD.

bg = None          # the pre-rendered wide background layer (a make_layer canvas)
LW = 0             # world (layer) size in pixels
LH = 0
cam_x = 0.0        # top-left of the visible window, in world pixels
cam_y = 0.0
vx = 0.0           # drift velocity (auto mode)
vy = 0.0
coins = []         # [world_x, world_y, taken]
got = 0
t = 0.0
fps = 0.0


def _build_world():
    # Pre-render the ENTIRE world into the layer ONCE: a checkerboard + landmark rings
    # + coordinate labels + diagonals, so motion in any direction reads instantly. This
    # cost is paid a single time at _init -- not per frame. That is the whole point.
    bg.cls(col("dark_blue"))
    tile = 32
    gy = 0
    while gy < LH:
        gx = 0
        while gx < LW:
            if ((gx // tile) + (gy // tile)) % 2 == 0:
                bg.rect(gx, gy, tile, tile, col("indigo"))
            gx += tile
        gy += tile
    bg.line(0, 0, LW, LH, col("dark_green"))
    bg.line(0, LH, LW, 0, col("dark_green"))
    ly = 0
    while ly < LH:
        lx = 0
        while lx < LW:
            bg.circb(lx, ly, 18, col("light_grey"))
            bg.circ(lx, ly, 4, col("orange"))
            bg.print("%d,%d" % (lx, ly), lx + 6, ly + 6, col("white"))
            lx += 128
        ly += 128


def _spawn_coins():
    global coins, got
    coins = []
    n = 8
    step_x = LW // (n + 1)
    step_y = LH // (n + 1)
    for i in range(n):
        cx = step_x * (i + 1)
        cy = step_y * (((i * 3) % n) + 1)
        coins.append([cx, cy, False])
    got = 0


def _init():
    global bg, LW, LH, cam_x, cam_y, vx, vy, t, fps
    world = int(cfg("world", 2))
    if world < 2:
        world = 2
    LW = W * world
    LH = H * world
    bg = make_layer(LW, LH)
    _build_world()
    cam_x = (LW - W) / 2.0
    cam_y = (LH - H) / 2.0
    spd = float(cfg("speed", 60))
    vx = spd
    vy = spd * 0.7
    t = 0.0
    fps = 0.0
    _spawn_coins()


def _update(dt):
    global cam_x, cam_y, vx, vy, t, fps, got
    t += dt
    if dt > 0.0:
        fps = fps * 0.9 + (1.0 / dt) * 0.1     # smoothed on-screen FPS
    maxx = float(LW - W)
    maxy = float(LH - H)
    if cfg("mode", "auto") == "drive":
        spd = float(cfg("speed", 60)) * 2.0
        dx = (1 if btn("right") else 0) - (1 if btn("left") else 0)
        dy = (1 if btn("down") else 0) - (1 if btn("up") else 0)
        cam_x += dx * spd * dt
        cam_y += dy * spd * dt
    else:
        cam_x += vx * dt                       # drift + bounce -> every direction
        cam_y += vy * dt
        if cam_x < 0.0:
            cam_x = 0.0
            vx = -vx
        elif cam_x > maxx:
            cam_x = maxx
            vx = -vx
        if cam_y < 0.0:
            cam_y = 0.0
            vy = -vy
        elif cam_y > maxy:
            cam_y = maxy
            vy = -vy
    if cam_x < 0.0:
        cam_x = 0.0
    elif cam_x > maxx:
        cam_x = maxx
    if cam_y < 0.0:
        cam_y = 0.0
    elif cam_y > maxy:
        cam_y = maxy
    # collect coins the player (screen centre) drives over; respawn when all are taken
    pcx = cam_x + W / 2.0
    pcy = cam_y + H / 2.0
    for c in coins:
        if not c[2] and abs(c[0] - pcx) < 14 and abs(c[1] - pcy) < 14:
            c[2] = True
            got += 1
            beep(660, 0.05)
    if got >= len(coins):
        _spawn_coins()


def _draw():
    icx = int(cam_x)
    icy = int(cam_y)
    # this frame's whole background: ONE window copy (no per-frame re-render)
    draw_layer(bg, icx, icy)
    # coins on top: world space -> screen space = world - camera
    pulse = 4 + (1 if int(t * 6) % 2 == 0 else 0)
    for c in coins:
        if c[2]:
            continue
        sx = c[0] - icx
        sy = c[1] - icy
        if -8 <= sx <= W + 8 and -8 <= sy <= H + 8:
            circ(sx, sy, pulse, col("yellow"))
            circb(sx, sy, pulse, col("orange"))
    # the player: a crosshair fixed at screen centre (the camera follows it)
    cxp = W // 2
    cyp = H // 2
    circb(cxp, cyp, 8, col("white"))
    line(cxp - 12, cyp, cxp + 12, cyp, col("red"))
    line(cxp, cyp - 12, cxp, cyp + 12, col("red"))
    # HUD: live FPS (validates the engine) + camera + coin count
    rect(0, 0, W, 10, col("black"))
    print("FPS %d  CAM %d,%d  COINS %d/%d"
          % (int(fps + 0.5), icx, icy, got, len(coins)), 4, 2, col("white"))
