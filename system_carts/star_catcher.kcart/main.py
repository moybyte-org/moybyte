# Star Catcher -- a game cartridge. Move the catcher LEFT/RIGHT to catch falling
# stars. With no input it auto-plays (attract mode), so it's lively in the
# simulator GIF; pressing a key takes over. Same runtime as the wallpaper -- a
# game is just another cartridge.

BW = 48          # catcher width
BH = 14
score = 0
bx = 0.0
stars = []
catcher = None

FROG = [
    ".GG...GG.",
    "GWGGGGGWG",
    "GGGGGGGGG",
    "GGKGGGKGG",
    ".GGGGGGG.",
]
ROBOT = [
    ".LLLLL.",
    "LKOKOKL",
    "LLLLLLL",
    ".L...L.",
]


def _make_catcher(kind):
    if kind == "robot":
        return image(ROBOT, {"L": col("light_grey"), "O": col("red"), "K": col("black")})
    return image(FROG, {"G": col("green"), "W": col("white"), "K": col("black")})


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
    catcher = _make_catcher(cfg("basket", "frog"))


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
        circfill(int(s[0]), int(s[1]), 3, col("yellow"))
    by = H - 24 - BH
    rectfill(0, H - 24, W, 24, col("dark_blue"))     # floor
    rectfill(int(bx), by, BW, BH, col("brown"))      # basket
    spr(catcher, int(bx) + BW // 2 - 18, by - 18, 4)
    text("SCORE " + str(score), 10, 10, col("white"), 3)
