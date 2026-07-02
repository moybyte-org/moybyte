#!/usr/bin/env python3
"""Generate the teaser-site demo GIFs from the REAL shared console (headless).

Drives `runtime/host_app` (the same Workstation the T-Deck runs) with a scripted,
*visible* cursor that taps real controls, and records short animated GIFs telling
the "three ages of making" story, plus a paint gag:

  tap    (age ~5)  open a game -> EDIT -> tap the CATCHER property -> GO ->
                    the character changes.
  blocks (age ~7)  open the block editor -> tap ADD -> snap a new block in.
  code   (age ~12) EDIT -> tap CODE -> change SPR_SCALE 4 -> 16 -> RUN ->
                    the character grows.
  paint            open the paint editor -> draw on the sprite -> back to the
                    game, the character wears it.

Re-runnable any time the console changes so the site footage tracks the latest
look (`make site-gifs`). Interactions use REAL clicks through handle_pointer;
control rects are read from the console module / live layout objects, so they
stay correct across versions (only a few fixed coords remain, noted inline).

    python tools/make_site_gifs.py                 # all -> site/media/*.gif
    python tools/make_site_gifs.py --scene code    # just one
    python tools/make_site_gifs.py --scale 2 --fps 20
"""

import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from runtime import host_app          # noqa: E402
from runtime import console as C      # noqa: E402  (control-rect constants live here)

SYSTEM_CARTS = os.path.join(ROOT, "system_carts")
OUT_DIR = os.path.join(ROOT, "site", "media")
SAVE_DIR = os.path.expanduser("~/.moybyte/projects")


def _c(rect):
    """Center (x, y) of a (x, y, w, h) control rect."""
    return (rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0)


class Recorder:
    """Boots the console and records frames while a scripted cursor taps real UI."""

    def __init__(self, fps):
        self.dt = 1.0 / fps
        self.ws = host_app.build_workstation(SAVE_DIR)
        self.ws.pointer.idle_ms = 10 ** 9        # never auto-hide the cursor
        self.ws.pointer.visible = True
        self.drv = host_app.ConsoleDriver(self.ws)
        self.frames = []
        self.cx, self.cy = 160, 120

    # -- frame primitives ---------------------------------------------------
    def _tick(self, click=False, down=False, press=None):
        p = self.ws.pointer
        p.x, p.y, p.visible = int(round(self.cx)), int(round(self.cy)), True
        self.ws.mark_dirty()
        if press:
            self.drv.press(press)
        self.drv._click = click
        self.drv._down = down
        self.drv.frame(self.dt)
        cv = self.drv.current_canvas()
        self.frames.append((cv.w, cv.h, self.drv.rgb888()))

    def settle(self, n):
        for _ in range(n):
            self._tick()

    def move_to(self, x, y, steps=18, down=False):
        x0, y0 = self.cx, self.cy
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)              # ease in/out
            self.cx = x0 + (x - x0) * t
            self.cy = y0 + (y - y0) * t
            self._tick(down=down)

    def click(self, x, y, steps=18, dwell=6, after=10):
        """Glide to (x, y), pause so the target is legible, then a real tap."""
        self.move_to(x, y, steps)
        self.settle(dwell)                       # let the viewer see WHERE
        self._tick(click=True, down=True)        # press
        self._tick(down=False)                   # release
        self.settle(after)                       # let the UI react

    def stroke(self, pts):
        """A visible drag that actually paints in the paint editor."""
        self.cx, self.cy = pts[0]
        self.settle(3)
        self._tick(click=True, down=True)
        for (x, y) in pts[1:]:
            self.move_to(x, y, steps=7, down=True)
        self._tick(down=False)

    def type_keys(self, codes, per=2):
        for code in codes:
            if self.ws.editor is not None:
                self.ws.editor.key(code)
            self.ws.mark_dirty()
            self.settle(per)

    # -- cart -------------------------------------------------------------
    def open_cart(self, name, autoplay=True):
        # Always reset to the pristine system cart so scenes are deterministic
        # (GO/edits otherwise persist config into ~/.moybyte and leak between runs).
        dst = os.path.join(self.ws.carts_root, name)
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(os.path.join(SYSTEM_CARTS, name), dst)
        self.ws.launcher.items = host_app.moy_carts.scan(self.ws.carts_root)
        for i, c in enumerate(self.ws.launcher.items):
            if os.path.basename(c["path"]) == name:
                self.ws.launcher.sel = i
                break
        orig = self.ws.open
        def _open():
            orig()
            if autoplay and self.ws.cart and isinstance(self.ws.config, dict) \
                    and "autoplay" in self.ws.config:
                self.ws.config["autoplay"] = 1
                self.ws._start()
        self.ws.open = _open
        self.ws.open()


# --- scenes -----------------------------------------------------------------
def scene_tap(fps):
    """Age ~5: tap a property, press GO, the character changes."""
    r = Recorder(fps)
    r.open_cart("star_catcher.moy")
    r.settle(28)
    r.click(*_c(C._MENU_BTN))                     # EDIT (Make it mine)
    r.settle(18)
    # tap the OTHER catcher tile on the CATCHER card (real hit-test)
    row = next(x for x in r.ws._card_layout() if x["f"]["key"] == "basket")
    cur = r.ws.config.get("basket", 0)
    target = None
    for k, cell in r.ws._choice_cells(row):
        if row["f"]["choices"][k] != cur:
            target = _c(cell); break
    if target:
        r.click(*target, steps=14)
    r.settle(14)
    r.click(*_c(C._RUN_BTN))                      # GO -> apply + play
    r.settle(46)
    return r.frames


