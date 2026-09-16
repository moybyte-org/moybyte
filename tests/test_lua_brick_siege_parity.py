"""Parity: the Brick Siege Lua port against main.py, on the shipped VM (#67).

Runs experiments/lua_bridge/brick_parity.py's harness: the real
system_carts/brick_siege.moy/main.py and system_carts/brick_siege_lua.moy/main.lua
under one deterministic fake API (shared PRNG, shared tilemap, scripted
buttons), the Lua side under runtime/lua_host's MoycoreHostRun -- libmoy's
binding over the vendored Lua 5.4 the boards compile, LUA_32BITS and all --
comparing every draw call, the whole game state and the tilemap after EVERY
frame. The contract is the harness's docstring: draw calls, integers, booleans,
strings and the tilemap EXACT; the rnd-derived timers within float32 rounding
of the double.

3000 frames is not arbitrary: it is long enough for both scenarios to reach a WAVE
CLEAR banner, a GAME OVER banner and several round restarts (so _init re-entry and
_reset_field's stamp-back of the crumbled bricks are covered), which a 600-frame run
like sakura's is not.

Plus a real-console smoke run (the fake API proves the LOGIC; this proves the port
against the SHIPPED make_api -- every verb name, arity and return shape the cart
actually calls, through runtime/lua_host.py's registration and prelude).

Both halves need the host Lua binding, which is a C compiler rather than a
package; each skips on its own when it did not build.
"""

import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _need_lua():
    from runtime import lua_host
    if lua_host.moycore_supports("") is not True:
        pytest.skip("host lua binding not built (needs a C compiler)")


def test_brick_siege_lua_parity():
    _need_lua()
    sys.path.insert(0, os.path.join(ROOT, "experiments", "lua_bridge"))
    from brick_parity import run_parity

    assert run_parity(frames=3000, verbose=True)


def test_brick_siege_lua_runs_under_the_real_player(tmp_path):
    _need_lua()
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
    run = ws.player._lua
    assert run.get_global_len("tanks") == 1            # _init built the P1 tank
    for _ in range(240):                               # 8s: spawns, shots, booms
        ws.frame(1 / 30)
    assert ws.player.cart_error is None
    assert float(run.get_global("t")) > 0.0            # the cart kept ticking
