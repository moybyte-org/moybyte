"""The T-Deck's W A S D move wherever letters do not type (owner call
2026-10-23, #242): beside the trackball they step the home, lists, grids,
menus, popovers and dialog rows, and there they are no letter, so no verb
answers them (native/moy_input/moy_kbd.c, THE MOVE KEYS). A text surface
types them; a game gets its buttons and its key() both.

The console end of it is driven the way the glass is: the C3's five matrix
bytes through the board's own keyboard driver (moy_kbd.c) into the console's
input table, every frame, with the console telling the driver its text mode
and its cart keys as it does on the board.
"""

from pathlib import Path

from runtime import host_app, ui_draw
from runtime import moy_input as mi
from runtime.host_canvas import make_system_canvas
from test_tdeck_input import _Kbd, _frame, _keyboard
from test_tdeck_keymap import _chord, _input_module
from ws_helpers import build_ws, build_ws_with_shelf

import pytest

IDLE = bytes(5)


def _matrix(*codes):
    """The five matrix bytes with these plain keys held."""
    return bytes(_chord(_input_module(), *codes))


def _sym(code):
    return bytes(_chord(_input_module(), code, sym=True))


# -- the driver -----------------------------------------------------------------

@pytest.mark.parametrize("raw", [True, False])
def test_a_move_key_presses_its_direction_and_types_nothing_off_text(raw):
    want = {"w": "up", "a": "left", "s": "down", "d": "right"}
    for ch, button in want.items():
        data = _matrix(ord(ch)) if raw else bytes([ord(ch)])
        table, kbd, _fake = _keyboard(data, raw=raw, cart=False)
        _frame(kbd, table)
        assert table.held(button), ch
        assert table.last_key == 0, "%s is a move here, no letter" % ch


@pytest.mark.parametrize("raw", [True, False])
def test_every_other_key_still_types_off_text(raw):
    """C is the home's Change and keeps its letter; Sym+W is '1', no move."""
    for data, key in (((_matrix(ord("c")) if raw else b"c"), ord("c")),
                      ((_sym(ord("w")) if raw else b"1"), ord("1"))):
        table, kbd, _fake = _keyboard(data, raw=raw, cart=False)
        _frame(kbd, table)
        assert table.last_key == key
        assert not table.held("up")


def test_a_text_surface_types_the_move_keys_and_a_game_gets_both():
    table, kbd, fake = _keyboard(IDLE, raw=True, cart=False)
    table.text_mode = True
    _frame(kbd, table)
    fake.frames = _matrix(ord("w"))
    _frame(kbd, table)
    assert table.last_key == ord("w") and not table.held("up"), "a field types W"
    table, kbd, _fake = _keyboard(_matrix(ord("w")), raw=True)
    _frame(kbd, table)
    assert table.last_key == ord("w") and table.held("up"), "a game reads both"


# -- the console ----------------------------------------------------------------

class _TDeck:
    """The T-Deck's keyboard on a host console: the driver over the
    console's own input table, attached as the board attaches it."""

    def __init__(self, ws, drv):
        self.ws, self.drv = ws, drv
        self.fake = _Kbd([b"\x00"])
        self.kbd = mi.KbdDriver(ws.input, self.fake)
        self.fake.frames = IDLE
        ws.keyboard = self.kbd
        ws.sync_keyboard_mode()
        ui_draw.input_kind("tdeck")
        self.tick()

    def tick(self, n=1):
        for _ in range(n):
            self.kbd.pass_()
            self.drv.frame(1 / 30)

    def key(self, data, settle=8):
        """One press and release, then `settle` frames for a glide."""
        if isinstance(data, str):
            data = _matrix(ord(data))
        self.fake.frames = data
        self.tick()
        self.fake.frames = IDLE
        self.tick(settle)


@pytest.fixture(autouse=True)
def _keys_after():
    yield
    ui_draw.input_kind("keys")


def _console(tmp_path, n=6):
    ws = build_ws_with_shelf(Path(tmp_path), n)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    return ws, drv, _TDeck(ws, drv)


def _pixels(drv):
    return bytes(drv.rgb888())


def _make_card(ws, t):
    ws.set_home_tab("make")
    t.tick(4)
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items) if it.get("path"))
    t.tick(2)


def test_d_and_a_glide_the_homes_carousel(tmp_path):
    ws, _drv, t = _console(tmp_path)
    assert ws.launcher.sel == 0
    t.key("d")
    t.key("d")
    assert ws.launcher.sel == 2
    t.key("a")
    assert ws.launcher.sel == 1
    t.key("w")
    assert ws.launcher_layer._tab_focus, "W from the carousel takes the tab row"


def test_s_and_w_step_settings_rows(tmp_path):
    ws, drv, t = _console(tmp_path)
    ws.open_settings()
    t.tick(4)
    before = _pixels(drv)
    t.key("s")
    moved = _pixels(drv)
    assert moved != before, "S steps the focus down a row"
    t.key("w")
    assert _pixels(drv) == before, "W steps it back"


