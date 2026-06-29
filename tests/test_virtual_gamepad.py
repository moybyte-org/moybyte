"""Virtual gamepad (#42 Thread 1): an on-screen d-pad + A/B overlay drawn over a
running cart that feeds the EXISTING btn()/btnp() so button-based carts become
touch-playable with ZERO cart changes.

These drive the SHARED console through the host ConsoleDriver (the same per-frame
model the simulator + device use). They pin:
  * a touch on a pad zone holds that button (btn) and fires ONE btnp press edge;
  * a held touch keeps btn() down without re-firing btnp;
  * a pad-zone touch is CONSUMED (never leaks to touch()), while a non-pad touch
    passes through to touch() unchanged;
  * the Settings GAMEPAD toggle persists to system.json and the default is
    backend-chosen (off here, on for the touch-only web build);
  * with the pad OFF, behavior is byte-identical to before (no injection, touches
    reach touch());
  * a gamepad B press reaches the CART (not the in-cart EDIT shortcut), and the
    keyboard B shortcut still opens the editor (no regression);
  * inject_button exists + behaves identically in BOTH input backends (host ==
    device), and console.py wires the overlay into the running-cart frame.
"""

import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# A cart that records what btn()/btnp()/touch() see each frame.
PAD_CART_SRC = """
held = []
presses = 0
taps = []

def _update(dt):
    global presses
    for b in ("up", "down", "left", "right", "a", "b"):
        if btn(b):
            held.append(b)
        if btnp(b):
            presses = presses + 1
    t = touch()
    if t and t[2]:
        taps.append((t[0], t[1]))

def _draw():
    cls(0)
"""


