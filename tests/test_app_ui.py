"""What a responsive app's `ui` verbs do the same on every tier
(docs/ui_api_2026-10.md, docs/app_api_v1.md section 3), each held by one
fixture app per tier in tests/fixtures/app_ui/ running on the T-Deck's
320x240 console:

  * clip_*.moy -- ui verbs an app places in a table's parts draw inside
    the table's view: a row cut at the view's top or foot paints nothing
    over what lies above or below the table;
  * tabs_*.moy -- ui.tabs takes Q and E itself, wherever the keys do not
    type into a field, so an app reads no key for its tabs and its frames
    settle;
  * back_*.moy -- the host's back (B, the T-Deck's K, Esc, the bar's
    chevron, a desk window's) steps out of the app's own views before it
    leaves the app: a popover it drew, then the page it lent `ui.bar`'s way
    back for.
"""

import os
import shutil
import sys
from array import array

import pytest

from runtime import host_app
from ws_helpers import build_desktop_ws, make_drv, open_cart, quiesce

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "app_ui")
DT = 1.0 / 30.0
TIERS = ("python", "lua")
_EXT = {"python": "py", "lua": "lua"}
_TITLE = {"python": "Py", "lua": "Lua"}


def _frame(drv):
    quiesce(drv.ws)
    drv.ws._dirty = True
    drv.frame(DT)


def _pmem(ws, n):
    p = ws.run.player
    lua = getattr(p, "_lua", None)
    if not lua:
        return [p.ns["pmem"](i) for i in range(n)]
    run = getattr(lua, "_run", None)
    if run is not None and hasattr(run, "pmem"):
        return list(run.pmem()[1])[:n]
    img = array("i", bytearray(4 * 256))
    sys.modules[type(lua).__module__]._moycore.pmem_image(img)
    return list(img[:n])


def _px(ws):
    """The run's canvas as a pixel reader, and its content rect."""
    cv = ws.run.canvas
    buf = cv._buf
    w = cv.w

    def at(x, y):
        i = 2 * (y * w + x)
        return buf[i] | buf[i + 1] << 8
    return at, ws.run.player.ns["content_rect"]()


def _open(app, tier, base, desk=False):
    """The fixture `app` of `tier` running on a fresh T-Deck console, or in
    a window on a fresh desk."""
    root = str(base / "carts")
    host_app.moy_carts.ensure_dirs(root)
    name = "%s_%s.moy" % (app, _EXT[tier])
    shutil.copytree(os.path.join(FIX, name), os.path.join(root, name))
    ws = build_desktop_ws(base) if desk else host_app.build_workstation(root)
    ws.look.set_theme("moy", persist=False)
    ws.pointer.visible = False
    ws.bar_layer._clock_text = lambda: "10:30"
    if desk:
        ws.open_desk()
        cart = next(c for c in ws.carts.all if c["title"] == "%s %s" % (
            app.capitalize(), _TITLE[tier]))
        ws._open_workspace(cart)
        ws.play(ws.launcher_layer)
    else:
        open_cart(ws, "%s %s" % (app.capitalize(), _TITLE[tier]))
    if ws.confirm.open:             # a copied app's consent: Open
        act = ws.confirm._act
        ws.confirm.cancel()
        act()
    assert ws.run.player.cart_error is None, (tier, ws.run.player.cart_error)
    return ws


def _content(ws):
    at, (x, y, w, h) = _px(ws)
    return bytes(b for j in range(y, y + h) for i in range(x, x + w)
                 for b in at(i, j).to_bytes(2, "little"))


# -- placed verbs in a table's parts draw inside its view ------------------------


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """Each tier's (content pixels, the views, the ground's colour, a reader)."""
    out = {}
    for tier in TIERS:
        ws = _open("clip", tier, tmp_path_factory.mktemp("clip_" + tier))
        drv = make_drv(ws)
        for _ in range(6):
            _frame(drv)
        assert ws.run.player.cart_error is None, (tier, ws.run.player.cart_error)
        m = _pmem(ws, 8)
        at, (x, y, w, h) = _px(ws)
        views = [tuple(m[0:4]), tuple(m[4:8])]
        bands = {}
        for vx, vy, vw, vh in views:
            for top in (vy - 12, vy + vh):
                bands[(vx, top)] = [at(i, j) for j in range(top, top + 12)
                                    for i in range(vx, vx + vw)]
        out[tier] = (_content(ws), views, at(x, y + h - 1), bands)
        ws.exit()
    return out


@pytest.mark.parametrize("tier", TIERS)
def test_verbs_placed_in_a_part_draw_inside_the_tables_view(clip, tier):
    """A label and a button the app places in each row's part: the rows the
    view cuts paint nothing in the 12 pixels above or below it."""
    _px_, views, ground, bands = clip[tier]
    for (vx, top), band in sorted(bands.items()):
        stray = sum(1 for c in band if c != ground)
        assert stray == 0, "%s: %d pixels drawn past the view at x=%d y=%d" % (
            tier, stray, vx, top)


def test_the_parts_draw_the_same_pixels_on_every_tier(clip):
    assert clip["lua"][1] == clip["python"][1]
    assert clip["lua"][0] == clip["python"][0], "lua drew other pixels than python"


