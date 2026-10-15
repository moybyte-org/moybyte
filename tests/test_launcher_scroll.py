"""Tests for the home's carousel (docs/shell_layout_2026-10.md §12): one row of
square cards that slides sideways under the tab row, the card at the centre
larger, on EVERY tier (the 320x240 baseline included). With more carts than fit
the band, touch drag / a fling / keyboard nav must reveal the last cart, the
row must come to rest with a card at the centre, a clean tap on the centre card
must open it and a tap on another card bring it to the centre, and a drag must
NEVER open one. Also covers the trackball cursor sensitivity tweak (#2).

These build the SAME shared console the device runs (runtime.host_app) and drive
it through ConsoleDriver -- mouse == touch, arrows == trackball -- exactly like
tests/test_v04_userland.py."""

from pathlib import Path
from ws_helpers import build_ws_with_shelf as _ws_with_carts

ROOT = Path(__file__).resolve().parent.parent


def test_more_carts_than_one_viewport_seeded(tmp_path):
    ws = _ws_with_carts(tmp_path, 10)
    assert len(ws.launcher.items) >= 10
    assert ws.launcher.max_scroll() > 0              # the scrolling precondition


def _settle(drv, n=120):
    """Frames until the row is still (the glide and any fling spent)."""
    ws = drv.ws
    for _ in range(n):
        drv.frame(1 / 30)
        if not ws.launcher.moving:
            drv.frame(1 / 30)
            return
    raise AssertionError("the carousel never came to rest")


# -- the row ------------------------------------------------------------------

def test_cards_lay_out_in_one_row_the_centre_card_largest(tmp_path):
    ws = _ws_with_carts(tmp_path, 10)
    lz = ws.launcher
    bx, by, bw, bh = lz.band_rect()
    r0 = lz.tile_rect(0)                       # scroll 0: card 0 at the centre
    r1 = lz.tile_rect(1)
    assert r0[0] + r0[2] // 2 == bx + bw // 2
    assert r0[2] == r0[3] and r1[2] == r1[3]   # square cards
    assert r1[2] == lz.card_side()             # one pitch away: scale 1.0
    assert r0[2] == lz.card_side() * 3 // 2    # the centre at the board's 1.5
    assert r0[1] + r0[3] // 2 == r1[1] + r1[3] // 2   # one centre line
    assert r1[0] >= r0[0] + r0[2]              # neighbours never overlap it
    for r in (r0, r1):
        assert by <= r[1] and r[1] + r[3] <= by + bh


