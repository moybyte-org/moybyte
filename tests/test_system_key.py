"""Super, Esc, the system menu and the pause menu (docs/look_2026-10.md step
25a; docs/shell_ux_v1.md, the system key).

  * Super (the `start` button on a board -- a keyboard's GUI key, a T-Deck's
    Mic -- or the host's system_key) opens the
    system menu from every surface and, over a fullscreen game, the pause
    menu; pressed again it closes the one it opened, and Esc closes either;
  * the pause holds the cart: no tick, its time() and its music held, and
    Resume gives it its next frame as if no time passed;
  * the pause menu's Restart, Options and Quit;
  * under a theme with a depth the system menu is the look's card, a popover
    of the public UI API's, whose rect the windowed desk knows before it
    draws (`SystemMenuUI.look_card`, held here to the engine's).
"""

import pytest

from runtime import audio_session, ui_draw
from runtime.moy_input import InputTable
from ws_helpers import build_desktop_ws, build_ws, build_ws_with_cart, make_drv, open_cart, quiesce

_DT = 1 / 30

COUNTER = """
n = 0
def _update(dt=None):
    global n
    n += 1
def _draw():
    cls(1)
"""


def _frames(drv, k=2):
    for _ in range(k):
        drv.frame(_DT)


def _press(ws, name):
    """One frame of a board's input: `name` pressed, through the console's
    router, as a BLE keyboard's GUI key or Esc arrives."""
    was = ws.input
    i = InputTable()
    i.source("kbd").set_button(name, True)
    i.begin_frame()
    ws.input = i
    try:
        ws.handle_input()
    finally:
        ws.input = was


def _game(tmp_path):
    ws = build_ws_with_cart(tmp_path, COUNTER, title="Counter", type="game")
    drv = make_drv(ws)
    _frames(drv, 3)
    assert ws.screen == "desktop" and ws.plays_fullscreen()
    return ws, drv


# -- the key routing ---------------------------------------------------------------


def test_super_opens_and_closes_the_system_menu_from_the_home(tmp_path):
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    _frames(drv)
    drv.system_key()
    assert ws.sysmenu.open and ws.sysmenu.widget == "menu" and not ws.pause.open
    drv.system_key()
    assert not ws.sysmenu.open
    drv.system_key()
    drv.escape()
    assert not ws.sysmenu.open, "Esc closes it"


@pytest.mark.parametrize("surface", ["settings", "calc", "editor"])
def test_super_opens_the_system_menu_from_every_surface(tmp_path, surface):
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    if surface == "settings":
        ws.open_settings()
    elif surface == "calc":
        ws.open_app(ws._apps_by_id["calc"])
    else:
        open_cart(ws, "Star Catcher")
        ws._exit_to_caller()
        ws.open_in_editor(next(c for c in ws.carts.all if c["title"] == "Star Catcher"))
    _frames(drv)
    top = ws.screen
    drv.system_key()
    assert ws.sysmenu.open and not ws.pause.open, surface
    drv.system_key()
    assert not ws.sysmenu.open and ws.screen == top


def test_super_on_the_windowed_desk_opens_the_system_menu(tmp_path):
    ws = build_desktop_ws(tmp_path)
    drv = make_drv(ws)
    ws.open_desk()
    _frames(drv)
    drv.system_key()
    assert ws.sysmenu.open
    drv.system_key()
    assert not ws.sysmenu.open


def test_a_boards_start_button_is_super_and_its_stop_is_esc(tmp_path):
    ws = build_ws(tmp_path)
    _frames(make_drv(ws))
    _press(ws, "start")
    assert ws.sysmenu.open
    _press(ws, "start")
    assert not ws.sysmenu.open
    ws, _drv = _game(tmp_path / "g")
    _press(ws, "start")
    assert ws.pause.open and not ws.sysmenu.open
    _press(ws, "stop")
    assert not ws.pause.open


def _tdeck_frame(ws, frame, raw=True, text=False):
    """One frame of the T-Deck's keyboard through the kernel's driver (the C3's
    bytes -> moy_kbd.c -> a board's table), then the console's router over that
    table: what the Mic key does from the glass."""
    from test_tdeck_input import _frame, _keyboard
    table, drv, _fake = _keyboard(frame, raw=raw)
    table.text_mode = text
    _frame(drv, table)
    was = ws.input
    ws.input = table
    try:
        ws.handle_input()
    finally:
        ws.input = was
    return table


MIC = bytes([0x40, 0, 0, 0, 0])             # the matrix with only Mic down


