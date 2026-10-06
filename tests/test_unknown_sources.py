"""Settings -> UNKNOWN SOURCES (owner, 2026-09-29; docs/wasm_tier_plan_2026-09.md).

The owner's switch for running a compiled cart whose module carries no
signature -- a cart somebody rebuilt from its source, on their own console.
Only a board's engine checks signatures (tests/test_wasm_signing.py holds the
load decision, the on-glass suites hold the board to it); what is pinned here
is the setting every tier carries:

  * it is OFF on a fresh console, persists under its own key and comes back;
  * turning it ON goes through a warning with KEEP OFF focused, and only the
    warning's own button (or a deliberate move and a press) turns it on;
  * turning it OFF is immediate;
  * the dev channel's word sets it without the warning and without
    persisting, and `state` reports it;
  * the Player's notice for an unsigned cart names the switch, on the notice
    panel, and a module whose signature fails keeps the error panel.
"""

import os

from runtime import host_app
from runtime.settings_layer import TOGGLE_CONFIRMS
from ws_helpers import build_ws

KEY = "unknown_sources"
DT = 1 / 30


def _settings(tmp_path, **kw):
    ws = build_ws(tmp_path, **kw)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    drv.frame(DT)
    return ws, drv


def _row_center(ws):
    """Scroll the UNKNOWN SOURCES row into view and return where to tap it."""
    sl = ws.settings_layer
    i = [r[0] for r in sl._settings_rows()].index(KEY)
    sl.set_msel = i
    sl._settings_scroll()
    x, y, w, h = sl._settings_row_rect(i)
    return i, x + w // 2, y + h // 2


def _hit_center(ws, verb):
    for rect, v, _arg in ws.settings_layer._confirm_hits._items:
        if v == verb:
            x, y, w, h = rect
            return x + w // 2, y + h // 2
    raise AssertionError("the warning drew no %r button" % verb)


def test_a_fresh_console_has_it_off_and_its_row_on_every_tier(tmp_path):
    for n, kw in enumerate(({}, dict(sys_size=(1024, 600), font_scale=2,
                                     windowed=True))):
        ws = build_ws(tmp_path / str(n), **kw)
        assert ws.unknown_sources is False
        assert ws.system.text(KEY) is None
        rows = ws.settings_layer._settings_rows()
        assert (KEY, "UNKNOWN SOURCES", "diag") in rows


