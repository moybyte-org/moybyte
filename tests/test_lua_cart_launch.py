"""The #67 Phase 2 launch seam: a manifest "runtime": "lua" cart starts through
the injected Lua runtime, ticks under the normal Player loop, exits cleanly, and
degrades to the cart-error panel when the runtime is missing. Store-side, the
runtime/main fields survive load and duplicate.

The host runs the BOARDS' Lua now (libmoy's binding over the same vendored 5.4);
lupa went on 2026-08-14. So the Lua-side tests skip when the native binding did
not BUILD -- a C compiler, not a wheel -- and the store passthrough tests always
run.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _need_lua():
    """Skip unless the host lua binding BUILT. It used to be
    `importorskip("lupa")`; the host has no second Lua VM any more, so what can
    be missing is a compiler rather than a package."""
    from runtime import lua_host
    if lua_host.moycore_supports("") is not True:
        pytest.skip("host lua binding not built (needs a C compiler)")


from ws_helpers import build_ws as _ws


def _open(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            ws.open()
            return
    raise AssertionError("no seed cart titled " + title)


# -- store passthrough (no lupa needed) -----------------------------------------

def test_load_carries_runtime_and_main(tmp_path):
    from runtime import moy_carts
    ws = _ws(tmp_path)
    by_title = {c["title"]: c for c in ws.launcher.items}
    lua = by_title["Sakura Lua"]
    # slim_carts (#66 live-set diet) keeps the light runtime/main fields...
    assert lua["runtime"] == "lua" and lua["main"] == "main.lua"
    # ...and the full store load reads src FROM main.lua.
    full = moy_carts.load(lua["path"])
    assert full["src"].lstrip().startswith("--")
    py = by_title["Sakura"] if "Sakura" in by_title else None
    if py is not None:                                   # wallpapers may not list
        assert py.get("runtime", "python") == "python"


def test_duplicate_preserves_lua_runtime(tmp_path):
    from runtime import moy_carts
    ws = _ws(tmp_path)
    src = next(c for c in ws.launcher.items if c["title"] == "Sakura Lua")
    cart = moy_carts.load(src["path"])
    dup = moy_carts.duplicate(cart, str(tmp_path / "carts"))
    assert dup["runtime"] == "lua" and dup["main"] == "main.lua"
    assert dup["src"] == cart["src"]
    assert (Path(dup["path"]) / "main.lua").exists()
    assert not (Path(dup["path"]) / "main.py").exists()


def test_save_code_writes_the_manifest_main(tmp_path):
    from runtime import moy_carts
    ws = _ws(tmp_path)
    src = next(c for c in ws.launcher.items if c["title"] == "Sakura Lua")
    cart = moy_carts.load(src["path"])
    new_src = cart["src"] + "\n-- edited\n"
    status, msg = moy_carts.save_code(cart, new_src)
    assert status == moy_carts.SAVE_OK, msg
    assert (Path(cart["path"]) / "main.lua").read_text().endswith("-- edited\n")


# -- the launch seam (needs lupa) ------------------------------------------------

def test_lua_cart_runs_under_the_player(tmp_path):
    _need_lua()
    ws = _ws(tmp_path)
    _open(ws, "Sakura Lua")
    assert ws.player.cart_error is None
    assert ws.player._lua is not None                    # started via the seam
    assert ws.player._update is not None and ws.player._draw is not None
    for _ in range(30):
        ws.frame(1 / 30)
    assert ws.player.cart_error is None
    # the cart world lives in the LUA state: 120 petals falling
    # Read through the run's own accessor, not lupa's internals: the host runs
    # the boards' Lua now, and a test that reaches into one embedding's guts
    # only ever tested that embedding.
    assert ws.player._lua.get_global_len("petals") == 120


def test_lua_cart_exit_closes_the_state(tmp_path):
    _need_lua()
    ws = _ws(tmp_path)
    _open(ws, "Sakura Lua")
    ws.frame(1 / 30)
    lua = ws.player._lua
    ws._exit_to_caller()
    assert ws.player._lua is None                        # closed with the run
    assert lua.update is None                            # handle torn down


def test_lua_error_routes_to_the_cart_panel(tmp_path):
    _need_lua()
    from runtime import moy_carts
    ws = _ws(tmp_path)
    bad = moy_carts.create("Bad Lua", str(tmp_path / "carts"),
                           src="function _update(dt)\n  error('boom')\nend\n",
                           runtime="lua", main="main.lua")
    assert bad["runtime"] == "lua"
    ws.launcher.items.append(bad)
    _open(ws, "Bad Lua")
    assert ws.player.cart_error is None                  # loads fine
    ws.frame(1 / 30)                                     # first _update raises
    assert ws.player.cart_error is not None
    assert "boom" in ws.player.cart_error
    # #67 Phase 5: the error position maps to the cart line (error() on line 2)
    # and lupa's traceback block is trimmed to the device-parity one-liner.
    assert ws.player.crash_line == 2
    assert "stack traceback" not in ws.player.cart_error


def test_lua_crash_line_is_the_deepest_frame(tmp_path):
    # The raise point inside a helper wins over the _update call site -- the
    # same deepest-cart-frame rule the Python traceback walk applies.
    _need_lua()
    from runtime import moy_carts
    ws = _ws(tmp_path)
    src = ("function helper()\n"
           "  local n = nil\n"
           "  return n + 1\n"                            # line 3: the crash
           "end\n"
           "function _update(dt)\n"
           "  helper()\n"                                # line 6: the call site
           "end\n")
    bad = moy_carts.create("Deep Lua", str(tmp_path / "carts"),
                           src=src, runtime="lua", main="main.lua")
    ws.launcher.items.append(bad)
    _open(ws, "Deep Lua")
    ws.frame(1 / 30)
    assert ws.player.crash_line == 3


def test_lua_load_error_drops_on_the_line(tmp_path):
    # A syntax error opens the panel AT START with the offending line, like a
    # Python SyntaxError (#24) -- so EDIT lands the kid on the bad line.
    _need_lua()
    from runtime import moy_carts
    ws = _ws(tmp_path)
    bad = moy_carts.create("Broken Lua", str(tmp_path / "carts"),
                           src="local x = 1\ny = = 2\n",  # line 2: bad syntax
                           runtime="lua", main="main.lua")
    ws.launcher.items.append(bad)
    _open(ws, "Broken Lua")
    assert ws.player.cart_error is not None
    assert "cart:2" in ws.player.cart_error
    assert ws.player.crash_line == 2
    ws.frame(1 / 30)                                     # panel frame must not raise


def test_lua_cart_line_parser_shapes():
    # The text parser both backends' error texts route through (no lupa needed).
    from runtime.player import _lua_cart_line
    assert _lua_cart_line("cart:12: attempt to add a nil value") == 12
    assert _lua_cart_line("LuaError: cart:3: boom") == 3
    assert _lua_cart_line('[string "cart"]:7: unexpected symbol') == 7
    assert _lua_cart_line("cart:9: boom\nstack traceback:\n\tcart:20: in f") == 9
    assert _lua_cart_line("RuntimeError: restart:5: nope") is None
    assert _lua_cart_line("no position here") is None
    assert _lua_cart_line("") is None
    assert _lua_cart_line(None) is None


# test_bullet_storm_seed_cart_runs lived here until 2026-08-14. It forced a
# DEATH by writing the cart's globals (park the ship on the enemy, zero the
# i-frames) and asserting the game-over state came out clean -- and it could
# only do that through lupa, which hands Lua real Python objects. The moycore
# boundary marshals ints and one string, exposes globals READ-ONLY by design,
# and lupa is gone, so the poke is not expressible any more.
#
# What is lost, stated rather than quietly dropped: nothing else drives this
# cart to its game-over branch. test_bullet_storm_runs_on_moycore below keeps
# the load, the 300 crash-free frames and the live swarm; the death path is
# uncovered until something can reach it from OUTSIDE the VM (a scripted input
# feed that plays badly on purpose is the obvious shape, and the semantic-trace
# harness already has that machinery).

def test_bullet_storm_runs_on_moycore(tmp_path):
    """The same cart on the boards' own Lua, observed from outside.

    This is the half the lupa test cannot cover and the half that regressed:
    bullet_storm's `_init` calls make_layer, which is object-valued, so before
    the shared handle glue reached this runtime the cart's layer came back nil
    -- and because _init raised, the run failed to LOAD and quietly fell back
    to lupa, which is why every lua test still passed while moycore ran none of
    these carts. So the assertion that matters is the negative one: the run is
    a MoycoreHostRun, not a fallback.
    """
    from runtime import lua_host
    if lua_host.moycore_supports("") is not True:
        pytest.skip("host lua binding not built")
    ws = _ws(tmp_path)
    _open(ws, "Bullet Storm")
    assert ws.player.cart_error is None
    assert type(ws.player._lua).__name__ == "MoycoreHostRun", \
        "the cart fell back to lupa -- the handle glue did not take"
    for _ in range(300):
        ws.frame(1 / 60)
        assert ws.player.cart_error is None
    # The swarm is live, read through the runtime-agnostic global reader.
    assert ws.player._lua.get_global("nb") > 30


def test_missing_runtime_opens_the_panel_not_a_hang(tmp_path):
    ws = _ws(tmp_path)
    ws.lua_runtime = None                                # a device-shaped build
    _open(ws, "Sakura Lua")
    assert ws.player.cart_error is not None
    assert "Lua runtime" in ws.player.cart_error
    ws.frame(1 / 30)                                     # panel frame must not raise
