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
  * a verb libmoy does not bind can be REGISTERED on top of its table, which
    is what lets moybyte's superset (layers, view) ride one runtime instead of
    needing a second.

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
import sys
sys.path.insert(0, "@RUNTIME@")          # for lua_ext, staged by build.sh
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

moycore.run_begin(fb, W, H, None, sheet, None, 0, 0, snap, aq, pm, {"k": "v"})
print("START", moycore.load(SRC, "@cart"))
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
sp = moycore.tick_split()
print("SPLIT", len(sp), 1 if (sp[0] >= 0 and sp[1] >= 0) else 0,
      1 if (sp[0] + sp[1]) > 0 else 0)
print("PMEM", moycore.pmem_image(pm), pm[0])
moycore.close()
print("CLOSED", moycore.active())
# The SRAM floor knob and the census behind it. Off-board there is one region,
# so the knob reports the compiled default and the census is None -- but the
# NAMES have to be there, because the caller (run_desktop) sets the floor
# unconditionally on both runtimes and a missing name is what left moycore at
# 48KB on the S3 while moy_lua went to 24.
print("FLOOR", moycore.set_sram_floor(24), moycore.set_sram_floor(1),
      moycore.set_sram_floor(9999))
print("CENSUS", moycore.alloc_stats())

# view and background are CORE upstream now, so libmoy answers them and the
# host READS the result instead of being called -- zero crossings for view.
moycore.run_begin(fb, W, H, None, sheet, None, 0, 0, snap, aq, None, None)
print("VIEW0", moycore.view())
print("VIEWLOAD", moycore.load(
    "function _init() view(128, 120) background(5) end\n"
    "function _update(dt) end\n"
    "function _draw() rect(0, 0, 2, 2, 9) end\n", "@view"))
moycore.tick(0.03125)
print("VIEW1", moycore.view())
print("BG", 1 if fb[(63 * W + 95) * 2] or fb[(63 * W + 95) * 2 + 1] else 0)
moycore.close()

# The superset rides the same runtime: register a Python-backed verb, then a
# cart that calls it.
moycore.run_begin(fb, W, H, None, sheet, None, 0, 0, snap, aq, None, None)
seen = []
moycore.register("make_layer", lambda w, h: (seen.append((w, h)), 7)[1])
moycore.register("draw_layer", lambda h, x, y: seen.append((h, x, y)))
print("EXT", moycore.load(
    "function _init() L = make_layer(9, 5) end\n"
    "function _update(dt) end\n"
    "function _draw() cls(0) draw_layer(L, 1, 2) end\n", "@ext"))
moycore.tick(0.03125)
print("EXTCALLS", seen, moycore.get_global("L"))
moycore.close()

# OBJECT-valued verbs ride the shared prelude, not the trampoline. This is the
# real runtime/lua_ext.py, imported and executed -- not a transcription of it.
from lua_ext import PRELUDE_TABLE, PRELUDE_HANDLES, install_handles

class _Layer:
    def __init__(self, w, h):
        self.wh = (w, h)
    def spr(self, *a):
        calls.append(("spr",) + a)
    def cls(self, c):
        calls.append(("cls", c))

class _Img:
    pass

calls = []
_img = _Img()
NS = {"make_layer": lambda w, h: (calls.append(("new", w, h)), _Layer(w, h))[1],
      "draw_layer": lambda l, x, y: calls.append(("draw", l.wh, x, y)),
      "image": lambda n: _img if n == "bg" else None,
      "table": lambda n: 77}
moycore.run_begin(fb, W, H, None, sheet, None, 0, 0, snap, aq, None, None)
moycore.register("moy_table_verb", NS["table"])
install_handles(NS, moycore.register)
print("PRE", moycore.exec(PRELUDE_TABLE + PRELUDE_HANDLES, "prelude"))
print("OBJ", moycore.load(
    "function _init()\n"
    "  L = make_layer(9, 5)\n"
    "  B = image('bg')\n"
    "  MISS = image('nope')\n"
    "  T = table('scores')\n"
    "  N = #({1,2,3})\n"          # the table LIBRARY must survive the graft
    "end\n"
    "function _update(dt) end\n"
    "function _draw()\n"
    "  L:cls(3) L:spr(B, 1, 2) L:spr(9, 1, 2, -1, 2, 1) draw_layer(L, 5, 6)\n"
    "end\n", "@obj"))
moycore.tick(0.03125)
print("OBJCALLS", calls)
print("OBJGLOBALS", moycore.get_global("T"), moycore.get_global("N"),
      moycore.get_global("MISS"))
moycore.close()

