#!/usr/bin/env bash
# Moybyte P4 port (#58): build mainline MicroPython (ESP32_GENERIC_P4, C6_WIFI
# variant) + the moy_dsi native module (EK79007 MIPI-DSI panel) for the
# Waveshare ESP32-P4-WIFI6-Touch-LCD-7B.
#
# Unlike the T-Deck build this does NOT use lvgl_micropython (no P4/DSI support
# there) -- it is a plain mainline build with USER_C_MODULES. Output flashes at
# offset 0x2000:
#   esptool --port /dev/ttyACM0 --baud 921600 write_flash 0x2000 dist/p4/moybyte_p4.bin
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${SCRIPT_DIR}/.build"
MPY_DIR="${BUILD_DIR}/micropython"
MPY_TAG="${MPY_TAG:-v1.28.0}"
BOARD="MOYBYTE_P4"
BOARD_DIR="${SCRIPT_DIR}/boards/${BOARD}"
DIST_DIR="${REPO_ROOT}/dist/p4"

mkdir -p "${BUILD_DIR}" "${DIST_DIR}"

# 1) MicroPython checkout (pinned tag).
if [ ! -d "${MPY_DIR}" ]; then
  echo "== cloning micropython ${MPY_TAG}"
  git clone --depth 1 -b "${MPY_TAG}" https://github.com/micropython/micropython "${MPY_DIR}"
fi

# 2) ESP-IDF v5.5.1: reuse the T-Deck build's checkout when present (same
#    version, saves a 500MB clone); otherwise clone our own into .build/.
if [ -z "${IDF_DIR:-}" ]; then
  TDECK_IDF="${REPO_ROOT}/firmware/lilygo_t_deck_plus_micropython/.build/lvgl_micropython/lib/esp-idf"
  if [ -f "${TDECK_IDF}/export.sh" ]; then
    IDF_DIR="${TDECK_IDF}"
  else
    IDF_DIR="${BUILD_DIR}/esp-idf"
    if [ ! -f "${IDF_DIR}/export.sh" ]; then
      echo "== cloning esp-idf v5.5.1"
      git clone --depth 1 -b v5.5.1 --recursive --shallow-submodules \
        https://github.com/espressif/esp-idf "${IDF_DIR}"
    fi
  fi
fi
echo "== using ESP-IDF at ${IDF_DIR}"
set +u
# shellcheck disable=SC1091
source "${IDF_DIR}/export.sh" >/dev/null
set -u

# 2b) moy_dsi needs esp_lcd in the main component's REQUIRES. The
#     USER_C_MODULES cmake is skipped during idf.py's early-expansion phase --
#     exactly when REQUIRES are collected -- so appending IDF_COMPONENTS there
#     can never work; patch the port's list instead (idempotent).
COMMON_CMAKE="${MPY_DIR}/ports/esp32/esp32_common.cmake"
if ! grep -q "^    esp_lcd$" "${COMMON_CMAKE}"; then
  sed -i '/^list(APPEND IDF_COMPONENTS$/a\    esp_lcd' "${COMMON_CMAKE}"
  echo "== patched esp32_common.cmake: added esp_lcd to IDF_COMPONENTS"
fi

# 3) mpy-cross (host tool, needed by the port build).
make -C "${MPY_DIR}/mpy-cross" -j"$(nproc)"

# 4) The esp32 port build (out-of-tree board definition).
cd "${MPY_DIR}/ports/esp32"
make submodules BOARD_DIR="${BOARD_DIR}"
make BOARD_DIR="${BOARD_DIR}" \
  USER_C_MODULES="${SCRIPT_DIR}/native/moy_dsi/micropython.cmake"

# 5) Collect the merged image (bootloader+partitions+app; flash at 0x2000).
BOUT="build-${BOARD}"
cp "${BOUT}/firmware.bin" "${DIST_DIR}/moybyte_p4.bin"
echo "OK -> ${DIST_DIR}/moybyte_p4.bin (flash at offset 0x2000)"
