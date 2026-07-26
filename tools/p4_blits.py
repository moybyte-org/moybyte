#!/usr/bin/env python3
"""Where the desktop's frame time actually goes: size + cost of the BIG surface
copies (blit_strip / blit_strip_rect / scroll_rect / blit565_scale), measured on
glass.

The 2026-07-26 per-verb profile (tools/p4_verbs.py) found the console UI is no
longer draw-call bound: a windowed content scroll spends ~30 of its ~40ms in ONE
blit_strip -- the full window-content layer stamped 1:1 onto the screen. This
script measures those copies precisely (pixels, ms, MB/s) so the fix targets the
copy, not the draw calls above it.

Usage:  python tools/p4_blits.py [--port /dev/ttyACM0]
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from p4_autotest import P4Board            # noqa: E402


# Records (verb, pixels, us) per big-copy call, plus the end-to-end frame time,
# so the copy share of a frame is a measured ratio and not an inference.
PROBE = """
import time
_tk = time.ticks_us
_td = time.ticks_diff
ws._bl = []
ws._fr = []
ws._rec = False
def _wrapc(cv, tag):
    if getattr(cv, '_blitted', False):
        return
    _bs = cv.blit_strip
    def g_bs(layer, dst_x=0, dst_y=0, _bs=_bs, tag=tag):
        if not ws._rec:
            return _bs(layer, dst_x, dst_y)
        t = _tk()
        r = _bs(layer, dst_x, dst_y)
        ws._bl.append((tag + '.strip', layer.w * layer.h, _td(_tk(), t)))
        return r
    cv.blit_strip = g_bs
    _br = cv.blit_strip_rect
    def g_br(layer, dx, dy, rx, ry, rw, rh, _br=_br, tag=tag):
        if not ws._rec:
            return _br(layer, dx, dy, rx, ry, rw, rh)
        t = _tk()
        r = _br(layer, dx, dy, rx, ry, rw, rh)
        ws._bl.append((tag + '.striprect', rw * rh, _td(_tk(), t)))
        return r
    cv.blit_strip_rect = g_br
    _sr = cv.scroll_rect
    def g_sr(rx, ry, rw, rh, dx, dy, _sr=_sr, tag=tag):
        if not ws._rec:
            return _sr(rx, ry, rw, rh, dx, dy)
        t = _tk()
        r = _sr(rx, ry, rw, rh, dx, dy)
        ws._bl.append((tag + '.scroll', rw * rh, _td(_tk(), t)))
        return r
    cv.scroll_rect = g_sr
    cv._blitted = True
ws._wrapc = _wrapc
"""

FRAME_HOOK = """
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
"""

REWRAP = """
ws._wrapc(ws.canvas, 'screen')
_sc = getattr(ws, 'sys_canvas', None)
if _sc is not None and _sc is not ws.canvas:
    ws._wrapc(_sc, 'sys')
"""


def arm(b):
    b.pyexec(REWRAP)
    b.cmd("py ws._bl.clear() or ws._fr.clear() or 1", wait_for="PY")
    b.cmd("py setattr(ws, '_rec', True) or 1", wait_for="PY")


def report(b, name):
    b.cmd("py setattr(ws, '_rec', False) or 1", wait_for="PY")
    # Roll up on-device: the raw list can be thousands of tuples (too big for
    # one serial line, and the repr alone would cost more than the measurement).
    b.pyexec("_agg = {}\n"
             "for _v, _px, _us in ws._bl:\n"
             "    _e = _agg.get(_v)\n"
             "    if _e is None: _e = _agg[_v] = [0, 0, 0]\n"
             "    _e[0] += 1; _e[1] += _px; _e[2] += _us\n"
             "ws._agg = _agg")
    agg = b.pyval("ws._agg") or {}
    fr = b.pyval("(len(ws._fr), sum(ws._fr), sorted(ws._fr)[len(ws._fr)//2] "
                 "if ws._fr else 0)") or (0, 0, 0)
    nfr, tot, med = fr
    copy_us = sum(v[2] for v in agg.values())
    print("\n== %s ==" % name)
    print("   painted frames=%d  median frame=%dms  total=%dms"
          % (nfr, med, tot // 1000))
    if nfr:
        print("   big copies = %.1fms/frame (%.0f%% of frame time)"
              % (copy_us / 1000.0 / nfr, 100.0 * copy_us / max(1, tot)))
    print("   %-22s %6s %10s %9s %8s" % ("copy", "calls", "Mpix", "ms/call",
                                         "MB/s"))
    for v, (n, px, us) in sorted(agg.items(), key=lambda kv: -kv[1][2]):
        # RGB565 copy moves 2 bytes read + 2 written per pixel.
        mbs = (px * 4.0 / 1e6) / (us / 1e6) if us else 0
        print("   %-22s %6d %10.2f %9.2f %8.0f"
              % (v, n, px / 1e6, us / 1000.0 / n, mbs))
    return agg


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
        b.pyexec(FRAME_HOOK)

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
            print("   window = %s" % (o[2],))

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
            print("   window = %s" % (o[2],))

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
            print("   band = %s" % (band,))
    finally:
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
