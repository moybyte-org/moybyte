"""The public UI API's parts (docs/look_2026-10.md step 12; the verbs are
docs/ui_api_2026-10.md §1's): a test per kind under the engine, and one parts
page drawn by a Python, a Lua and a compiled app to the same pixels.

  * group: a caption, then a card under its rows -- rounded where the theme
    has a depth, square and ringed where it has none -- whose foot keeps the
    last row's focus mark and draws no second one;
  * info is no stop; link answers its activation by key and by tap;
  * cycle wraps, seg clamps, swatches pick by tap and draw their colours;
  * toggle draws its On/Off word and flips; slider (a segment a step, with
    `steps`) maps the pointer over its track and steps by arrow;
  * hints take the page's foot, which its rows then stop above;
  * an icon goes on the next row or button; progress is no stop;
  * a table's current row follows focus and opens by Enter or a second tap,
    a scroll region keeps the focused row in its rect, a field types each
    key once, and a dialog holds the input with focus on its first stop;
  * a page begun in a window lies on the window's body, a fullscreen one on
    its own ground, and every runtime's host says which.

The Files verbs (step 21) have a page of their own drawn the same three
ways (`tests/fixtures/ui_parts/files_*.moy`, `tests/fixtures/wasm/app_files.moy`),
and so do Appearance's (step 20): a tab row moves by left and right and takes
a tap, and a grid of cards lays them in centred columns, moves between them,
activates one and cuts and rings each picture the app draws
(`tests/fixtures/ui_parts/look_*.moy`, `tests/fixtures/wasm/app_look.moy`).
The sprite editor's popover (step 24) opens focus in it under its opener or
`at`'s rect, holds the input, and goes on a press outside or Esc; its page is
drawn the same three ways too (`tests/fixtures/ui_parts/pop_*.moy`,
`tests/fixtures/wasm/app_pop.moy`).
"""

import os
import shutil
import tempfile
from array import array
from pathlib import Path

import pytest

from runtime import host_app, ui, ui_draw
from runtime.host_canvas import make_system_canvas
from runtime.chrome import THEME_NAMES
from ws_helpers import MOY_VARIANTS, build_ws, make_drv, open_cart, quiesce, use_theme

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARTS = os.path.join(ROOT, "tests", "fixtures", "ui_parts")
WASM = os.path.join(ROOT, "tests", "fixtures", "wasm", "app_parts.moy")
_DT = 1.0 / 30.0
(P_FRAMES, P_OPENS, P_SIZE, P_MODE, P_ACCENT, P_FOCUS, P_FAIL, P_TOP, P_NAME, P_DONES,
 P_LEVEL) = range(11)
N_PMEM = 11
BITS = {"left": 1, "right": 2, "up": 4, "down": 8, "a": 16, "b": 32, "run": 64}

_THEMES = {}
# The seeds' theme-modes.
_FIVE = (("moy", "light"), ("moy", "dark"), ("blocks", "light"), ("contrast", "light"),
         ("contrast", "dark"))


def _theme(name, variant):
    """The theme colours of a workstation in `name` (a seed, else a fixture
    package, tests/fixtures/themes/), its look installed."""
    ws = _THEMES.get((name, variant))
    if ws is None:
        ws = build_ws(Path(tempfile.mkdtemp()))
        _THEMES[(name, variant)] = ws
    if name in THEME_NAMES:
        ws.look.set_theme(name, persist=False, variant=variant)
    else:
        use_theme(ws, name, variant)
    return ws.theme_colors


class _Run:
    """A page of verbs on a system canvas under a theme, frame by frame."""

    def __init__(self, draw, theme=("moy", "light"), app=None):
        self.th = _theme(*theme)
        self.app = app
        self.cv = make_system_canvas(320, 240)
        self.ctx = ui_draw.ui_context()
        self.inp = array("i", [0] * ui_draw.UI_IN_LEN)
        self.draw = draw
        self.out = None

    def frame(self, press=(), ptr=None, typed=0):
        i = self.inp
        i[0] = i[1] = 0
        for k in press:
            i[1] |= BITS[k]
        i[0] = i[1]
        i[2], i[3], i[4] = ptr if ptr is not None else (0, 0, 0)
        i[5] = ord(typed) if isinstance(typed, str) else typed
        ui_draw.ui_frame(self.ctx, self.cv, self.th, None, i, app=self.app)
        self.out = self.draw()
        return self.out

    def tap(self, x, y):
        self.frame(ptr=(x, y, 1 | 2 | 4))
        return self.frame(ptr=(x, y, 1))

    def px(self, x, y):
        b = self.cv._buf
        o = 2 * (y * self.cv.w + x)
        return b[o] << 8 | b[o + 1]


# The handheld's geometry (style.json's `group` and `value_row` metrics at fs 1).
INSET, PAD, CAPTION, ROW, GAP = 6, 10, 19, 20, 3
HINTS = 20      # the hint band a page's foot gives up


def _group_page(n=3, last_focus=False, theme=("moy", "light")):
    st = {}

    def draw():
        st["rect"] = ui.page("")
        ui.group("General")
        for i in range(n):
            st[i] = ui.link("Row %d" % i, "v")
        ui.group()
        if last_focus:
            ui.focus("Row %d" % (n - 1))
        return st
    return _Run(draw, theme)


def test_a_group_is_a_caption_and_a_card_its_corners_rounded_by_the_depth():
    r = _group_page()
    r.frame()
    r.frame()
    top = r.out["rect"][1] + CAPTION
    bottom = top + 3 * ROW
    ground = r.px(2, top + 30)
    card = r.px(160, top + ROW + 10)
    assert card != ground, "the card is the surface colour on the panel's ground"
    # (the first row holds focus: its edge bar is at the top left corner)
    for x, y in ((319 - INSET, top), (INSET, bottom - 1), (319 - INSET, bottom - 1)):
        assert r.px(x, y) == ground, "a card corner at (%d, %d) is cut" % (x, y)
    assert r.px(INSET, top + ROW + 10) != ground, "the card's side is square between its corners"
    assert r.px(INSET + PAD + 40, top + 2 * ROW) != card, "a rule heads the third row"
    # the focused first row's plate is the line between it and the second
    assert r.px(INSET + PAD + 40, top + ROW) == card, "no rule under the focused row"
    assert r.px(INSET + PAD + 40, top) != card, "the focused row is on its plate"


def test_the_cards_foot_keeps_the_last_rows_focus_mark_and_draws_no_second():
    last = _group_page(n=3, last_focus=True)
    for _ in range(3):
        ui_draw.marks(None, True)
        last.frame()
    assert ui_draw.marks()[1] == 1, "one focus mark a frame"
    top = last.out["rect"][1] + CAPTION
    y = top + 3 * ROW - 4                 # inside the foot's corner, on the edge bar
    bar = last.px(INSET + 1, y)
    mid = last.px(INSET + 1, top + 2 * ROW + 10)
    assert bar == mid, "the edge bar runs into the rounded foot"
    assert last.px(INSET + 1, top + ROW + 10) != mid, "only the focused row wears it"


@pytest.mark.parametrize("theme", _FIVE, ids=lambda t: "-".join(t))
def test_touch_draws_no_focus_mark_and_a_key_brings_it_back(theme):
    """Under touch, as on a phone, nothing wears a focus mark: the focused
    row draws as it does at rest, and the shell's word on whether focus
    draws is kept. The first key or pad press brings the mark back."""
    was = ui_draw.input_kind()
    try:
        ui_draw.input_kind("keys")
        keys = _group_page(n=3, last_focus=True, theme=theme)
        rest = _group_page(n=3, theme=theme)        # focus on the first row
        for r in (keys, rest):
            r.frame()
            r.frame()
        ui_draw.input_kind("touch")
        assert ui_draw.marks()[0], "the shell's word stays: focus draws"
        touch = _group_page(n=3, last_focus=True, theme=theme)
        rest_t = _group_page(n=3, theme=theme)
        for r in (touch, rest_t):
            r.frame()
            ui_draw.marks(None, True)
            r.frame()
            assert ui_draw.marks()[1] == 0, "no focus mark is drawn under touch"
        assert bytes(touch.cv._buf) == bytes(rest_t.cv._buf), "the focused row draws as at rest"
        ui_draw.input_kind("pad")
        touch.frame()
        assert bytes(touch.cv._buf) == bytes(keys.cv._buf), "a pad's press brings it back"
    finally:
        ui_draw.input_kind(was)


def test_info_is_no_stop_and_link_answers_its_activation():
    st = {}

    def draw():
        ui.page("")
        ui.group("G")
        ui.info("Board", "T-Deck")
        st["a"] = ui.link("Wi-Fi", "Home")
        st["b"] = ui.link("Bluetooth", "Off")
        ui.group()
        return st
    r = _Run(draw)
    r.frame()
    assert ui.focus() == "Wi-Fi", "the info row takes no focus"
    got = r.frame(("a",))
    assert got["a"] and not got["b"]
    assert not r.frame()["a"]
    got = r.tap(100, CAPTION + 2 * ROW + 10)
    assert ui.focus() == "Bluetooth" and got["b"], "a tap activates and focuses"


def _choice_page(kind, items, cur=0):
    st = {"cur": cur}

    def draw():
        ui.page("")
        ui.group("G")
        st["cur"] = getattr(ui, kind)("Pick", items, st["cur"])
        ui.group()
        return st
    return _Run(draw), st


def test_cycle_wraps_both_ways_and_activation_takes_the_next():
    r, st = _choice_page("cycle", ["S", "M", "L"], 1)
    r.frame()
    r.frame(("right",))
    assert st["cur"] == 2
    r.frame(("right",))
    assert st["cur"] == 0, "right past the last wraps"
    r.frame(("left",))
    assert st["cur"] == 2, "left past the first wraps"
    r.frame(("a",))
    assert st["cur"] == 0
    assert ui.focus() == "Pick", "left and right are the cycle's own"


def test_seg_clamps_and_a_tap_picks_its_option():
    r, st = _choice_page("seg", ["Day", "Night"], 0)
    r.frame()
    r.frame(("left",))
    assert st["cur"] == 0, "a seg stops at its first option"
    r.frame(("right",))
    r.frame(("right",))
    assert st["cur"] == 1
    before = bytes(r.cv._buf)
    # the options are right-aligned, Day left of Night: walk taps leftward
    for x in range(300, 150, -3):
        r.tap(x, CAPTION + 10)
        if st["cur"] == 0:
            break
    assert st["cur"] == 0, "a tap on Day picks it"
    assert bytes(r.cv._buf) != before, "the current mark moved"


def test_swatches_draw_their_colours_and_a_tap_picks_one():
    cols = [0xff0000, 0x00c000, 0x0000ff]
    r, st = _choice_page("swatches", cols, 0)
    r.frame()
    r.frame()
    y = CAPTION + 10
    right = 320 - INSET - PAD
    # dot i's centre: right - 11 + 5 - (n - 1 - i) * 15
    centres = [right - 6 - (2 - i) * 15 for i in range(3)]
    seen = {r.px(x, y) for x in centres}
    assert len(seen) == 3, "three colours"
    assert r.px(centres[2], y) >> 11 == 0 and r.px(centres[2], y) & 31 == 31, "exact blue"
    r.tap(centres[2], y)
    assert st["cur"] == 2
    r.frame(("left",))
    assert st["cur"] == 1


def test_the_current_swatch_wears_an_ink_ring_off_a_ground_gap():
    """The current colour is ringed in the row's ink, the row's ground
    showing between ring and dot, not the theme's current plate."""
    cols = [0xff0000, 0x00c000, 0x0000ff]
    r, _st = _choice_page("swatches", cols, 0)
    r.frame()
    r.frame()
    y = CAPTION + 10
    right = 320 - INSET - PAD
    cx = right - 6 - 2 * 15
    red = r.px(cx, y)
    x = cx
    while r.px(x - 1, y) == red:
        x -= 1
    ground = r.px(x - 20 if x > 40 else 2, y)
    # a pixel or two of AA edge, then the gap in the ground, then the ring
    run = [r.px(x - k, y) for k in range(1, 7)]
    assert ground in run, run
    gap_at = run.index(ground)
    assert any(p != ground for p in run[gap_at:]), "an ink ring outside the gap"
    # the ring is the ink, not the plate: its middle is far from the ground
    ring = next(p for p in run[gap_at:] if p != ground)
    lum = lambda p: (p >> 11) * 2 + ((p >> 5) & 63) + (p & 31) * 2   # noqa: E731
    assert abs(lum(ring) - lum(ground)) > 40, (hex(ring), hex(ground))


def test_the_current_swatchs_ring_stands_clear_of_its_neighbours():
    """3 px of the row's ground between the current dot's ring and the dots
    either side (the handheld's ring ran a pixel from the next dot)."""
    cols = [0xff0000, 0x00c000, 0x0000ff]
    r, _st = _choice_page("swatches", cols, 1)
    r.frame()
    r.frame()
    y = CAPTION + 10
    green = next(x for x in range(319, 0, -1) if _rgb(r.px(x, y))[1] > 150
                 and _rgb(r.px(x, y))[0] < 80)
    x = green
    while r.px(x - 1, y) == r.px(green, y):
        x -= 1
    mid = (x + green) // 2
    ground = r.px(200, y)                          # the card between the label and the dots
    for step in (1, -1):
        xx = mid
        while r.px(xx, y) == r.px(mid, y):          # the dot
            xx += step
        while r.px(xx, y) != ground:                # its antialiased edge
            xx += step
        while r.px(xx, y) == ground:                # the ground inside the ring
            xx += step
        while r.px(xx, y) != ground:                # the ring
            xx += step
        clear = 0
        while r.px(xx, y) == ground:
            xx += step
            clear += 1
        assert clear >= 3, (step, clear)


