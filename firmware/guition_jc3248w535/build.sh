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
MODULES_DIR="${SCRIPT_DIR}/modules"
MANIFEST="${BUILD_DIR}/moybyte_guition_s3_manifest.py"

# shellcheck source=../../tools/esp32_build_lib.sh
source "${REPO_ROOT}/tools/esp32_build_lib.sh"
moybyte_resolve_build_python

mkdir -p "${BUILD_DIR}" "${DIST_DIR}"

# ---------------------------------------------------------------------------
# 1) Toolchain: our OWN MicroPython checkout (sharing one would race the other
#    boards' sed-patched esp32_common.cmake), and ESP-IDF v5.5.1 reused from
#    the P4, which owns the shared checkout (see its build.sh) -- falling back
#    to a clone of our own, which is what happens on every CI runner.
#
#    The T-Deck's checkout used to be the first candidate here and is DELETED:
#    it was the fork era's own clone, orphaned when the shared build lib
#    (2026-08-17) pointed the T-Deck's build at the P4's, and this board's
#    CMake cache had pinned CMAKE_TOOLCHAIN_FILE into it -- an entry CMake will
#    not re-point after the first configure. Name one owner, in one direction.
# ---------------------------------------------------------------------------
moybyte_clone_micropython
moybyte_setup_idf esp32s3 \
  "${REPO_ROOT}/firmware/esp32_p4_wifi6_touch_lcd_7b/.build/esp-idf"

# ---------------------------------------------------------------------------
# 2) The patch ladder -- THIS BOARD'S half of the build, deliberately short.
#    Not applied, each a decision: the esp_lcd tx_color no-acquire patch
#    (moy_axs drives spi_master raw, no esp_lcd anywhere in this build) and
#    the #69 I2C GIL release (no input-poller thread here -- the AXS touch is
#    one 8-byte read per frame on an otherwise idle bus).
# ---------------------------------------------------------------------------

# 2a) REPR_C unboxed floats (#66) -- the same chip-class lever the T-Deck
#     measured; same S3, same boxing cost.
moybyte_patch_repr_c

# 2b) Un-static esp_native_code_free_all (#66) -- shared with both siblings.
moybyte_patch_native_code_free
moybyte_patch_espnow_ring_race

# 2c) PSRAM temperature retune (#169) -- REQUIRED by the 120MHz MSPI profile in
#     sdkconfig.board (adopted 2026-08-19).
moybyte_patch_psram_retune

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