# time() must ADVANCE INSIDE a tick. Input is frozen for the frame on purpose;
# a clock bundled into that same snapshot is not a clock, and the cart that
# proves it is Bench Lua -- it grows a batch until the batch costs TARGET_MS,
# measured with time(), so against a frozen clock it doubles forever (on glass:
# a purple screen and "cls k=32768" climbing).
moycore.run_begin(fb, W, H, None, None, None, 0, 0, snap, aq, None, None)
snap[moycore.SNAP_TIME_MS] = 5000
print("TLOAD", moycore.load(
    "function _update(dt)\n"
    "  T0 = time()\n"
    "  local s = 0\n"
    "  for i = 1, 1500000 do s = s + i % 7 end\n"
    "  T1 = time()\n"
    "end\n"
    "function _draw() end\n", "@clock"))
moycore.tick(0.03125)
_t0, _t1 = moycore.get_global("T0"), moycore.get_global("T1")
print("CLOCK", 1 if _t0 >= 5000 else 0, 1 if _t1 > _t0 else 0)
moycore.close()

# The p8 shim's masked map walk (#66 M0). A 4x1 strip of cells with distinct
# flag bytes, drawn under three masks; each surviving cell stamps one 8x8 tile.
MAPW, MAPH = 4, 1
cells = bytearray(MAPW * MAPH)
cells[0] = 0 + 1        # tile 0: p8 NEVER draws it, whatever its flags say
cells[1] = 5 + 1        # tile 5: gff 0x01
cells[2] = 6 + 1        # tile 6: gff 0x02
cells[3] = 0            # empty cell
solid = bytearray(128 * 256)
for i in range(len(solid)):
    solid[i] = 9        # every tile fully opaque colour 9

for i in range(len(fb)):
    fb[i] = 0
moycore.run_begin(fb, W, H, None, solid, cells, MAPW, MAPH, snap, aq, None, None)
print("MASKPRESENT", moycore.exec(
    "P = (__moy_map_masked ~= nil) and (__moy_map_flags ~= nil)", "@probe"),
    moycore.get_global("P"))
print("MASKLOAD", moycore.load(
    "function _init() __moy_map_flags('00000000000102') end\n"
    "function _update(dt) end\n"
    "function _draw() end\n", "@mask"))


def _walk(mask):
    moycore.exec("cls(0) R = __moy_map_masked(0, 0, 0, 0, 4, 1, %d)" % mask,
                 "@walk")
    # PIXELS, not bytes: a 565 pixel is two bytes and a palette entry may have
    # a zero half, so counting bytes counts some pixels once and some twice.
    n = 0
    for i in range(0, len(fb), 2):
        if fb[i] or fb[i + 1]:
            n += 1
    return moycore.get_global("R"), n


print("MASK0", _walk(0))        # no mask: tiles 5 and 6 draw, tile 0 never
print("MASK1", _walk(1))        # gff bit 0: tile 5 only
print("MASK2", _walk(2))        # gff bit 1: tile 6 only
print("MASK4", _walk(4))        # nothing carries bit 2
moycore.close()

# A cart with NO sheet and NO map -- a brand-new project. Every sheet/map verb
# must survive it, because on this side a NULL deref is a board reset.
for i in range(len(fb)):
    fb[i] = 0
moycore.run_begin(fb, W, H, None, None, None, 0, 0, snap, aq, None, None)
print("BARE", moycore.load(
    "function _update(dt) end\n"
    "function _draw()\n"
    "  spr(1, 0, 0) sspr(0, 0, 8, 8, 0, 0)\n"
    "  map(0, 0) tline(0, 0, 8, 8, 0, 0, 65536, 0)\n"
    "  mset(1, 1, 3) X = mget(1, 1)\n"
    "end\n", "@bare"))
print("BARETICK", moycore.tick(0.03125))
nz = 0
for b in fb:
    if b:
        nz += 1
print("BAREPX", nz, moycore.get_global("X"))
moycore.close()

