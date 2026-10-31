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
exactly like a dead board (the T-Deck README's RX section -- three separate "the board
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
# block at the end brings the radios up. The baseline is (free, largest)
# internal SRAM at the launcher on a fresh boot of a module-free image of the
# same tree: board.toml's moy_wasm `take` turned to `deny`, built from an
# EMPTY build dir (a configured one keeps the engine in its module table and
# fails to link; a fresh `tools/worktree.py new` has one). Measured
# 2026-10-31 on 1993fb0c, two fresh boots alike: 126791 free, 86016 largest;
# the image with the engine read 125243 / 81920. A change that adds internal
# statics re-measures it the same way.
WASM_IDLE_BASELINE = (126791, 86016)
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
# less a margin -- the hello cart's measured on 2026-09-25, the blit fixture's
# on 2026-09-28 with its frames going to the glass from the cart's memory; the
# measurements are the owner's to post, and native/moy_wasm/README.md states
# the per-board ceiling.
WASM_HELLO_FPS_FLOOR = 27
WASM_BLIT_FPS_FLOOR = 49


@pytest.fixture(scope="module")
def wasm_carts(board):
    return on_glass.wasm_carts_push(board, WASM_BOARD_DIR)


def test_a_compiled_app_reaches_its_roles_through_imports(board):
    root = on_glass.wasm_app_push(board, WASM_BOARD_DIR)
    on_glass.wasm_app_reaches_its_roles(board, root)


def test_a_compiled_app_draws_the_toolkits_widgets(board):
    root = on_glass.wasm_app_push(board, WASM_BOARD_DIR)
    on_glass.wasm_app_draws_the_toolkit(board, root)


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


# A compiled cart's frame goes to the glass from the cart's own memory
# (native/moy_flush/moy_fold.h's frame fold): with the FPS chip on, every flush
# is folded from the cart, and the bands are the composite the frame makes,
# byte for byte -- a palette frame (the blit fixture) and a blit565 one (Jet).
def test_a_palette_frame_goes_to_the_glass_from_the_carts_memory(board, wasm_carts):
    on_glass.compiled_frames_go_to_the_glass_from_the_cart(board, wasm_carts["blit"], 2)


def test_a_blit565_frame_goes_to_the_glass_from_the_carts_memory(board):
    on_glass.jet_push(board, WASM_BOARD_DIR)
    on_glass.compiled_frames_go_to_the_glass_from_the_cart(board, on_glass.JET_TITLE, 1)


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
# cores, when they were set (2026-09-30); the figures are #158's. The cart stays installed as it ships.
JET_HALF_FPS_FLOOR = 15
JET_FULL_FPS_FLOOR = 12


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


# A file doom1.wad's size (4196020 bytes): the same regression, over this
# board's USB-Serial/JTAG rather than the Waveshare's unflow-controlled UART.
def test_a_doom_sized_file_is_skipped_when_current_and_sent_when_not(board):
    on_glass.big_push_skips_when_current_and_sends_when_changed(
        board, WASM_BOARD_DIR)


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


def test_the_bar_wifi_widget_opens_the_wifi_panel(board):
    on_glass.the_bar_wifi_widget_opens_the_wifi_panel(board)


def test_every_system_app_claims_exactly_one_cart(board):
    on_glass.every_app_claims_one_cart(board)


def test_leaving_paint_saves_a_stroke(board):
    on_glass.paint_saves_a_stroke_on_leaving(board)


def test_py_probe_reaches_the_live_console(board):
    on_glass.py_probe_reaches_the_console(board)


def test_the_painter_matches_the_host(board):
    on_glass.painter_matches_host(board)


def test_the_sprites_match_the_host(board):
    on_glass.sprites_match_host(board)


def test_the_picker_kernel_matches_sspr(board):
    on_glass.picker_kernel_matches_sspr(board)


def test_shift_last_and_copy_last_into_match_the_reference(board):
    on_glass.shift_last_matches_the_reference(board)


def test_a_fuzzed_font_corpus_loads_as_on_the_host(board):
    on_glass.fonts_match_host(board)


def test_a_fuzzed_layout_corpus_reads_as_on_the_host(board):
    on_glass.layouts_match_host(board)


def test_custom_themes_derive_as_on_the_host(board):
    on_glass.theme_custom_matches_host(board)


def test_the_theme_font_is_installed(board):
    on_glass.the_theme_font_is_installed(board)


def test_the_font_set_is_the_tiers(board):
    on_glass.the_font_set_is_the_tiers(board, 'handheld')


def test_the_glyph_lane_draws_the_hosts_bytes(board):
    on_glass.glyph_lane_matches_host(board, 'handheld')


def test_a_leaked_session_leaves_the_panel_flushing(board):
    on_glass.a_leaked_session_leaves_the_panel_flushing(board)


def test_a_stale_handle_is_refused_loudly(board):
    on_glass.stale_handle_is_refused_loudly(board)


def test_diag_toggle_roundtrips(board):
    on_glass.diag_toggle_roundtrips(board)


def test_swipe_rides_the_real_pointer_feed(board):
    on_glass.home_shelf_fling(board, 260, 60, 140)


def test_a_long_press_opens_the_cards_verbs(board):
    on_glass.home_long_press_opens_card_verbs(board)


# Matrix frames (d0..d4) of the keys the home's strip names, and what this
# image's own decoder must type for each: C N Y X F, and Sym+G for Search's /.
HOME_KEYS = (([0, 0, 0x20, 0, 0], "c"), ([0, 0, 0, 0x20, 0], "n"),
             ([0, 0, 0, 0x04, 0], "y"), ([0, 0x10, 0, 0, 0], "x"),
             ([0, 0, 0x40, 0, 0], "f"), ([0x04, 0, 0x02, 0, 0], "/"))
_DECODE = "__import__('moy_input').keyboard().decode(bytes(%r))[1]"


def test_the_homes_letter_keys_arrive_from_the_matrix(board):
    """Design review R3-02: the home keeps the keyboard on the raw matrix
    (Mic and a held direction read there), so its verb letters must be keys
    the matrix decode types. This image's decoder answers each; then the C it
    types, queued as a typed key, does Change on a Games card (the Editor).
    A person pressing the key is the one step no suite takes."""
    assert board.pyval("ws.screen", strict=True) == "launcher"
    assert board.pyval("ws.input.text_mode", strict=True) is False
    assert board.pyval("ws.keyboard.raw_mode", strict=True) is True, \
        "the home is not on the matrix (keyboard firmware older than 2025-06-12?)"
    for data, ch in HOME_KEYS:
        assert board.pyval(_DECODE % data, strict=True) == ord(ch), ch
    try:
        board.pyexec("ws.set_home_tab('games'); ws.launcher.sel = [i for i, it in "
                     "enumerate(ws.launcher.items) if it.get('path')][0]", strict=True)
        board.pyexec("ws.input.source('local').key(%s)" % (_DECODE % HOME_KEYS[0][0]),
                     strict=True)
        board.drain(0.8)
        assert board.pyval("ws.screen", strict=True) == "menu", "C did not open the Editor"
    finally:
        board.pyexec("ws.go_home()", strict=True)
        board.drain(0.5)


def _glass_key(board, k):
    board.pyexec("ws.input.source('local').key(%d)" % k, strict=True)
    board.drain(0.4)


def _glass_start(board):
    """One press of `start` with the T-Deck's keys the input in use."""
    board.pyexec("import ui_draw; ui_draw.input_kind('tdeck'); "
                 "ws.input.source('local').set_button('start', True)", strict=True)
    board.drain(0.3)
    board.pyexec("ws.input.source('local').set_button('start', False)", strict=True)
    board.drain(0.3)


def test_a_field_reads_the_matrix_and_mic_cancels_it(board):
    """Owner call 2026-10-18: in a field Enter is Done and Mic is Cancel.
    The Name field keeps the keyboard on the matrix (where Mic shows; the
    C3's ASCII mode sends nothing for it), a Mic press puts the typed text
    back, Enter keeps it, and Mic outside the field opens the system menu.
    The keys are queued into the console's table, Mic as `start`: a person
    pressing them is the one step no suite takes."""
    was = board.pyval("ws.system.get('name', '')", strict=True)
    try:
        board.pyexec("ws.open_settings(); ws.settings_layer.show_row('name')", strict=True)
        board.drain(0.8)
        assert board.pyval("ws.settings_layer.editing_field()", strict=True) is True
        assert board.pyval("ws.input.text_mode", strict=True) is True
        assert board.pyval("ws.keyboard.raw_mode", strict=True) is True, \
            "a text surface reads the matrix"
        _glass_key(board, ord("q"))
        _glass_start(board)
        assert board.pyval("ws.system.get('name', '')", strict=True) == was
        assert board.pyval("ws.settings_layer.editing_field()", strict=True) is False
        assert board.pyval("ws.sysmenu.open", strict=True) is False
        board.pyexec("ws.settings_layer.show_row('name')", strict=True)
        board.drain(0.5)
        _glass_key(board, ord("z"))
        _glass_key(board, 13)
        assert board.pyval("ws.system.get('name', '')", strict=True) == (was or "") + "z"
        _glass_start(board)                       # outside the field: the console's key
        assert board.pyval("ws.sysmenu.open", strict=True) is True
    finally:
        board.pyexec("ws.sysmenu.close(); ws.popover_touch(); ws.go_home()", strict=True)
        board.pyval("ws.system.set('name', %r)" % (was,))   # a flash write: its own line
        board.drain(0.5)


def test_a_cart_runs_and_exits(board):
    on_glass.cart_runs_and_exits(board, "star")


def test_the_pause_menu_dims_the_game_and_holds_the_heap(board):
    on_glass.pause_menu_dims_and_holds_the_heap(board, "star")


def test_a_lua_cart_runs_and_exits(board):
    """The Lua tier is supposed to reach EVERY board by default (the point of
    the shared native staging), so pin it with a real run, not an import."""
    on_glass.cart_runs_and_exits(board, "sakura lua", title="Sakura Lua")


@pytest.mark.parametrize("spec,title", on_glass.VM_FREE_SEEDS)
def test_a_vm_free_frame_makes_no_crossing(board, spec, title):
    on_glass.a_vm_free_frame_makes_no_crossing(board, spec, title)


@pytest.mark.parametrize("spec,title", on_glass.VM_FREE_SEEDS)
def test_a_front_run_makes_no_crossing(board, spec, title):
    on_glass.a_front_run_makes_no_crossing(board, spec, title)


# The compiled half of the same check: the blit fixture's frames run in the
# kernel's Player on the engine's thread, and neither its frames nor its run's
# books since launch hold an APP, SERVICE or REFUSED crossing; the hello
# fixture reads its greeting from its own folder at _init, in C.
@pytest.mark.parametrize("which", ("blit", "hello"))
def test_a_compiled_frame_makes_no_crossing(board, wasm_carts, which):
    title = wasm_carts[which]
    on_glass.a_vm_free_frame_makes_no_crossing(board, title.lower(), title,
                                               runtime="wasm")


# The runaway watch (native/moy_play/moy_play.h): a frame that never comes
# back is ended, Lua on its line and a compiled cart at its next import call.
@pytest.fixture(scope="module")
def stuck_carts(board):
    on_glass.stuck_carts_push(board, WASM_BOARD_DIR)


@pytest.mark.parametrize("runtime", ("lua", "wasm"))
def test_a_frame_that_never_ends_is_ended(board, stuck_carts, runtime):
    on_glass.a_stuck_run_is_ended(board, runtime)


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


def test_a_quiet_console_collects_its_garbage(board):
    on_glass.a_quiet_console_collects_its_garbage(board)


def test_the_saver_comes_and_its_wake_repaints(board):
    on_glass.idle_saver_and_wake(board)


def test_internal_flash_commits_under_a_running_cart(board):
    ms = on_glass.internal_flash_commits_under_a_cart(board)
    print("\nlongest internal-flash commit under a cart: %d ms" % ms)


def test_the_frame_is_the_kernels(board):
    on_glass.the_frame_is_the_kernels(board)


def test_perf_line_is_the_one_format(board):
    """#206 item 2. This board has no windowed WM and no PPA, so those columns
    must read `-`: absence, never a 0 that a dead meter would also print."""
    got = on_glass.perf_line_is_the_one_format(board)
    for name in ("wmr", "wmw", "wms", "ppa", "fence_ms", "gfence_ms"):
        assert got[name] is None, (name, got[name])

# -- the engine's radio guards (docs/wasm_tier_plan_2026-09.md, phase 1) ------
# LAST in the file: both bring WiFi up (released again), and the WiFi driver
# keeps its internal RAM for the rest of the boot; the second starts BLE, which
# is off here until Settings asks for it, and stops it again.


def test_load_unload_loop_under_a_live_cart_and_wifi(board, wasm):
    on_glass.wasm_load_unload_under_flush_and_wifi(board, wasm)


def test_a_run_with_wifi_and_ble_up_costs_at_most_a_constant(board, wasm):
    on_glass.wasm_low_water_with_radios_up(board, wasm)


def test_the_kernel_lit_the_logo_before_any_vm(board):
    on_glass.the_kernel_lit_the_logo(board)


def test_heapcaps_mem_and_hush_are_the_kernels_words(board):
    on_glass.board_words_are_the_kernels(board)


def test_loop_is_the_kernels_line_with_every_stage(board):
    on_glass.loop_line_is_the_kernels(board)


def test_a_ctrl_c_ends_a_run_in_front_and_reaches_the_repl(board):
    on_glass.a_ctrl_c_ends_a_front_run(board)


# The VM stop (docs/kernel_cartpath_2026-10.md section 5): the kernel stops the
# VM and starts it again with no cart, the route and the lease set before each
# stop read back after it, PSRAM flat while it is down; Doom runs with no VM
# (its fit forced to fail: it fits a fresh T-Deck), `state` says so, and its
# end -- the dev channel's `end`, or a Ctrl-C -- brings a VM back. Each ends on
# a freshly started VM, as the soft resets below do.
def test_vm_stops_leave_psram_flat_and_the_routes_read_back(board):
    on_glass.vm_stops_leave_psram_flat(board, n=5)


def test_doom_runs_with_the_vm_down(board):
    on_glass.doom_runs_with_the_vm_down(board, hold_link=True)


def test_a_ctrl_c_during_a_stopped_run_reaches_a_vm(board):
    on_glass.doom_runs_with_the_vm_down(board, ctrl_c=True)


def test_a_run_off_the_shelf_keeps_the_vm_for_its_place(board):
    on_glass.a_run_off_the_shelf_keeps_the_vm_for_its_place(board)


def test_a_stuck_run_with_the_vm_down_is_ended(board, stuck_carts):
    on_glass.a_stuck_run_with_the_vm_down_is_ended(board)


# LAST in the file: twenty soft resets end on a freshly started VM.
def test_twenty_soft_resets_leave_psram_flat(board):
    on_glass.soft_resets_leave_psram_flat(board, n=20)
