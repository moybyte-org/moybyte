"""Layout reads line metrics (docs/theming_2026-09.md §4.7, phase 4 step 2).

The toolkit's text and the shell's layouts read the metrics of the font each
role draws in, not an 8px cell. With petme128 those metrics ARE the cell, so
the goldens hold this byte for byte; this file installs a font that is not
petme128 -- the step-1 fixture, a proportional 6x9 box on an 11-row line --
and holds the property instead: whatever the font, a label lands inside the
box laid out for it, drawn as wide as it was measured, and the boxes laid out
around text move with the font rather than overlapping it.
"""

import json

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
from runtime import ui_draw
from runtime.chrome import CodeLayout, Layout, theme_colors
from runtime.host_canvas import indices_of
from runtime.host_canvas import make_system_canvas as SystemCanvas
from ws_helpers import boot_on_fixture

@pytest.fixture(autouse=True)
def _on_the_night_fixture(monkeypatch):
    """Every workstation here boots on the `night` fixture (ws_helpers): a
    palette theme with no look, drawn in the default's look flat."""
    boot_on_fixture(monkeypatch)

ROOT = runtime.__path__[0] + "/.."
STYLE = json.loads(open(ROOT + "/native/moy_ui/style.json").read())
SAMPLE = ROOT + "/tests/fixtures/fonts/sample6x9.fnt"
ROLES = ("ui", "title", "mono", "caption", "key", "op", "result")
PETME = (0, 8, 8, 7)
BG = 9
TH = theme_colors("night")
LONG = "A LABEL FAR TOO LONG FOR ITS BOX, Wiq"


@pytest.fixture
def sample():
    """The fixture font installed for every role; petme128 back after."""
    rc, fid = ui_draw._C.font_load(open(SAMPLE, "rb").read())
    assert rc == 0
    for role in ROLES:
        assert ui_draw.use_font(role, fid)
    yield fid
    for role in ROLES:
        ui_draw.use_font(role, 0)
    ui_draw._C.font_free(fid)


def _ink(draw, w=200, h=80, fs=1):
    """The bounding box (x0, y0, x1, y1), exclusive, of the pixels `draw(cv,
    text)` paints for its text: the canvas with the text, against the same
    call with the text empty. None when the text painted nothing."""
    planes = []
    for text in (True, False):
        cv = SystemCanvas(w, h, font_scale=fs)
        cv.cls(BG)
        draw(cv, text)
        b = bytes(cv._buf)
        planes.append([b[2 * i:2 * i + 2] for i in range(w * h)])
    hit = [i for i in range(w * h) if planes[0][i] != planes[1][i]]
    if not hit:
        return None
    xs = [i % w for i in hit]
    ys = [i // w for i in hit]
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1)


def _inside(box, rect):
    x, y, w, h = rect
    return box is not None and x <= box[0] and y <= box[1] and \
        box[2] <= x + w and box[3] <= y + h


def test_the_roles_are_style_jsons_and_read_petme128_until_installed():
    assert STYLE["theme"]["font_roles"] == list(ROLES)   # moy_ui.h's MOY_UI_FONT_*
    for role in ROLES:
        assert ui_draw.font(role) == PETME
    for fs in (1, 2, 3):
        lay = Layout(320, 240, fs)
        assert (lay.font_w, lay.line_h) == (8 * fs, 8 * fs)
        code = CodeLayout(320, 240, fs)
        assert (code.cell, code.lh) == (8 * fs, 10 * fs)
    with pytest.raises(ValueError):
        ui_draw.font("body")


def test_an_installed_font_is_what_the_layouts_read(sample):
    for role in ROLES:
        assert ui_draw.font(role) == (sample, 6, 11, 8)
    for fs in (1, 2):
        lay = Layout(320, 240, fs)
        assert (lay.font_w, lay.line_h) == (6 * fs, 11 * fs)
        code = CodeLayout(320, 240, fs)
        assert (code.cell, code.lh) == (6 * fs, 13 * fs)
        x, y, w, h = code.code_area()
        assert x + w <= code.w and y + h <= code.status_band[1]
        assert code.cols == (code.w - code.x0) // code.cell


