"""On-glass Guition JC3248W535 console tests (#202): drive the real board.

Gated: they run only when MOYBYTE_GUITION_PORT is set (e.g.
`MOYBYTE_GUITION_PORT=/dev/ttyACM1 .venv/bin/python -m pytest
tests/test_guition_on_glass.py -v`) -- the T-Deck suite's shape pointed at the
third board, per the port checklist's stage-6 exit criterion
(docs/board_ports_2026-08.md).

DELIBERATELY NO RESET, for the T-Deck's reason and by the same route: this
board's `[serial]` block declares `attach_only` because its USB-Serial/JTAG is
ON the SoC, so a reset tears the USB device down under the open handle and the
reader looks exactly like a dead board. The suite ATTACHES to the running
desktop, asserts, and leaves the console on the launcher.

Tests share the session in file order. The bodies every fullscreen-tier board
shares live in tests/on_glass.py; what is here is what is this board's.
"""

import pytest

import on_glass
from on_glass import ROOT

PORT, pytestmark = on_glass.gate("MOYBYTE_GUITION_PORT", "the Guition S3")


@pytest.fixture(scope="module")
def board():
    with on_glass.session(
            PORT,
            board_dir=ROOT / "firmware" / "guition_jc3248w535") as b:
        yield b


# The engine's idle cost (docs/wasm_tier_plan_2026-09.md, guard 1). FIRST in
# the file on purpose: the comparison is against a fresh boot, and the wasm
# block at the end brings the radios up. Measured 2026-09-25 on a module-free
# image of the same tree, at the launcher right after boot: (free, largest)
# internal SRAM -- less EXFAT_SRAM, what FatFS's exFAT build holds in internal
# SRAM at the idle desk (2026-10-05, the same image and a fresh boot:
# 93091 free with it, 93731 without), so the guard keeps measuring the engine's
# own cost.
EXFAT_SRAM = 640
# And less KERNEL_SRAM, the kernel's static share (native/moy_kernel: its
# panic wrapper in IRAM and its statics), which every console image carries
# and docs/native_kernel_2026-09.md section 6.1 bounds on its own: measured
# 2026-10-06 on this board, the internal heap an image that takes
# the module has against the same tree denying it (the worse of two fresh
# boots: 93067 and 93131 free without it, 92039 with it).
# Plus the glass's statics (native/moy_glass: its tables' roots and the
# present engine), 56 bytes of .dram0.bss by the link map against dev
# 82144715, 2026-10-07.
# And the links' statics (native/moy_net/moy_link.c: the link's ring header,
# its latch and its flag), 40 bytes of .bss and .data by the objects' sizes,
# 2026-10-07; the port's espnow module they replace held its ring in the heap.
# And the WiFi driver's (native/moy_net/moy_wifi.c: its state, latch and
# connect flag), 56 bytes of .bss and .data by the object's sizes, 2026-10-07.
# Plus input's statics (native/moy_input: the kernel table's latches, the
# drivers' state, the kernel's I2C bus, which the carve compiled as a stub, and
# NimBLE's bond cache, ble_store_config, in place of MicroPython's bluetooth),
# 1024 bytes of .dram0 by the link map against dev 8ecf490a, 2026-10-07.
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
# and the line buffer are PSRAM), 488 bytes of idle internal heap measured
# 2026-10-08 against the same tree's dev image on a fresh boot.
# Less what moy_alloc's registry gave back: its nodes moved to PSRAM beside
# their buffers (native/moy_alloc), 580 bytes of idle internal heap measured
# 2026-10-08 against the same tree's image before the move, fresh boots.
# And first light's, the board's dev-channel words' and the kernel's HITCH and
# LOOP lines', 92 bytes of idle internal heap measured 2026-10-08 against the
# same tree before them, fresh boots.
# And the cart path's statics (native/moy_play: the run's pointer, whose state
# is PSRAM, and the binding's two root pointers; native/moycore: the frame's
# split, which replaced the binding's own), 8 bytes of internal heap by the
# heap's total against dev 502be7fd, fresh boots, 2026-10-08.
KERNEL_SRAM = 1092 + 56 + 40 + 56 + 1024 + 4 + 5 + 38 + 4 + 8 + 4 + 488 - 580 + 92 + 8
# And less CARD_SRAM, the card volume's remaining internal SRAM: the SPI3 bus
# moy_sd initialises and keeps, and its sdspi device and driver structs (the
# store's FATFS, read cache and card state are PSRAM): measured 2026-10-07 on
# this board, the same tree with tf_card mounting the card volume and with it
# left out, fresh boots at the launcher (91747 free with it, 93551 without,
# twice each).
CARD_SRAM = 1804
WASM_IDLE_BASELINE = (95507 - EXFAT_SRAM - KERNEL_SRAM - CARD_SRAM, 55296)
WASM_BOARD_DIR = ROOT / "firmware" / "guition_jc3248w535"


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
# less a margin -- the hello cart's measured on 2026-09-25, the blit fixture's
# on 2026-09-28 with its frames going to the glass from the cart's memory; the
# measurements are the owner's to post, and native/moy_wasm/README.md states
# the per-board ceiling.
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
# cores, when they were set (2026-09-29); the figures are #158's. The cart
# stays installed as it ships.
JET_HALF_FPS_FLOOR = 16
JET_FULL_FPS_FLOOR = 12


