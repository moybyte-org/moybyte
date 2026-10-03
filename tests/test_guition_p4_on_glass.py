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


# The engine's idle cost (docs/wasm_tier_plan_2026-09.md, guard 1). FIRST in
# the file on purpose: the comparison is against a fresh boot, and the wasm
# block at the end brings the radios up. Measured 2026-09-25 on a module-free
# image of the same tree, at the launcher right after boot: (free, largest)
# internal SRAM.
WASM_IDLE_BASELINE = (188991, 94208)
WASM_BOARD_DIR = ROOT / "firmware" / "guition_jc8012p4a1c"


def test_the_wasm_engine_costs_the_idle_desk_at_most_a_constant(board):
    on_glass.wasm_idle_cost_is_bounded(board, WASM_IDLE_BASELINE,
                                       ble_at_boot=True)

@pytest.fixture(scope="module")
def wasm(board):
    return on_glass.wasm_push(board, WASM_BOARD_DIR)


# The rest of the engine's checks that bring no radio up, while the desk is
# still fresh: the Lua guard in particular needs an internal heap the radios
# have not spent, or its control run falls back to PSRAM by itself.


def test_the_hello_module_runs_and_foreign_modules_are_refused(board, wasm):
    on_glass.wasm_hello_runs_and_foreign_modules_are_refused(
        board, WASM_BOARD_DIR, wasm)


def test_unknown_sources_lets_only_a_missing_signature_through(board, wasm):
    on_glass.wasm_unknown_sources_lets_only_a_missing_signature_through(
        board, wasm)


def test_a_misaligned_load_or_store_of_any_width_is_exact(board, wasm):
    on_glass.wasm_misaligned_access_is_exact(board, wasm)


def test_a_saturating_float_to_int_conversion_is_exact(board, wasm):
    on_glass.wasm_saturating_conversions_are_exact(board, wasm)


def test_the_run_stack_works_in_psram_and_internal_sram(board, wasm):
    on_glass.wasm_run_stack_placements(board, wasm)


def test_terminate_reaches_a_runaway_only_at_its_end(board, wasm):
    on_glass.wasm_runaway_runs_to_its_end(board, wasm)


def test_a_lua_cart_after_a_wasm_run_keeps_its_sram(board, wasm):
    on_glass.wasm_lua_after_wasm_keeps_its_sram(board, wasm)


# -- the Player path (docs/wasm_tier_plan_2026-09.md, phase 3) ---------------
# Compiled carts from the launcher, WiFi off -- the state a cart plays in, so
# before the radio guards. The floors are this board's median drawn fps
# measured on 2026-09-25, less a margin; the measurements are the owner's to
# post, and native/moy_wasm/README.md states the per-board ceiling.
WASM_HELLO_FPS_FLOOR = 27
WASM_BLIT_FPS_FLOOR = 38


@pytest.fixture(scope="module")
def wasm_carts(board):
    return on_glass.wasm_carts_push(board, WASM_BOARD_DIR)


def test_the_hello_wasm_cart_holds_its_floor(board, wasm_carts):
    on_glass.wasm_cart_holds_its_floor(board, wasm_carts["hello"],
                                       WASM_HELLO_FPS_FLOOR,
                                       check=on_glass.hello_read_its_greeting)


def test_a_full_frame_blit_cart_holds_its_floor(board, wasm_carts):
    on_glass.wasm_cart_holds_its_floor(board, wasm_carts["blit"],
                                       WASM_BLIT_FPS_FLOOR)


# par (moy-spec SPEC.md §16.10): a compiled cart's items on this board's
# second core leave exactly what running them in order leaves, each on its
# own stack, and the board's one lane runs some of them.
def test_par_items_across_the_cores_match_them_in_order(board, wasm_carts):
    on_glass.wasm_par_matches_items_in_order(board, lanes=1)


# A palette frame stays the blit's on a P4 (ESP-IDF disables the PPA's
# palette mode); a direct-colour one goes to the glass from the cart's memory
# (the Jet section below), and the board says it lacks the frame fold by
# absence.
def test_a_compiled_carts_palette_frames_are_written_into_the_canvas(board, wasm_carts):
    on_glass.p4_palette_frames_keep_the_blit(board, wasm_carts["blit"])


