# Harpoon Pop -- a bubble-popping arcade cart (#79, stage 1: single-player seed).
#
# Big bubbles bounce around the arena in fixed parabolic arcs. Walk LEFT/RIGHT and
# fire a vertical HARPOON straight up (A / UP / RUN, or TAP the screen). A hit
# bubble SPLITS into two smaller ones; the smallest pops outright. Clear every
# bubble to finish the level; get touched and you lose a life. Lose them all and
# the round resets -- your best score is kept (pmem high score).
#
# TOUCH: hold anywhere to walk toward your finger, tap to fire.
#
# The physics are fully DETERMINISTIC (fixed bounce heights per size, fixed split
# velocities, every random draw through rnd()) -- that is what makes this the
# natural proof cart for shared-screen and two-console lockstep play (#65 / #7):
# stage 2/3 become a transport change, not a rewrite.
#
# AUTOPLAY (in "Make it mine") is OFF by default -- YOU play. Flip it on to watch
# the console play itself (attract mode / the demo GIF path).

# -- arena geometry ----------------------------------------------------------
HUD_H = 14                 # top strip: score / level / lives, drawn over the sky
FLOOR_Y = H - 16           # top of the floor slab
CEIL_Y = HUD_H + 2         # harpoons vanish (and bubbles arc below) this line
WALL_L = 3
WALL_R = W - 3
PW = 16                    # player width
PH = 18                    # player height
PLAYER_TOP = FLOOR_Y - PH

# -- bubble tuning (indexed by size 0..3; 0 = smallest, pops outright) -------
RADII = (5, 9, 14, 20)
BUBBLE_COL = ("pink", "yellow", "orange", "red")
BOUNCE = (150.0, 175.0, 200.0, 225.0)   # upward speed at a floor bounce -> fixed arc height
HSPEED = (58.0, 50.0, 44.0, 38.0)       # base horizontal drift (scaled by cfg SPEED)
POINTS = (30, 20, 15, 10)               # score per pop (smaller = worth more)
GRAVITY = 260.0
POP_VY = 130.0             # upward leap a fresh split child gets
INVULN = 1.3              # seconds of blinking safety after a hit

# -- state (module globals ARE the cart namespace, so tests can poke them) ---
bubbles = []               # each: [x, y, vx, vy, size]
harpoon = None             # None, or [x, tip_y] (one on screen at a time)
px = 0.0                   # player left edge
score = 0
best = 0
lives = 3
level = 1
invuln = 0.0
dead_t = 0.0               # >0 = brief "you were hit" freeze
clear_t = 0.0              # >0 = "level clear" banner
over = 0.0                 # >0 = game-over banner timer (autoplay auto-restarts)
blink = 0.0                # drives the invulnerability flicker


def _speed_scale():
    return float(cfg("bubble_speed", 100)) / 100.0


def _spawn_level():
    # Root bubbles for the current level: more (and a touch faster) as you climb.
    # Deterministic -- positions and directions come through rnd() so a seeded
    # replay matches frame-for-frame (the #65 lockstep contract).
    global bubbles, harpoon
    bubbles = []
    harpoon = None
    n = int(cfg("start_bubbles", 2)) + (level - 1) // 2
    n = max(1, min(n, 6))
    # Bubble SIZE ramps with the level so level 1 is gentle (a medium bubble
    # cascades to ~7 pops) and the huge size-3 bubbles arrive once you have your
    # footing. Keeps all four sizes in play without a brutal first screen.
    rsize = 2 if level < 3 else 3
    span = (WALL_R - WALL_L) / (n + 1)
    for i in range(n):
        x = WALL_L + span * (i + 1) + (rnd(20) - 10)
        vdir = -1.0 if rnd(1.0) < 0.5 else 1.0
        vx = vdir * HSPEED[rsize] * _speed_scale() * (1.0 + (level - 1) * 0.06)
        bubbles.append([x, CEIL_Y + 20 + rnd(20), vx, 0.0, rsize])


def _init():
    background(col("dark_blue"))     # the arena sky, restored by the engine each frame
    global px, score, best, lives, level, invuln, dead_t, clear_t, over, blink
    px = W / 2 - PW / 2
    score = 0
    best = pmem(0)
    lives = int(cfg("lives", 3))
    level = 1
    invuln = 0.0
    dead_t = 0.0
    clear_t = 0.0
    over = 0.0
    blink = 0.0
    _spawn_level()


def _fire():
    global harpoon
    if harpoon is None and dead_t <= 0.0 and clear_t <= 0.0 and over <= 0.0:
        harpoon = [px + PW / 2, float(FLOOR_Y)]


