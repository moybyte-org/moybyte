"""Hostile modules against the host's build of the wasm tier.

  * an import a par item makes is refused on every table: a compiled app's
    moybyte.app row answers STALE from an item and does nothing, as libmoy's
    own rows trap (native/moycore/libmoy/moy_wasm.c's `par`), because on a
    board the items run on several cores at once;
  * the fuzz corpus and every crash the fuzzers found (tests/fixtures/
    wasm_fuzz/, README there) replay through WAMR built as the host builds
    it -- load, instantiate, a bounded run of each export -- and through a
    board's AOT loader, admission and provenance check where the machine
    can build a 32-bit one, with none of them crashing.
"""

import os
import subprocess

import pytest

from runtime import host_app, os_log
from runtime import moy_log as L
from tools import wasm_cart
from ws_helpers import open_cart

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUZZ = os.path.join(ROOT, "tests", "fixtures", "wasm_fuzz")

# _init hands par one item, which calls moybyte_log("item") and keeps the
# answer at 512; _init then logs "refused" when the answer was negative and
# "ran" when it was not.
_PAR_APP = """
(module
  (import "moy" "par" (func $par (param i32 i32 i32 i32)))
  (import "moybyte.app" "moybyte_log" (func $log (param i32 i32 i32) (result i32)))
  (memory (export "memory") 2 2)
  (global $sp (export "__stack_pointer") (mut i32) (i32.const 4096))
  (data (i32.const 256) "item")
  (data (i32.const 264) "refused")
  (data (i32.const 272) "ran")
  (func (export "_par") (param $i i32) (param $a i32)
    (i32.store (i32.const 512) (call $log (i32.const 1) (i32.const 256) (i32.const 4))))
  (func (export "_init")
    (i32.store (i32.const 512) (i32.const 7))
    (call $par (i32.const 1) (i32.const 0) (i32.const 8192) (i32.const 4096))
    (if (i32.lt_s (i32.load (i32.const 512)) (i32.const 0))
      (then (drop (call $log (i32.const 1) (i32.const 264) (i32.const 7))))
      (else (drop (call $log (i32.const 1) (i32.const 272) (i32.const 3))))))
  (func (export "_update") (param f32))
  (func (export "_draw")))
"""


def _binding_or_skip():
    from runtime import wasm_binding, wasm_host
    if wasm_host.available():
        return
    why = wasm_binding.why_unavailable() or "unknown"
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
        pytest.fail("the host wasm binding did not build: %s" % why)
    pytest.skip("no host wasm binding: %s" % why)


def test_a_par_item_reaches_no_app_row(tmp_path):
    _binding_or_skip()
    src = tmp_path / "src"
    (src / "src").mkdir(parents=True)
    (src / "src" / "main.wat").write_text(_PAR_APP)
    (src / "manifest.json").write_text(
        '{"format": "moy-1", "title": "Par App", "id": "local.parapp", "runtime": "wasm",'
        ' "main": "main.wasm", "memory": 2, "extensions": ["moybyte.app"],'
        ' "moybyte": {"type": "app", "permissions": []}}')
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    wasm_cart.build(str(src), os.path.join(root, "par_app.moy"))
    ws = host_app.build_workstation(root)
    since = L.next()
    open_cart(ws, "Par App")
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    pid = ws.run.player.pid
    ws._dirty = True
    ws.frame(1.0 / 30.0)
    lines = [t for _s, _t, _l, q, t in os_log.records(since, 256)
             if q == pid and not t.startswith("start ")]
    ws.exit()
    assert lines == ["refused"], lines


def _inputs(*dirs):
    out = []
    for d in dirs:
        p = os.path.join(FUZZ, d)
        if os.path.isdir(p):
            out += sorted(os.path.join(p, n) for n in os.listdir(p))
    return out


@pytest.mark.parametrize("target,dirs", [
    ("interp_fast64", ("seeds", "interp_fast64", "crashes/interp_fast64")),
    ("interp_classic32", ("seeds", "interp_classic32", "crashes/interp_classic32")),
    ("aot_rv32", ("aot_rv32", "crashes/aot_rv32")),
    ("aot_xtensa", ("aot_xtensa", "crashes/aot_xtensa")),
])
def test_the_fuzz_corpus_and_its_crashes_replay_clean(target, dirs):
    """Every kept input through its harness (tools/wasm_fuzz.py replay),
    under the sanitizers where the compiler has them: none may crash."""
    from tools import wasm_fuzz
    from runtime import native_build
    if native_build.cc() is None:
        pytest.skip("no C compiler")
    files = _inputs(*dirs)
    assert files, "no inputs kept for %s" % target
    try:
        bad = wasm_fuzz.replay(target, files, timeout=600)
    except RuntimeError as exc:
        if "no WAMR at the pin" in str(exc) and not os.environ.get("CI"):
            pytest.skip(str(exc))
        raise
    if bad is None:
        pytest.skip("%s needs a 32-bit libc here" % target)
    assert not bad, "\n\n".join("%s\n%s" % (os.path.basename(p), why) for p, why in bad[:3])
