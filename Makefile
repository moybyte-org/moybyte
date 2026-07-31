VENV ?= .venv
SYSTEM_PYTHON ?= python3
PYTHON ?= $(VENV)/bin/python
MONITOR_SECONDS ?= 12
LOG ?= /tmp/moybyte_lilygo_serial.log
IDF_PYTHON ?= $(HOME)/.espressif/python_env/idf5.5_py3.10_env/bin/python
MPY_FW_DIR ?= firmware/lilygo_t_deck_plus_micropython
MPY_BUILD_DIR ?= $(MPY_FW_DIR)/.build/lvgl_micropython/lib/micropython/ports/esp32/build-ESP32_GENERIC_S3-SPIRAM_OCT
MPY_APP_BIN ?= $(MPY_FW_DIR)/dist/current/moybyte-current-app.bin
MPY_FULL_BIN ?= $(MPY_FW_DIR)/dist/current/moybyte-current-full-dio-0x0.bin
# OTA (#53): the bootable app partition is ota_0. With the dual-OTA table it sits at
# 0x20000 (otadata shifted it up from the legacy 0x10000). The app-only cable flash
# below writes here AND clears otadata (MPY_OTADATA_OFFSET) so the bootloader boots the
# slot we just wrote -- not a stale ota_1 left by a prior SD/OTA update. Override for a
# non-OTA single-factory build: make ... MPY_APP_OFFSET=0x10000 MPY_OTADATA_OFFSET=
MPY_APP_OFFSET ?= 0x20000
# OTA boot selector (otadata) region, erased by the cable flash so the bootloader falls
# back to ota_0 (no factory partition). Empty -> skip the erase (non-OTA factory build).
MPY_OTADATA_OFFSET ?= 0x1d000
MPY_OTADATA_SIZE ?= 0x2000

MPY_FLASH_MODE ?= dio

# OTA online update (#53 Phase 3): host a manifest + .bin for Settings -> UPDATE ONLINE.
OTA_PORT ?= 8000
# Two-channel publish (#53): stage artifacts under OTA_ROOT/<channel>/ and serve that
# dir (the systemd host, tools/moybyte-ota.service) so the device pulls stable or beta.
OTA_ROOT ?= $(HOME)/.moybyte-ota

.PHONY: setup test site-gifs firmware-build-lilygo-micropython firmware-flash-lilygo-micropython firmware-flash-lilygo-micropython-no-reset firmware-flash-lilygo-micropython-full firmware-flash-lilygo-micropython-full-erase firmware-run-lilygo-micropython firmware-monitor-lilygo-micropython firmware-build-p4 firmware-flash-p4 firmware-monitor-p4 firmware-stage-xiao-zero ota-manifest ota-serve ota-publish-unstable ota-publish-stable ota-host ota-serve-install sync-issues check-venv

# A PLAIN venv on purpose. Two flags used to live here and both hid bugs on every
# machine but the maintainer's:
#   --system-site-packages   leaked the host's pygame/wheel in, so missing deps only
#                            showed up on a clean machine.
#   --no-build-isolation     forbade pip from fetching a build backend, so the stock
#                            venv setuptools (59.6 on 3.10, absent on 3.12+) failed
#                            with "invalid command 'bdist_wheel'".
# `sim` is installed too: tools/simulate_desktop.py (the first thing a new dev runs)
# needs pygame.
setup:
	$(SYSTEM_PYTHON) -m venv $(VENV)
# A venv seeded by an older distro python can carry setuptools < 64, which has
# no PEP 660 editable hook -- and there is no setup.py to fall back to since the
# .moyproj SDK went. Upgrade first, then install.
	$(PYTHON) -m pip install -q --upgrade pip setuptools
	$(PYTHON) -m pip install -e '.[dev,sim]'

# Without this, every venv-backed target below dies with a bare
# "/bin/sh: .venv/bin/python: No such file or directory".
check-venv:
	@test -x $(PYTHON) || { echo "no venv at $(VENV)/ -- run: make setup"; exit 1; }

VENV_TARGETS := test \
                site-gifs sync-issues \
                ota-manifest ota-serve ota-publish-unstable \
                ota-publish-stable ota-host ota-serve-install firmware-flash-p4 \
                firmware-monitor-p4
$(VENV_TARGETS): check-venv

