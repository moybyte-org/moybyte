# Space Desktop -- a living wallpaper cartridge (v0.4 demo).
#
# This is "the computer itself", made of editable code. Change config and press
# Run: more stars, a faster sky, a different pet, a new background. The API
# (cls/pix/rect/spr/print/cfg/col/rnd/image -- TIC-80 style) is injected by the
# runtime.

stars = []
pet = None
pet_x = 0.0
pet_dir = 1
t = 0.0

# 9x7 frog and 7x6 robot, drawn as ASCII -> palette indices.
FROG = [
    ".GG...GG.",
    "GWGGGGGWG",
    "GGGGGGGGG",
    "GGKGGGKGG",
    "GGGGGGGGG",
    ".GGGGGGG.",
    "..G.G.G..",
]
ROBOT = [
    ".LLLLL.",
    "LKOKOKL",
    "LLLLLLL",
    "LKLLLKL",
    "LLLLLLL",
    ".L...L.",
]


def _make_pet(kind):
    if kind == "robot":
        return image(ROBOT, {"L": col("light_grey"), "O": col("red"), "K": col("black")})
    return image(FROG, {"G": col("green"), "W": col("white"), "K": col("black")})


def _init():
    global stars, pet, pet_x
    n = int(cfg("star_count", 80))
    spd = cfg("star_speed", 30)
    stars = []
    for _i in range(n):
        stars.append([rnd(W), rnd(H), spd * (0.4 + rnd(0.6))])
    pet = _make_pet(cfg("pet", "frog"))
    pet_x = W * 0.5


def _update(dt):
    global pet_x, pet_dir, t
    t += dt
    for s in stars:
        s[1] += s[2] * dt
        if s[1] >= H:
            s[1] = 0
            s[0] = rnd(W)
    pet_x += pet_dir * 40 * dt
    if pet_x > W - 40 or pet_x < 4:
        pet_dir = -pet_dir


def _draw():
    cls(col(cfg("bg", "dark_blue")))
    for s in stars:
        pix(s[0], s[1], 7 if s[2] > 25 else 6)
    rect(0, H - 24, W, 24, col("dark_green"))   # ground
    bob = 2 if (int(t * 4) % 2 == 0) else 0
    spr(pet, int(pet_x), H - 24 - 28 - bob, scale=4)  # 4x-scaled pet on the ground
    print("MY SPACE COMPUTER", 10, 10, col("white"), 3)
