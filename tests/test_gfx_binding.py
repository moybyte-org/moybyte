"""The host's RGB565 compositor, and its parity with the one on glass.

`runtime/gfx_binding.py` exists so `device_canvas.py` -- the raster the boards
and the browser run -- can run on the host too, which is what lets the two
canvas implementations become one. That only holds if the two kernels agree
BYTE FOR BYTE, so the important test here is not the unit ones: it is
`test_matches_the_native_moy_gfx`, which drives the same operations through the
real native module under a unix MicroPython build and diffs the framebuffers.

Without that, this file would only prove the host shim agrees with itself. The
lesson recorded in CLAUDE.md is exactly why it matters: the board once failed
`provisional_tline` against the golden while the host passed, because the only
lane exercising the real C kernel was on-glass conformance and it had never
been run on that verb.

THAT CHECK USED TO BE ABSENT ALMOST EVERYWHERE, which is worse than not having
it, because the suite looked green either way. It pointed at a MicroPython
binary somebody had built by hand, following prose in a README; no Makefile
target and no CI step produced one, so it ran on a single machine and skipped as
a lone `s` on every other. It is a `make unix-micropython` target now, CI builds
it on every push, and a missing binary WARNS locally and FAILS wherever a build
is expected (`_require_unix_mp`). `tests/test_device_canvas_parity.py` shed its
~400-line Python transcription of libmoy's nine verbs on the strength of it.

WHAT THE OP SCRIPT COVERS, and why it is not just the happy path. A cart's
coordinates reach these kernels already camera-shifted and clip-intersected by
`DeviceCanvas._fill`, in Python, so the spec conformance goldens exercise only
clipped, positive, in-bounds calls. The CLAMPING half of the script aims at
everything they cannot: negative origins, rects one row past the last, sources
off the sheet, deliberate capacity overruns, palette and cell-array indices
exactly on their boundary. Those branches exist, in the shim's own words, "to
stop a bad call scribbling over the framebuffer" -- and the framebuffer is a
memoryview into a larger arena so that a scribble is something the comparison
can SEE rather than a write past the end that both sides make invisibly.
"""

import os
import re
import struct
import subprocess
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"

# Where `make unix-micropython` puts the desktop MicroPython with the native
# usermods compiled in. The second candidate is the hand-built tree the recipe
# in native/moycore/README.md produces, kept so a machine that already has one
# does not rebuild; `MOYBYTE_MICROPYTHON` overrides both (the same variable
# experiments/audio_parity/audio_parity.py reads).
UNIX_MP_CANDIDATES = (
    ROOT / ".build" / "unix_micropython" / "micropython" / "ports" / "unix"
    / "build-moybyte" / "micropython",
    TDECK / ".build" / "lvgl_micropython" / "lib" / "micropython"
    / "ports" / "unix" / "build-moyluagfx" / "micropython",
)

from runtime import gfx_binding as g                             # noqa: E402

pytestmark = pytest.mark.skipif(not g.available(),
                                reason="no C compiler: no host gfx binding")

W, H = 16, 8


def _find_unix_mp():
    env = os.environ.get("MOYBYTE_MICROPYTHON")
    if env and os.path.exists(env):
        return env
    for cand in UNIX_MP_CANDIDATES:
        if cand.exists():
            return str(cand)
    return None


_NO_MP = """the COMPILED-VS-COMPILED check did not run: no desktop MicroPython
with the moy_gfx usermod. Build one -- it takes about fifteen seconds:

    make unix-micropython

Without it nothing in `make test` compares two independently COMPILED rasters.
The host binding is still checked against itself, and the device canvas suite
still checks the host against a Python transcription -- but a transcription can
be right while the C is wrong, which is exactly how the board once failed
provisional_tline against the golden while this repo was green."""


