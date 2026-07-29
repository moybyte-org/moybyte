#!/usr/bin/env python3
"""Generate the README's demo GIFs from the REAL shared console (headless).

Drives `runtime/host_app` (the same `Workstation` the T-Deck runs) with a scripted,
*visible* cursor that taps real controls, and records short animated GIFs of the
v0.5 shell:

  paint   open the Editor on Pixel Pet -> SPRITES tab -> paint a smile on the
          pet's tile -> PLAY -> the pet is wearing it in the running game.
  code    open the Editor on Star Catcher -> CODE tab -> retype a constant ->
          PLAY -> the change is on screen.
  blocks  open the Editor on Tap Game -> BLOCKS tab -> snap a new block into the
          program -> CODE tab -> the same block, compiled to Python.
  tap     (not in the README) the Config "Make it mine" cards: pick another pet,
          PLAY, the game runs the new one.

Re-runnable any time the console changes so the README footage tracks the latest
look (`make site-gifs`). Interactions are REAL taps through handle_pointer and
real typed keys through the driver; every control rect is read from the live
layout objects (the Editor's lent bar zone, PaintLayout, CodeLayout, BlockLayout),
so they stay correct across versions.

The recording runs in a throwaway carts dir, so it never shows -- or disturbs --
whatever is in the developer's own ~/.moybyte store.

    python tools/make_site_gifs.py                 # all -> docs/media/*.gif
    python tools/make_site_gifs.py --scene code    # just one
    python tools/make_site_gifs.py --scale 3 --fps 20
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from runtime import host_app            # noqa: E402
from runtime import bar_layer as BAR    # noqa: E402  (the bar's lent-zone rect)
from runtime import editor_app as EDA   # noqa: E402  (the Editor's tab ladder)
from runtime import palette             # noqa: E402  (MOY64 -> the exact GIF palette)
from runtime import widgets as WID      # noqa: E402  (the achievement catalog)

SYSTEM_CARTS = os.path.join(ROOT, "system_carts")
OUT_DIR = os.path.join(ROOT, "docs", "media")
BACKSPACE = 0x08


def _c(rect):
    """Center (x, y) of a (x, y, w, h) control rect."""
    return (rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0)


class Recorder:
    """Boots the console and records frames while a scripted cursor taps real UI."""

    def __init__(self, fps, carts_dir):
        self.dt = 1.0 / fps
        self.ws = host_app.build_workstation(carts_dir)
        self.carts_dir = carts_dir
        # Pre-unlock every achievement. The celebration banner is real UI, but a
        # fresh store unlocks "First Steps" on the very first open and the toast
        # would then sit across the top of every recording's opening seconds.
        for entry in WID.ACHIEVEMENTS:
            self.ws.ach.unlocked[entry[0]] = True
        self.ws.pointer.idle_ms = 10 ** 9        # never auto-hide the cursor
        self.ws.pointer.visible = True
        self.ws.show_fps = False                 # no dev chip over the payoff shot
        self.drv = host_app.ConsoleDriver(self.ws)
        self.frames = []
        self.cursor = True
        self.cx, self.cy = 160, 120

    # -- frame primitives ---------------------------------------------------
    def _tick(self, click=False, down=False):
        p = self.ws.pointer
        p.x, p.y = int(round(self.cx)), int(round(self.cy))
        p.visible = self.cursor
        self.ws.mark_dirty()
        self.drv._click = click
        self.drv._down = down
        self.drv.frame(self.dt)
        cv = self.drv.current_canvas()
        cv.flush_batch()
        # Record INDICES, not RGB: the console is an indexed surface, so the GIF
        # can carry the MOY64 palette verbatim and skip quantization entirely.
        self.frames.append((cv.w, cv.h, bytes(cv.buf)))

    def settle(self, n):
        for _ in range(n):
            self._tick()

    def play(self, n):
        """Watch the running cart with the cursor parked: the payoff shot should be
        the game, not a stray arrow sitting on the button that started it."""
        self.cursor = False
        self.settle(n)
        self.cursor = True

    def move_to(self, x, y, steps=14, down=False):
        x0, y0 = self.cx, self.cy
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)              # ease in/out
            self.cx = x0 + (x - x0) * t
            self.cy = y0 + (y - y0) * t
            self._tick(down=down)

    def click(self, x, y, steps=14, dwell=7, after=8):
        """Glide to (x, y), pause so the target is legible, then a real tap."""
        self.move_to(x, y, steps)
        self.settle(dwell)                       # let the viewer see WHERE
        self._tick(click=True, down=True)        # press
        self._tick(down=False)                   # release
        self.settle(after)                       # let the UI react

    def stroke(self, pts, steps=5, dwell=3):
        """A visible drag that actually paints in the paint editor."""
        self.move_to(pts[0][0], pts[0][1])
        self.settle(dwell)
        self._tick(click=True, down=True)
        for (x, y) in pts[1:]:
            self.move_to(x, y, steps=steps, down=True)
        self._tick(down=False)

    def type_keys(self, codes, per=3):
        """Type through the REAL driver (queued bytes -> the console's key edge),
        with `per` idle frames between so each keystroke reads as one press."""
        for code in codes:
            self.drv.type_char(code)
            self._tick()                         # the frame that consumes it
            self.settle(per)

    # -- carts + navigation --------------------------------------------------
    def reset_cart(self, name, cfg=None):
        """Copy a pristine system cart into the store (so a re-run is deterministic),
        optionally overriding its config.json, then rescan and return its entry."""
        dst = os.path.join(self.ws.carts_root, name)
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(os.path.join(SYSTEM_CARTS, name), dst)
        if cfg:
            base = {}
            with open(os.path.join(dst, "manifest.json"), encoding="utf-8") as f:
                base.update(json.load(f).get("config") or {})
            base.update(cfg)
            with open(os.path.join(dst, "config.json"), "w", encoding="utf-8") as f:
                json.dump(base, f)
        items = host_app.moy_carts.scan(self.ws.carts_root)
        self.ws._apply_items(items)
        cart = next(c for c in self.ws._all_carts
                    if os.path.basename(c["path"]) == name)
        for i, it in enumerate(self.ws.launcher.items):
            if it.get("path") == cart.get("path"):
                self.ws.launcher.sel = i
                break
        return cart

    def open_editor(self, cart):
        """The Make tile -> project-picker -> pick destination, without the two
        grid screens: land the Editor on the cart's Config tab (spec Section 6)."""
        self.ws.open_in_editor(cart)

    # -- live control geometry ----------------------------------------------
    def zone_rect(self, tab):
        """The rect of one icon in the Editor's lent top-bar zone (the tab ladder:
        PROJECTS / Config / Blocks / Code / Sprites / Map / Scene / Music / UNDO /
        REDO / PLAY). `tab` is the ladder's tab name, or None for PLAY."""
        lay = getattr(self.ws.layout, "zone_left", None) or BAR._ZONE_LEFT_GAME
        x0, y0, _w, h = lay
        ic = h if h > 0 else BAR._BAR_ICON
        for i, (name, _glyph) in enumerate(EDA._ZONE_TABS):
            if name == tab:
                return (x0 + i * ic, y0, ic, ic)
        raise KeyError(tab)

    def tab(self, name, **kw):
        """Tap a tab-ladder icon (PLAY is `name=None`)."""
        self.click(*_c(self.zone_rect(name)), **kw)

    def swatch(self, idx):
        """Center of palette swatch `idx` in the paint editor's color column."""
        lay = self.ws.paint_layer.layout
        return (lay.sw_x0 + (idx % lay.sw_cols) * lay.sw + lay.sw / 2.0,
                lay.sw_y0 + (idx // lay.sw_cols) * lay.sw + lay.sw / 2.0)

    def cell(self, gx, gy):
        """Center of pixel (gx, gy) in the paint editor's zoomed sprite grid."""
        lay = self.ws.paint_layer.layout
        size = lay.pg_span // self.ws.paint.dim
        return (lay.pg_x0 + (gx + 0.5) * size, lay.pg_y0 + (gy + 0.5) * size)

    def block_row(self, idx):
        """Center-left point of outline row `idx` in the block editor."""
        bu = self.ws.block_ui
        lay = bu.block_layout
        area = lay.area()
        return (area[0] + area[2] / 2.0,
                lay.y0 + (idx - bu.blk_top + 0.5) * lay.row_h)

    def pick_menu(self, item, **kw):
        """Tap `item` in the block editor's open modal menu (a category name or a
        block id), scrolling it into view first."""
        bu = self.ws.block_ui
        menu = bu.blk_menu
        lay = bu.block_layout
        mx, my, mw, _mh = lay.menu
        idx = menu["items"].index(item)
        if idx >= menu["top"] + lay.menu_rows:
            menu["top"] = idx - lay.menu_rows + 1
        elif idx < menu["top"]:
            menu["top"] = idx
        y = my + 16 * lay.fs + (idx - menu["top"] + 0.5) * lay.menu_row_h
        self.click(mx + mw / 2.0, y, **kw)

    def caret_xy(self, row, col, above=5):
        """Screen center of character (row, col) in the code editor, scrolling the
        view so the line sits `above` rows down from the top first."""
        ws = self.ws
        ed = ws.editor
        lay = ws.code_layout
        ed.top = max(0, min(row - above, len(ed.lines) - lay.rows))
        ed.left = 0
        ws.mark_dirty()
        x0 = ws.code_layer._text_x0(lay, ed)
        return (x0 + (col + 0.5) * lay.cell,
                lay.y0 + (row - ed.top + 0.5) * lay.lh)


# --- scenes -----------------------------------------------------------------
def scene_paint(r):
    """The hero: paint a smile onto the pet's sprite, PLAY, it's wearing it.

    Pixel Pet's tile 0 is a frog with a flat red mouth bar; dragging the two
    corners up turns it into a smile, and the pet is drawn from that same tile at
    4x, so PLAY shows it immediately.
    """
    cart = r.reset_cart("pet.moy", cfg={"autoplay": 1})
    r.open_editor(cart)                      # lands on Config ("Make it mine")
    r.settle(14)
    r.tab("paint", after=12)                 # -> SPRITES
    r.click(*r.swatch(8), steps=12, after=6)  # red
    r.stroke([r.cell(*p) for p in
              ((1, 4), (2, 5), (3, 5), (4, 5), (5, 5), (6, 4))])
    r.settle(20)                             # the tile preview updates too
    r.tab(None, after=4)                     # PLAY
    r.play(46)                               # ...the pet is wearing it
    return r.frames


def scene_code(r):
    """The code editor is a tab in the same console: retype a constant, PLAY."""
    cart = r.reset_cart("star_catcher.moy", cfg={"autoplay": 1})
    r.open_editor(cart)
    r.settle(12)
    r.tab("code", after=14)
    ed = r.ws.editor
    row = next(i for i, line in enumerate(ed.lines) if line.startswith("SPR_SCALE"))
    col = ed.lines[row].index("=") + 3        # just past the value
    r.click(*r.caret_xy(row, col, above=3), steps=16, after=8)
    r.type_keys([BACKSPACE, ord("8")], per=5)
    r.settle(16)
    r.tab(None, after=4)                      # PLAY -> a much bigger catcher
    r.play(50)
    return r.frames


def scene_blocks(r):
    """Blocks compile to the same Python: snap a block in, then open the CODE tab
    on the very line it generated."""
    cart = r.reset_cart("tap_game.moy")
    r.open_editor(cart)
    r.ws.editor_app.open_blocks()             # open ON the Blocks tab (Tap Game is
    r.settle(12)                              # a block cart -- that IS its source)
    bu = r.ws.block_ui
    lay = bu.block_layout
    r.click(*r.block_row(5), steps=11, after=8)   # the "+" slot after "move coin"
    r.click(*_c(lay.add_btn), steps=11, after=10)  # ADD -> "PICK A KIND"
    r.pick_menu("sound", steps=11, dwell=9, after=10)  # ...a category...
    r.pick_menu("beep", steps=11, dwell=9, after=6)    # ...and the block itself
    r.settle(14)                              # the new block, snapped in
    # "<< CODE" is the block editor's own graduate rung: compile the outline and
    # open the generated main.py in the code tab.
    r.click(*_c(lay.code_btn), steps=12, after=10)
    ed = r.ws.editor
    row = next((i for i, line in enumerate(ed.lines) if "beep(440)" in line), 0)
    r.click(*r.caret_xy(row, len(ed.lines[row])), steps=14, after=8)
    r.settle(22)
    return r.frames


def scene_tap(r):
    """The "Make it mine" cards: pick another pet on the Config tab, then PLAY."""
    cart = r.reset_cart("pet.moy", cfg={"autoplay": 1})
    r.open_editor(cart)
    r.settle(20)
    rows = r.ws.cards_layer._card_layout()
    row = next(x for x in rows if x["f"]["key"] == "pet")
    cur = r.ws.config.get("pet", 0)
    target = None
    for k, box in r.ws.cards_layer._choice_cells(row):
        if row["f"]["choices"][k] != cur:
            target = _c(box)
            break
    if target:
        r.click(*target, steps=12, after=14)
    r.tab(None, after=4)                      # PLAY
    r.play(44)
    return r.frames


SCENES = {"paint": scene_paint, "code": scene_code,
          "blocks": scene_blocks, "tap": scene_tap}


def save_gif(frames, path, scale, fps, hold_last=18):
    """Write the recorded index frames as one GIF.

    Two things keep these small enough to live in a repo everyone clones:
    the palette is MOY64 *verbatim* (the console is an indexed surface, so there
    is no quantization step and no color drift), and frames are written with
    `disposal=1`, which lets Pillow ship each frame as only the rectangle that
    actually changed -- a still editor screen costs a few bytes, and an identical
    frame costs nothing at all (its duration is folded into the one before it).
    """
    from PIL import Image
    flat = []
    for rgb in palette.MOY64:
        flat.extend(rgb)
    flat.extend([0] * (768 - len(flat)))
    imgs = []
    for (w, h, buf) in frames:
        im = Image.frombytes("P", (w, h), buf)
        im.putpalette(flat)
        if scale != 1:
            im = im.resize((w * scale, h * scale), Image.NEAREST)
        imgs.append(im)
    imgs += [imgs[-1]] * hold_last
    imgs[0].save(path, save_all=True, append_images=imgs[1:],
                 duration=int(round(1000.0 / fps)), loop=0, optimize=True,
                 disposal=1)
    kb = os.path.getsize(path) // 1024
    print("wrote %s  (%d frames, %dx%d, %dKB)"
          % (os.path.relpath(path, ROOT), len(imgs), imgs[0].width,
             imgs[0].height, kb))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", choices=list(SCENES) + ["all"], default="all")
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--fps", type=int, default=20)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    names = list(SCENES) if args.scene == "all" else [args.scene]
    for name in names:
        carts = tempfile.mkdtemp(prefix="moybyte-gif-")
        try:
            rec = Recorder(args.fps, carts)
            save_gif(SCENES[name](rec),
                     os.path.join(args.out, "%s.gif" % name), args.scale, args.fps)
        finally:
            shutil.rmtree(carts, ignore_errors=True)


if __name__ == "__main__":
    main()
