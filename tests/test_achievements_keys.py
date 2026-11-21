"""Settings' Achievements page by its keys (runtime/system/achievements_ui.py):
what a person does with it, never its internals. Focus opens on the first
badge, the arrows move across a row and down the grid, Enter opens the
focused badge's popover (its name, how it is found and the day it was, or
its goal's count), Back closes the popover onto its badge and then leaves
the page; a secret is named Secret until found."""

import pytest

import moybyte  # noqa: F401  (the frozen device names, before the console)
from ws_helpers import build_desktop_ws, build_ws, make_drv, no_unlock_banner, settle_badges

_DT = 1 / 30


def _page(tmp_path, desk=False, played=()):
    ws = no_unlock_banner((build_desktop_ws if desk else build_ws)(tmp_path))
    drv = make_drv(ws)
    ws.ach.seed({"first_open": 20261012})
    for k in played:
        ws.ach.played(k)
    settle_badges(ws)
    if desk:
        ws.home.open_desk()
    ws.open_settings()
    sl = ws.settings_layer
    sl.open_page("achievements")
    for _ in range(3):
        drv.frame(_DT)
    return ws, drv, sl, sl.achievements


def _keys(drv, *names):
    for k in names:
        drv.press(k)
        drv.frame(_DT)
        drv.frame(_DT)


@pytest.mark.parametrize("desk", (False, True))
def test_focus_opens_on_the_first_badge_and_the_arrows_walk_the_grid(tmp_path, desk):
    ws, drv, sl, pg = _page(tmp_path, desk)
    assert sl.page == "achievements" and sl.page_title() == "Achievements"
    assert pg.focused() == "system first_open"
    _keys(drv, "right")
    second = pg.focused()
    assert second not in (None, "system first_open")
    _keys(drv, "left")
    assert pg.focused() == "system first_open"
    _keys(drv, "down")
    below = pg.focused()
    assert below not in (None, "system first_open", second)
    _keys(drv, "up")
    assert pg.focused() == "system first_open"


@pytest.mark.parametrize("desk", (False, True))
def test_enter_opens_a_found_badge_and_back_closes_then_leaves(tmp_path, desk):
    ws, drv, sl, pg = _page(tmp_path, desk)
    assert pg.popover() is None
    _keys(drv, "a")
    got = pg.popover()
    assert got is not None and got[0] == "system first_open"
    assert got[1] == "First Steps" and got[3] == "Found 12 October"
    _keys(drv, "b")
    assert pg.popover() is None and sl.page == "achievements"
    assert pg.focused() == "system first_open"
    _keys(drv, "b")
    assert sl.page is None


def _walk(drv, sl, pg):
    """Every badge the arrows reach from the first, each popover as Enter
    opens it: each badge found by its keys from the first (Up past the
    first row leaves the grid, Down comes back onto its first badge), then
    every arrow tried from it."""
    def go(path):
        _keys(drv, *(("up",) * 10 + ("down",) + path))
        return pg.focused()
    first = go(())
    paths, todo = {first: ()}, [()]
    while todo:
        path = todo.pop(0)
        for k in ("right", "down", "left", "up"):
            f = go(path + (k,))
            if f is not None and f not in paths:
                paths[f] = path + (k,)
                todo.append(path + (k,))
    seen = {}
    for f, path in paths.items():
        go(path)
        _keys(drv, "a")
        seen[f] = pg.popover()
        _keys(drv, "b")
    return seen


def test_a_goal_under_way_says_its_count(tmp_path):
    ws, drv, sl, pg = _page(tmp_path, played=("a", "b", "c"))
    seen = _walk(drv, sl, pg)
    assert seen["system play_five"][2:] == ("Play 5 carts", "3 of 5")


def test_every_badge_is_reached_and_a_secret_is_named_secret_until_found(tmp_path):
    ws, drv, sl, pg = _page(tmp_path)
    seen = _walk(drv, sl, pg)
    assert len(seen) == 15 and all(p is not None and p[0] == f for f, p in seen.items())
    secret = [p for p in seen.values() if p[1] == "Secret"]
    assert secret and all(p[2:] == ("Keep exploring", "Not found yet") for p in secret)
    assert sl.page == "achievements"


def _changed_box(a, b, w, h, below):
    """The box of the pixels that differ between RGB565 frames `a` and `b`
    (w×h), over the rows above the page's hint band (`below` rows off its foot)."""
    xs, ys = [], []
    for y in range(h - below):
        o = 2 * y * w
        for x in range(w):
            if a[o + 2 * x:o + 2 * x + 2] != b[o + 2 * x:o + 2 * x + 2]:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1) if xs else None


@pytest.mark.parametrize("desk", (False, True))
def test_a_badge_opens_centred_across_the_page_over_what_it_covers_alone(tmp_path, desk):
    """Each badge of a row opens its popover at the one place, centred
    across the page, and what it does not cover stays as drawn (no wash)."""
    ws, drv, sl, pg = _page(tmp_path, desk)
    cv = ws.glass_canvas
    shut = bytes(cv._buf)
    boxes = []
    for i in range(3):
        _keys(drv, "a")
        assert pg.popover() is not None
        top, opened = shut, bytes(cv._buf)
        boxes.append(_changed_box(top, opened, cv.w, cv.h, 24))
        _keys(drv, "b", "right")
        shut = bytes(cv._buf)
    assert boxes[0] == boxes[1] == boxes[2], boxes
    x0, y0, x1, _y1 = boxes[0]
    if not desk:
        assert x0 > 8 and abs(x0 - (cv.w - x1)) <= 2, (boxes[0], cv.w)
        return
    # In a desk window the page is the window's content: the card's top edge,
    # the first row it changes, stands centred in it and spans less than it.
    cx, _cy, cw, _ch = ws.wm.windows["settings"].content_rect()
    o = 2 * y0 * cv.w
    xs = [x for x in range(cv.w) if top[o + 2 * x:o + 2 * x + 2] != opened[o + 2 * x:o + 2 * x + 2]]
    left, right = xs[0] - cx, cx + cw - (xs[-1] + 1)
    assert left > 8 and abs(left - right) <= 2, ((xs[0], xs[-1] + 1), (cx, cw))