def _nearest_bubble():
    # The bubble whose column is closest to the player -- the autopilot lines up
    # under it, then fires.
    pcx = px + PW / 2
    best_b = None
    best_d = 0.0
    for b in bubbles:
        d = abs(b[0] - pcx)
        if best_b is None or d < best_d:
            best_b = b
            best_d = d
    return best_b


def _flee_dir(from_x):
    # Step away from x, but turn around at a wall so we never pin ourselves.
    d = -1.0 if from_x >= px + PW / 2 else 1.0
    if px <= WALL_L + 3:
        d = 1.0
    elif px >= WALL_R - PW - 3:
        d = -1.0
    return d


def _auto_intent():
    # Attract-mode brain (survive first, then clear). Returns (move_dir, want_fire),
    # deterministic so a two-console replay agrees frame-for-frame (#65):
    #   1. a bubble about to land on us -> DODGE (unless we can pop it right now);
    #   2. our harpoon is already up    -> FLEE the busy column, don't fire;
    #   3. otherwise line up under the nearest bubble and FIRE.
    if not bubbles:
        return 0.0, False
    pcx = px + PW / 2
    tb = _nearest_bubble()
    g = tb[0] - pcx
    aligned = abs(g) < 16
    danger = None
    for b in bubbles:
        gg = b[0] - pcx
        if b[3] > 0 and b[1] > PLAYER_TOP - 52 and abs(gg) < RADII[b[4]] + 12:
            if danger is None or abs(gg) < abs(danger):
                danger = b[0]
    can_pop_now = aligned and harpoon is None
    if danger is not None and not can_pop_now:
        return _flee_dir(danger), False          # get out from under it
    if harpoon is not None:
        return _flee_dir(harpoon[0]), False      # rope busy: keep clear while it rises
    d = 0.0 if aligned else (1.0 if g > 0 else -1.0)
    return d, aligned


def _hits_player(x, y, r):
    # circle (bubble) vs the player's rectangle
    cx = max(px, min(x, px + PW))
    cy = max(PLAYER_TOP, min(y, PLAYER_TOP + PH))
    dx = x - cx
    dy = y - cy
    return dx * dx + dy * dy <= r * r


def _pop(i):
    # Pop bubble i: score it, and (unless it is the smallest) split it into two
    # smaller bubbles that leap apart in fixed arcs.
    global score, best
    b = bubbles[i]
    x, y, size = b[0], b[1], b[4]
    score += POINTS[size]
    if score > best:
        best = score
        pmem(0, best)
    sfx(2)
    del bubbles[i]
    if size > 0:
        cs = size - 1
        sp = HSPEED[cs] * _speed_scale()
        bubbles.append([x, y, -sp, -POP_VY, cs])
        bubbles.append([x, y, sp, -POP_VY, cs])


def _lose_life():
    global lives, invuln, dead_t, over, harpoon
    if invuln > 0.0 or dead_t > 0.0 or over > 0.0:
        return
    lives -= 1
    harpoon = None
    sfx(3)
    if lives <= 0:
        over = 1.6
    else:
        dead_t = 0.7
        invuln = INVULN