def _require_unix_mp():
    """The binary, or a LOUD absence.

    A bare `pytest.skip` was the old behaviour and it is what let this check be
    absent for months: a skip is one `s` in the progress line, and the thing
    being skipped is the only lane that runs the real kernel. So the absence
    warns (which pytest prints in its summary even under -q) and, wherever a
    build is expected -- CI, or anyone who sets MOYBYTE_REQUIRE_UNIX_MP -- it
    FAILS instead. `CI` is set by GitHub Actions itself, so deleting the build
    step from the workflow turns this red rather than quiet.
    """
    exe = _find_unix_mp()
    if exe is not None:
        return exe
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_UNIX_MP"):
        pytest.fail(_NO_MP)
    warnings.warn(UserWarning(_NO_MP), stacklevel=2)
    pytest.skip("no desktop MicroPython with moy_gfx (see the warning above)")


# The op script's destination is a memoryview SLICE of a bigger allocation, and
# the snapshot covers the whole allocation. That is what makes the capacity
# guards observable: `buf` reports 128 pixels, both kernels derive their row
# count from that, and a guard that fails to clamp writes into ARENA_ROWS of
# canary beyond it -- which the comparison sees. Snapshotting only the framebuffer
# would make every capacity escape invisible on BOTH sides and read as agreement,
# which is how "the clamps are covered" could be believed without being true.
ARENA_ROWS = 4


def _blank():
    return bytearray(W * H * 2)


