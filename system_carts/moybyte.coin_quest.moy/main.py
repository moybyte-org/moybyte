# Made with Moybyte blocks. Edit in the block editor (or graduate to code).

score = 0


def _init():
    global score
    score = 0


def _update(dt):
    global score
    for _self in actors("player"):
        if btn("left"):
            move_actor(_self, -2, 0)
        if btn("right"):
            move_actor(_self, 2, 0)
        if btn("up"):
            move_actor(_self, 0, -2)
        if btn("down"):
            move_actor(_self, 0, 2)
    for _self in actors("coin"):
        if touching(_self, "player"):
            remove_actor(_self)
            score = score + (1)
            sfx(0)


def _draw():
    cls(col("dark_blue"))
    draw_scene()
    print("SCORE", 4, 4, col("white"))
    print(score, 44, 4, col("yellow"))
