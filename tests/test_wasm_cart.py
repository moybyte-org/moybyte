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
  * a cart bigger than the console -- the huge fixture, past any board's PSRAM,
    or any cart past the host's configured limit -- opens the fit NOTICE by
    the boards' own footprint arithmetic, not an error panel, and a load that
    still runs out of memory gets the same notice;
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


def _tier(root):
    wasm_cart.build(os.path.join(FIXTURES, "tier.moy"), os.path.join(root, "tier.moy"))


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


def test_the_tier_cart_frame_is_the_one_every_tier_holds(tmp_path, request):
    """The tier cart (tests/fixtures/wasm/tier.moy) draws the same frame on
    every tier: par's four bands into a 565 frame, blit565, then `read`'s and
    `cfg`'s text and a circle over it. This is the host's side of its golden
    (tests/tier_frame.py); tests/test_web_wasm_e2e.py holds the browser's
    canvas to the same digest. Re-baseline as the hello golden is."""
    import tier_frame
    import device_canvas as dc
    _binding_or_skip()
    ws = host_app.build_workstation(_store(tmp_path, _tier))
    open_cart(ws, "Tier Wasm")
    _frames(ws, GOLDEN_FRAMES)
    assert ws.player.cart_error is None, ws.player.cart_error
    cv = ws.sys_canvas
    assert (cv.w, cv.h) == (tier_frame.W, tier_frame.H)
    got = tier_frame.digest(tier_frame.from_canvas(cv._buf, dc.PAL565_WIRE is not dc.PAL565))
    if os.environ.get(UPDATE_ENV) or request.config.getoption("--update-goldens",
                                                              default=False):
        tier_frame.GOLDEN.write_text(json.dumps({tier_frame.ROW: got}, indent=2,
                                                sort_keys=True) + "\n")
        return
    assert got == tier_frame.golden(), (
        "the tier cart's frame moved. Re-baseline only if you can say which "
        "pixel moved and why: %s=1 .venv/bin/python -m pytest "
        "tests/test_wasm_cart.py -k tier" % UPDATE_ENV)


def test_the_tier_cart_streams_and_traps_on_the_host(tmp_path):
    """Its other two halves: every _update fills the stream's room through
    `snd`, which the host's output drains, and button A traps -- the run ends
    on the error panel with no EDIT, as every compiled cart's trap does."""
    _binding_or_skip()
    ws = host_app.build_workstation(_store(tmp_path, _tier))
    open_cart(ws, "Tier Wasm")
    _frames(ws, 10)
    queued, played, _starved, room = ws.player._lua.snd_counts()[:4]
    assert queued > 0 and played > 0 and queued - played + room == 2048
    ws.input.set_held("a", True)
    for _ in range(3):
        ws.input.begin_frame()
        ws._dirty = True
        ws.frame(_DT)
    assert "unreachable" in (ws.player.cart_error or ""), ws.player.cart_error


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


# A cart that writes time() into pmem slot 0 every _update.
CLOCK = """
(module
  (import "moy" "time" (func $time (result i32)))
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))
  (memory (export "memory") 1 1)
  (func (export "_init"))
  (func (export "_update") (param f32)
    (drop (call $pmem (i32.const 0) (call $time) (i32.const 1))))
  (func (export "_draw")))
"""


def test_a_compiled_carts_time_is_the_players_cart_clock(tmp_path):
    """time() through the Player is milliseconds since the Player stamped the
    run, as on a board (lua_ext.snap_shared fills the snapshot's clock on
    both). It read 0 plus the tick's own milliseconds until the host filled
    it, and Doom's game clock never left its first tic."""
    _binding_or_skip()
    from runtime.ticks import _ticks_diff
    ws = host_app.build_workstation(_store(
        tmp_path, _wat_cart("Clock", CLOCK, 1)))
    open_cart(ws, "Clock")
    assert ws.player.cart_error is None, ws.player.cart_error
    ws.input.cart_start_ms = _ticks_diff(ws.input.cart_start_ms, 5000)
    ws.frame(_DT)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert 5000 <= _pmem(ws)[0] < 6000, _pmem(ws)[0]


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


