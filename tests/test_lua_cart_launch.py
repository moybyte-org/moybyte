"""The #67 Phase 2 launch seam: a manifest "runtime": "lua" cart starts through
the injected Lua runtime (runtime/lua_host.py over lupa on the host), ticks
under the normal Player loop, exits cleanly, and degrades to the cart-error
panel when the runtime is missing (today's device builds). Store-side, the
runtime/main fields survive load and duplicate.

The Lua-side tests skip without lupa; the store passthrough tests always run.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws(tmp_path):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"))


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
    pytest.importorskip("lupa")
    ws = _ws(tmp_path)
    _open(ws, "Sakura Lua")
    assert ws.player.cart_error is None
    assert ws.player._lua is not None                    # started via the seam
    assert ws.player._update is not None and ws.player._draw is not None
    for _ in range(30):
        ws.frame(1 / 30)
    assert ws.player.cart_error is None
    # the cart world lives in the LUA state: 120 petals falling
    petals = ws.player._lua._lua.globals().petals
    assert len(petals) == 120


def test_lua_cart_exit_closes_the_state(tmp_path):
    pytest.importorskip("lupa")
    ws = _ws(tmp_path)
    _open(ws, "Sakura Lua")
    ws.frame(1 / 30)
    lua = ws.player._lua
    ws._exit_to_caller()
    assert ws.player._lua is None                        # closed with the run
    assert lua.update is None                            # handle torn down


def test_lua_error_routes_to_the_cart_panel(tmp_path):
    pytest.importorskip("lupa")
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
    pytest.importorskip("lupa")
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
    pytest.importorskip("lupa")
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


def test_bullet_storm_seed_cart_runs(tmp_path):
    # The bullet-hell stress cart (#67): a Lua seed whose whole point is
    # hundreds of live pooled bullets through the batch spr path. 300 frames
    # crash-free, the swarm actually fills, and a forced death reaches the
    # game-over state without an error.
    pytest.importorskip("lupa")
    ws = _ws(tmp_path)
    _open(ws, "Bullet Storm")
    assert ws.player.cart_error is None
    g = ws.player._lua._lua.globals()
    for _ in range(300):
        ws.frame(1 / 60)
        assert ws.player.cart_error is None
    assert g.nb > 30                                     # the swarm is live
    g.px, g.py = g.ex, g.ey                              # park inside the storm
    g.iframe = 0
    for _ in range(900):
        ws.frame(1 / 60)
        assert ws.player.cart_error is None
        g.px, g.py = g.ex, g.ey
        g.iframe = 0
        if g.over:
            break
    assert g.over                                        # death -> panel, no crash
    ws.frame(1 / 60)                                     # game-over frame draws clean


def test_missing_runtime_opens_the_panel_not_a_hang(tmp_path):
    ws = _ws(tmp_path)
    ws.lua_runtime = None                                # a device-shaped build
    _open(ws, "Sakura Lua")
    assert ws.player.cart_error is not None
    assert "Lua runtime" in ws.player.cart_error
    ws.frame(1 / 30)                                     # panel frame must not raise
