# Brick Siege -- a top-down tank battle over a destructible brick field (#35).
# Drive your green tank with the arrows (or WASD on the device): you both MOVE and
# FACE that way. Press A to FIRE -- one bullet on screen at a time, so every shot
# counts. Blast the grey enemy tanks before they wreck your EAGLE BASE (bottom
# center) or before they get you. Brick walls (#) crumble cell-by-cell to bullets;
# steel walls (S) stop bullets cold. Clear every enemy in the wave to WIN; lose all
# your lives -- or let the base fall -- and it's GAME OVER (it restarts after a beat).
#
# AUTOPLAY (in "Make it mine") is OFF by default -- YOU play. Flip it ON and a little
# auto-pilot drives your tank (attract mode): it hunts the nearest enemy and fires.
#
# Everything is the indexed canvas + map()/mget()/mset() over the cart's tilemap
# (map.moymap): the brick/steel field is ONE native map() blit, and destroying a brick
# is just mset(cell, -1). Tanks/bullets/explosions are small lists so the per-frame
# allocation stays tiny -- MicroPython friendly (no f-strings, only the cart API).
#
# MULTIPLAYER HOOK (#7, NOT wired): player state lives in the `players` list of
# [x, y, dir, alive, cooldown, lives] structs. Today it holds ONE player (P1). A 2nd
# co-op tank is "add players.append([...]) for P2 + read btn() for a 2nd pad" -- the
# update/draw/fire/collision code already loops over `players`, so nothing else has
# to change. See _make_player / the `for p in players` loops below. Sprite tile 15
# is a blue P2 tank, ready for that day. Networking (#7) is explicitly out of scope.

TS = 16              # world tile size: each map cell is an 8x8 sheet tile at scale 2
MW = 15              # tilemap width in cells  (matches map.moymap)
MH = 15              # tilemap height in cells -> a 240x240 battlefield on the left
FIELD = MW * TS      # 240: battlefield is the left square; HUD lives in the right strip
BRICK = 8            # sheet tile id for a destructible brick wall
STEEL = 9            # sheet tile id for an indestructible steel wall
EAGLE = 10           # eagle base sprite
BROKEN = 14          # destroyed-base sprite
BULLET_TILE = 11
EXP_S = 12
EXP_B = 13

# player/enemy tank sprite tile per facing direction (0=up 1=down 2=left 3=right)
P_TANK = (0, 1, 2, 3)        # player (green)
E_TANK = (4, 5, 6, 7)        # enemy (grey/orange)

TANK = 14            # tank collision box (px) -- a touch under TS so it slips through 1-tile gaps
HALF = TANK // 2
PSPEED = 60.0        # player tank speed (px/s)
ESPEED = 42.0        # enemy tank speed (px/s)
BSPEED = 150.0       # bullet speed (px/s)
COOLDOWN = 0.35      # min seconds between a tank's shots
LIVES = 3

# direction -> (dx, dy) unit step
DV = ((0, -1), (0, 1), (-1, 0), (1, 0))

# base (eagle) cell, bottom-center of the field
BASE_CX = 7
BASE_CY = 14

# -- state ------------------------------------------------------------------
players = []         # [x, y, dir, alive, cooldown, lives]  (multiplayer hook: list)
enemies = []         # [x, y, dir, alive, cooldown, think]  think = retarget timer
bullets = []         # [x, y, dir, owner]  owner: 0=player, 1=enemy
booms = []           # [x, y, life, big]   explosion particles
spawn_q = 0          # enemies still waiting to enter the wave
spawn_t = 0.0        # countdown to the next enemy spawn
base_alive = True
score = 0
state = 0            # 0 playing, 1 won, 2 game over
state_t = 0.0        # banner / restart timer
t = 0.0
shake = 0.0


# -- map helpers ------------------------------------------------------------

def _cell_tile(cx, cy):
    # tile id in a cell, or -1 if empty / out of the field
    if cx < 0 or cx >= MW or cy < 0 or cy >= MH:
        return STEEL          # outside the field = a solid wall (keeps tanks in)
    return mget(cx, cy)


