"""The routing greps: which shared body a board takes, and that a fallback
rung is absent.

Everything here reads SOURCE TEXT, and stays a grep on purpose: each pin is
that a board still CALLS a shared helper, that a mechanism is defined ONCE
(a body count, an absence), or a build/C fact no host test can execute. The
behaviour behind every one of these is executed elsewhere -- the docstrings
name where -- and a grep that pinned an implementation line an executable
test already covers was deleted from this file, not kept beside it.

The T-Deck (`ROOT`) is the main subject: it is the board whose boot the other
console boards' spines were carved from. `tests/board_source.runtime_text`
resolves a board's module PLUS every shared body it delegates to, so a grep
keeps asking its question after a body moves into a spine.
"""

import re
from pathlib import Path

from board_source import runtime_text

from tools import board_config


ROOT = Path("firmware/lilygo_t_deck_plus_mainline")
DEVICE = Path("device")
NATIVE = Path("native")
PATCHES = Path("patches")
_REPO = Path(__file__).resolve().parent.parent


def _staged():
    """The shared modules a FRESH T-Deck build freezes, by destination name --
    `build.sh` stages every `runtime/*.py` except the files `board.toml`
    denies, so the question is asked of the staged set."""
    from tools.board_config import staged_modules
    return staged_modules(_REPO / ROOT, _REPO)


def _device_backend_src():
    """The device backend's source, as the greps below mean it: the board's
    `moy_runtime` and every spine it delegates to, `device_api` (the device
    re-export home of make_api), the one `cart_api` body, and `device_boot`
    -- read through `_staged()` on purpose, which asserts the spine really is
    staged onto this board."""
    return "\n".join((
        runtime_text(ROOT / "modules" / "moy_runtime.py"),
        (DEVICE / "device_api.py").read_text(encoding="utf-8"),
        Path("runtime/cart_api.py").read_text(encoding="utf-8"),
        _staged()["device_boot.py"].read_text(encoding="utf-8"),
    ))


def _panel_src():
    """This board's panel backend: its compositor SUBCLASS plus its native
    driver. The shared body is `device/banded_panel.py`, executed by
    tests/test_banded_panel.py."""
    return (
        (ROOT / "modules" / "tdeck_panel.py").read_text(encoding="utf-8"),
        (ROOT / "native" / "moy_lcd" / "modmoy_lcd.c").read_text(encoding="utf-8"),
    )


def _flush_src():
    """The SHARED banded-flush engine (native/moy_flush): the core-0 feeder,
    the bounce slots and their pacing, the kick/drain handoff and the PUMP
    meters. The panel modules keep their transports."""
    return (NATIVE / "moy_flush" / "moy_flush.c").read_text(encoding="utf-8")


# -- the boot spine's services --------------------------------------------------

def test_ota_updater_wired_into_run_desktop_with_rollback_confirm():
    """The updater is CONSTRUCTED by this board (its SD gate is this board's
    alone); the boot verdict and the rollback confirm are the spine's OtaHealth,
    executed in tests/test_ota_health.py and tests/test_device_boot.py."""
    runtime = _device_backend_src()

    assert "import moy_ota" in runtime
    # The gate and the staging dir both FOLLOW THE STORE: a T-Deck with no card
    # keeps a writable store on internal flash, and an updater that went on
    # bracketing the SD bus and staging onto /sd/update would be aimed at a
    # card that is not there.
    assert "ws.updater = moy_ota.OtaUpdater(ws._with_sd, update_dir=update_dir)" in runtime
    assert "(None if self.on_sd else FLASH_UPDATE_DIR)" in runtime
    assert "ws.updater.mark_valid()" not in runtime


def test_ota_online_wired_and_console_has_online_flow():
    """run_desktop hands the wifi service to the updater for online updates;
    the rows and phases are executed in tests/test_remote_update.py and
    tests/test_web_update.py."""
    runtime = _device_backend_src()
    assert "ws.updater.set_wifi(ws.wifi, go_online=lambda: autoconnect_wifi(ws.wifi))" in runtime


def test_micropython_native_sd_shares_display_spi_host():
    """The live SD path attaches the card to the host esp_lcd already
    initialized (moy_sd) instead of re-running spi_bus_initialize like
    machine.SDCard, which hangs the shared bus once the panel is up. The Python
    half is executed in tests/test_moybyte_sd.py; the C module and the board's
    session routing are pinned here."""
    mod = (NATIVE / "moy_sd" / "modmoy_sd.c").read_text(encoding="utf-8")
    cmake = (NATIVE / "moy_sd" / "micropython.cmake").read_text(encoding="utf-8")
    runtime = _device_backend_src()

    # Native module attaches (init_device) rather than re-initializing the bus.
    assert "MP_REGISTER_MODULE(MP_QSTR_moy_sd" in mod
    assert "sdspi_host_init_device" in mod
    assert "sdmmc_read_sectors" in mod and "sdmmc_write_sectors" in mod
    assert "target_link_libraries(usermod INTERFACE usermod_moy_sd)" in cmake

    # The SD session is wrapped so it drains any in-flight panel DMA first (the
    # double-buffer SD-vs-panel mutual exclusion), but still delegates to the
    # native live-mount path.
    assert "_ws._with_sd = store.session" in runtime
    assert "return moybyte_sd.with_sd_live(fn)" in runtime
    # can_manage defaults to "the store root is known" inside the shared
    # console.wire_workstation_core (the runtime hands it carts_root).
    assert "wire_workstation_core(ws, moy_carts, carts_root, make_api" in runtime
    assert "ws.can_manage = False" not in runtime


def test_micropython_touch_and_idle_cursor():
    """The GT911 driver is constructed by run_desktop and fed into the shared
    pointer through the spine's `apply_touch`, and the pointer ticks in the
    SHARED FrameLoop. The driver is executed in tests/test_device_input.py,
    apply_touch in tests/test_device_boot.py, the Pointer's auto-hide in
    tests/test_desktop_shell.py."""
    runtime = _device_backend_src()
    shell = (ROOT / "modules" / "moybyte_shell.py").read_text(encoding="utf-8")
    boot_spine = Path("runtime/device_boot.py").read_text(encoding="utf-8")

    assert "touch = Touch(canvas.w, canvas.h" in runtime
    assert "apply_touch(touch, pointer)" in runtime
    assert "pointer.tick(now)" in boot_spine
    assert "loop = FrameLoop(" in runtime
    # Touch calibration bring-up mode (serial-only, flush-once): a rung of the
    # board's MODES ladder, reached as `s.MODE = "touch"; s.main()`.
    assert '"touch"' in shell and "MODES = (" in shell