# Flashing/monitoring needs a board on a serial port, and the T-Deck images need the
# ESP-IDF 5.5 toolchain. Both are legitimately environment-dependent -- say which
# piece is missing instead of failing with a bare `test` or "No such file".
REQUIRE_PORT = @test -n "$(PORT)" || { echo "PORT is not set -- e.g. make $@ PORT=/dev/ttyACM0 (try: make device-port)"; exit 1; }
REQUIRE_IDF = @test -x $(IDF_PYTHON) || { echo "no ESP-IDF python at $(IDF_PYTHON) -- install the ESP-IDF 5.5 toolchain (see $(MPY_FW_DIR)/README.md) or pass IDF_PYTHON=..."; exit 1; }
# esptool/pyserial are the `device` extra: `make setup` does not install them
# because flashing needs hardware. The P4 targets run them from the project venv.
REQUIRE_ESPTOOL = @$(PYTHON) -c "import esptool" >/dev/null 2>&1 || { echo "esptool is not installed -- run: $(PYTHON) -m pip install -e '.[device]'"; exit 1; }
REQUIRE_PYSERIAL = @$(PYTHON) -c "import serial" >/dev/null 2>&1 || { echo "pyserial is not installed -- run: $(PYTHON) -m pip install -e '.[device]'"; exit 1; }

test:
	$(PYTHON) -m pytest

# Regenerate the teaser-site demo GIFs from the real console (headless).
site-gifs:
	$(PYTHON) tools/make_site_gifs.py

# Mirror GitHub issues into docs/issues/ (open/ + closed/ + INDEX.md) so issue
# numbers referenced in commits/docs/chat resolve locally. Needs the `gh` CLI, authed.
sync-issues:
	$(PYTHON) tools/sync_issues.py

firmware-build-lilygo-micropython:
	bash firmware/lilygo_t_deck_plus_micropython/build.sh

# OTA (#53 Phase 3): emit dist/latest.json from the built image (auto size + sha256 +
# version read from moy_ota.FIRMWARE_VERSION). Point it at your host with OTA_BASE_URL;
# with none it uses http://<LAN-IP>:$(OTA_PORT) for a local test against `make ota-serve`.
#   make ota-manifest                         # local test
#   make ota-manifest OTA_BASE_URL=https://you.example.com/moybyte
ota-manifest:
	$(PYTHON) tools/gen_ota_manifest.py $(if $(OTA_BASE_URL),--base-url $(OTA_BASE_URL)) --port $(OTA_PORT)

# Serve dist/ (the .bin + latest.json) over plain HTTP for the device to pull from.
ota-serve:
	cd $(MPY_FW_DIR)/dist && $(PYTHON) -m http.server $(OTA_PORT)

# Publish the CURRENT working tree (uncommitted OK) as a BETA build the device can pull
# over WiFi: build with the unstable channel stamp, then copy the image + a matching
# manifest into OTA_ROOT/unstable/. No commit, no PC needed by the tester -- on the
# device: Settings -> CHANNEL = BETA -> UPDATE ONLINE. The beta version is the build
# epoch, so every publish reads as newer than the last.
ota-publish-unstable:
	MOYBYTE_SKIP_VFS_BOOT=1 MOYBYTE_OTA_CHANNEL=unstable $(MAKE) firmware-build-lilygo-micropython
	$(PYTHON) tools/gen_ota_manifest.py --root $(OTA_ROOT) $(if $(OTA_BASE_URL),--base-url $(OTA_BASE_URL)) --port $(OTA_PORT)

# Publish a STABLE build (normally from master) into OTA_ROOT/stable/.
ota-publish-stable:
	MOYBYTE_SKIP_VFS_BOOT=1 MOYBYTE_OTA_CHANNEL=stable $(MAKE) firmware-build-lilygo-micropython
	$(PYTHON) tools/gen_ota_manifest.py --root $(OTA_ROOT) $(if $(OTA_BASE_URL),--base-url $(OTA_BASE_URL)) --port $(OTA_PORT)

# Serve OTA_ROOT (both channels) over HTTP for the device. Foreground; for a persistent
# host use the systemd --user service below.
ota-host:
	mkdir -p $(OTA_ROOT)
	$(PYTHON) -m http.server $(OTA_PORT) --directory $(OTA_ROOT)

