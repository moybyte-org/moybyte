#!/usr/bin/env bash
# Moybyte P4 port (#58): build mainline MicroPython (ESP32_GENERIC_P4, C6_WIFI
# variant) + the moy_dsi native module (EK79007 MIPI-DSI panel) for the
# Waveshare ESP32-P4-WIFI6-Touch-LCD-7B.
#
# A plain mainline build with USER_C_MODULES -- the strategy both boards use
# now (this board went mainline first, because the deleted lvgl_micropython
# fork had no P4/DSI support, and became the T-Deck port's template). The
# SHARED half of the build lives in tools/esp32_build_lib.sh since 2026-08-17;
# what stays here is this board's patch ladder and sdkconfig facts. Output
# flashes at offset 0x2000:
#   esptool --port /dev/ttyACM0 --baud 921600 write_flash 0x2000 dist/p4/moybyte_p4.bin
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${SCRIPT_DIR}/.build"
MPY_DIR="${BUILD_DIR}/micropython"
MPY_TAG="${MPY_TAG:-v1.28.0}"
BOARD="MOYBYTE_P4"
BOARD_DIR="${SCRIPT_DIR}/boards/${BOARD}"
PATCH_DIR="${SCRIPT_DIR}/patches"
DIST_DIR="${REPO_ROOT}/dist/p4"
MODULES_DIR="${SCRIPT_DIR}/modules"
MANIFEST="${BUILD_DIR}/moybyte_p4_manifest.py"

# shellcheck source=../../tools/esp32_build_lib.sh
source "${REPO_ROOT}/tools/esp32_build_lib.sh"
moybyte_resolve_build_python

mkdir -p "${BUILD_DIR}" "${DIST_DIR}" "${MODULES_DIR}"

# ---------------------------------------------------------------------------
# 1) Toolchain: MicroPython at the pinned tag, and ESP-IDF v5.5.1, reusing the
#    T-Deck's checkout when it exists (same version, saves a 500MB clone).
# ---------------------------------------------------------------------------
moybyte_clone_micropython
moybyte_setup_idf esp32p4 \
  "${REPO_ROOT}/firmware/lilygo_t_deck_plus_mainline/.build/esp-idf"

# ---------------------------------------------------------------------------
# 2) The patch ladder -- THIS BOARD'S half of the build. All marker-guarded,
#    because both the .build tree and a reused IDF checkout persist.
# ---------------------------------------------------------------------------

# 2a) Steady-state BLE keyboard notifications must not wait behind MicroPython's
#     synchronous NimBLE IRQ/GIL path. The P4-only native queue consumes
#     registered HID handles before Python dispatch; pairing/bonding/discovery
#     remain on the supported synchronous path.
MODBLUETOOTH_C="${MPY_DIR}/extmod/modbluetooth.c"
if [ -f "${MODBLUETOOTH_C}" ] && \
   ! grep -q "moy_ble_hid_queue_on_notify" "${MODBLUETOOTH_C}"; then
  echo "== applying P4 BLE-HID native notification fast-path patch"
  patch -d "${MPY_DIR}" -p1 < "${PATCH_DIR}/modbluetooth_ble_hid_fastpath.patch"
fi

# 2b) #106: backport current ESP-IDF's dedicated DSI bridge-underrun ISR and
#     keep the frame-restart DW-GDMA interrupt above ESP-Hosted's SDIO
#     interrupt. IDF v5.5 checks the bridge only from the DMA callback; if SDIO
#     delays that callback, the display has already gone blue and the status
#     can be cleared unseen.
DSI_DPI_C="${IDF_DIR}/components/esp_lcd/dsi/esp_lcd_panel_dpi.c"
if [ -f "${DSI_DPI_C}" ] && \
   ! grep -q "Moybyte P4: dedicated DSI bridge underrun IRQ" "${DSI_DPI_C}"; then
  echo "== applying P4 DSI bridge IRQ/priority fix (#106)"
  patch -d "${IDF_DIR}" -p1 < "${PATCH_DIR}/esp_lcd_dsi_underrun_hook.patch"
fi

# 2c) moy_dsi needs esp_lcd, moy_ppa needs esp_driver_ppa (the P4 pixel
#     accelerator) in the main component's REQUIRES.
moybyte_idf_component esp_lcd
moybyte_idf_component esp_driver_ppa

