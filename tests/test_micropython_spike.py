import importlib.util
from pathlib import Path


ROOT = Path("firmware/lilygo_t_deck_plus_micropython")


def test_micropython_spike_scaffold_exists():
    assert (ROOT / "README.md").exists()
    assert (ROOT / "build.sh").exists()
    assert (ROOT / "modules" / "main.py").exists()
    assert (ROOT / "modules" / "kidcode" / "__init__.py").exists()
    assert (ROOT / "modules" / "projects" / "tiny_runner.py").exists()


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


def test_micropython_spike_has_host_simulator():
    makefile = Path("Makefile").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    simulator = Path("tools/simulate_micropython_spike.py").read_text(encoding="utf-8")

    assert "firmware-sim-lilygo-micropython:" in makefile
    assert "tools/simulate_micropython_spike.py" in makefile
    assert "Host Simulator" in readme
    assert "fake-lvgl" in readme
    assert "--source path/to/project.py" in readme
    assert "not an ESP32 display-driver simulator" in readme
    assert "class ScriptedKeyboard" in simulator
    assert "class RecordingRenderer" in simulator
    assert "class FakeLVGL" in simulator
    assert "class FakeLVGLRenderer" in simulator
    assert '"stop": "stop"' in simulator
    assert 'parser.add_argument("--source"' in simulator


