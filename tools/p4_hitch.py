#!/usr/bin/env python3
"""Find the OUTLIER frames, not the average one.

By 2026-07-26 the console's median painted frame during a gesture was already at
60fps (Settings 15ms) while p90 was 63ms and the worst 157ms -- and a run of fast
frames punctuated by hitches is exactly what reads as "smoother but not smooth".
Averages hide that, so this records EVERY painted frame with a per-phase
breakdown and reports the slowest ones with what dominated them.

Usage:  python tools/p4_hitch.py [--port /dev/ttyACM0] [--surface settings|picker]
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from p4_autotest import P4Board            # noqa: E402


# Per frame: total ms plus the ms attributed to each wrapped phase THIS frame, so
# a slow frame names its own cause instead of being averaged away.
PROBE = """
import time, gc
_tk = time.ticks_ms
_td = time.ticks_diff
ws._fr = []
ws._rec = False
ws._cur = {}
ws._wrapped = set()
def _acc(nm, us):
    ws._cur[nm] = ws._cur.get(nm, 0) + us
ws._acc = _acc
def _wrapm(obj, nm, tag):
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

HOOK = """
if not hasattr(ws, '_hhook'):
    _of = ws.frame
    def _f(dt, _of=_of):
        a = _tk()
        n0 = ws._frames_drawn
        g0 = gc.mem_alloc()
        ws._cur = {}
        r = _of(dt)
        if ws._rec and ws._frames_drawn != n0:
            ws._fr.append((_td(_tk(), a), ws._cur,
                           (gc.mem_alloc() - g0) // 1024))
        return r
    ws.frame = _f
    ws._hhook = True
ws._wrapm(ws.wm, '_draw_windows', 'wm.windows')
ws._wrapm(ws.wm, '_win_chrome', 'wm.chrome')
ws._wrapm(ws.wm, '_blit_backdrop_cache', 'desk.cache')
ws._wrapm(ws, '_composite_game', 'composite')
ws._wrapm(ws, '_journal_idle_tick', 'journal')
_c = ws.canvas._comp
for _m in ('flush', 'present_pending'):
    ws._wrapm(_c, _m, 'comp.' + _m)
for _l in ws.wm.visible_stack_rev():
    _n = getattr(_l, 'id', None) or type(_l).__name__
    ws._wrapm(_l, 'draw', 'layer:' + str(_n))
_wp = getattr(ws, 'wallpaper', None)
if _wp is not None:
    ws._wrapm(_wp, 'draw', 'wallpaper')
for _k, _w in getattr(ws.wm, '_wins', {}).items():
    try:
        ws._wrapm(ws.wm._content_for(_w.kind), 'draw', 'content:' + _k)
    except Exception:
        pass
"""


def report(b, name, top=6):
    b.cmd("py setattr(ws, '_rec', False) or 1", wait_for="PY")
    n = b.pyval("len(ws._fr)") or 0
    if not n:
        print("  %s: no painted frames" % name)
        return
    tot = b.pyval("sorted(f[0] for f in ws._fr)") or []
    med = tot[len(tot) // 2]
    p90 = tot[min(len(tot) - 1, int(len(tot) * 0.9))]
    print("\n== %s ==  %d painted frames   median=%dms p90=%dms max=%dms"
          % (name, n, med, p90, tot[-1]))
    # The slowest frames, each with its own phase split.
    b.pyexec("ws._slow = sorted(ws._fr, key=lambda f: -f[0])[:%d]" % top)
    slow = b.pyval("[(f[0], f[2], sorted(f[1].items(), key=lambda kv: -kv[1])[:3])"
                   " for f in ws._slow]") or []
    print("   %-8s %-9s %s" % ("frame ms", "alloc KB", "dominant phases"))
    for ms, kb, phases in slow:
        print("   %-8d %-9d %s" % (ms, kb,
                                   "  ".join("%s=%d" % (k, v) for k, v in phases)))
    # How much of the total time lives in frames slower than 2x the median?
    over = [t for t in tot if t > 2 * med]
    print("   frames >2x median: %d of %d, carrying %d%% of the time"
          % (len(over), n, 100 * sum(over) // max(1, sum(tot))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    args = ap.parse_args()
    b = P4Board(args.port)
    try:
        def fresh():
            """Boot into the DESK world with the probe loaded.

            Deliberately a RESET between surfaces, not ws.go_home(): go_home lands
            in the fullscreen Library (the play world), where the picker is not a
            window at all -- so ws.wm._wins has no 'make' and the measurement died
            with a KeyError. A reset also keeps the two surfaces from contaminating
            each other's caches."""
            b.reset()
            b.pyexec(PROBE)

        fresh()

        def gesture(x0, y0, x1, y1, n=30):
            b.ser.write(("swipe %d %d %d %d %d\n" % (x0, y0, x1, y1, n)).encode())
            b.ser.flush()
            b.wait_line("swipe done", 60)
            b.drain(1.2)

        b.open("settings")
        b.drain(3.0)
        b.pyexec(HOOK)
        w = b.state()["wins"]["settings"]
        cx = w[0] + 1 + w[2] // 2
        ct = w[1] + 1 + w[4]
        lo, hi = ct + (w[3] - w[4]) - 50, ct + 50
        for _ in range(2):
            gesture(cx, lo, cx, hi)          # warm
        b.cmd("py ws._fr.clear() or 1", wait_for="PY")
        b.cmd("py setattr(ws, '_rec', True) or 1", wait_for="PY")
        for _ in range(3):
            gesture(cx, lo, cx, hi)
        report(b, "settings scroll")

        fresh()
        b.open("picker")
        b.drain(14.0)
        b.pyexec(HOOK)
        g = b.pyval("ws.wm._wins['make'].ctx.layout.lib_grid")
        if g is None or "make" not in b.state()["wins"]:
            print("\n  picker did not open as a window -- skipped")
            return 0
        w = b.state()["wins"]["make"]
        ox, oy = w[0] + 1, w[1] + 1 + w[4]
        gx, gy, gw, gh = g
        cy = oy + gy + gh // 2
        for _ in range(3):
            gesture(ox + gx + gw - 40, cy, ox + gx + 60, cy)
        b.cmd("py ws._fr.clear() or 1", wait_for="PY")
        b.cmd("py setattr(ws, '_rec', True) or 1", wait_for="PY")
        for _ in range(3):
            gesture(ox + gx + gw - 40, cy, ox + gx + 60, cy)
        report(b, "picker drag")
    finally:
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
