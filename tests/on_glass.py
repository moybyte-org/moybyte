"""The three on-glass suites' shared body (#206 item 3).

`tests/test_{p4,tdeck,guition}_on_glass.py` each drive a REAL board over
serial, and 82 of the two S3 suites' lines were the same lines: the attach
fixture, the skip-if-no-port gate, and the state / `py` / diag / mem / cart
checks that ask nothing board-specific. The P4's third copy had already
drifted -- the same predicates, worse failure text.

None of it is a test. The suites keep their own `def test_*`, so the collected
ids stay per board and a failure names the board it happened on; these are the
bodies those tests call.

What is deliberately NOT here is every genuine difference, which stays in the
suite that owns it: the P4's windowed-desk tour, its OTA-verifier trio and its
own cart exit path (`ws.exit()`, so the T-Deck keeps the kid-facing `quit()`
flag pinned), the Guition's landscape canvas and sync-RPC tests, each board's
swipe coordinates, and the tail of the idle-blank check -- the P4 pins `power
0`, the S3s pin the restore.
"""

import contextlib
import hashlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))          # p4_autotest.P4Board


def gate(env_var, board):
    """`(port, skip-marker)` for a suite gated on a board being plugged in."""
    port = os.environ.get(env_var)
    return port, pytest.mark.skipif(
        not port, reason="%s not set (needs %s on serial)" % (env_var, board))


@contextlib.contextmanager
def session(port, board_dir):
    """One board, held open for a whole suite.

    RESET OR ATTACH IS THE BOARD'S OWN DECLARATION, not a choice made here.
    `attach_only` in its `[serial]` block says the USB serial sits ON the SoC,
    so a reset pulse re-enumerates the device under this open handle and every
    read afterwards returns nothing, forever -- indistinguishable from a dead
    board. Such a board is attached to a desktop that is ALREADY running and
    left where it was found; `P4Board.reset()` refuses one outright. The same
    block supplies the line state at open, which is the other half of that
    hardware fact: an open with both lines LOW chip-resets a SoC-USB board.
    """
    from p4_autotest import P4Board
    b = P4Board(port, board_dir=board_dir)
    try:
        if b.attach_only:
            # A first drain absorbs whatever diag lines are mid-flight before
            # the first command's reply is awaited.
            b.drain(0.8)
            if b.cmd("state", wait_for="STATE ", timeout=10.0) is None:
                raise RuntimeError(
                    "%s did not answer `state` on %s -- is the desktop "
                    "running? (this suite attaches, it does not reset)"
                    % (b.expect_board or Path(board_dir).name, port))
            # Identity, not just liveness: both S3s share usb id 303a:1001 and
            # answer `state` just as happily, so an answer alone proves only
            # that SOME console is listening. A `PORT=auto` resolves it; an
            # explicit port aimed at the wrong board raises here.
            b.verify_board()
        else:
            b.reset()                  # verifies identity once the desk is up
        yield b
    finally:
        b.close()


# -- the checks every fullscreen-tier board shares ---------------------------


def fullscreen_tier_state(board):
    st = board.state()
    assert isinstance(st.get("frames"), int) and st["frames"] > 0
    # The process back-stack IS this tier's window model, and `ws.screen` is a
    # read-only projection of its top -- assert the documented invariant.
    assert st.get("stack"), st
    assert st["stack"][-1] == st["screen"]
    # psave: [asleep, timeout_secs] -- the idle blank is live and awake.
    asleep, secs = st["psave"]
    assert asleep is False
    assert secs > 0


def wifi_status_is_readable(board):
    st = board.state()
    assert "wifi_err" not in st, st.get("wifi_err")
    assert st.get("wifi") is None or isinstance(st["wifi"], list)


def wifi_is_off_at_rest(board):
    """The radio is a LEASE (2026-09-07): a console that is not serving the
    web, updating, in the WIFI panel, running a network cart or in a match
    holds nothing and reports no link. `wifi_held` is the lease's holders;
    a firmware from before the lease has no such key, and says so here."""
    st = board.state()
    assert "wifi_held" in st, "no wifi_held in state: firmware predates the lease"
    assert st["wifi_held"] == [], "something holds the radio at rest: %r" % (
        st["wifi_held"],)
    assert st.get("wifi") is None or st["wifi"][0] is False, st.get("wifi")


def every_app_claims_one_cart(board):
    """Every registered system app claims exactly one cart. Naming the wrong
    ones is the point: the failure this catches is seed/title drift in ONE
    app, and a dump of the whole claims table buries it."""
    claims = board.state().get("app_claims") or {}
    assert claims, "no system apps registered?"
    wrong = {k: v for k, v in claims.items() if v != 1}
    assert not wrong, "app cart claims off (seed/title drift?): %r" % wrong


def py_probe_reaches_the_console(board):
    line = board.cmd("py ws._frames_drawn", wait_for="PY ")
    assert line is not None and line.startswith("PY "), line
    assert int(line.split("PY ", 1)[1]) > 0
    # The loop objects are in the py scope (comp/boot/pump).
    line = board.cmd("py boot.done", wait_for="PY ")
    assert line == "PY True", line


