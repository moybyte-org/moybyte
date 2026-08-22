# Harpoon Pop -- a bubble-popping arcade cart (#79).
#
# Big bubbles bounce around the arena in fixed parabolic arcs. Walk LEFT/RIGHT and
# fire a vertical HARPOON straight up (A / UP / RUN, or TAP the screen). A hit
# bubble SPLITS into two smaller ones; the smallest pops outright. Clear every
# bubble to finish the level; get touched and you lose a life. Lose them all and
# the round resets -- your best score is kept (pmem high score).
#
# TOUCH: hold anywhere to walk toward your finger, tap to fire.
#
# TWO PLAYERS (#65) -- this cart was built for it and it is wired now: when
# players() reports 2, a second harpooner joins the same arena. Where the second
# player comes from is not this cart's business -- the T-Deck's own keyboard split
# in two (Settings -> 2 PLAYERS), or another console over the radio (#7). It reads
# btn(name, i) either way, which is the point of the unified API.
#
# Co-op: the arena is cleared TOGETHER and the score is shared, but each player
# has their own lives and their own harpoon. The round ends when everybody is out.
#
# The physics are fully DETERMINISTIC (fixed bounce heights per size, fixed split
# velocities, every random draw through rnd()) -- which is what lets two consoles
# run the same arena from one seed and agree frame for frame.
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
INVULN = 1.3               # seconds of blinking safety after a hit
WALK = 150.0               # walking speed (px/s)
OUT = 1e9                  # a dead_t nobody counts down from: out for the round

# The colours that tell two kids apart at a glance.
BODY = ("green", "indigo")
TRIM = ("white", "yellow")

# -- state (module globals ARE the cart namespace, so tests can poke them) ---
bubbles = []               # each: [x, y, vx, vy, size]
# ONE entry per player, and the whole two-player change: everything below loops
# over this list, so the single-player game is this list with one item in it and
# the co-op game is the same code with two.
#   [x, harpoon, lives, invuln, dead_t, index]     harpoon: None or [x, tip_y]
hunters = []
score = 0                  # SHARED -- you are clearing the arena together
best = 0
level = 1
clear_t = 0.0              # >0 = "level clear" banner
over = 0.0                 # >0 = game-over banner timer (autoplay auto-restarts)
blink = 0.0                # drives the invulnerability flicker

H_X = 0                    # field names for a hunter, so the code below reads
H_ROPE = 1                 # like prose instead of like an index puzzle
H_LIVES = 2
H_INV = 3
H_DEAD = 4
H_I = 5


def _speed_scale():
    return float(cfg("bubble_speed", 100)) / 100.0


def _spawn_x(i, n):
    # Alone you start in the middle; together you start a third in from each
    # wall, so nobody is standing on their friend at the whistle.
    if n < 2:
        return W / 2 - PW / 2
    return (W / 3 if i == 0 else W * 2 / 3) - PW / 2


def _spawn_level():
    # Root bubbles for the current level: more (and a touch faster) as you climb.
    # Deterministic -- positions and directions come through rnd() so a seeded
    # replay matches frame-for-frame (the #65 lockstep contract).
    global bubbles
    bubbles = []
    for hu in hunters:
        hu[H_ROPE] = None
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
    global hunters, score, best, level, clear_t, over, blink
    # ONE hunter per connected player. players() is 1 on a console nobody has
    # joined, so this is the single-player game verbatim; it becomes co-op the
    # moment a second controller exists, with no mode to pick and no menu.
    n = players()
    if n > 2:
        n = 2                        # this arena holds two
    lives = int(cfg("lives", 3))
    hunters = []
    for i in range(n):
        hunters.append([_spawn_x(i, n), None, lives, 0.0, 0.0, i])
    score = 0
    best = pmem(0)
    level = 1
    clear_t = 0.0
    over = 0.0
    blink = 0.0
    _spawn_level()


def _fire(hu):
    if hu[H_ROPE] is None and hu[H_DEAD] <= 0.0 and clear_t <= 0.0 and over <= 0.0:
        # Each hunter has their OWN rope. Sharing one between two kids would make
        # each of them feel like their own trigger was broken.
        hu[H_ROPE] = [hu[H_X] + PW / 2, float(FLOOR_Y)]


def _nearest_bubble(hu):
    # The bubble whose column is closest to this hunter -- the autopilot lines up
    # under it, then fires.
    pcx = hu[H_X] + PW / 2
    best_b = None
    best_d = 0.0
    for b in bubbles:
        d = abs(b[0] - pcx)
        if best_b is None or d < best_d:
            best_b = b
            best_d = d
    return best_b


