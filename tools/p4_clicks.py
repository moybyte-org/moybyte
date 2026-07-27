#!/usr/bin/env python3
"""How long a CLICK takes on glass: the one-shot transitions, not the drag frames.

`p4_surface_sweep.py` measures steady-state drag frames and reports a "worst"
column that is really this: the open/first-paint transition after a tap. That
frame is 100-220ms on most surfaces and it is what a kid actually feels when they
switch tabs or open a project -- a drag at 76ms feels heavy, but a 200ms click
feels BROKEN.

This drives each transition through the console's own verbs (so it is exactly the
code a tap runs, minus the touch plumbing), records every painted frame with
p4_alloc's zero-retention CADENCE hook, and reports the transition's cost as the
frames it took and the worst one.

  python tools/p4_clicks.py [--port /dev/ttyACM0] [--only tab_map,open_picker]

READ THE COLUMNS AS: `frames` is how many painted frames the transition spanned
(the settle), `worst` the single most expensive one, `sum` the total wall the kid
waits. A transition that is 3 x 60ms is worse than one 120ms frame even though
its `worst` is lower -- `sum` is the honest latency number.

Each scenario resets the board so it cannot inherit another's warm caches, then
runs its `pre` verbs, arms the recorder, and runs the `click` verb.

MEASURED 2026-07-27, P4 on glass @ 1024x600 (60fps = 16.7ms). Sum ms across
three states: the cold pipeline, after arming the prefetch from boot, and
after also raising _COVER_SLICE_MS 8 -> 20 ("-" = not re-run):

  | click         | cold  | boot prefetch | +20ms slice |
  |---------------|-------|---------------|-------------|
  | back_to_desk  | 1108  |  536          |  408 (2 fr) |
  | open_picker   |  824  |  376          |  312 (2 fr) |
  | tab_blocks    |  252  |  252          |    -        |
  | tab_map       |  224  |  220          |  220        |
  | open_project  |  180  |  180          |    -        |
  | tab_sprites   |  120  |  120          |    -        |
  | tab_code      |   96  |   96          |    -        |
  | open_settings |   72  |   72          |   72        |

THE TWO ~1s CLICKS WERE THE COVER PIPELINE, not the surface they open.
Attributed with tools/p4_attrib.py's wrap hook over the same driver (cold):

  back_to_desk, 6 painted frames      open_picker, 5 painted frames
    cover_load      32 x  1572ms        cover_load      32 x  1561ms
    launcher.draw    6 x   400 excl     picker.draw      6 x   416 excl
    wallpaper.draw   6 x   222          ws._relayout     1 x    25
    cover_prefetch 200 x  1338 incl     cover_prefetch 195 x  1334 incl

A cover's blob read + parse is ~49ms and there are ~29 carts; each pop-in
re-armed the redraw gate, so the desk repainted SIX times (~104ms each) with
the loads landing on painted frames. THE FIX (2026-07-27, two halves):
  1. The idle prefetch is ARMED FROM BOOT (console._cover_seen starts True,
     re-armed on a store re-scan). The old arming -- only after a surface drew
     a cover -- kept the cache cold at exactly the moment of the click: neither
     Settings nor the desk icon column (tile-0 art) ever armed it.
  2. _COVER_SLICE_MS 8 -> 20: with runs warm a native build is ~2ms, so a
     transition's visible set lands on the first painted frame or two instead
     of spreading over 2-3 full ~190ms repaints. The cold path is unshaped by
     this: the first build of a frame always proceeded regardless of budget,
     and after any ~50ms load the 20ms ceiling still refuses a second.
The remaining ~200ms/frame IS the desk/picker repaint itself (launcher 67 +
wallpaper 37 + windows/chrome) -- ui_damage_model territory, not covers.

TRIED AND REVERTED (pre-fix): giving _cover_prefetch_tick a 150ms time budget
on idle frames. Measured ZERO change (1108 -> 1132, noise), because with the
old arming the prefetch never ran at all -- the budget of a thing that never
runs is irrelevant. Fix the arming before touching budgets.
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from p4_autotest import P4Board            # noqa: E402
from p4_alloc import PROBE, CADENCE        # noqa: E402

HELPER = """
def _pick(want=''):
    items = ws.picker.items
    pick = -1
    for i in range(len(items)):
        if items[i].get('type') == 'new':
            continue
        t = str(items[i].get('title') or '')
        if want and want.lower() not in t.lower():
            continue
        pick = i
        break
    if pick < 0:
        for i in range(len(items)):
            if items[i].get('type') != 'new':
                pick = i
                break
    ws.picker.sel = pick
    return str(items[pick].get('title'))
