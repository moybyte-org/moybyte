#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${SCRIPT_DIR}/.build"
UPSTREAM_DIR="${BUILD_DIR}/lvgl_micropython"
DIST_DIR="${SCRIPT_DIR}/dist"
CURRENT_DIR="${DIST_DIR}/current"
MANIFEST="${BUILD_DIR}/kidcode_manifest.py"
PATCH_DIR="${SCRIPT_DIR}/patches"
ARTIFACT_NAME="${KIDCODE_ARTIFACT_NAME:-kidcode_micropython_tdeck}"
APP_BIN="${DIST_DIR}/${ARTIFACT_NAME}.bin"
FULL_DIO_BIN="${DIST_DIR}/${ARTIFACT_NAME}_full_dio_0x0.bin"
FULL_QIO_BIN="${DIST_DIR}/${ARTIFACT_NAME}_full_qio_0x0.bin"
CURRENT_APP_BIN="${CURRENT_DIR}/kidcode-current-app.bin"
CURRENT_FULL_DIO_BIN="${CURRENT_DIR}/kidcode-current-full-dio-0x0.bin"
CURRENT_FULL_QIO_BIN="${CURRENT_DIR}/kidcode-current-full-qio-0x0.bin"
BUILD_JOBS="${KIDCODE_BUILD_JOBS:-2}"
BUILD_NICE="${KIDCODE_BUILD_NICE:-15}"
EARLY_BOARD_INIT="${KIDCODE_EARLY_BOARD_INIT:-0}"
SKIP_VFS_BOOT="${KIDCODE_SKIP_VFS_BOOT:-0}"
BOARD_CONFIG="${KIDCODE_BOARD_CONFIG:-generic}"
REPL_MODE="${KIDCODE_REPL:-cdc_uart}"
BUILD_PYTHON="${KIDCODE_BUILD_PYTHON:-}"

if [ -z "${BUILD_PYTHON}" ]; then
  if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
    BUILD_PYTHON="${REPO_ROOT}/.venv/bin/python"
  else
    BUILD_PYTHON="python3"
  fi
fi

mkdir -p "${BUILD_DIR}" "${DIST_DIR}" "${CURRENT_DIR}"

if [ ! -d "${UPSTREAM_DIR}/.git" ]; then
  git clone --depth 1 https://github.com/lvgl-micropython/lvgl_micropython "${UPSTREAM_DIR}"
fi

MPY_MAIN_C="${UPSTREAM_DIR}/lib/micropython/ports/esp32/main.c"
MPY_BOOT_PY="${UPSTREAM_DIR}/lib/micropython/ports/esp32/modules/_boot.py"
MPY_BOOT_ORIG="${BUILD_DIR}/micropython_esp32_boot.py.orig"
if [ "${EARLY_BOARD_INIT}" = "1" ]; then
  if ! grep -q "kidcode_tdeck_early_board_init" "${MPY_MAIN_C}"; then
    patch -d "${UPSTREAM_DIR}/lib/micropython" -p1 < "${PATCH_DIR}/esp32_tdeck_early_board_init.patch"
  fi
else
  if grep -q "kidcode_tdeck_early_board_init" "${MPY_MAIN_C}"; then
    patch -R -d "${UPSTREAM_DIR}/lib/micropython" -p1 < "${PATCH_DIR}/esp32_tdeck_early_board_init.patch"
  fi
fi

if [ ! -f "${MPY_BOOT_ORIG}" ]; then
  cp "${MPY_BOOT_PY}" "${MPY_BOOT_ORIG}"
fi
if [ "${SKIP_VFS_BOOT}" = "1" ]; then
  cat > "${MPY_BOOT_PY}" <<'EOF'
import gc

print("KidCode diagnostic: skipped MicroPython VFS mount")
gc.collect()
EOF
else
  cp "${MPY_BOOT_ORIG}" "${MPY_BOOT_PY}"
fi

# Stage the KidCode kc_alloc native C module (DMA allocator for the canvas
# blitter) into the upstream ext_mod tree and include it in the build. The
# upstream ext_mod can be wiped on re-clone, so we re-stage every build.
KC_ALLOC_SRC="${SCRIPT_DIR}/native/kc_alloc"
KC_ALLOC_DST="${UPSTREAM_DIR}/ext_mod/kc_alloc"
if [ -d "${KC_ALLOC_SRC}" ]; then
  rm -rf "${KC_ALLOC_DST}"
  cp -r "${KC_ALLOC_SRC}" "${KC_ALLOC_DST}"
  EXT_MOD_CMAKE="${UPSTREAM_DIR}/ext_mod/micropython.cmake"
  if ! grep -q 'kc_alloc/micropython.cmake' "${EXT_MOD_CMAKE}"; then
    sed -i '/lcd_utils\/micropython.cmake/a include(${CMAKE_CURRENT_LIST_DIR}/kc_alloc/micropython.cmake)' "${EXT_MOD_CMAKE}"
  fi
