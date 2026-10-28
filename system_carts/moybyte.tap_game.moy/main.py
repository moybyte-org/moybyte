# Made with Moybyte blocks. Edit in the block editor (or graduate to code).

score = 0
tx = 0
ty = 0
timer = 0
over = 0

def _touched():
    t = touch()
    return bool(t) and t[2]

def _touch_x():
    t = touch()
    return t[0] if t else -100

def _touch_y():
    t = touch()
    return t[1] if t else -100


def new_game(ticks):
    global score, timer, over
    score = 0
    timer = ticks
    over = 0


def move_coin():
    global tx, ty
    tx = (int(rnd(270)) + 15)
    ty = (int(rnd(150)) + 56)


def _init():
    new_game(600)
    move_coin()


def _update(dt):
    global score, timer, over
    if (over == 0):
        timer = timer + (-1)
        if _touched():
            if (_touch_x() > tx):
                if (_touch_x() < (tx + 28)):
                    if (_touch_y() > ty):
                        if (_touch_y() < (ty + 28)):
                            score = score + (1)
                            move_coin()
                            beep(880)
        if (timer < 1):
            over = 1


def _draw():
    cls(col("dark_blue"))
    if (over == 0):
        spr(0, tx, ty)
        rectb(tx, ty, 28, 28, col("yellow"))
        print("SCORE", 8, 28, col("white"))
        print(score, 56, 28, col("yellow"))
        print("TIME", 8, 40, col("white"))
        print(timer, 56, 40, col("green"))
        print("TAP THE COIN", 8, 224, col("light_grey"))
    if (over > 0):
        print("GAME OVER", 110, 100, col("red"))
        print("SCORE", 120, 120, col("white"))
        print(score, 168, 120, col("yellow"))
        print("TAP TO REPLAY", 104, 140, col("light_grey"))
    if ((over > 0) and _touched()):
        new_game(600)
