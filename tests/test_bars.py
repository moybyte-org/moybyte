"""Bars on any edge (docs/shell_layout_2026-10.md §4, §7, §13 step 5).

Every resolved bar is drawn as its own cached strip at its rect, its cells
packed once per relayout (`shell_layout.pack`), its taps routed by the bar
they land on, and the content rect is what all of them leave. Today's one top
bar packs to exactly the geometry it had (the shell goldens hold that byte for
byte); these tests take the layouts the goldens do not: side bars, two bars,
flex widgets sharing a bar, the desk's chips and windows against them.
"""

import json

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
import moy_ui
from runtime import bar_layer as BL
from runtime import layout_base, shell_layout
from runtime.layout_base import DEFAULT_BARS, LayoutBase
from test_content_rect import _inside, _layouts, _open_every_surface, _rect_map, _settle, _tap
from ws_helpers import build_desktop_ws, build_ws

DT = 1.0 / 30.0


@pytest.fixture(autouse=True)
def _top_again():
    yield
    layout_base.place_bars(DEFAULT_BARS)


def _bar(edge, size=18, left=(), center=(), right=(), text=()):
    return (edge, size, tuple(left), tuple(center), tuple(right), tuple(text))


def _pick(ws, bars):
    """Install `bars` as a package's layout and pick it, as Settings does."""
    pkg = {"format": 2, "name": "bars", "layout": {"bars": [
        {"edge": b[0], "size": b[1], "left": list(b[2]), "center": list(b[3]),
         "right": list(b[4]), "text": list(b[5])} for b in bars]}}
    rc, _notes = moy_ui.theme_install(json.dumps(pkg).encode())
    assert rc == 0
    ws.look.set_theme(ws.look.theme_name, persist=False)
    ws.system.set("layout_theme", "bars")
    ws.look.apply_layout()


def _cells(packed, i):
    return {c[0]: c[1] for c in packed[i][3]}


# -- packing -------------------------------------------------------------------

def test_todays_bar_packs_to_the_fixed_game_cluster():
    """The game canvas's strip: the packing of today's bar at 320 wide is the
    fixed cluster the crash-era constants name."""
    got = _cells(shell_layout.pack(320, 240, 18, 1, 1, 40, of=DEFAULT_BARS), 0)
    assert got["battery"] == BL._ZONE_BATT
    assert got["wifi"] == BL._ZONE_WIFI
    assert got["menu"] == BL._ZONE_GEAR
    assert got["close"] == BL._ZONE_CONTEXT_X
    assert got["clock"] == BL._ZONE_CLOCK
    assert got["zone"] == BL._ZONE_LEFT_GAME
    # the taskbar's chips float over the middle from a quarter of the bar to
    # the right cluster, and bound nothing
    assert got["chips"] == (80, 0, BL._ZONE_CLOCK[0] - 80, 18)


def test_a_fixed_centre_cell_bounds_the_flex_zone():
    lay = (_bar("top", 18, ["zone"], ["clock"], ["wifi", "battery", "close", "menu"]),)
    got = _cells(shell_layout.pack(1024, 600, 36, 2, 1, 30, of=lay), 0)
    cx = got["clock"][0]
    assert cx == (1024 - 30) // 2
    assert got["zone"][0] + got["zone"][2] == cx - 4      # the gap before the clock
    assert got["menu"][0] + got["menu"][2] == 1024 - 4    # the right slot from the end


def test_flex_widgets_share_the_slack_of_their_run():
    lay = (_bar("bottom", 22, ["menu", "separator", "chips", "zone"], [],
                ["close", "wifi", "battery", "clock"]),)
    (edge, rect, _dy, cells), = shell_layout.pack(1024, 600, 36, 2, 1, 30, of=lay)
    assert rect == (0, 600 - 44, 1024, 44)
    got = {c[0]: c[1] for c in cells}
    chips, zone = got["chips"], got["zone"]
    assert abs((chips[2] - 48) - (zone[2] - 128)) <= 1   # an even share past each minimum
    assert zone[0] + zone[2] + 4 == got["close"][0]       # up to the right slot
    # a 22-row bar keeps the 1-pixel icon inset centred
    assert got["menu"][1] == 1 + (44 - 36) // 2


