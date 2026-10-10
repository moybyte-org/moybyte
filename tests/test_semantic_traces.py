"""The moycore SEMANTIC trace harness (#191 / moycore plan §4.2) -- the pin
that must exist BEFORE any more stage-1 verb crossings.

Pixel conformance sees the raster; it cannot see SEMANTICS -- btnp edges, pmem
sign wrap, camera(x)-with-default-y, the pal() reset form, the order the audio
backend hears its verbs. Today a Lua cart and a Python cart agree on all of it
BY CONSTRUCTION (LuaCartRun's registry is a loop over make_api's dict -- the
same closures). Stage 1 breaks that construction on purpose: input state,
camera/clip/pal ownership and the audio queue get C implementations for Lua
carts while Python carts keep the closures -- parallel implementations at the
semantic layer, the one disease class this repo keeps paying for. This harness
is the automated pin that makes the trade safe: one scripted trace (frames of
input + a twin cart that exercises the state verbs and LOGS what it observes)
replayed down BOTH cart paths, then per-frame canvas hashes, the observation
log, the audio command log and the final pmem image are compared 1:1.

Both paths are the REAL device code, run under the unix-port MicroPython with
the real native modules -- nothing is faked but the board:

  side A (lua):    the vendored Lua VM under moycore -- libmoy's binding, the
                   whole cart frame in C -- driven by the real moycore_glue,
                   over a real DeviceCanvas
  side B (python): the same trace exec'd as a Python cart over the same
                   device_api.make_api closures and a second real DeviceCanvas

so when a stage-1 crossing swaps side A's lane from trampoline to C, this
trace is what proves the C implementation semantics-identical. (The Lua
PORTS themselves are pinned separately by the sakura/brick-siege parity
tests, on the same host binding; this file owns the device seam.)

Traced float literals are deliberately binary-exact (0.25, 0.5): the boards
build LUA_32BITS, so a literal like 0.1 crosses as float32 and differs from
Python's double BY DESIGN -- that recorded gap must not fail the pin.

Runs on the desktop MicroPython `make unix-micropython` builds; its absence is
loud rather than a silent skip -- tests/unix_mp.py.

EXTENDED 2026-08-12 (before stage 2, per the plan's rule that a crossing
extends the vocabulary FIRST): the twins now also exercise `cls()`'s default
form, `map` with colorkey AND scale, `clip` clamped from both directions,
`camera`'s RETURN value read back so the value SET is observed and not merely
the value returned, `pal` with indices past 63 (masked) and a repeat of an
earlier tint (which must reuse its palgen id rather than mint one), `palt`
un-setting, and the 2-arg `pix` READ -- the one verb that observes canvas state
as a value, and an odd form that falls back to the trampoline.

Each was mutation-tested, and two of them failed that test first, which is the
reason they are worth reading: a `camera(6,4)` -> `camera(6,5)` slip was
invisible until something was drawn under the new camera and the value read
back, and a `pix(5,5)` -> `pix(6,5)` slip was invisible until the sample
straddled a drawn edge instead of sitting inside a flat region. A trace that
observes a value which does not depend on the thing being tested passes for
the wrong reason.

EXTENDED 2026-10-04 (#225, before a Lua layer's drawing moved onto libmoy's
verbs): the twins draw INTO the layer every frame -- its own camera (set, and
read back), clip and pal around shapes, text, sspr, map, spr and tline -- and
read its pixels in pairs across the edges those move. Mutation-tested: the
layer's camera, clip and pal, the camera read-back, and the sspr/tline/map/spr
/line/rectb/print placements each turn it red.

EXTENDED 2026-10-05 (#224, before the store crosses in sprint 1b): a second
trace, the STORE's. The native store will expose carts by handle
(runtime/moy_catalogue.py over runtime/moy_index.py), and this replays one
scripted session of that interface -- create, catalogue, a rescan, entry and
load, path and handle, new, duplicate, delete and every call on the deleted
handle, a freed slot taken again under its next generation, a folder removed
behind the store's back and reconciled away, a root that will not list, a
second store beside the first (a row's key is its root and folder), forged
and non-int handles, a full index,
then the seed (cold, warm, and a version bump that keeps the kid's config),
load whole, a publish, a torn file recovered from its backup, the journal's
append (and its no-op), undo and redo to the floor and the ceiling, and a
full root table giving up the store named longest ago -- on CPython, on the boards' VM and, where it is built, on the boards'
32-bit object model. The log is pinned VERBATIM (STORE_TRACE): the handle
values are slot.generation, so the allocation order is part of the contract a
native binding must keep, not an accident of this one. Mutation-tested: the
generation bump on release, lowest-free-first reuse, the reconcile's release
and the unlisted root's no-op each turn it red. The same log is pinned over the
native index (sprint 1a's twin, native/moy_index) on both object models.

EXTENDED 2026-10-06 (#224, before the spine crosses in sprint 2): a third
trace, the SPINE's (its Python oracle tests/spine_twin.py, and the strike ledger in
runtime/crash_guard.py). One session of handle tables (a foreign, released,
forged and non-int handle each refused), the app registry, the back-stack and
the return routes, the WiFi leases, the settings rows (the setter that
persists, the dirty store and its retried write, the read decoded afresh) and
the ledger's arm / heal / strike / forgive / proof bracket over those rows,
pinned verbatim on every VM like the store's: the handle values are kind.slot.generation. Mutation-tested: the
generation bump, lowest-free reuse, the kind check, goto's RETURN truncation,
the route's editor-before-app order and the ledger's proof skip each turn it
red. The same log is pinned over the native spine (sprint 2's twin,
native/moy_spine) on both object models, the ledger half still the Python
CrashGuard.

EXTENDED 2026-10-07 (#224, sprint 3's carve, before the survival set
crosses): five more, one per twin -- input (runtime/moy_input.py), the glass
(native/moy_glass under device_canvas, since sprint 3's pass 1), the frame loop
(runtime/frame_loop.py), the links (native/moy_net, runtime/moy_sync.py
and device/moy_ota_health.py) and audio sessions (runtime/audio_session.py).
Each is pinned on every VM and over the native spine; the section above the
drivers says what each logs.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from unix_mp import find_unix_mp, require_unix_mp

ROOT = Path(__file__).resolve().parent.parent


DT = 0.03125          # 1/32: binary-exact, so LUA_32BITS floats carry it whole
FRAMES = 24

# The scripted input: frame -> held buttons. Edges (btnp) derive from the
# transitions, so the script exercises press, hold, release and re-press.
HELD = {
    3: ("a",), 4: ("a", "left"), 5: ("a", "left"), 6: ("a",),
    9: ("left",),
    10: ("up", "b"), 11: ("up", "b"), 12: ("up",),
    15: ("a",), 16: (), 17: ("a",),          # release + immediate re-press
    20: ("right", "run"), 21: ("right",),
}

# The trace cart, written twice -- line-faithful twins. It reads input, walks
# pmem (including the signed-32-bit wrap SPEC.md 4.2 pins), drives the audio
# verbs in a defined order, edits the tilemap, and draws through every state
# verb FORM that stage 1 will have to reproduce in C: camera(x,y), camera(x),
# camera(), clip(x,y,w,h), clip(), pal(a,b), pal(), palt(i,on), palt() -- each
# interleaved with draws (rect/spr/map/sspr/tline/print/pix) so a state slip
# lands in the frame hash even when no observation logs it.

PY_CART = """\
F = [0]
LYR = [None]

def _init():
    l = make_layer(128, 64)
    l.cls(3)
    l.spr(1, 8, 8)
    l.spr(2, 40, 20, 20)
    LYR[0] = l
    pmem(3, -7)
    trace(0, "pmem_init", pmem(3), pmem(200))

def _update(dt):
    F[0] = F[0] + 1
    f = F[0]
    trace(f, "in", btn("a"), btnp("a"), btn("left"), btnp("left"),
          btn("up"), btnp("up"), btn("run"), btnp("run"))
    if btnp("a"):
        sfx(1)
        pmem(0, pmem(0) + 1)
    if btnp("left"):
        sfx(2, 0)
        beep(440, 0.25)
    if btnp("b"):
        pmem(1, pmem(1) - 2)
    if f == 5:
        music(0, False)
        volume(3)
    if f == 8:
        pmem(7, 2147483647)
        pmem(7, pmem(7) + 1)
    if f == 12:
        music_stop()
        sound_stop()
    mset(2, 3, f % 9)
    mset(f % 16, 5, 1 + f % 8)
    trace(f, "st", mget(2, 3), mget(15, 11), pmem(0), pmem(1), pmem(7))

def _draw():
    f = F[0]
    cls(1)
    camera(4, 2)
    rect(10, 10, 30, 20, 9)
    spr(3, 12, 30)
    spr(5, 24, 30, 20)
    rectb(2, 2, 90, 60, 7)
    camera()
    # --- drawing INTO the layer (#225): a Lua cart's layer methods are the
    # screen's libmoy verbs retargeted, a Python cart's are make_api over the
    # layer's canvas. The layer persists across frames, so every frame draws
    # over the last; its camera/clip/pal are its own, set and read back.
    l = LYR[0]
    l.camera(f % 3, 1)
    l.rect(4, 4, 10, 6, 8 + f % 4)
    l.line(0, 0, 40, f % 30, 11)
    l.clip(2, 2, 60, 40)
    l.pal(11, 12)
    l.circ(30, 20, 5, 11)
    l.rectb(20, 30, 30, 20, 11)
    l.pal()
    l.clip()
    l.print("L" + str(f), 50, 10, 7)
    lx, ly = l.camera()
    l.sspr(4, 8, 16, 16, 70, 30, 24, 18)
    l.tline(0, 60, 95, 60, 0, 131072, 65536, 0)
    l.map(0, 0, 4, 3, 96, 0)
    l.spr(4, 100, 40, 20)
    # Read ACROSS edges, never inside a flat run: both sides of the rect's
    # two edges (the layer camera moves them every frame, and the rect's colour
    # changes with it), the rectb's left edge (pal), and either side of the
    # clip's bottom row on its right edge.
    trace(f, "layer", lx, ly, l.pix(3 - f % 3, 4), l.pix(4 - f % 3, 4),
          l.pix(13 - f % 3, 4), l.pix(14 - f % 3, 4))
    trace(f, "layer2", l.pix(20 - f % 3, 35), l.pix(49 - f % 3, 41),
          l.pix(49 - f % 3, 42))
    draw_layer(LYR[0], f % 8, 2)
    clip(8, 6, 70, 44)
    circ(46, 30, 12, 5)
    pal(9, 3)
    rect(50, 8, 20, 12, 9)
    pal()
    palt(20, True)
    spr(7, 40, 20, -1, 2, 1)
    palt()
    clip()
    camera(-3)
    map(0, 0, 8, 6, 4, 4)
    camera()
    sspr(4, 8, 16, 16, 60, 40, 24, 18)
    tline(0, 60, 95, 60, 0, 131072, 65536, 0)
    pix(f % W, 3, 7)
    print("f" + str(f), 2, 50, 7)
    # --- forms the first vocabulary missed (moycore stage 2 moves ALL of these
    # at once, so each one needs a trace before the switch, not after) --------
    px, py = camera(6, 4)                  # the RETURN value: a tuple here,
    rect(0, 0, 6, 6, 6)                    # two values in Lua. Drawn UNDER the
    qx, qy = camera()                      # new camera, and read BACK, so the
    trace(f, "cam", px, py, qx, qy)        # value set is observed, not just
                                           # the value returned
    clip(-8, -4, 200, 200)                 # the clamps, both directions
    rect(0, 0, 12, 12, 6)
    clip()
    pal(70, 66)                            # > 63: masked, not out of range
    rect(70, 40, 8, 8, 6)
    pal(9, 3)                              # a tint seen before -> the palgen
    rect(78, 40, 8, 8, 9)                  # id must be REUSED, not minted
    pal()
    palt(4, True)
    palt(4, False)                         # un-setting, not just setting
    spr(6, 2, 20, 4)
    map(1, 1, 5, 4, 20, 30, 0, 2)          # colorkey AND scale
    # The 2-arg READ: an odd form (it falls back to the trampoline) AND the
    # only verb that observes canvas state as a value. Sampled ACROSS the edge
    # of the rect drawn above -- reading two points of the same colour makes a
    # coordinate slip invisible, which is exactly what a first draft did.
    trace(f, "read", pix(11, 5), pix(12, 5), pix(5, 5))
    if f == 3:
        cls()                              # the default-argument form
"""

LUA_CART = """\
local f = 0

function _init()
  LYR = make_layer(128, 64)
  LYR:cls(3)
  LYR:spr(1, 8, 8)
  LYR:spr(2, 40, 20, 20)
  pmem(3, -7)
  trace(0, "pmem_init", pmem(3), pmem(200))
end

function _update(dt)
  f = f + 1
  trace(f, "in", btn("a"), btnp("a"), btn("left"), btnp("left"),
        btn("up"), btnp("up"), btn("run"), btnp("run"))
  if btnp("a") then
    sfx(1)
    pmem(0, pmem(0) + 1)
  end
  if btnp("left") then
    sfx(2, 0)
    beep(440, 0.25)
  end
  if btnp("b") then
    pmem(1, pmem(1) - 2)
  end
  if f == 5 then
    music(0, false)
    volume(3)
  end
  if f == 8 then
    pmem(7, 2147483647)
    pmem(7, pmem(7) + 1)
  end
  if f == 12 then
    music_stop()
    sound_stop()
  end
  mset(2, 3, f % 9)
  mset(f % 16, 5, 1 + f % 8)
  trace(f, "st", mget(2, 3), mget(15, 11), pmem(0), pmem(1), pmem(7))
