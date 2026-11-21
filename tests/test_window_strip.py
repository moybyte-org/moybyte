"""A window's title strip on the engine (runtime/wm/wm_strip_ui.py, the page;
runtime/wm/wm_strip.py, its glue): the frame is drawn bare and the strip
page draws the back chevron, the icon and title and the three buttons as bar
items; the window manager resolves a tap from the page's rects and runs the
button's page action; a strip is drawn by the engine into a kept layer and
stamped from it, inside the canvas's clip, so a drag frame does no page
work. Both engines lay the strip out alike."""

import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_wm_windowed import _drv, _moy_desk  # noqa: E402

import wm_strip as SE  # noqa: E402


def _strip(ws, win):
    return (win.x + 1, win.y + 1, win.w - 2, win.title_h)


def _rows(cv, r):
    x, y, w, h = r
    return [bytes(cv._buf[((y + j) * cv.w + x) * 2:((y + j) * cv.w + x + w) * 2]) for j in range(h)]


def test_the_strip_s_buttons_are_the_page_s_alike_on_both_engines(tmp_path):
    import uitree_app
    ws = _moy_desk(tmp_path)
    ws.spine.open_app(ws.app("calc"))
    ws.frame(1 / 30)
    wm = ws.wm
    win = wm.windows["calc"]
    got = []
    try:
        for engine in ("c", "reference"):
            uitree_app.use_engine(engine)
            SE._PAGE[0] = None
            got.append(wm._frame_buttons(win))
    finally:
        uitree_app.use_engine("c")
        SE._PAGE[0] = None
    assert got[0] == got[1]
    assert [n for n, _r, _s in got[0]] == ["min", "max", "close"]
    sx, sy, sw, sh = _strip(ws, win)
    for _n, (bx, by, bw, bh), right in got[0]:
        assert right and sx <= bx and bx + bw <= sx + sw and by == sy and bh == sh
    a = wm._strip_args(win)
    head = SE.head(*a)
    assert head[0] + head[2] <= got[0][0][1][0]          # the title ends before minimise


def test_a_long_title_is_cut_and_the_buttons_keep_their_place(tmp_path):
    ws = _moy_desk(tmp_path)
    ws.spine.open_app(ws.app("calc"))
    ws.frame(1 / 30)
    wm = ws.wm
    win = wm.windows["calc"]
    a = list(wm._strip_args(win))
    short = SE.buttons(a[0], a[1], a[2], a[3], a[4], a[5], a[6], [])
    a[2] = "A window title far too long for any strip this window could ever hold " * 3
    long_ = SE.buttons(a[0], a[1], a[2], a[3], a[4], a[5], a[6], [])
    assert long_ == short
    head = SE.head(*a)
    assert head[0] + head[2] <= long_[0][1][0]


