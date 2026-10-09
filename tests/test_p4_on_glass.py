"""On-glass P4 console tests (#58): drive the REAL board over serial.

Gated: they run only when MOYBYTE_P4_PORT is set (e.g.
`MOYBYTE_P4_PORT=/dev/ttyACM0 .venv/bin/python -m pytest
tests/test_p4_on_glass.py -v`), so the normal host suite never needs
hardware. One board reset per module; tests share the session and are
ordered (file order == execution order), each leaving the console in the
state the next one expects -- an on-glass tour, not isolated units.

The device half is the serial dev-command set in
firmware/esp32_p4_wifi6_touch_lcd_7b/modules/moy_runtime.py (`swipe` feeds
the real pointer path, `state` answers with a JSON snapshot); the driver is
tools/p4_autotest.P4Board. This is the only one of the three suites whose
board may be RESET -- its `[serial]` block does not declare `attach_only`,
because the CH343 is an external USB-UART that survives a chip reset. The
handful of checks it shares with the two fullscreen-tier boards live in
tests/on_glass.py; everything below that is windowed-desk or OTA is this
board's alone.
"""

import pytest

import on_glass
from on_glass import ROOT

PORT, pytestmark = on_glass.gate("MOYBYTE_P4_PORT", "the P4")


@pytest.fixture(scope="module")
def board():
    with on_glass.session(
            PORT,
            board_dir=ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b") as b:
        b.cmd("diag 1")            # this suite asserts PERF lines flow
        yield b
        # Leave the board freshly booted on the desk for a human -- the one
        # board this may be done to (see the module docstring).
        b.ser.write(b"\r\x03")
        b.drain(0.5)
        b.ser.write(b"\x04")
        b.drain(1.0)


# The engine's idle cost (docs/wasm_tier_plan_2026-09.md, guard 1). FIRST in
# the file on purpose: the comparison is against a fresh boot, and the wasm
# block at the end brings the radios up. Measured 2026-09-30 on a module-free
# image of the same tree, at the launcher right after boot: (free, largest)
# internal SRAM -- less TF_CARD_SRAM, what the card driver holds once the card
# is the cart store (2026-10-05, same image and same fresh boot: 275143 free
# with the engine and no card code, 274299 with the card mounted). The engine's
# own cost stays the bound the guard measures.
TF_CARD_SRAM = 844
# And less KERNEL_SRAM, the kernel's static share (native/moy_kernel: its
# panic wrapper in IRAM and its statics), which every console image carries
# and docs/native_kernel_2026-09.md section 6.1 bounds on its own: measured
# 2026-10-06 on the Waveshare P4; the Guition P4 runs the same code, the internal heap an image that takes
# the module has against the same tree denying it.
# Plus the glass's statics (native/moy_glass: its tables' roots and the
# present engine), 48 bytes of .dram0.bss by the link map against dev
# 82144715, 2026-10-07.
# And the links' statics (native/moy_net/moy_link.c: the link's ring header,
# its latch and its flag), 40 bytes of .bss and .data by the objects' sizes,
# 2026-10-07; the port's espnow module they replace held its ring in the heap.
# And the WiFi driver's (native/moy_net/moy_wifi.c: its state, latch and
# connect flag), 56 bytes of .bss and .data by the object's sizes, 2026-10-07.
# Plus the kernel's audio (native/moy_audio, which this
# board takes since #82: its table roots, counters and output state), 304 bytes
# of .dram0.data and .bss by the link map against dev cafae3a2, 2026-10-07; the
# feeder's stack, ring and lock are allocated at the first cart, not at boot.
# Plus input's statics (native/moy_input: the kernel table's latches, the
# drivers' state, the kernel's I2C bus, which the carve compiled as a stub, and
# NimBLE's bond cache, ble_store_config, in place of MicroPython's bluetooth),
# 1455 bytes of .dram0.bss and .data by the objects' sizes, 2026-10-07.
# And the webhost's (native/moy_net/moy_webhost.c: the pointer to its state,
# which with its buffers is PSRAM), 4 bytes of .bss by the link map, 2026-10-07.
# And the WiFi driver's reconnect timer handle and backoff, 5 bytes of .bss by
# the link map, 2026-10-07 (the esp_timer it creates is the heap's).
# And the updater's (native/moy_net/moy_ota.c: the pointers to its state, its
# clients, its keys and the trusted set, which are PSRAM), its slot's position
# (moy_net_port.c) and the WiFi driver's kept-network flag, 38 bytes of .bss by
# the objects' sizes, 2026-10-07.
# And the web-console switch's (native/moy_net/moy_webconsole.c: the pointer
# to its state, which is PSRAM), 4 bytes of .bss by the object's size,
# 2026-10-07.
# And the setup portal's responder (native/moy_net/moy_dns.c: the pointer to
# its state, PSRAM) and the WiFi driver's access-point interface, 8 bytes of
# .bss by the objects' sizes, 2026-10-08.
# And the internal flash volume's (native/moy_store/moy_kvfs.c: the pointer to
# the kernel's littlefs instance, whose config and caches are PSRAM), 4 bytes
# of .bss by the object's size, 2026-10-08.
# And the frame's (native/moy_kernel: the loop's, the reader's and the
# ladder's statics and the VM-side stage table; the meters, the PERF window
# and the line buffer are PSRAM), 756 bytes of idle internal heap measured
# 2026-10-08 against the same tree's dev image on a fresh boot.
# Less what moy_alloc's registry gave back, its nodes moved to PSRAM beside
# their buffers (native/moy_alloc), net of first light, the board's
# dev-channel words and the kernel's HITCH and LOOP lines: 312 bytes of idle
# internal heap measured 2026-10-08 against dev 326a147f, fresh boots.
# And the cart path's statics (native/moy_play: the run's pointer, whose state
# is PSRAM, and the binding's two root pointers; native/moycore: the frame's
# split, which replaced the binding's own), 16 bytes of internal heap by the
# heap's total against dev 502be7fd, fresh boots, 2026-10-08.
KERNEL_SRAM = 1288 + 48 + 40 + 56 + 304 + 1455 + 4 + 5 + 38 + 4 + 8 + 4 + 756 - 312 + 16
WASM_IDLE_BASELINE = (276743 - TF_CARD_SRAM - KERNEL_SRAM, 188416)
WASM_BOARD_DIR = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b"


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
WASM_BLIT_FPS_FLOOR = 50


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
# cores, when they were set (2026-10-01); the figures are #158's. The cart stays installed as it ships.
JET_HALF_FPS_FLOOR = 27
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
        board, on_glass.JET_TITLE, glass=True)


