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

MEASURED 2026-07-27, P4 on glass @ 1024x600 (60fps = 16.7ms):

  | click         | frames | worst | total |
  |---------------|--------|-------|-------|
  | back_to_desk  |   6    |  253  | 1108  |
  | open_picker   |   5    |  238  |  824  |
  | tab_blocks    |   1    |  268  |  252  |
  | tab_map       |   1    |  224  |  224  |
  | open_project  |   1    |  182  |  180  |
  | tab_sprites   |   1    |  121  |  120  |
  | tab_code      |   1    |   99  |   96  |
  | open_settings |   1    |   72  |   72  |

TWO CLICKS COST ABOUT A SECOND, and both are the COVER PIPELINE, not the surface
they open. Attributed with tools/p4_attrib.py's wrap hook over the same driver:

  back_to_desk, 6 painted frames      open_picker, 5 painted frames
    cover_load      32 x  1572ms        cover_load      32 x  1561ms
    launcher.draw    6 x   400 excl     picker.draw      6 x   416 excl
    wallpaper.draw   6 x   222          ws._relayout     1 x    25
    cover_prefetch 200 x  1338 incl     cover_prefetch 195 x  1334 incl

A cover's blob read + parse is ~49ms and there are ~29 carts. _cover_for builds
at most one per PAINTED frame (the _COVER_SLICE_MS budget), and each pop-in
re-arms the redraw gate -- so the desk repaints SIX times (~104ms each: launcher
67 + wallpaper 37) to fold in six covers, while the idle prefetch grinds through
the remaining ~1.3s. The transition itself is cheap; the covers are the click.

TRIED AND REVERTED: giving _cover_prefetch_tick a 150ms time budget on idle
frames instead of one cart per frame. Measured ZERO change (1108 -> 1132, noise),
because the prefetch never runs in either path -- `_cover_seen` is set only by
_cover_for, and neither the Settings window nor the desk's icon column (tile-0
sprite art, not covers) sets it. So the covers are always cold at the moment of
the click. Fix the arming before touching the budget.
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