def _flee_dir(hu, from_x):
    # Step away from x, but turn around at a wall so we never pin ourselves.
    d = -1.0 if from_x >= hu[H_X] + PW / 2 else 1.0
    if hu[H_X] <= WALL_L + 3:
        d = 1.0
    elif hu[H_X] >= WALL_R - PW - 3:
        d = -1.0
    return d


def _auto_intent(hu):
    # Attract-mode brain (survive first, then clear). Returns (move_dir, want_fire),
    # deterministic so a two-console replay agrees frame-for-frame (#65):
    #   1. a bubble about to land on us -> DODGE (unless we can pop it right now);
    #   2. our harpoon is already up    -> FLEE the busy column, don't fire;
    #   3. otherwise line up under the nearest bubble and FIRE.
    if not bubbles:
        return 0.0, False
    pcx = hu[H_X] + PW / 2
    tb = _nearest_bubble(hu)
    g = tb[0] - pcx
    aligned = abs(g) < 16
    danger = None
    for b in bubbles:
        gg = b[0] - pcx
        if b[3] > 0 and b[1] > PLAYER_TOP - 52 and abs(gg) < RADII[b[4]] + 12:
            if danger is None or abs(gg) < abs(danger):
                danger = b[0]
    can_pop_now = aligned and hu[H_ROPE] is None
    if danger is not None and not can_pop_now:
        return _flee_dir(hu, danger), False          # get out from under it
    if hu[H_ROPE] is not None:
        return _flee_dir(hu, hu[H_ROPE][0]), False   # rope busy: keep clear
    d = 0.0 if aligned else (1.0 if g > 0 else -1.0)
    return d, aligned


def _hits_hunter(hu, x, y, r):
    # circle (bubble) vs this hunter's rectangle
    cx = max(hu[H_X], min(x, hu[H_X] + PW))
    cy = max(PLAYER_TOP, min(y, PLAYER_TOP + PH))
    dx = x - cx
    dy = y - cy
    return dx * dx + dy * dy <= r * r


def _pop(i):
    # Pop bubble i: score it, and (unless it is the smallest) split it into two
    # smaller bubbles that leap apart in fixed arcs. The score is SHARED.
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


def _playing():
    n = 0
    for hu in hunters:
        if hu[H_LIVES] > 0:
            n += 1
    return n


def _lose_life(hu):
    global over
    if hu[H_INV] > 0.0 or hu[H_DEAD] > 0.0 or over > 0.0:
        return
    hu[H_LIVES] -= 1
    hu[H_ROPE] = None
    sfx(3)
    if hu[H_LIVES] <= 0:
        hu[H_DEAD] = OUT              # out for the round
        if _playing() == 0:           # ...and the round ends when EVERYBODY is
            over = 1.6
    else:
        hu[H_DEAD] = 0.7
        hu[H_INV] = INVULN


def _update(dt):
    global clear_t, over, blink, level
    blink += dt

    # -- transient states: game-over / level-clear -------------------------
    if over > 0.0:
        over -= dt
        # A press or a tap restarts immediately; autoplay restarts on its own.
        # EITHER player may restart -- btn with no player is the union of every
        # controller, which is exactly the "anyone can press it" meaning.
        if (btnp("a") or btnp("run") or btnp("b")
                or (touch() is not None and touch()[2])
                or (over <= 0.0 and cfg("autoplay", 0))):
            _init()
        return
    if clear_t > 0.0:
        clear_t -= dt
        if clear_t <= 0.0:
            level += 1
            _spawn_level()
        return

    tp = touch()
    auto = cfg("autoplay", 0)
    n = len(hunters)

    for hu in hunters:
        if hu[H_LIVES] <= 0:
            continue
        if hu[H_DEAD] > 0.0:
            hu[H_DEAD] -= dt
            if hu[H_DEAD] <= 0.0:
                hu[H_X] = _spawn_x(hu[H_I], n)
            continue
        if hu[H_INV] > 0.0:
            hu[H_INV] = max(0.0, hu[H_INV] - dt)

        # -- input: walk + fire. EACH hunter reads ITS OWN pad, so this one
        # loop drives one kid or two and the cart never learns whether the
        # second pad is half a keyboard or another console. Touch steers
        # player one only -- there is one finger and one screen.
        i = hu[H_I]
        auto_dir, auto_fire = _auto_intent(hu) if auto else (0.0, False)
        if btn("left", i) or btn("b", i):
            hu[H_X] -= WALK * dt
        elif btn("right", i):
            hu[H_X] += WALK * dt
        elif i == 0 and tp is not None and tp[3]:   # held -> walk to the finger
            want = tp[0] - PW / 2
            hu[H_X] += max(-WALK * dt, min(WALK * dt, want - hu[H_X]))
        elif auto:
            hu[H_X] += auto_dir * WALK * dt

        if btnp("a", i) or btnp("up", i) or btnp("run", i):
            _fire(hu)
        elif i == 0 and tp is not None and tp[2]:   # a tap fires
            _fire(hu)
        elif auto and auto_fire:
            _fire(hu)

        hu[H_X] = max(WALL_L, min(WALL_R - PW, hu[H_X]))

        # -- harpoon: rises from the floor; pops the first bubble it reaches --
        rope = hu[H_ROPE]
        if rope is not None:
            rope[1] -= float(cfg("harpoon_speed", 300)) * dt
            if rope[1] <= CEIL_Y:
                hu[H_ROPE] = None
            else:
                hx, tip = rope[0], rope[1]
                for bi in range(len(bubbles)):
                    b = bubbles[bi]
                    r = RADII[b[4]]
                    if abs(b[0] - hx) <= r and b[1] + r >= tip:
                        _pop(bi)
                        hu[H_ROPE] = None
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
    for hu in hunters:
        if hu[H_LIVES] <= 0 or hu[H_DEAD] > 0.0:
            continue
        for b in bubbles:
            if _hits_hunter(hu, b[0], b[1], RADII[b[4]]):
                _lose_life(hu)
                break

    # -- level cleared ------------------------------------------------------
    if not bubbles and clear_t <= 0.0 and over <= 0.0:
        clear_t = 1.2