end

function _draw()
  cls(1)
  camera(4, 2)
  rect(10, 10, 30, 20, 9)
  spr(3, 12, 30)
  spr(5, 24, 30, 20)
  rectb(2, 2, 90, 60, 7)
  camera()
  -- drawing into the layer; see the Python twin
  LYR:camera(f % 3, 1)
  LYR:rect(4, 4, 10, 6, 8 + f % 4)
  LYR:line(0, 0, 40, f % 30, 11)
  LYR:clip(2, 2, 60, 40)
  LYR:pal(11, 12)
  LYR:circ(30, 20, 5, 11)
  LYR:rectb(20, 30, 30, 20, 11)
  LYR:pal()
  LYR:clip()
  LYR:print("L" .. f, 50, 10, 7)
  local lx, ly = LYR:camera()
  LYR:sspr(4, 8, 16, 16, 70, 30, 24, 18)
  LYR:tline(0, 60, 95, 60, 0, 131072, 65536, 0)
  LYR:map(0, 0, 4, 3, 96, 0)
  LYR:spr(4, 100, 40, 20)
  trace(f, "layer", lx, ly, LYR:pix(3 - f % 3, 4), LYR:pix(4 - f % 3, 4),
        LYR:pix(13 - f % 3, 4), LYR:pix(14 - f % 3, 4))
  trace(f, "layer2", LYR:pix(20 - f % 3, 35), LYR:pix(49 - f % 3, 41),
        LYR:pix(49 - f % 3, 42))
  draw_layer(LYR, f % 8, 2)
  clip(8, 6, 70, 44)
  circ(46, 30, 12, 5)
  pal(9, 3)
  rect(50, 8, 20, 12, 9)
  pal()
  palt(20, true)
  spr(7, 40, 20, -1, 2, 1)
  palt()
  clip()
  camera(-3)
  map(0, 0, 8, 6, 4, 4)
  camera()
  sspr(4, 8, 16, 16, 60, 40, 24, 18)
  tline(0, 60, 95, 60, 0, 131072, 65536, 0)
  pix(f % W, 3, 7)
  print("f" .. f, 2, 50, 7)
  -- forms the first vocabulary missed; see the Python twin
  local px, py = camera(6, 4)
  rect(0, 0, 6, 6, 6)
  local qx, qy = camera()
  trace(f, "cam", px, py, qx, qy)
  clip(-8, -4, 200, 200)
  rect(0, 0, 12, 12, 6)
  clip()
  pal(70, 66)
  rect(70, 40, 8, 8, 6)
  pal(9, 3)
  rect(78, 40, 8, 8, 9)
  pal()
  palt(4, true)
  palt(4, false)
  spr(6, 2, 20, 4)
  map(1, 1, 5, 4, 20, 30, 0, 2)
  trace(f, "read", pix(11, 5), pix(12, 5), pix(5, 5))
  if f == 3 then
    cls()
  end
