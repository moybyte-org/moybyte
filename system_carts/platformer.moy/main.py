# Hop Quest -- a single-screen platformer. Walk LEFT / RIGHT, JUMP with UP / A /
# RUN. Collect every coin to light the goal flag green, then reach it to WIN the
# round (it celebrates, then resets). Fall off the bottom and you respawn at the
# start. Gravity + tile collision on a solid grid.
#
# AUTOPLAY (in "Make it mine") is OFF by default -- YOU climb. Flip it on to watch
# a little auto-pilot clear the level (attract mode). Pure indexed canvas + btn.

TS = 8           # tile size in pixels -- one map cell, drawn 1:1 from an 8x8 tile
# Keep TS at 8: the Map and Scene editors lay the tilemap out on an 8px grid and
# place actors at raw world pixels, so a cart that draws its map at any other
# cell size shows its actors sliding off the terrain in the Scene tab.
#
# The solid terrain lives in the cart's TILEMAP (map.moymap), drawn in ONE native
# map() call instead of ~381 per-frame rect/spr draws (#32) -- and collision reads
# it back with mget(). Every solid cell is sheet tile 7, the ground brick. The
# NON-tile placements -- coin, goal, spawn -- live in the cart's SCENE
# (scenes/main.moyscene, #85), read via scene() in _init. The map is 40 cols x
# 26 rows -> the 320x240 canvas (top HUD aside).
MW = 40          # tilemap width in cells (matches map.moymap)
MH = 26          # tilemap height in cells
COIN = 3         # sheet tiles: the coin spins between COIN and COIN + 1
FLAG = 5         # sheet tiles: goal flag, FLAG dark / FLAG + 1 armed

PW = 12
PH = 14

px = 0.0
py = 0.0
vx = 0.0
vy = 0.0
on_ground = False
coins = []        # [tx, ty, taken]
goal = (0, 0)
spawn = (0, 0)
won = 0.0
t = 0.0
ai_dir = 1
ai_jump = 0.0
got = 0           # coins collected this round (drives the HUD + goal color)
sparks = []       # collect/win particles: [x, y, vx, vy, life, color]
flash = 0.0       # white pop on collect, decays
lay = None        # pre-rendered background layer (sky + terrain) -- see _build_layer
_lay_sky = None   # the sky color the layer was painted with (rebuild on change)


# The player hero is a sprite-sheet tile (sprites.moygfx): tile 0 and tile 1, both
# editable in the paint editor. The collision box stays PW x PH; the sprite is
# drawn over it.


def _hero_tile():
    h = cfg("hero", 0)               # tile id (tolerate a stale color-name config)
    try:
        return int(h)
    except (TypeError, ValueError):
        return 0


def _solid(tx, ty):
    # Collision reads the tilemap: a non-empty cell (>= 0) is solid ground. Above the top and below the bottom is
    # open air (so jumps clear the ceiling and a fall drops off-screen); the left
    # and right edges are walls so the player can't walk out of the level.
    if ty < 0 or ty >= MH:
        return False
    if tx < 0 or tx >= MW:
        return True            # walls on the sides keep the player in
    return mget(tx, ty) >= 0