def test_a_cards_size_is_a_function_of_its_distance_alone(tmp_path):
    ws = _ws_with_carts(tmp_path, 10)
    lz = ws.launcher
    p = lz.pitch()
    sizes = []
    for off in (0, p // 4, p // 2, p):
        lz._scroll = 2 * p + off
        sizes.append(lz.tile_rect(2)[2])
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[0] == lz.card_side() * 3 // 2 and sizes[-1] == lz.card_side()
    lz._scroll = 2 * p - p // 4                # the same distance the other side
    assert lz.tile_rect(2)[2] == sizes[1]


def test_the_degradation_order_is_the_wms_constant(tmp_path):
    """§12's order, a per-board WM constant: "rest" draws every card at 1.0
    while the row moves and scales the centre once settled; "one" never
    scales; a lower HOME_SCALE is the reduced scale."""
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    lz = ws.launcher
    card = lz.card_side()
    drv.frame(1 / 30)
    assert (lz.motion, lz.scale) == ("full", 1.5)        # every board's verdict
    ws.wm.HOME_SCALE = 1.25
    ws.mark_dirty()
    drv.frame(1 / 30)
    assert lz.tile_rect(0)[2] == int(card * 1.25 + 0.5)
    ws.wm.HOME_SCALE = 1.5
    ws.wm.HOME_MOTION = "rest"
    drv.press("right")
    drv.frame(1 / 30)
    assert lz.moving
    assert all(r[2] == card for _i, r in lz._visible())
    _settle(drv)
    assert lz.tile_rect(1)[2] == card * 3 // 2
    ws.wm.HOME_MOTION = "one"
    ws.mark_dirty()
    drv.frame(1 / 30)
    assert lz.tile_rect(1)[2] == card


def test_tile_at_hit_tests_the_row(tmp_path):
    ws = _ws_with_carts(tmp_path, 10)
    lz = ws.launcher
    bx, by, bw, bh = lz.band_rect()
    for i in range(len(lz.items)):             # every visible card
        r = lz.tile_rect(i)
        if r is None:
            continue
        cx, cy = r[0] + r[2] // 2, r[1] + r[3] // 2
        if not (bx <= cx < bx + bw):
            continue                           # centre clipped away
        assert lz.tile_at(cx, cy) == i


# -- scrolling -------------------------------------------------------------------

def test_scroll_reveals_later_carts(tmp_path):
    ws = _ws_with_carts(tmp_path, 14)
    lz = ws.launcher
    assert lz.scroll == 0
    last = len(lz.items) - 1
    assert lz.tile_rect(last) is None                    # right of the band
    lz.scroll = lz.max_scroll()
    assert lz.tile_rect(last) is not None                # now visible


def test_scroll_clamps_at_both_ends(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    lz = ws.launcher
    lz.scroll_cols(-5)
    ws.mark_dirty()
    _settle(drv)
    assert lz.scroll == 0
    lz.scroll_cols(99)
    ws.mark_dirty()
    _settle(drv)
    assert lz.scroll == lz.max_scroll()
    assert lz.sel == len(lz.items) - 1                   # settled on the last card


def test_touch_drag_scrolls_the_row_and_rests_on_a_card(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    bx, by, bw, bh = ws.launcher.band_rect()
    cx, cy = bx + bw - 10, by + bh // 2
    drv.touch(cx, cy)                                    # finger down on the row
    drv.frame(1 / 30)
    for step in range(1, 6):                             # drag LEFT past the slop
        drv.touch_drag(cx - step * 12, cy)
        drv.frame(1 / 30)
    drv.touch_up()
    _settle(drv)
    lz = ws.launcher
    assert lz.scroll > 0                                 # the row followed the finger
    assert lz.scroll % lz.pitch() == 0                   # and rests on a card
    assert lz.sel == lz.scroll // lz.pitch()             # the one at the centre
    assert ws.screen == "launcher"                       # and the drag opened NOTHING


def test_sel_and_the_zone_change_only_on_settle(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    lz = ws.launcher
    gen = lz.zone_gen
    drv.press("right")
    drv.frame(1 / 30)
    assert lz.moving and lz.sel == 0 and lz.zone_gen == gen
    _settle(drv)
    assert lz.sel == 1 and lz.zone_gen == gen + 1


def test_drag_never_launches_the_pressed_card(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    r = ws.launcher.tile_rect(0)                         # the centre card
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
    frames skip the wallpaper + tab row (the ~100ms of a full home repaint on
    glass) and repaint only the carousel band + bar -- and the composed frame
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
    bx, by, bw, bh = ws.launcher.band_rect()
    cx, cy = bx + bw - 10, by + bh // 2
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
    partial = bytes(ws.sys_canvas._buf)
    scroll = ws.launcher.scroll
    # Force the FULL path for the identical state and compare the pixels.
    ws.launcher_layer._full_streak = 0
    ws.mark_dirty()
    drv.touch_drag(lastx, cy)                 # same pos: scroll unchanged
    drv.frame(1 / 30)
    assert calls[0] == n_mid + 1              # the forced frame went FULL
    assert ws.launcher.scroll == scroll
    full = bytes(ws.sys_canvas._buf)
    row = ws.sys_canvas.w * ws.layout.status_h    # compare below the bar (the
    assert full[row:] == partial[row:]            # clock may tick between)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "launcher"            # and the release opened nothing


# -- keyboard / trackball nav ---------------------------------------------------

def test_arrows_nav_reveals_last_cart(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    last = len(ws.launcher.items) - 1

    # Step right repeatedly; the row clamps at the last cart and rests there.
    for _ in range(len(ws.launcher.items) + 4):
        drv.press("right")
        drv.frame(1 / 30)
        drv.frame(1 / 30)        # release frame: each press is a discrete edge
    _settle(drv)
    assert ws.launcher.sel == last
    assert ws.launcher.tile_rect(last) is not None       # the row followed
    assert ws.launcher.scroll == ws.launcher.max_scroll()


def test_right_arrow_glides_one_card(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    drv.press("right")
    drv.frame(1 / 30)
    assert ws.launcher.flinging                          # the glide runs as a fling
    _settle(drv)
    assert ws.launcher.sel == 1
    drv.press("right")
    drv.frame(1 / 30)
    _settle(drv)
    assert ws.launcher.sel == 2
    drv.press("left")
    drv.frame(1 / 30)
    _settle(drv)
    assert ws.launcher.sel == 1


def test_up_takes_the_keys_to_the_tab_row(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    drv.press("up")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.launcher_layer._tab_focus
    drv.press("right")                                   # the next tab
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.launcher.tab == "mine"
    drv.press("down")                                    # back to the row
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert not ws.launcher_layer._tab_focus
    assert ws.launcher.tab == "mine"


# -- a tap -----------------------------------------------------------------------

def _tap_card(drv, i):
    r = drv.ws.launcher.tile_rect(i)
    cx, cy = r[0] + r[2] // 2, r[1] + r[3] // 2
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)


def test_tap_the_centre_card_opens_it(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    game = next(i for i, it in enumerate(ws.launcher.items)
                if it.get("path") and it.get("type") == "game")
    ws.launcher.sel = game                               # at the centre
    drv.frame(1 / 30)
    _tap_card(drv, game)
    assert ws.screen == "desktop"                        # the tap RAN the cart
    assert ws.launcher.sel == game


def test_tap_another_card_brings_it_to_the_centre(tmp_path):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, 14)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = len(ws.launcher.items) - 2
    drv.frame(1 / 30)
    last = len(ws.launcher.items) - 1
    target = ws.launcher.items[last]["title"]
    _tap_card(drv, last)
    assert ws.screen == "launcher"                       # it glides, it does not run
    _settle(drv)
    assert ws.launcher.sel == last
    _tap_card(drv, last)                                 # now at the centre: open
    assert ws.screen != "launcher"
    assert ws.launcher.items[ws.launcher.sel]["title"] == target


# -- the grid arrangement (the layout's `home`) -------------------------------

def test_the_grid_arrangement_shares_the_tabs_and_the_verbs(tmp_path):
    from runtime import host_app
    from runtime.launcher_layer import Carousel, Launcher

    ws = _ws_with_carts(tmp_path, 10)
    drv = host_app.ConsoleDriver(ws)
    assert type(ws.launcher) is Carousel                 # the default
    ws.set_home_tab("apps")
    ws.launcher.sel = 2
    want = ws.launcher.selected()
    ws.set_home_arrangement("grid")
    g = ws.launcher
    assert type(g) is Launcher and g.tab == "apps" and g.selected() is want
    home = ws.launcher_layer
    assert home.zone_verbs() == ("open",)                # the same bar verbs
    assert [t for t, _r, _on in home.tab_rects()] == ["games", "mine", "apps", "make"]
    ws.set_home_tab("games")
    drv.frame(1 / 30)
    assert not g._featured()                             # no Make tile: uniform columns
    drv.press("down")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert g.sel == 1                                    # down a row
    drv.press("up")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    drv.press("up")                                      # the top row: up to the tabs
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert home._tab_focus
    home._tab_focus = False
    game = next(i for i, it in enumerate(g.items) if it.get("type") == "game")
    r = g.tile_rect(game)
    drv.touch(r[0] + r[2] // 2, r[1] + r[3] // 2)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "desktop" and g.sel == game     # a card's tap runs it
    ws._exit_to_caller()
    ws.set_home_tab("make")
    assert g._featured() and g.tile_rect(0)[3] > g.tile_rect(1)[3]   # "+ New" tall


def test_a_layouts_home_picks_the_arrangement(tmp_path):
    import json

    import moy_ui
    from runtime.launcher_layer import Carousel, Launcher

    ws = _ws_with_carts(tmp_path, 4)
    rc, _n = moy_ui.theme_install(json.dumps({"format": 2, "name": "hg", "layout": {
        "home": "grid", "bars": [{"edge": "top", "left": ["zone"],
                                  "right": ["clock", "close", "menu"]}]}}).encode())
    assert rc == 0
    ws.system.set("layout_theme", "hg")
    ws.look.apply_layout()
    assert ws.look.home == "grid" and type(ws.launcher) is Launcher
    ws.system.set("layout_theme", None)
    ws.look.apply_layout()
    assert ws.look.home == "carousel" and type(ws.launcher) is Carousel
    moy_ui.theme_uninstall()


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
