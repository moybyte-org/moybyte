"""The public UI API's parts (docs/look_2026-10.md step 12; the verbs are
docs/ui_api_2026-10.md §1's): a test per kind under the engine, and one parts
page drawn by a Python, a Lua and a compiled app to the same pixels.

  * group: a caption, then a card under its rows -- rounded where the theme
    has a depth, square and ringed where it has none -- whose foot keeps the
    last row's focus mark and draws no second one;
  * info is no stop; link answers its activation by key and by tap;
  * cycle wraps, seg clamps, swatches pick by tap and draw their colours;
  * toggle draws its On/Off word and flips; slider maps the pointer over its
    track and steps by arrow;
  * hints take the page's foot, which its rows then stop above;
  * an icon goes on the next row or button; progress is no stop;
  * a table's current row follows focus and opens by Enter or a second tap,
    a scroll region keeps the focused row in its rect, a field types each
    key once, and a dialog holds the input with focus on its first stop.

The Files verbs (step 21) have a page of their own drawn the same three
ways (`tests/fixtures/ui_parts/files_*.moy`, `tests/fixtures/wasm/app_files.moy`).
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
P_FRAMES, P_OPENS, P_SIZE, P_MODE, P_ACCENT, P_FOCUS, P_FAIL, P_TOP = range(8)
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

    def __init__(self, draw, theme=("moy", "light")):
        self.th = _theme(*theme)
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
        ui_draw.ui_frame(self.ctx, self.cv, self.th, None, i)
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
        return list(lua._run.pmem()[1])[:8]
    if lua is not None:
        import sys
        img = array("i", bytearray(4 * 256))
        sys.modules[type(lua).__module__]._moycore.pmem_image(img)
        return list(img)[:8]
    pm = ws.player.ns["pmem"]
    return [pm(i) for i in range(8)]


def _frame(drv):
    quiesce(drv.ws)
    drv.ws._dirty = True
    drv.frame(_DT)


def _script(ws):
    """Settle; open the link; step the cycle; move to the seg and step it;
    then tap the swatch picker's last dot."""
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
            assert pm[:P_FAIL] == rpm[:P_FAIL], "%s step %d answered %r, python %r" % (
                name, i, pm, rpm)
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
