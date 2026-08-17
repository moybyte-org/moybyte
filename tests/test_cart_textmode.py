"""Cart text input (#38/#42): a RUNNING cart can opt into TEXT-keyboard mode via
the `textmode(on)` verb so key()/keyp() receive clean typed ASCII -- the capability
that unblocks on-device WiFi password entry and text in any cart.

These tests drive the SHARED console through the host ConsoleDriver (the exact
per-frame model the simulator + device use): a text-mode cart receives typed chars
and edges via key()/keyp(); a game cart still gets button input and NO stray text;
and text mode resets when the cart exits. The driver's in_text_mode() gate (which
tools/simulate_desktop.py uses to route typed unicode) is pinned too.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# A text-input cart: opts into text mode and records every key()/keyp() it sees.
TEXT_CART_SRC = """
typed = ""
edges = []
seen_text_off = False
_kprev = 0

def _update(dt):
    global typed, _kprev, seen_text_off
    textmode(True)
    k = key()
    if k and k != _kprev:
        typed = typed + chr(k)
    if keyp():
        edges.append(keyp())
    _kprev = k

def _draw():
    cls(0)
"""

# A game cart: never asks for text mode; reads buttons + records any stray key().
GAME_CART_SRC = """
moves = []
stray = ""

def _update(dt):
    global stray
    if btn("right"):
        moves.append("right")
    if btn("left"):
        moves.append("left")
    k = key()
    if k:
        stray = stray + chr(k)

def _draw():
    cls(0)
"""


def _open_cart(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            break
    ws.open()


def _make_ws_with_carts(tmp_path):
    from runtime import host_app
    from runtime import moy_carts

    carts_dir = str(tmp_path / "carts")
    os.makedirs(carts_dir, exist_ok=True)
    moy_carts.create("Typer", carts_dir, src=TEXT_CART_SRC, type="app")
    moy_carts.create("Mover", carts_dir, src=GAME_CART_SRC, type="game")
    return host_app.build_workstation(carts_dir)


def test_textmode_cart_receives_typed_chars_and_edges(tmp_path):
    from runtime import host_app

    ws = _make_ws_with_carts(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _open_cart(ws, "Typer")
    assert ws.screen == "desktop" and ws.cart_error is None

    # First frame: the cart's _update runs textmode(True) -> in_text_mode() True next.
    drv.frame(1.0 / 30)
    assert drv.in_text_mode() is True
    assert ws.input.text_mode is True

    # Type "hi" the way simulate_desktop routes it in text mode: type_char per press,
    # with a release frame between so each char is a fresh 0->key edge (keyp()).
    for ch in "hi":
        drv.type_char(ord(ch))
        drv.frame(1.0 / 30)
        drv.frame(1.0 / 30)                      # release edge

    assert ws.ns["typed"] == "hi"               # the cart's key() saw both chars
    # keyp() fired once per char (the press edge), not on the release/hold frames.
    assert ws.ns["edges"] == [ord("h"), ord("i")]


def test_game_cart_gets_buttons_and_no_stray_text(tmp_path):
    from runtime import host_app

    ws = _make_ws_with_carts(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _open_cart(ws, "Mover")
    assert ws.screen == "desktop" and ws.cart_error is None

    # A game cart never calls textmode -> stays in game mode; the driver does NOT
    # route typed chars to it (simulate_desktop gates type_char on in_text_mode()).
    drv.frame(1.0 / 30)
    assert drv.in_text_mode() is False
    assert getattr(ws.input, "text_mode", False) is False

    # Held d-pad drives btn() exactly as before (hold-to-move unaffected).
    drv.hold("right", True)
    drv.frame(1.0 / 30)
    drv.hold("right", False)
    drv.frame(1.0 / 30)
    assert "right" in ws.ns["moves"]

    # Even if a stray byte reached last_key, a game cart in_text_mode() is False so
    # the sim never feeds it; assert the cart saw no text the whole run.
    assert ws.ns["stray"] == ""


def test_backspace_is_a_plain_key_zero_special_casing(tmp_path):
    # Stage 5 exit model (spec Section 9): the #71 pause frame is GONE, and with it the
    # text-mode carve-out + the _bks_prev edge-detect. BACKSPACE is a PLAIN key
    # everywhere, with ZERO special-casing:
    #   * a running text-mode TOOL (the wifi cart's password field) receives BACKSPACE as
    #     a typed 0x08 -> DELETE, and it NEVER exits (the input backend routes 0x08 as a
    #     typed key, not the "home" button, in text mode -- so the exit gesture, which
    #     watches "home", simply never fires here);
    #   * a typing game keeps all 26 letters (q types, never a shortcut).
    # Exit is the deliberate hold-"home" gesture for games (tested in
    # test_desktop_shell.py) / the bar X for tools, not a single BACKSPACE anywhere.
    import os
    from runtime import host_app
    from runtime import moy_carts

    carts_dir = str(tmp_path / "carts")
    os.makedirs(carts_dir, exist_ok=True)
    moy_carts.create("TypeGame", carts_dir, src=TEXT_CART_SRC, type="game")
    moy_carts.create("TypeTool", carts_dir, src=TEXT_CART_SRC, type="app")
    ws = host_app.build_workstation(carts_dir)
    drv = host_app.ConsoleDriver(ws)

    # A running TEXT-mode TOOL: BACKSPACE is a typed key (DELETE), never an exit.
    _open_cart(ws, "TypeTool")
    drv.frame(1.0 / 30)                     # _update ran textmode(True)
    assert ws.input.text_mode is True
    drv.type_char(0x08)                     # BACKSPACE in text mode = the cart's DELETE...
    drv.frame(1.0 / 30)
    assert ws.screen == "desktop"           # ...never exits (zero special-casing)
    assert 0x08 in ws.ns["edges"]           # the cart itself saw the byte

    # A typing game keeps its letters: q TYPES, never a console shortcut, no exit.
    _open_cart(ws, "TypeGame")
    drv.frame(1.0 / 30)
    assert ws.input.text_mode is True
    drv.type_char(ord("q"))
    drv.frame(1.0 / 30)
    drv.frame(1.0 / 30)
    assert ws.screen == "desktop"
    assert ws.ns["typed"].endswith("q")


def test_textmode_resets_on_cart_exit(tmp_path):
    from runtime import host_app

    ws = _make_ws_with_carts(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _open_cart(ws, "Typer")
    drv.frame(1.0 / 30)
    assert ws.input.text_mode is True            # text-mode cart asked for it

    # Leaving to the home desktop must drop text mode (don't leak it into the
    # launcher / next cart). go_home() is the device + host exit path.
    ws.go_home()
    assert ws.input.text_mode is False
    assert drv.in_text_mode() is False           # not on the desktop anymore anyway

    # Opening a different (game) cart starts in game mode, not the previous text mode.
    _open_cart(ws, "Mover")
    drv.frame(1.0 / 30)
    assert getattr(ws.input, "text_mode", False) is False
    assert drv.in_text_mode() is False


def test_textmode_off_mid_cart_reverts_to_game(tmp_path):
    # A cart that flips text mode back off (e.g. wifi leaving its password screen)
    # restores game mode for the rest of the run.
    from runtime import host_app
    from runtime import moy_carts

    src = """