def test_a_tap_on_close_runs_the_page_s_action(tmp_path):
    import wm_strip_ui
    ws = _moy_desk(tmp_path)
    ws.spine.open_app(ws.app("calc"))
    ws.frame(1 / 30)
    wm = ws.wm
    win = wm.windows["calc"]
    ran = []
    real = SE.fire
    SE.fire = lambda name: (ran.append(name), real(name))[1]
    try:
        _n, (bx, by, bw, bh), _s = [b for b in wm._frame_buttons(win) if b[0] == "close"][0]
        drv = _drv(ws)
        drv.touch(bx + bw // 2, by + bh // 2)
        drv.frame(1 / 30)
    finally:
        SE.fire = real
    assert ran == ["close"] and wm_strip_ui.TAP[0] is not None
    assert "calc" not in wm.windows


def test_a_stamped_strip_is_the_page_drawn_straight(tmp_path):
    """The kept layer holds what the page draws onto the glass itself, and
    a window moved a step stamps the same rows at its new place."""
    ws = _moy_desk(tmp_path)
    ws.spine.open_app(ws.app("calc"))
    ws.frame(1 / 30)
    wm = ws.wm
    win = wm.windows["calc"]
    cv = wm.root_canvas
    inset = wm._win_radius(win)
    x, y, w, h = _strip(ws, win)
    inner = (x + inset, y, w - 2 * inset, h)
    wm._win_chrome(win, True)
    stamped = _rows(cv, inner)
    SE._STAMPS[0], SE._STAMPS[1] = cv, False                 # the canvas with no clipped stamp
    try:
        win._chrome_keys[0] = None
        wm._win_chrome(win, True)
    finally:
        SE._STAMPS[0] = None
    assert _rows(cv, inner) == stamped
    win.x += 1
    wm._win_chrome(win, True)
    assert _rows(cv, (inner[0] + 1, y, inner[2], h)) == stamped


def test_a_stamp_keeps_inside_the_clip_and_its_layers_are_bounded(tmp_path):
    ws = _moy_desk(tmp_path)
    ws.spine.open_app(ws.app("calc"))
    ws.frame(1 / 30)
    wm = ws.wm
    win = wm.windows["calc"]
    cv = wm.root_canvas
    x, y, w, h = _strip(ws, win)
    cv.rect(x, y, w, h, 3)
    before = _rows(cv, (x, y, w, h))
    cx = x + w // 2
    cv.clip(cx, y, 20, h)
    try:
        a = wm._strip_args(win)
        SE.stamp(cv, ws.theme_colors, win.x, win.y, win.w, win.h, win.title_h, "Clip", True,
                 wm._win_btns(win) | 32, wm._fs(), a[1], a[3], a[4], False, -1, 0)
    finally:
        cv.clip()
    after = _rows(cv, (x, y, w, h))
    for j in range(h):
        assert after[j][:(cx - x) * 2] == before[j][:(cx - x) * 2]
        assert after[j][(cx - x + 20) * 2:] == before[j][(cx - x + 20) * 2:]
    for i in range(SE.CAP + 3):
        SE.stamp(cv, ws.theme_colors, win.x, win.y, win.w, win.h, win.title_h, "T%d" % i, True,
                 wm._win_btns(win) | 32, wm._fs(), a[1], a[3], a[4], False, -1, 0)
    assert sum(1 for lay in SE._LAYS if lay is not None) == SE.CAP


def _desk_configs():
    import test_shell_goldens as G
    return [n for n, c in G.CONFIGS.items()
            if c["windowed"] and not c.get("console") and not c.get("plan")]


@pytest.mark.parametrize("config", _desk_configs())
def test_nothing_in_the_strip_is_cut_by_the_frame(tmp_path, config):
    """Every desk config's calc strip: the page's first and last items lie
    inside the strip, clear of the columns the stamp leaves to the frame
    (the depth's corners, `inset` at each end), and the page inks none of
    those columns -- what it would ink there the stamp would cut."""
    import test_shell_goldens as G
    import ui_draw as D
    ws = G._build(G.CONFIGS[config], tmp_path)
    ws.home.open_desk()
    ws.spine.open_app(ws.app("calc"))
    for _ in range(3):
        ws.frame(1 / 30)
    wm = ws.wm
    win = wm.windows["calc"]
    rect, unit, title, icon, tone, back, inset = wm._strip_args(win)
    sw, sh = rect[2], rect[3]
    head = SE.head(*wm._strip_args(win))
    close = [b for b in wm._frame_buttons(win) if b[0] == "close"][0][1]
    assert head[0] >= rect[0] + inset and close[0] + close[2] <= rect[0] + sw - inset, \
        (config, head, close, rect, inset)
    cv = wm.root_canvas
    th = ws.theme_colors
    btns = wm._win_btns(win)
    lays = []
    for page in (False, True):
        lay = cv.new_layer(sw, sh)
        D.frame(lay, th, (-1, -1, win.w, win.h), win.title_h, title, True, btns, wm._fs())
        if page:
            SE.draw(lay, th, (0, 0, sw, sh), unit, title, icon, tone, back, inset)
        lays.append(_rows(lay, (0, 0, sw, sh)))
    ground, inked = lays
    assert ground != inked
    edge = 2 * max(inset, 1)
    for j in range(sh):
        assert inked[j][:edge] == ground[j][:edge], (config, "left", j)
        assert inked[j][-edge:] == ground[j][-edge:], (config, "right", j)
