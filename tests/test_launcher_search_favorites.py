"""Tests for the #105 launcher-feature gaps that are host-side and decision-free:
search/filter across the run-grid, favorites (a corner-star badge toggle on the
selected card), and recents (the issue's own `desk_mru` system.json naming note).

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver: mouse == touch, arrows == trackball, type_char == a typed ASCII
byte), so these assert host==device behavior, not a host-only path."""

from pathlib import Path
from ws_helpers import shelf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


from ws_helpers import build_ws as _ws


def _real_items(ws):
    return [it for it in ws.launcher.items if it.get("path")]


def _find(ws, title):
    return next(it for it in ws.launcher.items if it.get("title") == title)


# -- search / filter ---------------------------------------------------------

def test_search_filters_the_grid(tmp_path):
    ws = _ws(tmp_path)
    full = list(ws.launcher.items)
    assert len(_real_items(ws)) > 1        # a real library to search over

    ws.set_search_query("star")
    titles = [it.get("title") for it in ws.launcher.items if it.get("path")]
    assert titles == ["Star Catcher"]
    assert len(ws.launcher.items) < len(full)


def test_search_is_case_insensitive_substring(tmp_path):
    ws = _ws(tmp_path)
    ws.set_search_query("STAR")
    assert [it["title"] for it in _real_items(ws)] == ["Star Catcher"]
    ws.set_search_query("cat")               # substring, mid-word
    assert [it["title"] for it in _real_items(ws)] == ["Star Catcher"]


def test_search_no_matches_empties_the_tab_but_keeps_the_new_card(tmp_path):
    ws = _ws(tmp_path)
    ws.set_search_query("zzzznotacart")
    assert ws.launcher.items == []             # Games: no match, no card
    ws.set_home_tab("make")                    # the filter follows the tab...
    assert len(ws.launcher.items) == 1         # ...and "+ New" survives it
    assert ws.launcher.items[0].get("type") == "new"


def test_empty_query_restores_the_full_grid(tmp_path):
    ws = _ws(tmp_path)
    before = [it.get("path") for it in ws.launcher.items]
    ws.set_search_query("star")
    assert [it.get("path") for it in ws.launcher.items] != before
    ws.set_search_query("")
    assert [it.get("path") for it in ws.launcher.items] == before


def test_a_rescan_reapplies_the_active_search_filter(tmp_path):
    """A create/dup/delete re-syncs the grid from a fresh scan (carts.apply) --
    an active search must survive that resync, not silently revert to the full
    list."""
    ws = _ws(tmp_path)
    ws.set_search_query("star")
    ws.carts.apply(ws.carts.all)   # the shape every cart-management verb calls
    assert [it["title"] for it in _real_items(ws)] == ["Star Catcher"]


