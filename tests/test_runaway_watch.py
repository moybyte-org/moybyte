"""The runaway watch (native/moy_play/moy_play.h, docs/kernel_cartpath_2026-10.md
§8.2): a tick that has not come back after its budget is ended from the
watcher's thread, and the run reads as raised with the panel's stuck words.

What each check watches for:

  * a Lua loop that never ends is ended on the line it is on, and crash-to-code
    lands there, under the stuck title;
  * pcall inside the cart does not swallow it;
  * a tick that does come back is never ended, however long the frames
    between ticks are;
  * a compiled cart's loop that calls an import is terminated at that call;
  * the budget's rule: MOY_PLAY_STUCK_SLOTS of the pacing slot, clamped.
"""

import os
import time

import pytest

from runtime import host_app, moy_carts
from tools import wat
from ws_helpers import build_ws

BUDGET_MS = 300


def _need_lua():
    from runtime import lua_binding
    if not lua_binding.HostLuaRun.available():
        pytest.skip("host lua binding not built (needs a C compiler)")


@pytest.fixture
def watch():
    from runtime import moy_play
    moy_play.watch(BUDGET_MS)
    yield moy_play
    moy_play.watch(0)


def _open(ws, cart):
    ws.launcher.items.append(cart)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == cart["title"]:
            ws.launcher.sel = i
            ws.open()
            return
    raise AssertionError(cart["title"])


def _lua_ws(tmp_path, title, src):
    ws = build_ws(tmp_path)
    cart = moy_carts.create(title, str(tmp_path / "carts"), src=src, runtime="lua",
                            main="main.lua")
    _open(ws, cart)
    assert ws.player.cart_error is None, ws.player.cart_error
    return ws


LOOP = """n = 0
function _update()
  n = n + 1
  if n > 2 then
    while true do n = n + 1 end
  end
end
function _draw() cls(1) end
"""


def test_a_lua_loop_that_never_ends_is_ended_on_its_line(tmp_path, watch):
    _need_lua()
    from runtime import chrome_ops
    ws = _lua_ws(tmp_path, "Loop Lua", LOOP)
    t0 = time.monotonic()
    for _ in range(3):
        ws.frame(1 / 30)
    took = time.monotonic() - t0
    err = ws.player.cart_error
    assert err is not None and "stuck: one frame ran over 1 s" in err, err
    assert ws.player.crash_line == 5, (ws.player.crash_line, err)
    assert BUDGET_MS / 1000.0 <= took < 5.0, took
    info = watch.info()
    assert info[14] is True and err.endswith(info[7]), info
    assert chrome_ops.crash_title(err, 5) == "Your game got stuck on line 5."


def test_pcall_does_not_keep_a_stuck_lua_frame_alive(tmp_path, watch):
    _need_lua()
    ws = _lua_ws(tmp_path, "Pcall Lua", """function _update()
  while true do
    pcall(function() while true do end end)
  end
end
""")
    ws.frame(1 / 30)
    err = ws.player.cart_error
    assert err is not None and "stuck:" in err, err
    assert watch.info()[14] is True


def test_a_tick_that_comes_back_is_never_ended(tmp_path, watch):
    """The budget is a tick's, not the time between ticks: a run whose
    frames are far apart, each one quick, runs on."""
    _need_lua()
    ws = _lua_ws(tmp_path, "Slow Lua", """n = 0
function _update() n = n + 1 end
function _draw() cls(n % 16) end
""")
    for _ in range(3):
        ws.frame(1 / 30)
        time.sleep(BUDGET_MS * 1.5 / 1000.0)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert watch.info()[14] is False


def test_an_ordinary_raise_keeps_the_crash_title(tmp_path, watch):
    _need_lua()
    from runtime import chrome_ops
    ws = _lua_ws(tmp_path, "Boom Lua", "function _update()\n  error('boom')\nend\n")
    ws.frame(1 / 30)
    err = ws.player.cart_error
    assert "boom" in err and watch.info()[14] is False
    assert chrome_ops.crash_title(err, 2) == "Your game stopped on line 2."
    assert chrome_ops.crash_title(err, None) == "Your game stopped."


SPIN = """(module
  (import "moy" "btn" (func $btn (param i32 i32) (result i32)))
  (memory (export "memory") 1 1)
  (global $n (mut i32) (i32.const 0))
  (func (export "_init"))
  (func (export "_update") (param $dt f32)
    (global.set $n (i32.add (global.get $n) (i32.const 1)))
    (if (i32.gt_s (global.get $n) (i32.const 2))
      (then (loop $ever (drop (call $btn (i32.const 0) (i32.const 0))) (br $ever)))))
  (func (export "_draw")))
"""


def test_a_compiled_loop_that_calls_an_import_is_terminated_there(tmp_path, watch):
    from runtime import wasm_binding, wasm_host
    if not wasm_host.available():
        pytest.skip("no host wasm binding: %s" % (wasm_binding.why_unavailable() or "?"))
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    d = os.path.join(root, "spin.moy")
    os.makedirs(d)
    with open(os.path.join(d, "manifest.json"), "w") as f:
        f.write('{"format": "moy-1", "title": "Spin Wasm", "runtime": "wasm", '
                '"main": "main.wasm", "memory": 1}')
    with open(os.path.join(d, "main.wasm"), "wb") as f:
        f.write(wat.assemble(SPIN))
    ws = host_app.build_workstation(root)
    cart = next(c for c in ws.carts.all if c["title"] == "Spin Wasm")
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == cart["title"]:
            ws.launcher.sel = i
            ws.open()
    assert ws.player.cart_error is None, ws.player.cart_error
    for _ in range(3):
        ws.frame(1 / 30)
    err = ws.player.cart_error
    assert err is not None and "stuck: one frame ran over 1 s" in err, err
    assert watch.info()[14] is True


def test_the_stuck_words_and_the_budget_rule_are_the_tables():
    from runtime import chrome_ops
    assert chrome_ops.say_title(chrome_ops.SAY_STUCK) == "Your game got stuck."
    assert chrome_ops.say_title(chrome_ops.SAY_FIT) == "Too big for this console."
    assert chrome_ops.say_title(chrome_ops.SAY_NEWER) == "Needs a newer console."
    assert chrome_ops.newer_text("Newer Wasm", ["a", "b"]) == (
        "Newer Wasm needs a newer console (missing: a, b). Update this console in "
        "Settings, then try again.")
    # docs/os_voice_v1.md's law 3 and §3.2: none of the banned words, no "I".
    for say in range(4):
        t = chrome_ops.say_title(say, 3).lower()
        assert not any(w in t.split() for w in ("error", "failed", "i", "we")), t
    h = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "native", "moy_play", "moy_play.h")).read()
    # Under every board's task-watchdog timeout (15 s in each sdkconfig.board).
    assert "#define MOY_PLAY_STUCK_MAX_MS 10000u" in h
