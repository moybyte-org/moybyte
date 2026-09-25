"""The compiled-cart Player path on the host (docs/wasm_tier_plan_2026-09.md,
phase 3): a `"runtime": "wasm"` cart through `ws.runtimes`, on the boards' own
import table (libmoy's moy_wasm.c) over WAMR built for Linux at the boards' pin.

What is pinned here:

  * the hello cart (tests/fixtures/wasm/hello.moy, WAT source assembled at
    test time -- no module is ever committed) runs from the launcher, and its
    frame is a PIXEL GOLDEN at the 320x240 row, the shell goldens' mechanism;
  * `_init`/`_update(dt)`/`_draw` run under the tick model exactly as a Lua
    cart's do;
  * a trap ends the run with no partial frame on the canvas, no crash-to-code
    and no EDIT action on its panel; quit() ends it as the cart's own choice;
  * a build without the runtime (an absent key) opens the runtime-missing panel;
  * the store never reads a compiled cart's main as text, the Code tab exists
    only when the cart ships `src/`, no text write ever reaches its module, a
    copy carries the module's bytes, and the sync walk leaves it home.

The binding needs a C compiler, cmake and the pinned WAMR (runtime/
wasm_binding.py fetches it). Without them the tests that run a cart SKIP on a
bench -- and FAIL under `CI` or `MOYBYTE_REQUIRE_HOST_WASM`, where a skip would
hide the tier.
"""

import hashlib
import json
import os
import shutil

import pytest

from runtime import host_app
from tools import wasm_cart, wat
from ws_helpers import open_cart

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "wasm")
HELLO = os.path.join(FIXTURES, "hello.moy")
GOLDEN_FILE = os.path.join(ROOT, "tests", "shell_goldens", "wasm_hello.json")
GOLDEN_ROW = "tdeck_320x240_fs1_dark"
UPDATE_ENV = "MOYBYTE_UPDATE_GOLDENS"
GOLDEN_FRAMES = 12
_DT = 1.0 / 30.0


def _binding_or_skip():
    from runtime import wasm_binding, wasm_host
    if wasm_host.available():
        return
    why = wasm_binding.why_unavailable() or "unknown"
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
        pytest.fail("the host wasm binding did not build: %s" % why)
    pytest.skip("no host wasm binding: %s" % why)


def _store(tmp_path, *carts):
    """A cart store holding the seeds plus `carts` (built cart folders)."""
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    for c in carts:
        c(root)
    return root


def _hello(root):
    wasm_cart.build(HELLO, os.path.join(root, "hello.moy"))


def _wat_cart(title, src, pages, **manifest):
    """A compiled cart assembled from inline WAT, as a store builder."""
    def build(root):
        d = os.path.join(root, title.lower().replace(" ", "_") + ".moy")
        os.makedirs(d)
        man = {"format": "moy-1", "title": title, "runtime": "wasm",
               "main": "main.wasm", "memory": pages}
        man.update(manifest)
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(man, f)
        with open(os.path.join(d, "main.wasm"), "wb") as f:
            f.write(wat.assemble(src))
    return build


def _quiesce(ws):
    ws.pointer.visible = False
    ws._toast_until = 0
    ws._egg_until = 0
    ws._confetti_until = 0
    ws.show_fps = False
    ws.perf_hud = False
    ws.perf_capture = False


def _frames(ws, n):
    for _ in range(n):
        _quiesce(ws)
        ws._dirty = True
        ws.frame(_DT)


def _cart(ws, title):
    return next(c for c in ws.carts.all if c["title"] == title)


# -- the run -------------------------------------------------------------------


def test_the_hello_cart_runs_from_the_launcher(tmp_path):
    _binding_or_skip()
    ws = host_app.build_workstation(_store(tmp_path, _hello))
    assert "wasm" in ws.runtimes
    open_cart(ws, "Hello Wasm")
    assert ws.player.cart_error is None, ws.player.cart_error
    assert type(ws.player._lua).__name__ == "WasmHostRun"
    _frames(ws, 30)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert ws.player._update is not None


