# Pixel Pet -- a virtual-pet cartridge. Keep your pet FED and HAPPY:
#   LEFT  = feed (fills the food meter)   RIGHT = play (fills the joy meter)
# Both meters drain over time; a hungry or bored pet gets sad. With no input it
# auto-cares for itself (attract mode) so it stays lively in the simulator GIF.
# Same runtime/API as every other cart -- a pet is just another cartridge.

food = 80.0
joy = 80.0
t = 0.0
idle = 0.0
pet = None
bob = 0
blink = 0.0

# 9x8 pet faces, recolored per "kind". E=eye, M=mouth, .=transparent.
FROG = [
    ".BBBBBBB.",
    "BBBBBBBBB",
    "BEBBBBBEB",
    "BBBBBBBBB",
    "BBMMMMMBB",
    "BBBBBBBBB",
    ".BBBBBBB.",
    "..B...B..",
]
CAT = [
    "B.......B",
    "BB.....BB",
    "BBBBBBBBB",
    "BEBBBBBEB",
    "BBBBNBBBB",
    "BBMMMMMBB",
    ".BBBBBBB.",
    "..BB.BB..",
]
ROBOT = [
    "BBBBBBBBB",
    "B.......B",
    "B.E...E.B",
    "B.......B",
    "B.MMMMM.B",
    "B.......B",
    "BBBBBBBBB",
    "..B...B..",
]
KINDS = {"frog": (FROG, "green"), "cat": (CAT, "orange"), "robot": (ROBOT, "light_grey")}


def _make_pet(kind):
    rows, body = KINDS.get(kind, KINDS["frog"])
    return image(rows, {"B": col(body), "E": col("black"), "M": col("red"),
                        "N": col("pink")}, ".")


def _init():
    global food, joy, t, idle, pet, bob, blink
    food = 80.0
    joy = 80.0
    t = 0.0
    idle = 0.0
    bob = 0
    blink = 0.0
    pet = _make_pet(cfg("pet", "frog"))


def _feed():
    global food
    food = min(100.0, food + 18.0)


def _play():
    global joy
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
        if idle > 1.5:                 # attract mode: care for the neediest meter
            if food <= joy:
                _feed()
            else:
                _play()
    bob = -2 if (int(t * 3) % 2 == 0) else 0


def _bar(x, y, w, v, c):
    rect(x, y, w, 8, col("dark_grey"))
    fill = int(w * v / 100.0)
    if fill > 0:
        rect(x, y, fill, 8, col(c))
    rectb(x, y, w, 8, col("white"))


def _draw():
    cls(col(cfg("bg", "dark_purple")))
    mood = min(food, joy)
    # ground
    rect(0, H - 26, W, 26, col("dark_green"))
    # the pet, bobbing; blink by hiding it for a beat (cheap eye-blink)
    show = not (2.7 < blink < 2.85)
    px = W // 2 - 18
    py = H - 26 - 36 + bob
    if show:
        spr(pet, px, py, scale=4)
    else:
        spr(pet, px, py + 2, scale=4)
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
