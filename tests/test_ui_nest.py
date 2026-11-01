"""A nest opened past the engine's depth is an error in every face
(docs/ui_blocks_2026-10.md §4.0): inside two open scroll regions
(moy_ui_api.h's MOY_UI_API_NEST), `ui.scroll`, `ui.text_view`, `ui.dialog`
and `ui.popover` raise in Python and Lua, and their wasm imports answer
-MOY_UI_DEEP, a value none of the four answers otherwise. The fixtures,
tests/fixtures/ui_nest/nest_py.moy, nest_lua.moy and
tests/fixtures/wasm/app_nest.moy, make the same calls through the Player
each runs in and leave their answers in pmem.
"""

import os
import shutil

import pytest

from runtime import host_app
from test_ui_public import _frame, _pmem, _wasm_or_skip
from ws_helpers import make_drv, open_cart

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEST = os.path.join(ROOT, "tests", "fixtures", "ui_nest")
WASM = os.path.join(ROOT, "tests", "fixtures", "wasm", "app_nest.moy")
MOY_UI_DEEP = 5                         # moy_ui.h
VERBS = ("scroll", "text_view", "dialog", "popover")


@pytest.fixture(scope="module")
def nest_runs(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("ui_nest") / "carts")
    host_app.moy_carts.ensure_dirs(root)
    for d in ("nest_py.moy", "nest_lua.moy"):
        shutil.copytree(os.path.join(NEST, d), os.path.join(root, d))
    titles = {"python": "UI Nest Py", "lua": "UI Nest Lua"}
    if _wasm_or_skip():
        from tools import wasm_cart
        wasm_cart.build(WASM, os.path.join(root, "app_nest.moy"))
        titles["wasm"] = "App Nest Wasm"
    out = {}
    for name, title in titles.items():
        ws = host_app.build_workstation(root)
        ws.pointer.visible = False
        open_cart(ws, title)
        drv = make_drv(ws)
        for _ in range(3):
            _frame(drv)
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        out[name] = _pmem(ws)[:6]
        ws.exit()
    return out


@pytest.mark.parametrize("face", ("python", "lua"))
def test_a_nest_past_the_depth_raises(nest_runs, face):
    pm = nest_runs[face]
    assert pm[5] >= 3, "the cart drew"
    for i, verb in enumerate(VERBS):
        assert pm[i] == 1, "%s: ui.%s nested too deep answered %d (1: raised)" % (
            face, verb, pm[i])
    assert pm[4] == 1, "a scroll region opens again once the nest is closed"


def test_a_nest_past_the_depth_answers_deep_in_wasm(nest_runs):
    if "wasm" not in nest_runs:
        pytest.skip("no host wasm binding")
    pm = nest_runs["wasm"]
    assert pm[5] >= 3, "the app drew"
    assert pm[:4] == [-MOY_UI_DEEP] * 4, dict(zip(VERBS, pm))
    assert pm[4] == 0, "a scroll region opens again once the nest is closed"
