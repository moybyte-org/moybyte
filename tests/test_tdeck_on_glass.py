"""On-glass T-Deck console tests (#201): drive the REAL board over serial.

Gated: they run only when MOYBYTE_TDECK_PORT is set (e.g.
`MOYBYTE_TDECK_PORT=/dev/ttyACM0 .venv/bin/python -m pytest
tests/test_tdeck_on_glass.py -v`), so the normal host suite never needs
hardware. The suite that #201 said was "worth more than the ~0.5MB of flash
the LVGL removal frees" -- the T-Deck's RX works on the mainline port, the
dev channel is the shared `runtime/dev_channel.py`, and this is the P4
suite's pattern pointed at the second board.

DELIBERATELY NO RESET, unlike the P4 suite -- and that is now READ, not
retyped: this board's `[serial]` block declares `attach_only` because its
USB-Serial/JTAG is ON the SoC, so a reset tears the USB device down under the
open handle and a reader that reopens too early sees zero bytes and looks
exactly like a dead board (CLAUDE.md's RX section -- three separate "the board
is silent" conclusions in one session were this). So this suite ATTACHES to the
running desktop, asserts, and leaves the console where it found it: on the
launcher.

Tests share the session in file order, each leaving the console in the state
the next one expects. The bodies every fullscreen-tier board shares live in
tests/on_glass.py; what is here is what is the T-Deck's.
"""

import pytest

import on_glass
from on_glass import ROOT

PORT, pytestmark = on_glass.gate("MOYBYTE_TDECK_PORT", "the T-Deck")


@pytest.fixture(scope="module")
def board():
    with on_glass.session(
            PORT,
            board_dir=ROOT / "firmware" / "lilygo_t_deck_plus_mainline") as b:
        yield b


# The engine's idle cost (docs/wasm_tier_plan_2026-09.md, guard 1). FIRST in
# the file on purpose: the comparison is against a fresh boot, and the wasm
# block at the end brings the radios up. Measured 2026-09-25 on a module-free
# image of the same tree, at the launcher right after boot: (free, largest)
# internal SRAM.
WASM_IDLE_BASELINE = (122535, 81920)
WASM_BOARD_DIR = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"


def test_the_wasm_engine_costs_the_idle_desk_at_most_a_constant(board):
    on_glass.wasm_idle_cost_is_bounded(board, WASM_IDLE_BASELINE,
                                       ble_at_boot=False)

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


def test_the_run_stack_works_in_psram_and_internal_sram(board, wasm):
    on_glass.wasm_run_stack_placements(board, wasm)


def test_terminate_reaches_a_runaway_only_at_its_end(board, wasm):
    on_glass.wasm_runaway_runs_to_its_end(board, wasm)


def test_a_lua_cart_after_a_wasm_run_keeps_its_sram(board, wasm):
    on_glass.wasm_lua_after_wasm_keeps_its_sram(board, wasm)


# -- the Player path (docs/wasm_tier_plan_2026-09.md, phase 3) ---------------
# Compiled carts from the launcher, WiFi off -- the state a cart plays in, so
# before the radio guards. The floors are this board's median drawn fps
# less a margin -- the hello cart's measured on 2026-09-25, the blit fixture's
# on 2026-09-28 with its frames going to the glass from the cart's memory; the
# measurements are the owner's to post, and native/moy_wasm/README.md states
# the per-board ceiling.
WASM_HELLO_FPS_FLOOR = 27
WASM_BLIT_FPS_FLOOR = 49


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


# A compiled cart's frame goes to the glass from the cart's own memory
# (native/moy_flush/moy_fold.h's frame fold): with the FPS chip on, every flush
# is folded from the cart, and the bands are the composite the frame makes,
# byte for byte -- a palette frame (the blit fixture) and a blit565 one (Jet).
def test_a_palette_frame_goes_to_the_glass_from_the_carts_memory(board, wasm_carts):
    on_glass.compiled_frames_go_to_the_glass_from_the_cart(board, wasm_carts["blit"], 2)


def test_a_blit565_frame_goes_to_the_glass_from_the_carts_memory(board):
    on_glass.jet_push(board, WASM_BOARD_DIR)
    on_glass.compiled_frames_go_to_the_glass_from_the_cart(board, on_glass.JET_TITLE, 1)


def test_a_compiled_cart_without_this_chips_module_is_refused(board, wasm_carts):
    on_glass.wasm_missing_module_is_refused(board, WASM_BOARD_DIR)


def test_a_compiled_cart_whose_module_was_tampered_with_is_refused(board, wasm_carts):
    on_glass.wasm_tampered_module_is_refused(board, WASM_BOARD_DIR)


