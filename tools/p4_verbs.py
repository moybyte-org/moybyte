#!/usr/bin/env python3
"""Per-VERB profile of a P4 console surface: how many rect/print/spr/blit calls a
real panel draw issues, and what each verb costs end to end.

The 2026-07-25 session established the desktop UI is wrapper-bound, not pixel-
bound (a 1x1 fill and a 181x121 fill differ by 5ns/px, while the rect() wrapper
costs ~52us). This names WHICH verb, on WHICH panel, so a batch kernel targets
the measured distribution instead of a guess.

Usage:
    python tools/p4_verbs.py [--port /dev/ttyACM0] [--scenario picker|settings|...]
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from p4_autotest import P4Board            # noqa: E402


def pyexec(b, code, timeout=60):
    if not b.pyexec(code, timeout=timeout):
        print("      ! device rejected a snippet")
        return False
    return True


def pyval(b, expr, timeout=30):
    return b.pyval(expr, timeout=timeout)


# Wrap the canvas verbs with a count + total-us counter. Wrapping is per-canvas
# and idempotent; the wrappers stay installed (cheap when _vrec is False) so a
# later scenario just re-arms rather than re-wrapping.
INSTRUMENT = """
import time
_tk = time.ticks_us
_td = time.ticks_diff
ws._vc = {}
ws._vrec = False
def _wrap_canvas(cv):
    if getattr(cv, '_verbed', False):
        return
    for _nm in ('rect', 'rectb', 'print', 'spr', 'cls', 'line', 'circ', 'circb',
                'blit_strip', 'blit_strip_rect', 'blit_window_from', 'map',
                'blit_indices', 'scroll_rect', 'pix'):
        _f = getattr(cv, _nm, None)
        if _f is None:
            continue
        def mk(_f=_f, _nm=_nm):
            def g(*a, **k):
                if not ws._vrec:
                    return _f(*a, **k)
                t = _tk()
                r = _f(*a, **k)
                e = ws._vc.get(_nm)
                if e is None:
                    e = ws._vc[_nm] = [0, 0]
                e[0] += 1
                e[1] += _td(_tk(), t)
                return r
            return g
        setattr(cv, _nm, mk())
    cv._verbed = True
ws._wrap_canvas = _wrap_canvas
"""

# Canvases are created per window, so re-scan before each scenario.
#
# `win.buf` IS the window's canvas: _make_ctx installs it as ws._sys_canvas, so
# a window's whole content draw runs against it and NOT against ws.canvas.
# Wrapping only the root canvas therefore measures the chrome and misses the
# panel -- which is exactly what hid ~45ms of a 74ms picker frame on the first
# pass (2026-07-26).
# NB all three: ws.canvas is the GAME canvas, ws.sys_canvas the root system one
# (which carries the window stamps and the fullscreen surfaces), and win.buf the
# per-window content canvas. Wrapping any subset silently mis-attributes -- the
# 29ms window stamp lives on sys_canvas, the panel draw on win.buf.
REWRAP = """
ws._wrap_canvas(ws.canvas)
_sc = ws.sys_canvas
if _sc is not None:
    ws._wrap_canvas(_sc)
for _w in getattr(ws.wm, '_wins', {}).values():
    _b = getattr(_w, 'buf', None)
    if _b is not None:
        ws._wrap_canvas(_b)
"""


def arm(b):
    pyexec(b, REWRAP)
    b.cmd("py ws._vc.clear() or setattr(ws, '_frames0', ws._frames_drawn) or 1",
          wait_for="PY")
    b.cmd("py setattr(ws, '_vrec', True) or 1", wait_for="PY")


def report(b, name):
    b.cmd("py setattr(ws, '_vrec', False) or 1", wait_for="PY")
    vc = pyval(b, "ws._vc") or {}
    frames = pyval(b, "ws._frames_drawn - ws._frames0") or 0
    tot_us = sum(v[1] for v in vc.values())
    print("\n== %s ==  painted frames=%d   verb total=%.1fms (%.1fms/frame)"
          % (name, frames, tot_us / 1000.0,
             tot_us / 1000.0 / max(1, frames)))
    print("   %-18s %7s %9s %8s %9s" % ("verb", "calls", "total ms", "us/call",
                                        "calls/fr"))
    for nm, (n, us) in sorted(vc.items(), key=lambda kv: -kv[1][1]):
        print("   %-18s %7d %9.1f %8.1f %9.1f"
              % (nm, n, us / 1000.0, us / float(n), n / float(max(1, frames))))
    return {"name": name, "frames": frames, "verbs": vc}


def win_origin(b, key):
    st = b.state()
    w = st["wins"].get(key)
    return None if w is None else (w[0] + 1, w[1] + 1 + w[4], w)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    args = ap.parse_args()
    b = P4Board(args.port)
    out = []
    try:
        b.reset()
        pyexec(b, INSTRUMENT)

        # -- desk idle (baseline: what a quiet frame still costs) --------------
        arm(b); b.drain(3.0); out.append(report(b, "desk idle"))

        # -- picker: open, settle, then drag-scroll ---------------------------
        b.open("picker"); b.drain(10.0)
        g = pyval(b, "ws.wm._wins['make'].ctx.layout.lib_grid")
        o = win_origin(b, "make")
        arm(b); b.drain(3.0); out.append(report(b, "picker idle"))
        if g and o:
            gx, gy, gw, gh = g
            cy = o[1] + gy + gh // 2
            arm(b)
            b.swipe(o[0] + gx + gw - 40, cy, o[0] + gx + 60, cy, frames=30)
            b.drain(2.0)
            out.append(report(b, "picker drag-scroll"))

        # -- settings scroll ---------------------------------------------------
        b.open("settings"); b.drain(2.0)
        o = win_origin(b, "settings")
        rh = pyval(b, "ws.wm._wins['settings'].ctx.layout.set_row_h")
        sx = pyval(b, "ws.wm._wins['settings'].ctx.layout.set_x")
        sw = pyval(b, "ws.wm._wins['settings'].ctx.layout.set_w")
        sy = pyval(b, "ws.wm._wins['settings'].ctx.layout.set_row_y0")
        if o and rh:
            cx = o[0] + sx + sw // 2
            y0 = o[1] + sy + 4 * rh
            arm(b)
            b.swipe(cx, y0, cx, y0 - 3 * rh, frames=25)
            b.drain(1.5)
            out.append(report(b, "settings scroll"))

        # -- fullscreen library (the smooth reference) -------------------------
        pyexec(b, "ws.go_home()"); b.drain(1.5)
        pyexec(b, "ws.open_library()"); b.drain(10.0)
        band = pyval(b, "ws.launcher.band_rect()")
        if band:
            bx, by, bw, bh = band
            cy = by + bh // 2
            arm(b)
            b.swipe(bx + bw - 40, cy, bx + 60, cy, frames=30)
            b.drain(2.0)
            out.append(report(b, "library drag  [reference]"))
    finally:
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