def scene_blocks(fps):
    """Age ~7: tap a `+` slot, pick a category, snap a block into the program."""
    r = Recorder(fps)
    r.open_cart("star_catcher.moy")
    r.settle(20)
    r.click(*_c(C._BLOCKS_BTN))                   # BLOCKS
    r.settle(18)
    be = r.ws.blocks_ed
    lay = r.ws.block_layout
    # select a real `+` insert slot in the outline
    inserts = [i for i, row in enumerate(be.rows) if row.kind == "insert"]
    be.cur = inserts[1] if len(inserts) > 1 else inserts[0]
    r.ws.blk_slot = 0
    r.ws._blk_reveal()
    r.ws.mark_dirty(); r.settle(8)
    # tap that `+` row (cursor is already the selected row -> opens the insert menu)
    ry = lay.y0 + (be.cur - r.ws.blk_top) * lay.row_h + lay.row_h / 2
    ax = lay.area()[0] + lay.area()[2] / 2
    r.click(ax, ry)                              # -> "PICK A KIND"
    r.settle(16)
    # pick the DRAW category, then its first block (cursor hovers the menu for show)
    mx, my, mw, mh = lay.menu
    if r.ws.blk_menu is not None:
        cats = r.ws.blk_menu["items"]
        di = cats.index("draw") if "draw" in cats else 0
        r.move_to(mx + mw / 2, my + 16 * lay.fs + (di + 0.5) * lay.menu_row_h)
        r.settle(6)
        r.ws.blk_menu["sel"] = di
        r.ws._blk_menu_select(); r.ws.mark_dirty(); r.settle(14)
    if r.ws.blk_menu is not None:               # now the blocks in that category
        r.move_to(mx + mw / 2, my + 16 * lay.fs + 0.5 * lay.menu_row_h)
        r.settle(6)
        r.ws.blk_menu["sel"] = 0
        r.ws._blk_menu_select(); r.ws.mark_dirty(); r.settle(6)
    r.ws._blk_reveal(); r.ws.mark_dirty()
    r.settle(34)                                # show the new block snapped in
    return r.frames


def scene_code(fps):
    """Age ~12: EDIT -> CODE -> change SPR_SCALE 4->16 -> RUN, character grows."""
    r = Recorder(fps)
    r.open_cart("star_catcher.moy")
    r.settle(22)
    r.click(*_c(C._MENU_BTN))                     # EDIT
    r.settle(16)
    r.click(*_c(C._CODE_BTN))                     # CODE tab (the click we missed)
    r.settle(16)
    ed = r.ws.editor
    # find the SPR_SCALE line and put the caret just after its value
    for i, line in enumerate(ed.lines):
        if line.startswith("SPR_SCALE"):
            ed.row = i
            ed.col = line.index("=") + 2 + len(line.split("=")[1].split()[0])
            ed.top = max(0, i - 4)
            break
    r.ws.mark_dirty(); r.settle(10)
    r.type_keys([0x08, ord("1"), ord("6")], per=3)   # backspace '4' -> type '16'
    r.settle(12)
    r.click(*_c(r.ws.code_layout.run_btn))       # RUN
    r.settle(48)
    return r.frames


def scene_paint(fps):
    """Draw two happy dots on the character, then back to the game wearing them."""
    r = Recorder(fps)
    r.open_cart("pet.moy")
    r.settle(18)
    r.click(*_c(C._PAINT_BTN))                    # DRAW
    r.settle(16)
    pe = r.ws.paint
    pe.color = 0                                  # black dots read as cheeky eyes
    cell = C._PG_SPAN // pe.dim

    def cellxy(gx, gy):
        return (C._PG_X0 + (gx + 0.5) * cell, C._PG_Y0 + (gy + 0.5) * cell)

    d = pe.dim
    r.click(*cellxy(round(d * 0.30), round(d * 0.40)), steps=16)   # left dot
    r.settle(8)
    r.click(*cellxy(round(d * 0.66), round(d * 0.40)), steps=12)   # right dot
    r.settle(16)
    r.click(*_c(C._CLOSE_BTN))                    # back to the game
    r.settle(30)
    return r.frames


SCENES = {"tap": scene_tap, "blocks": scene_blocks,
          "code": scene_code, "paint": scene_paint}


def save_gif(frames, path, scale, fps, hold_last=14):
    from PIL import Image
    imgs = []
    for (w, h, buf) in frames:
        im = Image.frombytes("RGB", (w, h), buf)
        if scale != 1:
            im = im.resize((w * scale, h * scale), Image.NEAREST)
        imgs.append(im)
    imgs += [imgs[-1]] * hold_last
    pal = imgs[0].quantize(colors=64, method=Image.MEDIANCUT)
    q = [im.quantize(palette=pal, dither=Image.NONE) for im in imgs]
    q[0].save(path, save_all=True, append_images=q[1:], duration=int(1000 / fps),
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
        save_gif(SCENES[name](args.fps),
                 os.path.join(args.out, "%s.gif" % name), args.scale, args.fps)


if __name__ == "__main__":
    main()
