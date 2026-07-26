#!/usr/bin/env python3
"""Attribute a UI frame's TIME to the code responsible -- the twin of p4_alloc.py.

p4_alloc answers "who allocates"; this answers "who spends the milliseconds, and
on what". For each wrapped phase it reports EXCLUSIVE wall time (self, children
subtracted), the number of native draw-gate calls it issued, and how much of its
wall was spent INSIDE those kernels. The remainder is MicroPython: loop overhead,
attribute lookups and the per-call MP->C transition.

  python tools/p4_attrib.py                       # map, paint, blocks
  python tools/p4_attrib.py --only paint
  python tools/p4_attrib.py --extra "ws.map_ui::_draw_dims::map.dims"

That kernel-vs-dispatch split is the whole point. A surface that is 80% kernel is
asking for fewer PIXELS (or an accelerator); a surface that is 50% dispatch is
asking for fewer CALLS, which on this codebase means a native batch verb -- the
shape already solved twice by spr_batch (#43) and spr_gate (#63).

HOW IT MEASURES
---------------
Exclusive attribution uses a stack, like p4_alloc: a call books its own total
minus what its wrapped children reported, and adds its total to its parent's
accumulator. Nesting wrappers is therefore safe, and the columns sum to the top
phase minus what is unwrapped (printed as "unattributed" -- how you learn the
wrap set is too shallow).

Native counters come from `DeviceCanvas.gate_counts()`, read as ABSOLUTE values
at entry and exit and differenced. Do NOT call gate_counts_reset() inside a
wrapper: it zeroes the counters an enclosing wrapper is mid-way through reading,
so the outer phase silently loses everything its children drew.

TRAPS (each of these produced a confident wrong number first)
-------------------------------------------------------------
  * The us timers are gated behind `_gate_state[_ST_PROF]`, which the ROOT canvas
    syncs once per frame from `cv._prof`. A WINDOW canvas is never synced, so
    arming `ws.sys_canvas._prof` before the window exists leaves fill_us at zero
    and the frame looks like 100% dispatch. This probe re-arms whenever
    `ws.sys_canvas` changes identity, and writes the state slot directly.
  * `ws.sys_canvas` is NOT one object on the windowed tier: wm_windowed hands
    each window its own buffer canvas (and the direct-render path hands back the
    root). Read it inside the wrapper, never cache it across calls.
  * Counting Python-level verb calls means REPLACING `cv.rect`, which on this
    board is a native C gate, with a Python wrapper -- ~10us x ~1000 calls, so it
    inflates the very wall it is measuring. Hence --verbs is opt-in, and its
    absolute times must not be quoted.
  * Only rect/rectb/print/pix are gated, so `spr`/`line`/`circ` time lands in the
    dispatch column rather than the kernel column. On the editor tabs that is a
    rounding error; on a sprite-heavy surface it is not.
  * The wrapped method must be the one that RUNS. layers.py routes the tabs
    (_MapLayer.draw -> ws.map_ui._draw_map, _BlocksLayer.draw ->
    ws.block_ui._draw_blocks), while paint owns its own PaintLayer.draw. Wrapping
    a plausible-but-unused method reports "0 calls" and reads like a fast phase.
    A missing attribute is reported as `!MISSING`, a wrapped-but-never-called one
    as 0 calls -- check both before believing a table.

RESULTS (2026-07-27, P4 on glass @ 1024x600, per painted frame during a drag)
------------------------------------------------------------------------------
See docs/ui_damage_model_v1.md Section 0.05 -- the numbers live there because
they redirected that plan and the doc has to carry the reasoning.
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from p4_autotest import P4Board            # noqa: E402

# -- device half ----------------------------------------------------------------

PROBE = """
import time
ws._ph = {}
ws._pstk = []
ws._prec = False
ws._pcv = None
def _wrapt(obj, nm, tag):
    f = getattr(obj, nm, None)
    if f is None:
        ws._ph[tag + '!MISSING'] = [0, 0, 0, 0, 0, 0, 0]
        return False
    def g(*a, **k):
        if not ws._prec:
            return f(*a, **k)
        cv = ws.sys_canvas
        if cv is not ws._pcv:
            # Arm the DRAW2 us timers on THIS canvas -- a window buffer is never
            # synced by the root's per-frame hook (see TRAPS).
            ws._pcv = cv
            try:
                cv._prof = True
                st = cv._gate_state
                if st is not None:
                    st[9] = 1
            except Exception:
                pass
        gcn = getattr(cv, 'gate_counts', None)
        c0 = gcn() if gcn is not None else (0, 0, 0, 0)
        ws._pstk.append([0, 0, 0, 0, 0])
        t = time.ticks_us()
        try:
            return f(*a, **k)
        finally:
            dt = time.ticks_diff(time.ticks_us(), t)
            c1 = gcn() if gcn is not None else (0, 0, 0, 0)
            kid = ws._pstk.pop()
            nf = c1[0] - c0[0]
            nt = c1[1] - c0[1]
            fus = c1[2] - c0[2]
            tus = c1[3] - c0[3]
            s = ws._ph.get(tag)
            if s is None:
                s = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
                ws._ph[tag] = s
            s[0] += 1
            s[1] += dt              # inclusive wall
            s[2] += dt - kid[0]     # exclusive wall
            s[3] += nf - kid[1]     # exclusive fills
            s[4] += fus - kid[2]    # exclusive us in the fill kernel
            s[5] += nt - kid[3]     # exclusive texts
            s[6] += tus - kid[4]    # exclusive us in the text kernel
            s[7] += nf              # INCLUSIVE fills (the summary reads these)
            s[8] += fus            # INCLUSIVE us in the fill kernel
            s[9] += nt             # INCLUSIVE texts
            s[10] += tus           # INCLUSIVE us in the text kernel
            if ws._pstk:
                p = ws._pstk[-1]
                p[0] += dt
                p[1] += nf
                p[2] += fus
                p[3] += nt
                p[4] += tus
    setattr(obj, nm, g)
    return True