phase = 0

def _update(dt):
    global phase
    phase = phase + 1
    textmode(phase < 3)     # text mode for the first couple frames, then off

def _draw():
    cls(0)
"""
    carts_dir = str(tmp_path / "carts")
    os.makedirs(carts_dir, exist_ok=True)
    moy_carts.create("Toggler", carts_dir, src=src, type="app")
    ws = host_app.build_workstation(carts_dir)
    drv = host_app.ConsoleDriver(ws)
    _open_cart(ws, "Toggler")

    drv.frame(1.0 / 30)                          # phase 1 -> text on
    assert ws.input.text_mode is True
    drv.frame(1.0 / 30)                          # phase 2 -> text on
    assert ws.input.text_mode is True
    drv.frame(1.0 / 30)                          # phase 3 -> text off
    assert ws.input.text_mode is False
    assert drv.in_text_mode() is False


def test_textmode_in_both_make_api_namespaces():
    # host == device contract: `textmode` is a base verb in BOTH backends' namespace
    # (so a cart authored once runs identically). Loads the device moy_runtime under
    # CPython the way the spike/parity tests do.
    import importlib.util
    from runtime import host_app

    SYSTEM_CARTS = ROOT / "system_carts"
    for name in ("editors", "audio", "console"):
        spec = importlib.util.spec_from_file_location(name, ROOT / "runtime" / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
    sys.path.insert(0, str(ROOT / "tools"))
    import gen_device_carts
    sys.modules["carts_data"] = gen_device_carts.as_module(str(SYSTEM_CARTS))
    fw = ROOT / "firmware" / "lilygo_t_deck_plus_mainline" / "modules" / "moy_runtime.py"
    spec = importlib.util.spec_from_file_location("moy_runtime", fw)
    dev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dev)

    class _StubInput:
        def held(self, n):
            return False

        def pressed(self, n):
            return False

    class _Stub:
        w = 320
        h = 240

        def __getattr__(self, name):
            return lambda *a, **k: 0

    host_ns = host_app.make_api(_Stub(), _StubInput(), {})
    dev_ns = dev.make_api(_Stub(), _StubInput(), {})
    assert "textmode" in host_ns and "textmode" in dev_ns
    # Both default to on=True and set input.text_mode.
    inp = _StubInput()
    host_app.make_api(_Stub(), inp, {})["textmode"]()
    assert inp.text_mode is True
    host_app.make_api(_Stub(), inp, {})["textmode"](False)
    assert inp.text_mode is False
