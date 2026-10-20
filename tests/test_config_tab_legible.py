"""The Config tab's own chips and choice glyphs read in the theme's face, in
both modes of the default theme, on the handheld's 320x240: the INFO chip
holds its label (`ui_draw.mini_btn` places it at the chip's corner pads, so
the chip holds the `ui` face's line between them), and a choice-icons card's
glyph reads on its cell, picked or not.
"""

import pytest

from ws_helpers import build_ws, device_frames, quiesce


def _word(cv, x, y):
    o = 2 * (y * cv.w + x)
    return cv._buf[o] << 8 | cv._buf[o + 1]


def _lum(v):
    out = 0.0
    for c, k in zip((((v >> 11) & 31) / 31, ((v >> 5) & 63) / 63, (v & 31) / 31),
                    (0.2126, 0.7152, 0.0722)):
        out += k * (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return out


def _contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)))
    return (lb + 0.05) / (la + 0.05)


def _config(tmp_path, variant):
    ws = build_ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant=variant)
    ws.open_in_editor(next(c for c in ws.carts.all if c.get("title") == "Sky Run"))
    ws.set_menu_view("cards")
    for _ in range(3):
        quiesce(ws)                 # no toast or pointer over the panel
        ws._dirty = True
        device_frames(ws, 1)
    return ws, ws.cards_layer, ws.glass_canvas


@pytest.mark.parametrize("variant", ["light", "dark"])
def test_the_info_chip_holds_its_label(tmp_path, variant):
    ws, cards, cv = _config(tmp_path, variant)
    x, y, w, h = cards.layout.info_btn
    assert cards.layout.lead > 0, "the theme's ui face is a pixel face: nothing to hold"
    fill = _word(cv, x + w - 1, y + h - 1)
    inside = [_word(cv, xx, yy) for yy in range(y, y + h) for xx in range(x, x + w)]
    assert any(_contrast(p, fill) >= 3 for p in inside), "INFO draws no label"
    # The rows under the chip are the panel's, as its right edge's are.
    body = _word(cv, x + w + 1, y + h + 1)
    under = [_word(cv, xx, yy) for yy in range(y + h, y + h + 3) for xx in range(x, x + w)]
    assert set(under) == {body}, "INFO's label runs out of its chip"


@pytest.mark.parametrize("variant", ["light", "dark"])
def test_a_choice_glyph_reads_on_its_cell(tmp_path, variant):
    ws, cards, cv = _config(tmp_path, variant)
    rows = [r for r in cards._card_layout() if r.get("display") == "choice-icons"]
    assert rows, "Sky Run's AUTORUN card is a choice-icons card"
    cells = cards._choice_cells(rows[0])
    assert len(cells) == 2
    for k, (x, y, w, h) in cells:
        fill = _word(cv, x + w // 2, y + 2)
        best = max(_contrast(_word(cv, xx, yy), fill)
                   for yy in range(y + 3, y + h - 3) for xx in range(x + 3, x + w - 3))
        assert best >= 3, "choice %d's glyph reads at %.2f:1 on its cell" % (k, best)