# Doom, built by the recipe (experiments/wasm_aot/doom/): its frames are the
# host's, and the run's median drawn fps holds a floor about a fifth under
# what this board drew when it was set (2026-09-28, the figures are #158's).
# Skips until the developer has built the cart, which is never in the
# repository or CI.
DOOM_FPS_FLOOR = 32


def test_doom_frames_match_the_host(board):
    on_glass.doom_frames_match_the_host(board, WASM_BOARD_DIR,
                                        floor=DOOM_FPS_FLOOR)


# A file doom1.wad's size (4196020 bytes): the regression for "the board did
# not arm the raw upload (no reply)" on this UART. Slow by design (this
# board's ack is its only backpressure) -- measured ~8 minutes for the send.
def test_a_doom_sized_file_is_skipped_when_current_and_sent_when_not(board):
    on_glass.big_push_skips_when_current_and_sends_when_changed(
        board, WASM_BOARD_DIR)


def test_boots_to_the_desk(board):
    st = board.state()
    assert st.get("desk") is True
    assert not st.get("order")


def test_wifi_status_is_readable(board):
    on_glass.wifi_status_is_readable(board)


def test_the_kernel_verifies_a_signed_manifest(board):
    on_glass.the_kernel_verifies_a_signed_manifest(board)


def test_the_internal_volume_is_the_kernels(board):
    on_glass.the_internal_volume_is_the_kernels(board)


def test_wifi_is_off_at_rest(board):
    on_glass.wifi_is_off_at_rest(board)


def test_appearance_cart_is_claimed(board):
    """The Appearance app must claim its cart on the DEVICE store, or Settings'
    APPEARANCE row silently does nothing."""
    st = board.state()
    cart = st.get("appearance_cart")
    assert cart, "no Appearance cart in the device store"
    assert cart.get("is_app") is True, cart


def test_every_system_app_claims_exactly_one_cart(board):
    """The same identity check for every registered app, not just Appearance."""
    on_glass.every_app_claims_one_cart(board)


def test_open_settings_window(board):
    board.open("settings")
    board.drain(0.5)
    st = board.state()
    assert "settings" in st.get("order", ())


def test_settings_rows_scroll_on_swipe(board):
    st = board.state()
    board.swipe_settings(st)
    st = board.state()
    assert (st["settings"]["set_top"] or 0) > 0, st["settings"]


