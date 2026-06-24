"""DesktopRuntime: load a cartridge, run its _init/_update/_draw lifecycle, and
recover from cartridge errors into a friendly on-canvas error screen.

This is the v0.4 "press Run" loop, host side. A bad cartridge never crashes the
host -- it draws an error card and keeps running, mirroring the device promise.
"""

from .api import make_api
from .canvas import Canvas
from .input import InputState


class DesktopRuntime:
    def __init__(self, canvas=None, input=None):
        self.canvas = canvas or Canvas()
        self.input = input or InputState()
        self.cart = None
        self.ns = None
        self._init = None
        self._update = None
        self._draw = None
        self.error = None

    def load(self, cart):
        """Load and start a cartridge. Returns True on success, False on error."""
        self.cart = cart
        self.error = None
        self._init = self._update = self._draw = None
        ns = make_api(self)
        try:
            exec(cart.main_source, ns)
            self.ns = ns
            self._init = ns.get("_init")
            self._update = ns.get("_update")
            self._draw = ns.get("_draw")
            if self._init:
                self._init()
        except Exception as exc:  # noqa: BLE001 - sandbox: any cart error is contained
            self._fail(exc)
            return False
        return True

    def step(self, dt):
        self.input.begin_frame()
        if self.error is not None:
            self._draw_error()
            return
        try:
            if self._update:
                self._update(dt)
            if self._draw:
                self._draw()
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            self._draw_error()

    def frame_rgb888(self):
        return self.canvas.to_rgb888()

    # -- error handling ------------------------------------------------------

    def _fail(self, exc):
        self.error = exc
        print("KidCode cartridge error:", repr(exc))

    def _draw_error(self):
        cv = self.canvas
        cv.cls(_pal_index("dark_purple"))
        cv.rect(8, 8, cv.w - 16, cv.h - 16, _pal_index("pink"))
        cv.print("OOPS", 20, 24, _pal_index("white"), scale=4)
        msg = str(self.error)
        # wrap to the canvas width at the 3x5 font's ~8px/char (scale 2).
        per_line = max(1, (cv.w - 40) // 8)
        y = 70
        for i in range(0, len(msg), per_line):
            cv.print(msg[i:i + per_line], 20, y, _pal_index("peach"), scale=2)
            y += 14
            if y > cv.h - 30:
                break


def _pal_index(name):
    from . import palette
    return palette.NAMES.get(name, 7)