def test_the_hello_cart_golden_at_the_320x240_row(tmp_path, request):
    """The frame the hello cart presents, hashed against committed bytes: a
    256-colour palette blit, verbs drawn over it in call order, a greeting read
    from the cart's own file. Re-baseline deliberately, as the shell goldens
    are: `MOYBYTE_UPDATE_GOLDENS=1` or `--update-goldens`."""
    _binding_or_skip()
    ws = host_app.build_workstation(_store(tmp_path, _hello))
    ws.look.set_theme_variant("dark", persist=False)
    open_cart(ws, "Hello Wasm")
    _frames(ws, GOLDEN_FRAMES)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert (ws.sys_canvas.w, ws.sys_canvas.h) == (320, 240)
    got = hashlib.sha256(bytes(ws.sys_canvas._buf)).hexdigest()
    update = (os.environ.get(UPDATE_ENV)
              or request.config.getoption("--update-goldens", default=False))
    if update:
        with open(GOLDEN_FILE, "w") as f:
            json.dump({GOLDEN_ROW: got}, f, indent=2, sort_keys=True)
            f.write("\n")
        return
    with open(GOLDEN_FILE) as f:
        want = json.load(f)[GOLDEN_ROW]
    assert got == want, (
        "the hello cart's frame moved (%s != %s). Re-baseline only if you can "
        "say which pixel moved and why: %s=1 .venv/bin/python -m pytest "
        "tests/test_wasm_cart.py -k golden" % (got, want, UPDATE_ENV))


# A cart that counts its hooks into pmem: slot 0 the _init calls, 1 the
# _update calls, 2 the _draw calls, 3 the last dt in microseconds.
COUNTER = """
(module
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))
  (memory (export "memory") 1 1)
  (func $bump (param $s i32)
    (drop (call $pmem (local.get $s)
      (i32.add (call $pmem (local.get $s) (i32.const 0) (i32.const 0))
               (i32.const 1))
      (i32.const 1))))
  (func (export "_init") (call $bump (i32.const 0)))
  (func (export "_update") (param $dt f32)
    (call $bump (i32.const 1))
    (drop (call $pmem (i32.const 3)
      (i32.trunc_f32_s (f32.mul (local.get $dt) (f32.const 1000000)))
      (i32.const 1))))
  (func (export "_draw") (call $bump (i32.const 2))))
"""


def _pmem(ws):
    return ws.player._lua._run.pmem()[1]


def test_the_hooks_run_under_the_tick_model(tmp_path):
    """A 30Hz cart on a 60Hz loop: `_update` at the cart's rate with `dt` its
    period, `_draw` on the scheduler's divisor -- the Lua tier's contract."""
    _binding_or_skip()
    ws = host_app.build_workstation(_store(
        tmp_path, _wat_cart("Counter", COUNTER, 1)))
    open_cart(ws, "Counter")
    assert ws.player.cart_error is None, ws.player.cart_error
    for _ in range(60):
        ws.frame(1.0 / 60.0)
    got = _pmem(ws)
    assert got[0] == 1, got[:4]
    assert 29 <= got[1] <= 31, got[:4]           # one second at 30 ticks
    assert 1 <= got[2] <= got[1], got[:4]
    assert abs(got[3] - 33333) <= 1, got[3]      # dt is the tick's period


TRAP_IN_DRAW = """
(module
  (import "moy" "cls" (func $cls (param i32)))
  (memory (export "memory") 1 1)
  (global $n (mut i32) (i32.const 0))
  (func (export "_init"))
  (func (export "_update") (param f32)
    (global.set $n (i32.add (global.get $n) (i32.const 1))))
  (func (export "_draw")
    (call $cls (i32.const 8))
    (if (i32.gt_s (global.get $n) (i32.const 3)) (then unreachable))))
"""


