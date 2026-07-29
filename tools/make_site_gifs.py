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
    python tools/make_site_gifs.py --windowed --out docs/media/desktop \
        --wallpaper open_machine --scene paint     # the desktop tier's set
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
from runtime import ui as UI            # noqa: E402  (labeled tab-row geometry)
from runtime import widgets as WID      # noqa: E402  (the achievement catalog)

SYSTEM_CARTS = os.path.join(ROOT, "system_carts")
OUT_DIR = os.path.join(ROOT, "docs", "media")
BACKSPACE = 0x08

# The WINDOWED desktop tier (--windowed): where the Editor window is parked so the
# desk's icon column, the wallpaper and the taskbar all stay in frame -- the whole
# point of recording this tier is the desktop AROUND the app, so the default
# near-fullscreen "Make" window is nudged in. And where the playtest window lands
# on PLAY, so the Editor stays readable beside it (spec Section 3's canonical
# picture) instead of the cascade burying it.
# The size is chosen, not arbitrary: below ~660px of window width the Editor's
# labeled tab row (CONFIG / BLOCKS / CODE / SPRITES / ...) collapses to the
# frozen icon ladder, which is exactly the thing this tier does better.
_EDITOR_WIN = (76, 44, 760, 400)        # x, y, w, h on a 1024x600 desk
_PLAYER_WIN = (348, 96)                 # x, y -- size stays the WM's integer scale


def _c(rect):
    """Center (x, y) of a (x, y, w, h) control rect."""
    return (rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0)


