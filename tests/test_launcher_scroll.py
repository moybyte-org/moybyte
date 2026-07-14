"""Tests for the desktop home (#28): the cart launcher is the Library SHELF --
a card grid that SCROLLS continuously LEFT-RIGHT (a shelf slides sideways; no
pages) with the one tall featured MAKE card pinned at the head of the list, on
EVERY tier (the 320x240 baseline included). With more carts than fit the
viewport, touch drag / footer arrows / keyboard nav must reveal the last cart,
a clean tap must open a card, and a drag must NEVER open one. Also covers the
trackball cursor sensitivity tweak (#2).

These build the SAME shared console the device runs (runtime.host_app) and drive
it through ConsoleDriver -- mouse == touch, arrows == trackball -- exactly like
tests/test_v04_userland.py."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws_with_carts(tmp_path, n):
    """A workstation whose launcher holds n carts (> one viewport), via the real
    store."""
    from runtime import host_app, moy_carts

    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)        # seeds the system carts
    while len(ws.launcher.items) < n:                 # top up with extra carts
        i = len(ws.launcher.items)
        moy_carts.create("Extra %02d" % i, carts_dir,
                         src="def _draw():\n    cls(1)\n", type="app")
        ws.launcher.items = moy_carts.scan(carts_dir)
    ws.launcher.sel = 0
    ws.launcher.scroll = 0
    return ws


def test_more_carts_than_one_viewport_seeded(tmp_path):
    ws = _ws_with_carts(tmp_path, 10)
    assert len(ws.launcher.items) >= 10
    assert ws.launcher.max_scroll() > 0              # the scrolling precondition


# -- grid layout (the shelf packing) ----------------------------------------

def test_tile_rects_lay_out_in_the_shelf_packing(tmp_path):
    ws = _ws_with_carts(tmp_path, 10)
    lz = ws.launcher
    lay = lz.layout
    # Slot 0 is the ONE tall featured card (column 0, spanning both rows).
    r0 = lz.tile_rect(0)
    assert r0[3] == 2 * lay.lib_card_h + lay.lib_gap
    # Slot 1 tops the next column; slot 2 sits below it in the SAME column.
    r1 = lz.tile_rect(1)
    assert r1[0] > r0[0] and r1[1] == r0[1]              # next column, same row
    r2 = lz.tile_rect(2)
    assert r2[1] > r1[1] and r2[0] == r1[0]              # below, same column
    # Slot 3 starts the following column, back at the top row.
    r3 = lz.tile_rect(3)
    assert r3 is None or (r3[0] > r1[0] and r3[1] == r1[1])


def test_tile_at_hit_tests_the_grid(tmp_path):
    ws = _ws_with_carts(tmp_path, 10)
    lz = ws.launcher
    for i in range(len(lz.items)):                       # every visible card
        r = lz.tile_rect(i)
        if r is None:
            continue
        gx, gy, gw, gh = lz.layout.lib_grid
        cx, cy = r[0] + r[2] // 2, r[1] + r[3] // 2
        if not (gx <= cx < gx + gw):
            continue                                     # centre clipped away
        assert lz.tile_at(cx, cy) == i


# -- continuous scrolling ----------------------------------------------------

def test_scroll_reveals_later_carts(tmp_path):
    ws = _ws_with_carts(tmp_path, 14)
    lz = ws.launcher
    assert lz.scroll == 0
    last = len(lz.items) - 1
    assert lz.tile_rect(last) is None                    # right of the viewport
    lz.scroll = lz.max_scroll()
    assert lz.tile_rect(last) is not None                # now visible


def test_scroll_clamps_at_both_ends(tmp_path):
    ws = _ws_with_carts(tmp_path, 10)
    lz = ws.launcher
    lz.scroll_cols(-5)
    assert lz.scroll == 0
    lz.scroll_cols(99)
    assert lz.scroll == lz.max_scroll()


def test_footer_arrow_taps_nudge_the_scroll(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    lay = ws.layout
    assert ws.launcher.scroll == 0
    x, y, w, h = lay.scroll_rt
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.launcher.scroll == min(lay.lib_step, ws.launcher.max_scroll())
    x, y, w, h = lay.scroll_lt
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.launcher.scroll == 0


def test_touch_drag_scrolls_the_grid(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    gx, gy, gw, gh = ws.layout.lib_grid
    cx, cy = gx + gw - 10, gy + gh // 2
    drv.touch(cx, cy)                                    # finger down on a card
    drv.frame(1 / 30)
    for step in range(1, 6):                             # drag LEFT past the slop
        drv.touch_drag(cx - step * 12, cy)
        drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.launcher.scroll > 0                        # the grid followed the finger
    assert ws.screen == "launcher"                       # and the drag opened NOTHING


def test_drag_never_launches_the_pressed_card(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    r = ws.launcher.tile_rect(1)                         # a real cart's card
    cx, cy = r[0] + r[2] // 2, r[1] + r[3] // 2
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    drv.touch_drag(cx - 40, cy)                          # well past the slop
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "launcher"                       # release after a drag: no run


def test_drag_partial_repaint_is_pixel_faithful(tmp_path):
    """The drag fast path (#58/#66): once the statics streak is armed, mid-drag
    frames skip the wallpaper + panel chrome (the ~100ms of a full home repaint
    on glass) and repaint only the grid band + bar -- and the composed frame
    must be byte-identical to a FULL repaint of the same state."""
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    for _ in range(80):                       # settle covers; arm the streak
        drv.frame(1 / 30)
    calls = [0]
    orig = ws.wallpaper.draw

    def spy(dt):
        calls[0] += 1
        return orig(dt)

    ws.wallpaper.draw = spy
    gx, gy, gw, gh = ws.layout.lib_grid
    cx, cy = gx + gw - 10, gy + gh // 2
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    lastx = cx
    for step in range(1, 8):                  # a real finger drag
        lastx = cx - step * 9
        drv.touch_drag(lastx, cy)
        drv.frame(1 / 30)
    assert ws.launcher.dragging
    n_mid = calls[0]
    lastx -= 9
    drv.touch_drag(lastx, cy)                 # one more eligible drag frame
    drv.frame(1 / 30)
    assert calls[0] == n_mid                  # partial: wallpaper NOT redrawn
    partial = bytes(ws.sys_canvas.buf)
    scroll = ws.launcher.scroll
    # Force the FULL path for the identical state and compare the pixels.
    ws.launcher_layer._full_streak = 0
    ws.mark_dirty()
    drv.touch_drag(lastx, cy)                 # same pos: scroll unchanged
    drv.frame(1 / 30)
    assert calls[0] == n_mid + 1              # the forced frame went FULL
    assert ws.launcher.scroll == scroll
    full = bytes(ws.sys_canvas.buf)
    row = ws.sys_canvas.w * ws.layout.status_h    # compare below the bar (the
    assert full[row:] == partial[row:]            # clock may tick between)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "launcher"            # and the release opened nothing


# -- grid keyboard / trackball nav -----------------------------------------

def test_arrows_nav_reveals_last_cart(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    last = len(ws.launcher.items) - 1

    # Step right repeatedly; the grid clamps at the last cart and the scroll follows.
    for _ in range(len(ws.launcher.items) + 4):
        drv.press("right")
        drv.frame(1 / 30)
        drv.frame(1 / 30)        # release frame: each press is a discrete edge
        if ws.launcher.sel == last:
            break

    assert ws.launcher.sel == last
    assert ws.launcher.tile_rect(last) is not None       # the scroll followed the cursor
    assert ws.launcher.scroll == ws.launcher.max_scroll()


def test_down_arrow_steps_within_a_column(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 1                                  # top of the first cart column
    drv.press("down")
    drv.frame(1 / 30)
    assert ws.launcher.sel == 2                          # same column, row 1
    drv.press("up")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.launcher.sel == 1                           # back up a row


def test_vertical_arrows_stay_on_the_tall_card(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    assert ws.launcher.sel == 0                          # the tall MAKE card
    drv.press("down")
    drv.frame(1 / 30)
    # The tall card spans both rows, so up/down have nowhere to go.
    assert ws.launcher.sel == 0
    drv.press("right")                                   # right hops a column
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.launcher.sel == 1
    drv.press("left")                                    # and left comes back
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.launcher.sel == 0


def test_right_arrow_hops_a_column(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    drv.press("right")
    drv.frame(1 / 30)
    drv.frame(1 / 30)        # release frame: each press is a discrete edge
    assert ws.launcher.sel == 1
    drv.press("right")                                   # next column's TOP card
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.launcher.sel == 3


# -- tap still opens a cart ------------------------------------------------

def test_tap_icon_opens_cart(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    # Tap the second card -> RUNS it on RELEASE. The locked model (spec
    # shell_ux_v1.md): a launcher tap RUNS the cart, always, for every type --
    # no maker/player tap_mode dispatch.
    r = ws.launcher.tile_rect(1)
    cx, cy = r[0] + r[2] // 2, r[1] + r[3] // 2
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "desktop"                        # the tap RAN the cart
    assert ws.launcher.sel == 1


def test_tap_a_scrolled_to_card_opens_the_right_cart(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.scroll = ws.launcher.max_scroll()        # end of the shelf
    last = len(ws.launcher.items) - 1
    target = ws.launcher.items[last]["title"]
    r = ws.launcher.tile_rect(last)
    cx, cy = r[0] + r[2] // 2, r[1] + r[3] // 2
    # The locked model (spec shell_ux_v1.md): a launcher tap RUNS the cart, always.
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    # The RIGHT card opened (a game runs as "desktop"; a system app cart opens
    # as its own screen kind -- either way the launcher was left).
    assert ws.screen != "launcher"
    assert ws.launcher.items[ws.launcher.sel]["title"] == target


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