fi

# Stage the KidCode kc_gfx native C module (VM-neutral RGB565 pixel kernel for the
# Stage 3 native compositor) into the upstream ext_mod tree, same pattern as
# kc_alloc (ext_mod is wiped on re-clone, so re-stage every build).
KC_GFX_SRC="${SCRIPT_DIR}/native/kc_gfx"
KC_GFX_DST="${UPSTREAM_DIR}/ext_mod/kc_gfx"
if [ -d "${KC_GFX_SRC}" ]; then
  rm -rf "${KC_GFX_DST}"
  cp -r "${KC_GFX_SRC}" "${KC_GFX_DST}"
  EXT_MOD_CMAKE="${UPSTREAM_DIR}/ext_mod/micropython.cmake"
  if ! grep -q 'kc_gfx/micropython.cmake' "${EXT_MOD_CMAKE}"; then
    sed -i '/kc_alloc\/micropython.cmake/a include(${CMAKE_CURRENT_LIST_DIR}/kc_gfx/micropython.cmake)' "${EXT_MOD_CMAKE}"
  fi
fi

# Stage the KidCode kc_sd native C module (SD card attached to the display-shared
# SPI host -- see native/kc_sd/modkc_sd.c) into the upstream ext_mod tree, same
# pattern as kc_gfx (ext_mod is wiped on re-clone, so re-stage every build).
KC_SD_SRC="${SCRIPT_DIR}/native/kc_sd"
KC_SD_DST="${UPSTREAM_DIR}/ext_mod/kc_sd"
if [ -d "${KC_SD_SRC}" ]; then
  rm -rf "${KC_SD_DST}"
  cp -r "${KC_SD_SRC}" "${KC_SD_DST}"
  EXT_MOD_CMAKE="${UPSTREAM_DIR}/ext_mod/micropython.cmake"
  if ! grep -q 'kc_sd/micropython.cmake' "${EXT_MOD_CMAKE}"; then
    sed -i '/kc_gfx\/micropython.cmake/a include(${CMAKE_CURRENT_LIST_DIR}/kc_sd/micropython.cmake)' "${EXT_MOD_CMAKE}"
  fi
fi

# Stage the shared host/device modules into the frozen modules tree. Canonical
# sources live in runtime/ (imported by the host as runtime.*); the device freezes
# these copies as top-level modules, so both consoles run literally the same code:
#   editors.py    -- CodeEditor / SpriteSheet / PaintEditor
#   console.py    -- launcher + desktop + cards/code/paint UI + Pointer
#   kid_carts.py  -- the .kcart store (scan/load/save/create/duplicate/delete)
cp "${REPO_ROOT}/runtime/editors.py" "${SCRIPT_DIR}/modules/editors.py"
cp "${REPO_ROOT}/runtime/console.py" "${SCRIPT_DIR}/modules/console.py"
cp "${REPO_ROOT}/runtime/kid_carts.py" "${SCRIPT_DIR}/modules/kid_carts.py"

BUILDER_ESP32="${UPSTREAM_DIR}/builder/esp32.py"
if [ -f "${BUILDER_ESP32}" ]; then
  sed -i \
    -e "s/f'--jobs {os.cpu_count()}'/'--jobs ${BUILD_JOBS}'/g" \
    -e "s/f'-j {os.cpu_count()}'/'-j ${BUILD_JOBS}'/g" \
    "${BUILDER_ESP32}"
  if ! grep -q "KIDCODE_SKIP_UPSTREAM_SUBMODULES" "${BUILDER_ESP32}"; then
    sed -i "/    update_makefile()/a\\
\\
    if os.environ.get('KIDCODE_SKIP_UPSTREAM_SUBMODULES', '1') == '1':\\
        print('skipping upstream MicroPython submodules target')\\
        return" "${BUILDER_ESP32}"
  fi
fi

export MAKEFLAGS="${MAKEFLAGS:--j${BUILD_JOBS}}"
export CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-${BUILD_JOBS}}"
export KIDCODE_SKIP_UPSTREAM_SUBMODULES="${KIDCODE_SKIP_UPSTREAM_SUBMODULES:-1}"
export GEN_SCRIPT="${GEN_SCRIPT:-python}"

