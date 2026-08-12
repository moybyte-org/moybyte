"""moycore stage 2: a Lua cart's whole frame runs in C.

The claim under test is narrow and load-bearing: `moycore.run_begin()` builds a
libmoy console over buffers the console already owns, and `moycore.tick(dt)`
runs the cart's `_update` and `_draw` end to end without re-entering Python.
Everything else in the stage rests on that.

What each assertion is really watching for, since a module that silently does
nothing would pass a naive smoke test:

  * pixels CHANGE with cart state -- a canvas wired to the wrong buffer, or a
    `_draw` that never ran, both produce a constant frame. (The first cart
    written against this module produced four identical hashes and looked
    broken; it was drawing map() over its own print(). The lesson is in the
    per-frame pixel-count assertions below rather than in a hash.)
  * the INPUT snapshot reaches the cart: a btnp edge written into the array
    before the tick has to come back out as the sfx the cart plays on it.
  * the AUDIO queue carries that sfx with its arguments, in order.
  * pmem is C-side with a dirty flag, which is the shape the device already
    defers it to (#66).
  * an error in cart code comes back as TEXT rather than as an exception or a
    dead VM -- the Player maps it to crash-to-code.

Needs the unix dual-usermod build with moycore in it; skipped otherwise, like
every other pin in this suite:

    cd firmware/lilygo_t_deck_plus_micropython/.build/usermods_luadraw
    ln -sfn ../../native/moycore moycore     # beside moy_gfx and moy_lua
    cd ../lvgl_micropython/lib/micropython/ports/unix
    make VARIANT=standard BUILD=build-moycore USER_C_MODULES=<abs usermods_luadraw>
"""

import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MP = os.path.join(ROOT, "firmware", "lilygo_t_deck_plus_micropython", ".build",
                  "lvgl_micropython", "lib", "micropython", "ports", "unix",
                  "build-moycore", "micropython")

DRIVER = r'''
import moycore
from array import array

W, H = 96, 64
fb = bytearray(W * H * 2)
snap = array("i", bytearray(4 * moycore.SNAP_LEN))
aq = array("h", bytearray(2 * (1 + moycore.AQ_SLOTS * moycore.AQ_MAX)))
pm = array("i", bytearray(4 * 256))
sheet = bytearray(128 * 256)
for i in range(len(sheet)):
    sheet[i] = (i * 7) & 15

SRC = """
local n = 0
function _init() pmem(0, 41) end
function _update(dt)
    n = n + 1
    pmem(0, pmem(0) + 1)
    if btnp("left") then sfx(3) end
    if btn("a") then sfx(5, 2) end
end
function _draw()
    cls(0)
    rect(0, 0, n * 4, 6, 8)      -- grows every frame: the liveness signal
    print("x" .. n, 2, 30, 7)
end
"""

print("START", moycore.run_begin(fb, W, H, None, sheet, None, 0, 0,
                                 snap, aq, pm, {"k": "v"}, SRC, "@cart"))
for f in range(4):
    snap[moycore.SNAP_TIME_MS] = f * 32
    snap[moycore.SNAP_BTNP] = (1 << 0) if f == 1 else 0     # MOY_BTN_LEFT
    snap[moycore.SNAP_BTN] = (1 << 4) if f == 2 else 0      # MOY_BTN_A
    aq[0] = 0
    err = moycore.tick(0.03125)
    nz = 0
    for b in fb:
        if b:
            nz += 1
    print("F", f, err, nz, aq[0], list(aq[1:1 + moycore.AQ_SLOTS]))
print("PMEM", moycore.pmem_image(pm), pm[0])
moycore.close()
print("CLOSED", moycore.active())

# A cart that raises must come back as text, with the VM still recoverable.
BAD = "function _update(dt) error('boom') end\nfunction _draw() end\n"
print("START2", moycore.run_begin(fb, W, H, None, None, None, 0, 0,
                                  snap, aq, None, None, BAD, "@bad"))
print("ERR", moycore.tick(0.03125))
moycore.close()
'''


def _run():
    p = subprocess.run([MP, "-c", DRIVER], capture_output=True, text=True,
                       timeout=180)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout


@pytest.mark.skipif(not os.path.isfile(MP),
                    reason="unix moycore build absent (see this module's docstring)")
def test_a_lua_cart_frame_runs_entirely_in_c():
    out = _run()
    lines = [l.split() for l in out.splitlines() if l]
    by = {l[0]: l for l in lines}

    assert by["START"][1] == "None", "the cart failed to load or _init raised: %s" % out

    frames = [l for l in lines if l[0] == "F"]
    assert len(frames) == 4, out
    counts = []
    for l in frames:
        assert l[2] == "None", "tick returned an error: %s" % out
        counts.append(int(l[3]))
    # The rect grows with the cart's own counter, so a frame that did not run
    # _update, or a canvas pointed somewhere else, shows up as a flat sequence.
    assert counts == sorted(counts) and counts[0] < counts[-1], \
        "the canvas did not change with cart state: %r" % counts

    # Input snapshot -> cart -> audio queue, with arguments and order intact.
    # frame 1 pressed LEFT (sfx 3, default channel), frame 2 held A (sfx 5 on 2).
    assert frames[0][4] == "0", "silent frame produced audio: %s" % out
    assert frames[1][4] == "1" and frames[1][5:] == ["[0,", "3,", "-1,", "0]"], \
        "btnp edge did not reach the cart as sfx(3): %s" % out
    assert frames[2][4] == "1" and frames[2][5:] == ["[0,", "5,", "2,", "0]"], \
        "btn held did not reach the cart as sfx(5, 2): %s" % out

    # pmem: 41 from _init plus one per frame, and the dirty flag armed.
    assert by["PMEM"][1] == "True" and by["PMEM"][2] == "45", out
    assert by["CLOSED"][1] == "False", out

    # A cart error is text, not an exception, and the module recovers.
    assert by["START2"][1] == "None", out
    assert by["ERR"][1] != "None" and "boom" in out, \
        "a raising _update must return its message: %s" % out
