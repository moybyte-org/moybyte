"""Tests for launcher cart-list scrolling (#1) and the trackball cursor
sensitivity tweak (#2). The launcher shows a fixed VISIBLE window; with more
carts than fit, a touch-drag (or a held finger dwelling in an edge band) must
pan the window, and trackball/keyboard nav must still reveal the last cart.

These build the SAME shared console the device runs (runtime.host_app) and drive
it through ConsoleDriver -- mouse == touch, arrows == trackball -- exactly like
tests/test_v04_userland.py."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws_with_carts(tmp_path, n):
    """A workstation whose launcher holds n carts (> VISIBLE), via the real store."""
    from runtime import host_app, kid_carts

    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)        # seeds the system carts
    while len(ws.launcher.items) < n:                 # top up with extra carts
        i = len(ws.launcher.items)
        kid_carts.create("Extra %02d" % i, carts_dir,
                         src="def _draw():\n    cls(1)\n", type="app")
        ws.launcher.items = kid_carts.scan(carts_dir)
    ws.launcher.sel = 0
    ws.launcher.top = 0
    return ws


def test_more_carts_than_visible_seeded(tmp_path):
    ws = _ws_with_carts(tmp_path, 8)
    assert len(ws.launcher.items) >= 8
    assert len(ws.launcher.items) > ws.launcher.VISIBLE   # the scrolling precondition


# -- touch drag ------------------------------------------------------------

def test_touch_drag_up_scrolls_the_window(tmp_path):
    from runtime import console as C
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 8)
    drv = host_app.ConsoleDriver(ws)
    assert ws.launcher.top == 0

    # Finger down on a tile, then drag UP past several tile pitches -> later carts.
    y0 = C._LIST_Y0 + 8
    drv.touch(160, y0)
    drv.frame(1 / 30)
    for k in range(1, 5):
        drv.touch_drag(160, y0 - k * ws.launcher.TILE_PITCH)
        drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)

    assert ws.launcher.top > 0                                  # window panned down
    assert ws.launcher.top <= ws.launcher.max_top()             # clamped
    assert ws.screen == "launcher"                              # a drag did NOT open a cart


def test_touch_drag_down_scrolls_back(tmp_path):
    from runtime import console as C
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 8)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.top = ws.launcher.max_top()                     # start at the bottom
    assert ws.launcher.top > 0

    # Drag DOWN to scroll back toward the top. One stroke can only travel the screen
    # height (~4 pitches), so repeat the stroke (lift + re-press) until the window is
    # back at the top -- robust to however many system carts are seeded.
    y0 = C._LIST_Y0 + 8
    pitch = ws.launcher.TILE_PITCH
    for _stroke in range(ws.launcher.max_top() + 2):
        if ws.launcher.top == 0:
            break
        drv.touch(160, y0)
        drv.frame(1 / 30)
        for k in range(1, 5):
            drv.touch_drag(160, y0 + k * pitch)                 # drag DOWN
            drv.frame(1 / 30)
        drv.touch_up()
        drv.frame(1 / 30)

    assert ws.launcher.top == 0                                 # back to the top


def test_drag_scroll_clamps_at_both_ends(tmp_path):
    ws = _ws_with_carts(tmp_path, 8)
    lz = ws.launcher
    lz.scroll(-5)                                               # can't go above 0
    assert lz.top == 0
    lz.scroll(999)                                              # can't pass the bottom
    assert lz.top == lz.max_top()
    assert lz.top + lz.VISIBLE == len(lz.items)


# -- autoscroll on dwell ---------------------------------------------------

def test_dwell_in_bottom_edge_autoscrolls(tmp_path):
    from runtime import console as C
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    assert ws.launcher.top == 0

    # Drag DOWN into the bottom edge band (establishing a real drag), then dwell
    # there -> the list keeps autoscrolling. Autoscroll only fires once the gesture
    # is classified as a drag (a held-still tap must NOT autoscroll -- see below).
    y0 = C._LIST_Y0 + 8
    ey = C._LIST_BOTTOM - 4
    drv.touch(160, y0)
    drv.frame(1 / 30)
    drv.touch_drag(160, ey)            # move into the band -> moved=True
    drv.frame(1 / 30)
    for _ in range(40):
        drv.touch_drag(160, ey)        # dwell in the band
        drv.frame(1 / 30)
        if ws.launcher.top > 0:
            break
    drv.touch_up()
    assert ws.launcher.top > 0


def test_dwell_in_top_edge_autoscrolls_back(tmp_path):
    from runtime import console as C
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.top = ws.launcher.max_top()
    assert ws.launcher.top > 0

    # Drag UP into the top edge band, then dwell -> autoscroll back to the top.
    y0 = C._LIST_BOTTOM - 8
    ty = C._LIST_Y0 + 4
    drv.touch(160, y0)
    drv.frame(1 / 30)
    drv.touch_drag(160, ty)            # move into the TOP band -> moved=True
    drv.frame(1 / 30)
    for _ in range(40):
        drv.touch_drag(160, ty)        # dwell in the TOP band
        drv.frame(1 / 30)
        if ws.launcher.top == 0:
            break
    drv.touch_up()
    assert ws.launcher.top == 0


def test_held_tap_on_first_tile_opens_that_tile(tmp_path):
    """A finger HELD still on the FIRST visible tile (whose y-range overlaps the
    top autoscroll band) must open THAT tile, not autoscroll and open a neighbor
    that slid under the still finger (#1)."""
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.top = 0
    target = ws.launcher.items[0]["title"]

    r = ws.launcher.tile_rect(0)
    cx, cy = r[0] + r[2] // 2, r[1] + 4        # near the top edge, inside the band
    drv.touch(cx, cy)
    for _ in range(40):                         # hold, perfectly still
        drv.touch_drag(cx, cy)
        drv.frame(1 / 30)
    assert ws.launcher.top == 0                 # a still finger did NOT autoscroll
    drv.touch_up()
    drv.frame(1 / 30)

    assert ws.screen == "desktop"
    assert ws.launcher.items[ws.launcher.sel]["title"] == target


def test_held_tap_on_last_tile_opens_that_tile(tmp_path):
    """A finger HELD still on the LAST visible tile (overlapping the bottom band)
    must open THAT tile, not its neighbor (#1)."""
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.top = 0
    last_visible = ws.launcher.VISIBLE - 1
    target = ws.launcher.items[last_visible]["title"]

    r = ws.launcher.tile_rect(last_visible)
    cx, cy = r[0] + r[2] // 2, r[1] + r[3] - 4   # near the bottom edge, in the band
    drv.touch(cx, cy)
    for _ in range(40):                          # hold, perfectly still
        drv.touch_drag(cx, cy)
        drv.frame(1 / 30)
    assert ws.launcher.top == 0                  # a still finger did NOT autoscroll
    drv.touch_up()
    drv.frame(1 / 30)

    assert ws.screen == "desktop"
    assert ws.launcher.items[ws.launcher.sel]["title"] == target


def test_one_px_down_drag_does_not_scroll_or_misopen(tmp_path):
    """A 1px DOWNWARD move must NOT scroll a row (the floor-division bug made
    -1 // 40 == -1, scrolling a whole row) and must still open the tapped tile (#2)."""
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 12)
    drv = host_app.ConsoleDriver(ws)
    # Start mid-list so a wrongful scroll(-1) actually moves `top` (at top=0 it
    # would clamp to 0 and hide the bug). Tap visible row 1 = item index top+1.
    ws.launcher.top = 3
    idx = ws.launcher.top + 1
    target = ws.launcher.items[idx]["title"]

    r = ws.launcher.tile_rect(idx)
    cx, cy = r[0] + r[2] // 2, r[1] + r[3] // 2
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    drv.touch_drag(cx, cy + 1)            # 1px DOWN -- below the moved threshold
    drv.frame(1 / 30)
    assert ws.launcher.top == 3           # no row scrolled (floor bug would do -1)
    drv.touch_up()
    drv.frame(1 / 30)

    assert ws.screen == "desktop"
    assert ws.launcher.items[ws.launcher.sel]["title"] == target   # the RIGHT cart


