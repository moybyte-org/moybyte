# My Art -- the static wallpaper published by the Paint app.

picture = None


def _init():
    global picture
    picture = image("bg")


def _draw():
    if picture is not None:
        spr(picture, 0, 0)
        return
    cls(col("white"))
    # A quiet placeholder before the first Paint save.
    rect(0, 184, W, 56, col("light_grey"))
    circ(246, 72, 24, col("yellow"))
    rect(34, 112, 8, 72, col("brown"))
    circ(38, 104, 30, col("pink"))
    circ(62, 119, 23, col("peach"))
