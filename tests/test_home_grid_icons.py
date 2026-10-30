"""The home's grid and icons arrangements (docs/look_2026-10.md step 33),
the layout's `home: grid` and `home: icons`.

The grid is a shelf of cards -- the cover at a clean scale, its title under
it cut to the card -- in the rows the glass holds, scrolling sideways with
its last column cut by a third or more; the icons are the covers' reduction
in tiles that scroll down, the focused cart named in a caption band on a
handheld and every tile named on the desk. Both keep the carousel's chrome:
the tabs in the bar (one row with its items), the verbs in the hint strip,
focus flat (the theme's ring on a grown card, no shadow)."""
import json

import pytest

from runtime import host_app, launcher_layer, ui_draw
from runtime.cover_cache import BASE, HALF
from ws_helpers import build_ws_with_shelf


@pytest.fixture(autouse=True)
def _keys():
    was = ui_draw.input_kind()
    ui_draw.input_kind("keys")
    yield
    ui_draw.input_kind(was)


def _home(tmp_path, arrangement, n=30, **kw):
    if kw:
        build_ws_with_shelf(tmp_path, n)
        ws = host_app.build_workstation(str(tmp_path / "carts"), **kw)
        ws.go_home()
        ws.open_library()
    else:
        ws = build_ws_with_shelf(tmp_path, n)
    drv = host_app.ConsoleDriver(ws)
    ws.set_home_arrangement(arrangement)
    ws.set_home_tab("games")
    ws.launcher.sel = 0
    ws.launcher.scroll = 0
    for _ in range(3):
        drv.frame(1 / 30)
    return ws, drv


def _press(drv, name, frames=2):
    drv.press(name)
    for _ in range(frames):
        drv.frame(1 / 30)


def _full_frame(ws, drv):
    ws.launcher_layer._lib_key = None
    ws.mark_dirty()
    drv.frame(1 / 30)


def test_a_layouts_home_picks_the_arrangement(tmp_path):
    import moy_ui
    from runtime.launcher_layer import Carousel, IconHome, Launcher

    ws = build_ws_with_shelf(tmp_path, 4)
    for home, cls in (("icons", IconHome), ("grid", Launcher)):
        rc, _n = moy_ui.theme_install(json.dumps({"format": 2, "name": "hi" + home, "layout": {
            "home": home, "bars": [{"edge": "top", "left": ["zone"],
                                    "right": ["clock", "close", "menu"]}]}}).encode())
        assert rc == 0
        ws.set_home_tab("apps")
        ws.launcher.sel = 1
        want = ws.launcher.selected()
        ws.system.set("layout_theme", "hi" + home)
        ws.look.apply_layout()
        g = ws.launcher
        assert ws.look.home == home and type(g) is cls
        assert g.tab == "apps" and g.selected() is want      # the same tab and card
        ws.system.set("layout_theme", None)
        ws.look.apply_layout()
        assert ws.look.home == "carousel" and type(ws.launcher) is Carousel
        moy_ui.theme_uninstall()


@pytest.mark.parametrize("size, side, rows", ((None, 64, 2), ((1024, 600), 128, 3)))
def test_the_shelf_takes_its_columns_and_rows_from_the_glass(tmp_path, size, side, rows):
    kw = {} if size is None else {"sys_size": size, "font_scale": 1, "windowed": True,
                                       "panel_diagonal_in": 7.0}
    ws, _drv = _home(tmp_path, "grid", **kw)
    g = ws.launcher
    c = g._cells()
    lay = ws.layout
    assert (c.side, c.rows) == (side, rows)
    # every row's card lies over the hint strip, in the band
    assert c.y0 + (c.rows - 1) * c.py + c.side <= c.hints[1] == c.band[1] + c.band[3]
    # the last column the glass shows stands cut by a third or more
    shown = lay.cx + lay.cw - (c.x0 + (c.cols - 1) * c.px)
    assert 0 < shown <= c.side * 2 // 3
    # column-major: a column stacks the rows, the next column right of it
    assert g.cell_of(0) == (0, 0) and g.cell_of(1) == (1, 0) and g.cell_of(rows) == (0, 1)
    r0, r1 = g.tile_rect(0), g.tile_rect(rows)
    assert r1[0] - r0[0] == c.px and r1[1] == r0[1]
    assert g.cover_specs()[0][1] == (HALF if side < 128 else BASE)


