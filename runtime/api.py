"""The v0.4 cartridge API surface, bound to a running DesktopRuntime.

`make_api(runtime)` returns the global namespace a cartridge's main.py executes
in: TIC-80-style drawing (cls/pix/line/rect/rectb/circ/circb/spr/print -- rect and
circ are filled, rectb/circb are outlines), input (btn/btnp), config access (cfg),
and helpers (rnd/flr/col/Image). The same names back the device runtime, so
cartridges are portable.
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

    def spr(n, x, y, colorkey=-1, scale=1):
        # TIC-80 spr(id, x, y[, colorkey, scale]): draw sprite `n` from the cart's
        # sheet. Also accepts an Image directly (ASCII-art sprites) -- then the 4th
        # positional is scale, e.g. spr(pet, x, y, scale=4).
        if isinstance(n, Image):
            return cv.spr(n, x, y, scale if colorkey == -1 else colorkey)
        sheet = runtime.cart.sheet if runtime.cart is not None else None
        if sheet is None:
            return
        img = sheet.tile_image(int(n), transparent=colorkey)
        if img is not None:
            cv.spr(img, x, y, scale)

    api = {
        # screen
        "W": cv.w,
        "H": cv.h,
        "cls": cv.cls,
        "pix": cv.pix,           # pix(x, y) reads, pix(x, y, c) writes
        "line": cv.line,
        "rect": cv.rect,         # filled (TIC-80)
        "rectb": cv.rectb,       # outline (TIC-80)
        "circ": cv.circ,         # filled (TIC-80)
        "circb": cv.circb,       # outline (TIC-80)
        "spr": spr,              # spr(id, x, y[, colorkey, scale]) or spr(Image, ...)
        "print": cv.print,       # print(s, x, y, color[, scale])
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