def test_the_tdeck_mic_key_pauses_a_game_and_opens_the_system_menu_elsewhere(tmp_path):
    ws = build_ws(tmp_path)
    _frames(make_drv(ws))
    table = _tdeck_frame(ws, MIC)
    assert table.held("start") and ws.sysmenu.open and not ws.pause.open
    _tdeck_frame(ws, MIC)
    assert not ws.sysmenu.open, "Mic again closes it"
    ws, _drv = _game(tmp_path / "g")
    _tdeck_frame(ws, MIC)
    assert ws.pause.open and ws.player.paused and not ws.sysmenu.open
    _tdeck_frame(ws, MIC)
    assert not ws.pause.open and not ws.player.paused


def test_the_tdeck_mic_key_works_in_text_mode_and_on_the_ascii_path(tmp_path):
    ws = build_ws(tmp_path)
    _frames(make_drv(ws))
    _tdeck_frame(ws, MIC, text=True)                  # a text surface, matrix still streaming
    assert ws.sysmenu.open
    _tdeck_frame(ws, MIC, text=True)
    assert not ws.sysmenu.open
    _tdeck_frame(ws, [b"\xe0"], raw=False, text=True)   # ASCII mode: Shift+Mic
    assert ws.sysmenu.open
    ws.sysmenu.close()
    ws, _drv = _game(tmp_path / "g")
    _tdeck_frame(ws, [b"\xe0"], raw=False)
    assert ws.pause.open


def test_the_tdeck_mic_key_never_reaches_the_cart(tmp_path):
    from runtime.cart_api import CART_BUTTONS
    assert "start" not in CART_BUTTONS
    ws, _drv = _game(tmp_path)
    table = _tdeck_frame(ws, bytes([0x40 | 0x02, 0, 0, 0, 0x08]))   # Mic + W + Backspace
    assert table.held("start") and table.held("up")
    assert table.last_key == 0x08, "Backspace is the cart's key; Mic is nobody's"


def test_super_over_a_fullscreen_game_pauses_and_again_resumes(tmp_path):
    ws, drv = _game(tmp_path)
    drv.system_key()
    assert ws.pause.open and ws.player.paused and not ws.sysmenu.open
    drv.system_key()
    assert not ws.pause.open and not ws.player.paused
    drv.system_key()
    drv.escape()
    assert not ws.pause.open, "Esc resumes"
    drv.system_key()
    drv.press("b")
    drv.frame(_DT)
    assert not ws.pause.open, "B resumes"


# -- the hold ------------------------------------------------------------------------


def test_the_pause_holds_the_carts_ticks_and_its_clock(tmp_path):
    ws, drv = _game(tmp_path)
    n0 = ws.player.ns["n"]
    start0 = ws.input.cart_start_ms
    drv.system_key()
    _frames(drv, 10)
    assert ws.player.ns["n"] == n0, "a held cart does not tick"
    ws.player._paused_at -= 500             # half a second held
    drv.system_key()
    assert ws.input.cart_start_ms - start0 >= 500, "time() skips the pause"
    _frames(drv, 2)
    assert ws.player.ns["n"] > n0, "Resume gives the cart its next frame"


def test_the_pause_holds_the_carts_audio(tmp_path):
    na = audio_session.native()
    if na is None:
        pytest.skip("no audio module on this host")
    ws, drv = _game(tmp_path)
    held = na.focused()
    assert held, "the cart's session is the audible one"
    drv.system_key()
    assert na.focused() == 0, "the pause takes the mix from it"
    drv.system_key()
    assert na.focused() == held, "Resume gives it back"


def test_a_paused_frame_does_not_animate(tmp_path):
    ws, drv = _game(tmp_path)
    drv.system_key()
    _frames(drv, 2)
    quiesce(ws)
    ws._splash_until = None
    assert not ws._animating(_DT), "the card is drawn on a change, not every frame"


# -- the pause menu's verbs ------------------------------------------------------------


def _key(drv, name):
    # a press frame and a release frame: the next press is an edge again
    drv.press(name)
    drv.frame(_DT)
    drv.frame(_DT)


def test_options_is_the_cards_second_page_and_esc_goes_back(tmp_path):
    ws, drv = _game(tmp_path)
    drv.system_key()
    drv.frame(_DT)
    _key(drv, "down")
    _key(drv, "down")
    _key(drv, "a")
    assert ws.pause.open and ws.pause.page == 1
    v0 = ws.settings_layer.level("volume")[0]
    _key(drv, "left")
    drv.frame(_DT)
    assert ws.settings_layer.level("volume")[0] == v0 - 1, "Options' Volume steps"
    drv.escape()
    assert ws.pause.open and ws.pause.page == 0
    ws.settings_layer.set_level("volume", v0)


