import importlib.util
import sys
from pathlib import Path

from tools import board_config


ROOT = Path("firmware/lilygo_t_deck_plus_mainline")
# The device tier moved out of the board directory when the fork went
# (it was never T-Deck-specific -- the P4 staged it from there too).
DEVICE = Path("device")
# The shared C modules moved out of the board directory with the fork;
# only moy_lcd (this board's panel driver) stays under ROOT/native.
NATIVE = Path("native")
PATCHES = Path("patches")
EDITORS_SRC = Path("runtime") / "editors.py"
_REPO = Path(__file__).resolve().parent.parent


def _staged():
    """The shared modules a FRESH T-Deck build freezes, by destination name.

    Since #161 Phase 3, `build.sh` stages every `runtime/*.py` except the files
    `board.toml` denies (each with its reason recorded there) -- so there is no
    per-module `cp` line left to grep for, and the greps below ask the staged
    set instead. That was always the question they meant.
    """
    from tools.board_config import staged_modules
    return staged_modules(_REPO / ROOT, _REPO)


def _device_backend_src():
    """The device backend's source, as the greps below mean it.

    Three files now, for two different reasons. `make_api` moved to
    `device_api.py` (#58, staged to every device target), and the BOOT SPINE +
    FRAME PUMP moved to the shared `runtime/device_boot.py` (#161 Phase 4/5) --
    the boot splash, the cart seed+scan, the Lua probe, the OTA verdict and the
    frame cadence were the last things this console wrote twice. `run_desktop`
    and everything that is this board's own hardware stay in `moy_runtime.py`.

    device_boot is read through `_staged()` rather than by path on purpose: that
    asserts it really is staged onto this board. A spine that is not frozen is a
    board that cannot boot, which a grep against `runtime/` would not notice.
    """
    return "\n".join((
        (ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8"),
        (DEVICE / "device_api.py").read_text(encoding="utf-8"),
        # THE make_api body since 2026-08-17 (one copy for every tier);
        # device_api is its device-side re-export home.
        Path("runtime/cart_api.py").read_text(encoding="utf-8"),
        _staged()["device_boot.py"].read_text(encoding="utf-8"),
    ))


def _editors_src():
    """The editor cores' combined source: editors.py is the re-exporting
    umbrella since the per-editor split (editors_base/_code/_sheet/_paint_map/
    _block/_music) -- greps that pin class definitions read all of them."""
    parts = [EDITORS_SRC.read_text(encoding="utf-8")]
    for name in ("editors_base", "editors_code", "editors_sheet",
                 "editors_paint_map", "editors_block", "editors_music"):
        parts.append((Path("runtime") / (name + ".py")).read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_micropython_spike_scaffold_exists():
    assert (ROOT / "README.md").exists()
    assert (ROOT / "build.sh").exists()
    assert (ROOT / "modules" / "main.py").exists()
    assert (DEVICE / "moybyte" / "__init__.py").exists()


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
    ota = (DEVICE / "moy_ota.py").read_text(encoding="utf-8")

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
    # See _device_backend_src: moy_runtime + device_api + the shared boot spine
    # are the surface these greps mean. The updater is still CONSTRUCTED here
    # (its SD gate is this board's alone); what it does afterwards -- the boot
    # verdict and the rollback confirm -- is the spine's OtaHealth.
    runtime = _device_backend_src()

    assert "import moy_ota" in runtime
    assert "ws.updater = moy_ota.OtaUpdater(_with_sd_synced)" in runtime
    # The rollback confirm is made from the FRAME LOOP, once the console has
    # actually painted -- not on the boot path, where "the desktop was built"
    # would confirm an image that never reaches the glass (#56). See
    # tests/test_ota_health.py for the behaviour; this pins the wiring.
    assert 'confirm_when_healthy(getattr(self.ws, "_frames_drawn", 0))' in runtime
    assert "boot_check()" in runtime
    assert "ws.updater.mark_valid()" not in runtime


def test_console_settings_has_firmware_update_screen():
    # The shared console owns all OTA pixels (host == device): a Settings UPDATE FW row
    # (shown only when an updater is injected and OTA-capable) drives a confirm/progress
    # screen. The host injects no updater, so the row never appears there.
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    settings_layer = (Path("runtime") / "settings_layer.py").read_text(encoding="utf-8")
    # The update SCREEN itself now lives in update_ui.py (UpdateUI, extracted from
    # console.py); the queries/config + dispatch stay in console.py.
    update_ui = (Path("runtime") / "update_ui.py").read_text(encoding="utf-8")

    assert "self.updater = None" in console
    assert "def _update_available" in console
    assert "def _settings_rows" in settings_layer
    assert '"UPDATE FW"' in settings_layer
    assert "def open_update" in update_ui
    assert "def _pump_update" in update_ui
    assert "def _draw_update" in update_ui
    # Stage 6d routed the console's screen readers at the WM stack top, so the update
    # branch is now `kind == "update"` (kind = self.wm.top_kind()) not `self.screen ==`.
    assert 'kind == "update"' in console
    assert "def _activate_settings_action" in settings_layer


def test_web_stack_split_after_the_streaming_sunset():
    # The 2026-08 streaming sunset (moycore plan 3.2) and its completion at
    # stage 4: the recording stack is GONE (absence pins in
    # test_streaming_sunset.py); what survives on the device side is a bare
    # socket/HTTP/WS transport core with no recorder coupling, which the 3.4
    # sync RPC rides. This greps the PRESENCE side. Executable behaviour:
    # test_moy_webserver.py.
    wv_ws = (Path("runtime") / "web_view_ws.py").read_text(encoding="utf-8")
    web = (DEVICE / "moy_webserver.py").read_text(encoding="utf-8")

    # -- the WS primitives leaf (web_view_ws): what the transport rides --
    assert "def ws_accept_key" in wv_ws and "def ws_handshake_response" in wv_ws
    assert "def ws_encode" in wv_ws and "def ws_decode" in wv_ws
    assert "258EAFA5-E914-47DA-95CA-C5AB0DC85B11" in wv_ws
    assert "Switching Protocols" in wv_ws

    # -- the DEVICE transport core (moy_webserver): sockets only, no recorder --
    assert "import web_view_ws" in web          # framing from the leaf, not web_view
    assert "class WebServer" in web
    assert "class _WSConn" in web
    assert "setblocking(False)" in web          # NON-blocking listening socket
    assert "def handle_http" in web             # the 3.4 sync RPC's endpoint seam
    assert "def send_text" in web               # ...and its push verb


def test_ota_online_download_streams_to_sd_with_checksum():
    # OTA Phase 3 (#53): a WiFi download fetches a manifest, streams the .bin straight
    # to SD (never buffering the whole image), and verifies sha256 before installing.
    ota = (DEVICE / "moy_ota.py").read_text(encoding="utf-8")

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
    # moy_runtime + device_api together are the device backend surface the
    # greps pin: make_api moved to device_api.py (#58, staged to every device
    # target); run_desktop and the loop stay in moy_runtime.py.
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    settings_layer = (Path("runtime") / "settings_layer.py").read_text(encoding="utf-8")
    # The online update SCREEN (checking/download/install phases) lives in
    # update_ui.py (UpdateUI); the _online_update_available query + row label
    # stay in console.py.
    update_ui = (Path("runtime") / "update_ui.py").read_text(encoding="utf-8")

    # run_desktop hands the wifi service to the updater for online updates.
    assert "ws.updater.set_wifi(ws.wifi, go_online=lambda: autoconnect_wifi(ws.wifi))" in runtime
    # The shared console grows the UPDATE ONLINE row + the checking/download phases.
    assert "def _online_update_available" in console
    assert '"UPDATE ONLINE"' in settings_layer
    assert "def open_update_online" in update_ui
    assert "def _start_download" in update_ui
    assert 'ph == "checking"' in update_ui or 'phase == "checking"' in update_ui
    assert 'self._upd_phase = "downloading"' in update_ui
    assert 'self._upd_phase = "confirm"' in update_ui   # online hands off to the local install


def test_micropython_spike_uses_tdeck_native_panel_geometry():
    display = (ROOT / "modules" / "tdeck_panel.py").read_text(encoding="utf-8")

    assert "WIDTH = 320" in display
    assert "HEIGHT = 240" in display



def test_micropython_spike_has_guarded_sd_project_loader():
    display = (ROOT / "modules" / "tdeck_panel.py").read_text(encoding="utf-8")
    sd_loader = (DEVICE / "moybyte_sd.py").read_text(encoding="utf-8")

    assert "class TDeckCompositor" in display
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
    # (moy_sd) instead of re-running spi_bus_initialize like machine.SDCard, which
    # hangs the shared bus once the panel is up. See modmoy_sd.c header.
    mod = (NATIVE / "moy_sd" / "modmoy_sd.c").read_text(encoding="utf-8")
    cmake = (NATIVE / "moy_sd" / "micropython.cmake").read_text(encoding="utf-8")
    build = (ROOT / "build.sh").read_text(encoding="utf-8")
    sd_loader = (DEVICE / "moybyte_sd.py").read_text(encoding="utf-8")
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))

    # Native module attaches (init_device) rather than re-initializing the bus.
    assert "MP_REGISTER_MODULE(MP_QSTR_moy_sd" in mod
    assert "sdspi_host_init_device" in mod
    assert "sdmmc_read_sectors" in mod and "sdmmc_write_sectors" in mod
    assert "target_link_libraries(usermod INTERFACE usermod_moy_sd)" in cmake
    # Staged per board.toml [native.shared] (the C-module list is data now).
    assert "moy_sd" in board_config.native_modules(ROOT)

    # Python live-mount path + block device backed by moy_sd.
    assert "class _NativeSDBlockDev" in sd_loader
    assert "def with_sd_live(fn):" in sd_loader
    assert "def mount_sd_live(" in sd_loader
    assert "import moy_sd" in sd_loader

    # Desktop enables management through the live path (no longer hard-disabled).
    # The SD session is wrapped so it drains any in-flight panel DMA first (the
    # #40 double-buffer SD-vs-panel mutual exclusion), but still delegates to the
    # native live-mount path.
    assert "ws._with_sd = _with_sd_synced" in runtime
    assert "return moybyte_sd.with_sd_live(fn)" in runtime
    # can_manage defaults to "the store root is known" inside the shared
    # console.wire_workstation_core (the runtime hands it carts_root).
    assert "wire_workstation_core(ws, moy_carts, carts_root, make_api" in runtime
    assert "ws.can_manage = False" not in runtime


def test_micropython_touch_and_idle_cursor():
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    shell = (ROOT / "modules" / "moybyte_shell.py").read_text(encoding="utf-8")
    # The GT911 Touch driver (+ TrackBall + TOUCH_* consts) now lives in
    # device_input.py (extracted from moy_runtime.py); run_desktop constructs it.
    device_input = (DEVICE / "device_input.py").read_text(encoding="utf-8")

    # GT911 touch driver on I2C0 (off the SPI bus), fed into the shared pointer.
    # The register map + its behaviour live in the shared gt911 core since
    # #202 Phase C and are unit-tested in test_gt911_core.py; this pins the
    # ROUTING (device_input imports the core's map as class attrs for the
    # calibration tooling).
    assert "class Touch:" in device_input
    assert "REG_STATUS = gt911.REG_STATUS" in device_input
    assert "TOUCH_SWAP" in device_input and "TOUCH_FLIP_Y" in device_input
    assert "touch = Touch(canvas.w, canvas.h" in runtime
    # The poll->pointer application is device_boot.apply_touch (2026-08-18).
    # Only the ROUTING is greppable; the verb's BODY is executed in
    # test_device_boot.py (#208 rank 5), which is what can tell a placed point
    # from a transposed one.
    assert "apply_touch(touch, pointer)" in runtime

    # Cursor auto-hide + the Pointer are a shared support widget now (widgets.py).
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    widgets = (Path("runtime") / "widgets.py").read_text(encoding="utf-8")
    assert "class Pointer:" in widgets
    assert "def tick(self, now):" in widgets
    assert "self.pointer.visible" in console               # draw guard (stays on the ws)
    # pointer.tick runs in the SHARED FrameLoop since #202 Phase B (one order
    # for every board), which run_desktop constructs.
    boot_spine = Path("runtime/device_boot.py").read_text(encoding="utf-8")
    assert "pointer.tick(now)" in boot_spine
    assert "loop = FrameLoop(" in runtime

    # Touch calibration bring-up mode (serial-only, flush-once).
    assert "RUN_TOUCH_CALIBRATE" in shell


def test_micropython_cart_textmode_flips_keyboard_ascii_raw():
    # Cart text input (#38/#42): a running cart opts into text-keyboard mode via the
    # `textmode` verb; the device backend then flips the T-Deck keyboard to clean
    # 1-byte ASCII so key()/keyp() yield typeable bytes, and back to raw/game mode
    # otherwise (so games keep hold-to-move). Firmware tests grep the frozen source.
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    player = (Path("runtime") / "player.py").read_text(encoding="utf-8")
    kb = (DEVICE / "moybyte" / "input.py").read_text(encoding="utf-8")

    # The device make_api exposes `textmode`, setting input.text_mode (host parity).
    assert "def textmode(on=True):" in runtime
    assert '"textmode": textmode,' in runtime
    assert "input.text_mode = bool(on)" in runtime

    # The shared console derives the keyboard mode from the cart's request each
    # running-cart frame and reverts on exit -- via the existing set_game_mode path.
    # _sync_cart_text_mode still lives on Workstation (console.py); the per-frame CALL
    # moved into Player.tick (Stage 2, player.py), so grep the call site there.
    assert "def _sync_cart_text_mode(self):" in console
    assert "ws._sync_cart_text_mode()" in player
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
    # password/name can't also trigger d-pad/shortcut actions (#38/#42). (The
    # guard lives in _apply since the #69 poller split raw-gated it for parity.)
    assert 'if not self.raw_mode and getattr(self.input, "text_mode", False):' in kb