def test_a_trap_ends_the_run_with_no_partial_frame_and_no_edit(tmp_path):
    _binding_or_skip()
    ws = host_app.build_workstation(_store(
        tmp_path, _wat_cart("Trapper", TRAP_IN_DRAW, 1)))
    open_cart(ws, "Trapper")
    assert ws.player.cart_error is None
    _frames(ws, 3)
    red = ws.canvas._wire[8]
    assert ws.canvas._buf[0] | ws.canvas._buf[1] << 8 == red
    _frames(ws, 3)
    err = ws.player.cart_error
    assert err and "unreachable" in err, err
    # The run is over and never called again; the kid stays on the panel --
    # no throw into the Code tab, no line to mark.
    assert ws.player._update is None and ws.player._draw is None
    assert ws.player.crash_line is None and ws.player.crash_file is None
    assert not ws.wm.top_is("menu"), "a trap threw into the Editor"
    # No partial frame: the red _draw that trapped is gone from the canvas;
    # what the panel does not cover is black.
    w = ws.canvas.w
    corner = (ws.canvas.h - 1) * w
    px = ws.canvas._buf[2 * corner] | ws.canvas._buf[2 * corner + 1] << 8
    assert px == ws.canvas._wire[0], hex(px)
    # The crash bar offers no EDIT/CODE for a compiled cart, and its slot
    # does nothing when tapped.
    from runtime import bar_layer
    assert bar_layer._edit_kind(ws.cart) is None
    x, y, bw, bh = bar_layer._MENU_BTN
    assert ws.bar_layer.handle_cart_tap(x + 1, y + 1) is False


QUITTER = """
(module
  (import "moy" "quit" (func $quit))
  (import "moy" "cls" (func $cls (param i32)))
  (memory (export "memory") 1 1)
  (func (export "_init"))
  (func (export "_update") (param f32) (call $quit) (call $cls (i32.const 8)))
  (func (export "_draw") (call $cls (i32.const 9))))
"""


def test_quit_ends_the_cart_as_its_own_choice(tmp_path):
    _binding_or_skip()
    ws = host_app.build_workstation(_store(
        tmp_path, _wat_cart("Quitter", QUITTER, 1)))
    open_cart(ws, "Quitter")
    _frames(ws, 2)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert ws.wm.top_is("launcher"), "quit() did not return to the caller"


def test_a_memory_the_manifest_does_not_declare_is_refused(tmp_path):
    _binding_or_skip()
    ws = host_app.build_workstation(_store(
        tmp_path, _wat_cart("Liar", COUNTER, 2)))
    open_cart(ws, "Liar")
    err = ws.player.cart_error or ""
    assert "refused" in err and "manifest declares 2" in err, err


def test_a_build_without_the_runtime_opens_the_runtime_missing_panel(tmp_path):
    """The host side of "a build without the module": the wasm key absent
    from `ws.runtimes`, which is what a board image without moy_wasm and a
    host without a compiler both are. The panel, never a hang."""
    ws = host_app.build_workstation(_store(tmp_path, _hello))
    ws.runtimes.pop("wasm", None)
    open_cart(ws, "Hello Wasm")
    assert "needs the wasm runtime (not in this build)" in ws.player.cart_error
    assert not ws.wm.top_is("menu")
    _frames(ws, 2)                                # the panel frame must not raise


# -- the store -------------------------------------------------------------------


def test_the_store_never_reads_the_module_as_text(tmp_path):
    root = _store(tmp_path, _hello)
    cart = host_app.moy_carts.load(os.path.join(root, "hello.moy"))
    assert cart["runtime"] == "wasm" and cart["main"] == "main.wasm"
    assert cart["src"] == ""
    assert cart["memory"] == 3
    # its source is src/, and that is its code
    assert host_app.moy_carts.cart_sources(cart) == ["src/main.wat"]
    assert "(module" in host_app.moy_carts.source_text(cart)
    # a slim scan still requires the module to exist
    os.remove(os.path.join(root, "hello.moy", "main.wasm"))
    assert host_app.moy_carts.load(os.path.join(root, "hello.moy"), src=False) is None