def test_scroll_survives_release(board):
    """Letting go must NOT snap the rows back to the top (the on-glass
    'thrown at the start' report)."""
    top = board.state()["settings"]["set_top"]
    board.drain(1.0)
    st = board.state()
    assert st["settings"]["set_top"] == top, st["settings"]


def test_appearance_opens(board):
    line = board.open("appearance")
    board.drain(0.5)
    st = board.state()
    assert "appearance" in st.get("order", ()), (line, st.get("order"))


def test_picker_opens(board):
    board.open("picker")
    board.drain(6.0)              # first-open cover pop-in settles
    st = board.state()
    assert "make" in st.get("order", ())


def test_window_buffers_are_single_retained_surfaces(board):
    """(#113) A window buffer holds the LAST paint, so it must advertise
    RETAINED_FRAMES = 1. The class default 2 describes the root DPI ping-pong;
    inheriting it made a picker drag shift by ~twice the real delta and ghost a
    duplicate of every card (owner-reported on glass, 2026-07-25)."""
    ret = board.state().get("win_retained") or {}
    assert ret, "no window buffers to check"
    assert all(v == 1 for v in ret.values()), ret


def test_draw_gates_are_installed(board):
    on_glass.draw_gates_are_installed(board, windowed=True)


def test_draw_gates_take_the_traffic(board):
    on_glass.draw_gates_take_the_traffic(board)


def test_py_probe_reaches_the_live_console(board):
    on_glass.py_probe_reaches_the_console(board)


def test_a_stale_handle_is_refused_loudly(board):
    on_glass.stale_handle_is_refused_loudly(board)


def test_diag_toggle_roundtrips(board):
    on_glass.diag_toggle_roundtrips(board)


def test_mem_reports_the_heap(board):
    on_glass.mem_reports_the_heap(board)


def test_the_saver_comes_and_its_wake_repaints(board):
    on_glass.idle_saver_and_wake(board)


def test_internal_flash_commits_under_a_running_cart(board):
    ms = on_glass.internal_flash_commits_under_a_cart(board)
    print("\nlongest internal-flash commit under a cart: %d ms" % ms)


def test_the_frame_is_the_kernels(board):
    on_glass.the_frame_is_the_kernels(board)


def test_no_display_underruns(board):
    on_glass.display_underruns_are_zero(board)


def test_window_chrome_freezes_during_a_content_scroll(board):
    """(#155) A window's title strip is disjoint from its content stamp, so a
    quiet frame must leave it alone once both ping-pong buffers hold it -- 8.2ms
    of a 70ms picker-scroll frame before the freeze. Probed MID-gesture: the
    freeze only engages while the desk serves its cache.

    tools/p4_chrome_freeze.py is the deeper version (it byte-compares the strip
    out of both ping-pong buffers); this pins that the freeze engages at all."""
    board.open("settings")
    board.drain(1.0)
    w = board.state()["wins"]["settings"]
    cx = w[0] + 1 + w[2] // 2
    ctop = w[1] + 1 + w[4]
    board.swipe_async(cx, ctop + (w[3] - w[4]) - 40, cx, ctop + 40, frames=200)
    board.drain(1.5)                     # let the streak reach both buffers
    quiet = board.pyval("ws.wm._chrome_quiet")
    streak = board.pyval("ws.wm._wins['settings']._chrome_streak")
    board.wait_line("swipe done", 30)
    assert quiet is True, "the scroll frame never went quiet"
    assert (streak or 0) >= 2, "chrome never froze (streak=%s)" % (streak,)


def test_perf_lines_flow(board):
    """One format on every board (#206 item 2), plus the column only this one
    can fill: the async-PPA overlap counters.

    The windowed WM's pass split (wmr/wmw/wms) moved to its own test below. It
    was asserted here as "not None" against whatever line an IDLE desk happened
    to emit, and since `c95bf89` those meters are TAKEN -- so a sample in which
    the WM drew nothing reads `-`, which is the honest answer and was being read
    as a regression."""
    got = on_glass.perf_line_is_the_one_format(board)
    assert isinstance(got["ppa"], tuple) and len(got["ppa"]) == 5, got
    assert got["ppa"][4] == 0, "PPA timeouts must stay 0: %r" % (got["ppa"],)


def test_the_wm_meters_answer_for_the_frame_they_measured(board):
    on_glass.wm_meters_answer_for_the_frame_they_measured(board)