def test_micropython_cart_textmode_flips_keyboard_ascii_raw():
    """The per-frame CALL that derives the keyboard mode from the cart's
    textmode request lives in Player.tick; the mode flip itself is executed in
    tests/test_cart_textmode.py and tests/test_tdeck_input.py."""
    player = (Path("runtime") / "player.py").read_text(encoding="utf-8")
    assert "ws._sync_cart_text_mode()" in player


def test_kid_mode_gates_diag_frame_eaters():
    """Settings -> PERF DIAG gates the felt diag costs on this board's loop:
    the live echo, the periodic diag->SD write (which also needs DIAG SD LOG),
    and the ring flush on cart exit. The toggle registry is executed in
    tests/test_settings_toggles.py; the flush gate in tests/test_device_diag.py."""
    runtime = _device_backend_src()
    assert '_live = bool(getattr(ws, "diag_live", False))' in runtime
    assert "diag.ECHO_LIVE = _live" in runtime
    assert "_cart_prev[0] and not _cart_now" in runtime    # cart-exit flush
    assert 'getattr(ws, "diag_sd", False)' in runtime      # timer flush gated


def test_input_poller_wired_with_gil_release_patch():
    """The poller only isolates a stall when machine.I2C frees the GIL across
    its blocking legacy-driver transaction wait: run_desktop prefers the poller
    and keeps the synchronous path as a live fallback, and the build applies
    the GIL patch. The poller's body is executed in tests/test_tdeck_input.py.

    The patch itself is an INLINE sed in this board's build.sh rather than a
    `moybyte_patch_*` of the shared lib, so it has no patch-harness twin; its
    guard stays a grep until it moves into the lib."""
    runtime = _device_backend_src()
    build = (ROOT / "build.sh").read_text(encoding="utf-8")

    assert "MOY_INPUT_POLLER = True" in runtime      # default ON, revert w/o rebuild
    assert "poller.consume()" in runtime
    assert "keyboard.poll()" in runtime              # the synchronous path stays live
    assert "poller thread died -> synchronous fallback" in runtime
    assert "touch._source = poller.consume_touch" in runtime
    assert "keyboard._poller_owned = True" in runtime
    # the GIL-release patch: applied by default, revertable, wraps cmd_begin
    assert "Moybyte #69 GIL" in build, "the GIL-release patch is not applied"
    assert "MP_THREAD_GIL_EXIT();" in build
    assert "i2c_master_cmd_begin(" in build
    assert "MP_THREAD_GIL_ENTER();" in build


def test_touch_holds_a_held_finger_between_gt911_samples():
    """The hold/stale/bound contract is ONE copy in the shared gt911 core
    (executed in tests/test_gt911_core.py); the driver must still ROUTE its
    no-news pass through it."""
    inp = (DEVICE / "device_input.py").read_text(encoding="utf-8")
    assert "return self._hp.hold()" in inp


def test_hitch_logger_wired():
    """The HITCH line's format is executed in tests/test_device_diag.py; this
    board's loop must still CALL it with every stage, and must not write the
    diag ring to SD at 5s during play."""
    runtime = _device_backend_src()
    assert '_diag_hitch(diag, ws, comp, elapsed, _t["kbd"], _t["inp"], _t["sb"],' in runtime
    # the diag->SD write (measured 80-120ms) must NOT run at 5s during play
    assert "20000 if ws.cart is not None else 5000" in runtime


def test_micropython_offline_diag_wiring():
    """Every diag line's FORMAT is executed in tests/test_device_diag.py,
    tests/test_moybyte_diag.py and tests/test_banded_panel.py; what this board
    owes is the CALLS, and the absence of the perf formatter it used to carry
    (one PERF line, one producer, every board)."""
    shell = (ROOT / "modules" / "moybyte_shell.py").read_text(encoding="utf-8")
    runtime = _device_backend_src()
    device_diag = (DEVICE / "device_diag.py").read_text(encoding="utf-8")

    assert "_dump_diag" not in shell            # the boot hook is gone with the prefetch
    assert "_diag_flush(diag, ws)" in runtime
    assert "_diag_perf_sample(" not in runtime
    assert "def _diag_perf_sample(" not in device_diag
    assert 'diag.ring("PERF", line[5:])' in runtime
    assert "_diag_drawbrk(diag, ws)" in runtime
    assert "_diag_draw2(diag, ws)" in runtime
    assert "_diag_pump(diag, comp)" in runtime
    assert "_diag_i2cstat(diag, keyboard, touch)" in runtime
    # Existing diagnostics routed through diag (printed AND persisted): the
    # frame-error trace and the in-cart crash.
    assert '_diag_log("frame error", exc, diag)' in runtime
    assert '_diag_log("cart error", _ce, diag)' in runtime


def test_device_wifi_wired():
    """The radio is a system service this board wires but never brings up at
    boot (WLAN reserves the internal RAM the LCD DMA needs): autoconnect is
    reached ONLY through the OTA path's go_online lambda. The service and the
    gating are executed in tests/test_wifi.py."""
    runtime = _device_backend_src()
    assert "make_wifi(moy_carts, carts_root)" in runtime   # via wire_workstation_core
    assert "go_online=lambda: autoconnect_wifi(ws.wifi)" in runtime
    assert runtime.count("autoconnect_wifi(ws.wifi)") == 1


def test_code_editor_wired_into_device_shell():
    """The console + editor cores are shared with the host (imported, not
    redefined) and the keyboard is handed to the shared console; the editor's
    save chain is executed in tests/test_code_multifile.py,
    tests/test_safe_edit.py and tests/test_journal_wiring.py."""
    runtime = _device_backend_src()
    assert "from console import Pointer, Workstation" in runtime
    assert "keyboard=keyboard" in runtime      # via wire_workstation_core


def test_both_boards_service_the_web_console_every_frame():
    """A bound listener nobody accepts on is indistinguishable from a dead one.

    Found on T-Deck glass 2026-08-16. `make_webhost` was wired to this board in
    #29, but the frame loop's web step had been a hardcoded `_t_web = 0` since
    the 2026-08 streaming sunset and nobody restored the poll. So the socket
    bound, `serving` read True, the row showed a correct address -- and every
    SYN sat in the listener's backlog of one until it timed out. The board was
    telling the truth the whole time; nothing was draining the queue.

    The tell that named it, worth keeping because it generalises: on the same
    board in the same second, port 8080 TIMED OUT while a closed port REFUSED.
    A refusal means no listener; a timeout means a listener nobody services.

    The P4 had this call from the day its own web console landed, which is why
    that board served and this one never did -- so this asserts it for BOTH,
    not for whichever one someone remembers.
    """
    # The drain is ONE helper (device_boot.poll_webhost); each board's frame
    # tail must still CALL it -- the failure this pins was exactly a tail that
    # stopped calling. That the helper actually polls, only while the host is
    # SERVING, and never breaks the frame when the transfer dies, is executed
    # in test_device_boot.py.
    for rel in ("firmware/lilygo_t_deck_plus_mainline/modules/moy_runtime.py",
                "firmware/esp32_p4_wifi6_touch_lcd_7b/modules/moy_runtime.py"):
        src = runtime_text(_REPO / rel)
        assert "poll_webhost(ws)" in src, (
            "%s never polls ws.webhost -- a bound listener with no accept() "
            "times out instead of refusing, which reads as a dead server" % rel)


