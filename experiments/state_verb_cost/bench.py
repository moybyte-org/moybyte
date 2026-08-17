"""State-verb cost attribution: crossing vs body, on the unix dual-usermod build.

M0(a) priced a Lua cart's state-verb trampolines at ~0.55ms/call on the S3 by
difference (issue #66's attribution table), a lumped number that cannot say
whether the milliseconds live in the Lua->MP CROSSING or in the verb BODY.
This bench splits them: it times (1) an empty registered verb called from Lua
(pure crossing), (2) each state verb's bound method called from a tight Python
loop (pure body), and (3) the same verbs called from Lua (crossing + body).
Everything runs on the REAL DeviceCanvas with the native draw gates live --
the same construction tests/test_semantic_traces.py proves faithful.

x86 microseconds are not S3 microseconds; the RATIOS are the deliverable.
The suspect this bench was built to try: _sync_gate_pal's 64-iteration Python
loop, which pal() runs on EVERY call (the celeste shim calls pal() reset every
frame and pal sandwiches around tinted draws) while camera()/clip() store 6
ints. Run:

    .venv/bin/python experiments/state_verb_cost/bench.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
UNIX_MP = (TDECK / ".build" / "lvgl_micropython" / "lib" / "micropython"
           / "ports" / "unix" / "build-moyluagfx" / "micropython")

DRIVER = r'''
import sys
sys.path.insert(0, @STAGE@)
sys.path.insert(0, @MODULES@)
import time

import moy_gfx, moy_lua                     # both usermods, or die loudly
import device_api
import device_canvas
from moy_lua_glue import LuaCartRun
from editors_sheet import SpriteSheet, TileMap
from widgets import Pmem
from array import array

N = @N@
REPS = @REPS@
ticks = time.ticks_us
tdiff = time.ticks_diff


class NullInput:
    def held(self, name):
        return False

    def pressed(self, name):
        return False


class NullAudio:
    def sfx(self, n, chan=None):
        pass

    def beep(self, freq, dur=0.15):
        pass

    def music(self, track, loop=True):
        pass

    def music_stop(self):
        pass

    def sound_stop(self, chan=None):
        pass

    def volume(self, level):
        pass


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


def make_side():
    canvas = device_canvas.DeviceCanvas(FakeComp(320, 240))
    assert canvas._gate_ctx is not None     # the C lanes must be live
    sheet = SpriteSheet(16, 32)
    tilemap = TileMap(20, 15)
    ns = device_api.make_api(canvas, NullInput(), {}, sheet=sheet,
                             audio=NullAudio(), tilemap=tilemap, pmem=Pmem())
    return canvas, ns


def best(fn):
    """Median-ish: best of REPS timed runs of fn() (each fn does N calls)."""
    runs = []
    for _ in range(REPS):
        t0 = ticks()
        fn()
        runs.append(tdiff(ticks(), t0))
    runs.sort()
    return runs[len(runs) // 2] / N          # us per call


canvas, ns = make_side()

# -- feature probe the fix depends on: array('H') slice assignment ------------
try:
    a = array("H", range(64))
    b = array("H", bytearray(128))
    b[:] = a
    print("SLICE", "ok" if list(b) == list(range(64)) else "corrupt")
except Exception as e:
    print("SLICE", "unsupported %r" % e)

# -- Python-side bodies (no crossing) -----------------------------------------


def nop():
    pass


def py_nop():
    f = nop
    for _ in range(N):
        f()


def py_camera():
    f = canvas.camera
    for _ in range(N):
        f(4, 2)


def py_clip():
    f = canvas.clip
    for _ in range(N):
        f(0, 0, 320, 240)


def py_pal_reset():
    f = canvas.pal
    for _ in range(N):
        f()


def py_pal_sandwich():
    f = canvas.pal
    for _ in range(N):                       # 2 calls per iter, reported /2
        f(9, 3)
        f()


def py_reset_state_clean():
    f = canvas.reset_state
    for _ in range(N):
        f()


def py_reset_state_dirty():
    f = canvas.reset_state
    p = canvas.pal
    for _ in range(N):                       # pal touch + frame-end restore
        p(9, 3)
        f()


base = best(py_nop)
print("RES py_nop_call            %.3f" % base)
print("RES py_camera              %.3f" % best(py_camera))
print("RES py_clip                %.3f" % best(py_clip))
print("RES py_pal_reset           %.3f" % best(py_pal_reset))
print("RES py_pal_sandwich_percall %.3f" % (best(py_pal_sandwich) / 2.0))
print("RES py_reset_state_clean   %.3f" % best(py_reset_state_clean))
print("RES py_reset_state_dirty   %.3f" % best(py_reset_state_dirty))

# -- Lua-side (crossing + body) ------------------------------------------------

LUA_TMPL = """
local function lnop() end
function _update(dt)
  %s
end
function _draw() end
"""

BODIES = {
    "lua_local_nop": "for i=1,%d do lnop() end" % @N@,
    "lua_probe":     "for i=1,%d do probe() end" % @N@,
    "lua_camera":    "for i=1,%d do camera(4, 2) end" % @N@,
    "lua_pal":       "for i=1,%d do pal(9, 3) pal() end" % @N@,
}


class Proj:
    pass


class Ws:
    pass


def lua_run(src):
    ws = Ws()
    ws.canvas = canvas
    proj = Proj()
    proj.sheet = SpriteSheet(16, 32)
    proj.tilemap = TileMap(20, 15)
    ws.project = proj
    ns2 = dict(ns)
    ns2["probe"] = nop
    return LuaCartRun(ws, ns2, src)


lua_base = None
for name in ("lua_local_nop", "lua_probe", "lua_camera", "lua_pal"):
    run = lua_run(LUA_TMPL % BODIES[name])
    if run.init:
        run.init()

    def tick():
        run.update(0.033)

    us = best(tick)
    run.close()
    if name == "lua_local_nop":
        lua_base = us
        print("RES %-26s %.3f" % (name, us))
    elif name == "lua_pal":
        print("RES %-26s %.3f" % (name + "_percall", us / 2.0))
        print("RES %-26s %.3f" % (name + "_minus_loop",
                                  (us - lua_base) / 2.0))
    else:
        print("RES %-26s %.3f" % (name, us))
        print("RES %-26s %.3f" % (name + "_minus_loop", us - lua_base))
print("DRIVER_DONE")
'''


def main():
    if not UNIX_MP.exists():
        sys.exit("no unix dual-usermod build at %s (see "
                 "tests/test_lua_draw_direct.py for the recipe)" % UNIX_MP)
    stage = Path(tempfile.mkdtemp(prefix="statecost_"))
    try:
        shutil.copy(ROOT / "runtime" / "font.py", stage / "moy_font.py")
        src = (DRIVER
               .replace("@STAGE@", repr(str(stage)))
               .replace("@MODULES@", repr(str(TDECK / "modules")))
               .replace("@N@", "20000")
               .replace("@REPS@", "7"))
        script = stage / "driver.py"
        script.write_text(src)
        out = subprocess.run([str(UNIX_MP), str(script)],
                             capture_output=True, text=True, timeout=600)
        sys.stdout.write(out.stdout)
        if "DRIVER_DONE" not in out.stdout:
            sys.stderr.write(out.stderr)
            sys.exit("driver died")
    finally:
        shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    main()