def test_up_and_down_drag_thresholds_are_symmetric(tmp_path):
    """One full TILE_PITCH each way steps exactly one row; sub-pitch moves step
    none. Up and down must be symmetric (no floor-division asymmetry, #2)."""
    from runtime import console as C
    from runtime import host_app

    pitch = C.Launcher.TILE_PITCH

    def steps_after_drag(start_top, dy):
        ws = _ws_with_carts(tmp_path, 12)
        drv = host_app.ConsoleDriver(ws)
        ws.launcher.top = start_top
        y0 = C._LIST_Y0 + 8
        drv.touch(160, y0)
        drv.frame(1 / 30)
        drv.touch_drag(160, y0 + dy)
        drv.frame(1 / 30)
        moved = ws.launcher.top - start_top
        drv.touch_up()
        return moved

    mid = C.Launcher(_ws_with_carts(tmp_path, 12).launcher.items).max_top() // 2

    # Just under a pitch each way -> no step (this is what the floor bug broke).
    assert steps_after_drag(mid, (pitch - 1)) == 0
    assert steps_after_drag(mid, -(pitch - 1)) == 0
    # Exactly one pitch each way -> exactly one row, symmetric magnitude.
    up = steps_after_drag(mid, -pitch)                  # finger moves UP -> scroll down (+1)
    down = steps_after_drag(mid, pitch)                 # finger moves DOWN -> scroll up (-1)
    assert up == 1
    assert down == -1


