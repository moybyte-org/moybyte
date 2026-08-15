"""#113 scroll-as-blit: UI scrolling shifts already-correct pixels and repaints
only the exposed band, instead of re-issuing every visible card's draw calls
per scroll frame (the P4's measured bottleneck is per-draw-call dispatch, so a
scroll step must not scale with visible content).

Three levels, all host-only and wall-clock-free (this box is slow; the perf
proxy is DISPATCH COUNT, which models the device verdict and cannot flake):

  * Canvas.scroll_rect -- the in-place shift primitive vs a naive reference,
    every direction, overlap-safe, clamped.
  * ui.ScrollRegion's paint ring -- blit_shift only fires when the target
    framebuffer provably holds this view's pixels (consecutive paints, same
    key, canvas capability).
  * The Library shelf + Editor picker pilots -- a blitted drag frame is
    byte-identical to a full repaint of the same state, and draws strictly
    fewer cards.

These build the SAME shared console the device runs (runtime.host_app) and
drive it through ConsoleDriver, exactly like tests/test_launcher_scroll.py."""

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# -- Canvas.scroll_rect ------------------------------------------------------

def _ref_scroll(src, w, h, rect, dx, dy):
    """Naive reference: every rect pixel whose source is also inside the rect
    takes it; everything else (exposed strips, outside the rect) is unchanged.

    Takes and returns a list of PIXEL values, not bytes -- a pixel is a 16-bit
    RGB565 word now, and packing one into a bytearray element would silently
    truncate it to the low half."""
    out = list(src)
    x0, y0 = max(0, rect[0]), max(0, rect[1])
    x1, y1 = min(w, rect[0] + rect[2]), min(h, rect[1] + rect[3])
    for y in range(y0, y1):
        for x in range(x0, x1):
            sx, sy = x - dx, y - dy
            if x0 <= sx < x1 and y0 <= sy < y1:
                out[y * w + x] = src[sy * w + sx]
    return out


def _noisy_canvas(w=40, h=30, seed=7):
    """A canvas of random PIXELS, so a shift that drops or duplicates one shows.

    The noise is written straight into the RGB565 framebuffer as arbitrary
    words: `scroll_rect` moves bytes and never resolves a colour, so the
    reference below can compare pixel VALUES without any of them having to be a
    palette entry -- and random 16-bit words are a stricter fixture than 64
    indices, because two distinct pixels are far less likely to collide.
    """
    from runtime.host_canvas import make_canvas

    cv = make_canvas(w, h)
    rnd = random.Random(seed)
    mv = memoryview(cv._buf).cast("H")
    for i in range(w * h):
        mv[i] = rnd.randrange(0x10000)
    return cv


def _pix(cv):
    """The canvas as a flat list of pixel values, for `_ref_scroll`."""
    return list(memoryview(cv._buf).cast("H"))


def test_scroll_rect_matches_reference_every_direction():
    rect = (5, 4, 25, 20)
    for dx, dy in ((3, 0), (-5, 0), (0, 2), (0, -4), (6, 3), (-2, -7),
                   (24, 0), (0, 19)):
        cv = _noisy_canvas()
        before = _pix(cv)
        cv.scroll_rect(rect[0], rect[1], rect[2], rect[3], dx, dy)
        assert _pix(cv) == _ref_scroll(before, cv.w, cv.h, rect, dx, dy), \
            (dx, dy)


def test_scroll_rect_zero_shift_is_a_noop():
    cv = _noisy_canvas()
    before = bytes(cv._buf)
    cv.scroll_rect(5, 4, 25, 20, 0, 0)
    assert bytes(cv._buf) == before


def test_scroll_rect_clamps_offcanvas_rects():
    for rect in ((-10, -10, 30, 25), (25, 20, 30, 30), (0, 0, 999, 999)):
        for dx, dy in ((4, 0), (0, -3), (-2, 5)):
            cv = _noisy_canvas(seed=11)
            before = _pix(cv)
            cv.scroll_rect(rect[0], rect[1], rect[2], rect[3], dx, dy)
            assert _pix(cv) == _ref_scroll(before, cv.w, cv.h, rect,
                                           dx, dy), (rect, dx, dy)


