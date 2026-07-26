#!/usr/bin/env python3
"""Frame cost of EVERY interactive surface, one boot each, on real glass.

Why one tool instead of ten one-offs: the surfaces were being profiled ad hoc,
and the ad hoc numbers disagreed with each other because they were measured in
different states (a picker measured 6s after opening is still building covers; the
same drag 16s later is 20ms faster). This resets the board per surface -- so no
surface inherits another's warm caches -- opens it through the console's own
verbs, drags it three times through the middle of its window, and reports the
painted-frame distribution from tools/p4_alloc.py's zero-retention CADENCE hook.

  python tools/p4_surface_sweep.py [--port /dev/ttyACM0] [--only code,map]

READ IT AS "cost of a drag frame", not "scroll". On the Sprites and Map tabs a
vertical drag PAINTS rather than scrolls, so those rows measure the drawing
gesture. That is still the number that matters for feel, but it is not the same
mechanism as a scrolled list.

Measured 2026-07-26, P4 @ 1024x600, after the session's bar-strip / cover-prefetch
fixes (60fps = 16.7ms):

  | surface   | median | p90 | worst | gc |
  |-----------|--------|-----|-------|----|
  | map       |   92   |  96 |  152  |  0 |
  | blocks    |   88   |  96 |  166  |  2 |
  | sprites   |   76   |  76 |  139  |  0 |
  | picker    |   68   |  72 |  117  |  0 |
  | code      |   48   |  56 |  130  |  1 |
  | settings  |   24   |  28 |   72  |  0 |
  | writer    |   20   |  20 |   98  |  0 |
  | storybook |   20   |  20 |   98  |  0 |
  | sheets    |   16   |  16 |   64  |  0 |
  | files     |   16   |  20 |   65  |  0 |

The ranking inverts the intuition, and it is the useful part:

  * The DESK LAB apps (sheets / files / writer / storybook) are the FASTEST --
    16-20ms, i.e. at or near 60fps -- despite sharing none of the #113 scroll
    machinery. They are sparse text and list surfaces; there is simply little to
    draw.
  * The EDITOR TABS are the SLOWEST (map 92, blocks 88, sprites 76, code 48),
    and they are where a kid spends the most time. None of them uses ScrollRegion
    either; they redraw their whole content per frame.
  * So the #113 scroll-as-blit work does not correlate with which surfaces are
    slow. Content volume does. The two surfaces that DID get the blit path
    (launcher shelf, picker) are mid-table, and at this screen size the blit only
    buys them ~4.5ms anyway (see launcher_layer.draw_shift).
  * Every surface still shows a ~64-166ms WORST frame, mostly the open/first-paint
    transition -- the same "first frame after quiet does everything" shape that the
    bar-strip fix removed from Settings.
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from p4_autotest import P4Board            # noqa: E402
from p4_alloc import PROBE, CADENCE        # noqa: E402

OPEN = {
    "settings":  "ws.open_settings()",
    "picker":    "ws.open_picker()",
    "code":      "ws._g['_edit']('code')",
    "blocks":    "ws._g['_edit']('blocks')",
    "sprites":   "ws._g['_edit']('paint')",
    "map":       "ws._g['_edit']('map')",
    "writer":    "ws.open_app(ws.writer_app)",
    "sheets":    "ws.open_app(ws.sheets_app)",
    "files":     "ws.open_app(ws.files_app)",
    "storybook": "ws.open_app(ws.storybook_app)",
}
HELPER = """
def _edit(tab):
    items = ws.picker.items
    for i in range(len(items)):
        if items[i].get('type') != 'new':
            ws.picker.sel = i
            break
    ws.open_picker()
    ws.pick_selected()
    ws.editor_app.set_tab(tab)
    return True
ws._g['_edit'] = _edit
"""
rows = []

def run(name, verb):
    b.reset()
    b.pyexec(PROBE); b.pyexec(CADENCE); b.pyexec(HELPER)
    b.pyval("bool(%s) or True" % verb, 60)   # open_app returns False if
                                             # no cart claims the app
    b.drain(6.0)
    st = b.state()
    wins = st.get("wins") or {}
    top = b.pyval("ws.wm.top_kind()")
    if wins:
        k = list(wins.keys())[0]
        for cand in ("make", name):
            if cand in wins:
                k = cand
        w = wins[k]
        cx = w[0] + 1 + w[2] // 2
        ct = w[1] + 1 + w[4]
        y0, y1 = ct + (w[3] - w[4]) - 40, ct + 40
        where = "win:" + k
    else:
        cx, y0, y1 = 512, 540, 80
        where = "fullscreen"
    b.pyexec("ws._g['_creset']()")
    b.pyexec("ws._crec = True")
    for _ in range(3):
        b.ser.write(("swipe %d %d %d %d 30\n" % (cx, y0, cx, y1)).encode())
        b.ser.flush(); b.wait_line("swipe done", 60); b.drain(1.0)
    b.pyexec("ws._crec = False")
    r = b.pyval("ws._g['_cagg']()") or (0,) * 9
    nf, nc, _a, _b2, _c, worst, hist, _d, _e = r
    tot = sum(v for _x, v in hist) or 1
    def pct(p):
        seen = 0
        for ms, v in hist:
            seen += v
            if seen >= tot * p:
                return ms
        return worst
    rows.append((name, top, where, nf, pct(.5), pct(.9), worst, nc))
    print("  %-10s %-10s %-12s frames=%-4d med=%-4s p90=%-4s worst=%-4s gc=%d"
          % (name, top, where, nf, pct(.5) if nf else "-", pct(.9) if nf else "-",
             worst if nf else "-", nc))

ap = argparse.ArgumentParser()
ap.add_argument("--port", default="/dev/ttyACM0")
ap.add_argument("--only", default="", help="comma-separated subset of the surfaces")
args = ap.parse_args()
b = P4Board(args.port)
want = [s_ for s_ in args.only.split(",") if s_] or list(OPEN)

try:
    for name in want:
        verb = OPEN[name]
        try:
            run(name, verb)
        except Exception as exc:  # noqa: BLE001 -- one bad surface must not end the sweep
            print("  %-10s FAILED: %s" % (name, exc))
    print("\n| surface | top | median | p90 | worst | frames | gc |")
    print("|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: -(r[4] or 0)):
        print("| %s | %s | %s | %s | %s | %d | %d |"
              % (r[0], r[1], r[4], r[5], r[6], r[3], r[7]))
finally:
    b.close()
