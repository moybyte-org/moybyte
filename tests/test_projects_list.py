"""One projects list (docs/shell_layout_2026-10.md §12, the owner's rulings of
2026-10-14): the home's Make tab is the only list of projects. There is no
project picker on any tier and no PROJECTS button on the Editor's bar; the
Editor's X backs out one level, to wherever it was opened from. In desktop
mode the desk's PROJECTS opens the SAME home in a window -- always its grid
arrangement, on Make -- and every pick in it opens what it opens as a window.

These drive the shared console the device runs (runtime.host_app) through
ConsoleDriver -- mouse == touch, arrows == trackball.
"""

from ws_helpers import build_desktop_ws, build_ws as _ws, shelf  # noqa: E402
import pytest
from ws_helpers import boot_on_fixture

@pytest.fixture(autouse=True)
def _on_the_night_fixture(monkeypatch):
    """These pin the lookless pixel bar and its coordinates: every
    workstation here boots on the `night` fixture (ws_helpers)."""
    boot_on_fixture(monkeypatch)


def _first_real(grid):
    return next(i for i, it in enumerate(grid.items) if it.get("path"))


def _drv(ws):
    from runtime import host_app
    return host_app.ConsoleDriver(ws)


class _Keys:
    """`i.pressed(name)` for driving a layer's handle_input directly."""

    def __init__(self, pressed):
        self._pressed = set(pressed)
        self.last_key = 0

    def pressed(self, name):
        return name in self._pressed


# -- the Make tab's list -----------------------------------------------------

def test_make_lists_the_new_card_then_every_editable_cart(tmp_path):
    from runtime.launcher_layer import NEW_TILE_TYPE
    ws = _ws(tmp_path)
    ws.set_home_tab("make")
    items = ws.launcher.items
    assert items[0]["type"] == NEW_TILE_TYPE and items[0]["path"] is None
    real = [c for c in items if c.get("path")]
    # Every editable cart is listed -- games AND wallpapers...
    assert {"game", "wallpaper"} <= {c.get("type") for c in real}
    # ...MINUS the carts a shell APP claims as its identity (Files/Paint/...):
    # TEMPORARY until #181 makes the apps real carts.
    claimed = [c for c in ws.carts.all
               if any(app.is_app(c) for app, _t in getattr(ws, "_apps", ()))]
    assert claimed, "fixture has no app carts -- the exclusion is untested"
    listed = {id(c) for c in real}
    for cart in claimed:
        assert id(cart) not in listed, cart.get("title")
    assert len(real) == len(ws.carts.all) - len(claimed)


def test_wallpapers_leave_the_tabs_that_run(tmp_path):
    """Wallpapers are a backdrop (shell_ux_v1.md): absent from Games, Mine and
    Apps, present on Make (editable) and in Settings (chosen as a backdrop)."""
    ws = _ws(tmp_path)
    for tab in ("games", "mine", "apps"):
        ws.set_home_tab(tab)
        assert all(c.get("type") != "wallpaper" for c in ws.launcher.items), tab
    ws.set_home_tab("make")
    assert any(c.get("type") == "wallpaper" for c in ws.launcher.items)
    store_wp = {c["path"] for c in ws.carts.all if c.get("type") == "wallpaper"}
    assert store_wp == {c["path"] for c in ws.look.wallpaper_carts()}


def test_projects_without_a_desk_is_the_home_make_tab(tmp_path):
    """The dev channel's `open projects` on a console-only board (or in
    console mode) lands on the home's Make tab: no window, no second home."""
    ws = _ws(tmp_path)
    ws.open_settings()
    ws.open_projects()
    assert ws.screen == "launcher" and ws.launcher.tab == "make"
    assert ws.projects_grid is None


def test_no_picker_survives(tmp_path):
    """The picker is gone on every tier: no grid, no layer, no kind, no verb."""
    ws = _ws(tmp_path)
    for name in ("picker", "editor_picker", "open_picker", "pick_selected"):
        assert not hasattr(ws, name), name
    assert "picker" not in ws._content_layers
    assert ws.projects_grid is None          # minted by the desk's PROJECTS only


