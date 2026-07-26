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

MEASURED 2026-07-26 on glass, P4 @ 1024x600, WITH REAL CONTENT SEEDED (60fps =
16.7ms); the three editor rows re-measured 2026-07-27 after the glyph/layout
memos (c422a12). Painted-frame distribution over three drag gestures:

  | surface       | content seeded        | median | p90 | worst | gc |
  |---------------|-----------------------|--------|-----|-------|----|
  | editor:map    | empty map, 960 fills  |   76   |  80 |  138  |  0 |
  | editor:blocks | empty outline + scene |   76   |  76 |  151  |  0 |
  | editor:paint  | 19 chrome glyphs      |   52   |  56 |  116  |  0 |
  | writer        | 200-line doc          |   52   |  52 |  128  |  0 |
  | editor:code   | 302-line cart         |   52   |  60 |  130  |  2 |
  | sheets        | 360-cell table        |   48   |  52 |   99  |  1 |
  | picker        | 29 carts, covers warm |   48   |  60 |   89  |  0 |
  | settings      | full row set          |   24   |  28 |   42  |  0 |

The first version of this table measured EMPTY surfaces and drew a conclusion that
had to be withdrawn. The deltas are why seeding is not optional:

  * writer   20ms (empty file GRID) -> 40 (empty text area) -> 52 (200 lines)
  * sheets   16ms (empty file GRID) -> 48 (360 cells)

With no file open, Writer and Sheets show a file grid rather than a text surface,
so an unseeded measurement is not a slow version of the real thing -- it is a
different screen.

What the seeding then established, and it is the useful part:

  * The EDITOR TABS are the slow family. (An earlier version of this header added
    "and their cost is CHROME, NOT CONTENT", because map/blocks/paint measured the
    same on an empty cart and on the richest one. That inference is WITHDRAWN:
    these draw loops iterate the VIEWPORT, so an empty map still issues its 960
    cell fills and the comparison had no power. Attributed directly with
    tools/p4_attrib.py, a populated map costs 74.4ms against an empty map's 65.0
    -- and the real finding is that 50-75% of every editor frame is per-call
    DISPATCH, not pixels. See docs/ui_damage_model_v1.md Section 0.06.)
  * CODE is only weakly content-sensitive (48ms on a system-app cart, 52ms on a
    302-line one).
  * SETTINGS at 24ms is the only surface close to budget, and it is the one that
    got this session's fixes.
  * Every surface still shows a 99-220ms WORST frame -- the open/first-paint
    transition. That class was removed from Settings (worst 42ms) and nowhere else.

  This sweep opens the FIRST cart on the shelf, whose block outline and map are
  empty. That is fine for tracking a surface against itself over time -- every row
  here was measured that way -- but it is not the cost a kid with a real project
  sees. Use tools/p4_attrib.py --cart for that.

SEEDING RECIPE (the formats are not guessable -- three wrong guesses cost a run
each):

  * doc:   save_file('docs', name, json.dumps({"format": "moytext-v1",
           "body": "<text>"})) -- writer's _open_doc runs the blob through
           _body_of, which reads ONLY the "body" key.
  * table: build a real formula.Sheet (set_cell(col, row, raw), keys are "A1"
           refs via make_ref) and save json.dumps(sheet.to_dict()). A hand-rolled
           {"rows","cols","cells"} dict leaves sheets_app.sheet None.
  * ORDER MATTERS for Sheets: open_named(name) only sets _pending_open, which is
           consumed by the app's open(). Call it BEFORE open_app(), not after.
  * cart:  carts_store.create(title, root, src=...) then
           ws._apply_items(store.scan(root)).
  * There is no load_code(path); reading a cart's source goes through load(path).

READ THE ROWS AS "cost of a drag frame", not "scroll". On Sprites and Map a
vertical drag PAINTS rather than scrolls, so those measure the drawing gesture.

STRUCTURAL NOTE (content-independent): the Editor tabs and the Desk Lab apps share
NO scroll machinery -- not ui.ScrollRegion, not the #113 blit path. Only the
launcher shelf, the picker and (partly) Settings do. So #113 does not correlate
with which surfaces are slow.
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
