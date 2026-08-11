"""The #189 libmoy-direct Lua draw verbs, driven through a REAL MicroPython VM.

moy_lua.bind_draw swaps a Lua cart's pix/rect/rectb/line/circ/circb/tri/trib/
print for lua_CFunctions that draw through moy_gfx's exported C API -- never
entering Python. The bar is BYTE-EQUALITY against the lanes those verbs
shadow: the same op drawn A) from Lua through the direct verbs and B) from
Python through the draw gates / moy_gfx kernels (exactly what DeviceCanvas
issues) must produce identical buffers, across camera offsets, clip rects,
pal remaps, float coords and degenerate shapes. A difference here is the
direct verb mangling an argument or missing a state read -- never the kernel,
which is shared by construction.

Skipped unless someone has built the unix port with BOTH usermods staged as
siblings (building MicroPython is not this suite's job):

  mkdir -p firmware/lilygo_t_deck_plus_micropython/.build/usermods_luadraw
  cd firmware/lilygo_t_deck_plus_micropython/.build/usermods_luadraw
  ln -sfn ../../native/moy_gfx moy_gfx && ln -sfn ../../native/moy_lua moy_lua
  cd ../lvgl_micropython/lib/micropython/ports/unix
  make VARIANT=standard BUILD=build-moyluagfx USER_C_MODULES=<abs .build/usermods_luadraw>
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_micropython"
UNIX_MP = (TDECK / ".build" / "lvgl_micropython" / "lib" / "micropython"
           / "ports" / "unix" / "build-moyluagfx" / "micropython")


def test_glue_binds_before_the_cart_executes():
    """bind_draw must run after register() (the trampolines are the odd-shape
    fallbacks) and BEFORE exec(src): the p8 shim captures the draw globals
    into Lua locals at load, so a late bind would leave the shim on the slow
    trampolines forever. Source-order check, no VM needed."""
    src = (TDECK / "modules" / "moy_lua_glue.py").read_text()
    bind = src.index("bind_draw")
    assert src.index("moy_lua.register(") < bind < src.index('exec(src, "@cart")')


# The A/B driver run inside the unix MicroPython. Prints one line per case:
# "<name> <hexA> <hexB>"; the host asserts hexA == hexB per case (so a failure
# names the case, not just "buffers differ").
DRIVER = r"""
import moy_gfx, moy_lua
from array import array

ST_CAM_X, ST_CAM_Y = 0, 1
ST_CX0, ST_CY0, ST_CX1, ST_CY1 = 2, 3, 4, 5
ST_W, ST_H = 6, 7
ST_FONT_SCALE = 8
ST_LEN = 14
GATE_RECT, GATE_RECTB, GATE_PRINT, GATE_PIX = 0, 1, 2, 3

W, H = 96, 64


class MockCanvas:
    def __init__(self):
        self._batch_arr = array("h", bytearray(2 * (4 + 4 * 64)))
        self._batch_arr[0] = 4
        self.upcalls = []              # stage-1: the batch upcalls that DIDN'T die

    def flush_batch(self):
        self.upcalls.append(("flush",))
        self._batch_arr[0] = 4

    def begin_batch(self, sheet, ck, sc, token):
        self.upcalls.append(("begin", ck, sc, token))
        self._batch_arr[0] = 4         # mock flush: pending foreign quads drop
        self._batch_arr[1] = ck
        self._batch_arr[2] = sc
        self._batch_arr[3] = token


def make_side():
    buf = bytearray(W * H * 2)
    st = array("i", bytearray(4 * ST_LEN))
    # A non-identity pal table (as _sync_gate_pal writes after a pal() remap):
    # distinct 565 words, index 9 remapped onto 3's word.
    pal = array("H", ((i * 517 + 11) & 0xFFFF for i in range(64)))
    pal[9] = pal[3]
    font = bytearray(8 * 96)
    for g in range(96):                     # every glyph distinct + nonzero
        font[g * 8 + (g % 8)] = 1 << (g % 8)
    canvas = MockCanvas()
    ctx = moy_gfx.make_draw_ctx(canvas, st, pal, canvas._batch_arr, font, 32)
    st[ST_W] = W
    st[ST_H] = H
    st[ST_CX1] = W
    st[ST_CY1] = H
    st[ST_FONT_SCALE] = 1
    ctx.set_buf(buf)
    return buf, st, pal, canvas, ctx


