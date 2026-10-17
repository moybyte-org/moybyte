"""Window frame styles (native/moy_ui/moy_ui_draw.c's moy_ui_frame,
docs/theming_2026-09.md §4.5): the `frame` key, each style inside the WM's
footprint, the button layout the hit-test reads, and the bar's band.

Classic is the frame the WM drew in Python; the goldens hold it byte for
byte on every config the default theme renders, and the Platinum and Win95
seeds pin the other styles on every golden surface.
"""

import json

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
import moy_ui
from runtime import ui_draw
from runtime.chrome import theme_colors
from runtime.host_canvas import indices_of
from runtime.host_canvas import make_system_canvas as SystemCanvas

OK = 0
N_UNKNOWN, N_DROPPED = 2, 4
STYLE = json.loads(open(runtime.__path__[0] + "/../native/moy_ui/style.json").read())
TH = STYLE["theme"]
BG = 9


@pytest.fixture
def frame():
    """Install a package naming `frame` and make its shape the toolkit's."""
    def use(**fr):
        d = {"format": 2, "name": "framed", "frame": fr}
        rc, notes = moy_ui.theme_install(json.dumps(d).encode())
        assert rc == OK
        ui_draw.use_shape("framed")
        return notes
    yield use
    ui_draw.use_shape(None)


def _names(btns):
    return [(n, right) for n, _r, right in btns]


def test_the_frame_key_reads_and_notes(frame):
    assert frame(style="win9x", title_align="center", buttons="C|T|MZ") == 0
    assert frame(style="beos") == N_DROPPED
    assert frame(title_align=3) == N_DROPPED
    for bad in ("CTMZZ", "CTM", "CTMX", "", 5):
        assert frame(buttons=bad) == N_DROPPED, bad
    assert frame(glow=1) == N_UNKNOWN
    rc, notes = moy_ui.theme_install(b'{"format": 2, "name": "f2", "frame": 3}')
    assert (rc, notes) == (OK, N_DROPPED)
    assert "frame" not in TH["reserved"]


def test_the_styles_and_orders_are_style_jsons():
    assert TH["frames"] == ["classic", "tab", "platinum", "win9x", "flat"]
    assert sorted(TH["frame_buttons"]) == sorted("CMZT")


@pytest.mark.parametrize("style,want", [
    ("classic", [("min", True), ("max", True), ("close", True)]),
    ("flat", [("min", True), ("max", True), ("close", True)]),
    ("win9x", [("min", True), ("max", True), ("close", True)]),
    ("platinum", [("close", False), ("max", True), ("min", True)]),
    ("tab", [("close", False), ("min", True), ("max", True)]),
])
def test_each_styles_own_button_order(frame, style, want):
    frame(style=style)
    btns = ui_draw.frame_buttons((10, 10, 300, 200), 18, "HELLO", True, 1)
    assert _names(btns) == want
    no_min = ui_draw.frame_buttons((10, 10, 300, 200), 18, "HELLO", False, 1)
    assert _names(no_min) == [b for b in want if b[0] != "min"]


def test_a_theme_orders_the_buttons(frame):
    frame(style="classic", buttons="ZT|CM")
    btns = ui_draw.frame_buttons((0, 0, 200, 100), 18, "T", True, 1)
    assert _names(btns) == [("max", False), ("close", True), ("min", True)]
    left = btns[0][1]
    assert left[0] == 2 and btns[-1][1][0] + btns[-1][1][2] == 200 - 2


def test_classic_is_the_frame_without_a_theme():
    ui_draw.use_shape(None)
    btns = ui_draw.frame_buttons((10, 10, 300, 200), 36, "HELLO", True, 2)
    ic = 32
    assert [r for _n, r, _s in btns] == [
        (10 + 300 - 2 - ic - 2 * (ic + 4), 13, ic, ic),
        (10 + 300 - 2 - ic - (ic + 4), 13, ic, ic),
        (10 + 300 - 2 - ic, 13, ic, ic)]


@pytest.mark.parametrize("style", TH["frames"])
@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("fs", [1, 2])
def test_every_style_stays_in_the_footprint(frame, style, active, fs):
    """The ring, the strip and the 3px shadow right and below: nothing else,
    which is what the WM's damage and its frozen chrome are built on."""
    frame(style=style)
    th = theme_colors("night")
    cv = SystemCanvas(200, 120)
    cv.cls(BG)
    x, y, w, h, t = 20, 10, 140, 90, 18 * fs
    ui_draw.frame(cv, th, (x, y, w, h), t, "A LONG WINDOW TITLE", active, True, fs)
    px = indices_of(cv)
    for j in range(cv.h):
        for i in range(cv.w):
            inside = x <= i < x + w and y <= j < y + h
            shadow = (x + w <= i < x + w + 3 and y + 3 <= j < y + h) or \
                     (x + 3 <= i < x + w + 3 and y + h <= j < y + h + 3)
            content = x + 1 <= i < x + w - 1 and y + 1 + t <= j < y + h - 1
            if content or not (inside or shadow):
                assert px[j * cv.w + i] == BG, (style, i, j)
    # the buttons are inside the strip
    for _n, (bx, by, bw, bh), _s in ui_draw.frame_buttons((x, y, w, h), t, "A LONG WINDOW TITLE",
                                                      True, fs):
        assert x < bx and bx + bw < x + w and y < by and by + bh <= y + 1 + t