# 2d) Un-static esp_native_code_free_all (#66) -- shared with the T-Deck.
#     Mainline's ports/esp32/main.c has the identical grow-only
#     esp_native_code_commit list, and the P4's RV32 native emitter feeds it
#     (MICROPY_EMIT_RV32=1), so edit->PLAY sessions would hit the same cliff,
#     just later (bigger internal pool).
moybyte_patch_native_code_free

# ---------------------------------------------------------------------------
# 3) Stage: the shared native modules (board.toml [native.shared] -- the two
#    denials, moy_sd and moy_audio, live there WITH their reasons; all plain C,
#    the S3-specific pieces are include-guarded, so they compile unchanged on
#    RISC-V) with the browser console blob generated into the staged copy
#    (never into the shared native/ tree two builds read -- this used to
#    generate there and race a concurrent T-Deck build); then the shared
#    PYTHON modules (#58 console staging, #161 Phase 3 -- board.toml holds the
#    denylist over runtime/ and the allowlist over device/, and the stager
#    prunes untracked strays); then the generated modules.
# ---------------------------------------------------------------------------
moybyte_stage_native
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" stage "${SCRIPT_DIR}"

#    carts_data.py is GENERATED from system_carts/ (same as the T-Deck) so the
#    P4's seed/fallback carts can never drift from the host source of truth.
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_device_carts.py" "${MODULES_DIR}/carts_data.py"

#    The OTA identity stamp (#53). An app image is board-specific in the
#    strongest way (Xtensa there, RISC-V here), so the manifest url carries the
#    board and the device refuses a manifest naming another one.
moybyte_ota_identity p4 "${REPO_ROOT}/device/moy_ota.py"

# ---------------------------------------------------------------------------
# 4) Frozen manifest + partition table (#58: OTA-shaped 2x4MB app slots +
#    auto-vfs tail -- the default 4MiBplus table's ~1.94MB app can't hold the
#    frozen console) + the stale-sdkconfig guard.
# ---------------------------------------------------------------------------
moybyte_frozen_manifest "${MANIFEST}"
moybyte_partition_and_sdkconfig_guard \
  "${BOARD_DIR}/partitions-moybyte-p4.csv" \
  "${MPY_DIR}/ports/esp32/build-${BOARD}/sdkconfig" \
  'CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions-moybyte-p4.csv"' \
  'CONFIG_BT_NIMBLE_TRANSPORT_ACL_FROM_LL_COUNT=64' \
  'CONFIG_BT_NIMBLE_HOST_TASK_STACK_SIZE=12288' \
  'CONFIG_CACHE_L2_CACHE_256KB=y' \
  'CONFIG_LCD_DSI_ISR_IRAM_SAFE=y' \
  'CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y'

# ---------------------------------------------------------------------------
# 5) Build.
# ---------------------------------------------------------------------------
make -C "${MPY_DIR}/mpy-cross" -j"$(nproc)"

cd "${MPY_DIR}/ports/esp32"
make submodules BOARD_DIR="${BOARD_DIR}"
make BOARD_DIR="${BOARD_DIR}" \
  USER_C_MODULES="${SCRIPT_DIR}/native/micropython.cmake" \
  FROZEN_MANIFEST="${MANIFEST}"

# ---------------------------------------------------------------------------
# 6) Collect images + the #168 size guard (the T-Deck's, brought over -- it
#    matters now that ~573KB of the slot is the baked web console).
#    firmware.bin is bootloader + table + app merged for a cable flash at
#    0x2000; micropython.bin is the APP partition image -- what an OTA writes
#    into the inactive slot. Handing firmware.bin to esp32.Partition would
#    write a bootloader into an app slot.
# ---------------------------------------------------------------------------
BOUT="build-${BOARD}"
cp "${BOUT}/firmware.bin" "${DIST_DIR}/moybyte_p4.bin"
cp "${BOUT}/micropython.bin" "${DIST_DIR}/moybyte_p4_app.bin"
moybyte_app_size_guard "${BOARD_DIR}/partitions-moybyte-p4.csv" \
  "${DIST_DIR}/moybyte_p4_app.bin"

echo "OK -> ${DIST_DIR}/moybyte_p4.bin (flash at offset 0x2000)"
echo "OK -> ${DIST_DIR}/moybyte_p4_app.bin (OTA payload, app partition)"