# A cart that keeps the console's sample queue full: every _update asks for
# the room and fills it from a 441 Hz square wave, counting what the console
# took into pmem slot 0.
TONE = """
(module
  (import "moy" "snd" (func $snd (param i32 i32) (result i32)))
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))
  (memory (export "memory") 1 1)
  (global $sent (mut i32) (i32.const 0))
  (func (export "_init") (local $i i32)
    (block $done
      (loop $each
        (br_if $done (i32.ge_u (local.get $i) (i32.const 2048)))
        (i32.store16 offset=1024 (i32.shl (local.get $i) (i32.const 1))
          (select (i32.const 8000) (i32.const -8000)
                  (i32.lt_u (i32.rem_u (local.get $i) (i32.const 50)) (i32.const 25))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $each))))
  (func (export "_update") (param f32)
    (global.set $sent (i32.add (global.get $sent)
      (call $snd (i32.const 1024) (call $snd (i32.const 0) (i32.const 0)))))
    (drop (call $pmem (i32.const 0) (global.get $sent) (i32.const 1))))
  (func (export "_draw")))
"""


def test_a_carts_samples_reach_the_audio_output_at_the_rate(tmp_path):
    """snd on the host: the cart fills the room each tick, the console's audio
    backend mixes the stream into every block it renders, and over ten seconds
    of frames the output takes 22050 frames a second of it -- what the
    cart queued is what played plus what is still queued, nothing starved
    once the stream began, and the blocks carry the tone."""
    _binding_or_skip()
    ws = host_app.build_workstation(_store(
        tmp_path, _wat_cart("Tone", TONE, 1)))
    open_cart(ws, "Tone")
    assert ws.player.cart_error is None, ws.player.cart_error
    run = ws.player._lua
    assert ws.audio.stream is run._run
    _frames(ws, 300)
    queued, played, starved, room = run.snd_counts()
    assert _pmem(ws)[0] == queued
    assert queued - played == 2048 - room
    assert starved == 0
    # The host renders at the engine's own rate, so the stream is resampled,
    # and the resampler holds the stream's next frame in hand.
    assert 22050 * 10 - 2 <= played <= 22050 * 10, played
    pcm = ws.audio.last_pcm
    peak = max(abs(int.from_bytes(pcm[i:i + 2], "little", signed=True))
               for i in range(0, len(pcm), 2))
    assert peak == 8000 * ws.audio.engine.master // 7, peak


QUITTER = """
(module
  (import "moy" "quit" (func $quit))
  (import "moy" "cls" (func $cls (param i32)))
  (memory (export "memory") 1 1)
  (func (export "_init"))
  (func (export "_update") (param f32) (call $quit) (call $cls (i32.const 8)))
  (func (export "_draw") (call $cls (i32.const 9))))
"""


def test_a_folder_reads_as_a_missing_file(tmp_path):
    """`read` of a name that is a folder in the cart -- the cart's own src/ --
    reads nothing and sizes 0, as a missing file does, where stdio would open
    the folder on Linux and answer its size query with garbage. A file beside
    it still reads."""
    _binding_or_skip()

    def _readdir(root):
        wasm_cart.build(os.path.join(FIXTURES, "readdir.moy"),
                        os.path.join(root, "readdir.moy"))
    root = _store(tmp_path, _readdir)
    ws = host_app.build_workstation(root)
    open_cart(ws, "Read Dir Wasm")
    assert ws.player.cart_error is None, ws.player.cart_error
    size = os.path.getsize(os.path.join(root, "readdir.moy", "manifest.json"))
    assert _pmem(ws)[:3] == [0, 0, size], _pmem(ws)[:3]


def _files(root):
    wasm_cart.build(os.path.join(FIXTURES, "wasm_files.moy"),
                    os.path.join(root, "wasm_files.moy"))


