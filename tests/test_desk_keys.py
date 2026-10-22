"""A desk keyboard's shortcuts, the same on every path a key event takes
(docs/media/look_2026-10/EDITOR_LAYOUT.md's Q-04): the simulator's
(`ConsoleDriver.key_down`), the browser page's (its `kd`/`ku`/`mods` events
through `web_input.apply_events`) and a BLE keyboard's (its HID reports
through the central's machine, `moy_hid.c`). A key event is its code and the
modifiers held with it (native/moy_input/moy_input.h); Super acts on its
release when no other key came while it was held; the letters are a cart's
buttons only while a game owns the keys.

The T-Deck's matrix keyboard has its own path and no Ctrl, Alt or Super
(tests/test_tdeck_keymap.py).
"""

import pytest

from runtime import moy_input as mi, ui_draw, web_input
from test_ble_keyboard import FakeStack, _ready
from ws_helpers import build_desktop_ws, build_ws, make_drv

DT = 1 / 30
PATHS = ("host", "browser", "ble")

# HID usages for the codes these tests type, and the modifier byte's bits.
_USAGE = {mi.KEY_LEFT: 0x50, mi.KEY_RIGHT: 0x4F, mi.KEY_DOWN: 0x51, mi.KEY_UP: 0x52,
          0x0D: 0x28, 0x1B: 0x29, 0x08: 0x2A, 0x09: 0x2B, 0x7F: 0x4C, ord("."): 0x37}
_USAGE.update({ord("a") + i: 0x04 + i for i in range(26)})
_USAGE.update({ord(c): 0x1E + i for i, c in enumerate("1234567890")})
_USAGE.update({mi.KEY_F1 + i: 0x3A + i for i in range(12)})
_HID_MOD = {mi.CTRL: 0x01, mi.SHIFT: 0x02, mi.ALT: 0x04, mi.SUPER: 0x08}


def _hid_mods(mods):
    return sum(b for m, b in _HID_MOD.items() if mods & m)


class Keyboard:
    """One desk keyboard on one path, over workstation `ws`."""

    def __init__(self, ws, path):
        self.ws = ws
        self.path = path
        self.drv = make_drv(ws)
        if path == "ble":
            self.m = mi.HidMachine(ws.input, FakeStack())
            _ready(self.m)

    def frame(self, n=1):
        for _ in range(n):
            if self.path == "ble":
                self.m.frame()
            self.drv.frame(DT)

    def _send(self, events):
        d = self.drv
        web_input.apply_events(events, d.input, d.pointer, on_key=d.type_char,
                               on_key_down=d.key_down, on_key_up=d.key_up,
                               on_mods=d.set_mods)

    def mods(self, mods):
        """The modifiers held now (a modifier key went down or up)."""
        if self.path == "host":
            self.drv.set_mods(mods)
        elif self.path == "browser":
            self._send([{"type": "mods", "mods": mods}])
        else:
            self.m.notify(3, 3, bytes((_hid_mods(mods), 0, 0, 0, 0, 0, 0, 0)))

    def down(self, code, mods=0):
        if self.path == "host":
            self.drv.key_down(code, mods)
        elif self.path == "browser":
            self._send([{"type": "kd", "code": code, "mods": mods}])
        else:
            u = _USAGE[code | 0x20 if 0x41 <= code <= 0x5A else code]
            self.m.notify(3, 3, bytes((_hid_mods(mods), 0, u, 0, 0, 0, 0, 0)))

    def up(self, code, mods=0):
        if self.path == "host":
            self.drv.key_up(code, mods)
        elif self.path == "browser":
            self._send([{"type": "ku", "code": code, "mods": mods}])
        else:
            self.m.notify(3, 3, bytes((_hid_mods(mods), 0, 0, 0, 0, 0, 0, 0)))

    def tap(self, code, mods=0):
        """A chord: its modifiers down, the key down and up, the modifiers up."""
        if mods:
            self.mods(mods)
            self.frame()
        self.down(code, mods)
        self.frame()
        self.up(code, mods)
        self.frame()
        if mods:
            self.mods(0)
            self.frame()


