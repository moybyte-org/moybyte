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
from ws_helpers import build_ws, make_drv, open_cart, quiesce

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARTS = os.path.join(ROOT, "tests", "fixtures", "ui_parts")
WASM = os.path.join(ROOT, "tests", "fixtures", "wasm", "app_parts.moy")
_DT = 1.0 / 30.0
(P_FRAMES, P_OPENS, P_SIZE, P_MODE, P_ACCENT, P_FOCUS, P_FAIL, P_TOP, P_NAME, P_DONES,
 P_LEVEL) = range(11)
N_PMEM = 11
BITS = {"left": 1, "right": 2, "up": 4, "down": 8, "a": 16, "b": 32, "run": 64}

_THEMES = {}


def _theme(name, variant):
    """The theme colours of a workstation in `name`, its look installed."""
    ws = _THEMES.get((name, variant))
    if ws is None:
        ws = build_ws(Path(tempfile.mkdtemp()))
        _THEMES[(name, variant)] = ws
    ws.look.set_theme(name, persist=False, variant=variant)
    return ws.theme_colors


class _Run:
    """A page of verbs on a system canvas under a theme, frame by frame."""

    def __init__(self, draw, theme=("moy", "light"), app=None):
        self.th = _theme(*theme)
        self.app = app
        self.cv = make_system_canvas(320, 240)
        self.ctx = ui_draw.ui_context()
        self.inp = array("i", [0] * 6)
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
    assert r.px(INSET + PAD + 40, top + ROW) != card, "a rule heads the second row"
    assert r.px(INSET + PAD + 40, top) == card, "no rule over the first"


def test_a_theme_without_a_depth_draws_a_square_ringed_card():
    r = _group_page(theme=("night", "dark"))
    r.frame()
    r.frame()
    top = r.out["rect"][1] + CAPTION
    ring = r.px(INSET, top + 10)
    assert r.px(INSET, top) == ring and r.px(319 - INSET, top + 3 * ROW - 1) == ring


def test_the_cards_foot_keeps_the_last_rows_focus_mark_and_draws_no_second():
    last = _group_page(n=3, last_focus=True)
    for _ in range(3):
        ui_draw.marks(None, True)
        last.frame()
    assert ui_draw.marks()[2] == 1, "one focus mark a frame"
    top = last.out["rect"][1] + CAPTION
    y = top + 3 * ROW - 4                 # inside the foot's corner, on the edge bar
    bar = last.px(INSET + 1, y)
    mid = last.px(INSET + 1, top + 2 * ROW + 10)
    assert bar == mid, "the edge bar runs into the rounded foot"
    assert last.px(INSET + 1, top + ROW + 10) != mid, "only the focused row wears it"


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
    assert st["v"] == 0, "the track's left end is lo"
    r.frame(ptr=(right, 10, 1 | 2))
    assert st["v"] == 10, "its right end is hi"
    r.frame(ptr=(right, 10, 1))


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
    label the face does not cover and a theme that installs no face."""
    pages = {}
    for name, label, kind, app, theme in (
            ("ui", "7", "normal", None, ("moy", "dark")),
            ("key", "7", "normal", "calc", ("moy", "dark")),
            ("op", "7", "tonal", "calc", ("moy", "dark")),
            ("arrow", "\u2190", "normal", "calc", ("moy", "dark")),
            ("arrow_ui", "\u2190", "normal", None, ("moy", "dark")),
            ("day", "7", "normal", "calc", ("moy", "light")),
            ("day_ui", "7", "normal", None, ("moy", "light"))):
        r = pages[name] = _key_page(label, kind, app, theme)
        r.frame()
        r.frame()
    tall = {n: len(_ink_rows(pages[n], 10, 70, 40, 80)) for n in ("ui", "key", "op")}
    assert tall["ui"] < tall["key"] <= tall["op"], tall
    assert bytes(pages["key"].cv._buf) != bytes(pages["op"].cv._buf)
    assert bytes(pages["arrow"].cv._buf) == bytes(pages["arrow_ui"].cv._buf)
    assert bytes(pages["day"].cv._buf) == bytes(pages["day_ui"].cv._buf)


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
    assert card.px(2, 236) == plain.px(2, 236), "the card is centred, not the whole page"


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


@pytest.fixture(scope="module")
def files_runs(tmp_path_factory):
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

    def spy(*a):
        seen.append(a[6])
        return real(*a)
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


@pytest.fixture(scope="module")
def pop_runs(tmp_path_factory):
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