def _written(root):
    """The Files Wasm cart's written folder, beside the carts store."""
    d = os.path.join(os.path.dirname(root), "written", "wasm_files")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def test_a_carts_written_files_outlive_a_restart(tmp_path):
    """Files Wasm checks itself over two turns (its src/main.wat says what):
    the first writes over a shipped default, keeps "Case" and "case" apart and
    lists what it wrote with what it shipped; the second, on a console started
    afresh over the same store, finds all of it and erases it, so the third is
    a first again. The files live beside the carts store, under the names
    moy-spec's libmoy/port/moy_files.c gives them, and the cart's folder is
    never written."""
    _binding_or_skip()
    root = _store(tmp_path, _files)
    folder = os.path.join(root, "wasm_files.moy")
    shipped = sorted(os.listdir(folder))
    for turn in (1, 2, 1):
        ws = host_app.build_workstation(root)
        open_cart(ws, "Files Wasm")
        assert ws.player.cart_error is None, ws.player.cart_error
        assert _pmem(ws)[:4] == [turn, 0, 0, 4], _pmem(ws)[:4]
        ws.player.release_world()
        if turn == 1:
            assert _written(root) == ["options.cfg", "saves%2f%43ase.sav",
                                      "saves%2fcase.sav", "saves%2fslot1.sav"]
        else:
            assert _written(root) == []
        assert sorted(os.listdir(folder)) == shipped


def test_a_write_cut_short_leaves_the_last_whole_copy(tmp_path):
    """A power loss mid-write leaves "<key>~part" (torn) or "<key>~done"
    (whole, not yet in place): the next session drops the first and puts the
    second in place, and only whole copies are ever read."""
    from runtime import cart_files
    cart = tmp_path / "carts" / "wasm_files.moy"
    cart.mkdir(parents=True)
    d = tmp_path / "written" / "wasm_files"
    d.mkdir(parents=True)
    (d / "options.cfg").write_bytes(b"old")
    (d / "options.cfg~done").write_bytes(b"new")
    (d / "saves%2fslot1.sav~part").write_bytes(b"to")
    f = cart_files.CartFiles(str(cart))
    assert sorted(os.listdir(d)) == ["options.cfg"]
    assert f.read(b"options.cfg", 0, 16) == b"new"
    assert f.read(b"saves/slot1.sav", 0, 0) is None
    assert f.write(b"saves/slot1.sav", b"whole") == 0
    assert sorted(os.listdir(d)) == ["options.cfg", "saves%2fslot1.sav"]
    assert f.name(b"saves/", 0) == b"saves/slot1.sav" and f.name(b"saves/", 1) is None
    assert f.erase(b"saves/slot1.sav") == 0 and f.erase(b"saves/slot1.sav") == -1


def test_the_key_is_the_desktop_players(tmp_path):
    """One rule names a written file on every host: moy-spec's
    libmoy/port/moy_files.c, whose own test pins these same names."""
    from runtime import cart_files
    for path, want in ((b"saves/slot1.sav", "saves%2fslot1.sav"),
                       (b"saves/Case.sav", "saves%2f%43ase.sav"),
                       (b".hidden", "%2ehidden"), (b"trail.", "trail%2e"),
                       (b"a b%c~d", "a%20b%25c%7ed"), (b"con.cfg", "%63on.cfg"),
                       (b"com1", "%63om1"), (b"console.cfg", "console.cfg")):
        assert cart_files.key(path) == want
        assert cart_files.path_of(want) == path
    assert cart_files.path_of("x~done") is None


