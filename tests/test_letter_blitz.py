"""Tests for the Letter Blitz cart (a letter-recognition shooting gallery for a
young kid): a stationary cannon pops wandering "letter tanks" that match a
FIND target, via either a touchscreen tap or the matching keyboard key. There
is no lose-state -- wrong picks just mute the sound and start a short input
cooldown. Mirrors the `_open_cart`/headless-run idiom in test_seed_carts.py.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SYSTEM_CARTS = ROOT / "system_carts"


def _open_cart(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            break
    else:  # pragma: no cover - guards a typo in the title
        raise AssertionError("seed cart not found: " + title)
    ws.open()


def _run(ws, frames, dt=1 / 30):
    for _ in range(frames):
        ws.input.begin_frame()
        ws.frame(dt)


def test_letter_blitz_folder_present_and_valid():
    import json

    d = SYSTEM_CARTS / "letter_blitz.moy"
    assert (d / "manifest.json").is_file()
    assert (d / "main.py").is_file()
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert man["format"] == "moybyte-cart-v1"
    assert man["type"] == "game"
    assert man["main"] == "main.py"
    assert man["edit"], "no Make-it-mine cards"
    src = (d / "main.py").read_text(encoding="utf-8")
    compile(src, str(d / "main.py"), "exec")
    assert "_init" in src and "_update" in src and "_draw" in src
    # a pure vector + procedural-glyph cart, like beeper.moy -- no sprite sheet
    assert not (d / "sprites.moygfx").exists()


def test_letter_blitz_opens_and_runs_headless(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    titles = {c["title"] for c in ws.launcher.items}
    assert "Letter Blitz" in titles

    _open_cart(ws, "Letter Blitz")
    assert ws.screen == "desktop"
    assert ws.ns is not None and ws._update and ws._draw
    _run(ws, 300)  # ~10s headless, tanks wandering, no input -- must not crash


def test_letter_blitz_correct_touch_tap_scores(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    ws.ns["tanks"][:] = [[100.0, 100.0, 0.0, 0.0, "A", 0.0, 5.0, 0, 0, 0, 5.0]]
    ws.ns["wanted"] = "A"
    lifetime0 = ws.ns["lifetime"]
    pop0 = ws.ns["pop_count"]

    ws.pointer.place(100, 100)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(1 / 30)
    ws.pointer.click = False  # a real tap is a one-frame edge, not a hold
    assert ws.ns["bullet"] is not None  # the shot is in flight, not resolved yet

    _run(ws, 10)  # let the short travel delay elapse
    assert ws.ns["lifetime"] == lifetime0 + 1
    assert ws.ns["pop_count"] == pop0 + 1
    assert ws.ns["bullet"] is None


def test_letter_blitz_correct_keypress_scores(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    ws.ns["tanks"][:] = [[100.0, 100.0, 0.0, 0.0, "B", 0.0, 5.0, 0, 0, 0, 5.0]]
    ws.ns["wanted"] = "B"
    lifetime0 = ws.ns["lifetime"]

    ws.input.last_key = ord("b")  # lowercase, as a real keypress would send
    ws.input.begin_frame()
    ws.frame(1 / 30)
    ws.input.last_key = 0

    _run(ws, 10)
    assert ws.ns["lifetime"] == lifetime0 + 1


def test_letter_blitz_wrong_pick_no_score_and_cooldown(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    ws.ns["tanks"][:] = [[150.0, 120.0, 0.0, 0.0, "B", 0.0, 5.0, 0, 0, 0, 5.0]]
    ws.ns["wanted"] = "Z"
    ws.ns["cooldown_t"] = 0.0
    lifetime0 = ws.ns["lifetime"]

    ws.pointer.place(150, 120)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(1 / 30)

    assert ws.ns["lifetime"] == lifetime0        # no score for a wrong pick
    assert ws.ns["bullet"] is None                # no explosion
    assert ws.ns["cooldown_t"] > 0.0               # mashing is blocked briefly

    # a second tap during the cooldown is a no-op even on the CORRECT tank
    ws.ns["tanks"][0][4] = "Z"
    ws.pointer.place(150, 120)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.ns["bullet"] is None
    assert ws.ns["lifetime"] == lifetime0


def test_letter_blitz_letters_unlocked_stepper_expands_pool(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    assert ws.ns["_unlocked"]() == "ABCDEF"       # default starting batch

    ws.config["letters_unlocked"] = 26
    assert ws.ns["_unlocked"]() == "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    # every glyph a tank could show must actually be drawable
    for ch in ws.ns["ALPHABET"]:
        img = ws.ns["_glyph"](ch)
        assert img.w == 5 and img.h == 7, ch


def test_letter_blitz_trace_bonus_every_fourth_pop(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    # pop 4 tanks in a row (deterministic: always tap the currently-wanted one).
    # Real taps are a one-frame event (the driver clears its edge each frame,
    # host_app.py:753/761) -- ws.pointer.click must be reset the same way here,
    # or it reads as a stuck "held" tap for every frame that follows.
    for _ in range(4):
        wanted = ws.ns["wanted"]
        for t in ws.ns["tanks"]:
            if t[4] == wanted:
                ws.pointer.place(t[0], t[1])
                ws.pointer.click = True
                break
        ws.input.begin_frame()
        ws.frame(1 / 30)
        ws.pointer.click = False
        _run(ws, 10)  # resolve the bullet before the next tap
        ws.ns["cooldown_t"] = 0.0

    assert ws.ns["pop_count"] == 4
    assert ws.ns["mode"] == "trace"

    # tapping during the bonus stamps a dot, not a gallery hit
    ws.pointer.place(160, 120)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(1 / 30)
    ws.pointer.click = False
    assert len(ws.ns["trace_dots"]) == 1

    _run(ws, 300)  # TRACE_DURATION (6s) at 1/30 -- must return to gallery on its own
    assert ws.ns["mode"] == "gallery"
