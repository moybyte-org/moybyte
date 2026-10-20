"""The desk's PROJECTS window draws the console home's design (owner,
2026-10-20): the hero, its details and buttons, the shelf, the hint strip and
the tabs in the window's band, laid out in the window's content rect at any
size, on the window's panel and never on the wallpaper. Built on the shell the
goldens build (`tests/test_shell_goldens.py`'s configs and pinning)."""

import types

import pytest

import test_shell_goldens as g
from runtime import launcher_layer

DESK = "p4_1024x600_cs2_fs1_windowed"


def _ws(tmp_path, theme="moy", mode="dark"):
    import moy_ui
    moy_ui.theme_uninstall()
    cfg = dict(g.CONFIGS[DESK], variant=mode)
    if "layout" not in cfg:
        cfg["theme"] = theme
    return g._build(cfg, tmp_path / "carts")


def _frame(ws, n=3):
    for _ in range(n):
        g._quiesce(ws)
        ws._dirty = True
        ws.frame(g._DT)


@pytest.fixture(autouse=True)
def _uninstall(monkeypatch):
    from runtime import cover_cache
    monkeypatch.setattr(cover_cache, "_COVER_SLICE_MS", g._COVER_SLICE_UNBOUNDED)
    yield
    import moy_ui
    from runtime import iconsets
    moy_ui.theme_uninstall()
    iconsets.reset()


def _projects(ws, size=None):
    ws.open_desk()
    ws.open_projects()
    _frame(ws)
    win = ws.wm._wins["projects"]
    if size is not None:
        ws.wm._resize_window(win, *size)
        _frame(ws)
    grid = ws.projects_grid
    grid.sel = next(i for i, it in enumerate(grid.items) if it.get("path"))
    _frame(ws)
    return win, grid


def _inside(r, box):
    return (r[0] >= box[0] and r[1] >= box[1] and r[0] + r[2] <= box[0] + box[2]
            and r[1] + r[3] <= box[1] + box[3])


def _apart(a, b):
    return (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1]
            or b[1] + b[3] <= a[1])


def test_the_window_draws_the_console_homes_design(tmp_path):
    """A Carousel of its own on Make, the desk's geometry: the hero, the
    pane beside it with the tab's buttons, the shelf under them."""
    ws = _ws(tmp_path)
    win, grid = _projects(ws)
    assert isinstance(grid, launcher_layer.Carousel) and grid is not ws.launcher
    assert grid.tab == "make"
    ws.wm._install(win.ctx)
    try:
        assert grid.desk(), "the window lays out the desk home, not the handheld's"
        verbs = [v for v, _r, _k in ws.projects_layer._desk_buttons()]
        assert verbs[:2] == ["change", "copy"]
        assert ws.bar_layer._zone_owner("projects") is ws.projects_layer
    finally:
        ws.wm._install(ws.wm._root_ctx)


@pytest.mark.parametrize("size", [None, (640, 528), (420, 500), (360, 534), (520, 340), (700, 400)])
def test_the_window_lays_out_in_its_content_rect_at_any_size(tmp_path, size):
    """Hero, details pane, its buttons, the shelf's band and the hint strip
    lie inside the window's content rect and apart from one another."""
    ws = _ws(tmp_path)
    win, grid = _projects(ws, size)
    ws.wm._install(win.ctx)
    try:
        lay = ws.layout
        content = (lay.cx, lay.cy, lay.cw, lay.ch)
        hero = grid.hero_rect()
        pane = grid.plate_rect()
        band = grid.band_rect()
        hints = grid.foot()[5]
        for name, r in (("hero", hero), ("pane", pane), ("band", band),
                        ("hints", hints)):
            assert _inside(r, content), "%s %r outside %r" % (name, r, content)
        assert _apart(hero, pane), (hero, pane)
        assert hero[1] + hero[3] <= band[1] and pane[1] + pane[3] <= band[1]
        assert band[1] + band[3] <= hints[1]
        buttons = ws.projects_layer._desk_buttons()
        assert buttons, "the pane holds at least its primary button"
        for _v, r, _k in buttons:
            assert _inside(r, pane), (r, pane)
    finally:
        ws.wm._install(ws.wm._root_ctx)


def _geom(cw, ch):
    lay = types.SimpleNamespace(fs=1, cx=0, cy=0, cw=cw, ch=ch)
    c = launcher_layer.Carousel([])
    return c._geom_desk(lay, 4)


def test_a_narrow_tall_window_stands_the_hero_over_its_details():
    """Too narrow for the pane beside the hero and tall enough for both
    stacked: the hero over the pane, the pane the width, the shelf under."""
    g9 = _geom(360, 1000)[9]
    (hx, hy, hero, _h), dx, dw, shelf_y, pad, _m, dy, _rows, _brows = g9
    assert hero >= launcher_layer._COVER_SIDE // 2
    assert dx == hx and dy >= hy + hero and dw == 360 - 2 * pad
    assert shelf_y >= dy + g9[5][5]


def test_a_narrow_short_window_keeps_the_hero_beside_its_details():
    """No height to stack at a hero as large: it stays beside the pane."""
    g9 = _geom(360, 460)[9]
    (hx, hy, hero, _h), dx, _dw, _shelf_y, _pad, _m, dy, _rows, _brows = g9
    assert dx > hx + hero and dy == hy


