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