def test_a_drag_touches_no_storage_and_rebuilds_no_cache(board):
    """The two invariants this board's UI perf now rests on, asserted on the real
    console via ws.note_cost (reported in `state` as "costs").

    Both were bugs found the slow way on 2026-07-26, each costing a day because
    neither produced any signal -- they just made two frames per gesture 5x
    slower:

      * cover blob reads (58ms each on this flash, 22ms even when the file is
        absent) landed on DRAG frames, because a cover was only loaded when its
        card first scrolled into view. Now prefetched on idle frames, so a drag
        must read nothing at all.
      * the bar's strip cache rebuilt on every switch between the WM's two draw
        destinations (root canvas via viewport / window buffer) -- 72ms of an 86ms
        frame. Now cached per destination, so a long drag must build ~once, not
        once per frame.

    Counted on the build/read side only, so this net costs nothing when healthy."""
    board.open("picker")
    board.drain(16.0)                    # let the idle prefetch finish the store
    g = board.pyval("ws.wm._wins['make'].ctx.layout.lib_grid")
    assert g is not None, "picker did not open as a window"
    w = board.state()["wins"]["make"]
    ox, oy = w[0] + 1, w[1] + 1 + w[4]
    gx, gy, gw, gh = g
    cy = oy + gy + gh // 2
    board.pyexec("ws.costs.clear()")
    f0 = board.state()["frames"]
    for _ in range(3):
        board.swipe(ox + gx + gw - 40, cy, ox + gx + 60, cy, 30)
    st = board.state()
    costs = st.get("costs") or {}
    painted = st["frames"] - f0
    assert painted > 60, "the drags painted only %d frames" % painted
    assert costs.get("cover.blob.read", 0) == 0, (
        "a drag read %d cover blobs from flash -- the idle prefetch is not "
        "covering the store (costs=%r)" % (costs.get("cover.blob.read"), costs))
    # A handful over ~135 frames is the healthy shape; one per frame is the bug.
    assert costs.get("bar.strip.render", 0) <= 4, (
        "the bar strip rebuilt %d times across %d frames (costs=%r)"
        % (costs.get("bar.strip.render"), painted, costs))


def test_idle_screen_blank_and_wake(board):
    """The #58 power save: the panel blanks after an idle timeout and any input
    wakes it, while the loop, the cart and this very serial channel stay live.

    The shared body is the blank/wake/`power off` trio every board runs; what
    is this board's is the tail -- 0 disables the timer outright, which the S3
    suites do not pin."""
    on_glass.idle_blank_and_wake(board)

    # 0 disables it outright: no blank, however long the silence.
    board.cmd("power 0", wait_for="REMOTE power")
    board.drain(6.0)
    line = board.cmd("power", wait_for="REMOTE power")
    assert "asleep=False" in line, "a disabled timer still blanked: %r" % line
    board.cmd("power 300", wait_for="REMOTE power")     # restore the default


# -- the OTA manifest verifier, in the kernel (#53, #224) ----------------------
#
# The verifier is native/moy_net/moy_ota.c's: the canonical text, SHA-256 and
# the RSA-2048 modexp in C. The host suite runs the same C over net_binding;
# every console's suite runs the image's own build of it
# (on_glass.the_kernel_verifies_a_signed_manifest), and this one times it.

SIGNED_MANIFEST = {
    "channel": "unstable", "version": 1785665581, "size": 4292512,
    "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "url": "https://example/app.bin", "label": "beta",
}


def test_verification_is_fast_enough_to_not_think_about(board):
    """It runs once per update check, behind a screen already waiting on the
    network; the bound catches an order-of-magnitude regression only."""
    import json as _json
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "tests"))
    from test_ota_signing import TEST_KEYS, sign_with_test_key

    manifest = dict(SIGNED_MANIFEST)
    manifest["sig"] = sign_with_test_key(manifest)
    assert board.pyexec(
        "import time\n"
        "KEYS = %r\n"
        "M = %r\n"
        "t0 = time.ticks_us()\n"
        "for _ in range(5):\n"
        "    __import__('moy_net').ota_judge(M, None, True, KEYS)\n"
        "VERIFY_US = time.ticks_diff(time.ticks_us(), t0) // 5\n"
        % (TEST_KEYS, _json.dumps(manifest)),
        timeout=60), board.last_error
    us = board.pyval("ws._g['VERIFY_US']", strict=True)
    print("\nota_judge: %dus (%.1fms)" % (us, us / 1000.0))
    assert 0 < us < 500_000, "verify took %dus -- something got much slower" % us


