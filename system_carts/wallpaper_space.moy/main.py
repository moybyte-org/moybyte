# Space Desktop -- a living wallpaper cartridge (v0.4 demo).
#
# This is "the computer itself", made of editable code. Change config and press
# Run: more stars, a faster sky, a different pet, a new background. The API
# (cls/pix/rect/spr/print/cfg/col/rnd/image -- TIC-80 style) is injected by the
# runtime.

stars = []
pet = 0          # the chosen pet sprite tile id (0 frog, 1 robot -- both editable)
pet_x = 0.0
pet_dir = 1
t = 0.0

# The pet faces live in the cart's sprite sheet (sprites.moygfx): frog=0, robot=1.
# Pick one in "Make it mine" and edit it in the paint editor.

# DESKTOP SAFE BAND. A wallpaper is 320x240, but a desktop backdrop is COVER-
# cropped: the smallest INTEGER upscale that covers the screen, centred. At
# 1024x600 that is 4x -> 1280x960 -> ~45 rows off the top AND bottom and ~32
# columns off each side, in cart pixels. The pet, the ground line and the
# caption live inside this band; the sky and stars still bleed to the real
# edges, so the handheld's full 320x240 view stays a complete picture.
SAFE_Y = 45
SAFE_X = 32
GROUND = H - SAFE_Y - 5          # the ground's top edge, just inside the band


def _init():
    global stars, pet, pet_x
    n = int(cfg("star_count", 80))
    spd = cfg("star_speed", 30)
    stars = []
    for _i in range(n):
        stars.append([rnd(W), rnd(H), spd * (0.4 + rnd(0.6))])
    p = cfg("pet", 0)                # tile id (tolerate a stale string config)
    try:
        pet = int(p)
    except (TypeError, ValueError):
        pet = 1 if p == "robot" else 0
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
    if pet_x > W - SAFE_X - 36 or pet_x < SAFE_X + 4:
        pet_dir = -pet_dir


def _paint_bg(name):
    # Match the "Make it mine" BG thumbnail presets: solid colors, a black
    # "night" sky (the stars below make it a starfield), or indigo/blue stripes.
    if name == "stripes":
        for i in range(0, W, 24):
            rect(i, 0, 12, H, col("indigo"))
            rect(i + 12, 0, 12, H, col("dark_blue"))
    elif name == "night":
        cls(col("black"))
    else:
        cls(col(name))


def _draw():
    _paint_bg(cfg("bg", "dark_blue"))
    for s in stars:
        pix(s[0], s[1], 7 if s[2] > 25 else 6)
    rect(0, GROUND, W, H - GROUND, col("dark_green"))   # ground
    bob = 2 if (int(t * 4) % 2 == 0) else 0
    spr(pet, int(pet_x), GROUND - 32 - bob, 0, 4)  # 4x-scaled pet tile (frog/robot)
    print("MY SPACE COMPUTER", SAFE_X + 8, SAFE_Y + 10, col("white"), 3)
