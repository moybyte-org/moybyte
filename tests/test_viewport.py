"""The viewport canvas (#155): draw 0,0-based into a sub-rect of a bigger buffer.

The contract that makes it useful is EQUIVALENCE: drawing through a viewport must
produce exactly the pixels you get by drawing into a private buffer and then
copying that buffer to the same place. That is what lets the windowed WM delete
the copy -- ~900KB of bus traffic per frame on the P4, where a full-screen copy
costs 27ms against a measured 91MB/s ceiling.

Everything outside the viewport must be untouched: a window's content clearing
itself must not wipe the desktop it is drawing on.

The canvas is the boards' own (`device_canvas.DeviceCanvas` over a host
compositor), so a pixel is an RGB565 WORD, not a palette index: buffer
comparisons go through `_buf`, and the out-of-viewport sentinel is a word rather
than a colour.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "runtime"))

import pytest                                            # noqa: E402

import canvas_probe as probe                             # noqa: E402
from runtime.host_canvas import make_canvas as Canvas    # noqa: E402

VX, VY, VW, VH = 37, 23, 120, 80          # a deliberately unaligned viewport
BW, BH = 240, 160

# An "untouched" marker for the pixels outside the viewport. A WORD, not a
# palette index -- and deliberately one no MOY64 entry produces, so a verb that
# wrote here could not accidentally reproduce it.
SENTINEL = 0x2A2A


def _prefill(cv):
    cv._buf[:] = b"\x2a" * len(cv._buf)


def _word(cv, x, y):
    """The raw framebuffer word at BUFFER (x, y) -- not surface-local."""
    return memoryview(cv._buf).cast("H")[y * cv._stride + x]


def _paint(cv, w, h):
    """A pattern that exercises every verb a panel uses, in surface-local
    coordinates -- including the ones that used to ignore camera and clip."""
    cv.cls(5)
    cv.rect(2, 2, 30, 12, 3)
    cv.rectb(40, 2, 24, 16, 7)
    cv.print("Hi", 4, 20, 11)
    cv.pix(1, 1, 9)
    cv.line(0, h - 1, w - 1, 0, 12)
    cv.circ(w // 2, h // 2, 9, 8)
    cv.circb(w // 2, h // 2, 14, 6)
    # Overflow every edge: the viewport must clamp exactly like a real surface.
    cv.rect(-15, -9, 40, 40, 4)
    cv.rect(w - 10, h - 6, 50, 50, 2)
    cv.print("overflowing text", w - 30, h - 9, 1)
    # Clip + camera, both surface-local.
    cv.clip(10, 10, 40, 30)
    cv.rect(0, 0, w, h, 13)
    cv.clip()
    cv.camera(5, 3)
    cv.rect(10, 10, 20, 20, 15)
    cv.print("cam", 12, 34, 2)
    cv.camera(0, 0)
    # pal must reach every verb through the viewport too.
    cv.pal(3, 12)
    cv.rect(60, 40, 18, 18, 3)
    cv.pal()


def _viewport_canvas():
    cv = Canvas(BW, BH)
    cv.cls(0)
    cv.set_viewport(VX, VY, VW, VH)
    return cv


def test_viewport_reports_the_logical_surface():
    cv = _viewport_canvas()
    assert (cv.w, cv.h) == (VW, VH)          # what a layout sees
    assert cv._stride == BW                  # what the buffer is


def test_drawing_through_a_viewport_equals_draw_then_copy():
    """The equivalence the whole change rests on."""
    # A: draw into a private surface, then stamp it at the viewport's origin.
    private = Canvas(VW, VH)
    _paint(private, VW, VH)
    staged = Canvas(BW, BH)
    staged.cls(0)
    staged.blit_strip(private, VX, VY)

    # B: draw the same thing straight through a viewport.
    direct = _viewport_canvas()
    _paint(direct, VW, VH)

    assert bytes(direct._buf) == bytes(staged._buf)


def test_nothing_outside_the_viewport_is_touched():
    """cls() means "my surface", not "the whole framebuffer"."""
    cv = Canvas(BW, BH)
    _prefill(cv)
    cv.set_viewport(VX, VY, VW, VH)
    _paint(cv, VW, VH)
    for y in range(BH):
        for x in range(BW):
            inside = VX <= x < VX + VW and VY <= y < VY + VH
            if not inside:
                assert _word(cv, x, y) == SENTINEL, (x, y)


def test_scroll_rect_shifts_inside_the_viewport_only():
    """#113's shift is surface-local and must not drag in neighbouring pixels."""
    cv = Canvas(BW, BH)
    _prefill(cv)
    cv.set_viewport(VX, VY, VW, VH)
    cv.cls(0)
    cv.rect(0, 0, 20, VH, 9)                 # a bar at the surface's left edge
    cv.scroll_rect(0, 0, VW, VH, 30, 0)      # shift it right by 30
    row = VY + VH // 2
    assert cv.pix(40, VH // 2) == 9                       # moved (surface-local)
    assert _word(cv, VX - 1, row) == SENTINEL             # outside is untouched
    assert _word(cv, VX + VW, row) == SENTINEL


def test_clear_viewport_restores_the_whole_surface():
    cv = _viewport_canvas()
    cv.clear_viewport()
    assert (cv.w, cv.h, cv._ox, cv._oy) == (BW, BH, 0, 0)
    cv.cls(6)
    assert set(probe.pixels(cv)) == probe.words_of({6}, cv)


def test_camera_reports_the_callers_value_not_the_offset():
    """camera() returns the previous USER camera; the viewport origin rides in
    the effective offset and must not leak into that contract (carts save and
    restore it)."""
    cv = _viewport_canvas()
    assert cv.camera(11, 13) == (0, 0)
    assert cv.camera(0, 0) == (11, 13)


def test_a_full_surface_canvas_is_bit_for_bit_unchanged():
    """The parity guard: without set_viewport nothing may move."""
    plain = Canvas(VW, VH)
    _paint(plain, VW, VH)
    again = Canvas(VW, VH)
    _paint(again, VW, VH)
    assert bytes(plain._buf) == bytes(again._buf)
    assert (plain._ox, plain._oy, plain._stride) == (0, 0, VW)


@pytest.mark.parametrize("vx,vy,vw,vh", [
    (0, 0, BW, BH),            # degenerate: the whole buffer
    (1, 1, 3, 3),              # tiny, odd offset
    (BW - 10, BH - 10, 10, 10),  # hard against the far corner
])
def test_equivalence_holds_at_the_edges(vx, vy, vw, vh):
    private = Canvas(vw, vh)
    _paint(private, vw, vh)
    staged = Canvas(BW, BH)
    staged.cls(0)
    staged.blit_strip(private, vx, vy)

    direct = Canvas(BW, BH)
    direct.cls(0)
    direct.set_viewport(vx, vy, vw, vh)
    _paint(direct, vw, vh)
    assert bytes(direct._buf) == bytes(staged._buf)