def test_a_freed_font_reads_as_petme128_again():
    rc, fid = ui_draw._C.font_load(open(SAMPLE, "rb").read())
    assert ui_draw.use_font("ui", fid) and ui_draw.font("ui")[0] == fid
    ui_draw._C.font_free(fid)
    assert ui_draw.font("ui") == PETME
    assert not ui_draw.use_font("ui", fid)              # a stale id installs nothing
    assert not ui_draw.use_font("ui", 12345)


def test_text_width_measures_in_the_roles_font(sample):
    assert ui_draw.text_width("Wiq", 1) == 24            # no role: petme128's cell
    w = ui_draw.text_width("Wiq", 1, "ui")
    assert 0 < w < 24 and ui_draw.text_width("Wiq", 3, "ui") == 3 * w


# -- each primitive's label lands inside its box, as wide as it was measured ----

RECT = (12, 6, 120, 24)


def _button(cv, text):
    ui_draw.button(cv, TH, RECT, LONG if text else "")


def _chip(cv, text):
    ui_draw.chip(cv, TH, RECT, LONG if text else "", fs=cv.font_scale)


def _row(cv, text):
    ui_draw.row(cv, TH, RECT, LONG if text else "", fs=cv.font_scale)


def _cell(cv, text):
    ui_draw.cell(cv, TH, (12, 6, 120, 60), LONG if text else None, fs=cv.font_scale)


def _panel(cv, text):
    # A panel's title is not fitted, so it is one that fits; the strip is
    # drawn either way, so only the title's own ink differs.
    ui_draw.panel(cv, TH, (12, 6, 150, 60), title="SETTINGS" if text else " ", fs=cv.font_scale)


@pytest.mark.parametrize("fs", [1, 2])
@pytest.mark.parametrize("draw, rect", [
    (_button, RECT), (_chip, RECT), (_row, RECT), (_cell, (12, 6, 120, 60)),
    (_panel, (12, 6, 150, 60)),
], ids=["button", "chip", "row", "cell", "panel"])
def test_a_label_lands_inside_its_box(sample, draw, rect, fs):
    box = _ink(draw, fs=fs)
    assert _inside(box, rect), (box, rect)


@pytest.mark.parametrize("fs", [1, 2])
@pytest.mark.parametrize("text", ["Wiq", LONG])
def test_label_draws_as_wide_as_it_measured(sample, fs, text):
    rect = (10, 4, 150, 30)
    got = []

    def draw(cv, on):
        got.append(ui_draw.label(cv, rect, text if on else "", 7))

    box = _ink(draw, fs=fs)
    assert _inside(box, rect)
    width = got[0]
    assert width <= rect[2] and box[2] - box[0] <= width
    if text == "Wiq":
        assert width == ui_draw.text_width(text, fs, "ui")
    # vertically, the line box is centred: the ink starts at its top
    line = ui_draw.font("ui")[2] * fs
    assert box[1] >= rect[1] + (rect[3] - line) // 2


def test_a_rows_label_stops_short_of_its_value(sample):
    rect = (4, 4, 150, 20)

    def both(cv, on):
        ui_draw.row(cv, TH, rect, LONG if on else "", value="99:99", fs=1)

    def value(cv, on):
        ui_draw.row(cv, TH, rect, "", value="99:99" if on else None, fs=1)

    label_box, value_box = _ink(both), _ink(value)
    assert _inside(label_box, rect) and _inside(value_box, rect)
    assert label_box[2] <= value_box[0]


@pytest.mark.parametrize("fs", [1, 2])
def test_tabs_are_as_wide_as_their_labels_and_never_overlap(sample, fs):
    labels = ["CODE", "SPRITES", "MAP"]
    on, rects = ui_draw._C.tab_rects((0, 0, 400, 20), "\0".join(labels).encode(), 3, fs, 2 * fs)
    assert on and len(rects) == 3
    for a, b in zip(rects, rects[1:]):
        assert a[0] + a[2] <= b[0]
    # each grows with its label's width in the role's font, never the 8px cell
    pad = [r[2] - ui_draw.text_width(t, fs, "ui") for r, t in zip(rects, labels)]
    assert pad[0] == pad[1] == pad[2]


