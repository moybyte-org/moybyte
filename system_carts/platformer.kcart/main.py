# Hop Quest -- a single-screen platformer cartridge. Walk LEFT / RIGHT, JUMP with
# UP / A / RUN. Collect every coin to win the round (then it resets); fall off the
# bottom and you respawn at the start. Gravity + tile collision on a solid grid.
# With no input a little auto-pilot plays it (attract mode) so the GIF stays lively.
# Pure indexed canvas + btn input -- identical on host and device.

TS = 16          # tile size in pixels
# Level map: # solid, = one-way-ish platform (also solid here), C coin, G goal,
# S spawn, space empty. 20 cols x 13 rows fits the 320x240 canvas (top HUD aside).
LEVEL = [
    "                    ",
    "                    ",
    "        CCC         ",
    "      #######        ",
    "                  G ",
    "   C          #### ",
    "  ####   CC         ",
    "          ##        ",
    " S        C    C    ",
    "####    #####  #### ",
    "             C      ",
    "          ####### C ",
    "####################",
]

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


def _solid(tx, ty):
    if ty < 0 or ty >= len(LEVEL):
        return False
    row = LEVEL[ty]
    if tx < 0 or tx >= len(row):
        return True            # walls on the sides keep the player in
    return row[tx] in "#="


def _init():
    global px, py, vx, vy, coins, goal, spawn, won, t, ai_dir, ai_jump
    coins = []
    for ty in range(len(LEVEL)):
        for tx in range(len(LEVEL[ty])):
            ch = LEVEL[ty][tx]
            if ch == "C":
                coins.append([tx, ty, False])
            elif ch == "G":
                goal = (tx, ty)
            elif ch == "S":
                spawn = (tx, ty)
    px = spawn[0] * TS
    py = spawn[1] * TS
    vx = 0.0
    vy = 0.0
    won = 0.0
    t = 0.0
    ai_dir = 1
    ai_jump = 0.0


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


def _update(dt):
    global px, py, vx, vy, on_ground, won, t, ai_dir, ai_jump
    t += dt
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
    if not any_input:
        # attract mode: drift toward the nearest uncollected coin (or the goal)
        target = None
        for c in coins:
            if not c[2]:
                target = (c[0] * TS, c[1] * TS)
                break
        if target is None:
            target = (goal[0] * TS, goal[1] * TS)
        if target[0] > px + 2:
            right = True
        elif target[0] < px - 2:
            left = True
        ai_jump -= dt
        if (target[1] < py - 4 or vx == 0.0) and on_ground and ai_jump <= 0.0:
            jump = True
            ai_jump = 0.6
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
        vy = -float(cfg("jump", 230))
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
    if py > len(LEVEL) * TS + 40:
        _respawn()
    # collect coins
    for c in coins:
        if not c[2]:
            cx = c[0] * TS + TS // 2
            cy = c[1] * TS + TS // 2
            if abs((px + PW / 2) - cx) < 12 and abs((py + PH / 2) - cy) < 12:
                c[2] = True
    # reach the goal with everything collected -> win
    gx = goal[0] * TS
    gy = goal[1] * TS
    if _all_taken() and abs(px - gx) < 14 and abs(py - gy) < 18:
        won = 1.5


def _draw():
    cls(col(cfg("sky", "dark_blue")))
    # tiles
    for ty in range(len(LEVEL)):
        row = LEVEL[ty]
        for tx in range(len(row)):
            if row[tx] in "#=":
                x = tx * TS
                y = ty * TS
                rect(x, y, TS, TS, col("brown"))
                rect(x, y, TS, 3, col("dark_green"))
                rectb(x, y, TS, TS, col("dark_grey"))
    # coins
    for c in coins:
        if not c[2]:
            cx = c[0] * TS + TS // 2
            cy = c[1] * TS + TS // 2
            r = 4 + (1 if int(t * 6) % 2 == 0 else 0)
            circ(cx, cy, r, col("yellow"))
            circb(cx, cy, r, col("orange"))
    # goal flag
    gx = goal[0] * TS
    gy = goal[1] * TS
    fc = "green" if _all_taken() else "red"
    rect(gx + 6, gy - 2, 2, TS + 2, col("white"))
    rect(gx + 8, gy, 8, 6, col(fc))
    # player
    rect(int(px), int(py), PW, PH, col(cfg("hero", "pink")))
    rectb(int(px), int(py), PW, PH, col("white"))
    # HUD
    got = 0
    for c in coins:
        if c[2]:
            got += 1
    print("COINS " + str(got) + "/" + str(len(coins)), 6, 4, col("white"), 1)
    if won > 0.0:
        print("YOU WIN!", W // 2 - 32, H // 2 - 8, col("yellow"), 2)