def test_the_jet_showcase_holds_its_floor_at_half_width(board):
    on_glass.jet_holds_its_floor(board, WASM_BOARD_DIR, JET_HALF_FPS_FLOOR,
                                 width="half", shading="phong")


def test_the_jet_showcase_holds_its_floor_at_full_width(board):
    on_glass.jet_holds_its_floor(board, WASM_BOARD_DIR, JET_FULL_FPS_FLOOR,
                                 shading="phong")


# Doom, built by the recipe (experiments/wasm_aot/doom/): its load needs the
# linear memory's block, the pool and the text at once, more PSRAM than this
# board -- the floor board -- has free in its 3 MB cart-runtime reserve with
# the shell resident, even on a fresh boot, so its fit check refuses it. The
# cart is not installed here, and this skips, saying so, until the reserve or
# the cart changes.
DOOM_SHORT = ("Doom does not fit this board's 3 MB cart-runtime reserve with "
              "the shell resident: its fit check refuses it (measured "
              "2026-09-26; experiments/wasm_aot/doom/README.md)")


def test_doom_frames_match_the_host(board):
    on_glass.doom_frames_match_the_host(board, WASM_BOARD_DIR, short=DOOM_SHORT)


def test_state_snapshot_has_the_fullscreen_tier_shape(board):
    on_glass.fullscreen_tier_state(board)


def test_the_system_canvas_is_the_landscape_glass(board):
    """The board's one structural novelty (#202): the first FULLSCREEN-tier
    console whose system canvas (480x320 landscape, rotated in moy_axs's band
    copy) is not its game canvas (320x240). Assert both sizes and the
    viewport seam through the live console."""
    line = board.cmd("py (ws.sys_canvas.w, ws.sys_canvas.h, ws.canvas.w, ws.canvas.h)",
                     wait_for="PY ")
    assert line == "PY (480, 320, 320, 240)", line
    # composite_game's placement: 1:1, centred both ways.
    line = board.cmd("py ws.wm.viewport()", wait_for="PY ")
    assert line == "PY (80, 40, 1)", line


def test_every_system_app_claims_exactly_one_cart(board):
    on_glass.every_app_claims_one_cart(board)


def test_py_probe_reaches_the_live_console(board):
    on_glass.py_probe_reaches_the_console(board)


def test_a_stale_handle_is_refused_loudly(board):
    on_glass.stale_handle_is_refused_loudly(board)


def test_diag_toggle_roundtrips(board):
    on_glass.diag_toggle_roundtrips(board)


def test_swipe_rides_the_real_pointer_feed(board):
    on_glass.home_shelf_fling(board, 400, 80, 160)


def test_a_failed_flush_is_recovered_from(board):
    """#205. The QSPI transport's failure exits, each taken ON PURPOSE:
    `moy_axs.fault(kind)` arms one for the next flush, the swipe supplies the
    flushes. A recovered failure is COUNTED once (timeouts for the two
    deadline shapes, errs for the two the transport refuses), the frames
    after it ship, and the console keeps drawing -- the whole point being
    that no natural run in three weeks ever took one of these paths."""
    lcd = "ws.comp._lcd"
    for kind, counter in (("FAULT_LATE", 6), ("FAULT_DROP", 6),
                          ("FAULT_QERR", 7), ("FAULT_HDR", 7)):
        before = board.pyval(lcd + ".pump_stats()", strict=True)
        flushes0 = board.pyval(lcd + ".stats()", strict=True)[0]
        board.pyexec("%s.fault(%s.%s)" % (lcd, lcd, kind), strict=True)
        board.swipe(400, 160, 80, 160, frames=20)
        board.drain(1.0)
        after = board.pyval(lcd + ".pump_stats()", strict=True)
        assert after[counter] == before[counter] + 1, (kind, before, after)
        other = 7 if counter == 6 else 6
        assert after[other] == before[other], (kind, before, after)
        # The frame after the failed one, and the ones after that, went out.
        flushes1 = board.pyval(lcd + ".stats()", strict=True)[0]
        assert flushes1 >= flushes0 + 2, (kind, flushes0, flushes1)
        board.swipe(80, 160, 400, 160, frames=20)
    st = board.state()
    assert st["stack"][-1] == "launcher", st["stack"]
    assert not st.get("cart_error"), st["cart_error"]


