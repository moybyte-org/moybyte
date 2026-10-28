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
from runtime.moy_store_base import builtin_name as _builtin_name  # noqa: E402
from ws_helpers import build_ws as _ws


from ws_helpers import make_drv as _drv
import pytest
from ws_helpers import boot_on_fixture

@pytest.fixture(autouse=True)
def _on_the_night_fixture(monkeypatch):
    """Every workstation here boots on the `night` fixture (ws_helpers): a
    palette theme with no look, drawn in the default's look flat."""
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


def _menu_key(drv, ws):
    """The ≡ menu's own entry point (the bar's ≡, the system key off a
    fullscreen game)."""
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    drv.frame(1 / 30)


def _labels(ws):
    return [it[1] for it in ws.sysmenu.items if it[0] in ("header", "item")]


def _row_center(ws, label):
    """Pointer center of the named selectable row in the open menu's card,
    as the look's card laid it out (`SystemMenuUI.look_hit`'s boxes)."""
    for it in ws.sysmenu.items:
        if it[0] == "item" and it[1] == label:
            box = ws.menu_ui._host.box(it[4])
            assert box is not None, "the card has not drawn %r" % label
            return (box[0] + box[2] // 2, box[1] + box[3] // 2)
    raise AssertionError("no row %r" % label)


def _open_cart(ws):
    ws.launcher.sel = 0
    ws.open()
    assert ws.screen == "desktop"
    # The ≡ menu over an open cart, its cart group in it: what the system key
    # opens over a cart that does not play fullscreen (`toggle_sysmenu`).


def _open_own_cart(ws, tmp_path):
    """Open a cart of the kid's own (not a built-in), as `_open_cart`
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

# -- opening / contents ------------------------------------------------------

def test_hamburger_tap_opens_menu_with_the_system_group(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_cart(ws)
    _menu_key(drv, ws)
    assert ws.sysmenu.open
    # a built-in: the console's own, so no Delete and no cart group (a run
    # restarts from the pause menu); off the home, Home leads the System
    # group, and restarting the console is last
    assert _labels(ws) == ["SYSTEM", "HOME", "SETTINGS", "ABOUT", "RESTART MOYBYTE"]
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
                           "SYSTEM", "HOME", "SETTINGS", "ABOUT", "RESTART MOYBYTE"]
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
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_cart(ws)
    _menu_key(drv, ws)
    assert ws.sysmenu.open
    _menu_key(drv, ws)                         # ≡ again closes
    assert not ws.sysmenu.open


# -- navigation (keyboard) skipping headers ---------------------------------

def test_keyboard_nav_skips_headers(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_own_cart(ws, tmp_path)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    assert ws.sysmenu.items[ws.sysmenu.sel][1] == "HOME"
    _key(drv, "up")                            # skips the SYSTEM header + the sep
    assert ws.sysmenu.items[ws.sysmenu.sel][1] == "DELETE CART"
    _key(drv, "down")
    assert ws.sysmenu.items[ws.sysmenu.sel][1] == "HOME"


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
def test_the_open_cart_menu_opens_on_home_not_delete(tmp_path, sleep):
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
    assert ws.sysmenu.items[ws.sysmenu.sel][1] == "HOME"
    n0 = len(ws.carts.all)
    _key(drv, "a")                             # the press goes home, deletes nothing
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


def test_a_cards_delete_stands_apart_under_a_rule(tmp_path):
    """The card group's Delete is set apart from the verbs above it, in the
    system menu and in the card's own menu (design review R4-13)."""
    ws = _ws(tmp_path)
    ws.set_home_tab("games")
    ws.carts.dup(next(it for it in ws.launcher.items if it.get("path")))
    ws.set_home_tab("make")
    from runtime.moy_store_base import builtin_name
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path") and builtin_name(it["path"]) is None)
    rows = ws.menu_ui.card_rows()
    i = next(i for i, r in enumerate(rows) if _danger(r))
    assert rows[i - 1] == ("sep",) and rows[i - 2][0] == "item", rows
    assert ws.menu_ui.open_card(ws.launcher_layer, (40, 60))
    assert ("sep",) in ws.sysmenu.items


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
            # a built-in is copied, never deleted
            own = _builtin_name(it["path"]) is None
            assert any(_danger(r) for r in ws.sysmenu.items) == own
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
    """About is a card over the washed content: a tap outside it, or Esc
    and B, closes it; its Licences row opens Settings' Licences page."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_cart(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    _tap(drv, *_row_center(ws, "ABOUT"))
    assert ws._about and not ws.sysmenu.open
    drv.frame(1 / 30)
    card = ws.menu_ui.about.host.box("About")
    assert card is not None
    _tap(drv, card[0] + card[2] // 2, card[1] + 10)     # inside: it stays
    assert ws._about
    _tap(drv, 2, card[1] + card[3] - 4)                 # outside: it closes
    drv.frame(1 / 30)
    assert not ws._about
    ws.menu_ui._menu_about()
    drv.frame(1 / 30)
    drv.press("b")
    drv.frame(1 / 30)
    assert not ws._about
    ws.menu_ui._menu_about()
    drv.frame(1 / 30)
    drv.frame(1 / 30)                     # the card's first frame lays its body out
    x, y, w, h = ws.menu_ui.about.host.box("Licences")
    _tap(drv, x + w // 2, y + h // 2)
    assert not ws._about
    assert ws.settings_layer.page == "licences"


def test_delete_cart_action_asks_then_deletes_and_goes_home(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    _open_own_cart(ws, tmp_path)
    n0 = len(ws.carts.all)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    _tap(drv, *_row_center(ws, "DELETE CART"))
    assert ws.confirm.open and ws.confirm.title == "Delete Extra?"
    assert len(ws.carts.all) == n0, "it asks first"
    drv.frame(1 / 30)
    x, y, w, h = ws.confirm._host.box("Delete")
    _tap(drv, x + w // 2, y + h // 2)
    drv.frame(1 / 30)
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


# -- a cut popover peeks a row (design review R5-01) -------------------------

import test_shell_goldens as _g  # noqa: E402

_THEME_MODES = [("moy", "light"), ("moy", "dark"), ("blocks", "light"),
                ("contrast", "light"), ("contrast", "dark")]
_FADE_H = 10            # the engine's fade, rows at the scale (moy_ui_api.c)


class _Ladder:
    """An idle ladder that only offers Sleep."""

    def sleep(self):
        pass


def _goldens_ws(tmp_path, theme, mode, inputs):
    cfg = dict(_g.CONFIGS["tdeck_320x240_fs1_dark"], variant=mode)
    cfg["theme"] = theme
    ws = _g._build(cfg, tmp_path / "carts")
    ws.use_inputs([("local", inputs)])
    return ws


def _settle(ws, n=6):
    for _ in range(n):
        _g._quiesce(ws)
        ws._dirty = True
        ws.frame(_g._DT)


def _rgb565(v):
    return ((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31


def _contrast(a, b):
    def lum(v):
        out = 0.0
        for c, k in zip(_rgb565(v), (0.2126, 0.7152, 0.0722)):
            c /= 255
            out += k * (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        return out
    la, lb = sorted((lum(a), lum(b)))
    return (lb + 0.05) / (la + 0.05)


def _fade_ink(ws, card):
    """The share of the cut body's fade band, its last FADE_H rows at the
    scale across the card's inner width, that differs from the card's
    ground by 1.5:1 or more, and the same share of the band's lower half:
    ink there is a row running on under the fade, where the foot of a row
    that ended above the cut reaches only the band's top. Third, the share
    of the strip two rows at the scale each side of the band's top (where
    the fade barely tints) at 3:1 or more, a pixel row mostly ink (a rule)
    not counted: the peeked row's words begin above the fade, where a peek
    whose words all lie deep in it leaves that strip blank (design review
    F6-01)."""
    from runtime import ui_draw
    cv = ws.glass_canvas
    buf = bytes(cv._buf)
    fs = cv.font_scale if hasattr(cv, "font_scale") else 1
    pad = ui_draw.metrics("value_row")[28] * fs
    x, y, w, h = card
    foot = y + h - pad
    # the card's ground: its pad under the body, clear of rows and fade
    o = 2 * ((y + h - pad // 2) * cv.w + x + w // 2)
    ground = buf[o] << 8 | buf[o + 1]
    band = _FADE_H * fs
    ink = []
    for yy in range(foot - band, foot):
        n = 0
        for xx in range(x + pad, x + w - pad):
            o = 2 * (yy * cv.w + xx)
            n += _contrast(buf[o] << 8 | buf[o + 1], ground) >= 1.5
        ink.append(n)
    row = w - 2 * pad
    k = 2 * fs
    read = 0
    for yy in range(foot - band - k, foot - band + k):
        n = 0
        for xx in range(x + pad, x + w - pad):
            o = 2 * (yy * cv.w + xx)
            n += _contrast(buf[o] << 8 | buf[o + 1], ground) >= 3.0
        if n <= 0.6 * row:
            read += n
    return (sum(ink) / (band * row), sum(ink[band // 2:]) / ((band - band // 2) * row),
            read / (2 * k * row))


def _peek_reads(theme, mode, inputs, band, low, read):
    assert band >= 0.02 and low >= 0.02, "%s %s %s: %.1f%% ink under the fade, %.1f%% " \
        "in its lower half" % (theme, mode, inputs, 100 * band, 100 * low)
    assert read >= 0.02, "%s %s %s: %.1f%% ink where the fade begins: the peeked row's " \
        "words lie under it" % (theme, mode, inputs, 100 * read)


@pytest.mark.parametrize("theme,mode", _THEME_MODES)
@pytest.mark.parametrize("inputs", ["tdeck", "touch"])
@pytest.mark.parametrize("sleep", [True, False])
def test_a_cut_system_menu_shows_a_row_under_its_fade(tmp_path, theme, mode, inputs, sleep):
    """Over Make the system menu's body is cut to the room under the bar; the
    cut never falls between rows, so the fade lies over part of a row and
    the kid sees that the menu runs on (design review R5-01). Measured in
    pixels: the fade band holds the next row's ink."""
    ws = _goldens_ws(tmp_path, theme, mode, inputs)
    if sleep:
        ws.idle_ladder = _Ladder()
    ws.carts.dup(_g._cart_by_title(ws, "Star Catcher"))
    ws.go_home()
    ws.set_home_tab("make")
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path") and _builtin_name(it["path"]) is None)
    _settle(ws, 2)
    ws._golden_popover = True
    ws.system_key()
    _settle(ws)
    card = ws.menu_ui._host.box("System")
    assert tuple(card) == ws.menu_ui.look_card()
    last = ws.menu_ui._host.box(ws.sysmenu.items[-1][4])
    cut = last is None or last[1] + last[3] > card[1] + card[3]
    if sleep and inputs == "tdeck":
        assert cut, "over Make with Sleep the T-Deck's menu is cut"
    if cut:
        _peek_reads(theme, mode, inputs, *_fade_ink(ws, card))
    ws.sysmenu.close()