def test_no_text_write_reaches_the_module(tmp_path):
    root = _store(tmp_path, _hello)
    path = os.path.join(root, "hello.moy")
    cart = host_app.moy_carts.load(path)
    before = open(os.path.join(path, "main.wasm"), "rb").read()
    status, _ = host_app.moy_carts.save_code(cart, ";; edited\n")
    assert status == host_app.moy_carts.SAVE_OK
    assert open(os.path.join(path, "src", "main.wat")).read() == ";; edited\n"
    status, _ = host_app.moy_carts.save_code(cart, "x", name="main.wasm")
    assert status == host_app.moy_carts.SAVE_BAD_SYNTAX
    assert open(os.path.join(path, "main.wasm"), "rb").read() == before


def test_the_code_tab_is_absent_unless_the_cart_ships_src(tmp_path):
    ws = host_app.build_workstation(_store(
        tmp_path, _hello, _wat_cart("Bare", COUNTER, 1)))
    ws.open_in_editor(_cart(ws, "Bare"))
    assert ws.code_sources() == []
    assert ws.editor_app.tab != "code"
    ws.set_menu_view("code")
    assert ws.editor_app.tab == "cards", "a Code tab over no code"
    assert "code" not in [t for t, _g in ws.editor_app._ladder()]
    assert "code" not in [t[0] for t in ws.editor_app._chips()]
    ws.open_in_editor(_cart(ws, "Hello Wasm"))
    assert ws.code_sources() == ["src/main.wat"]
    ws.set_menu_view("code")
    assert ws.editor_app.tab == "code"
    assert ws.code_file_name() == "src/main.wat"
    assert "(module" in ws.editor.text()


def test_a_copy_carries_the_module_bytes(tmp_path):
    root = _store(tmp_path, _hello)
    cart = host_app.moy_carts.load(os.path.join(root, "hello.moy"))
    dup = host_app.moy_carts.duplicate(cart, root)
    assert dup is not None and dup["runtime"] == "wasm" and dup["memory"] == 3
    a = open(os.path.join(cart["path"], "main.wasm"), "rb").read()
    b = open(os.path.join(dup["path"], "main.wasm"), "rb").read()
    assert a == b
    assert host_app.moy_carts.cart_sources(dup) == ["src/main.wat"]


def test_the_sync_walk_leaves_the_module_home(tmp_path):
    """The sync RPC declines binary files, so a compiled cart's module never
    crosses (runtime/moy_sync.py says so): its manifest and text do."""
    from runtime import moy_sync
    root = _store(tmp_path, _hello)
    path = os.path.join(root, "hello.moy")
    assert moy_sync._read_text(os.path.join(path, "main.wasm")) is None
    assert moy_sync._read_text(os.path.join(path, "manifest.json")) is not None


# -- the compiled module's name on a board ---------------------------------------


def test_the_tool_and_the_board_name_the_compiled_module_alike():
    from device import moycore_glue
    for main in ("main.wasm", "doom.wasm", "game"):
        for chip in ("esp32s3", "esp32p4"):
            assert (moycore_glue.aot_path("/c", main, chip)
                    == "/c/" + wasm_cart.aot_name(main, chip))


def test_the_board_reads_the_head_up_to_the_memory_section(tmp_path):
    from device import moycore_glue
    out = wasm_cart.build(HELLO, str(tmp_path / "hello.moy"))
    blob = open(out["main"], "rb").read()
    head = moycore_glue.wasm_head(out["main"])
    assert blob.startswith(head) and 8 < len(head) < len(blob)
    sections = [sid for sid, _n, _b in __import__(
        "tools.wasm_module", fromlist=["sections"]).sections(blob)]
    assert 5 in sections
