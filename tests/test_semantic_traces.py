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
trace is what proves the C implementation semantics-identical. (The HOST
tier's lua lane -- lupa -- is pinned separately by the sakura/brick-siege
golden parity tests; this file owns the device seam.)

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
"""

import shutil
import subprocess
from pathlib import Path

from unix_mp import require_unix_mp

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
    # BOARDS' one, because this harness models a device build. Staging the
    # host's (which is what it used to do) meant the one suite that drives real
    # input through the real glue was testing the wrong tier's input class.
    shutil.copy(ROOT / "device" / "moybyte" / "input.py", stage / "input.py")
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
