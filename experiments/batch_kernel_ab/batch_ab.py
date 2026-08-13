"""Does blit_batch beat N x moy_spr, and by how much? (plan 6.10, step one)

The whole batching rung rests on WHERE the ~6us/sprite goes. The bench numbers
that suggested "the kernel" were two rows of a table taken on different carts
and different runs, which is a reconstruction, not a measurement. So: the same
N sprites, the same canvas, the same sheet, drawn both ways in one process.

  A  one moy_gfx.blit_batch of N quads     -- the gate's coalesced path
  B  N x libmoy moy_spr, via moycore       -- what a Lua cart does today

B is timed with moycore.tick_split(), which reports the DRAW half of the tick in
microseconds, so the Lua loop's own overhead (a few hundred ns per iteration) is
the only thing in B that is not the kernel. A is timed with ticks_us around a
single call, so its Python overhead is one call, not N.

If B/A lands near 1.0 the win was the host crossing all along, libmoy has no
problem to solve, and the rung does not exist.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join("/home/nikola/Documents/Work/moybyte", "tests"))
import test_moycore_loop as t                                    # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
REPS = 9

DRIVER = r'''
import moy_gfx, moycore
from array import array
import time as _t

N = @N@
REPS = @REPS@
W, H = 320, 240
SHW, SHH = 128, 256

def us():
    return _t.ticks_us()

# One sheet, one wire palette, shared by both paths.
sheet = bytearray(SHW * SHH)
for i in range(len(sheet)):
    sheet[i] = (i * 7 + (i >> 7) * 3) & 63
wire = array("H", [(((i * 1013) | 1) & 0xFFFF) for i in range(64)])
palt = bytearray(64)                      # NOTHING transparent, both paths

# The N sprites BOTH paths draw: same tiles, same positions, scale 1, ck 0.
TILES = [(i & 7, (i * 37) % 300, (i * 53) % 220) for i in range(N)]

# -- A: one blit_batch -------------------------------------------------------
fb = bytearray(W * H * 2)
a = array("h", bytearray(2 * (4 + 4 * (N + 2))))
a[1] = -1         # colorkey: none
a[2] = 1          # scale
a[3] = 0          # token: the python writer
k = 4
for tile, x, y in TILES:
    a[k] = tile; a[k + 1] = x; a[k + 2] = y; a[k + 3] = 0
    k += 4
a[0] = k

best_a = 1 << 30
for r in range(REPS):
    t0 = us()
    moy_gfx.blit_batch(fb, W, H, a, sheet, SHW, SHH, wire, palt,
                       -1, 1, 0, 0, 0, 0, W, H)
    d = _t.ticks_diff(us(), t0)
    if d < best_a:
        best_a = d

# -- B: N x moy_spr through moycore's Lua ------------------------------------
fb2 = bytearray(W * H * 2)
snap = array("i", bytearray(4 * moycore.SNAP_LEN))
aq = array("h", bytearray(2 * 64))
moycore.run_begin(fb2, W, H, wire, sheet, None, 0, 0, snap, aq, None, None)
src = ("local T = {}\n"
       "for i = 0, %d do T[i] = { i & 7, (i * 37) %% 300, (i * 53) %% 220 } end\n"
       "function _update(dt) end\n"
       "function _draw()\n"
       "  for i = 0, %d do local q = T[i] spr(q[1], q[2], q[3], -1, 1, 0) end\n"
       "end\n" % (N - 1, N - 1))
err = moycore.load(src, "@ab")
best_b = 1 << 30
for r in range(REPS):
    moycore.tick(0.016)
    d = moycore.tick_split()[1]
    if d < best_b:
        best_b = d
# -- C: the CONTROL. The same Lua loop and the same N spr() calls, but every
# sprite parked off-screen so the kernel early-outs. What is left is the VM
# loop plus the C-function call overhead -- the part of B that is NOT the
# kernel, and without it B/A is just another reconstruction.
src_c = ("local T = {}\n"
         "for i = 0, %d do T[i] = { i & 7, 9000, 9000 } end\n"
         "function _update(dt) end\n"
         "function _draw()\n"
         "  for i = 0, %d do local q = T[i] spr(q[1], q[2], q[3], -1, 1, 0) end\n"
         "end\n" % (N - 1, N - 1))
moycore.close()
moycore.run_begin(fb2, W, H, wire, sheet, None, 0, 0, snap, aq, None, None)
err_c = moycore.load(src_c, "@abc")
best_c = 1 << 30
for r in range(REPS):
    moycore.tick(0.016)
    d = moycore.tick_split()[1]
    if d < best_c:
        best_c = d
moycore.close()

def nz(b):
    n = 0
    for i in range(0, len(b), 2):
        if b[i] or b[i + 1]:
            n += 1
    return n

print("LOADERR", err)
print("A_pixels", nz(fb))
print("B_pixels", nz(fb2))
print("N", N)
print("A_batch_us", best_a, "per_sprite_us", (best_a * 1000) // N)
print("B_spr_us", best_b, "per_sprite_us", (best_b * 1000) // N)
print("C_loop_us", best_c, "per_sprite_us", (best_c * 1000) // N)
'''

src = (DRIVER.replace("@N@", str(N)).replace("@REPS@", str(REPS)))
p = subprocess.run([t.MP, "-c", src], capture_output=True, text=True, timeout=300)
print(p.stdout.strip() or p.stderr[-900:])
if p.returncode == 0 and "B_spr_us" in p.stdout:
    d = dict(l.split()[0:2] for l in p.stdout.splitlines() if l.split())
    a_us, b_us = int(d["A_batch_us"]), int(d["B_spr_us"])
    if a_us:
        c_us = int(d["C_loop_us"])
        print("\nA one blit_batch      %3d us" % a_us)
        print("B N x moy_spr (Lua)   %3d us   -> B/A = %.2fx" % (b_us, b_us / a_us))
        print("C same loop, clipped  %3d us   (VM loop + call overhead)" % c_us)
        print("B-C kernel only       %3d us   -> (B-C)/A = %.2fx"
              % (b_us - c_us, (b_us - c_us) / a_us))
