#!/usr/bin/env python3
"""Generate the teaser-site demo GIFs from the REAL shared console (headless).

This drives `runtime/host_app` (the same Workstation the T-Deck runs) with a
scripted, *visible* cursor and records short animated GIFs of each flow — play,
edit code, draw, blocks — into `site/media/`. Re-run it any time the console
changes to refresh the site's footage with the latest look:

    python tools/make_site_gifs.py                 # all scenes -> site/media/*.gif
    python tools/make_site_gifs.py --scene code    # just one
    python tools/make_site_gifs.py --scale 2 --fps 20   # smaller / smoother

It is deliberately a *little* hardcoded (top-bar icon positions, canvas region,
which carts to open). Those are the only brittle bits; if the bar layout moves,
tweak `BAR` / `CANVAS` below. Mode switches go through public Workstation methods
(`_open_menu` / `_open_paint` / `set_menu_view`), so they stay correct across
versions even if the pixel coords drift — only the cursor's aim would be off.
"""

import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from runtime import host_app  # noqa: E402

SYSTEM_CARTS = os.path.join(ROOT, "system_carts")
OUT_DIR = os.path.join(ROOT, "site", "media")
SAVE_DIR = os.path.expanduser("~/.moybyte/projects")

# --- the "a bit hardcody" bits (320x240 system canvas) ---------------------
# Approx screen positions of the left-cluster top-bar switcher icons + the paint
# canvas. Cursor aim only; the actual mode switch uses Workstation methods.
BAR_Y = 9
BAR = {"home": 9, "code": 48, "paint": 66, "map": 84, "blocks": 102}
CANVAS = (46, 40, 150, 168)   # paint grid region x0,y0,x1,y1


class Recorder:
    """Boots the console and records frames while a scripted cursor moves."""

    def __init__(self, fps):
        self.dt = 1.0 / fps
        self.ws = host_app.build_workstation(SAVE_DIR)
        self.ws.pointer.idle_ms = 10 ** 9          # never auto-hide the cursor
        self.ws.pointer.visible = True
        self.drv = host_app.ConsoleDriver(self.ws)
        self.frames = []
        self.cx, self.cy = 160, 120

    # -- low level ----------------------------------------------------------
    def _tick(self, click=False, down=False, press=None):
        p = self.ws.pointer
        p.x, p.y, p.visible = int(self.cx), int(self.cy), True
        self.ws.mark_dirty()                       # force a repaint every frame
        if press:
            self.drv.press(press)
        self.drv._click = click
        self.drv._down = down
        self.drv.frame(self.dt)
        cv = self.drv.current_canvas()
        self.frames.append((cv.w, cv.h, self.drv.rgb888()))

    def hold(self, n, down=False):
        for _ in range(n):
            self._tick(down=down)

    def move_to(self, x, y, steps=14, down=False):
        x0, y0 = self.cx, self.cy
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)                 # ease in/out
            self.cx = x0 + (x - x0) * t
            self.cy = y0 + (y - y0) * t
            self._tick(down=down)

    def tap(self, x, y, steps=14):
        self.move_to(x, y, steps)
        self._tick(click=True, down=True)          # press
        self._tick(down=False)                     # release

    def stroke(self, pts):
        """A visible drag that actually paints in the paint editor."""
        self.cx, self.cy = pts[0]
        self._tick(click=True, down=True)
        for (x, y) in pts[1:]:
            self.move_to(x, y, steps=6, down=True)
        self._tick(down=False)

    def type_text(self, text, per=1):
        for ch in text:
            self.drv.type_char(ord(ch))
            for _ in range(per):
                self._tick()

    def press(self, name, n=1):
        for _ in range(n):
            self._tick(press=name)

    # -- cart helpers -------------------------------------------------------
    def open_cart(self, name):
        dst = os.path.join(self.ws.carts_root, name)
        if not os.path.exists(dst):
            shutil.copytree(os.path.join(SYSTEM_CARTS, name), dst)
        self.ws.launcher.items = host_app.moy_carts.scan(self.ws.carts_root)
        for i, c in enumerate(self.ws.launcher.items):
            if os.path.basename(c["path"]) == name:
                self.ws.launcher.sel = i
                break
        # keep autoplay games lively
        orig = self.ws.open
        def _open():
            orig()
            if self.ws.cart and isinstance(self.ws.config, dict) and "autoplay" in self.ws.config:
                self.ws.config["autoplay"] = 1
                self.ws._start()
        self.ws.open = _open
        self.ws.open()


