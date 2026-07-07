# Ocean Desktop -- a second wallpaper cartridge: rising bubbles + a swimming fish.
# Shows the gallery has variety; same shape as the Space Desktop.

bubbles = []
fish_x = 0.0
fish_dir = 1
t = 0.0

# The fish lives in the cart's sprite sheet (sprites.moygfx) as tile 0 -- edit it
# in the paint editor.


def _init():
    background(col(cfg("water", "blue")))  # re-declared when a config card re-runs _init
    global bubbles, fish_x
    n = int(cfg("bubble_count", 60))
    spd = cfg("rise_speed", 25)
    bubbles = []
    for _i in range(n):
        bubbles.append([rnd(W), rnd(H), 1 + int(rnd(2)), spd * (0.5 + rnd(0.8))])
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
    for b in bubbles:
        circb(int(b[0]), int(b[1]), b[2], col("white"))   # bubble outlines
    rect(0, H - 18, W, 18, col("brown"))          # seabed
    wob = 2 if (int(t * 3) % 2 == 0) else 0
    # spr-flip (#11): the fish faces right by default; flip it horizontally (mode 1)
    # when swimming left so it always faces where it's going -- the live demo of flip.
    # spr(id, x, y, COLORKEY, SCALE, FLIP): colorkey 0 keys out the tile's index-0
    # background so the fish floats over the water; scale 4; FLIP is the LAST arg (this
    # used to put the flip value in the colorkey slot -> the key went bad facing left).
    flip = 1 if fish_dir < 0 else 0
    spr(0, int(fish_x), H - 18 - 24 - wob, 0, 4, flip)   # fish, 4x, faces travel
    print("OCEAN", 10, 10, col("white"), 3)