def test_a_wide_window_is_the_console_homes_layout():
    """At the glass's own width the window is the console home: the pane
    beside a hero a quarter of the width holds."""
    g9 = _geom(1024, 520)[9]
    (hx, hy, hero, _h), dx, _dw, _shelf_y, pad, _m, dy, rows, brows = g9
    assert brows == 0, "every card's buttons on one row"
    assert pad == 48 and dy == hy and dx == hx + hero + 32 and rows == (True, True)


def test_the_window_lies_on_its_panel_never_the_wallpaper(tmp_path):
    """A wallpaper changes the desk around the window, not a pixel inside
    it; an idle window asks for no frame."""
    ws = _ws(tmp_path)
    win, _grid = _projects(ws)
    ws.look.select_wallpaper(None, persist=False)
    _frame(ws)
    plain = bytes(win.buf._buf)
    ws.look.select_wallpaper(ws.look._default_wallpaper(), persist=False)
    _frame(ws)
    assert bytes(win.buf._buf) == plain
    ws.look.select_wallpaper(None, persist=False)
    _frame(ws)
    assert not ws._animating(g._DT)


def test_a_short_window_drops_the_facts_then_the_metadata():
    """The pane gives up rows before it overruns the shelf: the facts
    first, then the metadata."""
    tall, mid, low = (_geom(800, h)[9] for h in (520, 300, 230))
    assert tall[7] == (True, True)
    assert mid[7] == (False, True)
    assert low[7] == (False, False)
    for g9 in (tall, mid, low):
        hy, hero, dy, pane = g9[0][1], g9[0][2], g9[6], g9[5][5]
        assert max(hy + hero, dy + pane) <= g9[3], "the pane runs into the shelf"


def _own(ws, grid):
    """Select a cart of the kid's own on the window's shelf: a copy of Star
    Catcher (a built-in is copied, never deleted)."""
    from runtime.moy_store_base import builtin_name
    star = next(c for c in ws.carts.all if c.get("title") == "Star Catcher")
    ws.carts.dup(star)
    _frame(ws)
    grid.sel = next(i for i, it in enumerate(grid.items)
                    if it.get("path") and builtin_name(it["path"]) is None)
    _frame(ws)


@pytest.mark.parametrize("size", [None, (360, 534)])
def test_make_leads_with_change_and_keeps_every_card_verb(tmp_path, size):
    """On Make the hero's filled button acts on the hero: CHANGE, with
    DUPLICATE and DELETE beside it, every one of them at any width (a button
    that does not fit wraps under, R5-02); NEW is the + tile's primary and
    stays on the strip's N."""
    from runtime import ui_draw
    ws = _ws(tmp_path)
    win, grid = _projects(ws, size)
    _own(ws, grid)
    ws.wm._install(win.ctx)
    was = ui_draw.input_kind()
    try:
        lay = ws.layout
        content = (lay.cx, lay.cy, lay.cw, lay.ch)
        home = ws.projects_layer
        buttons = home._desk_buttons()
        assert [(v, k) for v, _r, k in buttons] == [
            ("change", "primary"), ("copy", "normal"), ("delete", "danger")]
        pane = grid.plate_rect()
        for _v, r, _k in buttons:
            assert _inside(r, content) and _inside(r, pane), (r, pane)
        ui_draw.input_kind("keys")
        _items, verbs = home._hint_groups()
        assert verbs[:2] == ["new", "change"], "the strip keeps N New on Make"
        grid.sel = next(i for i, it in enumerate(grid.items)
                        if it.get("type") == launcher_layer.NEW_TILE_TYPE)
        _frame(ws)
        assert [(v, k) for v, _r, k in home._desk_buttons()] == [("new", "primary")]
    finally:
        ui_draw.input_kind(was)
        ws.wm._install(ws.wm._root_ctx)


def test_wrapped_buttons_take_up_and_down_between_their_rows(tmp_path):
    """Where the buttons wrap, up and down move between their rows before
    the keys go on to the tabs or back to the shelf."""
    ws = _ws(tmp_path)
    win, grid = _projects(ws, (360, 534))
    _own(ws, grid)
    ws.wm._install(win.ctx)
    try:
        home = ws.projects_layer
        buttons = home._desk_buttons()
        assert len({r[1] for _v, r, _k in buttons}) > 1, "the narrow pane wraps"
        assert home._button_rows_apart(buttons, 0) == (None, 1)
        assert home._button_rows_apart(buttons, 1) == (0, 2)
    finally:
        ws.wm._install(ws.wm._root_ctx)


def test_delete_stands_a_groups_gap_apart(tmp_path):
    """DEL is set apart from the verbs before it by a group's gap, wider
    than the gap between Change and Duplicate (R5-15)."""
    ws = _ws(tmp_path)
    win, grid = _projects(ws)
    _own(ws, grid)
    ws.wm._install(win.ctx)
    try:
        (_c, a, _k1), (_y, b, _k2), (_d, c, kind) = ws.projects_layer._desk_buttons()
        assert kind == "danger" and a[1] == b[1] == c[1], "one row at the full size"
        assert c[0] - (b[0] + b[2]) > b[0] - (a[0] + a[2]) > 0
    finally:
        ws.wm._install(ws.wm._root_ctx)
