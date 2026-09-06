"""On-glass Guition JC8012P4A1C console tests: drive the real board.

Gated: they run only when MOYBYTE_GUITION_P4_PORT is set (e.g.
`MOYBYTE_GUITION_P4_PORT=/dev/ttyACM4 .venv/bin/python -m pytest
tests/test_guition_p4_on_glass.py -v`) -- the port checklist's stage-6 exit
criterion (docs/board_ports_2026-08.md) for the second ESP32-P4 board.

DELIBERATELY NO RESET, for the two S3 boards' reason: this board's `[serial]`
block declares `attach_only` because its USB-Serial/JTAG is ON the SoC (no
CH343, unlike the Waveshare P4), so a reset tears the USB device down under
the open handle and the reader looks exactly like a dead board. The suite
ATTACHES to the running desktop, asserts, and leaves the console on the desk.

Tests share the session in file order. What is shared with every board lives
in tests/on_glass.py; what is here is this board's -- the portrait system
canvas, the windowed tier one size up from the Waveshare's, the GSL3680
pointer feed, and the PPA composite the two P4 boards share.
"""

import pytest

import on_glass
from on_glass import ROOT

PORT, pytestmark = on_glass.gate("MOYBYTE_GUITION_P4_PORT", "the Guition P4")


@pytest.fixture(scope="module")
def board():
    with on_glass.session(
            PORT,
            board_dir=ROOT / "firmware" / "guition_jc8012p4a1c") as b:
        b.cmd("diag 1")            # this suite asserts PERF lines flow
        yield b
        b.cmd("diag 0")


def test_boots_to_the_desk(board):
    st = board.state()
    assert st.get("desk") is True
    assert not st.get("order")


def test_the_system_canvas_is_landscape_on_portrait_glass(board):
    """The board's one structural novelty: a LANDSCAPE desk (1280x800) on a
    panel that scans PORTRAIT (800x1280) -- the rotated compositor paints one
    landscape buffer and rotates it onto the glass -- over the same 320x240
    game canvas."""
    line = board.cmd("py (ws.sys_canvas.w, ws.sys_canvas.h, ws.canvas.w, ws.canvas.h)",
                     wait_for="PY ")
    assert line == "PY (1280, 800, 320, 240)", line
    line = board.cmd("py (comp.rotated, comp.angle, comp._pw, comp._ph, ws.sys_canvas.RETAINED_FRAMES)",
                     wait_for="PY ")
    assert line == "PY (True, 270, 800, 1280, 1)", line     # 270 is up (owner-verified)


def test_the_pointer_is_the_gsl3680(board):
    """The touch driver came up: firmware uploaded and running (the chip's
    0xB0 signature -- `available`), the poll answering with no finger on the
    glass, at this glass's size, with the calibrated mapping (three corner
    holds, 2026-09-06)."""
    line = board.cmd("py (touch.available, touch.fingers, touch.w, touch.h, "
                     "touch.swap_xy, touch.flip_x, touch.flip_y, touch.raw_w, touch.raw_h)",
                     wait_for="PY ")
    # The 2026-09-06 calibration: landscape as mounted, no swap, no flips,
    # the firmware's 1664x896 scaled onto the glass.
    assert line == "PY (True, 0, 1280, 800, False, False, False, 1664, 896)", line


def test_the_ppa_composite_is_live(board):
    """The shared P4 silicon tier (device/p4_canvas.py over native/p4/moy_ppa)
    registered on this board too -- the game composite runs on the DMA
    engine, not the CPU kernel."""
    line = board.cmd("py ws.sys_canvas._ppa is not None", wait_for="PY ")
    assert line == "PY True", line


def test_wifi_status_is_readable(board):
    on_glass.wifi_status_is_readable(board)


def test_every_system_app_claims_exactly_one_cart(board):
    on_glass.every_app_claims_one_cart(board)


def test_py_probe_reaches_the_live_console(board):
    on_glass.py_probe_reaches_the_console(board)


def test_diag_toggle_roundtrips(board):
    on_glass.diag_toggle_roundtrips(board)
    board.cmd("diag 1", wait_for="REMOTE diag on")


def test_open_settings_window(board):
    board.open("settings")
    board.drain(0.5)
    st = board.state()
    assert "settings" in st.get("order", ())