def test_a_side_bar_stacks_its_cells_and_centres_them_across():
    lay = (_bar("top", 18, ["zone"], [], ["close"]),
           _bar("left", 24, ["menu", "separator"], [], ["clock", "battery"]))
    packed = shell_layout.pack(1024, 600, 36, 2, 1, 30, of=lay)
    assert packed[1][1] == (0, 36, 48, 600 - 36)          # between the top bar and the floor
    got = _cells(packed, 1)
    assert got["menu"] == ((48 - 32) // 2, 4, 32, 32)
    assert got["separator"] == (0, 4 + 32 + 4, 48, 8)
    assert got["battery"][1] + 32 == (600 - 36) - 4       # the right slot from the bottom
    assert got["clock"] == (0, got["battery"][1] - 4 - 32, 48, 32)


def test_the_content_rect_is_what_every_bar_leaves():
    lay = (_bar("top", 18), _bar("bottom", 20), _bar("left", 30), _bar("right", 16))
    assert layout_base.content_of(1024, 600, 36, lay) == (60, 36, 1024 - 60 - 32, 600 - 36 - 40)
    assert layout_base.content_of(1024, 600, 0, lay) == (0, 0, 1024, 600)


# -- the shell under side bars ---------------------------------------------------

FOUR = (_bar("top", 18, ["zone"], [], ["clock"]),
        _bar("bottom", 18, ["title"]),
        _bar("left", 20, ["menu"]),
        _bar("right", 20, [], [], ["close", "wifi", "battery"]))


def test_every_layout_lays_out_inside_what_four_bars_leave(tmp_path):
    ws = build_ws(tmp_path, sys_size=(480, 320))
    _open_every_surface(ws)
    top = _rect_map(ws)
    _pick(ws, FOUR)
    assert [b[0] for b in ws.look.bars] == ["top", "bottom", "left", "right"]
    bar = ws.bar_layer
    for name, lay in _layouts(ws):
        where = "app" if not isinstance(lay, LayoutBase) else "menu"
        assert (lay.cx, lay.cy, lay.cw, lay.ch) == bar.content_rect(where), name
    assert bar.content_rect("menu") == (20, 18, 480 - 40, 320 - 36)
    four = _rect_map(ws)
    bad, checked = [], 0
    for key, (r, content) in sorted(top.items()):
        if not _inside(r, content):
            continue
        checked += 1
        rb, cb = four[key]
        if not _inside(rb, cb):
            bad.append("%s.%s[%d] %r in %r" % (key + (rb, cb)))
    assert checked > 100
    assert not bad, "outside the content rect under four bars: %s" % bad


@pytest.mark.parametrize("where", ("home", "settings", "menu", "app"))
def test_each_bar_draws_its_strip_at_its_rect(tmp_path, where):
    from test_content_rect import SURFACES
    ws = build_ws(tmp_path)
    _pick(ws, FOUR)
    SURFACES[where](ws)
    frame = _settle(ws)
    stride = ws.sys_canvas.w * 2
    strips = ws.bar_layer._bar_strips[where]
    for i, (x, y, w, h) in enumerate(ws.bar_layer.bar_rects(where)):
        strip = strips[i][0][0]
        assert (strip.w, strip.h) == (w, h)
        band = b"".join(frame[stride * (y + r) + 2 * x:stride * (y + r) + 2 * (x + w)]
                        for r in range(h))
        assert band == bytes(strip._buf), "%s bar %d" % (where, i)


def test_taps_route_by_the_bar_they_land_on(tmp_path):
    ws = build_ws(tmp_path)
    _pick(ws, FOUR)
    ws.open_app(ws._apps_by_id["files"])
    _settle(ws)
    bar = ws.bar_layer
    lb, menu = bar.cell("app", "menu")
    _tap(ws, lb[1][0] + menu[0] + 2, lb[1][1] + menu[1] + 2)
    assert ws.sysmenu.open
    # the menu opens inward, from the left bar's inner edge at the cell
    assert ws.sysmenu.anchor_x == lb[1][0] + lb[1][2] and not ws.sysmenu.up
    ws.toggle_sysmenu()
    rb, close = bar.cell("app", "close")
    _tap(ws, rb[1][0] + close[0] + 2, rb[1][1] + close[1] + 2)
    assert ws.wm.top_kind() == "launcher"


def test_a_bar_without_the_clock_keeps_its_strip_on_the_tick(tmp_path):
    ws = build_ws(tmp_path)
    _pick(ws, FOUR)
    ws.open_settings()
    clock = ["12:34"]
    ws.bar_layer._clock_text = lambda: clock[0]
    for _ in range(3):
        ws._dirty = True
        ws.frame(DT)
    drawn = []
    orig = ws.bar_layer._render_cart_bar
    ws.bar_layer._render_cart_bar = lambda cv, key, i=0: (drawn.append(i), orig(cv, key, i))
    clock[0] = "12:35"
    ws._dirty = True
    ws.frame(DT)
    assert drawn == [0]                     # the top bar holds the clock; nothing else


def test_every_bar_is_a_strip_the_rotated_compositor_presents(tmp_path):
    ws = build_desktop_ws(tmp_path)
    got = []

    class Root:
        w, h = 1280, 800

        def set_strips(self, rects):
            got.append(rects)
    ws.bar_layer.publish_strips(Root())
    assert got[-1] == [(0, 0, 1280, 36)]
    _pick(ws, (_bar("top", 18, ["zone"], ["chips"], ["clock", "close", "menu"]),
               _bar("left", 24, ["wifi", "battery"])))
    assert got[-1] == [(0, 0, 1280, 36), (0, 36, 48, 800 - 36)]


# -- the desk ------------------------------------------------------------------

def test_the_desk_keeps_icons_windows_and_chips_off_a_side_bar(tmp_path):
    ws = build_desktop_ws(tmp_path)
    _pick(ws, (_bar("top", 18, ["zone"], [], ["clock", "close", "menu"]),
               _bar("left", 24, ["chips"], [], ["wifi", "battery"])))
    ws.open_desk()
    wm = ws.wm
    dx, dy, dw, dh = wm._desk_rect()
    assert (dx, dy) == (48, 36)
    for _key, box, pill, _label, _cart in wm._backdrop_layer._icon_rects():
        assert _inside(box, (dx, dy, dw, dh)) and _inside(pill, (dx, dy, dw, dh))
    ws.open_app(ws._apps_by_id["calc"])
    ws.frame(DT)
    win = wm._wins[wm._order[-1]]
    wm._move_window(win, -10_000, win.y)
    assert win.x == dx                                    # a side bar is a wall
    (lb, _cell) = ws.bar_layer.cell("desk", "chips")
    chips = wm._chip_rects()
    assert chips and all(_inside(r, lb[1]) and r[2] == r[3] for _k, r, _l in chips)
    key, r, label = chips[0]
    assert len(label) == 1
    wm._focus = None
    _tap(ws, r[0] + 2, r[1] + 2)
    assert wm._focus == key


def test_a_taskbar_packs_its_chips_from_the_cell_start(tmp_path):
    ws = build_desktop_ws(tmp_path)
    _pick(ws, (_bar("bottom", 22, ["menu", "separator", "chips", "zone"], [],
                    ["close", "wifi", "battery", "clock"]),))
    ws.open_desk()
    ws.open_app(ws._apps_by_id["calc"])
    ws.frame(DT)
    bar, cell = ws.bar_layer.cell("desk", "chips")
    (_k, r, _l), = ws.wm._chip_rects()
    assert r[0] == bar[1][0] + cell[0] and _inside(r, bar[1])


def test_a_window_keeps_its_band_on_top_under_a_bottom_bar(tmp_path):
    ws = build_desktop_ws(tmp_path)
    _pick(ws, (_bar("bottom", 18, ["zone"], [], ["clock", "close", "menu"]),))
    ws.open_desk()
    ws.open_settings()
    ws.frame(DT)
    win = ws.wm._wins[ws.wm._order[-1]]
    lay = win.ctx.layout
    assert lay.cy == lay.status_h and lay.bars[0][0] == "top"
    assert lay.bars[0][1] == (0, 0, lay.w, lay.status_h)


# -- the 2026-10 look's bar (docs/look_2026-10.md step 14) --------------------

def _looked_ws(tmp_path, variant="light", desk=False):
    if desk:
        ws = build_desktop_ws(tmp_path, font_scale=1, panel_diagonal_in=7.0)
    else:
        ws = build_ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant=variant)
    return ws