def test_the_ota_updater_is_live_on_this_board(board):
    """#53 on the P4. The partition table has been OTA-shaped since bring-up and
    update_ui frozen in all along; what was missing was moy_ota itself, the
    staging directory (this board has no SD), the identity stamp and the
    mark_valid call. Verified on glass 2026-08-02 by installing the board's OWN
    running image into the inactive slot: 3,085,216 bytes in 11 steps, reboot
    came up on ota_1 and marked itself valid there.

    Asserted here rather than re-run: a full install is ~90s and leaves the
    board on the other slot, which every test after this one would inherit."""
    assert board.pyval("ws.updater is not None") is True
    assert board.pyval("ws.updater.available()") is True, "not an OTA build"
    assert board.pyval("__import__('moy_ota').BOARD") == "p4"
    assert board.pyval("ws.updater.update_dir") == "/moy/update"
    assert board.pyval("ws.updater.slot()") in ("ota_0", "ota_1")
    # The manifest it would fetch is this board's, not the T-Deck's -- an OTA
    # payload is an app-partition image, so the wrong one cannot boot.
    url = board.pyval("ws.updater.manifest_url('unstable')")
    assert url.endswith("/latest-p4.json"), url
    # mark_valid ran; without it the bootloader reverts every OTA.
    assert any("marked app valid" in ln for ln in board.lines), \
        "no mark_valid at boot -- rollback would undo every update"


def test_the_rollback_confirm_comes_from_the_frame_loop(board):
    """The confirm has to be worth something, and where it is made decides that.

    Made on the boot path it certifies an image that has never drawn a pixel --
    a board that boots to a black screen (this project shipped one, #56) would
    confirm itself and lose the safety net. So it waits for a painted frame AND
    for the loop to keep running afterwards, and this asserts the loop is what
    actually got there on a live board.

    The full pass -- install, reboot into the new slot, and a reset inside the
    confirm window to force the bootloader to revert -- was run on glass
    2026-08-02: 15/15, both directions, banner and verdict correct each time.
    Not re-run here because one install is ~2min and leaves the board on the
    other slot, which every later test would inherit."""
    # The kernel's loop makes it (native/moy_kernel/moy_loop.c, its two
    # thresholds pinned in tests/test_moy_loop.py) and its service upcall
    # tells the updater; the loop has run at least that many frames since.
    assert board.pyval("ws.updater.confirmed") is True
    frames = board.pyval("__import__('moy_loop').frames()")
    assert frames >= 120, "confirmed after %s loop frames" % frames
    assert board.pyval("ws._frames_drawn") >= 1


def test_a_pending_marker_becomes_a_verdict_on_this_board(board):
    """The marker round trip against the real filesystem, without a 3MB install.

    What is device-specific here is exactly what a host test cannot reach: this
    board has no SD, so `with_sd` is a plain call-through and the marker lands on
    the internal VFS -- the same path that has to hold a rollback's evidence
    across a reboot.

    The mkdir is not scaffolding, it is the firmware's own step: `update_dir`
    does NOT exist on a board whose VFS has never taken an OTA, and `finish()`
    and the download opener each create it before their first write. This test
    writes the marker directly, so it has to do the same -- without it the test
    passes only on a board that happens to have downloaded an update earlier,
    and fails on any freshly-flashed one (a full cable flash wipes the VFS).
    That is exactly how it failed on 2026-08-15 after the canvas-flip reflash.

    AND IT HAS TO BE ITS OWN SHORT COMMAND, not a line of the snippet below.
    `pyexec` uploads a multi-line snippet in chunks; `cmd` sends one line. A
    real `os.mkdir` is a flash erase+write that stalls the frame loop for long
    enough that, with PERF diag streaming (the module fixture sends `diag 1`),
    a diag line interleaves into the chunk exchange and the reader parses the
    fragment as a serial COMMAND instead of Python -- surfacing as the baffling
    `PY ERR SyntaxError: invalid syntax for integer with base 10`. It reproduces
    only when the mkdir actually creates something: with the directory already
    present mkdir raises EEXIST immediately, never stalls, and the upload is
    clean. Any device call that blocks on flash belongs outside a chunked
    upload for the same reason."""
    board.cmd("py (lambda: (__import__('os').mkdir(ws.updater.update_dir), 1)[1])()",
              wait_for="PY", timeout=20)     # EEXIST if it is already there
    assert board.pyexec(
        "import json\n"
        "f = open(ws.updater._pending_path(), 'w')\n"
        "f.write(json.dumps({'slot': 'nowhere', 'label': 'v99'}))\n"
        "f.close()\n"
        "V = ws.updater.boot_check()\n"), \
        "the device raised while writing the pending marker"
    verdict = board.pyval("eval('V', ws._g)")
    assert verdict[0] == "rolled_back", verdict
    # Reading it must NOT consume it: an image that reports and then dies has to
    # still have its marker on the boot after the rollback.
    listing = board.pyval("__import__('os').listdir(ws.updater.update_dir)")
    assert "pending.json" in listing, listing
    board.pyexec("import os\n"
                 "os.remove(ws.updater._pending_path())\n"
                 "ws.updater.boot_verdict = None\n"
                 "ws._notice = None\n")
    assert "pending.json" not in board.pyval(
        "__import__('os').listdir(ws.updater.update_dir)")


