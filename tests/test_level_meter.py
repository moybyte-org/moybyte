"""A stepped level meter (`ui.slider` with `{"steps": True}`: Volume and
Brightness in the control center and in Settings) draws one segment a level,
the first `value` of them in the value's colour and the rest in the track's,
in every theme-mode. Read off the pixels of the T-Deck's 320x240 glass, so a
change to the segments' colours, the row's ground or a mark's layer that
leaves the meter empty (or one flat colour) fails here."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import focus_shots as F  # noqa: E402

L = F.L
TMS = [(t, m) for t, m in F.MODES]
LEVEL = 4
STEPS = 7
SEG_W = 6           # a segment's width at the T-Deck's scale; its pitch is 8


def _segments(img, box):
    """The row's segments, left to right, each as the pixel at its centre,
    with the row's ground (the row's middle, clear of its label): the
    segments' top edge is a run of SEG_W pixels off the ground."""
    x, y, w, h = box
    sy = y + (h - 8) // 2
    mid = x + w // 2
    ground = img.getpixel((mid, sy))
    runs, start = [], None
    for px in range(mid, x + w - 4):
        off = img.getpixel((px, sy)) != ground
        if off and start is None:
            start = px
        elif not off and start is not None:
            runs.append((start, px))
            start = None
    runs = [r for r in runs if r[1] - r[0] == SEG_W]
    return ground, [img.getpixel(((a + b) // 2, sy + 4)) for a, b in runs]


def _holds(img, box, where):
    ground, segs = _segments(img, box)
    assert len(segs) == STEPS, (where, "segments", len(segs))
    value = segs[0]
    assert value != ground, (where, "the value's colour is the ground's")
    assert segs[:LEVEL] == [value] * LEVEL, (where, "the filled segments", segs)
    assert all(s != value for s in segs[LEVEL:]), (where, "the rest is not the value's", segs)


@pytest.mark.parametrize("theme,mode", TMS, ids=["%s-%s" % tm for tm in TMS])
def test_the_control_centers_brightness_meter_fills_four_of_seven(theme, mode):
    held = []

    def setup(g, ws):
        ws.settings_layer.set_level("brightness", LEVEL)
        F._cc()(g, ws)
        held.append(ws)
    img, _ok, _r = L.render(L._goldens(),
                            L.HANDHELD, theme, mode, setup, False, "touch")
    box = held[-1].sysmenu.comp.host.box("Brightness")
    assert box is not None
    _holds(img.convert("RGB"), box, "%s-%s control center" % (theme, mode))


@pytest.mark.parametrize("theme,mode", TMS, ids=["%s-%s" % tm for tm in TMS])
def test_settings_brightness_meter_fills_four_of_seven(theme, mode):
    held = []

    def setup(g, ws):
        ws.open_settings()
        sl = ws.settings_layer
        sl.set_level("brightness", LEVEL)
        sl.show_row("brightness")
        held.append(sl)
    img, _ok, _r = L.render(L._goldens(), L.HANDHELD, theme, mode, setup, False, "touch")
    sl = held[-1]
    box = sl._host.box("Brightness")
    assert box is not None
    _holds(img.convert("RGB"), box, "%s-%s settings" % (theme, mode))