def _init():
    global px, py, vx, vy, coins, goal, spawn, won, t, ai_dir, ai_jump
    global got, sparks, flash
    coins = []
    # The coins/goal/spawn are PLACED ACTORS (#85): scenes/main.moyscene holds one
    # tagged row per placement (world-space pixels), read once here -- change the
    # level by moving actors, not by editing code.
    for a in scene():
        if a.tag == "coin":
            coins.append([a.x // TS, a.y // TS, False])
        elif a.tag == "goal":
            goal = (a.x // TS, a.y // TS)
        elif a.tag == "spawn":
            spawn = (a.x // TS, a.y // TS)
    px = spawn[0] * TS
    py = spawn[1] * TS
    vx = 0.0
    vy = 0.0
    won = 0.0
    t = 0.0
    ai_dir = 1
    ai_jump = 0.0
    got = 0
    sparks = []
    flash = 0.0
    _build_layer()                     # (re)paint the static background once per round


def _build_layer():
    # PERF HABIT (#66): the terrain never changes during play, so re-running the
    # full-screen map() every frame (~10ms on device) was pure waste. Paint the
    # STATIC background -- sky + terrain -- into a screen-sized layer ONCE; each
    # frame _draw stamps it back with draw_layer (one flat copy that also erases
    # last frame's sprites, so no cls() is needed either). A layer speaks the
    # whole drawing API (lay.cls / lay.map / lay.rect ...), and it's built once
    # per run, so the cost lives at start, not in the frame.
    global lay, _lay_sky
    if lay is None:
        lay = make_layer(W, H)         # allocate once per run (repaints reuse it)
    _lay_sky = cfg("sky", "dark_blue")
    lay.cls(col(_lay_sky))
    lay.map(0, 0, MW, MH, 0, 0, -1)


def _respawn():
    global px, py, vx, vy
    px = spawn[0] * TS
    py = spawn[1] * TS
    vx = 0.0
    vy = 0.0


def _hit_solid(nx, ny):
    # any solid tile overlapping the player box at (nx, ny)
    tx0 = int(nx) // TS
    tx1 = int(nx + PW - 1) // TS
    ty0 = int(ny) // TS
    ty1 = int(ny + PH - 1) // TS
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            if _solid(tx, ty):
                return True
    return False


def _all_taken():
    for c in coins:
        if not c[2]:
            return False
    return True


def _burst(x, y, n, color):
    for _i in range(n):
        sparks.append([x, y, (rnd(2.0) - 1.0) * 70, -rnd(80) - 10, 0.5, color])


def _update(dt):
    global px, py, vx, vy, on_ground, won, t, ai_dir, ai_jump, got, flash
    t += dt
    if flash > 0.0:
        flash = max(0.0, flash - dt)
    # particles always tick (so the win burst animates during the banner)
    keep = []
    for p in sparks:
        p[4] -= dt
        if p[4] > 0.0:
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[3] += 220.0 * dt
            keep.append(p)
    sparks[:] = keep
    if won > 0.0:
        won -= dt
        if won <= 0.0:
            _init()          # next round
        return
    move = float(cfg("speed", 90))
    left = btn("left")
    right = btn("right")
    jump = btn("up") or btn("a") or btn("run")
    any_input = left or right or jump
    attract = cfg("autoplay", 0) and not any_input
    if attract:
        # Attract auto-pilot: head toward the goal column and HOP whenever the
        # staircase rises ahead. The walk direction always pushes toward the goal
        # so it keeps advancing (no in-place bounce); the lateral momentum during
        # the hop carries the hero onto the next tread, so coin-by-coin it climbs
        # the whole staircase and reaches the goal -> the round completes + resets.
        gx = goal[0] * TS
        if gx > px + 2:
            right = True
        elif gx < px - 2:
            left = True
        ai_jump -= dt
        ddir = 1 if right else (-1 if left else 0)
        wall_ahead = False
        if on_ground and ddir != 0:
            edge = px + (PW if ddir > 0 else 0)
            ahead_tx = int(edge + ddir * 3) // TS
            for ty in (int(py), int(py + PH // 2), int(py + PH - 1)):
                if _solid(ahead_tx, ty // TS):
                    wall_ahead = True
                    break
        if wall_ahead and on_ground and ai_jump <= 0.0:
            jump = True
            ai_jump = 0.4
    vx = 0.0
    if left:
        vx = -move
    if right:
        vx = move
    # gravity
    vy += 600.0 * dt
    if vy > 360.0:
        vy = 360.0
    if jump and on_ground:
        # The auto-pilot uses a fixed, tuned hop so its climb is reliable no matter
        # what JUMP power the kid has dialled in; a real player gets cfg("jump").
        vy = -(210.0 if attract else float(cfg("jump", 230)))
        on_ground = False
    # move X then Y with tile collision
    nx = px + vx * dt
    if not _hit_solid(nx, py):
        px = nx
    else:
        while vx != 0.0 and not _hit_solid(px + (1 if vx > 0 else -1), py):
            px += 1 if vx > 0 else -1
    on_ground = False
    ny = py + vy * dt
    if not _hit_solid(px, ny):
        py = ny
    else:
        step = 1 if vy > 0 else -1
        while not _hit_solid(px, py + step):
            py += step
        if vy > 0:
            on_ground = True
        vy = 0.0
    # fall off the bottom -> respawn
    if py > MH * TS + 40:
        _respawn()
    # collect coins
    for c in coins:
        if not c[2]:
            cx = c[0] * TS + 8            # center of the 8px coin drawn at 2x
            cy = c[1] * TS + 8
            if abs((px + PW / 2) - cx) < 12 and abs((py + PH / 2) - cy) < 12:
                c[2] = True
                got += 1
                flash = 0.12
                _burst(cx, cy, 5, "yellow")
    # reach the goal with everything collected -> win
    gx = goal[0] * TS
    gy = goal[1] * TS
    if _all_taken() and abs(px - gx) < 14 and abs(py - gy) < 18:
        if won <= 0.0:
            _burst(gx + 8, gy, 14, "green")   # confetti on the win
        won = 1.5


def _draw():
    if flash > 0.0:
        # collect flash: a 1-2 frame white pop -- keep the immediate path so the
        # whole sky really flashes (the layer below is the normal frame).
        cls(col("white"))
        map(0, 0, MW, MH, 0, 0, -1)
    else:
        if cfg("sky", "dark_blue") != _lay_sky:
            _build_layer()             # "Make it mine" sky change -> repaint once
        # The whole background -- sky + the static terrain -- in ONE flat copy
        # (#66 PERF HABIT, see _build_layer). No cls() needed: the opaque layer
        # stamp erases last frame's sprites for free.
        draw_layer(lay, 0, 0)
    # coins (a two-frame spin so they read as collectible)
    for c in coins:
        if not c[2]:
            spr(COIN + (int(t * 6) % 2), c[0] * TS, c[1] * TS, 0, 2)
    # particles
    for p in sparks:
        pix(int(p[0]), int(p[1]), col(p[5]))
    # goal flag (green once every coin is collected)
    spr(FLAG + (1 if _all_taken() else 0), goal[0] * TS, goal[1] * TS, 0, 2)
    # player: an editable 8x8 hero tile at 2x (16px), centered on the PWxPH box
    spr(_hero_tile(), int(px) + PW // 2 - 8, int(py) + PH - 16, 0, 2)
    # HUD
    print("COINS " + str(got) + "/" + str(len(coins)), 6, 4, col("white"), 1)
    if won > 0.0:
        print("YOU WIN!", W // 2 - 32, H // 2 - 8, col("yellow"), 2)