buf_a, st_a, pal_a, canvas_a, ctx_a = make_side()
buf_b, st_b, pal_b, canvas_b, ctx_b = make_side()

# --- side A: the Lua direct verbs ------------------------------------------
moy_lua.init(canvas_a, None, canvas_a._batch_arr, 0x7A11)
for name in ("pix", "rect", "rectb", "line", "circ", "circb",
             "tri", "trib", "print", "sspr", "tline"):
    moy_lua.register(name, lambda *a: None)
assert moy_lua.bind_draw(ctx_a) is True

# --- side B: the lanes DeviceCanvas issues ---------------------------------
never = []
g_rect = moy_gfx.make_draw_gate(ctx_b, GATE_RECT, lambda *a: never.append(a))
g_rectb = moy_gfx.make_draw_gate(ctx_b, GATE_RECTB, lambda *a: never.append(a))
g_print = moy_gfx.make_draw_gate(ctx_b, GATE_PRINT, lambda *a: never.append(a))
g_pix = moy_gfx.make_draw_gate(ctx_b, GATE_PIX, lambda *a: never.append(a))


def col(ci):
    return pal_b[int(ci) & 63]


def b_shape(fn, *args):
    fn(buf_b, W, H, *args, st_b[ST_CAM_X], st_b[ST_CAM_Y],
       st_b[ST_CX0], st_b[ST_CY0], st_b[ST_CX1], st_b[ST_CY1])


def b_line(x0, y0, x1, y1, c):
    b_shape(moy_gfx.line, int(x0), int(y0), int(x1), int(y1), col(c))


CASES = [
    ("rect", "rect(5, 6, 20, 10, 9)",
     lambda: g_rect(5, 6, 20, 10, 9)),
    ("rect_float", "rect(5.7, 6.2, 20.9, 10.1, 3.8)",
     lambda: g_rect(5.7, 6.2, 20.9, 10.1, 3.8)),
    ("rect_neg", "rect(-4, -3, 10, 8, 5)",
     lambda: g_rect(-4, -3, 10, 8, 5)),
    ("rect_off", "rect(200, 200, 10, 10, 5)",
     lambda: g_rect(200, 200, 10, 10, 5)),
    ("rect_zero", "rect(10, 10, 0, 5, 5) rect(12, 12, -3, 5, 5)",
     lambda: (g_rect(10, 10, 0, 5, 5), g_rect(12, 12, -3, 5, 5))),
    ("rectb", "rectb(30, 5, 12, 9, 7)",
     lambda: g_rectb(30, 5, 12, 9, 7)),
    ("pix", "pix(50, 20, 8) pix(-1, 0, 8) pix(95, 63, 66)",
     lambda: (g_pix(50, 20, 8), g_pix(-1, 0, 8), g_pix(95, 63, 66))),
    ("line", "line(2, 60, 40, 33, 4) line(40, 1, 40, 20, 6)",
     lambda: (b_line(2, 60, 40, 33, 4), b_line(40, 1, 40, 20, 6))),
    ("circ", "circ(70, 30, 9, 2) circ(70, 30, 0, 4)",
     lambda: (b_shape(moy_gfx.circ, 70, 30, 9, col(2)),
              b_shape(moy_gfx.circ, 70, 30, 0, col(4)))),
    ("circb", "circb(20, 40, 7, 12)",
     lambda: b_shape(moy_gfx.circb, 20, 40, 7, col(12))),
    ("tri", "tri(60, 5, 90, 12, 66, 40, 10)",
     lambda: b_shape(moy_gfx.tri, 60, 5, 90, 12, 66, 40, col(10))),
    ("tri_degen", "tri(10, 10, 10, 10, 10, 10, 6)",
     lambda: b_shape(moy_gfx.tri, 10, 10, 10, 10, 10, 10, col(6))),
    ("trib", "trib(12, 50, 30, 44, 22, 62, 14)",
     lambda: (b_line(12, 50, 30, 44, 14), b_line(30, 44, 22, 62, 14),
              b_line(22, 62, 12, 50, 14))),
    ("print", 'print("Hi moy!", 4, 4, 7)',
     lambda: g_print("Hi moy!", 4, 4, 7)),
    ("print_scale_ignored", 'print("Zz", 40, 50, 6, 3)',
     lambda: g_print("Zz", 40, 50, 6, 3)),
    ("pal_remap", "rect(80, 55, 8, 6, 9)",         # 9 aliases 3's 565 word
     lambda: g_rect(80, 55, 8, 6, 9)),
]