def test_it_persists_under_its_own_key_and_comes_back_on(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    ws.set_unknown_sources(True)
    assert ws.system.get(KEY) is True
    again = host_app.build_workstation(carts)
    assert again.unknown_sources is True
    again.set_unknown_sources(False)
    assert host_app.build_workstation(carts).unknown_sources is False


def test_tapping_the_row_opens_the_warning_and_changes_nothing(tmp_path):
    ws, drv = _settings(tmp_path)
    _i, x, y = _row_center(ws)
    drv.frame(DT)
    drv.click(x, y)
    drv.frame(DT)
    sl = ws.settings_layer
    assert sl.confirm_key == KEY
    assert sl.confirm_sel == 0, "KEEP OFF has the focus when it opens"
    assert ws.unknown_sources is False and ws.system.text(KEY) is None
    title, text, yes = TOGGLE_CONFIRMS[KEY]
    assert title == "UNKNOWN SOURCES" and yes == "TURN ON"
    assert text == ("Unsigned carts can do anything on this console. "
                    "Only run ones you trust.")
    drv.frame(DT)
    verbs = [v for _r, v, _a in sl._confirm_hits._items]
    assert verbs == ["keep", "accept"]


def test_keep_off_closes_the_warning_with_the_switch_off(tmp_path):
    ws, drv = _settings(tmp_path)
    ws.settings_layer._toggle_diag_row(KEY)
    drv.frame(DT)
    drv.click(*_hit_center(ws, "keep"))
    drv.frame(DT)
    assert ws.settings_layer.confirm_key is None
    assert ws.unknown_sources is False and ws.system.text(KEY) is None


def test_a_tap_elsewhere_on_the_warning_does_nothing(tmp_path):
    ws, drv = _settings(tmp_path)
    ws.settings_layer._toggle_diag_row(KEY)
    drv.frame(DT)
    lay = ws.layout
    drv.click(lay.set_x + 20, lay.set_row_y0 + 30)      # the warning's text
    drv.frame(DT)
    assert ws.settings_layer.confirm_key == KEY
    assert ws.unknown_sources is False


def test_turn_on_turns_it_on_and_persists_it(tmp_path):
    ws, drv = _settings(tmp_path)
    ws.settings_layer._toggle_diag_row(KEY)
    drv.frame(DT)
    drv.click(*_hit_center(ws, "accept"))
    drv.frame(DT)
    assert ws.settings_layer.confirm_key is None
    assert ws.unknown_sources is True and ws.system.get(KEY) is True


def test_on_the_keyboard_turning_it_on_takes_a_move_and_a_press(tmp_path):
    ws, drv = _settings(tmp_path)
    sl = ws.settings_layer
    i, _x, _y = _row_center(ws)

    def key(name):
        drv.press(name)
        drv.frame(DT)
        drv.frame(DT)                    # released: the next press is an edge

    drv.frame(DT)
    key("a")                             # A on the row opens the warning
    assert sl.confirm_key == KEY and ws.unknown_sources is False
    key("a")                             # ...and A again presses KEEP OFF
    assert sl.confirm_key is None and ws.unknown_sources is False
    sl.set_msel = i
    key("right")                         # a step on the row is the same door
    assert sl.confirm_key == KEY
    key("b")                             # B backs out with nothing changed
    assert sl.confirm_key is None and ws.unknown_sources is False
    sl.set_msel = i
    key("a")
    key("right")
    assert sl.confirm_sel == 1
    key("left")
    assert sl.confirm_sel == 0
    key("right")
    key("a")
    assert sl.confirm_key is None
    assert ws.unknown_sources is True and ws.system.get(KEY) is True


def test_turning_it_off_is_immediate(tmp_path):
    ws, drv = _settings(tmp_path)
    ws.set_unknown_sources(True)
    _i, x, y = _row_center(ws)
    drv.frame(DT)
    drv.click(x, y)
    drv.frame(DT)
    assert ws.settings_layer.confirm_key is None
    assert ws.unknown_sources is False and ws.system.get(KEY) is False


def test_the_other_toggles_flip_without_a_warning(tmp_path):
    ws, _drv = _settings(tmp_path)
    assert set(TOGGLE_CONFIRMS) == {KEY}
    was = ws.show_fps
    ws.settings_layer._toggle_diag_row("show_fps")
    assert ws.settings_layer.confirm_key is None and ws.show_fps is (not was)


def test_leaving_settings_takes_the_warning_down(tmp_path):
    ws, drv = _settings(tmp_path)
    ws.settings_layer._toggle_diag_row(KEY)
    drv.frame(DT)
    ws.go_home()
    ws.open_settings()
    drv.frame(DT)
    assert ws.settings_layer.confirm_key is None
    assert ws.unknown_sources is False


def test_the_warning_in_the_windowed_settings(tmp_path):
    """The desk tier's Settings WINDOW: the same warning, its buttons tapped
    in window-local coordinates."""
    ws, drv = _settings(tmp_path, sys_size=(1024, 600), font_scale=2,
                        windowed=True)
    ws.settings_layer._toggle_diag_row(KEY)
    drv.frame(DT)
    win = ws.wm._wins["settings"]
    x, y = _hit_center(ws, "accept")
    drv.click(win.x + 1 + x, win.y + 1 + win.title_h + y)
    drv.frame(DT)
    assert ws.unknown_sources is True


def test_the_dev_channel_sets_it_without_the_warning_or_persisting(tmp_path, capsys):
    from runtime.dev_channel import DevChannel, _remote_state
    from tests.test_dev_channel import FakePointer
    ws = build_ws(tmp_path)
    ch = DevChannel(ws, FakePointer())
    capsys.readouterr()
    assert _remote_state(ws)["unknown_sources"] is False
    ch.run(ws, "unknown_sources 1")
    assert capsys.readouterr().out.strip() == "REMOTE unknown_sources on"
    assert ws.unknown_sources is True and ws.system.text(KEY) is None
    assert ws.settings_layer.confirm_key is None
    st = _remote_state(ws)
    assert st["unknown_sources"] is True
    assert st["settings"]["confirm"] is None
    ch.run(ws, "unknown_sources 0")
    assert capsys.readouterr().out.strip() == "REMOTE unknown_sources off"
    assert _remote_state(ws)["unknown_sources"] is False
    ws.settings_layer._toggle_diag_row(KEY)
    assert _remote_state(ws)["settings"]["confirm"] == KEY


# -- the Player's notice for an unsigned cart ----------------------------------


class _Refusing:
    """A board's wasm runtime whose engine refused the module with `text`."""

    def __init__(self, text):
        self.text = text

    def __call__(self, ns, src):
        raise RuntimeError(self.text)


def _hello_store(tmp_path):
    from tools import wasm_cart
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    wasm_cart.build(os.path.join(os.path.dirname(__file__), "fixtures", "wasm",
                                 "hello.moy"), os.path.join(root, "hello.moy"))
    return root


class _Interp:
    """A successful wasm run that landed on the interpreter -- what
    device/moycore_glue.WasmRun looks like once its own retry took over for
    an unsigned module (the switch off) or a stale/foreign one
    (docs/wasm_tier_plan_2026-09.md, "A cart survives its firmware",
    2026-09-30). Neither case reaches the Player as a failure any more; both
    look like this from here, a plain successful run with `.interp` set and
    `.interp_cause` naming which of the two it was."""

    def __init__(self, ns, src, cause="unsigned"):
        del ns, src
        self.interp = True
        self.interp_cause = cause
        self.init = None
        self.draw_next = True

    def update(self, dt):
        del dt

    def draw(self):
        pass

    def close(self):
        pass


def _open_interp_hello(tmp_path, cause):
    from ws_helpers import open_cart
    ws = host_app.build_workstation(_hello_store(tmp_path))
    ws.runtimes["wasm"] = lambda ns, src: _Interp(ns, src, cause=cause)
    open_cart(ws, "Hello Wasm")
    return ws


def test_an_unsigned_cart_plays_on_the_interpreter_with_a_notice(tmp_path):
    """With the switch off an unsigned module is not refused: WasmRun
    already retried it on the interpreter before the Player ever saw an
    error, so the cart plays -- no notice panel, no error -- and the Player
    arms the short system notice that says the cart isn't signed."""
    from runtime import bar_layer, player
    from runtime.dev_channel import _remote_state
    ws = _open_interp_hello(tmp_path, "unsigned")
    p = ws.player
    assert p.notice is None and p.cart_error is None
    assert p._lua is not None and p._lua.interp
    assert not ws.wm.top_is("menu")
    st = _remote_state(ws)
    assert st["notice"] is None and st["cart_error"] is None
    assert bar_layer._edit_kind(ws.cart) is None
    assert ws._notice == (player.INTERP_NOTICE_TITLE,
                          player.INTERP_NOTICE_SUB["unsigned"], "warn")
    assert ws.notice_active()


def test_a_cart_with_no_matching_module_plays_with_a_needs_update_notice(tmp_path):
    """The same fallback, for the other cause: no module by this console's
    name at all, or one left over from a format this console has moved past.
    The Player's notice reads "needs an update" rather than "isn't signed"."""
    from runtime import player
    ws = _open_interp_hello(tmp_path, "missing")
    assert ws._notice == (player.INTERP_NOTICE_TITLE,
                          player.INTERP_NOTICE_SUB["missing"], "warn")
    assert ws.notice_active()


def test_a_module_this_firmware_cannot_link_blames_the_console(tmp_path):
    """The third cause: the module is this console's, and the load stopped on
    a helper the firmware's runtime does not register (#229). The cart is not
    what needs an update, the console is, and the sub-line says so -- within
    the characters the notice prints."""
    from runtime import player
    ws = _open_interp_hello(tmp_path, "firmware")
    sub = player.INTERP_NOTICE_SUB["firmware"]
    assert ws._notice == (player.INTERP_NOTICE_TITLE, sub, "warn")
    assert sub != player.INTERP_NOTICE_SUB["missing"]
    printed = []
    ws.sys_canvas.print = lambda text, *a, **k: printed.append(text)
    for text in player.INTERP_NOTICE_SUB.values():
        ws._notice = (player.INTERP_NOTICE_TITLE, text, "warn")
        del printed[:]
        ws._draw_notice()
        assert text in printed, (text, printed)


def test_a_module_whose_signature_fails_keeps_the_error_panel(tmp_path):
    """Tamper evidence is the one refusal WasmRun never retries -- it is
    the engine's own decision, made before the Player is asked anything --
    so from here it is still an ordinary failed run."""
    from ws_helpers import open_cart
    ws = host_app.build_workstation(_hello_store(tmp_path))
    for text in ("refused: bad signature", "refused: malformed signature"):
        ws.runtimes["wasm"] = _Refusing(text)
        open_cart(ws, "Hello Wasm")
        assert ws.player.notice is None
        assert ws.player.cart_error.endswith(text)
        ws._exit_to_caller()
