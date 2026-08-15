"""ScrollRegion's blit-shift ring against a ROTATING multi-buffer canvas --
the k-generic math the b2d287c parameterization must keep sound at the P4's
triple framebuffer (RETAINED_FRAMES=3), where the paint target holds the frame
painted THREE presents ago and a shift's delta spans three frames of movement.

The host runs single-buffered, so this emulates the rotation with N real
Canvas objects: paint lands in the back one, present() advances. A shift-path
frame (scroll_rect by blit_shift's delta + repaint of only the exposed band)
must be BYTE-IDENTICAL to a full repaint at the same offset, for k = 1, 2, 3.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

W, H = 64, 48


class _RotatingCanvas:
    """N real canvases in rotation: the P4 DPI model (paint back -> show ->
    advance; the next target holds the paint from N presents ago)."""

    def __init__(self, n):
        from runtime.host_canvas import make_canvas as Canvas
        self.RETAINED_FRAMES = n
        self.w, self.h = W, H
        self._bufs = [Canvas(W, H) for _ in range(n)]
        self._back = 0

    @property
    def _buf(self):
        return self._bufs[self._back]._buf

    def rect(self, *a):
        self._bufs[self._back].rect(*a)

    def scroll_rect(self, *a):
        self._bufs[self._back].scroll_rect(*a)

    def present(self):
        self._back = (self._back + 1) % self.RETAINED_FRAMES


def _row_color(y_world):
    return (y_world * 7 + 3) % 63 + 1        # offset-pure, alignment-free


def _full_paint(cv, off):
    for y in range(H):
        cv.rect(0, y, W, 1, _row_color(off + y))


def _shift_or_full(cv, region, frame_no):
    """The #113 consumer contract: shift the retained pixels by the ring's
    delta and repaint only the exposed band; full paint when ineligible.
    Returns "shift" or "full" (what happened)."""
    sh = region.blit_shift(cv, frame_no)
    if sh is None:
        _full_paint(cv, region.offset)
        region.note_painted(frame_no)
        return "full"
    delta, _stamp = sh
    if delta:
        cv.scroll_rect(0, 0, W, H, 0, -delta)
        if delta > 0:                        # scrolled forward: bottom exposed
            for y in range(H - delta, H):
                cv.rect(0, y, W, 1, _row_color(region.offset + y))
        else:                                # backward: top exposed
            for y in range(0, -delta):
                cv.rect(0, y, W, 1, _row_color(region.offset + y))
    region.note_painted(frame_no)
    return "shift"


def _run(k):
    from runtime.ui import ScrollRegion
    from runtime.host_canvas import make_canvas as Canvas
    cv = _RotatingCanvas(k)
    region = ScrollRegion()
    region.set((0, 0, W, H), 400)
    frame = 0
    shifts = 0
    # Arm: k consecutive full paints (one per buffer).
    for _ in range(k):
        assert _shift_or_full(cv, region, frame) == "full"
        cv.present()
        frame += 1
    # Scroll: per-frame deltas; on a k-rotation the target buffer is k frames
    # stale, so the applied shift spans up to k frames of movement.
    ref = Canvas(W, H)
    for d in (6, 6, 2, 9, -5, -6, 0, 4, -11, 7):
        region.scroll_by(d)
        kind = _shift_or_full(cv, region, frame)
        shifts += (kind == "shift")
        ref.cls(0)
        _full_paint(ref, region.offset)
        assert bytes(cv._buf) == bytes(ref._buf), \
            "k=%d frame=%d (%s) diverged from the full repaint" % (frame, d, kind)
        cv.present()
        frame += 1
    assert shifts > 0, "k=%d: the shift path never engaged" % k
    # A frame GAP (another surface painted) must force full paints again.
    frame += 2
    assert _shift_or_full(cv, region, frame) == "full"


def test_ring_pixels_exact_at_k1():
    _run(1)


def test_ring_pixels_exact_at_k2():
    _run(2)


def test_ring_pixels_exact_at_k3():
    _run(3)