def diag_toggle_roundtrips(board):
    """The toggle answers both ways -- and is left where it was found.

    It used to end on `diag 0`, which DISARMS the deep meters for every test
    after it in the tour. Two suites' fixtures send `diag 1` because they
    "assert PERF lines flow", and those are exactly the meters gated on
    `perf_capture`: with diag off, wmr/wmw/wms are never written at all, so a
    later test asserting them reads absence and cannot tell "nothing ran" from
    "nothing works". Restoring is what keeps a shared body from deciding the
    state of the suites that call it."""
    was = board.state()["diag"]
    board.cmd("diag 1", wait_for="REMOTE diag on")
    assert board.state()["diag"] is True
    board.cmd("diag 0", wait_for="REMOTE diag off")
    assert board.state()["diag"] is False
    if was:
        board.cmd("diag 1", wait_for="REMOTE diag on")
        assert board.state()["diag"] is True


def home_shelf_fling(board, x0, x1, y):
    """A horizontal fling over the home shelf: the gesture machinery (press
    edge, held interpolation, real release) through the same pointer the glass
    feeds -- and the console is still on home afterwards, so the suite's
    leave-it-where-you-found-it contract holds.

    The coordinates come from the caller because they are the one thing here
    that is not shared: this tier's glass is a different size per board."""
    frames0 = board.state()["frames"]
    board.swipe(x0, y, x1, y, frames=20)
    st = board.state()
    assert st["stack"][-1] == "launcher", st["stack"]
    assert st["frames"] > frames0, "the gesture drew no frames"
    # Swipe back so the shelf rests near where it started.
    board.swipe(x1, y, x0, y, frames=20)
    board.drain(0.8)                       # let the fling settle


def draw_gates_are_installed(board, windowed=False):
    """(#155) rect/rectb/print/pix must be the NATIVE moy_gfx callables on the
    root system canvas -- and, on the windowed tier, on every window content
    buffer too. Measured on P4 glass 2026-07-26: the Python wrapper cost
    50.2us against 5.2us for the fill_rect kernel it ends in, so an un-gated
    canvas silently pays ~10x per chrome call.

    Every board installs the gates through the ONE `DeviceCanvas` body, which
    is exactly why every board asks: the failure they catch is a staged
    constant or a kernel signature drifting under a board nobody re-measured.
    """
    assert board.pyval("str(type(ws.sys_canvas.rect))") == "<class 'draw_gate'>"
    assert board.pyval("ws.sys_canvas._gate_ctx is not None") is True
    if windowed:
        ungated = board.pyval(
            "[k for k, w in ws.wm._wins.items() if w.buf._gate_ctx is None]")
        assert ungated == [], ungated


def draw_gates_take_the_traffic(board):
    """The gates must actually be drawing -- a fallback that quietly swallowed
    every call would look installed and measure fast.

    Asked on SETTINGS, not on the home screen, and that is a tier difference
    worth naming: a fullscreen board at rest re-presents its captured launcher
    frame as ONE blit (launcher_layer's retained stamp, #66), so a home repaint
    there legitimately moves no gated verb at all. The first draft asked on the
    home screen, which is a desk board's shape, and read (0, 0) on the Guition
    S3 -- a green light on the P4s for a check that could not fail on the S3s.
    Settings draws real chrome on every tier. Restored before the assert, so a
    failure does not leave the next test on the wrong screen."""
    board.open("settings")
    board.drain(1.0)
    board.pyexec("ws.sys_canvas.gate_counts_reset()\nws.mark_dirty()")
    board.drain(1.5)
    fills, texts, _fu, _tu = board.pyval("ws.sys_canvas.gate_counts()")
    board.cmd("py ws.exit()", wait_for="PY")
    board.drain(0.5)
    assert fills > 0 and texts > 0, (fills, texts)


def display_underruns_are_zero(board):
    """The scan-out kept up for the whole tour. A DSI/DPI panel streams its
    framebuffer out of PSRAM continuously, so an underrun is the one failure
    that says the memory system lost a race with the glass -- and the compositor
    counts them for free. `None` means the backend cannot report, which is data,
    not a pass."""
    n = board.pyval("comp.underruns()", strict=True)
    assert n == 0, n


def web_console_is_baked_into_the_image(board):
    """The wasm console this board hands a browser lives in its OWN image, and
    reads back correctly from flash -- the one part no host test can reach,
    because it is the linker's answer, not the build script's.

    EVERY board asks, because the failure is silent on every board: a build
    made without `firmware/web_runner/dist` produces an image that compiles,
    boots and runs, and is about 700KB short -- two of them shipped on
    2026-09-08 before anyone noticed. Self-consistent on purpose (the board's
    own `moy_webhost.ASSETS`, not this checkout's `dist/`): a board may
    legitimately run an older build, and the question is whether ITS console
    is whole.
    """
    stamp = board.pyval("__import__('moy_web').stamp()")
    assert stamp and stamp != "0 0 none", (
        "this image has NO baked web console (stamp %r) -- it was built with "
        "no firmware/web_runner/dist" % (stamp,))
    count, total = int(stamp.split()[0]), int(stamp.split()[1])
    declared = board.pyval("sorted(__import__('moy_webhost').ASSETS)")
    assert count == len(declared), (stamp, declared)
    assert total > 400000, "a bundle this small is not the wasm console"
    names = board.pyval("__import__('moy_web').assets()")
    assert set(names) == {n + ".gz" for n in declared}, (names, declared)
    got = board.pyval(
        "[(n, len(__import__('moy_web').asset(n)), "
        "bytes(__import__('moy_web').asset(n)[:3])) "
        "for n in __import__('moy_web').assets()]")
    assert sum(g[1] for g in got) == total, got
    for name, size, magic in got:
        assert magic == b"\x1f\x8b\x08", (name, magic)
        assert size > 0, name