def test_micropython_cart_quit_verb_pops_to_the_caller():
    # A cart ENDS itself via the quit() verb (make_api): the exit a TEXT-mode cart MUST
    # provide, since hold-BACKSPACE can't reach it (typed 0x08 delete, no keyboard
    # autorepeat). host==device: the device make_api exposes the SAME `quit` name, and
    # the shared Player (frozen player.py) honors the flag. Firmware tests grep the source.
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    # The host make_api moved to host_api.py (#151) and then INTO the unified
    # cart_api (2026-08-17) -- host_api re-exports it, so the host surface the
    # grep pins is the pair.
    host = ((Path("runtime") / "host_api.py").read_text(encoding="utf-8")
            + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    player = (Path("runtime") / "player.py").read_text(encoding="utf-8")
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")

    # Both backends expose the SAME verb, setting the SAME input flag (host parity).
    for src in (runtime, host):
        assert "def _quit():" in src
        assert '"quit": _quit,' in src
        assert "input.cart_quit = True" in src

    # The shared Player honors the flag AFTER the cart's _update ran and pops to the
    # run caller; opening a fresh cart clears any stale flag (console._open_workspace).
    assert 'getattr(ws.input, "cart_quit", False)' in player
    assert "ws._exit_to_caller()" in player
    assert "self.input.cart_quit = False" in console


def _panel_src():
    """The mainline panel backend: this board's compositor SUBCLASS plus its
    native driver. What is left in `tdeck_panel.py` since 2026-08-21 is this
    board's alone -- the moy_lcd import, WIDTH/HEIGHT, the two revert flags,
    `sd_bracket`, `set_backlight`; the shared body is `_panel_base_src()`."""
    return (
        (ROOT / "modules" / "tdeck_panel.py").read_text(encoding="utf-8"),
        (ROOT / "native" / "moy_lcd" / "modmoy_lcd.c").read_text(encoding="utf-8"),
    )


def _panel_base_src():
    """The SHARED compositor body (device/banded_panel.py), which since
    2026-08-21 owns the half of the Python panel glue that `tdeck_panel` and
    the Guition's `guition_panel` had in common: the backend contract, the
    kick/drain overlap, the ping-pong and the PUMP meters (#206 item 1). It is
    the Python twin of `_flush_src()` one tier down, and it exists for the same
    reason -- the two boards' feeds CONVERGED on `d9aa73e`. Greps that used to
    read `tdeck_panel.py` for mechanism facts read this instead, and say so."""
    return (DEVICE / "banded_panel.py").read_text(encoding="utf-8")


def _flush_src():
    """The SHARED banded-flush engine (native/moy_flush), which since
    2026-08-21 owns the half of the flush that moy_lcd and the Guition's
    moy_axs had in common: the core-0 feeder, the bounce slots and their
    pacing, the kick/drain handoff and the PUMP meters. The panel modules keep
    their transports. Greps that used to read modmoy_lcd.c for engine facts
    read this instead -- and say so, so the next reader knows where to look."""
    return (NATIVE / "moy_flush" / "moy_flush.c").read_text(encoding="utf-8")


def test_panel_flush_dmas_only_from_internal_sram():
    """The bounce is the whole reason the banding artifacts went away (#66).

    This used to be pinned against moy_compositor.py, which the mainline port
    replaced with moy_lcd. The CONSTRAINT did not change -- the panel DMA may
    only read internal SRAM, because a PSRAM source starves the SPI FIFO and
    clocks out garbage rows (the 2026-07-03 band artifacts) -- it just moved
    into C, so this greps C.
    """
    _py, c = _panel_src()
    assert "INTERNAL SRAM" in c or "internal SRAM" in c
    # The slots themselves are the SHARED engine's since 2026-08-21 (both S3
    # boards allocate them through moy_flush_start), so the cap is pinned
    # there; the panel driver keeps the reason it exists.
    engine = _flush_src()
    assert "MALLOC_CAP_INTERNAL" in engine, (
        "the bounce slots must be internal-SRAM caps")
    assert "moy_flush.bounce" in engine


def test_only_the_first_band_carries_a_command():
    """"A full-screen flush must be a single tx_color", as the driver means it.

    The old rule was stated as one call; what it is really about is that
    re-issuing a command mid-frame is what glitches the boundary. The banded
    flush satisfies it by sending RAMWR once and streaming continuations.
    """
    _py, c = _panel_src()
    assert "RAMWR" in c
    assert "first" in c.lower() and "band" in c.lower()


def test_a_band_must_fit_one_spi_dma_transaction():
    """Pinned in C as a compile-time assert, which is stronger than this test.

    Kept anyway so the REASON survives in a readable place: past 32768 B the
    driver splits internally and the done-ISR stops counting bands one-for-one,
    which hid a real bug once.
    """
    _py, c = _panel_src()
    assert "32768" in c
    assert "a band must fit one SPI DMA transaction" in c


def test_flush_overlap_is_on_and_revertible_by_one_flag():
    """ASYNC_FLUSH replaces the fork's double-buffer/ping-pong switch.

    Same property the old test guarded: the overlap is ON, and it is one flag
    away from off, because that is what makes an A/B possible when the board
    misbehaves.
    """
    py, c = _panel_src()
    assert "ASYNC_FLUSH = True" in py
    assert "async_flush" in py
    # kick / pump / drain is the protocol the overlap is built from.
    for verb in ("kick", "pump"):
        assert verb in c, "the async flush protocol lost its %s()" % verb


def test_layer_copy_async_is_on_and_revertible():
    """The second overlap lever, same one-flag rule as ASYNC_FLUSH."""
    py, _c = _panel_src()
    assert "LAYER_COPY_ASYNC = True" in py


def test_panel_ping_pong_has_two_framebuffers():
    """Two buffers and an explicit swap -- the tear-free half of the design.

    The board asks for two; the swap is the shared body's (#206 item 1).
    """
    py, _c = _panel_src()
    assert "nfbs=2" in py or "nfbs = 2" in py
    assert "_swap" in _panel_base_src()


def test_bounce_pacing_is_measurable():
    """`bounce_stats`/`pump_last_us` feed the PUMP diag line.

    The fork reported this as FLUSHBRK; the port reports PUMP. Either way the
    point is that the pacing is MEASURED -- an overlap you cannot see is an
    overlap you cannot tell from a stall. Both meters are the shared
    compositor's since 2026-08-21, so BOTH S3 boards report them.
    """
    base = _panel_base_src()
    assert "bounce_stats" in base
    assert "pump_last_us" in base


def test_the_band_feed_runs_on_the_core0_feeder_task():
    """A band queued and then forgotten is a frame that never finishes.

    Under the 2ms machine.Timer that guarantee was the timer + the draw pokes
    + the idle drain; since 2026-08-21 it is a core-0 FreeRTOS feeder (ported
    from the Guition's moy_axs), which owns the whole flush -- so the VM-side
    pump plumbing must be GONE (a half-retired timer would silently double-feed
    a bounce slot) and the feeder must exist.

    It exists ONCE, in the shared engine: the port made the two boards'
    concurrency halves literal copies, so native/moy_flush is where the feeder,
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
    armed, and start() refuses to re-arm over the zombie."""
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


def test_the_sd_guard_is_a_nesting_depth_not_a_flag():
    """SD sessions nest, and a boolean guard's INNER close lifts the bracket
    while the outer session is still on the shared SPI host -- which is the
    Cache/MMU panic the guard exists to prevent, with nothing pointing at it.
    """
    _, c = _panel_src()
    assert "static volatile int s_sd_guard;" in c, "the guard must be a depth"
    assert "s_sd_guard++" in c and "s_sd_guard--" in c


def test_kid_mode_gates_diag_frame_eaters():
    # #68 kid mode: Settings -> PERF DIAG (default OFF, persisted) gates the two
    # felt diag costs -- the forced GC sample and the periodic diag->SD write --
    # and hushes the live echo; the ring still flushes on cart exit + crash.
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    settings_layer = (Path("runtime") / "settings_layer.py").read_text(encoding="utf-8")
    # The row + its default + its verb are ONE declaration since #209 section 7
    # (SETTINGS_TOGGLES); the boot apply loops over it, so what used to be a
    # literal `self.system.get("diag_live", False)` here is now the registry's
    # key and default. tests/test_settings_toggles.py is the ratchet on that.
    assert '("diag_live", "PERF DIAG", False, "set_diag_live", None, None)' \
        in settings_layer
    assert "def set_diag_live(self, on, persist=True):" in console
    assert "self.system.get(key, default)" in console            # persisted + applied
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    assert '_live = bool(getattr(ws, "diag_live", False))' in runtime
    assert "diag.ECHO_LIVE = _live" in runtime
    assert "with_sd_live" in runtime                               # forced GC gated
    assert "_cart_prev[0] and not _cart_now" in runtime    # cart-exit flush
    # The periodic diag->SD flush needs BOTH gates now (owner call 2026-07-08):
    # PERF DIAG (_live) AND Settings -> DIAG SD LOG (ws.diag_sd) -- serial-only
    # measurement has no 20s sdflush stutter.
    assert 'getattr(ws, "diag_sd", False)' in runtime               # timer flush gated


def test_i2c_timeout_knob_engaged():
    # #69 A/B: the clock-stretch cap is ON (5ms) -- a keyboard/touch stall becomes a
    # <=5ms failed read (one stale input frame), not a felt 60ms freeze. None reverts.
    inp_mod = (DEVICE / "moybyte" / "input.py").read_text(encoding="utf-8")
    assert "I2C_TIMEOUT_US = 5000" in inp_mod
    assert "timeout=self.I2C_TIMEOUT_US" in inp_mod


# -- the console's input order, for the driver tests below -------------------
#
# `InputState._held` is the union of the sources and `begin_frame` is its one
# author, so polling a driver and reading `state.held(...)` without merging
# reads the PREVIOUS frame. These helpers are the loop's order: poll, then
# merge.

def _kbd_frame(keyboard, state):
    keyboard.poll()
    state.begin_frame()


def _poller_frame(poller, state):
    poller.consume()
    state.begin_frame()


def test_capped_stall_holds_state_and_never_kills_the_keyboard():
    # #69: with the timeout cap a stall RAISES. That must cost ONE STALE FRAME --
    # the last good matrix state is held (returning "no buttons" would fake a
    # release+re-press, and btnp() would double-fire) -- and must NOT disable the
    # keyboard (the old any-exception -> available=False would have killed it
    # within a minute at the measured stall rate). Only ERR_RUN_LIMIT consecutive
    # failures (a genuinely absent keyboard) end the session.
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = module.InputState()
    keyboard = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    keyboard.input = state
    keyboard.available = True
    keyboard.raw_mode = True

    held_frame = bytes([0, 0x04, 0, 0, 0])          # "right" held in the matrix

    class FlakyI2C:
        def __init__(self):
            self.fail = False
        def readfrom(self, _addr, _size):
            if self.fail:
                raise OSError(116)                   # ETIMEDOUT (the capped stall)
            return held_frame

    i2c = FlakyI2C()
    keyboard._i2c = i2c
    _kbd_frame(keyboard, state)
    assert state.held("right")                       # baseline: the key is down
    i2c.fail = True                                  # one capped stall...
    _kbd_frame(keyboard, state)
    assert state.held("right")                       # ...held state survives the gap
    assert keyboard.available and keyboard.raw_mode  # nothing was disabled
    assert keyboard.stat_timeouts >= 1               # ...and it was counted
    i2c.fail = False
    _kbd_frame(keyboard, state)
    assert state.held("right")                       # clean resume, no phantom edge
    assert keyboard._err_run == 0                    # the run counter reset
    # A genuinely dead keyboard still disables after a solid failure run.
    i2c.fail = True
    for _ in range(module.TDeckKeyboard.ERR_RUN_LIMIT):
        _kbd_frame(keyboard, state)
    assert not keyboard.available


def test_blit565_opaque_row_fast_lane():
    # #66 CHROMEBRK follow-up: key<0 blits (the cached top-bar strip stamp, paint
    # bakes) copy each clipped row with ONE memcpy instead of the per-pixel loop.
    # The loop lives in moy_gfx_kernels.c since the compositor was extracted --
    # ONE copy, which the host binding links too.
    c = (NATIVE / "moy_gfx"
         / "moy_gfx_kernels.c").read_text(encoding="utf-8")
    assert "OPAQUE fast lane" in c


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


def test_seed_carts_model_the_fast_draw_habits():
    # The seed carts ARE the curriculum (#66): kids copy them, so they must model
    # the fast idioms the docs teach -- a DECLARED background (#63 habit 1: Battle
    # City names its backdrop once; the engine restores it every frame) and
    # static-scenery-in-a-layer (Hop Quest, like Sky Run).
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


def test_tdeck_keyboard_latches_event_keys_for_hold_window():
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
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

    _kbd_frame(keyboard, state)
    assert state.held("right")

    _kbd_frame(keyboard, state)
    assert state.held("right")

    keyboard._held_until_ms = module._ticks_ms() - 1
    keyboard._read_key = lambda: 0
    _kbd_frame(keyboard, state)
    assert not state.held("right")


def test_tdeck_keyboard_reads_raw_matrix_for_real_holds():
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
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

    _kbd_frame(keyboard, state)
    assert state.held("right")
    assert state.last_key == ord("d")

    _kbd_frame(keyboard, state)
    assert state.held("right")

    _kbd_frame(keyboard, state)
    assert not state.held("right")
    assert state.last_key == 0


def test_tdeck_raw_backspace_is_the_one_console_key():
    # THE ONE CONSOLE KEY: BACKSPACE (matrix [4][3] -> d4 bit 3) maps to the "home"
    # button on the raw path (AND reports last_key 0x08). Stage 5 makes this the EXIT
    # key: a held/streamed "home" is exactly what the Player's hold-BACKSPACE exit
    # gesture watches (raw mode streams the held key each frame, so this held
    # frame -> st.held("home") is the device wiring the exit relies on). q and e are
    # PLAIN LETTERS now (last_key only, no chrome role), and so is x: the action
    # buttons moved off Z/X onto K/L on 2026-08-14 (owner call -- Z/X are bottom
    # row, under the same thumb as WASD), so B is the K key. The rest of that
    # scheme is pinned in tests/test_tdeck_keymap.py, which also checks every raw
    # bit against the vendor firmware's own matrix table.
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def poll_frame(frame):
        state = module.InputState()
        keyboard = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
        keyboard.input = state
        keyboard.available = True
        keyboard.raw_mode = True
        keyboard._i2c = type("F", (), {"readfrom": lambda s, a, n: frame})()
        _kbd_frame(keyboard, state)
        return state

    st = poll_frame(bytes([0, 0, 0, 0, 0x08]))    # backspace held
    assert st.held("home")
    assert st.last_key == 0x08

    st = poll_frame(bytes([0x01, 0, 0, 0, 0]))    # q held: a letter, not a button
    assert not st._held
    assert st.last_key == ord("q")

    st = poll_frame(bytes([0, 0x01, 0, 0, 0]))    # e held: a letter, not a button
    assert not st._held
    assert st.last_key == ord("e")

    st = poll_frame(bytes([0, 0, 0, 0, 0x40]))    # k held: THE b button
    assert st.held("b")
    st = poll_frame(bytes([0, 0, 0, 0, 0x02]))    # l held: THE a button
    assert st.held("a")
    st = poll_frame(bytes([0, 0x10, 0, 0, 0]))    # x is a plain letter now
    assert not st._held
    assert st.last_key == ord("x")
    st = poll_frame(bytes([0, 0, 0, 0, 0x08]))    # backspace is NOT b anymore
    assert not st.held("b")


def _load_fw_input():
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bare_kbd(module, state, raw):
    kbd = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    kbd.input = state
    kbd.available = True
    kbd.raw_mode = raw
    kbd._held_buttons = ()
    kbd._held_until_ms = 0
    return kbd


def test_input_poller_ascii_bytes_deliver_one_frame_each():
    # #69 poller thread, ASCII staging: key bytes are one-shot EVENTS (the C3
    # reports each press once), so the poller queues them and consume() delivers
    # each for exactly one main frame -- with a forced 0-frame between identical
    # bytes so keyp()'s edge detector fires for both presses. The poller reads
    # faster than the frame loop consumes; nothing may be lost or doubled.
    module = _load_fw_input()
    state = module.InputState()
    kbd = _bare_kbd(module, state, raw=False)
    seq = [b"a", b"a", b"\x00"]

    class FakeI2C:
        def readfrom(self, _a, _n):
            return seq.pop(0) if seq else b"\x00"

    kbd._i2c = FakeI2C()
    p = module.InputPoller(kbd, None)
    p._poll_once()
    p._poll_once()
    p._poll_once()                          # two rapid 'a' presses now queued
    _poller_frame(p, state)
    assert state.last_key == ord("a")       # frame 1: first press
    assert state.held("left")               # ...with its latched button alias
    _poller_frame(p, state)
    assert state.last_key == 0              # frame 2: forced release gap
    _poller_frame(p, state)
    assert state.last_key == ord("a")       # frame 3: second press, not dropped
    _poller_frame(p, state)
    assert state.last_key == 0              # queue drained


def test_input_poller_raw_holds_state_across_a_stall():
    # #69 poller thread, raw staging: buttons are LEVEL state (latest snapshot
    # wins) and a capped I2C stall keeps the last good matrix -- the same
    # hold-not-release contract the synchronous path has (no phantom edges).
    module = _load_fw_input()
    state = module.InputState()
    kbd = _bare_kbd(module, state, raw=True)
    seq = [bytes([0x08, 0, 0, 0, 0]), OSError(110), bytes(5)]

    class FlakyI2C:
        def readfrom(self, _a, _n):
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

    kbd._i2c = FlakyI2C()
    p = module.InputPoller(kbd, None)
    p._poll_once()
    _poller_frame(p, state)
    assert state.held("left") and state.last_key == ord("a")
    p._poll_once()                          # capped stall -> hold, don't release
    _poller_frame(p, state)
    assert state.held("left")
    p._poll_once()                          # clean empty matrix -> real release
    _poller_frame(p, state)
    assert not state.held("left")


def test_input_poller_touch_subframe_tap_becomes_two_frames():
    # #69 poller thread, GT911 staging: the poller may see a whole tap (down +
    # up) between two main frames; consume_touch must deliver the point first
    # and the finger-up the NEXT frame so the tap edge is never swallowed.
    module = _load_fw_input()

    class FakeTouch:
        available = True

        def __init__(self):
            self.seq = [(100, 50), False]

        def read_raw(self):
            return self.seq.pop(0) if self.seq else None

    p = module.InputPoller(None, FakeTouch())
    p._poll_once()
    p._poll_once()                          # down + up both before one consume
    assert p.consume_touch() == (100, 50)   # frame 1: the press lands
    assert p.consume_touch() is False       # frame 2: the release
    assert p.consume_touch() is None        # steady state after


def test_input_poller_defers_mode_switch_to_the_bus_thread():
    # #69: with the poller owning the bus, set_game_mode from the main thread
    # must NOT write I2C (a write could collide with a poller read mid-stall) --
    # it queues the target and the poller applies it between reads.
    module = _load_fw_input()
    state = module.InputState()
    kbd = _bare_kbd(module, state, raw=False)
    kbd._raw_unsupported = False
    kbd._poller_owned = True
    writes = []

    class FakeI2C:
        def readfrom(self, _a, n):
            return bytes(n)

        def writeto(self, _a, buf):
            writes.append(bytes(buf))

    kbd._i2c = FakeI2C()
    kbd.set_game_mode(True)                 # main thread: queued only
    assert writes == [] and kbd.raw_mode is False
    p = module.InputPoller(kbd, None)
    p._poll_once()                          # poller applies it between reads
    assert writes[0] == b"\x03" and kbd.raw_mode is True


def _load_fw_device_input():
    # device_input imports the leaf device_util at module top; stage it into
    # sys.modules first (the test_device_canvas_parity loader pattern).
    du = importlib.util.spec_from_file_location(
        "device_util", DEVICE / "device_util.py"
    )
    dumod = importlib.util.module_from_spec(du)
    du.loader.exec_module(dumod)
    sys.modules["device_util"] = dumod
    spec = importlib.util.spec_from_file_location(
        "moybyte_device_input", DEVICE / "device_input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bare_touch(module):
    t = module.Touch.__new__(module.Touch)
    t.available = True
    t.addr = 0x5D
    t._i2c = None
    t._down = False
    t._source = None
    t.stat_n = 0
    t.stat_max_us = 0
    t.stat_over5 = 0
    t.stat_over20 = 0
    t.stat_first_big = None
    t.stat_int_edges = 0
    t.stat_skipped = 0
    t._int_pin = None
    t._int_count = [0]
    t._int_last = 0
    t._int_seen = False
    t._touching = False
    t._last_read_ms = module._ticks_ms()
    return t


def test_touch_int_gate_semantics():
    # #74 INT gate: reads are skipped ONLY once the pin has proven itself with a
    # first edge AND nothing is pending -- INT activity, a touch in progress and
    # the safety heartbeat all still read; no pin at all = today's blind polling.
    module = _load_fw_device_input()
    t = _bare_touch(module)
    assert t.should_read()                    # no INT pin -> always read

    t._int_pin = object()                     # pin came up, but no edge ever
    assert t.should_read()                    # gate not engaged -> still reads
    assert t.stat_skipped == 0

    t._int_count[0] += 1                      # first edge: data ready
    assert t.should_read()                    # ... consumed, gate now engaged
    assert t._int_seen
    t._last_read_ms = module._ticks_ms()      # a recent read
    assert not t.should_read()                # idle + no edge -> skipped
    assert t.stat_skipped == 1

    t._int_count[0] += 1                      # tap: an edge arrives
    assert t.should_read()                    # -> read this pass
    t._touching = True                        # finger down (read_raw saw a point)
    assert t.should_read()                    # full rate while touching, no edge needed
    t._touching = False
    t._last_read_ms = module._ticks_ms() - 1000
    assert t.should_read()                    # safety heartbeat past SAFETY_POLL_MS


def test_touch_read_raw_tracks_gate_state():
    # read_raw feeds the gate: a point sets _touching (full rate until the
    # release report lands), a fresh no-point sample clears it, and a not-ready
    # read changes nothing (it says nothing about the finger).
    module = _load_fw_device_input()
    t = _bare_touch(module)

    class FakeGT911:
        def __init__(self):
            self.frames = [(0x81, bytes([50, 0, 100, 0])),   # ready, 1 point
                           (0x80, b""),                       # ready, 0 points: up
                           (0x00, b"")]                       # not ready

        def readfrom_mem(self, _a, reg, _n, addrsize=16):
            st, pt = self.frames[0]
            return bytes([st]) if reg == module.Touch.REG_STATUS else pt

        def writeto_mem(self, _a, _reg, _buf, addrsize=16):
            self.frames.pop(0)                # the status clear consumes a frame

    t._i2c = FakeGT911()
    assert t.read_raw() == (100, 50)          # y(lo,hi) then x(lo,hi) layout
    assert t._touching
    assert t.read_raw() is False
    assert not t._touching
    t._touching = True                        # pretend mid-touch...
    assert t.read_raw() is None               # not-ready says nothing
    assert t._touching                        # ...state untouched


def test_input_poller_touch_respects_int_gate():
    # #69/#74: the poller consults Touch.should_read() before spending a GT911
    # transaction; a gated pass does zero touch I2C, and a fake without the
    # method (the older Touch shape) keeps the every-pass behaviour.
    module = _load_fw_input()

    class GatedTouch:
        available = True

        def __init__(self):
            self.reads = 0
            self.gate = [False, True]

        def should_read(self):
            return self.gate.pop(0) if self.gate else False

        def read_raw(self):
            self.reads += 1
            return (10, 20)

    t = GatedTouch()
    p = module.InputPoller(None, t)
    p._poll_once()                            # gated -> no I2C
    assert t.reads == 0 and p.consume_touch() is None
    p._poll_once()                            # gate opens -> one read
    assert t.reads == 1 and p.consume_touch() == (10, 20)


def test_touch_int_gate_wired():
    # Pin the #74 chain in the frozen sources: the GT911 INT pin (16, per the
    # T-Deck reference's BOARD_TOUCH_INT), both-edge counting (polarity-agnostic),
    # the poller consulting the gate, and the I2CSTAT verdict fields.
    device_input = (DEVICE / "device_input.py").read_text(encoding="utf-8")
    assert "INT_PIN = 16" in device_input
    assert "INT_GATE = True" in device_input                  # the A/B revert knob
    assert "SAFETY_POLL_MS" in device_input
    assert "Pin.IRQ_RISING | Pin.IRQ_FALLING" in device_input
    assert "def should_read(self):" in device_input
    poller = (DEVICE / "moybyte" / "input.py").read_text(encoding="utf-8")
    assert 'getattr(t, "should_read", None)' in poller
    diag = (DEVICE / "device_diag.py").read_text(encoding="utf-8")
    assert "int=%d skip=%d" in diag


def test_input_poller_wired_with_gil_release_patch():
    # The poller only isolates a stall when machine.I2C frees the GIL across its
    # blocking legacy-driver transaction wait -- pin the whole chain: the build
    # applies the patch by default, the patch wraps the right call, run_desktop
    # prefers the poller and keeps the synchronous path as a live fallback.
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    kb = (DEVICE / "moybyte" / "input.py").read_text(encoding="utf-8")
    build = (ROOT / "build.sh").read_text(encoding="utf-8")
    build_sh = (ROOT / "build.sh").read_text(encoding="utf-8")
    assert "Moybyte #69 GIL" in build_sh, "the GIL-release patch is not applied"
    assert "i2c_master_cmd_begin" in build_sh
    patch = build_sh

    assert "class InputPoller:" in kb
    assert "def apply_pending_mode(self):" in kb
    assert "def _poll_once(self):" in kb            # threadless-testable pass body
    assert "MOY_INPUT_POLLER = True" in runtime      # default ON, revert w/o rebuild
    assert "poller.consume()" in runtime
    assert "keyboard.poll()" in runtime              # the synchronous path stays live
    assert "poller thread died -> synchronous fallback" in runtime
    assert "touch._source = poller.consume_touch" in runtime
    assert "keyboard._poller_owned = True" in runtime
    # the GIL-release patch: applied by default, revertable, wraps cmd_begin
    assert "Moybyte #69 GIL" in build
    assert "Moybyte #69 GIL" in build
    assert "Moybyte #69 GIL" in patch
    assert "MP_THREAD_GIL_EXIT();" in patch
    assert "i2c_master_cmd_begin(" in patch
    assert "MP_THREAD_GIL_ENTER();" in patch


def test_tdeck_keyboard_keeps_raw_mode_for_physical_a_bit():
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
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

    _kbd_frame(keyboard, state)
    assert state.held("left")
    assert state.last_key == ord("a")
    assert keyboard.raw_mode


def test_tdeck_keyboard_falls_back_when_raw_mode_is_ignored():
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
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

    _kbd_frame(keyboard, state)
    assert state.held("right")
    assert state.last_key == ord("d")
    assert not keyboard.raw_mode


def test_tdeck_keyboard_set_game_mode_toggles_raw():
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
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


def test_device_canvas_uses_native_moy_gfx():
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    # The hot drawing ops go through the native moy_gfx kernel (fill/fill_rect/
    # blit565) writing into the shared framebuffer, not the per-pixel Python loop,
    # so complex carts stay fast.
    assert "self._gfx = compositor.gfx()" in device_canvas
    assert "self._gfx.fill(self._buf" in device_canvas          # cls
    # rect / circ. Matches both `self._gfx.fill_rect(` and the hoisted
    # `gfx.fill_rect(` _fill uses -- that wrapper caches self._gfx in a local
    # because it is the console chrome's hot verb and every interpreter dict
    # lookup in it is measurable (see _fill's note in device_canvas.py). The
    # pin is "the native kernel writes the shared framebuffer", not a spelling.
    assert "gfx.fill_rect(self._buf" in device_canvas
    assert "self._gfx.blit565(self._buf" in device_canvas       # spr
    # Sprites are cached as a pre-scaled RGB565 blit; sheet tiles reuse one Image
    # across frames so the cache is built once, not rebuilt every frame.
    assert "def _cache_rgb(self, img, scale, flip=0):" in device_canvas
    assert "tile_cache" in runtime
    # `gfx()` is the shared compositor's since 2026-08-21 (#206 item 1), so
    # the kernel reaches DeviceCanvas the same way on both S3 boards.
    assert "def gfx(self):" in _panel_base_src()


def test_scroll_rect_wired_for_ui_blit_scroll():
    # #113 scroll-as-blit: the device system canvas carries the scroll_rect
    # system verb (native moy_gfx row-memmove kernel + a bytearray-slice
    # fallback), so shelf/picker drag+fling frames shift pixels instead of
    # repainting every card. Grep the frozen sources + the C module like the
    # other firmware tests (#113; shipped on-glass -- the shelf/picker pilots).
    c = (NATIVE / "moy_gfx" / "modmoy_gfx.c").read_text(encoding="utf-8")
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    assert "moy_gfx_scroll_rect" in c
    assert "MP_QSTR_scroll_rect" in c
    assert "def scroll_rect(self, rx, ry, rw, rh, dx, dy):" in device_canvas
    assert "self._gfx.scroll_rect(self._buf" in device_canvas
    # A layer (win.buf on the windowed tier) is ONE persistent buffer: it must
    # override the root canvas's ping-pong RETAINED_FRAMES = 2 or blit_shift
    # would measure deltas against the wrong paint.
    assert "lay.RETAINED_FRAMES = 1" in device_canvas


def test_touch_holds_a_held_finger_between_gt911_samples():
    # #113/#74: the GT911 clock-stretches 20-45ms on 75-90% of the reads it makes
    # while a finger is DOWN, so at 30-60fps most frames carry no fresh sample --
    # but the finger is still on the glass. poll() must report the held point (the
    # P4's p4_input does the same); reporting None makes run_desktop's
    # `pointer.down = tp is not None` a phantom RELEASE, which ends the drag
    # mid-swipe and can fire a kinetic fling. The repeats are marked stale so the
    # console doesn't charge them a finger delta the hardware never measured.
    # The hold/stale/bound contract is ONE copy in the shared gt911 core since
    # #202 Phase C (both boards' drivers used to maintain it separately, and
    # the P4's copy shipped without the guards for months); the driver must
    # still ROUTE its no-news pass through it.
    inp = (DEVICE / "device_input.py").read_text(encoding="utf-8")
    runtime = (ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
    # The core's CONTENT (hold window, stale mark, the missed-release unwedge)
    # is unit-tested for real in test_gt911_core.py -- this file pins only the
    # ROUTING: the driver's no-news pass goes through the one shared copy.
    assert "return self._hp.hold()" in inp
    # The sample->pointer application is device_boot.apply_touch since
    # 2026-08-18 (both boards' poll_inputs hooks carried it verbatim); the
    # board must route through it. That the ONE copy carries the fresh mark --
    # and carries it BEFORE the no-sample bail -- is executed in
    # test_device_boot.py (#208 rank 5); a substring could not see the order.
    assert "apply_touch(touch, pointer)" in runtime


def test_native_blit_map_wired_for_tilemaps():
    # The tilemap blit (#32) is a native moy_gfx op (one C call per map() region) and
    # DeviceCanvas.map drives it from the INDEX sheet through libmoy, with a Python
    # per-tile fallback when moy_gfx is absent. Grep the frozen device sources +
    # the C module like the other firmware tests.
    c = (NATIVE / "moy_gfx" / "modmoy_gfx.c").read_text(encoding="utf-8")
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    assert "moy_gfx_blit_map" in c
    assert "MP_ROM_QSTR(MP_QSTR_blit_map)" in c          # registered in the module dict
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    assert runtime  # moy_runtime still imports the canvas (the code moved to device_canvas)
    assert "def map(self, tilemap, sheet" in device_canvas     # DeviceCanvas.map
    assert "self._gfx.blit_map(dst" in device_canvas           # native one-call blit (via _blit_map_into)
    assert "def _blit_map_into(self" in device_canvas          # #63 Fold 2: shared blit_map helper
    # The baked RGB565 tile atlas is GONE (#97): blit_map calls libmoy's
    # moy_map_draw, which reads the index sheet and resolves through the LUT.
    # Measured faster on both boards, and it hands back the atlas's 64 KB.
    assert "def _sheet_atlas" not in device_canvas
    assert "moy_map_draw(&c, &m, &sh" in c
    assert "sheet.pix, sheet.w, sheet.h" in device_canvas
    assert "def _map_py(self, tilemap, sheet" in device_canvas  # no-moy_gfx fallback
    # #63 Fold 2: map() auto-caches the rasterized region (blit565 composite of a hidden
    # 565 layer) so a camera-only change re-uses it instead of re-walking every cell.
    assert "self._mapcache" in device_canvas
    assert "def map_cache_reset(self):" in device_canvas


def test_native_vector_primitives_wired():
    # circ/circb/line are native moy_gfx ops (#43 follow-up): one C call rasterizes the
    # whole shape (was N per-scanline / per-pixel MP->C calls), with a Python fallback
    # when moy_gfx is absent. Grep the C module + the device canvas wiring.
    c = (NATIVE / "moy_gfx" / "modmoy_gfx.c").read_text(encoding="utf-8")
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    for fn in ("moy_gfx_circ", "moy_gfx_circb", "moy_gfx_line"):
        assert fn in c
    for q in ("MP_QSTR_circ", "MP_QSTR_circb", "MP_QSTR_line"):
        assert "MP_ROM_QSTR(%s)" % q in c                 # registered in the module dict
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    assert "self._gfx.circ(self._buf" in device_canvas
    assert "self._gfx.circb(self._buf" in device_canvas
    assert "self._gfx.line(self._buf" in device_canvas
    # blit_window is the scroll-engine (Stage 1) primitive -- a flat per-row window copy
    # from a wide pre-rendered background. Landed in the kernel ahead of the engine that
    # consumes it (see the scroll-engine issue); assert it's registered.
    assert "moy_gfx_blit_window" in c
    assert "MP_ROM_QSTR(MP_QSTR_blit_window)" in c
    # The device cart API exposes map/mget/mset, same names as the host make_api.
    assert '"map": map_, "mget": mget, "mset": mset,' in runtime


def test_native_text_wired_with_shared_font():
    # print() is native (#62) -- the LAST draw verb off framebuf: one moy_gfx.text C
    # call per string, camera + clip + pal honoured, rasterizing the SAME petme128
    # glyph blob the host draws from (runtime/font.py, staged as the frozen moy_font
    # by build.sh). framebuf.text (same glyphs, no clip rect) stays the fallback.
    c = (NATIVE / "moy_gfx" / "modmoy_gfx.c").read_text(encoding="utf-8")
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    assert "moy_gfx_text" in c
    assert "MP_ROM_QSTR(MP_QSTR_text)" in c               # registered in the module dict
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    assert "import moy_font" in device_canvas                   # shared glyph source
    assert "self._gfx_text(self._buf" in device_canvas          # native one-call text
    assert "self._fb.text(" in device_canvas                    # framebuf fallback kept
    assert "moy_font.py" in _staged()      # runtime/font.py, staged RENAMED


def test_native_spr_batch_wired_for_sprites():
    # The sprite-batch blit (#43) is a native moy_gfx op (one C call for N sprites, the
    # sprite analogue of blit_map / #32) and DeviceCanvas.spr_batch drives it from the
    # SAME index sheet map() reads, with a Python per-item fallback when
    # moy_gfx is absent. Grep the frozen device sources + the C module, like the other
    # firmware tests (this file does not execute device code).
    c = (NATIVE / "moy_gfx" / "modmoy_gfx.c").read_text(encoding="utf-8")
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    assert "moy_gfx_blit_batch" in c
    assert "MP_ROM_QSTR(MP_QSTR_blit_batch)" in c        # registered in the module dict
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    assert "def spr_batch(self, sheet, items" in device_canvas  # DeviceCanvas.spr_batch
    assert "self._gfx.blit_batch(self._buf" in device_canvas    # native one-call blit
    # ...and the ONLY thing that reaches it is the auto-batch gate. The cart verb of
    # the same name was deleted on 2026-08-14 (plan 6.10), so a cart gets the native
    # one-call blit by writing a plain spr() loop and by no other route.
    assert "def spr_tile(self, sheet" in device_canvas     # the gate's queue entry
    assert "def spr_batch(items" not in runtime            # no cart verb
    assert '"spr_batch"' not in runtime                    # not in the cart namespace
    assert '"rect_batch"' not in runtime and '"spans"' not in runtime


def test_native_blit_indices_wired_for_paint_images():
    # blit_indices (#63 Fold 3) is the native "images are data, not draw calls" bake: one
    # C call converts a palette-index bitmap -> RGB565, replacing the thousands of rect()
    # replays the old background-paint anti-pattern used. Landed in the kernel + both
    # canvases ahead of the paint-image asset flow that will consume it. Grep the C module
    # + the device canvas wiring (this file does not execute device code).
    c = (NATIVE / "moy_gfx" / "modmoy_gfx.c").read_text(encoding="utf-8")
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    assert "moy_gfx_blit_indices" in c
    assert "MP_ROM_QSTR(MP_QSTR_blit_indices)" in c       # registered in the module dict
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    assert "def blit_indices(self, indices, iw, ih, x, y)" in device_canvas   # DeviceCanvas method
    assert "self._gfx.blit_indices(self._buf" in device_canvas  # native one-call bake
    # The batch walks the sheet through libmoy's moy_spr -- one canvas for the whole
    # run, so the #43 dispatch win is untouched and only the inner loop moved (#97).
    assert "moy_spr(&c, &sh, (int)tid" in c
    assert "self._wire_pal(), self._palt" in device_canvas
    # Brick Siege adopts it: the moving sprites go out in one batch (#43) -- as a
    # plain contiguous spr() run the gate coalesces, which is also exactly what its
    # Lua twin does, so the pair is comparable by construction (plan 6.10).
    battle = (Path("system_carts") / "brick_siege.moy"
              / "main.py").read_text(encoding="utf-8")
    assert "spr(EAGLE if base_alive else BROKEN" in battle
    assert "spr_batch(" not in battle


def test_paint_image_assets_wired_device_and_carts():
    # Paint-image assets (#63 Fold 3) end-to-end on the device side: the .moyimg loader
    # in moy_carts, the make_api image(name) accessor + decode, and DeviceCanvas.spr's
    # bake-ONCE-via-blit_indices fast path. Grep the frozen device modules (this file
    # does not execute device code) + the moy_carts store + sakura's conversion.
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    carts = (Path("runtime") / "moy_carts.py").read_text(encoding="utf-8")
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")

    # moy_carts loads/writes a cart's images/ subfolder of .moyimg blobs.
    assert "def load_images(path):" in carts
    assert 'IMAGES_DIR = "images"' in carts
    assert '"images": images,' in carts                 # load() exposes them on the cart
    assert 'images = cart.get("images")' in carts       # seed_builtins writes them back

    # The device make_api takes `images` and exposes the image(name) accessor, decoding
    # a .moyimg into an Image via the deflate (zlib) inflate mirror of the host.
    assert "pmem=None, wifi=None, images=None, scenes=None, tables=None," in runtime
    assert "texts=None, net=None, gpio=None, owner=\"cart\"):" in runtime
    # _decode_moyimg lives in the unified cart_api since 2026-08-17 (one body
    # for every tier; the MicroPython lane inflates via `deflate`).
    cart_api_src = Path("runtime/cart_api.py").read_text(encoding="utf-8")
    assert "def _decode_moyimg(text):" in cart_api_src
    assert "deflate.DeflateIO(io.BytesIO(raw), deflate.ZLIB).read()" in cart_api_src
    assert 'im._paint = True' in runtime                 # tags the bake/ship fast paths

    # DeviceCanvas.spr bakes a paint image index->565 ONCE via blit_indices, then blit565s.
    assert "def _bake_indices(self, img):" in device_canvas
    # pal565 is passed as the array('H') BUFFER form, not the tuple -- the native kernel
    # reads it via the buffer protocol (a tuple crashes: object with buffer protocol required).
    # ... in the WIRE order (PAL565_WIRE: byte-swapped on the T-Deck SPI panel,
    # canonical on the P4's directly-scanned DPI framebuffer, #58).
    #
    # It reads `self._wire` and not the module buffer directly: the table is
    # per-canvas now so a cart can ship its own palette (SPEC.md 3.1), which
    # used to be accepted and silently ignored on every tier but the host.
    # `self._wire` IS `_PAL565_WIRE_BUF` until something overrides it, so the
    # stock path still passes the same array('H') BUFFER form.
    assert "self._gfx.blit_indices(buf, w, h, 0, 0, img.pix, w, h, self._wire)" in device_canvas
    assert "self._wire = _PAL565_WIRE_BUF" in device_canvas
    assert '_PAL565_WIRE_BUF = array("H", PAL565_WIRE)' in device_canvas
    assert 'getattr(img, "_paint", False) and scale == 1 and flip == 0' in device_canvas

    # The console threads the cart's images into make_api (open + wallpaper compile).
    assert 'self.images = self.cart.get("images") or {}' in console

    # sakura is converted: no _BG blob / _paint_bg replay; it fetches image("bg") and
    # bakes it into a layer with ONE spr(bg, 0, 0). The asset ships as images/bg.moyimg.
    sakura = (Path("system_carts") / "sakura.moy" / "main.py").read_text(encoding="utf-8")
    assert "_BG" not in sakura and "_paint_bg" not in sakura
    assert 'bg = image("bg")' in sakura and "lay.spr(bg, 0, 0)" in sakura
    assert (Path("system_carts") / "sakura.moy" / "images" / "bg.moyimg").is_file()


def test_native_spr_gate_wired():
    # #63 spr_gate: the kid-facing spr() is a NATIVE callable when moy_gfx is up.
    # WHY (measured on S3, warm 1MB heap): a Python call whose frame exceeds ~11
    # words heap-allocates it EVERY call (~1.5ms/call warm -- the frame-spill
    # pathology); the old 8-param spr closure -> spr_tile chain spilled twice per
    # sprite, so a kid's 120-sprite loop cost ~150ms/frame. The C gate has no
    # Python frame (~2-5us/call) and delegates Image/span/kwargs calls to the
    # Python closure unchanged -- same API, same pixels, fast by default.
    c = (NATIVE / "moy_gfx" / "modmoy_gfx.c").read_text(encoding="utf-8")
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    # C side: the gate type + factory exist and are registered.
    assert "moy_gfx_spr_gate_obj_t" in c
    assert "spr_gate_call" in c
    assert "MP_ROM_QSTR(MP_QSTR_make_spr_gate)" in c
    # C parses ints AND floats (kid float coords), delegates the rest.
    assert "mp_obj_is_small_int(o)" in c and "mp_obj_is_float(o)" in c
    assert "mp_call_function_n_kw(g->fallback, n_args, n_kw, args)" in c
    # blit_batch reads the batch array directly (array mode, no tuples).
    assert "ARRAY MODE (#63 spr_gate)" in c
    # Python side: the shared array('h') queue + begin_batch protocol + wiring.
    assert 'self._batch_arr = array("h", bytearray(2 * (4 + 4 * 512)))' in device_canvas
    assert "def begin_batch(self, sheet, colorkey=-1, scale=1, token=0):" in device_canvas
    assert "def make_spr_gate(self, sheet, fallback):" in device_canvas
    assert '"spr": _spr_entry,' in runtime
    # flush_batch draws the run via ONE array-mode native call.
    # Buffer geometry, not the logical surface: since the #155 viewport a canvas's
    # w/h can be a sub-rect of its buffer, so kernels take _stride/_bh.
    assert "self._gfx.blit_batch(self._buf, self._stride, self._bh, a," in device_canvas


def test_async_layer_copy_wired():
    # #54 Stage 2 (#63 follow-up): the draw_layer background restore can run on the
    # GDMA engine WHILE the cart's _update executes. C side guarded by __has_include
    # (the unix-port bench build has no esp_async_memcpy.h and simply lacks the
    # functions); Python side predicts at blit_window_from, kicks at sync_back
    # (pre-_update), consumes at the next blit_window_from, and drains at cls /
    # sync_back so an unconsumed copy never races CPU draws. Sync fallback on any
    # refusal (_async_ok latch), so old firmware / host parity is untouched.
    c = (NATIVE / "moy_gfx" / "modmoy_gfx.c").read_text(encoding="utf-8")
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    assert 'MOY_GFX_HAS_ASYNC_COPY' in c
    assert 'esp_async_memcpy_install(&cfg, &moy_gfx_mcp)' in c
    assert 'MP_ROM_QSTR(MP_QSTR_copy_async)' in c
    assert 'MP_ROM_QSTR(MP_QSTR_copy_wait)' in c
    assert "def _arm_layer_pred(self, layer, cam_x, cam_y):" in device_canvas
    assert "def _drain_lcopy(self):" in device_canvas
    assert 'hasattr(self._gfx, "copy_async")' in device_canvas
    # kick happens at sync_back (BEFORE the cart's _update -> real overlap)
    assert "self._gfx.copy_async(self._buf, 0, layer._buf," in device_canvas
    # cls drains an unconsumed in-flight copy (screen switches never race the DMA)
    assert "self._drain_lcopy()" in device_canvas
    # a layer edited this frame forces a miss (no stale-background frames)
    assert "hit = (not _dirty and pend[0] is layer" in device_canvas
    # ... and the kick is TIED to the SRAM-bounce flush (#66): against a panel
    # DMA that reads PSRAM, the PSRAM->PSRAM GDMA copy starves the SPI FIFO into
    # horizontal garbage bands (hardware 2026-07-03) -- it is only safe when the
    # panel reads internal SRAM. One flag must feed both, so turning bounce off
    # turns the layer copy off with it.
    assert "LAYER_COPY_ASYNC = _SRAM_BOUNCE_FLUSH" in device_canvas
    assert "from moy_compositor import SRAM_BOUNCE_FLUSH" in device_canvas
    assert "LAYER_COPY_ASYNC and self._gfx is not None" in device_canvas


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
    # #66: the SRAM-bounce flush needs three cooperating pieces -- the esp_lcd
    # no-acquire patch (continuation tx_color must be queue-only or every band
    # blocks on the previous one), the compositor default + pump machinery, and
    # the GDMA layer copy tied to the same flag (it is only artifact-safe when
    # the panel DMA reads internal SRAM).
    build = (ROOT / "build.sh").read_text(encoding="utf-8")
    # The compositor half is the SHARED body since 2026-08-21 (#206 item 1) --
    # this board subclasses it, so the meter and the fence are pinned there.
    comp = _panel_base_src()
    assert (PATCHES / "esp_lcd_tx_color_noacquire.patch").exists()
    assert "esp_lcd_tx_color_noacquire.patch" in build
    assert 'grep -q "Moybyte #66"' in build
    assert "pump_last_us" in comp, "the C-side pump is unmeasurable from Python"
    # 2026-08-21: the 2ms machine.Timer feeder is RETIRED -- moy_lcd's core-0
    # feeder task owns the band feed (test_the_band_feed_runs_on_the_core0_
    # feeder_task pins it); drain stays the VM-side fence.
    assert "drain" in comp, "the flush lost its drain fence"
    # hardware round 2 (#66): bands must outlast the 2ms pump timer (24-row
    # 1.5ms bands starved the SPI -> -30% fps) and the band copy must be the C
    # memcpy (memoryview slice-assign measured ~1ms+/band = FLUSHBRK setup 2.5ms)
    _c = (ROOT / "native" / "moy_lcd" / "modmoy_lcd.c").read_text(encoding="utf-8")
    assert "48" in _c and "band" in _c.lower(), (
        "band geometry moved into moy_lcd; it must still be stated somewhere")
    assert "memcpy" in _c, "the band copy must be the C memcpy, not a Python slice-assign"
    # #66 lever 2: two slots -- three was tried and reverted (see the A/B).
    assert "MOY_LCD_BOUNCE_SLOTS" in _c
    gfx_c = (NATIVE / "moy_gfx" / "modmoy_gfx.c").read_text(encoding="utf-8")
    assert "MP_ROM_QSTR(MP_QSTR_copy),       MP_ROM_PTR(&moy_gfx_copy_obj)" in gfx_c


def test_hitch_logger_wired():
    # #66: any frame past HITCH_MS logs a HITCH line naming the loop-tail costs
    # (diag sample / diag SD write / web poll) -- the tool for the Sakura
    # "micro-stutter every couple of seconds" hunt.
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    # The _diag_* logging functions (incl. _diag_hitch + HITCH_MS) now live in
    # device_diag.py; run_desktop still CALLS them (assert below stays vs runtime).
    device_diag = (DEVICE / "device_diag.py").read_text(encoding="utf-8")
    assert "HITCH_MS = 80" in device_diag
    assert "def _diag_hitch(" in device_diag
    # v2: input polls + ws.frame timed (v1 showed hitches with all stages zero).
    # v3: sync_back timed (the GDMA layer kick was still unmeasured), RAW phase
    # split printed (EMAs hid which phase a single spike lived in), pump= and
    # lw= (copy_wait trips) added; copy_wait is bounded ~250k spins and REPORTS
    # a trip, with the consume site forcing the sync path on one.
    assert '_diag_hitch(diag, ws, comp, elapsed, _t["kbd"], _t["inp"], _t["sb"],' in runtime
    assert "pump=%.1f lw=%d raw(logic=%.1f" in device_diag
    assert "self._lcopy_trips += 1" in device_canvas
    console_src = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    assert "def perf_breakdown_raw(self):" in console_src
    gfx_c = (NATIVE / "moy_gfx" / "modmoy_gfx.c").read_text(encoding="utf-8")
    assert "spins < 250000u" in gfx_c
    # the diag->SD write (measured 80-120ms) must NOT run at 5s during play
    assert "20000 if ws.cart is not None else 5000" in runtime


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
    called = ("\n%s\n" % fn) in src
    declined = ("# DECLINED %s " % fn) in src
    assert called != declined, (
        "%s: %s must be either called or declined in writing, exactly one"
        % (build, fn))
    return called


def test_repr_c_unboxed_floats_wired():
    # #66 micro-stutter root cause: REPR_A boxes EVERY float result (16B heap
    # alloc); sakura's 120-petal _update measured 73KB/frame of garbage -> the
    # heap-wrap gc collect (130-175ms) fired every ~1s INSIDE cart logic. REPR_C
    # packs floats into the object word (30-bit).
    #
    # The fork shipped this as a context diff against mpconfigport.h; the port
    # applies a GUARDED SED on the same line, which survives the line moving
    # between MicroPython releases. So this asserts the edit and its guard, not
    # a patch file -- and the guard matters more than the edit: a silent
    # no-op here is a board that quietly runs boxed floats again.
    lib = Path("tools/esp32_build_lib.sh").read_text(encoding="utf-8")
    assert "moybyte_patch_repr_c() {" in lib
    assert "MICROPY_OBJ_REPR_C" in lib
    assert "REPR_C patch did not apply" in lib, (
        "the sed lost its verification -- a shape change upstream would pass silently")
    assert "exit 1" in lib
    # ...and it is ONE body: no board may re-grow an inline copy of the sed.
    opted = [b for b in _esp32_builds() if _opts_in(b, "moybyte_patch_repr_c")]
    assert len(opted) >= 2, "both S3 boards run REPR_C"
    for build in _esp32_builds():
        assert "MICROPY_OBJ_REPR" not in build.read_text(encoding="utf-8"), (
            "%s carries its own REPR sed again" % build)


def test_psram_temperature_retune_wired():
    # #169: at 120MHz octal MSPI, IDF only STARTS the temperature retune for
    # verified flash vendor ids and otherwise returns ESP_ERR_NOT_SUPPORTED from
    # a SECONDARY ESP_SYSTEM_INIT_FN, which aborts the boot -- a board that
    # flashes cleanly, says nothing on serial, and reads exactly like a PSRAM
    # timing failure. Untested on either board's glass by any suite; this is the
    # only thing standing between the patch and a silent removal.
    lib = Path("tools/esp32_build_lib.sh").read_text(encoding="utf-8")
    assert "moybyte_patch_psram_retune() {" in lib
    assert "mspi_timing_by_mspi_delay.c" in lib
    assert "esp_psram_temp_retune_any_vendor.patch" in lib
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


def test_gc_diag_is_low_cadence():
    # #63: the forced-collect GC sample costs ~130ms on a cart-sized live set --
    # running it every 3s was a visible periodic hitch. 1-in-10 samples only.
    # _diag_gc + its cadence state now live in device_diag.py (extracted from
    # moy_runtime.py); run_desktop calls _diag_gc(diag) between frames.
    device_diag = (DEVICE / "device_diag.py").read_text(encoding="utf-8")
    assert "_GC_TICK = [0]" in device_diag
    assert "if tick % 10 != 0:" in device_diag


def test_scroll_layer_buffer_is_off_gc_heap():
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    # #63 (GC wall): a scroll/paint layer's 150KB RGB565 buffer is the biggest object a
    # cart keeps live, and collect cost scales with the live set (~0.16ms/KB on device).
    # _LayerComp must allocate it OFF the gc heap via moy_alloc (PSRAM, same allocator the
    # compositor framebuffers use) so gc.collect() never marks it -- keeping collect cheap
    # and the heap unfragmented (kid code untouched: fast by default). It must fall back to
    # a gc-heap bytearray on the host / if the allocator is absent, so it never regresses.
    # _LayerComp moved to device_canvas.py (extracted from moy_runtime.py).
    # Grab the _LayerComp.__init__ body.
    start = device_canvas.index("class _LayerComp")
    # (_Layer itself moved to the unified cart_api on 2026-08-17; _LayerComp is
    # the last class before the end-of-file tombstone.)
    layercomp = device_canvas[start:]
    assert "import moy_alloc" in layercomp
    # SPIRAM|DMA: off-heap in PSRAM (the GC win) AND DMA-eligible so it stays open to the
    # #54 Stage-2 GDMA async window-copy (free on S3 -- all PSRAM is DMA-reachable).
    # The caps constants come from lcd_bus on the lvgl build and from moy_alloc's own
    # exports on the mainline P4 build (#58, no lcd_bus) -- the _mem alias covers both.
    assert "moy_alloc.malloc_dma(nbytes, _mem.MEMORY_SPIRAM | _mem.MEMORY_DMA)" in layercomp
    assert "import lcd_bus as _mem" in layercomp
    assert "_mem = moy_alloc" in layercomp
    assert "buf = bytearray(nbytes)" in layercomp   # host / no-allocator fallback
    # The old unconditional gc-heap alloc must be gone.
    assert "self._buf = bytearray(w * h * 2)" not in layercomp


def _editor_class(name):
    """An editor core, from its canonical source.

    The fork's moy_runtime re-exported these (`from editors import ...`) and the
    tests reached through it. The mainline port does not: the board gets
    `editors.py` as a STAGED shared module, so "is it wired" is a staging
    question, asked by test_editor_cores_are_staged_to_the_board below, and
    "does it work" is asked of the canonical file.
    """
    spec = importlib.util.spec_from_file_location("editors", Path("runtime") / "editors.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["editors"] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, name)


def test_editor_cores_are_staged_to_the_board():
    """The board can only import what build.sh stages -- assert it does."""
    staged = _staged()
    for name in ("editors.py", "code_layer.py", "paint_layer.py"):
        assert name in staged, "%s is not staged to this board" % name


def _load_moy_runtime():
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    # moy_runtime does `from editors import ...` and `from console import ...`; the
    # device freezes build-staged copies of runtime/{editors,audio,console}.py as
    # top-level modules. Register those same canonical files so the device module
    # loads under CPython (editors [+ block_editor_ui #29 Part 2 / map_editor_ui #32
    # / music_editor_ui #50 / perf_hud #43/#44] + audio first -- console imports
    # all of them).
    for name in ("editors", "block_editor_ui", "map_editor_ui", "scene_editor_ui",
                 "music_editor_ui",
                 "perf_hud", "update_ui", "system_menu_ui", "achievements_ui",
                 "layers", "bar_layer", "cards_layer", "paint_layer", "settings_layer", "code_layer", "widgets", "audio", "wallpaper", "launcher_layer", "console"):
        spec = importlib.util.spec_from_file_location(name, Path("runtime") / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod

    # moy_runtime now also does `from device_util import ...` / `from device_wifi
    # import ...` -- device-only modules authored in the shared device/ tree at
    # the repo root (staged into modules/ at build; the staged copies are
    # gitignored, so a fresh checkout has none). Register them from device/ so
    # the device module execs under CPython (device_util first: device_wifi
    # imports it).
    for dname in ("device_util", "device_wifi", "device_input", "device_diag",
                  "device_audio", "device_canvas", "device_api"):
        ds = importlib.util.spec_from_file_location(
            dname, DEVICE / (dname + ".py"))
        dmod = importlib.util.module_from_spec(ds)
        ds.loader.exec_module(dmod)
        sys.modules[dname] = dmod

    # moy_runtime now does `from carts_data import CARTS` (build-generated from
    # system_carts/ -- see tools/gen_device_carts.py). Register the same generated
    # data so the device module execs under CPython.
    sys.path.insert(0, "tools")
    import gen_device_carts
    sys.modules["carts_data"] = gen_device_carts.as_module("system_carts")

    spec = importlib.util.spec_from_file_location(
        "moy_runtime", ROOT / "modules" / "moy_runtime.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_code_editor_edits_buffer():
    CodeEditor = _editor_class('CodeEditor')
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
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    project = (Path("runtime") / "project.py").read_text(encoding="utf-8")
    editor_app = (Path("runtime") / "editor_app.py").read_text(encoding="utf-8")
    carts = (Path("runtime") / "moy_carts.py").read_text(encoding="utf-8")

    # The console + editor cores are shared with the host (imported, not redefined).
    assert "from console import Pointer, Workstation" in runtime
    # The editor edits the real source and saves it through the (injected) store.
    # (#39 step 2 the constructor also takes the responsive cols/rows window.) The tab
    # builder that constructs the CodeEditor moved to EditorApp.set_tab (Stage 3,
    # editor_app.py); ws.save_code (the compile-check/UI half) stays on the console.
    assert 'ws.editor = CodeEditor(ws.cart["src"],' in editor_app
    assert "def save_code(self):" in console
    # The store-write half moved to Project.commit_code (Stage 1b, project.py -- also
    # staged onto the device); ws.save_code keeps the compile-check/UI half + delegates.
    assert "ws.carts_store.save_code(self.cart, src)" in project
    assert "def save_code(cart, src):" in carts
    # run_desktop injects the device make_api + SD cart store into the shared console.
    assert "wire_workstation_core(ws, moy_carts, carts_root, make_api" in runtime

    # The console flips the keyboard between ASCII (code editor: clean typing) and
    # the raw matrix (running cart: true hold-to-move) on every screen change. It
    # does NOT poke raw_mode directly or enable raw itself -- it asks the keyboard,
    # which knows whether the firmware supports it.
    assert "kb.set_game_mode(not on)" in console
    assert "kb._enable_raw_mode()" not in console
    inp = (DEVICE / "moybyte" / "input.py").read_text(encoding="utf-8")
    assert "def set_game_mode(self, on):" in inp       # the per-screen mode toggle
    # The editor/launcher must boot in ASCII -- __init__ never enables raw (raw is
    # only entered later, via set_game_mode, once a cart is running).
    init_src = inp.split("def __init__(self, input_state):", 1)[1].split("\n    def ", 1)[0]
    assert "_enable_raw_mode" not in init_src
    assert "keyboard=keyboard" in runtime      # via wire_workstation_core


def test_unified_top_bar_wired_into_device_shell():
    """The unified, themeable 18px top bar (Stage 1): the launcher's old 14px status
    strip + the running-cart's labeled button row are now ONE bar of 16x16 IconSheet
    sprites on BOTH screens. The device freezes the SAME runtime/console.py +
    editors.py + moy_carts.py, so grep the canonical sources (staged into modules/ at
    build) for the new wiring."""
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    chrome = (Path("runtime") / "chrome.py").read_text(encoding="utf-8")
    bar_layer = (Path("runtime") / "bar_layer.py").read_text(encoding="utf-8")
    editors = _editors_src()
    carts = (Path("runtime") / "moy_carts.py").read_text(encoding="utf-8")
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))

    # The bar is 18px and drawn by ONE unified drawer -- the old per-screen
    # _draw_desktop_buttons is gone, and the running-cart ("desktop") screen now calls
    # the shared bar (the bar surface + its 18px geometry live in bar_layer.py, #46).
    assert "_STATUS_H = 18" in bar_layer
    assert "def _draw_desktop_buttons" not in console
    assert 'self.bar_layer._draw_status_strip("desktop")' in console

    # The 16x16 IconSheet + its slot map + the bar's icon-blit helper.
    assert "class IconSheet(SpriteSheet):" in editors
    assert "TILE = 16" in editors
    assert "_ICON = {" in chrome            # the bar's icon-slot map lives in chrome.py now
    assert "def _icon(self, kind, x, y, cv=None):" in console
    assert "self.look.icon_sheet" in console      # the sheet is the look's (#209 D)

    # Storage: load/save the editable theme beside the carts dir (absent = default).
    assert "system_icons" in carts
    assert "def load_system_icons(" in carts
    assert "def save_system_icons(" in carts

    # The device run loop builds + injects the IconSheet the same way as the host
    # (the boot loads run inside the shared console.wire_workstation_core).
    assert "wire_workstation_core(ws, moy_carts, carts_root, make_api" in runtime
    console_src = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    assert "ws.look.load_icon_sheet()" in console_src


def test_icon_theme_editor_wired_into_device_shell():
    """Stage 2 of the themeable top bar: a kid repaints the SYSTEM icon sheet in the
    PAINT editor (Settings -> EDIT ICONS) and it persists. The device freezes the same
    runtime/console.py + moy_carts.py, so grep the canonical sources for the wiring
    that MUST match the working cart-sprite save path (or the device SD bus hangs)."""
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    appearance = (Path("runtime") / "appearance.py").read_text(encoding="utf-8")
    settings_layer = (Path("runtime") / "settings_layer.py").read_text(encoding="utf-8")
    paint_layer = (Path("runtime") / "paint_layer.py").read_text(encoding="utf-8")
    carts = (Path("runtime") / "moy_carts.py").read_text(encoding="utf-8")
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))

    # Entry point: an "action" Settings row (EDIT ICONS) that opens the theme editor.
    # The EDIT-ICONS lifecycle lives in ThemeLayer (paint_layer.py) now; ws.open_theme
    # stays as the reachable entry point (a thin forwarder).
    assert '("icons", "EDIT ICONS", "action")' in settings_layer
    assert "def open_theme(self):" in console
    assert 'ws.menu_view = "theme"' in paint_layer
    assert "ws._editing_icons = True" in paint_layer
    assert "self._editing_icons" in console

    # Save: the theme editor persists via save_system_icons through the SAME _with_sd
    # wrapper the cart sprite save (save_sprites) uses -- on device that's with_sd_live,
    # the native single-bus path; anything else hangs the panel flush. The verb sits
    # with the sheet it writes (#209 landing E): ws.look, beside set_/load_icon_sheet.
    assert "def save_icons(self):" in appearance
    assert "hexs, ws.carts_root, _ICON_VERSION)" in appearance
    assert "ws._with_sd(lambda: ws.carts_store.save_system_icons(" in appearance
    # Live re-theme: a save re-adopts the sheet so the bar's per-kind image cache (and
    # the device's per-Image RGB565 blit cache) is dropped and rebuilt from new pixels.
    assert "            self.set_icon_sheet(sheet)\n            ws.ach.note" in appearance
    assert "def set_icon_sheet(self, sheet):" in appearance

    # The same persistence wrapper + can_manage gate the device wires for cart saves
    # already covers the theme save -- with_sd_live is the live SD write path.
    assert "ws._with_sd = _with_sd_synced" in runtime
    assert "return moybyte_sd.with_sd_live(fn)" in runtime


def test_device_draw_api_uses_tic80_names():
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    # The DeviceCanvas draw-method DEFS moved to device_canvas.py; the make_api
    # namespace bindings ("rect": canvas.rect, ...) stay in moy_runtime.
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")

    # TIC-80 conventions on the device canvas + api: rect/circ filled, rectb/circb
    # outlines, pix for pixels, print for text. The old PICO-8-ish names are gone.
    for name in ("def pix(", "def rect(", "def rectb(", "def circ(", "def circb("):
        assert name in device_canvas, name
    assert '"rect": canvas.rect, "rectb": canvas.rectb' in runtime
    assert '"circ": canvas.circ, "circb": canvas.circb' in runtime
    assert '"cls": canvas.cls, "pix": canvas.pix' in runtime
    assert '"print": canvas.print' in runtime
    # The canvas no longer exposes the old names (SpriteSheet keeps its own
    # pget/pset for the sheet pixel buffer, which is fine -- check the canvas/api).
    for gone in ("def rectfill(", "def circfill("):
        assert gone not in device_canvas, gone
    for gone in ("canvas.pset", "canvas.rectfill", '"text": canvas.print'):
        assert gone not in runtime, gone


def test_device_sprite_sheet_and_paint_editor():
    S, P = _editor_class('SpriteSheet'), _editor_class('PaintEditor')
    # spec=False throughout this block: these are small fixtures for the sheet's
    # own tile arithmetic and the paint editor, none of which reaches libmoy. A
    # real CART sheet is 16x32 (SPEC.md 3.2) -- see editors_sheet's module note.
    sh = S(4, 4, spec=False)                # 32x32, 16 sprites
    assert sh.count == 16 and (sh.w, sh.h) == (32, 32) and sh.is_blank()
    assert sh.tile_origin(5) == (8, 8)
    sh.tset(5, 1, 2, 9)
    assert sh.tget(5, 1, 2) == 9 and sh.dirty
    sh2 = S.from_hex(sh.to_hex(), 4, 4, spec=False)   # hex round-trips, dirty resets
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
    m = _load_moy_runtime()
    sheet = _editor_class('SpriteSheet')(4, 4, spec=False)      # small fixture, not a cart sheet
    sheet.tset(3, 0, 0, 11)
    calls = []
    tiles = []

    class StubCanvas:
        w = 320
        h = 240

        def spr(self, img, x, y, scale=1, flip=0):
            calls.append((img.w, img.h, x, y, scale, flip))

        def spr_tile(self, sheet, tile, x, y, colorkey=-1, scale=1, flip=0):
            tiles.append((tile, x, y, colorkey, scale, flip))

        def __getattr__(self, name):
            return lambda *a, **k: 0

    class StubInput:
        def held(self, name):
            return False

        def pressed(self, name):
            return False

    api = m.make_api(StubCanvas(), StubInput(), {}, sheet)
    api["spr"](3, 100, 60)                  # 1x1 sheet tile -> auto-batch via spr_tile (#63)
    assert tiles[-1] == (3, 100, 60, -1, 1, 0)
    # Image now lives in device_canvas (make_api exposes it in the cart ns).
    api["spr"](api["Image"].from_ascii(["#"], {"#": 7}), 8, 9, scale=4)  # Image -> immediate spr
    assert calls[-1] == (1, 1, 8, 9, 4, 0)
    # Multi-tile span (#30): spr(n, x, y, w=2, h=2) blits a 16x16 image from the
    # sheet immediately (the device path, so host == device for larger sprites).
    api["spr"](0, 12, 14, w=2, h=2)
    assert calls[-1] == (16, 16, 12, 14, 1, 0)
    # Flip (#11): spr(n, x, y, scale=1, flip=3) on a 1x1 tile forwards flip to spr_tile.
    api["spr"](3, 5, 6, -1, 1, 3)
    assert tiles[-1] == (3, 5, 6, -1, 1, 3)


def test_device_paint_editor_sizes_and_spans(tmp_path=None):
    # The shared PaintEditor's larger-sprite support (#30) under the device modules:
    # cycle_size steps 1->2->3, the 2x2 region writes the four constituent tiles,
    # and tile_span_image builds the contiguous block.
    m = _load_moy_runtime()
    cols = 16
    sh = _editor_class('SpriteSheet')(cols, 16, spec=False)     # half-height fixture, not a cart sheet
    pe = _editor_class('PaintEditor')(sh)
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
    m = _load_moy_runtime()
    from editors import TileMap
    sheet = _editor_class('SpriteSheet')(4, 4, spec=False)      # small fixture, not a cart sheet
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
    SpriteSheet = _editor_class('SpriteSheet')
    sh = SpriteSheet(4, 4, spec=False)           # small fixture, not a cart sheet
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
    # clears the cache when it changes. Checked here on the MULTI-TILE span path (which
    # still resolves through make_api's tile_cache); plain 1x1 sprites auto-batch (#63)
    # straight from the sheet, so a paint edit needs no cache invalidation at all.
    m = _load_moy_runtime()
    sheet = _editor_class('SpriteSheet')(4, 4, spec=False)      # small fixture, not a cart sheet
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
    api["spr"](0, 0, 0, w=2, h=2)
    first = blitted[-1]
    # Same sheet, same id, no edit -> the cached span Image is reused (object identity).
    api["spr"](0, 0, 0, w=2, h=2)
    assert blitted[-1] is first

    # A paint edit bumps sheet.gen -> the cache is invalidated and a fresh Image
    # (rebuilt from the new pixels) is blitted next frame.
    sheet.pset(0, 0, 5)
    api["spr"](0, 0, 0, w=2, h=2)
    assert blitted[-1] is not first


def test_device_sprite_storage_wired():
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    project = (Path("runtime") / "project.py").read_text(encoding="utf-8")
    carts = (Path("runtime") / "moy_carts.py").read_text(encoding="utf-8")
    # device cart API -- also takes the injected audio backend (#16) + tilemap
    # (#32) + persistent memory (pmem, #11).
    # make_api now also takes the capability-gated wifi backend LAST (#38).
    assert "def make_api(canvas, input, config, sheet=None, audio=None," in runtime
    assert "pmem=None, wifi=None, images=None, scenes=None, tables=None," in runtime
    assert "texts=None, net=None, gpio=None, owner=\"cart\"):" in runtime
    assert "self.sheet = self._build_sheet()" in console                   # shared console
    # The sprite store-write moved to Project.commit_sprites (Stage 1b, project.py --
    # also staged onto the device); ws.save_sprites stays as the tested forward.
    assert "ws.carts_store.save_sprites(self.cart, hexs)" in project
    assert "def save_sprites(cart, hex_text):" in carts
    assert '"sprites": sprites' in carts


def test_device_audio_wired():
    # Audio core (#16): shared model/mixer (runtime/audio.py) + device I2S backend
    # stub + host==device API surface + sounds.json storage. Source-level checks
    # mirror how the other firmware tests grep the frozen device modules.
    audio = (Path("runtime") / "audio.py").read_text(encoding="utf-8")
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    player = (Path("runtime") / "player.py").read_text(encoding="utf-8")
    project = (Path("runtime") / "project.py").read_text(encoding="utf-8")
    carts = (Path("runtime") / "moy_carts.py").read_text(encoding="utf-8")
    build = (ROOT / "build.sh").read_text(encoding="utf-8")
    device_audio = (DEVICE / "device_audio.py").read_text(encoding="utf-8")

    # The shared audio core: dependency-light (math only) synth + mixer + model.
    assert "class AudioEngine:" in audio
    assert "def render(self, nframes):" in audio
    # The allocation-free render core the device backend feeds I2S from (#16): the
    # device reuses one persistent buffer, so render_into must exist and render()
    # must delegate to it (keeps host bytes-returning behavior identical).
    assert "def render_into(self, out, nframes):" in audio
    assert "self.render_into(out, nframes)" in audio
    assert "class AudioBank:" in audio
    # Project builds the per-cart AudioEngine (Stage 1 moved _build_audio there);
    # the console's cart tick still feeds it via self.audio.tick.
    assert "from audio import" in project
    assert "AudioBank" in project and "AudioEngine" in project
    assert "def _build_audio(self):" in project
    # The cart tick's mixer feed moved to Player.tick (Stage 2, player.py) as
    # ws.audio.tick(dt); it still runs every running-cart frame.
    assert "ws.audio.tick(dt)" in player
    # The device make_api binds the same six audio names as the host.
    for name in ('"sfx": _sfx', '"beep": _beep', '"music": _music',
                 '"music_stop": _music_stop', '"sound_stop": _sound_stop',
                 '"volume": _volume'):
        assert name in runtime, name
    # The device I2S backend is wired in (heard on a T-Deck 2026-08-09).
    assert "class DeviceAudio:" in device_audio
    assert "from machine import I2S, Pin" in device_audio
    assert "mode=I2S.TX" in device_audio
    assert "make_audio=make_audio" in runtime  # via wire_workstation_core
    # The feed must be NON-BLOCKING: irq() flips the I2S port into non-blocking mode
    # and a completion flag gates the next write, so write() can never stall the
    # single-threaded render loop (the cause of the reported FPS drop / crackle).
    assert "self.i2s.irq(self._on_done)" in device_audio
    assert "self._na.render(buf, n)" in device_audio
    assert "if self._busy:" in device_audio
    # sounds.json storage in the shared cart store.
    assert "def save_sounds(cart, bank_dict):" in carts
    assert '"sounds": sounds' in carts
    # build.sh stages the shared audio module into the frozen modules tree.
    assert "audio.py" in _staged()


def test_music_editor_wired_into_device_shell():
    # Music/sound editor (#50): the shared MusicEditor core (runtime/editors.py) +
    # its UI (runtime/music_editor_ui.py, extracted from console.py) + console's
    # glue (a menu_view, the top-bar switcher, save to sounds.json). It lives in
    # the SAME shared files build.sh freezes onto the device, so source-level
    # greps prove it's on both ends (host == device).
    editors = _editors_src()
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    editor_app = (Path("runtime") / "editor_app.py").read_text(encoding="utf-8")
    layers = (Path("runtime") / "layers.py").read_text(encoding="utf-8")
    music_ui = (Path("runtime") / "music_editor_ui.py").read_text(encoding="utf-8")
    chrome = (Path("runtime") / "chrome.py").read_text(encoding="utf-8")
    bar_layer = (Path("runtime") / "bar_layer.py").read_text(encoding="utf-8")
    project = (Path("runtime") / "project.py").read_text(encoding="utf-8")
    carts = (Path("runtime") / "moy_carts.py").read_text(encoding="utf-8")
    build = (ROOT / "build.sh").read_text(encoding="utf-8")

    # The editor CORE is a single shared class (not redefined on the device),
    # on the #111 op-history core (History/_MusicOps + the OpHistoryMixin
    # facade) -- see test_op_history_wiring.py.
    assert "class MusicEditor(OpHistoryMixin):" in editors
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    assert "class MusicEditor" not in runtime, "device redefines MusicEditor"
    # editors.py must stay dependency-free (the frozen-module contract): it must NOT
    # import audio just to edit the bank -- SFX/MusicTrack are injected as factories.
    assert "import audio" not in editors
    assert "from audio import" not in editors

    # The music editor's UI (extracted from console.py) imports the shared core +
    # the audio factories it injects.
    assert "MusicEditor" in music_ui
    assert "from audio import" in music_ui and "MusicTrack" in music_ui and "SFX" in music_ui
    # A new menu sub-view + its open/build path, mirroring map/blocks. The tab open +
    # build path moved to EditorApp (Stage 3, editor_app.py); ws._open_music stays as
    # the tested forward. The music-preview frame dispatch stayed on the console (ws).
    assert 'self.musicedit = MusicEditor(bank' in music_ui
    assert "def _open_music(self):" in console
    assert 'elif view == "music":' in editor_app
    assert 'self.menu_view == "music"' in console         # music-preview frame dispatch
    # The top-bar mode switcher (the 6th icon) + its tap action + drawn icon -- the bar
    # surface + its geometry live in bar_layer.py now (#46 file split).
    assert "_MUSIC_BTN = (" in bar_layer
    assert "ws._open_music()" in bar_layer         # the bar tool-switch tap (BarLayer, #46)
    assert 'ws._icon("music"' in bar_layer
    assert '"music": 15' in chrome                          # IconSheet slot for the icon (chrome.py)
    # SAVE persists to sounds.json through the existing shared store. The store-write
    # moved to Project.commit_sounds (Stage 1b, project.py -- also staged onto the
    # device, like save_map/save_code -- it uses the shared ws.save_status field);
    # ws.save_sounds stays as the tested forward MusicEditorUI's SAVE dispatches to.
    assert "def save_sounds(self):" in console
    assert "ws.carts_store.save_sounds(self.cart, bank_dict)" in project
    assert "def save_sounds(cart, bank_dict):" in carts
    # Live preview drives the SAME injected AudioEngine the cart uses, and the frame
    # loop ticks the mixer + keeps animating while a preview is up. The music-editor's
    # own mixer feed is _MusicLayer.draw (layers.py); the running-cart mixer feed moved
    # to Player.tick (Stage 2). Grep the music preview's feed in layers.py.
    assert "def _play_music_preview(self):" in music_ui
    assert "ws.audio.tick(dt)" in layers
    # The editor lives in the shared files build.sh freezes onto the device.
    staged = _staged()
    for name in ("editors.py", "console.py", "music_editor_ui.py", "perf_hud.py",
                 "update_ui.py", "system_menu_ui.py", "achievements_ui.py"):
        assert name in staged, name


def test_native_moy_audio_is_vendored_libmoy():
    # #97: the device synth is not ours. libmoy -- moy-spec's own C implementation
    # of SPEC.md 8 -- is vendored into native/moy_audio/libmoy/ and COMPILED IN, so
    # the T-Deck and the web runner are conformant by construction rather than by a
    # hand-maintained twin. These greps pin the vendoring itself: that the sources
    # are present, built, and not quietly replaced by a reimplementation.
    lib = NATIVE / "moy_audio" / "libmoy"
    assert (lib / "moy_audio.c").exists()
    assert (lib / "moy_audio.h").exists()
    assert (lib / "LICENSE").exists()          # MIT, carried with the source
    upstream = (lib / "UPSTREAM.md").read_text(encoding="utf-8")
    assert "Do not edit these files" in upstream
    # WHICH commit, and whether the copy still matches it, moved out of this
    # page and into native/libmoy_vendor.json, where a hash can check it --
    # tests/test_libmoy_vendor.py is that check. A prose line reading "vendored
    # at <sha>" could say anything.
    assert "libmoy_vendor.json" in upstream
    assert "make vendor-libmoy" in upstream

    lib_c = (lib / "moy_audio.c").read_text(encoding="utf-8")
    # A few load-bearing SPEC.md 8.3 constants, so a re-vendor that silently drops
    # the calibrated synthesis fails here rather than on someone's speaker.
    assert "109.0f / 110.0f" in lib_c          # the phaser's detune, not 127/128
    assert "8.858923f" in lib_c                # the noise low-pass cutoff
    assert "moy_bank_parse" in lib_c

    c = (NATIVE / "moy_audio" / "modmoy_audio.c").read_text(encoding="utf-8")
    cmake = (NATIVE / "moy_audio" / "micropython.cmake").read_text(encoding="utf-8")
    mk = (NATIVE / "moy_audio" / "micropython.mk").read_text(encoding="utf-8")
    build = (ROOT / "build.sh").read_text(encoding="utf-8")
    device_audio = (DEVICE / "device_audio.py").read_text(encoding="utf-8")

    # The module is a BINDING: it includes libmoy's header and calls its public
    # API. Every SPEC.md 8.2 verb is forwarded, not reimplemented.
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
    # Staged per board.toml [native.shared] (re-staged every build).
    assert "moy_audio" in board_config.native_modules(ROOT)

    # DeviceAudio forwards verbs and hands the bank over ONCE per cart, keeping a
    # A build WITHOUT moy_audio is SILENT -- the Python-twin fallback died with
    # moycore stage 0 (#97): the twin's drift class was the bug, and silence is
    # the honest degradation. The absence branch must exist and say so.
    assert "import moy_audio" in device_audio
    assert "self._na = moy_audio" in device_audio
    assert "self._na = None" in device_audio                     # absence branch
    assert "SILENT" in device_audio
    assert "self.engine.play_sfx" not in device_audio            # no twin lane
    assert "self.engine.render_into" not in device_audio
    assert "na.bank_load(json.dumps(bank.to_dict()))" in device_audio
    assert "def _sync_bank(self):" in device_audio               # re-push after an edit
    assert "self.engine.bank.rev != self._bank_rev" in device_audio
    assert "self._na.sfx(" in device_audio
    assert "self._na.render(buf, n)" in device_audio
    # The per-frame voice marshalling is gone -- nothing crosses per frame now.
    # Matched as CALLS, since the module docstring names them all explaining what
    # went away.
    for gone in (".voice_set(", ".voice_read(", "._advance_music(",
                 "self._commit_gen", "self._await_active"):
        assert gone not in device_audio, gone


def test_web_runner_audio_forwards_to_libmoy():
    # The wasm runner loads the SAME native module (its build.sh stages
    # native/moy_audio and the module ships its own micropython.mk), so the
    # browser's synth is libmoy too -- one audible behaviour across all three
    # targets. Pin the forwarding, and that no per-frame marshalling came back.
    web = Path("firmware/web_runner")
    boot = (web / "web_boot.py").read_text(encoding="utf-8")
    build = (web / "build.sh").read_text(encoding="utf-8")
    assert "native/moy_audio" in build
    # The module carries its own Makefile fragment now; the runner must not be
    # copying a second, drifting copy over it.
    assert not (web / "moy_audio_micropython.mk").exists()
    assert "moy_audio_micropython.mk" not in build
    assert "self._ka.bank_load(" in boot
    assert "self._ka.render(buf, n)" in boot
    assert "def is_active(self):" in boot
    for gone in (".voice_set(", ".voice_read(", "._advance_music("):
        assert gone not in boot, gone


def test_native_moy_audio_core1_task_wired():
    # CRACKLE FIX (#41): the I2S feed used to be coupled to the render loop -- tick()
    # fed I2S once per ~50-80 ms frame on core 0, so a long draw under-ran the DMA ->
    # crackle. The fix is a dedicated native C task PINNED TO CORE 1 that owns the IDF
    # i2s_std channel and feeds it continuously, decoupled from rendering. core 0 (the
    # MicroPython VM) cannot run Python on core 1; only a pure-C task can. Source-level
    # checks, the same way the other firmware tests grep the device sources.
    c = (NATIVE / "moy_audio" / "modmoy_audio.c").read_text(encoding="utf-8")
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    audio_src = (Path("runtime") / "audio.py").read_text(encoding="utf-8")
    device_audio = (DEVICE / "device_audio.py").read_text(encoding="utf-8")

    # The C side spawns a FreeRTOS task PINNED TO CORE 1 that owns the I2S write loop.
    assert "xTaskCreatePinnedToCore(" in c
    assert "moy_audio_task" in c            # the core-1 feeder task body
    # pinned to core 1 (the last xTaskCreatePinnedToCore arg); the comment names it too
    assert "1 /* core 1 */" in c
    assert "core 1" in c
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

    # The Python DeviceAudio prefers the core-1 task and exposes a revert flag.
    assert "MOY_AUDIO_CORE1 = True" in device_audio          # default-on, the crackle fix
    assert "if MOY_AUDIO_CORE1 and self._na is not None:" in device_audio
    assert "self._na.audio_start(I2S_BCK, I2S_WS, I2S_DOUT, AUDIO_RATE)" in device_audio
    assert "self._core1 = True" in device_audio
    # In core-1 mode tick() does NOTHING at all now (#97). It used to run the
    # music scheduler and commit voice state across every frame; libmoy owns the
    # sequencers, so there is no per-frame work left to do on core 0.
    assert "if self._core1:\n            return" in device_audio
    assert "self._core1 = True" in device_audio
    # The legacy single-core feed stays as the FALLBACK (machine.I2S) so a bad result
    # is revert-able (MOY_AUDIO_CORE1=False). It renders through the NATIVE module
    # (no moy_audio -> no I2S open at all: silence, #97 stage 0), and TOPS the deep
    # DMA ring UP toward full each tick (the single-core crackle fix) instead of
    # feeding exactly rate*dt (which kept the ring near-empty -> under-ran).
    assert "legacy single-core feed" in device_audio
    # only open machine.I2S in fallback, and only with a renderer to feed it
    assert "if not self._core1 and self._na is not None:" in device_audio
    assert "self.i2s = I2S(" in device_audio
    assert "AUDIO_IBUF_FRAMES = AUDIO_IBUF // 2" in device_audio
    assert "self._buffered" in device_audio                  # software ring-occupancy estimate
    assert "want = AUDIO_IBUF_FRAMES - self._buffered" in device_audio
    assert "self._buffered += n" in device_audio             # account for what we wrote
    # AUDIO DIAGNOSTICS: each sfx/music trigger logs an event line, so what
    # reached the mixer is readable on serial/SD when the board is playing blind.
    # (The periodic core-1 "active=/committed=" sample went with the commit
    # machinery it reported on -- there is nothing per-frame left to sample.)
    assert "AUDIO_DIAG = True" in device_audio
    assert "def _diag_trigger(self, kind, n, chan):" in device_audio
    assert '_diag_note("AUDIO"' in device_audio


def test_core1_writeback_cannot_clobber_a_fresh_trigger():
    # THE OVERLAPPING-SFX DROP, and why it cannot recur (#41 -> #97).
    #
    # The core-1 task used to mix from a SNAPSHOT of a shared voice array and
    # fold its advanced cursor back afterwards, which meant deciding whose copy
    # was authoritative. The first attempt used a content proxy (same nsteps +
    # first step + step_dur) that a same-SFX retrigger satisfies exactly, so
    # "sound 1 ends inside the block, sound 2 starts on the reused channel"
    # folded active=0 back over the fresh trigger: sound 2 never played and the
    # channel leaked as busy. That was fixed with an exact per-voice commit
    # counter.
    #
    # The counter is gone now, because the thing it arbitrated is gone: libmoy
    # owns the state and there is exactly ONE copy of it, so there is no
    # snapshot, no fold-back and nothing to reconcile. This test now pins the
    # structural property rather than the fix -- if a second copy of the voice
    # state ever reappears, so does the bug, and this is where it should be
    # argued out.
    c = (NATIVE / "moy_audio" / "modmoy_audio.c").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in c.splitlines()
                     if not ln.lstrip().startswith("//"))

    # No second copy of the engine state, so no reconciliation machinery.
    for gone in ("moy_voice_t", "snap[", "memcpy(snap", "->seq", "shared->"):
        assert gone not in code, gone
    # The task renders straight out of the one engine struct, under the lock.
    assert "moy_audio_render(&s_audio, block + off, MOY_MIX_CHUNK)" in code
    assert code.count("static moy_audio  s_audio;") == 1


def test_device_wifi_wired():
    # WiFi (#38): the device network.WLAN service backend + capability-gated `wifi`
    # injection + autoconnect + the shared credential store. Source-level checks
    # mirror how the other firmware tests grep the frozen device modules.
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    player = (Path("runtime") / "player.py").read_text(encoding="utf-8")
    carts = (Path("runtime") / "moy_carts.py").read_text(encoding="utf-8")
    # The DeviceWifi backend + make_wifi/autoconnect_wifi now live in device_wifi.py
    # (extracted from moy_runtime.py); run_desktop still calls them (asserts below).
    device_wifi = (DEVICE / "device_wifi.py").read_text(encoding="utf-8")

    # make_api takes the gated wifi backend LAST and injects `wifi` only when set.
    assert "def make_api(canvas, input, config, sheet=None, audio=None," in runtime
    assert "pmem=None, wifi=None, images=None, scenes=None, tables=None," in runtime
    assert "texts=None, net=None, gpio=None, owner=\"cart\"):" in runtime
    assert 'ns["wifi"] = wifi' in runtime
    # The device WLAN backend (STUB -- needs hardware verification). LAZY: the WLAN
    # stack is brought up on demand (scan/connect), NEVER at boot -- bringing it up at
    # boot reserved the internal RAM the LCD DMA flush needs and froze the desktop
    # (OSError 257 / ESP_ERR_NO_MEM). The radio comes up only via _ensure_wlan.
    assert "class DeviceWifi:" in device_wifi
    assert "def _ensure_wlan(self):" in device_wifi        # lazy radio bring-up
    assert "network.WLAN(network.STA_IF)" in device_wifi    # (lives inside _ensure_wlan now)
    assert "def make_wifi(store=None, root=None):" in device_wifi
    assert "def autoconnect_wifi(wifi):" in device_wifi     # still defined, but NOT called at boot
    # run_desktop wires the system service but does NOT bring WiFi up at boot (WLAN
    # reserves the internal RAM the LCD DMA needs -- WiFi<->display coexistence is #38).
    assert "make_wifi(moy_carts, carts_root)" in runtime   # via wire_workstation_core
    # autoconnect is NOT called eagerly at boot; it is only reused, deferred, by the OTA
    # online-update path (#53) via the go_online lambda -- never as a bare boot statement.
    assert "go_online=lambda: autoconnect_wifi(ws.wifi)" in runtime
    assert runtime.count("autoconnect_wifi(ws.wifi)") == 1
    # Each frame is guarded so one bad flush can't brick the device.
    assert "Moybyte frame error:" in runtime
    # The shared console gates injection on the "network" manifest permission.
    # _cart_has_perm stays on Workstation (console.py); the gating call moved into
    # Player.start (Stage 2, player.py), which reaches it via ws._cart_has_perm.
    assert "def _cart_has_perm(self, name):" in console
    assert 'ws.wifi if ws._cart_has_perm("network") else None' in player
    # The shared cart store carries permissions + persists known networks.
    assert '"permissions": man.get("permissions", []),' in carts
    assert "def load_wifi(root=CARTS_DIR):" in carts
    assert "def save_wifi(networks, root=CARTS_DIR):" in carts
    assert "def remember_wifi(ssid, password, root=CARTS_DIR):" in carts
    assert "def forget_wifi(ssid, root=CARTS_DIR):" in carts


def test_editor_cores_are_shared_single_source():
    # One canonical file (runtime/editors.py); the device imports it and the build
    # stages it into the frozen modules tree -- no duplicated class definitions.
    editors = _editors_src()
    for cls in ("class CodeEditor:", "class SpriteSheet:",
                "class PaintEditor(OpHistoryMixin):"):   # #111 op-history core
        assert cls in editors, cls
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    # The HOST canvas is the boards' own class now (runtime/host_canvas.py builds
    # `device_canvas.DeviceCanvas` on CPython), so "the host does not redefine the
    # cores" is checked where a redefinition could still be written.
    host_canvas = Path("runtime/host_canvas.py").read_text(encoding="utf-8")
    # Neither backend redefines the shared cores.
    for cls in ("class CodeEditor", "class SpriteSheet", "class PaintEditor"):
        assert cls not in runtime, "device redefines " + cls
        assert cls not in host_canvas, "host canvas redefines " + cls
    # The build stages the canonical files into modules/ so the device freezes them.
    staged = _staged()
    for name in ("editors", "editors_base", "editors_code", "editors_sheet",
                 "editors_paint_map", "editors_block", "editors_music"):
        assert name + ".py" in staged, name


def test_micropython_offline_diag_wiring():
    """Offline on-device diagnostics (moybyte_diag): a RAM ring persisted to SD and
    dumped to serial at the NEXT boot, since run_desktop's takeover loop starves USB
    serial. Grep the frozen device sources for the boot-dump, the with_sd_live flush,
    and the perf-sample wiring (the firmware tests assert structure, not execution)."""
    diag = (DEVICE / "moybyte_diag.py").read_text(encoding="utf-8")
    device_canvas = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    shell = (ROOT / "modules" / "moybyte_shell.py").read_text(encoding="utf-8")
    runtime = ((ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
               + (DEVICE / "device_api.py").read_text(encoding="utf-8")
               + Path("runtime/cart_api.py").read_text(encoding="utf-8"))
    console = (Path("runtime") / "console.py").read_text(encoding="utf-8")
    player = (Path("runtime") / "player.py").read_text(encoding="utf-8")
    # The _diag_* logging functions moved to device_diag.py (extracted from
    # moy_runtime.py); run_desktop still CALLS them (the _diag_X(...) asserts
    # stay vs runtime, the def/log-format asserts point at device_diag).
    device_diag = (DEVICE / "device_diag.py").read_text(encoding="utf-8")

    # The diag module exists with the bounded ring + the stable dump markers + the
    # single-file (one-session) log path.
    assert "class _Ring(object):" in diag
    assert 'DUMP_HEADER = "===== Moybyte diag dump (previous session) ====="' in diag
    assert 'DUMP_FOOTER = "===== end diag dump ====="' in diag
    assert 'LOG_PATH = "/sd/moybyte/diag.log"' in diag
    assert "ENABLED = True" in diag                       # default-on, documented toggle
    # The on/off toggle is documented as how to disable.
    assert "DISABLE" in diag
    # log() echoes every line live to stdout (the empirical loop-serial test) in
    # addition to buffering -- so reading /dev/ttyACM* DURING a run is a direct test
    # of whether the takeover loop actually starves serial.
    assert "ECHO_LIVE = True" in diag
    assert 'print("Moybyte", line)' in diag
    # No rotation / second file (owner: the file is just the most-recent session).
    assert "diag.prev.log" not in diag

    # The previous-session dump is a REPL affordance now: its boot hook rode the
    # #56 pre-display SD prefetch A/B path, which shipped OFF and was removed
    # (nothing touches SD before the panel is up). The reader keeps the bus-safe
    # pre-display machine.SDCard path (moybyte_sd.with_sd), NOT the live native
    # path, so calling it from the REPL before the desktop starts stays safe.
    assert "def dump_previous_to_serial(" in diag
    assert "_dump_diag" not in shell            # the boot hook is gone with the prefetch
    assert "moybyte_sd.with_sd(" in diag

    # Periodic SD flush goes through the live single-bus path (with_sd_live), runs
    # between frames, and overwrites the whole ring (one session per file).
    assert "def flush_to_sd(with_sd):" in diag
    assert 'open(LOG_PATH, "w")' in diag                  # overwrite, never append
    assert "_diag_flush(diag, ws)" in runtime
    assert 'with_sd = getattr(ws, "_with_sd", None)' in device_diag   # = with_sd_live (in _diag_flush)
    assert "diag.flush_to_sd(with_sd)" in device_diag

    # Perf samples: this board persists them for the offline dump, but the LINE
    # is not composed here any more (#206 item 2). One format for all three
    # boards (runtime/perf_line.py), measured by one body on the shared
    # FrameLoop.account hook, printed like every other board AND ringed here --
    # through `ring`, not `log`, which would put it on the wire twice.
    assert "def ring(tag, msg):" in diag
    assert "def format_perf(" not in diag and "def log_perf(" not in diag
    assert "_diag_perf_sample(" not in runtime
    assert "def _diag_perf_sample(" not in device_diag
    assert 'diag.ring("PERF", line[5:])' in runtime
    # The shared console EXPOSES the numbers host-safely; the sampler READS them.
    # net= is the #65 lockstep witness -- the shared tick rate a LINKED game
    # renders at (the console gates every frame the tick is not due for) -- and
    # `-` when no session gates anything. It comes from perf_net(), NOT from
    # perf_sample: the meter consumes its window and perf_sample is also the
    # `is a cart running?` probe every other diag helper here calls.
    assert "def perf_net(self):" in console
    assert "def perf_sample(self):" in console
    assert "ws.perf_sample()" in device_diag               # the other helpers' probe
    assert "self.perf_capture = False" in console         # default off -> host identical
    # 2026-08-03: capture is no longer unconditional -- it follows the persisted
    # PERF DIAG toggle (the deep meters cost ~1-1.5ms/frame, a kid-mode tax),
    # and the sampler re-syncs it live when the toggle flips.
    assert 'ws.perf_capture = bool(getattr(ws, "diag_live", False))' in runtime
    assert "ws.perf_capture = _live" in runtime           # the 3s diag re-sync
    assert "_perf = self.perf_hud or self.perf_capture" in console

    # DRAWBRK (#43 follow-up): the phase split of draw= into cart _update (logic) /
    # cart _draw (render) / audio.tick / console chrome, sampled alongside PERF so we
    # can see where the per-frame draw cost actually goes instead of guessing.
    assert "def perf_breakdown(self):" in console
    assert 'diag.log("DRAWBRK", "logic=%.2f render=%.2f%s audio=%.2f chrome=%.2f"' in device_diag
    assert "_diag_drawbrk(diag, ws)" in runtime
    assert "ws.perf_breakdown()" in device_diag

    # bg= (#172): render's declared-backdrop share. Player.tick TIMES the
    # background() restore and charges it to render -- it runs before the render
    # bracket opens, so it used to fall out of `draw - upd - cart - audio` and
    # surface as CHROME. On glass that read as ~4.7ms of Brick Siege's frame
    # under a bucket named for the shell, with CHROMEBRK naming none of it, which
    # is what sent #172 hunting a shell regression that was never there.
    assert "def perf_backdrop(self):" in console
    assert "ws._pf_bg = bg" in player                      # timed by the writer
    assert "+ bg     # cart _draw + backdrop" in player    # ...and folded into render
    assert 'bg_s = (" (bg=%.2f)" % pb())' in device_diag

    # GC line (#63, sakura ~14fps profiling): the forced-collect pause + churn, sampled on
    # the ~3s cadence (gc.mem_alloc/free WALK the heap, so never per frame).
    assert "def _diag_gc(diag):" in device_diag
    assert 'diag.log("GC", "collect=%dms free=%dk live=%dk churn=%dk"' in device_diag

    # DRAW2 line (#63): split the render EMA into the two native pixel ops -- the layer
    # window-copy (blit_window) vs the sprite blit_batch -- so we know which one is the
    # real cost of a full-frame cart (sakura's ~120ms render). Timed in microseconds
    # around the native calls, reset per frame by batch_reset.
    assert "def _diag_draw2(diag, ws):" in device_diag
    assert ('diag.log("DRAW2", "layer=%.2fms batch=%.2fms '
            'map=%.2fms text=%.2fms "') in device_diag
    assert '"fill=%.2fms gated(fill=%d text=%d)"' in device_diag
    assert "_diag_draw2(diag, ws)" in runtime

    # ...and the gated microseconds are FOLDED IN. The #155 native gates shadow
    # rect/rectb/print/pix, so a gated call never reaches the Python method that
    # holds _t_fill_us -- fill read 0.00ms for any cart using them while the
    # gates did all the filling. The C side always timed itself and
    # DeviceCanvas always exposed gate_counts(); the defect was that NOTHING
    # CALLED IT, which no format assertion above could have caught. So pin the
    # consumer, and pin the per-frame reset that keeps it from being a running
    # total (found via zoomed celeste: 29.6ms render, 20.6ms in no bucket).
    assert 'gc = getattr(cv, "gate_counts", None)' in device_diag
    assert "nf, nt, gf, gt = gc()" in device_diag
    assert "self.gate_counts_reset()" in device_canvas
    assert "self._t_layer_us += _ticks_diff(_ticks_us(), _t0)" in device_canvas
    assert "self._t_batch_us += _ticks_diff(_ticks_us(), _t0)" in device_canvas
    assert "self._t_map_us += _ticks_diff(_ticks_us(), _t0)" in device_canvas
    assert "self._t_text_us += _ticks_diff(_ticks_us(), _t0)" in device_canvas

    # CHROMEBRK (#66 lever 5): the sub-split of the chrome remainder (bar /
    # composite / cursor / other) so a trim targets the real cost.
    assert "def perf_chrome(self):" in console
    assert 'diag.log("CHROMEBRK", "bar=%.2f cmp=%.2f cur=%.2f other=%.2f"' in device_diag

    # PUMP (#66 lever 4): bounce-feed pacing -- SPI idle gaps + feed time, the
    # measure-first data for band size / pump period / third-slot tuning, plus
    # blocked= and timeouts=/errs=/stopfail=, which the C cannot raise and so
    # can only be counted.
    compositor = _panel_base_src()   # shared body since 2026-08-21 (#206 item 1)
    assert "def bounce_stats(self):" in compositor
    assert ('"pump=%.2f idle=%.2f gaps=%d feed=%.2f blocked=%.2f "\n'
            '                 "bands=%d fold=%d timeouts=%d errs=%d stopfail=%d"'
            in device_diag)
    assert "_diag_pump(diag, comp)" in runtime
    # fold= is the #190 liveness proof. Behaviour is pinned by
    # tests/test_banded_panel.py; what a grep can pin is that the definition
    # exists on the board with the lever and NOT on the one without it.
    guition_panel = (Path("firmware/guition_jc3248w535/modules")
                     / "guition_panel.py").read_text(encoding="utf-8")
    assert "def fold_count(self):" in guition_panel
    assert "fold_stats()[0]" in guition_panel
    assert "fold_count" not in _panel_src()[0], (
        "the T-Deck has no fold; absence is how a board says it lacks a lever")

    # I2CSTAT (#69): per-session kbd/touch I2C latency (max + >5ms/>20ms counts),
    # so the 13-60ms keyboard stalls are sized across a session, not just inside
    # >80ms HITCH frames.
    inp_mod = (DEVICE / "moybyte" / "input.py").read_text(encoding="utf-8")
    device_audio = (DEVICE / "device_audio.py").read_text(encoding="utf-8")
    assert "def _timed_read(self, nbytes):" in inp_mod
    assert "I2C_TIMEOUT_US" in inp_mod
    assert 'diag.log("I2CSTAT",' in device_diag
    assert "_diag_i2cstat(diag, keyboard, touch)" in runtime

    # Existing diagnostics routed through diag (printed AND persisted): boot heap,
    # the frame-error trace, the in-cart crash, and the audio I2S status line.
    assert '_diag_log("frame error", exc, diag)' in runtime
    assert '_diag_log("cart error", _ce, diag)' in runtime
    assert '_diag_note("audio", "I2S' in device_audio


def test_ota_two_channel_wired():
    # #53 two-channel OTA: moy_ota learns its channel from a build-stamped _ota_build,
    # offers cross-channel switches, and the manifest fetch is channel-aware; the shared
    # console exposes a CHANNEL Settings toggle; build.sh stamps the channel. Device code
    # isn't executed here (host offer-logic is in test_ota_manifest), so grep the sources.
    kc = (DEVICE / "moy_ota.py").read_text(encoding="utf-8")
    assert "FIRMWARE_CHANNEL" in kc
    assert "import _ota_build" in kc                     # build-stamped identity
    assert "def channel(self):" in kc
    assert "def offers(self, manifest" in kc
    assert "def version_label(self):" in kc
    assert "def manifest_url(self, channel=None):" in kc
    assert "def check_online(self, channel=None):" in kc
    # The shared console (staged to the device) drives the channel toggle + flow.
    console = Path("runtime/console.py").read_text(encoding="utf-8")
    settings_layer = Path("runtime/settings_layer.py").read_text(encoding="utf-8")
    assert '("ota_channel", "CHANNEL", "channel")' in settings_layer
    assert "def _cycle_channel(self, d):" in console
    # u.offers(...) is inside _pump_update, which now lives in update_ui.py (UpdateUI).
    update_ui = Path("runtime/update_ui.py").read_text(encoding="utf-8")
    assert "u.offers(manifest, ch)" in update_ui
    # The build stamps the channel into a generated _ota_build module + dist
    # manifest -- via the SHARED build lib since 2026-08-17 (both boards call
    # moybyte_ota_identity), so the grep spans build.sh plus the lib.
    build = ((ROOT / "build.sh").read_text(encoding="utf-8")
             + Path("tools/esp32_build_lib.sh").read_text(encoding="utf-8"))
    assert "moybyte_ota_identity" in build
    assert "MOYBYTE_OTA_CHANNEL" in build
    assert "_ota_build.py" in build
    assert "ota_build.json" in build


def test_no_undefined_names_in_extracted_modules():
    """Guard the #1 hazard of the moy_runtime / console extractions: a moved
    function/closure body referencing a symbol its new namespace never imported
    back. Those only NameError at CALL time (a cart draws, a browser hits /assets),
    so the host shim's module-load + the golden single-frame renders can't see them
    -- they reach hardware as 'NameError: name _X isn't defined'. pyflakes' static
    undefined-name analysis catches exactly this class across the whole file. It
    already found the real _Layer regression (make_api's make_layer scroll path)."""
    try:
        from pyflakes.checker import Checker
        from pyflakes.messages import UndefinedName
    except ImportError:  # pragma: no cover - pyflakes is a dev dep; skip if absent
        import pytest
        pytest.skip("pyflakes not installed")
    import ast

    targets = sorted((ROOT / "modules").glob("device_*.py"))
    targets.append(ROOT / "modules" / "moy_runtime.py")
    targets += [Path("runtime") / n for n in (
        "console.py", "project.py", "player.py", "editor_app.py", "wm.py", "perf_hud.py", "update_ui.py", "system_menu_ui.py",
        "achievements_ui.py", "layers.py", "bar_layer.py", "cards_layer.py", "paint_layer.py", "settings_layer.py", "code_layer.py", "widgets.py", "wallpaper.py", "launcher_layer.py",
        "block_editor_ui.py", "map_editor_ui.py", "music_editor_ui.py")]

    bad = []
    for path in targets:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, str(path))
        for m in Checker(tree, str(path)).messages:
            if isinstance(m, UndefinedName):
                bad.append("%s:%d %s" % (path.name, m.lineno,
                                         m.message % m.message_args))
    assert not bad, "undefined names (would NameError at runtime): " + "; ".join(bad)


def test_player_isolation_no_forbidden_names():
    """The Player's bundle (Stage 2 of docs/history/shell_ux_technical_plan_v1.md Section 2):
    it receives the Project + the RAW canvas/input/audio/make_api, and NOTHING else --
    not the cart store, the shell top bar, the home grid, or the layouts. That
    isolation is what makes the run/return cut real: a cart runs identically whether
    launched from the home grid or (Stage 3) the editor, because the Player can't reach
    either. Enforce it structurally -- player.py must never NAME any of those surfaces
    (the bar draw/tap it needs in the pause frame goes through thin ws helpers). A stray
    reach-through would compile+pass every behavior test yet quietly re-couple the run
    loop to the shell, so this source grep is the guard the reviewer + the plan rely on."""
    player = (Path("runtime") / "player.py").read_text(encoding="utf-8")
    for forbidden in ("menu_view", "launcher", "bar_layer", "carts_store"):
        assert forbidden not in player, (
            "player.py names '%s' -- the Player must not reach the store/bar/home grid/"
            "layouts (Stage 2 isolation, plan Section 2)" % forbidden)


def test_one_lua_runtime_wired():
    """#67 -> moycore: ONE Lua runtime, and moy_lua is the VM under it.

    The three tests this replaces pinned the OTHER runtime -- its module
    registration, its trampoline registry, its direct-draw family, its batch
    protocol. All of that is deleted: moycore binds the same vendored VM
    through libmoy's own binding, so what is left to assert is the negative
    (moy_lua exports no module) and that every tier reaches moycore with no
    chooser in front of it.
    """
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
    staged_native = board_config.native_modules(ROOT)
    assert "moy_lua" in staged_native    # the VM is still staged
    assert "moycore" in staged_native

    # No chooser on any tier: one import, one factory, an ImportError floor.
    # The two BOARDS reach it through the shared boot spine since #161 Phase 4
    # -- runtime/device_boot.py holds ONE probe where there used to be two
    # hand-kept copies, which is the whole class of bug that phase exists to
    # close -- so the import lives there. Each board still has to CALL it, and
    # the second loop is what pins that: a spine nobody invokes is a feature
    # that quietly does not exist, exactly like the web console on the T-Deck.
    for src_path in ((_REPO / "runtime" / "device_boot.py"),
                     (Path("firmware/web_runner") / "web_boot.py")):
        src = src_path.read_text(encoding="utf-8")
        assert "from moycore_glue import make_moycore_runtime" in src, src_path
        assert "make_lua_runtime" not in src, "%s still builds the old runtime" % src_path
        assert "except ImportError" in src, src_path
    for src_path in ((ROOT / "modules" / "moy_runtime.py"),
                     (Path("firmware/esp32_p4_wifi6_touch_lcd_7b") / "modules"
                      / "moy_runtime.py")):
        src = src_path.read_text(encoding="utf-8")
        assert "boot.lua_runtime(ws" in src, src_path
        assert "make_lua_runtime" not in src, "%s still builds the old runtime" % src_path
    assert not (ROOT / "modules" / "moy_lua_glue.py").exists()
    api_src = (DEVICE / "device_api.py").read_text(encoding="utf-8")
    assert "moy_lua_glue" not in api_src


def test_moycore_hardware_learned_constraints_pinned():
    """The S3-measured taxes and the safety contracts, now on moycore.

    Each of these cost real hardware time to find, and none of them is
    executable by a host test -- so they are greps, and they moved with the
    code rather than being retired with it.
    """
    mod = (NATIVE / "moycore" / "modmoycore.c").read_text(encoding="utf-8")
    lua_dir = NATIVE / "moy_lua" / "lua"
    # 1) lua_Alloc is internal-SRAM-first with a headroom floor and a PSRAM
    #    fallback (all-PSRAM measured ~2x slower on the S3's 120MHz-OCT bus),
    #    and the floor is a RUNTIME knob -- run_desktop drops it 48 -> 24KB
    #    once the boot-time internal claims are taken. moycore shipped without
    #    the knob while run_desktop lowered the old runtime's, which left a
    #    moycore cart at 48KB: ~97% PSRAM on this board, i.e. the 2x regime.
    assert "MALLOC_CAP_INTERNAL" in mod
    assert "48 * 1024" in mod                          # the WiFi/DMA headroom floor
    assert mod.index("MALLOC_CAP_INTERNAL") < mod.index("MALLOC_CAP_SPIRAM")
    assert "MP_QSTR_set_sram_floor" in mod
    runtime_src = (ROOT / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
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
    #    HW FPU and halve TValue. Host lupa stays on doubles -- golden-frame
    #    parity is host-only for float-heavy carts.
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


def test_there_is_one_new_layer_factory_and_it_pins_retained_frames():
    """(#113) A layer/window buffer is ONE persistent surface, so a blit-
    scrolling caller must measure against the LAST paint -- RETAINED_FRAMES = 1.
    The class default is 2 because it describes the ROOT ping-pong (the P4's
    DPI double buffer), so the new_layer factory has to override it.

    "The factory", singular: P4SystemCanvas.new_layer used to COPY
    DeviceCanvas.new_layer's body (to construct its own class), and the
    override was lost in that copy -- once the paint ring armed on the desktop
    tier, a picker drag shifted by ~twice the real delta and ghosted a
    duplicate of every card (owner-reported on glass, 2026-07-25). The copy
    lost the cart-palette rider the same way. The fix is structural now: the
    subclasses supply only the `_make_layer` construction hook, so this pins
    (a) the one body still sets RETAINED_FRAMES = 1 and (b) no tier has grown
    a copy of it back."""
    from pathlib import Path

    src = Path("device/device_canvas.py").read_text(encoding="utf-8")
    i = src.find("def new_layer(")
    assert i > 0, "no new_layer factory in device_canvas.py"
    body = src[i:i + 2400]
    assert "RETAINED_FRAMES = 1" in body, \
        "new_layer must pin RETAINED_FRAMES = 1 (#113)"
    for mod in (Path("firmware/esp32_p4_wifi6_touch_lcd_7b/modules/moy_runtime.py"),
                Path("firmware/web_runner/web_canvas.py"),
                Path("runtime/host_canvas.py")):
        assert "def new_layer(" not in mod.read_text(encoding="utf-8"), \
            mod.name + ": grew a new_layer copy back; use the _make_layer hook"


def test_lua_table_verb_never_clobbers_the_table_library():
    """#164: the #78 `table()` cart verb must ride Lua's `table` LIBRARY as a
    metatable __call, never replace it -- a ported cart's p8 shim needs
    table.remove (the shim generator lives in moy-spec now)."""
    # The graft lives in the shared prelude (runtime/lua_ext.py), so every
    # runtime that imports it inherits the #164 fix instead of carrying its own
    # copy of the line.
    ext = (ROOT.parent.parent / "runtime" / "lua_ext.py").read_text(
        encoding="utf-8")
    assert "setmetatable(table, { __call" in ext
    host = (ROOT.parent.parent / "runtime" / "lua_host.py").read_text(
        encoding="utf-8")
    # The host has ONE lane now (lupa went on 2026-08-14), and it REGISTERS the
    # verb rather than assigning it into the globals -- the assignment lane this
    # used to also pin was lupa's, which could hand Lua a Python object directly.
    assert 'reg("moy_table_verb", tv)' in host
    assert 'g["moy_table_verb"] = v' not in host
    glue = (DEVICE / "moycore_glue.py").read_text(encoding="utf-8")
    assert '_moycore.register("moy_table_verb", tv)' in glue
    # ...and the register loop must SKIP the bare name, or it sets the global
    # `table` and clobbers the library before the prelude can graft anything.
    # NOT_REGISTRABLE moved into the shared lua_ext with the prelude it pairs
    # with (it was twinned between the glue and lua_host, unchecked); the glue
    # is pinned on IMPORTING it, so a board that stopped sharing the list would
    # still be caught here.
    assert "NOT_REGISTRABLE" in glue
    assert '"table",' in ext[ext.index("NOT_REGISTRABLE = frozenset(("):]


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
    # The drain is ONE helper since 2026-08-18 (device_boot.poll_webhost);
    # each board's frame tail must still CALL it -- the failure this pins was
    # exactly a tail that stopped calling. That the helper actually polls, only
    # while the host is SERVING, and never breaks the frame when the transfer
    # dies, is executed in test_device_boot.py (#208 rank 5).
    for rel in ("firmware/lilygo_t_deck_plus_mainline/modules/moy_runtime.py",
                "firmware/esp32_p4_wifi6_touch_lcd_7b/modules/moy_runtime.py"):
        src = (_REPO / rel).read_text(encoding="utf-8")
        assert "poll_webhost(ws)" in src, (
            "%s never polls ws.webhost -- a bound listener with no accept() "
            "times out instead of refusing, which reads as a dead server" % rel)