# A cart that raises must come back as text, with the VM still recoverable.
BAD = "function _update(dt) error('boom') end\nfunction _draw() end\n"
moycore.run_begin(fb, W, H, None, None, None, 0, 0, snap, aq, None, None)
print("START2", moycore.load(BAD, "@bad"))
print("ERR", moycore.tick(0.03125))
moycore.close()
'''


def _run():
    src = DRIVER.replace("@RUNTIME@", os.path.join(ROOT, "runtime"))
    p = subprocess.run([MP, "-c", src], capture_output=True, text=True,
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

    # The tick's two halves, in microseconds. The loop times update() and
    # draw() to get its logic/render split and moycore runs BOTH inside
    # update(), so without this the diag reads `logic = the whole frame,
    # render = 0` -- which against every logic/render pair recorded since #67
    # looks like logic doubling. Both halves non-negative and something
    # measurable in the pair; the actual durations are a machine's business.
    assert by["SPLIT"][1:] == ["2", "1", "1"], \
        "tick_split must report both halves of the last tick: %s" % out

    # pmem: 41 from _init plus one per frame, and the dirty flag armed.
    assert by["PMEM"][1] == "True" and by["PMEM"][2] == "45", out
    assert by["CLOSED"][1] == "False", out

    # The SRAM-floor knob exists and clamps. moycore shipped without it while
    # run_desktop lowered moy_lua's 48KB -> 24KB at boot, so a cart that moved
    # to the new runtime kept the high floor -- on the S3's 269KB internal heap
    # that is the ~97%-PSRAM case the SRAM-first allocator exists to avoid, and
    # nothing reported it. Off-board this build has one region, so the values
    # are the compiled default; what is under test is that the NAME is there.
    assert by["FLOOR"][1:] == ["48", "48", "48"], \
        "set_sram_floor missing or not clamping: %s" % out
    assert by["CENSUS"][1] == "None", out

    # view/background reached the cart with no trampoline registered for them.
    assert by["VIEW0"][1] == "None", out
    assert by["VIEWLOAD"][1] == "None", out
    assert by["VIEW1"][1:] == ["(128,", "120)"], \
        "libmoy did not record the cart's view() for the host to read: %s" % out
    assert by["BG"][1] == "1", \
        "background() did not clear -- libmoy owns that when no host takes it: %s" % out

    # The superset registers onto the SAME runtime and the cart calls it.
    assert by["EXT"][1] == "None", out
    assert "EXTCALLS [(9, 5), (7, 1, 2)] 7" in out, \
        "a registered verb did not reach Python (or its return did not reach Lua): %s" % out

    # Object-valued verbs. A trampoline cannot marshal a Layer, so before the
    # shared prelude reached this runtime a cart calling make_layer() got
    # "unsupported value" and fell back to the OLD Lua runtime -- which is what
    # sakura_lua, brick_siege_lua, ray_lua and bullet_storm all did, silently,
    # while every test passed. These pins are the ones that would have said so.
    assert by["PRE"][1] == "None", out
    assert by["OBJ"][1] == "None", \
        "the prelude did not define make_layer/image for the cart: %s" % out
    assert ("OBJCALLS [('new', 9, 5), ('cls', 3), "
            "('spr', <_Img object>, 1, 2), ('spr', 9, 1, 2, -1, 2, 1), "
            "('draw', (9, 5), 5, 6)]" in out.replace(
                out[out.index("<_Img"):out.index(">", out.index("<_Img")) + 1],
                "<_Img object>")), \
        "layer/image handles did not reach the Python objects: %s" % out
    # table() rides Lua's table LIBRARY as __call (#164), so both work.
    assert by["OBJGLOBALS"][1:] == ["77", "3", "None"], \
        "the table graft or the missing-image nil regressed: %s" % out

    # time() reads the host's frame base AND advances within the tick. The
    # snapshot freezes INPUT for a frame deliberately; freezing the clock with
    # it made every in-frame measurement read zero, which is not a subtle
    # failure -- Bench Lua grows a batch until it costs TARGET_MS, so it grew
    # without bound and painted a purple screen with "cls k=32768" climbing.
    assert by["TLOAD"][1] == "None", out
    assert by["CLOCK"][1:] == ["1", "1"], \
        "time() must carry the host's base AND advance inside a tick: %s" % out

    # The p8 shim's masked map walk. moy_lua has had this since #66 M0 and
    # moycore did not, so a ported p8 cart moving to the new runtime silently
    # dropped back to the shim's LUA cell loop -- 4.5ms of celeste's S3 render,
    # measured by difference. "One runtime" has to mean the fast one, so the
    # walk crossed; these are the semantics it has to keep.
    assert by["MASKPRESENT"][1:] == ["None", "True"], \
        "the shim probes these by name; without them it takes its Lua loop: %s" % out
    assert by["MASKLOAD"][1] == "None", out
    # Each drawn cell is one opaque 8x8 tile = 64 pixels. tile 0 NEVER draws
    # (p8 semantics), so an unmasked walk draws 2 of the 4 cells, not 3.
    assert by["MASK0"][1:] == ["(True,", "128)"], \
        "unmasked walk: tiles 5 and 6, and NOT p8's tile 0: %s" % out
    assert by["MASK1"][1:] == ["(True,", "64)"], out
    assert by["MASK2"][1:] == ["(True,", "64)"], out
    assert by["MASK4"][1:] == ["(True,", "0)"], \
        "a mask nothing carries must draw nothing, not everything: %s" % out

    # A console with no sheet and no map is a legal console (a brand-new
    # project), and every sheet/map verb has to survive it: libmoy took those
    # by pointer and dereferenced without asking, so `spr(0,0,0)` in an empty
    # cart was a segfault -- on a board, a reset with no message, from the two
    # lines a beginner types first. Fixed upstream, vendored, pinned here.
    assert by["BARE"][1] == "None" and by["BARETICK"][1] == "None", out
    assert by["BAREPX"][1] == "0", "an absent sheet drew something: %s" % out
    assert by["BAREPX"][2] == "-1", \
        "an absent map must read as empty, not junk: %s" % out

    # A cart error is text, not an exception, and the module recovers.
    assert by["START2"][1] == "None", out
    assert by["ERR"][1] != "None" and "boom" in out, \
        "a raising _update must return its message: %s" % out