def cart_runs_and_exits(board, spec, title=None, door="quit", clear=0):
    """THE test the 2026-08-17 _GATE_SEQ regression bought: a staged-constant
    deletion broke make_spr_gate -- and therefore EVERY cart start -- while
    both boards' suites stayed green, because nothing on glass ever RAN a
    cart. Launch through the real launcher path, assert the cart is ticking,
    then leave by this tier's door.

    `door` is a real difference between the tiers, not a preference. "quit"
    is the kid-facing `cart_quit` flag the Player honors (the same flag the
    cart API's quit() verb sets), and on a fullscreen tier it pops all the way
    to the launcher, which is what those boards pin. "shell" is `ws.exit()`,
    the desk's own close: on the windowed tier a run started from a
    picker/Settings arrangement does not pop through the flag, so those boards
    pin the launch and the close instead. `clear` ws.exit()s that many times
    first, to shut whatever windows the tour above left open -- a `run` from a
    picker arrangement opens the cart under it.
    """
    for _ in range(clear):
        board.cmd("py ws.exit()", wait_for="PY")
        board.drain(0.5)
    line = board.cmd("run %s" % spec, wait_for="REMOTE run")
    assert line is not None and "no cart match" not in line, line
    board.drain(2.5 if clear else 2.0)
    st = board.state()
    assert st.get("cart"), "the cart never started: %r" % st
    if title is not None:
        assert st["cart"] == title, st["cart"]
    assert not st.get("cart_error"), st["cart_error"]
    f0 = st["frames"]
    board.drain(1.0)
    assert board.state()["frames"] > f0, "the cart is not ticking"
    if door == "quit":
        board.cmd("py ws.input.cart_quit = True", wait_for="PY")
    else:
        board.cmd("py ws.exit()", wait_for="PY")
    board.drain(1.5)
    st = board.state()
    assert not st.get("cart"), "%s did not end the run: %r" % (door, st)
    if door == "quit":
        assert st["stack"][-1] == "launcher", st["stack"]


def idle_blank_and_wake(board):
    """The idle blank (shared IdleBlank + shared power verb): blank on silence,
    wake on the next serial command, `power off` outranking its own arrival.

    Retuned to a few seconds here, restored by the caller -- the shipped
    default is 5 minutes. Silence is the actual stimulus, so the wait must send
    nothing."""
    board.cmd("power 3", wait_for="REMOTE power")
    board.drain(0.3)
    board.drain(6.0)                       # say nothing; let the timer expire
    assert board.state()["psave"][0] is True, "the panel never blanked"
    # ...and that state query was serial traffic, which counts as activity, so
    # the panel is already awake again by the following frame.
    board.drain(0.5)
    assert board.state()["psave"][0] is False, "input did not wake the panel"

    # `power off` blanks immediately. It arrives ON the serial channel, which is
    # itself activity -- the explicit blank has to outrank that or it wakes in
    # the same iteration (it did, before _ps_force).
    board.cmd("power off", wait_for="REMOTE power")
    board.drain(1.0)
    assert board.state()["psave"][0] is True, "`power off` did not blank"


def idle_timeout_restored(board):
    """Put the shipped default back, and prove it took."""
    board.cmd("power 300", wait_for="REMOTE power")
    board.drain(0.5)
    assert board.state()["psave"][0] is False
    assert board.state()["psave"][1] == 300


def mem_reports_the_heap(board):
    line = board.cmd("mem", wait_for="REMOTE mem")
    assert line is not None and "live=" in line and "free=" in line, line


def perf_line_is_the_one_format(board):
    """The PERF line, on real glass, in the one shape every board emits
    (#206 item 2).

    It had three shapes under one name, and the T-Deck's went through the diag
    ring -- whose `Moybyte <uptime> ` stamp made both readers filter it out, so
    the board whose fps most needed measuring was invisible to the tool that
    measures it. Hence: parse with the module that WRITES the line, assert every
    declared field arrived, and assert absence is spelled `-`.

    An idle desk paints nothing, so fps= reads 0/<loop rate>. That is the
    idle-paints-zero invariant, and this line is its witness."""
    from runtime.perf_line import FIELDS, parse_perf
    n0 = len(board.lines)
    board.drain(5.0)
    lines = board.perf_lines(n0)
    assert lines, "no PERF lines in 5s"
    got = parse_perf(lines[-1])
    missing = [n for n, _s, _u in FIELDS if n not in got]
    assert not missing, "%s: fields missing from %r" % (missing, lines[-1])
    fps = got["fps"]
    # Drawn frames cannot exceed looped ones -- the pair is the whole point of
    # `fps=<drawn>/<looped>`, and an idle desk sits at 0 drawn.
    assert isinstance(fps, tuple) and fps[1] > 0, lines[-1]
    assert 0 <= fps[0] <= fps[1], lines[-1]
    for name, _spec, _unit in FIELDS:
        v = got[name]
        assert v is None or isinstance(v, (float, tuple, str)), (name, v)
    # WHICH columns are `-` is the board's own capability claim, so the suites
    # assert that themselves -- this body is shared by a board that fills them.
    return got