def test_par_leaves_what_running_the_items_in_order_leaves(tmp_path):
    """The Par Wasm fixture checks itself every frame: par's eight items,
    each on the stack the rule gives it, against the same eight called one
    after another -- no byte differs, every item saw its own stack pointer
    and the caller's came back. The host runs par's items in order; the
    boards' suites run the same fixture across their cores."""
    _binding_or_skip()

    def _par(root):
        wasm_cart.build(os.path.join(FIXTURES, "par.moy"), os.path.join(root, "par.moy"))
    ws = host_app.build_workstation(_store(tmp_path, _par))
    open_cart(ws, "Par Wasm")
    assert ws.player.cart_error is None, ws.player.cart_error
    _frames(ws, 5)
    assert ws.player.cart_error is None, ws.player.cart_error
    diff, frames, bad_sp, bad_caller = _pmem(ws)[:4]
    assert frames >= 3 and (diff, bad_sp, bad_caller) == (0, 0, 0), _pmem(ws)[:5]


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


def test_a_copy_the_store_has_no_room_for_leaves_no_half_cart(tmp_path, monkeypatch):
    """A module is the biggest file a cart carries, and a card that fills
    while it is copied must not leave a folder the shelf would list and the
    Player could never run: the copy goes, and the shell says CAN'T COPY."""
    root = _store(tmp_path, _hello)
    ws = host_app.build_workstation(root)
    before = sorted(os.listdir(root))

    def _full(src, dst, chunk=4096):
        with open(dst, "wb") as f:
            f.write(b"\0" * 100)
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(host_app.moy_carts, "_copy_bytes", _full)
    ws.picker.sel = next(i for i, it in enumerate(ws.picker.items)
                         if it.get("title") == "Hello Wasm")
    ws.carts.dup()
    assert sorted(os.listdir(root)) == before
    assert not any(c["title"] == "Hello Wasm copy" for c in ws.carts.all)
    assert ws._notice == ("CAN'T COPY", "the store is full", "warn")


def test_the_sync_walk_leaves_the_module_home(tmp_path):
    """The sync RPC declines binary files, so a compiled cart's module never
    crosses (runtime/moy_sync.py says so): its manifest and text do."""
    from runtime import moy_sync
    root = _store(tmp_path, _hello)
    path = os.path.join(root, "hello.moy")
    assert moy_sync._read_text(os.path.join(path, "main.wasm")) is None
    assert moy_sync._read_text(os.path.join(path, "manifest.json")) is not None


# -- a cart this console cannot fit ---------------------------------------------
#
# docs/wasm_tier_plan_2026-09.md: a cart above the floor is allowed, and a
# console that cannot fit it refuses at launch with a plain notice -- never an
# error panel, never a crash. The host refuses by its configured limit
# (wasm_host.MEMORY_LIMIT) with the boards' own footprint arithmetic.

HUGE = os.path.join(FIXTURES, "huge.moy")


def _huge(root):
    wasm_cart.build(HUGE, os.path.join(root, "huge.moy"))


def _px(cv, x, y):
    i = 2 * (y * cv.w + x)
    return cv._buf[i] | cv._buf[i + 1] << 8


def test_the_footprint_is_the_boards_own_arithmetic():
    """wasm_binding.footprint is native/moy_wasm/moy_wasm_footprint.h
    compiled into the host binding -- the header the boards' engine allocates
    by -- so a host and a board refuse by the same numbers. Pinned here
    against the rule the header states: the file's block (the linear
    memory's, TLSF's rounding and the mapping's header on top, never smaller
    than the file), the pool (256 KB and a quarter of the module), the module
    itself and the 16 KB run stack; the block is the largest of those as the
    heap must find it free."""
    _binding_or_skip()
    from runtime import wasm_binding

    def heap(n):
        return n + n // 32 + 1024

    for memory, module in ((3 * 65536, 4000), (26 * 65536, 1000774),
                           (0, 5000), (65536, 3000000), (640 * 65536, 103)):
        total, block = wasm_binding.footprint(memory, module)
        file_block = max(heap(memory) if memory else 0, module)
        pool = 256 * 1024 + module // 4
        assert total == file_block + pool + module + 16 * 1024, (memory, module)
        assert block == heap(max(file_block, pool, module)), (memory, module)