def test_a_cart_runs_and_exits(board):
    on_glass.cart_runs_and_exits(board, "star")


def test_a_lua_cart_runs_and_exits(board):
    """moycore on the third board: the Lua tier is supposed to reach every
    board by default (the whole point of the shared native staging), so pin it
    with a real run, not just an import."""
    on_glass.cart_runs_and_exits(board, "sakura lua", title="Sakura Lua")


@pytest.mark.parametrize("spec,title", on_glass.VM_FREE_SEEDS)
def test_a_vm_free_frame_makes_no_crossing(board, spec, title):
    on_glass.a_vm_free_frame_makes_no_crossing(board, spec, title)


# The compiled half of the same check: the blit fixture's frames run in the
# kernel's Player on the engine's thread, and neither its frames nor its run's
# books since launch hold an APP, SERVICE or REFUSED crossing.
def test_a_compiled_frame_makes_no_crossing(board, wasm_carts):
    title = wasm_carts["blit"]
    on_glass.a_vm_free_frame_makes_no_crossing(board, title.lower(), title,
                                               runtime="wasm")


def test_idle_screen_blank_and_wake(board):
    on_glass.idle_blank_and_wake(board)
    on_glass.idle_timeout_restored(board)


def test_mem_reports_the_heap(board):
    on_glass.mem_reports_the_heap(board)


def test_the_saver_comes_and_its_wake_repaints(board):
    on_glass.idle_saver_and_wake(board)


def test_internal_flash_commits_under_a_running_cart(board):
    ms = on_glass.internal_flash_commits_under_a_cart(board)
    print("\nlongest internal-flash commit under a cart: %d ms" % ms)


def test_the_frame_is_the_kernels(board):
    on_glass.the_frame_is_the_kernels(board)


def test_wifi_is_off_at_rest(board):
    on_glass.wifi_is_off_at_rest(board)


def test_wifi_status_is_readable(board):
    on_glass.wifi_status_is_readable(board)


def test_the_kernel_verifies_a_signed_manifest(board):
    on_glass.the_kernel_verifies_a_signed_manifest(board)


def test_the_internal_volume_is_the_kernels(board):
    on_glass.the_internal_volume_is_the_kernels(board)


def test_draw_gates_are_installed(board):
    on_glass.draw_gates_are_installed(board)


def test_draw_gates_take_the_traffic(board):
    on_glass.draw_gates_take_the_traffic(board)


def test_the_cart_store_is_the_card_when_one_mounted(board):
    on_glass.cart_store_follows_the_card(board)


def test_the_web_console_is_baked_into_this_image(board):
    on_glass.web_console_is_baked_into_the_image(board)


def _skip_unparked(board, why):
    """Skip this test -- but not with the glass left PARKED.

    `web` parks the console on the connection screen before anyone knows
    whether the batch will land, and both of this test's early exits are taken
    after that. A bare skip therefore ended the session with a console that
    draws nothing, and the suite shares ONE board in file order: every later
    test read an idle board, and `test_perf_line_is_the_one_format` failed with
    "no PERF lines in 5s" -- a real failure, of the previous test's tidying.
    The finally at the bottom does this for the paths that reach it."""
    board.cmd("py ws.stop_web_console(); print('WEBOFF')",
              wait_for="WEBOFF", timeout=8.0)
    # A push that reached the board leaves its handler rescanning the store,
    # and on this board's TF card that is a minute during which nothing
    # answers. Skip only once the console is answering again, or the next
    # test reads a board that looks dead.
    board.cmd("state", wait_for="STATE ", timeout=120.0)
    pytest.skip(why)


