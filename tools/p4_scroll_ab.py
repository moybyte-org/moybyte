#!/usr/bin/env python3
"""A/B the #113 scroll-as-blit against a plain full repaint, on glass.

#113 shifts a scrolled surface's retained pixels with scroll_rect and repaints
only the exposed band, on the premise that repainting is expensive and shifting
is cheap. On the P4 that premise is worth re-testing:

  * a scroll_rect READS and WRITES the whole region  (2 passes of traffic)
  * a repaint only WRITES it                         (1 pass)
  * and the 2026-07-26 draw gates made a chrome rect ~10x cheaper, so the
    "repainting is expensive" half of the premise moved.

The measured wall is ~91 MB/s for a full-screen copy either way (CPU or PPA), so
passes over memory -- not draw calls -- decide the frame. Flipping a canvas's
RETAINED_FRAMES to 0 disables the blit path without touching any other code,
which makes this a clean A/B.

Usage:  python tools/p4_scroll_ab.py [--port /dev/ttyACM0]
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
ws._fr = []
ws._rec = False
ws._shifts = 0
if not hasattr(ws, '_abhook'):
    _of = ws.frame
    def _f(dt, _of=_of):
        a = _tk()
        n0 = ws._frames_drawn
        r = _of(dt)
        if ws._rec and ws._frames_drawn != n0:
            ws._fr.append(_td(_tk(), a))
        return r
    ws.frame = _f
    ws._abhook = True
def _count_shifts(cv):
    # Count scroll_rect calls so a run PROVES which path it exercised.
    if getattr(cv, '_abwrapped', False):
        return
    _sr = cv.scroll_rect
    def g(*a, **k):
        ws._shifts += 1
        return _sr(*a, **k)
    cv.scroll_rect = g
    cv._abwrapped = True
ws._count_shifts = _count_shifts
"""


def stats(vals):
    s = sorted(vals)
    n = len(s)
    return (n, s[n // 2], s[min(n - 1, int(n * 0.9))], s[-1])


def _verdict(blits, reps):
    blits = [v for v in blits if v]
    reps = [v for v in reps if v]
    if not blits or not reps:
        return
    a = sorted(blits)[len(blits) // 2]
    c = sorted(reps)[len(reps) // 2]
    print("  -> blit %.1fms vs repaint %.1fms   repaint is %.2fx"
          % (a / 1000.0, c / 1000.0, c / float(a)))


def run(b, name, setup, gesture, retained):
    # Reset the shelf to a FIXED start offset and let the covers for that view
    # settle before recording. Without this the A/B is worthless: each drag
    # leaves the list further along, so a later run scrolls a shorter distance
    # (one recorded ZERO shifts -- it was already at the end), and newly
    # revealed cards pay a cover decode that lands entirely in one condition.
    if setup:
        b.pyexec(setup)
    b.drain(1.5)
    b.pyexec("ws._count_shifts(%s)\n%s.RETAINED_FRAMES = %d"
             % (retained[0], retained[0], retained[1]))
    b.cmd("py ws._fr.clear() or setattr(ws, '_shifts', 0) or 1", wait_for="PY")
    b.cmd("py setattr(ws, '_rec', True) or 1", wait_for="PY")
    gesture()
    b.cmd("py setattr(ws, '_rec', False) or 1", wait_for="PY")
    fr = b.pyval("ws._fr") or []
    shifts = b.pyval("ws._shifts")
    if not fr:
        print("  %-34s (no painted frames)" % name)
        return None
    n, med, p90, mx = stats(fr)
    print("  %-34s n=%-4d median=%-6.1f p90=%-6.1f max=%-6.1f  shifts=%s"
          % (name, n, med / 1000.0, p90 / 1000.0, mx / 1000.0, shifts))
    return med


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    args = ap.parse_args()
    b = P4Board(args.port)
    try:
        b.reset()
        b.pyexec(PROBE)

        # -- windowed picker ------------------------------------------------
        b.open("picker")
        b.drain(10.0)
        g = b.pyval("ws.wm._wins['make'].ctx.layout.lib_grid")
        st = b.state()
        w = st["wins"]["make"]
        ox, oy = w[0] + 1, w[1] + 1 + w[4]
        gx, gy, gw, gh = g
        cy = oy + gy + gh // 2

        def drag():
            b.swipe(ox + gx + gw - 40, cy, ox + gx + 60, cy, frames=30)
            b.drain(2.0)

        RESET = "ws.launcher.set_scroll(0)\nws.mark_dirty()"

        # Warm EVERY cover once (both directions) so no decode lands in a run.
        for _ in range(6):
            drag()
        b.pyexec(RESET)
        b.drain(4.0)

        print("\n=== picker drag-scroll (windowed)  font_scale=%s ==="
              % b.pyval("ws.look.font_scale"))
        buf = "ws.wm._wins['make'].buf"
        # Interleaved A/B/A/B: a single ordered pass drifts (cover cache warms,
        # heap fragments), and the first run of this A/B disagreed with itself
        # by more than the effect being measured.
        blits, reps = [], []
        for i in range(2):
            blits.append(run(b, "A%d scroll-as-blit (RETAINED=1)" % (i + 1),
                             RESET, drag, (buf, 1)))
            reps.append(run(b, "B%d full repaint  (RETAINED=0)" % (i + 1),
                            RESET, drag, (buf, 0)))
        _verdict(blits, reps)

        # -- fullscreen library ---------------------------------------------
        b.pyexec("ws.go_home()")
        b.drain(1.5)
        b.pyexec("ws.open_library()")
        b.drain(10.0)
        band = b.pyval("ws.launcher.band_rect()")
        bx, by, bw, bh = band
        lcy = by + bh // 2

        def ldrag():
            b.swipe(bx + bw - 40, lcy, bx + 60, lcy, frames=30)
            b.drain(2.0)

        for _ in range(6):
            ldrag()
        b.pyexec(RESET)
        b.drain(4.0)

        print("\n=== library drag-scroll (fullscreen)  font_scale=%s ==="
              % b.pyval("ws.look.font_scale"))
        blits, reps = [], []
        for i in range(2):
            blits.append(run(b, "A%d scroll-as-blit (RETAINED=2)" % (i + 1),
                             RESET, ldrag, ("ws.sys_canvas", 2)))
            reps.append(run(b, "B%d full repaint  (RETAINED=0)" % (i + 1),
                            RESET, ldrag, ("ws.sys_canvas", 0)))
        _verdict(blits, reps)
    finally:
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
