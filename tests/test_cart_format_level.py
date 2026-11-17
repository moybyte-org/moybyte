"""A cart built for a newer console is refused before any of it runs.

moy-spec SPEC.md 13: a manifest's `format` is "moy-" and a level, and a host
refuses a cart whose level is above its own. The Player compares it
(`player_fit._format_newer`) before any runtime is reached, whatever the
cart's runtime, and refuses on the notice a compiled cart missing an import
gets: "Needs a newer console." A `format` that names no level -- Moybyte's
own "moybyte-cart-v1" -- is a Moybyte cart and runs.

The comparison's cases are moy-spec's, vendored (tests/format_vectors/).
"""

import json
from pathlib import Path

import pytest

from runtime import host_app, player_fit
from ws_helpers import open_cart

VECTORS = Path(__file__).resolve().parent / "format_vectors" / "format.json"
NEWER_TITLE = "Needs a newer console."


@pytest.mark.parametrize("case", json.loads(VECTORS.read_text(encoding="utf-8"))["cases"],
                         ids=lambda c: c["name"])
def test_the_format_level_is_compared_as_moy_spec_says(case, monkeypatch):
    """A case moy-spec refuses as newer is refused here; every other runs,
    including one moy-spec calls no moy cart, which here is a Moybyte cart."""
    monkeypatch.setattr(player_fit, "FORMAT_LEVEL", case["level"])
    cart = {} if case["format"] is None else {"format": case["format"]}
    newer = player_fit._format_newer(cart)
    assert (newer is not None) == (case["verdict"] == "newer")
    assert newer is None or newer == case["format"]


def _need_lua():
    from runtime import lua_binding
    if not lua_binding.HostLuaRun.available():
        pytest.skip("host lua binding not built (needs a C compiler)")


def _ws_with(tmp_path, title, fmt, runtime, main, src):
    carts = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts)
    host_app.moy_carts.create(title, carts, src=src, type="game", runtime=runtime,
                              main=main, format=fmt)
    return host_app.build_workstation(carts)


def _assert_refused_as_newer(ws, fmt):
    p = ws.run.player
    assert p.notice_title == NEWER_TITLE, p.cart_error
    assert p.notice == p.cart_error and fmt in p.notice
    assert p.newer
    assert p.lua is None and p.update is None and p.draw is None
    assert "ran" not in p.cart_error
    ws.frame(1 / 30)                      # the notice's frame draws
    assert p.notice_title == NEWER_TITLE


def test_a_python_cart_from_a_newer_console_is_refused_before_it_runs(tmp_path):
    ws = _ws_with(tmp_path, "Future Py", "moy-2", "python", "main.py",
                  "raise RuntimeError('ran')\n")
    open_cart(ws, "Future Py")
    _assert_refused_as_newer(ws, "moy-2")


def test_a_spec_cart_from_a_newer_console_is_refused_before_it_runs(tmp_path):
    """A bare spec manifest, Lua by the spec's default: the store reads any
    level's cart as a spec cart, and the Player refuses it with the Lua
    runtime present."""
    _need_lua()
    carts = tmp_path / "carts"
    host_app.moy_carts.ensure_dirs(str(carts))
    d = carts / "future_lua.moy"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"format": "moy-2", "title": "Future Lua"}))
    (d / "main.lua").write_text("error('ran')\n")
    ws = host_app.build_workstation(str(carts))
    assert "lua" in ws.runtimes
    open_cart(ws, "Future Lua")
    _assert_refused_as_newer(ws, "moy-2")


@pytest.mark.parametrize("fmt, spec", [("moy-1", True), ("moy-2", True), ("moy-10", True),
                                       ("moy-01", False), ("moy-0", False),
                                       ("moy-1.1", False), ("moybyte-cart-v1", False)])
def test_the_store_reads_a_cart_at_any_level_as_a_spec_cart(tmp_path, fmt, spec):
    """native/moy_store's catalogue: "moy-" and a level is a spec cart (Lua,
    main.lua, a game at 30 fps), so a newer one reaches the Player's
    comparison as the cart it is; any other format keeps Moybyte's defaults."""
    d = tmp_path / "c.moy"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"format": fmt, "title": "C"}))
    (d / "main.lua").write_text("function _draw() end\n")
    (d / "main.py").write_text("def _draw():\n    pass\n")
    cart = host_app.moy_carts.load(str(d))
    assert cart["format"] == fmt
    want = ("lua", "main.lua", "game", 30) if spec else ("python", "main.py", "app", 0)
    assert (cart["runtime"], cart["main"], cart["type"], cart["fps"]) == want


def test_a_lua_cart_at_this_consoles_level_runs(tmp_path):
    _need_lua()
    ws = _ws_with(tmp_path, "Now Lua", "moy-%d" % player_fit.FORMAT_LEVEL, "lua",
                  "main.lua", "function _draw() cls(1) end\n")
    open_cart(ws, "Now Lua")
    p = ws.run.player
    assert p.cart_error is None and p.notice is None
    assert p.lua is not None
