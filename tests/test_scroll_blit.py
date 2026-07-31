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
    takes it; everything else (exposed strips, outside the rect) is unchanged."""
    out = bytearray(src)
    x0, y0 = max(0, rect[0]), max(0, rect[1])
    x1, y1 = min(w, rect[0] + rect[2]), min(h, rect[1] + rect[3])
    for y in range(y0, y1):
        for x in range(x0, x1):
            sx, sy = x - dx, y - dy
            if x0 <= sx < x1 and y0 <= sy < y1:
                out[y * w + x] = src[sy * w + sx]
    return bytes(out)


def _noisy_canvas(w=40, h=30, seed=7):
    from runtime.canvas import Canvas

    cv = Canvas(w, h)
    rnd = random.Random(seed)
    cv.buf[:] = bytes(rnd.randrange(64) for _ in range(w * h))
    return cv


def test_scroll_rect_matches_reference_every_direction():
    rect = (5, 4, 25, 20)
    for dx, dy in ((3, 0), (-5, 0), (0, 2), (0, -4), (6, 3), (-2, -7),
                   (24, 0), (0, 19)):
        cv = _noisy_canvas()
        before = bytes(cv.buf)
        cv.scroll_rect(rect[0], rect[1], rect[2], rect[3], dx, dy)
        assert bytes(cv.buf) == _ref_scroll(before, cv.w, cv.h, rect, dx, dy), \
            (dx, dy)


def test_scroll_rect_zero_shift_is_a_noop():
    cv = _noisy_canvas()
    before = bytes(cv.buf)
    cv.scroll_rect(5, 4, 25, 20, 0, 0)
    assert bytes(cv.buf) == before


def test_scroll_rect_clamps_offcanvas_rects():
    for rect in ((-10, -10, 30, 25), (25, 20, 30, 30), (0, 0, 999, 999)):
        for dx, dy in ((4, 0), (0, -3), (-2, 5)):
            cv = _noisy_canvas(seed=11)
            before = bytes(cv.buf)
            cv.scroll_rect(rect[0], rect[1], rect[2], rect[3], dx, dy)
            assert bytes(cv.buf) == _ref_scroll(before, cv.w, cv.h, rect,
                                                dx, dy), (rect, dx, dy)


def test_scroll_rect_whole_shift_away_is_a_noop():
    cv = _noisy_canvas()
    before = bytes(cv.buf)
    cv.scroll_rect(5, 4, 25, 20, 25, 0)     # |dx| >= rw: nothing survives
    assert bytes(cv.buf) == before


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
    partial = bytes(ws.sys_canvas.buf)
    scroll = ws.launcher.scroll
    # Force the FULL path for the identical state and compare the pixels.
    ws.launcher_layer._full_streak = 0
    ws.launcher._region.invalidate()
    ws.mark_dirty()
    drv.touch_drag(lastx, cy)                 # same pos: scroll unchanged
    drv.frame(1 / 30)
    assert ws.launcher.scroll == scroll
    full = bytes(ws.sys_canvas.buf)
    row = ws.sys_canvas.w * ws.layout.status_h    # compare below the bar (the
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
    partial = bytes(ws.sys_canvas.buf)
    scroll = ws.picker.scroll
    ws.editor_picker._full_streak = 0
    ws.picker._region.invalidate()
    ws.mark_dirty()
    drv.touch_drag(lastx, cy)
    drv.frame(1 / 30)
    assert ws.picker.scroll == scroll
    full = bytes(ws.sys_canvas.buf)
    row = ws.sys_canvas.w * ws.layout.status_h
    assert full[row:] == partial[row:]
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "picker"              # the drag opened nothing


# -- the web transport (#113 Phase 6: the scr op) ----------------------------

def test_surface_delta_never_collapses_a_scr_stream():
    """A stream carrying a scroll shift is NOT idempotent -- the browser
    REPLAYS a {"same":1} surface from its cache, and re-applying scr shifts
    the pixels again -- so an exactly-repeated scr stream must still ship in
    full (while a plain repeated stream still dedups)."""
    from runtime import web_view

    d = web_view.SurfaceDelta()
    plain = [{"id": "a", "domain": "system",
              "cmds": [["rect", 0, 0, 4, 4, 7]]}]
    assert "cmds" in d.encode(plain)[0]
    assert d.encode(plain)[0].get("same") == 1          # normal dedup works
    scrolly = [{"id": "a", "domain": "system",
                "cmds": [["scr", 0, 0, 100, 50, -3, 0],
                         ["rect", 97, 0, 3, 50, 7]]}]
    assert "cmds" in d.encode(scrolly)[0]
    assert "cmds" in d.encode(scrolly)[0]               # repeat: STILL full
    assert d.encode(plain)[0].get("same") is None       # cache moved on
    assert d.encode(plain)[0].get("same") == 1


def test_tee_canvas_pins_retention_off_and_records_the_shift():
    """The device web-view Tee: the console's blit gate must read 0 (the panel
    ping-pong and the browser's retained buffer disagree on retention), and a
    direct scroll_rect call must be recorded AND forwarded (an __getattr__
    fallthrough would leave the browser stream torn)."""
    from runtime import web_view

    class _Real:
        w, h = 320, 240
        RETAINED_FRAMES = 2

        def __init__(self):
            self.calls = []

        def scroll_rect(self, *a):
            self.calls.append(a)

    real = _Real()
    rec = web_view.DrawRecorder(320, 240)
    rec.enabled = True
    tee = web_view.TeeCanvas(real, rec)
    assert tee.RETAINED_FRAMES == 0            # class attr beats __getattr__
    rec.begin()
    tee.scroll_rect(1, 2, 30, 20, -3, 0)
    rec.commit()
    assert ["scr", 1, 2, 30, 20, -3, 0] in rec.frame()
    assert real.calls == [(1, 2, 30, 20, -3, 0)]


def test_web_stream_blit_frames_replay_to_the_raster_truth(tmp_path):
    """End to end over the web transport: drive the SAME drag on a raster
    console and on the recording WebConsole, replay every shipped frame
    (including the scr blit frames) onto a raster canvas, and the result must
    be byte-identical to the raster console's screen below the bar."""
    import shutil

    from runtime import host_app, web_view
    from runtime.canvas import Canvas
    from tools.web_console import WebConsole

    ws = _ws_with_carts(tmp_path / "raster", 14)         # truth console
    drv = host_app.ConsoleDriver(ws)
    shutil.copytree(tmp_path / "raster" / "carts", tmp_path / "web")
    wc = WebConsole(str(tmp_path / "web"))               # recording console
    wc.ws.launcher.sel = 0
    wc.ws.launcher.scroll = 0

    frames = []

    def step_web():
        cmds, _title, _audio = wc.step_frame()
        if cmds:
            frames.append(cmds)

    for _ in range(80):                                  # settle both consoles
        drv.frame(1 / 30)
        step_web()
    gx, gy, gw, gh = ws.layout.lib_grid
    cx, cy = gx + gw - 10, gy + gh // 2
    drv.touch(cx, cy)
    wc.driver.touch(cx, cy)
    drv.frame(1 / 30)
    step_web()
    for i in range(1, 7):                                # identical drags
        x = cx - i * 9
        drv.touch_drag(x, cy)
        wc.driver.touch_drag(x, cy)
        drv.frame(1 / 30)
        step_web()
    assert ws.launcher.dragging and wc.ws.launcher.dragging
    assert ws.launcher.scroll == wc.ws.launcher.scroll   # same physics
    # scroll-as-blit is OFF over the web transports (CommandCanvas.RETAINED_FRAMES
    # = 0), so a drag ships FULL bands and never a ["scr", ...] shift.
    #
    # This is a MITIGATION, not a root-cause fix, and the distinction matters: the
    # owner sees scrolling go black on BOTH web transports (the wasm runner and
    # tools/web_console.py). This test passed throughout, because it replays the
    # FLAT stream with every frame delivered in order -- which is exactly the
    # configuration the bug does NOT appear in. Neither the #76 per-surface delta
    # nor the #44 dirty gate's skipped frames are exercised here, and the shift's
    # correctness depends on what the browser actually retained.
    #
    # So the mechanism is proven sound in isolation and broken in the field, and
    # until that gap is explained the safe contract is self-contained frames. The
    # assertion is inverted rather than deleted so re-enabling the optimisation has
    # to come back through here deliberately.
    saw_scr = sum(1 for cmds in frames for c in cmds if c and c[0] == "scr")
    assert saw_scr == 0, "web frames must be self-contained (no scroll shift)"
    # Covers ship ONCE via /assets imgref (#113) -- so a real client fetches
    # assets and replays the frames against them. All covers are live by now,
    # and their serial names are stable, so the final assets serve every frame.
    assets = wc.assets()
    saw_ref = sum(1 for cmds in frames for c in cmds
                  if c and c[0] == "imgref" and str(c[3]).startswith("cover:"))
    assert saw_ref > 0                                   # the ship-once lane engaged
    replayed = Canvas(wc.canvas.w, wc.canvas.h)
    layers, atlas = {}, {}
    for cmds in frames:
        web_view.replay_to_canvas(cmds, replayed, layers=layers, atlas=atlas,
                                  assets=assets)
    row = replayed.w * ws.layout.status_h
    assert bytes(replayed.buf)[row:] == bytes(ws.sys_canvas.buf)[row:]


