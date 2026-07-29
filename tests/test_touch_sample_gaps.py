"""Touch backends that can't sample every frame (#113 + #74).

The T-Deck's GT911 clock-stretches 20-45ms on most of the reads it makes while
a finger is down, so a drag yields ~20-30 samples/s against a 30-60fps loop:
MOST frames carry no new sample even though the finger never left the glass.
Two things follow, and both are tested here:

* the device backend HOLDS the last point (device_input.Touch.poll), so the
  console keeps seeing a held finger -- pinned by the firmware grep test in
  test_micropython_spike.py;
* the repeat frames are marked `pointer.fresh = False`, and the console banks
  their time instead of charging the kinetic velocity a delta the hardware
  never measured (Workstation._tick_pointer_dt). Charging it -- what a naive
  "every frame is a sample" feed does -- halves the release velocity per stale
  frame, so real flicks died below MIN_FLING.

All dt is injected, so every trajectory here is exact (the wall-clock rule)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DT = 16.0        # injected ms per loop frame
GAP = 3          # a fresh hardware sample every GAP-th frame (the device's rate)


def _region(extent=100, content=800):
    from runtime import ui

    r = ui.ScrollRegion()
    r.set((0, 0, 50, extent), content)
    return r


def _gappy_drag(r, dt_of_stale, frames=9, step=8, start=500):
    """A finger travelling `step` px per FRAME, reported every GAP-th frame.

    `dt_of_stale` is what the console charges a repeat sample: None is the fix
    (learn nothing, keep the velocity), DT is the pre-fix behaviour (a real
    zero-delta measurement, which decays the EMA toward a stop)."""
    r.drag_start(start)
    p = start
    reported = start
    for i in range(frames):
        p -= step
        if (i + 1) % GAP == 0:
            reported = p
            r.drag_move(reported, dt_ms=DT * GAP)   # banked: GAP frames of time
        else:
            r.drag_move(reported, dt_ms=dt_of_stale)
    return p


# -- the physics -------------------------------------------------------------

def test_a_gappy_finger_still_flings():
    # 11 frames: the finger lifts a frame or two after the last hardware report,
    # which is the ordinary case (releases don't wait for the GT911).
    r = _region()
    _gappy_drag(r, dt_of_stale=None, frames=11)
    r.drag_end()
    assert r.animating


def test_charging_stale_samples_a_zero_delta_kills_the_fling():
    # The regression this fix exists for: the same finger, the same travel, with
    # every repeat frame counted as a measurement of a motionless finger. Two
    # stale frames before the release quarter the EMA -- the flick dies.
    r = _region()
    _gappy_drag(r, dt_of_stale=DT, frames=11)
    r.drag_end()
    assert not r.animating


def test_banked_time_measures_the_true_finger_speed():
    from runtime import ui

    smooth = _region()          # a backend that samples every frame
    smooth.drag_start(500)
    p = 500
    for _ in range(9):
        p -= 8
        smooth.drag_move(p, dt_ms=DT)

    gappy = _region()
    _gappy_drag(gappy, dt_of_stale=None)

    assert gappy.offset == smooth.offset                  # same travel
    # Same measured speed (0.5 px/ms): the EMA has seen fewer samples, so it has
    # converged less -- but it must not be off by the GAP factor, which is what
    # charging one frame's dt to a GAP-frame delta would do.
    assert gappy._vel < smooth._vel * 1.05
    assert gappy._vel > smooth._vel * 0.75
    assert gappy._vel >= ui.ScrollRegion.MIN_FLING


def test_a_finger_that_really_stops_still_reads_as_a_stop():
    # The stale-sample rule must not resurrect the fling a held finger cancels:
    # a finger resting on the glass produces FRESH samples at the same position.
    r = _region()
    _gappy_drag(r, dt_of_stale=None)
    for _ in range(12):
        r.drag_move(r._drag, dt_ms=DT)     # fresh, and going nowhere
    r.drag_end()
    assert not r.animating


# -- the console's dt bookkeeping --------------------------------------------

def _ws(tmp_path):
    from runtime import host_app

    return host_app.build_workstation(str(tmp_path / "carts"))


def test_stale_frames_bank_their_time_for_the_next_real_sample(tmp_path):
    ws = _ws(tmp_path)
    ws._frame_dt_ms = DT
    p = ws.pointer

    p.fresh = True
    ws._tick_pointer_dt(p)
    assert ws._pointer_dt_ms == DT

    p.fresh = False
    ws._tick_pointer_dt(p)
    assert ws._pointer_dt_ms is None       # measured nothing -> charge nothing
    ws._tick_pointer_dt(p)
    assert ws._pointer_dt_ms is None

    p.fresh = True
    ws._tick_pointer_dt(p)
    assert ws._pointer_dt_ms == DT * 3     # the two skipped frames, banked
    ws._tick_pointer_dt(p)
    assert ws._pointer_dt_ms == DT         # ...and spent exactly once


def test_banked_time_is_clamped_like_the_frame_tick(tmp_path):
    ws = _ws(tmp_path)
    ws._frame_dt_ms = DT
    ws.pointer.fresh = False
    for _ in range(50):                    # a long stall (the board slept, a GC hit)
        ws._tick_pointer_dt(ws.pointer)
    ws.pointer.fresh = True
    ws._tick_pointer_dt(ws.pointer)
    assert ws._pointer_dt_ms == 100.0      # the physics can't be spiked by a hitch


# -- end to end: the shelf under a device-shaped sample stream ----------------

def _ws_with_carts(tmp_path, n=14):
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


def _shelf_drag_with_gaps(tmp_path, frames=12, step=6):
    """Drag the shelf the way the T-Deck feeds it: the finger moves every frame,
    the hardware reports every GAP-th one, and the backend repeats its last
    point (marked stale) in between."""
    from runtime import host_app

    ws = _ws_with_carts(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    for _ in range(80):
        drv.frame(1 / 60)
    gx, gy, gw, gh = ws.layout.lib_grid
    cx, cy = gx + gw - 10, gy + gh // 2
    drv.touch(cx, cy)
    drv.frame(1 / 60)
    x = cx
    for i in range(frames):
        x -= step
        if (i + 1) % GAP == 0:
            drv.touch_drag(x, cy)          # a real GT911 report
            ws.pointer.fresh = True
        else:
            ws.pointer.fresh = False       # held finger, no new sample
        drv.frame(1 / 60)
    return ws, drv, cx - x


def test_the_shelf_follows_a_finger_through_sample_gaps(tmp_path):
    # The pre-fix device feed dropped pointer.down on every stale frame, which
    # ended the gesture (DragTap.frame -> drag_end) and left the rest of the
    # swipe moving nothing, because a resumed hold has no press edge to re-arm
    # the drag. The shelf must track the finger's whole travel instead.
    ws, _drv, travel = _shelf_drag_with_gaps(tmp_path)
    reported = travel - travel % (GAP * 6)          # the last REPORTED position
    assert ws.launcher.scroll >= reported - 2
    assert ws.screen == "launcher"                  # and it opened nothing


def test_a_gappy_swipe_ends_in_a_fling(tmp_path):
    ws, drv, _travel = _shelf_drag_with_gaps(tmp_path)
    drv.touch_up()
    drv.frame(1 / 60)
    assert ws.launcher.flinging
    off = ws.launcher.scroll
    for _ in range(300):
        drv.frame(1 / 60)
        if not ws.launcher.flinging:
            break
    assert not ws.launcher.flinging
    assert ws.launcher.scroll > off