# -- Q and E switch ui.tabs ------------------------------------------------------


def _key(drv, ch):
    drv.key_down(ord(ch))
    _frame(drv)
    drv.key_up(ord(ch))
    _frame(drv)


def _press(drv, name):
    drv.press(name)
    _frame(drv)
    _frame(drv)


# (step, the keys, the tab after it from 1, the field's length after it)
_TAB_SCRIPT = (
    ("E on the focused tab row", ("e",), 2, 0),
    ("E on a button", ("down", "e"), 3, 0),
    ("E past the last tab", ("e",), 3, 0),
    ("Q", ("q",), 2, 0),
    ("Q E typed into the field", ("down", "q", "e"), 2, 2),
)


@pytest.fixture(scope="module")
def tabs(tmp_path_factory):
    """Each tier's [(the tab, the field's length, whether the frames after
    settled -- two drew the same pixels -- and the content pixels)] after
    each step of _TAB_SCRIPT."""
    out = {}
    for tier in TIERS:
        ws = _open("tabs", tier, tmp_path_factory.mktemp("tabs_" + tier))
        drv = make_drv(ws)
        for _ in range(4):
            _frame(drv)
        got = []
        for _what, keys, _tab, _n in _TAB_SCRIPT:
            for k in keys:
                if len(k) == 1:
                    _key(drv, k)
                else:
                    _press(drv, k)
            settled, last = False, None
            for _ in range(6):          # the goldens' settle: two frames alike
                _frame(drv)
                now = _content(ws)
                if now == last:
                    settled = True
                    break
                last = now
            m = _pmem(ws, 3)
            got.append((m[0], m[1], settled, _content(ws)))
        assert ws.run.player.cart_error is None, (tier, ws.run.player.cart_error)
        out[tier] = got
        ws.exit()
    return out


@pytest.mark.parametrize("tier", TIERS)
def test_q_and_e_switch_the_tabs_the_app_draws(tabs, tier):
    """Q and E step the tab row, stopping at its ends, wherever focus is but
    a field, which they type into; the frames after each step settle."""
    for (what, _keys, tab, n), (got_tab, got_n, settled, _px_) in zip(_TAB_SCRIPT, tabs[tier]):
        assert (got_tab, got_n) == (tab, n), (tier, what)
        assert settled, "%s: the frames after %s never settled" % (tier, what)


def test_the_tabs_draw_the_same_pixels_on_every_tier(tabs):
    for (what, _k, _t, _n), lua, py in zip(_TAB_SCRIPT, tabs["lua"], tabs["python"]):
        assert lua[3] == py[3], "%s: lua drew other pixels than python" % what


# -- back steps out of an app's own views first ----------------------------------


def _back(ws, drv, how):
    """One back by `how`: B (the T-Deck's K is B), Esc as the host's keyboard
    sends it, or a tap on the chevron (the handheld bar's, a desk window's
    title strip's)."""
    if how == "b":
        _press(drv, "b")
        return
    if how == "esc":
        if drv.takes_escape():
            drv.escape()
        else:
            drv.key_down(27)
            _frame(drv)
            drv.key_up(27)
    elif ws.windowed_chrome:
        win = ws.wm._wins["desktop"]
        rect = dict(ws.wm._strip_buttons(win)).get("back")
        if rect is None:                # no chevron: the window's close leaves
            ws.wm._close_window("desktop")
        else:
            drv.click(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
    else:
        ws.bar_layer.go_back("tool")
    _frame(drv)
    _frame(drv)


def _state(ws):
    """(page, popover shown, ways back the app heard, a chevron shows), or
    None once the app has been left."""
    if ws.wm.top_kind() != "desktop" or ws.run.player.ns is None:
        return None
    m = _pmem(ws, 3)
    if ws.windowed_chrome:
        shows = "back" in dict(ws.wm._strip_buttons(ws.wm._wins["desktop"]))
    else:
        shows = ws.run.player.has_page()
    return (m[0], m[1], m[2], shows)


@pytest.mark.parametrize("desk", (False, True), ids=("tdeck", "desk"))
@pytest.mark.parametrize("how", ("b", "esc", "chevron"))
@pytest.mark.parametrize("tier", TIERS)
def test_back_steps_out_of_the_apps_views_before_it_leaves(tmp_path, tier, how, desk):
    """Details opens the second page, More its popover. Back dismisses the
    popover, then steps the page back (ui.bar answers its way back), then,
    with no view of the app's open, the chevron leaves the app and the keys
    stay the app's own."""
    ws = _open("back", tier, tmp_path, desk)
    drv = make_drv(ws)
    for _ in range(3):
        _frame(drv)
    _press(drv, "a")                    # Details
    _press(drv, "a")                    # More
    got = [_state(ws)]
    for _ in range(3):
        _back(ws, drv, how)
        got.append(_state(ws))
    first = (1, 0, 1, False)
    assert got[:3] == [(2, 1, 0, True), (2, 0, 0, True), first], (tier, how, got)
    assert got[3] == (None if how == "chevron" else first), (tier, how, got)
    ws.exit()
