"""The host's RGB565 compositor, and its parity with the one on glass.

`runtime/gfx_binding.py` exists so `device_canvas.py` -- the raster the boards
and the browser run -- can run on the host too, which is what lets the two
canvas implementations become one. That only holds if the two kernels agree
BYTE FOR BYTE, so the important test here is not the unit ones: it is
`test_matches_the_native_moy_gfx`, which drives the same operations through the
real native module under a unix MicroPython build and diffs the framebuffers.

Without that, this file would only prove the host shim agrees with itself. The
device_canvas parity suite has the same shape for the indexed raster, and the
lesson recorded in CLAUDE.md is exactly why it matters: the board once failed
`provisional_tline` against the golden while the host passed, because the only
lane exercising the real C kernel was on-glass conformance and it had never
been run on that verb.
"""

import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_micropython"
UNIX_MP = (TDECK / ".build" / "lvgl_micropython" / "lib" / "micropython"
           / "ports" / "unix" / "build-moyluagfx" / "micropython")

from runtime import gfx_binding as g                             # noqa: E402

pytestmark = pytest.mark.skipif(not g.available(),
                                reason="no C compiler: no host gfx binding")

W, H = 16, 8


def _blank():
    return bytearray(W * H * 2)


def _px(buf):
    return struct.unpack("<%dH" % (len(buf) // 2), bytes(buf))


# The script both sides run. Kept as data so the CPython side and the
# MicroPython side cannot drift into testing different things.
OPS = """
fill_rect(buf, 16, 2, 1, 5, 3, 0xABCD)
fill_rect(buf, 16, -3, 0, 6, 1, 0x1234)
fill_rect(buf, 16, 13, 5, 9, 2, 0x00F0)
scroll_rect(buf, 16, 0, 0, 16, 8, 2, 0)
scroll_rect(buf, 16, 0, 0, 16, 8, 0, -1)
blit565(buf, 16, 8, 3, 2, src, 4, 4, -1, 0, 0, 16, 8)
blit565(buf, 16, 8, 14, 6, src, 4, 4, 0x0002, 0, 0, 16, 8)
blit565(buf, 16, 8, -1, -1, src, 4, 4, -1, 0, 0, 16, 8)
blit_batch(buf, 16, 8, quads, sheet, 128, 256, lut, palt, -1, 1, 0, 0, 0, 0, 16, 8)
blit_batch(buf, 16, 8, quads, sheet, 128, 256, lut, palt, 7, 1, 2, 1, 0, 0, 16, 8)
blit_map(buf, 16, 8, 0, 0, cells, 4, 4, 0, 0, 2, 2, sheet, 128, 256, lut, palt, -1, 1, 0, 0, 16, 8)
blit_map(buf, 16, 8, 3, 2, cells, 4, 4, 1, 1, 3, 3, sheet, 128, 256, lut, palt, 5, 1, 0, 0, 16, 8)
line(buf, 16, 8, 1, 1, 14, 6, 0x3210, 0, 0, 0, 0, 16, 8)
line(buf, 16, 8, -4, 3, 20, 3, 0x3210, 2, 1, 1, 1, 15, 7)
circ(buf, 16, 8, 8, 4, 3, 0x7bef, 0, 0, 0, 0, 16, 8)
circb(buf, 16, 8, 12, 5, 4, 0x001f, 1, 1, 0, 0, 16, 8)
tri(buf, 16, 8, 1, 1, 14, 2, 6, 7, 0xf800, 0, 0, 0, 0, 16, 8)
blit_indices(buf, 16, 8, 2, 1, idx, 5, 4, pal565)
blit_indices(buf, 16, 8, -2, 6, idx, 5, 4, pal565)
sspr(buf, 16, 8, sheet, 128, 256, 0, 0, 8, 8, 2, 1, 11, 5, -1, 0, lut, palt, 0, 0, 0, 0, 16, 8)
sspr(buf, 16, 8, sheet, 128, 256, 8, 0, 8, 8, 4, 2, 6, 3, 3, 1, lut, palt, 1, 1, 0, 0, 16, 8)
tline(buf, 16, 8, cells, 4, 4, sheet, 128, 256, 0, 1, 15, 6, 0, 0, 65536, 32768, -1, lut, palt, 0, 0, 0, 0, 16, 8)
"""

# Sheet/map fixtures, built identically on both sides. Tiles 0 and 1 are given
# DIFFERENT solid colours and the map mixes them, because tile 0 is blank by
# SPEC convention in a map and a fixture made only of it would compare two
# empty framebuffers and call that agreement.
FIXTURES = """
sheet = bytearray(128 * 256)
for _y in range(8):
    for _x in range(8):
        # Tile n lives at ((n % 16) * 8, (n // 16) * 8) in a 128-wide sheet.
        # Every tile is ASYMMETRIC in both axes, which is not decoration: an
        # earlier draft used solid tiles, and a mutation that dropped the
        # per-quad FLIP entirely was invisible -- flipping a solid block gives
        # back the same pixels. Diagonal halves make flip, and each axis of it,
        # observable.
        sheet[_y * 128 + _x] = 5 if _x < _y else 1              # tile 0
        sheet[_y * 128 + 8 + _x] = 7 if _x < 4 else 3           # tile 1
        sheet[_y * 128 + 16 + _x] = 9 if _y < 3 else 11         # tile 2
lut = array.array("H", [(i * 0x0101) & 0xFFFF for i in range(64)])
palt = bytearray(64)
palt[9] = 1                     # index 9 transparent: exercises the palt path
quads = array.array("h", [0] * 12)
quads[4:8] = array.array("h", [0, 2, 3, 0])
quads[8:12] = array.array("h", [1, 9, 1, 1])       # flip=1 on an asymmetric tile
quads[0] = 12
cells = bytearray([0, 1, 2, 1, 1, 0, 1, 2, 2, 1, 0, 1, 1, 2, 1, 0])
idx = bytearray([(_i * 7) % 64 for _i in range(5 * 4)])
pal565 = array.array("H", [(0xF000 - i * 0x0123) & 0xFFFF for i in range(64)])
"""


def _fixtures():
    ns = {"array": __import__("array"), "bytearray": bytearray, "range": range}
    exec(FIXTURES, ns)                         # noqa: S102 -- fixed text
    return {k: ns[k] for k in ("sheet", "lut", "palt", "quads", "cells",
                               "idx", "pal565")}


def _run_ops(mod, buf, src):
    """Run each op and snapshot the buffer AFTER it.

    Per-op and not once at the end, because a single final comparison is a
    false green waiting to happen: the first draft of this test ended with two
    scrolls and three blits, which between them overwrote the region the
    negative-origin fill had touched -- so a deliberate mutation of that clamp
    produced an identical final framebuffer and the test passed. Snapshotting
    each step means no later op can bury an earlier divergence, and the failure
    names the verb that broke.
    """
    shots = []
    env = {"buf": buf, "src": src}
    env.update(_fixtures())            # once: a fresh sheet per op would hide
    for line in OPS.strip().splitlines():          # state carried between them
        line = line.strip()
        if not line:
            continue
        name, _, rest = line.partition("(")
        args = eval("(" + rest.rstrip(), {}, env)     # noqa: S307 -- fixed text
        getattr(mod, name)(*args)
        shots.append("".join("%04x" % v for v in _px(buf)))
    return shots


def test_the_binding_is_available_when_a_compiler_is():
    assert g.available()


def test_fill_respects_the_buffer_capacity():
    buf = _blank()
    g.fill(buf, W * H * 4, 0x0777)          # ask for more than exists
    assert all(p == 0x0777 for p in _px(buf))


def test_fill_rect_clamps_a_negative_origin_instead_of_wrapping():
    buf = _blank()
    g.fill_rect(buf, W, -3, 0, 6, 1, 0x1234)
    row = _px(buf)[:W]
    assert row[:3] == (0x1234,) * 3, row[:6]
    assert row[3] == 0
    # ...and nothing wrapped onto the previous row's tail.
    assert set(_px(buf)[W:]) == {0}


def test_fill_rect_clips_past_the_right_edge_and_the_last_row():
    buf = _blank()
    g.fill_rect(buf, W, 13, 5, 9, 9, 0x00F0)
    px = _px(buf)
    for y in range(H):
        row = px[y * W:(y + 1) * W]
        expect = (0x00F0,) * 3 if y >= 5 else (0,) * 3
        assert row[13:] == expect, (y, row)


def test_blit565_opaque_takes_the_memcpy_lane_and_still_clips():
    buf = _blank()
    src = bytearray(struct.pack("<16H", *range(1, 17)))
    g.blit565(buf, W, H, -1, -1, src, 4, 4, -1, 0, 0, W, H)
    px = _px(buf)
    # Row 0 of the destination is source row 1, shifted one left.
    assert px[0:3] == (6, 7, 8), px[0:4]
    assert px[3] == 0


def test_blit565_colorkey_skips_only_the_keyed_pixel():
    buf = _blank()
    src = bytearray(struct.pack("<4H", 1, 2, 3, 4))
    g.blit565(buf, W, H, 0, 0, src, 4, 1, 2, 0, 0, W, H)
    assert _px(buf)[:4] == (1, 0, 3, 4)


def test_scroll_rect_moves_pixels_and_leaves_the_vacated_span():
    buf = bytearray(struct.pack("<%dH" % (W * H), *range(W * H)))
    g.scroll_rect(buf, W, 0, 0, W, H, 2, 0)
    row0 = _px(buf)[:W]
    assert row0[2:] == tuple(range(W - 2))
    assert row0[:2] == (0, 1)          # untouched, per the device kernel


def test_the_async_pair_refuses_so_callers_take_the_sync_path():
    """Not a stub for its own sake: returning False puts device_canvas on the
    same branch a board takes when its DMA driver declines the copy, so the
    host exercises a path the device also has."""
    buf = _blank()
    assert g.copy_async(buf, 0, buf, 0, 4) is False
    assert g.copy_wait() is True


DRIVER = r'''
import sys, struct, array
sys.path.insert(0, @MODULES@)
import moy_gfx

W, H = 16, 8
buf = bytearray(W * H * 2)
src = bytearray(struct.pack("<16H", *range(1, 17)))

@FIXTURES@

def _shot():
    print("SHOT " + "".join("%04x" % v
          for v in struct.unpack("<%dH" % (W * H), bytes(buf))))

def fill_rect(*a): moy_gfx.fill_rect(*a); _shot()
def scroll_rect(*a): moy_gfx.scroll_rect(*a); _shot()
def blit565(*a): moy_gfx.blit565(*a); _shot()
def blit_batch(*a): moy_gfx.blit_batch(*a); _shot()
def blit_map(*a): moy_gfx.blit_map(*a); _shot()
def line(*a): moy_gfx.line(*a); _shot()
def circ(*a): moy_gfx.circ(*a); _shot()
def circb(*a): moy_gfx.circb(*a); _shot()
def tri(*a): moy_gfx.tri(*a); _shot()
def blit_indices(*a): moy_gfx.blit_indices(*a); _shot()
def sspr(*a): moy_gfx.sspr(*a); _shot()
def tline(*a): moy_gfx.tline(*a); _shot()

@OPS@
'''


@pytest.mark.skipif(not UNIX_MP.exists(),
                    reason="no unix MicroPython build with moy_gfx")
def test_matches_the_native_moy_gfx(tmp_path):
    """THE test in this file: the same ops, the real kernel, byte-for-byte.

    Everything else here proves the host shim is self-consistent. This proves
    it is the SAME compositor device_canvas gets on a board -- which is the
    entire premise of running that class on the host.
    """
    src_body = DRIVER.replace("@MODULES@", repr(str(TDECK / "modules")))
    src_body = src_body.replace("@FIXTURES@", FIXTURES.strip())
    src_body = src_body.replace("@OPS@", OPS.strip())
    script = tmp_path / "driver.py"
    script.write_text(src_body)
    out = subprocess.run([str(UNIX_MP), str(script)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    device = [l[5:] for l in out.stdout.splitlines() if l.startswith("SHOT ")]

    buf = _blank()
    src = bytearray(struct.pack("<16H", *range(1, 17)))
    host = _run_ops(g, buf, src)

    ops = [l.strip() for l in OPS.strip().splitlines() if l.strip()]
    assert len(device) == len(ops), (
        "the device driver produced %d snapshots for %d ops -- the two sides "
        "are not running the same script" % (len(device), len(ops)))
    assert len(host) == len(device)
    for i, (h, d) in enumerate(zip(host, device)):
        assert h == d, (
            "host gfx binding diverged from the native moy_gfx at op %d:\n"
            "  op    : %s\n  host  : %s\n  device: %s" % (i, ops[i], h, d))
