#!/usr/bin/env python3
"""Panel-by-panel P4 UI benchmark: open each surface, perform real actions
(tap / drag / scroll / tab switch), and log end-to-end FRAME times.

Why end-to-end: timing a layer's draw() in isolation is misleading -- it misses
the window stamp, the desk backdrop under it, and the compositor flush. The
2026-07-26 session learned this the hard way (a picker drag measured 26ms by
content-draw and 181ms by real frame). So every number here is measured around
`ws.frame()`, the whole loop iteration the glass actually shows.

Usage:
    python tools/p4_bench.py --board p4 [--out bench.md]
    python tools/p4_bench.py --board tdeck --repaint settings,files,editor:code
    python tools/p4_bench.py --board p4 --layout '[{"edge": "left", ...}]'
    python tools/p4_bench.py --board p4 --popovers [--layout ...]
    python tools/p4_bench.py --board tdeck --kernel

`--layout` benches under a theme layout's bars (docs/shell_layout_2026-10.md
§5): installed as a package beside the seeds and picked, as Settings does,
after the board is reset or attached, and unpicked at the end.

`--popovers` benches the bar widgets' popovers alone (docs/shell_layout_2026-10.md
§8): open, navigate, idle and close, over three desk windows and over a
playtest, and a frame on a minute boundary (with a docked calendar when the
layout places one). The open and close are real taps on the widget's cell;
a key is the popover layer's own handler, with the router's dirty mark where
the image's WM does not let the popover take keys.

`--kernel` benches the home's scaled-blit kernel (step 7a): one call per card
size and a synthetic focus-band frame into the canvas the home draws into,
the shelf's drag frame beside them, and the board's pixels for a fixed set of
calls against the host binding's.

Reports per scenario: frame count, median, p90, max (ms) plus the per-Layer
median split, so the worst offender in a slow frame is named, not guessed.
"""

from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from p4_autotest import add_board_args, board_from_args  # noqa: E402


def pyexec(b, code, timeout=60):
    """P4Board.pyexec (the chunked, RX-safe uploader). The escape-into-one-
    exec-line version that lived here predated it and silently could not run
    a long snippet."""
    ok = b.pyexec(code, timeout=timeout)
    if not ok:
        print("      ! device error (see -v log)")
    return ok


def pyval(b, expr, timeout=30):
    return b.pyval(expr, timeout=timeout)


INSTRUMENT = """
import time
ws._T = time.ticks_ms
ws._D = time.ticks_diff
ws._ft = []
ws._lt = {}
ws._recording = False
ws._force = False
if not hasattr(ws, '_bench_installed'):
    _of = ws.frame
    def _f(dt, _of=_of):
        if ws._recording and ws._force:
            ws.mark_dirty()        # --repaint: every frame paints the surface
        a = ws._T()
        n0 = ws._frames_drawn
        r = _of(dt)
        # Record only frames that actually PAINTED. The redraw gate makes an
        # idle frame a ~0ms no-op, and those dominate any sample window -- a
        # median over all frames reads 0 even while every painted frame takes
        # 174ms. What the eye judges is the painted-frame cadence.
        if ws._recording and ws._frames_drawn != n0:
            ws._ft.append(ws._D(ws._T(), a))
        return r
    ws.frame = _f
    ws._bench_installed = True
"""

# Layers are re-created per world/relayout, so (re)wrap them per scenario.
WRAP_LAYERS = """
ws._lt = {}
for _l in ws.wm.visible_stack_rev():
    if getattr(_l, '_benched', False):
        continue
    nm = getattr(_l, 'id', type(_l).__name__)
    _od = _l.draw
    def mk(_od=_od, nm=nm):
        def f(dt):
            a = ws._T()
            r = _od(dt)
            if ws._recording:
                ws._lt.setdefault(nm, []).append(ws._D(ws._T(), a))
            return r
        return f
    _l.draw = mk()
    _l._benched = True
"""