@pytest.fixture(params=PATHS)
def path(request, monkeypatch):
    """A board's BLE keyboard writes a board's table: every button, `start`
    and `full` among them, which the host's eight do not hold."""
    if request.param == "ble":
        from runtime import host_app
        monkeypatch.setattr(host_app, "InputState", mi.InputTable)
    return request.param


def _sprite(tmp_path, path):
    ws = build_ws(tmp_path)
    ws.launcher.sel = 0
    ws.open()
    ws._open_paint()
    assert ws.menu_view == "paint"
    kb = Keyboard(ws, path)
    ui_draw.input_kind("keys")
    pe = ws.paint
    pe.clear()
    pe.color = 7
    pe.begin_stroke()
    pe.paint(0, 0)
    pe.end_stroke()
    kb.frame(2)
    return ws, kb


def _px(ws, x, y):
    ox, oy = ws.sheet.tile_origin(ws.paint.n)
    return ws.sheet.pget(ox + x, oy + y)


def test_ctrl_z_and_ctrl_y_walk_the_sprite_tabs_history(tmp_path, path):
    ws, kb = _sprite(tmp_path, path)
    assert _px(ws, 0, 0) == 7
    kb.tap(ord("z"), mi.CTRL)
    assert _px(ws, 0, 0) == 0
    kb.tap(ord("y"), mi.CTRL)
    assert _px(ws, 0, 0) == 7


def test_f_flips_and_s_does_not_shift_the_sprite(tmp_path, path):
    """The owner's report: S in the sprite tab shifted the sprite down,
    because a desk keyboard's letters were the d-pad on every surface."""
    ws, kb = _sprite(tmp_path, path)
    for letter in "sad":
        kb.tap(ord(letter))
        assert _px(ws, 0, 0) == 7, letter
    n = ws.paint.n
    kb.tap(ord("w"))                    # W and Q: the sprite after and before
    assert ws.paint.n == n + 1
    kb.tap(ord("q"))
    assert ws.paint.n == n and _px(ws, 0, 0) == 7
    kb.tap(ord("f"))
    last = ws.paint.dim - 1
    assert _px(ws, last, 0) == 7 and _px(ws, 0, 0) == 0
    kb.tap(mi.KEY_LEFT)                 # the arrows are the shift
    assert _px(ws, last - 1, 0) == 7


def test_ctrl_c_copies_for_the_stamp_and_ctrl_v_pastes(tmp_path, path):
    ws, kb = _sprite(tmp_path, path)
    kb.tap(ord("c"), mi.CTRL)
    assert ws.paint.has_clip
    kb.tap(0x7F)
    assert _px(ws, 0, 0) == 0
    kb.tap(ord("v"), mi.CTRL)
    assert _px(ws, 0, 0) == 7


def test_del_clears_the_sprite(tmp_path, path):
    ws, kb = _sprite(tmp_path, path)
    kb.tap(0x7F)
    assert _px(ws, 0, 0) == 0


def _code(tmp_path, path, text):
    ws = build_ws(tmp_path)
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws.set_menu_view("code")
    ed = ws.editor
    ed.set_text(text)
    kb = Keyboard(ws, path)
    kb.frame(2)
    return ws, kb, ed


def test_tab_indents_and_shift_tab_outdents_in_the_code_tab(tmp_path, path):
    ws, kb, ed = _code(tmp_path, path, "x = 1")
    ed.row, ed.col = 0, 0
    kb.tap(0x09)
    assert ed.lines[0] == "  x = 1"
    kb.tap(0x09, mi.SHIFT)
    assert ed.lines[0] == "x = 1"


