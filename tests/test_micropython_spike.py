import importlib.util
import sys
from pathlib import Path


ROOT = Path("firmware/lilygo_t_deck_plus_micropython")
EDITORS_SRC = Path("runtime") / "editors.py"


def test_micropython_spike_scaffold_exists():
    assert (ROOT / "README.md").exists()
    assert (ROOT / "build.sh").exists()
    assert (ROOT / "modules" / "main.py").exists()
    assert (ROOT / "modules" / "kidcode" / "__init__.py").exists()


def test_micropython_spike_documents_sd_launcher_bin():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "launcher-friendly `.bin`" in readme
    assert "kidcode_micropython_tdeck.bin" in readme
    assert "use `kidcode_micropython_tdeck.bin`" in readme
    assert "update error" in readme
    assert "native `240x320` portrait" in readme
    assert "Launcher-based boot is still the preferred quick app-test loop" in readme
    assert "full USB flashing at `0x0` is confirmed to work" in readme
    assert "USB full flashing is valid on this board" in readme
    assert "KIDCODE_BOARD_CONFIG=tdeck" in readme
    assert "kidcode_lvgl_tdeck_board_jtag_full_dio_0x0.bin" in readme


def test_micropython_spike_build_uses_lvgl_micropython_and_frozen_modules():
    build = (ROOT / "build.sh").read_text(encoding="utf-8")
    patch = (ROOT / "patches" / "esp32_tdeck_early_board_init.patch").read_text(encoding="utf-8")

    assert "lvgl-micropython/lvgl_micropython" in build
    assert "BOARD=ESP32_GENERIC_S3" in build
    assert "BOARD_VARIANT=SPIRAM_OCT" in build
    assert "DISPLAY=st7789" in build
    assert "FROZEN_MANIFEST" in build
    assert "export IDF_PATH" in build
    assert "export.sh" in build
    assert "--partition-size=4194304" in build
    # OTA (#53): the build asks the lvgl_micropython builder for a dual-app partition
    # table (otadata + ota_0 + ota_1), and merges the full image at the derived ota_0
    # offset rather than the legacy hardcoded 0x10000.
    assert "--ota" in build
    assert "${APP_OFFSET}" in build
    assert "MPY_BUILD_DIR" in build
    assert "micropython.bin" in build
    assert "full-flash" not in build
    assert "KIDCODE_BUILD_JOBS" in build
    assert "KIDCODE_BUILD_PYTHON" in build
    assert "nice -n" in build
    assert "ionice -c 3" in build
    assert "KIDCODE_SKIP_UPSTREAM_SUBMODULES" in build
    assert "KIDCODE_EARLY_BOARD_INIT" in build
    assert "KIDCODE_BOARD_CONFIG" in build
    assert "KIDCODE_REPL" in build
    assert "KIDCODE_ARTIFACT_NAME" in build
    assert "export GEN_SCRIPT" in build
    assert "--custom-board-path=display_configs/LilyGo-TDeck" in build
    assert "boards/sdkconfig\\.usb" in build
    assert "MICROPY_HW_ESP_USB_SERIAL_JTAG" in build
    assert "esp32_tdeck_early_board_init.patch" in build
    assert "patch -R" in build
    assert "kidcode_tdeck_early_board_init" in patch
    assert "KIDCODE_TDECK_POWERON   GPIO_NUM_10" in patch


def test_micropython_spike_makefile_has_flash_and_monitor_targets():
    makefile = Path("Makefile").read_text(encoding="utf-8")
    wrapper = Path("tools/esptool_no_modem.py").read_text(encoding="utf-8")

    assert "firmware-flash-lilygo-micropython:" in makefile
    assert "firmware-flash-lilygo-micropython-no-reset:" in makefile
    assert "firmware-flash-lilygo-micropython-full:" in makefile
    assert "firmware-run-lilygo-micropython:" in makefile
    assert "firmware-monitor-lilygo-micropython:" in makefile
    assert "MPY_APP_BIN" in makefile
    assert "MPY_FULL_BIN" in makefile
    assert "bootloader/bootloader.bin" in makefile
    assert "partition_table/partition-table.bin" in makefile
    # OTA (#53): the app-only flash targets write to the ota_0 offset (0x20000 on the
    # dual-OTA table), not the legacy 0x10000 -- overridable via MPY_APP_OFFSET.
    assert "MPY_APP_OFFSET ?= 0x20000" in makefile
    assert "$(MPY_APP_OFFSET) $(MPY_APP_BIN)" in makefile
    assert "0x0 $(MPY_FULL_BIN)" in makefile
    assert "tools/esptool_no_modem.py" in makefile
    assert "--before default_reset --after hard_reset" in makefile
    assert "--before no_reset --after no_reset" in makefile
    assert "--no-stub run" in makefile
    assert "serial.serial_for_url = _patched_serial_for_url" in wrapper
    assert "rtscts" in wrapper
    assert "False" in wrapper
    assert "ResetStrategy._setDTRandRTS" in wrapper


def test_ota_updater_module_flashes_inactive_slot_from_sd():
    # OTA (#53): the device updater writes an SD .bin into the inactive OTA slot via
    # esp32.Partition (block-erase writeblocks) and reboots; rollback is the safety net.
    ota = (ROOT / "modules" / "kc_ota.py").read_text(encoding="utf-8")

    assert "class OtaUpdater" in ota
    assert 'UPDATE_DIR = "/sd/update"' in ota
    assert "BLOCK = 4096" in ota
    assert "IMAGE_MAGIC = 0xE9" in ota
    # capability + the esp_ota partition dance
    assert "def available" in ota
    assert "esp32.Partition.RUNNING" in ota
    assert "get_next_update()" in ota
    assert ".writeblocks(" in ota
    assert ".set_boot()" in ota
    assert "mark_app_valid_cancel_rollback()" in ota
    # SD is touched only through the injected (panel-DMA-draining) wrapper
    assert "self._with_sd(" in ota
    # stepwise install API the shared console drives
    assert "def begin(self" in ota
    assert "def step(self" in ota
    assert "def finish(self" in ota
    assert "machine.reset()" in ota


def test_ota_updater_wired_into_run_desktop_with_rollback_confirm():
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")

    assert "import kc_ota" in runtime
    assert "ws.updater = kc_ota.OtaUpdater(_with_sd_synced)" in runtime
    # the healthy-boot rollback confirm
    assert "ws.updater.mark_valid()" in runtime


def test_console_settings_has_firmware_update_screen():
    # The shared console owns all OTA pixels (host == device): a Settings UPDATE FW row
    # (shown only when an updater is injected and OTA-capable) drives a confirm/progress
    # screen. The host injects no updater, so the row never appears there.
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")

    assert "self.updater = None" in console
    assert "def _update_available" in console
    assert "def _settings_rows" in console
    assert '"UPDATE FW"' in console
    assert "def open_update" in console
    assert "def _pump_update" in console
    assert "def _draw_update" in console
    assert 'self.screen == "update"' in console
    assert "def _activate_settings_action" in console


def test_device_web_view_module_present_and_protocol_shaped():
    # Device web view (#41/#22): a kc_webserver module records the cart's per-frame draw
    # calls (a TeeCanvas over the real DeviceCanvas) and serves them to a browser over
    # WiFi via the SAME draw-command protocol the host web console uses, so the same
    # web_console.html renders device frames. We grep the frozen source (firmware tests
    # don't execute MicroPython); the executable behaviour is in tests/test_kc_webserver.py.
    web = (ROOT / "modules" / "kc_webserver.py").read_text(encoding="utf-8")
    assert "class DrawRecorder" in web          # per-frame draw-command recorder
    assert "class TeeCanvas" in web             # forwards to the panel canvas + records
    assert "class WebServer" in web             # non-blocking, one request per poll()
    assert "self.recorder.enabled" in web       # the gate -> zero cost when no browser
    assert "setblocking(False)" in web          # NON-blocking listening socket
    # The draw-command protocol routes (/assets over HTTP; the legacy /frame & /input HTTP
    # endpoints remain as a poll fallback alongside the WebSocket live channel).
    assert '"/assets"' in web and '"/frame"' in web and '"/input"' in web
    assert "def assets_payload" in web and "def frame_payload" in web
    assert "def apply_events" in web            # browser events -> InputState/Pointer
    # WEBSOCKET TRANSPORT (#41 swap): the persistent live channel replaced the per-frame
    # HTTP poll. The handshake (RFC 6455 accept-key + 101) + frame encode/decode + the
    # persistent conn (_WSConn) must be present; the page connects to "/ws".
    assert "def ws_accept_key" in web and "def ws_handshake_response" in web
    assert "def ws_encode" in web and "def ws_decode" in web
    assert "class _WSConn" in web               # the persistent, non-blocking WS connection
    assert "258EAFA5-E914-47DA-95CA-C5AB0DC85B11" in web   # the RFC 6455 magic GUID
    assert '"/ws"' in web or "/ws" in web       # the WebSocket route the page connects to
    assert "Switching Protocols" in web         # the 101 upgrade response
    # SERVE-TIME defspr (#41 BUG-1 fix): the bitmap is delivered when the browser RECEIVES
    # a frame referencing it (drop-robust), not at record-time first-sight. So the server
    # reconstructs the defspr (defspr_cmd) and prepends it (served_frame), tracking a
    # `served` set that resets on /assets (reset_served) and on a dropped atlas (atlas_gen).
    assert "def defspr_cmd" in web and "def served_frame" in web
    assert "def reset_served" in web and "atlas_gen" in web
    # STREAM MODE (#41 30fps lever): headless while a browser plays -- the Tee record-only
    # path + a 30fps web cap.
    assert "record_only" in web and "def stream_mode" in web
    assert "WEB_FPS_CAP = 30" in web


def test_device_web_view_wired_into_run_desktop_cooperatively():
    # The web view is serviced from run_desktop's single-threaded loop: a TeeCanvas
    # swapped in as ws.canvas (panel still renders), begin/commit around the frame, and
    # ONE poll() BETWEEN frames (never mid-flush). The Settings WEB VIEW row toggles it.
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    assert "import kc_webserver" in runtime
    assert "class WebView" in runtime
    assert "web = WebView(" in runtime
    assert "ws.web_hook = web" in runtime
    assert "web.begin_frame()" in runtime       # start a recording before the frame
    assert "web.commit_frame()" in runtime      # publish the frame's commands
    assert "web.poll()" in runtime              # service one request between frames
    # The recorder must record draw commands, never stream the raw framebuffer.
    assert "DrawRecorder" in runtime
    # STREAM MODE (#41 30fps lever): the WebView drives the panel headless while a browser
    # plays -- skip the flush via the compositor (skip_flush) + a one-time enter notice.
    assert "_apply_stream_mode" in runtime
    assert "skip_flush" in runtime
    comp = (ROOT / "modules" / "kc_compositor.py").read_text(encoding="utf-8")
    assert "self.skip_flush" in comp            # flush() is a no-op while streaming