def _pframes():
    return ws._frames_drawn
def _prows():
    return [(k, v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8],
             v[9], v[10]) for k, v in ws._ph.items()]
ws._g['_wrapt'] = _wrapt
ws._g['_pframes'] = _pframes
ws._g['_prows'] = _prows
"""

# Optional: count Python-level draw verbs per phase. Replaces the native gates
# with Python wrappers, so it inflates wall -- read the COUNTS, not the times.
VERBS = """
ws._nv = {}
def _countverbs(cv):
    for nm in ('rect', 'rectb', 'spr', 'print', 'pix', 'line', 'circ', 'circfill'):
        f = getattr(cv, nm, None)
        if f is None:
            continue
        def mk(f=f, nm=nm):
            def g(*a, **k):
                t = time.ticks_us()
                r = f(*a, **k)
                s = ws._nv.get(nm)
                if s is None:
                    s = [0, 0]
                    ws._nv[nm] = s
                s[0] += 1
                s[1] += time.ticks_diff(time.ticks_us(), t)
                return r
            return g
        setattr(cv, nm, mk())
    return True
ws._g['_countverbs'] = _countverbs
"""

HELPER = """
def _edit(tab, want=''):
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
    ws.open_picker()
    ws.pick_selected()
    ws.editor_app.set_tab(tab)
    return str(items[pick].get('title'))
def _litpx(sh, n):
    ox, oy = sh.tile_origin(n)
    c = 0
    for yy in range(sh.TILE):
        for xx in range(sh.TILE):
            if sh.pget(ox + xx, oy + yy):
                c += 1
    return c
def _paintpick():
    # The Paint tab opens on sprite 0, which on most carts is BLANK (Coin Quest's
    # art starts at 1). Land on the first tile that has pixels.
    pe = ws.paint
    sh = pe.sheet if pe is not None else None
    if sh is None:
        return -1
    for n in range(64):
        try:
            if _litpx(sh, n):
                pe.n = n
                return n
        except Exception:
            break
    return -1
def _content():
    # What is actually ON the surface. An empty editor is not a fast editor, it
    # is a DIFFERENT screen -- the surface sweep learned that the expensive way.
    p = ws.project
    be = getattr(ws.block_ui, 'blocks_ed', None)
    se = getattr(ws.scene_ui, 'sceneedit', None)
    tm = getattr(p, 'tilemap', None)
    pe = ws.paint
    n = -1
    npx = -1
    if pe is not None and pe.sheet is not None:
        n = pe.n
        npx = _litpx(pe.sheet, n)
    return (len(be.rows) if be is not None else -1,
            len(se.rows) if se is not None else -1,
            (tm.w, tm.h) if tm is not None else None, n, npx)
