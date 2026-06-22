from kidcode import *

box = None
dx = 1
dy = 1


def setup():
    global box, dx, dy
    box = sprite("robot", x=16, y=32, w=10, h=10)
    dx = 1
    dy = 1


def update(dt):
    global dx, dy
    if button("left"):
        dx = -2
    if button("right"):
        dx = 2
    if button("up"):
        dy = -2
    if button("down"):
        dy = 2

    box.x += dx
    box.y += dy

    if box.x <= 2 or box.x >= 116:
        dx = -dx
        box.x = max(2, min(116, box.x))
    if box.y <= 16 or box.y >= 116:
        dy = -dy
        box.y = max(16, min(116, box.y))


def draw():
    clear(0)
    text("bounce", 4, 4, 1)
    rect(1, 15, 126, 112, 5, False)
    draw_sprite(box)