# -- the panel and the flush engine ---------------------------------------------

def test_panel_flush_dmas_only_from_internal_sram():
    """The panel DMA may only read internal SRAM: a PSRAM source starves the
    SPI FIFO and clocks out garbage rows. The slots are the SHARED engine's
    (both S3 boards allocate them through moy_flush_start), so the cap is
    pinned there."""
    engine = _flush_src()
    assert "MALLOC_CAP_INTERNAL" in engine, (
        "the bounce slots must be internal-SRAM caps")
    assert "moy_flush.bounce" in engine


def test_only_the_first_band_carries_a_command():
    """Re-issuing a command mid-frame is what glitches the boundary: the banded
    flush sends RAMWR once and streams continuations (the band sequence itself
    is executed by tests/test_moy_flush_c.py)."""
    _py, c = _panel_src()
    assert "RAMWR" in c


def test_the_band_feed_runs_on_the_core0_feeder_task():
    """A band queued and then forgotten is a frame that never finishes.

    The feed is a core-0 FreeRTOS feeder which owns the whole flush -- so the
    VM-side pump plumbing must be GONE (a half-retired timer would silently
    double-feed a bounce slot) and the feeder must exist.

    It exists ONCE, in the shared engine: native/moy_flush is where the feeder,
    the handoff and the pacing live, and each panel module supplies only its
    transport hooks. That split is pinned here too -- a feeder task
    re-appearing inside a panel driver means somebody forked the protocol back
    apart.
    """
    py, c = _panel_src()
    engine = _flush_src()
    assert "xTaskCreatePinnedToCore" in engine
    assert "moy_lcd_feed" in c, "the board names its feeder task in its ops"
    assert "moy_flush_start" in c, "the panel driver must start the engine"
    assert "xTaskCreatePinnedToCore" not in c, (
        "the feeder belongs to moy_flush; a panel driver growing its own has "
        "forked the handoff protocol back into two copies")
    assert "isr_cpu_id" in c, "the done-ISR must land on the feeder's core"
    assert "moy_flush_band_done_from_isr" in c, (
        "the done-ISR's counting/wake half is the engine's, static inline so "
        "the callback keeps its own IRAM placement")
    # The Python compositor no longer feeds anything: no timer, no poke export.
    assert "self.pump_if_pending" not in py
    assert "machine import Timer" not in py


def test_a_stop_that_is_not_acknowledged_frees_nothing():
    """`moy_flush_stop()` used to wait ~100ms for the feeder, give up SILENTLY,
    and then free the bounce slots and reset the bookkeeping anyway -- with the
    task handle still non-NULL, so `moy_flush_band_done_from_isr` could notify a
    task mid-delete against slots that had just been freed. It was the only wait
    in the engine with no latch and no counter, and both boards' deinit comments
    asserted a guarantee it did not make.

    So: a bound past the frame deadline (a timed-out frame is still unwinding
    and giving up on it buys nothing), and on failure NOTHING is handed back --
    the free and the reset must sit AFTER the early return, the exit latch stays
    armed, and start() refuses to re-arm over the zombie.

    Stays a source pin: driving a feeder that never acknowledges through the C
    harness (tests/moy_flush_harness/) is a scenario the harness does not have,
    and the ORDER of a return against a free is what this checks."""
    engine = _flush_src()
    body = engine[engine.index("bool moy_flush_stop(void)"):]
    body = body[:body.index("\nvoid moy_flush_reset")]
    assert "MOY_FLUSH_STOP_TIMEOUT_MS" in body, "the bound must be a named fence"
    fail = body.index("moy_flush.stop_fails++")
    give_up = body.index("return false;", fail)
    assert give_up < body.index("moy_flush_free_bounce()"), (
        "a stop that gave up must return BEFORE freeing memory an ISR may "
        "still touch")
    assert "task_exit = false" not in body[:give_up], (
        "the exit latch stays armed so the feeder leaves when it can")
    header = (NATIVE / "moy_flush" / "moy_flush.h").read_text(encoding="utf-8")
    assert "MOY_FLUSH_STOP_TIMEOUT_MS ((MOY_FLUSH_TIMEOUT_US / 1000)" in header
    assert "moy_flush.stop_fails" in engine and "stop_fails)" in engine, (
        "the counter must reach the pump_stats tuple -- a failure nobody can "
        "read is the one that gets explained away")
    start = engine[engine.index("bool moy_flush_start"):]
    assert "moy_flush.task != NULL && moy_flush.task_exit" in start[:start.index("s_ops = ops;")], (
        "start() must refuse to re-arm over a feeder that never stopped")
    # ...and the two boards' deinit must honour the answer, which is what makes
    # their comments true: the transport may not go away under a live feeder.
    for mod in (ROOT / "native" / "moy_lcd" / "modmoy_lcd.c",
                Path("firmware/guition_jc3248w535/native/moy_axs/modmoy_axs.c")):
        src = mod.read_text(encoding="utf-8")
        assert "if (!moy_flush_stop()) {" in src, (
            "%s deinit ignores a failed stop" % mod)


def test_the_guition_transport_cannot_leak_a_queue_slot_or_leave_cs_low():
    """#205, pinned where the on-glass proof cannot reach (a host checkout).
    The class of leak was a retrieve loop reclaiming what an ISR had COUNTED:
    with SPI_DEVICE_NO_RETURN_RESULT there is no result to retrieve, so the
    verb that could disagree with the count must be gone with it. And every
    exit that left CS asserted -- a command's failed parameters, a frame the
    last band never closed -- reaches the one close; every VM-side verb that
    drains before it mutates feeder-owned state refuses a feeder that never
    came back."""
    src = Path("firmware/guition_jc3248w535/native/moy_axs/modmoy_axs.c").read_text(
        encoding="utf-8")
    assert "SPI_DEVICE_HALFDUPLEX | SPI_DEVICE_NO_RETURN_RESULT" in src
    assert "spi_device_get_trans_result" not in src, (
        "a retrieve loop is the leak class coming back")
    assert ".queue_size = MOY_AXS_BOUNCE_SLOTS + 2" in src, (
        "queue depth is bands the ISR has not started, never bands per frame")

    def body(sig):
        # The DEFINITION, not the forward declaration the ops struct needs.
        head = re.search(r"static %s\([^;]*\) \{" % re.escape(sig), src).start()
        return src[head:src.index("\n}\n", head)]

    assert "moy_axs_cs_close_acquired()" in body("esp_err_t moy_axs_cmd_acquired")
    end = body("void moy_axs_frame_end")
    assert "moy_flush.done < moy_flush.target" in end, "wait the queued bands out"
    assert end.index("moy_axs_cs_close_acquired()") \
        < end.index("spi_device_release_bus("), "close CS before the bus goes"
    qb = body("esp_err_t moy_axs_queue_band")
    assert "err == ESP_OK && last" in qb and "s_cs_open = false" in qb
    for verb in ("kick", "show", "set_madctl", "set_rot", "cmd_py", "fold_test"):
        vb = body("mp_obj_t moy_axs_" + verb)
        assert vb.index("moy_flush_drain()") < vb.index("moy_axs_require_idle()"), verb
    # The proof hook the on-glass suite drives, one constant per failure path.
    for k in ("FAULT_DROP", "FAULT_QERR", "FAULT_HDR", "FAULT_LATE"):
        assert "MP_QSTR_" + k in src, k