def test_a_left_group_does_not_swallow_the_title(tmp_path, frame):
    """Platinum's close box sits left of the title: a tap on the title still
    drags, and a tap by the box still closes."""
    from test_wm_windowed import _drv, _ws
    ws = _ws(tmp_path)
    frame(style="platinum")
    ws.open_settings()
    _drv(ws).frame(1 / 30)
    win = ws.wm._wins["settings"]
    btns = dict((n, r) for n, r, _s in ws.wm._frame_buttons(win))
    cx, cy, cw, ch = btns["close"]
    assert ws.wm._strip_button_hit(win, cx + cw // 2, cy + ch // 2) == "close"
    assert ws.wm._strip_button_hit(win, win.x + 1, cy) == "close"
    assert ws.wm._strip_button_hit(win, win.x + win.w // 2, cy) is None
    mx = btns["max"]
    assert ws.wm._strip_button_hit(win, mx[0] + 1, mx[1] + 1) == "max"


@pytest.mark.parametrize("style", TH["frames"])
def test_the_band(frame, style):
    frame(style=style)
    th = theme_colors("night")
    cv = SystemCanvas(40, 20)
    cv.cls(BG)
    ui_draw.frame_band(cv, th, (0, 0, 40, 12))
    px = indices_of(cv)
    assert px[5 * 40 + 5] == th["bar"]
    want = th["shade_dark"] if style == "win9x" else th["bar_edge"]
    assert px[11 * 40 + 5] == want
    assert px[12 * 40 + 5] == BG
    # clipped above the bottom line, the line is not drawn (the clock's repaint)
    cv.cls(BG)
    cv.clip(0, 0, 40, 11)
    cv.gate_counts_reset()
    ui_draw.frame_band(cv, th, (0, 0, 40, 12), True)
    cv.clip()
    px = indices_of(cv)
    assert px[5 * 40 + 5] == th["surface"] and px[11 * 40 + 5] == BG
    assert cv.gate_counts()[0] == (2 if style == "win9x" else 1)


# -- the depth's window parts (docs/look_2026-10.md step 13) ------------------

@pytest.fixture
def looked():
    """Install theme `name`'s depth in a mode and its live colours; answers
    (tokens, a canvas over its block)."""
    from test_ui_style_data import _live
    def use(name, light, size=(420, 200)):
        ui_draw.use_depth(name, light)
        live = _live(name, "light" if light else "dark")
        cv = SystemCanvas(*size)
        cv.theme_block(live.block)
        cv.cls(live.tokens["desktop"])
        return live.tokens, cv
    yield use
    ui_draw.use_depth(None, False)


def _word(v):
    return ui_draw._C.theme_rgb(v)[0]


def _px(cv, x, y):
    i = (y * cv.w + x) * 2
    return cv._buf[i] << 8 | cv._buf[i + 1]


@pytest.mark.parametrize("light", [True, False])
def test_the_title_buttons_are_minimize_maximize_close(looked, light):
    """Every built-in theme's controls are minimize, maximize and close as
    title buttons, each a 28-pixel target inside the 44-row header, and a
    running game's window has no minimize."""
    looked("moy", light)
    rect = (100, 50, 400, 300)
    btns = ui_draw.frame_buttons(rect, 44, "CALC", True, 1)
    assert _names(btns) == [("min", True), ("max", True), ("close", True)]
    for _n, (x, y, w, h), _right in btns:
        assert w >= 28 and h >= 28
        assert 100 < x and x + w < 500 and 50 < y and y + h <= 50 + 45
    pitch = [b[1][0] for b in btns]
    assert pitch[1] - pitch[0] == pitch[2] - pitch[1] == 34
    game = ui_draw.frame_buttons(rect, 44, "GAME", False, 1)
    assert _names(game) == [("max", True), ("close", True)]
    assert ui_draw.frame_icon(rect, 44) == (116, 62, 20, 20)


@pytest.mark.parametrize("light", [True, False])
def test_moys_header_is_the_windows_colour_under_a_value_bar(looked, light):
    """`win.header: bar`: the focused window's header is the window's colour
    under a 4-pixel value bar across its top; a window behind has no bar."""
    th, cv = looked("moy", light)
    x, y, w, h = 20, 20, 300, 160
    for active in (True, False):
        cv.cls(th["desktop"])
        ui_draw.frame(cv, th, (x, y, w, h), 44, "CALC", active, True, 1)
        mid = x + w // 2
        top = _word(th["value"]) if active else _word(th["surface"])
        # Night rings its windows in `line`, over the bar's first row
        first = 0 if light else 1
        assert [_px(cv, mid, y + j) for j in range(first, 4)] == [top] * (4 - first)
        assert _px(cv, mid, y + 4) == _px(cv, mid, y + 30) == _word(th["surface"])
        assert _px(cv, mid, y + 44) == _word(th["edge"])            # the rule
    assert ui_draw.frame_geom(w, h, 44)[0] > 0                      # rounded


def test_a_header_fill_and_close_alone(looked):
    """A look's `win.header: fill` fills a focused header with `value` (a
    window behind takes `win.fill_back`, else its colour), `win.controls:
    close` leaves close alone, and `win.icon: false` leaves no icon slot."""
    from test_ui_style_data import _install, _uninstall
    _install({"format": 2, "name": "wp", "inherits": "moy", "look": {
        "win": {"header": "fill", "controls": "close", "icon": False, "rule": 0}}})
    try:
        th, cv = looked("wp", True)
        rect = (20, 20, 300, 160)
        assert _names(ui_draw.frame_buttons(rect, 44, "T", True, 1)) == [("close", True)]
        assert ui_draw.frame_icon(rect, 44) is None
        ui_draw.frame(cv, th, rect, 44, "T", True, True, 1)
        assert _px(cv, 170, 40) == _px(cv, 170, 64) == _word(th["value"])
        cv.cls(th["desktop"])
        ui_draw.frame(cv, th, rect, 44, "T", False, True, 1)
        assert _px(cv, 170, 40) == _px(cv, 170, 64) == _word(th["surface"])
    finally:
        _uninstall()


def test_a_theme_without_a_look_keeps_its_frame():
    """Legacy seeds draw their frame style: no icon slot, their own boxes."""
    ui_draw.use_depth("night", False)
    try:
        assert ui_draw.frame_icon((0, 0, 300, 200), 18) is None
        assert _names(ui_draw.frame_buttons((0, 0, 300, 200), 18, "T", True, 1)) == [
            ("min", True), ("max", True), ("close", True)]
        assert ui_draw.frame_buttons((0, 0, 300, 200), 18, "T", True, 1)[0][1][2] == 16
    finally:
        ui_draw.use_depth(None, False)


# -- the desk's tiles (docs/look_2026-10.md step 25) --------------------------

def _rgb(hexs):
    return ui_draw._C.theme_rgb(0x1000000 | int(hexs, 16))[0]


@pytest.mark.parametrize("light", [True, False])
def test_a_desk_tile_is_the_looks_fill_over_a_soft_shadow(looked, light):
    """`tile_fill` at `tile_r` (12, the default: Moy sets none) over the
    theme's soft shadow at MOY_UI_TILE_SHADOW percent: the tile's pixels are
    its fill, its corner is rounded, the pixels under it darken."""
    th, cv = looked("moy", light)
    ground = _px(cv, 5, 5)
    assert ui_draw.bar_part(cv, th, ui_draw.BAR_TILE, (40, 40, 44, 44))
    fill = _rgb("ffffff" if light else "23274b")
    assert _px(cv, 62, 62) == _px(cv, 41, 62) == _px(cv, 62, 41) == fill
    assert _px(cv, 40, 40) != fill                                 # rounded
    assert _px(cv, 62, 84 + 3) != ground                           # the shadow
    assert _px(cv, 300, 150) == ground


def test_an_outlined_tile_casts_no_shadow(looked):
    """`tile_outline` rings the tile in its colour, and an outlined tile
    casts no shadow (THEMES.md: the tiles Blocks and Pico outline)."""
    from test_ui_style_data import _install, _uninstall
    _install({"format": 2, "name": "tl", "inherits": "moy", "look": {
        "tile_r": 6, "tile_fill": "#ff0000", "tile_outline": [2, "#00ff00"]}})
    try:
        th, cv = looked("tl", True)
        ground = _px(cv, 5, 5)
        assert ui_draw.bar_part(cv, th, ui_draw.BAR_TILE, (40, 40, 44, 44))
        assert _px(cv, 62, 40) == _px(cv, 62, 41) == _rgb("00ff00")
        assert _px(cv, 62, 62) == _rgb("ff0000")
        assert _px(cv, 62, 84 + 3) == ground                       # no shadow
    finally:
        _uninstall()


def test_a_theme_without_a_look_draws_no_tile():
    ui_draw.use_depth("night", False)
    try:
        cv = SystemCanvas(80, 80)
        cv.cls(BG)
        assert not ui_draw.bar_part(cv, theme_colors("night"), ui_draw.BAR_TILE,
                                    (8, 8, 44, 44))
        assert set(indices_of(cv)) == {BG}
    finally:
        ui_draw.use_depth(None, False)