def _blocks_tank(cx, cy):
    # any non-empty wall cell stops a tank
    return _cell_tile(cx, cy) >= 0


def _tank_hits_wall(x, y):
    # does a TANKxTANK box at (x, y) overlap any wall cell?
    cx0 = int(x) // TS
    cy0 = int(y) // TS
    cx1 = int(x + TANK - 1) // TS
    cy1 = int(y + TANK - 1) // TS
    cy = cy0
    while cy <= cy1:
        cx = cx0
        while cx <= cx1:
            if _blocks_tank(cx, cy):
                return True
            cx += 1
        cy += 1
    return False


# -- spawning ---------------------------------------------------------------

def _make_player(cx):
    # one player tank, facing up, at cell column cx on the bottom row band.
    return [cx * TS + (TS - TANK) // 2, (MH - 1) * TS + (TS - TANK) // 2,
            0, True, 0.0, LIVES]


def _respawn_player(p):
    p[0] = BASE_CX * TS + (TS - TANK) // 2 - 3 * TS
    p[1] = (MH - 1) * TS + (TS - TANK) // 2
    p[2] = 0
    p[3] = True
    p[4] = 0.0


# enemy entry columns (top of the field): left, center, right -- kept clear in the map
ENEMY_COLS = (1, 7, 13)


def _spawn_enemy():
    cx = ENEMY_COLS[int(rnd(len(ENEMY_COLS)))]
    x = cx * TS + (TS - TANK) // 2
    y = (TS - TANK) // 2
    # don't stack a new enemy on top of an existing one
    for e in enemies:
        if e[3] and abs(e[0] - x) < TANK and abs(e[1] - y) < TANK:
            return False
    enemies.append([x, y, 1, True, 0.0, 0.0])   # facing down, into the field
    return True


def _wave_size():
    n = int(cfg("enemies", 6))
    if n < 1:
        n = 1
    if n > 16:
        n = 16
    return n


def _init():
    # Declare the battlefield backdrop ONCE -- the engine restores it every frame
    # ("Make it fast" habit 1), so _draw never clears the screen itself.
    background(col("dark_blue"))
    global players, enemies, bullets, booms, spawn_q, spawn_t
    global base_alive, score, state, state_t, t, shake
    # rebuild the brick/steel field from the cart's tilemap source (map.moymap),
    # so a finished round starts fresh even though we mset() bricks to empty.
    _reset_field()
    players = [_make_player(BASE_CX - 3)]      # P1 -- multiplayer: append P2 here
    enemies = []
    bullets = []
    booms = []
    score = 0
    base_alive = True
    state = 0
    state_t = 0.0
    t = 0.0
    shake = 0.0
    spawn_q = _wave_size()
    spawn_t = 0.5
    # seed a couple of enemies immediately so the field isn't empty on frame 1
    if _spawn_enemy():
        spawn_q -= 1


# The cart's tilemap is shared (mset edits persist for the run), so we snapshot the
# original layout from map.moymap ONCE and stamp it back at each round start.
_FIELD0 = None


def _snapshot_field():
    global _FIELD0
    _FIELD0 = []
    for cy in range(MH):
        for cx in range(MW):
            _FIELD0.append(mget(cx, cy))


def _reset_field():
    if _FIELD0 is None:
        _snapshot_field()
        return
    i = 0
    for cy in range(MH):
        for cx in range(MW):
            mset(cx, cy, _FIELD0[i])
            i += 1


# -- firing & collisions ----------------------------------------------------

def _fire(tank, owner):
    if tank[4] > 0.0:
        return False
    # one bullet at a time per owner side (player) -- count this owner's live shots.
    if owner == 0:
        for b in bullets:
            if b[3] == 0:
                return False
    d = tank[2]
    dx, dy = DV[d]
    # muzzle at the tank's leading edge, centered on the barrel
    cx = tank[0] + HALF + dx * HALF
    cy = tank[1] + HALF + dy * HALF
    bullets.append([cx - 2, cy - 2, d, owner])
    tank[4] = COOLDOWN
    sfx(1)
    return True


