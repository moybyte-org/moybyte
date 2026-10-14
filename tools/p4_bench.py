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

`--layout` benches under a theme layout's bars (docs/shell_layout_2026-10.md
§5): installed as a package beside the seeds and picked, as Settings does,
after the board is reset or attached, and unpicked at the end.

`--popovers` benches the bar widgets' popovers alone (docs/shell_layout_2026-10.md
§8): open, navigate, idle and close, over three desk windows and over a
playtest, and a frame on a minute boundary (with a docked calendar when the
layout places one). The open and close are real taps on the widget's cell;
a key is the popover layer's own handler, with the router's dirty mark where
the image's WM does not let the popover take keys.

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
    ap.add_argument("--layout", default=None, metavar="BARS",
                    help="a JSON list of bar objects to bench under")
    args = ap.parse_args()
    b = board_from_args(args)
    try:
        if not args.repaint and not b.attach_only:
            b.reset()               # an attach-only board is benched as found
        if args.layout:
            pick_layout(b, args.layout)
        if args.repaint:
            bench_repaint(b, args.repaint.split(","), args.seconds, args.out)
        elif args.popovers:
            bench_popovers(b, args.out)
        else:
            bench_all(b, args.out)
        if args.layout:
            pick_layout(b, None)
    finally:
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
