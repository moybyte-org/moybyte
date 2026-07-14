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


def test_missing_runtime_opens_the_panel_not_a_hang(tmp_path):
    ws = _ws(tmp_path)
    ws.lua_runtime = None                                # a device-shaped build
    _open(ws, "Sakura Lua")
    assert ws.player.cart_error is not None
    assert "Lua runtime" in ws.player.cart_error
    ws.frame(1 / 30)                                     # panel frame must not raise