# -- the windowed tier's three PERF columns ----------------------------------

_WM_METERS = ("wmr", "wmw", "wms")


def wm_meters_answer_for_the_frame_they_measured(board, win="settings"):
    """`wmr`/`wmw`/`wms` say what THIS sample measured, and nothing when no
    frame measured them.

    They were READ rather than taken until `c95bf89`, so the layer stopped
    writing the moment a cart opened while the attribute kept its last desktop
    value forever: both P4s reported `wmw=46` under every cart, identical across
    carts whose whole frames differed by 8x, and that constant was taken for a
    fixed window-manager tax every cart paid. Absence is spelled `-` now.

    BOTH HALVES, because either one alone passes for the wrong reason. At REST
    all three must be ABSENT -- a number there is the stale constant back. Under
    a window DRAG all three must carry one -- absence everywhere would equally
    satisfy a column that is simply dead, which is the hole the old assertion
    sat in: it only ever sampled an idle desk, so it could not tell "nothing ran"
    from "nothing works", and it read the honest `-` as a regression.

    A window DRAG rather than a content scroll, because the scroll takes the
    #155 restore skip once the cache is in both ping-pong buffers and so stops
    writing `wmr` BY DESIGN. A moving window uncovers genuinely damaged backdrop
    every frame, which is the comment at the skip.

    Leaves the window where it found it -- these suites are one ordered tour.
    """
    from runtime.perf_line import parse_perf

    # These three are gated on `perf_capture`, so with diag off they are never
    # written and BOTH halves below would read `-` -- the rest half passing for
    # the wrong reason and the drag half failing for it. Armed here rather than
    # inherited from suite order, and asserted so a board that cannot arm says
    # so instead of quietly measuring nothing.
    board.cmd("diag 1", wait_for="REMOTE diag on")
    assert board.state()["diag"] is True, "the deep meters would not be written"

    # `open` TOGGLES, so a window the previous test left up would be closed.
    if not (board.state().get("order") or ()):
        board.open(win)
        board.drain(1.5)

    n0 = len(board.lines)
    board.drain(4.0)
    lines = board.perf_lines(n0)
    assert lines, "no PERF lines in 4s -- is diag on?"
    rest = parse_perf(lines[-1])
    for name in _WM_METERS:
        assert rest[name] is None, (
            "%s= carries %r on an IDLE desk, where the windowed WM drew "
            "nothing: the meter is being read rather than taken (c95bf89)"
            % (name, rest[name]))

    # The board's OWN drag verb, which exists for exactly this: it grabs the
    # TOP window's title strip and oscillates it "so the PERF sampler reports
    # DRAG-time fps". Hand-aiming a swipe at a window by NAME is what a tour
    # cannot do -- the P4 suite leaves one window up and the Guition P4 suite
    # leaves the picker stacked over it, so the swipe landed on the picker and
    # settings never moved. Oscillating also returns the window to where it
    # started, so nothing has to be put back.
    n0 = len(board.lines)
    started = board.cmd("drag 160", wait_for="REMOTE drag ", timeout=15.0)
    assert started and "no window open" not in started, \
        "the drag verb declined: %r" % (started,)
    board.drain(6.0)
    seen = {}
    for ln in board.perf_lines(n0):
        got = parse_perf(ln)
        for name in _WM_METERS:
            if got.get(name) is not None:
                seen.setdefault(name, got[name])
    board.wait_line("drag done", 45)
    board.drain(0.5)

    missing = [n for n in _WM_METERS if n not in seen]
    assert not missing, (
        "%s never carried a number across a whole window drag (saw %r) -- "
        "the column is dead, not merely quiet" % (missing, seen))
    return seen


# -- the WebAssembly engine (docs/wasm_tier_plan_2026-09.md, phase 1) --------
#
# native/moy_wasm on glass: the spike's 6502 core built by tools/wasm_module.py
# for this board's chip with the pinned compilers, pushed into the board's
# cart store, and run on the engine's own thread -- plus the plan's four
# guards. The numbers pinned below were measured on 2026-09-25 (#158 carries
# them); a guard that trips is a regression in internal SRAM, which on the S3
# boards is the resource the whole tier is gated on.

