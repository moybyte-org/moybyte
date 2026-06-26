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
    assert "0x10000 $(MPY_APP_BIN)" in makefile
    assert "0x0 $(MPY_FULL_BIN)" in makefile
    assert "tools/esptool_no_modem.py" in makefile
    assert "--before default_reset --after hard_reset" in makefile
    assert "--before no_reset --after no_reset" in makefile
    assert "--no-stub run" in makefile
    assert "serial.serial_for_url = _patched_serial_for_url" in wrapper
    assert "rtscts" in wrapper
    assert "False" in wrapper
    assert "ResetStrategy._setDTRandRTS" in wrapper


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
    assert "ws._with_sd = kidcode_sd.with_sd_live" in runtime
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
    assert "def _cache_rgb(self, img, scale):" in runtime
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
    # The device cart API exposes map/mget/mset, same names as the host make_api.
    assert '"map": map_, "mget": mget, "mset": mset,' in runtime


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

        def spr(self, img, x, y, scale=1):
            calls.append((img.w, img.h, x, y, scale))

        def __getattr__(self, name):
            return lambda *a, **k: 0

    class StubInput:
        def held(self, name):
            return False

        def pressed(self, name):
            return False

    api = m.make_api(StubCanvas(), StubInput(), {}, sheet)
    api["spr"](3, 100, 60)                  # TIC-80 indexed sprite from the sheet
    assert calls[-1] == (8, 8, 100, 60, 1)
    api["spr"](m.Image.from_ascii(["#"], {"#": 7}), 8, 9, scale=4)  # Image still works
    assert calls[-1] == (1, 1, 8, 9, 4)
    # Multi-tile span (#30): spr(n, x, y, w=2, h=2) blits a 16x16 image from the
    # sheet (the device path, so host == device for larger sprites).
    api["spr"](0, 12, 14, w=2, h=2)
    assert calls[-1] == (16, 16, 12, 14, 1)


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

        def spr(self, img, x, y, scale=1):
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
    assert "class AudioBank:" in audio
    # The console builds a per-cart AudioEngine and injects an audio backend.
    assert "from audio import AudioBank, AudioEngine" in console
    assert "def _build_audio(self):" in console
    assert "self.audio.tick(dt)" in console
    # The device make_api binds the same six audio names as the host.
    for name in ('"sfx": _sfx', '"beep": _beep', '"music": _music',
                 '"music_stop": _music_stop', '"sound_stop": _sound_stop',
                 '"volume": _volume'):
        assert name in runtime, name
    # The device I2S backend is wired in (stub -- NEEDS ON-DEVICE VERIFICATION).
    assert "class DeviceAudio:" in runtime
    assert "from machine import I2S, Pin" in runtime
    assert "mode=I2S.TX" in runtime
    assert "ws.make_audio = make_audio" in runtime
    assert "NEEDS ON-DEVICE VERIFICATION" in runtime
    # sounds.json storage in the shared cart store.
    assert "def save_sounds(cart, bank_dict):" in carts
    assert '"sounds": sounds' in carts
    # build.sh stages the shared audio module into the frozen modules tree.
    assert 'cp "${REPO_ROOT}/runtime/audio.py" "${SCRIPT_DIR}/modules/audio.py"' in build


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
    # The device WLAN backend (STUB -- needs hardware verification) + autoconnect.
    assert "class DeviceWifi:" in runtime
    assert "network.WLAN(network.STA_IF)" in runtime
    assert "def make_wifi(store=None, root=None):" in runtime
    assert "def autoconnect_wifi(wifi):" in runtime
    assert "NEEDS ON-DEVICE VERIFICATION" in runtime
    # run_desktop injects the system service + autoconnects from saved creds.
    assert "ws.wifi = make_wifi(kid_carts, carts_root)" in runtime
    assert "autoconnect_wifi(ws.wifi)" in runtime
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