def test_the_sd_guard_is_a_nesting_depth_not_a_flag():
    """SD sessions nest, and a boolean guard's INNER close lifts the bracket
    while the outer session is still on the shared SPI host -- which is the
    Cache/MMU panic the guard exists to prevent, with nothing pointing at it.
    """
    _, c = _panel_src()
    assert "static volatile int s_sd_guard;" in c, "the guard must be a depth"
    assert "s_sd_guard++" in c and "s_sd_guard--" in c


def test_async_layer_copy_wired():
    """The GDMA layer copy is TIED to the SRAM-bounce flush: against a panel
    DMA that reads PSRAM, the PSRAM->PSRAM GDMA copy starves the SPI FIFO into
    horizontal garbage bands -- it is only safe when the panel reads internal
    SRAM. One flag must feed both, so turning bounce off turns the layer copy
    off with it. The copy's refusal path is executed in tests/test_gfx_binding.py."""
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    assert "LAYER_COPY_ASYNC = _SRAM_BOUNCE_FLUSH" in device_canvas
    assert "from moy_compositor import SRAM_BOUNCE_FLUSH" in device_canvas


def test_the_mainline_tdeck_arms_the_async_layer_copy_before_its_canvas():
    """#54 St.2 on the mainline port, where the flag has to come from elsewhere.

    `device_canvas.py` reads `LAYER_COPY_ASYNC` from `moy_compositor`, which the
    mainline build does not stage (its compositor is `tdeck_panel` over the C
    `moy_lcd`), so the import guard resolves it False. That file is STAGED from
    the shared `device/` tree and is not this board's to edit, so the flag is declared in
    `tdeck_panel` -- the module that plays `moy_compositor`'s part there, and the
    module that owns the fact the lever rests on -- and `run_desktop` assigns it
    across.

    The thing worth pinning is the ORDER. `DeviceCanvas.__init__` latches
    `_async_ok` from the module global, so an assignment that drifts BELOW the
    construction reaches nothing at all: the lever would be off, the flag would
    read True, and no diag line would contradict either. That is a silent
    failure with a green grep, which is exactly the shape a test is for.

    Only the two TRACKED board files are read -- the mainline's `modules/` is
    gitignored apart from its board-authored files, so a fresh checkout has no
    staged `device_canvas.py` to look at.
    """
    mainline = _REPO / "firmware" / "lilygo_t_deck_plus_mainline" / "modules"
    panel = (mainline / "tdeck_panel.py").read_text(encoding="utf-8")
    runtime = (mainline / "moy_runtime.py").read_text(encoding="utf-8")

    # Declared in the compositor module, as a plain module constant, so the
    # revert is one flag exactly like ASYNC_FLUSH beside it.
    assert "\nLAYER_COPY_ASYNC = True\n" in panel
    assert "\nASYNC_FLUSH = True\n" in panel

    # ... and applied onto the staged module, never edited into it.
    assign = "device_canvas.LAYER_COPY_ASYNC = tdeck_panel.LAYER_COPY_ASYNC"
    assert assign in runtime
    assert "import device_canvas" in runtime

    # THE ORDER. Both live in run_desktop, and the assignment must precede the
    # first DeviceCanvas.
    build = "canvas = DeviceCanvas(comp)"
    assert build in runtime
    assert runtime.index("def run_desktop") < runtime.index(assign) < runtime.index(build)

    # DRAW2 is the only line that names `layer=` (the copy this lever removes)
    # and `fill=` (what a colour background() actually costs). Without it the
    # lever has no instrument on this board.
    assert "_diag_draw2" in runtime


def test_sram_bounce_flush_wired():
    """The SRAM-bounce flush needs the esp_lcd no-acquire patch (continuation
    tx_color must be queue-only or every band blocks on the previous one) and
    the two bounce slots -- three was tried and reverted. The compositor half
    is executed in tests/test_banded_panel.py.

    The patch application is a `patch` call in this board's build.sh with its
    own applied-marker grep, not a `moybyte_patch_*` of the shared lib, so it
    has no patch-harness twin; its guard stays a grep until it moves."""
    build = (ROOT / "build.sh").read_text(encoding="utf-8")
    assert (PATCHES / "esp_lcd_tx_color_noacquire.patch").exists()
    assert "esp_lcd_tx_color_noacquire.patch" in build
    assert 'grep -q "Moybyte #66"' in build
    _c = (ROOT / "native" / "moy_lcd" / "modmoy_lcd.c").read_text(encoding="utf-8")
    assert "MOY_LCD_BOUNCE_SLOTS" in _c


# -- one body, on both moy_gfx surfaces -------------------------------------------

def test_the_pre_kernel_guards_are_one_body():
    """The two moy_gfx surfaces must not re-grow their own guard sets.

    They had one drift already: the host refused `dh <= 0` on five verbs and the
    board never did, so a zero-height canvas drew nothing here and fourteen
    pixels on glass, and no test could see it. The clamping branches are exactly
    the ones the conformance goldens never reach, so the only defence is that
    there is nothing to diverge -- mg_solid_prologue / mg_map_ok / mg_is_moy_sheet
    in moy_gfx_kernels.h, called by both."""
    header = (NATIVE / "moy_gfx" / "moy_gfx_kernels.h").read_text(encoding="utf-8")
    for fn in ("mg_solid_prologue", "mg_map_ok"):
        assert "static inline int %s(" % fn in header
    surfaces = {
        "modmoy_gfx.c": (NATIVE / "moy_gfx" / "modmoy_gfx.c").read_text(encoding="utf-8"),
        "moyhost_gfx.c": (Path("runtime") / "moyhost_gfx.c").read_text(encoding="utf-8"),
    }
    for name, src in surfaces.items():
        # tri/circ/circb/line, both sides.
        assert src.count("mg_solid_prologue(") == 4, (
            "%s: the solid-verb prologue is written out again" % name)
        # tline/blit_map on both sides (the device adds DrawCtx.set_map_src).
        assert src.count("mg_map_ok(") >= 2, (
            "%s: the map-cells guard is written out again" % name)
        assert "MOY_MAP_MAX" not in src, (
            "%s: the SPEC 3.3 bound belongs in mg_map_ok, where it is checked "
            "before mw * mh can overflow" % name)


