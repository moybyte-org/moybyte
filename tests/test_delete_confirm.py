"""Deleting a cart asks first, on every route (owner, 2026-10-20): a delete
is permanent -- the Trash holds a kid's files, never a cart -- so Make's X
and DEL, the desk PROJECTS window's Delete, a card menu's Delete and the
system menu's "Delete cart" all open one dialog (`DeleteConfirm`, the
public UI API's `ui.dialog`): "Delete <title>?", "This can't be undone.",
Cancel and Delete, Cancel where focus opens. Cancel keeps the cart; Delete
removes it. Driven through the real input feeds: keys, the T-Deck's
matrix, a pad and touch."""

import pytest

from runtime import host_app, moy_carts, ui_draw
from ws_helpers import build_ws, shelf


@pytest.fixture(autouse=True)
def _keys():
    from runtime import layout_base
    was = (layout_base.metric_class(), layout_base.render_scale())
    yield
    ui_draw.input_kind("keys")
    layout_base.place_class(*was)         # the desk's metrics are one per process


def _drv(ws):
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    return drv


def _frames(drv, n=3):
    for _ in range(n):
        drv.frame(1 / 30)


def _own(ws, title="Extra"):
    """A cart of the kid's own, selected on Make (a built-in is copied,
    never deleted)."""
    moy_carts.create(title, str(ws.carts_root), src="def _draw():\n    cls(1)\n",
                     type="game")
    ws.carts.apply(shelf(str(ws.carts_root)))
    ws.set_home_tab("make")
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("title") == title)
    return ws.launcher.selected()


def _there(ws, title="Extra"):
    return any(c.get("title") == title for c in ws.carts.all)


def _asked(ws, drv, title="Extra"):
    """The confirm is up over `title`, drawn, with focus on Cancel."""
    _frames(drv)
    c = ws.confirm
    assert c.open and c.title == "Delete %s?" % title
    assert c.focused == "Cancel", "Cancel takes the default focus"
    assert _there(ws, title), "nothing goes before the answer"
    return c


def _press(drv, *names):
    for n in names:
        drv.press(n)
        _frames(drv, 1)
    _frames(drv, 2)


