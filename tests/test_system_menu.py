"""Tests for the top-bar dropdown system menu (#52): a ≡ (hamburger) toggle at the
LEFT of the unified 18px bar opens a left-anchored dropdown built on a reusable Popup
primitive. Always a SYSTEM group (Settings/About/Reboot); a CART group (Restart/Delete)
is prepended only when a cart is open.

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver: mouse == touch, arrows == trackball, Enter == run / A, Esc == stop),
so these assert host==device behavior. The Popup primitive is also unit-tested directly.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws(tmp_path):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"))


def _drv(ws):
    from runtime import host_app
    return host_app.ConsoleDriver(ws)


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
    """Pointer center of the named selectable row in the open dropdown."""
    from runtime import console as C
    cy = C._POPUP_Y
    for it in ws.sysmenu.items:
        rh = C._POPUP_SEP_H if it[0] == "sep" else C._POPUP_ROW_H
        if it[0] == "item" and it[1] == label:
            return (C._POPUP_X + 10, cy + rh // 2)
        cy += rh
    raise AssertionError("no row %r" % label)


def _open_cart(ws):
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"


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

def test_hamburger_tap_opens_menu_with_cart_and_system_groups(tmp_path):
    from runtime import console as C
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_cart(ws)
    _tap(drv, *_center(C._SYSMENU_BTN))
    assert ws.sysmenu.open
    assert _labels(ws) == ["CART", "RESTART CART", "DELETE CART",
                           "SYSTEM", "SETTINGS", "ABOUT", "REBOOT"]


def test_menu_omits_cart_group_when_no_cart(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    assert ws.cart is None and ws.screen == "launcher"
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    labels = _labels(ws)
    assert "CART" not in labels
    assert "RESTART CART" not in labels and "DELETE CART" not in labels
    assert labels == ["SYSTEM", "SETTINGS", "ABOUT", "REBOOT"]


# -- dismissal ---------------------------------------------------------------

def test_esc_dismisses(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    assert ws.sysmenu.open
    _key(drv, "stop")                          # ESC -> "stop"
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
    _open_cart(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    assert ws.sysmenu.items[ws.sysmenu.sel][1] == "RESTART CART"
    _key(drv, "down")
    assert ws.sysmenu.items[ws.sysmenu.sel][1] == "DELETE CART"
    _key(drv, "down")                          # skips the sep + the SYSTEM header
    assert ws.sysmenu.items[ws.sysmenu.sel][1] == "SETTINGS"


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


def test_restart_cart_action_reruns_via_apply(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_cart(ws)
    # Spy on apply() (the existing restart flow).
    calls = []
    orig = ws.apply
    ws.apply = lambda: (calls.append(1), orig())[1]
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    _tap(drv, *_row_center(ws, "RESTART CART"))
    assert calls == [1]
    assert ws.screen == "desktop" and not ws.sysmenu.open


def test_delete_cart_action_deletes_then_goes_home(tmp_path):
    from runtime import kid_carts
    ws = _ws(tmp_path)
    drv = _drv(ws)
    # Need >1 cart so del_cart isn't blocked by the keep-at-least-one guard.
    kid_carts.create("Extra", str(tmp_path / "carts"), src="def _draw():\n    cls(1)\n",
                     type="app")
    ws.launcher.set_items(kid_carts.scan(str(tmp_path / "carts")))
    n0 = len(ws.launcher.items)
    _open_cart(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    _tap(drv, *_row_center(ws, "DELETE CART"))
    assert len(ws.launcher.items) == n0 - 1
    assert ws.screen == "launcher" and not ws.sysmenu.open


def test_reboot_action_uses_hook_when_present(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    fired = []
    ws.reboot_hook = lambda: fired.append("reboot")
    _open_cart(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    _tap(drv, *_row_center(ws, "REBOOT"))
    assert fired == ["reboot"]
    assert not ws.sysmenu.open


def test_reboot_action_safe_stub_without_hook(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    assert ws.reboot_hook is None
    _open_cart(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    _tap(drv, *_row_center(ws, "REBOOT"))      # no hook -> safe go_home stub
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
    assert len(set(drv.rgb888())) > 2          # the launcher + dropdown painted
    _open_cart(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    assert len(set(drv.rgb888())) > 2          # over a running cart too