def test_there_is_one_new_layer_factory_and_it_pins_retained_frames():
    """"The factory", singular: P4SystemCanvas.new_layer used to COPY
    DeviceCanvas.new_layer's body (to construct its own class), and the
    override was lost in that copy -- once the paint ring armed on the desktop
    tier, a picker drag shifted by ~twice the real delta and ghosted a
    duplicate of every card (owner-reported on glass, 2026-07-25). The copy
    lost the cart-palette rider the same way. The fix is structural: the
    subclasses supply only the `_make_layer` construction hook, so this pins
    that no tier has grown a copy back. What the one body sets
    (RETAINED_FRAMES = 1) is executed in tests/test_scroll_blit.py."""
    src = Path("device/device_canvas.py").read_text(encoding="utf-8")
    assert src.find("def new_layer(") > 0, "no new_layer factory in device_canvas.py"
    for mod in (Path("firmware/esp32_p4_wifi6_touch_lcd_7b/modules/moy_runtime.py"),
                Path("firmware/guition_jc8012p4a1c/modules/moy_runtime.py"),
                Path("device/p4_canvas.py"),
                Path("firmware/web_runner/web_canvas.py"),
                Path("runtime/host_canvas.py")):
        assert "def new_layer(" not in mod.read_text(encoding="utf-8"), \
            mod.name + ": grew a new_layer copy back; use the _make_layer hook"


def test_paint_image_assets_wired_device_and_carts():
    """ONE codec for a paint image: `cart_api._decode_moyimg` is a FORWARD to
    moy_image, never a second inflater. The drawing tiers used to inflate the
    envelope themselves while the store read Paint's second codec, which is how
    a picture came back blank on whichever tier held the other half. The codec
    is executed in tests/test_moy_image.py."""
    cart_api_src = Path("runtime/cart_api.py").read_text(encoding="utf-8")
    assert "return moy_image.decode_moyimg(text)" in cart_api_src
    assert "deflate" not in cart_api_src and "zlib" not in cart_api_src


def test_editor_cores_are_shared_single_source():
    """One canonical file (runtime/editors.py); neither backend redefines the
    shared cores."""
    runtime = _device_backend_src()
    # The HOST canvas is the boards' own class now (runtime/host_canvas.py builds
    # `device_canvas.DeviceCanvas` on CPython), so "the host does not redefine the
    # cores" is checked where a redefinition could still be written.
    host_canvas = Path("runtime/host_canvas.py").read_text(encoding="utf-8")
    for cls in ("class CodeEditor", "class SpriteSheet", "class PaintEditor"):
        assert cls not in runtime, "device redefines " + cls
        assert cls not in host_canvas, "host canvas redefines " + cls


def test_seed_carts_model_the_fast_draw_habits():
    """The seed carts ARE the curriculum: kids copy them, so they must model
    the fast idioms the docs teach -- a DECLARED background (the engine restores
    it every frame; the play frame never clears) and static scenery in a layer.

    A source pin on the CARTS, not on the console: every seed cart runs
    headless in tests/test_seed_carts.py, but that suite watches pixels, and
    telling a `cls()` from a declared backdrop there would take a recording
    canvas the console does not carry -- out of proportion for two idioms."""
    battle = (Path("system_carts") / "brick_siege.moy" / "main.py").read_text(encoding="utf-8")
    assert 'background(col("dark_blue"))' in battle     # the backdrop is DECLARED
    assert 'cls(' not in battle.split("def _draw()")[1].split("def ")[0], (
        "the play frame must not clear -- the engine restores the declared backdrop")
    assert 'rect(0, 0, FIELD, FIELD' not in battle      # no double-paint backdrop
    hop = (Path("system_carts") / "platformer.moy" / "main.py").read_text(encoding="utf-8")
    assert "def _build_layer():" in hop
    assert "lay.map(0, 0, MW, MH" in hop                # terrain rendered once
    assert "draw_layer(lay, 0, 0)" in hop               # stamped per frame
    api_doc = (Path("docs") / "moy_cart_api.md").read_text(encoding="utf-8")
    assert "## Make it fast" in api_doc                 # the habits are documented


def test_player_isolation_no_forbidden_names():
    """The Player's bundle: it receives the Project + the RAW canvas/input/
    audio/make_api, and NOTHING else -- not the cart store, the shell top bar,
    the home grid, or the layouts. That isolation is what makes the run/return
    cut real: a cart runs identically whether launched from the home grid or
    the editor, because the Player can't reach either. Enforce it structurally
    -- player.py must never NAME any of those surfaces (the bar draw/tap it
    needs goes through thin ws helpers). A stray reach-through would compile
    and pass every behavior test yet quietly re-couple the run loop to the
    shell, so this source grep is the guard."""
    player = (Path("runtime") / "player.py").read_text(encoding="utf-8")
    for forbidden in ("menu_view", "launcher", "bar_layer", "carts_store"):
        assert forbidden not in player, (
            "player.py names '%s' -- the Player must not reach the store/bar/home grid/"
            "layouts (Stage 2 isolation, plan Section 2)" % forbidden)


# -- the shared build ladder --------------------------------------------------------

def _esp32_builds():
    """Every board `build.sh` that builds on tools/esp32_build_lib.sh.

    DISCOVERED, not listed: this test used to read ROOT's build.sh alone, and
    the Guition's byte-identical copies of the two blocks below were asserted by
    nothing at all -- so a rot in one of them left `make test` green and the
    board silently shipping REPR_A (the 16B-per-float boxing whose heap-wrap
    collect is #66's 130-175ms hitch, with no symptom naming the cause).
    """
    out = [p for p in sorted(Path("firmware").glob("*/build.sh"))
           if "esp32_build_lib.sh" in p.read_text(encoding="utf-8")]
    assert len(out) >= 3, "board discovery found %d build.sh" % len(out)
    return out


def _opts_in(build, fn):
    """A board opts into a shared patch by CALLING it, and opts out by naming it
    in a `# DECLINED <fn>` line whose reason follows -- board.toml's `[[deny]]
    why=` in the one file that is not board.toml. Silence is neither."""
    src = build.read_text(encoding="utf-8")
    # A call sits at the start of a line and may carry arguments; the name
    # inside a comment or a longer identifier is neither.
    called = re.search(r"^%s(\s|$)" % fn, src, re.M) is not None
    declined = ("# DECLINED %s " % fn) in src
    assert called != declined, (
        "%s: %s must be either called or declined in writing, exactly one"
        % (build, fn))
    return called