def test_console_settings_has_web_view_toggle():
    # Host == device: the shared console grows a Settings WEB VIEW ON/OFF row (with the
    # served URL) ONLY when a web_hook is injected -- the device does, the host doesn't
    # (it has tools/web_console.py), so the row never appears on the host.
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    assert "self.web_hook = None" in console
    assert '"WEB VIEW"' in console
    assert "def _toggle_web_view" in console
    assert 'kind == "web"' in console


def test_ota_online_download_streams_to_sd_with_checksum():
    # OTA Phase 3 (#53): a WiFi download fetches a manifest, streams the .bin straight
    # to SD (never buffering the whole image), and verifies sha256 before installing.
    ota = (ROOT / "modules" / "kc_ota.py").read_text(encoding="utf-8")

    assert "FIRMWARE_VERSION = " in ota          # bumped per release (#53), so don't pin the value
    assert 'OTA_CFG_NAME = "ota.json"' in ota
    assert 'DOWNLOAD_NAME = "firmware.bin"' in ota
    # capability + manifest + connectivity
    assert "def online_available(self):" in ota
    assert "def manifest_url(self, channel=None):" in ota   # channel-aware (#53 2-channel)
    assert "def check_online(self, channel=None):" in ota
    assert "def ensure_online(self):" in ota
    # streaming download API the console drives (mirror of begin/step/finish)
    assert "def begin_download(self" in ota
    assert "def download_step(self" in ota
    assert "def download_finish(self" in ota
    # raw-socket streaming (NOT urequests, which would buffer the whole 3MB) + sha256
    assert "import socket" in ota
    assert "hashlib.sha256()" in ota
    assert "sha256 mismatch" in ota
    assert "server_hostname=host" in ota          # https path
    # never holds the full image: writes each chunk through the SD wrapper
    assert "def _consume(self, chunk):" in ota


def test_ota_online_wired_and_console_has_online_flow():
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")

    # run_desktop hands the wifi service to the updater for online updates.
    assert "ws.updater.set_wifi(ws.wifi, go_online=lambda: autoconnect_wifi(ws.wifi))" in runtime
    # The shared console grows the UPDATE ONLINE row + the checking/download phases.
    assert "def _online_update_available" in console
    assert '"UPDATE ONLINE"' in console
    assert "def open_update_online" in console
    assert "def _start_download" in console
    assert 'ph == "checking"' in console or 'phase == "checking"' in console
    assert 'self._upd_phase = "downloading"' in console
    assert 'self._upd_phase = "confirm"' in console   # online hands off to the local install


def test_micropython_spike_uses_tdeck_native_panel_geometry():
    display = (ROOT / "modules" / "tdeck_display.py").read_text(encoding="utf-8")

    assert "width = 240" in display
    assert "height = 320" in display
    assert "display._ORIENTATION_TABLE = (0, 160, 192, 96)" in display
    assert "display.set_rotation" in display



def test_micropython_spike_has_guarded_sd_project_loader():
    display = (ROOT / "modules" / "tdeck_display.py").read_text(encoding="utf-8")
    sd_loader = (ROOT / "modules" / "kidcode_sd.py").read_text(encoding="utf-8")

    assert "def get_spi_bus():" in display
    assert "SD_PROJECT_FILE_PATHS" in sd_loader
    assert '"/sd/project.py"' in sd_loader
    assert "SD_FREQ = 800000" in sd_loader
    assert "def read_first_project_source" in sd_loader
    assert "SDCard(spi_bus=spi_bus, cs=SD_CS, freq=SD_FREQ)" in sd_loader
    assert "Pin(TFT_CS, Pin.OUT, value=1)" in sd_loader
    assert "machine.SPI.Bus(host=SPI_HOST" in sd_loader
    assert "def _unmount_if_possible" in sd_loader
    assert "def _deinit_if_possible" in sd_loader
    assert "def _looks_mounted(os_module):" in sd_loader
    assert 'os_module.statvfs(SD_MOUNT) != os_module.statvfs("/")' in sd_loader
    assert "vfs.mount(block_device, path)" in sd_loader


def test_micropython_native_sd_shares_display_spi_host():
    # The live SD path attaches the card to the host esp_lcd already initialized
    # (kc_sd) instead of re-running spi_bus_initialize like machine.SDCard, which
    # hangs the shared bus once the panel is up. See modkc_sd.c header.
    mod = (ROOT / "native" / "kc_sd" / "modkc_sd.c").read_text(encoding="utf-8")
    cmake = (ROOT / "native" / "kc_sd" / "micropython.cmake").read_text(encoding="utf-8")
    build = (ROOT / "build.sh").read_text(encoding="utf-8")
    sd_loader = (ROOT / "modules" / "kidcode_sd.py").read_text(encoding="utf-8")
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")

    # Native module attaches (init_device) rather than re-initializing the bus.
    assert "MP_REGISTER_MODULE(MP_QSTR_kc_sd" in mod
    assert "sdspi_host_init_device" in mod
    assert "sdmmc_read_sectors" in mod and "sdmmc_write_sectors" in mod
    assert "target_link_libraries(usermod INTERFACE usermod_kc_sd)" in cmake
    assert "ext_mod/kc_sd" in build
    assert "kc_sd/micropython.cmake" in build

    # Python live-mount path + block device backed by kc_sd.
    assert "class _NativeSDBlockDev" in sd_loader
    assert "def with_sd_live(fn):" in sd_loader
    assert "def mount_sd_live(" in sd_loader
    assert "import kc_sd" in sd_loader

    # Desktop enables management through the live path (no longer hard-disabled).
    # The SD session is wrapped so it drains any in-flight panel DMA first (the
    # #40 double-buffer SD-vs-panel mutual exclusion), but still delegates to the
    # native live-mount path.
    assert "ws._with_sd = _with_sd_synced" in runtime
    assert "return kidcode_sd.with_sd_live(fn)" in runtime
    assert "ws.can_manage = carts_root is not None" in runtime
    assert "ws.can_manage = False" not in runtime


def test_micropython_touch_and_idle_cursor():
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    shell = (ROOT / "modules" / "kidcode_shell.py").read_text(encoding="utf-8")

    # GT911 touch driver on I2C0 (off the SPI bus), fed into the shared pointer.
    assert "class Touch:" in runtime
    assert "0x814E" in runtime and "0x8150" in runtime          # GT911 status/point regs
    assert "TOUCH_SWAP" in runtime and "TOUCH_FLIP_Y" in runtime
    assert "touch = Touch(canvas.w, canvas.h" in runtime
    assert "tp = touch.poll()" in runtime
    assert "pointer.place(tp[0], tp[1])" in runtime

    # Cursor auto-hide + the Pointer live in the shared console now.
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    assert "class Pointer:" in console
    assert "def tick(self, now):" in console
    assert "self.pointer.visible" in console               # draw guard
    assert "pointer.tick(now)" in runtime

    # Touch calibration bring-up mode (serial-only, flush-once).
    assert "def run_touch_calibrate(handler):" in runtime
    assert "RUN_TOUCH_CALIBRATE" in shell
    assert "run_touch_calibrate(_task_handler)" in shell


def test_micropython_cart_textmode_flips_keyboard_ascii_raw():
    # Cart text input (#38/#42): a running cart opts into text-keyboard mode via the
    # `textmode` verb; the device backend then flips the T-Deck keyboard to clean
    # 1-byte ASCII so key()/keyp() yield typeable bytes, and back to raw/game mode
    # otherwise (so games keep hold-to-move). Firmware tests grep the frozen source.
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    kb = (ROOT / "modules" / "kidcode" / "input.py").read_text(encoding="utf-8")

    # The device make_api exposes `textmode`, setting input.text_mode (host parity).
    assert "def textmode(on=True):" in runtime
    assert '"textmode": textmode,' in runtime
    assert "input.text_mode = bool(on)" in runtime

    # The shared console derives the keyboard mode from the cart's request each
    # running-cart frame and reverts on exit -- via the existing set_game_mode path.
    assert "def _sync_cart_text_mode(self):" in console
    assert "self._sync_cart_text_mode()" in console
    assert 'getattr(self.input, "text_mode", False)' in console
    assert "kb.set_game_mode(not want_text)" in console
    # _set_text_mode is the single source of truth: it sets text_mode for the code
    # editor (on=True) AND clears it elsewhere (on=False), so a typed key never also
    # latches its game-button alias in either the editor or a text-mode cart.
    assert "self.input.text_mode = bool(on)" in console

    # The keyboard's ASCII<->raw switch (the device mechanism) is unchanged and the
    # older-firmware fallback (RAW_GAME_MODE / _raw_unsupported) is still respected.
    assert "def set_game_mode(self, on):" in kb
    assert "RAW_GAME_MODE" in kb and "_raw_unsupported" in kb
    # In ASCII text mode the keyboard poll reports the key but does NOT latch its
    # game-button alias (w/a/s/d/z/x -> up/left/down/right/a/b), so typing a
    # password/name can't also trigger d-pad/shortcut actions (#38/#42).
    assert 'if getattr(self.input, "text_mode", False):' in kb


def test_micropython_spike_documents_tdeck_reference_paths():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    notes = (Path("docs/history") / "SPIKE_RESULTS.md").read_text(encoding="utf-8")

    assert "lvgl_micropython/display_configs/LilyGo-TDeck" in readme
    assert "TulipCC" in readme
    assert "native framebuffer/canvas" in readme
    assert "https://github.com/shorepine/tulipcc" in notes
    assert "No LilyGO-maintained MicroPython T-Deck example" in notes


