# Letter Blitz -- pop the tank showing the FIND letter. Tap it on the
# touchscreen, or press the matching key on the keyboard: the turret SWIVELS
# to aim at the picked tank (lock-on reticle blinking) and fires when lined
# up. A correct hit freezes the action for a heartbeat, blooms rings and
# sparks, floats the letter up out of the explosion and plays that letter's
# own two-note arpeggio; a wrong pick just gets a single dull "boop" and a
# short visible reload, no explosion. There are no lives and no game over,
# ever. Every 10 finds earns a little fanfare.
#
# The letter-tanks patrol a Battle-City-style maze -- random mirrored wall
# segments, different every arena, with the outer lane always open -- on
# their own (no player driving), lane-snapped to the 16px grid like the
# classic, treads and barrel turned the way they actually move and shoot.
# Their pot-shots chew through the bricks, and a tank that runs into a wall
# blasts it open and drives on through -- pure background spectacle, never
# aimed at each other or at the player's own shot. The maze rebuilds itself
# fresh behind every trace bonus, so the arena never decays into an empty
# field.
#
# Teaching hooks: each letter has its own fixed musical note; the note plays
# again softly whenever that letter becomes the FIND target, and replays as a
# hint when the kid has been stuck a while (the matching tank blinks too).
#
# Grow the alphabet with the LETTERS stepper in "Make it mine" -- it controls
# how many letters (starting from A) are in play; TANKS/SPEED tune the
# gallery, TRACE toggles the "draw it" bonus screen that shows up every few
# pops: the kid DRAWS the letter with a finger (crayon strokes follow the
# drag; ink-covering strokes glow yellow, each newly covered cell chirps a
# rising note, and tracing the whole letter brings the GREAT! cheer early --
# but it always ends in GREAT!, there is no way to fail).

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

# Tank bodies ('B') + tread rails ('T'), baked with two placeholder colors so
# _draw_tank can pal()-remap the hull per tank/state while the treads stay a
# fixed steel tone. Two orientations: treads on the sides when driving
# vertically, on top and bottom when driving horizontally -- plus a barrel
# rect drawn toward the actual facing, so the pot-shots visibly come out of
# the gun. Drawn at scale 1 -- small enough to fit a single maze corridor.
TANK_V_ROWS = [
    "TT..........TT",
    "TTBBBBBBBBBBTT",
    "TTBBBBBBBBBBTT",
    "TTBBBBBBBBBBTT",
    "TTBBBBBBBBBBTT",
    "TTBBBBBBBBBBTT",
    "TTBBBBBBBBBBTT",
    "TTBBBBBBBBBBTT",
    "TTBBBBBBBBBBTT",
    "TT..........TT",
]
TANK_H_ROWS = [
    "..TTTTTTTTTT..",
    "..TTTTTTTTTT..",
    "BBBBBBBBBBBBBB",
    "BBBBBBBBBBBBBB",
    "BBBBBBBBBBBBBB",
    "BBBBBBBBBBBBBB",
    "BBBBBBBBBBBBBB",
    "BBBBBBBBBBBBBB",
    "..TTTTTTTTTT..",
    "..TTTTTTTTTT..",
]
TANK_IMG_W = 14
TANK_IMG_H = 10
TANK_SCALE = 1
TANK_DRAW_W = TANK_IMG_W * TANK_SCALE
TANK_DRAW_H = TANK_IMG_H * TANK_SCALE
TANK_HALF = 7    # collision half-extent against the maze walls
TANK_R = 10      # tap/key selection hit-test radius (a bit generous vs. the sprite)

# The player's turret BASE -- the barrel isn't baked into the sprite anymore:
# it's drawn as a thick line along the live `aim` vector, so the turret can
# visibly swivel toward the picked tank before it fires.
CANNON_ROWS = [
    ".BBBBBB.",
    "BBBBBBBB",
    "BBBBBBBB",
    ".BBBBBB.",
]
CANNON_IMG_W = 8
CANNON_IMG_H = 4
CANNON_SCALE = 3
BARREL_LEN = 16
AIM_SPEED = 7.0          # aim-lerp rate: a full swing settles in ~0.2s -- fast,
                         # but slow enough that the swivel READS as taking aim
AIM_LOCK = 0.995         # fire when dot(aim, target) crosses this (~5 degrees)

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
# The console always overlays its 18px system bar on a running cart (#46), so
# the cart's own HUD lives in the 20..48 band and the maze starts below BOTH.
HUD_Y = 20
HUD_H = 28
GRID_Y0 = 48

# This is a TYPING game -- it calls textmode(True) so every letter reaches keyp()
# cleanly. But that means the console's usual game exit (hold BACKSPACE) can't
# reach it: in text mode BACKSPACE is a plain typed key (delete), and the T-Deck
# keyboard has no autorepeat, so a held BACKSPACE never accumulates the hold. A
# textmode(True) cart MUST provide its OWN exit (see docs/moy_cart_api.md). Ours
# is a tap-anytime X button in the top-right corner -- above the HUD (row < 20),
# clear of the maze (which starts at GRID_Y0), and touch-only so it never depends
# on the keyboard. Tapping it calls quit() to return to the launcher.
EXIT_W = 16
EXIT_H = 14
EXIT_PAD = 3


def _exit_rect():
    return W - EXIT_W - EXIT_PAD, EXIT_PAD, EXIT_W, EXIT_H


def _exit_tapped(tp):
    if tp is None or not tp[2]:
        return False
    ex, ey, ew, eh = _exit_rect()
    return ex <= tp[0] < ex + ew and ey <= tp[1] < ey + eh