def _shared_patches():
    """Every `moybyte_patch_*` the shared build lib defines. DISCOVERED, so a
    new one is covered by the ladder test the day it lands."""
    lib = Path("tools/esp32_build_lib.sh").read_text(encoding="utf-8")
    out = re.findall(r"^(moybyte_patch_[a-z0-9_]+)\(\) \{", lib, re.M)
    assert len(out) >= 7, "patch discovery found %d functions" % len(out)
    return sorted(out)


def test_every_board_answers_for_every_shared_patch():
    """The ladder is a MATRIX, and silence is the cell that rots. Two of the
    seven were spot-checked and the rest were not: both P4s called
    moybyte_patch_gc_split_reserve while MOYBYTE_GC_SPLIT_RESERVE was defined
    by the two S3 boards alone, so the patch reserved 0 on them from the day
    of the port -- applied, verified, and capping nothing. No test could see
    it, because a CALL was never checked against the board that has to mean
    it."""
    for build in _esp32_builds():
        for fn in _shared_patches():
            _opts_in(build, fn)


def test_the_gc_split_reserve_call_and_the_board_define_agree():
    """The patch's cap is MOYBYTE_GC_SPLIT_RESERVE, defaulted to 0 by the patch
    itself. A board that calls it without defining it reserves nothing, which
    is indistinguishable from a working reserve -- the same shape as `fold=0`.
    So the call and the define travel together, both ways."""
    for build in _esp32_builds():
        called = _opts_in(build, "moybyte_patch_gc_split_reserve")
        hdr = board_config.sdkconfig_path(build.parent).parent / "mpconfigboard.h"
        defined = "MOYBYTE_GC_SPLIT_RESERVE" in hdr.read_text(encoding="utf-8")
        assert called == defined, (
            "%s: calls the split-reserve patch = %r but defines "
            "MOYBYTE_GC_SPLIT_RESERVE = %r" % (build, called, defined))


def test_repr_c_unboxed_floats_wired():
    """REPR_C (unboxed 30-bit floats) is ONE body in the shared lib -- its sed
    and guard are executed in tests/test_esp32_build_lib.py -- and no board may
    re-grow an inline copy of the sed."""
    opted = [b for b in _esp32_builds() if _opts_in(b, "moybyte_patch_repr_c")]
    assert len(opted) >= 2, "both S3 boards run REPR_C"
    for build in _esp32_builds():
        assert "MICROPY_OBJ_REPR" not in build.read_text(encoding="utf-8"), (
            "%s carries its own REPR sed again" % build)


def test_psram_temperature_retune_wired():
    """#169: at 120MHz octal MSPI, IDF only STARTS the temperature retune for
    verified flash vendor ids and otherwise aborts the boot from a SECONDARY
    ESP_SYSTEM_INIT_FN -- a board that flashes cleanly, says nothing on serial,
    and reads exactly like a PSRAM timing failure. The patch function is
    executed in tests/test_esp32_build_lib.py; what a board owes is the pairing
    with the 120MHz profile, and no inline copy."""
    assert Path("patches/esp_psram_temp_retune_any_vendor.patch").exists()
    opted = [b for b in _esp32_builds()
             if _opts_in(b, "moybyte_patch_psram_retune")]
    assert len(opted) >= 2, "both S3 boards run the retune patch"
    for build in opted:
        # The patch is REQUIRED BY the 120MHz MSPI profile, not optional beside
        # it: a board that opts in must actually be running that profile, and a
        # board that drops back to 80M should drop the patch with it.
        sdk = board_config.sdkconfig_path(build.parent).read_text(encoding="utf-8")
        assert "CONFIG_SPIRAM_SPEED_120M=y" in sdk, (
            "%s takes the #169 patch but is not on the 120MHz MSPI profile"
            % build)
    for build in _esp32_builds():
        assert "mspi_timing_by_mspi_delay.c" not in build.read_text(
            encoding="utf-8"), ("%s carries its own #169 patch again" % build)


def _idf_candidates(build):
    """The sibling ESP-IDF checkouts this build.sh offers moybyte_setup_idf."""
    out = []
    for line in build.read_text(encoding="utf-8").splitlines():
        _, sep, rest = line.partition("${REPO_ROOT}/firmware/")
        if sep and "/.build/esp-idf" in rest:
            out.append(rest.partition("/.build/esp-idf")[0])
    return out


def test_exactly_one_board_owns_the_esp_idf_checkout():
    """ESP-IDF v5.5.1 is ~600MB and identical for all three boards, so they
    share ONE clone: `moybyte_setup_idf` takes a candidate list and falls back
    to cloning into its own `.build/esp-idf`. That makes the ownership a
    GRAPH, and the graph is what rots.

    It rotted once. Each board used to clone its own; when the shared build lib
    landed (2026-08-17) the T-Deck started naming the P4's, which left the
    T-Deck's own clone an orphan its own build no longer resolved -- while the
    P4 named it FIRST and the Guition named it first too. Two boards' CMake
    caches ended up pinning CMAKE_TOOLCHAIN_FILE into a directory nobody
    owned, and CMake will not re-point that entry after the first configure: the
    day the orphan was deleted, both builds would have failed on a dead path
    rather than reconfiguring. (2026-08-27: it was, and they were wiped.)

    So the shape is pinned, not the paths. One owner, every other board names
    it and nothing else, and the owner names nobody -- a cycle or a second
    orphan cannot be spelled.
    """
    named = {b.parent.name: _idf_candidates(b) for b in _esp32_builds()}
    for board, cands in named.items():
        assert len(set(cands)) == len(cands), (
            "%s lists the same ESP-IDF checkout twice" % board)
    owners = {c for cands in named.values() for c in cands}
    assert len(owners) == 1, (
        "the boards reach for %d ESP-IDF checkouts (%s) -- one of them is "
        "nobody's, and a CMake cache that pins it cannot be re-pointed"
        % (len(owners), ", ".join(sorted(owners)) or "none"))
    owner = owners.pop()
    assert owner in named, (
        "%s is named as the shared ESP-IDF owner but builds nothing here"
        % owner)
    assert not named[owner], (
        "%s owns the shared ESP-IDF checkout and also borrows one" % owner)
    for board, cands in named.items():
        if board != owner:
            assert cands == [owner], (
                "%s reaches past the owner (%s): %s" % (board, owner, cands))


# -- the vendored synth and its two feeders ------------------------------------------