end
"""

# The driver run inside the unix MicroPython. @TOKENS@ substituted by the
# test (plain .replace -- the driver body is full of literal % operators).
DRIVER = r'''
import sys
# The SOURCE trees, not a board's staged modules/ dir (gitignored build
# output: absent on a fresh checkout, stale on a warm one -- and running
# yesterday's staged copy is the one thing this file exists to rule out).
# RUNTIME carries the shared console files (editors_sheet/widgets), DEVICE the
# device tier on top of it, and STAGE outranks both with the files the build
# stages under their frozen names (lua_ext/input/moy_font/moy_image/moy_fs).
sys.path.insert(0, @RUNTIME@)
sys.path.insert(0, @DEVICE@)
sys.path.insert(0, @STAGE@)
import hashlib

import moy_gfx, moycore                    # both usermods, or die loudly
from input import InputState, BUTTONS      # the BOARDS' real input class
import device_api
import device_canvas
from moycore_glue import MoycoreRun
from editors_sheet import SpriteSheet, TileMap
from widgets import Pmem

W, H = 96, 64
DT = @DT@
FRAMES = @FRAMES@
HELD = @HELD@
PY_CART = @PY_CART@
LUA_CART = @LUA_CART@


def norm(v):
    if v is True:
        return 1
    if v is False:
        return 0
    if isinstance(v, float) and v == int(v):
        return int(v)
    return v


class ScriptInput(InputState):
    """The scripted feed, driving the REAL InputState rather than imitating it.

    It used to reimplement held/pressed over its own two tuples, and when
    moycore started asking for button_masks() that would have been a THIRD
    copy of the bit order living in the very suite meant to catch drift.
    Subclassing means the harness exercises production's edge detection, its
    set handling and its mask derivation -- so if any of those change, this
    trace moves with them instead of quietly agreeing with a stale twin.

    AND IT SUBCLASSES THE BOARDS' CLASS, not the host's (2026-08-14). There are
    two InputState classes; this harness models a DEVICE build, and it was
    staging runtime/input.py -- so for input it compared the host against
    itself. That is the hole the rotated d-pad went through: the boards' BUTTONS
    is a different tuple in a different ORDER, moycore packs the mask from it,
    and every Lua cart on both boards read its d-pad a quarter turn off while
    this suite stayed green.

    It cannot happen quietly again, because of the ASYMMETRY between the twins:
    the Python cart reads buttons BY NAME through make_api, the Lua cart reads
    the BITMASK through moycore's snapshot. A wrong bit order moves one twin and
    not the other, so it lands as a frame-hash mismatch here rather than as a
    bug report from a kid."""

    def set_frame(self, f):
        want = HELD.get(f, ())
        for n in BUTTONS:
            self.set_button(n, n in want)
        self.begin_frame()


class RecAudio:
    def __init__(self):
        self.log = []

    def sfx(self, n, chan=None):
        self.log.append(("sfx", int(n), -1 if chan is None else int(chan)))

    def beep(self, freq, dur=0.15):
        self.log.append(("beep", norm(float(freq)), norm(float(dur))))

    def music(self, track, loop=True):
        self.log.append(("music", int(track), 1 if loop else 0))

    def music_stop(self):
        self.log.append(("music_stop",))

    def sound_stop(self, chan=None):
        self.log.append(("sound_stop", -1 if chan is None else int(chan)))

    def volume(self, level):
        self.log.append(("volume", int(level)))


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
    canvas = device_canvas.DeviceCanvas(FakeComp(W, H))
    assert canvas._gate_ctx is not None     # the C lanes must be live
    sheet = SpriteSheet(16, 32)             # 128 x 256: SPEC 3.2 sheet shape
    for i in range(len(sheet.pix)):
        sheet.pix[i] = (i * 7 + (i >> 7) * 3) & 63
    tilemap = TileMap(20, 15)
    for i in range(len(tilemap.cells)):
        tilemap.cells[i] = (i * 5) % 11     # 0 = empty cells too
    inp = ScriptInput()
    audio = RecAudio()
    pmem = Pmem()
    ns = device_api.make_api(canvas, inp, {}, sheet=sheet, audio=audio,
                             tilemap=tilemap, pmem=pmem)
    tlog = []
    ns["trace"] = lambda *a: tlog.append(tuple(norm(v) for v in a))
    return canvas, sheet, tilemap, inp, audio, pmem, ns, tlog


def run_side(kind):
    canvas, sheet, tilemap, inp, audio, pmem, ns, tlog = make_side()
    hashes = []
    if kind == "lua":
        class Proj:
            pass

        class Ws:
            pass

        ws = Ws()
        ws.canvas = canvas
        proj = Proj()
        proj.sheet = sheet
        proj.tilemap = tilemap
        ws.project = proj
        ws.input = inp
        ws.pmem = pmem
        ws.audio = audio                    # the run's session: the drain's target
        run = MoycoreRun(ws, ns, LUA_CART)
        inp.set_frame(0)
        # _init ran inside run_begin (libmoy's moy_lua_init), so there is no
        # separate init step -- run.init is None by construction.
        canvas.reset_state()
        for f in range(1, FRAMES + 1):
            inp.set_frame(f)
            run.update(DT)                  # _update AND _draw, both in C
            run.draw()                      # present, empty: shape parity
            canvas.reset_state()            # the Player's frame-end flush
            hashes.append(hashlib.sha256(canvas._buf).digest().hex())
        stats = (moycore.active(), moycore.alloc_stats())
        run.flush_pmem()
        run.close()
    else:
        exec(PY_CART, ns)
        inp.set_frame(0)
        ns["_init"]()
        canvas.reset_state()
        for f in range(1, FRAMES + 1):
            inp.set_frame(f)
            ns["_update"](DT)
            ns["_draw"]()
            canvas.reset_state()
            hashes.append(hashlib.sha256(canvas._buf).digest().hex())
        stats = None
    return hashes, tlog, audio.log, list(pmem.cells), stats


ha, ta, aa, pa, stats_a = run_side("lua")
hb, tb, ab, pb, _ = run_side("py")

for f in range(FRAMES):
    print("HASH", f + 1, ha[f], hb[f])
for e in ta:
    print("TA", e)
for e in tb:
    print("TB", e)
for e in aa:
    print("AA", e)
for e in ab:
    print("AB", e)
print("PMEM_A", pa)
print("PMEM_B", pb)
print("STATS", stats_a[0], stats_a[1])
print("DRIVER_DONE")
'''


def test_semantic_trace_lua_vs_python(tmp_path):
    exe = require_unix_mp(
        "moycore", "moy_gfx",
        why="This is THE semantic pin between the two cart runtimes -- input "
            "edges, state-verb ownership, audio order, pmem. Without it, "
            "nothing at all compares them, and CLAUDE.md's rule is to run it "
            "before crossing anything further.")
    stage = tmp_path / "stage"
    stage.mkdir()
    # moy_font is what build.sh stages from runtime/font.py -- the gate ctx
    # (and with it every direct lane) needs it at device_canvas import time.
    shutil.copy(ROOT / "runtime" / "font.py", stage / "moy_font.py")
    # lua_ext is the object-verb glue (prelude + int-handle registry) both Lua
    # runtimes import; build.sh stages it from runtime/ the same way.
    shutil.copy(ROOT / "runtime" / "lua_ext.py", stage / "lua_ext.py")
    # input.py so the scripted feed can BE the console's InputState rather than
    # a second implementation of held/pressed/button_masks -- and it is the
    # BOARDS' one, because this harness models a device build: the native
    # table over all fifteen names. Staging the host's (which is what it used
    # to do) meant the one suite that drives real input through the real glue
    # was testing the wrong tier's input class.
    (stage / "input.py").write_text(
        "from moy_input import InputTable as InputState, NAMES as BUTTONS\n")
    # Same reasoning as the input class above: device_canvas takes Image from
    # moy_image now (ONE definition, shared with the host canvas), and the real
    # build stages it -- with its moy_fs leaf -- out of runtime/ into modules/.
    shutil.copy(ROOT / "runtime" / "moy_image.py", stage / "moy_image.py")
    shutil.copy(ROOT / "runtime" / "moy_fs.py", stage / "moy_fs.py")
    script = tmp_path / "driver.py"
    body = DRIVER
    for token, value in (("@STAGE@", str(stage)),
                         ("@DEVICE@", str(ROOT / "device")),
                         ("@RUNTIME@", str(ROOT / "runtime")),
                         ("@DT@", DT), ("@FRAMES@", FRAMES),
                         ("@HELD@", HELD),
                         ("@PY_CART@", PY_CART), ("@LUA_CART@", LUA_CART)):
        body = body.replace(token, repr(value))
    script.write_text(body)
    out = subprocess.run([exe, str(script)], capture_output=True,
                         text=True, timeout=180)
    assert out.returncode == 0, out.stderr or out.stdout
    lines = out.stdout.strip().splitlines()
    assert lines[-1] == "DRIVER_DONE", out.stdout

    rows = {"TA": [], "TB": [], "AA": [], "AB": []}
    hashes, pmem_a, pmem_b, stats = [], None, None, None
    for line in lines[:-1]:
        tag, rest = line.split(" ", 1)
        if tag == "HASH":
            hashes.append(rest.split())
        elif tag in rows:
            rows[tag].append(rest)
        elif tag == "PMEM_A":
            pmem_a = rest
        elif tag == "PMEM_B":
            pmem_b = rest
        elif tag == "STATS":
            stats = rest

    bad = ["frame %s" % f for f, a, b in hashes if a != b]
    assert not bad, "canvas hashes diverge on: " + ", ".join(bad)

    # The observation log: btn/btnp edges, pmem walk (incl. the signed-32-bit
    # wrap), mget after mset -- value-for-value, in order.
    assert rows["TA"] == rows["TB"], (
        "semantic trace diverges:\n  first lua: %s\n  first py:  %s"
        % (next((a for a, b in zip(rows["TA"], rows["TB"]) if a != b), "?"),
           next((b for a, b in zip(rows["TA"], rows["TB"]) if a != b), "?")))

    # The audio backend heard the same commands in the same order.
    assert rows["AA"] == rows["AB"], (rows["AA"], rows["AB"])

    # The persistent image both carts leave behind.
    assert pmem_a == pmem_b, (pmem_a, pmem_b)

    # And side A really ran under moycore. Vacuity is the failure mode this
    # guards: a run that quietly fell back would agree with side B perfectly,
    # for the wrong reason -- it would BE side B's closures. The old form
    # checked the direct-draw and batch counters; those mechanisms are gone
    # with the runtime that had them, so what is observable now is that the
    # module held the VM for the whole trace.
    assert stats.startswith("True "), \
        "side A did not run under moycore: %s" % stats


# -- the store's trace (#224, sprint 1b) ---------------------------------------
#
# One driver, three interpreters. Every line the driver prints that starts
# with "T " is an observation; the store's own diagnostics (a manifest that
# will not read) print too and are not compared, since their errno text is the
# VM's. @RUNTIME@ and @ROOT@ are the source tree and a fresh store dir.

STORE_DRIVER = r'''import os
import sys
sys.path.insert(0, @RUNTIME@)

import moy_carts
import moy_catalogue as cat
from moy_index import Index, StaleHandle, SLOTS, SLOT_BITS

ROOT = @ROOT@
A = ROOT + "/a/carts"
B = ROOT + "/b/carts"
SRC = "def _draw():\n    cls(1)\n"
HMAX = [0]


def rel(p):
    return p[len(ROOT) + 1:]


def h_(h):
    """A handle as the log shows it: slot.generation, and its magnitude kept."""
    if isinstance(h, int) and h > HMAX[0]:
        HMAX[0] = h
    return "%d.%d" % (h & (SLOTS - 1), h >> SLOT_BITS)


def say(*a):
    print("T", " ".join(str(x) for x in a))


def tried(fn, *a):
    try:
        return fn(*a)
    except StaleHandle:
        return "STALE"
    except TypeError:
        return "TYPE"
    except OSError as e:
        return "OSERROR %d" % e.args[0]


def shelf(root):
    return " ".join("%s=%s" % (e["title"], h_(e["h"])) for e in cat.catalogue(root))


cat.ensure_dirs(A)
for t in ("Beta", "Alpha", "Gamma"):
    c = cat.create(t, A, src=SRC)
    say("create", t, h_(c["h"]), rel(c["path"]))
say("catalogue", shelf(A))
say("rescan", shelf(A))
es = cat.catalogue(A)
alpha, beta, gamma = [e["h"] for e in es]
e = cat.entry(alpha)
say("entry", h_(e["h"]), sorted(k for k in e if k in ("src", "sprites", "h", "title", "cfg")))
w = cat.load(alpha)
say("load", h_(w["h"]), w["title"], len(w["src"]), "src" in w)
say("path", rel(cat.path(beta)), cat.handle(cat.path(beta)) == beta, cat.valid(beta))

n = cat.new(A)
say("new", n["title"], h_(n["h"]))
d = cat.duplicate(alpha, A)
say("duplicate", d["title"], h_(d["h"]), d["src"] == SRC)
say("shelf", shelf(A))
cat.delete(d["h"])
say("delete", cat.valid(d["h"]), tried(cat.load, d["h"]), tried(cat.entry, d["h"]),
    tried(cat.path, d["h"]), tried(cat.delete, d["h"]), tried(cat.duplicate, d["h"], A))
say("folder gone", not moy_carts._exists(d["path"]))
x = cat.create("Delta", A, src=SRC)
say("reuse", h_(x["h"]), "old", h_(d["h"]), tried(cat.load, d["h"]))

moy_carts._rmtree(cat.path(gamma))
say("behind", cat.valid(gamma), cat.load(gamma), cat.entry(gamma))
say("shelf", shelf(A))
say("reconciled", cat.valid(gamma), tried(cat.load, gamma))

say("unlisted", cat.catalogue(ROOT + "/nowhere"), cat.valid(alpha), cat.valid(beta))

cat.ensure_dirs(B)
other = cat.create("Other", B, src=SRC)["h"]
say("other store", shelf(B), cat.valid(alpha), tried(lambda h: cat.load(h)["title"], alpha))
say("back", shelf(A), cat.valid(other), rel(cat.path(other)), cat.handle(cat.path(other)) == other)

C = ROOT + "/c/carts"
SRC2 = "def _draw():\n    cls(2)\n"
SRC3 = "def _draw():\n    cls(3)\n"
SEED = [{"title": "Seed One", "type": "game", "version": 2, "src": SRC,
         "cfg": {"speed": 3}, "flags": "01" * 512},
        {"title": "Seed Two", "type": "app", "version": 1, "src": SRC, "cfg": {}}]


def J(verb, h, *a):
    return tried(getattr(cat, "journal_" + verb), h, *a)


def seeded(seed, root):
    shelf = cat.seed(seed, root, cat.catalogue(root))
    again = cat.catalogue(root)
    assert [(e["path"], e["h"]) for e in shelf] == [(e["path"], e["h"]) for e in again]
    return shelf


cat.ensure_dirs(C)
say("seed", " ".join("%s=%s/%d" % (e["title"], h_(e["h"]), e["version"])
                     for e in seeded(SEED, C)))
one = cat.catalogue(C)[0]["h"]
moy_carts._write(cat.path(one) + "/config.json", '{"speed": 9}')
say("seed warm", " ".join("%s=%s/%d" % (e["title"], h_(e["h"]), e["version"])
                          for e in seeded(SEED, C)))
SEED[0]["version"] = 3
say("seed bump", " ".join("%s=%s/%d" % (e["title"], h_(e["h"]), e["version"])
                          for e in seeded(SEED, C)))
w = cat.load(one)
say("load", w["title"], w["version"], sorted(w["cfg"].items()), len(w["flags"]),
    w["src"] == SRC, w["src_before"], w["src_after"], w["sprites"], w["scenes"])
say("loaded", sorted(k for k in w if k not in ("path", "h")))

moy_carts.save_code(w, SRC2)
say("publish", cat.load(one)["src"] == SRC2)
main = cat.path(one) + "/" + w["main"]
moy_carts._write(main, SRC2[:9])
say("recover", cat.load(one)["src"] == SRC2, moy_carts._read(main) == SRC2)

say("journal", J("append", one, "main.py", SRC2), J("append", one, "main.py", SRC2),
    J("can_undo", one), J("can_redo", one))
moy_carts.save_code(w, SRC3)
say("journal", J("append", one, "main.py", SRC3), J("can_undo", one), J("can_redo", one))
say("undo", J("undo", one), cat.load(one)["src"] == SRC2, J("can_undo", one),
    J("can_redo", one))
say("undo floor", J("undo", one))
say("redo", J("redo", one), cat.load(one)["src"] == SRC3, J("can_redo", one))
say("redo ceiling", J("redo", one))
say("journal stale", J("append", d["h"], "main.py", SRC), J("undo", d["h"]),
    J("redo", d["h"]), J("can_undo", d["h"]), J("can_redo", d["h"]), J("compact", d["h"]))

for i in range(cat.ROOTS - 3):
    cat.ensure_dirs(ROOT + "/r%d/carts" % i)
    cat.catalogue(ROOT + "/r%d/carts" % i)
say("roots", cat.valid(other), cat.valid(alpha), cat.valid(one))
cat.ensure_dirs(ROOT + "/last/carts")
cat.catalogue(ROOT + "/last/carts")
say("roots full", cat.valid(other), cat.valid(alpha), tried(cat.load, alpha), cat.valid(one))
say("roots back", shelf(A), cat.valid(other), cat.valid(one))

for forged in (0, -1, alpha ^ (1 << SLOT_BITS), (1 << SLOT_BITS) | (SLOTS - 1)):
    say("forged", tried(cat.load, forged), cat.valid(forged))
for junk in (None, "1"):
    say("junk", tried(cat.load, junk), cat.valid(junk))

idx = Index()
hs = [idx.intern("/x/%d.moy" % i) for i in range(SLOTS)]
say("full", idx.count(), tried(idx.intern, "/x/more.moy"))
idx.release(hs[9])
say("refill", h_(idx.intern("/x/more.moy")), idx.find("/x/9.moy"))
say("ints", all(isinstance(h, int) for h in idx.handles()), HMAX[0] < (1 << 30))
print("DRIVER_DONE")
'''

STORE_TRACE = """\
create Beta 0.1 a/carts/local.beta.moy
create Alpha 1.1 a/carts/local.alpha.moy
create Gamma 2.1 a/carts/local.gamma.moy
catalogue Alpha=1.1 Beta=0.1 Gamma=2.1
rescan Alpha=1.1 Beta=0.1 Gamma=2.1
entry 1.1 ['h', 'title']
load 1.1 Alpha 24 True
path a/carts/local.beta.moy True True
new New Cart 3.1
duplicate Alpha copy 4.1 True
shelf Alpha=1.1 Alpha copy=4.1 Beta=0.1 Gamma=2.1 New Cart=3.1
delete False STALE STALE STALE STALE STALE
folder gone True
reuse 4.2 old 4.1 STALE
behind True None None
shelf Alpha=1.1 Beta=0.1 Delta=4.2 New Cart=3.1
reconciled False STALE
unlisted [] True True
other store Other=2.2 True Alpha
back Alpha=1.1 Beta=0.1 Delta=4.2 New Cart=3.1 True b/carts/local.other.moy True
seed Seed One=5.1/2 Seed Two=6.1/1
seed warm Seed One=5.1/2 Seed Two=6.1/1
seed bump Seed One=5.1/3 Seed Two=6.1/1
load Seed One 3 [('speed', 9)] 1024 True [] [] None {}
loaded ['author', 'blocks', 'canvas', 'cfg', 'edit', 'extensions', 'flags', 'format', 'fps', 'graduated', 'icon', 'id', 'images', 'input', 'main', 'map', 'memory', 'palette', 'permissions', 'runtime', 'scene_names', 'scenes', 'sounds', 'sprites', 'src', 'src_after', 'src_before', 'title', 'type', 'version', 'writable']
publish True
recover True True
journal 1 None False False
journal 2 True False
undo main.py True False True
undo floor None
redo main.py True False
redo ceiling None
journal stale STALE STALE STALE STALE STALE STALE
roots True True True
roots full True False STALE True
roots back Alpha=0.2 Beta=1.2 Delta=2.3 New Cart=3.2 False True
forged STALE False
forged STALE False
forged STALE False
forged STALE False
junk TYPE False
junk TYPE False
full 4096 OSERROR 28
refill 9.2 0
ints True True
"""


# Run ahead of STORE_DRIVER, it puts the NATIVE index under every import of
# moy_index: the extensible builtin, reached with the path emptied and then
# registered so runtime/moy_index.py never loads (modmoy_index.c's header).
NATIVE_INDEX = r'''import sys
_path = sys.path[:]
sys.path[:] = []
import moy_index
sys.path[:] = _path
assert not hasattr(moy_index, "__file__"), "no native moy_index in this binary"
sys.modules["moy_index"] = moy_index
'''


# Run ahead of a driver on the legs that pin the INTERFACE, it puts the spine's
# Python oracle (tests/spine_twin.py) under every import of moy_spine: no tier
# runs that twin any more (every image, the desktop MicroPython and the
# CPython host run native/moy_spine), so a leg that means the interface says
# so. NATIVE_SPINE below is the legs over the C.
TWIN_SPINE = """import sys
sys.path.insert(0, %r)
import spine_twin
sys.modules["moy_spine"] = spine_twin
""" % str(ROOT / "tests")


def _store_trace(exe, tmp_path, tag, prelude=None):
    root = tmp_path / tag
    root.mkdir()
    script = tmp_path / ("store_%s.py" % tag)
    script.write_text((TWIN_SPINE if prelude is None else prelude) + STORE_DRIVER.replace(
        "@RUNTIME@", repr(str(ROOT / "runtime"))).replace("@ROOT@", repr(str(root))))
    out = subprocess.run([exe, str(script)], capture_output=True, text=True,
                         timeout=180)
    assert out.returncode == 0, out.stderr or out.stdout
    lines = out.stdout.strip().splitlines()
    assert lines and lines[-1] == "DRIVER_DONE", out.stdout
    return [line[2:] for line in lines if line.startswith("T ")]


def _first_difference(got, want):
    for i, (a, b) in enumerate(zip(got, want)):
        if a != b:
            return "line %d:\n  got:  %s\n  want: %s" % (i + 1, a, b)
    return "lengths %d vs %d" % (len(got), len(want))


def test_store_trace_is_the_interface_on_every_vm(tmp_path):
    want = STORE_TRACE.splitlines()
    py = _store_trace(sys.executable, tmp_path, "cpython")
    assert py == want, "the store trace moved: " + _first_difference(py, want)
    exe = require_unix_mp(
        why="This is the store interface's pin on the VM a board runs: the "
            "handle values, the stale-handle refusals and the reconcile, "
            "replayed where the native store will be swapped in.")
    mp = _store_trace(exe, tmp_path, "micropython")
    assert mp == want, "MicroPython diverges: " + _first_difference(mp, want)
    board = find_unix_mp(board_model=True)
    if board is not None:
        b32 = _store_trace(board, tmp_path, "board_model")
        assert b32 == want, ("the 32-bit object model diverges: "
                             + _first_difference(b32, want))


def test_store_trace_holds_over_the_native_index(tmp_path):
    """The same session over the native index (sprint 1a's twin, whichever
    `make unix-micropython` built in), on both object models: the log must be
    STORE_TRACE line for line."""
    want = STORE_TRACE.splitlines()
    exe = require_unix_mp(
        "moy_index",
        why="The native store index's parity with the Python one on the VM "
            "a board runs: the handle values, refusals and reconcile.")
    mp = _store_trace(exe, tmp_path, "native", NATIVE_INDEX)
    assert mp == want, "the native index diverges: " + _first_difference(mp, want)
    board = find_unix_mp("moy_index", board_model=True)
    if board is not None:
        b32 = _store_trace(board, tmp_path, "native_board_model", NATIVE_INDEX)
        assert b32 == want, ("the native index in the 32-bit object model "
                             "diverges: " + _first_difference(b32, want))


# -- the spine's trace (#224, sprint 2) ----------------------------------------
#
# The same shape as the store's: one driver, every interpreter, the log pinned
# verbatim. @RUNTIME@ is the source tree.

SPINE_DRIVER = r'''import sys
sys.path.insert(0, @RUNTIME@)

import moy_spine as sp
from moy_spine import StaleHandle
from crash_guard import CrashGuard, WALLPAPER_KEY


def say(*a):
    print("T", " ".join(str(x) for x in a))


def h_(h):
    """A handle as the log shows it: kind.slot.generation."""
    return "%d.%d.%d" % ((h >> sp.KIND_SHIFT) & 15, h & (sp.SLOTS - 1),
                         h >> sp.GEN_SHIFT)


def tried(fn, *a):
    try:
        return fn(*a)
    except StaleHandle:
        return "STALE"
    except TypeError:
        return "TYPE"
    except ValueError:
        return "VALUE"
    except OSError as e:
        return "OSERROR %d" % e.args[0]


t = sp.Table(3, "thing", 4)
a = t.new("a")
b = t.new("b")
say("new", h_(a), h_(b), t.count(), t.get(b))
u = sp.Table(4, "other")
say("foreign", tried(u.get, a), u.valid(a))
t.release(a)
say("released", tried(t.get, a), t.valid(a), tried(t.release, a))
c = t.new("c")
say("reuse", h_(c), "old", h_(a), tried(t.get, a), t.get(c))
t.new(1)
e = t.new(2)
say("full", t.count(), tried(t.new, "x"))
t.release(e)
t.release(b)
say("handles", " ".join(h_(h) for h in t.handles()), t.count())
say("lowest", h_(t.new("f")), " ".join(h_(h) for h in t.handles()))
for forged in (0, -1, c ^ (1 << sp.GEN_SHIFT), c | (1 << 30), c ^ (1 << sp.KIND_SHIFT)):
    say("forged", tried(t.get, forged), t.valid(forged))
for junk in (None, "1"):
    say("junk", tried(t.get, junk), t.valid(junk))

reg = sp.AppRegistry()
for d in (("artwork", "Paint", False, None), ("files", "Files", True, (310, 230)),
          ("calc", "Calc", False, None)):
    say("register", d[0], h_(reg.register(*d)))
fh = reg.find("files")
say("find", h_(fh), reg.find("menu"), tried(reg.find, None))
say("row", reg.app_id(fh), reg.title(fh), reg.text_mode(fh), reg.min_size(fh))
say("refused", tried(reg.register, "files", "x"), tried(reg.register, "", "x"),
    tried(reg.register, "x" * 16, "x"), tried(reg.register, 5, "x"))
say("stale app", tried(reg.title, fh + (1 << sp.GEN_SHIFT)), reg.valid(fh))
say("apps", " ".join(h_(h) for h in reg.handles()), reg.count())

st = sp.BackStack()
rt = sp.Returns(reg)


def nav(k):
    say("goto", k, st.goto(k), "/".join(st.kinds()))


nav("menu")
rt.run(sp.EDITOR)
nav("desktop")
say("route", rt.route(False), rt.route(True), rt.caller())
say("spend", rt.spend(), rt.caller())
nav("menu")
nav("files")
say("note", rt.note(st.top()), rt.back())
nav("artwork")
say("note", rt.note("settings"), rt.back())
rt.run("files")
nav("desktop")
say("route", rt.route(False), rt.spend())
rt.run("launcher")
say("route", rt.route(False), rt.spend())
rt.run(None)
say("route", rt.route(False), rt.spend())
say("take", rt.take_back(), rt.back())
nav("launcher")
nav("desk")
nav("settings")
nav("launcher")
nav("desk")
nav("desktop")
nav("settings")
say("remove", st.remove("desktop"), st.remove("launcher"), st.remove("nope"),
    "/".join(st.kinds()))
say("refused", tried(st.goto, None), tried(st.goto, ""), tried(st.goto, "x" * 16),
    tried(rt.run, 3), tried(rt.note, None))
say("index", st.index("settings"), st.index("menu"), st.has("desk"), st.depth(), st.top())
for i in range(st.DEPTH - st.depth()):
    st.goto("k%d" % i)
say("deep", st.depth(), tried(st.goto, "more"), st.goto("desk"), st.depth())

ls = sp.Leases()
say("hold", ls.hold("update"), ls.hold("web"), ls.hold("update"), ",".join(ls.holders()))
say("held", ls.held("web"), ls.held("cart"), ls.mask())
say("release", ls.release("link"), ls.release("update"), ls.release("web"), ls.holders())
say("tags", tried(ls.hold, "wasm"), tried(ls.release, None), tried(ls.held, "nobody"))

written = []
s = sp.Settings(lambda text: written.append(text))
say("load", s.load('{"theme": "outline", "fs": 2, "favorites": ["/a.moy"]}'),
    sorted(s.keys()), s.dirty())
s.set("fs", 3)
s.set_text("guard", '{"open": "files"}')
say("rows", s.get("fs"), s.get("favorites"), s.text("guard"), s.get("nope"),
    s.get("nope", 7), s.dirty(), len(written))
say("written", written[0], written[1])
say("delete", s.delete("theme"), s.delete("theme"), sorted(s.keys()), len(written))
say("afresh", s.get("favorites") == s.get("favorites"),
    s.get("favorites") is s.get("favorites"))
r = sp.Settings()
r.set_text("b", "1")
r.set("a", [1, "x"])
r.set("b", 2)
r.delete("a")
r.set("a", None)
say("dump", r.dump(), r.dirty(), r.flush())
lands = []
w = sp.Settings(lambda text: lands.append(text) or len(lands) > 1)
w.set("a", 1)
say("failed", w.dirty(), len(lands), w.flush(), w.dirty(), len(lands), w.flush())
w.set("b", 2, False)
w.set_text("c", " 3 ", persist=False)
say("deferred", w.dirty(), len(lands), w.flush(), lands[-1])
say("refused", tried(s.set_text, "fs", "nope"), tried(s.set_text, "fs", 3),
    tried(s.load, "[1]"), tried(s.get, ""), tried(s.set, None, 1),
    tried(s.set, "", 1), tried(s.text, 4))

saves = []
store = sp.Settings(lambda text: saves.append(1))
g = CrashGuard(store)
say("arm", g.arm("files"), g.strikes("files"), g.last_open(), len(saves))
g.frame()
g.frame()
say("healing", g.frame(), g.strikes("files"), g.last_open(), len(saves))
for _ in range(3):
    g.arm("calc")
    g.release()
say("struck", g.strikes("calc"), g.disabled("calc"), g.arm("calc"), g.broken_ids())
say("forgive", g.forgive("calc"), g.strikes("calc"), g.forgive("calc"), len(saves))
w = CrashGuard(store, key=WALLPAPER_KEY)
say("proof", w.arm("sky", "p1"), w.heal(), len(saves), w.arm("sky", "p1"), len(saves),
    w.arm("sky", "p2"), w.strikes("sky"), len(saves))
say("keys", sorted(store.keys()), sorted(store.get(WALLPAPER_KEY)))
print("DRIVER_DONE")
'''

SPINE_TRACE = """\
new 3.0.1 3.1.1 2 b
foreign STALE False
released STALE False STALE
reuse 3.0.2 old 3.0.1 STALE c
full 4 OSERROR 28
handles 3.0.2 3.2.1 2
lowest 3.1.2 3.0.2 3.1.2 3.2.1
forged STALE False
forged STALE False
forged STALE False
forged STALE False
forged STALE False
junk TYPE False
junk TYPE False
register artwork 1.0.1
register files 1.1.1
register calc 1.2.1
find 1.1.1 0 TYPE
row files Files True (310, 230)
refused VALUE VALUE VALUE TYPE
stale app STALE True
apps 1.0.1 1.1.1 1.2.1 3
goto menu 1 launcher/menu
goto desktop 1 launcher/menu/desktop
route 1 3 menu
spend menu None
goto menu 2 launcher/menu
goto files 1 launcher/menu/files
note True files
goto artwork 1 launcher/menu/files/artwork
note False files
goto desktop 1 launcher/menu/files/artwork/desktop
route 2 files
route 0 launcher
route 0 None
take files None
goto launcher 2 launcher
goto desk 1 launcher/desk
goto settings 1 launcher/desk/settings
goto launcher 2 launcher
goto desk 1 launcher/desk
goto desktop 1 launcher/desk/desktop
goto settings 1 launcher/desk/desktop/settings
remove True False False launcher/desk/settings
refused TYPE VALUE VALUE TYPE TYPE
index 2 -1 True 3 settings
deep 32 OSERROR 28 2 2
hold 2 3 3 web,update
held True False 3
release 3 1 0 []
tags VALUE TYPE VALUE
load 3 ['favorites', 'fs', 'theme'] False
rows 3 ['/a.moy'] {"open": "files"} None 7 False 2
written {"theme": "outline", "fs": 3, "favorites": ["/a.moy"]} {"theme": "outline", "fs": 3, "favorites": ["/a.moy"], "guard": {"open": "files"}}
delete True False ['favorites', 'fs', 'guard'] 3
afresh True False
dump {"b": 2, "a": null} True False
failed True 1 True False 2 True
deferred True 2 True {"a": 1, "b": 2, "c":  3 }
refused VALUE TYPE VALUE VALUE TYPE VALUE TYPE
arm True 1 files 1
healing True 0 None 2
struck 3 True False ['calc']
forgive True 0 False 6
proof True True 8 True 8 True 1 9
keys ['app_guard', 'wallpaper_guard'] ['open', 'proven', 'strikes']
"""


def _spine_trace(exe, tmp_path, tag, prelude=None):
    script = tmp_path / ("spine_%s.py" % tag)
    script.write_text((TWIN_SPINE if prelude is None else prelude) + SPINE_DRIVER.replace(
        "@RUNTIME@", repr(str(ROOT / "runtime"))))
    out = subprocess.run([exe, str(script)], capture_output=True, text=True,
                         timeout=180)
    assert out.returncode == 0, out.stderr or out.stdout
    lines = out.stdout.strip().splitlines()
    assert lines and lines[-1] == "DRIVER_DONE", out.stdout
    return [line[2:] for line in lines if line.startswith("T ")]


# Run ahead of SPINE_DRIVER, it puts the NATIVE spine under every import of
# moy_spine: the extensible builtin, reached with the path emptied and then
# registered so no Python twin loads (modmoy_spine.c's header).
NATIVE_SPINE = r'''import sys
_path = sys.path[:]
sys.path[:] = []
import moy_spine
sys.path[:] = _path
assert not hasattr(moy_spine, "__file__"), "no native moy_spine in this binary"
sys.modules["moy_spine"] = moy_spine
'''


def test_spine_trace_is_the_interface_on_every_vm(tmp_path):
    want = SPINE_TRACE.splitlines()
    py = _spine_trace(sys.executable, tmp_path, "cpython")
    assert py == want, "the spine trace moved: " + _first_difference(py, want)
    exe = require_unix_mp(
        why="This is the spine interface's pin on the VM a board runs: the "
            "handle values, the refusals, the routes and the ledger, replayed "
            "where the native spine will be swapped in.")
    mp = _spine_trace(exe, tmp_path, "micropython")
    assert mp == want, "MicroPython diverges: " + _first_difference(mp, want)
    board = find_unix_mp(board_model=True)
    if board is not None:
        b32 = _spine_trace(board, tmp_path, "board_model")
        assert b32 == want, ("the 32-bit object model diverges: "
                             + _first_difference(b32, want))


def test_spine_trace_holds_over_the_native_spine(tmp_path):
    """The same session over the native spine (sprint 2's twin, whichever
    `make unix-micropython` built in), on both object models: the log must be
    SPINE_TRACE line for line."""
    want = SPINE_TRACE.splitlines()
    exe = require_unix_mp(
        "moy_spine",
        why="The native spine's parity with the Python one on the VM a board "
            "runs: the handle values, the refusals, the routes and the rows.")
    mp = _spine_trace(exe, tmp_path, "native", NATIVE_SPINE)
    assert mp == want, "the native spine diverges: " + _first_difference(mp, want)
    board = find_unix_mp("moy_spine", board_model=True)
    if board is not None:
        b32 = _spine_trace(board, tmp_path, "native_board_model", NATIVE_SPINE)
        assert b32 == want, ("the native spine in the 32-bit object model "
                             "diverges: " + _first_difference(b32, want))


# -- the survival set's traces (#224, sprint 3) ----------------------------------
#
# Sprint 3's carve pins each subsystem's twin before anything crosses
# (docs/kernel_survival_2026-10.md section 2 item 8): input (a scripted event
# stream -> the merged table's masks for players 0 and 1 and the union, and the
# pointer, per frame), the glass (a draw script -> the frame's hash, the buffer
# rows by owner and origin, the pool), the loop (twenty frames -> the stage
# order, the idle ladder and the upcalls per frame, the last ten driven from a
# second thread), the links (a sync batch -> the store's rows; the OTA health
# machine on canned installs) and audio sessions (two sessions, one focused).
# Each driver's log is pinned on CPython, on the desktop MicroPython in both
# object models, and over the native spine. @RUNTIME@, @DEVICE@ and @REPO@ are
# the source trees, @STAGE@ the files a build stages under frozen names, @ROOT@
# a fresh store dir.

INPUT_DRIVER = r'''import sys
sys.path.insert(0, @RUNTIME@)

import moy_input as mi
from input import InputState as HostInput
from lua_ext import MOY_BUTTONS


def say(*a):
    print("T", " ".join(str(x) for x in a))


def h_(h):
    return "%d.%d.%d" % ((h >> 8) & 15, h & 255, h >> 12)


def tried(fn, *a):
    try:
        fn(*a)
        return "ok"
    except ValueError:
        return "VALUE"


board = mi.InputTable()
host = HostInput()
say("names", ",".join(mi.NAMES))
say("host", ",".join(HostInput.BUTTONS))
say("refuse", tried(host.source("kbd").set_held, "start", True),
    tried(board.source("kbd").set_held, "start", True),
    tried(board.source("kbd").set_held, "jump", True))
board.source("kbd").set_held("start", False)
kbd, ble, touch = board.source("kbd"), board.source("ble", 1), board.source("touch")
say("sources", h_(kbd.h), h_(ble.h), h_(touch.h))
ptr = mi.Pointer(320, 240)
board.pointer = ptr

# (source, button, down) writes, a key, and a pointer sample, per frame
SCRIPT = {
    0: ([("kbd", "left", True)], 0, None),
    1: ([("kbd", "a", True), ("ble", "up", True)], 0x61, (10, 20, True)),
    2: ([("kbd", "left", False)], 0, (12, 22, True)),
    3: ([("ble", "b", True), ("ble", "home", True)], 0x1b, None),
    4: ([("kbd", "a", False), ("ble", "up", False)], 0, (12, 22, False)),
    5: ([("touch", "run", True)], 0, None),
    6: ([("ble", "b", False), ("ble", "home", False), ("touch", "run", False)], 0, None),
    7: ([("kbd", "select", True), ("ble", "start", True)], 0x0d, (300, 239, True)),
    8: ([], 0, None),
    9: ([("kbd", "select", False), ("ble", "start", False)], 0, None),
}
SRC = {"kbd": kbd, "ble": ble, "touch": touch}
out = [0, 0]
for f in range(10):
    writes, key, sample = SCRIPT[f]
    for name, button, down in writes:
        SRC[name].set_held(button, down)
    kbd.last_key = key
    if sample is not None:
        x, y, down = sample
        ptr.place(x, y)
        ptr.click = down and not ptr.down
        ptr.down = down
        ptr.fresh = True
    else:
        ptr.click = False
        ptr.fresh = False
    board.begin_frame()
    m = []
    for p in (None, 0, 1):
        board.button_masks(MOY_BUTTONS, p, out)
        m.append("%d/%d" % (out[0], out[1]))
    full = board.button_masks(mi.NAMES)
    say("frame", f, "moy", " ".join(m), "all %d/%d" % full,
        "key", board.last_key, "players", board.player_count(),
        "ptr", ptr.x, ptr.y, int(ptr.down), int(ptr.click), int(ptr.fresh))
board.release_all()
board.begin_frame()
say("release_all", "%d/%d" % board.button_masks(mi.NAMES))
print("DRIVER_DONE")
'''

INPUT_TRACE = """\
names left,right,up,down,a,b,run,home,x,y,stop,save,share,select,start
host left,right,up,down,a,b,run,home
refuse VALUE ok VALUE
sources 6.1.1 6.2.1 6.3.1
frame 0 moy 1/1 1/1 0/0 all 1/1 key 0 players 2 ptr 160 120 0 0 0
frame 1 moy 21/20 17/16 4/4 all 21/20 key 97 players 2 ptr 10 20 1 1 1
frame 2 moy 20/0 16/0 4/0 all 20/0 key 0 players 2 ptr 12 22 1 0 1
frame 3 moy 52/32 16/0 36/32 all 180/160 key 27 players 2 ptr 12 22 1 0 0
frame 4 moy 32/0 0/0 32/0 all 160/0 key 0 players 2 ptr 12 22 0 0 1
frame 5 moy 96/64 64/64 32/0 all 224/64 key 0 players 2 ptr 12 22 0 0 0
frame 6 moy 0/0 0/0 0/0 all 0/0 key 0 players 2 ptr 12 22 0 0 0
frame 7 moy 0/0 0/0 0/0 all 24576/24576 key 13 players 2 ptr 300 239 1 1 1
frame 8 moy 0/0 0/0 0/0 all 24576/0 key 0 players 2 ptr 300 239 1 0 0
frame 9 moy 0/0 0/0 0/0 all 0/0 key 0 players 2 ptr 300 239 1 0 0
release_all 0/0
"""

GLASS_DRIVER = r'''import sys
sys.path.insert(0, @RUNTIME@)
sys.path.insert(0, @DEVICE@)
sys.path.insert(0, @STAGE@)
try:
    import moy_gfx                          # the usermod, on a VM
except ImportError:                         # CPython: the host's binding
    sys.path.insert(0, @REPO@)
    from runtime import host_canvas
    host_canvas.install()                   # moy_gfx and moy_glass over ctypes
    import moy_gfx
import hashlib

import device_canvas as dc
import moy_glass as mg


def say(*a):
    print("T", " ".join(str(x) for x in a))


def h_(h):
    return "%d.%d.%d" % ((h >> 8) & 15, h & 255, h >> 12)


class Comp:
    def __init__(self, w, h):
        self._w, self._h = w, h
        self._buf = bytearray(w * h * 2)

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._buf

    def gfx(self):
        return moy_gfx


def rows(tag):
    h = dc._OWNERS.get(tag)
    if h is None:
        return "-"
    out = []
    for b in mg.rows(h):
        r = mg.row(b)
        out.append("%s:r%d:o%d:%d" % (h_(b), r[1], r[2], r[0]))
    return ",".join(out) or "-"


def pool():
    out = {}
    for b in mg.rows(-1):
        n = mg.row(b)[0]
        out[n] = out.get(n, 0) + 1
    return sorted(out.items())


def crc(cv):
    return hashlib.sha256(cv._comp._buf).digest()[:6].hex()


cv = dc.DeviceCanvas(Comp(96, 64))
cv.cls(1)
cv.rect(4, 4, 20, 12, 8)
cv.print("GLASS", 30, 10, 7)
say("draw", crc(cv), "canvas", h_(cv._crow.h))
lay = cv.new_layer(64, 32, owner="cart")
lay.cls(3)
lay.rect(0, 0, 8, 8, 10)
cv.blit_window_from(lay, 4, 0)
say("layer", crc(cv), "owner", h_(dc._OWNERS["cart"]), "rows", rows("cart"),
    "canvas", h_(lay._crow.h))
small = dc.DeviceCanvas(dc._LayerComp(32, 16, moy_gfx, dc._owner_h("cart")))
lay2 = small.new_layer(32, 16, owner="cart")
console = cv.new_layer(32, 16)
say("lent", rows("cart"), "console", h_(console._comp.buf_h))
cv.reclaim_layers("cart")
say("reclaim", rows("cart"), "pool", pool(), "live", int(lay._comp._b.live),
    int(small._comp._b.live), int(lay2._comp._b.live))
again = cv.new_layer(64, 32, owner="cart")
say("reuse", rows("cart"), int(again._comp._b.origin == mg.ORIGIN_POOL),
    "pool", pool())
console.release()
st = mg.stats()
say("release", "rows", st[0], "heap", st[5], "kernel", st[7], "cart", st[8])
s1 = mg.surface("win:make")
s2 = mg.surface("bar")
g0 = mg.content_gen(s1)
mg.touch(s1)
mg.epoch()
say("surf", h_(s1), h_(s2), int(mg.content_gen(s1) > g0),
    int(mg.content_gen(s2) == mg.content_gen(mg.surface("chips"))))
mg.sync(["win:other"])
say("sync", mg.surface_find("win:make"), h_(mg.surface_find("bar")))
k0 = mg.kernel_epoch()
mg.kernel_bump()
say("kernel", int(mg.kernel_epoch() != k0))
print("DRIVER_DONE")
'''

GLASS_TRACE = """\
draw bcf7a0e484b6 canvas 3.0.1
layer 55129292ac68 owner 5.0.1 rows 2.0.1:r1:o2:4096 canvas 3.1.1
lent 2.0.1:r1:o2:4096,2.1.1:r1:o2:1024,2.2.1:r1:o2:1024 console 2.3.1
reclaim - pool [(1024, 2), (4096, 1)] live 0 0 0
reuse 2.0.3:r1:o1:4096 1 pool [(1024, 2)]
release rows 3 heap 0 kernel 0 cart 4096
surf 4.0.1 4.1.1 1 0
sync 0 4.1.1
kernel 1
"""

LOOP_DRIVER = r'''import sys
sys.path.insert(0, @RUNTIME@)

import moy_loop

try:
    import _thread
except ImportError:
    _thread = None


def say(*a):
    print("T", " ".join(str(x) for x in a))


# The kernel's loop (native/moy_kernel/moy_loop.c) on its trace tier: a fake
# clock, scripted inputs, a byte queue for the dev channel, and every stage the
# loop drives logged as a token. 20 fps (a 50 ms slot), a light that dims, and
# the ladder's three rungs at 1, 2 and 3 seconds; a frame is 250 ms.
moy_loop.trace_init(20, True, 1000)
moy_loop.idle(moy_loop.DIM, 1)
moy_loop.idle(moy_loop.SAVER, 2)
moy_loop.idle(moy_loop.BLANK, 3)
moy_loop.capture(True)
DRAWN = [0]


def handle_input():
    moy_loop.trace_note("hi")


def handle_pointer():
    moy_loop.trace_note("hp")


def frame(dt):
    moy_loop.trace_note("frame:%d" % int(dt * 1000 + 0.5))
    DRAWN[0] += 1
    return DRAWN[0]


def words(line):
    # The console's words: `tap` starts a gesture the kernel plays.
    parts = line.split()
    if parts[0] == "tap":
        moy_loop.tap(int(parts[1]), int(parts[2]))
    moy_loop.trace_note("word:" + parts[0])
    return False


moy_loop.register(handle_input, handle_pointer, frame, words)

# Input on frames 0-2 and a touch on frame 18; a serial tap on frame 4.
ACTIVE = (0, 1, 2, 18)
CLOCK = [1000]


def one(f):
    moy_loop.trace_clock(CLOCK[0])
    a = f in ACTIVE
    moy_loop.trace_input(a, a)
    if f == 4:
        moy_loop.trace_feed(b"tap 10 20\n")
    r = moy_loop.step()
    up = moy_loop.upcalls()[0]
    say("frame", f, r, moy_loop.trace_log(), "up", "/".join(str(x) for x in up),
        "idle", moy_loop.idle()[0])
    CLOCK[0] += 250


for f in range(10):
    one(f)

# The last ten from a second thread: the loop keeps no state on a stack.
# The main thread blocks on a lock while the second one runs -- no polling
# beside it (the unix port runs threads with no GIL) -- and the second always
# releases it, so a failure there is reported, never a hang.
DONE = []


def rest():
    try:
        for f in range(10, 20):
            one(f)
        DONE.append(None)
    except Exception as e:
        DONE.append(repr(e))


import time


def wait_for_rest():
    # A function, so the waiting thread adds nothing to the module's globals
    # while the other one reads them: on a VM without a GIL that insertion
    # races the lookup and the reader sees a NameError.
    _thread.start_new_thread(rest, ())
    waited = 0
    while not DONE and waited < 3000:
        time.sleep(0.01)
        waited += 1
    if not DONE:
        raise SystemExit("the second thread never finished its ten frames")
    if DONE[0] is not None:
        raise SystemExit("the second thread raised " + DONE[0])


if _thread is not None:
    wait_for_rest()
else:
    rest()

# Every stage was metered once a frame; the tail has no deadline.
m = moy_loop.meters()
say("meters", " ".join("%s:%d:%s" % (n, m[n][5], m[n][4]) for n in moy_loop.stages()))
say("upcalls", "/".join(str(x) for x in moy_loop.upcalls()[1]))
moy_loop.unregister()
say("unregistered", moy_loop.step())
if DONE == [None]:
    print("DRIVER_DONE")
'''

LOOP_TRACE = """\
frame 0 0 inputs pointer:click present hi hp frame:0 fence light=255 first_light tail:drew feed sleep=50 up 3/0/0/0/0 idle 0
frame 1 0 inputs pointer:click present hi hp frame:100 tail:drew feed sleep=49 up 3/0/0/0/0 idle 0
frame 2 0 inputs pointer:click present hi hp frame:100 tail:drew feed sleep=48 up 3/0/0/0/0 idle 0
frame 3 0 inputs pointer present hi hp frame:100 tail:drew feed sleep=47 up 3/0/0/0/0 idle 0
frame 4 0 inputs pt=10,20,1,1 word:tap pointer present hi hp frame:100 tail:drew feed sleep=46 up 4/0/0/0/0 idle 0
frame 5 0 inputs pt=10,20,0,0 pointer present hi hp frame:100 tail:drew feed sleep=45 up 3/0/0/0/0 idle 0
frame 6 0 inputs pointer present hi hp frame:100 tail:drew feed sleep=44 up 3/0/0/0/0 idle 0
frame 7 0 inputs pointer present hi hp frame:100 tail:drew feed sleep=43 up 3/0/0/0/0 idle 0
frame 8 0 inputs pointer present hi hp frame:100 tail:drew say[PERF_cart=-_fps=4/4_net=-_tick=-_miss=-_busy=0ms_draw=-_flush=-_logic=-_render=-_chrome=-_wmr=-_wmw=-_wms=-_ppa=-_fence_ms=-_gfence_ms=-_home=-_gc=-] say[LOOP_n=9_ms=0.0_inputs=0.0_dev=0.0_idle=0.0_pointer=0.0_present=0.0_frame=0.0_backlight=0.0_pump_tail=0.0_tail=0.0_sleep=46.0_other=0.0] feed sleep=42 up 3/0/0/0/0 idle 0
frame 9 0 inputs light=48 say[Moybyte_power_save:_dim_(idle_1s)] pointer present hi hp frame:100 tail:drew feed sleep=42 up 3/0/0/0/0 idle 1
frame 10 0 inputs pointer present hi hp frame:100 tail:drew feed sleep=42 up 3/0/0/0/0 idle 1
frame 11 0 inputs pointer present hi hp frame:100 tail:drew feed sleep=42 up 3/0/0/0/0 idle 1
frame 12 0 inputs pointer present hi hp frame:100 tail:drew feed sleep=42 up 3/0/0/0/0 idle 1
frame 13 0 inputs say[Moybyte_power_save:_saver_(idle_2s)] pointer present hi hp frame:100 tail:drew feed sleep=42 up 3/0/0/0/0 idle 2
frame 14 0 inputs pointer present hi hp frame:100 tail:drew feed sleep=42 up 3/0/0/0/0 idle 2
frame 15 0 inputs pointer present hi hp frame:100 tail:drew feed sleep=42 up 3/0/0/0/0 idle 2
frame 16 0 inputs pointer present hi hp frame:100 tail:drew say[PERF_cart=-_fps=4/4_net=-_tick=-_miss=-_busy=0ms_draw=-_flush=-_logic=-_render=-_chrome=-_wmr=-_wmw=-_wms=-_ppa=-_fence_ms=-_gfence_ms=-_home=-_gc=-] say[LOOP_n=8_ms=0.0_inputs=0.0_dev=0.0_idle=0.0_pointer=0.0_present=0.0_frame=0.0_backlight=0.0_pump_tail=0.0_tail=0.0_sleep=42.0_other=0.0] feed sleep=42 up 3/0/0/0/0 idle 2
frame 17 0 inputs light=0 say[Moybyte_power_save:_blank_(idle_3s)] pointer present hi hp frame:100 tail:drew feed sleep=42 up 3/0/0/0/0 idle 3
frame 18 0 inputs light=255 repaint pointer:swallow present hi hp frame:100 tail:drew feed sleep=42 up 3/0/0/0/0 idle 0
frame 19 0 inputs pointer present hi hp frame:100 tail:drew feed sleep=42 up 3/0/0/0/0 idle 0
meters inputs:20:0 dev:20:0 idle:20:0 pointer:20:0 present:20:0 frame:20:0 backlight:20:0 pump_tail:20:0 tail:20:None pace:20:0 account:20:0
upcalls 61/0/0/0/0
unregistered 3
"""

LINKS_DRIVER = r'''import os
import sys
sys.path.insert(0, @RUNTIME@)
sys.path.insert(0, @DEVICE@)

try:
    import moy_net
except ImportError:                         # CPython: the C over ctypes
    sys.path.insert(0, @REPO@)
    from runtime import net_binding
    net_binding.install()
    import moy_net
import moy_sync
import moy_ota_health
from moy_ota_health import SlotHealth

ROOT = @ROOT@


def say(*a):
    print("T", " ".join(str(x) for x in a))


def tree(path, rel=""):
    out = []
    for name in sorted(os.listdir(path)):
        full = path + "/" + name
        r = rel + name
        try:
            os.listdir(full)
            out.extend(tree(full, r + "/"))
        except OSError:
            with open(full) as f:
                out.append("%s=%d" % (r, len(f.read())))
    return out


# -- a sync batch into a store --------------------------------------------------
carts = ROOT + "/carts"
os.mkdir(carts)
BATCH = moy_net.encode_batch(1, None, [
    {"p": "hop.moy/manifest.json", "t": '{"title": "Hop"}'},
    {"p": "hop.moy/main.py", "t": "def _draw():\n    cls(1)\n"},
    {"p": "hop.moy/big.lua", "t": "-- one", "part": 0},
    {"p": "hop.moy/big.lua", "t": " two", "part": 1},
    {"p": "hop.moy/big.lua", "pub": 1},
    {"p": "hop.moy/journal/x", "t": "never"},
    {"p": "../escape", "t": "never"},
    {"p": "old.moy/main.py", "t": "x"},
    {"p": "old.moy", "dc": 1}], "1234")
ops, pin, root_id = moy_sync.parse_batch(BATCH.encode())
say("parse", len(ops), pin, root_id)
applied, errs, shelf = moy_sync.apply_ops(carts, ops, root_id)
say("apply", applied, ",".join("%d:%s" % e for e in errs), int(shelf))
say("rows", " ".join(tree(carts)))
say("refuse", moy_sync.parse_batch(moy_net.encode_batch(2, "nope", [])),
    moy_sync.parse_batch(moy_net.encode_batch(1, "files", [])),
    moy_sync.parse_batch(b"{"))
files_ops = moy_sync.parse_batch(moy_net.encode_batch(2, "files", [{"p": "x"}]))
say("files", files_ops[2], len(files_ops[0]))

# -- the OTA health machine on a canned install --------------------------------
upd = ROOT + "/update"
os.mkdir(upd)


class Health(SlotHealth):
    def __init__(self, running):
        SlotHealth.__init__(self, lambda fn: fn(), upd)
        self._running = running
        self.valid = 0

    def _running_label(self):
        return self._running

    def version_label(self):
        return "0.9"

    def mark_valid(self):
        self.valid += 1
        return True


def pending(slot):
    with open(upd + "/" + moy_ota_health.PENDING_NAME, "w") as f:
        f.write('{"slot": "%s", "version": 5, "label": "0.8"}' % slot)


for running, staged in (("ota_1", "ota_1"), ("ota_0", "ota_1"), ("ota_0", None)):
    if staged:
        pending(staged)
    h = Health(running)
    say("boot", running, staged, h.boot_check())
    # The console's confirm is the kernel loop's (moy_loop.c marks the slot
    # valid); its service upcall retires the marker, once.
    ladder = [h.confirmed_by_kernel(), h.confirmed_by_kernel()]
    say("confirm", ladder, "valid", h.valid, "marker",
        int(moy_ota_health.PENDING_NAME in os.listdir(upd)))
h = Health("ota_0")
serve = []
for i in range(moy_ota_health.HEALTHY_SERVES + 10):
    if h.confirm_when_serving(i != 7):
        serve.append(i)
say("serving", serve, "valid", h.valid)

# -- the updater's pure half and its slot (native/moy_net/moy_ota.c) -----------
M = ('{"board": "tdeck", "channel": "unstable", "version": 1785665581, '
     '"size": 4292512, "sha256": "AB%s", "c6": {"version": 2, "size": 9, '
     '"sha256": "cd"}}' % ("01" * 31))
say("canon", moy_net.ota_canonical(M))
say("canon6", moy_net.ota_canonical_c6(M))
say("canon-bad", moy_net.ota_canonical('{"version": "x"}'), moy_net.ota_canonical("[1]"))
KEY = (("c" + "5" * 511, 65537),)
for text, board, need in ((M, "tdeck", True), (M, "tdeck", False), (M, "p4", False),
                          (M[:-1] + ', "sig": "00"}', None, False), ("nope", None, False)):
    say("judge", moy_net.ota_judge(text, board, need, KEY))
say("judge6", moy_net.ota_judge_c6(M, True, KEY), moy_net.ota_judge_c6(M, False, KEY))
say("verify", moy_net.ota_verify(b"x", "zz", KEY), moy_net.ota_verify(b"x", None))
for size, data in ((0, b""), (8, b"\x00" * 8), (8, b"\xe9" * 4), (8, b"\xe9" * 8)):
    try:
        moy_net.ota_slot_begin(size)
        ok = moy_net.ota_slot_write(data) and moy_net.ota_slot_close()
    except ValueError as e:
        ok = "raised " + str(e)
    say("slot", size, ok, moy_net.ota_state()[0], moy_net.ota_state()[6])
say("activate", moy_net.ota_activate(), moy_net.ota_activate())
for url in ("ftp://x/y", "http://h:0/"):
    try:
        moy_net.http_open(url, "t")
    except OSError as e:
        say("open", url, e.args[0])
try:
    moy_net.ota_dl_begin("", 1, "", 1)
except ValueError as e:
    say("dl", e)

# -- the web-console switch (native/moy_net/moy_webconsole.c) ------------------
moy_net.wc_on(0, carts, None, None, "1234", ("/run",), None)
say("wc join", moy_net.wc_state()[0], moy_net.wc_url(True) == "")
moy_net.wc_park(True)
say("wc park", moy_net.wc_state()[1])
moy_net.wc_park(False)
moy_net.wc_off()
say("wc off", moy_net.wc_state()[0])

# -- the setup portal's DNS (native/moy_net/moy_dns.c) -------------------------
Q = b"\xab\xcd\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07example\x03com\x00"
for qtype in (b"\x00\x01", b"\x00\x1c"):
    r = moy_net.dns_reply(Q + qtype + b"\x00\x01", "192.168.4.1")
    say("dns", len(r), r[2], r[7], r[-4:] == b"\xc0\xa8\x04\x01")
say("dns drop", moy_net.dns_reply(b"\xab\xcd\x81" + Q[3:] + b"\x00\x01\x00\x01", "1.2.3.4"))
print("DRIVER_DONE")
'''

LINKS_TRACE = """\
parse 9 1234 carts
apply 7 5:bad path,6:bad path 1
rows hop.moy/big.lua=10 hop.moy/main.py=24 hop.moy/main.py.bak=46 hop.moy/manifest.json=16 hop.moy/manifest.json.bak=38
refuse (None, None, None) (None, None, None) (None, None, None)
files files 1
boot ota_1 ota_1 ('ok', '0.8 -> 0.9')
confirm [True, False] valid 0 marker 0
boot ota_0 ota_1 ('rolled_back', 'put 0.9 back')
confirm [True, False] valid 0 marker 0
boot ota_0 None None
confirm [True, False] valid 0 marker 0
serving [307] valid 1
canon b'moybyte-ota-v2\\ntdeck\\nunstable\\n1785665581\\n4292512\\nab01010101010101010101010101010101010101010101010101010101010101'
canon6 b'moybyte-c6-v1\\ntdeck\\n2\\n9\\ncd'
canon-bad None None
judge unsigned update
judge None
judge wrong board
judge bad signature
judge bad manifest
judge6 unsigned c6 image None
verify False False
slot 0 raised empty image 0 empty image
slot 8 False 4 not an app image
slot 8 False 4 size 4/8
slot 8 True 2 
activate ota_1 None
open ftp://x/y 22
open http://h:0/ 22
dl manifest has no url
wc join 1 True
wc park True
wc off 0
dns 45 133 1 True
dns 29 133 0 False
dns drop None
"""

SESSION_DRIVER = r'''import sys
sys.path.insert(0, @RUNTIME@)
try:
    import moy_audio as na                  # the usermod, on a VM
except ImportError:                         # CPython: the same C over ctypes
    sys.path.insert(0, @REPO@)
    from runtime import audio_binding
    na = audio_binding.install()
import json

from audio import AudioBank
from audio_session import AudioSession
from lua_ext import drain_audio


def say(*a):
    print("T", " ".join(str(x) for x in a))


def h_(h):
    return "%d.%d.%d" % ((h >> 8) & 15, h & 255, h >> 12)


def calls():
    for r in na.trace():
        say("call", r[0], r[1], r[2], r[3])


buf = bytearray(800)


def sounds():
    na.render(buf, 400)
    return any(buf)


bank = json.dumps(AudioBank.default().to_dict())
na.set_rate(8000)
na.trace(True)
a = na.open(11, bank)
b = na.open(12, bank)
na.focus(a)
say("open", h_(a), h_(b), "focused", h_(na.focused()))
na.sfx(b, 0)
na.music(b, 0)
say("muted", sounds(), "active", na.active(b) != 0)
na.focus(b)
say("focus", h_(na.focused()), "sounds", sounds())
na.beep(a, 440, 0.25)
na.level(b, 4)
na.music_stop(b)
na.stop(a, 2)
calls()
c = na.open(11)
say("reopen", h_(c))
try:
    na.sfx(a, 1)
except ValueError:
    say("stale", h_(a))
na.close(c)
na.close(b)
say("end", na.focused())
calls()
na.hush()
say("hush", sounds())
s = AudioSession(AudioBank.default(), "cart")
na.trace()
drain_audio(s, (0, 1, 2, 3, 4, 5),
            ((0, 3, -1), (1, 0, 1), (2, 440, 250), (3, 0, 0), (4, -1, 0), (5, 6, 0)))
calls()
s.close()
na.trace(False)
print("DRIVER_DONE")
'''

SESSION_TRACE = """\
open 8.0.1 8.1.1 focused 8.0.1
muted False active True
focus 8.1.1 sounds True
call 0 1 11 0
call 1 1 12 0
call 0 3 0 0
call 1 4 0 -1
call 1 6 0 1
call 1 3 0 0
call 0 5 440000 250
call 1 9 4 0
call 1 7 0 0
call 0 8 2 0
reopen 8.0.2
stale 8.0.1
end 0
call 0 10 0 0
call 0 1 11 0
call 0 4 1 -1
call 0 10 0 0
call 1 10 0 0
hush False
call 0 4 3 -1
call 0 6 0 1
call 0 5 440000 250
call 0 7 0 0
call 0 8 -1 0
call 0 9 6 0
"""

# -- the tick model (#224, sprint 4: native/moy_play/moy_tick.c) -----------------
#
# The scheduler that paces a cart, driven through scripted loops -- a light
# scene, two late windows, a heavy scene, the probes back down, stalls, a
# tick-bound loop, the uncap and FREE switches, a thirty cart -- with its
# answers summed per stretch. The model is C in single precision on every
# tier, so each VM's log is the board's.

TICK_DRIVER = r'''import sys
sys.path.insert(0, @RUNTIME@)

import moy_play


def say(*a):
    print("T", " ".join(str(x) for x in a))


def run(name, t, frames, D, T, cost, stall_every=0, stall=0.7):
    # A board loop: a drawing frame costs D ms, a tick-only one T ms; every
    # `stall_every`-th frame stalls. Integer milliseconds, so the dt each VM
    # hands the model is the same float.
    n = d = 0
    for f in range(frames):
        ms = D if t.draw else T
        if stall_every and f % stall_every == stall_every - 1:
            ms = stall
        t.plan(ms / 1000)
        if t.n:
            t.note_tick(cost / 1000)
        n += t.n
        d += t.draw
    say(name, "n", n, "drew", d, "div", t.div, "ticks", t.ticks, "draws", t.draws,
        "misses", t.misses, "probing", t.probing)


t = moy_play.Tick()
say("idle", t.rate, t.div, t.n, t.draw, t.ticks)
t.start(60)
say("start", t.rate, t.tick_ms, t.div)
run("light", t, 300, 10, 2, 1)
run("late", t, 300, 25, 8, 1)
run("late2", t, 300, 25, 8, 1)
run("heavy", t, 400, 45, 3, 1)
run("cheap", t, 800, 10, 2, 1)
run("cheaper", t, 3000, 8, 2, 1)
run("stalls", t, 600, 12, 2, 1, stall_every=40, stall=700)
run("tickbound", t, 600, 20, 30, 20)
t.uncap_mode(True)
run("uncapped", t, 200, 10, 2, 1)
t.uncap_mode(False)
t.steady_mode(False)
run("free", t, 300, 40, 3, 1)
t.start(45)
say("thirty", t.rate, t.tick_ms, t.div, t.uncapped)
run("thirty", t, 400, 33, 33, 1)
run("thirty_slow", t, 400, 50, 10, 1)
say("fits", t.fits(1), t.fits(2), t.fits(4))
print("DRIVER_DONE")
'''

TICK_TRACE = """idle 0 1 0 False 0
start 60 16 1
light n 68 drew 68 div 1 ticks 68 draws 68 misses 0 probing False
late n 396 drew 247 div 2 ticks 464 draws 315 misses 0 probing False
late2 n 335 drew 188 div 2 ticks 799 draws 503 misses 0 probing False
heavy n 1080 drew 400 div 2 ticks 1879 draws 903 misses 0 probing False
cheap n 127 drew 63 div 2 ticks 2006 draws 966 misses 0 probing False
cheaper n 547 drew 522 div 1 ticks 2553 draws 1488 misses 0 probing False
stalls n 268 drew 223 div 1 ticks 2821 draws 1711 misses 15 probing False
tickbound n 601 drew 600 div 1 ticks 3422 draws 2311 misses 610 probing False
uncapped n 121 drew 200 div 1 ticks 3543 draws 2511 misses 610 probing False
free n 253 drew 89 div 3 ticks 3796 draws 2600 misses 610 probing False
thirty 30 33 1 False
thirty n 396 drew 396 div 1 ticks 396 draws 396 misses 0 probing False
thirty_slow n 600 drew 400 div 1 ticks 996 draws 796 misses 0 probing False
fits False False False
"""

SURVIVAL = {"input": (INPUT_DRIVER, INPUT_TRACE),
            "glass": (GLASS_DRIVER, GLASS_TRACE),
            "loop": (LOOP_DRIVER, LOOP_TRACE),
            "links": (LINKS_DRIVER, LINKS_TRACE),
            "session": (SESSION_DRIVER, SESSION_TRACE),
            "tick": (TICK_DRIVER, TICK_TRACE)}


def _survival_trace(name, exe, tmp_path, tag, prelude=None):
    driver = SURVIVAL[name][0]
    work = tmp_path / ("%s_%s" % (name, tag))
    stage = work / "stage"
    store = work / "store"
    stage.mkdir(parents=True)
    store.mkdir()
    # What a build stages under frozen names that the glass's canvas imports.
    shutil.copy(ROOT / "runtime" / "font.py", stage / "moy_font.py")
    for f in ("moy_image.py", "moy_fs.py"):
        shutil.copy(ROOT / "runtime" / f, stage / f)
    for token, value in (("@RUNTIME@", ROOT / "runtime"), ("@DEVICE@", ROOT / "device"),
                         ("@STAGE@", stage), ("@ROOT@", store), ("@REPO@", ROOT)):
        driver = driver.replace(token, repr(str(value)))
    script = work / "driver.py"
    script.write_text((TWIN_SPINE if prelude is None else prelude) + driver)
    out = subprocess.run([exe, str(script)], capture_output=True, text=True,
                         timeout=180)
    assert out.returncode == 0, out.stderr or out.stdout
    lines = out.stdout.strip().splitlines()
    assert lines and lines[-1] == "DRIVER_DONE", out.stdout
    return [line[2:] for line in lines if line.startswith("T ")]


@pytest.mark.parametrize("name", sorted(SURVIVAL))
def test_survival_trace_is_the_interface_on_every_vm(name, tmp_path):
    want = SURVIVAL[name][1].splitlines()
    py = _survival_trace(name, sys.executable, tmp_path, "cpython")
    assert py == want, "the %s trace moved: %s" % (name, _first_difference(py, want))
    exe = require_unix_mp(
        "moy_gfx",
        why="The survival set's twins on the VM a board runs, before sprint 3 "
            "crosses them (docs/kernel_survival_2026-10.md section 2 item 8).")
    mp = _survival_trace(name, exe, tmp_path, "micropython")
    assert mp == want, "MicroPython diverges: " + _first_difference(mp, want)
    board = find_unix_mp("moy_gfx", board_model=True)
    if board is not None:
        b32 = _survival_trace(name, board, tmp_path, "board_model")
        assert b32 == want, ("the 32-bit object model diverges: "
                             + _first_difference(b32, want))


@pytest.mark.parametrize("name", sorted(SURVIVAL))
def test_survival_trace_holds_over_the_native_spine(name, tmp_path):
    """The twins' rows are moy_spine tables; on a board those are the native
    spine's, so the logs must hold over it line for line."""
    want = SURVIVAL[name][1].splitlines()
    exe = require_unix_mp(
        "moy_spine", "moy_gfx",
        why="The survival twins' rows over the native spine, as a board runs "
            "them.")
    mp = _survival_trace(name, exe, tmp_path, "native", NATIVE_SPINE)
    assert mp == want, "the native spine diverges: " + _first_difference(mp, want)