def _boom(x, y, big):
    global shake
    booms.append([x, y, 0.30 if big else 0.18, 1 if big else 0])
    if big:
        shake = 4.0


def _hit_tank(bx, by, owner):
    # a bullet at (bx,by) -- does it hit a tank on the OTHER side? returns True if so.
    # player bullets (owner 0) hit enemies; enemy bullets hit players.
    global score
    if owner == 0:
        for e in enemies:
            if e[3] and e[0] - 2 <= bx <= e[0] + TANK and e[1] - 2 <= by <= e[1] + TANK:
                e[3] = False
                _boom(e[0] + HALF, e[1] + HALF, True)
                score += 100
                sfx(2)
                return True
    else:
        for p in players:
            if p[3] and p[0] - 2 <= bx <= p[0] + TANK and p[1] - 2 <= by <= p[1] + TANK:
                _kill_player(p)
                return True
    return False


def _kill_player(p):
    p[3] = False
    p[5] -= 1
    _boom(p[0] + HALF, p[1] + HALF, True)
    sfx(2)


def _hit_base(bx, by):
    global base_alive, state, state_t
    if not base_alive:
        return False
    x0 = BASE_CX * TS
    y0 = BASE_CY * TS
    if x0 <= bx <= x0 + TS and y0 <= by <= y0 + TS:
        base_alive = False
        _boom(x0 + TS // 2, y0 + TS // 2, True)
        state = 2
        state_t = 1.6
        sfx(2)
        return True
    return False


# -- AI ---------------------------------------------------------------------

def _ai_drive(e, dt):
    # simple enemy AI: roll forward; on hitting a wall (or now and then at random)
    # pick a new direction -- biased toward the base so the swarm presses the eagle.
    e[5] -= dt
    dx, dy = DV[e[2]]
    nx = e[0] + dx * ESPEED * dt
    ny = e[1] + dy * ESPEED * dt
    stuck = _tank_hits_wall(nx, ny)
    if stuck or e[5] <= 0.0:
        _ai_retarget(e)
    else:
        e[0] = nx
        e[1] = ny
    # shoot if a wall/target is roughly ahead, or just occasionally
    if e[4] <= 0.0 and rnd(1.0) < 0.012:
        _fire(e, 1)


def _ai_retarget(e):
    e[5] = 0.4 + rnd(1.2)
    # 60% of the time aim toward the base, else wander
    bx = BASE_CX * TS
    by = BASE_CY * TS
    if rnd(1.0) < 0.6:
        if abs(bx - e[0]) > abs(by - e[1]):
            want = 3 if bx > e[0] else 2
        else:
            want = 1 if by > e[1] else 0
    else:
        want = int(rnd(4))
    # only accept the new heading if a step that way is clear; else try another
    for _i in range(4):
        dx, dy = DV[want]
        if not _tank_hits_wall(e[0] + dx * 2, e[1] + dy * 2):
            e[2] = want
            return
        want = (want + 1) % 4
    e[2] = want


def _ai_player(p, dt):
    # attract auto-pilot: pick the nearest live enemy, LINE UP on its row or column,
    # then face it and fire. Lining up first (rather than charging diagonally) is what
    # lets the shots actually connect -- a bullet only travels along an axis.
    target = None
    bestd = 1e9
    for e in enemies:
        if e[3]:
            d = abs(e[0] - p[0]) + abs(e[1] - p[1])
            if d < bestd:
                bestd = d
                target = e
    if target is None:
        return 0, 0, False
    ex, ey = target[0], target[1]
    dxp = ex - p[0]
    dyp = ey - p[1]
    aligned_col = abs(dxp) < 8         # same vertical lane -> can shoot up/down
    aligned_row = abs(dyp) < 8         # same horizontal lane -> can shoot left/right
    ddx = 0
    ddy = 0
    fire = False
    if aligned_col:
        # lined up vertically: hold still, FACE the enemy, and shoot. Returning no
        # movement keeps _move_tank from spinning the turret off-target this frame.
        p[2] = 1 if dyp > 0 else 0
        fire = True
    elif aligned_row:
        p[2] = 3 if dxp > 0 else 2
        fire = True
    else:
        # not lined up: drive to close the SMALLER gap first (so it snaps onto a
        # shared row/column quickly), then the other branch takes the shot.
        if abs(dxp) <= abs(dyp):
            ddx = 1 if dxp > 0 else -1
        else:
            ddy = 1 if dyp > 0 else -1
    return ddx, ddy, fire


# -- per-frame update -------------------------------------------------------

def _move_tank(tank, ddx, ddy, speed, dt):
    # set facing from the input, then step on whichever axis was pressed, sliding
    # along walls (try X then Y) so the tank doesn't jam on a corner.
    if ddx > 0:
        tank[2] = 3
    elif ddx < 0:
        tank[2] = 2
    elif ddy > 0:
        tank[2] = 1
    elif ddy < 0:
        tank[2] = 0
    if ddx:
        nx = tank[0] + ddx * speed * dt
        if not _tank_hits_wall(nx, tank[1]):
            tank[0] = nx
    if ddy:
        ny = tank[1] + ddy * speed * dt
        if not _tank_hits_wall(tank[0], ny):
            tank[1] = ny
    # keep tanks inside the battlefield square
    if tank[0] < 0:
        tank[0] = 0
    if tank[0] > FIELD - TANK:
        tank[0] = FIELD - TANK
    if tank[1] < 0:
        tank[1] = 0
    if tank[1] > FIELD - TANK:
        tank[1] = FIELD - TANK


def _update(dt):
    global state, state_t, spawn_q, spawn_t, t, shake
    t += dt
    if shake > 0.0:
        shake = max(0.0, shake - dt * 14.0)
    # explosions always tick (so the boom animates through a banner)
    keep = []
    for bm in booms:
        bm[2] -= dt
        if bm[2] > 0.0:
            keep.append(bm)
    booms[:] = keep

    if state != 0:
        state_t -= dt
        if state_t <= 0.0:
            _init()
        return

    auto = cfg("autoplay", 0)

    # players (the loop is the multiplayer hook -- add P2 to `players` and it just works)
    for p in players:
        if not p[3]:
            continue
        if p[4] > 0.0:
            p[4] = max(0.0, p[4] - dt)
        ddx = 0
        ddy = 0
        fire = False
        # P1 reads the one hardware pad. (P2 would read a 2nd pad here.)
        if p is players[0]:
            left = btn("left")
            right = btn("right")
            up = btn("up")
            down = btn("down")
            any_in = left or right or up or down or btnp("a")
            if auto and not any_in:
                ddx, ddy, fire = _ai_player(p, dt)
            else:
                if left:
                    ddx = -1
                elif right:
                    ddx = 1
                elif up:
                    ddy = -1
                elif down:
                    ddy = 1
                fire = btnp("a")
        if ddx or ddy:
            _move_tank(p, ddx, ddy, PSPEED, dt)
        if fire:
            _fire(p, 0)

    # all players dead but lives remain -> respawn the dead ones; none left -> over
    living = 0
    for p in players:
        if p[3]:
            living += 1
        elif p[5] > 0:
            _respawn_player(p)
            living += 1
    if living == 0:
        state = 2
        state_t = 1.6
        return

    # enemies
    for e in enemies:
        if not e[3]:
            continue
        if e[4] > 0.0:
            e[4] = max(0.0, e[4] - dt)
        _ai_drive(e, dt)

    # feed the wave: spawn the queued enemies a few at a time
    if spawn_q > 0 and _alive_enemies() < 4:
        spawn_t -= dt
        if spawn_t <= 0.0:
            if _spawn_enemy():
                spawn_q -= 1
            spawn_t = 1.4

    # bullets
    bk = []
    for b in bullets:
        dx, dy = DV[b[2]]
        b[0] += dx * BSPEED * dt
        b[1] += dy * BSPEED * dt
        bx = b[0] + 2
        by = b[1] + 2
        # off the battlefield?
        if bx < 0 or bx > FIELD or by < 0 or by > FIELD:
            continue
        # wall?
        cx = int(bx) // TS
        cy = int(by) // TS
        tile = _cell_tile(cx, cy)
        if tile == BRICK:
            mset(cx, cy, -1)          # crumble the brick cell
            _boom(cx * TS + TS // 2, cy * TS + TS // 2, False)
            sfx(0)
            continue
        if tile == STEEL:
            _boom(bx, by, False)
            sfx(0)
            continue
        # base?
        if _hit_base(bx, by):
            continue
        # tank on the other side?
        if _hit_tank(bx, by, b[3]):
            continue
        bk.append(b)
    bullets[:] = bk

    # win? all queued spawned AND none left alive
    if spawn_q == 0 and _alive_enemies() == 0:
        state = 1
        state_t = 2.0
        sfx(2)


def _alive_enemies():
    n = 0
    for e in enemies:
        if e[3]:
            n += 1
    return n


# -- draw -------------------------------------------------------------------

def _draw():
    sx = 0
    sy = 0
    if shake > 0.0:
        sx = int(rnd(shake * 2) - shake)
        sy = int(rnd(shake * 2) - shake)
    # PERF HABIT (#63/#66): the backdrop is DECLARED (background() in _init), so
    # the engine repaints it before every frame -- no cls here at all. (The HUD
    # strip below repaints its own black over it.)
    # the whole brick/steel field in ONE native map() call (#32): 15x15 cells of
    # 8x8 tiles at scale 2 -> 16px world blocks. Destroyed bricks are empty cells.
    map(0, 0, MW, MH, sx, sy, 0, 2)

    # every moving sprite (eagle + enemies + players + bullets + explosions) goes out
    # in ONE native spr_batch call (#43) instead of N per-sprite spr() calls -- they're
    # all colorkey=0, scale=2, so one batch covers them. Draw order = list order, so
    # build the list in the old draw sequence (eagle, enemies, players, bullets, booms).
    # This kills the per-sprite MP->C draw-call count that gated the explosion FPS dip.
    batch = []
    # the eagle base (or its rubble) at the fortress center
    bx = BASE_CX * TS + sx
    by = BASE_CY * TS + sy
    batch.append((EAGLE if base_alive else BROKEN, bx, by))
    # enemies
    for e in enemies:
        if e[3]:
            batch.append((E_TANK[e[2]], int(e[0]) + sx, int(e[1]) + sy))
    # players
    for p in players:
        if p[3]:
            batch.append((P_TANK[p[2]], int(p[0]) + sx, int(p[1]) + sy))
    # bullets
    for b in bullets:
        batch.append((BULLET_TILE, int(b[0]) + sx - 4, int(b[1]) + sy - 4))
    # explosions
    for bm in booms:
        batch.append((EXP_B if bm[3] else EXP_S, int(bm[0]) - 8 + sx, int(bm[1]) - 8 + sy))
    spr_batch(batch, 0, 2)

    # -- HUD (right strip) --
    hx = FIELD + 4
    rect(FIELD, 0, W - FIELD, H, col("black"))
    print("BATTLE", hx, 6, col("yellow"), 1)
    print("CITY", hx, 16, col("yellow"), 1)
    print("SCORE", hx, 36, col("light_grey"), 1)
    print(str(score), hx, 46, col("white"), 1)
    print("LEFT", hx, 64, col("light_grey"), 1)
    print(str(spawn_q + _alive_enemies()), hx, 74, col("white"), 1)
    print("LIVES", hx, 92, col("light_grey"), 1)
    p0 = players[0] if players else None
    lv = p0[5] if p0 else 0
    # draw a little tank icon per life
    i = 0
    while i < lv and i < 5:
        spr(P_TANK[0], hx + i % 2 * 18, 104 + (i // 2) * 14, 0, 1)
        i += 1
    # base status pip
    print("BASE", hx, 150, col("light_grey"), 1)
    rect(hx, 160, 10, 8, col("green") if base_alive else col("red"))

    # banners
    if state == 1:
        print("WAVE CLEAR!", FIELD // 2 - 44, FIELD // 2 - 4, col("yellow"), 1)
    elif state == 2:
        print("GAME OVER", FIELD // 2 - 36, FIELD // 2 - 4, col("red"), 1)