def with_state(cam=(0, 0), clip=None):
    for st in (st_a, st_b):
        st[ST_CAM_X], st[ST_CAM_Y] = cam
        c = clip if clip is not None else (0, 0, W, H)
        st[ST_CX0], st[ST_CY0], st[ST_CX1], st[ST_CY1] = c


for name, lua_src, py_fn in CASES:
    moy_lua.exec(lua_src)
    py_fn()
    print(name, bytes(buf_a).hex(), bytes(buf_b).hex())

# The same matrix again under a camera offset AND a tight clip rect.
with_state(cam=(7, -3), clip=(10, 8, 70, 50))
for name, lua_src, py_fn in CASES:
    moy_lua.exec(lua_src)
    py_fn()
    print("camclip_" + name, bytes(buf_a).hex(), bytes(buf_b).hex())
with_state()

assert not never, never                    # no gate ever fell back

# --- stage-1 (#67): the C-side sprite-batch protocol ------------------------
# Side A drives Lua spr() against a ctx with a registered batch source: run
# breaks stamp the header in C and flushes go through moy_gfx_capi_flush_batch
# -- ZERO begin_batch/flush_batch upcalls, asserted on the mock. Side B is the
# exact call DeviceCanvas.flush_batch makes for a run: blit_batch in array
# mode with the same header/camera/clip/pal/palt. Byte-equality per scene.

