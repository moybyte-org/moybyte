"""The stale-by-N parameterization (prep for the P4 triple-framebuffer render
overlap, #58): every partial-paint mechanism that trusted the ping-pong's
literal 2 now derives its horizon from the canvas's RETAINED_FRAMES --
streak gates >= N, the full-paint debt N-1, the gesture damage trail N
entries, the scroll ring capped at the largest N anywhere.

Behavior at N<=2 is unchanged (the helper FLOORS at 2 -- the full suite is
that regression net); these tests pin what N=3 must change.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws(tmp_path, **kw):
    from runtime import host_app
    kw.setdefault("sys_size", (1024, 600))
    kw.setdefault("font_scale", 2)
    kw.setdefault("windowed", True)
    return host_app.build_workstation(str(tmp_path / "carts"), **kw)


def test_helper_floors_at_two(tmp_path):
    """Host canvas declares RETAINED_FRAMES = 1; the horizon still gates at 2
    (today's conservative behavior, byte-identical goldens). An N=3 canvas
    raises it to 3."""
    from runtime.launcher_layer import _retained_n
    ws = _ws(tmp_path)
    assert ws.sys_canvas.RETAINED_FRAMES == 1      # the host contract
    assert ws.wm._retained_n() == 2
    assert _retained_n(ws.sys_canvas) == 2
    ws.sys_canvas.RETAINED_FRAMES = 3              # instance shadow
    assert ws.wm._retained_n() == 3
    assert _retained_n(ws.sys_canvas) == 3


def test_full_paint_debt_scales_to_n_minus_one(tmp_path):
    """A change painted on an N-buffer root leaves N-1 other buffers owing a
    full paint before quiet partial frames may resume."""
    ws = _ws(tmp_path)
    from runtime import host_app
    drv = host_app.ConsoleDriver(ws)
    drv.frame(0.0)                                 # boots to the desk
    ws.sys_canvas.RETAINED_FRAMES = 3
    ws.wm.draw_stack()                             # not quiet (no game): a change
    assert ws.wm._full_debt == 2                   # N-1
    ws.sys_canvas.RETAINED_FRAMES = 1
    ws.wm.draw_stack()
    assert ws.wm._full_debt == 1                   # floored N=2 -> today's 1


def test_gesture_hist_seeds_n_entries(tmp_path):
    """The drag restore unions the last N frames' damage extents -- the seed
    must cover every physical buffer's pre-gesture stamp."""
    ws = _ws(tmp_path)
    from runtime import host_app
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    drv.frame(0.0)
    win = ws.wm._wins["settings"]
    gx, gy = win.x + win.w // 2, win.y + 4
    ws.sys_canvas.RETAINED_FRAMES = 3
    drv.touch(gx, gy)
    drv.frame(0.0)
    drv.touch_drag(gx + 80, gy + 50)
    drv.frame(0.0)                                 # the drag engages
    assert ws.wm._drag is not None
    assert len(ws.wm._gesture_hist) == 3


def test_scroll_ring_keeps_the_largest_n(tmp_path):
    """note_painted has no canvas to read N from, so the ring keeps
    _MAX_RETAINED entries -- trimming to 2 would leave len < k forever on an
    N=3 root and silently disable scroll-as-blit there."""
    from runtime.ui import ScrollRegion, _MAX_RETAINED
    assert _MAX_RETAINED >= 3
    r = ScrollRegion()
    r.set((0, 0, 100, 100), 1000)
    for f in range(5):
        r.note_painted(f)
    assert len(r._painted) == _MAX_RETAINED