# -- the browser console baked into the image (moy_web) -----------------------


def test_the_cart_store_is_the_card_when_one_mounted(board):
    on_glass.cart_store_follows_the_card(board)


def test_the_web_console_is_baked_into_this_image(board):
    on_glass.web_console_is_baked_into_the_image(board)


def test_the_console_is_served_out_of_the_firmware_image(board):
    """The console this board hands a browser comes out of its own image --
    the megabyte the page cannot boot without, read straight from flash."""
    assert board.pyexec(
        "import moy_webhost\n"
        "H = moy_webhost.WebHost('/moy/carts')\n"
        "R = H.handle_http('GET', '/worker.js', b'')\n"
        "HEAD, BODY = R.split(bytes((13, 10, 13, 10)), 1)\n")
    head = board.pyval("eval('HEAD', ws._g)")
    assert b"Content-Encoding: gzip" in head, head
    assert b"200 OK" in head and b"no-store" in head
    assert board.pyval("eval('bytes(BODY[:3])', ws._g)") == b"\x1f\x8b\x08"
    note = board.pyval("eval('H.source_note()', ws._g)")
    assert "baked into this firmware" in note, note


def test_a_lua_cart_runs_and_exits(board):
    """The Lua tier reaches every board by default; pin it with a real run."""
    on_glass.cart_runs_and_exits(board, "sakura lua", title="Sakura Lua",
                                 door="shell", clear=3)


def test_a_cart_runs_and_exits(board):
    """The desk tier's door: ws.exit() out, not the cart-quit flag. A `run`
    from the tour's picker arrangement opens the cart under it, where the
    first draft's cart-quit exit did not pop -- so this board pins the launch
    and the shell's own close, while the fullscreen tiers pin the kid-facing
    flag."""
    on_glass.cart_runs_and_exits(board, "star", door="shell", clear=3)


@pytest.mark.parametrize("spec,title", on_glass.VM_FREE_SEEDS)
def test_a_vm_free_frame_makes_no_crossing(board, spec, title):
    on_glass.a_vm_free_frame_makes_no_crossing(board, spec, title, door="shell",
                                               clear=3)


@pytest.mark.parametrize("spec,title", on_glass.VM_FREE_SEEDS)
def test_a_front_run_makes_no_crossing(board, spec, title):
    on_glass.a_front_run_makes_no_crossing(board, spec, title, clear=3)


# The compiled half of the same check: the blit fixture's frames run in the
# kernel's Player on the engine's thread, and neither its frames nor its run's
# books since launch hold an APP, SERVICE or REFUSED crossing; the hello
# fixture reads its greeting from its own folder at _init, in C.
@pytest.mark.parametrize("which", ("blit", "hello"))
def test_a_compiled_frame_makes_no_crossing(board, wasm_carts, which):
    title = wasm_carts[which]
    on_glass.a_vm_free_frame_makes_no_crossing(board, title.lower(), title,
                                               door="shell", clear=3, runtime="wasm")


# -- the engine's radio guards (docs/wasm_tier_plan_2026-09.md, phase 1) ------
# LAST in the file: both bring WiFi up (released again) and the second starts
# BLE, and the WiFi driver keeps its internal RAM for the rest of the boot.


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
    on_glass.a_ctrl_c_ends_a_front_run(board, clear=3)


# LAST in the file: twenty soft resets end on a freshly started VM.
def test_twenty_soft_resets_leave_psram_flat(board):
    on_glass.soft_resets_leave_psram_flat(board, n=20)