@pytest.mark.parametrize("theme,mode", _THEME_MODES)
@pytest.mark.parametrize("inputs", ["tdeck", "touch"])
def test_the_handheld_more_menu_shows_a_row_under_its_fade(tmp_path, theme, mode, inputs):
    """The sprite tab's ⋯ menu is cut above its button: its fade lies over
    part of a row, never over the bare card under the rule before Clear."""
    ws = _goldens_ws(tmp_path, theme, mode, inputs)
    ws.open_in_editor(_g._cart_by_title(ws, "Star Catcher"))
    ws.set_menu_view("paint")
    _settle(ws, 2)
    ws.paint.set_selection(1, 1, 4, 4)
    ws.paint_layer._open_more()
    _settle(ws)
    card = ws.paint_layer._host.box("More")
    clear = ws.paint_layer._host.box("Clear")
    cut = clear is None or clear[1] + clear[3] > card[1] + card[3]
    if inputs == "touch" and not cut:
        return                          # no strip: the whole menu fits
    assert cut, "the T-Deck's menu is cut above its button"
    _peek_reads(theme, mode, inputs, *_fade_ink(ws, card))


@pytest.mark.parametrize("theme,mode", _THEME_MODES)
def test_a_long_press_menu_slides_up_to_fit_whole(tmp_path, theme, mode):
    """A long press on a kid's own cart on Make opens its card's menu at
    the finger; with no room below the finger for every row it slides up
    over the point and shows them all, Delete included, as a phone's
    context menu does (design review F6-01), and `look_card` is the card
    the engine draws."""
    ws = _goldens_ws(tmp_path, theme, mode, "touch")
    ws.carts.dup(_g._cart_by_title(ws, "Star Catcher"))
    ws.go_home()
    ws.set_home_tab("make")
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path") and _builtin_name(it["path"]) is None)
    _settle(ws, 2)
    ws._golden_popover = True
    r = ws.launcher.tile_rect(ws.launcher.sel)
    at = (r[0] + r[2] // 2, r[1] + r[3] // 2)
    assert ws.menu_ui.open_card(ws.launcher_layer, at)
    _settle(ws)
    card = ws.menu_ui._host.box("System")
    assert tuple(card) == ws.menu_ui.look_card()
    lay = ws.layout
    assert card[1] + card[3] <= lay.cy + lay.ch, "the card stays in the content"
    assert card[1] < at[1] < card[1] + card[3], "the card slid over the finger"
    for it in ws.sysmenu.items:
        if it[0] == "item":
            b = ws.menu_ui._host.box(it[4])
            assert b is not None and card[1] <= b[1] and b[1] + b[3] <= card[1] + card[3], \
                "%s shows whole" % it[4]
    ws.sysmenu.close()


# -- the desk's PROJECTS window (design review R5-14, R5-16) ------------------


def _projects_own(tmp_path, theme="moy", mode="light", inputs="keys"):
    """The desk's PROJECTS window on a project of the kid's own."""
    cfg = dict(_g.CONFIGS["p4_1024x600_cs2_fs1_windowed"], variant=mode)
    cfg["theme"] = theme
    ws = _g._build(cfg, tmp_path / "carts")
    ws.use_inputs([("local", inputs)])
    ws.carts.dup(_g._cart_by_title(ws, "Star Catcher"))
    ws.open_desk()
    ws.open_projects()
    _settle(ws, 2)
    grid = ws.projects_grid
    grid.sel = next(i for i, it in enumerate(grid.items)
                    if it.get("path") and _builtin_name(it["path"]) is None)
    _settle(ws, 2)
    return ws


def _meets(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _inside(a, b):
    return b[0] <= a[0] and b[1] <= a[1] and a[0] + a[2] <= b[0] + b[2] \
        and a[1] + a[3] <= b[1] + b[3]


@pytest.mark.parametrize("theme,mode", _THEME_MODES)
@pytest.mark.parametrize("inputs", ["keys", "pad", "touch"])
def test_the_projects_card_menu_hangs_from_the_cover_inside_the_window(tmp_path, theme, mode,
                                                                       inputs):
    """The PROJECTS card menu (a pad's X, the strip's Options) hangs from the
    hero's cover, below or above it: never over the cover it acts on, never
    outside the window and never over its hint strip."""
    ws = _projects_own(tmp_path, theme, mode, inputs)
    ws._golden_popover = True
    assert ws.projects_layer.open_card_menu()
    _settle(ws)
    card = tuple(ws.menu_ui._host.box("System"))
    assert card == ws.menu_ui.look_card()
    content, strip, hero = ws.menu_ui._projects()
    assert hero is not None and not _meets(card, hero), (card, hero)
    assert _inside(card, content), (card, content)
    if strip is not None:
        assert card[1] + card[3] <= strip[1], "the card keeps off the strip"


@pytest.mark.parametrize("theme,mode", _THEME_MODES)
def test_the_system_menu_over_projects_leads_with_its_card(tmp_path, theme, mode):
    """Over a focused PROJECTS window the system menu leads with the selected
    card's group, as over the console home, and opens on a row that
    destroys nothing (R4-01)."""
    ws = _projects_own(tmp_path, theme, mode)
    ws._golden_popover = True
    ws.system_key()
    _settle(ws)
    m = ws.sysmenu
    card = ws.menu_ui.card_rows(ws.projects_layer)
    assert card and [it[-1] for it in m.items[:len(card)]] == [it[-1] for it in card]
    assert m.items[len(card)] == ("sep",)
    from runtime.console_notices import _DANGER_ICON
    assert m.items[m.sel][0] == "item" and m.items[m.sel][3] != _DANGER_ICON, m.items[m.sel]


def _ratio_ge(a, b, num, den):
    """The parts' own `ratio_ge` on two RGB565 words: luminance on squares
    for the sRGB curve."""
    def lum(v):
        r, g, b = (v >> 11) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31
        return 2126 * r * r + 7152 * g * g + 722 * b * b + 32512500
    hi, lo = max(lum(a), lum(b)), min(lum(a), lum(b))
    return den * hi >= num * lo


def _glass_word(ws, x, y):
    buf = ws.glass_canvas._buf
    o = 2 * (y * ws.glass_canvas.w + x)
    return buf[o] << 8 | buf[o + 1]


@pytest.mark.parametrize("theme,mode", _THEME_MODES)
def test_a_focused_delete_keeps_its_red_icon_where_the_red_reads(tmp_path, theme, mode):
    """Focus on a danger row (the PROJECTS card menu's Delete): where the
    focus mark takes the label off its red, the trash icon keeps the red
    wherever that holds 3:1 on the mark, so the row still says it destroys
    (design review R5-12); where it does not hold, the icon takes the
    mark's ink with the label."""
    ws = _projects_own(tmp_path, theme, mode)
    ws._golden_popover = True
    assert ws.projects_layer.open_card_menu()
    _settle(ws)
    m = ws.sysmenu
    dl = max(i for i, it in enumerate(m.items) if it[0] == "item")
    x, y, w, h = ws.menu_ui._host.box(m.items[dl][4])
    # unfocused, the row's ink is the danger ink: its commonest colour off
    # the card's ground
    hist = {}
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            v = _glass_word(ws, xx, yy)
            hist[v] = hist.get(v, 0) + 1
    ground = max(hist, key=hist.get)
    danger = max((v for v in hist if v != ground), key=hist.get)
    m.sel = dl
    _settle(ws)
    assert tuple(ws.menu_ui._host.box(m.items[dl][4])) == (x, y, w, h)
    mark = _glass_word(ws, x + w - 6, y + h // 2)
    side = 16 * ws.layout.fs
    icon = {_glass_word(ws, xx, yy) for yy in range(y, y + h) for xx in range(x, x + 2 * side)}
    label = {_glass_word(ws, xx, yy) for yy in range(y, y + h)
             for xx in range(x + 2 * side, x + w // 2)}
    if danger in label:
        return                          # the mark keeps the label red: nothing taken
    reads = _ratio_ge(danger, mark, 3, 1)
    assert (danger in icon) == reads, "%s %s: red %04x on the mark %04x, the icon %s" % (
        theme, mode, danger, mark, "red" if danger in icon else "not red")
