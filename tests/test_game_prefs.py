"""A game's prefs (docs/app_api_v1.md section 5): a Python and a Lua game hold
`prefs` without asking, as an app with the `prefs` permission does, each value
JSON in the console's settings under the game's id, kept across a restart of
its run. The compiled tier's are tests/test_wasm_game_ext.py's.
"""

import json

import pytest

from ws_helpers import build_ws

_PY = '''
def _init():
    runs = prefs.get("runs") or 0
    best = prefs.get("best")
    prefs.set("runs", runs + 1)
    prefs.set("best", {"name": "ana", "score": 40 + runs})
    pmem(0, runs + 1)
    pmem(1, -1 if best is None else best["score"])
'''

_LUA = '''
function _init()
  local runs = prefs.get("runs") or 0
  local best = prefs.get("best")
  prefs.set("runs", runs + 1)
  prefs.set("best", {name = "ana", score = 40 + runs})
  pmem(0, runs + 1)
  pmem(1, best == nil and -1 or best.score)
end
'''


def _game(tmp_path, ws, title, src, runtime, main):
    from runtime import moy_carts
    cart = moy_carts.create(title, str(tmp_path / "carts"), src=src, runtime=runtime,
                            main=main, type="game")
    ws.launcher.items.append(cart)
    ws.launcher.sel = len(ws.launcher.items) - 1
    return cart


def _play(ws):
    ws.open()
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    for _ in range(2):
        ws.input.begin_frame()
        ws.frame(1 / 30)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    pm = ws.run.project.pmem
    ws.spine.exit_to_caller()
    return list(pm.cells[:2])


@pytest.mark.parametrize("runtime", ["python", "lua"])
def test_a_game_keeps_its_prefs_across_a_restart(tmp_path, runtime):
    if runtime == "lua":
        from runtime import lua_binding
        if not lua_binding.HostLuaRun.available():
            pytest.skip("host lua binding not built (needs a C compiler)")
    ws = build_ws(tmp_path)
    src, main = (_PY, "main.py") if runtime == "python" else (_LUA, "main.lua")
    _game(tmp_path, ws, "Prefs Game", src, runtime, main)
    assert _play(ws) == [1, -1], "the first run finds no prefs"
    assert _play(ws) == [2, 40], "the second reads what the first set"
    assert _play(ws) == [3, 41]


def test_a_games_prefs_are_its_own_and_an_app_with_its_id_reads_them(tmp_path):
    """Two games keep two sets; the rows are the console's settings,
    namespaced by the cart's id, as an app's are."""
    ws = build_ws(tmp_path)
    _game(tmp_path, ws, "Prefs One", _PY, "python", "main.py")
    _play(ws)
    _play(ws)
    _game(tmp_path, ws, "Prefs Two", _PY, "python", "main.py")
    assert _play(ws) == [1, -1], "another game starts from none"
    rows = json.dumps(sorted(k for k in ws.system.keys() if k.endswith("_runs")))
    assert rows.count("_runs") == 2, rows