@pytest.mark.parametrize("fs", [1, 2])
def test_a_labelled_tab_ends_its_label_a_pad_inside_its_chip(sample, fs):
    """A labelled chip is pad, icon, pad, label, pad: the label's ink stops
    a pad short of the chip's right edge, as the icon starts a pad in (the
    open tab's pill ended on its label's last letter)."""
    pad = ui_draw.metrics("tab")[ui_draw.TAB_PAD] * fs
    rect = (0, 0, 400, 20 * fs)
    for label in ("Code", "Sprites", "MAP"):
        (_tid, (x, y, w, h), on), = ui_draw.tab_row_rects(rect, [("t", label)], fs)
        assert on

        def draw(cv, text, r=(x, y, w, h), label=label):
            ui_draw.tab(cv, TH, r, False, label if text else "", True)

        box = _ink(draw, w=400, h=20 * fs, fs=fs)
        assert box[0] >= x + 2 * pad + ui_draw.metrics("tab")[ui_draw.TAB_ICON] * fs
        assert box[2] <= x + w - pad, (label, box, (x, w))


@pytest.mark.parametrize("fs", [1, 2])
def test_a_frames_title_stays_clear_of_its_buttons(sample, fs):
    win, th_ = (10, 10, 180, 100), 18 * fs

    def draw(cv, on):
        ui_draw.frame(cv, TH, win, th_, LONG if on else "", True, True, fs)

    box = _ink(draw, w=220, h=140, fs=fs)
    assert _inside(box, (win[0], win[1], win[2], th_ + 1))
    for _id, (x, y, w, h), _right in ui_draw.frame_buttons(win, th_, LONG, True, fs):
        assert box[2] <= x or x + w <= box[0]


def test_the_game_canvas_stays_on_petme128():
    """game_btn, game_icon_btn and text_field draw on the game canvas, which
    a theme does not reach (§6): the same pixels whatever the roles name."""
    def frame():
        cv = SystemCanvas(160, 80)
        cv.cls(BG)
        ui_draw.game_btn(cv, (4, 4, 120, 20), "PLAY", 1)
        ui_draw.game_icon_btn(cv, (4, 28, 120, 20), "play", "GO", 1)
        ui_draw.text_field(cv, (4, 52, 120, 20), "name", "")
        return indices_of(cv)

    before = frame()
    rc, fid = ui_draw._C.font_load(open(SAMPLE, "rb").read())
    try:
        for role in ROLES:
            ui_draw.use_font(role, fid)
        assert frame() == before
    finally:
        for role in ROLES:
            ui_draw.use_font(role, 0)
        ui_draw._C.font_free(fid)


@pytest.mark.parametrize("fs", [1, 2, 3])
@pytest.mark.parametrize("kind", ["button", "chip"])
def test_a_short_label_sits_where_its_metrics_centre_it(sample, kind, fs):
    """Centred in both axes by the font's own numbers: across by its drawn
    width, down by its line advance, the glyph box hanging from the line's
    top. An 8px cell in either sum moves the ink out of this window."""
    text, rect = "Hq", (10, 6, 100, 14 * fs)

    def draw(cv, on):
        if kind == "button":
            ui_draw.button(cv, TH, rect, text if on else "")
        else:
            ui_draw.chip(cv, TH, rect, text if on else "", fs=fs)

    box = _ink(draw, w=140, h=60, fs=fs)
    _fid, _w, line, _asc = ui_draw.font("ui")
    tw = ui_draw.text_width(text, fs, "ui")
    tx = rect[0] + (rect[2] - tw) // 2
    ty = rect[1] + (rect[3] - line * fs) // 2
    assert tx <= box[0] and box[2] <= tx + tw
    assert ty <= box[1] and box[3] <= ty + 9 * fs           # the glyph box's height
