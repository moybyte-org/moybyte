"""A front-taking verb a cart calls from its own load or init
(runtime/system/front.py): the open it asks for runs at the console's frame
boundary, never inside the cart's call, so the one Player switches runs the
way any switch does -- the asking run closed, the receiver's runtime in
place, nothing of the asker's left behind."""

import json
import os

import pytest

from runtime import host_app, moy_evq, scheduler as S
from runtime import intents as I
from ws_helpers import open_cart

_DT = 1.0 / 30.0

SHARE_WAT = """(module
  (import "moybyte.app" "moybyte_share"
    (func $share (param i32 i32 i32 i32) (result i32)))
  (memory (export "memory") 1 1)
  (data (i32.const 64) "a line")
  (data (i32.const 96) "text/plain")
  (func (export "_init") (drop (call $share (i32.const 64) (i32.const 6)
                                            (i32.const 96) (i32.const 10))))
  (func (export "_update") (param $dt f32))
  (func (export "_draw"))
)
"""

SHARE_LUA_LOAD = 'moybyte.share("a line", "text/plain")\nfunction _draw() cls(1) end\n'
SHARE_LUA_INIT = ('function _init() moybyte.share("a line", "text/plain") end\n'
                  'function _draw() cls(1) end\n')


def _manifest(title, runtime, main, extra=None):
    man = {"format": "moy-1", "title": title, "id": "local.sharer", "runtime": runtime,
           "main": main, "moybyte": {"type": "app", "permissions": ["front"]}}
    man.update(extra or {})
    return man


def _wasm_cart(tmp_path, root):
    from runtime import wasm_binding, wasm_host
    from tools import wasm_cart
    if not wasm_host.available():
        why = wasm_binding.why_unavailable() or "unknown"
        if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
            pytest.fail("the host wasm binding did not build: %s" % why)
        pytest.skip("no host wasm binding: %s" % why)
    src = tmp_path / "src" / "sharer.moy"
    (src / "src").mkdir(parents=True)
    (src / "manifest.json").write_text(json.dumps(
        _manifest("Sharer", "wasm", "main.wasm", {"extensions": ["moybyte.app"], "memory": 1})))
    (src / "src" / "main.wat").write_text(SHARE_WAT)
    try:
        wasm_cart.build(str(src), os.path.join(root, "sharer.moy"))
    except wasm_cart.ToolchainMissing as exc:
        pytest.skip(str(exc))


def _lua_cart(root, text):
    if S._svc() is None:
        pytest.skip("no Lua runtime on this host")
    dst = os.path.join(root, "sharer.moy")
    os.makedirs(dst)
    with open(os.path.join(dst, "manifest.json"), "w") as f:
        json.dump(_manifest("Sharer", "lua", "main.lua"), f)
    with open(os.path.join(dst, "main.lua"), "w") as f:
        f.write(text)


@pytest.mark.parametrize("tier", ["wasm", "lua-load", "lua-init"])
def test_a_share_from_a_carts_load_opens_notes_at_the_frame_boundary(tmp_path, monkeypatch,
                                                                      tier):
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    if tier == "wasm":
        _wasm_cart(tmp_path, root)
    else:
        _lua_cart(root, SHARE_LUA_LOAD if tier == "lua-load" else SHARE_LUA_INIT)
    ws = host_app.build_workstation(root)
    runtime = "wasm" if tier == "wasm" else "lua"

    runs, closed = [], []
    make = ws.runtimes[runtime]

    def spy(ns, src):
        run = make(ns, src)
        runs.append(run)
        close = run.close

        def closing():
            closed.append(run)
            close()
        run.close = closing
        return run
    monkeypatch.setitem(ws.runtimes, runtime, spy)
    said = []
    share = I.INTENTS.share

    def answering(pid, data, mime):
        said.append(share(pid, data, mime))
        return said[-1]
    monkeypatch.setattr(I.INTENTS, "share", answering)

    try:
        open_cart(ws, "Sharer")
        player = ws.run.player
        asker = player.pid
        assert said == [True], "the request is accepted"
        assert ws.cart["title"] == "Sharer", "nothing opens inside the cart's call"
        assert runs and player._lua is runs[0] and not closed
        for _ in range(3):
            ws.mark_dirty()
            ws.frame(_DT)
        player = ws.run.player
        assert closed == runs, "the asking run was closed"
        assert ws.cart["title"] == "Notes" and player.cart_error is None
        assert player._app_id == "moybyte.notes" and not player._wasm
        assert player._lua is None and player.pid != asker, "the runtime is Notes'"
        assert not moy_evq.has(asker), "the asker's queue went with its run"
        assert asker not in I.INTENTS.held
        if tier == "wasm":
            assert runs[0]._run._r is None, "the module's instance was freed"
    finally:
        ws.spine.exit()