# -- the roles' trace (#224, sprint 5) -------------------------------------------
#
# Every row of the app ABI's role table (native/moy_app/roles.json) driven
# once through a context that holds every role, over a real Workstation and
# its store, and the effect logged: the net sprint 5's steps swap the roles'
# servers under. On CPython the Workstation is the host's (runtime/host_app.py);
# on the boards' object model it is the T-Deck's own desktop, booted by
# moy_runtime.run_desktop over test_frame_alloc's fakes, seeded from the same
# system carts. The driver counts every row it calls on the role objects
# themselves, so a row added to the table and not driven here fails as an
# "uncalled" line: the coverage ratchet. @ROWS@ is the table's (role, verb)
# list.

ROLES_DRIVER = r'''ROWS = @ROWS@
CALLED = {}


def say(*a):
    print("T", " ".join(str(x) for x in a))


def _count(obj, role):
    """Count every table row of `role` called on `obj`: the coverage ratchet's
    tally, kept on the instance so the class and every other holder are
    untouched. A C role's object takes no attribute on a VM: its rows are
    counted by native/moy_app itself (`_c_called`)."""
    for r, verb in ROWS:
        if r != role:
            continue
        real = getattr(obj, verb)

        def counted(*a, _real=real, _key=r + "." + verb, **kw):
            CALLED[_key] = CALLED.get(_key, 0) + 1
            return _real(*a, **kw)

        try:
            setattr(obj, verb, counted)
        except (AttributeError, TypeError):
            return


def _c_called(app, before):
    """The C rows called since `before` (an earlier `app.counts()`), and the
    rows the binding serves in Python, which it counts itself."""
    import moy_app
    now = app.counts()
    for i, name in enumerate(moy_app.rows()):
        if now[i] > before[i]:
            CALLED[name] = CALLED.get(name, 0) + now[i] - before[i]
    for name, n in app.served().items():
        CALLED[name] = CALLED.get(name, 0) + n


def _n(v):
    """A value as the log shows it: blobs by length, lists by their items."""
    if v is None or isinstance(v, (bool, int, str)):
        return repr(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "b%d" % len(v)
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(_n(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join(str(k) + ":" + _n(v[k]) for k in sorted(v)) + "}"
    if v is NO_STORE:
        return "NO_STORE"
    return type(v).__name__


def roles_trace(ws):
    ctx = ws.app_context("tracer", ROLES, prefs_ns="tracer")
    say("roles", " ".join(sorted(k for k in ROLES if hasattr(ctx, k))))
    for role in ROLES:
        _count(getattr(ctx, role), role)
    c_before = ws.app_abi.counts()

    # -- damage
    ws.app_abi.damage_take()
    ctx.damage.all()
    d_all = ws.app_abi.damage_take()
    ctx.damage.again()
    say("damage", d_all, ws.app_abi.damage_take(), ws.app_abi.damage_take())

    # -- surface
    s = ctx.surface
    cv = s.canvas()
    say("surface", cv is ws.sys_canvas, s.size() == (cv.w, cv.h), s.font_scale(),
        s.chrome_scale(), s.windowed(), s.bar_h() == ws.app_bar_h())
    ptr, pt = ws.pointer, s.pointer()
    say("pointer", ptr is not None, pt is None if ptr is None else
        tuple(pt) == (ptr.x, ptr.y, bool(ptr.down), bool(ptr.click),
                      bool(ptr.visible)))
    s.glyph("x", (0, 0, 16, 16), 7, cv)
    say("glyph drawn")

    # -- theme
    t = ctx.theme
    th = t.colors()
    say("theme", t.name(), t.variant(), t.skin(), t.light(), th == ws.theme_colors,
        len(th) > 10, t.token(0) == th["panel"], t.colors() is th)
    gen = t.gen()
    t.set("forest")
    t.set_variant("light")
    say("theme set", t.name(), t.variant(), t.light(), t.colors() == ws.theme_colors,
        t.gen() > gen)
    t.set_skin(t.skin())
    t.set("night", variant="dark")
    say("theme back", t.name(), t.variant(), t.light())

    # -- prefs
    p = ctx.prefs
    say("prefs absent", p.get("k"), p.get("k", 5))
    p.set("k", [1, "two"])
    say("prefs set", _n(p.get("k")), _n(ws.system.get("tracer_k")))
    p.clear("k")
    say("prefs cleared", p.get("k", "gone"))

    # -- files
    f = ctx.files
    say("files ready", f.readable(), f.ready())
    say("files list", _n(f.list("docs")), _n(f.count("docs")))
    name, err = f.new_name("docs", "trace note")
    say("new_name", name, err)
    say("save", _n(f.save("docs", name, f.encode_text("one\ntwo"))))
    blob, err = f.load("docs", name)
    say("load", _n(blob), err, _n(f.decode_text(blob)))
    say("duplicate", _n(f.duplicate("docs", name)))
    say("rename", _n(f.rename("docs", name, "renamed")))
    say("list", _n(f.list("docs")[0]))
    say("delete", _n(f.delete("docs", "renamed")))
    say("trash", _n(f.trash_list()))
    say("restore", _n(f.restore("docs", "renamed")))
    say("empty_trash", _n(f.delete("docs", "renamed")), _n(f.empty_trash()),
        _n(f.trash_list()))
    px = f.encode_image(2, 2, bytes([1, 2, 3, 4]))
    say("image", type(px).__name__, _n(f.decode_image(px)))
    cover = f.encode_cover(bytes(128 * 128))
    dc = f.decode_cover(cover)
    say("cover", cover is not None, dc is not None and len(dc))
    sig = f.sig(px)
    st = f.stamp(px, "drawings", "src", sig)
    src, ssig = f.provenance(st)
    say("stamp", type(sig).__name__, src, ssig == sig, _n(f.provenance(px)))
    say("drawing", _n(f.save("drawings", "pic", px)))
    say("history", _n(f.history_commit("drawings", "pic", [["dot", 1, 1]],
                                       keyframe=None)))
    say("history read", _n(f.history("drawings", "pic")),
        _n(f.history_ops("drawings", "pic")))
    say("session", _n(f.begin()), _n(f.count("drawings")), f.end(),
        ws.app_abi.files_end_all(), _n(f.begin()), _n(f.begin()),
        ws.app_abi.files_end_all())

    # -- carts
    c = ctx.carts
    say("carts ready", c.readable(), c.ready(), c.can_journal(), c.slug("A b-C!"))
    say("carts session", _n(c.begin()), c.end())
    n0 = len(c.all())
    made, err = c.create("Trace Cart", "def _draw():\n    cls(1)\n", "game")
    say("create", made is not None, err)
    c.rescan()
    cart = None
    for x in c.all():
        if x.get("title") == "Trace Cart":
            cart = x
    say("carts", len(c.all()) - n0, cart is not None)
    c.hydrate(cart)
    say("deck", _n(c.load_deck(cart)), _n(c.save_deck(cart, '{"pages": []}')),
        _n(c.load_deck(cart)))
    say("code", _n(c.save_code(cart, "def _draw():\n    cls(2)\n")))
    say("journal", _n(c.journal(cart, "main.py", "def _draw():\n    cls(3)\n", 0)),
        _n(c.journal({"title": "none"}, "main.py", "", 0)))
    cpx = c.encode_image(2, 2, bytes([5, 6, 7, 8]))
    imgs, err = c.images(cart)
    say("cart image", _n(imgs), err, _n(c.save_image(cart, "pic", cpx)),
        _n(f.decode_image(c.images(cart)[0]["pic"])))

    # -- nav
    nv = ctx.nav
    say("nav", nv.is_system_app(cart), cart in nv.projects())
    say("open_app", nv.open_app("calc"), nv.open_app("nope"), ws.wm.top_kind())
    ws.go_home()
    say("edit", nv.edit(cart, "code"), ws.wm.top_kind())
    ws.go_home()
    say("edit_file", nv.edit_file(cart, "manifest.json"), ws.wm.top_kind())
    ws.go_home()
    say("open_image", nv.open_image("pic", "drawings"), ws.wm.top_kind())
    ws.go_home()
    say("open_text", nv.open_text("missing.md", "docs"), ws.wm.top_kind())
    ws.go_home()
    say("run_script", _n(nv.run_script("docs", "nope.py")))
    ws.go_home()
    nv.text_mode(True)
    t1 = ws.input.text_mode
    nv.text_mode(False)
    say("text_mode", t1, ws.input.text_mode)
    nv.play(cart)
    say("play", ws.wm.top_kind())
    ws.go_home()

    # -- notify
    ctx.notify.achieve("open", "trace")
    say("notify")

    # -- wallpaper
    w = ctx.wallpaper
    say("wallpaper", w.current(), len(w.fills()) > 0, _n(w.id_for({"title": "Wall Z"})))
    say("wallpaper carts", len(w.carts()) >= 0, w.title("fill:nope"),
        w.title(w.id_for(w.carts()[0])) == w.carts()[0].get("title"))
    w.select("fill:black")
    say("selected", w.current(), ws.system.get("wallpaper"))
    w.preview(cv, (0, 0, 32, 24), 0)
    say("copy", _n(w.load_copy()), _n(w.save_copy(px)),
        _n(f.decode_image(w.load_copy()[0])))

    # -- artwork: Paint's open picture, through Paint's model and the role
    a = ctx.artwork
    paint = ws.artwork
    say("artwork", paint.is_paint_app(cart), _n(a.current()), paint.editable(),
        _n(paint.why_read_only()))
    paint.new_doc(32, 24)
    say("artwork new", _n(a.current()), _n(paint.load() is not None))
    say("artwork save", _n(paint.save(bytes(32 * 24), 32, 24)), _n(a.current()))
    paint.open_named("pic", "drawings")
    say("artwork follow", _n(a.follow("drawings", "nope", "x")),
        _n(a.follow("drawings", "pic", "pic2")), _n(a.current()), _n(paint.doc_name()))
    a.follow("drawings", "pic2", "pic")

    # -- the copies of a drawing (runtime/picture_copies.py), over these roles
    pc = PictureCopies(ctx)
    say("copies", _n(pc.usage("pic")), len(pc.targets()) >= 0,
        _n(w.thumbnail(16, 12) is not None))
    say("copies wall", _n(pc.set_wallpaper("pic")), _n(paint.sync_wallpaper()))
    k = list(pc.targets()).index("Trace Cart")
    say("copies attach", _n(pc.attach(k, "pic")), _n(pc.resend({"kind": "game", "index": k}, "pic")),
        _n([r["kind"] for r in pc.usage("pic")]))

    # -- clipboard
    cl = ctx.clipboard
    say("clip empty", repr(cl.text()))
    s0 = cl.seq()
    say("clip put", cl.put_text("hello"), cl.put_text("x" * 4097))
    say("clip", cl.text(), cl.kind(), cl.seq() - s0)

    # -- install
    i = ctx.install
    say("install", i.root() == ws.carts_root, i.writable(), i.can_pick(),
        i.home(), i.net() is ws.cart_net, i.keep() is ws.cart_keep,
        i.pick("x", 1, "h") is None)
    say("lease", i.hold() in (True, False), i.release())
    say("op", i.op(lambda: 7))
    i.rescan()
    folder = cart["path"].replace("\\", "/").rsplit("/", 1)[1]
    found = i.find(folder)
    say("find", i.find("nope.moy"), folder, found is not None and found.get("title"))
    fr = i.free()
    say("free", fr is None or len(fr) == 2)
    say("engine", type(i.runtimes()).__name__, i.memory() is None or len(i.memory()) == 2,
        i.fit("wasm", 0, 0, False), len(i.chip()))

    _c_called(ws.app_abi, c_before)
    uncalled = [r + "." + v for r, v in ROWS if (r + "." + v) not in CALLED]
    say("uncalled", " ".join(uncalled) or "-")
'''


