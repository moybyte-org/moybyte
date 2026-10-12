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
from runtime import ui
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
        d = {"format": 1, "name": "framed", "frame": fr}
        rc, notes = moy_ui.theme_install(json.dumps(d).encode())
        assert rc == OK
        ui.use_shape("framed")
        return notes
    yield use
    ui.use_shape(None)


def _names(btns):
    return [(n, right) for n, _r, right in btns]


def test_the_frame_key_reads_and_notes(frame):
    assert frame(style="win9x", title_align="center", buttons="C|T|MZ") == 0
    assert frame(style="beos") == N_DROPPED
    assert frame(title_align=3) == N_DROPPED
    for bad in ("CTMZZ", "CTM", "CTMX", "", 5):
        assert frame(buttons=bad) == N_DROPPED, bad
    assert frame(glow=1) == N_UNKNOWN
    rc, notes = moy_ui.theme_install(b'{"format": 1, "name": "f2", "frame": 3}')
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
    btns = ui.frame_buttons((10, 10, 300, 200), 18, "HELLO", True, 1)
    assert _names(btns) == want
    no_min = ui.frame_buttons((10, 10, 300, 200), 18, "HELLO", False, 1)
    assert _names(no_min) == [b for b in want if b[0] != "min"]


def test_a_theme_orders_the_buttons(frame):
    frame(style="classic", buttons="ZT|CM")
    btns = ui.frame_buttons((0, 0, 200, 100), 18, "T", True, 1)
    assert _names(btns) == [("max", False), ("close", True), ("min", True)]
    left = btns[0][1]
    assert left[0] == 2 and btns[-1][1][0] + btns[-1][1][2] == 200 - 2


def test_classic_is_the_frame_without_a_theme():
    ui.use_shape(None)
    btns = ui.frame_buttons((10, 10, 300, 200), 36, "HELLO", True, 2)
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
    ui.frame(cv, th, (x, y, w, h), t, "A LONG WINDOW TITLE", active, True, fs)
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
    for _n, (bx, by, bw, bh), _s in ui.frame_buttons((x, y, w, h), t, "A LONG WINDOW TITLE",
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
    ui.frame_band(cv, th, (0, 0, 40, 12))
    px = indices_of(cv)
    assert px[5 * 40 + 5] == th["bar"]
    want = th["shade_dark"] if style == "win9x" else th["bar_edge"]
    assert px[11 * 40 + 5] == want
    assert px[12 * 40 + 5] == BG
    # clipped above the bottom line, the line is not drawn (the clock's repaint)
    cv.cls(BG)
    cv.clip(0, 0, 40, 11)
    cv.gate_counts_reset()
    ui.frame_band(cv, th, (0, 0, 40, 12), True)
    cv.clip()
    px = indices_of(cv)
    assert px[5 * 40 + 5] == th["surface"] and px[11 * 40 + 5] == BG
    assert cv.gate_counts()[0] == (2 if style == "win9x" else 1)
