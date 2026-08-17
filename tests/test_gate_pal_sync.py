"""The incremental gate-pal table stays equal to the full rebuild -- always.

pal()/reset_state() maintain the draw gates' 64-entry RGB565 palette table
incrementally (one poke per remap, one slice-copy per reset) instead of
re-deriving all 64 entries per call -- that rebuild was ~80% of a pal() body,
and the celeste shim calls pal() every frame (attribution:
experiments/state_verb_cost/bench.py). The invariant that makes the fast path
safe: after ANY state mutation, _gate_pal[i] == PAL565_WIRE[_pal_map[i]] for
all i -- exactly what the cold rebuild (_sync_gate_pal) computes. This test
drives a seeded random walk over every mutating verb and re-derives the table
after each step; a single divergent entry is a wrong colour on glass that
pixel tests would only catch if they happened to draw through that index.

Runs on the desktop MicroPython `make unix-micropython` builds (real moy_gfx
gates); its absence is loud rather than a silent skip -- tests/unix_mp.py.
"""

import shutil
import subprocess
from pathlib import Path

from unix_mp import require_unix_mp

ROOT = Path(__file__).resolve().parent.parent
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"

DRIVER = r'''
import sys
# STAGE must outrank MODULES: modules/ is gitignored and never cleaned, so it
# still holds whatever a previous build staged there -- and this suite would
# then test that stale copy instead of the runtime/ source it just staged.
# (test_semantic_traces already orders them this way; this one did not, and it
# is what made a fresh Image in runtime/moy_image.py invisible here.)
sys.path.insert(0, @MODULES@)
sys.path.insert(0, @STAGE@)

import moy_gfx
import device_canvas


class FakeComp:
    def __init__(self, w, h):
        self._w = w
        self._h = h
        self._buf = bytearray(w * h * 2)

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._buf

    def gfx(self):
        return moy_gfx


canvas = device_canvas.DeviceCanvas(FakeComp(320, 240))
assert canvas._gate_pal is not None      # gates must be live or this is vacuous
WIRE = device_canvas.PAL565_WIRE

state = 0x2F6E2B4F


def rnd(n):
    global state
    state = (state * 1103515245 + 12345) & 0x7FFFFFFF
    return state % n


def check(step, op):
    pm = canvas._pal_map
    gp = canvas._gate_pal
    for i in range(64):
        want = WIRE[pm[i]]
        if gp[i] != want:
            print("DIVERGED step=%d op=%s idx=%d gate=%d want=%d"
                  % (step, op, i, gp[i], want))
            raise SystemExit(1)


canvas.reset_state()
check(0, "reset_state")
ops = 0
for step in range(1, 4001):
    k = rnd(10)
    if k < 3:
        a, b = rnd(64), rnd(64)
        canvas.pal(a, b)
        op = "pal(%d,%d)" % (a, b)
    elif k == 3:
        a = rnd(64)
        canvas.pal(a, a)                 # un-remap / identity value
        op = "pal(%d,%d)" % (a, a)
    elif k == 4:
        canvas.pal()
        op = "pal()"
    elif k == 5:
        canvas.palt(rnd(64), rnd(2))
        op = "palt(c,on)"
    elif k == 6:
        canvas.palt()
        op = "palt()"
    elif k == 7:
        canvas.reset_state()
        op = "reset_state"
    elif k == 8:
        canvas.set_viewport(rnd(64), rnd(64), 128 + rnd(128), 96 + rnd(96))
        op = "set_viewport"
    else:
        canvas.clear_viewport()
        op = "clear_viewport"
    check(step, op)
    ops += 1
print("OK ops=%d" % ops)
'''


def test_gate_pal_table_matches_full_rebuild(tmp_path):
    exe = require_unix_mp(
        "moy_gfx",
        why="Without it nothing walks the incremental gate-pal table against "
            "the cold rebuild, and a divergent entry is a wrong colour on "
            "glass that pixel tests only catch by luck.")
    stage = tmp_path / "stage"
    stage.mkdir()
    shutil.copy(ROOT / "runtime" / "font.py", stage / "moy_font.py")
    # device_canvas imports Image from moy_image (ONE definition, shared with the
    # host canvas). The real build stages both of these out of runtime/ into
    # modules/; this stage dir is where that is emulated, so they belong here
    # rather than being reached for through a `runtime` package the device does
    # not have.
    shutil.copy(ROOT / "runtime" / "moy_image.py", stage / "moy_image.py")
    shutil.copy(ROOT / "runtime" / "moy_fs.py", stage / "moy_fs.py")
    src = (DRIVER
           .replace("@STAGE@", repr(str(stage)))
           .replace("@MODULES@", repr(str(TDECK / "modules"))))
    script = tmp_path / "driver.py"
    script.write_text(src)
    out = subprocess.run([exe, str(script)],
                         capture_output=True, text=True, timeout=120)
    assert "OK ops=4000" in out.stdout, out.stdout + out.stderr
