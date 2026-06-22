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

MPY_FLASH_MODE ?= dio

.PHONY: setup test run-example run-headless compile-blocks doctor check-portable pack-example export-lilygo-example device-doctor device-port firmware-bundle-lilygo firmware-build-lilygo firmware-build-lilygo-micropython firmware-sim-lilygo-micropython firmware-flash-lilygo-micropython firmware-flash-lilygo-micropython-no-reset firmware-flash-lilygo-micropython-full firmware-flash-lilygo-micropython-full-erase firmware-run-lilygo-micropython firmware-monitor-lilygo-micropython firmware-upload-lilygo firmware-monitor-lilygo firmware-smoke-check-lilygo firmware-smoke-lilygo

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

export-lilygo-example:
	$(KIDCODE) export-device examples/tiny_runner.kcproj --board lilygo_t_deck_plus --out /tmp/kidcode_lilygo_t_deck_plus

firmware-bundle-lilygo:
	$(KIDCODE) firmware-header examples/tiny_runner.kcproj --board lilygo_t_deck_plus --out firmware/lilygo_t_deck_plus/include/kidcode_project_bundle.h

firmware-build-lilygo: firmware-bundle-lilygo
	pio run -d firmware/lilygo_t_deck_plus

firmware-build-lilygo-micropython:
	bash firmware/lilygo_t_deck_plus_micropython/build.sh

firmware-sim-lilygo-micropython:
	$(SYSTEM_PYTHON) tools/simulate_micropython_spike.py --renderer fake-lvgl --frames 120 --ascii

firmware-flash-lilygo-micropython:
	test -n "$(PORT)"
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before default_reset --after hard_reset write_flash --flash_mode dio --flash_size 16MB --flash_freq 80m 0x0 $(MPY_BUILD_DIR)/bootloader/bootloader.bin 0x8000 $(MPY_BUILD_DIR)/partition_table/partition-table.bin 0x10000 $(MPY_APP_BIN)

firmware-flash-lilygo-micropython-no-reset:
	test -n "$(PORT)"
	$(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before no_reset --after no_reset write_flash --flash_mode dio --flash_size 16MB --flash_freq 80m 0x0 $(MPY_BUILD_DIR)/bootloader/bootloader.bin 0x8000 $(MPY_BUILD_DIR)/partition_table/partition-table.bin 0x10000 $(MPY_APP_BIN)

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

firmware-upload-lilygo: firmware-bundle-lilygo
	test -n "$(PORT)"
	pio run -d firmware/lilygo_t_deck_plus -t upload --upload-port $(PORT)

firmware-monitor-lilygo:
	test -n "$(PORT)"
	pio device monitor -d firmware/lilygo_t_deck_plus -b 115200 --port $(PORT)

firmware-smoke-check-lilygo:
	test -n "$(LOG)"
	$(KIDCODE) firmware-smoke-check $(LOG) --board lilygo_t_deck_plus --project-id tiny_runner

firmware-smoke-lilygo:
	PORT_VALUE="$(PORT)"; \
	if [ -z "$$PORT_VALUE" ]; then PORT_VALUE="$$( $(KIDCODE) device-port )" || exit $$?; fi; \
	LOG_VALUE="$(LOG)"; \
	echo "Using port $$PORT_VALUE"; \
	echo "Writing serial log to $$LOG_VALUE"; \
	$(MAKE) firmware-upload-lilygo PORT="$$PORT_VALUE"; \
	timeout $(MONITOR_SECONDS)s pio device monitor -d firmware/lilygo_t_deck_plus -b 115200 --port "$$PORT_VALUE" --raw --quiet > "$$LOG_VALUE" || true; \
	$(KIDCODE) firmware-smoke-check "$$LOG_VALUE" --board lilygo_t_deck_plus --project-id tiny_runner
