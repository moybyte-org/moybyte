"""Public KidCode API functions."""

import random

from .app import game
from .runtime import current_context

colors = {
    "black": 0,
    "white": 1,
    "red": 2,
    "green": 3,
    "blue": 4,
    "yellow": 5,
    "pink": 6,
    "cyan": 7,
}


class _AudioProxy:
    def __getattr__(self, name):
        return getattr(current_context().audio, name)


class _FilesProxy:
    def __getattr__(self, name):
        return getattr(current_context().files, name)


class _RadioProxy:
    def __getattr__(self, name):
        return getattr(current_context().radio, name)


audio = _AudioProxy()
files = _FilesProxy()
radio = _RadioProxy()


def run(update=None, draw=None):
    current_context().register_run(update=update, draw=draw)


def sprite(name, x=0, y=0, w=8, h=8):
    return current_context().create_sprite(name, x=x, y=y, w=w, h=h)


def draw_sprite(item):
    current_context().screen.draw_sprite(item)


def clear(color=0):
    current_context().screen.clear(color)


def text(value, x, y, color=1):
    current_context().screen.text(value, x, y, color=color)


def rect(x, y, w, h, color=1, fill=True):
    current_context().screen.rect(x, y, w, h, color=color, fill=fill)


def circle(x, y, r, color=1, fill=False):
    current_context().screen.circle(x, y, r, color=color, fill=fill)


def line(x1, y1, x2, y2, color=1):
    current_context().screen.line(x1, y1, x2, y2, color=color)


def button(name):
    return current_context().input.held(name)


def button_pressed(name):
    return current_context().input.pressed(name)


def button_released(name):
    return current_context().input.released(name)


def beep():
    current_context().audio.beep()


def random_int(low, high):
    return random.randint(low, high)


def random_x():
    return random.randint(0, current_context().screen.width - 1)


def random_y():
    return random.randint(0, current_context().screen.height - 1)