def test_a_compiled_cart_with_no_module_runs_on_the_interpreter(board, wasm_carts):
    on_glass.wasm_no_module_at_all_runs_on_the_interpreter(board, WASM_BOARD_DIR)


def test_a_module_for_another_chip_runs_on_the_interpreter(board, wasm_carts):
    on_glass.wasm_a_module_for_another_chip_runs_on_the_interpreter(board, WASM_BOARD_DIR)


def test_a_stale_format_module_runs_on_the_interpreter(board, wasm_carts):
    on_glass.wasm_stale_format_module_runs_on_the_interpreter(board, WASM_BOARD_DIR)


def test_a_compiled_cart_whose_module_was_tampered_with_is_refused(board, wasm_carts):
    on_glass.wasm_tampered_module_is_refused(board, WASM_BOARD_DIR)


def test_an_unsigned_cart_runs_only_with_unknown_sources_on(board, wasm_carts):
    on_glass.wasm_unsigned_cart_follows_unknown_sources(board, WASM_BOARD_DIR)


def test_a_compiled_cart_too_big_for_the_board_opens_the_notice(board, wasm_carts):
    on_glass.wasm_too_big_cart_opens_the_notice(board, WASM_BOARD_DIR)


def test_a_compiled_cart_for_a_newer_console_opens_the_notice(board, wasm_carts):
    on_glass.wasm_newer_cart_opens_the_notice(board, WASM_BOARD_DIR)


def test_a_folder_in_the_cart_reads_as_a_missing_file(board, wasm_carts):
    on_glass.wasm_read_of_a_folder_reads_nothing(board, WASM_BOARD_DIR)


def test_a_compiled_carts_written_files_outlive_its_session(board, wasm_carts):
    on_glass.wasm_written_files_outlive_the_session(board, WASM_BOARD_DIR)


# The compiled tier's showcase, Jet Teapot (ports/jet/README.md), from the
# launcher: uncapped with WiFi off, in Phong -- the costliest of its three
# shadings and the steadiest to measure -- at half and at full width. The
# floors sit about a fifth under what this board drew, its raster on both
# cores, when they were set (2026-10-01); the figures are #158's. The cart
# stays installed as it ships.
JET_HALF_FPS_FLOOR = 26
JET_FULL_FPS_FLOOR = 22


def test_the_jet_showcase_holds_its_floor_at_half_width(board):
    on_glass.jet_holds_its_floor(board, WASM_BOARD_DIR, JET_HALF_FPS_FLOOR,
                                 width="half", shading="phong")


def test_the_jet_showcase_holds_its_floor_at_full_width(board):
    on_glass.jet_holds_its_floor(board, WASM_BOARD_DIR, JET_FULL_FPS_FLOOR,
                                 shading="phong")


# Its blit565 frames go to the glass from the cart's memory: the PPA scales
# them from there and the GDMA snapshots each into the run's scratch, and
# nothing writes them into the game canvas (device/p4_canvas.py).
def test_the_showcases_frames_go_to_the_glass_from_the_cart(board):
    on_glass.jet_push(board, WASM_BOARD_DIR)
    on_glass.p4_compiled_frames_go_to_the_glass_from_the_cart(
        board, on_glass.JET_TITLE, glass=False)


# Doom, built by the recipe (experiments/wasm_aot/doom/): skips until the
# developer has built the cart, which is never in the repository or CI.
def test_doom_frames_match_the_host(board):
    on_glass.doom_frames_match_the_host(board, WASM_BOARD_DIR)


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
    # 270 is up (owner-verified); RETAINED_FRAMES is 2 since the paint buffer
    # ping-pongs (2026-09-08, the desk's async composite) -- the horizon the WM
    # already floored to.
    assert line == "PY (True, 270, 800, 1280, 2)", line


