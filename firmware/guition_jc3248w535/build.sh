#!/usr/bin/env bash
# Moybyte Guition JC3248W535 (ESP32-S3, 3.5" 320x480 QSPI AXS15231B) -- the
# third board, and the first built through the #202 port kit from day one:
# everything shared lives in tools/esp32_build_lib.sh, the staged module sets
# are board.toml data, and what remains here is this board's patch ladder and
# sdkconfig facts.
#
# Build -> flash -> look:
#
#   ./build.sh
#   make firmware-flash-guition-s3 PORT=/dev/ttyACM1
#
# The BOOT button (if a wedge ever needs the ROM loader) is the board's BOOT
# pad -- GPIO0; hold it while powering on.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${SCRIPT_DIR}/.build"
MPY_DIR="${BUILD_DIR}/micropython"
MPY_TAG="${MPY_TAG:-v1.28.0}"
BOARD="MOYBYTE_GUITION_S3"
BOARD_DIR="${SCRIPT_DIR}/boards/${BOARD}"
DIST_DIR="${REPO_ROOT}/dist/guition_s3"
PATCH_DIR="${REPO_ROOT}/patches"
MODULES_DIR="${SCRIPT_DIR}/modules"
MANIFEST="${BUILD_DIR}/moybyte_guition_s3_manifest.py"

# shellcheck source=../../tools/esp32_build_lib.sh
source "${REPO_ROOT}/tools/esp32_build_lib.sh"
moybyte_resolve_build_python

mkdir -p "${BUILD_DIR}" "${DIST_DIR}"

# ---------------------------------------------------------------------------
# 1) Toolchain: our OWN MicroPython checkout (sharing one would race the other
#    boards' sed-patched esp32_common.cmake), ESP-IDF v5.5.1 reused from
#    whichever sibling has one.
# ---------------------------------------------------------------------------
moybyte_clone_micropython
moybyte_setup_idf esp32s3 \
  "${REPO_ROOT}/firmware/lilygo_t_deck_plus_mainline/.build/esp-idf" \
  "${REPO_ROOT}/firmware/esp32_p4_wifi6_touch_lcd_7b/.build/esp-idf"

# ---------------------------------------------------------------------------
# 2) The patch ladder -- THIS BOARD'S half of the build, deliberately short.
#    Not applied, each a decision: the esp_lcd tx_color no-acquire patch
#    (moy_axs drives spi_master raw, no esp_lcd anywhere in this build) and
#    the #69 I2C GIL release (no input-poller thread here -- the AXS touch is
#    one 8-byte read per frame on an otherwise idle bus).
# ---------------------------------------------------------------------------
MPCONFIGPORT_H="${MPY_DIR}/ports/esp32/mpconfigport.h"

# 2a) REPR_C unboxed floats (#66) -- the same chip-class lever the T-Deck
#     measured: kid float-physics carts otherwise allocate a 16B heap box per
#     float result, and the heap-wrap gc collect is a 130-175ms visible hitch.
if ! grep -q "MICROPY_OBJ_REPR_C" "${MPCONFIGPORT_H}"; then
  sed -i 's|^#define MICROPY_OBJ_REPR  *(MICROPY_OBJ_REPR_A)|#define MICROPY_OBJ_REPR                    (MICROPY_OBJ_REPR_C) /* Moybyte #66: unboxed 30-bit floats */|' \
    "${MPCONFIGPORT_H}"
  grep -q "MICROPY_OBJ_REPR_C" "${MPCONFIGPORT_H}" || {
    echo "!! REPR_C patch did not apply -- mpconfigport.h's MICROPY_OBJ_REPR line changed shape" >&2
    exit 1
  }
  echo "== patched mpconfigport.h: MICROPY_OBJ_REPR_C (#66)"
fi

# 2b) Un-static esp_native_code_free_all (#66) -- shared with both siblings.
moybyte_patch_native_code_free

# 2c) PSRAM temperature retune, un-gated by flash vendor (#169). REQUIRED by
#     the 120MHz MSPI profile in sdkconfig.board (adopted 2026-08-19), for the
#     T-Deck's reason verbatim: IDF only starts the retune for verified flash
#     vendor ids and otherwise ABORTS THE BOOT from a secondary init fn -- a
#     board that flashes cleanly, says nothing, and reads like a PSRAM timing
#     failure. If sdkconfig.board ever reverts to 80MHz, this patch is inert.
MSPI_TUNING_C="${IDF_DIR}/components/esp_hw_support/mspi_timing_tuning/port/esp32s3/mspi_timing_by_mspi_delay.c"
if [ -f "${MSPI_TUNING_C}" ] && ! grep -q "Moybyte #169" "${MSPI_TUNING_C}"; then
  echo "== applying PSRAM temperature-retune vendor-gate patch (#169)"
  patch -d "${IDF_DIR}" -p1 < "${PATCH_DIR}/esp_psram_temp_retune_any_vendor.patch"
fi

# ---------------------------------------------------------------------------
# 3) Stage: shared native modules (board.toml [native.shared]) with the web
#    blob generated into the staged copy, then the shared Python modules
#    (board.toml [modules.*]), then the generated modules (`keep` names both).
# ---------------------------------------------------------------------------
moybyte_stage_native
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" stage "${SCRIPT_DIR}"

"${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_device_carts.py" "${MODULES_DIR}/carts_data.py"

#    The OTA identity stamp (#53): a fresh board id -- an OTA payload is an
#    app-partition image and the manifest is per board.
moybyte_ota_identity guition_s3 "${REPO_ROOT}/device/moy_ota.py"

# ---------------------------------------------------------------------------
# 4) Frozen manifest + partition table + the stale-sdkconfig guard.
# ---------------------------------------------------------------------------
moybyte_frozen_manifest "${MANIFEST}"
moybyte_sdkconfig_guard "${BOARD_DIR}" \
  "${MPY_DIR}/ports/esp32/build-${BOARD}/sdkconfig"

# ---------------------------------------------------------------------------
# 5) Build + collect (shared lib: mpy-cross, the port, the two images and the
#    #168 size guard). The merged image cable-flashes at 0x0 (S3 bootloader
#    offset).
# ---------------------------------------------------------------------------
moybyte_build_and_collect "${BOARD_PARTITION_CSV}" \
  moybyte_guition_s3 "full image, cable flash at 0x0"
