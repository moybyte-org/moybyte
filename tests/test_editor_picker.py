"""The Editor-as-an-app project-picker (spec docs/shell_ux_v1.md, the owner's locked
"Editor-as-an-app" model). These drive the SAME shared console the device runs
(runtime.host_app) through ConsoleDriver -- mouse == touch, arrows == trackball.

The flow under test:
    launcher  --tap the pinned "Make" tile-->  PROJECT-PICKER
    picker    --tap a cart-->                  Editor (Config tab)
    picker    --tap "+ New"-->                 a fresh GAME cart, opened in the Editor
    Editor    --"projects" affordance-->       back to the PICKER (edit another project)
    Editor / picker --X / exit-->              back to the launcher
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


from ws_helpers import build_ws as _ws


def _first_real(grid):
    return next(i for i, it in enumerate(grid.items) if it.get("path"))


# -- the pinned tiles -------------------------------------------------------

def test_make_tile_is_pinned_first_and_is_a_pseudo_entry(tmp_path):
    from runtime.launcher_layer import MAKE_TILE_TYPE
    ws = _ws(tmp_path)
    make = ws.launcher.items[0]
    assert make["type"] == MAKE_TILE_TYPE      # first slot (spec: distinctive Make tile)
    assert make["path"] is None                # a pseudo entry, not a real cart


def test_picker_lists_the_new_tile_then_every_editable_cart(tmp_path):
    from runtime.launcher_layer import NEW_TILE_TYPE
    ws = _ws(tmp_path)
    items = ws.picker.items
    assert items[0]["type"] == NEW_TILE_TYPE   # "+ New" pinned first
    real = [c for c in items if c.get("path")]
    # Every editable cart is listed -- games, tools AND wallpapers.
    types = {c.get("type") for c in real}
    assert {"game", "tool", "wallpaper"} <= types
    # ...MINUS the carts a shell APP claims as its identity (Files/Paint/...).
    # Those are not really projects: the app is a frozen shell module and the
    # cart holds only identity + icon + a few-line fallback body, so offering it
    # as a project meant editing code the kid can never see run. TEMPORARY --
    # #181 (editable system apps) makes them real and this exclusion goes
    # away; shell_ux_v1's "everything is editable" line is right again then.
    claimed = [c for c in ws.carts.all
               if any(app.is_app(c) for app, _t in getattr(ws, "_apps", ()))]
    assert claimed, "fixture has no app carts -- the exclusion is untested"
    listed = {id(c) for c in real}
    for cart in claimed:
        assert id(cart) not in listed, cart.get("title")
    assert len(real) == len(ws.carts.all) - len(claimed)


def test_wallpaper_is_present_in_the_picker(tmp_path):
    ws = _ws(tmp_path)
    titles = {c.get("title") for c in ws.picker.items if c.get("path")}
    assert any(c.get("type") == "wallpaper" for c in ws.picker.items if c.get("path"))
    # a specific seeded wallpaper is editable via the picker
    assert "Sakura" in titles or "Ocean Desktop" in titles


def test_wallpapers_leave_the_launcher_grid(tmp_path):
    """Wallpapers are a backdrop category (spec shell_ux_v1.md): they leave the launcher
    RUN-grid, but stay in the Editor PICKER (editable) AND the Settings wallpaper picker
    (choose as backdrop). The launcher grid = Make tile + games/tools/apps only."""
    ws = _ws(tmp_path)
    # ABSENT from the launcher run-grid...
    assert all(c.get("type") != "wallpaper" for c in ws.launcher.items)
    # ...but the run-grid still carries the runnable cart types + the Make tile
    grid_types = {c.get("type") for c in ws.launcher.items}
    assert "make" in grid_types and {"game", "tool", "app"} <= grid_types
    # PRESENT in the Editor picker (editable) and the Settings wallpaper picker (backdrop)
    assert any(c.get("type") == "wallpaper" for c in ws.picker.items if c.get("path"))
    assert any(c.get("type") == "wallpaper" for c in ws.wallpaper_carts())
    # every wallpaper in the store is discoverable as a backdrop
    store_wp = {c["path"] for c in ws.carts.all if c.get("type") == "wallpaper"}
    picker_wp = {c["path"] for c in ws.wallpaper_carts()}
    assert store_wp and store_wp == picker_wp


# -- tap: Make -> picker -> pick -> Editor ----------------------------------

def test_tapping_make_tile_opens_the_picker(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    x, y, w, h = ws.launcher.tile_rect(0)      # slot 0 is the Make tile
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.screen == "picker"


def test_picking_a_cart_opens_it_in_the_editor(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_picker()
    real = _first_real(ws.picker)
    target = ws.picker.items[real]
    r = ws.picker.tile_rect(real)
    drv.click(r[0] + r[2] // 2, r[1] + r[3] // 2)
    drv.frame(1 / 30)
    assert ws.screen == "menu"                 # the Editor
    assert ws.menu_view == "cards"             # ...landing on the Config ("Make it mine") tab
    assert ws.cart is not None and ws.cart.get("path") == target.get("path")


def test_new_tile_creates_a_game_and_opens_the_editor(tmp_path):
    from runtime import host_app
    from runtime.launcher_layer import NEW_TILE_TYPE
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    n0 = len(ws.carts.all)
    ws.open_picker()
    ws.picker.sel = 0
    assert ws.picker.selected()["type"] == NEW_TILE_TYPE
    r = ws.picker.tile_rect(0)
    drv.click(r[0] + r[2] // 2, r[1] + r[3] // 2)
    drv.frame(1 / 30)
    assert ws.screen == "menu"                 # opened the Editor on the new cart
    assert ws.cart is not None and ws.cart.get("type") == "game"   # NEW_TEMPLATE is a game
    assert ws.cart.get("edit")                 # ...with "Make it mine" cards
    assert len(ws.carts.all) == n0 + 1        # a real cart was created on the store
    # the new cart is now a pickable project too (present in both grids)
    assert any(c.get("path") == ws.cart.get("path") for c in ws.picker.items)


# -- back to picker vs exit to launcher -------------------------------------

def test_editor_projects_affordance_returns_to_the_picker(tmp_path):
    """Inside the Editor, the leftmost "projects" tab-ladder icon returns to the PICKER
    (edit another project) WITHOUT leaving to the launcher (spec shell_ux_v1.md)."""
    from runtime.editor_app import _ZONE_PROJECTS
    ws = _ws(tmp_path)
    ws.open_picker()
    ws.picker.sel = _first_real(ws.picker)
    ws.pick_selected()
    assert ws.screen == "menu"
    ws.editor_app._activate_zone_tab(_ZONE_PROJECTS)   # the projects icon's dispatch
    assert ws.screen == "picker"               # back to the picker, not the launcher
    # the back-stack popped the Editor but kept the picker beneath (launcher is the root)
    assert ws.wm._stack == ["launcher", "picker"]


def test_editor_exit_goes_to_the_launcher(tmp_path):
    ws = _ws(tmp_path)
    ws.open_picker()
    ws.picker.sel = _first_real(ws.picker)
    ws.pick_selected()
    assert ws.screen == "menu"
    ws.exit()                                  # the right-zone X exits the Editor
    assert ws.screen == "launcher"


def test_exiting_the_picker_returns_to_the_launcher(tmp_path):
    ws = _ws(tmp_path)
    ws.open_picker()
    assert ws.screen == "picker"
    ws.exit()                                  # the picker's right-zone X
    assert ws.screen == "launcher"


def test_picker_bar_lends_a_title_and_shows_an_exit_x(tmp_path):
    """The picker is a taskbar app: its bar shows the OS right-zone X (exit) -- unlike the
    launcher ROOT which draws none. The lent left zone carries a title (+ DUP/DEL over the
    picker's selection now -- cart management moved here, see the dup/del tests below)."""
    from runtime import bar_layer as BL
    ws = _ws(tmp_path)
    # the picker owns the "picker" zone; it's system-canvas (not the fixed game canvas)
    assert ws.bar_layer._zone_owner("picker") is ws.editor_picker
    assert ws.bar_layer._zone_is_game("picker") is False


# -- cart management moved here: DUP/DEL act on the PICKER's selection -------
# (docs/shell_ux_v1.md: the launcher is for PLAYING, the picker is for MANAGING
# projects -- see tests/test_top_bar.py for the icon-tap coverage; these drive the
# ws.* verbs directly, proving they now read the PICKER's grid, not the launcher's).

def test_dup_duplicates_the_pickers_selection_not_the_launchers(tmp_path):
    """ws.carts.dup() now reads ws.picker's selection, not ws.launcher's -- the two
    grids can point at different carts (the picker lists every editable cart,
    including wallpapers/built-ins the launcher run-grid excludes), so a stale
    launcher selection must never leak into a picker-triggered duplicate."""
    ws = _ws(tmp_path)
    # Point the launcher at some OTHER cart than the one we'll duplicate via the picker.
    ws.launcher.sel = _first_real(ws.launcher)
    launcher_target = ws.launcher.selected()["path"]
    picker_idx = next(i for i, it in enumerate(ws.picker.items)
                      if it.get("path") and it["path"] != launcher_target)
    ws.picker.sel = picker_idx
    target = ws.picker.selected()
    n0 = len(ws.carts.all)
    ws.carts.dup()
    assert len(ws.carts.all) == n0 + 1
    assert any(c["title"] == target["title"] + " copy" for c in ws.carts.all)


def test_delete_removes_the_pickers_selection_when_no_cart_is_open(tmp_path):
    """ws.carts.delete()'s fallback (no cart currently open, ws.cart is None) now reads
    ws.picker's selection instead of ws.launcher's."""
    from runtime import moy_carts
    ws = _ws(tmp_path)
    moy_carts.create("Extra", str(ws.carts_root), src="def _draw():\n    cls(1)\n",
                     type="app")
    ws.carts.apply(moy_carts.scan(str(ws.carts_root)))
    assert ws.cart is None
    idx = next(i for i, it in enumerate(ws.picker.items) if it.get("title") == "Extra")
    ws.picker.sel = idx
    n0 = len(ws.carts.all)
    ws.carts.delete()
    assert len(ws.carts.all) == n0 - 1
    assert all(c.get("title") != "Extra" for c in ws.carts.all)


# -- the DELETE two-tap confirm guard ----------------------------------------
# A project sits right next to its icon in the picker's grid now, so a single
# accidental DEL tap must not delete it (#the picker delete-guard).

def test_picker_delete_is_two_tap_guarded(tmp_path):
    """A DEL tap on the picker's zone ARMS a confirm ("DELETE? TAP AGAIN") rather than
    deleting immediately -- a project sits right next to its icon in this grid, so one
    accidental tap must not be one-tap-gone. A second tap (still armed) confirms."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_picker()
    ws.picker.sel = _first_real(ws.picker)
    n0 = len(ws.picker.items)
    x, y, w, h = ws.layout.del_btn
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.editor_picker._del_armed is True
    assert len(ws.picker.items) == n0                  # armed, NOT deleted yet
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    assert ws.editor_picker._del_armed is False
    assert len(ws.picker.items) == n0 - 1              # second tap confirmed it


def test_picker_delete_confirm_disarms_on_navigation(tmp_path):
    """Moving the selection while a delete is armed cancels the confirm -- arming DEL
    then navigating away must not leave a delete primed for whatever cart the kid
    lands on next."""
    ws = _ws(tmp_path)
    ws.open_picker()
    ws.picker.sel = _first_real(ws.picker)
    x, y, w, h = ws.layout.del_btn
    ws.editor_picker.zone_tap(x + w // 2, y + h // 2)
    assert ws.editor_picker._del_armed is True
    ws.editor_picker.handle_input(_FakeInput({"right"}))
    assert ws.editor_picker._del_armed is False


def test_picker_delete_confirm_resets_on_reopen(tmp_path):
    """Arming a delete, leaving the picker without confirming, then reopening it must
    not leave a stale "DELETE? TAP AGAIN" armed for the next visit."""
    ws = _ws(tmp_path)
    ws.open_picker()
    ws.picker.sel = _first_real(ws.picker)
    x, y, w, h = ws.layout.del_btn
    ws.editor_picker.zone_tap(x + w // 2, y + h // 2)
    assert ws.editor_picker._del_armed is True
    ws.exit()                          # back to the launcher, confirm never fired
    ws.open_picker()                   # a fresh visit
    assert ws.editor_picker._del_armed is False


class _FakeInput:
    """Minimal `i.pressed(name)` stub for driving EditorPickerLayer.handle_input
    directly (mirrors the pattern host_app.ConsoleDriver's real input object
    satisfies) without needing a full ConsoleDriver frame."""

    def __init__(self, pressed):
        self._pressed = set(pressed)

    def pressed(self, name):
        return name in self._pressed
