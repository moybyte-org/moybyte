#!/usr/bin/env python3
"""Whole-frame phase breakdown on glass: every WM layer's draw(), the game
composite, the cursor, the compositor flush -- and the residual.

Complements tools/p4_verbs.py (per canvas verb) and tools/p4_blits.py (per big
surface copy) by accounting for the WHOLE painted frame, so no cost hides in a
phase nobody wrapped. Written 2026-07-26 after the verb + copy probes together
explained only ~half of a 74ms picker-drag frame.

Usage:  python tools/p4_frame.py [--port /dev/ttyACM0] [--scenario NAME]
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from p4_autotest import P4Board            # noqa: E402


PROBE = """
import time
_tk = time.ticks_us
_td = time.ticks_diff
ws._ph = {}
ws._fr = []
ws._rec = False
ws._wrapped = set()
def _acc(nm, us):
    e = ws._ph.get(nm)
    if e is None:
        e = ws._ph[nm] = [0, 0]
    e[0] += 1
    e[1] += us
ws._acc = _acc
def _wrapm(obj, nm, tag):
    # Guard by (object, name) in a device-side set: MicroPython will not hold an
    # arbitrary attribute on a closure, so the obvious `g._phased = True` marker
    # never sticks and every re-arm NESTS another wrapper -- which double- and
    # triple-counted whole scenarios on the first run (2026-07-26).
    k = (id(obj), nm)
    if k in ws._wrapped:
        return
    f = getattr(obj, nm, None)
    if f is None:
        return
    def g(*a, **k2):
        if not ws._rec:
            return f(*a, **k2)
        t = _tk()
        r = f(*a, **k2)
        ws._acc(tag, _td(_tk(), t))
        return r
    ws._wrapped.add(k)
    setattr(obj, nm, g)
ws._wrapm = _wrapm
"""

# The frame hook must measure the WHOLE loop iteration, and the layer set is
# rebuilt per relayout, so layers are (re)wrapped from inside the hook.
HOOK = """
if not hasattr(ws, '_fhook'):
    _of = ws.frame
    def _f(dt, _of=_of):
        a = _tk()
        n0 = ws._frames_drawn
        r = _of(dt)
        if ws._rec and ws._frames_drawn != n0:
            ws._fr.append(_td(_tk(), a))
        return r
    ws.frame = _f
    ws._fhook = True
ws._wrapm(ws, '_composite_game', 'composite_game')
ws._wrapm(ws, '_draw_cursor', 'cursor')
ws._wrapm(ws.wm, 'draw_stack', 'wm.draw_stack(build)')
"""

WRAP_LAYERS = """
for _k, _w in getattr(ws.wm, '_wins', {}).items():
    try:
        ws._wrapm(ws.wm._content_for(_w.kind), 'draw', 'content:' + _k)
    except Exception:
        pass
for _n in ('_draw_app_window', '_win_chrome', '_draw_taskbar_chips',
           '_blit_backdrop_cache', '_draw_desk_icons'):
    ws._wrapm(ws.wm, _n, 'wm.' + _n)
for _l in ws.wm.visible_stack_rev():
    _n = getattr(_l, 'id', None) or type(_l).__name__
    ws._wrapm(_l, 'draw', 'layer:' + str(_n))
_wp = getattr(ws, 'wallpaper', None)
if _wp is not None:
    ws._wrapm(_wp, 'draw', 'wallpaper')
_c = ws.canvas._comp
for _m in ('flush', 'present_pending'):
    ws._wrapm(_c, _m, 'comp.' + _m)
"""


def arm(b):
    b.pyexec(WRAP_LAYERS)
    b.cmd("py ws._ph.clear() or ws._fr.clear() or 1", wait_for="PY")
    b.cmd("py setattr(ws, '_rec', True) or 1", wait_for="PY")


def report(b, name):
    b.cmd("py setattr(ws, '_rec', False) or 1", wait_for="PY")
    ph = b.pyval("ws._ph") or {}
    fr = b.pyval("(len(ws._fr), sum(ws._fr), sorted(ws._fr)[len(ws._fr)//2] "
                 "if ws._fr else 0)") or (0, 0, 0)
    nfr, tot, med = fr
    print("\n== %s ==  painted frames=%d  median frame=%.1fms"
          % (name, nfr, med / 1000.0))
    if not nfr:
        return ph
    # draw_stack wraps the layers, so it double-counts them; report it apart.
    inner = sum(v[1] for k, v in ph.items()
                if k.startswith("layer:") or k in ("wallpaper", "cursor",
                                                   "composite_game"))
    print("   %-30s %6s %10s %9s" % ("phase", "calls", "ms/frame", "% frame"))
    for k, (n, us) in sorted(ph.items(), key=lambda kv: -kv[1][1]):
        print("   %-30s %6d %10.2f %8.0f%%"
              % (k, n, us / 1000.0 / nfr, 100.0 * us / tot))
    resid = tot - inner - sum(v[1] for k, v in ph.items()
                              if k.startswith("comp."))
    print("   %-30s %6s %10.2f %8.0f%%"
          % ("(residual: loop + input + gate)", "-", resid / 1000.0 / nfr,
             100.0 * resid / tot))
    return ph


def win_origin(b, key):
    st = b.state()
    w = st["wins"].get(key)
    return None if w is None else (w[0] + 1, w[1] + 1 + w[4], w)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    args = ap.parse_args()
    b = P4Board(args.port)
    try:
        b.reset()
        b.pyexec(PROBE)
        b.pyexec(HOOK)

        b.open("picker")
        b.drain(10.0)
        g = b.pyval("ws.wm._wins['make'].ctx.layout.lib_grid")
        o = win_origin(b, "make")
        if g and o:
            gx, gy, gw, gh = g
            cy = o[1] + gy + gh // 2
            arm(b)
            b.swipe(o[0] + gx + gw - 40, cy, o[0] + gx + 60, cy, frames=30)
            b.drain(2.0)
            report(b, "picker drag-scroll (windowed)")

        b.open("settings")
        b.drain(2.0)
        o = win_origin(b, "settings")
        rh = b.pyval("ws.wm._wins['settings'].ctx.layout.set_row_h")
        sx = b.pyval("ws.wm._wins['settings'].ctx.layout.set_x")
        sw = b.pyval("ws.wm._wins['settings'].ctx.layout.set_w")
        sy = b.pyval("ws.wm._wins['settings'].ctx.layout.set_row_y0")
        if o and rh:
            cx = o[0] + sx + sw // 2
            y0 = o[1] + sy + 4 * rh
            arm(b)
            b.swipe(cx, y0, cx, y0 - 3 * rh, frames=25)
            b.drain(1.5)
            report(b, "settings scroll (windowed)")

        b.pyexec("ws.go_home()")
        b.drain(1.5)
        b.pyexec("ws.open_library()")
        b.drain(10.0)
        band = b.pyval("ws.launcher.band_rect()")
        if band:
            bx, by, bw, bh = band
            cy = by + bh // 2
            arm(b)
            b.swipe(bx + bw - 40, cy, bx + 60, cy, frames=30)
            b.drain(2.0)
            report(b, "library drag (fullscreen)  [reference]")
    finally:
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