WASM_DIR = "wasm_hello"
# step(20000) on a fresh instance: the 6502 core's cycle count, identical on
# every runtime the spike measured.
WASM_STEP_20000 = 59973
# What building the tier in may cost the idle desk's internal SRAM, against a
# module-free image of the same tree on a fresh boot. Measured 2026-09-25 with
# the import table's registration storage allocated per session from PSRAM:
# free fell 1496-1536 bytes on all four boards, the engine's and moycore's
# static data; every per-cart structure, the table's storage included, is
# allocated when a cart opens, from PSRAM. The largest block fell 0 (both
# P4s), 2048 (Guition S3) and 4096 (T-Deck) -- it moves in the heap's own
# steps as the static data shifts the regions, so its bound carries one more.
WASM_IDLE_FREE_COST_MAX = 2048
WASM_IDLE_LARGEST_COST_MAX = 6144
# What one run may take from internal SRAM beyond the idle desk's, with the
# board's own run stack: the thread's control block and bookkeeping (~0.8-1 KB
# measured with a PSRAM stack; an internal stack adds its whole size).
WASM_RUN_SRAM_MAX = 2048


def _wasm_chip(board_dir):
    from tools import board_config
    return board_config.load(board_dir)["board"]["chip"]


_WASM_BUILT = {}


def wasm_modules(chip):
    """{name: local .aot} for `chip`, built once per session: the hello
    module, and four a board must refuse -- no key, another fork, other
    flags (the key saying so), another chip."""
    if chip not in _WASM_BUILT:
        import tempfile
        from tools import wasm_module as wm
        out = tempfile.mkdtemp(prefix="moy_wasm_%s_" % chip)
        wasm = wm.hello_wasm()
        other = "esp32p4" if chip == "esp32s3" else "esp32s3"
        mods = {}
        for name, c, kw in (("hello", chip, {}),
                            ("nokey", chip, {"key": False}),
                            ("badfork", chip, {"fork": "0" * 40}),
                            ("badflags", chip, {"override": {"opt": "2"}}),
                            ("otherchip", other, {})):
            mods[name] = os.path.join(out, name + ".aot")
            wm.build(wasm, c, mods[name], **kw)
        _WASM_BUILT[chip] = mods, hashlib.sha256(wasm).hexdigest()
    return _WASM_BUILT[chip]


def wasm_push(board, board_dir):
    """Push this chip's modules to <ws.carts_root>/wasm_hello/ over the one
    upload transport (`recv`). A folder without the .moy suffix, so the
    launcher never lists it as a cart. Returns {name: path on the board}."""
    import push_cart as pc
    from tools import board_config
    mods, _sha = wasm_modules(_wasm_chip(board_dir))
    ser = board_config.load(board_dir)["serial"]
    root = str(board.pyval("str(ws.carts_root)", timeout=20, strict=True))
    dest = root.rstrip("/") + "/" + WASM_DIR
    was = pc.quiet_diag(board)
    try:
        win = pc.raw_window(board, int(ser.get("window") or 4096))
        assert board.pyexec(pc.HELPERS), "could not install the upload helpers"
        board.pyval("ws._g['_mkdir'](%r)" % dest)
        for name, local in sorted(mods.items()):
            pc.push_file_raw(board, local, "%s/%s.aot" % (dest, name), win)
    finally:
        pc.restore_diag(board, was)
    return {name: "%s/%s.aot" % (dest, name) for name in mods}


def wasm_run(board, path, export, args=(), timeout=180.0, during=None, **kw):
    """One run through moy_wasm.start() -> result(). `during(board)` is called
    while the run's thread is live, before waiting for it."""
    extra = "".join(", %s=%r" % kv for kv in sorted(kw.items()))
    line = board.cmd("py __import__('moy_wasm').start(%r, %r, %r%s)"
                     % (path, export, tuple(args), extra),
                     wait_for="PY", timeout=30)
    assert line and line.strip() == "PY None", line
    if during is not None:
        during(board)
    import time
    end = time.time() + timeout
    while not board.pyval("__import__('moy_wasm').done()", strict=True):
        assert time.time() < end, "the run did not end in %gs" % timeout
        board.drain(0.2)
    return board.pyval("__import__('moy_wasm').result()", timeout=60,
                       strict=True)


def _internal_heap(board):
    regs = board.pyval("__import__('esp32').idf_heap_info(0x804)", strict=True)
    return sum(r[1] for r in regs), max(r[2] for r in regs)


def _ble(board):
    return "(getattr(ws, 'ble_keyboard', None) or ws.keyboard)"


def wasm_idle_cost_is_bounded(board, baseline, ble_at_boot):
    """Guard 1: the idle desk's internal SRAM with the engine built in, against
    `baseline` -- (free, largest block) measured on a module-free image of the
    same tree on a fresh boot, within WASM_IDLE_FREE_COST_MAX and
    WASM_IDLE_LARGEST_COST_MAX.

    A fresh boot is the only fair comparison, because two radios keep what
    they took: the WiFi driver holds its allocation after the lease powers it
    down, and BLE once started stays up. A desk that has had either this boot
    (beyond what the board starts by itself) is refused with the reason,
    never compared."""
    assert board.pyval("__import__('moy_wasm').FORK", strict=True)
    if board.pyval("bool(getattr(ws.wifi, 'driver_up', False))", strict=True):
        pytest.skip("the WiFi driver has run this boot and keeps its internal "
                    "RAM; reboot the board and run the suite first")
    if not ble_at_boot and board.pyval(
            "bool(getattr(%s, 'available', False))" % _ble(board), strict=True):
        pytest.skip("BLE was started this boot; reboot the board first")
    free, largest = _internal_heap(board)
    print("\nWASM idle internal: free=%d largest=%d (module-free image: %d / %d)"
          % (free, largest, baseline[0], baseline[1]))
    assert free >= baseline[0] - WASM_IDLE_FREE_COST_MAX, (free, baseline)
    assert largest >= baseline[1] - WASM_IDLE_LARGEST_COST_MAX, (largest, baseline)
    return free, largest