case "${BOARD_CONFIG}" in
  generic)
    MPY_BUILD_DIR="${UPSTREAM_DIR}/lib/micropython/ports/esp32/build-ESP32_GENERIC_S3-SPIRAM_OCT"
    cat > "${MANIFEST}" <<EOF
freeze("${SCRIPT_DIR}/modules", opt=3)
EOF
    ;;
  tdeck)
    MPY_BUILD_DIR="${UPSTREAM_DIR}/lib/micropython/ports/esp32/build-LilyGo-TDeck"
    cat > "${MANIFEST}" <<EOF
include("${UPSTREAM_DIR}/display_configs/LilyGo-TDeck/manifest.py")
freeze("${SCRIPT_DIR}/modules", opt=3)
EOF
    ;;
  *)
    echo "Unknown KIDCODE_BOARD_CONFIG=${BOARD_CONFIG}; expected generic or tdeck" >&2
    exit 1
    ;;
esac

# Force the frozen-module compile to re-run whenever a frozen source changes.
# Ninja rests custom commands on identical manifest content, so a plain `cat >`
# with the same text silently skips re-freezing and stale .mpy get flashed.
# Embedding an md5 of every frozen .py makes the manifest genuinely change.
echo "# frozen-source fingerprint: $(find "${SCRIPT_DIR}/modules" -type f -name '*.py' -exec md5sum {} + 2>/dev/null | sort | md5sum | cut -d' ' -f1)" >> "${MANIFEST}"

cd "${UPSTREAM_DIR}"

if [ -f "${UPSTREAM_DIR}/lib/esp-idf/export.sh" ]; then
  export IDF_PATH="${UPSTREAM_DIR}/lib/esp-idf"
  # Seed the ESP-IDF environment so rebuilds reuse the already-installed tools.
  # The upstream wrapper still verifies the toolchain before compiling.
  . "${IDF_PATH}/export.sh" >/dev/null 2>&1
fi

RUNNER=()
if command -v ionice >/dev/null 2>&1; then
  RUNNER+=(ionice -c 3)
fi
if command -v nice >/dev/null 2>&1; then
  RUNNER+=(nice -n "${BUILD_NICE}")
fi

case "${REPL_MODE}" in
  cdc_uart)
    REPL_ARGS=(--enable-cdc-repl=y --enable-uart-repl=y)
    ;;
  cdc)
    REPL_ARGS=(--enable-cdc-repl=y --enable-uart-repl=n)
    ;;
  jtag)
    REPL_ARGS=(--enable-jtag-repl=y --enable-cdc-repl=n --enable-uart-repl=n)
    ;;
  uart)
    REPL_ARGS=(--enable-cdc-repl=n --enable-uart-repl=y)
    ;;
  none)
    REPL_ARGS=(--enable-cdc-repl=n --enable-uart-repl=n)
    ;;
  *)
    echo "Unknown KIDCODE_REPL=${REPL_MODE}; expected cdc_uart, cdc, jtag, uart, or none" >&2
    exit 1
    ;;
esac