@pytest.mark.parametrize("theme", _FIVE, ids=lambda t: "-".join(t))
@pytest.mark.parametrize("ground", ["surface", "chip"])
def test_a_label_on_a_current_plate_clears_it_for_text(theme, ground):
    """A label on a current plate -- a row's current option on its card, the
    desk bar's chip of the focused window -- is text, and clears the plate
    4.5:1 as drawn (Moy Night's chip printed #b4aaf6 on #414473, 4.36:1)."""
    th = _theme(*theme)
    cv = make_system_canvas(80, 40)
    cv.rect(0, 0, 80, 40, th["surface"])
    if ground == "chip":
        if not ui_draw.bar_part(cv, th, ui_draw.BAR_CHIP, (4, 4, 72, 32), True):
            pytest.skip("a theme with no depth draws no chip")
    else:
        ui_draw.current(cv, th, (4, 4, 72, 32))
    ink = ui_draw.current_ink(cv, th)
    ink = th["chrome_ink" if ground == "chip" else "ink"] if ink is None else ink
    px = lambda x, y: cv._buf[2 * (y * 80 + x)] << 8 | cv._buf[2 * (y * 80 + x) + 1]  # noqa
    plate = px(40, 20)
    cv.rect(0, 0, 2, 2, ink)
    assert _ratio(px(0, 0), plate) >= 4.5, (theme, round(_ratio(px(0, 0), plate), 2))


@pytest.mark.parametrize("theme", _FIVE, ids=lambda t: "-".join(t))
def test_the_current_marks_content_takes_an_ink_that_reads_on_it(theme):
    """Where a look gives the current content an ink of its own
    (`current.ink`, the second cue beside a soft plate: Moy's plate alone is
    1.15 to 1.32:1 on its ground), the mark answers it in front and in a
    window behind, and it holds the theme floor's 3:1 on the plate as drawn
    (theme.contrast; the plate's readability bit holds it too). Moy gives
    it one. A solid plate (Contrast's) is its outline in a window behind,
    the content there in its own ink."""
    th = _theme(*theme)
    cv = make_system_canvas(40, 20)
    cv.rect(0, 0, 40, 20, th["surface"])
    ink = ui_draw.current(cv, th, (2, 2, 36, 16))
    if ink is None:
        assert theme[0] != "moy", "Moy's current names an ink"
        return
    assert ink != th["ink"]
    from runtime.chrome import theme_look
    solid = theme_look(*theme)["current.alpha"] >= 100
    behind = ui_draw.current(cv, th, (2, 2, 36, 16), back=True)
    assert behind == (None if solid else ink)
    cv.rect(0, 0, 40, 20, th["surface"])
    ui_draw.current(cv, th, (2, 2, 36, 16))
    assert ui_draw.current_ink(cv, th) == ink
    px = lambda x, y: cv._buf[2 * (y * 40 + x)] << 8 | cv._buf[2 * (y * 40 + x) + 1]  # noqa
    plate = px(20, 10)
    cv.rect(0, 0, 2, 2, ink)
    assert _ratio(px(0, 0), plate) >= 3.0, (theme, round(_ratio(px(0, 0), plate), 2))


def test_toggle_draws_its_word_and_flips():
    st = {"on": False}

    def draw():
        ui.page("")
        st["on"] = ui.toggle("Sound", st["on"])
        return st
    r = _Run(draw)
    r.frame()
    off = bytes(r.cv._buf)
    r.frame(("a",))
    assert st["on"]
    r.frame()
    assert bytes(r.cv._buf) != off, "On draws differently from Off"
    r.frame(("a",))
    assert not st["on"]


def test_slider_maps_the_pointer_over_its_track_and_steps_by_arrow():
    st = {"v": 5}

    def draw():
        ui.page("")
        st["v"] = ui.slider("Volume", st["v"], 0, 10)
        return st
    r = _Run(draw)
    r.frame()
    r.frame(("right",))
    assert st["v"] == 6
    right = 320 - 4                       # the row's pad off the group
    r.frame(ptr=(right - 84, 10, 1 | 2 | 4))
    r.frame(ptr=(right - 84, 10, 1 | 2))
    assert st["v"] == 6, "a press that has not moved is no drag yet"
    r.frame(ptr=(right - 84, 10, 1))
    assert st["v"] == 0, "a tap on the track's left end is lo"
    r.frame(ptr=(right - 84, 10, 1 | 2 | 4))
    r.frame(ptr=(right, 10, 1 | 2))
    assert st["v"] == 10, "dragged sideways to its right end: hi"
    r.frame(ptr=(right, 10, 1))
    assert st["v"] == 10


def _long_page():
    """A slider and a seg among enough links that the page scrolls."""
    st = {"v": 5, "mode": 0}

    def draw():
        ui.page("")
        ui.link("Top", "")
        st["v"] = ui.slider("Level", st["v"], 0, 10)
        st["mode"] = ui.seg("Mode", ["One", "Two", "Three"], st["mode"])
        for i in range(16):
            ui.link("Row %d" % i, "")
        return st
    r = _Run(draw)
    r.frame()
    r.frame()
    return r, st


def _groups_page(groups=5, rows=3):
    def draw():
        st = {"rect": ui.page("")}
        ui.hints(("confirm", "Open"))
        for g in range(groups):
            ui.group("Group %d" % g)
            for i in range(rows):
                ui.link("G%d R%d" % (g, i), "")
        ui.group()
        return st
    r = _Run(draw)
    r.frame()
    r.frame()
    return r


def test_keys_scroll_a_header_wholly_in_or_out_and_a_first_row_brings_its():
    """Moving focus scrolls the page so no group's caption is cut by the
    page's top edge -- wholly in, or wholly out -- and a group's first row
    comes into view with its caption (a Settings page scrolled by keys cut
    "General" in half under the bar)."""
    groups, rows = 5, 3
    r = _groups_page(groups, rows)
    ids = ["G%d R%d" % (g, i) for g in range(groups) for i in range(rows)]
    at0 = {i: _box(r, i)[1] for i in ids}               # content y at scroll 0
    top = r.out["rect"][1]
    caps = [at0["G%d R0" % g] - CAPTION for g in range(groups)]
    walk = ["down"] * (len(ids) - 1) + ["up"] * (len(ids) - 1)
    for key in walk:
        r.frame((key,))
        r.frame()
        f = ui.focus()
        scroll = at0[ids[0]] - _box(r, ids[0])[1]
        view = top + scroll
        for g, cy in enumerate(caps):
            assert not cy < view < cy + CAPTION, \
                "focus on %s: Group %d's caption cut %d px" % (f, g, view - cy)
        if f.endswith(" R0"):
            g = int(f[1:f.index(" ")])
            assert caps[g] >= view, "focus on %s: its caption is out of view" % f
        _x, y, _w, h = _box(r, f)
        assert top <= y and y + h <= top + r.out["rect"][3] - HINTS, \
            "focus on %s: the row is out of view (above the hint band)" % f