def _arena():
    """(whole allocation, the framebuffer view into its head).

    The canary rows are PATTERNED, not zero. A zeroed tail catches a stray
    write but not a stray `scroll_rect`, which only moves bytes: shifting a row
    of zeros by one produces a row of zeros, so a missing row clamp reads as
    agreement. Patterned, the shift shows.
    """
    arena = bytearray(W * (H + ARENA_ROWS) * 2)
    for i in range(W * H * 2, len(arena)):
        arena[i] = (i * 37 + 11) & 0xFF
    return arena, memoryview(arena)[:W * H * 2]


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
blit565_scale(buf, 16, 8, 0, 0, src, 4, 4, 2)
blit565_scale(buf, 16, 8, -4, -2, src, 4, 4, 3)
blit565_scale(buf, 16, 8, 11, 5, src, 4, 4, 2)
blit565_scale(buf, 16, 8, 3, 1, src, 4, 4, 1)
blit565_scale(buf, 16, 8, 1, 2, src, 4, 4, 0)
text(buf, 16, 8, msg, 0, 0, 0xf81f, font, 65, 1, 0, 0, 0, 0, 16, 8)
text(buf, 16, 8, msg, -3, 1, 0x07e0, font, 65, 1, 0, 0, 0, 0, 16, 8)
text(buf, 16, 8, msg, 4, 1, 0x8f1f, font, 65, 1, 2, 1, 3, 2, 12, 6)
text(buf, 16, 8, msg, 0, 0, 0xffe0, font, 65, 2, 0, 0, 0, 0, 16, 8)
text(buf, 16, 8, msg, -5, -3, 0x4a69, font, 65, 2, 1, -1, 2, 1, 14, 7)
text(buf, 16, 8, msg, 2, 1, 0x9cd3, font, 65, 3, 0, 0, 1, 1, 15, 7)
"""

# The CLAMPING regimes, which nothing else in `make test` reaches on both sides.
#
# The spec conformance goldens replay CART draws, and a cart's coordinates
# arrive here already camera-shifted and clip-intersected by DeviceCanvas._fill
# -- in Python, before the kernel is called. So every golden exercises the
# happy path: positive origins, rects inside the buffer, sources inside the
# sheet. The branches below it never touches are precisely the ones
# runtime/moyhost_gfx.c says exist "to stop a bad call scribbling over the
# framebuffer": x < 0, x >= stride, y >= max_rows, and the dcap/scap capacity
# guards that derive a row count from the buffer instead of trusting the one
# they were handed.
#
# They are a parity concern and not merely a safety one. A clamp is arithmetic
# duplicated in two places -- moyhost_gfx.c and modmoy_gfx.c -- and the two
# have to agree on WHICH pixel is the last one written. Ops here therefore aim
# just past every edge rather than far past it: a rect that misses the buffer
# by a mile agrees trivially, and a rect that ends one pixel over does not.
#
# Comments are allowed from here on; both sides skip them (`_op_lines`).
CLAMP_OPS = """
# -- fill: the whole point of its `cap` argument -----------------------------
fill(buf, 999, 0x0777)
fill(buf, -5, 0x1111)
fill(buf, 0, 0x2222)
fill(buf, 128, 0x3333)
# -- fill_rect: each clamp branch, plus the two early refusals ---------------
fill_rect(buf, 16, 20, 1, 4, 2, 0x2345)
fill_rect(buf, 16, 1, 40, 4, 2, 0x3456)
fill_rect(buf, 16, 2, 6, 4, 99, 0x4567)
fill_rect(buf, 16, 3, -4, 4, 6, 0x5678)
fill_rect(buf, 16, -2, -2, 5, 5, 0x6789)
fill_rect(buf, 16, -9, 2, 5, 2, 0x789a)
fill_rect(buf, 16, 5, 5, 0, 3, 0x89ab)
fill_rect(buf, 0, 0, 0, 4, 4, 0x9abc)
fill_rect(buf, 16, 12, 3, 99, 2, 0xabcd)
fill_rect(buf, 16, 15, 7, 1, 1, 0xbcde)
# ONE row over the last, not ninety: `y + h > max_rows` clamps either way when
# the overhang is large, so a mutant that clamps at max_rows + 1 survives every
# op above. Aiming at the edge is what kills it. Same idea for the ops below
# that end in an odd-looking 17 or 9.
fill_rect(buf, 16, 4, 7, 5, 2, 0xcdef)
# -- scroll_rect: a rect hanging off the buffer, a shift wider than the rect -
scroll_rect(buf, 16, -4, -2, 24, 20, 3, 1)
scroll_rect(buf, 16, 2, 2, 4, 4, 9, 0)
scroll_rect(buf, 16, 0, 0, 16, 8, 0, -3)
scroll_rect(buf, 16, 0, 0, 16, 8, 0, 0)
scroll_rect(buf, 16, 10, 4, 20, 20, -3, 2)
scroll_rect(buf, 16, 0, 1, 16, 8, 1, 0)
scroll_rect(buf, 16, 2, 2, 4, 4, 4, 0)
# -- blit565: the dcap/scap row clamps and the opaque lane's clipped span ----
blit565(buf, 16, 99, 2, 2, src, 4, 4, -1, 0, 0, 16, 99)
blit565(buf, 16, 8, 1, 1, src, 4, 99, -1, 0, 0, 16, 8)
blit565(buf, 16, 8, 1, 1, src, 4, 4, -1, 3, 0, 5, 8)
blit565(buf, 16, 8, -10, 2, src, 4, 4, -1, 0, 0, 16, 8)
blit565(buf, 16, 8, 2, 2, src, 4, 4, -1, -5, -5, 20, 20)
blit565(buf, 16, 8, 14, 6, src, 4, 4, 0x0003, 0, 0, 16, 8)
blit565(buf, 16, 10, 2, 8, src, 4, 4, -1, 0, 0, 16, 10)
# -- blit565_scale: negative origin, off the right edge, scale below 1 -------
blit565_scale(buf, 16, 99, 0, 0, src, 4, 99, 2)
blit565_scale(buf, 16, 8, -40, -40, src, 4, 4, 3)
blit565_scale(buf, 16, 8, 20, 2, src, 4, 4, 2)
blit565_scale(buf, 16, 8, 1, 1, src, 4, 4, -3)
blit565_scale(buf, 16, 8, -1, 6, src, 4, 4, 4)
blit565_scale(buf, 16, 8, 1, 0, src, 4, 4, 4)
# -- blit_window: the window clamped to BOTH buffers ------------------------
blit_window(buf, 16, 8, big, 32, 4, 2)
blit_window(buf, 16, 8, big, 32, -3, -3)
blit_window(buf, 16, 8, big, 32, 20, 2)
blit_window(buf, 16, 8, big, 32, 4, 14)
blit_window(buf, 16, 8, big, 32, 30, 2)
blit_window(buf, 16, 8, big, 32, 40, 0)
blit_window(buf, 16, 8, big, 0, 0, 0)
blit_window(buf, 16, 8, big, 32, 17, 2)
blit_window(buf, 16, 8, big, 32, 4, 9)
blit_window(buf, 16, 8, big, 32, 2, -1)
# -- blit_indices: the icap row guard, and an index past the palette --------
blit_indices(buf, 16, 8, 1, 0, idx, 5, 9, pal565)
blit_indices(buf, 16, 8, 0, 0, idx, 5, 4, pal8)
blit_indices(buf, 16, 8, -9, -9, idx, 5, 4, pal565)
blit_indices(buf, 16, 8, 14, 7, idx, 5, 4, pal565)
blit_indices(buf, 16, 99, 2, 2, idx, 5, 4, pal565)
blit_indices(buf, 16, 10, 1, 8, idx, 5, 4, pal565)
blit_indices(buf, 16, 8, 3, 1, idx8, 4, 4, pal8)
# -- blit_batch: a sheet that is not SPEC 3.2's, and the cursor/scale clamps -
blit_batch(buf, 16, 8, quads, sheet, 64, 256, lut, palt, -1, 1, 0, 0, 0, 0, 16, 8)
blit_batch(buf, 16, 8, quads, sheet, 128, 256, lut, palt, -1, 0, 0, 0, -4, -4, 99, 99)
blit_batch(buf, 16, 8, quads, sheet, 128, 256, lut, palt, -1, 2, 40, 40, 0, 0, 16, 8)
blit_batch(buf, 16, 8, quads, sheet, 128, 256, lut, None, 3, 1, -20, -20, 0, 0, 16, 8)
blit_batch(buf, 16, 8, no_quads, sheet, 128, 256, lut, palt, -1, 1, 0, 0, 0, 0, 16, 8)
blit_batch(buf, 16, 8, over_quads, sheet, 128, 256, lut, palt, -1, 1, 0, 0, 0, 0, 16, 8)
# -- blit_map: the cells-length and sheet refusals, a region off the map -----
blit_map(buf, 16, 8, 0, 0, cells, 8, 8, 0, 0, 2, 2, sheet, 128, 256, lut, palt, -1, 1, 0, 0, 16, 8)
blit_map(buf, 16, 8, 0, 0, cells, 4, 4, 0, 0, 2, 2, sheet, 64, 256, lut, palt, -1, 1, 0, 0, 16, 8)
blit_map(buf, 16, 8, -5, -5, cells, 4, 4, 2, 2, 9, 9, sheet, 128, 256, lut, palt, -1, 1, 0, 0, 16, 8)
blit_map(buf, 16, 8, 2, 1, cells, 4, 4, 0, 0, 4, 4, sheet, 128, 256, lut, palt, 0, 2, -3, -3, 99, 99)
blit_map(buf, 16, 8, 0, 0, cells, 4, 4, 0, 0, 0, 2, sheet, 128, 256, lut, palt, -1, 1, 0, 0, 16, 8)
# -- the solid verbs: coordinates and clip rects outside the buffer ----------
tri(buf, 16, 8, -50, -50, 60, 4, 3, 40, 0x07ff, 0, 0, 0, 0, 16, 8)
tri(buf, 16, 8, 2, 2, 6, 2, 4, 6, 0x07e0, 0, 0, 9, 9, 3, 3)
tri(buf, 16, 8, 1, 1, 5, 5, 9, 1, 0xf81f, -30, -30, -4, -4, 99, 99)
line(buf, 16, 8, -100, -100, 100, 100, 0x001f, 0, 0, 0, 0, 16, 8)
line(buf, 16, 8, 3, 3, 3, 3, 0x07e0, 0, 0, -9, -9, 99, 99)
line(buf, 16, 8, 0, 0, 15, 7, 0xffff, 40, 40, 0, 0, 16, 8)
circ(buf, 16, 8, 8, 4, 40, 0x39c7, 0, 0, 0, 0, 16, 8)
circ(buf, 16, 8, 8, 4, 0, 0x0022, 0, 0, 0, 0, 16, 8)
circ(buf, 16, 8, -20, -20, 6, 0x0033, 0, 0, -5, -5, 40, 40)
circb(buf, 16, 8, 8, 4, 40, 0x4444, 0, 0, 0, 0, 16, 8)
circb(buf, 16, 8, 2, 2, 5, 0x5555, 0, 0, 6, 6, 2, 2)
circb(buf, 16, 8, 20, 2, 3, 0x6666, 10, 0, 0, 0, 16, 8)
# The clip rect is intersected with the BUFFER by every libmoy verb here
# (mg_clip, which is one function both sides include). One past each edge, so a mutant that
# intersects one late has somewhere to write and be seen.
circ(buf, 16, 8, 8, 6, 5, 0x7777, 0, 0, 0, 0, 16, 9)
line(buf, 16, 8, 0, 4, 20, 4, 0x8888, 0, 0, 0, 0, 17, 8)
circ(buf, 16, 8, 1, 4, 4, 0x9999, 0, 0, -1, -1, 16, 8)
# -- sspr: a source off the sheet, a degenerate destination, both-axis flip --
sspr(buf, 16, 8, sheet, 128, 256, 120, 250, 16, 16, 1, 1, 8, 6, -1, 0, lut, palt, 0, 0, 0, 0, 16, 8)
sspr(buf, 16, 8, sheet, 64, 256, 0, 0, 8, 8, 0, 0, 8, 8, -1, 0, lut, palt, 0, 0, 0, 0, 16, 8)
sspr(buf, 16, 8, sheet, 128, 256, 0, 0, 8, 8, 2, 2, 0, 5, -1, 0, lut, palt, 0, 0, 0, 0, 16, 8)
sspr(buf, 16, 8, sheet, 128, 256, 8, 8, 8, 8, -6, -4, 20, 14, -1, 3, lut, palt, 0, 0, 0, 0, 16, 8)
sspr(buf, 16, 8, sheet, 128, 256, 16, 0, 8, 8, 3, 1, 6, 6, 9, 2, lut, palt, 4, 3, -2, -2, 99, 99)
# -- tline: the same two refusals, and a walk that leaves the map -----------
tline(buf, 16, 8, cells, 8, 8, sheet, 128, 256, 0, 1, 15, 6, 0, 0, 65536, 32768, -1, lut, palt, 0, 0, 0, 0, 16, 8)
tline(buf, 16, 8, cells, 4, 4, sheet, 64, 256, 0, 1, 15, 6, 0, 0, 65536, 32768, -1, lut, palt, 0, 0, 0, 0, 16, 8)
tline(buf, 16, 8, cells, 4, 4, sheet, 128, 256, -20, 3, 40, 3, -100000, -100000, 65536, 0, -1, lut, palt, 0, 0, 0, 0, 16, 8)
tline(buf, 16, 8, cells, 4, 4, sheet, 128, 256, 2, -5, 12, 20, 0, 0, 0, 0, 5, lut, palt, 3, 2, -4, -4, 99, 99)
# -- text: empty, an inverted clip, scale 0, and every glyph out of range ---
text(buf, 16, 8, empty, 0, 0, 0xf81f, font, 65, 1, 0, 0, 0, 0, 16, 8)
text(buf, 16, 8, msg, 2, 2, 0x07e0, font, 65, 0, 0, 0, 0, 0, 16, 8)
text(buf, 16, 8, msg, 2, 2, 0x001f, font, 200, 2, 0, 0, 0, 0, 16, 8)
text(buf, 16, 8, msg, 2, 2, 0x0f0f, font, 65, 2, 0, 0, 9, 9, 3, 3)
text(buf, 16, 8, msg, -40, 1, 0xfe00, font, 65, 2, 0, 0, 0, 0, 16, 8)
text(buf, 16, 8, msg, 40, 1, 0xfe01, font, 65, 2, 0, 0, 0, 0, 16, 8)
text(buf, 16, 8, msg, 1, 1, 0x1234, font, 65, 3, -30, -30, -9, -9, 99, 99)
text(buf, 16, 8, long, 0, 3, 0x4321, font, 65, 1, 0, 0, 0, 0, 16, 8)
text(buf, 16, 8, long, -5, 2, 0x4322, font, 65, 2, 0, 0, 2, 1, 14, 7)
text(buf, 16, 8, msg, 0, 6, 0x4323, font, 65, 2, 0, 0, 0, 0, 16, 8)
text(buf, 16, 8, msg, 0, 7, 0x4324, font, 65, 1, 0, 0, 0, 0, 16, 8)
# -- dh = 0 on the libmoy verbs, which is where the two sides ACTUALLY differed
#
# Found 2026-08-15 while extracting the compositor, and the reason the "one
# semantic difference" claim needed checking rather than believing. These verbs
# ignore `dh` on the device (`(void)dh`; the row count comes from the buffer
# capacity) but moyhost_gfx.c used to refuse `dh <= 0` outright -- so `line`
# with a zero-height canvas drew NOTHING on the host and fourteen pixels on
# glass. Nothing could see it: the goldens only ever replay a real canvas, and
# these ops were the first to hand the kernels a degenerate one. The host was
# moved onto the board's behaviour; these pin it, and they are only meaningful
# with an EXPLICIT clip rect (a defaulted cy1 would be 0 and clip everything
# away on both sides, agreeing for the wrong reason).
line(buf, 16, 0, 1, 1, 14, 6, 0x2001, 0, 0, 0, 0, 16, 8)
circ(buf, 16, 0, 8, 4, 3, 0x2002, 0, 0, 0, 0, 16, 8)
circb(buf, 16, 0, 5, 3, 3, 0x2003, 0, 0, 0, 0, 16, 8)
tri(buf, 16, 0, 1, 1, 13, 2, 5, 6, 0x2004, 0, 0, 0, 0, 16, 8)
sspr(buf, 16, 0, sheet, 128, 256, 0, 0, 8, 8, 2, 1, 9, 5, -1, 0, lut, palt, 0, 0, 0, 0, 16, 8)
tline(buf, 16, 0, cells, 4, 4, sheet, 128, 256, 0, 1, 15, 6, 0, 0, 65536, 32768, -1, lut, palt, 0, 0, 0, 0, 16, 8)
blit_batch(buf, 16, 0, quads, sheet, 128, 256, lut, palt, -1, 1, 0, 0, 0, 0, 16, 8)
"""

OPS = OPS + CLAMP_OPS


def _op_lines(text):
    """The executable lines of an op script: no blanks, no comments."""
    out = []
    for line in text.strip().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out

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
# A synthetic petme128-LAYOUT font (8 bytes per glyph, column-major, LSB = top
# row), not the real one: moy_font.py is a build artefact, and a fixture both
# sides can BUILD cannot drift the way two staged copies of a blob can. Every
# glyph is asymmetric in both axes for the same reason the sheet tiles are --
# a mirror-image bug in the glyph walk has to be able to show.
font = bytearray([1, 3, 7, 15, 31, 63, 127, 255,          # 0: left-thin, top-heavy
                  255, 127, 63, 31, 15, 7, 3, 1,          # 1: its mirror in x
                  128, 192, 224, 240, 248, 252, 254, 255,  # 2: bottom-heavy
                  1, 0, 2, 0, 4, 0, 8, 0])                # 3: sparse
# 'A'/'B'/'C' index glyphs 0/1/2 off first=65; '!' (33) is BELOW first, which is
# the out-of-range case -- it must fall back to glyph 0, and glyph 0 is drawn
# rather than blank so that fallback is observable.
msg = b"AB!C"
empty = b""
# Longer than the buffer is wide at every scale the op script uses, so the
# right-edge break (`x >= cx1`) and the left-edge skip are both taken.
long = b"ABCABCABCABCABCABC"
# A 32x16 RGB565 source for blit_window, which needs one WIDER and TALLER than
# the destination or its two clamps (window past the source width, window past
# the source rows) can never fire. Built byte-wise so both sides get the same
# bytes without needing array.tobytes(), which MicroPython does not have.
big = bytearray(32 * 16 * 2)
for _i in range(len(big)):
    big[_i] = (_i * 29 + 7) & 0xFF
# EIGHT entries, against index bytes that run to 63: the `p >= pcap` skip.
pal8 = array.array("H", [(0x0F00 + i * 0x0311) & 0xFFFF for i in range(8)])
# ...and index bytes that straddle it EXACTLY. `idx` happens to contain no 8,
# so a mutant skipping at `p >= pcap + 1` survived it: the boundary has to be
# in the data or the boundary is not tested.
idx8 = bytearray([6, 7, 8, 9, 8, 0, 8, 63, 7, 8, 5, 8, 8, 8, 1, 8])
# The array form's slot 0 is a write CURSOR, and both sides clamp it to the
# array's own length. Below the 4-word header -> zero items; past the end ->
# as many as the buffer actually holds (one here, not the 249 it claims).
no_quads = array.array("h", [0, 0, 0, 0])
over_quads = array.array("h", [999, 0, 0, 0, 5, 1, 1, 0])
"""