def test_the_shelf_scrolls_sideways_to_the_focus(tmp_path):
    ws, drv = _home(tmp_path, "grid")
    g = ws.launcher
    c = g._cells()
    for _ in range(c.cols + 1):
        _press(drv, "right")
    assert g.cell_of(g.sel) == (0, c.cols + 1) and g.scroll > 0
    r = g.tile_rect(g.sel)
    assert c.band[0] < r[0] and r[0] + r[2] < c.band[0] + c.band[2]   # whole, in view
    _press(drv, "down")
    assert g.cell_of(g.sel) == (1, c.cols + 1)


def test_focus_is_flat_and_the_focused_title_bold(tmp_path, monkeypatch):
    ws, drv = _home(tmp_path, "grid")
    g = ws.launcher
    focus, casts, labels = [], [], []
    real_focus, real_label = ui_draw.focus, ui_draw.label

    def spy_focus(cv, th, rect, *a, **k):
        focus.append((rect, k.get("flat")))
        return real_focus(cv, th, rect, *a, **k)

    def spy_label(cv, rect, text, ink, *a):
        labels.append((rect, text, a))
        return real_label(cv, rect, text, ink, *a)

    monkeypatch.setattr(ui_draw, "focus", spy_focus)
    monkeypatch.setattr(ui_draw, "label", spy_label)
    monkeypatch.setattr(ui_draw, "cast", lambda *a, **k: casts.append(a))
    _full_frame(ws, drv)
    c = g._cells()
    r = g.tile_rect(g.sel)
    grown = (r[0] - c.grow, r[1] - c.grow, r[2] + 2 * c.grow, r[3] + 2 * c.grow)
    assert (grown, True) in focus and not casts, "the theme's ring on the grown card, no shadow"
    bold = [t for _r, t, a in labels if len(a) > 2 and a[2] is not None]
    assert bold == [g.selected()["title"]]
    assert all(rr[2] == c.side for rr, _t, _a in labels), "titles cut to the card"
    # no PLAY or CHANGE on a card: Enter plays and C changes, in the hint strip
    items, verbs = ws.launcher_layer._hint_groups()
    assert verbs[:2] == ["play", "change"]


@pytest.mark.parametrize("arrangement", ("grid", "icons"))
def test_the_tabs_are_the_bars_one_row(tmp_path, arrangement):
    ws, drv = _home(tmp_path, arrangement)
    ll = ws.launcher_layer
    assert ll.tabs_in_bar(), "the tabs sit in the bar's zone"
    _press(drv, "up")                                       # the top row: the tabs
    assert ll.focused_tab() == "games"
    _press(drv, "right")
    assert ll.focused_tab() == "mine" and ws.launcher.tab == "games"
    _press(drv, "run")
    assert ws.launcher.tab == "mine" and not ll._tab_focus


@pytest.mark.parametrize("size, tile, cols", ((None, 40, 5), ((1024, 600), 64, 10)))
def test_the_icons_take_their_columns_from_the_glass(tmp_path, size, tile, cols):
    kw = {} if size is None else {"sys_size": size, "font_scale": 1, "windowed": True,
                                       "panel_diagonal_in": 7.0}
    ws, drv = _home(tmp_path, "icons", **kw)
    g = ws.launcher
    c = g._cells()
    assert (c.side, c.cols) == (tile, cols)
    assert (c.caption is None) == (size is not None), "a caption band on the handheld alone"
    if c.caption is not None:
        assert c.band[1] + c.band[3] == c.caption[1] and c.caption[1] + c.caption[3] == c.hints[1]
        # the band clips the top of the next row: the grid reads as scrolling
        assert any(c.y0 + k * c.py < c.caption[1] < c.y0 + k * c.py + c.side
                   for k in range(c.rows))
    assert g.cell_of(cols) == (1, 0)
    assert all(div == HALF for _it, div in g.cover_specs())
    _press(drv, "down")
    assert g.sel == cols
    _press(drv, "right")
    assert g.sel == cols + 1


