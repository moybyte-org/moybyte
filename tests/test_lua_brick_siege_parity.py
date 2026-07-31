"""Golden parity: the Brick Siege Lua port must match main.py bit-for-bit (#67).

Runs experiments/lua_bridge/brick_parity.py's harness: the real
system_carts/brick_siege.moy/main.py and system_carts/brick_siege_lua.moy/main.lua
under one deterministic fake API (shared PRNG, shared tilemap, scripted buttons),
comparing every draw call, the whole game state and the tilemap after EVERY frame.
Exact equality -- both runtimes are IEEE doubles, so any epsilon is a porting bug.

3000 frames is not arbitrary: it is long enough for both scenarios to reach a WAVE
CLEAR banner, a GAME OVER banner and several round restarts (so _init re-entry and
_reset_field's stamp-back of the crumbled bricks are covered), which a 600-frame run
like sakura's is not.

Plus a real-console smoke run (the fake API proves the LOGIC; this proves the port
against the SHIPPED make_api -- every verb name, arity and return shape the cart
actually calls, through runtime/lua_host.py's sandbox prelude).

Skips when `lupa` (the optional #67 Phase 3 host-runner dep) isn't installed.
"""

import os
import sys

import pytest

pytest.importorskip("lupa")

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "experiments", "lua_bridge"))
from brick_parity import run_parity  # noqa: E402


def test_brick_siege_lua_parity():
    assert run_parity(frames=3000, verbose=True)


def test_brick_siege_lua_runs_under_the_real_player(tmp_path):
    # The shipped api, not the harness's fake: catches a verb the port calls with
    # the wrong arity/shape (print's scale arg, col names, background, map, spr).
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == "Brick Siege Lua":
            ws.launcher.sel = i
            ws.open()
            break
    else:
        raise AssertionError("Brick Siege Lua is not on the shelf")
    assert ws.player.cart_error is None
    assert ws.player._lua is not None                  # started via the #67 seam
    g = ws.player._lua._lua.globals()
    assert len(g.players) == 1                         # _init built the P1 tank
    for _ in range(240):                               # 8s: spawns, shots, booms
        ws.frame(1 / 30)
    assert ws.player.cart_error is None
    assert float(g.t) > 0.0                            # the cart kept ticking