def test_scroll_rect_whole_shift_away_is_a_noop():
    cv = _noisy_canvas()
    before = bytes(cv._buf)
    cv.scroll_rect(5, 4, 25, 20, 25, 0)     # |dx| >= rw: nothing survives
    assert bytes(cv._buf) == before


# -- ScrollRegion's paint ring ----------------------------------------------

class _Cv:
    """A canvas stub with the two capabilities blit_shift probes."""
    RETAINED_FRAMES = 1

    def scroll_rect(self, *a):
        pass


def _region(extent=100, content=400):
    from runtime import ui

    r = ui.ScrollRegion()
    r.set((0, 0, 50, extent), content)
    return r


def test_blit_shift_needs_the_canvas_capability():
    r = _region()
    r.note_painted(10, key="k")

    class _NoBlit:
        RETAINED_FRAMES = 1

    assert r.blit_shift(_NoBlit(), 11, key="k") is None      # no scroll_rect
    assert r.blit_shift(object(), 11, key="k") is None       # no retention


def test_blit_shift_requires_consecutive_paints():
    r = _region()
    r.note_painted(10, key="k")
    r.offset = 30
    assert r.blit_shift(_Cv(), 12, key="k") is None    # gap: frame 11 missing
    assert r.blit_shift(_Cv(), 11, key="k") == (30, None)


def test_blit_shift_pins_the_key_and_returns_the_stamp():
    r = _region()
    r.note_painted(10, key=("sel", 1), stamp=(5, 6, 8, 13))
    r.offset = 12
    assert r.blit_shift(_Cv(), 11, key=("sel", 2)) is None   # state changed
    assert r.blit_shift(_Cv(), 11, key=("sel", 1)) == (12, (5, 6, 8, 13))


def test_blit_shift_ping_pong_measures_against_the_older_paint():
    class _Cv2(_Cv):
        RETAINED_FRAMES = 2

    r = _region()
    r.offset = 10
    r.note_painted(10, key="k")             # the buffer two paints back
    r.offset = 20
    r.note_painted(11, key="k")
    r.offset = 25
    # The target buffer holds the frame-10 paint (offset 10): delta is 15.
    assert r.blit_shift(_Cv2(), 12, key="k") == (15, None)
    assert r.blit_shift(_Cv(), 12, key="k") == (5, None)     # single-buffer: 5


def test_blit_shift_rejects_a_full_view_shift_and_invalidate_clears():
    r = _region(extent=100)
    r.note_painted(10, key="k")
    r.offset = 100                          # a whole viewport: nothing survives
    assert r.blit_shift(_Cv(), 11, key="k") is None
    r.offset = 40
    assert r.blit_shift(_Cv(), 11, key="k") is not None
    r.invalidate()
    assert r.blit_shift(_Cv(), 11, key="k") is None


# -- the shelf + picker pilots (the shared console, end to end) --------------

def _ws_with_carts(tmp_path, n):
    from runtime import host_app, moy_carts

    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    while len(ws.launcher.items) < n:
        i = len(ws.launcher.items)
        moy_carts.create("Extra %02d" % i, carts_dir,
                         src="def _draw():\n    cls(1)\n", type="app")
        ws.launcher.items = moy_carts.scan(carts_dir)
    ws.launcher.sel = 0
    ws.launcher.scroll = 0
    return ws


def _spy_scroll_rect(ws):
    calls = [0]
    orig = ws.sys_canvas.scroll_rect

    def spy(*a):
        calls[0] += 1
        return orig(*a)

    ws.sys_canvas.scroll_rect = spy
    return calls