# -- a tap on Make edits; the Editor's X backs out one level -------------------

def test_a_tap_on_the_make_tab_edits(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.set_home_tab("make")
    ws.launcher.sel = _first_real(ws.launcher)  # at the centre
    target = ws.launcher.selected()
    drv.frame(1 / 30)
    x, y, w, h = ws.launcher.tile_rect(ws.launcher.sel)
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "cards"
    assert ws.cart.get("path") == target.get("path")


def test_new_card_creates_a_game_and_opens_the_editor(tmp_path):
    from runtime.launcher_layer import NEW_TILE_TYPE
    ws = _ws(tmp_path)
    drv = _drv(ws)
    n0 = len(ws.carts.all)
    ws.set_home_tab("make")
    ws.launcher.sel = 0
    assert ws.launcher.selected()["type"] == NEW_TILE_TYPE
    drv.frame(1 / 30)
    x, y, w, h = ws.launcher.tile_rect(0)
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.screen == "menu"
    assert ws.cart is not None and ws.cart.get("type") == "game"
    assert len(ws.carts.all) == n0 + 1
    # Backing out lands on the new project's card.
    path = ws.cart.get("path")
    ws.exit()
    assert ws.screen == "launcher" and ws.launcher.tab == "make"
    assert ws.launcher.selected().get("path") == path


def test_the_editor_bar_has_no_projects_button(tmp_path):
    from runtime import editor_app as EA
    assert all(t != "projects" and g != "projects" for t, g in EA._ZONE_TABS)
    ws = _ws(tmp_path)
    ws.set_home_tab("make")
    ws.launcher.sel = _first_real(ws.launcher)
    ws.launch_selected()
    rect = ws.layout.zone_left
    glyphs = [g for _t, g, _r in ws.editor_app._ladder_rects(rect)]
    assert "projects" not in glyphs


def test_the_narrow_bar_names_the_open_tab_only_at_the_full_gap(tmp_path):
    """The narrow bar's one rule: its icons never abut, and the open tab's
    name goes before the icons squeeze. Every icon fits the zone, evenly
    gapped; the open tab is named exactly where the whole ladder holds it
    at the full gap, as a wider zone does."""
    from runtime import editor_app as EA
    ws = _ws(tmp_path)
    ws.set_home_tab("make")
    ws.launcher.sel = _first_real(ws.launcher)
    ws.launch_selected()
    ea = ws.editor_app
    rect = ws.layout.zone_left
    full = EA._LADDER_GAP * ea._zone_scale()
    for tab, name in (("cards", "Config"), ("paint", "Sprites"), ("code", "Code")):
        ws.set_menu_view(tab)
        for zone in (rect, (rect[0], rect[1], rect[2] + 80, rect[3])):
            rects, gap = ea._ladder_layout(zone)
            assert len(rects) == len(ea._ladder())       # nothing dropped
            assert rects[-1][2][0] + rects[-1][2][2] <= zone[0] + zone[2]
            cut = ea._group_cut(rects)
            gaps = [b[2][0] - a[2][0] - a[2][2] for a, b in zip(rects, rects[1:])]
            group = gaps.pop(cut - 1)                    # tabs | actions (D-02)
            assert rects[cut][0] == EA._ZONE_UNDO
            assert set(gaps) == {gap} and gap > 0
            assert EA._GROUP_GAP * ea._zone_scale() <= group
            assert group <= EA._GROUP_MAX * ea._zone_scale()
            label = ea._open_label(zone)
            assert label in ("", name)
            if label:
                assert gap == full
        assert ea._open_label((rect[0], rect[1], rect[2] + 80, rect[3])) == name


def test_the_editor_x_returns_to_the_tab_it_came_from(tmp_path):
    """CHANGE works from any tab, so the Editor's X is the back-stack's pop:
    the home on the tab and card the Editor was opened from."""
    ws = _ws(tmp_path)
    for tab in ("games", "make"):
        ws.set_home_tab(tab)
        ws.launcher.sel = _first_real(ws.launcher)
        want = ws.launcher.selected().get("path")
        if tab == "make":
            ws.launch_selected()
        else:
            ws.change_selected()
        assert ws.screen == "menu", tab
        assert ws.wm.stack.kinds() == ["launcher", "menu"], tab
        ws.exit()
        assert ws.screen == "launcher" and ws.launcher.tab == tab
        assert ws.launcher.selected().get("path") == want


# -- COPY and DEL moved onto Make -------------------------------------------

def _make_with(ws, title):
    ws.set_home_tab("make")
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("title") == title)


