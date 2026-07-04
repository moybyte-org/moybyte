# Letter Blitz -- pop the tank showing the FIND letter. Tap it on the
# touchscreen, or press the matching key on the keyboard -- either fires the
# cannon. A correct hit plays that letter's own musical note and explodes the
# tank; a wrong pick just gets a single dull "boop" and a short cooldown, no
# explosion. There are no lives and no game over, ever.
#
# The letter-tanks patrol a Battle-City-style brick maze on their own (no
# player driving) and take occasional pot-shots that chew through the bricks
# in front of them -- pure background spectacle, never aimed at each other or
# at the player's own shot.
#
# Grow the alphabet with the LETTERS stepper in "Make it mine" -- it controls
# how many letters (starting from A) are in play; TANKS/SPEED tune the
# gallery, TRACE toggles the "draw it" bonus screen that shows up every few
# pops.

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# A 5x7 dot-matrix glyph per uppercase letter ('#' = ink, '.' = transparent),
# baked into an Image the first time it's needed (see _glyph). Authoring all
# 26 now -- not just the default starting batch -- is what lets the LETTERS
# stepper honestly go all the way to Z later with no more art work.
GLYPH_ROWS = {
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "C": [".####", "#....", "#....", "#....", "#....", "#....", ".####"],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "F": ["#####", "#....", "#....", "####.", "#....", "#....", "#...."],
    "G": [".####", "#....", "#....", "#.###", "#...#", "#...#", ".####"],
    "H": ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "I": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"],
    "J": ["..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."],
    "K": ["#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "M": ["#...#", "##.##", "#.#.#", "#...#", "#...#", "#...#", "#...#"],
    "N": ["#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "Q": [".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "U": ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "V": ["#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."],
    "W": ["#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"],
    "X": ["#...#", ".#.#.", "..#..", "..#..", "..#..", ".#.#.", "#...#"],
    "Y": ["#...#", ".#.#.", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "Z": ["#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"],
}
GLYPH_W = 5
GLYPH_H = 7

# The tank body ('B') + tread rails ('T'), baked with two placeholder colors so
# _draw_tank can pal()-remap the hull per tank/state while the treads stay a
# fixed steel tone. A short barrel nub up top plus tread rails read as an
# actual vehicle instead of a bare circle. Drawn at scale 1 -- small enough to
# fit through a single maze corridor.
TANK_ROWS = [
    "......BB......",
    "......BB......",
    "TT..........TT",
    "TTBBBBBBBBBBTT",
    "TTBBBBBBBBBBTT",
    "TTBBBBBBBBBBTT",
    "TTBBBBBBBBBBTT",
    "TTBBBBBBBBBBTT",
    "TTBBBBBBBBBBTT",
    "TT..........TT",
]
TANK_IMG_W = 14
TANK_IMG_H = 10
TANK_SCALE = 1
TANK_DRAW_W = TANK_IMG_W * TANK_SCALE
TANK_DRAW_H = TANK_IMG_H * TANK_SCALE
TANK_HALF = 7    # collision half-extent against the maze walls
TANK_R = 10      # tap/key selection hit-test radius (a bit generous vs. the sprite)

# The player's stationary turret: a darker barrel ('C') on a steel base ('B'),
# baked straight to their final colors since this one never needs recoloring.
CANNON_ROWS = [
    "...CC...",
    "...CC...",
    ".BBBBBB.",
    "BBBBBBBB",
    "BBBBBBBB",
    ".BBBBBB.",
]
CANNON_IMG_W = 8
CANNON_IMG_H = 6
CANNON_SCALE = 3

BODY_COLORS = ["green", "blue", "orange", "indigo", "pink"]

# A classic two-row offset brick tile (mortar left transparent so the floor
# color shows through as the gap lines) -- the Battle-City-style destructible
# "squares" the tanks patrol between and shoot through.
BRICK_ROWS = [
    "BBBB.BBB",
    "BBBB.BBB",
    "BBBB.BBB",
    "........",
    "BB.BBBB.",
    "BB.BBBB.",
    "BB.BBBB.",
    "........",
]
BRICK_SRC = 8
CELL = 16
BRICK_SCALE = CELL // BRICK_SRC
GRID_COLS = 20
GRID_ROWS = 10
GRID_Y0 = 32     # below the top HUD

DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
TANK_BULLET_SPEED = 130.0

# Each letter gets its own fixed note (not a random chime) so, over many
# plays, the sound itself becomes a second, subtle way to recognize a letter.
LETTER_FREQ = [196.0 * (2 ** (i / 12.0)) for i in range(26)]
WRONG_FREQ = 110.0

COOLDOWN = 0.5
BULLET_TIME = 0.18
STUCK_THRESHOLD = 12.0
TRACE_EVERY = 4
TRACE_DURATION = 6.0

CANNON_X = 160
CANNON_Y = 224

walls = set()         # {(row, col)} standing bricks
tanks = []            # each: [x, y, vx, vy, letter, flinch_t, retarget_t, color_idx, fx, fy, fire_t]
tank_bullets = []      # ambient shots: [x, y, vx, vy]
wanted = "A"
bullet = None          # [tank_ref, elapsed] or None -- at most one player shot in flight
sparks = []            # [x, y, vx, vy, life, color]
cooldown_t = 0.0
stuck_t = 0.0
pop_count = 0
lifetime = 0
mode = "gallery"       # or "trace"
trace_letter = "A"
trace_t = 0.0
trace_dots = []        # list of [x, y] tap-stamps over the guide glyph

_glyph_cache = {}
_tank_img = None
_cannon_img = None
_brick_img = None


def _glyph(letter):
    img = _glyph_cache.get(letter)
    if img is None:
        img = image(GLYPH_ROWS[letter], {"#": col("white")})
        _glyph_cache[letter] = img
    return img


def _draw_glyph(letter, x, y, ink, scale=1):
    pal(col("white"), ink)
    spr(_glyph(letter), x, y, scale)
    pal()


def _tank_sprite():
    global _tank_img
    if _tank_img is None:
        _tank_img = image(TANK_ROWS, {"B": col("white"), "T": col("light_grey")})
    return _tank_img


def _cannon_sprite():
    global _cannon_img
    if _cannon_img is None:
        _cannon_img = image(CANNON_ROWS, {"B": col("light_grey"), "C": col("dark_grey")})
    return _cannon_img


def _brick_sprite():
    global _brick_img
    if _brick_img is None:
        _brick_img = image(BRICK_ROWS, {"B": col("orange")})
    return _brick_img


def _unlocked():
    n = int(cfg("letters_unlocked", 6))
    n = max(3, min(26, n))
    return ALPHABET[:n]


def _pick_letter(exclude):
    pool = _unlocked()
    choices = []
    for ch in pool:
        if ch not in exclude:
            choices.append(ch)
    if not choices:
        choices = list(pool)
    return choices[int(rnd(len(choices)))]


def _build_maze():
    walls.clear()
    for cr in range(1, GRID_ROWS - 1, 3):
        for cc in range(1, GRID_COLS - 1, 4):
            walls.add((cr, cc))
            walls.add((cr, cc + 1))
            walls.add((cr + 1, cc))
            walls.add((cr + 1, cc + 1))


def _solid(x, y):
    if x < 0 or x >= W or y < GRID_Y0 or y >= GRID_Y0 + GRID_ROWS * CELL:
        return True
    c = int(x // CELL)
    r = int((y - GRID_Y0) // CELL)
    return (r, c) in walls


def _open_spot():
    for _try in range(40):
        x = TANK_HALF + rnd(W - 2 * TANK_HALF)
        y = GRID_Y0 + TANK_HALF + rnd(GRID_ROWS * CELL - 2 * TANK_HALF)
        if not _solid(x, y):
            return x, y
    return W / 2.0, GRID_Y0 + (GRID_ROWS * CELL) / 2.0


def _retarget(t):
    speed = float(cfg("speed", 30))
    look = TANK_HALF + 3
    opts = []
    for ddx, ddy in DIRS:
        if not _solid(t[0] + ddx * look, t[1] + ddy * look):
            opts.append((ddx, ddy))
    if not opts:
        opts = list(DIRS)
    ddx, ddy = opts[int(rnd(len(opts)))]
    t[2] = ddx * speed
    t[3] = ddy * speed
    t[8] = ddx
    t[9] = ddy
    t[6] = 1.2 + rnd(1.8)


def _place(t):
    x, y = _open_spot()
    t[0] = x
    t[1] = y
    t[7] = int(rnd(len(BODY_COLORS)))
    t[10] = rnd(2.0)


def _init():
    global tanks, wanted, bullet, sparks, cooldown_t, stuck_t, pop_count
    global lifetime, mode, trace_letter, trace_t, trace_dots, tank_bullets
    _build_maze()
    n = int(cfg("tank_count", 4))
    n = max(2, min(8, n))
    tanks = []
    used = []
    for _i in range(n):
        letter = _pick_letter(used)
        used.append(letter)
        t = [0.0, 0.0, 0.0, 0.0, letter, 0.0, 0.0, 0, 0, 0, 0.0]
        _place(t)
        _retarget(t)
        tanks.append(t)
    wanted = tanks[int(rnd(len(tanks)))][4]
    tank_bullets = []
    bullet = None
    sparks = []
    cooldown_t = 0.0
    stuck_t = 0.0
    pop_count = 0
    lifetime = pmem(0)
    mode = "gallery"
    trace_letter = "A"
    trace_t = 0.0
    trace_dots = []


def _tank_fire(t):
    fx = t[8]
    fy = t[9]
    if fx == 0 and fy == 0:
        return
    bx = t[0] + fx * (TANK_HALF + 4)
    by = t[1] + fy * (TANK_HALF + 4)
    tank_bullets.append([bx, by, fx * TANK_BULLET_SPEED, fy * TANK_BULLET_SPEED])


def _move_tanks(dt):
    for t in tanks:
        nx = t[0] + t[2] * dt
        ny = t[1] + t[3] * dt
        blocked = False
        if t[2] > 0.0 and _solid(nx + TANK_HALF, t[1]):
            nx = t[0]
            blocked = True
        elif t[2] < 0.0 and _solid(nx - TANK_HALF, t[1]):
            nx = t[0]
            blocked = True
        if t[3] > 0.0 and _solid(t[0], ny + TANK_HALF):
            ny = t[1]
            blocked = True
        elif t[3] < 0.0 and _solid(t[0], ny - TANK_HALF):
            ny = t[1]
            blocked = True
        t[0] = nx
        t[1] = ny
        t[6] -= dt
        if blocked or t[6] <= 0.0:
            _retarget(t)
        t[10] -= dt
        if t[10] <= 0.0:
            _tank_fire(t)
            t[10] = 1.2 + rnd(2.2)
        if t[5] > 0.0:
            t[5] = max(0.0, t[5] - dt)


def _update_tank_bullets(dt):
    keep = []
    for b in tank_bullets:
        b[0] += b[2] * dt
        b[1] += b[3] * dt
        if _solid(b[0], b[1]):
            c = int(b[0] // CELL)
            r = int((b[1] - GRID_Y0) // CELL)
            if (r, c) in walls:
                walls.discard((r, c))
                _burst(b[0], b[1], col("orange"))
            continue  # consumed either way (brick or arena edge)
        keep.append(b)
    tank_bullets[:] = keep


def _burst(x, y, color):
    for _i in range(8):
        sparks.append([x, y, (rnd(2.0) - 1.0) * 90, (rnd(2.0) - 1.0) * 90 - 20, 0.45, color])


def _update_sparks(dt):
    keep = []
    for p in sparks:
        p[4] -= dt
        if p[4] > 0.0:
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[3] += 200.0 * dt
            keep.append(p)
    sparks[:] = keep


def _tank_at(x, y):
    for t in tanks:
        dx = x - t[0]
        dy = y - t[1]
        if dx * dx + dy * dy <= TANK_R * TANK_R:
            return t
    return None


def _tank_for_key(code):
    if not code or code < 32 or code > 126:
        return None
    ch = chr(code).upper()
    for t in tanks:
        if t[4] == ch:
            return t
    return None


def _pop(t):
    global wanted, stuck_t, pop_count, lifetime, mode, trace_letter, trace_t, trace_dots
    popped = t[4]
    _burst(t[0], t[1], col(BODY_COLORS[t[7] % len(BODY_COLORS)]))
    beep(LETTER_FREQ[ALPHABET.find(popped)], 0.16)
    others = [tk[4] for tk in tanks if tk is not t]
    t[4] = _pick_letter(others)
    _place(t)
    _retarget(t)
    t[5] = 0.0
    stuck_t = 0.0
    pop_count += 1
    lifetime += 1
    pmem(0, lifetime)
    letters_now = [tk[4] for tk in tanks]
    wanted = letters_now[int(rnd(len(letters_now)))]
    if cfg("trace_bonus", 1) and pop_count % TRACE_EVERY == 0:
        mode = "trace"
        trace_letter = popped
        trace_t = TRACE_DURATION
        trace_dots = []


def _trigger(t):
    global bullet, cooldown_t
    if t[4] == wanted:
        bullet = [t, 0.0]
    else:
        beep(WRONG_FREQ, 0.12)
        t[5] = 0.25
        cooldown_t = COOLDOWN


def _update_trace(dt):
    # touch() only ever reports a one-shot press edge (never "still held"), so
    # this is tap-to-stamp -- a dot per tap -- rather than a continuous drag.
    global trace_t, mode, trace_dots
    trace_t -= dt
    tp = touch()
    if tp is not None and tp[2]:
        trace_dots.append([tp[0], tp[1]])
    if trace_t <= 0.0:
        mode = "gallery"
        trace_dots = []


def _update(dt):
    global cooldown_t, stuck_t, bullet

    if mode == "trace":
        _update_trace(dt)
        return

    if cooldown_t > 0.0:
        cooldown_t = max(0.0, cooldown_t - dt)
    stuck_t += dt

    _move_tanks(dt)
    _update_tank_bullets(dt)
    _update_sparks(dt)

    if bullet is not None:
        bullet[1] += dt
        if bullet[1] >= BULLET_TIME:
            _pop(bullet[0])
            bullet = None

    if bullet is None and cooldown_t <= 0.0:
        target = None
        tp = touch()
        if tp is not None and tp[2]:
            target = _tank_at(tp[0], tp[1])
        if target is None:
            code = keyp()
            if code:
                target = _tank_for_key(code)
        if target is not None:
            _trigger(target)


def _draw_tank(t):
    x = int(t[0])
    y = int(t[1])
    body = col(BODY_COLORS[t[7] % len(BODY_COLORS)])
    if t[5] > 0.0:
        body = col("red")
    elif t[4] == wanted and stuck_t > STUCK_THRESHOLD:
        if int(time() / 300) % 2 == 0:
            body = col("orange")
    pal(col("white"), body)
    pal(col("light_grey"), col("dark_grey"))
    spr(_tank_sprite(), x - TANK_DRAW_W // 2, y - TANK_DRAW_H // 2, TANK_SCALE)
    pal()
    _draw_glyph(t[4], x - GLYPH_W, y - (GLYPH_H * 2) // 2, col("white"), 2)


def _draw_trace():
    cls(col("black"))
    pal(col("white"), col("dark_grey"))
    spr(_glyph(trace_letter), W // 2 - (GLYPH_W * 6) // 2, H // 2 - (GLYPH_H * 6) // 2, 6)
    pal()
    for d in trace_dots:
        circ(int(d[0]), int(d[1]), 3, col("yellow"))
    print("TAP TO DRAW!", W // 2 - 44, 16, col("white"), 1)
    print(str(int(trace_t) + 1), W // 2 - 4, H - 20, col("light_grey"), 1)


def _draw_walls():
    for (r, c) in walls:
        spr(_brick_sprite(), c * CELL, GRID_Y0 + r * CELL, BRICK_SCALE)


def _draw():
    if mode == "trace":
        _draw_trace()
        return

    cls(col("dark_blue"))
    _draw_walls()

    for b in tank_bullets:
        rect(int(b[0]) - 1, int(b[1]) - 1, 2, 2, col("light_grey"))

    for t in tanks:
        _draw_tank(t)

    if bullet is not None:
        t = bullet[0]
        frac = min(1.0, bullet[1] / BULLET_TIME)
        bx = CANNON_X + (t[0] - CANNON_X) * frac
        by = CANNON_Y + (t[1] - CANNON_Y) * frac
        tfrac = max(0.0, frac - 0.15)
        tlx = CANNON_X + (t[0] - CANNON_X) * tfrac
        tly = CANNON_Y + (t[1] - CANNON_Y) * tfrac
        line(int(tlx), int(tly), int(bx), int(by), col("orange"))
        circ(int(bx), int(by), 3, col("yellow"))

    for p in sparks:
        rect(int(p[0]), int(p[1]), 2, 2, p[5])

    spr(_cannon_sprite(), CANNON_X - CANNON_IMG_W * CANNON_SCALE // 2,
        CANNON_Y - CANNON_IMG_H * CANNON_SCALE // 2, CANNON_SCALE)

    rect(2, 2, 138, 26, col("black"))
    rectb(2, 2, 138, 26, col("light_grey"))
    print("FIND", 8, 8, col("white"), 1)
    _draw_glyph(wanted, 48, 2, col("yellow"), 4)
    print(str(lifetime) + " FOUND", W - 96, 8, col("light_grey"), 1)
