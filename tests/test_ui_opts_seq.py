"""A verb's options handed to the module as the list or tuple they are
(native/moy_ui/modmoy_ui.c's `opts_of`, runtime/ui.py's `_opts`) draw what
the joined bytes draw, answer what they answer, and, where they fit its
buffer, build nothing: under the boards' MicroPython, the real module, each
page drawn both ways."""

import subprocess

from unix_mp import require_unix_mp

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DRIVER = r'''
import sys, gc
sys.path.insert(0, @ROOT@ + "/runtime")
sys.path.insert(0, @ROOT@ + "/device")
import moy_gfx
from array import array


class Comp:
    buf_h = 200

    def __init__(self):
        self.fb = bytearray(240 * 200 * 2)

    def size(self):
        return (240, 200)

    def framebuffer(self):
        return self.fb

    def gfx(self):
        return moy_gfx


import device_canvas
import ui_draw
import ui

cv = device_canvas.SystemCanvas(Comp())
ctx = ui_draw.ui_context()
inp = array("i", [0] * ui_draw.UI_IN_LEN)

LONG = tuple("option number %d of a long list" % i for i in range(40))
WORDS = ["Off", "Low", "High"]
NUMS = (1, 22, 333)
HINTS = ("A", "Open", "B", "Back")
MENU = ("Copy\tc", "Paste\tv", "Delete")
ICONS = ("", "", "close")


TABS = ["Sound", "Song"]
SHORT = [False]
OUT = [0] * 5


def page():
    out = OUT
    ui.page("Options")
    ui.hints(HINTS)
    ui.group("Rows")
    out[0] = ui.cycle("Level", WORDS, 1)
    out[1] = ui.seg("Size", NUMS, 2)
    out[2] = ui.list("Many", WORDS if SHORT[0] else LONG, 3)
    ui.group()
    ui.at(4, 120, 200, 20)
    out[3] = ui.tabs("View", TABS, 0)
    ui.at(4, 140, 200, 60)
    out[4] = ui.menu("", MENU, ICONS)
    ui.end()
    return out


def frame():
    ui_draw.ui_frame(ctx, cv, None, None, inp)
    return page()


def run(seq):
    ui._SEQ = seq
    cv._buf[:] = bytes(len(cv._buf))
    for _ in range(3):
        got = list(frame())
    return got, bytes(cv._buf)


print("SEQ", ui._SEQ)
a, pa = run(True)
b, pb = run(False)
print("ANSWERS", a == b, a, b)
print("PIXELS", pa == pb, sum(1 for i in range(0, len(pa), 2) if pa[i:i + 2] != pb[i:i + 2]))
ui._SEQ = True
SHORT[0] = True
frame()
frame()
gc.collect()
gc.disable()
def frames(n):
    while n:
        frame()
        n -= 1


frames(1)
m = gc.mem_alloc()
frames(20)
print("ALLOC", gc.mem_alloc() - m)
gc.enable()
print("DONE")
'''


def test_options_as_a_sequence_draw_and_answer_as_their_bytes():
    exe = require_unix_mp("moy_ui", board_model=True,
                          why="the module's options read from a list or tuple")
    prog = DRIVER.replace("@ROOT@", repr(str(ROOT)))
    out = subprocess.run([exe, "-c", prog], capture_output=True, text=True, timeout=120,
                         cwd=str(ROOT))
    assert out.returncode == 0, out.stderr[-2000:]
    lines = dict(ln.split(" ", 1) for ln in out.stdout.splitlines() if " " in ln)
    assert "DONE" in out.stdout, out.stdout[-2000:]
    assert lines["SEQ"] == "True", "the board model's module takes no sequence"
    assert lines["ANSWERS"].startswith("True"), lines["ANSWERS"]
    assert lines["PIXELS"].startswith("True"), lines["PIXELS"]
    # options that fit the module's buffer cost the frame nothing
    assert lines["ALLOC"] == "0", lines["ALLOC"]