def _drag_frames(drv, cx, cy, steps, step_px=9):
    """Press at (cx, cy) and drag left `steps` samples; returns the last x."""
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    x = cx
    for i in range(1, steps + 1):
        x = cx - i * step_px
        drv.touch_drag(x, cy)
        drv.frame(1 / 30)
    return x


def test_shelf_blit_frame_is_pixel_faithful(tmp_path):
    """A blitted drag frame's composed screen must be byte-identical to a FULL
    repaint of the identical state -- the #113 band contract, proven on the
    real console (the test_launcher_scroll golden pattern)."""
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    for _ in range(80):                       # settle covers; arm streak + ring
        drv.frame(1 / 30)
    calls = _spy_scroll_rect(ws)
    gx, gy, gw, gh = ws.layout.lib_grid
    cx, cy = gx + gw - 10, gy + gh // 2
    lastx = _drag_frames(drv, cx, cy, 7)
    assert ws.launcher.dragging
    assert calls[0] > 0                       # the blit path actually engaged
    n_blit = calls[0]
    lastx -= 9
    drv.touch_drag(lastx, cy)                 # one more eligible blit frame
    drv.frame(1 / 30)
    assert calls[0] > n_blit
    partial = bytes(ws.sys_canvas._buf)
    scroll = ws.launcher.scroll
    # Force the FULL path for the identical state and compare the pixels.
    ws.launcher_layer._full_streak = 0
    ws.launcher._region.invalidate()
    ws.mark_dirty()
    drv.touch_drag(lastx, cy)                 # same pos: scroll unchanged
    drv.frame(1 / 30)
    assert ws.launcher.scroll == scroll
    full = bytes(ws.sys_canvas._buf)
    row = ws.sys_canvas.w * ws.layout.status_h * 2  # compare below the bar (the
    assert full[row:] == partial[row:]            # clock may tick between)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "launcher"            # and the release opened nothing


def test_shelf_blit_frame_draws_fewer_cards(tmp_path, monkeypatch):
    """The point of #113: an eligible drag frame draws only the cards touching
    the exposed strip (+ cursor damage), strictly fewer than the full path's
    every-visible-card -- dispatch count is the host's flake-free perf proxy."""
    import runtime.launcher_layer as LL
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    for _ in range(80):
        drv.frame(1 / 30)
    cards = [0]
    orig_real = LL.Launcher._draw_cart_card
    orig_pseudo = LL.Launcher._draw_pseudo_card

    def spy_real(self, *a, **k):
        cards[0] += 1
        return orig_real(self, *a, **k)

    def spy_pseudo(self, *a, **k):
        cards[0] += 1
        return orig_pseudo(self, *a, **k)

    monkeypatch.setattr(LL.Launcher, "_draw_cart_card", spy_real)
    monkeypatch.setattr(LL.Launcher, "_draw_pseudo_card", spy_pseudo)
    calls = _spy_scroll_rect(ws)
    gx, gy, gw, gh = ws.layout.lib_grid
    cx, cy = gx + gw - 10, gy + gh // 2
    lastx = _drag_frames(drv, cx, cy, 6)
    assert ws.launcher.dragging and calls[0] > 0
    cards[0] = 0
    drv.touch_drag(lastx - 9, cy)             # one blitted drag frame
    drv.frame(1 / 30)
    n_blit = cards[0]
    ws.launcher_layer._full_streak = 0        # force the full path, same state
    ws.launcher._region.invalidate()
    ws.mark_dirty()
    cards[0] = 0
    drv.touch_drag(lastx - 9, cy)
    drv.frame(1 / 30)
    n_full = cards[0]
    assert n_blit < n_full                    # strictly fewer draws per frame
    assert n_full >= 4                        # sanity: the shelf was populated


