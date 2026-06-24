"""The v0.4 cartridge API surface, bound to a running DesktopRuntime.

`make_api(runtime)` returns the global namespace a cartridge's main.py executes
in: PICO-8-style drawing (cls/pset/line/rect/rectfill/circ/spr/text), input
(btn/btnp), config access (cfg), and helpers (rnd/flr/col/Image). The same names
will back a device runtime, so cartridges are portable.
"""

import random

from . import palette as _pal
from .canvas import Image


def make_api(runtime):
    cv = runtime.canvas

    def cfg(key, default=None):
        return runtime.cart.config.get(key, default) if runtime.cart else default

    def col(name_or_index):
        return _pal.color(name_or_index)

    def rnd(n=1.0):
        return random.random() * n

    def flr(x):
        return int(x // 1)

    def image(rows, mapping, transparent="."):
        return Image.from_ascii(rows, mapping, transparent=transparent)

    api = {
        # screen
        "W": cv.w,
        "H": cv.h,
        "cls": cv.cls,
        "pset": cv.pset,
        "pget": cv.pget,
        "line": cv.line,
        "rect": cv.rect,
        "rectfill": cv.rectfill,
        "circ": cv.circ,
        "circfill": cv.circfill,
        "spr": cv.spr,
        "text": cv.print,        # text(s, x, y, color[, scale])
        # input
        "btn": runtime.input.held,
        "btnp": runtime.input.pressed,
        "pointer": lambda: runtime.input.pointer,
        # config + helpers
        "cfg": cfg,
        "col": col,
        "rnd": rnd,
        "flr": flr,
        "Image": Image,
        "image": image,
    }
    return api