_FIXTURE_NAMES = ("sheet", "lut", "palt", "quads", "cells", "idx", "pal565",
                  "font", "msg", "empty", "long", "big", "pal8", "idx8",
                  "no_quads", "over_quads")


def _fixtures():
    ns = {"array": __import__("array"), "bytearray": bytearray, "range": range,
          "len": len}
    exec(FIXTURES, ns)                         # noqa: S102 -- fixed text
    return {k: ns[k] for k in _FIXTURE_NAMES}


def _run_ops(mod, arena, buf, src):
    """Run each op and snapshot the whole ARENA after it.

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
    for line in _op_lines(OPS):                    # state carried between them
        name, _, rest = line.partition("(")
        args = eval("(" + rest.rstrip(), {}, env)     # noqa: S307 -- fixed text
        getattr(mod, name)(*args)
        shots.append("".join("%04x" % v for v in _px(arena)))
    return shots


def test_the_binding_is_available_when_a_compiler_is():
    assert g.available()


# -- the surface, against the verbs device_canvas actually reaches for --------
#
# gfx_binding.py's header promised this check for a while before it existed. It
# is worth having because the two ways a verb reaches the kernel are NOT the
# same risk. An unguarded `self._gfx.circ(...)` is a hard dependency: if the
# host binding lacks that name, the host console dies at its first draw with an
# AttributeError out of a ctypes wrapper. A `getattr(gfx, "sspr", None)` is
# optional by construction -- the canvas carries a Python fallback -- so a
# native-only accelerator (make_draw_ctx, fill_spans) is allowed to be absent
# here and says nothing about drift.
#
# Reading the CALL SITES rather than a hand-kept list is the whole point: a verb
# added to device_canvas and forgotten here fails on the day it is added.

_CANVAS = TDECK / "modules" / "device_canvas.py"

# `getattr(gfx, "name", ...)` / `getattr(self._gfx, "name", ...)` -- probed, so
# the canvas has a fallback and the binding need not carry it.
_PROBED = re.compile(r'getattr\(\s*(?:self\._)?gfx\s*,\s*"(\w+)"')
# `gfx.name(...)` / `self._gfx.name(...)` -- called outright. The negative
# lookbehind keeps `self._gfx_text` and other attributes out.
_CALLED = re.compile(r'(?:self\._gfx|(?<![\w.])gfx)\.(\w+)\s*\(')


def _canvas_verbs():
    src = _CANVAS.read_text(encoding="utf-8")
    probed = set(_PROBED.findall(src))
    return probed, set(_CALLED.findall(src)) - probed


def test_the_binding_carries_every_verb_device_canvas_calls_outright():
    probed, required = _canvas_verbs()
    # A floor on both, so a refactor that changes how the canvas names its
    # kernel cannot turn this into a test of an empty set that still passes.
    assert len(required) >= 12, sorted(required)
    assert len(probed) >= 4, sorted(probed)
    missing = sorted(n for n in required if not callable(getattr(g, n, None)))
    assert not missing, (
        "device_canvas calls these on the kernel with no fallback, and the host "
        "binding does not have them -- on the host that is an AttributeError at "
        "the first draw: %s" % missing)


def test_the_optional_libmoy_verbs_are_implemented_here_anyway():
    """tri/sspr/tline/text are PROBED by device_canvas, and present regardless.

    Pinning the decision _SIGS records: "optional" on a board means "the board
    would be slower", not "the host may diverge". If one of these were dropped
    here the canvas would quietly take its Python fallback and the host would
    stop exercising the same raster the glass runs -- green, and no longer
    testing anything.
    """
    probed, _ = _canvas_verbs()
    for name in ("tri", "sspr", "tline", "text"):
        assert name in probed, "%s is no longer probed by device_canvas" % name
        assert callable(getattr(g, name, None)), name


# copy_async/copy_wait are the refusal pair -- they move no pixels, so an op
# script entry for them would compare two unchanged framebuffers. They have
# test_the_async_pair_refuses_so_callers_take_the_sync_path instead.
_NO_PIXELS = frozenset(("copy_async", "copy_wait"))


def test_the_op_script_exercises_every_verb_the_canvas_depends_on():
    """The compiled-vs-compiled check must reach each of them.

    This is the hole the provisional_tline day went through: a verb can be
    present on both sides, be called by the canvas, and still never be run
    through two independently compiled kernels -- and then the only thing
    comparing them is a transcription, which can be right while the C is wrong.
    """
    _, required = _canvas_verbs()
    run = {line.partition("(")[0] for line in _op_lines(OPS)}
    uncovered = sorted(n for n in required - _NO_PIXELS if n not in run)
    assert not uncovered, (
        "device_canvas depends on these and the host/device op script never "
        "runs them, so nothing compares the two compilations: %s" % uncovered)


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

W, H, ARENA_ROWS = 16, 8, @ARENA_ROWS@
# `buf` is a SLICE: it reports H rows, the arena has ARENA_ROWS more, and the
# snapshot covers both -- so a capacity guard that fails to clamp is visible
# rather than a write nobody reads. See the host side for why.
arena = bytearray(W * (H + ARENA_ROWS) * 2)
for _i in range(W * H * 2, len(arena)):
    arena[_i] = (_i * 37 + 11) & 0xFF          # patterned canary; see _arena()
buf = memoryview(arena)[:W * H * 2]
src = bytearray(struct.pack("<16H", *range(1, 17)))

@FIXTURES@

def _shot():
    print("SHOT " + "".join("%04x" % v
          for v in struct.unpack("<%dH" % (W * (H + ARENA_ROWS)), bytes(arena))))

def fill(*a): moy_gfx.fill(*a); _shot()
def fill_rect(*a): moy_gfx.fill_rect(*a); _shot()
def scroll_rect(*a): moy_gfx.scroll_rect(*a); _shot()
def blit_window(*a): moy_gfx.blit_window(*a); _shot()
def blit565(*a): moy_gfx.blit565(*a); _shot()
def blit565_scale(*a): moy_gfx.blit565_scale(*a); _shot()
def text(*a): moy_gfx.text(*a); _shot()
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


def test_matches_the_native_moy_gfx(tmp_path):
    """THE test in this file: the same ops, the real kernel, byte-for-byte.

    Everything else here proves the host shim is self-consistent. This proves
    it is the SAME compositor device_canvas gets on a board -- which is the
    entire premise of running that class on the host.

    It is also the only thing in `make test` that compares two independently
    COMPILED rasters, which is why its absence is loud (`_require_unix_mp`) and
    why `make unix-micropython` exists to produce the binary it needs.
    """
    exe = _require_unix_mp()
    src_body = DRIVER.replace("@MODULES@", repr(str(TDECK / "modules")))
    src_body = src_body.replace("@ARENA_ROWS@", str(ARENA_ROWS))
    src_body = src_body.replace("@FIXTURES@", FIXTURES.strip())
    src_body = src_body.replace("@OPS@", OPS.strip())
    script = tmp_path / "driver.py"
    script.write_text(src_body)
    out = subprocess.run([exe, str(script)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    device = [l[5:] for l in out.stdout.splitlines() if l.startswith("SHOT ")]

    arena, buf = _arena()
    src = bytearray(struct.pack("<16H", *range(1, 17)))
    host = _run_ops(g, arena, buf, src)

    ops = _op_lines(OPS)
    assert len(device) == len(ops), (
        "the device driver produced %d snapshots for %d ops -- the two sides "
        "are not running the same script" % (len(device), len(ops)))
    assert len(host) == len(device)
    for i, (h, d) in enumerate(zip(host, device)):
        assert h == d, (
            "host gfx binding diverged from the native moy_gfx at op %d:\n"
            "  op    : %s\n  host  : %s\n  device: %s" % (i, ops[i], h, d))