def _update(dt):
    global px, harpoon, invuln, dead_t, clear_t, over, blink, level
    blink += dt

    # -- transient states: game-over / level-clear / hit-freeze ------------
    if over > 0.0:
        over -= dt
        # a press or a tap restarts immediately; autoplay restarts on its own
        if (btnp("a") or btnp("run") or btnp("b")
                or (touch() is not None and touch()[2])
                or (over <= 0.0 and cfg("autoplay", 0))):
            _init()
        return
    if clear_t > 0.0:
        clear_t -= dt
        if clear_t <= 0.0:
            _spawn_level()
        return
    if dead_t > 0.0:
        dead_t -= dt
        if dead_t <= 0.0:
            px = W / 2 - PW / 2
        return
    if invuln > 0.0:
        invuln = max(0.0, invuln - dt)

    # -- input: walk + fire (keyboard, touch, or the attract-mode autopilot) --
    speed = 150.0
    tp = touch()
    auto = cfg("autoplay", 0)
    auto_dir, auto_fire = _auto_intent() if auto else (0.0, False)
    if btn("left") or btn("b"):
        px -= speed * dt
    elif btn("right"):
        px += speed * dt
    elif tp is not None and tp[3]:                 # held -> walk toward the finger
        want = tp[0] - PW / 2
        px += max(-speed * dt, min(speed * dt, want - px))
    elif auto:
        px += auto_dir * speed * dt

    if btnp("a") or btnp("up") or btnp("run"):
        _fire()
    elif tp is not None and tp[2]:                 # a tap fires
        _fire()
    elif auto and auto_fire:
        _fire()

    px = max(WALL_L, min(WALL_R - PW, px))

    # -- harpoon: rises from the floor; pops the first bubble it reaches ----
    if harpoon is not None:
        harpoon[1] -= float(cfg("harpoon_speed", 300)) * dt
        if harpoon[1] <= CEIL_Y:
            harpoon = None
        else:
            hx, tip = harpoon[0], harpoon[1]
            for i in range(len(bubbles)):
                b = bubbles[i]
                r = RADII[b[4]]
                if abs(b[0] - hx) <= r and b[1] + r >= tip:
                    _pop(i)
                    harpoon = None
                    break

    # -- bubble physics: fixed parabolic arcs, wall + floor bounces ---------
    for b in bubbles:
        b[3] += GRAVITY * dt
        b[0] += b[2] * dt
        b[1] += b[3] * dt
        r = RADII[b[4]]
        if b[0] - r < WALL_L:
            b[0] = WALL_L + r
            b[2] = abs(b[2])
        elif b[0] + r > WALL_R:
            b[0] = WALL_R - r
            b[2] = -abs(b[2])
        if b[1] + r >= FLOOR_Y:
            b[1] = FLOOR_Y - r
            b[3] = -BOUNCE[b[4]]                    # fixed upward speed -> same arc height
        if b[4] == 0 and b[1] - r < CEIL_Y:         # tiny fast ones can reach the top: reflect
            b[1] = CEIL_Y + r
            b[3] = abs(b[3])

    # -- lose a life on contact --------------------------------------------
    for b in bubbles:
        if _hits_player(b[0], b[1], RADII[b[4]]):
            _lose_life()
            break

    # -- level cleared ------------------------------------------------------
    if not bubbles and clear_t <= 0.0 and over <= 0.0:
        clear_t = 1.2
        level += 1


def _bubble(b):
    x, y, r = int(b[0]), int(b[1]), RADII[b[4]]
    c = col(BUBBLE_COL[b[4]])
    circ(x, y, r, c)
    circb(x, y, r, col("white"))
    hi = max(1, r // 4)                             # a little glossy highlight
    circ(x - r // 3, y - r // 3, hi, col("white"))


def _draw():
    # walls + floor over the declared sky backdrop
    rect(0, FLOOR_Y, W, H - FLOOR_Y, col("brown"))
    rect(0, FLOOR_Y, W, 2, col("peach"))
    rect(0, 0, WALL_L, FLOOR_Y, col("dark_grey"))
    rect(WALL_R, 0, W - WALL_R, FLOOR_Y, col("dark_grey"))

    for b in bubbles:
        _bubble(b)

    if harpoon is not None:
        hx, tip = int(harpoon[0]), int(harpoon[1])
        rect(hx - 1, tip, 2, FLOOR_Y - tip, col("light_grey"))
        rect(hx - 2, tip, 4, 3, col("yellow"))      # the harpoon tip

    # the player -- a little cannon; blink while invulnerable
    show = dead_t <= 0.0 and (invuln <= 0.0 or int(blink * 12) % 2 == 0)
    if show:
        px_i = int(px)
        rect(px_i, PLAYER_TOP + 6, PW, PH - 6, col("green"))
        rect(px_i + PW // 2 - 2, PLAYER_TOP, 4, 8, col("light_grey"))  # barrel
        rect(px_i + 3, PLAYER_TOP + 10, 3, 3, col("white"))           # eyes
        rect(px_i + PW - 6, PLAYER_TOP + 10, 3, 3, col("white"))

    # HUD: score (left), level (centre), lives as hearts (right)
    print("SCORE " + str(score), 6, 3, col("white"))
    print("LV " + str(level), W // 2 - 16, 3, col("yellow"))
    for i in range(int(cfg("lives", 3))):
        c = "red" if i < lives else "dark_grey"
        rect(W - 12 - i * 11, 4, 8, 7, col(c))

    if clear_t > 0.0:
        print("LEVEL CLEAR!", W // 2 - 44, H // 2 - 4, col("green"))
    if over > 0.0:
        print("GAME OVER", W // 2 - 36, H // 2 - 12, col("red"))
        print("SCORE " + str(score), W // 2 - 40, H // 2 + 2, col("white"))
        print("BEST " + str(best), W // 2 - 32, H // 2 + 14, col("yellow"))
