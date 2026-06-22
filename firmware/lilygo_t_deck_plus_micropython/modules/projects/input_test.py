from kidcode import *

BUTTONS = (
    ("left", 4, 20),
    ("right", 68, 20),
    ("up", 4, 38),
    ("down", 68, 38),
    ("a", 4, 56),
    ("b", 68, 56),
    ("run", 4, 74),
    ("home", 68, 74),
)

tick = 0


def setup():
    global tick
    tick = 0


def update(dt):
    global tick
    tick += 1


def draw():
    clear(0)
    text("input test", 4, 4, 1)
    for name, x, y in BUTTONS:
        color = 2 if button(name) else 1
        rect(x, y, 8, 8, color, True)
        text(name, x + 12, y, color)
    text("tick " + str(tick), 4, 108, 1)