def _names_of(packed):
    return [[c[0] for c in b[3]] for b in packed]


@pytest.mark.parametrize("desk", [False, True])
def test_moys_bar_is_the_moy_button_then_status(tmp_path, desk):
    """Moy's layout: the Moy button first, its tile's cell, then the chips
    (desk) and the zone; the X, Wi-Fi, battery and the clock at the right;
    the handheld bar 28 rows (`hhbar.h`), the desk's 40, and back to 18 on a
    theme with no look."""
    ws = _looked_ws(tmp_path, desk=desk)
    L = ws.layout
    assert L.status_h == (40 if desk else 28)
    names = _names_of(L.bars)[0]
    assert names[0] == "menu" and names[-4:] == ["close", "wifi", "battery", "clock"]
    assert ("chips" in names) == desk
    menu = L.bars[0][3][0][1]
    assert menu[0] == 2 * L.cs and menu[2] == layout_base.sys_tile(L.status_h)[2]
    tile = ws.bar_layer.sys_tile(menu, L.bars[0][1], False)
    assert tile[2:] == ((36, 34) if desk else (22, 20))
    ws.look.set_theme("night", persist=False)
    assert ws.layout.status_h == (40 if desk else 18)
    assert ws.layout.bars[0][3][0][0] == "zone"