# --- scenes -----------------------------------------------------------------
def scene_play(fps):
    r = Recorder(fps)
    r.open_cart("star_catcher.moy")
    r.hold(4)
    r.move_to(90, 150); r.move_to(230, 90); r.move_to(160, 120)
    r.hold(50)
    return r.frames


def scene_code(fps):
    r = Recorder(fps)
    r.open_cart("star_catcher.moy")
    r.hold(4)
    r.tap(BAR["code"], BAR_Y)                       # cursor -> CODE
    r.ws._open_menu()
    if r.ws.menu_view != "code":
        r.ws.set_menu_view("code")
    r.hold(6)
    r.move_to(120, 90)                              # cursor into the text
    for _ in range(3):
        r.ws.nav(0, 1); r._tick()
    r.type_text("  # try changing me!", per=1)
    r.hold(14)
    return r.frames


def scene_draw(fps):
    r = Recorder(fps)
    r.open_cart("pet.moy")
    r.hold(4)
    r.tap(BAR["paint"], BAR_Y)                      # cursor -> DRAW
    r.ws._open_paint()
    r.hold(6)
    x0, y0, x1, y1 = CANVAS
    r.stroke([(70, 70), (95, 72), (120, 78), (135, 95)])
    r.stroke([(80, 120), (110, 128), (140, 120)])
    r.hold(16)
    return r.frames


def scene_blocks(fps):
    r = Recorder(fps)
    r.open_cart("star_catcher.moy")
    r.hold(4)
    r.tap(BAR["blocks"], BAR_Y)                     # cursor -> BLOCKS
    r.ws._open_menu()
    r.ws.set_menu_view("blocks")
    r.hold(8)
    r.move_to(160, 80); r.move_to(120, 150)
    for _ in range(3):
        r.ws.nav(0, 1); r._tick()
    r.hold(14)
    return r.frames


SCENES = {"play": scene_play, "code": scene_code,
          "draw": scene_draw, "blocks": scene_blocks}


def save_gif(frames, path, scale, fps, hold_last=10):
    from PIL import Image
    imgs = []
    for (w, h, buf) in frames:
        im = Image.frombytes("RGB", (w, h), buf)
        if scale != 1:
            im = im.resize((w * scale, h * scale), Image.NEAREST)
        imgs.append(im)
    imgs += [imgs[-1]] * hold_last                  # pause on the last frame
    pal = imgs[0].quantize(colors=64, method=Image.MEDIANCUT)
    q = [im.quantize(palette=pal, dither=Image.NONE) for im in imgs]
    dur = int(1000 / fps)
    q[0].save(path, save_all=True, append_images=q[1:], duration=dur,
              loop=0, optimize=True, disposal=2)
    kb = os.path.getsize(path) // 1024
    print("wrote %s  (%d frames, %dx%d, %dKB)"
          % (os.path.relpath(path, ROOT), len(q), imgs[0].width, imgs[0].height, kb))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", choices=list(SCENES) + ["all"], default="all")
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--fps", type=int, default=20)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    names = list(SCENES) if args.scene == "all" else [args.scene]
    for name in names:
        frames = SCENES[name](args.fps)
        save_gif(frames, os.path.join(args.out, "%s.gif" % name), args.scale, args.fps)


if __name__ == "__main__":
    main()