def test_settings_rows_fit_or_scroll(board):
    """On the Waveshare's 600px desk the Settings list overflows its window
    and a swipe scrolls it; on this 1280px-tall desk the whole list FITS (the
    window is 768px tall, the rows 468px on the first flash), so the honest
    assertion is the one the geometry picks: no scroll when nothing overflows,
    a scroll when it does."""
    st = board.state()
    view_h = st["settings"]["view"][3]
    content = st["settings"]["content"]
    board.swipe_settings(st)
    st = board.state()
    if content <= view_h:
        assert (st["settings"]["set_top"] or 0) == 0, st["settings"]
    else:
        assert (st["settings"]["set_top"] or 0) > 0, st["settings"]


def test_picker_opens(board):
    board.open("picker")
    board.drain(6.0)              # first-open cover pop-in settles
    st = board.state()
    assert "make" in st.get("order", ())


def test_window_buffers_are_single_retained_surfaces(board):
    ret = board.state().get("win_retained") or {}
    assert ret, "no window buffers to check"
    assert all(v == 1 for v in ret.values()), ret


def test_perf_line_is_the_one_format(board):
    on_glass.perf_line_is_the_one_format(board)


def _cart_runs_and_exits(board, spec, title=None):
    """The Waveshare suite's windowed-tier idiom, not the shared fullscreen
    body: ws.exit() first to clear whatever the tour left open (Settings + the
    picker -- a `run` from that state opens the cart under the picker's
    project arrangement, where the cart-quit flag does not pop), then run,
    then ws.exit() out."""
    for _ in range(3):
        board.cmd("py ws.exit()", wait_for="PY")
        board.drain(0.5)
    line = board.cmd("run %s" % spec, wait_for="REMOTE run")
    assert line is not None and "no cart match" not in line, line
    board.drain(2.5)
    st = board.state()
    assert st.get("cart"), "the cart never started: %r" % st
    if title is not None:
        assert st["cart"] == title, st["cart"]
    assert not st.get("cart_error"), st["cart_error"]
    f0 = st["frames"]
    board.drain(1.0)
    assert board.state()["frames"] > f0, "the cart is not ticking"
    board.cmd("py ws.exit()", wait_for="PY")
    board.drain(1.5)
    st = board.state()
    assert not st.get("cart"), "exit did not end the run: %r" % st


def test_a_cart_runs_and_exits(board):
    _cart_runs_and_exits(board, "star")


def test_a_lua_cart_runs_and_exits(board):
    """moycore on the second P4: the Lua tier reaches every board by default
    (the shared native staging), so pin it with a real run."""
    _cart_runs_and_exits(board, "sakura lua", title="Sakura Lua")


def test_a_quiet_game_frame_rotates_one_rect(board):
    """The whole point of the rotated compositor: a running game pays ONE
    scale+rotate of the game canvas per frame, not a whole-frame rotate. Run
    a cart (fullscreen: the ws.exit() calls first close the tour's windows),
    let it tick, and read the meters -- rect frames must have grown far more
    than full frames. Runs after the window tests on purpose: its exits leave
    the desk, which open_desk() at the end restores."""
    for _ in range(3):
        board.cmd("py ws.exit()", wait_for="PY")
        board.drain(0.5)
    line = board.cmd("run star", wait_for="REMOTE run")
    assert line is not None and "no cart match" not in line, line
    board.drain(2.5)
    st = board.state()
    assert st.get("cart"), "the cart never started: %r" % st
    before = board.pyval("comp.overlap_stats()", strict=True)
    board.drain(2.0)
    after = board.pyval("comp.overlap_stats()", strict=True)
    rect = after[0] - before[0]
    full = after[2] - before[2]
    assert rect >= 20, (before, after)
    assert full <= 3, (before, after)
    board.cmd("py ws.exit()", wait_for="PY")
    board.drain(1.5)
    assert not board.state().get("cart")
    board.cmd("py ws.open_desk()", wait_for="PY")     # leave the desk for the next test
    board.drain(0.5)


def test_idle_screen_blank_and_wake(board):
    on_glass.idle_blank_and_wake(board)
    on_glass.idle_timeout_restored(board)


def test_mem_reports_the_heap(board):
    on_glass.mem_reports_the_heap(board)


def test_no_dsi_underruns(board):
    """The scan-out kept up for the whole tour: the 800x1280@60Hz DPI stream
    is ~123MB/s of PSRAM reads, more than the Waveshare's, and PSRAM at 200MHz
    is what makes it hold (sdkconfig.board)."""
    line = board.cmd("py comp.underruns()", wait_for="PY ")
    assert line == "PY 0", line
