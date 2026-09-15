#!/usr/bin/env bash
# Moybyte Guition P4 (Guition JC8012P4A1C: ESP32-P4NRW32 + ESP32-C6, 10.1"
# 800x1280 JD9365 MIPI-DSI, GSL3680 touch) -- the fifth build target and the
# second ESP32-P4 board, a VARIANT of the Waveshare 7B's port (#58): mainline
# MicroPython (ESP32_GENERIC_P4, C6_WIFI variant) + the P4 silicon tier
# (native/p4, staged as board.toml data). The SHARED half of the build lives in
# tools/esp32_build_lib.sh; what stays here is this board's patch ladder --
# the Waveshare's, line for line, because it is the same silicon -- and its
# sdkconfig facts. Output flashes at offset 0x2000:
#   make firmware-flash-guition-p4 PORT=/dev/ttyACM4
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${SCRIPT_DIR}/.build"
MPY_DIR="${BUILD_DIR}/micropython"
MPY_TAG="${MPY_TAG:-v1.28.0}"
BOARD="MOYBYTE_GUITION_P4"
BOARD_DIR="${SCRIPT_DIR}/boards/${BOARD}"
DIST_DIR="${REPO_ROOT}/dist/guition_p4"
MODULES_DIR="${SCRIPT_DIR}/modules"
MANIFEST="${BUILD_DIR}/moybyte_guition_p4_manifest.py"

# shellcheck source=../../tools/esp32_build_lib.sh
source "${REPO_ROOT}/tools/esp32_build_lib.sh"
moybyte_resolve_build_python

mkdir -p "${BUILD_DIR}" "${DIST_DIR}" "${MODULES_DIR}"

# ---------------------------------------------------------------------------
# 1) Toolchain: our OWN MicroPython checkout (sharing one would race the other
#    boards' sed-patched esp32_common.cmake), and ESP-IDF v5.5.1 reused from
#    the Waveshare P4, which owns the shared checkout (see its build.sh) --
#    falling back to a clone of our own, which is what happens on every CI
#    runner. The #106 DSI patch below is marker-guarded, so two P4 builds over
#    one IDF apply it once.
# ---------------------------------------------------------------------------
moybyte_clone_micropython
moybyte_setup_idf esp32p4 \
  "${REPO_ROOT}/firmware/esp32_p4_wifi6_touch_lcd_7b/.build/esp-idf"

# ---------------------------------------------------------------------------
# 2) The patch ladder -- the Waveshare's, because it is the same silicon.
# ---------------------------------------------------------------------------

# 2a) The P4 SILICON patches (shared lib, both P4 boards): the BLE-HID
#     notification fast path into MicroPython's modbluetooth.c, and the #106
#     DSI bridge-underrun ISR backport into the (shared) ESP-IDF checkout.
moybyte_patch_p4_ble_hid_fastpath
moybyte_patch_p4_dsi_underrun

# 2b) moy_dsi needs esp_lcd, moy_ppa needs esp_driver_ppa (the P4 pixel
#     accelerator) in the main component's REQUIRES.
moybyte_idf_component esp_lcd
moybyte_idf_component esp_driver_ppa

# 2c) ESP-Hosted 2.7.0 -> 2.12.12 -- the espnow-on-p4 track (shared lib). On
#     THIS board the C6 runs Guition's factory slave; hosted 2.12 against it is
#     what the first boot reports (README records the verdict).
moybyte_patch_esp_hosted_bump esp32p4

# 2d) Un-static esp_native_code_free_all (#66) -- shared with every board.
moybyte_patch_native_code_free
moybyte_patch_espnow_ring_race

# 2e) REPR_C -- FLOAT WIDTH IS PART OF THE LOCKSTEP CONTRACT (the Waveshare's
#     build.sh carries the measured argument): every board that can hold a
#     link runs REPR_C.
moybyte_patch_repr_c

# DECLINED moybyte_patch_gc_split_reserve -- the split-heap growth cap (#66).
# MOYBYTE_GC_SPLIT_RESERVE is set by the two S3 boards' mpconfigboard.h alone,
# so a call here reserves 0. Whether a P4 wants a reserve is unmeasured (#58).

# DECLINED moybyte_patch_psram_retune -- not applicable: an ESP32-S3 MSPI
# timing-tuner patch (#169); this is an ESP32-P4 and the file does not exist
# in its build.

# ---------------------------------------------------------------------------
# 3) Stage: the shared native modules + the P4 silicon tier (board.toml
#    [native.shared] / [native.p4]) with the browser console blob generated
#    into the staged copy; then the shared PYTHON modules (board.toml holds the
#    denylist over runtime/ and the allowlist over device/, and the stager
#    prunes untracked strays); then the generated modules.
# ---------------------------------------------------------------------------
moybyte_stage_native
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" stage "${SCRIPT_DIR}"

#    carts_data.py is built from system_carts/ so the seed + embedded-fallback
#    carts can never drift from the host source of truth. PACKED: one
#    raw-deflate blob per cart.
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_device_carts.py" --packed "${MODULES_DIR}/carts_data.py"

#    The OTA identity stamp (#53): a fresh board id -- an OTA payload is an
#    app-partition image and the manifest is per board.
moybyte_ota_identity guition_p4 "${REPO_ROOT}/device/moy_ota.py"

# ---------------------------------------------------------------------------
# 4) Frozen manifest + partition table (OTA-shaped 2x4MB app slots + auto-vfs
#    tail on 16MB) + the stale-sdkconfig guard.
# ---------------------------------------------------------------------------
moybyte_frozen_manifest "${MANIFEST}"
moybyte_sdkconfig_guard "${BOARD_DIR}" \
  "${MPY_DIR}/ports/esp32/build-${BOARD}/sdkconfig"

# ---------------------------------------------------------------------------
# 5) Build + collect (shared lib: mpy-cross, the port, the two images and the
#    #168 size guard).
# ---------------------------------------------------------------------------
moybyte_build_and_collect "${BOARD_PARTITION_CSV}" \
  moybyte_guition_p4 "flash at offset 0x2000"
