"""Tests for the desktop home (#28): the cart launcher is now a PAGED ICON GRID
(Picotron/TIC-80-style desktop), not a flat vertical strip. With more carts than
fit one page, grid nav + page chevrons must reveal the last cart, and a tap on an
icon must open it. Also covers the trackball cursor sensitivity tweak (#2).

These build the SAME shared console the device runs (runtime.host_app) and drive
it through ConsoleDriver -- mouse == touch, arrows == trackball -- exactly like
tests/test_v04_userland.py."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws_with_carts(tmp_path, n):
    """A workstation whose launcher holds n carts (> one page), via the real store."""
    from runtime import host_app, moy_carts

    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)        # seeds the system carts
    while len(ws.launcher.items) < n:                 # top up with extra carts
        i = len(ws.launcher.items)
        moy_carts.create("Extra %02d" % i, carts_dir,
                         src="def _draw():\n    cls(1)\n", type="app")
        ws.launcher.items = moy_carts.scan(carts_dir)
    ws.launcher.sel = 0
    ws.launcher.page = 0
    return ws


def test_more_carts_than_one_page_seeded(tmp_path):
    ws = _ws_with_carts(tmp_path, 10)
    assert len(ws.launcher.items) >= 10
    assert len(ws.launcher.items) > ws.launcher.PAGE     # the paging precondition
    assert ws.launcher.max_page() >= 1


# -- grid layout -----------------------------------------------------------

def test_tile_rects_lay_out_in_a_grid(tmp_path):
    ws = _ws_with_carts(tmp_path, 10)
    lz = ws.launcher
    # Page 0 shows the first PAGE tiles; off-page indices return None.
    assert lz.tile_rect(0) is not None
    assert lz.tile_rect(lz.PAGE - 1) is not None
    assert lz.tile_rect(lz.PAGE) is None                 # spills to the next page
    # Row 0 cols are left-to-right; row 1 sits below row 0.
    r0 = lz.tile_rect(0)
    r1 = lz.tile_rect(1)
    rrow2 = lz.tile_rect(lz.COLS)
    assert r1[0] > r0[0] and r1[1] == r0[1]              # next column, same row
    assert rrow2[1] > r0[1] and rrow2[0] == r0[0]        # next row, first column


def test_tile_at_hit_tests_the_grid(tmp_path):
    ws = _ws_with_carts(tmp_path, 10)
    lz = ws.launcher
    for i in range(lz.PAGE):
        x, y, w, h = lz.tile_rect(i)
        assert lz.tile_at(x + w // 2, y + h // 2) == i


# -- paging ----------------------------------------------------------------

def test_flip_page_reveals_later_carts(tmp_path):
    ws = _ws_with_carts(tmp_path, 10)
    lz = ws.launcher
    assert lz.page == 0
    last = len(lz.items) - 1
    assert lz.tile_rect(last) is None                    # not on page 0
    lz.flip_page(1)
    assert lz.page == 1
    assert lz.tile_rect(last) is not None                # now visible


def test_flip_page_clamps_at_both_ends(tmp_path):
    ws = _ws_with_carts(tmp_path, 10)
    lz = ws.launcher
    lz.flip_page(-5)
    assert lz.page == 0
    lz.flip_page(99)
    assert lz.page == lz.max_page()


def test_page_chevron_taps_flip_pages(tmp_path):
    from runtime import console as C
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    assert ws.launcher.page == 0
    drv.click(C._PAGE_NEXT[0] + 4, C._PAGE_NEXT[1] + 10)
    drv.frame(1 / 30)
    assert ws.launcher.page == 1
    drv.click(C._PAGE_PREV[0] + 4, C._PAGE_PREV[1] + 10)
    drv.frame(1 / 30)
    assert ws.launcher.page == 0


# -- grid keyboard / trackball nav -----------------------------------------

def test_arrows_nav_reveals_last_cart(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    last = len(ws.launcher.items) - 1

    # Step right repeatedly; the grid clamps at the last cart and the page follows.
    for _ in range(len(ws.launcher.items) + 4):
        drv.press("right")
        drv.frame(1 / 30)
        drv.frame(1 / 30)        # release frame: each press is a discrete edge
        if ws.launcher.sel == last:
            break

    assert ws.launcher.sel == last
    assert ws.launcher.tile_rect(last) is not None       # the page followed the cursor
    assert ws.launcher.page == ws.launcher.max_page()


def test_down_arrow_steps_a_grid_row(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    assert ws.launcher.sel == 0
    drv.press("down")
    drv.frame(1 / 30)
    assert ws.launcher.sel == ws.launcher.COLS           # one row down
    drv.press("up")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.launcher.sel == 0                           # back up a row


def test_right_arrow_steps_a_column(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    drv.press("right")
    drv.frame(1 / 30)
    assert ws.launcher.sel == 1


# -- tap still opens a cart ------------------------------------------------

def test_tap_icon_opens_cart(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    # Tap the second icon -> opens it (maker default: into the Editor, spec Section 4).
    r = ws.launcher.tile_rect(1)
    cx, cy = r[0] + r[2] // 2, r[1] + r[3] // 2
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "menu"                           # the tap opened the cart (Editor)
    assert ws.launcher.sel == 1


def test_tap_icon_on_second_page_opens_the_right_cart(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.flip_page(1)
    first_on_page = ws.launcher.page * ws.launcher.PAGE
    target = ws.launcher.items[first_on_page]["title"]
    r = ws.launcher.tile_rect(first_on_page)
    cx, cy = r[0] + r[2] // 2, r[1] + r[3] // 2
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "menu"                           # opened (maker default: Editor)
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
