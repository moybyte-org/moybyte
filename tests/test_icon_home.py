"""The icon home (docs/shell_layout_2026-10.md §12, theming phase 6 step 11):
the layout's `home: icons`, the grid arrangement with one-tile cards.

What it must keep from the grid -- the tabs, the selection, the keys, the
taps, the bar's verbs, the drag partial's pixel contract -- and what it
changes: square tiles in the viewport, no featured "+ New", the name only
under the focused cell."""
import json

from ws_helpers import build_ws_with_shelf as _ws_with_carts


def _drag_frames(drv, cx, cy, steps, step_px=9):
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    x = cx
    for i in range(1, steps + 1):
        x = cx - i * step_px
        drv.touch_drag(x, cy)
        drv.frame(1 / 30)
    return x


def _icons(tmp_path, n, **kw):
    from runtime import host_app

    ws = _ws_with_carts(tmp_path, n)
    if kw:
        ws = host_app.build_workstation(str(tmp_path / "carts"), **kw)
    drv = host_app.ConsoleDriver(ws)
    ws.set_home_arrangement("icons")
    return ws, drv


def test_a_layouts_home_picks_the_icons(tmp_path):
    import moy_ui
    from runtime.launcher_layer import Carousel, IconHome

    ws = _ws_with_carts(tmp_path, 4)
    rc, _n = moy_ui.theme_install(json.dumps({"format": 2, "name": "hi", "layout": {
        "home": "icons", "bars": [{"edge": "top", "left": ["zone"],
                                   "right": ["clock", "close", "menu"]}]}}).encode())
    assert rc == 0
    ws.set_home_tab("apps")
    ws.launcher.sel = 1
    want = ws.launcher.selected()
    ws.system.set("layout_theme", "hi")
    ws.look.apply_layout()
    g = ws.launcher
    assert ws.look.home == "icons" and type(g) is IconHome
    assert g.tab == "apps" and g.selected() is want      # the same tab and card
    ws.system.set("layout_theme", None)
    ws.look.apply_layout()
    assert ws.look.home == "carousel" and type(ws.launcher) is Carousel
    moy_ui.theme_uninstall()


def _cells_fit(g):
    lay = g.layout
    gx, gy, gw, gh = lay.lib_grid
    t = lay.tile
    assert t >= 32
    assert lay.lib_card_w == t and lay.lib_card_h > t
    assert lay.lib_step >= t                              # columns never overlap
    rects = [g.tile_rect(i) for i in range(lay.rows)]
    for i, r in enumerate(rects):
        assert r is not None and r[2] == t
        assert gy <= r[1] and r[1] + r[3] <= gy + gh      # tile and label band
        if i:
            assert r[1] >= rects[i - 1][1] + rects[i - 1][3]
    # The last whole column ends at the viewport's right edge.
    last = (lay.cols - 1) * lay.lib_step + t
    assert gw - lay.cols < last <= gw


def test_the_cells_fill_the_viewport_on_both_sizes(tmp_path):
    ws, _drv = _icons(tmp_path, 30)
    g = ws.launcher
    assert (g.layout.rows, g.layout.cols) == (2, 4)       # 320x240
    _cells_fit(g)
    assert g.layout.lib_grid == ws.layout.lib_grid        # the grid's panel
    assert g.band_rect() == ws._home_grid("grid", []).band_rect()   # ...its band
    big, _drv = _icons(tmp_path / "big", 30, sys_size=(1024, 600), font_scale=1,
                       windowed=True)
    gb = big.launcher
    assert gb.layout.rows == 3 and gb.layout.cols >= 6
    _cells_fit(gb)
    # One cells object per layout: a re-applied layout reuses it.
    cells = gb.layout
    gb.set_layout(big.layout)
    assert gb.layout is cells


def test_new_is_a_cell_and_the_icon_is_the_covers_reduction(tmp_path):
    from runtime.cover_cache import ICON

    ws, _drv = _icons(tmp_path, 6)
    g = ws.launcher
    ws.set_home_tab("make")
    assert g.items[0].get("type") == "new" and not g._featured()
    assert g.tile_rect(0)[3] == g.tile_rect(1)[3]         # no tall card
    specs = g.cover_specs()
    assert specs and all(div == ICON for _it, div in specs)
    assert all(not it.get("icon") and it.get("path") for it, _d in specs)
    assert g.action_rects() is None                       # the verbs are the bar's
    assert ws.launcher_layer.zone_verbs()[0] == "new"