def test_the_handheld_caption_names_the_focused_cart(tmp_path):
    ws, drv = _home(tmp_path, "icons")
    g = ws.launcher
    printed = []
    cv = ws.sys_canvas
    real = cv.print

    def spy(s, x, y, c, *a):
        printed.append((s, y))
        return real(s, x, y, c, *a)

    cv.print = spy
    try:
        _press(drv, "right")
        _full_frame(ws, drv)
    finally:
        cv.print = real
    cap = g._cells().caption
    words = [s for s, y in printed if cap[1] <= y < cap[1] + cap[3]]
    assert words[0] == g.selected()["title"]
    assert "Built in" in words or "Installed" in words or "Yours" in words


def test_the_desk_names_every_tile(tmp_path, monkeypatch):
    ws, drv = _home(tmp_path, "icons", sys_size=(1024, 600), font_scale=1, windowed=True,
                     panel_diagonal_in=7.0)
    g = ws.launcher
    labels = []
    real = ui_draw.label

    def spy(cv, rect, text, ink, *a):
        labels.append((text, len(a) > 2 and a[2] is not None))
        return real(cv, rect, text, ink, *a)

    monkeypatch.setattr(ui_draw, "label", spy)
    _full_frame(ws, drv)
    shown = [g.items[i] for i, _r in g._visible()]
    assert len(labels) == len(shown) > 10
    assert [t for t, b in labels if b] == [g.selected()["title"]]


@pytest.mark.parametrize("arrangement, dx, dy", (("grid", -9, 0), ("icons", 0, -9)))
def test_a_drag_frame_equals_a_full_paint(tmp_path, arrangement, dx, dy):
    """The band frame (#113) repaints the band through its table pass: a
    drag frame's pixels equal a full paint's at the same offset, and the
    drag opens nothing."""
    ws, drv = _home(tmp_path, arrangement, 40)
    g = ws.launcher
    for _ in range(10):                                   # arm the streak and the capture
        _full_frame(ws, drv)
    calls = [0]
    real = g.band_pass

    def spy(*a):
        calls[0] += 1
        return real(*a)

    g.band_pass = spy
    b = g.band_rect()
    x, y = b[0] + b[2] // 2, b[1] + b[3] // 2
    drv.touch(x, y)
    drv.frame(1 / 30)
    for _ in range(7):
        x, y = x + dx, y + dy
        drv.touch_drag(x, y)
        drv.frame(1 / 30)
    assert g.dragging and g.scroll > 0 and calls[0] > 0
    partial = bytes(ws.sys_canvas._buf)
    scroll = g.scroll
    ws.launcher_layer._full_streak = 0
    g._region.invalidate()
    ws.mark_dirty()
    drv.touch_drag(x, y)
    drv.frame(1 / 30)
    assert g.scroll == scroll
    full = bytes(ws.sys_canvas._buf)
    row = ws.sys_canvas.w * 2 * ws.layout.cy
    assert full[row:] == partial[row:]
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "launcher"


def test_the_arrangement_is_in_the_statics(tmp_path):
    ws, _drv = _home(tmp_path, "icons", 6)
    cv = ws.sys_canvas
    key = ws.launcher_layer._statics_key(cv)
    ws.set_home_arrangement("grid")
    assert ws.launcher_layer._statics_key(cv) != key


@pytest.mark.parametrize("arrangement", ("grid", "icons"))
def test_an_idle_home_paints_nothing(tmp_path, arrangement):
    ws, drv = _home(tmp_path, arrangement, 10)
    for _ in range(80):
        drv.frame(1 / 30)
    n = ws._frames_drawn
    for _ in range(30):
        drv.frame(1 / 30)
    assert ws._frames_drawn == n


def test_a_tap_on_a_card_runs_it(tmp_path):
    ws, drv = _home(tmp_path, "grid", 10)
    g = ws.launcher
    game = next(i for i, it in enumerate(g.items) if it.get("type") == "game")
    g.sel = game
    g._scroll_to_sel()
    drv.frame(1 / 30)
    r = g.tile_rect(game)
    drv.touch(r[0] + r[2] // 2, r[1] + r[3] // 2)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.screen != "launcher" and launcher_layer.PSEUDO_TILE_TYPES