def wasm_hello_runs_and_foreign_modules_are_refused(board, board_dir, paths):
    """The engine end to end: this build's key is the tool's, the hello module
    loads from the cart store and returns the core's cycle count on the run
    thread, and every module the key does not vouch for is refused before
    anything in it runs."""
    from tools import wasm_module as wm
    chip = _wasm_chip(board_dir)
    assert board.pyval("__import__('moy_wasm').KEY", strict=True) == wm.key_tail(chip)
    r = wasm_run(board, paths["hello"], "step", (20000,))
    assert r["ok"], r["error"]
    assert r["value"] == WASM_STEP_20000, r
    assert r["loops"] == 1 and r["mismatches"] == 0, r
    assert r["wasm"] == wasm_modules(chip)[1], r["wasm"]
    assert 0 < r["stack_used"] < r["stack"], r
    print("\nWASM hello step(20000): load %d us, instantiate %d us, call %d us, "
          "stack %d of %d (%s), pool peak %d"
          % (r["load_us"], r["inst_us"], r["call_us"], r["stack_used"], r["stack"],
             "PSRAM" if r["stack_psram"] else "internal", r["pool_peak"]))
    for name, why in (("nokey", "refused: no moybyte.key"),
                      ("badfork", "refused: key mismatch 'fork 0000"),
                      ("badflags", "refused: key mismatch 'opt 2'"),
                      ("otherchip", "load: ")):
        r = wasm_run(board, paths[name], "step", (20000,))
        assert not r["ok"] and r["loops"] == 0, (name, r)
        assert r["error"].startswith(why), (name, r["error"])


# The internal-SRAM stack variant is attempted only when the heap's largest
# free block holds this many stacks: the variant takes a whole stack from the
# region WiFi, BLE and the display's DMA share, and a desk that has had its
# radios up this boot keeps a few KB there (about 7 KB on the T-Deck, measured
# 2026-09-25) -- not enough to try it without starving the board.
WASM_INTERNAL_STACK_ROOM = 2


def wasm_run_stack_placements(board, paths):
    """The run stack in PSRAM (the boards' setting, `moy_wasm.STACK`) and in
    internal SRAM: the same answer, what each costs the internal heap for the
    run, and how fast. The internal variant runs only when the internal heap
    has room for it (WASM_INTERNAL_STACK_ROOM stacks in one block); otherwise
    it is recorded as skipped with the free figures that decided it, the same
    on a fresh board and a warm one. Returns {psram: result or None}."""
    out = {}
    stack = board.pyval("__import__('moy_wasm').STACK[0]", strict=True)
    for psram in (True, False):
        if not psram:
            free, largest = board.pyval("__import__('moy_wasm').mem()[:2]",
                                        strict=True)
            if largest < WASM_INTERNAL_STACK_ROOM * stack:
                print("\nWASM stack internal: skipped -- internal free %d, "
                      "largest block %d, wants %d" % (
                          free, largest, WASM_INTERNAL_STACK_ROOM * stack))
                out[psram] = None
                continue
        r = wasm_run(board, paths["hello"], "step", (400000,), psram_stack=psram)
        assert r["ok"], r["error"]
        out[psram] = r
        print("\nWASM stack %s: step(400000) %d us, stack used %d, run cost "
              "internal %d" % ("PSRAM" if psram else "internal", r["call_us"],
                               r["stack_used"], r["sram_before"] - r["sram_min"]))
    if out[False] is not None:
        assert out[True]["value"] == out[False]["value"]
    return out


def wasm_runaway_runs_to_its_end(board, paths, n=400000000):
    """What terminate() can do (README.md, "Stopping a run"): it raises the
    exception in the instance, and AOT code reads it only when an import
    returns. The hello module's `spin` calls none, so it runs to its natural
    end and THEN reports the termination. This pins that answer; a fork that
    adds loop-edge checks changes it, and this is the test to update then."""
    ref = wasm_run(board, paths["hello"], "spin", (n,))
    assert ref["ok"], ref["error"]

    def _stop(b):
        b.drain(ref["call_us"] / 5e6)
        assert b.pyval("__import__('moy_wasm').terminate()", strict=True) is True

    r = wasm_run(board, paths["hello"], "spin", (n,), during=_stop)
    assert not r["ok"] and "terminated by user" in r["error"], r
    assert r["terminated"] is True
    assert r["run_us"] >= 0.9 * ref["run_us"], (r["run_us"], ref["run_us"])
    return ref["call_us"], r["run_us"]