def test_a_menu_over_the_code_tab_steps_and_gives_the_tab_its_text_back(tmp_path):
    """The Code tab types, so its keyboard is in text mode; the system menu
    over it is rows, so W A S D step them, and K backs out. Closing it
    hands the tab its typing back, with no letter typed into the code."""
    ws, drv, t = _console(tmp_path)
    _make_card(ws, t)
    t.key("c")
    ws.set_menu_view("code")
    t.tick(2)
    assert ws.input.text_mode
    src = ws.editor.text()
    ws.system_key()
    t.tick(2)
    assert not ws.input.text_mode
    before = _pixels(drv)
    t.key("s")
    assert _pixels(drv) != before, "S steps the menu's rows"
    ws.system_key()
    t.tick(2)
    assert ws.input.text_mode and ws.menu_view == "code"
    assert ws.editor.text() == src


def test_a_menu_row_that_opens_a_surface_keeps_its_mode(tmp_path):
    """What the menu did wins over what it covered: Settings opened from the
    system menu over the Code tab is no text surface."""
    ws, _drv, t = _console(tmp_path)
    _make_card(ws, t)
    t.key("c")
    ws.set_menu_view("code")
    t.tick(2)
    ws.system_key()
    t.tick(2)
    ws.sysmenu.close()
    ws.open_settings()
    t.tick(3)
    assert ws.screen == "settings" and not ws.input.text_mode


def test_files_rows_step_and_its_rename_field_types(tmp_path):
    ws, drv, t = _console(tmp_path)
    ws.open_app(ws._apps_by_id["files"])
    t.tick(3)
    assert not ws.input.text_mode, "Files' rows are no text surface"
    before = _pixels(drv)
    t.key("s")
    assert _pixels(drv) != before
    files = ws.files_app
    files.dialog = "rename"
    files.rename_text = ""
    t.tick(2)
    assert ws.input.text_mode
    t.key("w", settle=2)
    assert files.rename_text.endswith("w"), "the field types W"
    files.dialog = None
    t.tick(2)
    assert not ws.input.text_mode


def test_a_and_d_step_the_delete_confirms_buttons(tmp_path):
    ws, _drv, t = _console(tmp_path)
    _make_card(ws, t)
    ws.confirm.ask(ws.launcher.selected())
    t.tick(2)
    assert ws.confirm.open
    t.key("d", settle=3)
    first = ws.confirm.focused
    t.key("a", settle=3)
    assert ws.confirm.focused != first, "A and D move between Cancel and Delete"
    assert ws.confirm.open


def test_the_sprite_tab_w_moves_the_cursor_and_e_steps_the_sprite(tmp_path):
    ws, _drv, t = _console(tmp_path)
    _make_card(ws, t)
    t.key("c")
    ws.set_menu_view("paint")
    t.tick(2)
    pe = ws.paint
    cur = ws.paint_layer._cur
    cur[0], cur[1] = 3, 3
    n = pe.n
    t.key("w", settle=2)
    assert (cur[0], cur[1]) == (3, 2) and pe.n == n, "W is up, not the next sprite"
    t.key("e", settle=2)
    assert pe.n == n + 1
    t.key("q", settle=2)
    assert pe.n == n


def test_my_art_w_is_no_wallpaper_on_the_tdeck(tmp_path):
    ws, _drv, t = _console(tmp_path)
    ws.open_app(ws._apps_by_id["myart"])
    t.tick(3)
    calls = []
    art = ws.myart_app
    art._verb = lambda v, *a, **k: calls.append(v)
    t.key("w", settle=2)
    assert calls == []
    t.key("m", settle=2)
    assert calls == ["menu"], "M still opens the card menu"


# -- the hints ------------------------------------------------------------------

BAND = (0, 220, 320, 20)


@pytest.fixture(scope="module")
def th(tmp_path_factory):
    ws = build_ws(tmp_path_factory.mktemp("hints"))
    ws.look.set_theme("moy", persist=False, variant="dark")
    return ws.theme_colors


def _strip(th, items, kind):
    ui_draw.input_kind(kind)
    cv = make_system_canvas(320, 240)
    spans = ui_draw.hints(cv, th, BAND, items)
    return bytes(cv._buf), spans


def test_the_tdeck_hints_name_wasd_beside_the_trackball(th):
    """Change is A and D beside the trackball's sign, a move WASD, a scroll
    W and S; every other input draws what it drew before."""
    assert _strip(th, ("change", "Change"), "tdeck") == _strip(th, ("A D ball_lr", "Change"), "tdeck")
    assert _strip(th, ("move", "Pan"), "tdeck") == _strip(th, ("WASD", "Pan"), "tdeck")
    assert _strip(th, ("scroll", "Scroll"), "tdeck") == _strip(th, ("W S", "Scroll"), "tdeck")
    for kind in ("keys", "pad"):
        assert _strip(th, ("scroll", "Scroll"), kind) == _strip(th, ("up down", "Scroll"), kind)
        assert _strip(th, ("move", "Pan"), kind) == _strip(th, ("up down left right", "Pan"), kind)


def test_a_verb_spelled_with_a_move_key_shows_no_key_on_the_tdeck(th):
    """My Art's and Paint's W (Use as wallpaper) is a move on the T-Deck:
    a hint or a menu row spelled with it draws no key there, and its row is
    still the way to it."""
    blank = bytes(make_system_canvas(320, 240)._buf)
    assert _strip(th, ("W", "Wallpaper"), "tdeck") == (blank, ((5, 5),))
    assert _strip(th, ("W", "Wallpaper"), "keys")[0] != blank
    assert _strip(th, ("C", "Colours"), "tdeck")[0] != blank
