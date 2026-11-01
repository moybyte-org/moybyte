"""On-glass M5Stack Core2 console tests: drive the REAL board over serial.

Gated: they run only when MOYBYTE_CORE2_PORT is set (`tools/board.py pass
core2` sets it), so the normal host suite never needs hardware.

The Core2 is the T-Deck's tier on the classic ESP32: the same fullscreen
console on the same 320x240 panel module, so its suite is the T-Deck's shared
bodies (tests/on_glass.py) minus what the board does not have -- the
WebAssembly tier (denied in its board.toml), the C3 keyboard's matrix and the
VM stop (no MOY_VM_STOP). Its console is a UART behind a CH9102, which is not
attach-only: the session opens with both lines deasserted, as `[serial]`
declares, and leaves the console on the launcher.
"""

import pytest

import on_glass
from on_glass import ROOT

PORT, pytestmark = on_glass.gate("MOYBYTE_CORE2_PORT", "the M5Stack Core2")


@pytest.fixture(scope="module")
def board():
    with on_glass.session(
            PORT, board_dir=ROOT / "firmware" / "m5stack_core2") as b:
        yield b


def test_state_snapshot_has_the_fullscreen_tier_shape(board):
    on_glass.fullscreen_tier_state(board)


def test_wifi_status_is_readable(board):
    on_glass.wifi_status_is_readable(board)


def test_the_kernel_verifies_a_signed_manifest(board):
    on_glass.the_kernel_verifies_a_signed_manifest(board)


def test_the_internal_volume_is_the_kernels(board):
    on_glass.the_internal_volume_is_the_kernels(board)


def test_wifi_is_off_at_rest(board):
    on_glass.wifi_is_off_at_rest(board)


def test_every_system_app_claims_exactly_one_cart(board):
    on_glass.every_app_claims_one_cart(board)


def test_py_probe_reaches_the_live_console(board):
    on_glass.py_probe_reaches_the_console(board)


def test_the_painter_matches_the_host(board):
    on_glass.painter_matches_host(board)


def test_the_sprites_match_the_host(board):
    on_glass.sprites_match_host(board)


def test_shift_last_and_copy_last_into_match_the_reference(board):
    on_glass.shift_last_matches_the_reference(board)


def test_the_font_set_is_the_tiers(board):
    on_glass.the_font_set_is_the_tiers(board, 'handheld')


def test_diag_toggle_roundtrips(board):
    on_glass.diag_toggle_roundtrips(board)


def test_swipe_rides_the_real_pointer_feed(board):
    on_glass.home_shelf_fling(board, 260, 60, 140)


def test_a_cart_runs_and_exits(board):
    on_glass.cart_runs_and_exits(board, "star")


def test_the_pause_menu_dims_the_game_and_holds_the_heap(board):
    on_glass.pause_menu_dims_and_holds_the_heap(board, "star")


def test_a_lua_cart_runs_and_exits(board):
    on_glass.cart_runs_and_exits(board, "sakura lua", title="Sakura Lua")


@pytest.mark.parametrize("spec,title", on_glass.VM_FREE_SEEDS)
def test_a_vm_free_frame_makes_no_crossing(board, spec, title):
    on_glass.a_vm_free_frame_makes_no_crossing(board, spec, title)


def test_draw_gates_are_installed(board):
    on_glass.draw_gates_are_installed(board)


def test_the_web_console_is_baked_into_this_image(board):
    on_glass.web_console_is_baked_into_the_image(board)


def test_idle_screen_blank_and_wake(board):
    on_glass.idle_blank_and_wake(board)
    on_glass.idle_timeout_restored(board)


def test_mem_reports_the_heap(board):
    on_glass.mem_reports_the_heap(board)


def test_internal_flash_commits_under_a_running_cart(board):
    on_glass.internal_flash_commits_under_a_cart(board)


def test_the_frame_is_the_kernels(board):
    on_glass.the_frame_is_the_kernels(board)


def test_the_kernel_lit_the_logo_before_any_vm(board):
    on_glass.the_kernel_lit_the_logo(board)


def test_heapcaps_mem_and_hush_are_the_kernels_words(board):
    on_glass.board_words_are_the_kernels(board)


def test_loop_is_the_kernels_line_with_every_stage(board):
    on_glass.loop_line_is_the_kernels(board)
