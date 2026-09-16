"""Parity: the sakura Lua port against main.py, on the shipped VM (#67).

Runs experiments/lua_bridge/host_parity.py's harness: the real main.py and
main.lua under one deterministic fake API (shared PRNG, scripted touch, and the
shed scene both carts ship, parsed by the shared `widgets.Scenes`), the Lua
side under runtime/lua_host's MoycoreHostRun -- the boards' Lua, LUA_32BITS
and all -- comparing every draw call and the final petal state. The contract
is the harness's docstring: the same calls in the same order, every sprite
within a pixel, the PRNG consumed in lockstep, the petal floats within the
drift 600 frames of float32 arithmetic accumulate.

The harness needs the host Lua binding (a C compiler, not a package) and skips
without it, as does the last test here; the pair in between always runs: both
twins' scenes and the Python twin opened by the REAL console, which is where
the shed points come from since the pasted `EMIT` literal became
`scenes/blossoms.moyscene` (#214). That migration is the whole reason the Lua
half needed `scene()` to work at all.
"""

import json
import os
import sys

import pytest

from ws_helpers import open_cart

ROOT = os.path.join(os.path.dirname(__file__), "..")
CARTS = os.path.join(ROOT, "system_carts")


def _need_lua():
    from runtime import lua_host
    if lua_host.moycore_supports("") is not True:
        pytest.skip("host lua binding not built (needs a C compiler)")


def test_sakura_lua_parity():
    _need_lua()
    sys.path.insert(0, os.path.join(ROOT, "experiments", "lua_bridge"))
    from host_parity import run_parity

    assert run_parity(frames=600, verbose=True)


def test_both_twins_ship_the_same_scene_and_no_pasted_table():
    """The migration's own guard. Two copies of a 126-row table in source is
    what #214 set out to delete, and a byte difference between the two scenes
    is a canopy the twins shed from differently."""
    blobs = []
    for slug in ("sakura", "sakura_lua"):
        d = os.path.join(CARTS, slug + ".moy")
        with open(os.path.join(d, "scenes", "blossoms.moyscene")) as fh:
            blobs.append(fh.read())
        man = json.load(open(os.path.join(d, "manifest.json")))
        assert man["assets"]["scenes"] == ["blossoms"], slug
    assert blobs[0] == blobs[1], "the twins' scenes drifted"
    rows = json.loads(blobs[0])
    assert len(rows) > 100 and all(r["tag"] == "blossom" for r in rows)

    for name in ("sakura.moy/main.py", "sakura_lua.moy/main.lua"):
        src = open(os.path.join(CARTS, name)).read()
        assert "EMIT = [(" not in src and "EMIT = { {" not in src, \
            "%s still carries the pasted shed table" % name


def test_the_python_twin_sheds_from_the_scene(tmp_path):
    """Sakura runs as the DESKTOP BACKDROP, and that path built its api without
    scenes -- a wallpaper is a cart, so `scene()` was a NameError there and the
    guard swallowed it into the solid fill."""
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.look.select_wallpaper("sakura", persist=False)
    assert ws.wallpaper._wp_draw is not None, "the wallpaper failed to compile"
    rows = json.loads(open(os.path.join(
        CARTS, "sakura.moy", "scenes", "blossoms.moyscene")).read())
    emit = ws.wallpaper._wp_ns["EMIT"]
    assert [(a.x, a.y) for a in emit] == [(r["x"], r["y"]) for r in rows]
    for _ in range(30):
        ws.wallpaper.draw(1 / 30)


@pytest.mark.skipif(
    not __import__("runtime.lua_binding", fromlist=["x"]).HostLuaRun.available(),
    reason="no C compiler for the host lua binding")
def test_the_lua_twin_sheds_from_the_same_scene(tmp_path):
    """`scene()` reached a Lua cart as nil until #214, so this is the call the
    migration could not be applied without."""
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    open_cart(ws, "Sakura Lua")
    assert ws.player.cart_error is None
    assert ws.player._lua is not None
    rows = json.loads(open(os.path.join(
        CARTS, "sakura_lua.moy", "scenes", "blossoms.moyscene")).read())
    assert ws.player._lua.get_global_len("EMIT") == len(rows)
    for _ in range(30):
        ws.frame(1 / 30)
    assert ws.player.cart_error is None