def test_paint_image_replay_honors_the_clip():
    """The shelf edge-card cover bug (web view, pre-#113 but exposed by kinetic
    scrolling): a paint-tagged image drawn across a clip edge must replay
    clipped exactly like the raster truth -- the page's im/imr and the Python
    replayer used canvas-clamped blits that ignored the clip rect, so covers
    bled outside the Library panel."""
    from runtime import web_view
    from runtime.canvas import Canvas, Image

    img = Image(20, 10, [8] * 200, transparent=-1)
    img._paint = True                      # ships as ["img", ...] like a cover
    truth = Canvas(40, 30)
    cc = web_view.CommandCanvas(40, 30)
    for cv in (truth, cc):
        cv.clip(10, 5, 15, 12)
        cv.spr(img, 4, 3, 1)               # straddles the clip's left/top edge
        cv.spr(img, 20, 14, 1)             # ...and the right/bottom edge
        cv.clip()
    cmds = cc.take_commands()
    assert any(c[0] == "img" for c in cmds)     # the paint fast wire engaged
    replayed = Canvas(40, 30)
    web_view.replay_to_canvas(cmds, replayed)
    assert bytes(replayed.buf) == bytes(truth.buf)


# -- the WINDOWED tier: the ring must survive per-frame layout swaps ---------

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
