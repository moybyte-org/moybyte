# Made with Moybyte blocks. Edit in the block editor (or graduate to code).

score = 0


def _init():
    global score
    score = 0


def _update(dt):
    global score
    for _actor1 in actors("player"):
        if btn("left"):
            move_actor(_actor1, -2, 0)
        if btn("right"):
            move_actor(_actor1, 2, 0)
        if btn("up"):
            move_actor(_actor1, 0, -2)
        if btn("down"):
            move_actor(_actor1, 0, 2)
    for _actor1 in actors("coin"):
        if touching(_actor1, "player"):
            remove_actor(_actor1)
            score = score + (1)
            sfx(0)


def _draw():
    cls(col("dark_blue"))
    draw_scene()
    print("SCORE", 4, 4, col("white"))
    print(score, 44, 4, col("yellow"))
