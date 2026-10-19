"""Tests for the top-bar dropdown system menu (#52): a ≡ (hamburger) toggle at the
LEFT of the unified 18px bar opens a left-anchored dropdown built on a reusable Popup
primitive. Always a SYSTEM group (Settings/About/Reboot); a group headed by the open
cart's title (Delete, for a cart the store can delete -- never a built-in) is
prepended only when a cart is open. The cursor never opens on a danger row.

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver: mouse == touch, arrows == trackball, Enter == run / A),
so these assert host==device behavior. ESC is the exception: it arrives as the
`stop` BUTTON, which only the DEVICE tier's BUTTONS has. The Popup primitive is also unit-tested directly.
"""

from pathlib import Path
from ws_helpers import shelf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

import canvas_probe as probe  # noqa: E402  (pixel-width-agnostic "it drew" probes)


from runtime.moy_input import InputTable as DeviceInputState  # noqa: E402
from ws_helpers import build_ws as _ws


from ws_helpers import make_drv as _drv
import pytest
from ws_helpers import boot_on_fixture

@pytest.fixture(autouse=True)
def _on_the_night_fixture(monkeypatch):
    """These pin the lookless pixel bar and its coordinates: every
    workstation here boots on the `night` fixture (ws_helpers)."""
    boot_on_fixture(monkeypatch)


def _center(rect):
    x, y, w, h = rect
    return x + w // 2, y + h // 2


def _key(drv, name):
    # One discrete button edge: a press frame + a release frame (the console's
    # edge detector needs the release between presses, per the launcher-nav tests).
    drv.press(name)
    drv.frame(1 / 30)
    drv.frame(1 / 30)


def _tap(drv, x, y):
    drv.click(x, y)
    drv.frame(1 / 30)
    drv.frame(1 / 30)


def _labels(ws):
    return [it[1] for it in ws.sysmenu.items if it[0] in ("header", "item")]