def _draw_exit():
    ex, ey, ew, eh = _exit_rect()
    rect(ex, ey, ew, eh, col("black"))
    rectb(ex, ey, ew, eh, col("light_grey"))
    line(ex + 4, ey + 3, ex + ew - 5, ey + eh - 4, col("white"))
    line(ex + ew - 5, ey + 3, ex + 4, ey + eh - 4, col("white"))


DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
TANK_BULLET_SPEED = 130.0

# Each letter gets its own fixed note (not a random chime) so, over many
# plays, the sound itself becomes a second, subtle way to recognize a letter.
LETTER_FREQ = [196.0 * (2 ** (i / 12.0)) for i in range(26)]
WRONG_FREQ = 110.0

COOLDOWN = 0.5
BULLET_TIME = 0.18
FREEZE_TIME = 0.12       # hitstop: the world holds its breath on a correct pop
STUCK_THRESHOLD = 8.0    # seconds before the blink + note hints kick in
HINT_EVERY = 2.5
MILESTONE_EVERY = 10
TRACE_EVERY = 4

# Arena moods: (floor, brick) palette indices. The mood advances on every
# arena rebuild, so each trace bonus / boss ends with a "new level" feel --
# day, dusk, night, forest. Indexed color makes this nearly free.
MOODS = ((1, 9), (2, 15), (0, 12), (3, 4))

# Boss letter: every 26 finds one DOUBLE-SIZE tank rolls in (the walls come
# down for it), takes 3 hits, chants its letter bigger on each one.
BOSS_EVERY = 26
BOSS_HP = 3
BOSS_HALF = 13

# Best-streak record board, entered arcade-style with initials the kid taps
# or types -- the most 90s ritual there is, and it's secretly a letter lesson.
RECORD_MIN_STREAK = 3
RECORD_TIMEOUT = 25.0    # no dead ends for a small kid: auto-saves and returns

STAR_ROWS = ["..#..", ".###.", "#####", ".###.", "..#.."]
TRACE_DURATION = 10.0    # drawing takes longer than tapping; finishing ends it early
TRACE_GREAT = 1.2        # the last stretch of the bonus is the GREAT! cheer
TRACE_SCALE = 14         # guide glyph scale: 70x98px -- finger-sized cells to trace

CANNON_X = 160
CANNON_Y = 224

walls = set()          # {(row, col)} standing bricks
tanks = []             # each: [x, y, vx, vy, letter, flinch_t, retarget_t, color_idx, fx, fy, fire_t]
tank_bullets = []      # ambient shots: [x, y, vx, vy]
wanted = "A"
bullet = None          # [tank_ref, elapsed] or None -- at most one player shot in flight
aiming = None          # tank the turret is swiveling toward (fires when locked)
aim = [0.0, -1.0]      # barrel direction (unit vector; idle points up)
boss = None            # the big letter tank: same layout as a tank + [11] = hp
streak = 0             # consecutive correct picks (wrong pick ends the run)
best_streak = 0        # pmem(1); initials in pmem(2..4)
initials = ""
rec_letters = []       # initials being entered on the record screen
rec_t = 0.0
mood_idx = 0           # index into MOODS; advances on every arena rebuild
sparks = []            # [x, y, vx, vy, life, color]
rings = []             # [x, y, radius, life] expanding pop/spawn circles
rising = []            # [letter, x, y, life] the popped letter floating up
fx_text = []           # [msg, cx, y, life, color] floating celebration text
melody = []            # [delay, freq, dur] tiny note sequencer (arpeggios, fanfares)
cooldown_t = 0.0
freeze_t = 0.0
stuck_t = 0.0
hint_t = 0.0
wanted_flash_t = 0.0
pop_count = 0
lifetime = 0
mode = "gallery"       # or "trace"
trace_letter = "A"
trace_t = 0.0
trace_strokes = []     # crayon strokes: each is a list of [x, y, on_ink] points
trace_covered = set()  # glyph ink cells the strokes have touched (progress)
trace_was_held = False
trace_done = False

# Sprite caches, keyed by EVERYTHING that changes the pixels -- letter, ink
# color, scale, orientation. "Make it fast" rule this cart models: never wrap
# spr() in pal() on the play path. pal() invalidates the engine's baked sprite
# cache, so every draw re-bakes the sprite pixel by pixel (that alone cost this
# cart most of its frame budget); baking a tinted copy ONCE per color and
# reusing it keeps every frame a cheap cached blit. Scale is in the key because
# the engine caches one baked scale per Image -- drawing one Image at two
# scales every frame would re-bake it twice a frame.
_glyph_cache = {}      # (letter, ink, scale) -> Image
_tank_cache = {}       # (horizontal, body, scale) -> Image (treads baked dark)
_cannon_imgs = [None, None]   # [ready, reloading]
_brick_cache = {}      # brick color -> Image
_star_img = None

# The floor + standing bricks live in a full-screen #54 layer: one window-copy
# per frame instead of cls + ~54 brick sprites (draw-call count is the device
# fps bottleneck, and per-sprite pixels are the web view's bandwidth hog). The
# layer is redrawn only when a brick actually changes (_bg_dirty).
_bg = None
_bg_dirty = True

# HUD text cache: [found_str, for_lifetime, best_str, for_best, for_initials]
_hud_strs = ["", -1, "", -1, ""]


def _glyph(letter, ink, scale):
    key = (letter, ink, scale)
    img = _glyph_cache.get(key)
    if img is None:
        img = image(GLYPH_ROWS[letter], {"#": ink})
        _glyph_cache[key] = img
    return img


