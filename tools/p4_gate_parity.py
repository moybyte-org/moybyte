#!/usr/bin/env python3
"""On-glass pixel-parity check for the native draw gates (#155).

Draws the SAME deterministic pattern into a scratch layer twice -- once through
the native rect/rectb/print/pix gates, once through the Python methods they
shadow (removed with delattr, so the class methods show through again) -- and
compares the two RGB565 buffers byte for byte on the device.

The gates are a pure fast lane: any pixel difference is a bug, not a tradeoff.
Exercised against camera offsets, clip rects, pal remaps, negative and
off-surface coordinates, float coords, and both font scales.

Usage:  python tools/p4_gate_parity.py [--port /dev/ttyACM0]
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from p4_autotest import P4Board            # noqa: E402


# One pattern function, replayed against whatever rect/print the layer currently
# exposes. Every case is a shape the console chrome actually draws.
PATTERN = """
def _pat(cv):
    cv.reset_state()
    cv.cls(0)
    cv.rect(5, 5, 40, 20, 3)
    cv.rectb(50, 5, 40, 20, 7)
    cv.print('Hi Ab', 5, 40, 11)
    cv.pix(3, 3, 9)
    # negative / oversized / zero-area -- the clamp paths
    cv.rect(-10, -6, 30, 30, 5)
    cv.rect(180, 100, 60, 60, 6)
    cv.rect(20, 60, 0, 10, 4)
    cv.rect(20, 60, 10, -3, 4)
    cv.rectb(-4, 70, 20, 20, 2)
    # float coords (layout code produces them)
    cv.rect(70.7, 45.2, 12.9, 8.4, 8)
    cv.print('float', 70.5, 60.5, 12)
    # clip rect
    cv.clip(30, 30, 60, 40)
    cv.rect(0, 0, 200, 120, 10)
    cv.print('clipped text here', 10, 35, 1)
    cv.rectb(25, 25, 70, 50, 13)
    cv.pix(35, 35, 14)
    cv.pix(5, 5, 14)
    cv.clip()
    # camera
    cv.camera(12, 7)
    cv.rect(20, 20, 25, 15, 15)
    cv.print('cam', 20, 40, 2)
    cv.pix(21, 21, 3)
    cv.camera(-9, -4)
    cv.rect(0, 0, 18, 9, 6)
    cv.camera(0, 0)
    # camera AND clip together
    cv.camera(5, 5)
    cv.clip(40, 20, 50, 50)
    cv.rect(30, 10, 80, 80, 9)
    cv.print('both', 42, 30, 4)
    cv.clip()
    cv.camera(0, 0)
    # pal remap must reach every verb
    cv.pal(3, 12)
    cv.rect(120, 10, 20, 20, 3)
    cv.rectb(145, 10, 20, 20, 3)
    cv.print('pal', 120, 35, 3)
    cv.pix(121, 34, 3)
    cv.pal()
    cv.rect(120, 60, 20, 20, 3)
    # out-of-range colour index (wraps &63) and empty string
    cv.rect(160, 60, 15, 15, 70)
    cv.print('', 5, 100, 3)
    cv.print('tail', 5, 108, 3)
ws._pat = _pat
"""

CHECK = """
def _parity(w, h, fs):
    cv = ws.sys_canvas.new_layer(w, h)
    try:
        cv.set_font_scale(fs)
    except Exception:
        pass
    if cv._gate_ctx is None:
        return ('no gates', 0, 0)
    ws._pat(cv)
    a = bytearray(cv._buf)                 # gated pixels
    for _n in ('rect', 'rectb', 'print', 'pix'):
        try:
            delattr(cv, _n)                # unshadow the Python methods
        except Exception:
            pass
    gated_off = type(cv.rect).__name__
    ws._pat(cv)
    b = cv._buf                            # Python pixels
    bad = 0
    first = -1
    for i in range(len(a)):
        if a[i] != b[i]:
            bad += 1
            if first < 0:
                first = i
    return (gated_off, bad, first)
ws._parity = _parity
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    args = ap.parse_args()
    b = P4Board(args.port)
    rc = 0
    try:
        b.reset()
        if not b.pyexec(PATTERN) or not b.pyexec(CHECK):
            print("FAIL: could not upload the probe")
            return 1
        for (w, h, fs) in ((200, 120, 1), (200, 120, 2), (137, 91, 2)):
            r = b.pyval("ws._parity(%d, %d, %d)" % (w, h, fs), timeout=120)
            if r is None:
                print("FAIL %dx%d fs=%d: device error" % (w, h, fs))
                rc = 1
                continue
            mode, bad, first = r
            ok = (mode == "bound_method" or mode == "function") and bad == 0
            print("%-22s gates-off=%-14s mismatched bytes=%-6d first=%d  %s"
                  % ("%dx%d fs=%d" % (w, h, fs), mode, bad, first,
                     "PASS" if ok else "FAIL"))
            if not ok:
                rc = 1
    finally:
        b.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
