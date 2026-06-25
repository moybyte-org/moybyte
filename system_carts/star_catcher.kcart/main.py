# Star Catcher -- a game cartridge. Move the catcher LEFT/RIGHT to catch falling
# stars. With no input it auto-plays (attract mode), so it's lively in the
# simulator GIF; pressing a key takes over. Same runtime as the wallpaper -- a
# game is just another cartridge.
#
# The CATCHER is a real sprite-sheet tile (id 0 = frog, id 1 = robot, both
# editable in the paint editor). The "Make it mine" CATCHER card is a sprite-tile
# picker: tap the frog or the robot to choose -- no reading required.

BW = 48          # catcher width
BH = 14
SPR_SCALE = 4    # the 8x8 catcher tile is drawn at 4x (32x32)
score = 0
bx = 0.0
catcher = 0      # the chosen catcher sprite tile id
stars = []


def _spawn(s):
    s[0] = rnd(W - 8)
    s[1] = -rnd(H * 0.5) - 8
    s[2] = cfg("fall_speed", 70) * (0.7 + rnd(0.6))


def _init():
    global score, bx, stars, catcher
    score = 0
    bx = W / 2 - BW / 2
    stars = []
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
    best = None
    for s in stars:
        if best is None or s[1] > best[1]:
            best = s
    return best


def _update(dt):
    global bx, score
    speed = 160
    if btn("left"):
        bx -= speed * dt
    elif btn("right"):
        bx += speed * dt
    else:
        target = _nearest_star()           # attract mode: drift toward a star
        if target is not None:
            want = target[0] - BW / 2
            bx += max(-speed * dt, min(speed * dt, want - bx))
    bx = max(0, min(W - BW, bx))
    by = H - 24 - BH
    for s in stars:
        s[1] += s[2] * dt
        if s[1] + 6 >= by and s[1] <= by + BH and bx <= s[0] <= bx + BW:
            score += 1
            _spawn(s)
        elif s[1] > H:
            _spawn(s)


def _draw():
    cls(col("black"))
    for s in stars:
        circ(int(s[0]), int(s[1]), 3, col("yellow"))
    by = H - 24 - BH
    rect(0, H - 24, W, 24, col("dark_blue"))     # floor
    rect(int(bx), by, BW, BH, col("brown"))      # basket
    spr(catcher, int(bx) + BW // 2 - 8 * SPR_SCALE // 2, by - 8 * SPR_SCALE,
        -1, SPR_SCALE)                            # catcher tile (frog/robot)
    print("SCORE " + str(score), 10, 10, col("white"), 3)
