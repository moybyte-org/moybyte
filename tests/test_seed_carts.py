"""Tests for the new seed cartridges (#5, #19): Pixel Pet, Tiny Runner, Hop Quest
(platformer), and Tap Only Red (touch mini-game). Each loads through the SHARED
console via host_app (the same code path the device runs) and is driven headless
for several frames -- exercising attract-mode auto-play and, where relevant, the
input contract (buttons + the touch() api). Kept in its own file so it doesn't
collide with the existing test_v04_userland.py suite.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SYSTEM_CARTS = ROOT / "system_carts"

NEW_CARTS = ("Pixel Pet", "Tiny Runner", "Hop Quest", "Tap Only Red")


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


# -- the carts exist as well-formed .kcart folders --------------------------

def test_seed_cart_folders_present_and_valid():
    import json

    for folder in ("pet", "tiny_runner", "platformer", "tap_red"):
        d = SYSTEM_CARTS / (folder + ".kcart")
        assert (d / "manifest.json").is_file(), folder
        assert (d / "main.py").is_file(), folder
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        assert man["format"] == "kidcode-cart-v1"
        assert man["type"] == "game"
        assert man["main"] == "main.py"
        assert man["edit"], folder + " has no Make-it-mine cards"
        # main.py must at least define the cart entrypoints + be compilable
        src = (d / "main.py").read_text(encoding="utf-8")
        compile(src, str(d / "main.py"), "exec")
        assert "_init" in src and "_update" in src and "_draw" in src


# -- each loads through the shared console + runs headless without error ----

def test_all_new_carts_open_and_run_headless(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    titles = {c["title"] for c in ws.launcher.items}
    for title in NEW_CARTS:
        assert title in titles, "seed cart missing from gallery: " + title

    for title in NEW_CARTS:
        _open_cart(ws, title)
        assert ws.screen == "desktop", title  # _start succeeded (else still launcher)
        assert ws.ns is not None and ws._update and ws._draw
        _run(ws, 90)                            # attract-mode auto-play, no crash
        assert len(set(ws.canvas.buf)) > 1, title + " drew nothing"
        ws.go_home()


def test_carts_are_lively_in_attract_mode(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    for title in NEW_CARTS:
        _open_cart(ws, title)
        snaps = set()
        for _ in range(120):
            ws.input.begin_frame()
            ws.frame(1 / 30)
            snaps.add(bytes(ws.canvas.buf[::97]))   # cheap sparse snapshot
        assert len(snaps) > 3, title + " is static in attract mode"
        ws.go_home()


# -- per-cart gameplay sanity ------------------------------------------------

def test_pet_feeding_and_playing_raise_meters(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Pixel Pet")
    ws.ns["food"] = 10.0
    ws.ns["joy"] = 10.0
    ws.input.set_held("left", True)             # LEFT = feed
    ws.input.begin_frame()
    ws.frame(1 / 30)
    ws.input.set_held("left", False)
    assert ws.ns["food"] > 10.0
    ws.input.set_held("right", True)            # RIGHT = play
    ws.input.begin_frame()
    ws.frame(1 / 30)
    ws.input.set_held("right", False)
    assert ws.ns["joy"] > 10.0


def test_tiny_runner_collision_resets_and_keeps_best(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Tiny Runner")
    ws.ns["score"] = 50.0
    ws.ns["hero_y"] = 0.0
    ws.ns["vel"] = 0.0
    ws.ns["obs"][:] = [[ws.ns["HERO_X"], 10, 30]]   # an obstacle on top of the hero
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.ns["score"] == 0.0                    # run reset on the hit
    assert ws.ns["best"] >= 50                       # best run preserved


def test_platformer_collect_all_and_reach_goal_wins(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Hop Quest")
    for coin in ws.ns["coins"]:
        coin[2] = True                               # collect everything
    gx, gy = ws.ns["goal"]
    ts = ws.ns["TS"]
    ws.ns["px"] = float(gx * ts)
    ws.ns["py"] = float(gy * ts)
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.ns["won"] > 0.0                        # standing on the goal -> win


def test_platformer_falling_off_respawns(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Hop Quest")
    spawn = ws.ns["spawn"]
    ts = ws.ns["TS"]
    ws.ns["py"] = float(len(ws.ns["LEVEL"]) * ts + 200)   # well below the level
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.ns["py"] == float(spawn[1] * ts)            # back at the spawn tile


def test_tap_red_scores_red_and_penalizes_other(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Tap Only Red")
    col = ws.ns["col"]
    cx, cy = ws.canvas.w // 2, ws.canvas.h // 2

    # tap a RED bubble -> +1 score (touch() reads the pointer via the api)
    ws.ns["bubbles"][:] = [[float(cx), float(cy), 16, col("red"), True]]
    s0 = ws.ns["score"]
    ws.pointer.place(cx, cy)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.ns["score"] == s0 + 1

    # tap a non-red bubble -> a miss
    ws.ns["bubbles"][:] = [[float(cx), float(cy), 16, col("blue"), False]]
    m0 = ws.ns["misses"]
    ws.pointer.place(cx, cy)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.ns["misses"] == m0 + 1
