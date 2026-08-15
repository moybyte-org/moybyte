# HOW TO RUN (#63): build MicroPython's unix port with the real moy_gfx, then
# run this under it -- the whole device engine (DeviceCanvas/make_api/C spr_gate)
# executes under the real MicroPython VM + allocator on the PC, no hardware:
#   cd firmware/lilygo_t_deck_plus_micropython/.build/lvgl_micropython/lib/micropython/ports/unix
#   make -j8 VARIANT=standard MICROPY_PY_BTREE=0 MICROPY_PY_FFI=0 MICROPY_PY_SSL=0 \
#        USER_C_MODULES=<dir containing a symlink to native/moy_gfx>
#   build-standard/micropython -X heapsize=16m tools/bench_unix_mp.py
# (native/moy_gfx/micropython.mk is the unix-port glue; MOYBYTE_ROOT overrides the
# repo root.) x86 timings are NOT S3 timings -- use it for correctness (pixel parity,
# gate wiring, coalescing) and allocator-behaviour A/Bs, not absolute fps.
# bench_unix -- the REAL stack under REAL MicroPython (unix port): actual
# moy_runtime.DeviceCanvas + make_api + the actual C moy_gfx spr_gate +
# MicroPython's actual gc/allocator. Verifies (a) the C gate draws pixel-
# identically to the Python spr path, (b) the frame-spill fix under the real
# allocator, cold and warm. Only S3 clock-speed absolutes are out of scope.
import sys
import time
import gc

import os
# Default resolved from THIS file, not a maintainer's home directory (the same
# trap that took CI down in replayer_view_test.mjs). MOYBYTE_ROOT still wins:
# the micropython unix build runs this from wherever it likes.
_ROOT = os.getenv("MOYBYTE_ROOT") or os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))
sys.path.insert(0, _ROOT + "/firmware/lilygo_t_deck_plus_micropython/modules")

import moy_gfx
import moy_runtime as m
from editors import SpriteSheet


class FakeComp:
    def __init__(self, w, h):
        self._w = w
        self._h = h
        self._buf = bytearray(w * h * 2)

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._buf

    def back_buffer(self):
        return self._buf

    def gfx(self):
        return moy_gfx


class StubInput:
    pointer = None

    def held(self, name):
        return False

    def pressed(self, name):
        return False


def build_ns(canvas, sheet, use_gate):
    if not use_gate:
        canvas.make_spr_gate = lambda s, f: None
    elif "make_spr_gate" in canvas.__dict__:
        del canvas.make_spr_gate
    ns = m.make_api(canvas, StubInput(), {}, sheet=sheet)
    exec(m._BENCH_KID_CODE, ns)
    ns["lay"] = ns["make_layer"](canvas.w, canvas.h)
    ns["lay"].cls(3)
    ns["_init"]()
    return ns


def run_cfg(label, canvas, sheet, use_gate, frames=60):
    ns = build_ns(canvas, sheet, use_gate)
    upd = ns["_update"]
    drw = ns["_draw"]
    gc.collect()
    tu = 0
    td = 0
    for i in range(frames):
        canvas.batch_reset()
        t0 = time.ticks_us()
        upd(0.033)
        t1 = time.ticks_us()
        drw()
        canvas.flush_batch()
        t2 = time.ticks_us()
        tu += time.ticks_diff(t1, t0)
        td += time.ticks_diff(t2, t1)
    print("BENCH %s: update=%.2fms draw=%.2fms (batch=%d/%d gate=%s)"
          % (label, tu / frames / 1000.0, td / frames / 1000.0,
             canvas._batch_flushes, canvas._batch_sprites,
             "C" if use_gate else "py"))
    return ns


def make_sheet():
    # Spec-shaped by default now (16 x 32, SPEC.md 3.2). It has to be: the C gate
    # lane flushes through moy_gfx.blit_batch, which REFUSES a non-spec sheet and
    # draws nothing -- so while the default was 16x16 this bench's parity check was
    # comparing an empty canvas against the python spr path's real one.
    sheet = SpriteSheet()
    px = sheet.pix
    for i in range(len(px)):
        px[i] = (i * 7) % 15 + 1
    return sheet


# --- 1. PIXEL PARITY: real C gate vs python spr path, same mixed scene --------
def scene(ns, cv):
    spr = ns["spr"]
    cv.cls(1)
    for i in range(12):
        spr((i % 3) + 1, i * 9, 5, 0)
    for i in range(5):
        spr(2, i * 9, 30, 3)          # colorkey change
    spr(1, 10.6, 44.9, 0)              # float coords
    spr(1, 90000, 10, 0)               # clamp
    spr(90000, 20, 10, 0)              # invalid tile
    cv.rect(60, 60, 8, 8, 5)           # run break
    spr(3, 70, 60, 0)
    spr(2, 80, 60, -1, 2)              # scale change
    cv.flush_batch()


sheet = make_sheet()
cvA = m.DeviceCanvas(FakeComp(320, 240))
nsA = build_ns(cvA, sheet, True)
assert type(nsA["spr"]).__name__ == "spr_gate", "expected the C gate, got %r" % nsA["spr"]
scene(nsA, cvA)
cvB = m.DeviceCanvas(FakeComp(320, 240))
nsB = build_ns(cvB, sheet, False)
scene(nsB, cvB)
same = bytes(cvA._buf) == bytes(cvB._buf)
print("BENCH parity C-gate vs python path:", "IDENTICAL" if same else "DIFFERS")
assert same

# --- 2. sakura replica: cold heap ---------------------------------------------
cv = m.DeviceCanvas(FakeComp(320, 240))
run_cfg("cold py  ", cv, sheet, False)
run_cfg("cold gate", cv, sheet, True)

# --- 3. warm/fragmented heap (the production trigger) -------------------------
ballast = [bytearray(150 * 1024)]
for i in range(6000):
    ballast.append([i, i, i, i, i, i, i, i])
frag = [(i, i, i, i) for i in range(20000)]
keep = []
for i in range(0, 20000, 2):
    keep.append(frag[i])
frag = keep
gc.collect()
print("BENCH warm heap live=%dk" % (gc.mem_alloc() >> 10))
run_cfg("warm py  ", cv, sheet, False)
run_cfg("warm gate", cv, sheet, True)
print("BENCH done")