def test_sync_push_writes_the_store_and_the_shelf_follows(board):
    """The 3.4 sync RPC against the REAL board: bring the webhost up, POST a
    batch from this machine over the LAN, and read the result back over
    serial -- the file on the TF card, the cart on the LIVE launcher (the
    shelf rescan, with no reboot anywhere), then a dc batch that removes it
    again. Skips rather than fails when the bench has no shared network:
    everything up to the HTTP hop is the other tests' job.

    Runs LAST-ish on purpose: it flips WiFi + the webhost on, and puts both
    back the way it found them."""
    import json as _json
    import urllib.request

    line = board.cmd("web", wait_for="WEB ", timeout=30.0)
    if line is None or "http://" not in line:
        _skip_unparked(board,
                       "webhost did not come up (no wifi on this bench): %r"
                       % line)
    # Since #197 the `web` line is the PAIRED url -- the pin rides ?pin= and
    # every write batch must carry it (a bare batch is the 403 the pin exists
    # to give). The glass is parked on the connection screen while this runs.
    paired = "http://" + line.split("http://", 1)[1].split()[0].rstrip("/")
    pin = paired.split("pin=", 1)[1].split("&")[0] if "pin=" in paired else None
    url = paired.split("?", 1)[0].rstrip("/")
    try:
        batch = _json.dumps({"v": 1, "pin": pin, "ops": [
            {"p": "pytest_sync.moy/manifest.json",
             "t": '{"title": "Pytest Sync", "type": "game", "main": "main.py"}'},
            {"p": "pytest_sync.moy/main.py",
             "t": "def _draw():\n    cls(11)\n"},
        ]}).encode()
        r = urllib.request.urlopen(urllib.request.Request(
            url + "/sync", data=batch,
            headers={"Content-Type": "application/json"}), timeout=15)
        doc = _json.loads(r.read())
    except OSError as exc:
        _skip_unparked(board,
                       "board url unreachable from this machine: %s" % exc)
    try:
        assert doc == {"ok": 2, "err": []}, doc
        if pin:
            # The gate itself, on glass: the same batch without the pin.
            import urllib.error
            try:
                urllib.request.urlopen(urllib.request.Request(
                    url + "/sync",
                    data=_json.dumps({"v": 1, "ops": []}).encode(),
                    headers={"Content-Type": "application/json"}), timeout=15)
                assert False, "a pinless batch was accepted"
            except urllib.error.HTTPError as exc:
                assert exc.code == 403, exc.code
            # ...and the READ half is gated too since 2026-08-25: this board's
            # store is a child's work, and it used to be there for the asking
            # to anything on the same WiFi. The boot assets stay open, because
            # the page is what asks for the pin.
            for path in ("/carts.json", "/files.json"):
                try:
                    urllib.request.urlopen(url + path, timeout=20)
                    assert False, "%s answered without a pin" % path
                except urllib.error.HTTPError as exc:
                    assert exc.code == 403, (path, exc.code)
            r = urllib.request.urlopen(url + "/carts.json?pin=" + pin,
                                       timeout=60)
            assert "pytest_sync.moy/main.py" in _json.loads(r.read())
            r = urllib.request.urlopen(url + "/sync", timeout=15)
            assert _json.loads(r.read()) == {"sync": 1}, \
                "the capability marker must stay open, or no page finds a board"
        board.drain(0.5)
        line = board.cmd(
            "py print('SYNCED=' + repr(any((c.get('path') or '')"
            ".endswith('pytest_sync.moy') for c in ws.launcher.items)))",
            wait_for="SYNCED=", timeout=8.0)
        assert line is not None and "SYNCED=True" in line, line
        # ...and the dc op takes it back off the card AND the shelf.
        batch = _json.dumps({"v": 1, "pin": pin,
                             "ops": [{"p": "pytest_sync.moy",
                                      "dc": 1}]}).encode()
        r = urllib.request.urlopen(urllib.request.Request(
            url + "/sync", data=batch,
            headers={"Content-Type": "application/json"}), timeout=15)
        assert _json.loads(r.read())["ok"] == 1
        board.drain(0.5)
        line = board.cmd(
            "py import os; print('GONE=' + repr("
            "any((c.get('path') or '').endswith('pytest_sync.moy') "
            "for c in ws.launcher.items) is False "
            "and 'pytest_sync.moy' not in os.listdir(ws.carts_root)))",
            wait_for="GONE=", timeout=8.0)
        assert line is not None and "GONE=True" in line, line
    finally:
        # Leave the board as found: unpark the glass and stop the host.
        # stop_web_console, not toggle -- a toggle would START a host that
        # died under the parked screen (the reason the verb exists).
        board.cmd("py ws.stop_web_console(); print('WEBOFF')",
                  wait_for="WEBOFF", timeout=8.0)


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


def test_the_kernel_lit_the_logo_before_any_vm(board):
    on_glass.the_kernel_lit_the_logo(board)


def test_heapcaps_mem_and_hush_are_the_kernels_words(board):
    on_glass.board_words_are_the_kernels(board)


def test_loop_is_the_kernels_line_with_every_stage(board):
    on_glass.loop_line_is_the_kernels(board)


# LAST in the file: twenty soft resets end on a freshly started VM.
def test_twenty_soft_resets_leave_psram_flat(board):
    on_glass.soft_resets_leave_psram_flat(board, n=20)
