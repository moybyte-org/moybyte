VENV ?= .venv
SYSTEM_PYTHON ?= python3
PYTHON ?= $(VENV)/bin/python
MOYBYTE ?= $(VENV)/bin/moybyte
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

.PHONY: setup test run-example run-headless compile-blocks site-gifs doctor check-portable pack-example export-lilygo-example device-doctor device-port firmware-bundle-lilygo firmware-build-lilygo firmware-build-lilygo-micropython firmware-sim-lilygo-micropython firmware-flash-lilygo-micropython firmware-flash-lilygo-micropython-no-reset firmware-flash-lilygo-micropython-full firmware-flash-lilygo-micropython-full-erase firmware-run-lilygo-micropython firmware-monitor-lilygo-micropython firmware-upload-lilygo firmware-monitor-lilygo firmware-smoke-check-lilygo firmware-smoke-lilygo ota-manifest ota-serve ota-publish-unstable ota-publish-stable ota-host ota-serve-install

setup:
	$(SYSTEM_PYTHON) -m venv --system-site-packages $(VENV)
	$(PYTHON) -m pip install --no-build-isolation -e '.[dev]'

test:
	$(PYTHON) -m pytest

doctor:
	$(MOYBYTE) doctor

device-doctor:
	$(MOYBYTE) device-doctor --board lilygo_t_deck_plus

device-port:
	$(MOYBYTE) device-port

run-example:
	$(MOYBYTE) run examples/tiny_runner.moyproj

run-headless:
	$(MOYBYTE) run examples/tiny_runner.moyproj --headless --frames 60

compile-blocks:
	$(MOYBYTE) compile examples/blocks_demo.moyproj

# Regenerate the teaser-site demo GIFs from the real console (headless).
site-gifs:
	$(PYTHON) tools/make_site_gifs.py

check-portable:
	$(MOYBYTE) check-portable examples/tiny_runner.moyproj examples/blocks_demo.moyproj examples/music_player_stub.moyproj examples/radio_pong_stub.moyproj

pack-example:
	$(MOYBYTE) pack examples/tiny_runner.moyproj --out /tmp/tiny_runner.kc8

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
	test -n "$(PORT)"
	@[ -z "$(MPY_OTADATA_OFFSET)" ] || $(IDF_PYTHON) tools/esptool_no_modem.py --chip esp32s3 -p $(PORT) -b 460800 --before default_reset --after no_reset erase_region $(MPY_OTADATA_OFFSET) $(MPY_OTADATA_SIZE)
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