def _px(cv, x, y):
    i = (y * cv.w + x) * 2
    return cv._buf[i] << 8 | cv._buf[i + 1]


def _word(v):
    from runtime import ui_draw
    return ui_draw._C.theme_rgb(v)[0]


@pytest.mark.parametrize("variant,fill", [("light", 0xffff), ("dark", None)])
def test_the_desk_bar_is_solid_over_the_backdrop(tmp_path, variant, fill):
    """The band is `bar.fill` at full strength wherever it lies, its line
    under it: the backdrop's discs never show through it."""
    ws = _looked_ws(tmp_path, variant, desk=True)
    _settle(ws)
    cv = ws.wm._root_canvas
    row = [_px(cv, x, 20) for x in range(400, 900, 7)]
    want = fill if fill is not None else _px(cv, 400, 20)
    assert set(row) == {want}
    if variant == "light":
        assert _px(cv, 600, 39) != want                  # bar.line, one row
        assert _px(cv, 600, 38) == want


def test_the_moy_button_takes_the_current_plate_while_its_menu_shows(tmp_path):
    """The Moy button is the pixel Moy on its tile; while the system menu
    it opens shows, the tile takes the current plate."""
    ws = _looked_ws(tmp_path)
    _settle(ws)
    cv = ws.sys_canvas
    L = ws.layout
    tx, ty, tw, tht = ws.bar_layer.sys_tile(L.bars[0][3][0][1], L.bars[0][1], False)
    at = (tx + 1, ty + tht // 2)                          # the tile's edge, past Moy
    rest = _px(cv, *at)
    ws.toggle_sysmenu()
    _settle(ws)
    assert ws.sysmenu.open and _px(cv, *at) != rest
    ws.toggle_sysmenu()
    _settle(ws)
    assert _px(cv, *at) == rest


def test_the_focused_windows_chip_is_raised_under_the_current_mark(tmp_path):
    ws = _looked_ws(tmp_path, desk=True)
    ws.open_settings()
    _settle(ws)
    cv = ws.wm._root_canvas
    chips = ws.wm._chip_rects()
    assert chips and all(r[3] == 28 for _k, r, _l in chips)
    bar = _px(cv, 600, 20)
    for key, (x, y, w, h), _label in chips:
        inside = _px(cv, x + w - 4, y + h // 2)
        assert (inside != bar) == (key == ws.wm._focus)