SW, SH = 128, 256                      # MOY_SHEET_W x MOY_SHEET_H (SPEC 3.2)
sheet_px = bytearray(SW * SH)
for sy in range(SH):
    for sx in range(SW):
        sheet_px[sy * SW + sx] = (sx * 7 + sy * 3 + (sx // 8)) & 63
palt_a = bytearray(64)
palt_b = bytearray(64)
ctx_a.set_batch_src(sheet_px, SW, SH, palt_a)

arr_b = canvas_b._batch_arr


def b_batch(items, ck=-1, scale=1):
    # Side B: the flush_batch lane -- chunked at the queue's 64-quad capacity
    # exactly where side A's full-queue break lands.
    for i in range(0, len(items), 64):
        chunk = items[i:i + 64]
        k = 4
        for (t, x, y, fl) in chunk:
            arr_b[k] = t
            arr_b[k + 1] = x
            arr_b[k + 2] = y
            arr_b[k + 3] = fl
            k += 4
        arr_b[0] = k
        moy_gfx.blit_batch(buf_b, W, H, arr_b, sheet_px, SW, SH,
                           pal_b, palt_b, ck, scale,
                           st_b[ST_CAM_X], st_b[ST_CAM_Y],
                           st_b[ST_CX0], st_b[ST_CY0],
                           st_b[ST_CX1], st_b[ST_CY1])
        arr_b[0] = 4


def lua_sprs(items, ck=None, scale=None):
    calls = []
    for (t, x, y, fl) in items:
        if ck is None:
            calls.append("spr(%d, %d, %d)" % (t, x, y))
        elif scale is None:
            calls.append("spr(%d, %d, %d, %d)" % (t, x, y, ck))
        else:
            calls.append("spr(%d, %d, %d, %d, %d, %d)" % (t, x, y, ck, scale, fl))
    calls.append("rect(0, 0, 0, 0, 0)")   # zero-size: order-rule flush, no pixels
    return " ".join(calls)


RUN_A = [(1, 5, 5, 0), (2, 14, 5, 0), (3, 23, 5, 0), (17, 5, 40, 0)]
RUN_FLIPS = [(4, 40, 8, 0), (4, 50, 8, 1), (4, 60, 8, 2), (4, 70, 8, 3)]
RUN_BIG = [((i * 5) & 127, (i * 9) % (W + 16) - 8, (i * 7) % (H + 16) - 8, i & 3)
           for i in range(100)]

BATCH_SCENES = [
    ("batch_run", lambda: b_batch(RUN_A),
     lua_sprs(RUN_A)),
    ("batch_colorkey", lambda: b_batch(RUN_A, ck=9),
     lua_sprs([i[:3] + (0,) for i in RUN_A], ck=9)),
    ("batch_scale_flip", lambda: b_batch(RUN_FLIPS, ck=3, scale=2),
     lua_sprs(RUN_FLIPS, ck=3, scale=2)),
    ("batch_lone", lambda: b_batch([(9, 33, 33, 0)]),
     lua_sprs([(9, 33, 33, 0)])),
    ("batch_full_queue", lambda: b_batch(RUN_BIG, ck=1, scale=1),
     lua_sprs(RUN_BIG, ck=1, scale=1)),
    ("batch_ck_break", lambda: (b_batch(RUN_A[:2], ck=2), b_batch(RUN_A[2:], ck=5)),
     lua_sprs([i[:3] + (0,) for i in RUN_A[:2]], ck=2) + " " +
     lua_sprs([i[:3] + (0,) for i in RUN_A[2:]], ck=5)),
]

for name, py_fn, lua_src in BATCH_SCENES:
    moy_lua.exec(lua_src)
    py_fn()
    print(name, bytes(buf_a).hex(), bytes(buf_b).hex())

# ...and the same under a camera offset + tight clip (sprites clip/offset too).
with_state(cam=(9, -4), clip=(12, 10, 80, 52))
for name, py_fn, lua_src in BATCH_SCENES:
    moy_lua.exec(lua_src)
    py_fn()
    print("camclip_" + name, bytes(buf_a).hex(), bytes(buf_b).hex())
with_state()

# palt: index 20 transparent -- registered buffers are read LIVE, no re-bind.
palt_a[20] = 1
palt_b[20] = 1
moy_lua.exec(lua_sprs(RUN_A))
b_batch(RUN_A)
print("batch_palt", bytes(buf_a).hex(), bytes(buf_b).hex())
palt_a[20] = 0
palt_b[20] = 0

# The whole batch section ran with ZERO begin_batch/flush_batch upcalls.
assert canvas_a.upcalls == [], canvas_a.upcalls
bstats = moy_lua.batch_stats()
assert bstats[0] > 0 and bstats[1] > 0 and bstats[3] == 0, bstats

# A FOREIGN pending run (a Python writer's token) must take the upcall lane:
# preload a token-0 run, then a Lua spr -- begin_batch upcall, bup counted.
arr_a = canvas_a._batch_arr
arr_a[1] = -1
arr_a[2] = 1
arr_a[3] = 0
arr_a[4] = 7
arr_a[5] = 90
arr_a[6] = 55
arr_a[7] = 0
arr_a[0] = 8
moy_lua.exec("spr(9, 80, 55, -1, 1, 0) rect(0, 0, 0, 0, 0)")
b_batch([(9, 80, 55, 0)])
print("batch_foreign", bytes(buf_a).hex(), bytes(buf_b).hex())
assert canvas_a.upcalls == [("begin", -1, 1, 0x7A11)], canvas_a.upcalls
assert moy_lua.batch_stats()[3] >= 1, moy_lua.batch_stats()

# --- stage-1b (#67): sspr + tline direct ------------------------------------
# Side A: the Lua verbs against the registered sheet/map sources. Side B: the
# exact moy_gfx kernel calls DeviceCanvas.sspr/tline make. Byte-equality, incl.
# the default-arg forms and under camera+clip.

MW, MH = 16, 12
map_cells = bytearray(MW * MH)
for my in range(MH):
    for mx in range(MW):
        map_cells[my * MW + mx] = ((mx + my * 3) % 9)     # 0 = empty cells too
ctx_a.set_map_src(map_cells, MW, MH)


def b_sspr(sx, sy, sw, sh, dx, dy, ddw, ddh, ck=-1, flip=0):
    moy_gfx.sspr(buf_b, W, H, sheet_px, SW, SH, sx, sy, sw, sh,
                 dx, dy, ddw, ddh, ck, flip, pal_b, palt_b,
                 st_b[ST_CAM_X], st_b[ST_CAM_Y],
                 st_b[ST_CX0], st_b[ST_CY0], st_b[ST_CX1], st_b[ST_CY1])


def b_tline(x0, y0, x1, y1, u, v, du, dv, ck=-1):
    moy_gfx.tline(buf_b, W, H, map_cells, MW, MH, sheet_px, SW, SH,
                  x0, y0, x1, y1, u, v, du, dv, ck, pal_b, palt_b,
                  st_b[ST_CAM_X], st_b[ST_CAM_Y],
                  st_b[ST_CX0], st_b[ST_CY0], st_b[ST_CX1], st_b[ST_CY1])


TEX_CASES = [
    ("sspr_plain", "sspr(4, 8, 16, 16, 10, 10)",
     lambda: b_sspr(4, 8, 16, 16, 10, 10, 16, 16)),
    ("sspr_stretch", "sspr(0, 0, 8, 8, 40, 5, 24, 20)",
     lambda: b_sspr(0, 0, 8, 8, 40, 5, 24, 20)),
    ("sspr_shrink_ck", "sspr(0, 16, 32, 32, 60, 30, 12, 9, 5)",
     lambda: b_sspr(0, 16, 32, 32, 60, 30, 12, 9, 5)),
    ("sspr_flip", "sspr(8, 8, 12, 10, 4, 44, 18, 15, -1, 3)",
     lambda: b_sspr(8, 8, 12, 10, 4, 44, 18, 15, -1, 3)),
    ("sspr_offcanvas", "sspr(0, 0, 16, 16, -6, 56, 20, 20)",
     lambda: b_sspr(0, 0, 16, 16, -6, 56, 20, 20)),
    ("tline_h", "tline(0, 20, 95, 20, 0, 131072, 65536, 0)",
     lambda: b_tline(0, 20, 95, 20, 0, 131072, 65536, 0)),
    ("tline_diag", "tline(5, 60, 90, 25, 262144, 0, 49152, 32768)",
     lambda: b_tline(5, 60, 90, 25, 262144, 0, 49152, 32768)),
    ("tline_wrap_ck", "tline(0, 35, 95, 45, -655360, 917504, 98304, -16384, 3)",
     lambda: b_tline(0, 35, 95, 45, -655360, 917504, 98304, -16384, 3)),
]

canvas_a.upcalls = []
for name, lua_src, py_fn in TEX_CASES:
    moy_lua.exec(lua_src)
    py_fn()
    print(name, bytes(buf_a).hex(), bytes(buf_b).hex())
with_state(cam=(6, -2), clip=(8, 6, 82, 54))
for name, lua_src, py_fn in TEX_CASES:
    moy_lua.exec(lua_src)
    py_fn()
    print("camclip_" + name, bytes(buf_a).hex(), bytes(buf_b).hex())
with_state()
assert canvas_a.upcalls == [], canvas_a.upcalls   # all direct, zero upcalls

# Un-registering the map drops tline (and only tline) back to its trampoline.
falls0 = moy_lua.draw_stats()[6]
ctx_a.set_map_src(None)
moy_lua.exec("tline(0, 3, 20, 3, 0, 0, 65536, 0)")
assert moy_lua.draw_stats()[6] == falls0 + 1, moy_lua.draw_stats()
ctx_a.set_map_src(map_cells, MW, MH)

# Non-parity semantics, asserted directly:
# print of a non-UTF-8 byte draws glyph 0 and advances one cell (SPEC.md 6's
# "draws nothing for that byte" holds for the REAL font, whose glyph 0 is
# blank; this synthetic font makes the mapping observable instead).
moy_lua.exec('rect(0, 0, 96, 64, 0) print("\255", 10, 10, 7)')
i = 2 * (10 * W + 10)
expect = pal_a[7]
got = buf_a[i] | (buf_a[i + 1] << 8)
assert got == expect, (got, expect)

# An odd arity falls back to the Python trampoline (the closure registered
# before bind_draw), whose None return reads as nil in Lua.
reads = []
moy_lua.register("probe", lambda v: reads.append(v))
moy_lua.exec("probe(pix(1) or -1)")
assert reads == [-1], reads

stats = moy_lua.draw_stats()
assert stats[0] > 0 and stats[1] > 0 and stats[2] > 0, stats
assert stats[6] >= 1, stats               # the fallback trip above

moy_lua.close()
print("DRIVER_DONE")
"""


def test_direct_verbs_match_the_python_lanes(tmp_path):
    if not UNIX_MP.exists():
        pytest.skip("no ports/unix MicroPython built with moy_gfx+moy_lua "
                    "(see this file's docstring for the two commands)")
    script = tmp_path / "driver.py"
    script.write_text(DRIVER)
    out = subprocess.run([str(UNIX_MP), str(script)], capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr or out.stdout
    lines = out.stdout.strip().splitlines()
    assert lines[-1] == "DRIVER_DONE", out.stdout
    bad = []
    for line in lines[:-1]:
        name, ha, hb = line.split()
        if ha != hb:
            diff = sum(1 for a, b in zip(bytes.fromhex(ha), bytes.fromhex(hb))
                       if a != b)
            bad.append("%s (%d bytes differ)" % (name, diff))
    assert not bad, "direct verbs diverge from the Python lanes: " + ", ".join(bad)