def _drag(r, x0, y0, x1, y1, steps=4):
    r.frame(ptr=(x0, y0, 1 | 2 | 4))
    for i in range(1, steps + 1):
        r.frame(ptr=(x0 + (x1 - x0) * i // steps, y0 + (y1 - y0) * i // steps, 1 | 2))
    r.frame(ptr=(x1, y1, 1))


def test_a_vertical_drag_from_a_slider_or_a_seg_scrolls_the_page():
    """As on a phone: the drag's direction is decided at the slop, and a
    mostly-vertical one scrolls the page from a slider's row (its track
    included) and a seg's, changing neither value."""
    r, st = _long_page()
    x, y, w, h = _box(r, "Level")[:4]
    _drag(r, x + w - 40, y + h // 2, x + w - 34, y + h // 2 - 30)
    assert _box(r, "Level")[1] == y - 30, "the page scrolled with the drag"
    assert st["v"] == 5, "the slider kept its value"
    x, y, w, h = _box(r, "Mode")[:4]
    _drag(r, x + w - 10, y + h // 2, x + w - 13, y + h // 2 - 20)
    assert _box(r, "Mode")[1] == y - 20, "a seg's row scrolls the page too"
    assert st["mode"] == 0, "and is no tap"


def test_a_sideways_drag_moves_a_slider_and_does_not_scroll():
    r, st = _long_page()
    x, y, w, h = _box(r, "Level")[:4]
    right = x + w
    _drag(r, right - 60, y + h // 2, right, y + h // 2 - 8)
    assert st["v"] == 10, "the knob followed the pointer to the track's end"
    assert _box(r, "Level")[1] == y, "the page did not scroll"
    x, y, w, h = _box(r, "Mode")[:4]
    r.tap(x + w - 10, y + h // 2)
    assert st["mode"] == 2, "a tap on a seg still takes the option tapped"


def _level_page(v, o):
    """A group of a link, a 0..7 slider under override `o` and a link."""
    st = {"v": v}

    def draw():
        st["rect"] = ui.page("")
        ui.group("Sound")
        ui.link("Top", "")
        st["v"] = ui.slider("Volume", st["v"], 0, 7, o)
        ui.link("Foot", "")
        ui.group()
        return st
    r = _Run(draw)
    r.frame()
    r.frame()
    return r, st


def test_a_stepped_slider_draws_a_segment_a_step_and_no_number():
    """`steps`: seven segments for 0..7, the power popover's gauge (6 wide on
    an 8 pitch) right-aligned in the track, the first `v` in the value
    colour; no number and no knob, where a plain slider has both."""
    r, _st = _level_page(3, {"steps": True})
    plain, _pst = _level_page(3, None)
    mid = r.out["rect"][1] + CAPTION + ROW + ROW // 2
    right = 320 - INSET - PAD
    x0 = right - (7 * 8 - 2)
    card = r.px(x0 + 6, mid)
    assert card == r.px(120, mid), "the gap between segments is the card"
    on, off = r.px(x0 + 2, mid), r.px(x0 + 3 * 8 + 2, mid)
    assert on != card and off != card and on != off
    assert [r.px(x0 + i * 8 + 2, mid) for i in range(7)] == [on] * 3 + [off] * 4
    assert r.px(x0 + 6 * 8 + 5, mid) == off and r.px(x0 + 6 * 8 + 6, mid) == card, \
        "the last segment ends at the row's pad"
    assert all(r.px(x, mid) == card for x in range(100, x0)), "no number, no knob"
    assert any(plain.px(x, mid) != card for x in range(100, x0)), \
        "a plain slider draws its number and track there"


def test_a_stepped_slider_takes_a_tap_on_a_segment_and_the_arrows():
    r, st = _level_page(3, {"steps": True})
    mid = r.out["rect"][1] + CAPTION + ROW + ROW // 2
    x0 = 320 - INSET - PAD - (7 * 8 - 2)
    r.tap(x0 + 4 * 8 + 2, mid)
    assert st["v"] == 5, "a tap on segment 4 fills it and those before it"
    r.tap(x0 - 10, mid)
    assert st["v"] == 0, "left of the segments is lo: muted"
    r.frame(("right",))
    assert st["v"] == 1


def _rgb(p):
    return ((p >> 11) * 255 // 31, (p >> 5 & 63) * 255 // 63, (p & 31) * 255 // 31)


def _ratio(a, b):
    """The WCAG contrast of two RGB565 words."""
    def lum(p):
        out = []
        for c in _rgb(p):
            c /= 255
            out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _blend(p, a, b):
    """Whether RGB565 `p` lies between `a` and `b` (an antialiased edge)."""
    pa, pb, pp = _rgb(a), _rgb(b), _rgb(p)
    d = [pb[k] - pa[k] for k in range(3)]
    n = sum(v * v for v in d)
    if n == 0:
        return False
    t = sum((pp[k] - pa[k]) * d[k] for k in range(3)) / n
    return 0 < t < 1 and all(abs(pa[k] + t * d[k] - pp[k]) <= 16 for k in range(3))


_VALUE_ROWS = ("On", "Off", "Level", "Steps")


def _value_rows_page(theme, focus):
    """A group of a toggle On, a toggle Off, a slider and a stepped slider,
    `focus` the focused one."""
    def draw():
        st = {"rect": ui.page("")}
        ui.group("G")
        ui.toggle("On", True)
        ui.toggle("Off", False)
        ui.slider("Level", 5, 0, 10)
        ui.slider("Steps", 3, 0, 7, {"steps": True})
        ui.group()
        ui.focus(focus)
        return st
    r = _Run(draw, theme)
    for _ in range(3):
        r.frame()
    return r


# The seeds whose part focus mark is a plate in the theme's focus colour
# (`look.focus` invert): the row's controls lie on it.
_INVERT_ROWS = (("contrast", "dark"), ("contrast", "light"), ("blocks", "light"))


@pytest.mark.parametrize("theme", _INVERT_ROWS, ids=lambda t: "-".join(t))
@pytest.mark.parametrize("row", _VALUE_ROWS)
def test_a_focused_value_rows_controls_read_on_its_focus_plate(theme, row):
    """On a row filled by its focus mark, the toggle's track, knob and
    outline, the slider's track and fill and the stepped bar draw in the
    mark's ink, each clearing the plate 3:1 (a white track on Contrast
    Dark's yellow was 1.27:1)."""
    i = _VALUE_ROWS.index(row)
    r = _value_rows_page(theme, row)
    y0 = r.out["rect"][1] + CAPTION + i * ROW
    plate = r.px(INSET + 6, y0 + ROW // 2)
    calm = _value_rows_page(theme, _VALUE_ROWS[(i + 1) % len(_VALUE_ROWS)])
    assert plate != calm.px(INSET + 6, y0 + ROW // 2), "the focus mark is a plate"
    right = 320 - INSET - PAD
    counts = {}
    for y in range(y0 + 2, y0 + ROW - 2):
        for x in range(right - 90, right):
            p = r.px(x, y)
            counts[p] = counts.get(p, 0) + 1
    inks = [p for p, n in counts.items() if p != plate and n >= 6]
    clear = [p for p in inks if _ratio(p, plate) >= 3.0]
    assert clear, "the control draws"
    for p in inks:
        if any(_blend(p, plate, c) for c in clear):
            continue                       # an edge's antialiasing
        assert _ratio(p, plate) >= 3.0, (
            "%s: %06x on the plate %06x is %.2f:1" % (row, int("%02x%02x%02x" % _rgb(p), 16),
                                                      int("%02x%02x%02x" % _rgb(plate), 16),
                                                      _ratio(p, plate)))


@pytest.mark.parametrize("variant", ["dark", "light"])
def test_contrasts_card_frame_is_two_pixels_and_runs_past_a_focused_row(variant):
    """Contrast frames its cards at 2 px, as its windows and bars (look
    `card.frame`). A focused row's plate stays inside the frame (Dark's
    yellow covered it), and its edge has one weight all round: Light's
    outline is the frame's width, and the next row draws no rule under it
    (2 px over the row, 3 under it)."""
    theme = ("contrast", variant)
    r = _value_rows_page(theme, "Off")
    top = r.out["rect"][1] + CAPTION
    card = r.px(160, top + 3 * ROW + ROW // 2 - 6)
    frame = r.px(INSET, top + 3 * ROW + 4)
    assert frame != card
    assert r.px(INSET + 1, top + 3 * ROW + 4) == frame, "a 2 px frame"
    assert r.px(INSET + 2, top + 3 * ROW + 4) == card
    y0, mid = top + ROW, top + ROW + ROW // 2
    assert r.px(INSET, mid) == r.px(INSET + 1, mid) == frame, "the frame runs past the row"
    plate = r.px(INSET + 6, mid)
    assert r.px(INSET + 2, mid) in (plate, frame)
    # down the middle: the lines over the plate and under it, to the card
    col = [r.px(160, y) for y in range(y0 - 1, y0 + ROW + 3)]
    first = col.index(plate)
    last = len(col) - 1 - col[::-1].index(plate)
    over = 0
    while col[first - 1 - over] not in (card, plate):
        over += 1
    under = 0
    while col[last + 1 + under] not in (card, plate):
        under += 1
    assert over == under, (over, under)


@pytest.mark.parametrize("theme", _INVERT_ROWS, ids=lambda t: "-".join(t))
def test_a_focused_stepped_bar_fills_its_steps_in_the_rows_ink(theme):
    """The filled steps are the bright part on the plate: the row's ink, the
    rest the plate inside an outline (Blocks' empty steps were brighter
    than its filled ones)."""
    r = _value_rows_page(theme, "Steps")
    mid = r.out["rect"][1] + CAPTION + 3 * ROW + ROW // 2
    plate = r.px(INSET + 6, mid)
    x0 = 320 - INSET - PAD - (7 * 8 - 2)
    on = r.px(x0 + 2, mid)
    assert _ratio(on, plate) >= 3.0
    assert [r.px(x0 + k * 8 + 2, mid) for k in range(7)] == [on] * 3 + [plate] * 4
    assert r.px(x0 + 3 * 8, mid) == on, "an empty step is outlined in the ink"


def _seg_page(theme, cur):
    def draw():
        st = {"rect": ui.page("")}
        ui.group("G")
        ui.seg("Mode", ["AAA", "BBB"], cur)
        ui.group()
        return st
    r = _Run(draw, theme)
    for _ in range(3):
        r.frame()
    return r


@pytest.mark.parametrize("theme", _INVERT_ROWS, ids=lambda t: "-".join(t))
@pytest.mark.parametrize("cur", [0, 1])
def test_a_focused_segs_current_option_reads_on_its_focus_plate(theme, cur):
    """On a row filled by its focus mark, the current option is a pill in
    the mark's ink, clearing the plate 3:1, its label in the plate's colour
    on it (Contrast Dark's current pill was #ffee62 on the #ffe600 plate,
    1.07:1, with the same black labels as the other option)."""
    a, b = _seg_page(theme, cur), _seg_page(theme, 1 - cur)
    y0 = a.out["rect"][1] + CAPTION
    plate = a.px(INSET + 6, y0 + ROW // 2)
    diff = [(x, y) for x in range(INSET, 320 - INSET) for y in range(y0 + 1, y0 + ROW - 1)
            if a.px(x, y) != b.px(x, y)]
    xs = sorted({x for x, _y in diff})
    half = (xs[0] + xs[-1]) // 2
    mine = [a.px(x, y) for x, y in diff if (x <= half) == (cur == 0)]
    counts = {}
    for p in mine:
        counts[p] = counts.get(p, 0) + 1
    fill = max((p for p in counts if p != plate), key=lambda p: counts[p])
    assert _ratio(fill, plate) >= 3.0, "the current pill %.2f:1 on the plate" % _ratio(fill, plate)
    label = max((p for p in counts if p != fill and counts[p] >= 3), key=lambda p: _ratio(p, fill))
    assert _ratio(label, fill) >= 4.5, "its label %.2f:1 on it" % _ratio(label, fill)


def _swatch_page(theme, colours, focus=True):
    def draw():
        st = {"rect": ui.page("")}
        ui.group("G")
        ui.link("Top", "")
        ui.swatches("Accent", colours, 0)
        ui.group()
        ui.focus("Accent" if focus else "Top")
        return st
    r = _Run(draw, theme)
    for _ in range(3):
        r.frame()
    return r


@pytest.mark.parametrize("theme", _FIVE, ids=lambda t: "-".join(t))
@pytest.mark.parametrize("focus", [True, False], ids=["focused", "rest"])
def test_a_swatch_its_ground_hides_wears_a_keyline(theme, focus):
    """A swatch in its row ground's own colour -- Contrast's yellow on its
    yellow focus plate, a white dot on a white card -- wears a keyline in
    the row's ink, so it still shows."""
    probe = _swatch_page(theme, [0x000000, 0x000000], focus)
    y = probe.out["rect"][1] + CAPTION + ROW + ROW // 2
    gx = 160                                  # the row's ground: its plate, or its card
    ground = probe.px(gx, y)
    g = _rgb(ground)
    same = _swatch_page(theme, [0x000000, g[0] << 16 | g[1] << 8 | g[2]], focus)
    other = _swatch_page(theme, [0x000000, 0xffffff - (g[0] << 16 | g[1] << 8 | g[2])], focus)
    xs = [x for x in range(160, 320 - INSET) if same.px(x, y) != other.px(x, y)]
    assert xs, "the second dot draws"
    dot = [same.px(x, yy) for x in range(xs[0] - 1, xs[-1] + 2) for yy in range(y - 6, y + 7)]
    assert any(_ratio(p, ground) >= 3.0 for p in dot), "the dot in its ground's colour is lost"


_SURFACES = {}


def _surface_theme(desk, name, variant):
    """The theme colours of a handheld or a desk workstation in `name`, its
    look and its class's faces installed."""
    ws = _SURFACES.get(desk)
    if ws is None:
        kw = dict(sys_size=(1024, 600), font_scale=1, windowed=True,
                  panel_diagonal_in=7.0) if desk else {}
        ws = build_ws(Path(tempfile.mkdtemp()), **kw)
        _SURFACES[desk] = ws
    ws.look.set_theme(name, persist=False, variant=variant)
    return ws.theme_colors


def _hint_shape(desk, theme, items):
    """(cap height, legend ink rows, legend ink columns, word ink rows) of
    one keycap hint pair drawn on the panel."""
    th = _surface_theme(desk, *theme)
    h = 40
    cv = make_system_canvas(400, h)
    cv.rect(0, 0, 400, h, th["panel"])
    ui_draw.input_kind("keys")
    (x0, x1), = ui_draw.hints(cv, th, (0, 0, 400, h), items, desk)
    px = lambda x, y: cv._buf[2 * (y * 400 + x)] << 8 | cv._buf[2 * (y * 400 + x) + 1]  # noqa
    bg = px(0, 0)
    x = x0
    while px(x, h // 2) == bg:
        x += 1
    cx0 = x
    while px(x, h // 2) != bg:
        x += 1
    cx1 = x
    col = [y for y in range(h) if px((cx0 + cx1) // 2, y) != bg]
    cap = col[-1] - col[0] + 1
    fill = px(cx0 + 3, col[0] + 3)
    inner = [(xx, y) for xx in range(cx0 + 3, cx1 - 3) for y in range(col[0] + 3, col[-1] - 2)
             if _ratio(px(xx, y), fill) >= 1.6]
    word = [y for xx in range(cx1 + 1, x1) for y in range(h) if _ratio(px(xx, y), bg) >= 1.6]
    rows = lambda v: max(v) - min(v) + 1  # noqa: E731
    return (cap, rows([y for _x, y in inner]), rows([xx for xx, _y in inner]),
            rows(word))


@pytest.mark.parametrize("theme", _FIVE, ids=lambda t: "-".join(t))
@pytest.mark.parametrize("desk", [False, True], ids=["handheld", "desk"])
def test_a_keycaps_legend_is_no_size_under_its_word(desk, theme):
    """A cap holds its word's capitals with 3 px over and under, its legend's
    capitals at most a pixel shorter than the word's (the desk's caption
    face, three pixels under the rows', drew Esc a size smaller than Close),
    and an arrow sign at least half the cap across."""
    cap, legend, _w, word = _hint_shape(desk, theme, ["ESC", "ESC"])
    assert legend >= word - 1, (legend, word)
    assert cap >= word + 6, (cap, word)
    cap, _h, arrow, _word = _hint_shape(desk, theme, ["left", "ESC"])
    assert 2 * arrow >= cap - 4, (arrow, cap)


def _hint_band(theme, kind, items):
    """The width of one hint pair drawn under input `kind`, and its pixels."""
    th = _surface_theme(False, *theme)
    cv = make_system_canvas(400, 40)
    cv.rect(0, 0, 400, 40, th["panel"])
    was = ui_draw.input_kind()
    ui_draw.input_kind(kind)
    try:
        (x0, x1), = ui_draw.hints(cv, th, (0, 0, 400, 40), items)
    finally:
        ui_draw.input_kind(was)
    return x1 - x0, bytes(cv._buf)


@pytest.mark.parametrize("theme", _FIVE, ids=lambda t: "-".join(t))
def test_the_change_legend_draws_each_inputs_own_sign(theme):
    """`change` is a keyboard's two arrow keys, the T-Deck's A and D beside
    its trackball and a gamepad's d-pad: one square cap for the d-pad (as
    wide as one arrow's), each its own sign (the T-Deck and a pad drew a
    keyboard's arrows)."""
    keys, kpx = _hint_band(theme, "keys", ("change", "Change"))
    one, _px = _hint_band(theme, "keys", ("left", "Change"))
    tdeck, tpx = _hint_band(theme, "tdeck", ("change", "Change"))
    pad, ppx = _hint_band(theme, "pad", ("change", "Change"))
    assert keys > one, "a keyboard's two arrow caps"
    assert tdeck > keys, "the T-Deck's three: A, D and the trackball's"
    assert pad == one, "one cap: the d-pad's"
    assert len({kpx, tpx, ppx}) == 3, "each input draws its own"


def test_hints_take_the_pages_foot_and_rows_stop_above_them():
    def draw():
        ui.page("")
        ui.hints(["enter", "Open", "Esc", "Back"])
        ui.group("G")
        for i in range(12):
            ui.info("Row %d" % i, "x")
        ui.group()
    r = _Run(draw)
    r.frame()
    r.frame()
    ground = r.px(2, 236)
    assert r.px(300, 236) == ground, "no row draws in the hints' band"
    assert r.px(300, 215) != ground, "the rows reach down to it"
    assert any(r.px(x, 230) != ground for x in range(8, 40)), "a keycap is drawn"


# -- one parts page, three runtimes ----------------------------------------------------


def _wasm_or_skip():
    from runtime import wasm_binding, wasm_host
    if wasm_host.available():
        return True
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
        pytest.fail("the host wasm binding did not build: %s"
                    % (wasm_binding.why_unavailable() or "unknown"))
    return False


def _pixels(ws):
    ns = ws.player.ns
    cv = ns["screen"]()
    x, y, w, h = ns["content_rect"]()
    buf = cv._buf
    out = bytearray()
    for j in range(y, y + h):
        out += buf[2 * (j * cv.w + x):2 * (j * cv.w + x + w)]
    return bytes(out)


def _pmem(ws):
    lua = getattr(ws.player, "_lua", None)
    if lua is not None and hasattr(lua._run, "pmem"):
        return list(lua._run.pmem()[1])[:N_PMEM]
    if lua is not None:
        import sys
        img = array("i", bytearray(4 * 256))
        sys.modules[type(lua).__module__]._moycore.pmem_image(img)
        return list(img)[:N_PMEM]
    pm = ws.player.ns["pmem"]
    return [pm(i) for i in range(N_PMEM)]


def _frame(drv):
    quiesce(drv.ws)
    drv.ws._dirty = True
    drv.frame(_DT)


def _script(ws):
    """Settle; open the link; step the cycle; move to the seg and step it;
    then tap the swatch picker's last dot; move down to the field, type in
    it and press Enter; move down to the stepped slider and step it."""
    drv = make_drv(ws)
    shots = []
    for _ in range(3):
        _frame(drv)
    shots.append((_pixels(ws), _pmem(ws)))
    for key in ("a", "down", "right", "right", "down", "right", "down", "left"):
        drv.press(key)
        _frame(drv)
        _frame(drv)
        shots.append((_pixels(ws), _pmem(ws)))
    top = shots[-1][1][P_TOP]
    yy = top + CAPTION + 4 * ROW + GAP + CAPTION + ROW + 10
    drv.touch(320 - INSET - PAD - 6, yy)
    _frame(drv)
    drv.touch_up()
    _frame(drv)
    _frame(drv)
    shots.append((_pixels(ws), _pmem(ws)))
    for step in ("down", ord("x"), 13, "down", "left"):
        if isinstance(step, int):
            drv.type_char(step)
        else:
            drv.press(step)
        _frame(drv)
        _frame(drv)
        shots.append((_pixels(ws), _pmem(ws)))
    return shots


@pytest.fixture(scope="module")
def parts_runs(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("ui_parts") / "carts")
    host_app.moy_carts.ensure_dirs(root)
    for d in ("parts_py.moy", "parts_lua.moy"):
        shutil.copytree(os.path.join(PARTS, d), os.path.join(root, d))
    titles = {"python": "UI Parts Py", "lua": "UI Parts Lua"}
    if _wasm_or_skip():
        from tools import wasm_cart
        wasm_cart.build(WASM, os.path.join(root, "app_parts.moy"))
        titles["wasm"] = "App Parts Wasm"
    out = {}
    for name, title in titles.items():
        ws = host_app.build_workstation(root)
        ws.pointer.visible = False
        open_cart(ws, title)
        make_drv(ws).frame(_DT)
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        out[name] = _script(ws)
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        ws.exit()
    return out


def test_the_parts_page_is_one_page_in_every_runtime(parts_runs):
    ref = parts_runs["python"]
    for name, got in parts_runs.items():
        assert len(got) == len(ref)
        for i, ((px, pm), (rpx, rpm)) in enumerate(zip(got, ref)):
            assert (pm[:P_FAIL] == rpm[:P_FAIL]
                    and pm[P_FAIL + 1:] == rpm[P_FAIL + 1:]), \
                "%s step %d answered %r, python %r" % (name, i, pm, rpm)
            assert pm[P_FAIL] == 0, "%s: a row failed (%d)" % (name, pm[P_FAIL])
            assert px == rpx, "%s step %d drew other pixels than python" % (name, i)


def test_the_parts_page_answers_its_script(parts_runs):
    s = [p for _px, p in parts_runs["python"]]
    assert s[0][P_FOCUS] == len("Wi-Fi") and s[0][P_SIZE] == 1
    assert s[1][P_OPENS] == 1, "a opened the link"
    assert s[3][P_SIZE] == 2 and s[4][P_SIZE] == 0, "the cycle wrapped"
    assert s[5][P_FOCUS] == len("Mode"), "down skipped the info row"
    assert s[6][P_MODE] == 1
    assert s[7][P_FOCUS] == len("Accent")
    assert s[8][P_ACCENT] == 0, "left at the first dot stays"
    assert s[9][P_ACCENT] == 2, "the tap picked the last dot"
    assert s[10][P_FOCUS] == len("Name"), "down from the swatches is the field"
    assert s[11][P_NAME] == len("picx"), "the field took the typed key"
    assert s[12][P_DONES] == 1, "Enter in the field"
    assert s[13][P_FOCUS] == len("Level") and s[14][P_LEVEL] == 2, "left steps the level"


# -- an icon on a row, and a progress bar (docs/look_2026-10.md step 19) --------------

def _row_page(before):
    """One group of one link, `before()` called just ahead of it."""
    st = {}

    def draw():
        st["rect"] = ui.page("")
        ui.group("General")
        st["named"] = before()
        st["opened"] = ui.link("Row", "v")
        ui.link("Next", "w")
        ui.group()
        return st
    return _Run(draw)


def _ink_cols(r, y0, y1, x0, x1):
    """The columns from x0 to x1 that hold a pixel other than the card's."""
    card = r.px(x1 + 1, y0)
    return [x for x in range(x0, x1) if any(r.px(x, y) != card for y in range(y0, y1))]


def test_an_icon_draws_before_the_label_and_moves_it_right():
    plain = _row_page(lambda: False)
    plain.frame()
    plain.frame()
    iconed = _row_page(lambda: ui.icon("wifi"))
    iconed.frame()
    iconed.frame()
    assert iconed.out["named"] is True
    top = iconed.out["rect"][1] + CAPTION            # the first row, the icon's
    x0 = INSET + PAD
    a = _ink_cols(plain, top + 2, top + ROW - 2, x0, 160)
    b = _ink_cols(iconed, top + 2, top + ROW - 2, x0, 160)
    assert a and b and b[0] < a[0] + 8, "the icon starts where the label did"
    assert b[-1] > a[-1] + 12, "the label moved right past the icon"


def test_an_icon_applies_to_the_next_row_only():
    st = {}

    def draw():
        st["rect"] = ui.page("")
        ui.group("General")
        ui.icon("lock")
        ui.link("Row", "v")
        ui.link("Next", "w")
        ui.group()
        return st
    r = _Run(draw)
    r.frame()
    r.frame()
    plain = _row_page(lambda: False)
    plain.frame()
    plain.frame()
    top = r.out["rect"][1] + CAPTION + ROW          # the second row, "Next"
    y0, y1 = top + 2, top + ROW - 2
    assert _ink_cols(r, y0, y1, INSET + PAD, 160) == _ink_cols(plain, y0, y1, INSET + PAD, 160)


def test_an_icon_no_set_or_glyph_names_draws_nothing():
    plain = _row_page(lambda: False)
    plain.frame()
    plain.frame()
    none = _row_page(lambda: ui.icon("no such icon"))
    none.frame()
    none.frame()
    assert none.out["named"] is False
    assert bytes(none.cv._buf) == bytes(plain.cv._buf)


def test_the_icon_slots_are_the_sets():
    """The C names the icon set's slots in tools/make_icons.py's order: the
    table in native/moy_ui/moy_ui.c is runtime/iconsets.py's SLOTS."""
    from runtime import iconsets
    src = open(os.path.join(ROOT, "native", "moy_ui", "moy_ui.c")).read()
    body = src[src.index("s_icon_slots[] = {"):]
    body = body[:body.index("};")]
    import re
    assert tuple(re.findall(r'"([a-z0-9_]+)"', body)) == iconsets.SLOTS


def _progress_page(v, hi):
    st = {}

    def draw():
        st["rect"] = ui.page("")
        ui.group("Update")
        ui.progress("Done", v, hi)
        st["after"] = ui.link("After", "")
        ui.group()
        return st
    return _Run(draw)


def test_progress_is_no_stop_and_fills_its_bar_with_the_value():
    half = _progress_page(5, 10)
    half.frame()
    half.frame()
    assert ui.focus() == "After", "the progress row is no stop"
    full = _progress_page(10, 10)
    full.frame()
    full.frame()
    none = _progress_page(0, 10)
    none.frame()
    none.frame()
    y = half.out["rect"][1] + CAPTION + ROW // 2
    right = 320 - INSET - PAD
    a = _ink_cols(none, y - 1, y + 2, 200, right)
    b = _ink_cols(half, y - 1, y + 2, 200, right)
    assert bytes(half.cv._buf) != bytes(none.cv._buf) != bytes(full.cv._buf)
    assert a == b or len(b) >= len(a), "the track is drawn either way"


def test_progress_clamps_its_value():
    over = _progress_page(15, 10)
    over.frame()
    over.frame()
    full = _progress_page(10, 10)
    full.frame()
    full.frame()
    assert bytes(over.cv._buf) == bytes(full.cv._buf)
    under = _progress_page(-3, 10)
    under.frame()
    under.frame()
    zero = _progress_page(0, 10)
    zero.frame()
    zero.frame()
    assert bytes(under.cv._buf) == bytes(zero.cv._buf)


# -- a list in columns, a scroll region, a field, a dialog and an icon on a
# button (docs/look_2026-10.md step 21) ------------------------------------------------

HEAD = 18                            # a table's header row (`value_row`'s `head`)


def _box(r, id):
    return ui_draw._C.api_box(r.ctx, id)


def _table_page(rows, at=None, cols=("", "Name", "Kind"), after=True):
    st = {"cur": 0, "opened": False}

    def draw():
        st["rect"] = ui.page("")
        if at is not None:
            ui.at(*at)
        st["cur"], st["opened"] = ui.table("Art", cols, rows, st["cur"])
        if after:
            st["after"] = ui.link("After", "")
        return st
    return _Run(draw)


def _rows(n):
    return [("paint", "file %d" % i, "Drawing") for i in range(n)]


def _row_mid(r, i, id="Art"):
    x, y, w, _h = _box(r, id)
    return x + w // 2, y + CAPTION + HEAD + i * ROW + ROW // 2


def test_a_table_moves_its_current_row_with_focus_and_enter_opens_it():
    r = _table_page(_rows(3))
    r.frame()
    r.frame()
    assert ui.focus() == "Art"
    assert r.frame(("down",))["cur"] == 1
    st = r.frame(("a",))
    assert st["cur"] == 1 and st["opened"] is True
    assert r.frame()["opened"] is False, "opened is the frame's answer"
    r.frame(("up",))
    r.frame(("up",))
    assert r.out["cur"] == 0 and ui.focus() == "Art", "up at the first row stays"
    r.frame(("down",))
    r.frame(("down",))
    r.frame(("down",))
    assert r.out["cur"] == 2 and ui.focus() == "After", "down past the last row leaves"


def test_a_tap_makes_a_row_current_and_a_second_tap_opens_it():
    r = _table_page(_rows(3))
    r.frame()
    r.frame()
    st = r.tap(*_row_mid(r, 2))
    assert st["cur"] == 2 and st["opened"] is False
    st = r.tap(*_row_mid(r, 2))
    assert st["cur"] == 2 and st["opened"] is True


def test_a_placed_table_scrolls_in_its_rect_to_keep_the_current_row():
    top = 40
    r = _table_page(_rows(10), at=(0, top, 320, CAPTION + HEAD + 3 * ROW))
    r.frame()
    r.frame()
    for _ in range(5):
        r.frame(("down",))
    assert r.out["cur"] == 5
    r.frame()
    st = r.tap(*_row_mid(r, 0))
    assert st["cur"] == 3, "rows 3 to 5 are in view, the current one at the foot"


def test_a_tables_picture_column_draws_the_named_icon():
    a = _table_page([("paint", "x", "Drawing")])
    b = _table_page([("no such icon", "x", "Drawing")])
    for r in (a, b):
        r.frame()
        r.frame()
    _x, y, _w, _h = _box(a, "Art")
    y0 = y + CAPTION + HEAD
    pic = _ink_cols(a, y0 + 2, y0 + ROW - 2, INSET, 40)
    assert pic and _ink_cols(b, y0 + 2, y0 + ROW - 2, INSET, 40) != pic


def _band(r, y0, y1):
    return [r.px(x, y) for y in range(y0, y1) for x in range(r.cv.w)]


def test_a_tables_rows_fade_where_they_run_on_past_its_rect():
    """A placed table whose rows run on below its rect fades its foot, as a
    page's end does (design review R4-10); one whose rows all show draws
    none, and its top rows are the same either way."""
    top = 40
    at = (0, top, 320, CAPTION + HEAD + 3 * ROW)
    fits = _table_page(_rows(3), at=at, after=False)
    runs = _table_page(_rows(9), at=at, after=False)
    for r in (fits, runs):
        r.frame()
        r.frame()
    foot = top + CAPTION + HEAD + 3 * ROW
    head = top + CAPTION + HEAD
    assert _band(fits, foot - 8, foot) != _band(runs, foot - 8, foot), "no fade at its foot"
    assert _band(fits, head, head + ROW) == _band(runs, head, head + ROW)


def test_a_scrolled_table_fades_its_head():
    top = 40
    at = (0, top, 320, CAPTION + HEAD + 3 * ROW)
    still = _table_page(_rows(9), at=at, after=False)
    moved = _table_page(_rows(9), at=at, after=False)
    for r in (still, moved):
        r.frame()
        r.frame()
    for _ in range(4):
        moved.frame(("down",))
    moved.frame()
    head = top + CAPTION + HEAD
    # the current row at the foot scrolls row 2 to the head: the fade over
    # it differs from row 2 drawn unfaded two rows lower
    assert _band(moved, head, head + 10) != _band(still, head + 2 * ROW, head + 2 * ROW + 10)


def test_a_table_in_a_narrow_rect_keeps_its_last_column_inside():
    """The columns after the first text column keep their widest cell's
    width, right-aligned to the rect's inset; the first takes what is left
    and cuts a long name, so no cell draws past the rect."""
    long = [("paint", "a name far longer than the room it has", "Wallpaper")] * 3
    x0, w = 40, 200
    r = _table_page(long, at=(x0, 40, w, CAPTION + HEAD + 3 * ROW))
    r.frame()
    r.frame()
    y0 = 40 + CAPTION + HEAD + ROW             # a row that is not the current one
    ink = _ink_cols(r, y0 + 2, y0 + ROW - 2, x0, x0 + w)
    assert ink and ink[-1] < x0 + w - INSET, "the Kind cell ends inside the rect"
    assert _ink_cols(r, y0 + 2, y0 + ROW - 2, x0 + w, 318) == [], "nothing past it"
    # the caption face, else the ui role's (the engine's font_of)
    face = ui_draw.font("caption")[0] or ui_draw.font("ui")[0]
    kind = ui_draw._C.text_width("Wallpaper", 1, face)
    gaps = [(b - a, b) for a, b in zip(ink, ink[1:])]
    widest, kind_x = max(gaps)
    assert widest > 4 and ink[-1] - kind_x < kind, "the name is cut short of the Kind"


def _key_page(label, kind="normal", app=None, theme=("moy", "dark")):
    def draw():
        ui.page("")
        ui.at(10, 40, 60, 40)
        return ui.button(label, kind)
    return _Run(draw, theme, app)


def _ink_rows(r, x0, x1, y0, y1):
    """The rows of a button's label: those holding a pixel other than its
    field's, inside its corners and above its focus mark."""
    field = r.px(x0 + 6, (y0 + y1) // 2)
    return [y for y in range(y0 + 4, y1 - 6)
            if any(r.px(x, y) != field for x in range(x0 + 8, x1 - 8))]


def test_a_buttons_font_leaf_draws_its_label_in_the_key_face():
    """Moy's Night names `key` for Calc's normal keys and `op` for its
    operators (app_buttons.calc): a shell app's host passes its id, and its
    labels draw in those faces; any other page keeps the ui face, as does a
    label the face does not cover."""
    pages = {}
    for name, label, kind, app, theme in (
            ("ui", "7", "normal", None, ("moy", "dark")),
            ("key", "7", "normal", "calc", ("moy", "dark")),
            ("op", "7", "tonal", "calc", ("moy", "dark")),
            ("arrow", "\u2190", "normal", "calc", ("moy", "dark")),
            ("arrow_ui", "\u2190", "normal", None, ("moy", "dark"))):
        r = pages[name] = _key_page(label, kind, app, theme)
        r.frame()
        r.frame()
    tall = {n: len(_ink_rows(pages[n], 10, 70, 40, 80)) for n in ("ui", "key", "op")}
    assert tall["ui"] < tall["key"] <= tall["op"], tall
    assert bytes(pages["key"].cv._buf) != bytes(pages["op"].cv._buf)
    assert bytes(pages["arrow"].cv._buf) == bytes(pages["arrow_ui"].cv._buf)


def _field_page(text, mx=8):
    st = {"text": text, "done": False}

    def draw():
        st["rect"] = ui.page("")
        st["text"], st["done"] = ui.field("Name", st["text"], mx)
        ui.link("After", "")
        return st
    return _Run(draw)


def test_a_focused_field_types_each_key_once_and_backspace_takes_a_character():
    r = _field_page("ab")
    r.frame()
    r.frame()
    assert ui.focus() == "Name"
    assert r.frame(typed="c")["text"] == "abc"
    assert r.frame(typed="c")["text"] == "abc", "a held key types once"
    r.frame()
    assert r.frame(typed="a")["text"] == "abca", "`a` is a letter in a field"
    assert r.frame()["done"] is False
    r.frame(typed=8)
    assert r.out["text"] == "abc"
    r.frame()
    r.frame(typed=8)
    assert r.out["text"] == "ab"


def test_a_field_backspaces_a_whole_utf8_character_and_keeps_to_its_max():
    r = _field_page("xé", mx=4)
    r.frame()
    r.frame()
    assert r.frame(typed=8)["text"] == "x"
    r.frame()
    for ch in "yzwv":
        r.frame(typed=ch)
        r.frame()
    assert r.out["text"] == "xyzw", "at most `max` bytes"


def test_enter_in_a_field_is_its_done_and_the_a_button_is_not():
    r = _field_page("ab")
    r.frame()
    r.frame()
    assert r.frame(("a",))["done"] is False
    assert r.frame(typed=13)["done"] is True
    r.frame()
    assert r.frame(("run",))["done"] is True


def test_an_unfocused_field_takes_no_keys():
    r = _field_page("ab")
    r.frame()
    r.frame(("down",))
    assert ui.focus() == "After"
    assert r.frame(typed="c")["text"] == "ab"


TRACK = 84      # style.json's value_row `track` at fs 1: a slider's, and a grouped field's


def test_a_field_in_a_group_lays_out_like_the_other_rows():
    """On a group's card a field's label is where a link's is, and its box
    is the row's value: a slider's track wide at the row's right, the room
    between the two the card's."""
    st = {}

    def draw():
        st["rect"] = ui.page("")
        ui.group("You")
        ui.link("Top", "")                      # holds focus, away from both rows
        ui.link("Name", "v", {"id": "link"})
        st["text"], _d = ui.field("Name", "Kit", 16)
        ui.link("Foot", "")                     # the card's rounded foot, below both
        ui.group()
        return st
    r = _Run(draw)
    r.frame()
    r.frame()
    top = r.out["rect"][1] + CAPTION
    link_y, field_y = top + ROW, top + 2 * ROW
    right = 320 - INSET - PAD
    label = _ink_cols(r, link_y + 2, link_y + ROW - 2, INSET + 2, 60)
    assert label and label[0] >= INSET + PAD - 1, "the link's label sits at the group's pad"
    assert _ink_cols(r, field_y + 2, field_y + ROW - 2, INSET + 2, 60) == label, \
        "the field's label is drawn where the link's is"
    card = r.px(right - TRACK - 20, field_y + ROW // 2)
    mid = field_y + ROW // 2
    box = [x for x in range(INSET + PAD + 40, 320 - INSET) if r.px(x, mid) != card]
    assert box and box[0] == right - TRACK, "the box starts a track's width from the right"
    assert box[-1] < right + 1, "and ends at the row's pad"
    assert all(r.px(x, mid) == card for x in range(label[-1] + 2, right - TRACK)), \
        "the card shows between the label and the box"


def _row_or_field_page(theme, field, focus=True, last=False):
    """A group of Top, then Name -- a field, else a link -- then Foot (none
    where `last`, Name then the card's last row), focus on Name (on Top
    where not `focus`)."""
    def draw():
        st = {"rect": ui.page("")}
        ui.group("You")
        ui.link("Top", "")
        if field:
            ui.field("Name", "", 16)
        else:
            ui.link("Name", "")
        if not last:
            ui.link("Foot", "")
        ui.group()
        ui.focus("Name" if focus else "Top")
        return st
    r = _Run(draw, theme)
    for _ in range(3):
        r.frame()
    return r


@pytest.mark.parametrize("theme", _FIVE, ids=lambda t: "-".join(t))
@pytest.mark.parametrize("last", [False, True], ids=["mid", "last"])
def test_a_focused_field_row_wears_the_value_rows_focus_mark(theme, last):
    """A focused field's row wears the focus mark a focused link's row does,
    over the whole row (a card's last row included): down the row's left
    margin, outside its label and box, the mark runs the row's height less
    what the link's leaves at its ends, and across it as wide. Before, the
    mark showed only where the card's foot redrew it: a sliver in Moy, two
    squares under Blocks, specks in Contrast."""
    def marked(focused, field):
        on = _row_or_field_page(theme, field, last=last)
        off = _row_or_field_page(theme, field, focus=False, last=last)
        _x, y0, _w, h = _box(on, "Name")
        rows = [y - y0 for y in range(y0, y0 + h) if on.px(INSET + 3, y) != off.px(INSET + 3, y)]
        mid = y0 + h // 2
        cols = [x for x in range(INSET, INSET + PAD - 2) if on.px(x, mid) != off.px(x, mid)]
        return h, rows, cols
    fh, frows, fcols = marked(True, True)
    lh, lrows, lcols = marked(True, False)
    assert lrows and lcols, "the link's row is marked"
    assert fcols == lcols, "the field's mark is as wide as the link's"
    assert len(frows) == fh - (lh - len(lrows)), "the field's mark runs its row's height"
    assert frows == list(range(frows[0], frows[0] + len(frows))), "in one piece"


@pytest.mark.parametrize("theme", _FIVE, ids=lambda t: "-".join(t))
def test_a_focused_fields_ring_is_the_focus_colour_and_reads_on_its_row(theme):
    """Focused (being typed in), the field's ring is two pixels where its
    rest ring is one, the second outside the box, in the focus colour (the
    mark's ink on an invert mark), and clears what lies round it 3:1 --
    Contrast's stayed its one pixel of border colour, so only the caret
    told it was being typed in."""
    on = _row_or_field_page(theme, True)
    off = _row_or_field_page(theme, True, focus=False)
    _x, y0, _w, h = _box(on, "Name")
    y = y0 + h // 2
    x = 320 - INSET - PAD - TRACK
    ring = on.px(x, y)
    assert on.px(x - 1, y) == ring and off.px(x - 1, y) != off.px(x, y), "two pixels, not one"
    assert _ratio(ring, on.px(x - 2, y)) >= 3.0, "%.2f:1" % _ratio(ring, on.px(x - 2, y))


@pytest.mark.parametrize("theme", _FIVE, ids=lambda t: "-".join(t))
@pytest.mark.parametrize("desk", [False, True], ids=["handheld", "desk"])
def test_a_fields_text_and_caret_keep_inside_its_ring(theme, desk):
    """In a group the field fits its row, as tall as its siblings (design
    review R4-29): its rest ring a pixel clear of the rules over and under
    it, and its letters' ink as far inside the ring as that leaves -- 2
    pixels on a handheld's 20 pixel row, the full 3 on the desk's -- so a
    descender stays off the ring and the caret inside the box. A box of
    the row less 3 pixels a side once put a handheld's descender on the
    ring and the caret over it."""
    def render(text):
        def draw():
            ui.page("")
            ui.group("You")
            ui.field("Name", text, 16)
            ui.group()
            ui.focus("Name")
        cv = make_system_canvas(640 if desk else 320, 240)
        ctx = ui_draw.ui_context()
        for _ in range(3):
            ui_draw.ui_frame(ctx, cv, _theme(*theme), None, array("i", [0] * ui_draw.UI_IN_LEN), desk=desk)
            draw()
        b = cv._buf
        return (lambda x, y: b[2 * (y * cv.w + x)] << 8 | b[2 * (y * cv.w + x) + 1],
                ui_draw._C.api_box(ctx, "Name"))
    full, (x0, y0, w, h) = render("Mgyj")
    empty, _b = render("")
    cx, mid = x0 + w - 30, y0 + h // 2          # inside the box, right of the text
    ground = empty(cx, mid)
    inner0 = mid
    while empty(cx, inner0 - 1) == ground:
        inner0 -= 1
    inner1 = mid
    while empty(cx, inner1 + 1) == ground:
        inner1 += 1
    diff = [(x, y) for x in range(x0, cx) for y in range(y0, y0 + h) if full(x, y) != empty(x, y)]
    caret = max(x for x, _y in diff)
    was = {x for x in range(x0 + w // 2, cx) if empty(x, mid) == full(caret, mid) != ground}
    ink = sorted({y for x, y in diff if x < caret - 1 and x not in was})   # the empty one's caret
    # the face's ink centred: on the handheld a smooth face's antialiased
    # foot row may take one of the 2 pixels under its descender
    pad = 3 if desk else 2
    assert ink[0] - inner0 >= pad, "the letters %d px under the ring" % (ink[0] - inner0)
    assert inner1 - ink[-1] >= pad - (0 if desk else 1), \
        "the descender %d px over the ring" % (inner1 - ink[-1])
    assert h == ui_draw.metrics("row")[6 if desk else 5], "the row is its siblings' height"
    rows = [y for x, y in diff if x == caret]
    assert inner0 <= min(rows) and max(rows) <= inner1, "the caret pokes out of the box"


def _dialog_page(shown, body_field=False):
    st = {"pick": None, "under": 0, "text": "n"}

    def draw():
        st["rect"] = ui.page("")
        if ui.link("Under", ""):
            st["under"] += 1
        if shown[0]:
            st["pick"] = ui.dialog("Delete?", ("Cancel", "Delete"), "danger")
            if body_field:
                st["text"], _done = ui.field("Name", st["text"], 8)
            else:
                ui.label("For good.")
            st["end"] = ui.dialog()
        return st
    return _Run(draw)


def test_a_dialog_opens_focus_on_its_first_button_and_answers_the_one_activated():
    shown = [True]
    r = _dialog_page(shown)
    for _ in range(3):
        r.frame()
    assert ui.focus() == "Cancel", "focus opens on the first, the safe one"
    assert r.out["pick"] == -1 and r.out["end"] == -1
    r.frame(("right",))
    assert ui.focus() == "Delete"
    assert r.frame(("a",))["pick"] == 1


def test_a_dialog_opens_focus_on_its_bodys_first_stop():
    r = _dialog_page([True], body_field=True)
    for _ in range(3):
        r.frame()
    assert ui.focus() == "Name"
    assert r.frame(typed="o")["text"] == "no"


def test_nothing_under_a_dialog_takes_input_and_focus_comes_back_after_it():
    shown = [False]
    r = _dialog_page(shown)
    r.frame()
    r.frame()
    under = _box(r, "Under")
    assert ui.focus() == "Under"
    shown[0] = True
    for _ in range(3):
        r.frame()
    r.tap(under[0] + 4, under[1] + under[3] // 2)
    r.frame(("a",))
    assert r.out["under"] == 0, "the link under the dialog took input"
    shown[0] = False
    r.frame()
    r.frame()
    assert ui.focus() == "Under", "focus went back where the dialog took it from"
    r.frame(("a",))
    assert r.out["under"] == 1


def test_a_dialog_draws_a_card_over_the_page():
    plain = _dialog_page([False])
    card = _dialog_page([True])
    for r in (plain, card):
        for _ in range(3):
            r.frame()
    assert card.px(160, 120) != plain.px(160, 120)
    # the card is centred, the page around it dimmed under the scrim (D-01)
    corner, under = _rgb(card.px(2, 236)), _rgb(plain.px(2, 236))
    assert card.px(2, 236) != card.px(160, 120), "the card is centred, not the whole page"
    assert sum(corner) < sum(under), "the page under a dialog dims"


def test_a_scroll_region_keeps_the_focused_row_in_its_rect():
    top, h = 60, 3 * ROW
    st = {}

    def draw():
        st["rect"] = ui.page("")
        ui.at(0, top, 320, h)
        ui.scroll("list")
        for i in range(6):
            ui.link("Row %d" % i, "")
        ui.scroll()
        ui.at(0, 200, 320, ROW)
        ui.link("Below", "")
        return st
    r = _Run(draw)
    r.frame()
    r.frame()
    assert _box(r, "Row 0")[1] == top
    for _ in range(4):
        r.frame(("down",))
    r.frame()
    assert ui.focus() == "Row 4"
    y = _box(r, "Row 4")[1]
    assert top <= y and y + ROW <= top + h, "the region scrolled to show it"
    r.frame(("down",))
    r.frame(("down",))
    assert ui.focus() == "Below", "the rows after the region stack after it"


def _button_page(icon, label="Delete"):
    st = {}

    def draw():
        st["rect"] = ui.page("")
        ui.at(10, 40, 120, 24)
        st["named"] = ui.icon(icon) if icon else None
        st["hit"] = ui.button(label, "danger")
        return st
    return _Run(draw)


def test_an_icon_puts_its_glyph_on_the_next_button():
    plain = _button_page(None)
    iconed = _button_page("lock")             # the glyph table's: every canvas has it
    bare = _button_page(None, "")
    alone = _button_page("lock", "")
    for r in (plain, iconed, bare, alone):
        r.frame()
        r.frame()
    assert iconed.out["named"] is True
    a = _ink_cols(plain, 46, 58, 16, 120)
    b = _ink_cols(iconed, 46, 58, 16, 120)
    assert b[0] < a[0] and b[-1] > a[-1] - 2, "the icon leads the label"
    assert bytes(alone.cv._buf) != bytes(bare.cv._buf), "an icon alone is the button's face"


# -- the Files verbs' page in every runtime ---------------------------------------------

WASM_FILES = os.path.join(ROOT, "tests", "fixtures", "wasm", "app_files.moy")
F_FRAMES, F_CUR, F_OPENED, F_LEN, F_DONES, F_FOCUS, F_FAIL, F_PICKS = range(8)
# The script: each step a press (a button's name) or a typed key (an int).
FILES_SCRIPT = ("down", "a", "down", "down", "down", "down", "down", "down", "down",
                ord("x"), 8, 13, "down", "a", "right", "a")


def _files_script(ws):
    drv = make_drv(ws)
    shots = []
    for _ in range(3):
        _frame(drv)
    shots.append((_pixels(ws), _pmem(ws)))
    for step in FILES_SCRIPT:
        if isinstance(step, int):
            drv.type_char(step)
        else:
            drv.press(step)
        _frame(drv)
        _frame(drv)
        _frame(drv)
        shots.append((_pixels(ws), _pmem(ws)))
    return shots


@pytest.fixture(scope="module", params=MOY_VARIANTS)
def files_runs(request, tmp_path_factory):
    root = str(tmp_path_factory.mktemp("ui_files") / "carts")
    host_app.moy_carts.ensure_dirs(root)
    for d in ("files_py.moy", "files_lua.moy"):
        shutil.copytree(os.path.join(PARTS, d), os.path.join(root, d))
    titles = {"python": "UI Files Py", "lua": "UI Files Lua"}
    if _wasm_or_skip():
        from tools import wasm_cart
        wasm_cart.build(WASM_FILES, os.path.join(root, "app_files.moy"))
        titles["wasm"] = "App Files Wasm"
    out = {}
    for name, title in titles.items():
        ws = host_app.build_workstation(root)
        ws.look.set_theme("moy", persist=False, variant=request.param)
        ws.pointer.visible = False
        open_cart(ws, title)
        make_drv(ws).frame(_DT)
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        out[name] = _files_script(ws)
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        ws.exit()
    return out


def test_the_files_page_is_one_page_in_every_runtime(files_runs):
    ref = files_runs["python"]
    for name, got in files_runs.items():
        assert len(got) == len(ref)
        for i, ((px, pm), (rpx, rpm)) in enumerate(zip(got, ref)):
            assert pm[:F_FAIL] == rpm[:F_FAIL], "%s step %d answered %r, python %r" % (
                name, i, pm, rpm)
            assert pm[F_FAIL] == 0, "%s: a row failed (%d)" % (name, pm[F_FAIL])
            assert pm[F_PICKS] == rpm[F_PICKS], (name, i)
            assert px == rpx, "%s step %d drew other pixels than python" % (name, i)


def test_the_files_page_answers_its_script(files_runs):
    s = [p for _px, p in files_runs["python"]]
    assert s[0][F_FOCUS] == len("Art") and s[0][F_CUR] == 0
    assert s[1][F_CUR] == 1
    assert s[2][F_OPENED] == 1, "Enter opened the current row"
    assert s[5][F_CUR] == 4, "the rows past the table's rect were reached"
    assert s[6][F_FOCUS] == len("One"), "down past the last row left the table"
    assert s[8][F_FOCUS] == len("Three"), "the scroll region's hidden row took focus"
    assert s[9][F_FOCUS] == len("Name")
    assert s[10][F_LEN] == 4 and s[11][F_LEN] == 3, "typed, then backspaced"
    assert s[12][F_DONES] == 1, "Enter in the field"
    assert s[13][F_FOCUS] == len("Delete")
    assert s[14][F_FOCUS] == len("Cancel"), "the dialog took focus to its first button"
    assert s[15][F_FOCUS] == len("Delete")
    assert s[16][F_PICKS] == 2, "the dialog answered its second button"


# -- a page in a window's body ---------------------------------------------------------
#
# A page whose host begins it in a window (`ui_frame(window=True)`; a run's
# snapshot slot SNAP_WINDOW, which the kernel's Player writes from the console)
# lies on the window's body as the frame paints it; a fullscreen page keeps
# its own ground.

def _ground(theme, window):
    """The ground pixel of an empty page begun in a window or not."""
    r = _Run(lambda: ui.page(""), theme)
    ui_draw.ui_frame(r.ctx, r.cv, r.th, None, None, False, window)
    ui.page("")
    return r.px(300, 200)


def _window_body(theme):
    """The colour a window's frame paints its body, at its foot."""
    th = _theme(*theme)
    cv = make_system_canvas(320, 240)
    ui_draw.frame(cv, th, (10, 10, 300, 200), 20, "Window", True, False, 1)
    b = cv._buf
    o = 2 * (208 * cv.w + 160)
    return b[o] << 8 | b[o + 1]


@pytest.mark.parametrize("variant", ["light", "dark"])
def test_a_page_in_a_window_lies_on_the_windows_body(variant):
    theme = ("moy", variant)
    body = _window_body(theme)
    assert _ground(theme, True) == body
    full = _ground(theme, False)
    assert full != body, "a fullscreen page keeps the panel's ground"
    cv = make_system_canvas(8, 8)
    cv.rect(0, 0, 8, 8, _theme(*theme)["panel"])
    assert full == cv._buf[0] << 8 | cv._buf[1], "the fullscreen ground is the panel"


def test_a_theme_without_a_depth_keeps_its_page_in_a_window():
    theme = ("night", "dark")
    assert _ground(theme, True) == _ground(theme, False)


@pytest.fixture(scope="module")
def parts_root(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("ui_window") / "carts")
    host_app.moy_carts.ensure_dirs(root)
    for d in ("parts_py.moy", "parts_lua.moy"):
        shutil.copytree(os.path.join(PARTS, d), os.path.join(root, d))
    titles = {"python": "UI Parts Py", "lua": "UI Parts Lua"}
    if _wasm_or_skip():
        from tools import wasm_cart
        wasm_cart.build(WASM, os.path.join(root, "app_parts.moy"))
        titles["wasm"] = "App Parts Wasm"
    return root, titles


@pytest.mark.parametrize("desk", [False, True])
def test_every_runtime_begins_its_page_in_the_window_it_runs_in(parts_root, monkeypatch,
                                                                  desk):
    """A Python app's frame begins with the window flag; a Lua and a compiled
    app's snapshot carries it to the RUN face's begin (moy_ui_wasm.c)."""
    from runtime import player, wasm_binding
    seen = []
    real = player._ui_frame

    def spy(*a, **kw):
        seen.append(a[6])
        return real(*a, **kw)
    monkeypatch.setattr(player, "_ui_frame", spy)
    root, titles = parts_root
    kw = dict(sys_size=(1024, 600), font_scale=2, windowed=True) if desk else {}
    for name, title in titles.items():
        ws = host_app.build_workstation(root, **kw)
        ws.pointer.visible = False
        open_cart(ws, title)
        for _ in range(2):
            make_drv(ws).frame(_DT)
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        assert ws.windowed_chrome is desk
        if name == "python":
            assert seen and set(seen) == {desk}, seen
        else:
            run = ws.player._lua
            snap = getattr(run, "snap", None)
            if snap is None:
                snap = run._run.snap
            assert snap[wasm_binding.SNAP_WINDOW] == (1 if desk else 0), name
        ws.exit()


# -- the Appearance verbs (step 20): tabs, a grid of cards --------------------------------

def _tabs_page(label="Kind"):
    st = {"cur": 0}

    def draw():
        ui.page("")
        st["cur"] = ui.tabs(label, ("Pictures", "Carts", "Art"), st["cur"])
        ui.link("After", "")
        return st
    return _Run(draw)


def test_tabs_move_by_left_and_right_stop_at_their_ends_and_take_a_tap():
    r = _tabs_page()
    r.frame()
    r.frame()
    assert ui.focus() == "Kind"
    assert r.frame(("right",))["cur"] == 1
    r.frame(("right",))
    assert r.frame(("right",))["cur"] == 2, "the last tab stays"
    x, y, w, h = _box(r, "Kind")
    assert r.tap(x + 12, y + h // 2)["cur"] == 0, "a tap picks the tab under it"
    r.frame(("down",))
    assert ui.focus() == "After"


def test_a_tab_rows_label_is_its_id_and_draws_nothing():
    a, b = _tabs_page("Kind"), _tabs_page("Other")
    for r in (a, b):
        r.frame()
        r.frame()
    assert bytes(a.cv._buf) == bytes(b.cv._buf)


def _grid_page(n=4, w=66, h=50, group=False, at=None, colour=8):
    st = {"act": None, "cur": 0, "boxes": []}

    def draw():
        st["rect"] = ui.page("")
        if at is not None:
            ui.at(*at)
            ui.scroll("region")
        if group:
            ui.group("Theme")
        ui.grid("cards", w, h)
        st["boxes"] = []
        for i in range(n):
            got = ui.card("Card %d" % i, "sub", {"current": i == st["cur"]})
            st["boxes"].append(got)
            if got[2]:
                r.cv.rect(got[0], got[1], got[2], got[3], colour)
            if got[4] & ui.ACTIVATED:
                st["act"] = i
        ui.grid()
        ui.link("After", "")
        if group:
            ui.group()
        if at is not None:
            ui.scroll()
        return st
    r = _Run(draw)
    return r


def test_a_grid_lays_its_cards_in_centred_columns_and_rows():
    r = _grid_page()
    r.frame()
    st = r.frame()
    b = st["boxes"]
    assert [c[2:4] for c in b[1:]] == [(66, 50)] * 3
    assert b[0][2] > 66, "the focused card is lifted: grown, its picture with it"
    assert b[1][1] == b[2][1] and b[3][1] > b[2][1], "three columns, then a row"
    assert b[3][0] == b[1][0] - (b[2][0] - b[1][0]), "the second row starts under the first"
    left, right = b[3][0], 320 - (b[2][0] + b[2][2])
    assert abs(left - right) <= 1, "the columns are centred"
    after = _box(r, "After")
    assert after[1] > b[3][1] + 50, "the rows stack under the grid"


def test_the_arrows_move_between_cards_and_enter_activates_one():
    r = _grid_page()
    r.frame()
    r.frame()
    assert ui.focus() == "Card 0"
    r.frame(("right",))
    assert ui.focus() == "Card 1"
    r.frame(("left",))
    r.frame(("down",))
    assert ui.focus() == "Card 3"
    st = r.frame(("a",))
    assert st["act"] == 3
    assert r.frame()["act"] == 3 and r.out["boxes"][3][4] & ui.ACTIVATED == 0


def test_a_tap_activates_the_card_under_it():
    r = _grid_page()
    r.frame()
    r.frame()
    x, y, w, h, _st = r.out["boxes"][2]
    r.out["act"] = None
    assert r.tap(x + w // 2, y + h // 2)["act"] == 2


def test_a_cards_picture_is_cut_to_the_radius_and_ringed_after_the_app_draws():
    r = _grid_page(colour=8)
    r.frame()
    r.frame()
    x, y, w, h, _st = r.out["boxes"][1]
    inside = r.px(x + w // 2, y + h // 2)
    assert r.px(x, y) != inside, "the corner is cut over the ground"
    assert r.px(x + w // 2, y) != inside, "the ring is drawn over the picture's edge"
    assert r.px(x + w // 2, y + 3) == inside


def test_the_current_card_wears_the_current_mark():
    r = _grid_page()
    r.frame()
    r.frame()
    a, b = r.out["boxes"][0], r.out["boxes"][1]
    pad = 5                                     # style.json's card_pad
    assert r.px(a[0] - pad + 2, a[1] + 20) != r.px(b[0] - pad + 2, b[1] + 20)


def test_a_grid_in_a_group_lies_on_the_ground_between_its_caption_and_rows():
    r = _grid_page(group=True)
    r.frame()
    r.frame()
    b = r.out["boxes"]
    ground = r.px(2, b[0][1] + 10)
    assert r.px(INSET + 1, b[0][1] + 10) == ground, "no card under the grid"
    after = _box(r, "After")
    assert r.px(INSET + 2, after[1] + after[3] // 2) != ground, "the rows sit on a card"


def test_a_card_scrolled_out_of_view_answers_no_picture():
    r = _grid_page(n=6, at=(0, 40, 320, 60))
    r.frame()
    st = r.frame()
    assert st["boxes"][1][2] == 66
    assert st["boxes"][3][2] == 0 and st["boxes"][3][3] == 0, "the second row is out of view"


# -- the Appearance verbs' page in every runtime -------------------------------------------

WASM_LOOK = os.path.join(ROOT, "tests", "fixtures", "wasm", "app_look.moy")
L_FRAMES, L_TAB, L_PICKED, L_ACTS, _L4, L_FOCUS, L_FAIL, L_LEFT = range(8)
LOOK_SCRIPT = ("right", "down", "right", "a", "down", "down", "up", "left")


@pytest.fixture(scope="module")
def look_runs(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("ui_look") / "carts")
    host_app.moy_carts.ensure_dirs(root)
    for d in ("look_py.moy", "look_lua.moy"):
        shutil.copytree(os.path.join(PARTS, d), os.path.join(root, d))
    titles = {"python": "UI Look Py", "lua": "UI Look Lua"}
    if _wasm_or_skip():
        from tools import wasm_cart
        wasm_cart.build(WASM_LOOK, os.path.join(root, "app_look.moy"))
        titles["wasm"] = "App Look Wasm"
    out = {}
    for name, title in titles.items():
        ws = host_app.build_workstation(root)
        ws.pointer.visible = False
        open_cart(ws, title)
        make_drv(ws).frame(_DT)
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        drv = make_drv(ws)
        shots = []
        for _ in range(3):
            _frame(drv)
        shots.append((_pixels(ws), _pmem(ws)))
        for step in LOOK_SCRIPT:
            drv.press(step)
            for _ in range(3):
                _frame(drv)
            shots.append((_pixels(ws), _pmem(ws)))
        out[name] = shots
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        ws.exit()
    return out


def test_the_look_page_is_one_page_in_every_runtime(look_runs):
    ref = look_runs["python"]
    for name, got in look_runs.items():
        assert len(got) == len(ref)
        for i, ((px, pm), (rpx, rpm)) in enumerate(zip(got, ref)):
            assert pm[L_FAIL] == 0, "%s: a row failed (%d)" % (name, pm[L_FAIL])
            assert pm[:L_FAIL] == rpm[:L_FAIL], "%s step %d answered %r, python %r" % (
                name, i, pm, rpm)
            assert pm[L_LEFT] == rpm[L_LEFT], (name, i)
            assert px == rpx, "%s step %d drew other pixels than python" % (name, i)


def test_the_look_page_answers_its_script(look_runs):
    s = [p for _px, p in look_runs["python"]]
    assert s[0][L_FOCUS] == len("Kind") and s[0][L_TAB] == 0
    assert s[1][L_TAB] == 1, "right on the tabs"
    assert s[2][L_FOCUS] in (len("Red"), len("Green"), len("Blue"))
    assert s[4][L_ACTS] == 1 and s[4][L_PICKED] == s[4][L_PICKED]
    assert s[6][L_FOCUS] == len("After")
    assert s[0][L_LEFT] > 0


# -- a popover (docs/look_2026-10.md step 24) ------------------------------------------


def _popover_page(shown, at=None, w=160):
    st = {"size": 0, "under": 0, "open": None, "done": 0, "end": None}

    def draw():
        ui.page("")
        if ui.button("Pick"):
            shown[0] = True
        if ui.link("Under", ""):
            st["under"] += 1
        if shown[0]:
            if at is not None:
                ui.at(*at)
            shown[0] = st["open"] = ui.popover("pick", w)
            st["size"] = ui.seg("Size", ("1", "2", "3"), st["size"])
            if ui.button("Done"):
                shown[0] = False
                st["done"] += 1
            st["end"] = ui.popover()
        return st
    return _Run(draw)


def _open_popover(at=None):
    shown = [False]
    r = _popover_page(shown, at)
    r.frame()
    r.frame()
    r.frame(("a",))
    for _ in range(3):
        r.frame()
    return r, shown


def test_a_popover_opens_focus_on_its_first_stop_and_hangs_under_its_opener():
    r, shown = _open_popover()
    assert shown[0] and r.out["open"] is True and r.out["end"] is False
    assert ui.focus() == "Size"
    pick, size = _box(r, "Pick"), _box(r, "Size")
    assert size[1] > pick[1] + pick[3], "the card hangs under the button that opened it"
    r.frame(("right",))
    assert r.out["size"] == 1
    r.frame(("down",))
    assert ui.focus() == "Done"
    r.frame(("a",))
    assert r.out["done"] == 1 and not shown[0]
    r.frame()
    r.frame()
    assert ui.focus() == "Pick", "focus went back to the opener"


def test_nothing_outside_a_popover_takes_input():
    r, shown = _open_popover()
    r.frame(("up",))
    r.frame(("a",))
    assert r.out["under"] == 0 and shown[0]


def test_a_press_outside_a_popover_dismisses_it():
    r, shown = _open_popover()
    r.frame(ptr=(2, 236, 1 | 2 | 4))
    assert r.out["open"] is False and not shown[0]
    r.frame(ptr=(2, 236, 1))
    r.frame()
    assert r.out["under"] == 0, "the press that dismissed it reached nothing"


def test_a_press_inside_a_popover_keeps_it():
    r, shown = _open_popover()
    b = _box(r, "Done")
    r.frame(ptr=(b[0] + 2, b[1] + 2, 1 | 2 | 4))
    assert r.out["open"] is True
    r.frame(ptr=(b[0] + 2, b[1] + 2, 1))
    assert r.out["done"] == 1


def test_esc_dismisses_a_popover():
    r, shown = _open_popover()
    r.frame(typed=27)
    assert r.out["open"] is False and not shown[0]


def test_a_popover_hangs_from_ats_rect_and_goes_above_it_without_room_below():
    r, _shown = _open_popover(at=(40, 200, 80, 20))
    size = _box(r, "Size")
    assert size[1] + size[3] <= 200, "no room under the rect: the card goes above it"


def test_a_popover_draws_a_card_and_leaves_the_rest():
    plain = _popover_page([False])
    for _ in range(3):
        plain.frame()
    r, _shown = _open_popover()
    size = _box(r, "Size")
    assert r.px(300, 236) == plain.px(300, 236), "only the card is drawn over the page"
    assert any(r.px(x, size[1] - 2) != plain.px(x, size[1] - 2)
               for x in range(size[0], size[0] + size[2]))


# -- the popover's page in every runtime ---------------------------------------------------

WASM_POP = os.path.join(ROOT, "tests", "fixtures", "wasm", "app_pop.moy")
Q_FRAMES, Q_OPEN, Q_SIZE, Q_CLOSES, _Q4, Q_FOCUS, Q_FAIL, Q_UNDER = range(8)
POP_SCRIPT = ("a", "right", "down", "a", "down", "a")


@pytest.fixture(scope="module", params=MOY_VARIANTS)
def pop_runs(request, tmp_path_factory):
    root = str(tmp_path_factory.mktemp("ui_pop") / "carts")
    host_app.moy_carts.ensure_dirs(root)
    for d in ("pop_py.moy", "pop_lua.moy"):
        shutil.copytree(os.path.join(PARTS, d), os.path.join(root, d))
    titles = {"python": "UI Pop Py", "lua": "UI Pop Lua"}
    if _wasm_or_skip():
        from tools import wasm_cart
        wasm_cart.build(WASM_POP, os.path.join(root, "app_pop.moy"))
        titles["wasm"] = "App Pop Wasm"
    out = {}
    for name, title in titles.items():
        ws = host_app.build_workstation(root)
        ws.look.set_theme("moy", persist=False, variant=request.param)
        ws.pointer.visible = False
        open_cart(ws, title)
        make_drv(ws).frame(_DT)
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        drv = make_drv(ws)
        shots = []
        for _ in range(3):
            _frame(drv)
        shots.append((_pixels(ws), _pmem(ws)))
        for step in POP_SCRIPT:
            drv.press(step)
            for _ in range(3):
                _frame(drv)
            shots.append((_pixels(ws), _pmem(ws)))
        out[name] = shots
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        ws.exit()
    return out


def test_the_popover_page_is_one_page_in_every_runtime(pop_runs):
    ref = pop_runs["python"]
    for name, got in pop_runs.items():
        assert len(got) == len(ref)
        for i, ((px, pm), (rpx, rpm)) in enumerate(zip(got, ref)):
            assert pm[Q_FAIL] == 0, "%s: a row failed (%d)" % (name, pm[Q_FAIL])
            assert pm[:Q_FAIL] == rpm[:Q_FAIL], "%s step %d answered %r, python %r" % (
                name, i, pm, rpm)
            assert pm[Q_UNDER] == rpm[Q_UNDER], (name, i)
            assert px == rpx, "%s step %d drew other pixels than python" % (name, i)


def test_the_popover_page_answers_its_script(pop_runs):
    s = [p for _px, p in pop_runs["python"]]
    assert s[0][Q_FOCUS] == len("Pick") and s[0][Q_OPEN] == 0
    assert s[1][Q_OPEN] == 1 and s[1][Q_FOCUS] == len("Size"), "a opens it, focus in it"
    assert s[2][Q_SIZE] == 1, "right on the seg"
    assert s[3][Q_FOCUS] == len("Done")
    assert s[4][Q_CLOSES] == 1 and s[4][Q_OPEN] == 0
    assert s[4][Q_FOCUS] == len("Pick"), "focus back on the opener"
    assert s[6][Q_UNDER] == 1, "the link under it takes input again"


# -- a menu (docs/look_2026-10.md step 25a) ---------------------------------------------

_PAUSE_ITEMS = ("Resume", "Restart", "Options", "Quit")
_PAUSE_ICONS = ("play", "rotate", "settings", "close")


def _menu_page(shown, icons=_PAUSE_ICONS):
    """A centred card (a popover with nothing to hang from) holding a header
    row, a menu of four and its hints, then a captioned menu on the page."""
    st = {"pick": -1, "open": None, "sys": -1}

    def draw():
        ui.page("")
        if shown[0]:
            shown[0] = st["open"] = ui.popover("pause", 200)
            ui.icon("moy")
            ui.info("Star", "Paused")
            st["pick"] = ui.menu("", _PAUSE_ITEMS, icons)
            ui.hints(("enter", "Choose", "Esc", "Resume"))
            ui.popover()
            if st["pick"] == 3:
                shown[0] = False
        st["sys"] = ui.menu("System", ("Search", "Settings"), ("search", "settings"))
        return st
    return _Run(draw)


def _open_menu(icons=_PAUSE_ICONS):
    shown = [True]
    r = _menu_page(shown, icons)
    for _ in range(3):
        r.frame()
    return r, shown


def _card(r):
    got = ui_draw._C.api_box(r.ctx, "pause")
    assert got is not None, "the popover's card answers by its id"
    return got


def test_a_menu_is_a_stop_per_item_and_answers_the_one_activated():
    r, shown = _open_menu()
    assert ui.focus() == "Resume", "focus opens on the card's first item"
    boxes = [_box(r, t) for t in _PAUSE_ITEMS]
    assert all(b is not None for b in boxes)
    pitch = boxes[1][1] - boxes[0][1]
    for a, b in zip(boxes, boxes[1:]):
        assert b[1] - a[1] == pitch and a[3] == ROW, "the items stack as rows"
    r.frame(("down",))
    r.frame(("down",))
    assert ui.focus() == "Options"
    r.frame(("a",))
    assert r.out["pick"] == 2
    r.frame()
    assert r.out["pick"] == -1, "an item answers the frame it is activated"
    b = _box(r, "Restart")
    r.tap(b[0] + b[2] // 2, b[1] + b[3] // 2)
    assert r.out["pick"] == 1


def test_a_menu_draws_each_items_icon_before_its_label():
    r, _shown = _open_menu()
    bare, _ = _open_menu(icons=())
    b = _box(r, "Quit")
    assert b == _box(bare, "Quit"), "an icon moves no row"
    y = b[1] + b[3] // 2
    assert any(r.px(x, y) != bare.px(x, y) for x in range(b[0], b[0] + 2 * b[3]))


def _rows_popover(n, at, tall=False):
    def draw():
        ui.page("")
        ui.at(*at)
        ui.popover("pick", 160)
        for i in range(n):
            ui.link("Row %d" % i, "")
        ui.popover()
    r = _Run(draw)
    if tall:
        r.cv = make_system_canvas(320, 480)
    for _ in range(3):
        r.frame()
    return r


def _meets(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


@pytest.mark.parametrize("y", [0, 20, 60, 110, 160, 200, 220])
@pytest.mark.parametrize("n", [2, 6, 30])
def test_a_popover_keeps_off_the_edges_and_its_anchor(y, n):
    """Wherever its anchor stands and however long its body, the card keeps
    4 pixels at the scale off every edge of the surface and never covers
    the rect it hangs from: below it, else above it, else on the side with
    more room, its body cut to that room (design review R4-04)."""
    at = (300, y, 20, 20)
    r = _rows_popover(n, at)
    x0, y0, w, h = _box(r, "pick")
    assert x0 >= 4 and y0 >= 4 and x0 + w <= 320 - 4 and y0 + h <= 240 - 4, (x0, y0, w, h)
    assert not _meets((x0, y0, w, h), at), "the card covers its anchor"


def test_a_cut_popover_fades_its_body_where_its_rows_run_on():
    """A body cut to the card's room fades toward the card over its last
    rows; the same rows on a glass tall enough to hold them all do not."""
    at = (40, 20, 80, 20)
    cut, whole = _rows_popover(12, at), _rows_popover(12, at, tall=True)
    x0, y0, w, h = _box(cut, "pick")
    assert h < _box(whole, "pick")[3], "the body is cut"
    first = _box(cut, "Row 0")
    assert first == _box(whole, "Row 0")
    foot = y0 + h - (first[1] - y0)                  # the body's foot: the card's pad above it
    xs = range(x0 + 2, x0 + w - 2)

    def band(r, a, b):
        return [r.px(x, y) for y in range(a, b) for x in xs]
    assert band(cut, foot - 8, foot) != band(whole, foot - 8, foot), "no fade at the cut"
    assert band(cut, first[1], first[1] + 4) == band(whole, first[1], first[1] + 4)


def test_a_popover_with_nothing_to_hang_from_is_centred():
    r, _shown = _open_menu()
    card = _card(r)
    assert abs(card[0] + card[2] // 2 - 160) <= 1
    assert abs(card[1] + card[3] // 2 - 120) <= 1


def test_a_menu_in_a_card_is_ruled_from_the_rows_above_and_its_hints_below():
    r, _shown = _open_menu()
    card = _card(r)
    ground = r.px(card[0] + card[2] // 2, card[1] + 3)

    def rules(y0, y1):
        xs = range(card[0] + 24, card[0] + card[2] - 24)
        return [y for y in range(y0, y1)
                if r.px(xs[0], y) != ground and all(r.px(x, y) == r.px(xs[0], y) for x in xs)]
    resume, quit_ = _box(r, "Resume"), _box(r, "Quit")
    assert rules(resume[1] - 12, resume[1]), "a rule over the menu"
    assert rules(quit_[1] + quit_[3], quit_[1] + quit_[3] + 12), "a rule over the hints"


def test_esc_closes_the_card_and_a_captioned_menu_stacks_on_the_page():
    r, shown = _open_menu()
    r.frame(typed=27)
    assert r.out["open"] is False and not shown[0]
    r.frame()
    r.frame()
    assert _box(r, "Search") is not None and _box(r, "Resume") is None
    r.frame(("down",))
    r.frame(("a",))
    assert r.out["sys"] == 1


# -- the menu's page in every runtime ------------------------------------------------------

WASM_MENU = os.path.join(ROOT, "tests", "fixtures", "wasm", "app_menu.moy")
M_FRAMES, M_OPEN, M_LAST, M_PICKS, _M4, M_FOCUS, M_FAIL, M_SYS = range(8)
MENU_SCRIPT = ("down", "down", "a", "down", "a", "down", "a")


@pytest.fixture(scope="module")
def menu_runs(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("ui_menu") / "carts")
    host_app.moy_carts.ensure_dirs(root)
    for d in ("menu_py.moy", "menu_lua.moy"):
        shutil.copytree(os.path.join(PARTS, d), os.path.join(root, d))
    titles = {"python": "UI Menu Py", "lua": "UI Menu Lua"}
    if _wasm_or_skip():
        from tools import wasm_cart
        wasm_cart.build(WASM_MENU, os.path.join(root, "app_menu.moy"))
        titles["wasm"] = "App Menu Wasm"
    out = {}
    for name, title in titles.items():
        ws = host_app.build_workstation(root)
        ws.pointer.visible = False
        open_cart(ws, title)
        make_drv(ws).frame(_DT)
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        drv = make_drv(ws)
        shots = []
        for _ in range(3):
            _frame(drv)
        shots.append((_pixels(ws), _pmem(ws)))
        for step in MENU_SCRIPT:
            drv.press(step)
            for _ in range(3):
                _frame(drv)
            shots.append((_pixels(ws), _pmem(ws)))
        out[name] = shots
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        ws.exit()
    return out


def test_the_menu_page_is_one_page_in_every_runtime(menu_runs):
    ref = menu_runs["python"]
    for name, got in menu_runs.items():
        assert len(got) == len(ref)
        for i, ((px, pm), (rpx, rpm)) in enumerate(zip(got, ref)):
            assert pm[M_FAIL] == 0, "%s: a row failed (%d)" % (name, pm[M_FAIL])
            assert pm[:M_FAIL] == rpm[:M_FAIL], "%s step %d answered %r, python %r" % (
                name, i, pm, rpm)
            assert pm[M_SYS] == rpm[M_SYS], (name, i)
            assert px == rpx, "%s step %d drew other pixels than python" % (name, i)


def test_the_menu_page_answers_its_script(menu_runs):
    s = [p for _px, p in menu_runs["python"]]
    assert s[0][M_OPEN] == 1 and s[0][M_FOCUS] == len("Resume"), "focus opens in the card"
    assert s[2][M_FOCUS] == len("Options")
    assert s[3][M_LAST] == 3 and s[3][M_PICKS] == 1, "a activates Options"
    assert s[5][M_LAST] == 4 and s[5][M_OPEN] == 0, "Quit closes the card"
    assert s[7][M_SYS] == 2, "the page's menu takes input again"


# -- the apps' restyle (docs/look_2026-10.md step 31): tools, the app's bar, a
# -- rule, the override keys wash and lead ---------------------------------------------


def _tools(current=False, disabled=False):
    st = {}

    def draw():
        ui.page("")
        ui.at(10, 100, 42, 32)
        st["sel"] = ui.tool("select", "Select", {"current": current})
        ui.at(56, 100, 42, 32)
        st["copy"] = ui.tool("copy", "Copy\tCtrl+C", {"disabled": disabled})
        return st
    r = _Run(draw)
    r.frame()
    r.frame()
    return r


def test_a_tool_is_a_flat_stop_its_word_under_its_icon():
    r = _tools()
    assert _box(r, "Select") == (10, 100, 42, 32)
    assert _box(r, "Copy") == (56, 100, 42, 32), "its id is its word, not its key"
    ground = r.px(5, 95)
    assert r.px(70, 102) == ground, "flat: no fill where no mark lies"
    words = [r.px(x, y) for x in range(56, 98) for y in range(120, 132)]
    assert any(p != ground for p in words), "the word under the icon"
    r.tap(77, 116)
    assert r.out["copy"] is True


def test_a_disabled_tool_is_no_stop_and_a_current_one_wears_the_mark():
    off = _tools(disabled=True)
    assert _box(off, "Copy") is None
    plain, cur = _tools(), _tools(current=True)
    assert plain.px(12, 102) != cur.px(12, 102), "the current mark's plate"


def test_a_resting_pointer_on_a_desk_tool_shows_its_word_and_key_at_the_end():
    th = _theme("moy", "light")
    cv = make_system_canvas(400, 200)
    ctx = ui_draw.ui_context()
    inp = array("i", [0] * ui_draw.UI_IN_LEN)
    out = {}

    def frame(ptr):
        inp[2], inp[3], inp[4] = ptr
        ui_draw.ui_frame(ctx, cv, th, None, inp, desk=True)
        ui.page("")
        ui.at(20, 20, 36, 36)
        out["hit"] = ui.tool("paste", "Paste\tCtrl+V")
        ui.end()

    def px():
        return bytes(cv._buf)
    frame((300, 150, 1))
    frame((300, 150, 1))
    away = px()
    frame((38, 38, 1))
    frame((38, 38, 1))
    rest = px()
    o = lambda x, y: 2 * (y * cv.w + x)                  # noqa: E731
    under = [rest[o(x, y)] != away[o(x, y)] for x in range(0, 120) for y in range(60, 86)]
    assert any(under), "the tooltip under the tool"


def _bar_page():
    st = {}

    def draw():
        ui.page("")
        st["b"] = ui.bar("Story", ("undo", "redo"), (False, True), True)
        return st
    r = _Run(draw)
    r.frame()
    return r


def test_the_bar_an_app_lends_is_read_and_tapped_by_its_host():
    from runtime import iconsets
    r = _bar_page()
    title, slots, off, back, _chip = ui_draw.bar_lent(r.ctx)
    assert title == "Story" and [iconsets.SLOTS[s] for s in slots] == ["undo", "redo"]
    assert off == 0b10 and back is True
    ui_draw.bar_tap(r.ctx, 0)
    r.frame()
    assert r.out["b"] == 0, "the tool the bar activated, once"
    r.frame()
    assert r.out["b"] == -1
    ui_draw.bar_tap(r.ctx, 1)
    r.frame()
    assert r.out["b"] == -1, "a tool at half strength activates nothing"
    ui_draw.bar_tap(r.ctx, -2)
    r.frame()
    assert r.out["b"] == ui.UP


def test_a_bar_not_lent_this_frame_is_none():
    st = {"lend": True}

    def draw():
        ui.page("")
        if st["lend"]:
            ui.bar("Here")
    r = _Run(draw)
    r.frame()
    assert ui_draw.bar_lent(r.ctx) is not None
    st["lend"] = False
    r.frame()
    assert ui_draw.bar_lent(r.ctx) is None


def test_a_rule_is_a_line_across_or_down_ats_rect():
    def draw():
        ui.page("")
        ui.at(10, 50, 100, 7)
        ui.rule()
        ui.at(200, 20, 9, 100)
        ui.rule()
    r = _Run(draw)
    r.frame()
    ground = r.px(5, 5)
    assert r.px(50, 50) != ground and r.px(50, 51) == ground, "one pixel across"
    assert r.px(200, 60) != ground and r.px(201, 60) == ground, "one pixel down"


def _wash_page(wash):
    def draw():
        ui.page("")
        ui.hints(("confirm", "Choose"))
        ui.link("Row", "")
        ui.at(100, 60, 100, 20)
        ui.popover("m", 120, {"wash": True} if wash else None)
        ui.menu("", ("One", "Two"))
        ui.popover()
    r = _Run(draw)
    for _ in range(3):
        r.frame()
    return r


def test_a_washing_popover_dims_what_it_covers_but_not_the_hint_strip():
    plain, washed = _wash_page(False), _wash_page(True)
    assert plain.px(10, 40) != washed.px(10, 40), "the page under it dims"
    assert plain.px(4, 236) == washed.px(4, 236), "the hint strip does not"


def _lead_table(lead, group):
    def draw():
        ui.page("")
        if group:
            ui.group("Notes")
        ui.table("", ("", ""), (("new", "New note"), ("notes", "One")), 1,
                 {"id": "vault", "lead": True} if lead else {"id": "vault"})
        if group:
            ui.group()
    r = _Run(draw)
    r.frame()
    r.frame()
    return r


def test_a_lead_row_is_the_tables_verb_in_value():
    plain, lead = _lead_table(False, False), _lead_table(True, False)
    x, y, w, _h = _box(plain, "vault")
    row = [(i, y + 10) for i in range(x, x + w // 2)]
    assert [plain.px(*p) for p in row] != [lead.px(*p) for p in row]


def test_a_table_in_a_group_draws_its_rows_as_the_groups():
    r = _lead_table(False, True)
    x, y, w, h = _box(r, "vault")
    ground = r.px(2, y + 5)
    assert r.px(x + w // 2, y + 25) != ground, "the rows lie on the card"


# -- the restyle's page in every runtime ---------------------------------------------------

WASM_RESTYLE = os.path.join(ROOT, "tests", "fixtures", "wasm", "app_restyle.moy")
R_FRAMES, R_MAKES, R_TOOLS, R_BAR, _R4, R_FOCUS, R_FAIL = range(7)
RESTYLE_SCRIPT = ("a", "down", "a", "right", "a")


@pytest.fixture(scope="module")
def restyle_runs(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("ui_restyle") / "carts")
    host_app.moy_carts.ensure_dirs(root)
    for d in ("restyle_py.moy", "restyle_lua.moy"):
        shutil.copytree(os.path.join(PARTS, d), os.path.join(root, d))
    titles = {"python": "UI Restyle Py", "lua": "UI Restyle Lua"}
    if _wasm_or_skip():
        from tools import wasm_cart
        wasm_cart.build(WASM_RESTYLE, os.path.join(root, "app_restyle.moy"))
        titles["wasm"] = "App Restyle Wasm"
    out = {}
    for name, title in titles.items():
        ws = host_app.build_workstation(root)
        ws.pointer.visible = False
        open_cart(ws, title)
        make_drv(ws).frame(_DT)
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        drv = make_drv(ws)
        shots = []
        for _ in range(3):
            _frame(drv)
        shots.append((_pixels(ws), _pmem(ws)))
        for step in RESTYLE_SCRIPT:
            drv.press(step)
            for _ in range(3):
                _frame(drv)
            shots.append((_pixels(ws), _pmem(ws)))
        out[name] = shots
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        ws.exit()
    return out


def test_the_restyle_page_is_one_page_in_every_runtime(restyle_runs):
    ref = restyle_runs["python"]
    for name, got in restyle_runs.items():
        assert len(got) == len(ref)
        for i, ((px, pm), (rpx, rpm)) in enumerate(zip(got, ref)):
            assert pm[R_FAIL] == 0, "%s: a row failed (%d)" % (name, pm[R_FAIL])
            assert pm[:R_FAIL] == rpm[:R_FAIL], "%s step %d answered %r, python %r" % (
                name, i, pm, rpm)
            assert px == rpx, "%s step %d drew other pixels than python" % (name, i)


def test_the_restyle_page_answers_its_script(restyle_runs):
    s = [p for _px, p in restyle_runs["python"]]
    assert s[0][R_FOCUS] == len("Make")
    assert s[1][R_MAKES] == 1, "a activates the verb"
    assert s[3][R_TOOLS] in (1, 10), "a tool under it activates"
    assert all(p[R_BAR] == 0 for p in s), "no host tapped the bar"