def roles_rows():
    """The role table's (role, verb) rows, in table order."""
    import json
    rows = json.loads((ROOT / "native" / "moy_app" / "roles.json").read_text())
    return [(r["role"], r["verb"]) for r in rows["rows"]]


def _roles_driver():
    return ROLES_DRIVER.replace("@ROWS@", repr(roles_rows()))


def _roles_trace_cpython(tmp_path):
    from runtime import host_app
    from runtime.app_context import NO_STORE, ROLES
    from runtime.picture_copies import PictureCopies
    import contextlib
    import io
    ns = {"ROLES": ROLES, "NO_STORE": NO_STORE, "PictureCopies": PictureCopies}
    exec(_roles_driver(), ns)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ns["roles_trace"](ws)
    return [line[2:] for line in out.getvalue().splitlines()
            if line.startswith("T ")]


ROLES_BOOT = r'''
import sys
import _thread
import time
ROOT = @ROOT@
sys.path.insert(0, ROOT + "/stage")
sys.path.insert(0, ROOT + "/fakes")
DONE = _thread.allocate_lock()
DONE.acquire()


def main():
    try:
        import fake_machine
        sys.modules["machine"] = fake_machine
        import moycore as _real_moycore
        import moycore_shim
        moycore_shim.install(_real_moycore, moycore_shim)
        sys.modules["moycore"] = moycore_shim
        import moybyte_sd
        moybyte_sd._live_mounted = True
        import carts_data
        import moy_runtime

        def _load(self, boot, store):
            carts, root = boot.load_carts(store, carts_data.CARTS_Z,
                                          root=ROOT + "/carts", media="SD")
            self.on_sd = True
            return carts, root, None

        moy_runtime._Storage.load = _load
        moy_runtime.POWER_SAVE_MS = 0
        desk = moy_runtime.run_desktop()
        import moy_loop
        for _ in range(5):
            moy_loop.step()
        from app_context import NO_STORE, ROLES
        from picture_copies import PictureCopies
        g = {"ROLES": ROLES, "NO_STORE": NO_STORE, "PictureCopies": PictureCopies}
        exec(DRIVER, g)
        g["roles_trace"](desk.ws)
        print("DRIVER_DONE")
    except BaseException as exc:             # noqa -- say it, then end the run
        sys.print_exception(exc)
    DONE.release()


DRIVER = @DRIVER@
_thread.stack_size(4 << 20)
_thread.start_new_thread(main, ())
DONE.acquire()
time.sleep_ms(50)
'''