def test_an_unsigned_cart_runs_only_with_unknown_sources_on(board, wasm_carts):
    on_glass.wasm_unsigned_cart_follows_unknown_sources(board, WASM_BOARD_DIR)


def test_a_compiled_cart_too_big_for_the_board_opens_the_notice(board, wasm_carts):
    on_glass.wasm_too_big_cart_opens_the_notice(board, WASM_BOARD_DIR)


def test_a_folder_in_the_cart_reads_as_a_missing_file(board, wasm_carts):
    on_glass.wasm_read_of_a_folder_reads_nothing(board, WASM_BOARD_DIR)


# The compiled tier's showcase, Jet Teapot (ports/jet/README.md), from the
# launcher: uncapped with WiFi off, in Phong -- the costliest of its three
# shadings and the steadiest to measure -- at half and at full width. The
# floors sit about a fifth under what this board drew when they were set
# (2026-09-28); the figures are #158's. The cart stays installed as it ships.
JET_HALF_FPS_FLOOR = 11
JET_FULL_FPS_FLOOR = 8


def test_the_jet_showcase_holds_its_floor_at_half_width(board):
    on_glass.jet_holds_its_floor(board, WASM_BOARD_DIR, JET_HALF_FPS_FLOOR,
                                 width="half", shading="phong")


def test_the_jet_showcase_holds_its_floor_at_full_width(board):
    on_glass.jet_holds_its_floor(board, WASM_BOARD_DIR, JET_FULL_FPS_FLOOR,
                                 shading="phong")


# Doom, built by the recipe (experiments/wasm_aot/doom/): its load needs the
# linear memory's block, the pool and the text at once, which is about what
# this board's 3 MB cart-runtime reserve has free with the shell resident --
# more on a fresh boot, less once the radios and a session's carts have run.
# So the fit check decides, and the suite holds the board to it: where it
# fits, the frames are the host's and the run's median drawn fps holds a
# floor about a fifth under what this board drew when it was set
# (2026-09-28, the figures are #158's); the fit notice where it does not.
# Skips until the developer has built the cart.
DOOM_FPS_FLOOR = 28


def test_doom_runs_or_opens_the_notice(board, wasm_carts):
    on_glass.doom_runs_or_opens_the_notice(board, WASM_BOARD_DIR,
                                           floor=DOOM_FPS_FLOOR)


def test_state_snapshot_has_the_fullscreen_tier_shape(board):
    on_glass.fullscreen_tier_state(board)


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


def test_swipe_rides_the_real_pointer_feed(board):
    on_glass.home_shelf_fling(board, 260, 60, 140)


def test_a_cart_runs_and_exits(board):
    on_glass.cart_runs_and_exits(board, "star")


def test_a_lua_cart_runs_and_exits(board):
    """The Lua tier is supposed to reach EVERY board by default (the point of
    the shared native staging), so pin it with a real run, not an import."""
    on_glass.cart_runs_and_exits(board, "sakura lua", title="Sakura Lua")


def test_draw_gates_are_installed(board):
    on_glass.draw_gates_are_installed(board)


def test_draw_gates_take_the_traffic(board):
    on_glass.draw_gates_take_the_traffic(board)


def test_the_web_console_is_baked_into_this_image(board):
    on_glass.web_console_is_baked_into_the_image(board)


def test_idle_screen_blank_and_wake(board):
    on_glass.idle_blank_and_wake(board)
    on_glass.idle_timeout_restored(board)


def test_mem_reports_the_heap(board):
    on_glass.mem_reports_the_heap(board)


def test_perf_line_is_the_one_format(board):
    """#206 item 2. This board has no windowed WM and no PPA, so those columns
    must read `-`: absence, never a 0 that a dead meter would also print."""
    got = on_glass.perf_line_is_the_one_format(board)
    for name in ("wmr", "wmw", "wms", "ppa", "fence_ms", "gfence_ms"):
        assert got[name] is None, (name, got[name])

# -- the engine's radio guards (docs/wasm_tier_plan_2026-09.md, phase 1) ------
# LAST in the file: both bring WiFi up (released again) and the second starts
# BLE, and the WiFi driver keeps its internal RAM for the rest of the boot.


def test_load_unload_loop_under_a_live_cart_and_wifi(board, wasm):
    on_glass.wasm_load_unload_under_flush_and_wifi(board, wasm)


def test_a_run_with_wifi_and_ble_up_costs_at_most_a_constant(board, wasm):
    on_glass.wasm_low_water_with_radios_up(board, wasm)