def _launch(want=''):
    items = ws.launcher.items
    for i in range(len(items)):
        t = str(items[i].get('title') or '')
        if want and want.lower() in t.lower():
            ws.launcher.sel = i
            break
    return ws.launch_selected() if hasattr(ws, 'launch_selected') else None
ws._g['_pick'] = _pick
ws._g['_launch'] = _launch
"""

# name -> (setup verbs run BEFORE recording, the click verb itself)
SCENARIOS = {
    "open_picker":   ([], "ws.open_picker()"),
    "open_settings": ([], "ws.open_settings()"),
    "open_project":  (["ws.open_picker()", "ws._g['_pick']('coin')"],
                      "ws.pick_selected()"),
    "tab_map":       (["ws.open_picker()", "ws._g['_pick']('coin')",
                       "ws.pick_selected()"],
                      "ws.editor_app.set_tab('map')"),
    "tab_sprites":   (["ws.open_picker()", "ws._g['_pick']('coin')",
                       "ws.pick_selected()"],
                      "ws.editor_app.set_tab('paint')"),
    "tab_blocks":    (["ws.open_picker()", "ws._g['_pick']('coin')",
                       "ws.pick_selected()"],
                      "ws.editor_app.set_tab('blocks')"),
    "tab_code":      (["ws.open_picker()", "ws._g['_pick']('coin')",
                       "ws.pick_selected()", "ws.editor_app.set_tab('map')"],
                      "ws.editor_app.set_tab('code')"),
    "back_to_desk":  (["ws.open_settings()"], "ws.go_home()"),
}

rows = []


def run(b, name, pre, click, settle=4.0):
    b.reset()
    b.pyexec(PROBE)
    b.pyexec(CADENCE)
    b.pyexec(HELPER)
    for verb in pre:
        b.pyval("bool(%s) or True" % verb, 90)
        b.drain(3.0)
    b.drain(3.0)                       # let the precondition settle fully
    b.pyexec("ws._g['_creset']()")
    b.pyexec("ws._crec = True")
    b.pyval("bool(%s) or True" % click, 90)
    b.drain(settle)
    b.pyexec("ws._crec = False")
    r = b.pyval("ws._g['_cagg']()") or (0,) * 9
    nf, nc, _a, _b, _c, worst, hist, _d, _e = r
    total = sum(ms * v for ms, v in hist)
    rows.append((name, nf, worst, total, nc))
    print("  %-14s frames=%-3d worst=%-4d sum=%-5d gc=%d"
          % (name, nf, worst, total, nc))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    want = [s for s in args.only.split(",") if s] or list(SCENARIOS)
    b = P4Board(args.port)
    try:
        for name in want:
            pre, click = SCENARIOS[name]
            try:
                run(b, name, pre, click)
            except Exception as exc:  # noqa: BLE001 -- one bad click must not end the run
                print("  %-14s FAILED: %s" % (name, exc))
        print("\n| click | frames | worst ms | total ms | gc |")
        print("|---|---|---|---|---|")
        for r in sorted(rows, key=lambda r: -r[3]):
            print("| %s | %d | %d | %d | %d |" % r[:5])
    finally:
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