def test_kc_compositor_plan_strips_and_host_guard():
    spec = importlib.util.spec_from_file_location(
        "kc_compositor", ROOT / "modules" / "kc_compositor.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Host guard: no bus -> no compositor (never touches kc_alloc/framebuf).
    assert module.make_compositor(None) is None

    # Even strip height tiles the full panel exactly.
    assert module.plan_strips(240, 40) == [
        (0, 40), (40, 40), (80, 40), (120, 40), (160, 40), (200, 40)
    ]

    # Uneven strip height: last band is the shorter remainder, and the bands
    # always cover the full height with no gaps or overlap.
    bands = module.plan_strips(240, 36)
    assert bands[-1] == (216, 24)
    assert sum(rows for _y, rows in bands) == 240
    for (y, rows), (next_y, _r) in zip(bands, bands[1:]):
        assert y + rows == next_y

    try:
        module.plan_strips(240, 0)
        assert False, "strip_h <= 0 should raise"
    except ValueError:
        pass


def test_kc_compositor_dirty_region_math():
    spec = importlib.util.spec_from_file_location(
        "kc_compositor", ROOT / "modules" / "kc_compositor.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # clip_rect clamps to the screen, including negative origins and overruns.
    assert module.clip_rect(10, 10, 5, 5, 320, 240) == (10, 10, 5, 5)
    assert module.clip_rect(-5, -5, 20, 20, 320, 240) == (0, 0, 15, 15)
    assert module.clip_rect(315, 0, 20, 10, 320, 240) == (315, 0, 5, 10)
    assert module.clip_rect(400, 0, 10, 10, 320, 240)[2] == 0  # off-screen -> w=0

    # union_rect grows the bounding box; None is the identity.
    assert module.union_rect(None, (1, 2, 3, 4)) == (1, 2, 3, 4)
    assert module.union_rect((1, 2, 3, 4), None) == (1, 2, 3, 4)
    assert module.union_rect((0, 0, 10, 10), (20, 20, 5, 5)) == (0, 0, 25, 25)

    # DirtyTracker unions adds and resets on take().
    dt = module.DirtyTracker()
    assert dt.take(320, 240) is None
    dt.add(10, 10, 5, 5)
    dt.add(100, 100, 10, 10)
    assert dt.take(320, 240) == (10, 10, 100, 100)
    assert dt.take(320, 240) is None  # reset after take
    dt.add(0, 0, 0, 0)  # empty adds are ignored
    assert dt.is_empty()


def test_kc_compositor_flush_breakdown_instrumentation():
    """The flush-breakdown instrumentation (perf #33/#12): flush() times its
    sub-steps and logs a `FLUSHBRK copy=.. tx=.. setup=.. n=.. total=..` line via
    kidcode_diag, so the owner can read live whether the ~28 ms flush is SPI clock
    (tx) or non-transfer overhead (copy/setup). Grep the device source (the firmware
    tests assert structure, not execution) + the importable module constants."""
    comp_src = (ROOT / "modules" / "kc_compositor.py").read_text(encoding="utf-8")

    # The revert-able gate + the sample throttle are single named constants.
    assert "FLUSH_INSTRUMENT = True" in comp_src
    assert "FLUSH_SAMPLE_EVERY = 30" in comp_src
    # The exact log line shape the owner reads live (copy/tx/setup/n/total in ms).
    assert 'msg = "copy=%.2f tx=%.2f setup=%.2f n=%d total=%.2f" % (' in comp_src
    assert '"FLUSHBRK"' in comp_src
    # The transfer (`tx`) is timed in isolation -- the band push is factored out of
    # flush() so the instrumented path times exactly the SPI/DMA, not the copy.
    assert "def _flush_full_frame(self):" in comp_src
    assert "def _flush_instrumented(self):" in comp_src
    assert "def _log_flushbrk(self, copy_us, tx_us, setup_us, n, total_us):" in comp_src
    # Timing uses ticks_us for sub-ms resolution (the copy is ~ms-scale).
    assert "time.ticks_us" in comp_src
    # REVERT is documented as flipping the one flag, and the untimed flush path is
    # preserved byte-for-byte (the `_frame[:] = self._fb` copy + the band loop).
    assert "To REVERT: set FLUSH_INSTRUMENT = False" in comp_src
    assert "self._frame[:] = self._fb" in comp_src

    # The module still imports + the gate constant is the importable knob.
    spec = importlib.util.spec_from_file_location(
        "kc_compositor", ROOT / "modules" / "kc_compositor.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.FLUSH_INSTRUMENT is True
    assert module.FLUSH_SAMPLE_EVERY == 30

    # The clock finding is documented at the SPI bootstrap: the T-Deck panel pins
    # are NOT the S3 IOMUX-native FSPI pins, so 80 MHz is a board limit, and the
    # freq constant is the documented revert lever.
    disp_src = (ROOT / "modules" / "tdeck_display.py").read_text(encoding="utf-8")
    assert "IOMUX" in disp_src
    assert "GPIO matrix" in disp_src
    assert "freq = 80000000" in disp_src


def test_kc_compositor_double_buffer_enabled_and_revertible():
    """DMA double-buffering / flush overlap (#40): a ping-pong of two PSRAM frame
    buffers so the panel DMA runs WHILE the CPU renders the next frame. Device-
    confirmed stable + the copy-removal win (flush 28->20ms, copy=0, ~13->16-19fps),
    so it is now the DEFAULT ON. It stays a single-flag revert: set DOUBLE_BUFFER =
    False -> the proven single-buffer banded flush runs byte-for-byte (the #40
    instant fallback). Grep the device source for the design + the gate + the
    SD-vs-panel-DMA mutual exclusion."""
    comp_src = (ROOT / "modules" / "kc_compositor.py").read_text(encoding="utf-8")
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")

    # The gate is a single named constant, now DEFAULT ON (device-confirmed). It
    # stays revertible (a one-flag fallback to the single-buffer path) -- assert the
    # actual top-level assignment is True (the line may carry a trailing comment).
    assert "\nDOUBLE_BUFFER = True" in comp_src
    # ... and importable so the flag is verifiable, not just textual.
    spec = importlib.util.spec_from_file_location(
        "kc_compositor", ROOT / "modules" / "kc_compositor.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DOUBLE_BUFFER is True

    # Two distinct PSRAM ping-pong buffers (A=_fb, B=_fb_b); flush picks the back.
    assert "self._fb_b = None" in comp_src
    assert "self.double_buffer = DOUBLE_BUFFER" in comp_src
    # The swap/kick/drain machinery + deferred completion (the held-back final band).
    assert "def _flush_double(self):" in comp_src
    assert "def _swap_buffers(self):" in comp_src
    assert "def _kick_front(self):" in comp_src
    assert "def _drain_dma(self):" in comp_src
    assert "self._dma_pending" in comp_src              # the done-flag
    # The kick queues bands async (last=False) and RETURNS without waiting; the
    # blocking last=True band is deferred to the next drain (the overlap mechanism).
    assert ", 0, False)" in comp_src                    # async band (last=False)
    assert ", 0, True)" in comp_src                     # the deferred completion band
    # No PSRAM->PSRAM copy on the double path -- it flushes the just-rendered buffer
    # directly (the whole point: A/B are distinct so DMA reads A while CPU writes B).
    assert "No `_frame[:] = _fb` copy" in comp_src

    # The single-buffer path is preserved byte-for-byte (the instant revert).
    assert "if self.double_buffer:\n            self._flush_double()\n            return" in comp_src
    assert "self._frame[:] = self._fb" in comp_src      # untouched single-buffer copy

    # SD vs panel-DMA mutual exclusion: SD shares the SPI host, so an SD op may not
    # overlap an in-flight panel DMA. sync() drains it; run_desktop wraps with_sd_live
    # to sync() FIRST.
    assert "def sync(self):" in comp_src
    assert "def _with_sd_synced(fn):" in runtime
    assert "comp.sync()" in runtime
    assert "return kidcode_sd.with_sd_live(fn)" in runtime

    # The canvas follows the back buffer each frame (a stale pointer would draw into
    # the buffer mid-DMA -> tear); run_desktop calls it before drawing.
    assert "def back_buffer(self):" in comp_src
    assert "def sync_back(self):" in runtime
    assert "canvas.sync_back()" in runtime


def test_kc_compositor_double_buffer_pingpong_logic():
    """Exercise the ping-pong flush logic with stub native modules (no hardware):
    verify the buffer SWAP, the DEFERRED completion (final band held back, drained on
    the next flush), that NO per-frame full-frame copy happens, and that sync() drains
    the in-flight DMA for the SD mutual-exclusion. This is the executable counterpart
    of the grep test -- it proves the invariant, not just the structure."""
    import types

    # Stub the device-only natives the Compositor.__init__ imports. kc_alloc.malloc_dma
    # returns a plain bytearray (DMA memory is just RAM on the host); lcd_bus exposes
    # the MEMORY_* flags; framebuf is a no-op FrameBuffer (we drive raw buffers here).
    fake_alloc = types.ModuleType("kc_alloc")
    fake_alloc.malloc_dma = lambda n, flags=0: bytearray(n)
    fake_lcd = types.ModuleType("lcd_bus")
    fake_lcd.MEMORY_SPIRAM = 1
    fake_lcd.MEMORY_DMA = 2

    class _FB:
        def __init__(self, buf, w, h, fmt):
            self.buf = buf
        def fill(self, c):
            pass
        def fill_rect(self, *a):
            pass
        def text(self, *a):
            pass
    fake_framebuf = types.ModuleType("framebuf")
    fake_framebuf.FrameBuffer = _FB
    fake_framebuf.RGB565 = 1

    saved = {k: sys.modules.get(k) for k in ("kc_alloc", "lcd_bus", "framebuf", "kc_gfx")}
    sys.modules["kc_alloc"] = fake_alloc
    sys.modules["lcd_bus"] = fake_lcd
    sys.modules["framebuf"] = fake_framebuf
    sys.modules.pop("kc_gfx", None)   # no native kernel -> framebuf fallback path
    try:
        spec = importlib.util.spec_from_file_location(
            "kc_compositor_pp", ROOT / "modules" / "kc_compositor.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Enable double-buffer the way the owner would (flip the flag, rebuild) so the
        # 2nd PSRAM buffer is allocated at construction. The default stays False on
        # disk (the other test asserts that); this only mutates the freshly-imported
        # test copy of the module.
        module.DOUBLE_BUFFER = True

        # A fake lcd_bus.SPIBus recording every tx_color (the DMA) + tx_param (window).
        class FakeBus:
            def __init__(self):
                self.colors = []   # (cmd, nbytes, y0, y1, last)
                self.params = []   # (cmd, ...)
            def tx_param(self, cmd, data):
                self.params.append(cmd)
            def tx_color(self, cmd, data, x0, y0, x1, y1, _z, last):
                self.colors.append((cmd, len(bytes(data)), y0, y1, last))

        bus = FakeBus()
        comp = module.Compositor(bus, 320, 240, strip_h=24)
        assert comp.double_buffer is True   # picked up the enabled module flag
        # Two distinct physical buffers must exist for ping-pong.
        assert comp._fb_b is not None
        assert comp._fb is not comp._fb_b

        a = comp._fb
        # FRAME 1: back is A. flush() -> drain(no-op) + swap(front=A,back=B) + kick(A).
        assert comp.back_buffer() is a
        comp.flush()
        # The just-rendered A is now the FRONT (in flight); the canvas draws into B.
        assert comp._front is a
        assert comp.back_buffer() is comp._fb_b
        # A's bands were queued async EXCEPT the final one, which is held back.
        assert comp._dma_pending is not None
        last_flags = [c[4] for c in bus.colors]
        assert last_flags and all(f is False for f in last_flags)  # none completed yet
        n_after_kick1 = len(bus.colors)
        assert n_after_kick1 >= 1                                  # async bands issued
        # No full-frame PSRAM->PSRAM copy: _frame stays whatever it was (unused).
        # (The double path never assigns _frame[:] = _fb.)

        # FRAME 2: flush() drains A's held-back final band (last=True) FIRST, then
        # swaps (front=B, back=A) and kicks B.
        comp.flush()
        # Exactly one completing (last=True) transfer happened (A's drained final band).
        completed = [c for c in bus.colors if c[4] is True]
        assert len(completed) == 1
        assert comp._front is comp._fb_b
        assert comp.back_buffer() is a                              # recycled, drained
        assert comp._dma_pending is not None                       # B's final band held

        # sync() drains the in-flight DMA (B's held band) for the SD mutual exclusion,
        # leaving NOTHING pending on the shared bus.
        comp.sync()
        assert comp._dma_pending is None
        completed2 = [c for c in bus.colors if c[4] is True]
        assert len(completed2) == 2                                # B's band drained too
        # sync() with nothing pending is a safe no-op (idempotent).
        comp.sync()
        assert comp._dma_pending is None
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_kc_compositor_async_flush_overlap_logic():
    """Exercise the ASYNC_FLUSH path (#43): when the bus accepts a callback,
    `tx_color` is non-blocking, so _kick_front fires EVERY band (none held back) and
    _drain_dma waits on the completion COUNTER instead of a busy-wait band. Proves:
    async turns on only when register_callback succeeds; the kick fires all bands and
    holds none (`_dma_pending` stays None); the completion ISR counter gates the drain;
    swap recycles the drained buffer; sync() leaves nothing in flight."""
    import types

    fake_alloc = types.ModuleType("kc_alloc")
    fake_alloc.malloc_dma = lambda n, flags=0: bytearray(n)
    fake_lcd = types.ModuleType("lcd_bus")
    fake_lcd.MEMORY_SPIRAM = 1
    fake_lcd.MEMORY_DMA = 2

    class _FB:
        def __init__(self, buf, w, h, fmt):
            self.buf = buf
        def fill(self, c):
            pass
        def fill_rect(self, *a):
            pass
        def text(self, *a):
            pass
    fake_framebuf = types.ModuleType("framebuf")
    fake_framebuf.FrameBuffer = _FB
    fake_framebuf.RGB565 = 1

    saved = {k: sys.modules.get(k) for k in ("kc_alloc", "lcd_bus", "framebuf", "kc_gfx")}
    sys.modules["kc_alloc"] = fake_alloc
    sys.modules["lcd_bus"] = fake_lcd
    sys.modules["framebuf"] = fake_framebuf
    sys.modules.pop("kc_gfx", None)
    try:
        spec = importlib.util.spec_from_file_location(
            "kc_compositor_async", ROOT / "modules" / "kc_compositor.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Defaults on disk are the device defaults; assert them so the revert knob holds.
        src = (ROOT / "modules" / "kc_compositor.py").read_text("utf-8")
        assert "\nASYNC_FLUSH = True" in src
        assert "\nPSRAM_DIRECT_FLUSH = True" in src
        assert module.ASYNC_FLUSH is True
        # This test exercises the BANDED async path (per-band counter logic); the
        # PSRAM-direct single-transfer path is asserted separately at the end.
        module.PSRAM_DIRECT_FLUSH = False

        # A bus that records the registered completion callback and DEFERS completion:
        # tx_color queues (returns immediately, no cb), and complete_all() simulates the
        # SPI ISR firing the callback once per queued band -- the device's real signal.
        class FakeAsyncBus:
            def __init__(self):
                self.colors = []
                self.params = []
                self.cb = None
                self._inflight = 0
            def register_callback(self, cb):
                self.cb = cb
            def tx_param(self, cmd, data):
                self.params.append(cmd)
            def tx_color(self, cmd, data, x0, y0, x1, y1, _z, last):
                self.colors.append((cmd, len(bytes(data)), y0, y1, last))
                self._inflight += 1          # queued, not yet "done"
            def complete_all(self):
                while self._inflight > 0:
                    self._inflight -= 1
                    if self.cb is not None:
                        self.cb()

        bus = FakeAsyncBus()
        comp = module.Compositor(bus, 320, 240, strip_h=24)
        assert comp.double_buffer is True
        assert comp._async is True                  # register_callback succeeded
        assert bus.cb == comp._on_dma_done          # our ISR-safe counter bump

        a = comp._fb
        # FRAME 1: drain(no-op) + swap(front=A, back=B) + kick(A) fires ALL bands async.
        assert comp.back_buffer() is a
        n0 = len(bus.colors)
        comp.flush()
        fired1 = len(bus.colors) - n0
        assert comp._front is a
        assert comp.back_buffer() is comp._fb_b
        assert comp._dma_pending is None            # async holds NOTHING back
        assert comp._dma_target == fired1 >= 2      # every band issued this kick
        assert comp._dma_done_n == 0                # none completed yet (deferred)
        # The overlap unlock (#43): only the FIRST band carries a command (RAMWR); the
        # rest stream with cmd = -1 so esp_lcd doesn't drain inflight between bands.
        kick1 = bus.colors[n0:n0 + fired1]
        assert kick1[0][0] == module.RAMWR
        assert all(c[0] == -1 for c in kick1[1:])

        # The panel DMA finishes during the next frame's render (simulated):
        bus.complete_all()
        assert comp._dma_done_n == comp._dma_target

        # FRAME 2: drain(A -> instant, counters reset) + swap(front=B, back=A) + kick(B).
        n1 = len(bus.colors)
        comp.flush()
        fired2 = len(bus.colors) - n1
        assert comp._front is comp._fb_b
        assert comp.back_buffer() is a              # A recycled, fully drained
        assert comp._dma_pending is None
        assert comp._dma_target == fired2           # reset by drain, then B's bands fired
        assert comp._dma_done_n == 0                # B not yet completed

        # sync() drains B for the SD mutual exclusion, leaving nothing in flight.
        bus.complete_all()
        comp.sync()
        assert comp._dma_target == 0
        assert comp._dma_done_n == 0
        comp.sync()                                 # idempotent no-op
        assert comp._dma_target == 0

        # PSRAM-direct path (#43): the whole frame ships in ONE tx_color (one acquire ->
        # overlap), not N bands. esp_lcd would chunk it internally; at this layer it's a
        # single full-frame call, and the completion ISR fires once -> target == 1.
        module.PSRAM_DIRECT_FLUSH = True
        n2 = len(bus.colors)
        comp.flush()
        fired3 = len(bus.colors) - n2
        assert fired3 == 1                          # ONE tx_color for the whole frame
        assert bus.colors[n2][0] == module.RAMWR    # carries RAMWR; window already armed
        assert comp._dma_target == 1
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_tdeck_keyboard_latches_event_keys_for_hold_window():
    spec = importlib.util.spec_from_file_location(
        "kidcode_firmware_input", ROOT / "modules" / "kidcode" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    state = module.InputState()
    keyboard = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    keyboard.input = state
    keyboard.available = True
    keyboard.raw_mode = False
    keyboard._i2c = object()
    keyboard._held_buttons = ()
    keyboard._held_until_ms = 0
    keys = [ord("d"), 0]
    keyboard._read_key = lambda: keys.pop(0)

    keyboard.poll()
    assert state.held("right")

    keyboard.poll()
    assert state.held("right")

    keyboard._held_until_ms = module._ticks_ms() - 1
    keyboard._read_key = lambda: 0
    keyboard.poll()
    assert not state.held("right")


def test_tdeck_keyboard_reads_raw_matrix_for_real_holds():
    spec = importlib.util.spec_from_file_location(
        "kidcode_firmware_input", ROOT / "modules" / "kidcode" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    state = module.InputState()
    keyboard = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    keyboard.input = state
    keyboard.available = True
    keyboard.raw_mode = True
    raw_frames = [bytes([0, 0x04, 0, 0, 0]), bytes([0, 0x04, 0, 0, 0]), bytes(5)]

    class FakeI2C:
        def readfrom(self, _addr, _size):
            return raw_frames.pop(0)

    keyboard._i2c = FakeI2C()

    keyboard.poll()
    assert state.held("right")
    assert state.last_key == ord("d")

    keyboard.poll()
    assert state.held("right")

    keyboard.poll()
    assert not state.held("right")
    assert state.last_key == 0


def test_tdeck_keyboard_keeps_raw_mode_for_physical_a_bit():
    spec = importlib.util.spec_from_file_location(
        "kidcode_firmware_input", ROOT / "modules" / "kidcode" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    state = module.InputState()
    keyboard = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    keyboard.input = state
    keyboard.available = True
    keyboard.raw_mode = True

    class FakeI2C:
        def readfrom(self, _addr, _size):
            return bytes([0x08, 0, 0, 0, 0])

    keyboard._i2c = FakeI2C()

    keyboard.poll()
    assert state.held("left")
    assert state.last_key == ord("a")
    assert keyboard.raw_mode


def test_tdeck_keyboard_falls_back_when_raw_mode_is_ignored():
    spec = importlib.util.spec_from_file_location(
        "kidcode_firmware_input", ROOT / "modules" / "kidcode" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    state = module.InputState()
    keyboard = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    keyboard.input = state
    keyboard.available = True
    keyboard.raw_mode = True
    keyboard._held_buttons = ()
    keyboard._held_until_ms = 0

    class FakeI2C:
        def readfrom(self, _addr, _size):
            return bytes([ord("d"), 0, 0, 0, 0])

    keyboard._i2c = FakeI2C()

    keyboard.poll()
    assert state.held("right")
    assert state.last_key == ord("d")
    assert not keyboard.raw_mode


def test_tdeck_keyboard_set_game_mode_toggles_raw():
    spec = importlib.util.spec_from_file_location(
        "kidcode_firmware_input", ROOT / "modules" / "kidcode" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    writes = []

    class FakeI2C:
        def writeto(self, _addr, data):
            writes.append(bytes(data))

    kb = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    kb.input = module.InputState()
    kb.available = True
    kb.raw_mode = False
    kb._raw_unsupported = False
    kb._i2c = FakeI2C()
    kb._held_buttons = ()
    kb._held_until_ms = 0
    RAW = module.TDeckKeyboard.RAW_MODE_CMD
    KEY = module.TDeckKeyboard.KEY_MODE_CMD

    # Entering a cart -> raw matrix (0x03) for true hold-to-move.
    kb.set_game_mode(True)
    assert kb.raw_mode and writes == [RAW]

    # Idempotent: no extra I2C traffic while already in the wanted mode.
    kb.set_game_mode(True)
    assert writes == [RAW]

    # Opening the code editor -> back to 1-byte ASCII (0x04) so typing is clean.
    kb.set_game_mode(False)
    assert not kb.raw_mode and writes == [RAW, KEY]

    # A board whose keyboard firmware ignored 0x03 sticks on ASCII: no more retries.
    kb._raw_unsupported = True
    kb.set_game_mode(True)
    assert not kb.raw_mode and writes == [RAW, KEY]


def test_device_canvas_uses_native_kc_gfx():
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    # The hot drawing ops go through the native kc_gfx kernel (fill/fill_rect/
    # blit565) writing into the shared framebuffer, not the per-pixel Python loop,
    # so complex carts stay fast.
    assert "self._gfx = compositor.gfx()" in runtime
    assert "self._gfx.fill(self._buf" in runtime          # cls
    assert "self._gfx.fill_rect(self._buf" in runtime     # rect / circ
    assert "self._gfx.blit565(self._buf" in runtime       # spr
    # Sprites are cached as a pre-scaled RGB565 blit; sheet tiles reuse one Image
    # across frames so the cache is built once, not rebuilt every frame.
    assert "def _cache_rgb(self, img, scale, flip=0):" in runtime
    assert "tile_cache" in runtime
    comp = (ROOT / "modules" / "kc_compositor.py").read_text(encoding="utf-8")
    assert "def gfx(self):" in comp


def test_native_blit_map_wired_for_tilemaps():
    # The tilemap blit (#32) is a native kc_gfx op (one C call per map() region) and
    # DeviceCanvas.map drives it from a baked RGB565 tile atlas, with a Python
    # per-tile fallback when kc_gfx is absent. Grep the frozen device sources +
    # the C module like the other firmware tests.
    c = (ROOT / "native" / "kc_gfx" / "modkc_gfx.c").read_text(encoding="utf-8")
    assert "kc_gfx_blit_map" in c
    assert "MP_ROM_QSTR(MP_QSTR_blit_map)" in c          # registered in the module dict
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    assert "def map(self, tilemap, sheet" in runtime     # DeviceCanvas.map
    assert "self._gfx.blit_map(self._buf" in runtime     # native one-call blit
    assert "def _sheet_atlas(self, sheet, colorkey):" in runtime  # baked RGB565 atlas
    assert "def _map_py(self, tilemap, sheet" in runtime  # no-kc_gfx fallback


def test_native_vector_primitives_wired():
    # circ/circb/line are native kc_gfx ops (#43 follow-up): one C call rasterizes the
    # whole shape (was N per-scanline / per-pixel MP->C calls), with a Python fallback
    # when kc_gfx is absent. Grep the C module + the device canvas wiring.
    c = (ROOT / "native" / "kc_gfx" / "modkc_gfx.c").read_text(encoding="utf-8")
    for fn in ("kc_gfx_circ", "kc_gfx_circb", "kc_gfx_line"):
        assert fn in c
    for q in ("MP_QSTR_circ", "MP_QSTR_circb", "MP_QSTR_line"):
        assert "MP_ROM_QSTR(%s)" % q in c                 # registered in the module dict
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    assert "self._gfx.circ(self._buf" in runtime
    assert "self._gfx.circb(self._buf" in runtime
    assert "self._gfx.line(self._buf" in runtime
    # blit_window is the scroll-engine (Stage 1) primitive -- a flat per-row window copy
    # from a wide pre-rendered background. Landed in the kernel ahead of the engine that
    # consumes it (see the scroll-engine issue); assert it's registered.
    assert "kc_gfx_blit_window" in c
    assert "MP_ROM_QSTR(MP_QSTR_blit_window)" in c
    # The device cart API exposes map/mget/mset, same names as the host make_api.
    assert '"map": map_, "mget": mget, "mset": mset,' in runtime


def test_native_spr_batch_wired_for_sprites():
    # The sprite-batch blit (#43) is a native kc_gfx op (one C call for N sprites, the
    # sprite analogue of blit_map / #32) and DeviceCanvas.spr_batch drives it from the
    # SAME baked RGB565 tile atlas map() uses, with a Python per-item fallback when
    # kc_gfx is absent. Grep the frozen device sources + the C module, like the other
    # firmware tests (this file does not execute device code).
    c = (ROOT / "native" / "kc_gfx" / "modkc_gfx.c").read_text(encoding="utf-8")
    assert "kc_gfx_blit_batch" in c
    assert "MP_ROM_QSTR(MP_QSTR_blit_batch)" in c        # registered in the module dict
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    assert "def spr_batch(self, sheet, items" in runtime  # DeviceCanvas.spr_batch
    assert "self._gfx.blit_batch(self._buf" in runtime    # native one-call blit
    assert "def spr_batch(items" in runtime               # make_api spr_batch
    assert '"spr_batch": spr_batch,' in runtime           # exposed in the cart namespace
    # The batch reuses the map() atlas (one bake, keyed on sheet.gen), not per-sprite.
    assert "atlas, ntiles = self._sheet_atlas(sheet, colorkey)" in runtime
    # Battle City adopts it: the moving sprites go out in one batch (#43).
    battle = (Path("system_carts") / "battle_city.kcart"
              / "main.py").read_text(encoding="utf-8")
    assert "spr_batch(" in battle


def _load_kid_runtime():
    # kid_runtime does `from editors import ...` and `from console import ...`; the
    # device freezes build-staged copies of runtime/{editors,audio,console}.py as
    # top-level modules. Register those same canonical files so the device module
    # loads under CPython (editors + audio first -- console imports both).
    for name in ("editors", "audio", "console"):
        spec = importlib.util.spec_from_file_location(name, Path("runtime") / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod

    # kid_runtime now does `from carts_data import CARTS` (build-generated from
    # system_carts/ -- see tools/gen_device_carts.py). Register the same generated
    # data so the device module execs under CPython.
    sys.path.insert(0, "tools")
    import gen_device_carts
    sys.modules["carts_data"] = gen_device_carts.as_module("system_carts")

    spec = importlib.util.spec_from_file_location(
        "kid_runtime", ROOT / "modules" / "kid_runtime.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_code_editor_edits_buffer():
    CodeEditor = _load_kid_runtime().CodeEditor
    ed = CodeEditor("def _draw():\n    cls(1)\n")
    assert ed.lines == ["def _draw():", "    cls(1)", ""]

    # type at the end of line 1
    ed.row, ed.col = 1, len(ed.lines[1])
    for ch in " hi":
        ed.key(ord(ch))
    assert ed.lines[1] == "    cls(1) hi"
    assert ed.dirty

    # enter splits and carries the indentation (kid-friendly Python)
    ed.row, ed.col = 1, len(ed.lines[1])
    ed.key(0x0D)
    assert ed.lines[2] == "    " and len(ed.lines) == 4

    # backspace at column 0 joins with the previous line
    ed.row, ed.col = 2, 0
    ed.key(0x08)
    assert len(ed.lines) == 3 and ed.lines[1] == "    cls(1) hi    "

    # tab inserts two spaces; control bytes are ignored
    n = len(ed.lines[ed.row])
    assert ed.key(0x09) and len(ed.lines[ed.row]) == n + 2
    assert ed.key(0x01) is False

    # tap-to-place clamps into range and round-trips through text()
    ed.place(999, 999)
    assert ed.row == len(ed.lines) - 1 and ed.col == len(ed.lines[-1])
    assert ed.text() == "\n".join(ed.lines)


def test_code_editor_wired_into_device_shell():
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    carts = (Path("runtime") / "kid_carts.py").read_text(encoding="utf-8")

    # The console + editor cores are shared with the host (imported, not redefined).
    assert "from editors import CodeEditor, PaintEditor, SpriteSheet" in runtime
    assert "from console import NAMES, Pointer, Workstation" in runtime
    # The editor edits the real source and saves it through the (injected) store.
    assert "self.editor = CodeEditor(self.cart[\"src\"])" in console
    assert "def save_code(self):" in console
    assert "self.carts_store.save_code(self.cart, src)" in console
    assert "def save_code(cart, src):" in carts
    # run_desktop injects the device make_api + SD cart store into the shared console.
    assert "ws.make_api = make_api" in runtime
    assert "ws.carts_store = kid_carts" in runtime

    # The console flips the keyboard between ASCII (code editor: clean typing) and
    # the raw matrix (running cart: true hold-to-move) on every screen change. It
    # does NOT poke raw_mode directly or enable raw itself -- it asks the keyboard,
    # which knows whether the firmware supports it.
    assert "kb.set_game_mode(not on)" in console
    assert "kb._enable_raw_mode()" not in console
    inp = (ROOT / "modules" / "kidcode" / "input.py").read_text(encoding="utf-8")
    assert "def set_game_mode(self, on):" in inp       # the per-screen mode toggle
    # The editor/launcher must boot in ASCII -- __init__ never enables raw (raw is
    # only entered later, via set_game_mode, once a cart is running).
    init_src = inp.split("def __init__(self, input_state):", 1)[1].split("\n    def ", 1)[0]
    assert "_enable_raw_mode" not in init_src
    assert "ws.keyboard = keyboard" in runtime


def test_unified_top_bar_wired_into_device_shell():
    """The unified, themeable 18px top bar (Stage 1): the launcher's old 14px status
    strip + the running-cart's labeled button row are now ONE bar of 16x16 IconSheet
    sprites on BOTH screens. The device freezes the SAME runtime/console.py +
    editors.py + kid_carts.py, so grep the canonical sources (staged into modules/ at
    build) for the new wiring."""
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    editors = EDITORS_SRC.read_text(encoding="utf-8")
    carts = (Path("runtime") / "kid_carts.py").read_text(encoding="utf-8")
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")

    # The bar is 18px and drawn by ONE unified drawer -- the old per-screen
    # _draw_desktop_buttons is gone, and the running-cart ("desktop") screen now calls
    # the shared bar.
    assert "_STATUS_H = 18" in console
    assert "def _draw_desktop_buttons" not in console
    assert 'self._draw_status_strip("desktop")' in console

    # The 16x16 IconSheet + its slot map + the bar's icon-blit helper.
    assert "class IconSheet(SpriteSheet):" in editors
    assert "TILE = 16" in editors
    assert "_ICON = {" in console
    assert "def _icon(self, kind, x, y, cv=None):" in console
    assert "self.icon_sheet" in console

    # Storage: load/save the editable theme beside the carts dir (absent = default).
    assert "system_icons" in carts
    assert "def load_system_icons(" in carts
    assert "def save_system_icons(" in carts

    # The device run loop builds + injects the IconSheet the same way as the host.
    assert "ws.load_icon_sheet()" in runtime


def test_icon_theme_editor_wired_into_device_shell():
    """Stage 2 of the themeable top bar: a kid repaints the SYSTEM icon sheet in the
    PAINT editor (Settings -> EDIT ICONS) and it persists. The device freezes the same
    runtime/console.py + kid_carts.py, so grep the canonical sources for the wiring
    that MUST match the working cart-sprite save path (or the device SD bus hangs)."""
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    carts = (Path("runtime") / "kid_carts.py").read_text(encoding="utf-8")
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")

    # Entry point: an "action" Settings row (EDIT ICONS) that opens the theme editor.
    assert '("icons", "EDIT ICONS", "action")' in console
    assert "def open_theme(self):" in console
    assert 'self.menu_view = "theme"' in console
    assert "self._editing_icons" in console

    # Save: the theme editor persists via save_system_icons through the SAME _with_sd
    # wrapper the cart sprite save (save_sprites) uses -- on device that's with_sd_live,
    # the native single-bus path; anything else hangs the panel flush.
    assert "def save_icons(self):" in console
    assert "self.carts_store.save_system_icons(hexs, self.carts_root, _ICON_VERSION)" in console
    assert "self._with_sd(lambda: self.carts_store.save_system_icons(" in console
    # Live re-theme: a save re-adopts the sheet so the bar's per-kind image cache (and
    # the device's per-Image RGB565 blit cache) is dropped and rebuilt from new pixels.
    assert "self.set_icon_sheet(self.icon_sheet)" in console
    assert "def set_icon_sheet(self, sheet):" in console

    # The same persistence wrapper + can_manage gate the device wires for cart saves
    # already covers the theme save -- with_sd_live is the live SD write path.
    assert "ws._with_sd = _with_sd_synced" in runtime
    assert "return kidcode_sd.with_sd_live(fn)" in runtime


def test_device_draw_api_uses_tic80_names():
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")

    # TIC-80 conventions on the device canvas + api: rect/circ filled, rectb/circb
    # outlines, pix for pixels, print for text. The old PICO-8-ish names are gone.
    for name in ("def pix(", "def rect(", "def rectb(", "def circ(", "def circb("):
        assert name in runtime, name
    assert '"rect": canvas.rect, "rectb": canvas.rectb' in runtime
    assert '"circ": canvas.circ, "circb": canvas.circb' in runtime
    assert '"cls": canvas.cls, "pix": canvas.pix' in runtime
    assert '"print": canvas.print' in runtime
    # The canvas no longer exposes the old names (SpriteSheet keeps its own
    # pget/pset for the sheet pixel buffer, which is fine -- check the canvas/api).
    for gone in ("def rectfill(", "def circfill(", "canvas.pset", "canvas.rectfill",
                 '"text": canvas.print'):
        assert gone not in runtime, gone


def test_device_sprite_sheet_and_paint_editor():
    m = _load_kid_runtime()
    S, P = m.SpriteSheet, m.PaintEditor
    sh = S(4, 4)                            # 32x32, 16 sprites
    assert sh.count == 16 and (sh.w, sh.h) == (32, 32) and sh.is_blank()
    assert sh.tile_origin(5) == (8, 8)
    sh.tset(5, 1, 2, 9)
    assert sh.tget(5, 1, 2) == 9 and sh.dirty
    sh2 = S.from_hex(sh.to_hex(), 4, 4)     # hex round-trips, dirty resets
    assert sh2.pix == sh.pix and sh2.dirty is False
    pe = P(sh)
    pe.color = 12
    pe.paint(0, 0)
    assert sh.tget(0, 0, 0) == 12
    pe.pick(0, 0)
    assert pe.color == 12
    pe.select(-1)
    assert pe.n == sh.count - 1


def test_device_spr_is_sheet_indexed_and_accepts_image():
    m = _load_kid_runtime()
    sheet = m.SpriteSheet(4, 4)
    sheet.tset(3, 0, 0, 11)
    calls = []

    class StubCanvas:
        w = 320
        h = 240

        def spr(self, img, x, y, scale=1, flip=0):
            calls.append((img.w, img.h, x, y, scale, flip))

        def __getattr__(self, name):
            return lambda *a, **k: 0

    class StubInput:
        def held(self, name):
            return False

        def pressed(self, name):
            return False

    api = m.make_api(StubCanvas(), StubInput(), {}, sheet)
    api["spr"](3, 100, 60)                  # TIC-80 indexed sprite from the sheet
    assert calls[-1] == (8, 8, 100, 60, 1, 0)
    api["spr"](m.Image.from_ascii(["#"], {"#": 7}), 8, 9, scale=4)  # Image still works
    assert calls[-1] == (1, 1, 8, 9, 4, 0)
    # Multi-tile span (#30): spr(n, x, y, w=2, h=2) blits a 16x16 image from the
    # sheet (the device path, so host == device for larger sprites).
    api["spr"](0, 12, 14, w=2, h=2)
    assert calls[-1] == (16, 16, 12, 14, 1, 0)
    # Flip (#11): spr(n, x, y, scale=1, flip=3) forwards flip to canvas.spr.
    api["spr"](3, 5, 6, -1, 1, 3)
    assert calls[-1] == (8, 8, 5, 6, 1, 3)


def test_device_paint_editor_sizes_and_spans(tmp_path=None):
    # The shared PaintEditor's larger-sprite support (#30) under the device modules:
    # cycle_size steps 1->2->3, the 2x2 region writes the four constituent tiles,
    # and tile_span_image builds the contiguous block.
    m = _load_kid_runtime()
    cols = 16
    sh = m.SpriteSheet(cols, 16)
    pe = m.PaintEditor(sh)
    assert pe.size == 1 and pe.dim == 8
    pe.cycle_size(); assert pe.size == 2 and pe.dim == 16
    pe.color = 7
    pe.paint(10, 11)                        # bottom-right tile of the 2x2 block
    assert sh.tget(cols + 1, 2, 3) == 7
    img = sh.tile_span_image(0, 2, 2)
    assert (img.w, img.h) == (16, 16) and img.pix[11 * 16 + 10] == 7


def test_device_make_api_map_mget_mset(tmp_path=None):
    # The device cart API exposes map()/mget()/mset() bound to the injected TileMap
    # (#32): mget/mset round-trip through it, and map() forwards to canvas.map with
    # the cart's tilemap + sheet. Exercised under CPython via the frozen modules.
    m = _load_kid_runtime()
    from editors import TileMap
    sheet = m.SpriteSheet(4, 4)
    tm = TileMap(3, 3)
    mapped = []

    class StubCanvas:
        w = 320
        h = 240

        def map(self, tilemap, sheet, *args):
            mapped.append((tilemap, sheet, args))

        def __getattr__(self, name):
            return lambda *a, **k: 0

    class StubInput:
        def held(self, name):
            return False

        def pressed(self, name):
            return False

    api = m.make_api(StubCanvas(), StubInput(), {}, sheet, None, tm)
    api["mset"](1, 2, 5)
    assert api["mget"](1, 2) == 5 and tm.mget(1, 2) == 5
    api["map"](0, 0, 3, 3, 0, 0, -1, 2)
    assert mapped and mapped[-1][0] is tm and mapped[-1][1] is sheet
    assert mapped[-1][2] == (0, 0, 3, 3, 0, 0, -1, 2)
    # With no tilemap injected, the API stays callable (map() no-ops, mget -> -1).
    api2 = m.make_api(StubCanvas(), StubInput(), {}, sheet)
    assert api2["mget"](0, 0) == -1
    api2["map"](0, 0)                       # no crash, draws nothing


def test_sprite_sheet_pset_bumps_gen():
    # pset bumps a generation counter so a running cart's tile cache can detect a
    # sprite edit and rebuild (host/device parity for live sprite edits).
    SpriteSheet = _load_kid_runtime().SpriteSheet
    sh = SpriteSheet(4, 4)
    assert sh.gen == 0
    sh.pset(0, 0, 5)
    assert sh.gen == 1
    sh.pset(1, 0, 6)
    assert sh.gen == 2
    # An out-of-bounds pset is a no-op and must not bump gen.
    sh.pset(-1, 0, 7)
    sh.pset(sh.w, 0, 7)
    assert sh.gen == 2
    # tset routes through pset, so it bumps too.
    sh.tset(0, 2, 2, 9)
    assert sh.gen == 3


def test_device_tile_cache_invalidated_on_sprite_edit():
    # The device tile cache (and each Image's RGB565 blit cache) snapshots a tile's
    # pixels. After a kid edits a sprite, the running cart must re-blit fresh art,
    # not the stale cached Image. make_api watches the sheet's gen counter and
    # clears the cache when it changes.
    m = _load_kid_runtime()
    sheet = m.SpriteSheet(4, 4)
    sheet.tset(0, 0, 0, 3)
    blitted = []

    class StubCanvas:
        w = 320
        h = 240

        def spr(self, img, x, y, scale=1, flip=0):
            blitted.append(img)

        def __getattr__(self, name):
            return lambda *a, **k: 0

    class StubInput:
        def held(self, name):
            return False

        def pressed(self, name):
            return False

    api = m.make_api(StubCanvas(), StubInput(), {}, sheet)
    api["spr"](0, 0, 0)
    first = blitted[-1]
    # Same sheet, same id, no edit -> the cached Image is reused (object identity).
    api["spr"](0, 0, 0)
    assert blitted[-1] is first

    # A paint edit bumps sheet.gen -> the cache is invalidated and a fresh Image
    # (rebuilt from the new pixels) is blitted next frame.
    sheet.pset(0, 0, 5)
    api["spr"](0, 0, 0)
    assert blitted[-1] is not first


def test_device_sprite_storage_wired():
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    carts = (Path("runtime") / "kid_carts.py").read_text(encoding="utf-8")
    # device cart API -- also takes the injected audio backend (#16) + tilemap
    # (#32) + persistent memory (pmem, #11).
    # make_api now also takes the capability-gated wifi backend LAST (#38).
    assert "def make_api(canvas, input, config, sheet=None, audio=None," in runtime
    assert "pmem=None, wifi=None):" in runtime
    assert "self.sheet = self._build_sheet()" in console                   # shared console
    assert "self.carts_store.save_sprites(self.cart, hexs)" in console
    assert "def save_sprites(cart, hex_text):" in carts
    assert '"sprites": sprites' in carts


def test_device_audio_wired():
    # Audio core (#16): shared model/mixer (runtime/audio.py) + device I2S backend
    # stub + host==device API surface + sounds.json storage. Source-level checks
    # mirror how the other firmware tests grep the frozen device modules.
    audio = (Path("runtime") / "audio.py").read_text(encoding="utf-8")
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    carts = (Path("runtime") / "kid_carts.py").read_text(encoding="utf-8")
    build = (ROOT / "build.sh").read_text(encoding="utf-8")

    # The shared audio core: dependency-light (math only) synth + mixer + model.
    assert "class AudioEngine:" in audio
    assert "def render(self, nframes):" in audio
    # The allocation-free render core the device backend feeds I2S from (#16): the
    # device reuses one persistent buffer, so render_into must exist and render()
    # must delegate to it (keeps host bytes-returning behavior identical).
    assert "def render_into(self, out, nframes):" in audio
    assert "self.render_into(out, nframes)" in audio
    assert "class AudioBank:" in audio
    # The console builds a per-cart AudioEngine and injects an audio backend.
    assert "from audio import" in console
    assert "AudioBank" in console and "AudioEngine" in console
    assert "def _build_audio(self):" in console
    assert "self.audio.tick(dt)" in console
    # The device make_api binds the same six audio names as the host.
    for name in ('"sfx": _sfx', '"beep": _beep', '"music": _music',
                 '"music_stop": _music_stop', '"sound_stop": _sound_stop',
                 '"volume": _volume'):
        assert name in runtime, name
    # The device I2S backend is wired in (NEEDS ON-DEVICE VERIFICATION).
    assert "class DeviceAudio:" in runtime
    assert "from machine import I2S, Pin" in runtime
    assert "mode=I2S.TX" in runtime
    assert "ws.make_audio = make_audio" in runtime
    assert "NEEDS ON-DEVICE VERIFICATION" in runtime
    # The feed must be NON-BLOCKING: irq() flips the I2S port into non-blocking mode
    # and a completion flag gates the next write, so write() can never stall the
    # single-threaded render loop (the cause of the reported FPS drop / crackle).
    assert "self.i2s.irq(self._on_done)" in runtime
    assert "self.engine.render_into(buf, n)" in runtime
    assert "if self._busy:" in runtime
    # sounds.json storage in the shared cart store.
    assert "def save_sounds(cart, bank_dict):" in carts
    assert '"sounds": sounds' in carts
    # build.sh stages the shared audio module into the frozen modules tree.
    assert 'cp "${REPO_ROOT}/runtime/audio.py" "${SCRIPT_DIR}/modules/audio.py"' in build


def test_music_editor_wired_into_device_shell():
    # Music/sound editor (#50): the shared MusicEditor core (runtime/editors.py) +
    # its console wiring (a menu_view, the top-bar switcher, save to sounds.json, the
    # live preview). It lives in the SAME shared files build.sh freezes onto the
    # device, so source-level greps prove it's on both ends (host == device).
    editors = EDITORS_SRC.read_text(encoding="utf-8")
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    carts = (Path("runtime") / "kid_carts.py").read_text(encoding="utf-8")
    build = (ROOT / "build.sh").read_text(encoding="utf-8")

    # The editor CORE is a single shared class (not redefined on the device).
    assert "class MusicEditor:" in editors
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    assert "class MusicEditor:" not in runtime, "device redefines MusicEditor"
    # editors.py must stay dependency-free (the frozen-module contract): it must NOT
    # import audio just to edit the bank -- SFX/MusicTrack are injected as factories.
    assert "import audio" not in editors
    assert "from audio import" not in editors

    # The console imports the shared core + the audio factories it injects.
    assert "MusicEditor" in console
    assert "from audio import" in console and "MusicTrack" in console and "SFX" in console
    # A new menu sub-view + its open/build path, mirroring map/blocks.
    assert 'self.musicedit = MusicEditor(bank' in console
    assert "def _open_music(self):" in console
    assert 'elif view == "music":' in console
    assert 'if self.menu_view == "music":' in console     # input + frame dispatch
    # The top-bar mode switcher (the 6th icon) + its tap action + drawn icon.
    assert "_MUSIC_BTN = (" in console
    assert "self._open_music()" in console
    assert 'self._icon("music"' in console
    assert '"music": 15' in console                        # IconSheet slot for the icon
    # SAVE persists to sounds.json through the existing shared store.
    assert "def save_sounds(self):" in console
    assert "self.carts_store.save_sounds(self.cart, bank_dict)" in console
    assert "def save_sounds(cart, bank_dict):" in carts
    # Live preview drives the SAME injected AudioEngine the cart uses, and the frame
    # loop ticks the mixer + keeps animating while a preview is up.
    assert "def _play_music_preview(self):" in console
    assert "self.audio.tick(dt)" in console
    # The editor lives in the shared files build.sh freezes onto the device.
    assert 'cp "${REPO_ROOT}/runtime/editors.py" "${SCRIPT_DIR}/modules/editors.py"' in build
    assert 'cp "${REPO_ROOT}/runtime/console.py" "${SCRIPT_DIR}/modules/console.py"' in build


def test_native_kc_audio_mixer_wired():
    # Native PCM mixer (#16): the per-sample software mix in runtime/audio.py is the
    # device bottleneck (~12 FPS, crackle), so the heavy inner loop moves into the C
    # module native/kc_audio (mirror of kc_gfx/kc_sd). DeviceAudio PREFERS it and
    # falls back to the Python render_into when it isn't frozen in. Source-level
    # checks, the same way the other firmware tests grep the device sources.
    c = (ROOT / "native" / "kc_audio" / "modkc_audio.c").read_text(encoding="utf-8")
    cmake = (ROOT / "native" / "kc_audio" / "micropython.cmake").read_text(encoding="utf-8")
    build = (ROOT / "build.sh").read_text(encoding="utf-8")
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")

    # The C module exposes the per-block kernel API + registers as `kc_audio`.
    assert "MP_REGISTER_MODULE(MP_QSTR_kc_audio" in c
    assert "kc_audio_voice_set" in c          # push exact Python _Voice state into C
    assert "kc_audio_voice_read" in c         # read advanced render state back out
    assert "kc_audio_render" in c             # the heavy per-sample mix loop
    # cmake links the usermod the kc_gfx/kc_sd way.
    assert "target_link_libraries(usermod INTERFACE usermod_kc_audio)" in cmake
    # build.sh stages it into ext_mod next to kc_gfx/kc_sd (re-staged every build).
    assert "ext_mod/kc_audio" in build
    assert "kc_audio/micropython.cmake" in build

    # DeviceAudio prefers the native mixer but keeps a Python fallback so a build
    # WITHOUT kc_audio still works (and the host is unaffected).
    assert "import kc_audio" in runtime
    assert "self._kc_audio = kc_audio" in runtime
    assert "self._kc_audio = None" in runtime                      # fallback branch
    assert "def _render_native(self, buf, n):" in runtime
    assert "if self._kc_audio is not None:" in runtime
    assert "self._render_native(buf, n)" in runtime
    assert "self.engine.render_into(buf, n)" in runtime            # Python fallback path
    # The native path keeps the Python engine the source of truth: it still runs the
    # music scheduler in Python and pushes/reads voice state around the C mix.
    assert "eng._advance_music" in runtime
    assert "ka.voice_set(" in runtime
    assert "ka.render(buf, n, eng.rate, eng.volume)" in runtime
    assert "ka.voice_read(c)" in runtime


def test_native_kc_audio_core1_task_wired():
    # CRACKLE FIX (#41): the I2S feed used to be coupled to the render loop -- tick()
    # fed I2S once per ~50-80 ms frame on core 0, so a long draw under-ran the DMA ->
    # crackle. The fix is a dedicated native C task PINNED TO CORE 1 that owns the IDF
    # i2s_std channel and feeds it continuously, decoupled from rendering. core 0 (the
    # MicroPython VM) cannot run Python on core 1; only a pure-C task can. Source-level
    # checks, the same way the other firmware tests grep the device sources.
    c = (ROOT / "native" / "kc_audio" / "modkc_audio.c").read_text(encoding="utf-8")
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    audio_src = (Path("runtime") / "audio.py").read_text(encoding="utf-8")

    # The C side spawns a FreeRTOS task PINNED TO CORE 1 that owns the I2S write loop.
    assert "xTaskCreatePinnedToCore(" in c
    assert "kc_audio_task" in c            # the core-1 feeder task body
    # pinned to core 1 (the last xTaskCreatePinnedToCore arg); the comment names it too
    assert "1 /* core 1 */" in c
    assert "core 1" in c
    # The task owns the IDF i2s_std channel (separate from machine.I2S) + writes it.
    assert "i2s_new_channel(" in c
    assert "i2s_channel_init_std_mode(" in c
    assert "i2s_channel_write(" in c
    # The cross-core voice handoff is mutex-protected (core 0 writes, core 1 reads):
    # a torn read / use-after-free is NOT acceptable (a momentary glitch is).
    assert "xSemaphoreCreateMutex(" in c
    assert "xSemaphoreTake(" in c
    assert "xSemaphoreGive(" in c
    # The core-1 task must NEVER call into the MicroPython runtime (no MP heap/GIL from
    # core 1) -- it mixes from a plain-C snapshot of the shared voices.
    assert "kc_mix_block(snap" in c
    # MP control surface for the task: start (returns False -> fallback), stop, the
    # commit lock, the live master volume, and the published active mask.
    for fn in ("kc_audio_audio_start", "kc_audio_audio_stop", "kc_audio_voice_lock",
               "kc_audio_voice_unlock", "kc_audio_set_master", "kc_audio_active_mask"):
        assert fn in c, fn
    for name in ('MP_QSTR_audio_start', 'MP_QSTR_audio_stop', 'MP_QSTR_voice_lock',
                 'MP_QSTR_voice_unlock', 'MP_QSTR_set_master', 'MP_QSTR_active_mask'):
        assert name in c, name

    # The Python DeviceAudio prefers the core-1 task and exposes a revert flag.
    assert "KC_AUDIO_CORE1 = True" in runtime          # default-on, the crackle fix
    assert "if KC_AUDIO_CORE1 and self._kc_audio is not None:" in runtime
    assert "self._kc_audio.audio_start(I2S_BCK, I2S_WS, I2S_DOUT, AUDIO_RATE)" in runtime
    assert "self._core1 = True" in runtime
    # In core-1 mode tick() does NO per-frame I2S write / per-sample mix -- it only
    # schedules + commits voice state across to the C task.
    assert "def _tick_core1(self, dt):" in runtime
    assert "if self._core1:" in runtime
    assert "ka.voice_lock()" in runtime
    assert "ka.voice_unlock()" in runtime
    assert "ka.active_mask()" in runtime
    assert "ka.voice_set(c, v.active, v.steps, v.step_dur, v.loop," in runtime
    # BATTLE CITY FIX (#41): commit is keyed off the monotonic _Voice.gen counter, NOT
    # (id(steps), active). id(steps) aliases on a GC list-address reuse, so a rapid
    # same-SFX retrigger read as "unchanged" and was never committed (silent). gen
    # bumps on every play()/stop(), so every rapid/overlapping sfx commits.
    assert "self._commit_gen" in runtime               # gen-keyed dirty tracking
    assert "self.gen" in audio_src                      # _Voice.gen counter (audio.py)
    assert "voices[c].gen != self._commit_gen[c]" in runtime
    assert "self._commit_gen[c] = v.gen" in runtime
    # The mask-clear must NOT clobber a voice the cart re-triggered this frame: it only
    # honours a done-clear when gen still matches the last commit (no pending trigger).
    assert "v.gen == self._commit_gen[c]" in runtime
    assert "self.gen += 1" in audio_src                 # bumped on play() AND stop()
    # The legacy single-core feed stays as the FALLBACK (machine.I2S) so a bad result
    # is revert-able (KC_AUDIO_CORE1=False) and a no-kc_audio build still works. It now
    # TOPS the deep DMA ring UP toward full each tick (the single-core crackle fix)
    # instead of feeding exactly rate*dt (which kept the ring near-empty -> under-ran).
    assert "legacy single-core feed (fallback)" in runtime
    assert "if not self._core1:" in runtime            # only open machine.I2S in fallback
    assert "self.i2s = I2S(" in runtime
    assert "AUDIO_IBUF_FRAMES = AUDIO_IBUF // 2" in runtime
    assert "self._buffered" in runtime                  # software ring-occupancy estimate
    assert "want = AUDIO_IBUF_FRAMES - self._buffered" in runtime
    assert "self._buffered += n" in runtime             # account for what we wrote
    # AUDIO DIAGNOSTICS: each sfx/music trigger logs an event line; core-1 logs a
    # rate-limited active=/committed= sample so Battle City's audio is debuggable blind.
    assert "AUDIO_DIAG = True" in runtime
    assert "def _diag_trigger(self, kind, n, chan):" in runtime
    assert "def _diag_core1_sample(self, mask):" in runtime
    assert '_diag_note("AUDIO"' in runtime
    assert "core1 active=%d committed=%d" in runtime


def test_device_wifi_wired():
    # WiFi (#38): the device network.WLAN service backend + capability-gated `wifi`
    # injection + autoconnect + the shared credential store. Source-level checks
    # mirror how the other firmware tests grep the frozen device modules.
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    carts = (Path("runtime") / "kid_carts.py").read_text(encoding="utf-8")

    # make_api takes the gated wifi backend LAST and injects `wifi` only when set.
    assert "def make_api(canvas, input, config, sheet=None, audio=None," in runtime
    assert "pmem=None, wifi=None):" in runtime
    assert 'ns["wifi"] = wifi' in runtime
    # The device WLAN backend (STUB -- needs hardware verification). LAZY: the WLAN
    # stack is brought up on demand (scan/connect), NEVER at boot -- bringing it up at
    # boot reserved the internal RAM the LCD DMA flush needs and froze the desktop
    # (OSError 257 / ESP_ERR_NO_MEM). The radio comes up only via _ensure_wlan.
    assert "class DeviceWifi:" in runtime
    assert "def _ensure_wlan(self):" in runtime           # lazy radio bring-up
    assert "network.WLAN(network.STA_IF)" in runtime       # (lives inside _ensure_wlan now)
    assert "def make_wifi(store=None, root=None):" in runtime
    assert "def autoconnect_wifi(wifi):" in runtime        # still defined, but NOT called at boot
    assert "NEEDS ON-DEVICE VERIFICATION" in runtime
    # run_desktop wires the system service but does NOT bring WiFi up at boot (WLAN
    # reserves the internal RAM the LCD DMA needs -- WiFi<->display coexistence is #38).
    assert "ws.wifi = make_wifi(kid_carts, carts_root)" in runtime
    # autoconnect is NOT called eagerly at boot; it is only reused, deferred, by the OTA
    # online-update path (#53) via the go_online lambda -- never as a bare boot statement.
    assert "go_online=lambda: autoconnect_wifi(ws.wifi)" in runtime
    assert runtime.count("autoconnect_wifi(ws.wifi)") == 1
    # Each frame is guarded so one bad flush can't brick the device.
    assert "KidCode frame error:" in runtime
    # The shared console gates injection on the "network" manifest permission.
    assert "def _cart_has_perm(self, name):" in console
    assert 'self.wifi if self._cart_has_perm("network") else None' in console
    # The shared cart store carries permissions + persists known networks.
    assert '"permissions": man.get("permissions", []),' in carts
    assert "def load_wifi(root=CARTS_DIR):" in carts
    assert "def save_wifi(networks, root=CARTS_DIR):" in carts
    assert "def remember_wifi(ssid, password, root=CARTS_DIR):" in carts
    assert "def forget_wifi(ssid, root=CARTS_DIR):" in carts


def test_editor_cores_are_shared_single_source():
    # One canonical file (runtime/editors.py); the device imports it and the build
    # stages it into the frozen modules tree -- no duplicated class definitions.
    editors = EDITORS_SRC.read_text(encoding="utf-8")
    for cls in ("class CodeEditor:", "class SpriteSheet:", "class PaintEditor:"):
        assert cls in editors, cls
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    canvas = Path("runtime/canvas.py").read_text(encoding="utf-8")
    # Neither backend redefines the shared cores.
    for cls in ("class CodeEditor:", "class SpriteSheet:", "class PaintEditor:"):
        assert cls not in runtime, "device redefines " + cls
    assert "class SpriteSheet:" not in canvas, "host canvas redefines SpriteSheet"
    # build.sh stages the canonical file into modules/ so the device freezes it.
    build = (ROOT / "build.sh").read_text(encoding="utf-8")
    assert 'cp "${REPO_ROOT}/runtime/editors.py" "${SCRIPT_DIR}/modules/editors.py"' in build


def test_micropython_offline_diag_wiring():
    """Offline on-device diagnostics (kidcode_diag): a RAM ring persisted to SD and
    dumped to serial at the NEXT boot, since run_desktop's takeover loop starves USB
    serial. Grep the frozen device sources for the boot-dump, the with_sd_live flush,
    and the perf-sample wiring (the firmware tests assert structure, not execution)."""
    diag = (ROOT / "modules" / "kidcode_diag.py").read_text(encoding="utf-8")
    shell = (ROOT / "modules" / "kidcode_shell.py").read_text(encoding="utf-8")
    runtime = (ROOT / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")

    # The diag module exists with the bounded ring + the stable dump markers + the
    # single-file (one-session) log path.
    assert "class _Ring(object):" in diag
    assert 'DUMP_HEADER = "===== KidCode diag dump (previous session) ====="' in diag
    assert 'DUMP_FOOTER = "===== end diag dump ====="' in diag
    assert 'LOG_PATH = "/sd/kidcode/diag.log"' in diag
    assert "ENABLED = True" in diag                       # default-on, documented toggle
    # The on/off toggle is documented as how to disable.
    assert "DISABLE" in diag
    # log() echoes every line live to stdout (the empirical loop-serial test) in
    # addition to buffering -- so reading /dev/ttyACM* DURING a run is a direct test
    # of whether the takeover loop actually starves serial.
    assert "ECHO_LIVE = True" in diag
    assert 'print("KidCode", line)' in diag
    # No rotation / second file (owner: the file is just the most-recent session).
    assert "diag.prev.log" not in diag

    # Boot dump runs in main() BEFORE init_display() -- the bus-safe pre-display
    # window where serial is alive and machine.SDCard mounting is safe.
    assert "def dump_previous_to_serial(" in diag
    assert "_dump_diag()" in shell
    main_src = shell.split("def main(", 1)[1].split("def ", 1)[0]
    assert main_src.index("_dump_diag()") < main_src.index("_init_display()")
    # The boot read uses the pre-display machine.SDCard path (kidcode_sd.with_sd),
    # NOT the live native path -- documented as the safe pre-display read.
    assert "kidcode_sd.with_sd(" in diag
    assert "BEFORE init_display" in diag

    # Periodic SD flush goes through the live single-bus path (with_sd_live), runs
    # between frames, and overwrites the whole ring (one session per file).
    assert "def flush_to_sd(with_sd):" in diag
    assert 'open(LOG_PATH, "w")' in diag                  # overwrite, never append
    assert "_diag_flush(diag, ws)" in runtime
    assert 'with_sd = getattr(ws, "_with_sd", None)' in runtime   # = with_sd_live
    assert "diag.flush_to_sd(with_sd)" in runtime

    # Perf samples: a structured PERF line sampled every few seconds while a cart
    # runs (the per-cart frame-timing payload for the offline dump).
    assert "def format_perf(cart, fps, flush_ms, draw_ms):" in diag
    assert 'return "PERF cart=%s fps=%d flush=%d draw=%d" % (' in diag
    assert "_diag_perf_sample(diag, ws)" in runtime
    assert "ws.perf_sample()" in runtime
    # The shared console EXPOSES the numbers host-safely; the device SAMPLES them.
    assert "def perf_sample(self):" in console
    assert "self.perf_capture = False" in console         # default off -> host identical
    assert "ws.perf_capture = True" in runtime            # device turns capture on
    assert "_perf = self.perf_hud or self.perf_capture" in console

    # DRAWBRK (#43 follow-up): the phase split of draw= into cart _update (logic) /
    # cart _draw (render) / audio.tick / console chrome, sampled alongside PERF so we
    # can see where the per-frame draw cost actually goes instead of guessing.
    assert "def perf_breakdown(self):" in console
    assert 'diag.log("DRAWBRK", "logic=%.2f render=%.2f audio=%.2f chrome=%.2f"' in runtime
    assert "_diag_drawbrk(diag, ws)" in runtime
    assert "ws.perf_breakdown()" in runtime

    # Existing diagnostics routed through diag (printed AND persisted): boot heap,
    # the frame-error trace, the in-cart crash, and the audio I2S status line.
    assert '_diag_log("mem",' in runtime
    assert '_diag_log("frame error", exc, diag)' in runtime
    assert '_diag_log("cart error", _ce, diag)' in runtime
    assert '_diag_note("audio", "I2S' in runtime


def test_ota_two_channel_wired():
    # #53 two-channel OTA: kc_ota learns its channel from a build-stamped _ota_build,
    # offers cross-channel switches, and the manifest fetch is channel-aware; the shared
    # console exposes a CHANNEL Settings toggle; build.sh stamps the channel. Device code
    # isn't executed here (host offer-logic is in test_ota_manifest), so grep the sources.
    kc = (ROOT / "modules" / "kc_ota.py").read_text(encoding="utf-8")
    assert "FIRMWARE_CHANNEL" in kc
    assert "import _ota_build" in kc                     # build-stamped identity
    assert "def channel(self):" in kc
    assert "def offers(self, manifest" in kc
    assert "def version_label(self):" in kc
    assert "def manifest_url(self, channel=None):" in kc
    assert "def check_online(self, channel=None):" in kc
    # The shared console (staged to the device) drives the channel toggle + flow.
    console = Path("runtime/console.py").read_text(encoding="utf-8")
    assert '("ota_channel", "CHANNEL", "channel")' in console
    assert "def _cycle_channel(self, d):" in console
    assert "u.offers(manifest, ch)" in console
    # build.sh stamps the channel into a generated _ota_build module + dist manifest.
    build = (ROOT / "build.sh").read_text(encoding="utf-8")
    assert "KIDCODE_OTA_CHANNEL" in build
    assert "_ota_build.py" in build
    assert "ota_build.json" in build