def test_the_pointer_is_the_gsl3680(board):
    """The touch driver came up: firmware uploaded and running (the chip's
    0xB0 signature -- `available`), the poll answering with no finger on the
    glass, at this glass's size, with the calibrated mapping (three corner
    holds, 2026-09-06)."""
    line = board.cmd("py (touch.available, touch.fingers, touch.w, touch.h, "
                     "touch.swap_xy, touch.flip_x, touch.flip_y)",
                     wait_for="PY ")
    # The 2026-09-06 calibration: landscape as mounted, no swap, no flips.
    assert line == "PY (True, 0, 1280, 800, False, False, False)", line
    # The mapping is the board's FITTED knobs (guition_p4_input.RAW_*: the
    # five-target fit, not the firmware's nominal 1664x896), and the shared
    # driver must carry exactly them -- a re-fit changes the module, not this.
    line = board.cmd("py (touch.raw_x0, touch.raw_y0, touch.raw_w, touch.raw_h) == "
                     "tuple(getattr(__import__('guition_p4_input'), k) "
                     "for k in ('RAW_X0', 'RAW_Y0', 'RAW_W', 'RAW_H'))",
                     wait_for="PY ")
    assert line == "PY True", line


def test_the_ppa_composite_is_live(board):
    """The shared P4 silicon tier (device/p4_canvas.py over native/p4/moy_ppa)
    registered on this board too -- the game composite runs on the DMA
    engine, not the CPU kernel."""
    line = board.cmd("py ws.sys_canvas._ppa is not None", wait_for="PY ")
    assert line == "PY True", line


def test_wifi_status_is_readable(board):
    on_glass.wifi_status_is_readable(board)


def test_wifi_is_off_at_rest(board):
    on_glass.wifi_is_off_at_rest(board)


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


def test_the_wm_meters_answer_for_the_frame_they_measured(board):
    """The second windowed board makes the same check: a tier difference is an
    ARGUMENT to a shared body, never a reason for only one board to assert
    (.claude/rules/testing.md). This one was pinned on the Waveshare P4 alone
    and silently unpinned here."""
    on_glass.wm_meters_answer_for_the_frame_they_measured(board)


def test_a_cart_runs_and_exits(board):
    on_glass.cart_runs_and_exits(board, "star", door="shell", clear=3)


def test_a_lua_cart_runs_and_exits(board):
    """moycore on the second P4: the Lua tier reaches every board by default
    (the shared native staging), so pin it with a real run."""
    on_glass.cart_runs_and_exits(board, "sakura lua", title="Sakura Lua",
                                 door="shell", clear=3)


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
    before = board.pyval("comp.rotate_stats()", strict=True)
    board.drain(2.0)
    after = board.pyval("comp.rotate_stats()", strict=True)
    # rotate_stats: (full frames, full_us, rect frames, rect_us, copies).
    full = after[0] - before[0]
    rect = after[2] - before[2]
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


def test_no_display_underruns(board):
    """The scan-out kept up for the whole tour: the 800x1280@60Hz DPI stream
    is ~123MB/s of PSRAM reads, more than the Waveshare's, and PSRAM at 200MHz
    is what makes it hold (sdkconfig.board)."""
    on_glass.display_underruns_are_zero(board)


def test_draw_gates_are_installed(board):
    on_glass.draw_gates_are_installed(board, windowed=True)


def test_draw_gates_take_the_traffic(board):
    on_glass.draw_gates_take_the_traffic(board)


def test_the_web_console_is_baked_into_this_image(board):
    on_glass.web_console_is_baked_into_the_image(board)

# -- the engine's radio guards (docs/wasm_tier_plan_2026-09.md, phase 1) ------
# LAST in the file: both bring WiFi up (released again) and the second starts
# BLE, and the WiFi driver keeps its internal RAM for the rest of the boot.


def test_load_unload_loop_under_a_live_cart_and_wifi(board, wasm):
    on_glass.wasm_load_unload_under_flush_and_wifi(board, wasm)


def test_a_run_with_wifi_and_ble_up_costs_at_most_a_constant(board, wasm):
    on_glass.wasm_low_water_with_radios_up(board, wasm)