def _bubble(b):
    x, y, r = int(b[0]), int(b[1]), RADII[b[4]]
    c = col(BUBBLE_COL[b[4]])
    circ(x, y, r, c)
    circb(x, y, r, col("white"))
    hi = max(1, r // 4)                             # a little glossy highlight
    circ(x - r // 3, y - r // 3, hi, col("white"))


def _hunter(hu):
    # blink while invulnerable; a hunter who is out for the round is not drawn
    if hu[H_LIVES] <= 0 or hu[H_DEAD] > 0.0:
        return
    if hu[H_INV] > 0.0 and int(blink * 12) % 2:
        return
    i = hu[H_I]
    body = col(BODY[i])
    trim = col(TRIM[i])
    x = int(hu[H_X])
    rect(x, PLAYER_TOP + 6, PW, PH - 6, body)
    rect(x + PW // 2 - 2, PLAYER_TOP, 4, 8, col("light_grey"))   # barrel
    rect(x + 3, PLAYER_TOP + 10, 3, 3, trim)                     # eyes
    rect(x + PW - 6, PLAYER_TOP + 10, 3, 3, trim)


def _draw():
    # walls + floor over the declared sky backdrop
    rect(0, FLOOR_Y, W, H - FLOOR_Y, col("brown"))
    rect(0, FLOOR_Y, W, 2, col("peach"))
    rect(0, 0, WALL_L, FLOOR_Y, col("dark_grey"))
    rect(WALL_R, 0, W - WALL_R, FLOOR_Y, col("dark_grey"))

    for b in bubbles:
        _bubble(b)

    # ropes first, so a hunter is drawn over their own line
    for hu in hunters:
        rope = hu[H_ROPE]
        if rope is not None:
            hx, tip = int(rope[0]), int(rope[1])
            rect(hx - 1, tip, 2, FLOOR_Y - tip, col("light_grey"))
            rect(hx - 2, tip, 4, 3, col(TRIM[hu[H_I]]))     # the harpoon tip
    for hu in hunters:
        _hunter(hu)

    # HUD: score (left), level (centre), each player's lives (right) in their
    # own colour, so two kids can find their own hearts at a glance.
    print("SCORE " + str(score), 6, 3, col("white"))
    print("LV " + str(level), W // 2 - 16, 3, col("yellow"))
    x = W - 9
    for hu in hunters:
        c = col(BODY[hu[H_I]])
        i = 0
        while i < hu[H_LIVES] and i < 5:
            rect(x - i * 7, 4, 5, 7, c)
            i += 1
        x -= (i if i else 1) * 7 + 5

    if clear_t > 0.0:
        print("LEVEL CLEAR!", W // 2 - 44, H // 2 - 4, col("green"))
    if over > 0.0:
        print("GAME OVER", W // 2 - 36, H // 2 - 12, col("red"))
        print("SCORE " + str(score), W // 2 - 40, H // 2 + 2, col("white"))
        print("BEST " + str(best), W // 2 - 32, H // 2 + 14, col("yellow"))