def test_the_engine_and_the_host_twin_share_one_statement_of_the_rule():
    """Routing, not arithmetic: the pool and the stack are defined once, in
    the header both tiers compile, and the engine reads its file block from
    it rather than restating the formula."""
    native = os.path.join(ROOT, "native")
    defs = []
    for dirpath, _dirs, names in os.walk(native):
        if "/wamr" in dirpath or ".staged" in dirpath:
            continue
        for name in names:
            if name.endswith((".c", ".h")):
                text = open(os.path.join(dirpath, name), encoding="utf-8").read()
                if "#define MOY_WASM_POOL_SHARE" in text:
                    defs.append(name)
    assert defs == ["moy_wasm_footprint.h"], defs
    engine = open(os.path.join(native, "moy_wasm", "modmoy_wasm.c"),
                  encoding="utf-8").read()
    assert "moy_wasm_file_block(" in engine and "hold / 32" not in engine
    host = open(os.path.join(ROOT, "runtime", "moyhost_wasm.c"),
                encoding="utf-8").read()
    assert '#include "moy_wasm_footprint.h"' in host


def test_a_cart_bigger_than_the_console_opens_the_notice_not_an_error(tmp_path):
    """The huge fixture declares 40 MB, more than any board has: refused
    before it loads, naming the cart, what it needs and what this console has
    free, on the Player's panel drawn as a notice -- no error title, no EDIT,
    the same way out -- and the console runs the next cart as before."""
    _binding_or_skip()
    from runtime import bar_layer, player
    from runtime.dev_channel import _remote_state
    ws = host_app.build_workstation(_store(tmp_path, _huge, _hello))
    open_cart(ws, "Huge Wasm")
    p = ws.player
    assert p.notice is not None and p.notice == p.cart_error
    # The host always interprets (there is no AOT tier on it), so this is the
    # interpreted rule's number: the module file is never reused for the
    # linear memory, so a 40 MB declaration holds two ~41 MB blocks at once.
    assert p.notice == ("Huge Wasm needs 82.6 MB of memory to run. This "
                        "console has 32.0 MB free."), p.notice
    assert p._lua is None and p._update is None and p._draw is None
    assert not ws.wm.top_is("menu"), "a notice threw into the Editor"
    _frames(ws, 3)
    cv = ws.canvas
    x, y = (cv.w - min(292, cv.w - 12)) // 2, min(40, (cv.h - min(132, cv.h - 16)) // 2)
    assert _px(cv, x + 1, y + 1) == cv._wire[ws.player.NAMES["orange"]]
    assert _px(cv, x + 1, y + 1) != cv._wire[ws.player.NAMES["red"]]
    assert player.NOTICE_TITLE == "Too big for this console."
    st = _remote_state(ws)
    assert st["notice"] == p.notice and st["cart_error"] is None
    assert bar_layer._edit_kind(ws.cart) is None
    ws._exit_to_caller()
    open_cart(ws, "Hello Wasm")
    assert ws.player.cart_error is None and ws.player.notice is None
    _frames(ws, 3)
    assert ws.player.cart_error is None, ws.player.cart_error


def test_the_hosts_configured_limit_is_what_it_refuses_by(tmp_path, monkeypatch):
    """The host twin behaves as a board does: a cart whose footprint is over
    MEMORY_LIMIT gets the notice, and the same cart under it runs."""
    _binding_or_skip()
    from runtime import wasm_host
    ws = host_app.build_workstation(_store(tmp_path, _hello))
    monkeypatch.setattr(wasm_host, "MEMORY_LIMIT", 300 * 1024)
    open_cart(ws, "Hello Wasm")
    assert ws.player.notice == ("Hello Wasm needs 0.5 MB of memory to run. "
                                "This console has 0.2 MB free."), ws.player.notice
    ws._exit_to_caller()
    monkeypatch.undo()
    open_cart(ws, "Hello Wasm")
    assert ws.player.cart_error is None and ws.player.notice is None


