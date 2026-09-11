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