# Install + start the OTA host as a systemd --user service so it stays up while the PC
# is on (linger keeps it across logout). Run this yourself once; re-run after changing
# OTA_ROOT/OTA_PORT. (loginctl enable-linger may prompt for sudo.)
ota-serve-install:
	mkdir -p $(OTA_ROOT) $(HOME)/.config/systemd/user
	sed -e 's#@PYTHON@#$(abspath $(PYTHON))#g' -e 's#@ROOT@#$(OTA_ROOT)#g' -e 's#@PORT@#$(OTA_PORT)#g' \
	    tools/moybyte-ota.service > $(HOME)/.config/systemd/user/moybyte-ota.service
	systemctl --user daemon-reload
	systemctl --user enable --now moybyte-ota.service
	loginctl enable-linger $(USER) || true
	@echo "OTA host: serving $(OTA_ROOT) on :$(OTA_PORT) (systemd --user moybyte-ota)"

firmware-flash-lilygo-micropython:
	$(REQUIRE_PORT)
	$(REQUIRE_IDF)
	@[ -z "$(MPY_OTADATA_OFFSET)" ] || $(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before default_reset --after no_reset erase_region $(MPY_OTADATA_OFFSET) $(MPY_OTADATA_SIZE)
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before default_reset --after hard_reset write_flash --flash_mode dio --flash_size 16MB --flash_freq 80m 0x0 $(MPY_BUILD_DIR)/bootloader/bootloader.bin 0x8000 $(MPY_BUILD_DIR)/partition_table/partition-table.bin $(MPY_APP_OFFSET) $(MPY_APP_BIN)

firmware-flash-lilygo-micropython-no-reset:
	$(REQUIRE_PORT)
	$(REQUIRE_IDF)
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before no_reset --after no_reset write_flash --flash_mode dio --flash_size 16MB --flash_freq 80m 0x0 $(MPY_BUILD_DIR)/bootloader/bootloader.bin 0x8000 $(MPY_BUILD_DIR)/partition_table/partition-table.bin $(MPY_APP_OFFSET) $(MPY_APP_BIN)

firmware-flash-lilygo-micropython-full:
	$(REQUIRE_PORT)
	$(REQUIRE_IDF)
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before default_reset --after hard_reset write_flash 0x0 $(MPY_FULL_BIN)

firmware-flash-lilygo-micropython-full-erase:
	$(REQUIRE_PORT)
	$(REQUIRE_IDF)
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before no_reset --after hard_reset write_flash --flash_mode $(MPY_FLASH_MODE) --flash_size 16MB --flash_freq 80m --erase-all 0x0 $(MPY_FULL_BIN)

firmware-run-lilygo-micropython:
	$(REQUIRE_PORT)
	$(REQUIRE_IDF)
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) --before no_reset --after hard_reset --no-stub run

firmware-monitor-lilygo-micropython:
	$(REQUIRE_PORT)
	$(REQUIRE_IDF)
	$(IDF_PYTHON) -m serial.tools.miniterm $(PORT) 115200

# ESP32-P4 (Waveshare 7B, #58): mainline-MicroPython build via the board dir's
# build.sh -> dist/p4/moybyte_p4.bin, flashed at 0x2000 (the P4's app offset).
# Serial = CH343 (no native-takeover starvation, REPL stays alive), so plain
# esptool auto-reset works; esptool comes from the project venv.
P4_BIN ?= dist/p4/moybyte_p4.bin

firmware-build-p4:
	firmware/esp32_p4_wifi6_touch_lcd_7b/build.sh

firmware-flash-p4:
	$(REQUIRE_PORT)
	$(REQUIRE_ESPTOOL)
	$(PYTHON) -m esptool --chip esp32p4 --port $(PORT) --baud 921600 write_flash 0x2000 $(P4_BIN)

firmware-monitor-p4:
	$(REQUIRE_PORT)
	$(REQUIRE_PYSERIAL)
	$(PYTHON) -m serial.tools.miniterm $(PORT) 115200

# MoyByte Zero (Seeed XIAO ESP32-S3): pure-Python, no native build. One-time flash of stock
# MicroPython is documented in firmware/seeed_xiao_esp32s3_zero/README.md; this stages the
# shared console modules + the Zero backend over mpremote (PORT defaults to the first ttyACM*).
firmware-stage-xiao-zero:
	bash firmware/seeed_xiao_esp32s3_zero/stage.sh $(PORT)

