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

    def flush_batch(self):
        self._batch_arr[0] = 4

    def begin_batch(self, sheet, ck, sc, token):
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
             "tri", "trib", "print"):
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
