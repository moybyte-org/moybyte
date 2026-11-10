"""What a verb hands the board's module as it is -- its options as the list
or tuple they are (native/moy_ui/modmoy_ui.c's `opts_of`, runtime/toolkit/ui.py's
`_opts`), a label's text options in its override dict (`label_text`) --
draws what the face's own encoding draws, answers what it answers, and,
where the options fit the module's buffer, builds nothing: under the boards'
MicroPython, the real module, each page drawn both ways."""

import subprocess

from unix_mp import require_unix_mp, runtime_tree

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DRIVER = r'''
import sys, gc
sys.path.insert(0, @RUNTIME@)
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
TEXTS = ({"wrap": True, "lines": 2}, {"wrap": True}, {"align": "center"},
         {"tone": "dim"}, {"face": "mono", "tone": None}, {"tone": "bogus"},
         {"wrap": 0, "face": "title"}, {"inset": True}, {"align": "center", "inset": True},
         {"id": "x"}, {})
# labels' override tables (the module reads each in one walk of its dict):
# colours and flags, a None value, a key the engine does not read
OVERS = ({"fill": 0x204060, "ink": 0x123456, "current": True},
         {"border": 0x00ff00, "disabled": True, "steps": True, "ink": 0xff8000},
         {"id": "row", "nav": False, "mark": True, "zz": 1, "ink": 0x00ffff},
         {"wash": False, "lead": True, "hint": "h", "ink": 0xffffff},
         {"ink": 0xff00ff, "fill": None, "bar": True},
         {"gen": 3, "current": False, "border": 0xff00ff, "ink": 0x0000ff})
BUBBLES = (("in", {"head": "Ana Ridge", "signal": 2, "lines": 1, "id": "b0"}),
           ("out", {"tone": "failed", "status": "Not sent", "max": 0.5, "anchor": True}),
           ("out", None))
SIGNAL = {"signal": 3}
REGION = {"values": ("Europe", "United States", "China"), "check": True}
POP = {"wash": "view"}
HOME = {"values": ("2 min", "", "1 h"), "subs": ("Gate opens at seven", "Snow", ""),
        "icons": ("bubble", "@Ana Ridge", ""), "counts": (0, 3, "12"),
        "tags": ("Public", "", ""), "marks": ("", "lock", "lock")}
CARD = {"card": True}
TAG = {"tag": "Public"}
DFOCUS = {"focus": "Cancel"}
NOTE = ("Every radio on a mesh uses one preset.", "On another one you hear none.")
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
    for i in range(len(TEXTS)):
        ui.at(120, 4 + 12 * i, 110, 24)
        ui.label("a label long enough to wrap twice over", TEXTS[i])
    for i in range(len(OVERS)):
        ui.at(4, 4 + 18 * i, 100, 16)
        ui.label(WORDS[i % 3], OVERS[i])
    for i in range(len(BUBBLES)):
        ui.at(4, 40 + 30 * i, 230, 30)
        ui.bubble("a message long enough to wrap and be cut", BUBBLES[i][0], BUBBLES[i][1])
    ui.at(4, 130, 230, 20)
    ui.info("Signal", "-104 dBm", SIGNAL)
    ui.at(4, 150, 230, 48)
    ui.list("Region", WORDS, 1, REGION)
    ui.at(40, 40, 120, 30)
    ui.popover("pv", 200, POP)
    ui.info("Status", "Not sent")
    ui.popover()
    ui.group("", CARD)
    ui.list("Two", WORDS, -1, HOME)
    ui.group()
    ui.link("LongFast", "", TAG)
    ui.note("warn", NOTE)
    ui.dialog("Switch?", ("Cancel", "Switch"), "primary", DFOCUS)
    ui.toggle("Keep", True)
    ui.dialog()
    ui.end()
    return out


def frame():
    ui_draw.ui_frame(ctx, cv, None, None, inp)
    return page()


def run(seq):
    ui._SEQ = seq
    ui._OVER_DICT = seq
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
ui._OVER_DICT = True
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


def test_what_the_module_reads_itself_draws_and_answers_as_the_face_encodes_it():
    exe = require_unix_mp("moy_ui", board_model=True,
                          why="the module's options read from a list or tuple")
    prog = DRIVER.replace("@ROOT@", repr(str(ROOT))).replace(
        "@RUNTIME@", repr(runtime_tree()))
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


MORE = 1 << 29                          # moy_ui_api.h's MOY_UI_O_MORE


def _read(lib, block):
    """The override block's keys as the engine reads it: each field, a span
    as its text."""
    import ctypes
    from runtime.moy_ui_binding import _Over, _Span
    raw = ctypes.create_string_buffer(block, len(block))
    ov = _Over()
    assert lib.moy_ui_over_block(ctypes.byref(ov), raw, len(block)) == 0
    out = {}
    for name, _t in _Over._fields_:
        v = getattr(ov, name)
        out[name] = ctypes.string_at(v.p, v.n) if isinstance(v, _Span) and v.p else (
            None if isinstance(v, _Span) else v)
    return out


def test_the_second_flags_word_is_the_next_keys_door():
    """Bit 29 of the block's flags word says a second word of flags follows
    the focus's text, its keys' words after it: a block that carries one
    reads it, and every key before it as the block without it does; a block
    whose bit says so and ends before the word, or whose word is below 0,
    reads none."""
    from runtime import glass_binding, ui     # ui binds the module's calls
    lib = glass_binding._lib()
    o = {"id": "row", "hint": "Message", "gen": 7, "snap": True, "values": ("a", "b"),
         "check": True, "subs": ("one", "two"), "tag": "Public", "focus": "Cancel"}
    block = ui._block(o)
    at = 4 * 9 + len(b"row") + 4 + len(b"Message")      # the flags word
    fl = int.from_bytes(block[at:at + 4], "little")
    assert not fl & MORE
    head = block[:at] + (fl | MORE).to_bytes(4, "little") + block[at + 4:]
    plain = _read(lib, block)
    assert plain["flags2"] == 0
    got = _read(lib, head + (0x40000001).to_bytes(4, "little") + b"\x05\x00\x00\x00later")
    assert got.pop("flags2") == 0x40000001
    plain.pop("flags2")
    assert got == plain
    assert _read(lib, head)["flags2"] == 0
    assert _read(lib, head + (-1).to_bytes(4, "little", signed=True))["flags2"] == 0