def test_restart_runs_the_cart_from_the_top(tmp_path):
    ws, drv = _game(tmp_path)
    _frames(drv, 5)
    drv.system_key()
    _key(drv, "down")
    _key(drv, "a")
    assert not ws.pause.open and not ws.player.paused
    assert ws.screen == "desktop" and ws.player.ns["n"] <= 2


def test_quit_leaves_for_the_caller(tmp_path):
    ws, drv = _game(tmp_path)
    drv.system_key()
    for _ in range(3):
        _key(drv, "down")
    _key(drv, "a")
    assert not ws.pause.open and ws.screen != "desktop"


def test_a_tap_on_an_item_chooses_it_and_outside_resumes(tmp_path):
    ws, drv = _game(tmp_path)
    drv.system_key()
    _frames(drv, 3)
    b = ws.pause._host.box("Options")
    drv.click(b[0] + b[2] // 2, b[1] + b[3] // 2)
    drv.frame(_DT)
    assert ws.pause.page == 1
    drv.escape()
    _frames(drv, 2)
    drv.click(1, 1)
    drv.frame(_DT)
    assert not ws.pause.open


# -- the look's system menu ---------------------------------------------------------------


def _moy(ws):
    ws.look.set_theme("moy", persist=False, variant="light")


@pytest.mark.parametrize("desk", [False, True])
@pytest.mark.parametrize("cart", [False, True])
def test_the_look_cards_rect_is_the_one_the_engine_draws(tmp_path, desk, cart):
    ws = build_desktop_ws(tmp_path) if desk else build_ws(tmp_path)
    _moy(ws)
    drv = make_drv(ws)
    if desk:
        ws.open_desk()
    if cart:
        ws.open_in_editor(next(c for c in ws.carts.all if c["title"] == "Star Catcher"))
    _frames(drv)
    ws.toggle_sysmenu()
    _frames(drv, 3)
    assert ws.menu_ui.looked()
    drawn = ws.menu_ui._host.box("System")
    assert drawn is not None and tuple(drawn) == ws.menu_ui.look_card()
    x, y, w, h = ws.menu_ui.panel_rect()
    assert x <= drawn[0] and y <= drawn[1] and x + w >= drawn[0] + drawn[2]
    assert y + h >= drawn[1] + drawn[3], "the painted rect holds the card and its shadow"


class _Ladder:
    """An idle ladder that only offers Sleep (the boards' spine sets one)."""

    def sleep(self):
        pass


def _meets(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


@pytest.mark.parametrize("desk", [False, True])
def test_the_look_card_keeps_clear_of_the_bar_the_strip_and_its_anchor(tmp_path, desk):
    """The card hangs 4 pixels at the scale under the bar and keeps as far
    above the home's hint strip, whose Close it leaves showing; over Make
    with Sleep offered its body is cut to that room, and the rows it cut
    are reached by focus (design review R4-04)."""
    ws = build_desktop_ws(tmp_path) if desk else build_ws(tmp_path)
    _moy(ws)
    drv = make_drv(ws)
    ws.idle_ladder = _Ladder()
    if desk:
        ws.open_desk()
    else:
        ws.set_home_tab("make")
        ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items) if it.get("path"))
    _frames(drv)
    ws._golden_popover = True
    ws.toggle_sysmenu()
    _frames(drv, 3)
    card = ws.menu_ui._host.box("System")
    assert tuple(card) == ws.menu_ui.look_card()
    x, y, w, h = card
    fs = ws.sys_canvas.font_scale
    lay = ws.layout
    assert y >= lay.cy + 4 * fs, "the card hangs under the bar"
    foot = ws.launcher_layer.grid.foot()
    bottom = foot[5][1] if (foot is not None and not desk) else lay.cy + lay.ch
    assert y + h <= bottom - 4 * fs, "the card keeps off the strip"
    assert not _meets(card, ws.menu_ui._menu_cell()), "the card covers its anchor"
    if not desk:
        last = ws.menu_ui._host.box("Restart Moybyte")
        assert last is None or last[1] + last[3] > y + h, "the body is cut"
        m = ws.sysmenu
        for _ in range(len(m.items)):
            _key(drv, "down")
        assert m.sel == len(m.items) - 1
        _frames(drv, 3)
        last = ws.menu_ui._host.box("Restart Moybyte")
        assert last[1] >= y and last[1] + last[3] <= y + h, "focus scrolls the last row in"
    ws.sysmenu.close()


