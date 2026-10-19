"""The owner's 2026-10-19 calls on the themes and the chrome (design review
round 3, docs/look_2026-10.md's Decisions), held in the parts that draw
them; the modals' scrim is tests/test_modal_scrim.py's.

  * D-05: an empty field shows its hint in the disabled ink, a typed one
    does not;
  * m15: a toggle's knob is one colour in a mode, the state on the track;
  * m04: a disabled key's plate fades toward the surface;
  * m07: Blocks underlines a current row's label, no bar beside it;
  * D-14: Contrast's current row is a solid ink plate under inverse text.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pytest  # noqa: E402

from runtime import ui, ui_draw  # noqa: E402
from test_ui_parts import _FIVE, _Run, _rgb  # noqa: E402


def _c565(r, idx):
    """Colour index `idx` as the canvas draws it (one pixel, put back)."""
    buf = r.cv._buf
    keep = bytes(buf[0:2])
    r.cv.rect(0, 0, 1, 1, idx)
    v = buf[0] << 8 | buf[1]
    buf[0:2] = keep
    return v


def _colours(r, box):
    x0, y0, w, h = box
    return {r.px(x, y) for y in range(y0, y0 + h) for x in range(x0, x0 + w)}


def _field(theme, text, hint):
    st = {"t": text}

    def draw():
        st["rect"] = ui.page("")
        st["t"], _done = ui.field("Name", st["t"], 16, {"hint": hint} if hint else None)
        return st
    r = _Run(draw, theme)
    r.frame()
    r.frame()
    return r


@pytest.mark.parametrize("theme", _FIVE, ids=lambda t: "-".join(t))
def test_an_empty_field_shows_its_hint_in_the_disabled_ink(theme):
    bare = _field(theme, "", None)
    hinted = _field(theme, "", "Your name")
    assert bytes(hinted.cv._buf) != bytes(bare.cv._buf), "the hint draws"
    new = _colours(hinted, (0, 0, 320, 40)) - _colours(bare, (0, 0, 320, 40))
    dis = _c565(hinted, ui_draw.disabled_ink(hinted.th))
    assert dis in new, "in the theme's disabled ink"
    typed = _field(theme, "Ann", "Your name")
    plain = _field(theme, "Ann", None)
    assert bytes(typed.cv._buf) == bytes(plain.cv._buf), "a typed field shows no hint"


def _toggle(theme, on):
    st = {"on": on}

    def draw():
        st["rect"] = ui.page("")
        st["on"] = ui.toggle("Sound", st["on"])
        return st
    r = _Run(draw, theme)
    r.frame()
    r.frame()
    return r


@pytest.mark.parametrize("mode", ("light", "dark"))
def test_a_toggles_knob_is_one_colour_in_a_mode(mode):
    on, off = _toggle(("moy", mode), True), _toggle(("moy", mode), False)
    switch = (320 - 60, 0, 60, 30)          # the toggle at the row's right
    knob = _c565(on, on.th["ink"]) if mode == "dark" else 0xFFFF
    assert knob in _colours(on, switch) and knob in _colours(off, switch)
    navy = _c565(on, on.th["value_ink"])
    assert navy == knob or navy not in _colours(on, switch), "On takes no knob of its own"


def _key(disabled):
    def draw():
        ui.page("")
        ui.at(20, 40, 120, 60)
        return ui.button(".", "normal", {"disabled": True} if disabled else None)
    r = _Run(draw, ("moy", "light"))
    r.frame()
    r.frame()
    return r


def test_a_disabled_keys_plate_fades_toward_the_surface():
    on, off = _key(False), _key(True)
    plate, faded = _rgb(on.px(30, 70)), _rgb(off.px(30, 70))
    surface = _rgb(_c565(on, on.th["surface"]))
    assert faded != plate
    assert sum(abs(a - b) for a, b in zip(faded, surface)) \
        < sum(abs(a - b) for a, b in zip(plate, surface)), "the plate fades to the surface"


def _list(theme):
    st = {}

    def draw():
        st["rect"] = ui.page("")
        st["cur"] = ui.list("", ("Projects", "Notes", "Art"), 1, {"mark": False})
        return st
    r = _Run(draw, theme)
    r.frame()
    r.frame()
    return r


def test_blocks_underlines_the_current_rows_label():
    r = _list(("blocks", "light"))
    value = _c565(r, r.th["value"])
    hits = [(x, y) for y in range(0, 240) for x in range(0, 320) if r.px(x, y) == value]
    assert hits, "the current row is marked in the value colour"
    xs = sorted({x for x, _y in hits})
    ys = sorted({y for _x, y in hits})
    assert len(ys) <= 3 and xs[-1] - xs[0] > 20, "an underline, not a bar beside the row"
    assert xs[-1] < 200, "under the label alone"


@pytest.mark.parametrize("mode", ("light", "dark"))
def test_contrasts_current_row_is_a_solid_ink_plate_under_inverse_text(mode):
    r = _list(("contrast", mode))
    ink, inverse = _c565(r, r.th["ink"]), _c565(r, r.th["value_ink"])
    x0, y0, w, h = r.out["rect"]
    rows = {}
    for y in range(y0, y0 + h):
        line = [r.px(x, y) for x in range(x0 + 8, x0 + w - 8)]
        rows[y] = sum(1 for p in line if p == ink) / len(line)
    plate = [y for y, share in rows.items() if share > 0.6]
    assert plate, "a row filled with the ink"
    band = {r.px(x, yy) for yy in range(plate[0], plate[-1] + 1) for x in range(x0, x0 + 120)}
    assert inverse in band, "its label in the inverse ink"