def wasm_lua_after_wasm_keeps_its_sram(board, paths):
    """Guard 3: a Lua cart run after a wasm run keeps the internal SRAM it had
    before one -- moycore's report says the same about PSRAM fallback as a
    control run just before the wasm run. A COMPARISON, never an absolute: a
    console whose radios have been up this boot already sits below the Lua
    floor (#158), so the control falls back by itself there and the question
    is only whether the wasm run changed the answer. Same on a fresh board and
    a warm one."""
    def lua_run():
        board.cmd("run sakura lua", wait_for="REMOTE run")
        board.drain(2.5)
        assert board.state().get("cart"), "the Lua cart did not start"
        board.leave_cart()
        board.drain(1.0)
        return board.state()["sram"]

    control = lua_run()
    r = wasm_run(board, paths["hello"], "step", (20000,), loops=5)
    assert r["ok"], r["error"]
    after = lua_run()
    print("\nWASM Lua sram before the wasm run %r, after %r" % (control, after))
    assert after["psram_fallback"] == control["psram_fallback"], (control, after)
    return control, after


def wasm_load_unload_under_flush_and_wifi(board, paths, loops=200):
    """Guard 4: load / key check / instantiate / call / unload, `loops` times
    over, while a cart animates every frame and the WiFi radio is up -- the
    cache sync at each load runs with the other core busy. Every pass must
    return the same cycle count (a stale instruction cache would not)."""
    board.cmd("py ws.wifi_hold('wasm')", wait_for="PY", timeout=20)
    try:
        board.cmd("run star", wait_for="REMOTE run")
        board.drain(2.5)
        assert board.state().get("cart"), "the animating cart did not start"
        seen = {}

        def _frames(b):
            seen["f0"] = b.state()["frames"]

        r = wasm_run(board, paths["hello"], "step", (20000,), loops=loops,
                     during=_frames)
        drew = board.state()["frames"] - seen["f0"]
        board.leave_cart()
        print("\nWASM %d load/unload passes under a live cart + WiFi: %d us, "
              "load %d..%d us, %d frames drawn meanwhile"
              % (r["loops"], r["run_us"], r["load_us"], r["load_us_max"], drew))
        assert r["ok"], r["error"]
        assert r["loops"] == loops and r["mismatches"] == 0, r
        assert r["value"] == WASM_STEP_20000, r
        assert drew > 0, "no frame drew while the loop ran"
        return r, drew
    finally:
        board.cmd("py ws.wifi_release('wasm')", wait_for="PY", timeout=20)


def wasm_low_water_with_radios_up(board, paths):
    """Guard 2: with WiFi and BLE up, a run takes at most WASM_RUN_SRAM_MAX of
    internal SRAM beyond what the idle desk already had (the heap's
    local-minimum monitor, started before the run's thread exists) -- a
    COMPARISON against the desk it ran on, never an absolute floor.

    Returned beside it: the low-water mark against moycore's internal-SRAM
    floor. On the S3 boards the console with both radios up already sits
    below that floor before any wasm runs (measured on a module-free image,
    #158), and WiFi is not meant to be on while a cart plays -- so the floor
    comparison is a fact about the console, reported, and the run's own cost is
    what this pins."""
    board.cmd("py ws.wifi_hold('wasm')", wait_for="PY", timeout=20)
    try:
        assert board.pyval("%s.start()" % _ble(board), timeout=30) is True
        board.drain(3.0)
        r = wasm_run(board, paths["hello"], "step", (20000,), loops=20)
        floor = board.pyval("__import__('moycore').sram_report()[2]", strict=True)
    finally:
        board.cmd("py ws.wifi_release('wasm')", wait_for="PY", timeout=20)
    assert r["ok"], r["error"]
    cost = r["sram_before"] - r["sram_min"]
    print("\nWASM with WiFi+BLE up: internal before %d, low-water %d (run cost "
          "%d), moycore floor %d -> %s the floor"
          % (r["sram_before"], r["sram_min"], cost, floor,
             "at or above" if r["sram_min"] >= floor else "BELOW"))
    assert cost <= WASM_RUN_SRAM_MAX, (cost, r)
    return r["sram_before"], r["sram_min"], floor


# -- the Player path (docs/wasm_tier_plan_2026-09.md, phase 3) -----------------
#
# A compiled cart run from the launcher: tests/fixtures/wasm/ built for this
# board's chip by tools/wasm_cart.py (WAT assembled, the module compiled with
# the pinned compiler, the key this build wants), pushed into the store as a
# .moy folder over `recv`, rescanned, and run with `run` like any cart. The fps
# floors are the suites' own, per board, measured with WiFi off -- the state a
# cart plays in.

WASM_CARTS = {"hello": "Hello Wasm", "blit": "Blit Wasm"}


def _push_folder(board, board_dir, local, dest):
    """Every file under `local` to `dest` on the board, over `recv`."""
    import push_cart as pc
    from tools import board_config
    ser = board_config.load(board_dir)["serial"]
    names = pc.cart_files(local)
    was = pc.quiet_diag(board)
    try:
        win = pc.raw_window(board, int(ser.get("window") or 4096))
        assert board.pyexec(pc.HELPERS), "could not install the upload helpers"
        board.pyval("ws._g['_mkdir'](%r)" % dest)
        for sub in pc.sub_dirs(names):
            board.pyval("ws._g['_mkdir'](%r)" % (dest + "/" + sub))
        for name in names:
            pc.push_file_raw(board, os.path.join(local, name), dest + "/" + name,
                             win)
    finally:
        pc.restore_diag(board, was)