def test_the_card_menu_never_covers_the_point_it_opened_at(tmp_path):
    ws = build_ws(tmp_path)
    _moy(ws)
    drv = make_drv(ws)
    ws.set_home_tab("make")
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items) if it.get("path"))
    _frames(drv)
    for at in ((160, 40), (160, 120), (160, 200), (20, 100), (300, 100)):
        assert ws.menu_ui.open_card(ws.launcher_layer, at)
        _frames(drv, 3)
        x, y, w, h = ws.menu_ui._host.box("System")
        assert not (y < at[1] < y + h), (at, (x, y, w, h))
        ws.sysmenu.close()
        _frames(drv)


def test_the_look_card_takes_taps_on_its_rows_and_closes_outside(tmp_path):
    ws = build_ws(tmp_path)
    _moy(ws)
    drv = make_drv(ws)
    _frames(drv)
    drv.system_key()
    _frames(drv, 3)
    b = ws.menu_ui._host.box("Settings")
    drv.click(b[0] + b[2] // 2, b[1] + b[3] // 2)
    _frames(drv, 2)
    assert not ws.sysmenu.open and ws.screen == "settings"
    drv.system_key()
    _frames(drv, 3)
    drv.click(ws.sys_canvas.w - 2, ws.sys_canvas.h - 2)
    _frames(drv, 2)
    assert not ws.sysmenu.open


def test_the_look_card_follows_the_menus_keys(tmp_path):
    ws = build_ws(tmp_path)
    _moy(ws)
    drv = make_drv(ws)
    _frames(drv)
    drv.system_key()
    _frames(drv, 3)
    _key(drv, "down")
    drv.frame(_DT)
    from runtime import ui
    ui_draw.ui_frame(ws.menu_ui._host.ctx, ws.sys_canvas, ws.theme_colors)
    assert ui.focus() == ws.sysmenu.items[ws.sysmenu.sel][4]


def test_a_theme_without_a_depth_keeps_the_kernels_menu(tmp_path):
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    _frames(drv)
    drv.system_key()
    _frames(drv)
    if not ui_draw.depth()[0]:
        assert not ws.menu_ui.looked() and ws.menu_ui._host is None


@pytest.mark.parametrize("desk", [False, True])
def test_the_pause_cards_title_row_draws_moy_in_its_icon_slot(tmp_path, desk):
    """The title row names Moy as its icon (`ui.icon("moy")`), and the engine
    draws Moy's pixel tile there (iconsets.use_parts installs it): in the
    column of the rows' icons, over the first row."""
    if desk:
        ws = build_desktop_ws(tmp_path)
        ws.open_library()
        ws._open_workspace(next(c for c in ws.carts.all if c["title"] == "Star Catcher"))
        ws.run(ws.project, ws.launcher_layer)
        drv = make_drv(ws)
        _frames(drv, 3)
    else:
        ws, drv = _game(tmp_path)
    _moy(ws)
    drv.system_key()
    _frames(drv, 3)
    assert ws.pause.open
    card = ws.pause._host.box("Pause")
    first = ws.pause._host.box("Resume")
    cv = ws.sys_canvas
    side = 16 * ws.layout.fs

    def slot():
        """The title row's icon column: the card's left to the rows' icon
        width past the first row's left, from the card's top to the row's."""
        b = cv._buf
        out = []
        for y in range(card[1] + 1, first[1]):
            o = 2 * (y * cv.w + first[0])
            out.append(bytes(b[o:o + 2 * (side + 8)]))
        return out
    with_moy = slot()
    from runtime import ui_draw
    ui_draw._C.moy_use(None, 0)
    try:
        ws._dirty = True
        _frames(drv, 2)
        assert slot() != with_moy, "Moy draws in the title row's icon slot"
    finally:
        ws.look.look_gen += 1          # the next icon_spec puts the tile back
        ws.icon_spec()


def test_the_ux_spec_states_one_console_key_per_input():
    """docs/shell_ux_v1.md section 9a is the console key's rule (owner call,
    2026-10-18): one key a device keeps, never a cart's -- the T-Deck's Mic at
    the matrix bit its decoder reads -- with BACKSPACE a plain key and no
    exit a textmode game must build. The rules it retired stay out of it."""
    from pathlib import Path
    from runtime import moy_input as mi
    doc = (Path(__file__).resolve().parent.parent / "docs" / "shell_ux_v1.md").read_text(
        encoding="utf-8")
    at = doc.index("### 9a.")
    sec = doc[at:doc.index("\n---", at)]
    for key in ("GUI key", "**Mic**", "Start", "Moy button"):
        assert key in sec, key
    assert "d0 bit 6" in sec and mi.decode_raw(bytes([1 << 6, 0, 0, 0, 0]))[0] == ("start",)
    for retired in ("own** exit", "has none", "hold gesture can", "chord is an owner"):
        assert retired not in sec, retired