def _roles_trace_board(exe, tmp_path):
    import test_frame_alloc as fa
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import gen_device_carts
    finally:
        sys.path.pop(0)
    fa._stage(tmp_path, exe)
    stage = tmp_path / "stage"
    (stage / "carts_data.mpy").unlink()
    (stage / "carts_data.py").write_text(gen_device_carts.render_packed_module(
        gen_device_carts.build_packed(str(ROOT / "system_carts"))))
    script = tmp_path / "roles_driver.py"
    script.write_text(ROLES_BOOT.replace("@ROOT@", repr(str(tmp_path)))
                      .replace("@DRIVER@", repr(_roles_driver())))
    out = subprocess.run([exe, "-X", "heapsize=64M", str(script)],
                         capture_output=True, text=True, timeout=240,
                         stdin=subprocess.PIPE)
    lines = out.stdout.strip().splitlines()
    assert lines and lines[-1] == "DRIVER_DONE", out.stdout[-4000:] + out.stderr
    return [line[2:] for line in lines if line.startswith("T ")]


ROLES_TRACE = """\
roles artwork carts clipboard damage files install nav notify prefs surface theme wallpaper
damage 1 2 0
surface True True 1 1 False True
pointer True True
glyph drawn
theme night dark default False True True True True
theme set forest light True True True
theme back night dark False
prefs absent None 5
prefs set [1,'two'] [1,'two']
prefs cleared gone
files ready True True
files list [[],None] [0,None]
new_name trace_note None
save ['trace_note',None]
load 'one\\ntwo' None ['one','two']
duplicate ['trace_note_2',None]
rename ['renamed',None]
list ['renamed','trace_note_2']
delete ['renamed',None]
trash [[['docs','renamed']],None]
restore ['renamed',None]
empty_trash ['renamed',None] [None,None] [[],None]
image str [2,2,b4]
cover True 3
stamp int drawings/src True [None,None]
drawing ['pic',None]
history [None,None]
history read [[{ops:[['dot',1,1]],t:'seg'}],None] [[['dot',1,1]],None]
session [True,None] [1,None] None 0 [True,None] [True,None] 2
carts ready True True True a_b_c
carts session [True,None] None
create True None
carts 1 True
deck [None,None] [None,None] ['{"pages": []}',None]
code [['ok',''],None]
journal [1,None] [None,'NO CART']
cart image {} None [None,None] [2,2,b4]
nav False True
open_app True False calc
edit True menu
edit_file True desktop
open_image True artwork
open_text True desktop
run_script [False,"CAN'T READ IT"]
text_mode True False
play desktop
notify
wallpaper moybyte.moy_night True 'moybyte.wall_z'
wallpaper carts True None True
selected fill:black fill:black
copy [None,None] [None,None] [2,2,b4]
artwork False ['drawings','pic'] True ''
artwork new ['drawings','drawing_2'] False
artwork save True ['drawings','drawing_2']
artwork follow False True ['drawings','pic2'] 'pic2'
copies [] True True
copies wall True True
copies attach 'Trace Cart' True ['wall','game']
clip empty ''
clip put True False
clip hello text 1
install True True False None True True True
lease True None
op 7
find None local.trace_cart.moy Trace Cart
free True
engine tuple True None 2
uncalled -
"""


def test_roles_trace_is_the_interface_on_every_vm(tmp_path):
    want = ROLES_TRACE.splitlines()
    py = _roles_trace_cpython(tmp_path / "cpython")
    assert py == want, "the roles trace moved: " + _first_difference(py, want)
    exe = require_unix_mp(
        "moycore", "moy_gfx", "moy_audio", board_model=True,
        why="The app ABI's roles on the boards' object model, over the "
            "T-Deck's own desktop: the net sprint 5 swaps their servers under.")
    (tmp_path / "board").mkdir()
    b32 = _roles_trace_board(exe, tmp_path / "board")
    assert b32 == want, ("the T-Deck desktop diverges: "
                         + _first_difference(b32, want))