def _row_center(ws, label):
    """Pointer center of the named selectable row in the open dropdown. The panel
    now anchors under the right-zone ≡ button (Stage 4), so take the live left edge
    from panel_rect() rather than the flush-left _POPUP_X."""
    from runtime import console as C
    px = ws.sysmenu.panel_rect()[0]      # anchored left edge (right-side ≡, Stage 4)
    cy = C._POPUP_Y
    for it in ws.sysmenu.items:
        rh = C._POPUP_SEP_H if it[0] == "sep" else C._POPUP_ROW_H
        if it[0] == "item" and it[1] == label:
            return (px + 10, cy + rh // 2)
        cy += rh
    raise AssertionError("no row %r" % label)


def _open_cart(ws):
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    # Stage 5 retired the #71 pause frame: the in-cart ≡/bar is CRASH chrome now (while
    # a cart PLAYS the game owns the full canvas), so these ≡-dropdown tests drive the
    # crashed state, where the bar (with the cart group in its ≡ menu) is drawn.
    ws.cart_error = "boom"
    ws._dirty = True


def _open_own_cart(ws, tmp_path):
    """Open a cart of the kid's own (not a built-in), crashed as `_open_cart`
    leaves one, with a second real cart beside it."""
    from runtime import moy_carts
    moy_carts.create("Extra", str(tmp_path / "carts"), src="def _draw():\n    cls(1)\n",
                     type="app")
    ws.carts.apply(shelf(str(tmp_path / "carts")))   # refresh both grids + carts.all
    ws.set_home_tab("mine")                           # a cart of the kid's own
    titles = [it.get("title") for it in ws.launcher.items]
    ws.launcher.sel = titles.index("Extra")
    ws.open()
    assert ws.screen == "desktop" and ws.cart.get("title") == "Extra"
    ws.cart_error = "boom"
    ws._dirty = True


# -- the reusable Popup primitive -------------------------------------------

def test_popup_open_close_toggle():
    from runtime import host_app, console as C  # noqa: F401 (registers aliases)
    p = C.Popup()
    items = [("item", "A", None), ("item", "B", None)]
    assert not p.open
    p.show(items)
    assert p.open and p.sel == 0
    p.close()
    assert not p.open
    p.toggle(items)
    assert p.open
    p.toggle(items)
    assert not p.open


def test_popup_cursor_skips_headers_and_separators():
    from runtime import host_app, console as C  # noqa: F401
    p = C.Popup()
    fired = []
    items = [
        ("header", "CART"),
        ("item", "R", lambda: fired.append("R")),
        ("item", "D", lambda: fired.append("D")),
        ("sep",),
        ("header", "SYSTEM"),
        ("item", "S", lambda: fired.append("S")),
    ]
    p.show(items)
    assert p.items[p.sel][1] == "R"          # first selectable, not the header
    p.move(1)
    assert p.items[p.sel][1] == "D"
    p.move(1)                                 # skips the sep AND the SYSTEM header
    assert p.items[p.sel][1] == "S"
    p.move(1)                                 # clamps at the last selectable
    assert p.items[p.sel][1] == "S"
    p.move(-1)
    assert p.items[p.sel][1] == "D"
    p.move(-1)
    assert p.items[p.sel][1] == "R"
    p.move(-1)                                # clamps at the first selectable
    assert p.items[p.sel][1] == "R"


def test_popup_activate_fires_action_and_closes():
    from runtime import host_app, console as C  # noqa: F401
    p = C.Popup()
    fired = []
    p.show([("item", "X", lambda: fired.append("X"))])
    p.activate()
    assert fired == ["X"]
    assert not p.open                         # close-on-select


def test_popup_outside_tap_dismisses_inside_does_not():
    from runtime import host_app, console as C  # noqa: F401
    p = C.Popup()
    p.show([("header", "H"), ("item", "A", None), ("item", "B", None)])
    x, y, w, h = p.panel_rect()
    # A tap well outside the panel dismisses.
    assert p.click(x + w + 50, y + 5) is True
    assert not p.open
    # A tap on a header row is consumed but does NOT dismiss.
    p.show([("header", "H"), ("item", "A", None)])
    assert p.click(x + 2, C._POPUP_Y + 1) is True       # header row
    assert p.open


def test_sysmenu_popup_anchors_under_the_right_side_hamburger(tmp_path):
    """Fix A: Stage 4 moved the ≡ toggle to the bar's RIGHT zone, so the dropdown must
    open UNDER it (right-aligned, hanging down-left), not at the old flush-left x=0.
    toggle_sysmenu right-aligns the panel to the live ≡ button and clamps it on-screen."""
    from runtime import console as C
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.toggle_sysmenu()
    assert ws.sysmenu.open
    x, y, w, h = ws.sysmenu.panel_rect()
    bx, by, bw, bh = ws.layout.sysmenu_btn
    # Panel right edge aligns with the ≡ button's right edge (down-left drop), and it
    # sits well right of the old x=0 anchor -- the whole point of the fix.
    assert x + w == bx + bw
    assert x > C._POPUP_X                      # no longer flush-left
    assert 0 <= x and x + w <= ws.sys_canvas.w  # fully on screen


# -- the ≡ icon placement (no overlap) --------------------------------------

def test_hamburger_is_leftmost_and_switchers_shifted_right():
    from runtime import console as C
    # ≡ at x=2 (leftmost), the tool switchers each one stride to its right.
    assert C._SYSMENU_BTN[0] == 2
    assert C._HOME_BTN[0] == 2 + C._BAR_STRIDE
    assert C._MENU_BTN[0] == 2 + 2 * C._BAR_STRIDE
    assert C._BLOCKS_BTN[0] == 2 + 5 * C._BAR_STRIDE
    # The rightmost switcher (blocks) ends well clear of the clock's left edge.
    blocks_right = C._BLOCKS_BTN[0] + C._BAR_ICON
    assert blocks_right <= C._BAR_CLOCK[0]


# -- opening / contents ------------------------------------------------------

def test_hamburger_tap_opens_menu_with_the_system_group(tmp_path):
    from runtime import console as C
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_cart(ws)
    _tap(drv, *_center(C._SYSMENU_BTN))
    assert ws.sysmenu.open
    # a built-in: the console's own, so no Delete and no cart group (a run
    # restarts from the pause menu); restarting the console is last
    assert _labels(ws) == ["SYSTEM", "SETTINGS", "ABOUT", "RESTART MOYBYTE"]
    reboot = ws.sysmenu.items[-1]
    assert reboot[3] == "power" and reboot[4] == "Restart Moybyte"


def test_sleep_is_offered_where_the_board_has_an_idle_ladder(tmp_path):
    """Sleep is the ladder's BLANK rung now: a row only where the kernel's
    ladder drives the light (absent on the host's console), before the
    console's restart."""
    from runtime import idle_ladder
    from runtime import moy_loop as L
    ws = _ws(tmp_path)
    drv = _drv(ws)
    L.trace_init(30, False, 1000)
    ws.idle_ladder = idle_ladder.IdleLadder(ws, L, 300)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    assert _labels(ws)[-2:] == ["SLEEP", "RESTART MOYBYTE"]
    assert ws.sysmenu.items[-2][3] == "sleep"
    blanked = []
    ws.idle_ladder.loop = type("Loop", (), {"power": staticmethod(blanked.append)})
    _tap(drv, *_row_center(ws, "SLEEP"))
    assert not ws.sysmenu.open
    assert blanked == [False]


def test_an_own_cart_offers_delete_last_in_its_group(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_own_cart(ws, tmp_path)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    assert _labels(ws) == ["EXTRA", "DELETE CART",
                           "SYSTEM", "SETTINGS", "ABOUT", "RESTART MOYBYTE"]
    head = ws.sysmenu.items[0]
    assert head[0] == "header" and head[4] == "Extra"     # the look's caption


def test_menu_omits_cart_group_when_no_cart(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    assert ws.cart is None and ws.screen == "launcher"
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    labels = _labels(ws)
    assert "CART" not in labels
    assert "DELETE CART" not in labels
    # Over the home the selected card's group stands first, in an open
    # cart's place (R3-18): its title over its verbs. Then the system's
    # rows, SEARCH first, launcher-only (#105: over the run-grid it filters).
    sel = ws.launcher.selected()
    card = ([sel["title"].upper()] + [
        {"favourite": "ADD FAVOURITE", "change": "CHANGE", "copy": "DUPLICATE",
         "delete": "DELETE"}[v] for v in ws.launcher_layer.card_verbs()]
        if sel and sel.get("path") else [])
    assert labels == card + ["SYSTEM", "SEARCH", "SETTINGS", "ABOUT", "RESTART MOYBYTE"]


# -- dismissal ---------------------------------------------------------------

def test_esc_dismisses(tmp_path):
    """ESC arrives as `stop`, a name only the DEVICE tier's BUTTONS carries
    (the host's ends at `home`, and the sim routes ESC through
    ConsoleDriver.escape instead), so the shared handler is driven with the
    InputState it meets on glass."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    assert ws.sysmenu.open
    i = DeviceInputState()
    i.source("kbd").set_button("stop", True)
    i.begin_frame()
    ws._sysmenu_layer.handle_input(i)
    assert not ws.sysmenu.open


def test_b_dismisses(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    _key(drv, "b")
    assert not ws.sysmenu.open


def test_outside_tap_dismisses(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    _tap(drv, 300, 200)                        # far outside the left-anchored panel
    assert not ws.sysmenu.open


def test_hamburger_toggles_closed(tmp_path):
    from runtime import console as C
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_cart(ws)
    _tap(drv, *_center(C._SYSMENU_BTN))
    assert ws.sysmenu.open
    _tap(drv, *_center(C._SYSMENU_BTN))        # ≡ again closes
    assert not ws.sysmenu.open


# -- navigation (keyboard) skipping headers ---------------------------------

def test_keyboard_nav_skips_headers(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_own_cart(ws, tmp_path)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    assert ws.sysmenu.items[ws.sysmenu.sel][1] == "SETTINGS"
    _key(drv, "up")                            # skips the SYSTEM header + the sep
    assert ws.sysmenu.items[ws.sysmenu.sel][1] == "DELETE CART"
    _key(drv, "down")
    assert ws.sysmenu.items[ws.sysmenu.sel][1] == "SETTINGS"


# -- the cursor never opens on a danger row (design review R4-01) ------------

def test_popup_opens_past_a_danger_row():
    from runtime import host_app, console as C  # noqa: F401
    from runtime import widgets
    p = C.Popup()
    danger = ("item", "DELETE", None, widgets.DANGER_ICON, "Delete")
    p.show([("header", "CART"), danger, ("sep",), ("header", "SYSTEM"),
            ("item", "SETTINGS", None, "settings", "Settings")])
    assert p.items[p.sel][1] == "SETTINGS"
    # a menu of danger rows alone still opens on its first row
    p.show([("header", "CART"), danger, danger])
    assert p.sel == 1


def _danger(it):
    from runtime import widgets
    return len(it) > 3 and it[3] == widgets.DANGER_ICON


def _assert_opens_safe(ws, what):
    m = ws.sysmenu
    assert m.open, what
    it = m.items[m.sel]
    assert it[0] == "item", what
    assert not _danger(it), "%s: the menu opens on %r" % (what, it[4] if len(it) > 4 else it[1])


def _ladder(ws):
    from runtime import idle_ladder
    from runtime import moy_loop as L
    L.trace_init(30, False, 1000)
    ws.idle_ladder = idle_ladder.IdleLadder(ws, L, 300)


@pytest.mark.parametrize("sleep", [False, True])
def test_the_open_cart_menu_opens_on_settings_not_delete(tmp_path, sleep):
    """One press of the key that opened it must not delete the open cart:
    the cursor lands on the System group's first row."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    if sleep:
        _ladder(ws)
    _open_own_cart(ws, tmp_path)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    assert "DELETE CART" in _labels(ws)
    _assert_opens_safe(ws, "open cart")
    assert ws.sysmenu.items[ws.sysmenu.sel][1] == "SETTINGS"
    n0 = len(ws.carts.all)
    _key(drv, "a")                             # the press runs Settings, deletes nothing
    assert len(ws.carts.all) == n0


@pytest.mark.parametrize("sleep", [False, True])
@pytest.mark.parametrize("tab", ["games", "mine", "make"])
def test_every_home_menu_opens_on_a_safe_row(tmp_path, tab, sleep):
    """Over each home tab, the system menu and the card's own menu (a long
    press, a pad's X) open on a row that destroys nothing."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    if sleep:
        _ladder(ws)
    if tab == "mine":
        ws.set_home_tab("games")
        ws.carts.dup(next(it for it in ws.launcher.items if it.get("path")))
    ws.set_home_tab(tab)
    real = [i for i, it in enumerate(ws.launcher.items) if it.get("path")]
    for i in (real or [0]):
        ws.launcher.sel = i
        ws.toggle_sysmenu()
        drv.frame(1 / 30)
        _assert_opens_safe(ws, "%s card %d, system menu" % (tab, i))
        ws.sysmenu.close()
        if ws.menu_ui.open_card(ws.launcher_layer, (40, 60)):
            _assert_opens_safe(ws, "%s card %d, card menu" % (tab, i))
            ws.sysmenu.close()


@pytest.mark.parametrize("sleep", [False, True])
def test_the_desk_menus_open_on_a_safe_row(tmp_path, sleep):
    from ws_helpers import build_desktop_ws
    ws = build_desktop_ws(tmp_path)
    drv = _drv(ws)
    if sleep:
        _ladder(ws)
    ws.open_desk()
    drv.frame(1 / 30)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    _assert_opens_safe(ws, "desk")
    ws.sysmenu.close()
    ws.open_projects()
    drv.frame(1 / 30)
    g = ws.projects_grid
    for i, it in enumerate(g.items):
        if not it.get("path"):
            continue
        g.sel = i
        if ws.menu_ui.open_card(ws.projects_layer, (200, 200)):
            assert any(_danger(r) for r in ws.sysmenu.items)
            _assert_opens_safe(ws, "projects card %d" % i)
            ws.sysmenu.close()


# -- actions wire to the right console methods ------------------------------

def test_settings_action_opens_settings(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_cart(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    _tap(drv, *_row_center(ws, "SETTINGS"))
    assert ws.screen == "settings" and not ws.sysmenu.open


def test_about_action_opens_dismissible_modal(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_cart(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    _tap(drv, *_row_center(ws, "ABOUT"))
    assert ws._about and not ws.sysmenu.open
    _tap(drv, 100, 100)                        # any tap dismisses
    assert not ws._about


def test_delete_cart_action_deletes_then_goes_home(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_own_cart(ws, tmp_path)
    n0 = len(ws.carts.all)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    _tap(drv, *_row_center(ws, "DELETE CART"))
    assert len(ws.carts.all) == n0 - 1
    assert "Extra" not in [c.get("title") for c in ws.carts.all]
    assert ws.screen == "launcher" and not ws.sysmenu.open


def test_reboot_action_uses_hook_when_present(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    fired = []
    ws.reboot_hook = lambda: fired.append("reboot")
    _open_cart(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    _tap(drv, *_row_center(ws, "RESTART MOYBYTE"))
    assert fired == ["reboot"]
    assert not ws.sysmenu.open


def test_reboot_action_safe_stub_without_hook(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    assert ws.reboot_hook is None
    _open_cart(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    _tap(drv, *_row_center(ws, "RESTART MOYBYTE"))      # no hook -> safe go_home stub
    assert ws.screen == "launcher" and not ws.sysmenu.open


# -- closed = zero behavior change ------------------------------------------

def test_closed_menu_does_not_break_bar_or_mode_switch(tmp_path):
    from runtime import console as C
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_cart(ws)
    assert not ws.sysmenu.open
    # The HOME icon (now one stride right of ≡) still goes home.
    _tap(drv, *_center(C._HOME_BTN))
    assert ws.screen == "launcher"
    # The EDIT/CODE icon still opens the editor menu.
    _open_cart(ws)
    _tap(drv, *_center(C._MENU_BTN))
    assert ws.screen == "menu"


def test_menu_renders_on_top_without_error(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    assert probe.distinct_pixels_in(drv.rgb888(), 3) > 2          # the launcher + dropdown painted
    _open_cart(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    assert probe.distinct_pixels_in(drv.rgb888(), 3) > 2          # over a running cart too
