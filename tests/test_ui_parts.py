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
  * hints take the page's foot, which its rows then stop above.
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

    def frame(self, press=(), ptr=None):
        i = self.inp
        i[0] = i[1] = 0
        for k in press:
            i[1] |= BITS[k]
        i[0] = i[1]
        i[2], i[3], i[4] = ptr if ptr is not None else (0, 0, 0)
        i[5] = 0
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
    yy = top + CAPTION + 3 * ROW + GAP + CAPTION + ROW + 10
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