# -- arrows / trackball nav still scrolls ----------------------------------

def test_arrows_nav_reveals_last_cart(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 8)
    drv = host_app.ConsoleDriver(ws)
    last = len(ws.launcher.items) - 1

    for _ in range(len(ws.launcher.items) + 2):
        drv.press("down")
        drv.frame(1 / 30)
        drv.frame(1 / 30)        # release frame: each press is a discrete edge
        if ws.launcher.sel == last:
            break

    assert ws.launcher.sel == last
    # The selection passed the visible window, so the window followed it: the last
    # tile is on screen and the down arrow affordance is gone.
    assert ws.launcher.tile_rect(last) is not None
    assert ws.launcher.top == ws.launcher.max_top()
    assert not (ws.launcher.top + ws.launcher.VISIBLE < len(ws.launcher.items))


def test_arrow_affordances_track_scroll_position(tmp_path):
    ws = _ws_with_carts(tmp_path, 8)
    lz = ws.launcher

    # At the top: only the DOWN arrow should be implied (top==0, more below).
    assert lz.top == 0
    assert lz.top + lz.VISIBLE < len(lz.items)

    lz.scroll(2)                                   # somewhere in the middle
    assert lz.top > 0 and lz.top + lz.VISIBLE < len(lz.items)   # both arrows

    lz.scroll(lz.max_top())                         # at the bottom: only UP
    assert lz.top > 0
    assert not (lz.top + lz.VISIBLE < len(lz.items))


# -- tap still opens a cart ------------------------------------------------

def test_tap_without_drag_opens_cart(tmp_path):
    from runtime import console as C
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 8)
    drv = host_app.ConsoleDriver(ws)
    # Tap (down + up, no movement) on the second visible tile -> opens it.
    r = ws.launcher.tile_rect(1)
    cx, cy = r[0] + r[2] // 2, r[1] + r[3] // 2
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "desktop"                  # the tap opened the cart


# -- trackball sensitivity (#2) --------------------------------------------

def test_cursor_delta_is_faster_and_accelerates():
    from runtime import console as C

    assert C._cursor_delta(0) == 0
    # A single pulse now moves more than one pixel (snappier).
    assert C._cursor_delta(1) == C._CURSOR_BASE + C._CURSOR_ACCEL
    assert C._cursor_delta(1) > 1
    # Symmetric in sign.
    assert C._cursor_delta(-1) == -C._cursor_delta(1)
    # Super-linear: a fast roll (more pulses/frame) moves more than N single pulses.
    assert C._cursor_delta(6) > 6 * C._cursor_delta(1)
    # A brisk flick crosses a large fraction of the 320px screen in a few rolls.
    assert C._cursor_delta(8) >= 320 // 3
