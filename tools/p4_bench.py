#!/usr/bin/env python3
"""Panel-by-panel P4 UI benchmark: open each surface, perform real actions
(tap / drag / scroll / tab switch), and log end-to-end FRAME times.

Why end-to-end: timing a layer's draw() in isolation is misleading -- it misses
the window stamp, the desk backdrop under it, and the compositor flush. The
2026-07-26 session learned this the hard way (a picker drag measured 26ms by
content-draw and 181ms by real frame). So every number here is measured around
`ws.frame()`, the whole loop iteration the glass actually shows.

Usage:
    python tools/p4_bench.py [--port /dev/ttyACM0] [--out bench.md]

Reports per scenario: frame count, median, p90, max (ms) plus the per-Layer
median split, so the worst offender in a slow frame is named, not guessed.
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from p4_autotest import P4Board            # noqa: E402


def pyexec(b, code, timeout=60):
    """Run a multi-line snippet on the device (single quotes inside, please)."""
    esc = code.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    r = b.cmd('py exec("%s")' % esc, wait_for="PY", timeout=timeout)
    if r and "ERR" in r:
        print("      ! device error: %s" % r.strip())
    return r


def pyval(b, expr, timeout=30):
    r = b.cmd("py " + expr, wait_for="PY", timeout=timeout) or ""
    if "PY ERR" in r:
        return None
    try:
        return eval(r.split("PY ", 1)[1])
    except Exception:  # noqa: BLE001
        return None


INSTRUMENT = """
import time
ws._T = time.ticks_ms
ws._D = time.ticks_diff
ws._ft = []
ws._lt = {}
ws._recording = False
if not hasattr(ws, '_bench_installed'):
    _of = ws.frame
    def _f(dt, _of=_of):
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


def bench_all(b, out_path):
    bench = Bench(b)
    print("\n=== P4 panel benchmark (end-to-end frame ms) ===")

    # -- desk ---------------------------------------------------------------
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
    for app in ("files", "artwork", "writer", "sheets", "calc", "storybook",
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
            bench.start()
            b.swipe(cx + 150, cy, cx - 150, cy, frames=20); b.drain(1.5)
            bench.stop("app drag: %s" % app)
        pyexec(b, "ws.go_home()"); b.drain(2.0)

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

    table = bench.table()
    print("\n" + table)
    if out_path:
        with open(out_path, "w") as f:
            f.write("# P4 panel benchmark (end-to-end frame ms)\n\n")
            f.write(table + "\n")
        print("\nwrote %s" % out_path)
    return bench


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    b = P4Board(args.port)
    try:
        b.reset()
        bench_all(b, args.out)
    finally:
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