def test_native_moy_audio_is_vendored_libmoy():
    """The device synth is not ours: libmoy is vendored into
    native/moy_audio/libmoy/ and COMPILED IN, so the boards are conformant by
    construction. The vendoring is checked by hash in
    tests/test_libmoy_vendor.py; what this pins is that the module is a
    BINDING -- every verb forwarded, no synth of its own -- and that both
    build systems compile the vendored source."""
    c = (NATIVE / "moy_audio" / "modmoy_audio.c").read_text(encoding="utf-8")
    cmake = (NATIVE / "moy_audio" / "micropython.cmake").read_text(encoding="utf-8")
    mk = (NATIVE / "moy_audio" / "micropython.mk").read_text(encoding="utf-8")

    assert "MP_REGISTER_MODULE(MP_QSTR_moy_audio" in c
    assert '#include "moy_audio.h"' in c
    for fn in ("moy_bank_parse(", "moy_audio_init(", "moy_audio_render(",
               "moy_audio_sfx(", "moy_audio_beep(", "moy_audio_music(",
               "moy_audio_music_stop(", "moy_audio_sound_stop(",
               "moy_audio_volume("):
        assert fn in c, fn
    # ...and it does NOT carry a synth of its own. These are the giveaways of the
    # reimplementation this replaced; if one comes back, the boards have two
    # synths again and only one of them is the spec. Checked against the CODE
    # only -- the header comment names them all, explaining what went away.
    code = "\n".join(ln for ln in c.splitlines()
                     if not ln.lstrip().startswith("//"))
    for gone in ("moy_sample_wave", "moy_mix_block", "moy_advance_step",
                 "voice_set", "voice_read", "active_mask", "set_master"):
        assert gone not in code, gone

    # Both build systems compile the vendored source alongside the binding:
    # cmake for the boards, the .mk for ports/unix (how the binding is tested off
    # hardware) and the wasm runner.
    assert "libmoy/moy_audio.c" in cmake
    assert "target_link_libraries(usermod INTERFACE usermod_moy_audio)" in cmake
    assert "libmoy/moy_audio.c" in mk
    assert "SRC_USERMOD_C += $(MOY_AUDIO_MOD_DIR)/modmoy_audio.c" in mk


def test_web_runner_audio_forwards_to_libmoy():
    """The wasm runner loads the SAME native module (its build.sh stages
    native/moy_audio and the module ships its own micropython.mk), so the
    browser's synth is libmoy too -- one audible behaviour across every target.
    Pin the forwarding, and that no per-frame marshalling came back."""
    web = Path("firmware/web_runner")
    boot = (web / "web_boot.py").read_text(encoding="utf-8")
    build = (web / "build.sh").read_text(encoding="utf-8")
    assert "native/moy_audio" in build
    # The module carries its own Makefile fragment; the runner must not be
    # copying a second, drifting copy over it.
    assert not (web / "moy_audio_micropython.mk").exists()
    assert "moy_audio_micropython.mk" not in build
    assert "self._ka.bank_load(" in boot
    assert "self._ka.render(buf, n)" in boot
    assert "def is_active(self):" in boot
    for gone in (".voice_set(", ".voice_read(", "._advance_music("):
        assert gone not in boot, gone


def test_native_moy_audio_core1_task_wired():
    """The I2S feed is a dedicated native C task PINNED TO CORE 1 that owns the
    IDF i2s_std channel and feeds it continuously, decoupled from rendering:
    core 0 (the MicroPython VM) cannot run Python on core 1, only a pure-C task
    can. C-side facts no host test reaches; the Python DeviceAudio half is
    executed in tests/test_device_audio.py."""
    c = (NATIVE / "moy_audio" / "modmoy_audio.c").read_text(encoding="utf-8")

    assert "xTaskCreatePinnedToCore(" in c
    assert "moy_audio_task" in c            # the core-1 feeder task body
    assert "1 /* core 1 */" in c            # pinned to core 1 (the last argument)
    # The task owns the IDF i2s_std channel (separate from machine.I2S) + writes it.
    assert "i2s_new_channel(" in c
    assert "i2s_channel_init_std_mode(" in c
    assert "i2s_channel_write(" in c
    # The one engine struct is mutex-protected (core 0 calls verbs, core 1
    # renders): a torn read is NOT acceptable (a momentary glitch is).
    assert "xSemaphoreCreateMutex(" in c
    assert "xSemaphoreTake(" in c
    assert "xSemaphoreGive(" in c
    # The task renders in CHUNKS, dropping the lock between them, so a verb call
    # from core 0 waits tens of microseconds rather than a whole 32 ms block.
    assert "MOY_MIX_CHUNK" in c
    assert "off += MOY_MIX_CHUNK" in c
    # The core-1 task must NEVER call into the MicroPython runtime (no MP heap or
    # GIL from core 1) -- it only touches libmoy's plain-C state.
    assert "moy_audio_render(&s_audio, block + off, MOY_MIX_CHUNK)" in c
    # MP control surface for the task: start (returns False -> fallback) and stop.
    for fn in ("mod_audio_start", "mod_audio_stop", "mod_running"):
        assert fn in c, fn
    for name in ("MP_QSTR_audio_start", "MP_QSTR_audio_stop", "MP_QSTR_running",
                 "MP_QSTR_bank_load", "MP_QSTR_active", "MP_QSTR_volume"):
        assert name in c, name


def test_core1_writeback_cannot_clobber_a_fresh_trigger():
    """THE OVERLAPPING-SFX DROP, and why it cannot recur (#41 -> #97).

    The core-1 task used to mix from a SNAPSHOT of a shared voice array and
    fold its advanced cursor back afterwards, which meant deciding whose copy
    was authoritative. The first attempt used a content proxy (same nsteps +
    first step + step_dur) that a same-SFX retrigger satisfies exactly, so
    "sound 1 ends inside the block, sound 2 starts on the reused channel"
    folded active=0 back over the fresh trigger: sound 2 never played and the
    channel leaked as busy. That was fixed with an exact per-voice commit
    counter.

    The counter is gone now, because the thing it arbitrated is gone: libmoy
    owns the state and there is exactly ONE copy of it, so there is no
    snapshot, no fold-back and nothing to reconcile. This pins the structural
    property rather than the fix -- if a second copy of the voice state ever
    reappears, so does the bug, and this is where it should be argued out."""
    c = (NATIVE / "moy_audio" / "modmoy_audio.c").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in c.splitlines()
                     if not ln.lstrip().startswith("//"))

    # No second copy of the engine state, so no reconciliation machinery.
    for gone in ("moy_voice_t", "snap[", "memcpy(snap", "->seq", "shared->"):
        assert gone not in code, gone
    # The task renders straight out of the one engine struct, under the lock.
    assert "moy_audio_render(&s_audio, block + off, MOY_MIX_CHUNK)" in code
    assert code.count("static moy_audio  s_audio;") == 1


# -- one Lua runtime ---------------------------------------------------------------------

