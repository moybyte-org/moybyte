# Pixel Pet -- a virtual pet. Keep your pet FED and HAPPY:
#   LEFT  = feed (fills the food meter)   RIGHT = play (fills the joy meter)
# Both meters drain over time; a hungry or bored pet gets sad, and how it looks
# (and its bounce) follows its mood. Care for it and it shows little hearts.
#
# AUTOPLAY (in "Make it mine") is OFF by default -- YOU look after it. Flip it on
# and the pet cares for itself (attract mode). A pet is just another cartridge.

food = 80.0
joy = 80.0
t = 0.0
idle = 0.0
pet = 0          # the chosen pet sprite tile (0 frog, 1 cat, 2 robot -- editable)
bob = 0
blink = 0.0
hearts = []      # floating care feedback: [x, y, life]


# The pet faces live in the cart's sprite sheet (sprites.kgfx): frog=0, cat=1,
# robot=2. Pick one in "Make it mine" and edit it in the paint editor.


def _pet_tile():
    p = cfg("pet", 0)                # tile id (tolerate a stale string config)
    try:
        return int(p)
    except (TypeError, ValueError):
        return {"cat": 1, "robot": 2}.get(p, 0)


def _init():
    global food, joy, t, idle, pet, bob, blink, hearts
    food = 80.0
    joy = 80.0
    t = 0.0
    idle = 0.0
    bob = 0
    blink = 0.0
    hearts = []
    pet = _pet_tile()


def _heart(x):
    hearts.append([x, H - 26 - 40, 0.8])


def _feed():
    global food
    if food < 100.0:
        _heart(W // 2 - 18)
    food = min(100.0, food + 18.0)


def _play():
    global joy
    if joy < 100.0:
        _heart(W // 2 + 18)
    joy = min(100.0, joy + 18.0)


def _update(dt):
    global food, joy, t, idle, bob, blink
    t += dt
    blink += dt
    if blink > 3.0:
        blink = 0.0
    decay = cfg("decay", 4)
    food = max(0.0, food - decay * dt)
    joy = max(0.0, joy - decay * 0.8 * dt)
    acted = False
    if btn("left"):
        _feed()
        acted = True
    if btn("right"):
        _play()
        acted = True
    if acted:
        idle = 0.0
    else:
        idle += dt
        if cfg("autoplay", 0) and idle > 1.5:   # attract: care for the neediest meter
            if food <= joy:
                _feed()
            else:
                _play()
    # floating hearts rise + fade
    keep = []
    for h in hearts:
        h[2] -= dt
        if h[2] > 0.0:
            h[1] -= 26.0 * dt
            keep.append(h)
    hearts[:] = keep
    # a happier pet bounces faster
    mood = min(food, joy)
    rate = 3 if mood > 40 else 2
    bob = -3 if (int(t * rate) % 2 == 0) else 0


def _bar(x, y, w, v, c):
    rect(x, y, w, 8, col("dark_grey"))
    fill = int(w * v / 100.0)
    if fill > 0:
        rect(x, y, fill, 8, col(c))
    rectb(x, y, w, 8, col("white"))


def _smiley(cx, cy, mood):
    # a tiny mood face floating above the pet (smile / flat / frown)
    pix(cx - 4, cy - 2, col("white"))
    pix(cx + 4, cy - 2, col("white"))
    if mood > 60:                                 # smile
        line(cx - 3, cy + 2, cx, cy + 4, col("white"))
        line(cx, cy + 4, cx + 3, cy + 2, col("white"))
    elif mood > 25:                               # flat
        line(cx - 3, cy + 3, cx + 3, cy + 3, col("white"))
    else:                                         # frown
        line(cx - 3, cy + 4, cx, cy + 2, col("white"))
        line(cx, cy + 2, cx + 3, cy + 4, col("white"))


def _draw():
    cls(col(cfg("bg", "dark_purple")))
    mood = min(food, joy)
    # ground
    rect(0, H - 26, W, 26, col("dark_green"))
    # the pet, bobbing; blink by hiding it for a beat (cheap eye-blink)
    show = not (2.7 < blink < 2.85)
    px = W // 2 - 16            # 8x8 tile drawn at 4x = 32px wide
    py = H - 26 - 36 + bob
    spr(pet, px, py if show else py + 2, -1, 4)    # tile id from the cart sheet
    _smiley(W // 2, py - 8, mood)
    # floating hearts
    for h in hearts:
        x = int(h[0])
        y = int(h[1])
        pix(x - 1, y, col("pink"))
        pix(x + 1, y, col("pink"))
        pix(x, y + 1, col("pink"))
    # mood word
    if mood > 60:
        word = "HAPPY"
        wc = "green"
    elif mood > 25:
        word = "OK"
        wc = "yellow"
    else:
        word = "SAD"
        wc = "red"
    print("PET: " + word, 10, 10, col(wc), 2)
    # meters
    print("FOOD", 10, 34, col("white"), 1)
    _bar(48, 33, 110, food, "orange")
    print("JOY", 10, 50, col("white"), 1)
    _bar(48, 49, 110, joy, "pink")
    print("LEFT=FEED  RIGHT=PLAY", 10, H - 16, col("light_grey"), 1)
