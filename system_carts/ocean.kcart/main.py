# Ocean Desktop -- a second wallpaper cartridge: rising bubbles + a swimming fish.
# Shows the gallery has variety; same shape as the Space Desktop.

bubbles = []
fish = None
fish_x = 0.0
fish_dir = 1
t = 0.0

FISH = [
    "...WWW..",
    ".WWWWWWK",
    "WWWWWWWW",
    ".WWWWWWK",
    "...WWW..",
]


def _init():
    global bubbles, fish, fish_x
    n = int(cfg("bubble_count", 60))
    spd = cfg("rise_speed", 25)
    bubbles = []
    for _i in range(n):
        bubbles.append([rnd(W), rnd(H), 1 + int(rnd(2)), spd * (0.5 + rnd(0.8))])
    fish = image(FISH, {"W": col("orange"), "K": col("black")})
    fish_x = W * 0.5


def _update(dt):
    global fish_x, fish_dir, t
    t += dt
    for b in bubbles:
        b[1] -= b[3] * dt
        if b[1] < 0:
            b[1] = H
            b[0] = rnd(W)
    fish_x += fish_dir * 50 * dt
    if fish_x > W - 40 or fish_x < 4:
        fish_dir = -fish_dir


def _draw():
    cls(col(cfg("water", "blue")))
    for b in bubbles:
        circb(int(b[0]), int(b[1]), b[2], col("white"))   # bubble outlines
    rect(0, H - 18, W, 18, col("brown"))          # seabed
    wob = 2 if (int(t * 3) % 2 == 0) else 0
    spr(fish, int(fish_x), H - 18 - 24 - wob, scale=4)
    print("OCEAN", 10, 10, col("white"), 3)