def wasm_carts_push(board, board_dir):
    """Build the compiled fixture carts for this board's chip, push each into
    the store as `<name>.moy`, and rescan so the launcher lists them. Returns
    {name: title}."""
    import tempfile
    from tools import wasm_cart
    chip = _wasm_chip(board_dir)
    root = str(board.pyval("str(ws.carts_root)", timeout=20, strict=True))
    tmp = tempfile.mkdtemp(prefix="moy_wasm_carts_")
    for name in WASM_CARTS:
        out = os.path.join(tmp, name + ".moy")
        wasm_cart.build(str(ROOT / "tests" / "fixtures" / "wasm" / (name + ".moy")),
                        out, chips=(chip,))
        _push_folder(board, board_dir, out,
                     root.rstrip("/") + "/wasm_" + name + ".moy")
    board.pyval("len(ws.rescan_carts() or ())", timeout=60)
    titles = board.pyval("[c['title'] for c in ws.carts.all]", timeout=20,
                         strict=True)
    for title in WASM_CARTS.values():
        assert title in titles, "%s is not on the shelf after the push" % title
    return dict(WASM_CARTS)


def wasm_cart_fps(board, title, seconds=10.0, check=None):
    """Run `title` from the launcher, check it ticks with no error on the
    wasm runtime, and read its drawn fps off the PERF line over `seconds`
    (the first sample is the start and is dropped). `check(board)` runs once
    the cart is up. Leaves the desk as it was found. Returns (median drawn
    fps, [every sample's (drawn, looped)])."""
    from runtime.perf_line import parse_perf
    assert not board.state().get("wifi_held"), "WiFi is held: not a cart's state"
    line = board.cmd("run %s" % title.lower(), wait_for="REMOTE run")
    assert line is not None and "no cart match" not in line, line
    try:
        board.drain(2.5)
        st = board.state()
        assert st.get("cart") == title, st.get("cart")
        assert not st.get("cart_error"), st["cart_error"]
        if check is not None:
            check(board)
        n0 = len(board.lines)
        board.drain(seconds)
        st = board.state()
        assert not st.get("cart_error"), st["cart_error"]
        slug = title.replace(" ", "_")
        got = [parse_perf(ln) for ln in board.perf_lines(n0)]
        samples = [g["fps"] for g in got if g.get("cart") == slug]
    finally:
        board.leave_cart()
        board.drain(1.0)
    assert len(samples) >= 3, "too few PERF samples under %s: %r" % (title, got)
    drawn = sorted(s[0] for s in samples[1:])
    median = drawn[len(drawn) // 2]
    print("\nWASM %s: drawn fps %s (median %s)" % (
        title, [s[0] for s in samples], median))
    return median, samples


def hello_read_its_greeting(board):
    """The hello cart's `read` crossed to the VM and back: its greeting's
    black band spans the text (8 pixels a character) only when the read
    returned the file's bytes; with nothing read it is 8 pixels wide and row 9
    at x=100 is the blit's gradient."""
    px = board.pyval("(lambda b, i: b[i] | b[i + 1] << 8)"
                     "(ws.canvas._buf, 2 * (9 * ws.canvas.w + 100))", strict=True)
    black = board.pyval("ws.canvas._wire[0]", strict=True)
    assert px == black, "the greeting was not read: pixel %#06x" % px


def wasm_cart_holds_its_floor(board, title, floor, check=None):
    """The compiled cart presents at or above this board's pinned floor."""
    fps, _samples = wasm_cart_fps(board, title, check=check)
    assert fps >= floor, "%s drew %s fps, under the floor %s" % (title, fps, floor)
    return fps


def wasm_missing_module_is_refused(board, board_dir):
    """A compiled cart whose module was compiled for another chip is refused
    before anything runs: the Player's panel names it, and the desk comes back."""
    chip = _wasm_chip(board_dir)
    other = "esp32p4" if chip == "esp32s3" else "esp32s3"
    import tempfile
    from tools import wasm_cart
    tmp = tempfile.mkdtemp(prefix="moy_wasm_other_")
    out = os.path.join(tmp, "other.moy")
    wasm_cart.build(str(ROOT / "tests" / "fixtures" / "wasm" / "hello.moy"), out,
                    chips=(other,))
    with open(os.path.join(out, "manifest.json")) as f:
        man = f.read().replace('"Hello Wasm"', '"Other Chip Wasm"')
    with open(os.path.join(out, "manifest.json"), "w") as f:
        f.write(man)
    root = str(board.pyval("str(ws.carts_root)", timeout=20, strict=True))
    _push_folder(board, board_dir, out, root.rstrip("/") + "/wasm_other.moy")
    board.pyval("len(ws.rescan_carts() or ())", timeout=60)
    board.cmd("run other chip wasm", wait_for="REMOTE run")
    try:
        board.drain(2.0)
        err = board.state().get("cart_error") or ""
    finally:
        board.leave_cart()
        board.drain(1.0)
    assert "no module compiled for this board" in err, err
    return err
