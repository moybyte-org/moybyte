"""The kernel's Player drives a Lua cart's frame (native/moy_play/moy_play.c,
docs/kernel_cartpath_2026-10.md §3.2), on the desktop MicroPython.

The run is launched from its cart folder's catalogue entry, bound to an input
table, an audio session and a Tick, and every frame is one call: the press
edges, the snapshot the cart reads (filled in C from the table), the cart's
_update and _draw, and its audio played straight into the session. What each
check watches for:

  * the verdict comes from the folder, not from the caller;
  * a button held and pressed in the TABLE reaches the cart, with no
    snapshot written from Python;
  * sfx/music/beep reach the SESSION in the cart's order, with the cart's
    arguments (the kernel's audio trace), with no queue drained in Python;
  * quit() ends the frame where it stands and says so; a view() declared
    mid-run says so once;
  * the cart's error comes back as its own text, and the books close with
    no upcall of any class made from launch to end.
"""

import json
import os
import subprocess

from unix_mp import require_unix_mp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CART = r"""
n = 0
function _init() n = 0 end
function _update()
  n = n + 1
  if btnp("left") then sfx(3) end
  if btn("a") then sfx(5, 2) end
  if n == 3 then music(2) beep(440, 0.25) end
  if n == 4 then view(160, 120) end
  if n == 6 then quit() end
  if n == 9 then error("boom") end
end
function _draw() cls(n % 16) end
"""

DRIVER = r'''
import moycore, moy_play, moy_input, moy_audio
from array import array

W, H = 64, 48
fb = bytearray(W * H * 2)
snap = array("i", bytearray(4 * moycore.SNAP_LEN))
aq = array("i", bytearray(4 * (1 + moycore.AQ_SLOTS * moycore.AQ_MAX)))
pm = array("i", bytearray(4 * 256))
moycore.run_begin(fb, W, H, None, None, None, 0, 0, snap, aq, pm, None, None, True)
print("LOAD", moycore.load([(open("@CART@/main.lua").read(), "@cart")]))

inp = moy_input.HostInputTable()
au = moy_audio.open(1)
moy_audio.bank(au, '{"sfx": [], "music": []}')
tick = moy_play.Tick()
tick.start(30)
run = moy_play.launch("@CART@", True)
moy_play.bind(run, inp, au, tick)
moy_play.open(run)
print("INFO0", moy_play.info(run))

moy_audio.trace(True)
bits = []
def frame(dt=1/30):
    try:
        bits.append(moy_play.frame(run, 1, dt, True, 5, 6, 1))
    except RuntimeError as exc:
        bits.append("ERR " + str(exc))

# 1: nothing held. 2: LEFT pressed. 3: A held (music+beep fire too).
frame()
inp.set_button("left", True); inp.begin_frame(); frame()
inp.set_button("left", False); inp.set_button("a", True); inp.begin_frame(); frame()
inp.set_button("a", False); inp.begin_frame()
frame()                       # 4: view
frame()                       # 5
frame()                       # 6: quit
print("TRACE", [r[1:] for r in moy_audio.trace() if r[1] in (4, 5, 6)])
frame(); frame(); frame()     # 7, 8, 9: the error
print("BITS", bits)
print("TOUCH", snap[moycore.SNAP_TOUCH_X], snap[moycore.SNAP_TOUCH_Y], snap[moycore.SNAP_TOUCH_DOWN])
moy_play.end(run)
print("INFO1", moy_play.info(run))
try:
    moy_play.frame(run, 1, 1/30, True, 0, 0, 0)
    print("STALE no")
except RuntimeError as exc:
    print("STALE", exc)
moycore.close()
'''


def _run(tmp_path):
    exe = require_unix_mp(
        "moycore", "moy_play",
        why="Without it nothing runs the kernel's Player over a real Lua "
            "cart: the snapshot from the input table, the audio into the "
            "session and the run's books.")
    cart = tmp_path / "frame.moy"
    cart.mkdir()
    (cart / "manifest.json").write_text(json.dumps({
        "format": "moy-1", "title": "Frame", "id": "test.frame",
        "runtime": "lua", "main": "main.lua",
        "moybyte": {"type": "game", "permissions": ["graphics", "input", "audio"]},
    }))
    (cart / "main.lua").write_text(CART)
    src = DRIVER.replace("@CART@", str(cart))
    p = subprocess.run([exe, "-c", src], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    return {l.split(" ", 1)[0]: l.split(" ", 1)[1] for l in p.stdout.splitlines()
            if " " in l}


def test_the_player_drives_a_lua_frame_from_the_input_table(tmp_path):
    by = _run(tmp_path)
    assert by["LOAD"] == "None", by
    info0 = eval(by["INFO0"])
    assert info0[:3] == ("lua", True, "free"), info0
    bits = eval(by["BITS"])
    # 4 declares a view, 6 quits; 7 and 8 run on (the Player, not the
    # frame, ends a run that quit), 9 raises with the cart's own text.
    assert bits[:8] == [0, 0, 0, 2, 0, 1, 0, 0], bits
    assert bits[8].startswith("ERR ") and "boom" in bits[8], bits
    # sfx(3) on the LEFT press edge, sfx(5, 2) while A is held, then
    # music(2) and the beep in the cart's order: the session's own trace.
    trace = eval(by["TRACE"])
    verbs = [t[0] for t in trace]
    assert verbs == [4, 4, 6, 5], trace
    assert list(trace[0][1:]) == [3, -1] and list(trace[1][1:]) == [5, 2], trace
    assert list(trace[2][1:]) == [2, 1], trace
    assert by["TOUCH"] == "5 6 1", by["TOUCH"]


def test_a_run_closes_its_books_and_refuses_its_handle_after(tmp_path):
    by = _run(tmp_path)
    info1 = eval(by["INFO1"])
    runtime, vm_free, why, frames, ticks, upcalls, ended, error, so, sf = info1
    assert (runtime, vm_free, why) == ("lua", True, "free")
    assert ended and "boom" in error, info1
    assert frames == 8 and ticks == 8, info1
    assert upcalls == (0, 0, 0, 0, 0), "a frame crossed into Python: %r" % (upcalls,)
    assert "stale" in by["STALE"], by["STALE"]
    assert so is None and sf is None, "no stack meter off a board"  # absence, never 0