def test_sysmenu_search_row_opens_typing_and_filters_live(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    assert ws.screen == "launcher"
    ws.toggle_sysmenu()
    labels = [it[1] for it in ws.sysmenu.items if it[0] == "item"]
    assert "SEARCH" in labels
    drv.press("a"); drv.frame(1 / 30)          # activates the first selectable row (SEARCH)
    assert ws.search_typing is True
    assert ws.input.text_mode is True
    assert not ws.sysmenu.open

    for ch in "star":
        drv.type_char(ord(ch))
        drv.frame(1 / 30)
    assert ws.search_query == "star"
    assert [it["title"] for it in _real_items(ws)] == ["Star Catcher"]

    drv.type_char(8); drv.frame(1 / 30)        # BACKSPACE
    assert ws.search_query == "sta"

    drv.type_char(13); drv.frame(1 / 30)       # ENTER: stop typing, keep the filter
    assert ws.search_typing is False
    assert ws.input.text_mode is False
    assert ws.search_query == "sta"
    assert [it["title"] for it in _real_items(ws)] == ["Star Catcher"]

    # The sysmenu row now offers to clear it.
    ws.toggle_sysmenu()
    labels = [it[1] for it in ws.sysmenu.items if it[0] == "item"]
    assert "CLEAR SEARCH" in labels
    drv.press("a"); drv.frame(1 / 30)
    assert ws.search_query == ""
    assert len(_real_items(ws)) > 1


def test_search_esc_cancels_and_restores_the_full_grid(tmp_path):
    ws = _ws(tmp_path)
    full_n = len(_real_items(ws))
    ws.open_search()
    ws.set_search_query("star")
    assert len(_real_items(ws)) == 1
    ws.close_search(clear=True)
    assert ws.search_query == ""
    assert ws.search_typing is False
    assert len(_real_items(ws)) == full_n


def test_search_row_is_absent_off_the_launcher(tmp_path):
    """SEARCH only makes sense over the run-grid -- Settings/the picker/etc. don't
    offer it."""
    ws = _ws(tmp_path)
    ws.open_settings()
    ws.toggle_sysmenu()
    labels = [it[1] for it in ws.sysmenu.items if it[0] == "item"]
    assert "SEARCH" not in labels and "CLEAR SEARCH" not in labels


# -- favorites -----------------------------------------------------------

def test_favorite_toggle_and_query(tmp_path):
    ws = _ws(tmp_path)
    cart = _find(ws, "Star Catcher")
    assert ws.carts.is_favorite(cart) is False
    ws.carts.toggle_favorite(cart)
    assert ws.carts.is_favorite(cart) is True
    ws.carts.toggle_favorite(cart)
    assert ws.carts.is_favorite(cart) is False


def test_favorite_persists_across_reboot(tmp_path):
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    cart = _find(ws, "Star Catcher")
    ws.carts.toggle_favorite(cart)
    assert moy_carts.load_system(carts_dir).get("favorites") == [cart["path"]]

    ws2 = host_app.build_workstation(carts_dir)
    cart2 = _find(ws2, "Star Catcher")
    assert ws2.carts.is_favorite(cart2) is True


def test_favorite_star_badge_tap_toggles_without_launching(tmp_path):
    """Uses the first real (visible-at-boot-scroll) card -- favorite_rect returns
    None for a card scrolled out of the shelf viewport, same as action_rects."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    idx = next(i for i, it in enumerate(ws.launcher.items) if it.get("path"))
    ws.launcher.sel = idx
    cart = ws.launcher.selected()
    frect = ws.launcher.favorite_rect(ws.launcher.sel)
    assert frect is not None
    x, y, w, h = frect
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.screen == "launcher"             # the badge tap must not launch the cart
    assert ws.carts.is_favorite(cart) is True


def test_favorite_rect_is_none_for_the_new_card(tmp_path):
    ws = _ws(tmp_path)
    ws.set_home_tab("make")
    ws.launcher.sel = 0                        # Make's "+ New" pseudo card
    assert ws.launcher.selected().get("type") == "new"
    assert ws.launcher.favorite_rect(0) is None


# -- recents (desk_mru) ---------------------------------------------------

def test_recent_carts_ordering_after_runs(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    star = _find(ws, "Star Catcher")
    battle = _find(ws, "Brick Siege")

    ws.launcher.sel = ws.launcher.items.index(star)
    ws.launch_selected()
    assert ws.system.get("desk_mru") == [star["path"]]
    ws.go_home()

    ws.launcher.sel = ws.launcher.items.index(battle)
    ws.launch_selected()
    assert ws.system.get("desk_mru") == [battle["path"], star["path"]]
    ws.go_home()

    recent = ws.carts.recent()
    assert [c["title"] for c in recent] == ["Brick Siege", "Star Catcher"]


def test_rerunning_a_cart_moves_it_to_the_front_without_duplicating(tmp_path):
    ws = _ws(tmp_path)
    star = _find(ws, "Star Catcher")
    battle = _find(ws, "Brick Siege")

    for cart in (star, battle, star):
        ws.launcher.sel = ws.launcher.items.index(cart)
        ws.launch_selected()
        ws.go_home()

    assert ws.system.get("desk_mru") == [star["path"], battle["path"]]


def test_recents_persist_across_reboot(tmp_path):
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    star = _find(ws, "Star Catcher")
    ws.launcher.sel = ws.launcher.items.index(star)
    ws.launch_selected()

    ws2 = host_app.build_workstation(carts_dir)
    assert ws2.system.get("desk_mru") == [star["path"]]
    assert [c["title"] for c in ws2.carts.recent()] == ["Star Catcher"]


def test_recents_cap_at_mru_limit(tmp_path):
    from runtime import moy_carts, host_app
    carts_dir = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts_dir)
    ws = host_app.build_workstation(carts_dir)
    # Pad the library with enough synthetic games to exceed the recents cap.
    n = ws.carts._MRU_CAP + 3
    for k in range(n):
        moy_carts.create("Pad%d" % k, carts_dir, src="def _draw():\n    cls(1)\n",
                          type="game")
    ws.carts.apply(shelf(carts_dir))
    ws.set_home_tab("mine")                    # made here: the kid's own
    pads = [it for it in ws.launcher.items if it.get("title", "").startswith("Pad")]
    assert len(pads) == n
    for it in pads:
        ws.launcher.sel = ws.launcher.items.index(it)
        ws.launch_selected()
        ws.go_home()

    assert len(ws.system.get("desk_mru")) == ws.carts._MRU_CAP
    # Newest-first: the last cart run is at the front.
    assert ws.system.get("desk_mru")[0] == pads[-1]["path"]


def test_recent_carts_skips_a_deleted_cart(tmp_path):
    ws = _ws(tmp_path)
    star = _find(ws, "Star Catcher")
    ws.launcher.sel = ws.launcher.items.index(star)
    ws.launch_selected()
    ws.go_home()
    assert ws.carts.recent()                    # sanity: recorded

    ws.system.set("desk_mru", ["nonexistent/path"] + ws.system.get("desk_mru"))
    assert all(c["path"] != "nonexistent/path" for c in ws.carts.recent())


# -- the star without a pointer (R2-12) ----------------------------------------

def _on_real_card(ws):
    idx = next(i for i, it in enumerate(ws.launcher.items) if it.get("path"))
    ws.launcher.sel = idx
    return ws.launcher.selected()


def test_f_toggles_the_selected_cards_star(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    cart = _on_real_card(ws)
    drv.type_char(ord("f")); drv.frame(1 / 30)
    assert ws.screen == "launcher"
    assert ws.carts.is_favorite(cart) is True
    drv.frame(1 / 30)
    drv.type_char(ord("F")); drv.frame(1 / 30)
    assert ws.carts.is_favorite(cart) is False


class _PadY:
    """A frame's input with a gamepad's Y pressed (the boards' fifteen-name
    vocabulary; the host's keyboard has no Y)."""

    def pressed(self, name):
        return name == "y"


def test_a_gamepads_y_toggles_the_star(tmp_path):
    ws = _ws(tmp_path)
    cart = _on_real_card(ws)
    assert ws.launcher_layer.handle_input(_PadY())
    assert ws.screen == "launcher"
    assert ws.carts.is_favorite(cart) is True


def test_the_new_card_takes_no_star_by_key(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.set_home_tab("make")
    ws.launcher.sel = 0
    before = list(ws.system.get("favorites", []))
    drv.type_char(ord("f")); drv.frame(1 / 30)
    assert ws.system.get("favorites", []) == before


def test_the_system_menu_over_the_home_stars_the_selected_card(tmp_path):
    ws = _ws(tmp_path)
    cart = _on_real_card(ws)
    ws.toggle_sysmenu()
    items = ws.sysmenu.items
    labels = [it[1] for it in items if it[0] == "item"]
    assert labels[0] == "SEARCH", "the menu still opens on the system's rows"
    assert labels[-1] == "ADD FAVOURITE"
    row = [it for it in items if it[0] == "item"][-1]
    row[2]()
    assert ws.carts.is_favorite(cart) is True
    ws.toggle_sysmenu()
    if not ws.sysmenu.open:
        ws.toggle_sysmenu()
    labels = [it[1] for it in ws.sysmenu.items if it[0] == "item"]
    assert labels[-1] == "REMOVE FAVOURITE"


def test_the_system_menu_offers_no_star_off_the_home(tmp_path):
    ws = _ws(tmp_path)
    ws.open_settings()
    ws.toggle_sysmenu()
    labels = [it[1] for it in ws.sysmenu.items if it[0] == "item"]
    assert "ADD FAVOURITE" not in labels and "REMOVE FAVOURITE" not in labels