def _draw_glyph(letter, x, y, ink, scale=1):
    spr(_glyph(letter, ink, scale), x, y, scale)


def _tank_sprite(horizontal, body, scale):
    key = (horizontal, body, scale)
    img = _tank_cache.get(key)
    if img is None:
        rows = TANK_H_ROWS if horizontal else TANK_V_ROWS
        img = image(rows, {"B": body, "T": col("dark_grey")})
        _tank_cache[key] = img
    return img


def _cannon_sprite(reloading):
    i = 1 if reloading else 0
    if _cannon_imgs[i] is None:
        ink = col("dark_grey") if reloading else col("light_grey")
        _cannon_imgs[i] = image(CANNON_ROWS, {"B": ink})
    return _cannon_imgs[i]


def _brick_sprite(ink):
    img = _brick_cache.get(ink)
    if img is None:
        img = image(BRICK_ROWS, {"B": ink})
        _brick_cache[ink] = img
    return img


def _star_sprite():
    global _star_img
    if _star_img is None:
        _star_img = image(STAR_ROWS, {"#": col("yellow")})
    return _star_img


def _brick_color():
    return col(MOODS[mood_idx % len(MOODS)][1])


def _play(delay, freq, dur):
    melody.append([delay, freq, dur])


def _letter_note(letter):
    return LETTER_FREQ[ALPHABET.find(letter)]


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
    # A Battle-City-style maze: random short wall SEGMENTS built on the left
    # half and mirrored to the right, so every arena looks designed (symmetry)
    # yet is different each time. The outer ring of cells always stays open --
    # a patrol lane, so tanks can circle even a dense maze. Inner pockets are
    # fine: a walled-in tank blasts its own way out (see _move_tanks).
    global _bg_dirty
    walls.clear()
    for _i in range(14):
        r = 1 + int(rnd(GRID_ROWS - 2))
        c = 1 + int(rnd(GRID_COLS // 2 - 1))
        horiz = rnd(1.0) < 0.5
        for k in range(2 + int(rnd(3))):       # runs of 2-4 bricks
            rr = r + (0 if horiz else k)
            cc = c + (k if horiz else 0)
            if 1 <= rr < GRID_ROWS - 1 and 1 <= cc < GRID_COLS // 2:
                walls.add((rr, cc))
                walls.add((rr, GRID_COLS - 1 - cc))
    _bg_dirty = True


def _redraw_bg():
    # Repaint the background layer from scratch (floor + every standing brick)
    # in the current arena MOOD's colors. Full repaints keep the layer's
    # recorded stream one clean batch (that's what the web view re-ships), and
    # they only happen when a brick changes or the mood advances.
    global _bg, _bg_dirty
    floor, brick = MOODS[mood_idx % len(MOODS)]
    if _bg is None:
        _bg = make_layer(W, H)
    _bg.cls(col(floor))
    img = _brick_sprite(col(brick))   # pre-tinted per mood (no pal on the layer)
    for (r, c) in walls:
        _bg.spr(img, c * CELL, GRID_Y0 + r * CELL, BRICK_SCALE)
    _bg_dirty = False


def _refresh_arena():
    # Rebuild the chewed-up maze (new random layout, next MOOD's colors) --
    # but never drop a brick on top of a tank.
    global mood_idx
    mood_idx += 1
    _build_maze()
    clear = list(tanks)
    if boss is not None:
        clear.append(boss)
    for t in clear:
        half = BOSS_HALF + 2 if t is boss else TANK_HALF
        c0 = int((t[0] - half) // CELL)
        c1 = int((t[0] + half) // CELL)
        r0 = int((t[1] - half - GRID_Y0) // CELL)
        r1 = int((t[1] + half - GRID_Y0) // CELL)
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                walls.discard((r, c))
    tank_bullets[:] = []


def _solid(x, y):
    if x < 0 or x >= W or y < GRID_Y0 or y >= GRID_Y0 + GRID_ROWS * CELL:
        return True
    c = int(x // CELL)
    r = int((y - GRID_Y0) // CELL)
    return (r, c) in walls


def _lane_x(x):
    return int(x // CELL) * CELL + CELL / 2.0


def _lane_y(y):
    return GRID_Y0 + int((y - GRID_Y0) // CELL) * CELL + CELL / 2.0


def _open_spot():
    # Spawn on a free cell's center so the tank starts lane-aligned.
    for _try in range(60):
        c = int(rnd(GRID_COLS))
        r = int(rnd(GRID_ROWS))
        if (r, c) not in walls:
            return c * CELL + CELL / 2.0, GRID_Y0 + r * CELL + CELL / 2.0
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
    # Battle-City lane snap: center the perpendicular axis on the 16px grid so
    # the tank drives clean corridors and never clips a brick corner.
    if ddx:
        t[1] = _lane_y(t[1])
    else:
        t[0] = _lane_x(t[0])
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
    global lifetime, mode, trace_letter, trace_t, trace_strokes, tank_bullets
    global rings, rising, fx_text, melody, freeze_t, hint_t, wanted_flash_t
    global trace_covered, trace_was_held, trace_done, aiming, aim
    global boss, streak, best_streak, initials, rec_letters, rec_t, mood_idx
    # TYPING GAME: ask the console for the text keyboard. Without this the
    # device keyboard stays in raw game mode, where only the 9 d-pad-mapped
    # keys produce letters at all -- and q/e got eaten as pause/stop. In text
    # mode EVERY letter reaches keyp() cleanly. That also means the console's
    # hold-BACKSPACE game exit can't reach us, so we provide our OWN exit: the
    # top-right X button (tap it -> quit()), drawn + handled below.
    textmode(True)
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
    aiming = None
    aim = [0.0, -1.0]
    sparks = []
    rings = []
    rising = []
    fx_text = []
    melody = []
    cooldown_t = 0.0
    freeze_t = 0.0
    stuck_t = 0.0
    hint_t = 0.0
    wanted_flash_t = 0.0
    pop_count = 0
    lifetime = pmem(0)
    boss = None
    streak = 0
    mood_idx = 0
    best_streak = pmem(1)
    initials = ""
    for i in range(3):
        code = pmem(2 + i)
        if code:
            initials += chr(code)
    rec_letters = []
    rec_t = 0.0
    mode = "gallery"
    trace_letter = "A"
    trace_t = 0.0
    trace_strokes = []
    trace_covered = set()
    trace_was_held = False
    trace_done = False


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
            if blocked and t[10] < 1.2:
                _tank_fire(t)             # Battle City: blast the wall in the way
                t[10] = 1.2 + rnd(1.2)
            _retarget(t)
        t[10] -= dt
        if t[10] <= 0.0:
            _tank_fire(t)
            t[10] = 1.2 + rnd(2.2)
        if t[5] > 0.0:
            t[5] = max(0.0, t[5] - dt)


def _update_tank_bullets(dt):
    global _bg_dirty
    n = 0
    for b in tank_bullets:
        b[0] += b[2] * dt
        b[1] += b[3] * dt
        if _solid(b[0], b[1]):
            c = int(b[0] // CELL)
            r = int((b[1] - GRID_Y0) // CELL)
            if (r, c) in walls:
                walls.discard((r, c))
                _bg_dirty = True
                _burst(b[0], b[1], _brick_color())
            continue  # consumed either way (brick or arena edge)
        tank_bullets[n] = b
        n += 1
    del tank_bullets[n:]


def _burst(x, y, color, n=8):
    for _i in range(n):
        sparks.append([x, y, (rnd(2.0) - 1.0) * 90, (rnd(2.0) - 1.0) * 90 - 20, 0.45, color])


def _tick_fx(dt):
    # Celebration bookkeeping -- runs in BOTH modes so an arpeggio queued at a
    # pop keeps playing into the trace bonus. Expired entries are squeezed out
    # IN PLACE (write-index + del of the tail) -- "Make it fast": reuse the
    # list, don't build a new one every frame (each fresh list is garbage the
    # collector eventually stops the game to sweep).
    global wanted_flash_t
    n = 0
    for m in melody:
        m[0] -= dt
        if m[0] <= 0.0:
            beep(m[1], m[2])
        else:
            melody[n] = m
            n += 1
    del melody[n:]
    n = 0
    for p in sparks:
        p[4] -= dt
        if p[4] > 0.0:
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[3] += 200.0 * dt
            sparks[n] = p
            n += 1
    del sparks[n:]
    n = 0
    for g in rings:
        g[2] += 90.0 * dt
        g[3] -= dt
        if g[3] > 0.0:
            rings[n] = g
            n += 1
    del rings[n:]
    n = 0
    for r in rising:
        r[2] -= 30.0 * dt
        r[3] -= dt
        if r[3] > 0.0:
            rising[n] = r
            n += 1
    del rising[n:]
    n = 0
    for f in fx_text:
        f[2] -= 20.0 * dt
        f[3] -= dt
        if f[3] > 0.0:
            fx_text[n] = f
            n += 1
    del fx_text[n:]
    if wanted_flash_t > 0.0:
        wanted_flash_t = max(0.0, wanted_flash_t - dt)


def _tank_at(x, y):
    if boss is not None:
        dx = x - boss[0]
        dy = y - boss[1]
        if dx * dx + dy * dy <= 22 * 22:  # the big tank is a big target
            return boss
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
    if boss is not None and boss[4] == ch:
        return boss
    for t in tanks:
        if t[4] == ch:
            return t
    return None


def _pop(t):
    global wanted, stuck_t, hint_t, pop_count, lifetime, mode, freeze_t
    global wanted_flash_t, trace_letter, trace_t, trace_strokes, trace_covered
    global trace_was_held, trace_done, streak
    popped = t[4]
    body = col(BODY_COLORS[t[7] % len(BODY_COLORS)])
    # The jackpot moment: hitstop + rings + a shower of sparks + the letter
    # itself floating up out of the explosion, singing its two-note arpeggio.
    freeze_t = FREEZE_TIME
    _burst(t[0], t[1], body, 14)
    rings.append([t[0], t[1], 3.0, 0.35])
    rings.append([t[0], t[1], 1.0, 0.5])
    rising.append([popped, t[0], t[1] - 10.0, 0.8])
    streak += 1
    note = _letter_note(popped)
    _play(0.0, note, 0.14)
    _play(0.12, note * 2.0, 0.18)
    # a hot streak fattens the chord (the letter's own note stays the root,
    # so the sound-identity lesson survives the celebration)
    if streak >= 3:
        _play(0.24, note * 3.0, 0.14)
    if streak >= 6:
        _play(0.36, note * 4.0, 0.18)
    others = [tk[4] for tk in tanks if tk is not t]
    t[4] = _pick_letter(others)
    _place(t)
    _retarget(t)
    t[5] = 0.0
    rings.append([t[0], t[1], 2.0, 0.3])  # spawn ring: the teleport reads as an arrival
    stuck_t = 0.0
    hint_t = 0.0
    pop_count += 1
    lifetime += 1
    pmem(0, lifetime)
    milestone = lifetime % MILESTONE_EVERY == 0
    if milestone:
        fx_text.append([str(lifetime) + " FOUND!", W // 2, 96.0, 1.4, col("yellow")])
        _play(0.30, 392.0, 0.10)
        _play(0.42, 523.25, 0.10)
        _play(0.54, 659.25, 0.22)
        for name in ("red", "orange", "yellow", "green", "blue", "pink"):
            _burst(t[0], t[1], col(name), 3)
    letters_now = [tk[4] for tk in tanks]
    wanted = letters_now[int(rnd(len(letters_now)))]
    wanted_flash_t = 1.0
    if lifetime % BOSS_EVERY == 0:
        _start_boss()          # the boss IS the celebration; trace waits
        return
    if cfg("trace_bonus", 1) and pop_count % TRACE_EVERY == 0:
        mode = "trace"
        trace_letter = popped
        trace_t = TRACE_DURATION
        trace_strokes = []
        trace_covered = set()
        trace_was_held = False
        trace_done = False
        _play(0.5, note, 0.3)  # "now trace it" invite
    else:
        # Hearing the new target's note while seeing its glyph is the
        # letter-sound association this cart is secretly teaching.
        _play(0.9 if milestone else 0.5, _letter_note(wanted), 0.1)
        if pop_count % TRACE_EVERY == 0:
            _refresh_arena()  # TRACE off still gets the fresh-bricks reset


def _start_boss():
    # Every BOSS_EVERY finds: the walls come down and one DOUBLE-SIZE letter
    # tank rolls in. Three hits to pop it; each hit chants its letter bigger.
    global boss, wanted, wanted_flash_t, _bg_dirty
    letter = _pick_letter([])
    for t in tanks:                # the boss owns its letter exclusively
        if t[4] == letter:
            others = [tk[4] for tk in tanks if tk is not t]
            t[4] = _pick_letter(others + [letter])
    walls.clear()
    _bg_dirty = True
    tank_bullets[:] = []
    boss = [W / 2.0, GRID_Y0 + 80.0, 0.0, 0.0, letter, 0.0, 0.0,
            int(rnd(len(BODY_COLORS))), 0, 0, 0.0, BOSS_HP]
    _retarget(boss)
    wanted = letter
    wanted_flash_t = 1.5
    fx_text.append(["BOSS TIME!", W // 2, 96.0, 1.6, col("red")])
    _burst(boss[0], boss[1], col("red"), 10)
    _play(0.0, 110.0, 0.2)         # dun...
    _play(0.25, 110.0, 0.2)        # dun...
    _play(0.5, 220.0, 0.35)        # DUN!


def _move_boss(dt):
    b = boss
    nx = b[0] + b[2] * dt
    ny = b[1] + b[3] * dt
    blocked = False
    if b[2] > 0.0 and _solid(nx + BOSS_HALF, b[1]):
        nx = b[0]
        blocked = True
    elif b[2] < 0.0 and _solid(nx - BOSS_HALF, b[1]):
        nx = b[0]
        blocked = True
    if b[3] > 0.0 and _solid(b[0], ny + BOSS_HALF):
        ny = b[1]
        blocked = True
    elif b[3] < 0.0 and _solid(b[0], ny - BOSS_HALF):
        ny = b[1]
        blocked = True
    b[0] = nx
    b[1] = ny
    b[6] -= dt
    if blocked or b[6] <= 0.0:
        _retarget(b)
        slow = 1.15 - 0.2 * b[11]  # wounded boss speeds up: 0.55 -> 0.75 -> 0.95
        b[2] *= slow
        b[3] *= slow
    if b[5] > 0.0:
        b[5] = max(0.0, b[5] - dt)


def _boss_hit():
    global freeze_t, boss
    b = boss
    b[11] -= 1
    note = _letter_note(b[4])
    freeze_t = FREEZE_TIME
    _burst(b[0], b[1], col(BODY_COLORS[b[7] % len(BODY_COLORS)]), 10)
    rings.append([b[0], b[1], 4.0, 0.4])
    if b[11] > 0:
        b[5] = 0.35
        _play(0.0, note, 0.2)      # each hit chants the letter bigger
        if b[11] == 1:
            _play(0.18, note * 2.0, 0.25)
    else:
        _boss_kill()


def _boss_kill():
    global boss, lifetime, streak, wanted, freeze_t, wanted_flash_t
    global stuck_t, hint_t
    b = boss
    letter = b[4]
    for name in ("red", "orange", "yellow", "green", "blue", "pink"):
        _burst(b[0], b[1], col(name), 5)
    rings.append([b[0], b[1], 3.0, 0.4])
    rings.append([b[0], b[1], 8.0, 0.55])
    rings.append([b[0], b[1], 1.0, 0.7])
    rising.append([letter, b[0], b[1] - 14.0, 1.0])
    fx_text.append(["GOT IT!", W // 2, 96.0, 1.4, col("yellow")])
    note = _letter_note(letter)
    _play(0.0, note, 0.14)         # the full victory ladder on the boss's note
    _play(0.12, note * 1.5, 0.14)
    _play(0.24, note * 2.0, 0.2)
    _play(0.45, 523.25, 0.1)
    _play(0.57, 659.25, 0.1)
    _play(0.69, 783.99, 0.25)
    freeze_t = 0.2
    boss = None
    lifetime += 1
    pmem(0, lifetime)
    streak += 1
    stuck_t = 0.0
    hint_t = 0.0
    _refresh_arena()               # fresh maze, next mood: a new level begins
    letters_now = [tk[4] for tk in tanks]
    wanted = letters_now[int(rnd(len(letters_now)))]
    wanted_flash_t = 1.0
    _play(1.1, _letter_note(wanted), 0.1)


def _start_record():
    # A broken record-streak flips INTO a celebration: the arcade initials
    # screen. The wrong pick that ended the run barely registers -- what the
    # kid feels is NEW RECORD.
    global mode, rec_letters, rec_t, cooldown_t
    mode = "record"
    rec_letters = list(initials)   # last initials pre-filled for a quick OK
    rec_t = RECORD_TIMEOUT
    cooldown_t = 0.0
    _burst(W // 2, 60, col("yellow"), 16)
    _play(0.0, 523.25, 0.1)
    _play(0.12, 659.25, 0.1)
    _play(0.24, 1046.5, 0.25)


def _finish_record():
    global best_streak, initials, streak, mode
    best_streak = streak
    pmem(1, streak)
    initials = ""
    for i in range(3):
        if i < len(rec_letters):
            initials += rec_letters[i]
            pmem(2 + i, ord(rec_letters[i]))
        else:
            pmem(2 + i, 0)
    streak = 0
    mode = "gallery"
    _play(0.0, 783.99, 0.2)


def _trigger(t):
    global aiming, cooldown_t, streak
    if t[4] == wanted:
        aiming = t                 # swivel first; _update_aim fires when locked
        beep(240.0, 0.05)          # servo blip: the turret starts turning
    else:
        beep(WRONG_FREQ, 0.12)
        t[5] = 0.25
        if streak >= RECORD_MIN_STREAK and streak > best_streak:
            _start_record()        # the run just ended -- but it's a RECORD
        else:
            streak = 0
            cooldown_t = COOLDOWN


def _update_aim(dt):
    # Swivel the barrel toward the picked tank (tracking it while it moves) and
    # FIRE once aligned. Pure vector math -- carts have no math module, so the
    # aim is a unit vector eased toward the target direction and renormalized
    # with ** 0.5; "aligned" is a dot-product threshold instead of an angle.
    global aiming, bullet
    t = aiming
    dx = t[0] - CANNON_X
    dy = t[1] - CANNON_Y
    d = (dx * dx + dy * dy) ** 0.5
    if d < 1.0:
        tx, ty = 0.0, -1.0
    else:
        tx, ty = dx / d, dy / d
    k = min(1.0, AIM_SPEED * dt)
    ax = aim[0] + (tx - aim[0]) * k
    ay = aim[1] + (ty - aim[1]) * k
    n = (ax * ax + ay * ay) ** 0.5
    if n < 0.001:                  # degenerate 180-degree flip: kick sideways
        ax, ay = ty, -tx
        n = 1.0
    aim[0] = ax / n
    aim[1] = ay / n
    if aim[0] * tx + aim[1] * ty >= AIM_LOCK:
        bullet = [t, 0.0]
        aiming = None


def _trace_origin():
    return (W // 2 - (GLYPH_W * TRACE_SCALE) // 2,
            H // 2 - (GLYPH_H * TRACE_SCALE) // 2)


def _trace_cell(x, y):
    # The glyph ink cell under (x, y), or None (off the glyph / on a gap).
    gx, gy = _trace_origin()
    c = int((x - gx) // TRACE_SCALE)
    r = int((y - gy) // TRACE_SCALE)
    if 0 <= c < GLYPH_W and 0 <= r < GLYPH_H and GLYPH_ROWS[trace_letter][r][c] == "#":
        return (r, c)
    return None


def _ink_total(letter):
    n = 0
    for row in GLYPH_ROWS[letter]:
        n += row.count("#")
    return n


def _update_trace(dt):
    # REAL drawing, not tap-stamps: touch()'s `held` flag streams the finger
    # position while it's down, so the stroke follows the drag like a crayon.
    # Each NEW ink cell the stroke covers chirps one note higher (progress you
    # can hear); trace (nearly) the whole letter and the cheer comes early.
    global trace_t, mode, trace_done, trace_was_held
    trace_t -= dt
    if trace_t > TRACE_GREAT:
        tp = touch()
        held = tp is not None and (tp[2] or tp[3])
        if held:
            x = int(tp[0])
            y = int(tp[1])
            if not trace_was_held:
                trace_strokes.append([])       # finger just landed: new stroke
            stroke = trace_strokes[-1]
            if not stroke or abs(x - stroke[-1][0]) + abs(y - stroke[-1][1]) >= 3:
                cell = _trace_cell(x, y)
                stroke.append([x, y, cell is not None])
                if cell is not None and cell not in trace_covered:
                    trace_covered.add(cell)
                    beep(330.0 * (2 ** (min(len(trace_covered), 24) / 12.0)), 0.05)
                    total = _ink_total(trace_letter)
                    if len(trace_covered) >= total - total // 10:
                        trace_t = TRACE_GREAT  # letter drawn! celebrate early
        trace_was_held = held
    elif not trace_done:
        # always end on a cheer -- there is no way to fail the bonus
        trace_done = True
        _burst(W // 2, H // 2, col("yellow"), 18)
        _play(0.0, 523.25, 0.12)
        _play(0.14, 659.25, 0.12)
        _play(0.28, 783.99, 0.25)
    if trace_t <= 0.0:
        mode = "gallery"
        _refresh_arena()  # fresh bricks behind the curtain


def _update_record(dt):
    # The initials screen: tap letters on the A-Z grid (or type them), then
    # OK. Auto-saves on the timeout so a small kid can never get stuck here.
    global rec_t
    rec_t -= dt
    tp = touch()
    if tp is not None and tp[2]:
        c = int((tp[0] - 41) // 34)
        r = int((tp[1] - 118) // 26)
        if 0 <= c < 7 and 0 <= r < 4:
            k = r * 7 + c
            if k < 26:
                if len(rec_letters) < 3:
                    rec_letters.append(ALPHABET[k])
                    beep(_letter_note(ALPHABET[k]), 0.1)  # initials sing too
            elif k == 26:
                if rec_letters:
                    rec_letters.pop()
                    beep(196.0, 0.05)
            elif rec_letters:
                _finish_record()
                return
    code = keyp()
    if code:
        if code in (13, 10) and rec_letters:
            _finish_record()
            return
        if code == 8 and rec_letters:
            rec_letters.pop()
            beep(196.0, 0.05)
        elif 32 < code < 127:
            ch = chr(code).upper()
            if ch in ALPHABET and len(rec_letters) < 3:
                rec_letters.append(ch)
                beep(_letter_note(ch), 0.1)
    if rec_t <= 0.0:
        _finish_record()


def _update(dt):
    global cooldown_t, stuck_t, hint_t, bullet, freeze_t

    _tick_fx(dt)

    if mode == "trace":
        _update_trace(dt)
        return
    if mode == "record":
        _update_record(dt)
        return

    if _exit_tapped(touch()):  # the cart's own exit (text-mode carts must provide one)
        quit()
        return

    if freeze_t > 0.0:  # hitstop: sparks fly, everything else holds
        freeze_t = max(0.0, freeze_t - dt)
        return

    if cooldown_t > 0.0:
        cooldown_t = max(0.0, cooldown_t - dt)
    stuck_t += dt
    if stuck_t > STUCK_THRESHOLD:
        # audio hint on a slow burn: replay the wanted letter's note
        hint_t -= dt
        if hint_t <= 0.0:
            _play(0.0, _letter_note(wanted), 0.12)
            hint_t = HINT_EVERY

    _move_tanks(dt)
    if boss is not None:
        _move_boss(dt)
    _update_tank_bullets(dt)

    if aiming is not None:
        _update_aim(dt)

    if bullet is not None:
        bullet[1] += dt
        if bullet[1] >= BULLET_TIME:
            tgt = bullet[0]
            if boss is not None and tgt is boss:
                _boss_hit()
            else:
                _pop(tgt)
            bullet = None

    if bullet is None and aiming is None and cooldown_t <= 0.0:
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
    fx = t[8]
    fy = t[9]
    if fx:  # barrel first, so the hull overlaps its root
        bx = x + fx * (TANK_HALF + 3)
        rect(min(x, bx), y - 1, abs(bx - x), 3, col("dark_grey"))
    elif fy:
        by = y + fy * (TANK_HALF + 3)
        rect(x - 1, min(y, by), 3, abs(by - y), col("dark_grey"))
    spr(_tank_sprite(fx != 0, body, TANK_SCALE),
        x - TANK_DRAW_W // 2, y - TANK_DRAW_H // 2, TANK_SCALE)
    _draw_glyph(t[4], x - GLYPH_W, y - GLYPH_H, col("white"), 2)


def _draw_boss():
    b = boss
    x = int(b[0])
    y = int(b[1])
    body = col("red") if b[5] > 0.0 else col(BODY_COLORS[b[7] % len(BODY_COLORS)])
    fx = b[8]
    fy = b[9]
    if fx:
        bx = x + fx * (BOSS_HALF + 6)
        rect(min(x, bx), y - 2, abs(bx - x), 5, col("dark_grey"))
    elif fy:
        by = y + fy * (BOSS_HALF + 6)
        rect(x - 2, min(y, by), 5, abs(by - y), col("dark_grey"))
    spr(_tank_sprite(fx != 0, body, TANK_SCALE * 2),
        x - TANK_DRAW_W, y - TANK_DRAW_H, TANK_SCALE * 2)
    _draw_glyph(b[4], x - (GLYPH_W * 3) // 2, y - (GLYPH_H * 3) // 2, col("white"), 3)
    for i in range(b[11]):  # hp pips: how many hits are left
        rect(x - 10 + i * 8, y - TANK_DRAW_H - 8, 5, 5, col("yellow"))


def _draw_record():
    cls(col("dark_blue"))
    blink = int(time() / 150) % 2 == 0
    print("NEW RECORD!", W // 2 - 88, 26, col("yellow") if blink else col("orange"), 2)
    s = str(streak) + " IN A ROW!"
    print(s, W // 2 - len(s) * 4, 48, col("white"), 1)
    for i in range(3):  # the three initial slots
        bx = W // 2 - 42 + i * 30
        active = i == len(rec_letters) and i < 3
        rectb(bx, 62, 24, 24, col("yellow") if active and blink else col("light_grey"))
        if i < len(rec_letters):
            _draw_glyph(rec_letters[i], bx + 7, 67, col("white"), 2)
    for k in range(28):  # the tappable A-Z grid + undo + OK
        r = k // 7
        c = k % 7
        cx0 = 41 + c * 34
        cy0 = 118 + r * 26
        rectb(cx0 + 1, cy0 + 1, 32, 24, col("dark_grey"))
        if k < 26:
            _draw_glyph(ALPHABET[k], cx0 + 12, cy0 + 5, col("light_grey"), 2)
        elif k == 26:
            print("<", cx0 + 13, cy0 + 9, col("peach"), 1)
        else:
            print("OK", cx0 + 9, cy0 + 9, col("green"), 1)
    _draw_fx()


def _draw_fx():
    for g in rings:
        circb(int(g[0]), int(g[1]), int(g[2]), col("yellow"))
    for p in sparks:
        rect(int(p[0]), int(p[1]), 2, 2, p[5])
    blink = int(time() / 100) % 2 == 0
    for r in rising:
        _draw_glyph(r[0], int(r[1]) - (GLYPH_W * 3) // 2, int(r[2]) - (GLYPH_H * 3) // 2,
                    col("yellow") if blink else col("white"), 3)
    for f in fx_text:
        print(f[0], f[1] - len(f[0]) * 8, int(f[2]), f[4], 2)


def _draw_trace():
    cls(col("black"))
    gx, gy = _trace_origin()
    spr(_glyph(trace_letter, col("dark_grey"), TRACE_SCALE), gx, gy, TRACE_SCALE)
    for stroke in trace_strokes:
        lx = -1
        ly = -1
        for pt in stroke:
            ink = col("yellow") if pt[2] else col("white")
            if lx >= 0:  # a doubled line + end dots = a chunky crayon stroke
                line(lx, ly, pt[0], pt[1], ink)
                line(lx, ly + 1, pt[0], pt[1] + 1, ink)
            circ(pt[0], pt[1], 2, ink)
            lx = pt[0]
            ly = pt[1]
    if trace_t > TRACE_GREAT:
        print("DRAW THE LETTER!", W // 2 - 64, 26, col("white"), 1)
        print(str(int(trace_t - TRACE_GREAT) + 1), W // 2 - 4, H - 20, col("light_grey"), 1)
    else:
        blink = int(time() / 150) % 2 == 0
        print("GREAT!", W // 2 - 36, H - 32, col("yellow") if blink else col("orange"), 2)
    _draw_fx()


def _draw_cannon():
    firing = bullet is not None and bullet[1] < 0.08
    y = CANNON_Y + (2 if firing else 0)  # recoil kick
    # The barrel: a thick line along the live aim vector (drawn first, so the
    # base sprite covers its root). It visibly swivels while _update_aim runs.
    mx = int(CANNON_X + aim[0] * BARREL_LEN)
    my = int(y + aim[1] * BARREL_LEN)
    bcol = col("black") if cooldown_t > 0.0 else col("dark_grey")
    line(CANNON_X - 1, y, mx - 1, my, bcol)
    line(CANNON_X, y, mx, my, bcol)
    line(CANNON_X + 1, y, mx + 1, my, bcol)
    circ(mx, my, 2, bcol)
    spr(_cannon_sprite(cooldown_t > 0.0),
        CANNON_X - CANNON_IMG_W * CANNON_SCALE // 2,
        y - CANNON_IMG_H * CANNON_SCALE // 2, CANNON_SCALE)
    if firing:
        circ(mx, my, 5, col("yellow"))   # muzzle flash rides the barrel tip
        circ(mx, my, 2, col("white"))
    if aiming is not None and int(time() / 80) % 2 == 0:
        r = 24 if aiming is boss else 13
        circb(int(aiming[0]), int(aiming[1]), r, col("yellow"))  # lock-on reticle
    if cooldown_t > 0.0:
        # the visible reload: a shrinking bar, so blocked input never reads as broken
        w = int(30 * cooldown_t / COOLDOWN)
        rect(CANNON_X - w // 2, CANNON_Y + 12, w, 2, col("red"))


def _draw():
    if mode == "trace":
        _draw_trace()
        return
    if mode == "record":
        _draw_record()
        return

    if _bg_dirty:
        _redraw_bg()
    draw_layer(_bg, 0, 0)  # floor + bricks in one copy (replaces cls + wall sprites)

    for b in tank_bullets:
        rect(int(b[0]) - 1, int(b[1]) - 1, 2, 2, col("light_grey"))

    for t in tanks:
        _draw_tank(t)
    if boss is not None:
        _draw_boss()

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

    _draw_fx()
    _draw_cannon()

    flashing = wanted_flash_t > 0.0 and int(time() / 120) % 2 == 0
    rect(2, HUD_Y, W - 4, HUD_H, col("black"))
    rectb(2, HUD_Y, W - 4, HUD_H, col("yellow") if flashing else col("light_grey"))
    print("FIND", 10, HUD_Y + 10, col("white"), 1)
    _draw_glyph(wanted, 50, HUD_Y, col("white") if flashing else col("yellow"), 4)
    for i in range(min(streak, 8)):  # the streak: one star per correct in a row
        spr(_star_sprite(), 84 + i * 8, HUD_Y + 11, 1)
    # HUD strings are rebuilt only when the number behind them changes --
    # "Make it fast": building a string every frame is invisible garbage.
    if _hud_strs[1] != lifetime:
        _hud_strs[1] = lifetime
        _hud_strs[0] = str(lifetime) + " FOUND"
    found = _hud_strs[0]
    print(found, W - 10 - len(found) * 8, HUD_Y + 4, col("light_grey"), 1)
    if best_streak > 0:
        if _hud_strs[3] != best_streak or _hud_strs[4] != initials:
            _hud_strs[3] = best_streak
            _hud_strs[4] = initials
            _hud_strs[2] = "BEST " + str(best_streak) + " " + initials
        b = _hud_strs[2]
        print(b, W - 10 - len(b) * 8, HUD_Y + 16, col("dark_grey"), 1)
    _draw_exit()  # the cart's own tap-to-quit X (a text-mode cart must provide its exit)
