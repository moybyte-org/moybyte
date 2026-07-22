"""#113 Phase 4: kinetic scrolling. A fast release FLINGS the shelf, which
coasts with per-ms friction and hard-stops at the edges; a slow or held
release is a stop; a touch during a coast CATCHES it (and that release is
never a tap). All physics dt is INJECTED (the loop's tick) -- no clock is
ever read, so every trajectory here is exactly reproducible (the wall-clock
flake rule for this box).

Region-level tests drive ui.ScrollRegion/DragTap directly; console-level
tests drive the real shared console through ConsoleDriver and prove the
fling frames keep riding the #113 blit path with the redraw gate open."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DT = 16.0        # injected ms per synthetic pointer frame


def _region(extent=100, content=800):
    from runtime import ui

    r = ui.ScrollRegion()
    r.set((0, 0, 50, extent), content)
    return r


def _drag(r, start=500, step=8, frames=6):
    """A leftward finger: axis positions decreasing => offset increasing."""
    r.drag_start(start)
    p = start
    for _ in range(frames):
        p -= step
        r.drag_move(p, dt_ms=DT)
    return p


# -- the physics (ScrollRegion) ----------------------------------------------

def test_flick_starts_a_fling_that_coasts_and_rests():
    r = _region()
    _drag(r)                       # 8px/16ms = 0.5 px/ms, over MIN_FLING
    off_release = r.offset
    r.drag_end()
    assert r.animating
    moved, ticks = 0, 0
    while r.animating and ticks < 1000:
        r.tick(DT)
        ticks += 1
    assert not r.animating
    assert r.offset > off_release          # it coasted past the release point
    assert 0 <= r.offset <= r._max_offset()


def test_hold_before_release_is_a_stop():
    r = _region()
    p = _drag(r)                   # fast travel...
    for _ in range(12):
        r.drag_move(p, dt_ms=DT)   # ...then the finger holds still
    r.drag_end()
    assert not r.animating


def test_slow_release_is_a_stop():
    r = _region()
    _drag(r, step=1)               # 1px/16ms ~ 0.06 px/ms, under MIN_FLING
    off = r.offset
    r.drag_end()
    assert not r.animating
    r.tick(DT)
    assert r.offset == off


def test_fling_hard_stops_at_the_edge():
    r = _region(content=220)       # short content: the fling must hit the end
    _drag(r, step=20, frames=8)
    r.drag_end()
    ticks = 0
    while r.animating and ticks < 1000:
        r.tick(DT)
        ticks += 1
    assert not r.animating
    assert r.offset == r._max_offset()     # parked exactly at the clamp


def test_new_touch_catches_a_fling():
    r = _region()
    _drag(r)
    r.drag_end()
    assert r.animating
    r.tick(DT)
    r.drag_start(300)              # finger down mid-coast
    assert not r.animating
    off = r.offset
    r.tick(DT)
    assert r.offset == off         # dead in the water


def test_catch_tap_is_swallowed_but_a_plain_tap_still_fires():
    from runtime import ui

    r = _region()                  # vertical region: the drag axis is y
    t = ui.DragTap(r)
    # A real fling via the tap machine.
    t.frame(30, 90, True, True, dt_ms=DT)
    y = 90
    for _ in range(6):
        y -= 8
        t.frame(30, y, False, True, dt_ms=DT)
    t.frame(30, y, False, False, dt_ms=DT)
    assert r.animating
    # The catching tap: press stops the coast, its clean release is NOT a tap.
    t.frame(25, 50, True, True, dt_ms=DT)
    assert not r.animating
    assert t.frame(25, 50, False, False, dt_ms=DT) is None
    # The next clean tap fires normally.
    t.frame(25, 50, True, True, dt_ms=DT)
    assert t.frame(25, 50, False, False, dt_ms=DT) == (25, 50)


def test_fling_trajectory_is_deterministic():
    def run():
        r = _region()
        _drag(r)
        r.drag_end()
        traj = []
        ticks = 0
        while r.animating and ticks < 1000:
            r.tick(DT)
            traj.append(r.offset)
            ticks += 1
        return traj

    a, b = run(), run()
    assert a == b and len(a) > 5           # a real multi-frame coast


# -- the console (shelf pilot, end to end) -----------------------------------

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


def _fling_setup(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    for _ in range(80):
        drv.frame(1 / 30)
    gx, gy, gw, gh = ws.layout.lib_grid
    cx, cy = gx + gw - 10, gy + gh // 2
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    for i in range(1, 6):                  # 12px per 33ms frame: a real flick
        drv.touch_drag(cx - i * 12, cy)
        drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)                      # the release frame starts the coast
    return ws, drv


def test_shelf_fling_coasts_after_release(tmp_path):
    ws, drv = _fling_setup(tmp_path)
    assert ws.launcher.flinging
    off_release = ws.launcher.scroll
    for _ in range(200):
        drv.frame(1 / 30)
        if not ws.launcher.flinging:
            break
    assert not ws.launcher.flinging        # it came to rest
    assert ws.launcher.scroll > off_release
    assert ws.screen == "launcher"         # the gesture opened nothing


def test_fling_frames_ride_the_blit_path(tmp_path):
    ws, drv = _fling_setup(tmp_path)
    assert ws.launcher.flinging
    calls = [0]
    orig = ws.sys_canvas.scroll_rect

    def spy(*a):
        calls[0] += 1
        return orig(*a)

    ws.sys_canvas.scroll_rect = spy
    for _ in range(5):
        drv.frame(1 / 30)
    assert calls[0] >= 3                   # coasting frames blit, not repaint


def test_fling_frame_is_pixel_faithful(tmp_path):
    ws, drv = _fling_setup(tmp_path)
    for _ in range(4):                     # a few coasting frames
        drv.frame(1 / 30)
    assert ws.launcher.flinging
    partial = bytes(ws.sys_canvas.buf)
    off = ws.launcher.scroll
    ws.launcher._region.stop()             # freeze the coast at this offset
    ws.launcher_layer._full_streak = 0     # force the FULL path, same state
    ws.launcher._region.invalidate()
    ws.mark_dirty()
    drv.frame(1 / 30)
    assert ws.launcher.scroll == off
    full = bytes(ws.sys_canvas.buf)
    row = ws.sys_canvas.w * ws.layout.status_h
    assert full[row:] == partial[row:]


def test_gate_recloses_after_the_fling_rests(tmp_path):
    ws, drv = _fling_setup(tmp_path)
    for _ in range(400):
        drv.frame(1 / 30)
        if not ws.launcher.flinging:
            break
    assert not ws.launcher.flinging
    drv.frame(1 / 30)                      # settle the last requested frame
    drawn = ws._frames_drawn
    for _ in range(5):
        drv.frame(1 / 30)                  # quiet frames: nothing repaints
    assert ws._frames_drawn == drawn