def _tap(ws, drv, label):
    x, y, w, h = ws.confirm._host.box(label)
    drv.click(x + w // 2, y + h // 2)
    _frames(drv, 2)


class _Pad:
    """A frame's input with one gamepad button pressed."""

    def __init__(self, name):
        self.name = name

    def steps(self, n):
        return int(bool(self.pressed(n)))

    def pressed(self, name):
        return name == self.name


def _verb_tap(ws, verb):
    layer = ws.launcher_layer
    x, y, w, h = layer._zone_action_rects(ws.layout.zone_left)[verb]
    return layer.zone_tap(x + w // 2, y + h // 2)


def test_make_x_asks_and_cancel_keeps_the_cart(tmp_path):
    ws = build_ws(tmp_path)
    drv = _drv(ws)
    _own(ws)
    drv.type_char(ord("x"))
    c = _asked(ws, drv)
    assert ws.menu_ui.scrims(), "the dialog dims what is behind it (D-01)"
    assert ws._modal_layer() == ("confirm",)
    _press(drv, "a")                              # A on Cancel
    assert not c.open and _there(ws)


def test_make_del_asks_and_delete_removes_the_cart(tmp_path):
    ws = build_ws(tmp_path, sys_size=(800, 480))
    drv = _drv(ws)
    _own(ws)
    n0 = len(ws.carts.all)
    assert ws.launcher_layer.zone_verbs()[-1] == "delete"
    assert _verb_tap(ws, "delete")
    c = _asked(ws, drv)
    _press(drv, "right", "a")                     # over to Delete, then A
    assert not c.open and not _there(ws)
    assert len(ws.carts.all) == n0 - 1


def test_b_esc_and_home_cancel(tmp_path):
    ws = build_ws(tmp_path)
    drv = _drv(ws)
    _own(ws)
    for how in ("b", "home", "esc"):
        ws.launcher_layer._do_verb("delete")
        c = _asked(ws, drv)
        if how == "esc":
            drv.escape()
            _frames(drv, 1)
        else:
            _press(drv, how)
        assert not c.open and _there(ws), how


def test_touch_answers_on_the_buttons_and_outside_the_card_cancels(tmp_path):
    ws = build_ws(tmp_path)
    drv = _drv(ws)
    ui_draw.input_kind("touch")
    _own(ws)
    home = ws.launcher_layer
    home._do_verb("delete")
    _asked(ws, drv)
    _tap(ws, drv, "Cancel")
    assert not ws.confirm.open and _there(ws)
    home._do_verb("delete")
    _asked(ws, drv)
    card = ws.confirm._host.box(ws.confirm.title)
    drv.click(card[0] + card[2] // 2, max(0, card[1] - 6))    # the scrim above it
    _frames(drv, 2)
    assert not ws.confirm.open and _there(ws)
    home._do_verb("delete")
    _asked(ws, drv)
    _tap(ws, drv, "Delete")
    assert not ws.confirm.open and not _there(ws)


def test_the_tdeck_x_asks_enter_keeps_and_mic_cancels(tmp_path):
    """The T-Deck's keyboard in raw mode, the home's: X asks, Enter on the
    default focus is Cancel, and Mic, the console's key, cancels."""
    from test_system_key import MIC, _tdeck_frame
    from test_tdeck_keymap import _chord, _input_module
    mod = _input_module()
    ws = build_ws(tmp_path)
    drv = _drv(ws)
    ui_draw.input_kind("tdeck")
    _own(ws)
    _tdeck_frame(ws, _chord(mod, ord("x")))
    c = _asked(ws, drv)
    _tdeck_frame(ws, _chord(mod, 13))             # Enter, on Cancel
    ws.frame(1 / 30)                              # the frame it lands in draws the answer
    _frames(drv, 2)
    assert not c.open and _there(ws)
    _tdeck_frame(ws, _chord(mod, ord("x")))
    _asked(ws, drv)
    _tdeck_frame(ws, MIC)
    assert not c.open and not ws.sysmenu.open and _there(ws)


def test_the_pad_reaches_it_through_the_card_menu(tmp_path):
    """A pad's X opens the card's verbs; its Delete row asks; B cancels and
    the pad's A on Delete removes the cart."""
    ws = build_ws(tmp_path)
    drv = _drv(ws)
    ui_draw.input_kind("pad")
    _own(ws)
    for answer in ("b", "delete"):
        ws.launcher_layer.handle_input(_Pad("x"))
        _frames(drv, 1)
        m = ws.sysmenu
        assert m.open
        m.sel = next(j for j, it in enumerate(m.items)
                     if it[0] == "item" and getattr(it[2], "verb", None) == "delete")
        _press(drv, "a")
        c = _asked(ws, drv)
        if answer == "b":
            _press(drv, "b")
            assert not c.open and _there(ws)
        else:
            _press(drv, "right", "a")
            assert not c.open and not _there(ws)


def test_a_long_press_card_menu_asks(tmp_path):
    ws = build_ws(tmp_path)
    drv = _drv(ws)
    ui_draw.input_kind("touch")
    _own(ws)
    assert ws.menu_ui.open_card(ws.launcher_layer, (100, 100))
    m = ws.sysmenu
    row = next(it for it in m.items
               if it[0] == "item" and getattr(it[2], "verb", None) == "delete")
    m.close()
    row[2]()
    _asked(ws, drv)


def test_the_system_menus_delete_cart_asks_over_the_open_cart(tmp_path):
    ws = build_ws(tmp_path)
    drv = _drv(ws)
    cart = _own(ws)
    ws.open_in_editor(cart)
    _frames(drv)
    assert ws.screen == "menu" and ws.cart.get("title") == "Extra"
    for answer in ("cancel", "delete"):
        ws.toggle_sysmenu()
        m = ws.sysmenu
        m.sel = next(j for j, it in enumerate(m.items)
                     if it[0] == "item" and it[2] == ws.menu_ui._menu_delete_cart)
        _press(drv, "a")
        c = _asked(ws, drv)
        if answer == "cancel":
            _press(drv, "a")
            assert not c.open and _there(ws) and ws.screen == "menu"
        else:
            _press(drv, "right", "a")
            assert not c.open and not _there(ws)
            assert ws.screen == "launcher", "a deleted open cart goes home"


def test_the_desk_projects_windows_delete_asks(tmp_path):
    """The desk's PROJECTS window: its Delete button and its card menu's
    Delete both ask, the dialog over the glass's content."""
    import moy_ui
    import test_shell_goldens as g
    moy_ui.theme_uninstall()
    ws = g._build(dict(g.CONFIGS["p4_1024x600_cs2_fs1_windowed"], variant="dark",
                       theme="moy"), tmp_path / "carts")
    drv = _drv(ws)
    ws.open_desk()
    ws.open_projects()
    _frames(drv)
    grid = ws.projects_grid
    _own(ws)
    ws.set_home_tab("make", grid)
    grid.sel = next(i for i, it in enumerate(grid.items) if it.get("title") == "Extra")
    _frames(drv)
    home = ws.projects_layer
    win = ws.wm._wins["projects"]
    ws.wm._install(win.ctx)
    try:
        buttons = home._desk_buttons()
        r = next(r for v, r, _k in buttons if v == "delete")
        home.handle_pointer(r[0] + r[2] // 2, r[1] + r[3] // 2, True)
    finally:
        ws.wm._install(ws.wm._root_ctx)
    c = _asked(ws, drv)
    _press(drv, "a")
    assert not c.open and _there(ws)
    assert ws.menu_ui.open_card(home, (0, 0))
    row = next(it for it in ws.sysmenu.items
               if it[0] == "item" and getattr(it[2], "verb", None) == "delete")
    ws.sysmenu.close()
    row[2]()
    _asked(ws, drv)
    _press(drv, "right", "a")
    assert not c.open and not _there(ws)
    moy_ui.theme_uninstall()


@pytest.mark.parametrize("kind", ["keys", "pad", "tdeck"])
def test_the_hint_strip_names_the_dialogs_keys(tmp_path, monkeypatch, kind):
    """While the confirm is up the home's hint strip names the dialog's
    keys, Choose and Cancel in the input's own keycaps, never the home's
    verbs (design review F6-02), as it names Close under the system menu;
    it names the home's verbs again once the dialog is answered."""
    from runtime import launcher_layer
    ws = build_ws(tmp_path)
    drv = _drv(ws)
    ui_draw.input_kind(kind)
    _own(ws)
    drawn = []
    hints = launcher_layer._ui_draw.hints

    def spy(cv, th, rect, items, desk=False, draw=True):
        if draw:
            drawn.append(tuple(items))
        return hints(cv, th, rect, items, desk, draw)

    def strip():
        cv = ws.glass_canvas
        x, y, w, h = ws.launcher_layer.grid.foot()[5]
        return [bytes(cv._buf[2 * (yy * cv.w + x):2 * (yy * cv.w + x + w)])
                for yy in range(y, y + h)]

    ws._dirty = True
    _frames(drv)
    home = strip()
    monkeypatch.setattr(launcher_layer._ui_draw, "hints", spy)
    assert ws.confirm.ask(ws.launcher.selected())
    c = _asked(ws, drv)
    assert drawn == [launcher_layer._CONFIRM_KEYS] * len(drawn) and drawn, drawn
    assert strip() != home
    c.cancel()
    _frames(drv)
    assert strip() == home, "the home's verbs come back with the dialog gone"