def test_keys_and_taps_are_the_grids(tmp_path):
    ws, drv = _icons(tmp_path, 10)
    g = ws.launcher
    home = ws.launcher_layer
    ws.set_home_tab("games")
    drv.frame(1 / 30)
    assert g.sel == 0
    drv.press("down")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert g.sel == 1                                     # down a row
    drv.press("right")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert g.sel == 1 + g.layout.rows                     # right a column
    drv.press("left")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    drv.press("up")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    drv.press("up")                                       # the top row: the tabs
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert home._tab_focus
    drv.press("right")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert g.tab == "mine"
    home._tab_focus = False
    ws.set_home_tab("games")
    game = next(i for i, it in enumerate(g.items) if it.get("type") == "game")
    r = g.tile_rect(game)
    drv.touch(r[0] + r[2] // 2, r[1] + r[3] // 2)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "desktop" and g.sel == game      # a cell's tap runs it


def test_only_the_focused_cell_carries_its_name(tmp_path):
    from runtime import ui

    ws, drv = _icons(tmp_path, 10)
    g = ws.launcher
    ws.set_home_tab("games")
    labels = []
    real = ui.icon_label

    def spy(cv, th, band, text, on, fs):
        labels.append((text, band))
        return real(cv, th, band, text, on, fs)

    ui.icon_label = spy
    try:
        ws.mark_dirty()
        drv.frame(1 / 30)
    finally:
        ui.icon_label = real
    sel = g.selected()
    assert [t for t, _b in labels] == [sel["title"]]
    band = labels[0][1]
    r = g.tile_rect(g.sel)
    assert band[1] == r[1] + g.layout.tile + g.layout.label_dy
    assert band[1] + band[3] == r[1] + r[3]               # inside the cell


def test_drag_frame_is_pixel_faithful_with_the_label_in_the_strip(tmp_path):
    """The drag partial's contract (#113) holds for the icon cells: a shifted
    band, its exposed strip repainted, equals a full paint at the same
    offset -- with the focused cell's label, wider than its tile, among the
    pixels the shift moves."""
    ws, drv = _icons(tmp_path, 30)
    g = ws.launcher
    ws.set_home_tab("make")
    g.sel = 2 * g.layout.rows                             # the third column
    for _ in range(80):                                   # settle; arm streak + ring
        drv.frame(1 / 30)
    calls = [0]
    orig = ws.sys_canvas.scroll_rect

    def spy(*a):
        calls[0] += 1
        return orig(*a)

    ws.sys_canvas.scroll_rect = spy
    gx, gy, gw, gh = ws.layout.lib_grid
    cx, cy = gx + gw - 10, gy + gh // 2
    lastx = _drag_frames(drv, cx, cy, 7)
    assert g.dragging and calls[0] > 0
    partial = bytes(ws.sys_canvas._buf)
    scroll = g.scroll
    ws.launcher_layer._full_streak = 0
    g._region.invalidate()
    ws.mark_dirty()
    drv.touch_drag(lastx, cy)
    drv.frame(1 / 30)
    assert g.scroll == scroll
    full = bytes(ws.sys_canvas._buf)
    row = ws.sys_canvas.w * ws.layout.status_h
    assert full[row:] == partial[row:]
    drv.touch_up()
    drv.frame(1 / 30)
    assert ws.screen == "launcher"                        # the drag opened nothing


def test_the_arrangement_is_in_the_statics(tmp_path):
    """The grid and the icons share a panel and a band, so the retained frame
    and the drag's streak must key on the arrangement itself."""
    ws, drv = _icons(tmp_path, 6)
    cv = ws.sys_canvas
    key = ws.launcher_layer._statics_key(cv)
    ws.set_home_arrangement("grid")
    assert ws.launcher_layer._statics_key(cv) != key


def test_home_idle_paints_nothing(tmp_path):
    ws, drv = _icons(tmp_path, 10)
    for _ in range(80):
        drv.frame(1 / 30)
    n = ws._frames_drawn
    for _ in range(30):
        drv.frame(1 / 30)
    assert ws._frames_drawn == n