ws._g['_edit'] = _edit
ws._g['_paintpick'] = _paintpick
ws._g['_content'] = _content
"""

BAR = ("ws.bar_layer", "_draw_status_strip", "bar.strip")

# (open verb, [(device expr, attribute, tag), ...]). The FIRST wrap is the phase
# everything else nests inside -- the table's "top".
SURFACES = {
    "map": ("ws._g['_edit']('map', %r)", [
        ("ws.map_ui", "_draw_map", "map._draw_map"),
        ("ws.map_ui", "_draw_sel_actions", "map.sel_actions"),
        ("ws.map_ui", "_draw_dims", "map.dims"),
        BAR,
    ]),
    "paint": ("ws._g['_edit']('paint', %r)", [
        ("ws.paint_layer", "draw", "paint.draw"),
        ("ws.paint_layer", "_draw_paint", "paint._draw_paint"),
        ("ws.paint_layer", "_draw_tools", "paint.tools"),
        ("ws.paint_layer", "_draw_grid_overlay", "paint.grid_overlay"),
        BAR,
    ]),
    "blocks": ("ws._g['_edit']('blocks', %r)", [
        ("ws.block_ui", "_draw_blocks", "blocks._draw_blocks"),
        ("ws.block_ui", "_draw_blk_row", "blocks.row"),
        ("ws.block_ui", "_draw_sprite_list", "blocks.sprite_list"),
        ("ws.block_ui", "_draw_scene_pane", "blocks.scene_pane"),
        ("ws.block_ui", "_layout_workspace", "blocks.layout_ws"),
        # The blocks tab's dominant phase is the SCENE pane it hosts on a wide
        # canvas (#93/#85), so its renderer is wrapped here too.
        ("ws.scene_ui", "_draw_scene", "scene._draw_scene"),
        ("ws.scene_ui", "_frame_world", "scene.frame_world"),
        BAR,
    ]),
    "scene": ("ws._g['_edit']('scene', %r)", [
        ("ws.scene_ui", "_draw_scene", "scene._draw_scene"),
        ("ws.scene_ui", "_frame_world", "scene.frame_world"),
        BAR,
    ]),
    "code": ("ws._g['_edit']('code', %r)", [
        ("ws.code_layer", "draw", "code.draw"),
        BAR,
    ]),
}


def run(b, name, verb, wraps, verbs=False, reps=2, frames=30, cart=""):
    b.reset()
    b.pyexec(PROBE)
    b.pyexec(HELPER)
    if verbs:
        b.pyexec(VERBS)
    opened = b.pyval(verb % cart, 90)
    b.drain(6.0)                        # first paint + any cover pop-in
    if name == "paint":
        b.pyval("ws._g['_paintpick']()", 60)   # off the blank sprite 0
    content = b.pyval("ws._g['_content']()", 60)
    missing = []
    for expr, attr, tag in wraps:
        ok = b.pyval("ws._g['_wrapt'](%s, %r, %r)" % (expr, attr, tag))
        if not ok:
            missing.append("%s.%s" % (expr, attr))
    if verbs:
        b.pyexec("ws._g['_countverbs'](ws.sys_canvas)")
    st = b.state()
    wins = st.get("wins") or {}
    if wins:
        k = "make" if "make" in wins else list(wins)[0]
        w = wins[k]
        cx = w[0] + 1 + w[2] // 2
        ct = w[1] + 1 + w[4]
        y0, y1 = ct + (w[3] - w[4]) - 40, ct + 40
    else:
        cx, y0, y1 = 512, 540, 80
    b.pyexec("ws._ph.clear()")
    b.pyexec("del ws._pstk[:]")
    n0 = b.pyval("ws._g['_pframes']()") or 0
    b.pyexec("ws._prec = True")
    for _ in range(reps):
        b.ser.write(("swipe %d %d %d %d %d\n" % (cx, y0, cx, y1, frames)).encode())
        b.ser.flush()
        b.wait_line("swipe done", 60)
        b.drain(1.0)
    b.pyexec("ws._prec = False")
    n1 = b.pyval("ws._g['_pframes']()") or 0
    rows = b.pyval("ws._g['_prows']()") or []
    nf = max(1, n1 - n0)

    print("\n== %s ==  cart=%s  %d painted frames%s"
          % (name, opened, nf,
             "  (VERB-COUNTING: wall inflated)" if verbs else ""))
    if content:
        print("   content: %s block rows, %s scene actors, map %s, "
              "sprite #%s (%s lit px)" % content)
    if missing:
        print("   !! not found: %s" % ", ".join(missing))
    print("   %-22s %6s %8s %8s %8s %8s %7s" %
          ("phase", "calls", "excl ms", "incl ms", "fills", "kern ms", "texts"))
    for r in sorted(rows, key=lambda r: -r[3]):
        tag, calls, incl, excl, fills, fus, texts, tus = r[:8]
        print("   %-22s %6.1f %8.1f %8.1f %8.1f %8.1f %7.1f" %
              (tag, calls / float(nf), excl / 1000.0 / nf, incl / 1000.0 / nf,
               fills / float(nf), fus / 1000.0 / nf, texts / float(nf)))
    # The first wrap is the enclosing phase; everything else nests inside it, so
    # the summary reads its INCLUSIVE columns (r[8]/r[9]).
    head = None
    for r in rows:
        if r[0] == wraps[0][2]:
            head = r
    if head is not None:
        incl = head[2]
        fills, fus, texts, tus = head[8], head[9], head[10], head[11]
        kern = (fus + tus) / 1000.0 / nf
        wall = incl / 1000.0 / nf
        # No "unattributed" row: exclusive time telescopes, so the top phase's own
        # `excl` IS the time it spends outside every wrapped child. Read that
        # column -- a big one means the wrap set is too shallow.
        print("\n   %s per painted frame:" % wraps[0][2])
        print("     wall             %7.1f ms" % wall)
        print("     native fills     %7.0f calls" % (fills / float(nf)))
        print("     in fill kernel   %7.1f ms   (%.0f%%, %.1f us/fill)"
              % (fus / 1000.0 / nf, 100.0 * fus / max(1, incl),
                 fus / float(max(1, fills))))
        print("     native texts     %7.0f calls, %.1f ms"
              % (texts / float(nf), tus / 1000.0 / nf))
        print("     => dispatch/py   %7.1f ms   (%.0f%%)"
              % (wall - kern, 100.0 * (incl - fus - tus) / max(1, incl)))
    if verbs:
        vr = b.pyval("[(k, v[0], v[1]) for k, v in ws._nv.items()]") or []
        print("\n   verb calls per frame (wall INFLATED by the wrapper; read the counts):")
        for k, n, us in sorted(vr, key=lambda r: -r[2]):
            print("     %-8s %7.0f calls  %6.1f ms  (%.0f us each)"
                  % (k, n / float(nf), us / 1000.0 / nf, us / float(max(1, n))))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--only", default="", help="comma-separated subset")
    ap.add_argument("--verbs", action="store_true",
                    help="also count Python-level verb calls (inflates wall)")
    ap.add_argument("--extra", action="append", default=[],
                    help="EXPR::ATTR::TAG, appended to every surface's wrap set")
    ap.add_argument("--cart", default="coin",
                    help="substring of the cart TITLE to open (default: coin -- "
                         "Coin Quest, the seed cart with blocks, a map and a scene; "
                         "the first cart on the shelf has an empty block outline)")
    args = ap.parse_args()
    want = [s for s in args.only.split(",") if s] or ["map", "paint", "blocks"]
    extra = [tuple(s.split("::")) for s in args.extra]
    b = P4Board(args.port)
    try:
        for name in want:
            verb, wraps = SURFACES[name]
            try:
                run(b, name, verb, list(wraps) + extra, verbs=args.verbs,
                    cart=args.cart)
            except Exception as exc:  # noqa: BLE001 -- one bad surface must not end the sweep
                print("  %-10s FAILED: %s" % (name, exc))
    finally:
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
