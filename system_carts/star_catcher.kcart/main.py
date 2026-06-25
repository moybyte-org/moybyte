# Star Catcher -- catch the falling stars! Move the catcher LEFT/RIGHT (B / A
# also work) to scoop up stars before they hit the floor. Each catch is +1 and
# builds a COMBO that scores extra; let one fall and you lose a heart. Lose all 3
# hearts and the round resets. Stars fall faster the higher you score, so the
# pressure climbs.
#
# AUTOPLAY (in "Make it mine") is OFF by default -- YOU play. Flip it on to watch
# the console play itself (attract mode).
#
# The CATCHER is a real sprite-sheet tile (id 0 = frog, id 1 = robot, both
# editable in the paint editor). The "Make it mine" CATCHER card is a sprite-tile
# picker: tap the frog or the robot to choose -- no reading required.

BW = 44          # catcher width (a touch tighter than before)
BH = 14
SPR_SCALE = 4    # the 8x8 catcher tile is drawn at 4x (32x32)
LIVES = 3
score = 0
combo = 0
best = 0
lives = LIVES
bx = 0.0
catcher = 0      # the chosen catcher sprite tile id
stars = []
sparks = []      # catch particles: [x, y, vx, vy, life]
flash = 0.0      # white catch glow, decays
shake = 0.0      # screen-shake amount, decays
over = 0.0       # >0 = game-over banner timer


def _base_speed():
    return float(cfg("fall_speed", 70))


def _spawn(s):
    s[0] = rnd(W - 8)
    s[1] = -rnd(H * 0.5) - 8
    # speed ramps with score so the game gets harder the better you do
    s[2] = _base_speed() * (0.7 + rnd(0.6)) * (1.0 + score * 0.02)


def _init():
    global score, combo, lives, bx, stars, catcher, sparks, flash, shake, over
    score = 0
    combo = 0
    lives = LIVES
    bx = W / 2 - BW / 2
    stars = []
    sparks = []
    flash = 0.0
    shake = 0.0
    over = 0.0
    for _i in range(int(cfg("star_count", 5))):
        s = [0, 0, 0]
        _spawn(s)
        stars.append(s)
    b = cfg("basket", 0)                   # tile id (tolerate a stale string config)
    try:
        catcher = int(b)
    except (TypeError, ValueError):
        catcher = 1 if b == "robot" else 0


def _nearest_star():
    # the star that will reach the floor SOONEST (time = distance / fall speed),
    # so the autoplay catcher commits early instead of thrashing between stars.
    by = H - 24 - BH
    best_s = None
    best_t = 0.0
    for s in stars:
        ttf = (by - s[1]) / max(1.0, s[2])
        if best_s is None or ttf < best_t:
            best_s = s
            best_t = ttf
    return best_s


def _burst(x, y):
    # a few sparks fly out of a caught star (cheap particles)
    for _i in range(6):
        sparks.append([x, y, (rnd(2.0) - 1.0) * 80, -rnd(90) - 20, 0.5])


def _catch(s):
    global score, combo, best, flash, shake
    score += 1 + combo // 5          # combo bonus: every 5 in a row scores extra
    combo += 1
    if score > best:
        best = score
    flash = 0.18
    shake = 3.0
    _burst(s[0], H - 24 - BH)
    _spawn(s)


def _drop(s):
    global lives, combo, shake, over
    combo = 0
    lives -= 1
    shake = 5.0
    if lives <= 0:
        over = 1.2
    _spawn(s)


def _update(dt):
    global bx, flash, shake, over
    if over > 0.0:
        over -= dt
        if over <= 0.0:
            _init()
        return
    speed = 200.0                       # tighter, snappier catcher
    left = btn("left") or btn("b")
    right = btn("right") or btn("a")
    if left:
        bx -= speed * dt
    elif right:
        bx += speed * dt
    elif cfg("autoplay", 0):            # attract mode: drift toward a star
        target = _nearest_star()
        if target is not None:
            want = target[0] - BW / 2
            bx += max(-speed * dt, min(speed * dt, want - bx))
    bx = max(0, min(W - BW, bx))
    by = H - 24 - BH
    for s in stars:
        s[1] += s[2] * dt
        if s[1] + 6 >= by and s[1] <= by + BH and bx <= s[0] <= bx + BW:
            _catch(s)
        elif s[1] > H:
            _drop(s)
    # particles
    keep = []
    for p in sparks:
        p[4] -= dt
        if p[4] > 0.0:
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[3] += 240.0 * dt
            keep.append(p)
    sparks[:] = keep
    if flash > 0.0:
        flash = max(0.0, flash - dt)
    if shake > 0.0:
        shake = max(0.0, shake - dt * 12.0)


def _draw():
    sx = 0
    if shake > 0.0:
        sx = int(rnd(shake * 2) - shake)
    cls(col("white") if flash > 0.0 else col("black"))
    for s in stars:
        circ(int(s[0]) + sx, int(s[1]), 3, col("yellow"))
        pix(int(s[0]) + sx, int(s[1]) - 4, col("white"))   # tiny sparkle tail
    for p in sparks:
        pix(int(p[0]) + sx, int(p[1]), col("yellow"))
    by = H - 24 - BH
    rect(0, H - 24, W, 24, col("dark_blue"))         # floor
    rect(int(bx) + sx, by, BW, BH, col("brown"))     # basket
    spr(catcher, int(bx) + sx + BW // 2 - 8 * SPR_SCALE // 2, by - 8 * SPR_SCALE,
        -1, SPR_SCALE)                               # catcher tile (frog/robot)
    print("SCORE " + str(score), 8, 8, col("white"), 2)
    if combo >= 2:
        print("X" + str(combo), 8, 26, col("yellow"), 1)
    # hearts (lives) top-right
    for i in range(LIVES):
        c = "red" if i < lives else "dark_grey"
        rect(W - 14 - i * 12, 9, 8, 7, col(c))
    if over > 0.0:
        print("GAME OVER", W // 2 - 36, H // 2 - 8, col("red"), 2)
        print("BEST " + str(best), W // 2 - 24, H // 2 + 10, col("yellow"), 1)