def test_micropython_host_simulator_moves_tiny_runner_player():
    spec = importlib.util.spec_from_file_location(
        "kidcode_micropython_sim", Path("tools/simulate_micropython_spike.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.run_simulation(
        frames=10,
        dt=1 / 30,
        script=module.parse_script("right:10"),
    )

    player = result["sprites"]["player"]
    assert result["rendered_frames"] == 10
    assert player["x"] > 60
    assert player["y"] == 60
    assert any(command["type"] == "text" and "score" in command["value"] for command in result["last_commands"])


def test_micropython_host_simulator_can_run_console_renderer_with_fake_lvgl():
    spec = importlib.util.spec_from_file_location(
        "kidcode_micropython_sim", Path("tools/simulate_micropython_spike.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.run_simulation(
        frames=5,
        dt=1 / 30,
        script=module.parse_script("right:5"),
        renderer_mode="fake-lvgl",
    )

    snapshot = result["renderer_snapshot"]
    assert result["renderer_mode"] == "fake-lvgl"
    assert "player" in snapshot["objects"]
    assert "coin" in snapshot["objects"]
    assert snapshot["objects"]["player"]["pos"][0] > snapshot["objects"]["coin"]["pos"][0]
    assert any("score" in value for value in snapshot["text"])


def test_micropython_runner_stop_and_run_reload_in_simulator():
    spec = importlib.util.spec_from_file_location(
        "kidcode_micropython_sim", Path("tools/simulate_micropython_spike.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    stopped = module.run_simulation(
        frames=3,
        dt=1 / 30,
        script=module.parse_script("home:3"),
        renderer_mode="fake-lvgl",
    )
    assert "stopped" in stopped["status"].lower()
    assert any("stopped" in value for value in stopped["renderer_snapshot"]["text"])

    reloaded = module.run_simulation(
        frames=8,
        dt=1 / 30,
        script=module.parse_script("home:1,run:1,right:6"),
        renderer_mode="fake-lvgl",
    )
    assert reloaded["sprites"]["player"]["x"] > 60
    assert "player" in reloaded["renderer_snapshot"]["objects"]


def test_fake_lvgl_renderer_reuses_text_objects_and_handles_lines():
    spec = importlib.util.spec_from_file_location(
        "kidcode_micropython_sim", Path("tools/simulate_micropython_spike.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    state = module.begin_firmware_imports()
    try:
        renderer = module.FakeLVGLRenderer()
        renderer.render(
            (
                {"type": "clear", "color": 0},
                {"type": "text", "value": "one", "x": 1, "y": 2, "color": 1},
                {"type": "line", "x1": 0, "y1": 0, "x2": 8, "y2": 0, "color": 3},
            )
        )
        child_count = renderer.frames[-1]["child_count"]
        renderer.render(
            (
                {"type": "clear", "color": 0},
                {"type": "text", "value": "two", "x": 1, "y": 2, "color": 1},
                {"type": "line", "x1": 0, "y1": 0, "x2": 8, "y2": 0, "color": 3},
            )
        )
        snapshot = renderer.frames[-1]
    finally:
        module.restore_imports(state)

    assert snapshot["child_count"] == child_count
    assert snapshot["text"] == ["two"]
    assert snapshot["objects"]["line_0"]["size"] == (9, 1)


def test_micropython_host_simulator_loads_external_source_file(tmp_path):
    source = tmp_path / "project.py"
    source.write_text(
        "\n".join(
            (
                "from kidcode import *",
                "p = sprite('player', x=10, y=20)",
                "def update(dt):",
                "    if button('right'):",
                "        p.x += 3",
                "def draw():",
                "    clear(0)",
                "    draw_sprite(p)",
                "    text('external', 1, 1, 1)",
            )
        ),
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location(
        "kidcode_micropython_sim", Path("tools/simulate_micropython_spike.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.run_simulation(
        frames=4,
        dt=1 / 30,
        script=module.parse_script("right:4"),
        source_path=source,
        renderer_mode="fake-lvgl",
    )

    assert result["source_path"] == str(source)
    assert result["sprites"]["player"]["x"] == 22
    assert any("external" in value for value in result["renderer_snapshot"]["text"])


def test_micropython_host_simulator_runs_frozen_game_slots():
    spec = importlib.util.spec_from_file_location(
        "kidcode_micropython_sim", Path("tools/simulate_micropython_spike.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    input_test = module.run_simulation(
        frames=3,
        dt=1 / 30,
        script=module.parse_script("a:3"),
        project="projects.input_test",
        renderer_mode="fake-lvgl",
    )
    assert any("input test" in value for value in input_test["renderer_snapshot"]["text"])

    bounce = module.run_simulation(
        frames=5,
        dt=1 / 30,
        script=module.parse_script("right:5"),
        project="projects.bounce_box",
        renderer_mode="fake-lvgl",
    )
    assert "robot" in bounce["sprites"]
    assert any("bounce" in value for value in bounce["renderer_snapshot"]["text"])


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


def test_micropython_spike_has_controlled_project_loader():
    loader = (ROOT / "modules" / "kidcode_project_loader.py").read_text(encoding="utf-8")
    shell = (ROOT / "modules" / "kidcode_shell.py").read_text(encoding="utf-8")
    kidcode_init = (ROOT / "modules" / "kidcode" / "__init__.py").read_text(encoding="utf-8")
    tiny_runner = (ROOT / "modules" / "projects" / "tiny_runner.py").read_text(encoding="utf-8")

    assert "def __init__(self, input_state, keyboard, renderer):" in loader
    assert "self.input = input_state" in loader
    assert "fps=%d k=%02x r=%d m=%02x" in loader
    assert "def _held_mask(self):" in loader
    assert "def try_load_source(self, project_name, source):" in loader
    assert "def try_load_file(self, paths):" in loader
    assert "def run_restart_cycles(self, module_name, count):" in loader
    assert "def _make_source_env(project_name):" in loader
    assert "def _normalize_source(source):" in loader
    assert "load_frozen_project" in loader
    assert "exec(source, env)" in loader
    assert "KidCode source exec starting" in loader
    assert 'line.strip() == "from kidcode import *"' in loader
    assert 'env[name] = getattr(kidcode, name)' in loader
    assert '"__builtins__": __builtins__' not in loader
    assert "PROJECT_FILE_PATHS" in shell
    assert "ENABLE_BOOT_SELF_TESTS = False" in shell
    assert "ENABLE_EXTERNAL_PROJECT_FILES = False" in shell
    assert "ENABLE_SD_PREFETCH = True" in shell
    assert "GAME_SLOTS" in shell
    assert "projects.input_test" in shell
    assert "projects.bounce_box" in shell
    assert "SD Project" in shell
    assert "def _prefetch_sd_project():" in shell
    assert "def _load_sd_project" in shell
    assert "cached SD project" in shell
    assert "/sd/kidcode/project.py" in shell
    assert "def reset_api():" in kidcode_init
    assert "game = _runtime.game" in kidcode_init
    assert "from kidcode import *" in tiny_runner
    assert "def setup():" in tiny_runner


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


def test_micropython_spike_enables_watchdog_after_display_bringup():
    shell = (ROOT / "modules" / "kidcode_shell.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "ENABLE_WATCHDOG = True" in shell
    assert "wdt = _make_watchdog() if ENABLE_WATCHDOG else None" in shell
    assert "Watchdog reset recovery is enabled" in readme


def test_micropython_spike_renderer_uses_title_status_and_screen_primitives():
    renderer = (ROOT / "modules" / "kidcode_lvgl_renderer.py").read_text(encoding="utf-8")
    shell = (ROOT / "modules" / "kidcode_shell.py").read_text(encoding="utf-8")

    assert "self.canvas_x" in renderer
    assert "self.canvas.set_pos(self.canvas_x, self.canvas_y)" in renderer
    assert "def render_message(self, status, lines):" in renderer
    assert "self.title.set_text(\"KidCode \" + str(value)[:28])" in renderer
    assert "renderer.set_status(\"renderer ok\")" in shell
    assert "obj = self.lv.obj(self.screen)" in renderer
    assert "label = self.lv.label(self.screen)" in renderer


def test_micropython_spike_documents_tdeck_reference_paths():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    notes = (ROOT / "SPIKE_RESULTS.md").read_text(encoding="utf-8")

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