def test_one_lua_runtime_wired():
    """ONE Lua runtime, and moy_lua is the VM under it: the VM target exports
    no module, moycore binds it through libmoy's own binding, and every tier
    reaches moycore with no chooser in front of it. The factory is executed in
    tests/test_moycore_glue.py."""
    lua_dir = NATIVE / "moy_lua" / "lua"
    assert not (NATIVE / "moy_lua" / "modmoy_lua.c").exists(), \
        "the second Lua runtime is back"
    # The VM survives, library sources only, no standalone mains.
    assert (lua_dir / "lvm.c").exists() and not (lua_dir / "lua.c").exists()
    cmake = (NATIVE / "moy_lua" / "micropython.cmake").read_text(
        encoding="utf-8")
    assert "MP_REGISTER_MODULE" not in cmake and "modmoy_lua" not in \
        cmake.split("# ")[-1], "the VM target must export no module"

    mod = (NATIVE / "moycore" / "modmoycore.c").read_text(encoding="utf-8")
    assert "MP_REGISTER_MODULE(MP_QSTR_moycore" in mod
    assert "MP_REGISTER_ROOT_POINTER" in mod       # gc-rooted callables list
    assert "moy_lua_open" in mod and "moy_lua_update" in mod   # libmoy's loop

    # No chooser on any tier: one import, one factory, an ImportError floor.
    # The BOARDS reach it through the shared boot spine -- runtime/device_boot.py
    # holds ONE probe where there used to be two hand-kept copies -- so the
    # import lives there. Each board still has to CALL it, and the second loop
    # is what pins that: a spine nobody invokes is a feature that quietly does
    # not exist, exactly like the web console on the T-Deck.
    for src_path in ((_REPO / "runtime" / "device_boot.py"),
                     (Path("firmware/web_runner") / "web_boot.py")):
        src = src_path.read_text(encoding="utf-8")
        assert "from moycore_glue import make_moycore_runtime" in src, src_path
        assert "make_lua_runtime" not in src, "%s still builds the old runtime" % src_path
        assert "except ImportError" in src, src_path
    for src_path in ((ROOT / "modules" / "moy_runtime.py"),
                     (Path("firmware/esp32_p4_wifi6_touch_lcd_7b") / "modules"
                      / "moy_runtime.py")):
        src = runtime_text(src_path)
        assert "boot.lua_runtime(ws" in src, src_path
        assert "make_lua_runtime" not in src, "%s still builds the old runtime" % src_path
    assert not (ROOT / "modules" / "moy_lua_glue.py").exists()
    api_src = (DEVICE / "device_api.py").read_text(encoding="utf-8")
    assert "moy_lua_glue" not in api_src


def test_moycore_hardware_learned_constraints_pinned():
    """The S3-measured taxes and the safety contracts, on moycore.

    Each of these cost real hardware time to find, and none of them is
    executable by a host test -- allocator caps, a per-file pragma, an
    nlr guard, a build-time VM option -- so they are greps, and they moved with
    the code rather than being retired with it.
    """
    mod = (NATIVE / "moycore" / "modmoycore.c").read_text(encoding="utf-8")
    lua_dir = NATIVE / "moy_lua" / "lua"
    # 1) lua_Alloc is internal-SRAM-first with a headroom floor and a PSRAM
    #    fallback (all-PSRAM measured ~2x slower on the S3's 120MHz-OCT bus),
    #    and the floor is a RUNTIME knob -- run_desktop drops it 48 -> 24KB
    #    once the boot-time internal claims are taken. moycore shipped without
    #    the knob while run_desktop lowered the old runtime's, which left a
    #    moycore cart at 48KB: ~97% PSRAM on this board, i.e. the 2x regime.
    #    (The knob itself is executed in tests/test_moycore_loop.py.)
    assert "MALLOC_CAP_INTERNAL" in mod
    assert "48 * 1024" in mod                          # the WiFi/DMA headroom floor
    assert mod.index("MALLOC_CAP_INTERNAL") < mod.index("MALLOC_CAP_SPIRAM")
    runtime_src = runtime_text(ROOT / "modules" / "moy_runtime.py")
    assert 'for _mod in ("moy_lua", "moycore")' in runtime_src, \
        "the boot-time floor drop must reach moycore by name"
    # 2) every vendored Lua source carries the in-source -O2 pragma (usermods
    #    compile at -Os, which halved the VM -- the #77 moy_gfx lesson; cmake
    #    source-file properties never reach the linked objects).
    missing = [q.name for q in sorted(lua_dir.glob("*.c"))
               if "#pragma GCC optimize" not in q.read_text(encoding="utf-8")]
    assert missing == [], "vendored lua sources missing the -O2 pragma: %s" % missing
    # 3) MP exceptions never longjmp through Lua frames: the trampoline's call
    #    into Python is nlr-protected.
    assert "nlr_push" in mod
    # 4) LUA_32BITS is ON (#67 owner decision 2026-07-18): both boards' FPUs are
    #    single-precision, so doubles are soft-float; 32-bit floats/ints use the
    #    HW FPU and halve TValue. The host builds this same header, so the
    #    parity harnesses run the cart the boards run.
    conf = (lua_dir / "luaconf.h").read_text(encoding="utf-8")
    assert "#define LUA_32BITS\t1" in conf
    # 5) the upcall marshalling diet (#107): small-ints integer args and
    #    interned names back as qstrs. mp_obj_new_int_from_ll unconditionally
    #    heap-allocates an mpz, and paying it per coordinate was 11KB/frame of
    #    garbage in celeste -- a 160-200ms auto-collect every ~6s of play (P4
    #    on glass 2026-07-27). from_ll survives only as the >31-bit fallback.
    tramp = mod[mod.index("static mp_obj_t lua_to_mp"):]
    assert "mp_obj_new_int((mp_int_t)v)" in tramp
    assert "mp_obj_new_int_from_ll" in tramp           # the >31-bit fallback
    # NB: #107's other half -- interning returned names as qstrs -- was the old
    # runtime's and did not come across, deliberately. It paid for itself there
    # because every verb was a trampoline; here the only string-taking verbs
    # left are image/table/text, which carts call once and cache.
    # 6) rnd is SEEDED per run. libmoy's xorshift32 treats con->rng == 0 as a
    #    fixed constant, so leaving it zero gave every run of every cart the
    #    same sequence -- invisible under the old runtime, whose prelude
    #    shadowed rnd with Lua's per-state math.random.
    assert "RUN.con.rng    = (uint32_t)mp_hal_ticks_us()" in mod
    # 7) the p8 shim's masked map walk came across with the cart (#66 M0):
    #    4.5ms of celeste's S3 render, and the shim nil-guards the names, so
    #    losing them costs performance silently.
    assert "__moy_map_masked" in mod and "__moy_map_flags" in mod