def test_ctrl_a_selects_all_and_a_chord_types_nothing(tmp_path, path):
    ws, kb, ed = _code(tmp_path, path, "ab\ncd")
    kb.tap(ord("1"), mi.CTRL)
    kb.tap(0x0D, mi.CTRL)
    kb.tap(ord("x"), mi.ALT)
    assert ed.lines == ["ab", "cd"]
    kb.tap(ord("a"), mi.CTRL)
    assert ed.has_selection()


def test_super_tapped_opens_the_system_menu_and_a_super_chord_does_not(tmp_path, path):
    ws = build_ws(tmp_path)
    kb = Keyboard(ws, path)
    kb.frame(2)
    kb.mods(mi.SUPER)
    kb.frame()
    assert not ws.sysmenu.open, "Super acts on its release"
    kb.mods(0)
    kb.frame(2)
    assert ws.sysmenu.open
    kb.tap(0x1B) if path == "ble" else ws.sysmenu.close()
    kb.frame(2)
    assert not ws.sysmenu.open
    kb.tap(mi.KEY_RIGHT, mi.SUPER)      # Super+Right: a chord
    kb.frame(2)
    assert not ws.sysmenu.open


def test_f11_fills_the_screen_with_the_focused_window(tmp_path, path):
    ws = build_desktop_ws(tmp_path)
    kb = Keyboard(ws, path)
    ws.open_settings()
    kb.frame(2)
    calls = []
    ws.wm.full_key = lambda: calls.append(1) or True
    kb.tap(mi.KEY_F1 + 10)
    assert calls == [1]


def test_shift_tab_steps_the_ui_engines_focus_back(tmp_path, path):
    """Tab steps a page's focus forward and Shift+Tab back (Settings)."""
    ws = build_ws(tmp_path)
    kb = Keyboard(ws, path)
    ws.open_settings()
    kb.frame(3)
    ctx = ws.settings_layer._input_host().ctx
    kb.tap(0x09)
    first = ui_draw._C.api_focus(ctx, None)
    kb.tap(0x09)
    assert ui_draw._C.api_focus(ctx, None) not in (None, first)
    kb.tap(0x09, mi.SHIFT)
    assert ui_draw._C.api_focus(ctx, None) == first


def test_the_arrows_step_the_codes_caret_and_shift_selects(tmp_path, path):
    """A BLE keyboard's arrows (buttons) step the caret as the host's do."""
    ws, kb, ed = _code(tmp_path, path, "abcdef\nghij")
    ed.row, ed.col = 0, 0
    ed.sel = None
    kb.tap(mi.KEY_RIGHT)
    assert (ed.row, ed.col) == (0, 1) and not ed.has_selection()
    kb.tap(mi.KEY_RIGHT, mi.SHIFT)
    kb.tap(mi.KEY_RIGHT, mi.SHIFT)
    assert ed.selected_text() == "bc"
    kb.tap(mi.KEY_DOWN)
    assert ed.row == 1 and not ed.has_selection()


def test_an_apps_letters_are_keys_too(tmp_path, path):
    """While an app (Notes, Files, Calc) runs, the letters are keys: only a
    game's are its buttons."""
    from ws_helpers import open_cart
    ws = build_ws(tmp_path)
    open_cart(ws, "Calc")
    kb = Keyboard(ws, path)
    kb.frame(3)
    assert not ws.input.cart_keys
    open_cart(ws, "Star Catcher")
    kb.frame(3)
    assert ws.input.cart_keys


def test_a_tap_inside_one_frame_still_presses(tmp_path):
    """The page batches a frame's events: a key down and up in one batch
    presses its button for one frame all the same."""
    ws, kb, ed = _code(tmp_path, "browser", "abcdef")
    ed.row, ed.col = 0, 0
    kb._send([{"type": "kd", "code": mi.KEY_RIGHT, "mods": 0},
              {"type": "ku", "code": mi.KEY_RIGHT, "mods": 0}])
    kb.frame(2)
    assert (ed.row, ed.col) == (0, 1)
