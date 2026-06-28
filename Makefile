VENV ?= .venv
SYSTEM_PYTHON ?= python3
PYTHON ?= $(VENV)/bin/python
KIDCODE ?= $(VENV)/bin/kidcode
MONITOR_SECONDS ?= 12
LOG ?= /tmp/kidcode_lilygo_serial.log
IDF_PYTHON ?= $(HOME)/.espressif/python_env/idf5.5_py3.10_env/bin/python
MPY_FW_DIR ?= firmware/lilygo_t_deck_plus_micropython
MPY_BUILD_DIR ?= $(MPY_FW_DIR)/.build/lvgl_micropython/lib/micropython/ports/esp32/build-ESP32_GENERIC_S3-SPIRAM_OCT
MPY_APP_BIN ?= $(MPY_FW_DIR)/dist/current/kidcode-current-app.bin
MPY_FULL_BIN ?= $(MPY_FW_DIR)/dist/current/kidcode-current-full-dio-0x0.bin
# OTA (#53): the bootable app partition is ota_0. With the dual-OTA table it sits at
# 0x20000 (otadata shifted it up from the legacy 0x10000). The app-only flash targets
# below write here; the FIRST flash of an OTA build must use -full-erase (rewrites the
# partition table + clears otadata so the bootloader boots ota_0). Override for a
# non-OTA single-factory build: make ... MPY_APP_OFFSET=0x10000
MPY_APP_OFFSET ?= 0x20000

MPY_FLASH_MODE ?= dio

# OTA online update (#53 Phase 3): host a manifest + .bin for Settings -> UPDATE ONLINE.
OTA_PORT ?= 8000

.PHONY: setup test run-example run-headless compile-blocks doctor check-portable pack-example export-lilygo-example device-doctor device-port firmware-bundle-lilygo firmware-build-lilygo firmware-build-lilygo-micropython firmware-sim-lilygo-micropython firmware-flash-lilygo-micropython firmware-flash-lilygo-micropython-no-reset firmware-flash-lilygo-micropython-full firmware-flash-lilygo-micropython-full-erase firmware-run-lilygo-micropython firmware-monitor-lilygo-micropython firmware-upload-lilygo firmware-monitor-lilygo firmware-smoke-check-lilygo firmware-smoke-lilygo ota-manifest ota-serve

setup:
	$(SYSTEM_PYTHON) -m venv --system-site-packages $(VENV)
	$(PYTHON) -m pip install --no-build-isolation -e '.[dev]'

test:
	$(PYTHON) -m pytest

doctor:
	$(KIDCODE) doctor

device-doctor:
	$(KIDCODE) device-doctor --board lilygo_t_deck_plus

device-port:
	$(KIDCODE) device-port

run-example:
	$(KIDCODE) run examples/tiny_runner.kcproj

run-headless:
	$(KIDCODE) run examples/tiny_runner.kcproj --headless --frames 60

compile-blocks:
	$(KIDCODE) compile examples/blocks_demo.kcproj

check-portable:
	$(KIDCODE) check-portable examples/tiny_runner.kcproj examples/blocks_demo.kcproj examples/music_player_stub.kcproj examples/radio_pong_stub.kcproj

pack-example:
	$(KIDCODE) pack examples/tiny_runner.kcproj --out /tmp/tiny_runner.kc8

firmware-build-lilygo-micropython:
	bash firmware/lilygo_t_deck_plus_micropython/build.sh

# OTA (#53 Phase 3): emit dist/latest.json from the built image (auto size + sha256 +
# version read from kc_ota.FIRMWARE_VERSION). Point it at your host with OTA_BASE_URL;
# with none it uses http://<LAN-IP>:$(OTA_PORT) for a local test against `make ota-serve`.
#   make ota-manifest                         # local test
#   make ota-manifest OTA_BASE_URL=https://you.example.com/kidcode
ota-manifest:
	$(PYTHON) tools/gen_ota_manifest.py $(if $(OTA_BASE_URL),--base-url $(OTA_BASE_URL)) --port $(OTA_PORT)

# Serve dist/ (the .bin + latest.json) over plain HTTP for the device to pull from.
ota-serve:
	cd $(MPY_FW_DIR)/dist && $(PYTHON) -m http.server $(OTA_PORT)

firmware-flash-lilygo-micropython:
	test -n "$(PORT)"
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before default_reset --after hard_reset write_flash --flash_mode dio --flash_size 16MB --flash_freq 80m 0x0 $(MPY_BUILD_DIR)/bootloader/bootloader.bin 0x8000 $(MPY_BUILD_DIR)/partition_table/partition-table.bin $(MPY_APP_OFFSET) $(MPY_APP_BIN)

firmware-flash-lilygo-micropython-no-reset:
	test -n "$(PORT)"
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before no_reset --after no_reset write_flash --flash_mode dio --flash_size 16MB --flash_freq 80m 0x0 $(MPY_BUILD_DIR)/bootloader/bootloader.bin 0x8000 $(MPY_BUILD_DIR)/partition_table/partition-table.bin $(MPY_APP_OFFSET) $(MPY_APP_BIN)

firmware-flash-lilygo-micropython-full:
	test -n "$(PORT)"
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before no_reset --after hard_reset write_flash 0x0 $(MPY_FULL_BIN)

firmware-flash-lilygo-micropython-full-erase:
	test -n "$(PORT)"
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before no_reset --after hard_reset write_flash --flash_mode $(MPY_FLASH_MODE) --flash_size 16MB --flash_freq 80m --erase-all 0x0 $(MPY_FULL_BIN)

firmware-run-lilygo-micropython:
	test -n "$(PORT)"
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) --before no_reset --after hard_reset --no-stub run

firmware-monitor-lilygo-micropython:
	test -n "$(PORT)"
	$(IDF_PYTHON) -m serial.tools.miniterm $(PORT) 115200