class Recorder:
    """Boots the console and records frames while a scripted cursor taps real UI."""

    def __init__(self, fps, carts_dir, sys_size=None, font_scale=1, windowed=False,
                 wallpaper=None):
        self.dt = 1.0 / fps
        self.ws = host_app.build_workstation(carts_dir, sys_size=sys_size,
                                             font_scale=font_scale,
                                             windowed=windowed)
        self.carts_dir = carts_dir
        # The desktop BACKDROP is a wallpaper-type cart chosen by slug (#28) -- the
        # same id the Appearance app persists, so `--wallpaper open_machine` records
        # exactly what a kid who picked it would see. None keeps the boot default
        # (the first wallpaper cart in the store). persist=False: a recording must
        # not depend on -- or leave -- a system.json, and the store is a temp dir.
        if wallpaper:
            self.ws.select_wallpaper(wallpaper, persist=False)
            if self.ws.wallpaper_id != wallpaper:
                raise SystemExit("no such wallpaper: %s (have: %s)"
                                 % (wallpaper, ", ".join(self.ws.wallpaper_options())))
        # The WINDOWED desktop tier (wm_windowed.WindowedWM): every geometry
        # helper below then resolves inside the active window's layout context
        # and translates to desktop coordinates -- see `local()`.
        self.windowed = windowed and hasattr(self.ws.wm, "desk_open")
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
        cv = self.ws.sys_canvas
        self.cx, self.cy = cv.w // 2, cv.h // 2

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

    def desk(self, n):
        """An establishing beat on the bare DESK before any window opens -- the
        windowed tier's own opening shot (wallpaper + system-app icon column +
        the one OS bar). A no-op on the fullscreen tier, which has no desk."""
        if self.windowed:
            self.settle(n)

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
        grid screens: land the Editor on the cart's Config tab (spec Section 6).
        On the windowed tier this also parks the "Make" window off the desk's
        icon column so the desktop stays in frame."""
        self.ws.open_in_editor(cart)
        if not self.windowed:
            return
        wm = self.ws.wm
        wm._sync_windows()
        win = wm._wins["make"]
        x, y, w, h = _EDITOR_WIN
        win.x, win.y = x, y
        wm._resize_window(win, w, h)     # rebuilds the buffer + layout context
        wm._prewarm(win)

    # -- live control geometry ----------------------------------------------
    #
    # Every rect below is read from a LIVE layout object. On the fullscreen tier
    # those layouts are the console's own; on the windowed tier each window has
    # its own `_LayoutCtx` (the app reflows to the window's size), so the helper
    # installs that context to read the geometry and then translates the result
    # out of window-local space into desktop coordinates. `local()` is the one
    # place that seam lives -- every helper goes through it, so the scenes below
    # are written once and run on both tiers.

    def local(self, fn, key="make"):
        """Resolve `fn()` -> (x, y) (or a rect) in the active window's layout
        context and translate it to desktop coordinates. A pass-through on the
        fullscreen tier."""
        if not self.windowed:
            return fn()
        wm = self.ws.wm
        wm._sync_windows()
        win = wm._wins[key]
        wm._install(win.ctx)
        try:
            out = fn()
        finally:
            wm._install(wm._root_ctx)
        ox, oy = win.x + 1, win.y + 1 + win.title_h
        if len(out) == 4:
            return (out[0] + ox, out[1] + oy, out[2], out[3])
        return (out[0] + ox, out[1] + oy)

    def _zone_rect_local(self, tab):
        """The Editor's lent top-bar zone resolves two ways: the frozen 320x240
        icon LADDER (PROJECTS / the seven tabs / UNDO / REDO / PLAY), or -- on any
        roomier surface, which is every window on the desktop tier -- the labeled
        CHIP row (`ui.tab_row`). Ask the same geometry the tap dispatch uses."""
        ws = self.ws
        rect = ws.bar_layer._zone_rect("menu")
        if getattr(ws.layout, "_base", True):
            x0, y0, _w, h = rect
            ic = h if h > 0 else BAR._BAR_ICON
            for i, (name, _glyph) in enumerate(EDA._ZONE_TABS):
                if name == tab:
                    return (x0 + i * ic, y0, ic, ic)
            raise KeyError(tab)
        proj, tabs_area, play_r = ws.editor_app._zone_parts(rect)
        if tab is None:
            return play_r
        if tab == EDA._ZONE_PROJECTS:
            return proj
        fs = max(1, rect[3] // 16)
        slim = [(tid, label) for tid, label, _ic in EDA._TAB_CHIPS]
        for tid, r, _labels_on in UI.tab_row_rects(tabs_area, slim, fs):
            if tid == tab:
                return r
        raise KeyError(tab)

    def zone_rect(self, tab):
        return self.local(lambda: self._zone_rect_local(tab))

    def tab(self, name, **kw):
        """Tap a tab-ladder icon / labeled chip (PLAY is `name=None`)."""
        self.click(*_c(self.zone_rect(name)), **kw)

    def swatch(self, idx):
        """Center of palette swatch `idx` in the paint editor's color column."""
        def _r():
            lay = self.ws.paint_layer.layout
            return (lay.sw_x0 + (idx % lay.sw_cols) * lay.sw + lay.sw / 2.0,
                    lay.sw_y0 + (idx // lay.sw_cols) * lay.sw + lay.sw / 2.0)
        return self.local(_r)

    def cell(self, gx, gy):
        """Center of pixel (gx, gy) in the paint editor's zoomed sprite grid."""
        def _r():
            lay = self.ws.paint_layer.layout
            size = lay.pg_span // self.ws.paint.dim
            return (lay.pg_x0 + (gx + 0.5) * size, lay.pg_y0 + (gy + 0.5) * size)
        return self.local(_r)

    def _block_layout(self):
        """The block editor's LIVE layout. On a roomy surface the Blocks tab splits
        into the Blocks+Scene workspace (#93/#85) and re-bounds its layout to the
        left pane -- `_layout_workspace()` is what both the draw and the tap path run
        first, so anything reading geometry must run it too or it hit-tests against
        the unsplit rects (silently landing taps in the scene pane)."""
        bu = self.ws.block_ui
        bu._layout_workspace()
        return bu.block_layout

    def last_insert_of_first_script(self):
        """The "+" slot at the END of the outline's FIRST script (its `on_start`):
        the row index just before the second top-level hat. Derived, not hardcoded,
        so the scene reads the same on any block cart."""
        rows = self.ws.block_ui.blocks_ed.rows
        nxt = next((i for i in range(1, len(rows))
                    if rows[i].kind == "block" and rows[i].depth == 0), len(rows))
        return nxt - 1

    def block_btn(self, name):
        """A button rect in the block editor's action bar (ADD / DEL / << CODE)."""
        return self.local(lambda: getattr(self._block_layout(), name))

    def block_row(self, idx):
        """Center-left point of outline row `idx` in the block editor."""
        def _r():
            bu = self.ws.block_ui
            lay = self._block_layout()
            area = lay.area()
            return (area[0] + area[2] / 2.0,
                    lay.y0 + (idx - bu.blk_top + 0.5) * lay.row_h)
        return self.local(_r)

    def pick_menu(self, item, **kw):
        """Tap `item` in the block editor's open modal menu (a category name or a
        block id), scrolling it into view first."""
        def _r():
            bu = self.ws.block_ui
            menu = bu.blk_menu
            lay = self._block_layout()
            mx, my, mw, _mh = lay.menu
            idx = menu["items"].index(item)
            if idx >= menu["top"] + lay.menu_rows:
                menu["top"] = idx - lay.menu_rows + 1
            elif idx < menu["top"]:
                menu["top"] = idx
            return (mx + mw / 2.0,
                    my + 16 * lay.fs + (idx - menu["top"] + 0.5) * lay.menu_row_h)
        self.click(*self.local(_r), **kw)

    def caret_xy(self, row, col, above=5):
        """Screen center of character (row, col) in the code editor, scrolling the
        view so the line sits `above` rows down from the top first."""
        def _r():
            ws = self.ws
            ed = ws.editor
            lay = ws.code_layout
            ed.top = max(0, min(row - above, len(ed.lines) - lay.rows))
            ed.left = 0
            ws.mark_dirty()
            x0 = ws.code_layer._text_x0(lay, ed)
            return (x0 + (col + 0.5) * lay.cell,
                    lay.y0 + (row - ed.top + 0.5) * lay.lh)
        return self.local(_r)

    def code_rows(self):
        """How many code lines the editor shows (differs per tier/window size)."""
        return self.local(lambda: (self.ws.code_layout.rows, 0))[0] \
            if not self.windowed else self._win_code_rows()

    def _win_code_rows(self):
        wm = self.ws.wm
        wm._install(wm._wins["make"].ctx)
        try:
            return self.ws.code_layout.rows
        finally:
            wm._install(wm._root_ctx)

    # -- PLAY (windowed): park the playtest window beside the Editor ----------
    def park_player(self):
        """On the desktop tier a PLAY spawns a player WINDOW on the cascade, which
        lands centered on top of the Editor. Move it so the Editor's tab row and
        its work stay visible next to the running cart -- the tier's whole point."""
        if not self.windowed:
            return
        wm = self.ws.wm
        wm._sync_windows()
        win = wm._wins.get("desktop")
        if win is not None:
            win.x, win.y = _PLAYER_WIN
        self.ws.mark_dirty()


# --- scenes -----------------------------------------------------------------
def scene_paint(r):
    """The hero: paint a smile onto the pet's sprite, PLAY, it's wearing it.

    Pixel Pet's tile 0 is a frog with a flat red mouth bar; dragging the two
    corners up turns it into a smile, and the pet is drawn from that same tile at
    4x, so PLAY shows it immediately.
    """
    cart = r.reset_cart("pet.moy", cfg={"autoplay": 1})
    r.desk(16)                               # (windowed tier only) the desktop
    r.open_editor(cart)                      # lands on Config ("Make it mine")
    r.settle(14)
    r.tab("paint", after=12)                 # -> SPRITES
    r.click(*r.swatch(8), steps=12, after=6)  # red
    r.stroke([r.cell(*p) for p in
              ((1, 4), (2, 5), (3, 5), (4, 5), (5, 5), (6, 4))])
    r.settle(20)                             # the tile preview updates too
    r.tab(None, after=2)                     # PLAY
    r.park_player()
    r.play(46)                               # ...the pet is wearing it
    return r.frames


def scene_code(r):
    """The code editor is a tab in the same console: retype a constant, PLAY."""
    cart = r.reset_cart("star_catcher.moy", cfg={"autoplay": 1})
    r.desk(16)
    r.open_editor(cart)
    r.settle(12)
    r.tab("code", after=14)
    ed = r.ws.editor
    row = next(i for i, line in enumerate(ed.lines) if line.startswith("SPR_SCALE"))
    col = ed.lines[row].index("=") + 3        # just past the value
    r.click(*r.caret_xy(row, col, above=3), steps=16, after=8)
    r.type_keys([BACKSPACE, ord("8")], per=5)
    r.settle(16)
    r.tab(None, after=2)                      # PLAY -> a much bigger catcher
    r.park_player()
    r.play(50)
    return r.frames


def scene_blocks(r):
    """Blocks compile to the same Python: snap a block in, then open the CODE tab
    on the very line it generated."""
    # WHICH block cart: on a roomy surface the Blocks tab opens the Blocks+Scene
    # WORKSPACE (#93/#85) -- scripts left, the placed-actor stage right. Tap Game
    # is a touch-only cart with no scene, so on the desktop tier that half of the
    # window would sit empty; Coin Quest is the same kind of block program WITH a
    # stage (a player + coins), which is what that view was built to show.
    cart = r.reset_cart("coin_quest.moy" if r.windowed else "tap_game.moy")
    r.desk(16)
    r.open_editor(cart)
    r.ws.editor_app.open_blocks()             # open ON the Blocks tab (a block cart
    r.settle(12)                              # -- the outline IS its source)
    r.click(*r.block_row(r.last_insert_of_first_script()), steps=11, after=8)
    r.click(*_c(r.block_btn("add_btn")), steps=11, after=10)  # ADD -> "PICK A KIND"
    r.pick_menu("sound", steps=11, dwell=9, after=10)  # ...a category...
    r.pick_menu("beep", steps=11, dwell=9, after=6)    # ...and the block itself
    r.settle(14)                              # the new block, snapped in
    # "<< CODE" is the block editor's own graduate rung: compile the outline and
    # open the generated main.py in the code tab.
    r.click(*_c(r.block_btn("code_btn")), steps=12, after=10)
    ed = r.ws.editor
    row = next((i for i, line in enumerate(ed.lines) if "beep(440)" in line), 0)
    r.click(*r.caret_xy(row, len(ed.lines[row])), steps=14, after=8)
    r.settle(22)
    return r.frames


def scene_tap(r):
    """The "Make it mine" cards: pick another pet on the Config tab, then PLAY."""
    cart = r.reset_cart("pet.moy", cfg={"autoplay": 1})
    r.desk(16)
    r.open_editor(cart)
    r.settle(20)

    def _other_pet():
        cards = r.ws.cards_layer
        row = next(x for x in cards._card_layout() if x["f"]["key"] == "pet")
        cur = r.ws.config.get("pet", 0)
        for k, box in cards._choice_cells(row):
            if row["f"]["choices"][k] != cur:
                return box
        return (0, 0, 0, 0)

    r.click(*_c(r.local(_other_pet)), steps=12, after=14)
    r.tab(None, after=2)                      # PLAY
    r.park_player()
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
    ap.add_argument("--windowed", action="store_true",
                    help="record the WINDOWED DESKTOP tier (wm_windowed.WindowedWM) "
                         "instead of the fixed 320x240 one; implies --size 1024x600 "
                         "and --scale 1")
    ap.add_argument("--size", default=None, metavar="WxH",
                    help="system canvas size (default 320x240; 1024x600 windowed)")
    ap.add_argument("--font-scale", type=int, default=1, dest="font_scale")
    ap.add_argument("--wallpaper", default=None, metavar="SLUG",
                    help="desktop backdrop: a wallpaper cart's slug (its folder name "
                         "without .moy, e.g. open_machine / moy_night) or a "
                         "\"fill:<color>\" built-in; default = the store's first")
    args = ap.parse_args()
    size = None
    if args.size:
        w, _, h = args.size.lower().partition("x")
        size = (int(w), int(h))
    elif args.windowed:
        size = (1024, 600)
    # The desktop tier is already 3.2x the area, so it records at 1:1 -- doubling
    # it would be a 2048x1200 GIF nobody wants to clone.
    scale = 1 if (args.windowed and args.scale == 2) else args.scale
    os.makedirs(args.out, exist_ok=True)
    names = list(SCENES) if args.scene == "all" else [args.scene]
    for name in names:
        carts = tempfile.mkdtemp(prefix="moybyte-gif-")
        try:
            rec = Recorder(args.fps, carts, sys_size=size,
                           font_scale=args.font_scale, windowed=args.windowed,
                           wallpaper=args.wallpaper)
            save_gif(SCENES[name](rec),
                     os.path.join(args.out, "%s.gif" % name), scale, args.fps)
        finally:
            shutil.rmtree(carts, ignore_errors=True)


if __name__ == "__main__":
    main()
