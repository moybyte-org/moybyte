#!/usr/bin/env python3
"""On-glass correctness check for the window-chrome freeze (#155).

The freeze skips a window's title strip / border once those pixels sit in BOTH
DPI ping-pong buffers. The failure mode it must not have is a STALE buffer: one
of the two never gets the current chrome, so the panel alternates between the
right pixels and last-second's, which reads as a flicker.

Method: run a slow content drag (so frames stay quiet and the freeze engages),
snapshot the chrome strip out of whichever buffer each frame targeted, and
require (a) every snapshot of a given buffer to match its previous one and
(b) the two buffers to match EACH OTHER. Then perturb (theme change), re-settle,
and repeat -- that exercises the streak-restart path, which is where a
half-refreshed buffer would show up.

Usage:  python tools/p4_chrome_freeze.py [--port /dev/ttyACM0]
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from p4_autotest import P4Board            # noqa: E402


PROBE = """
import moy_gfx
ws._strips = {}
ws._verdict = []
def _snap(key):
    win = ws.wm._wins[key]
    cv = ws.sys_canvas
    w = win.w
    h = win.title_h + 2                    # strip + the border line under it
    if win.x < 0 or win.y < 0 or win.x + w > cv.w:
        return ('oob', None)
    b = bytearray(w * h * 2)
    moy_gfx.pack_strip(cv._buf, cv.w, win.x, win.y, w, h, b)
    k = id(cv._buf)
    prev = ws._strips.get(k)
    ws._strips[k] = b
    same = None if prev is None else (bytes(prev) == bytes(b))
    ws._verdict.append((k, same))
    return (k, same)
ws._snap = _snap
def _cross():
    # Do the two ping-pong buffers hold the SAME chrome?
    vs = list(ws._strips.values())
    if len(vs) < 2:
        return ('one-buffer', len(vs))
    return ('cross', all(bytes(v) == bytes(vs[0]) for v in vs[1:]))
ws._cross = _cross
"""


def hold(b, x0, y0, x1, y1, frames):
    """Start a SLOW drag and return without waiting for it to finish.

    It must actually move: a zero-motion hold never sets _content_gesture (the
    scroll region reports a gesture only once the finger travels), so the desk
    backdrop keeps live-rendering, _desk_painted stays True and the frame is
    never quiet -- the freeze would simply never engage. Moving is also the
    stronger test: the CONTENT scrolls under a chrome strip that must not move
    a single pixel."""
    b.ser.write(("swipe %d %d %d %d %d\n"
                 % (x0, y0, x1, y1, frames)).encode())
    b.ser.flush()


def sample(b, key, n=8, gap=0.12):
    out = []
    for _ in range(n):
        r = b.pyval("ws._snap('%s')" % key)
        out.append(r)
        end = time.time() + gap
        while time.time() < end:
            b._pump()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    args = ap.parse_args()
    b = P4Board(args.port)
    rc = 0
    try:
        b.reset()
        b.pyexec(PROBE)
        b.open("settings")
        b.drain(2.0)
        st = b.state()
        w = st["wins"]["settings"]
        cx = w[0] + 1 + w[2] // 2
        ctop = w[1] + 1 + w[4]
        cy0 = ctop + (w[3] - w[4]) - 40        # drag from near the bottom...
        cy1 = ctop + 40                        # ...to near the top, slowly

        for phase in ("settled", "after theme change"):
            if phase != "settled":
                b.pyexec("ws.set_theme('berry' if ws.theme_name != 'berry' "
                         "else 'forest')")
                b.drain(1.5)
            b.pyexec("ws._strips.clear()\nws._verdict.clear()")
            hold(b, cx, cy0, cx, cy1, 200)
            b.drain(1.2)                     # let the freeze engage
            got = sample(b, "settings", n=10)
            froze = b.pyval("ws.wm._wins['settings']._chrome_streak")
            quiet = b.pyval("ws.wm._chrome_quiet")
            cross = b.pyval("ws._cross()")
            b.wait_line("swipe done", 30)

            repeats = [s for (_k, s) in got if s is not None]
            stable = all(repeats) if repeats else False
            nbuf = len(set(k for (k, _s) in got))
            ok = stable and cross and cross[1] is True and (froze or 0) >= 2
            print("%-20s streak=%-3s quiet=%-5s buffers=%d  "
                  "per-buffer-stable=%s cross-buffer-equal=%s  %s"
                  % (phase, froze, quiet, nbuf, stable,
                     cross[1] if cross else "?", "PASS" if ok else "FAIL"))
            if not ok:
                rc = 1
                print("      samples: %s" % (got,))
    finally:
        b.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