def test_picker_blit_frame_is_pixel_faithful(tmp_path):
    """The same band contract on the second pilot surface: the Editor project
    picker (the SAME Launcher grid over its dotted tool backdrop)."""
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    ws.open_picker()
    for _ in range(80):                       # settle covers; arm streak + ring
        drv.frame(1 / 30)
    assert ws.screen == "picker"
    calls = _spy_scroll_rect(ws)
    gx, gy, gw, gh = ws.layout.lib_grid
    cx, cy = gx + gw - 10, gy + gh // 2
    lastx = _drag_frames(drv, cx, cy, 7)
    assert ws.picker.dragging
    assert calls[0] > 0
    partial = bytes(ws.sys_canvas._buf)
    scroll = ws.picker.scroll
    ws.editor_picker._full_streak = 0
    ws.picker._region.invalidate()
    ws.mark_dirty()
    drv.touch_drag(lastx, cy)
    drv.frame(1 / 30)
    assert ws.picker.scroll == scroll
    full = bytes(ws.sys_canvas._buf)
    row = ws.sys_canvas.w * ws.layout.status_h
    assert full[row:] == partial[row:]
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "picker"              # the drag opened nothing


# -- the web transport (#113 Phase 6: the scr op) ----------------------------

def test_picker_blit_arms_in_a_window(tmp_path):
    """The desktop tier's picker drag must actually SHIFT, not repaint every
    card (on-glass P4 regression, 2026-07-25).

    `WindowedWM._install` swaps a window's layout context in to draw its
    content and swaps the ROOT context back afterwards -- for BOTH grids, so
    `Launcher.set_layout` runs several times per frame (measured: 1040 calls in
    4 idle seconds). It used to `invalidate()` the paint ring on every call, so
    the ring was ALWAYS empty here, blit_shift never returned a shift, and each
    drag frame fell back to repainting the whole band: ~73ms a frame on glass
    versus ~28ms once the shift arms. The ring's KEY (which carries the surface
    geometry) is what guards a layout change now, not an eager invalidate."""
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    del ws                                   # rebuild windowed, same cart dir
    ws = host_app.build_workstation(str(tmp_path / "carts"),
                                    sys_size=(1024, 600), font_scale=2,
                                    windowed=True)
    drv = host_app.ConsoleDriver(ws)
    ws.open_picker()
    for _ in range(80):                      # settle covers; arm streak + ring
        drv.frame(1 / 30)
    win = ws.wm._wins["make"]
    assert win.kind == "picker"

    # The ring must be non-empty AFTER a frame -- the bug left it wiped.
    assert ws.picker._region._painted, "the paint ring never survives a frame"

    shifts = [0]
    region = ws.picker._region
    orig = region.blit_shift

    def spy(cv, frame_no, key=None):
        r = orig(cv, frame_no, key)
        if r is not None:
            shifts[0] += 1
        return r

    region.blit_shift = spy

    lay = win.ctx.layout                     # window-local grid geometry
    gx, gy, gw, gh = lay.lib_grid
    ox, oy = win.x + 1, win.y + 1 + win.title_h
    cx, cy = ox + gx + gw - 12, oy + gy + gh // 2
    _drag_frames(drv, cx, cy, 10)
    assert ws.picker.dragging
    assert shifts[0] > 0, "no drag frame used the scroll-as-blit path"
    drv.touch_up()
    drv.frame(1 / 30)


def test_set_layout_keeps_the_paint_ring(tmp_path):
    """Re-applying a layout must not wipe the ring (that is the whole bug);
    a paint recorded under DIFFERENT geometry must still be unmatchable via
    the key."""
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    ws.open_picker()
    for _ in range(60):
        drv.frame(1 / 30)
    region = ws.picker._region
    assert region._painted
    before = list(region._painted)
    ws.picker.set_layout(ws.picker.layout)   # the per-frame context swap
    assert region._painted == before, "set_layout wiped the ring"
    # ... but the geometry IS pinned: the statics key carries the grid rect.
    key = ws.editor_picker._statics_key(ws.sys_canvas)
    assert ws.picker.layout.lib_grid in key
