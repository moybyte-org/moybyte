"""The scroll physics in C against the twin, in the boards' float model
(docs/kernel_toolkit_2026-10.md §3.4).

A fling decays by FRICTION ** dt in single precision, which is what a board's
MicroPython computes; CPython computes doubles. So the differential runs on the
desktop MicroPython built in the boards' object model (tests/unix_mp.py): the
twin's ScrollRegion and DragTap (tests/ui_twin.py, extracted as source) and
native/moy_ui's rules, driven by the same pointer script, compared as integer
offsets per tick and taps per sample.
"""

import ast
import os
import subprocess

from unix_mp import require_unix_mp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _twin_source():
    src = open(os.path.join(ROOT, "tests", "ui_twin.py")).read()
    tree = ast.parse(src)
    keep = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in ("ScrollRegion", "DragTap"):
            keep.append(ast.get_source_segment(src, node))
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", "") in ("_MAX_RETAINED",)):
            keep.append(ast.get_source_segment(src, node))
    return "\n\n".join(keep)


DRIVER = r'''
import moy_ui as C
from array import array

def rect_in(px, py, r):
    x, y, w, h = r
    return x <= px < x + w and y <= py < y + h

def _fs(cv):
    return 1

_METRICS = {"scrollbar": (4, 8)}
SB_MIN = 1

class CRegion:
    def __init__(self, view, content):
        self.view = view
        self.content = content
        self.offset = 0
        self.horizontal = False
        self._st = bytearray(16)

class CTap:
    def __init__(self, r):
        self.r = r
        self._t = bytearray(12)
        self._out = array("i", [0, 0, 0])

    def frame(self, px, py, click, down, slop=6, dt_ms=None):
        r = self.r
        tapped = C.dragtap(self._t, r._st, r.view, r.horizontal, px, py, click,
                           down, slop, dt_ms, r.offset, r.content, self._out)
        r.offset = self._out[0]
        return (self._out[1], self._out[2]) if tapped else None

def tick_c(r, dt):
    off = C.scroll_tick(r._st, dt, r.offset, r.content, r.view[3])
    if off >= 0:
        r.offset = off

def script(seed):
    # (px, py, click, down, dt) samples: a press, a drag of varying speed,
    # a release, then ticks; repeated with different seeds.
    out = []
    x, y = 30, 180
    out.append((x, y, True, True, 16))
    step = 3 + seed % 11
    for i in range(4 + seed % 6):
        y -= step + (i * seed) % 5
        out.append((x, y, False, True, 12 + (seed + i) % 9))
    out.append((x, y, False, False, 16))
    return out

flings = 0
for seed in range(40):
    content = 600 + seed * 37
    a = ScrollRegion()
    a.set((0, 0, 60, 200), content)
    ta = DragTap(a)
    b = CRegion((0, 0, 60, 200), content)
    tb = CTap(b)
    for (px, py, click, down, dt) in script(seed):
        ra = ta.frame(px, py, click, down, 6, dt)
        rb = tb.frame(px, py, click, down, 6, dt)
        if ra != rb or a.offset != b.offset:
            print("DIFF", seed, "sample", (px, py, click, down), a.offset, b.offset, ra, rb)
    flings += 1 if a.animating else 0
    n = 0
    while n < 400:
        dt = 14 + (n * 7 + seed) % 9
        a.tick(dt)
        tick_c(b, dt)
        if a.offset != b.offset:
            print("DIFF", seed, "tick", n, a.offset, b.offset)
            break
        n += 1
print("FLINGS", flings)
print("DONE")
'''


def test_the_physics_is_the_twins_in_single_precision():
    exe = require_unix_mp("moy_ui", board_model=True,
                          why="the scroll physics in the boards' float model")
    # the driver's helpers, then the twin's classes, then the run
    prog = DRIVER.split("for seed in range")[0] + "\n" + _twin_source() + \
        "\nfor seed in range" + DRIVER.split("for seed in range")[1]
    out = subprocess.run([exe, "-c", prog], capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "DONE" in out.stdout, out.stdout[-2000:]
    diffs = [ln for ln in out.stdout.splitlines() if ln.startswith("DIFF")]
    assert not diffs, "\n".join(diffs[:10])
    flings = int([ln for ln in out.stdout.splitlines() if ln.startswith("FLINGS")][0]
                 .split()[1])
    assert flings >= 10, "the script flung %d times: the coast went untested" % flings