def _verb_tap(ws, layer, verb):
    rects = layer._zone_action_rects(ws.layout.zone_left)
    x, y, w, h = rects[verb]
    return layer.zone_tap(x + w // 2, y + h // 2)


def _extra(ws, title="Extra"):
    """A project of the kid's own on the shelf (a built-in is copied, never
    deleted)."""
    from runtime import moy_carts
    moy_carts.create(title, str(ws.carts_root), src="def _draw():\n    cls(1)\n",
                     type="game")
    ws.carts.apply(shelf(str(ws.carts_root)))


def test_make_carries_copy_and_delete(tmp_path):
    ws = _ws(tmp_path, sys_size=(800, 480))
    _extra(ws)
    layer = ws.launcher_layer
    ws.set_home_tab("make")
    ws.launcher.sel = 0
    assert layer.zone_verbs() == ("new",)            # the "+ New" card
    _make_with(ws, "Extra")
    assert layer.zone_verbs() == ("new", "change", "copy", "delete")


def test_copy_duplicates_the_selected_project(tmp_path):
    ws = _ws(tmp_path, sys_size=(800, 480))
    ws.set_home_tab("make")
    ws.launcher.sel = _first_real(ws.launcher)
    target = ws.launcher.selected()
    n0 = len(ws.carts.all)
    assert _verb_tap(ws, ws.launcher_layer, "copy")
    assert len(ws.carts.all) == n0 + 1
    assert any(c["title"] == target["title"] + " copy" for c in ws.carts.all)


def test_delete_is_two_tap_guarded(tmp_path):
    from runtime import moy_carts
    ws = _ws(tmp_path, sys_size=(800, 480))
    moy_carts.create("Extra", str(ws.carts_root), src="def _draw():\n    cls(1)\n",
                     type="game")
    ws.carts.apply(shelf(str(ws.carts_root)))
    layer = ws.launcher_layer
    _make_with(ws, "Extra")
    n0 = len(ws.carts.all)
    assert _verb_tap(ws, layer, "delete")
    assert len(ws.carts.all) == n0                     # armed, not deleted
    assert layer.zone_verbs()[-1] == "sure"
    assert _verb_tap(ws, layer, "sure")
    assert len(ws.carts.all) == n0 - 1
    assert all(c.get("title") != "Extra" for c in ws.carts.all)


def test_delete_disarms_when_the_selection_moves(tmp_path):
    ws = _ws(tmp_path, sys_size=(800, 480))
    _extra(ws)
    _extra(ws, "Extra Two")
    layer = ws.launcher_layer
    _make_with(ws, "Extra")
    _verb_tap(ws, layer, "delete")
    assert layer.zone_verbs()[-1] == "sure"
    _make_with(ws, "Extra Two")
    assert layer.zone_verbs()[-1] == "delete"


# -- desktop mode: the home in a window ------------------------------------

def _desk(tmp_path):
    ws = build_desktop_ws(tmp_path)
    drv = _drv(ws)
    ws.open_desk()
    drv.frame(1 / 30)
    return ws, drv


def test_projects_opens_the_home_in_a_window_on_make(tmp_path):
    from runtime.launcher_layer import Carousel, Launcher
    ws, drv = _desk(tmp_path)
    assert isinstance(ws.launcher, Carousel)          # the fullscreen home's own
    ws.wm._backdrop_layer._open_icon("projects")      # the desk's icon
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop"
    assert ws.wm._order == ["projects"]
    g = ws.projects_grid
    assert type(g) is Launcher and g.tab == "make"   # the grid, on Make
    assert ws._content_layers["projects"] is ws.projects_layer
    # Switching the window's tab leaves the fullscreen home's alone.
    tab = ws.launcher.tab
    ws.set_home_tab("games", g)
    assert ws.launcher.tab == tab
    # A second PROJECTS brings it back to Make.
    ws.open_projects()
    assert g.tab == "make" and ws.wm._order == ["projects"]


def test_projects_window_is_a_normal_window(tmp_path):
    """It moves, resizes, minimises to a chip; its X closes it and never
    switches modes."""
    ws, drv = _desk(tmp_path)
    ws.open_projects()
    drv.frame(1 / 30)
    win = ws.wm._wins["projects"]
    lay = win.ctx.layout
    assert (lay.w, lay.h) == (win.content_rect()[2], win.content_rect()[3])
    ws.wm.close_window_kind("projects")
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop" and ws.wm._order == []
    assert ws.screen == "desk"


def test_a_make_pick_opens_the_editor_in_its_own_window(tmp_path):
    ws, drv = _desk(tmp_path)
    ws.open_projects()
    g = ws.projects_grid
    g.sel = _first_real(g)
    want = g.selected().get("path")
    ws.launch_selected(g)
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop"
    assert ws.wm._order == ["projects", "menu"]
    assert ws.cart.get("path") == want
    # The Editor window's X closes it, back to the home window.
    ws.wm.close_window_kind("menu")
    drv.frame(1 / 30)
    assert ws.wm._order == ["projects"] and ws.screen == "projects"


def test_a_games_pick_runs_in_a_window(tmp_path):
    ws, drv = _desk(tmp_path)
    ws.open_projects()
    g = ws.projects_grid
    ws.set_home_tab("games", g)
    g.sel = _first_real(g)
    ws.launch_selected(g)
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop"
    assert ws.wm._order == ["projects", "desktop"]   # the player's window
    assert ws.cart is not None


def test_an_apps_pick_opens_its_app_window(tmp_path):
    ws, drv = _desk(tmp_path)
    ws.open_projects()
    g = ws.projects_grid
    ws.set_home_tab("apps", g)
    i = next(i for i, it in enumerate(g.items)
             if ws._apps_by_id["calc"].is_app(it))
    g.sel = i
    ws.launch_selected(g)
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop"
    assert ws.wm._order == ["projects", "calc"]


def test_the_window_draws_through_the_window_bar(tmp_path):
    """The window's bar is its lent zone alone (no OS widgets in a window);
    the zone owner is the window's home, with the home's verbs, no SEARCH."""
    ws, drv = _desk(tmp_path)
    ws.open_projects()
    drv.frame(1 / 30)
    assert ws.bar_layer._zone_owner("projects") is ws.projects_layer
    g = ws.projects_grid
    ws.set_home_tab("games", g)
    g.sel = _first_real(g)
    assert "search" not in ws.projects_layer.zone_verbs()
    ws.set_home_tab("make", g)
    g.sel = 0
    assert ws.projects_layer.zone_verbs() == ("new",)


def test_b_closes_the_window_alone(tmp_path):
    ws, drv = _desk(tmp_path)
    ws.open_app(ws._apps_by_id["calc"])
    ws.open_projects()
    drv.frame(1 / 30)
    assert ws.wm._order == ["calc", "projects"]
    ws.projects_layer.handle_input(_Keys({"b"}))
    drv.frame(1 / 30)
    assert ws.wm.mode == "desktop" and ws.wm._order == ["calc"]