case "${BOARD_CONFIG}" in
  generic)
    BUILD_COMMAND=(
      "${BUILD_PYTHON}" make.py esp32
      BOARD=ESP32_GENERIC_S3
      BOARD_VARIANT=SPIRAM_OCT
      DISPLAY=st7789
      FROZEN_MANIFEST="${MANIFEST}"
      --flash-size=16
      --partition-size=4194304
      "${REPL_ARGS[@]}"
      --task-stack-size=16384
    )
    ;;
  tdeck)
    TDECK_TOML="${UPSTREAM_DIR}/display_configs/LilyGo-TDeck/LilyGo-TDeck.toml"
    TDECK_CMAKE="${UPSTREAM_DIR}/display_configs/LilyGo-TDeck/mpconfigboard.cmake"
    TDECK_H="${UPSTREAM_DIR}/display_configs/LilyGo-TDeck/mpconfigboard.h"
    TDECK_SDKCONFIG="${UPSTREAM_DIR}/display_configs/LilyGo-TDeck/sdkconfig.board"
    if grep -q '^display\.set_rotation]' "${TDECK_TOML}"; then
      sed -i 's/^display\.set_rotation]$/[display.set_rotation]/' "${TDECK_TOML}"
    fi
    if grep -q 'boards/sdkconfig\.usb' "${TDECK_CMAKE}"; then
      sed -i '/boards\/sdkconfig\.usb/d' "${TDECK_CMAKE}"
    fi
    if grep -q 'set(MICROPY_PY_TINYUSB OFF)' "${TDECK_CMAKE}"; then
      sed -i '/set(MICROPY_PY_TINYUSB OFF)/d' "${TDECK_CMAKE}"
    fi
    if [ "${REPL_MODE}" = "jtag" ]; then
      sed -i \
        -e 's/^#define MICROPY_HW_USB_CDC .*/#define MICROPY_HW_USB_CDC                  (0)/' \
        -e 's/^#define MICROPY_HW_ESP_USB_SERIAL_JTAG .*/#define MICROPY_HW_ESP_USB_SERIAL_JTAG      (1)/' \
        "${TDECK_H}"
      if ! grep -q '^CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y' "${TDECK_SDKCONFIG}"; then
        printf '\nCONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y\n' >> "${TDECK_SDKCONFIG}"
      fi
    else
      sed -i \
        -e 's/^#define MICROPY_HW_USB_CDC .*/#define MICROPY_HW_USB_CDC                  (1)/' \
        -e 's/^#define MICROPY_HW_ESP_USB_SERIAL_JTAG .*/#define MICROPY_HW_ESP_USB_SERIAL_JTAG      (0)/' \
        "${TDECK_H}"
      if grep -q '^CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y' "${TDECK_SDKCONFIG}"; then
        sed -i '/^CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y/d' "${TDECK_SDKCONFIG}"
      fi
    fi
    sed -i "s|^FROZEN_MANIFEST = .*|FROZEN_MANIFEST = \"${MANIFEST}\"|" "${TDECK_TOML}"
    BUILD_COMMAND=(
      "${BUILD_PYTHON}" make.py
      --custom-board-path=display_configs/LilyGo-TDeck
      --no-scrub
      esp32
      --task-stack-size=16384
    )
    ;;
esac

"${RUNNER[@]}" "${BUILD_COMMAND[@]}"

if [ ! -f "${MPY_BUILD_DIR}/micropython.bin" ]; then
  echo "No ESP32 app image found at ${MPY_BUILD_DIR}/micropython.bin" >&2
  exit 1
fi

cp "${MPY_BUILD_DIR}/micropython.bin" "${APP_BIN}"
echo "Wrote SD launcher app image: ${APP_BIN}"
cp "${APP_BIN}" "${CURRENT_APP_BIN}"
echo "Updated current app alias: ${CURRENT_APP_BIN}"

BOOTLOADER_BIN="${MPY_BUILD_DIR}/bootloader/bootloader.bin"
PARTITION_BIN="${MPY_BUILD_DIR}/partition_table/partition-table.bin"
if [ ! -f "${BOOTLOADER_BIN}" ] || [ ! -f "${PARTITION_BIN}" ]; then
  echo "Skipping full image merge; bootloader or partition table is missing" >&2
  exit 0
fi

if [ -n "${IDF_PYTHON:-}" ]; then
  ESPTOOL_PY="${IDF_PYTHON}"
elif [ -x "${HOME}/.espressif/python_env/idf5.5_py3.10_env/bin/python" ]; then
  ESPTOOL_PY="${HOME}/.espressif/python_env/idf5.5_py3.10_env/bin/python"
else
  ESPTOOL_PY="python3"
fi

"${ESPTOOL_PY}" -m esptool --chip esp32s3 merge_bin \
  -o "${FULL_DIO_BIN}" \
  --flash_mode dio \
  --flash_size 16MB \
  --flash_freq 80m \
  0x0 "${BOOTLOADER_BIN}" \
  0x8000 "${PARTITION_BIN}" \
  0x10000 "${MPY_BUILD_DIR}/micropython.bin"
echo "Wrote full flash image: ${FULL_DIO_BIN}"
cp "${FULL_DIO_BIN}" "${CURRENT_FULL_DIO_BIN}"
echo "Updated current full DIO alias: ${CURRENT_FULL_DIO_BIN}"

"${ESPTOOL_PY}" -m esptool --chip esp32s3 merge_bin \
  -o "${FULL_QIO_BIN}" \
  --flash_mode qio \
  --flash_size 16MB \
  --flash_freq 80m \
  0x0 "${BOOTLOADER_BIN}" \
  0x8000 "${PARTITION_BIN}" \
  0x10000 "${MPY_BUILD_DIR}/micropython.bin"
echo "Wrote full flash image: ${FULL_QIO_BIN}"
cp "${FULL_QIO_BIN}" "${CURRENT_FULL_QIO_BIN}"
echo "Updated current full QIO alias: ${CURRENT_FULL_QIO_BIN}"