def _open(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            break
    ws.open()


def _make_ws(tmp_path, gamepad_default=True, src=PAD_CART_SRC):
    from runtime import host_app
    from runtime import kid_carts

    carts_dir = str(tmp_path / "carts")
    os.makedirs(carts_dir, exist_ok=True)
    kid_carts.create("Pad", carts_dir, src=src, type="game")
    return host_app.build_workstation(carts_dir, gamepad_default=gamepad_default)


def _zone_center(name):
    import runtime.console as C

    rect = {"up": C._GP_UP, "down": C._GP_DOWN,
            "left": C._GP_LEFT, "right": C._GP_RIGHT}[name]
    return (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)


# --- hit-testing -> button held -------------------------------------------

def test_pad_touch_holds_button(tmp_path):
    from runtime import host_app

    ws = _make_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _open(ws, "Pad")
    assert ws.screen == "desktop" and ws.cart_error is None
    assert ws._gamepad_on() is True

    gx, gy = _zone_center("right")
    drv.touch(gx, gy)
    drv.frame(1.0 / 30)
    assert "right" in ws.ns["held"]      # btn("right") sees it down this frame


def test_each_dpad_and_ab_zone_maps_to_its_button(tmp_path):
    import runtime.console as C
    from runtime import host_app

    ws = _make_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _open(ws, "Pad")

    for name in ("up", "down", "left", "right"):
        gx, gy = _zone_center(name)
        assert ws._gamepad_hit(gx, gy) == [name], name
    for name, (cx, cy) in C._GP_ABTN:
        assert name in ws._gamepad_hit(cx, cy)
    # The hub (center of the cross) is art-only: no button.
    hx, hy, hw, hh = C._GP_HUB
    assert ws._gamepad_hit(hx + hw // 2, hy + hh // 2) == []
    # A point in the open play area presses nothing.
    assert ws._gamepad_hit(160, 120) == []


# --- btnp edge fires once per press ---------------------------------------

def test_btnp_fires_once_per_press_not_on_hold(tmp_path):
    from runtime import host_app

    ws = _make_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _open(ws, "Pad")

    import runtime.console as C
    ax, ay = C._GP_A
    drv.touch(ax, ay)
    drv.frame(1.0 / 30)
    assert ws.ns["presses"] == 1                 # one press edge

    drv.touch_drag(ax, ay)                        # finger still down, no new tap
    drv.frame(1.0 / 30)
    drv.touch_drag(ax, ay)
    drv.frame(1.0 / 30)
    assert ws.ns["presses"] == 1                 # held -> no re-fire

    drv.touch_up()
    drv.frame(1.0 / 30)
    # Press again -> a fresh edge.
    drv.touch(ax, ay)
    drv.frame(1.0 / 30)
    assert ws.ns["presses"] == 2


def test_btnp_released_edge_clears(tmp_path):
    # After release the button is no longer held (and stays released).
    from runtime import host_app

    ws = _make_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _open(ws, "Pad")
    gx, gy = _zone_center("left")
    drv.touch(gx, gy)
    drv.frame(1.0 / 30)
    drv.touch_up()
    ws.ns["held"].clear()
    drv.frame(1.0 / 30)
    assert "left" not in ws.ns["held"]
    assert ws.input.held("left") is False


# --- coexistence with touch() ---------------------------------------------

def test_pad_zone_touch_does_not_leak_to_touch(tmp_path):
    from runtime import host_app

    ws = _make_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _open(ws, "Pad")
    gx, gy = _zone_center("up")
    drv.touch(gx, gy)
    drv.frame(1.0 / 30)
    assert "up" in ws.ns["held"]
    assert ws.ns["taps"] == []                   # consumed by the pad


def test_non_pad_touch_passes_through_to_touch(tmp_path):
    from runtime import host_app

    ws = _make_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _open(ws, "Pad")
    drv.touch(160, 120)                          # open play area, no zone
    drv.frame(1.0 / 30)
    assert ws.ns["taps"] == [(160, 120)]
    assert ws.ns["held"] == []                   # no button injected


# --- the Settings toggle persists -----------------------------------------

def test_gamepad_toggle_persists_to_system_json(tmp_path):
    from runtime import host_app
    from runtime import kid_carts

    ws = _make_ws(tmp_path, gamepad_default=False)
    assert ws._gamepad_on() is False             # backend default (T-Deck-like) off

    # Flip it on via the Settings adjust path (the GAMEPAD row).
    rows = ws._settings_rows()
    keys = [r[0] for r in rows]
    assert "gamepad" in keys
    ws.set_msel = keys.index("gamepad")
    ws.settings_adjust(1)
    assert ws._gamepad_on() is True
    assert ws.system.get("gamepad") is True

    # Persisted: a fresh Workstation over the SAME carts dir reads it back ON, even
    # with the backend default OFF (the saved choice wins).
    carts_dir = ws.carts_root
    ws2 = host_app.build_workstation(carts_dir, gamepad_default=False)
    assert ws2.system.get("gamepad") is True
    assert ws2._gamepad_on() is True


def test_gamepad_default_unset_falls_back_to_backend(tmp_path):
    # With no saved value, _gamepad_on() returns the backend default either way.
    ws_on = _make_ws(tmp_path / "a", gamepad_default=True)
    assert "gamepad" not in ws_on.system
    assert ws_on._gamepad_on() is True

    ws_off = _make_ws(tmp_path / "b", gamepad_default=False)
    assert "gamepad" not in ws_off.system
    assert ws_off._gamepad_on() is False


# --- gamepad off = zero behavior change ------------------------------------

def test_gamepad_off_is_zero_behavior_change(tmp_path):
    from runtime import host_app

    ws = _make_ws(tmp_path, gamepad_default=False)   # off
    drv = host_app.ConsoleDriver(ws)
    _open(ws, "Pad")
    assert ws._gamepad_on() is False

    import runtime.console as C
    # A touch where a pad zone WOULD be: with the pad off it injects nothing AND the
    # touch reaches touch() like any other (the play area is unobstructed).
    gx, gy = _zone_center("right")
    drv.touch(gx, gy)
    drv.frame(1.0 / 30)
    assert ws.ns["held"] == []
    assert ws.ns["taps"] == [(gx, gy)]
    # The overlay also never lights any zone.
    assert ws._gp_zones == ()


# --- B routing: gamepad B -> cart, keyboard B -> editor (no regression) ----

def test_gamepad_b_reaches_cart_not_editor(tmp_path):
    import runtime.console as C
    from runtime import host_app

    ws = _make_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _open(ws, "Pad")
    bx, by = C._GP_B
    drv.touch(bx, by)
    drv.frame(1.0 / 30)
    assert ws.screen == "desktop"                # did NOT open the editor menu
    assert "b" in ws.ns["held"]                  # the cart saw the B button


def test_keyboard_b_still_opens_editor(tmp_path):
    from runtime import host_app

    ws = _make_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _open(ws, "Pad")
    drv.press("b")                               # a keyboard B press edge
    drv.frame(1.0 / 30)
    assert ws.screen == "menu"                   # in-cart B shortcut unaffected


# --- the overlay draws (no exception) + only on the running cart -----------

def test_overlay_draws_over_running_cart(tmp_path):
    from runtime import host_app

    ws = _make_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _open(ws, "Pad")
    before = ws._frames_drawn
    drv.frame(1.0 / 30)
    assert ws._frames_drawn > before             # the cart frame (with overlay) painted

    # Leaving the cart releases anything the pad held (no stale stuck button).
    import runtime.console as C
    gx, gy = _zone_center("right")
    drv.touch(gx, gy)
    drv.frame(1.0 / 30)
    assert ws.input.held("right") is True
    drv.touch_up()
    ws.go_home()
    drv.frame(1.0 / 30)
    assert ws.input.held("right") is False
    assert ws._gp_zones == ()


# --- structural: console parses; backends agree (host == device) -----------

def test_console_py_parses():
    src = (ROOT / "runtime" / "console.py").read_text(encoding="utf-8")
    ast.parse(src)               # the overlay edits must not break the module


def test_inject_button_in_both_input_backends():
    # host == device: inject_button is the shared injection seam. Load BOTH input
    # modules and assert the same held + edge semantics from identical calls.
    import importlib.util

    def _load(path, name):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    host_in = _load(ROOT / "runtime" / "input.py", "_gp_host_input")
    dev_in = _load(
        ROOT / "firmware" / "lilygo_t_deck_plus_micropython" / "modules"
        / "kidcode" / "input.py",
        "_gp_dev_input",
    )
    for InputState in (host_in.InputState, dev_in.InputState):
        i = InputState()
        i.begin_frame()
        # A fresh press: held + a forced press edge, no released.
        i.inject_button("right", True, pressed=True)
        assert i.held("right") is True
        assert i.pressed("right") is True
        assert i.released("right") is False
        # Next frame, still held but no fresh edge.
        i.begin_frame()
        i.inject_button("right", True, pressed=False)
        assert i.held("right") is True
        assert i.pressed("right") is False
        # Release fires a released edge, drops held.
        i.begin_frame()
        i.inject_button("right", False, released=True)
        assert i.held("right") is False
        assert i.released("right") is True


def test_console_wires_gamepad_into_running_cart():
    # The shared console (frozen onto the device verbatim) must wire the overlay into
    # the running-cart path: draw it after the cart, inject in handle_input, expose the
    # toggle + resolver. Grep the source the way the spike tests pin frozen wiring.
    src = (ROOT / "runtime" / "console.py").read_text(encoding="utf-8")
    assert "_draw_gamepad" in src
    assert "_gamepad_inject" in src
    assert "inject_button" in src
    assert "_gamepad_on" in src
    assert '"gamepad"' in src or "'gamepad'" in src
    # The overlay is drawn right after the in-cart top bar on the desktop frame.
    assert 'self._draw_status_strip("desktop")' in src
    # The device backend constructs the Workstation (default gamepad off there).
    dev = (ROOT / "firmware" / "lilygo_t_deck_plus_micropython" / "modules"
           / "kid_runtime.py").read_text(encoding="utf-8")
    assert "Workstation(" in dev