class _Runtime:
    """The host runtime with its reports kept and its start replaced."""

    def __init__(self, real, start=None, memory=None):
        self.real = real
        self.start = start
        self.mem = memory

    def footprint(self, cart):
        return self.real.footprint(cart)

    def memory(self):
        return self.mem if self.mem is not None else self.real.memory()

    def __call__(self, ns, src):
        if self.start is not None:
            raise self.start
        return self.real(ns, src)


def test_a_load_that_still_runs_out_of_memory_gets_the_same_notice(tmp_path):
    """The fit check passed, and the load failed for memory anyway -- the
    engines' "out of memory" text, or a MemoryError -- which is the same
    notice, saying the free total was there and not in usable pieces."""
    _binding_or_skip()
    ws = host_app.build_workstation(_store(tmp_path, _hello))
    real = ws.runtimes["wasm"]
    want = ("Hello Wasm needs 0.5 MB of memory to run. This console has "
            "32.0 MB free, but not in pieces it can use.")
    for exc in (RuntimeError("out of memory: AOT module instantiate failed: "
                             "allocate linear memory failed"),
                MemoryError("no PSRAM for the module file")):
        ws.runtimes["wasm"] = _Runtime(real, start=exc)
        open_cart(ws, "Hello Wasm")
        assert ws.player.notice == want, ws.player.notice
        ws._exit_to_caller()
    # any other start failure stays an error, with its own words
    ws.runtimes["wasm"] = _Runtime(real, start=RuntimeError("refused: bad signature"))
    open_cart(ws, "Hello Wasm")
    assert ws.player.notice is None
    assert ws.player.cart_error.endswith("refused: bad signature")


def test_a_console_whose_largest_block_is_too_small_says_so(tmp_path):
    _binding_or_skip()
    ws = host_app.build_workstation(_store(tmp_path, _hello))
    ws.runtimes["wasm"] = _Runtime(ws.runtimes["wasm"],
                                   memory=(32 * 1024 * 1024, 100 * 1024))
    open_cart(ws, "Hello Wasm")
    assert ws.player.notice == ("Hello Wasm needs 0.3 MB of memory in one "
                                "piece. The biggest piece this console has "
                                "free is 0.0 MB."), ws.player.notice


def test_the_notice_never_reads_as_a_fit():
    """What a cart needs rounds up and what the console has rounds down, so
    a refusal can never print the same two numbers."""
    from runtime.player import fit_notice
    mb = 1024 * 1024
    text = fit_notice("Doom", (int(2.95 * mb), mb), (int(2.94 * mb), 2 * mb))
    assert text == "Doom needs 3.0 MB of memory to run. This console has 2.9 MB free."
    assert fit_notice("Doom", None, None) == (
        "Doom needs more memory than this console has free.")


# -- the compiled module's name on a board ---------------------------------------


def test_the_tool_and_the_board_name_the_compiled_module_alike():
    from device import moycore_glue
    from tools import wasm_module
    fmt = wasm_module.format_version()
    for main in ("main.wasm", "doom.wasm", "game"):
        for chip in ("esp32s3", "esp32p4"):
            assert (moycore_glue.aot_path("/c", main, chip, fmt)
                    == "/c/" + wasm_cart.aot_name(main, chip))
            for other in ("0", "2", "99"):
                if other == fmt:
                    continue
                assert (moycore_glue.aot_path("/c", main, chip, other)
                        == "/c/" + wasm_cart.aot_name(main, chip, other))


def test_the_board_reads_the_head_up_to_the_memory_section(tmp_path):
    from device import moycore_glue
    out = wasm_cart.build(HELLO, str(tmp_path / "hello.moy"))
    blob = open(out["main"], "rb").read()
    head = moycore_glue.wasm_head(out["main"])
    assert blob.startswith(head) and 8 < len(head) < len(blob)
    sections = [sid for sid, _n, _b in __import__(
        "tools.wasm_module", fromlist=["sections"]).sections(blob)]
    assert 5 in sections