def stats(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return {"n": n, "median": s[n // 2], "p90": s[min(n - 1, int(n * 0.9))],
            "max": s[-1]}


class Bench:
    def __init__(self, board):
        self.b = board
        self.rows = []
        pyexec(board, INSTRUMENT)

    def start(self):
        pyexec(self.b, WRAP_LAYERS)
        self.b.cmd("py ws._ft.clear() or ws._lt.clear() or 0", wait_for="PY")
        self.b.cmd("py setattr(ws, '_recording', True) or 1", wait_for="PY")

    def stop(self, name, note=""):
        self.b.cmd("py setattr(ws, '_recording', False) or 1", wait_for="PY")
        ft = pyval(self.b, "ws._ft") or []
        pyexec(self.b, "ws._sum = dict((k, sorted(v)[len(v)//2]) for k, v in "
                       "ws._lt.items() if v)")
        layers = pyval(self.b, "ws._sum") or {}
        st = stats(ft)
        row = {"name": name, "note": note, "stats": st, "layers": layers}
        self.rows.append(row)
        if st:
            worst = sorted(layers.items(), key=lambda kv: -kv[1])[:3]
            print("  %-34s n=%-4d median=%-5d p90=%-5d max=%-5d  %s"
                  % (name, st["n"], st["median"], st["p90"], st["max"],
                     " ".join("%s=%d" % (k, v) for k, v in worst)))
        else:
            print("  %-34s (no frames captured) %s" % (name, note))
        return row

    def table(self):
        out = ["| scenario | frames | median ms | p90 ms | max ms | fps@median | top layers |",
               "|---|---|---|---|---|---|---|"]
        for r in self.rows:
            st = r["stats"]
            if not st:
                out.append("| %s | - | - | - | - | - | %s |" % (r["name"], r["note"]))
                continue
            top = sorted(r["layers"].items(), key=lambda kv: -kv[1])[:3]
            fps = 1000 // st["median"] if st["median"] else 0
            out.append("| %s | %d | %d | %d | %d | %d | %s |"
                       % (r["name"], st["n"], st["median"], st["p90"],
                          st["max"], fps,
                          ", ".join("%s %dms" % (k, v) for k, v in top)))
        return "\n".join(out)


def win_geom(b, key):
    """(origin_x, origin_y, window-local layout) for an open window."""
    st = b.state()
    w = st["wins"].get(key)
    if w is None:
        return None
    return (w[0] + 1, w[1] + 1 + w[4], w)


def home(b):
    """Home, and on a windowed tier the desk: go_home in console mode lands
    on the fullscreen Library, where an app opens without a window."""
    pyexec(b, "ws.go_home()"); b.drain(2.0)
    if b.state().get("desk") is False:
        pyexec(b, "ws.open_desk()"); b.drain(1.0)


def pick_layout(b, bars):
    """Install `bars` (a JSON list of bar objects) as the package `bench`
    and pick it; None unpicks it. The pick is a settings row, a flash write:
    its own short `cmd`, never inside a `pyexec` upload. What the console
    resolved goes to stdout."""
    if bars is not None:
        pkg = json.dumps({"format": 1, "name": "bench",
                          "layout": {"bars": json.loads(bars)}})
        pyexec(b, "import moy_ui\nws._bench_rc = moy_ui.theme_install(%r)"
               % pkg.encode())
    b.cmd("py ws.system.set('layout_theme', %r) or 1"
          % ("bench" if bars is not None else None), wait_for="PY")
    b.cmd("py ws.look.set_theme(ws.look.theme_name, persist=False) or 1",
          wait_for="PY")
    b.drain(2.0)
    print("  layout: %r" % (pyval(b, "ws.look.bars"),))


def bench_all(b, out_path):
    bench = Bench(b)
    print("\n=== P4 panel benchmark (end-to-end frame ms) ===")

    # -- desk ---------------------------------------------------------------
    home(b)
    bench.start(); b.drain(3.0); bench.stop("desk idle")

    # -- picker (PROJECTS) --------------------------------------------------
    b.open("picker"); b.drain(1.0)
    bench.start(); b.drain(12.0); bench.stop("picker open+settle")
    g = pyval(b, "ws.wm._wins['make'].ctx.layout.lib_grid")
    o = win_geom(b, "make")
    if g and o:
        gx, gy, gw, gh = g
        cy = o[1] + gy + gh // 2
        bench.start()
        b.swipe(o[0] + gx + gw - 40, cy, o[0] + gx + 60, cy, frames=30)
        b.drain(2.0)
        bench.stop("picker drag-scroll")
        bench.start()
        b.tap(o[0] + gx + 120, cy, settle=0.2); b.drain(2.5)
        bench.stop("picker tap card (opens Editor)")

    # -- editor tabs --------------------------------------------------------
    if pyval(b, "ws.screen") == "menu":
        for tab in ("cards", "code", "paint", "map", "scene", "music", "blocks"):
            bench.start()
            pyexec(b, "ws.editor_app.set_tab('%s')\nws.mark_dirty()" % tab)
            b.drain(2.0)
            bench.stop("editor tab: %s" % tab)
        bench.start(); b.drain(3.0); bench.stop("editor idle (blocks)")
        pyexec(b, "ws.open_picker()"); b.drain(2.0)

    # -- settings -----------------------------------------------------------
    b.open("settings"); b.drain(2.0)
    bench.start(); b.drain(3.0); bench.stop("settings idle")
    o = win_geom(b, "settings")
    lay = pyval(b, "ws.wm._wins['settings'].ctx.layout.set_row_h")
    sx = pyval(b, "ws.wm._wins['settings'].ctx.layout.set_x")
    sw = pyval(b, "ws.wm._wins['settings'].ctx.layout.set_w")
    sy = pyval(b, "ws.wm._wins['settings'].ctx.layout.set_row_y0")
    if o and lay:
        cx = o[0] + sx + sw // 2
        y0 = o[1] + sy + 4 * lay
        bench.start()
        b.swipe(cx, y0, cx, y0 - 3 * lay, frames=25); b.drain(1.5)
        bench.stop("settings scroll drag")
        bench.start()
        b.tap(cx, o[1] + sy + lay // 2, settle=0.3); b.drain(1.5)
        bench.stop("settings tap row")

    # -- window drag / resize ----------------------------------------------
    bench.start(); b.cmd("drag 60 6"); b.wait_line("drag done", 30); b.drain(1.0)
    bench.stop("window drag (settings)")

    # -- desk apps ----------------------------------------------------------
    for app in ("files", "artwork", "calc", "storybook",
                "appearance"):
        ok = pyval(b, "ws.open_app(ws._apps_by_id['%s'])" % app)
        if not ok:
            print("  %-34s (app not available)" % ("app: " + app))
            continue
        b.drain(2.5)
        bench.start(); b.drain(3.0); bench.stop("app idle: %s" % app)
        # a drag through the middle of the app window exercises its list/canvas
        st = b.state()
        key = [k for k in st.get("order", []) if k not in ("desktop",)]
        if key:
            w = st["wins"][key[-1]]
            ox, oy = w[0] + 1, w[1] + 1 + w[4]
            cx, cy = ox + w[2] // 2, oy + (w[3] - w[4]) // 2
            if app != "artwork":    # a drag over Paint is a stroke: an edit
                bench.start()
                b.swipe(cx + 150, cy, cx - 150, cy, frames=20); b.drain(1.5)
                bench.stop("app drag: %s" % app)
            # the app's own window by its title strip: its frame, outline
            # and shadow repainted at every step
            bench.start(); b.cmd("drag 60 6"); b.wait_line("drag done", 30)
            b.drain(1.0)
            bench.stop("window drag: %s" % app)
        home(b)

    # -- fullscreen Library (the smooth reference) --------------------------
    pyexec(b, "ws.open_library()"); b.drain(12.0)
    bench.start(); b.drain(3.0); bench.stop("library idle")
    band = pyval(b, "ws.launcher.band_rect()")
    if band:
        bx, by, bw, bh = band
        cy = by + bh // 2
        bench.start()
        b.swipe(bx + bw - 40, cy, bx + 60, cy, frames=30); b.drain(2.0)
        bench.stop("library drag-scroll  [reference]")

    # -- console mode (docs/shell_layout_2026-10.md §3): the switch's spikes,
    # read as the rows' max (the single-frame maximum) --------------------
    home(b)
    pyexec(b, "ws.open_settings()\n"
              "ws.open_app(ws._apps_by_id['calc'])\n"
              "ws.open_app(ws._apps_by_id['files'])")
    b.drain(4.0)
    bench.start(); pyexec(b, "ws.defer(ws.open_library)"); b.drain(4.0)
    bench.stop("PLAY, three windows parked")
    bench.start(); pyexec(b, "ws.defer(ws.open_desk)"); b.drain(4.0)
    bench.stop("X, three windows back")
    pyexec(b, "ws.open_library()"); b.drain(3.0)
    pyexec(b, "ws.wm.release_cache()")
    bench.start(); pyexec(b, "ws.defer(ws.open_desk)"); b.drain(4.0)
    bench.stop("X, one stamp released")
    # A live wallpaper behind the parked desk: the home's own backdrop is the
    # only one that may animate.
    live = pyval(b, "[ws.look.wp_id_for(c) for c in ws.look.wallpaper_carts()]") or []
    was = pyval(b, "ws.look.wallpaper_id")
    for wp in live:
        pyexec(b, "ws.look.select_wallpaper(%r, persist=False)" % wp)
        if pyval(b, "ws.wallpaper.is_animating(0.03)"):
            pyexec(b, "ws.open_library()"); b.drain(3.0)
            bench.start(); b.drain(3.0)
            bench.stop("library idle, live wallpaper %s (desk parked)" % wp)
            pyexec(b, "ws.open_desk()"); b.drain(1.0)
            break
    pyexec(b, "ws.look.select_wallpaper(%r, persist=False)" % was)
    home(b)

    table = bench.table()
    print("\n" + table)
    if out_path:
        with open(out_path, "w") as f:
            f.write("# P4 panel benchmark (end-to-end frame ms)\n\n")
            f.write(table + "\n")
        print("\nwrote %s" % out_path)
    return bench

KEY = """
class _PK:
    last_key = 0
    def __init__(self, n):
        self.n = n
    def pressed(self, n):
        return n == self.n
def _pk(n):
    ws._sysmenu_layer.handle_input(_PK(n))
    if not getattr(ws.wm, 'popover_takes_keys', lambda: False)():
        ws.mark_dirty()
ws._pk = _pk
"""


MINUTE = """
ws._bl = ws.bar_layer
ws._bl._clock_text = lambda: '12:00'
ws._rn = [0]
def _rc(cv, key, i=0, _or=ws._bl._render_cart_bar):
    ws._rn[0] += 1
    return _or(cv, key, i)
ws._bl._render_cart_bar = _rc
"""


def widget_cell(b, name):
    """The screen centre of the bar widget `name`'s cell, or None."""
    bars = pyval(b, "[(x[1], [(c[0], c[1]) for c in x[3]]) for x in ws.layout.bars]")
    for rect, cells in bars or ():
        for n, (x, y, w, h) in cells:
            if n == name:
                return rect[0] + x + w // 2, rect[1] + y + h // 2
    return None


def popover_rows(b, bench, where):
    for widget, key in (("menu", "down"), ("clock", "right"), ("battery", "down")):
        xy = widget_cell(b, widget)
        if xy is None:
            print("  %-34s (not on this layout)" % ("popover: " + widget))
            continue
        tag = "%s, %s" % (widget, where)
        bench.start(); b.tap(*xy, settle=0.2); b.drain(1.5)
        bench.stop("popover open: " + tag)
        if not pyval(b, "ws.sysmenu.open"):
            print("  %-34s (opens no popover on this image)" % tag)
            continue
        bench.start()
        for _ in range(4):
            pyexec(b, "ws._pk(%r)" % key); b.drain(0.4)
        b.drain(1.0)
        bench.stop("popover navigate x4: " + tag)
        bench.start(); b.drain(3.0); bench.stop("popover idle: " + tag)
        bench.start(); b.tap(*xy, settle=0.2); b.drain(1.5)
        bench.stop("popover close: " + tag)


def bench_popovers(b, out_path):
    bench = Bench(b)
    pyexec(b, KEY)
    print("\n=== popover benchmark (end-to-end frame ms) ===")
    home(b)
    pyexec(b, "ws.open_settings()\n"
              "ws.open_app(ws._apps_by_id['calc'])\n"
              "ws.open_app(ws._apps_by_id['files'])")
    b.drain(4.0)
    bench.start(); b.drain(3.0); bench.stop("desk idle, three windows")
    popover_rows(b, bench, "over windows")
    bench.start(); b.cmd("drag 60 6"); b.wait_line("drag done", 30); b.drain(1.0)
    bench.stop("window drag (files)  [reference]")
    # a playtest beside them: the quiet stack is the reference
    pyexec(b, "ws.launcher.sel = [i for i, c in enumerate(ws.launcher.items) "
              "if c.get('title') == 'Star Catcher'][0]\nws.open()")
    b.drain(4.0)
    bench.start(); b.drain(3.0); bench.stop("playtest alone  [reference]")
    popover_rows(b, bench, "over playtest")
    pyexec(b, "ws.wm.close_window_kind('desktop')"); b.drain(2.0)
    # a minute boundary: the next painted frame re-renders the clock's strip
    # (and nothing else's -- a docked calendar keeps its strip on the minute)
    pyexec(b, MINUTE)
    b.drain(1.0)
    bench.start(); pyexec(b, "ws._rn[0] = 0\nws.mark_dirty()"); b.drain(1.0)
    bench.stop("dirty frame, same minute")
    same = pyval(b, "ws._rn[0]")
    bench.start()
    pyexec(b, "ws._rn[0] = 0\nws._bl._clock_text = lambda: '12:01'\nws.mark_dirty()")
    b.drain(1.0)
    bench.stop("minute boundary frame")
    tick = pyval(b, "ws._rn[0]")
    print("  strip renders: same minute %s, minute boundary %s" % (same, tick))
    bench.rows[-1]["note"] = "strip renders %s" % tick
    pyexec(b, "del ws._bl._render_cart_bar\ndel ws._bl._clock_text")
    home(b)
    table = bench.table()
    print("\n" + table)
    if out_path:
        with open(out_path, "w") as f:
            f.write(table + "\n")
    return bench


def bench_repaint(b, surfaces, seconds, out_path):
    """The widget A/B (docs/kernel_toolkit_2026-10.md §10): each surface open
    and repainted EVERY frame for `seconds`, painted-frame ms. Board-neutral,
    so the S3s take it with the P4s."""
    bench = Bench(b)
    print("\n=== repaint benchmark (painted-frame ms) ===")
    home(b)
    for name in surfaces:
        if name == "settings":
            b.open("settings")
        elif name.startswith("editor:"):
            # an Editor tab on Star Catcher, as the shell goldens open it
            pyexec(b, "ws.open_in_editor([c for c in ws.carts.all "
                      "if c.get('title') == 'Star Catcher'][0])\n"
                      "ws.set_menu_view('%s')" % name[7:])
        elif not pyval(b, "ws.open_app(ws._apps_by_id['%s'])" % name):
            print("  %-34s (app not available)" % name)
            continue
        b.drain(2.5)
        bench.start(); b.drain(3.0); bench.stop("idle: %s" % name)
        b.cmd("py setattr(ws, '_force', True) or 1", wait_for="PY")
        bench.start(); b.drain(seconds); bench.stop("repaint: %s" % name)
        b.cmd("py setattr(ws, '_force', False) or 1", wait_for="PY")
        home(b)
    table = bench.table()
    print("\n" + table)
    if out_path:
        with open(out_path, "w") as f:
            f.write(table + "\n")
    return bench


# -- --kernel: the home's scaled-blit kernel (docs/shell_layout_2026-10.md
# section 12, step 7a) ---------------------------------------------------------

# The scales a card is benched at, relative to the cover's size on today's
# card: 1.0 is the outer cards, the rest bracket the centre card's.
KERNEL_SCALES = (1.0, 1.25, 1.5, 2.0)

# The cases the board must answer with the host's pixels: (dx, dy, tw, th,
# sw, sh), into a 64 x 40 target, each hashed after the call.
KERNEL_PIXEL_CASES = ((0, 0, 40, 24, 13, 9), (-7, -5, 50, 40, 16, 16),
                      (1, 2, 12, 7, 31, 23), (5, 4, 25, 25, 20, 20),
                      (30, 20, 50, 50, 7, 5), (-60, 0, 61, 40, 9, 9))

KERNEL_PIXELS = """
def _kb_pixels(gfx, cases):
    out = []
    for dx, dy, tw, th, sw, sh in cases:
        src = bytearray(sw * sh * 2)
        for i in range(sw * sh):
            v = (i * 40503 + 7) & 0xFFFF
            src[2 * i] = v & 0xFF
            src[2 * i + 1] = v >> 8
        dst = bytearray(64 * 40 * 2)
        gfx.blit565_resize(dst, 64, 40, dx, dy, tw, th, src, sw, sh)
        h = 0
        for b in dst:
            h = (h * 31 + b) & 0xFFFFFFFF
        out.append(h)
    return out
"""

KERNEL_BENCH = """
import gc, time, moy_gfx
import launcher_layer as _ll
_T, _D = time.ticks_us, time.ticks_diff

def _kb_med(f, n=15):
    ts = []
    for _ in range(n):
        a = _T()
        f()
        ts.append(_D(_T(), a))
    ts.sort()
    return ts[len(ts) // 2]

def _kb_run(scales):
    cv = ws.sys_canvas            # the home's canvas; ws.canvas is the game's
    buf, W, H = cv._buf, cv._stride, cv.h
    lay = ws.launcher.layout
    fs = lay.fs
    band_h = max(14 * fs, 20)
    cw, ch = lay.lib_card_w, lay.lib_card_h
    art = ch - band_h
    side = _ll._COVER_SIDE if _ll._cover_scale(_ll._COVER_SIDE, cw, art) \\
        else _ll._COVER_SIDE // 2
    s = max(1, _ll._cover_scale(side, cw, art))
    base = side * s
    src = None
    for img in ws.covers._cache.values():
        if img is not None and img.w == side and img.h == side:
            src = img.pix
            break
    real = src is not None
    if src is None:
        src = bytearray(side * side * 2)
        for i in range(0, len(src), 2):
            src[i] = i & 0xFF
            src[i + 1] = (i >> 7) & 0xFF
    bx, by, bw, bh = ws.launcher.band_rect()
    pitch = lay.lib_step
    if bx < 0 or by < 0 or bx + bw > W or by + bh > H:
        raise ValueError("the band %r is off the %dx%d canvas" % ((bx, by, bw, bh), W, H))
    gc.collect()
    r = {"canvas": (W, H), "card": (cw, ch), "pitch": pitch,
         "band_rect": (bx, by, bw, bh), "bake": side, "cover_scale": s,
         "base": base, "real_cover": real}
    # today's draw of one cover: blit565 at 1x, blit565_scale above it
    if s == 1:
        r["today_us"] = _kb_med(lambda: moy_gfx.blit565(
            buf, W, H, bx, by, src, side, side, -1, bx, by, bx + bw, by + bh))
    else:
        r["today_us"] = _kb_med(lambda: moy_gfx.blit565_scale(
            buf, W, H, bx, by, src, side, side, s))
    cy = by + bh // 2
    cards = []
    for k in scales:
        t = int(base * k + 0.5)
        x, y = bx + bw // 2 - t // 2, cy - t // 2
        us = _kb_med(lambda: moy_gfx.blit565_resize(
            buf, W, H, x, y, t, t, src, side, side, bx, by, bx + bw, by + bh))
        vis = (min(x + t, bx + bw) - max(x, bx)) * (min(y + t, by + bh) - max(y, by))
        cards.append((k, t, vis, us))
    r["cards"] = cards
    # the moving frame at each centre scale (docs/shell_layout_2026-10.md
    # section 12): the focus band -- the centre +- 1.5 pitches -- cleared, the
    # centre card at k and its two neighbours at 1.0 (the most the three
    # cards cover), each with its title band, the centre's outline and pill;
    # and the outer rects either side of it shifted by #113's scroll-as-blit
    mid = bx + bw // 2
    fx0 = max(bx, mid - 3 * pitch // 2)
    fx1 = min(bx + bw, mid + 3 * pitch // 2)
    r["focus"] = (fx0, by, fx1 - fx0, bh)
    clear = _kb_med(lambda: moy_gfx.fill_rect(buf, W, fx0, by, fx1 - fx0, bh,
                                              0x2104))
    def shift():
        if fx0 > bx:
            moy_gfx.scroll_rect(buf, W, bx, by, fx0 - bx, bh, 8, 0)
        if bx + bw > fx1:
            moy_gfx.scroll_rect(buf, W, fx1, by, bx + bw - fx1, bh, 8, 0)
    sh = _kb_med(shift)
    band = []
    for k in scales:
        t = int(base * k + 0.5)
        def frame(t=t):
            moy_gfx.fill_rect(buf, W, fx0, by, fx1 - fx0, bh, 0x2104)
            for i, tt in ((-1, base), (1, base), (0, t)):
                x = mid + i * pitch - tt // 2
                y = cy - tt // 2
                moy_gfx.blit565_resize(buf, W, H, x, y, tt, tt, src, side,
                                       side, fx0, by, fx1, by + bh)
                x0, x1 = max(x, fx0), min(x + tt, fx1)
                if x1 > x0:
                    moy_gfx.fill_rect(buf, W, x0, min(y + tt, by + bh - band_h),
                                      x1 - x0, band_h, 0)
            x = mid - t // 2
            y = cy - t // 2
            y0, y1 = max(y, by), min(y + t, by + bh)
            x0, x1 = max(x, fx0), min(x + t, fx1)
            o = 2 * fs
            moy_gfx.fill_rect(buf, W, x0, y0, x1 - x0, o, 0xFFFF)
            moy_gfx.fill_rect(buf, W, x0, y1 - o, x1 - x0, o, 0xFFFF)
            moy_gfx.fill_rect(buf, W, x0, y0, o, y1 - y0, 0xFFFF)
            moy_gfx.fill_rect(buf, W, x1 - o, y0, o, y1 - y0, 0xFFFF)
            moy_gfx.fill_rect(buf, W, mid - t // 4, y0 + 4 * fs, t // 2,
                              10 * fs, 0x07E0)
        band.append((k, clear, _kb_med(frame), sh))
    r["band"] = band
    return r
"""


def bench_kernel(b, out_path):
    """The step-7a micro-bench: the scaled-blit kernel per call at the card
    sizes the home draws, and a synthetic focus-band frame, into the canvas
    the home draws into, beside the shelf's drag frame today; and the board's
    pixels against the host's for the same calls."""
    sys.path.insert(0, __file__.rsplit("/", 2)[0])
    from runtime import gfx_binding
    ns = {}
    exec(KERNEL_PIXELS, ns)                    # noqa: S102 -- fixed text
    host = ns["_kb_pixels"](gfx_binding, KERNEL_PIXEL_CASES)
    pyexec(b, KERNEL_PIXELS + "import moy_gfx\n"
              "ws._kbp = _kb_pixels(moy_gfx, %r)" % (KERNEL_PIXEL_CASES,))
    dev = pyval(b, "ws._kbp")
    print("  pixels: %s" % ("board == host" if dev == host
                             else "DIFFER board %r host %r" % (dev, host)))
    bench = Bench(b)
    home(b)
    pyexec(b, "ws.open_library()"); b.drain(8.0)
    band = pyval(b, "ws.launcher.band_rect()")
    if band:
        bx, by, bw, bh = band
        cy = by + bh // 2
        bench.start()
        b.swipe(bx + bw - 40, cy, bx + 60, cy, frames=30); b.drain(2.0)
        bench.stop("library drag-scroll")
    pyexec(b, KERNEL_BENCH)
    # two lines: a one-line upload is evaluated as an expression
    pyexec(b, "ws._kb = None\nws._kb = _kb_run(%r)" % (KERNEL_SCALES,),
           timeout=120)
    r = pyval(b, "ws._kb", timeout=30)
    pyexec(b, "ws.mark_dirty()"); b.drain(1.0)
    if not r:
        print("  kernel bench: no answer")
        return None
    lines = ["canvas %dx%d, card %dx%d, pitch %d, band %r; cover bake %d^2 "
             "drawn at %dx = %d px (%s); today's cover draw %d us"
             % (r["canvas"] + r["card"] + (r["pitch"], r["band_rect"], r["bake"],
                r["cover_scale"], r["base"],
                "a real cover" if r["real_cover"] else "synthetic",
                r["today_us"])),
             "",
             "| scale | card px | visible px | blit565_resize us | ns/px |",
             "|---|---|---|---|---|"]
    for k, t, vis, us in r["cards"]:
        lines.append("| %.2f | %d^2 | %d | %d | %.1f |"
                     % (k, t, vis, us, 1000.0 * us / max(1, vis)))
    lines += ["", "focus band %r" % (r["focus"],), "",
              "| centre scale | band clear us | focus band frame us (clear + "
              "3 cards + fills) | outer shift us | moving frame us |",
              "|---|---|---|---|---|"]
    for k, clear, us, sh in r["band"]:
        lines.append("| %.2f | %d | %d | %d | %d |"
                     % (k, clear, us, sh, us + sh))
    lines += ["", bench.table()]
    text = "\n".join(lines)
    print(text)
    if out_path:
        with open(out_path, "w") as f:
            f.write(text + "\n")
    return r


def main():
    ap = argparse.ArgumentParser()
    add_board_args(ap)
    ap.add_argument("--out", default=None)
    ap.add_argument("--repaint", default=None, metavar="SURFACES",
                    help="comma-separated surfaces (settings, files, storybook,"
                         " editor:TAB, ...) to repaint every frame instead of"
                         " the panel run")
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--popovers", action="store_true",
                    help="the popover scenarios alone")
    ap.add_argument("--kernel", action="store_true",
                    help="the home's scaled-blit kernel and a focus-band "
                         "frame, beside the shelf's drag frame")
    ap.add_argument("--layout", default=None, metavar="BARS",
                    help="a JSON list of bar objects to bench under")
    args = ap.parse_args()
    b = board_from_args(args)
    try:
        if not args.repaint and not args.kernel and not b.attach_only:
            b.reset()               # an attach-only board is benched as found
        if args.layout:
            pick_layout(b, args.layout)
        if args.repaint:
            bench_repaint(b, args.repaint.split(","), args.seconds, args.out)
        elif args.popovers:
            bench_popovers(b, args.out)
        elif args.kernel:
            bench_kernel(b, args.out)
        else:
            bench_all(b, args.out)
        if args.layout:
            pick_layout(b, None)
    finally:
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
