"""Workstation: boot to a cartridge gallery (launcher), open a cartridge into the
desktop shell, and Home back to the gallery.

This is the v0.4 "everything is a cartridge" surface: the launcher lists every
.kcart it finds (protected system carts + the child's saved user carts) and runs
any of them -- wallpaper, game, app -- in the same DesktopShell. Driven by the
same discrete `press(button)` events as the shell, so it runs live or scripted.
"""

import json
import os

from . import palette as _pal
from .canvas import Canvas
from .cartridge import Cartridge
from .shell import DesktopShell

_C = _pal.NAMES
_TYPE_COLOR = {"wallpaper": "blue", "game": "red", "app": "green", "tool": "orange"}


class CartInfo:
    def __init__(self, path, title, type, system):
        self.path = path
        self.title = title
        self.type = type
        self.system = system


class Catalog:
    @staticmethod
    def scan(dirs):
        items = []
        for d in dirs:
            if not d or not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                if not name.endswith(".kcart"):
                    continue
                path = os.path.join(d, name)
                manifest_path = os.path.join(path, "manifest.json")
                if not os.path.isfile(manifest_path):
                    continue
                try:
                    with open(manifest_path, "r", encoding="utf-8") as fh:
                        m = json.load(fh)
                except (ValueError, OSError):
                    continue
                items.append(CartInfo(
                    path, m.get("title", name), m.get("type", "app"), bool(m.get("system", False))
                ))
        # system carts first, then by title -- stable, predictable gallery order.
        items.sort(key=lambda c: (not c.system, c.title.lower()))
        return items


class Launcher:
    COLS = 3

    def __init__(self, items):
        self.items = items
        self.sel = 0

    def press(self, btn):
        if not self.items:
            return
        n = len(self.items)
        if btn == "left":
            self.sel = (self.sel - 1) % n
        elif btn == "right":
            self.sel = (self.sel + 1) % n
        elif btn == "up":
            self.sel = (self.sel - self.COLS) % n
        elif btn == "down":
            self.sel = (self.sel + self.COLS) % n

    def selected(self):
        return self.items[self.sel] if self.items else None

    def draw(self, cv):
        cv.cls(_C["dark_blue"])
        cv.print("CARTRIDGES", 16, 12, _C["white"], 3)
        if not self.items:
            cv.print("NO CARTRIDGES FOUND", 16, 60, _C["peach"], 2)
            return
        tw, th = 140, 86
        gx, gy = 16, 46
        for i, item in enumerate(self.items):
            col_i = i % self.COLS
            row_i = i // self.COLS
            x = gx + col_i * (tw + 12)
            y = gy + row_i * (th + 12)
            selected = (i == self.sel)
            cv.rect(x, y, tw, th, _C["dark_purple"] if selected else _C["black"])
            cv.rectb(x, y, tw, th, _C["yellow"] if selected else _C["dark_grey"])
            cv.rect(x + 8, y + 8, tw - 16, 22, _C[_TYPE_COLOR.get(item.type, "indigo")])
            cv.print(item.title[:15], x + 10, y + 40, _C["white"], 2)
            cv.print(item.type.upper(), x + 10, y + 62, _C["peach"], 2)
        cv.print("ARROWS MOVE   RUN OPEN", 16, cv.h - 22, _C["light_grey"], 2)


class Workstation:
    def __init__(self, dirs, save_dir=None):
        self.dirs = list(dirs)
        self.save_dir = save_dir
        self.launcher = Launcher(Catalog.scan(self.dirs))
        self.canvas = Canvas()       # launcher's own surface
        self.screen = "launcher"
        self.shell = None

    def _rescan(self):
        prev = self.launcher.sel
        self.launcher = Launcher(Catalog.scan(self.dirs))
        self.launcher.sel = min(prev, max(0, len(self.launcher.items) - 1))

    def open_selected(self):
        info = self.launcher.selected()
        if info is None:
            return False
        try:
            cart = Cartridge.load(info.path)
        except Exception as exc:  # noqa: BLE001
            print("KidCode launcher: failed to load", info.path, exc)
            return False
        self.shell = DesktopShell(cart, save_dir=self.save_dir)
        self.screen = "desktop"
        return True

    def go_home(self):
        self.shell = None
        self.screen = "launcher"
        self._rescan()              # show any newly-saved user carts

    def press(self, btn):
        if self.screen == "launcher":
            if btn in ("run", "a"):
                self.open_selected()
            else:
                self.launcher.press(btn)
        else:  # desktop
            if btn == "home":
                self.go_home()
            else:
                self.shell.press(btn)

    def hold(self, name, down):
        # Gameplay (held) input goes to the running cartridge in desktop mode.
        if self.screen == "desktop" and self.shell and self.shell.mode == "desktop":
            self.shell.rt.input.set_held(name, down)

    def type_char(self, code):
        # Typed text goes to the code editor (when its panel is open).
        if self.screen == "desktop" and self.shell:
            self.shell.type_char(code)

    def click(self, x, y):
        # Pointer clicks reach the code/paint editor panels.
        if self.screen == "desktop" and self.shell:
            self.shell.click(x, y)

    def frame(self, dt):
        if self.screen == "launcher":
            self.launcher.draw(self.canvas)
        else:
            self.shell.frame(dt)

    def rgb888(self):
        if self.screen == "launcher":
            return self.canvas.to_rgb888()
        return self.shell.rgb888()

    def current_canvas(self):
        return self.canvas if self.screen == "launcher" else self.shell.rt.canvas
