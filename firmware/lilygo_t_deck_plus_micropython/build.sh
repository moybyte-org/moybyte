#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${SCRIPT_DIR}/.build"
UPSTREAM_DIR="${BUILD_DIR}/lvgl_micropython"
DIST_DIR="${SCRIPT_DIR}/dist"
CURRENT_DIR="${DIST_DIR}/current"
MANIFEST="${BUILD_DIR}/moybyte_manifest.py"
PATCH_DIR="${SCRIPT_DIR}/patches"
ARTIFACT_NAME="${MOYBYTE_ARTIFACT_NAME:-moybyte_micropython_tdeck}"
APP_BIN="${DIST_DIR}/${ARTIFACT_NAME}.bin"
FULL_DIO_BIN="${DIST_DIR}/${ARTIFACT_NAME}_full_dio_0x0.bin"
FULL_QIO_BIN="${DIST_DIR}/${ARTIFACT_NAME}_full_qio_0x0.bin"
CURRENT_APP_BIN="${CURRENT_DIR}/moybyte-current-app.bin"
CURRENT_FULL_DIO_BIN="${CURRENT_DIR}/moybyte-current-full-dio-0x0.bin"
CURRENT_FULL_QIO_BIN="${CURRENT_DIR}/moybyte-current-full-qio-0x0.bin"
BUILD_JOBS="${MOYBYTE_BUILD_JOBS:-2}"
BUILD_NICE="${MOYBYTE_BUILD_NICE:-15}"
EARLY_BOARD_INIT="${MOYBYTE_EARLY_BOARD_INIT:-0}"
SKIP_VFS_BOOT="${MOYBYTE_SKIP_VFS_BOOT:-0}"
BOARD_CONFIG="${MOYBYTE_BOARD_CONFIG:-generic}"
REPL_MODE="${MOYBYTE_REPL:-cdc_uart}"
BUILD_PYTHON="${MOYBYTE_BUILD_PYTHON:-}"

if [ -z "${BUILD_PYTHON}" ]; then
  if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
    BUILD_PYTHON="${REPO_ROOT}/.venv/bin/python"
  else
    BUILD_PYTHON="python3"
  fi
fi

mkdir -p "${BUILD_DIR}" "${DIST_DIR}" "${CURRENT_DIR}"

# Pin lvgl_micropython to a known, tested commit instead of cloning latest `main`.
# `main` is a rolling branch -- an unpinned clone meant a fresh `.build/` could pull a
# newer (possibly regressing) upstream and silently change the firmware out from under
# us. This is the exact commit we've validated on the T-Deck (and it's already past the
# RGB-bus StoreProhibited fix #514, which is RGB-panel-only and never affected our SPI
# st7789 panel anyway). Bump deliberately + re-test after wiping `.build/lvgl_micropython`.
LVGL_MPY_COMMIT="${MOYBYTE_LVGL_MPY_COMMIT:-14ad6ce2c5555272398debeff77b69021ca7ddda}"
if [ ! -d "${UPSTREAM_DIR}/.git" ]; then
  git clone https://github.com/lvgl-micropython/lvgl_micropython "${UPSTREAM_DIR}"
  git -C "${UPSTREAM_DIR}" checkout "${LVGL_MPY_COMMIT}"
fi

MPY_MAIN_C="${UPSTREAM_DIR}/lib/micropython/ports/esp32/main.c"
MPY_BOOT_PY="${UPSTREAM_DIR}/lib/micropython/ports/esp32/modules/_boot.py"
MPY_BOOT_ORIG="${BUILD_DIR}/micropython_esp32_boot.py.orig"
if [ "${EARLY_BOARD_INIT}" = "1" ]; then
  if ! grep -q "moybyte_tdeck_early_board_init" "${MPY_MAIN_C}"; then
    patch -d "${UPSTREAM_DIR}/lib/micropython" -p1 < "${PATCH_DIR}/esp32_tdeck_early_board_init.patch"
  fi
else
  if grep -q "moybyte_tdeck_early_board_init" "${MPY_MAIN_C}"; then
    patch -R -d "${UPSTREAM_DIR}/lib/micropython" -p1 < "${PATCH_DIR}/esp32_tdeck_early_board_init.patch"
  fi
fi

if [ ! -f "${MPY_BOOT_ORIG}" ]; then
  cp "${MPY_BOOT_PY}" "${MPY_BOOT_ORIG}"
fi
if [ "${SKIP_VFS_BOOT}" = "1" ]; then
  cat > "${MPY_BOOT_PY}" <<'EOF'
import gc

print("Moybyte diagnostic: skipped MicroPython VFS mount")
gc.collect()
EOF
else
  cp "${MPY_BOOT_ORIG}" "${MPY_BOOT_PY}"
fi

# Stage the Moybyte moy_alloc native C module (DMA allocator for the canvas
# blitter) into the upstream ext_mod tree and include it in the build. The
# upstream ext_mod can be wiped on re-clone, so we re-stage every build.
MOY_ALLOC_SRC="${SCRIPT_DIR}/native/moy_alloc"
MOY_ALLOC_DST="${UPSTREAM_DIR}/ext_mod/moy_alloc"
if [ -d "${MOY_ALLOC_SRC}" ]; then
  rm -rf "${MOY_ALLOC_DST}"
  cp -r "${MOY_ALLOC_SRC}" "${MOY_ALLOC_DST}"
  EXT_MOD_CMAKE="${UPSTREAM_DIR}/ext_mod/micropython.cmake"
  if ! grep -q 'moy_alloc/micropython.cmake' "${EXT_MOD_CMAKE}"; then
    sed -i '/lcd_utils\/micropython.cmake/a include(${CMAKE_CURRENT_LIST_DIR}/moy_alloc/micropython.cmake)' "${EXT_MOD_CMAKE}"
  fi
fi

# Stage the Moybyte moy_gfx native C module (VM-neutral RGB565 pixel kernel for the
# Stage 3 native compositor) into the upstream ext_mod tree, same pattern as
# moy_alloc (ext_mod is wiped on re-clone, so re-stage every build).
MOY_GFX_SRC="${SCRIPT_DIR}/native/moy_gfx"
MOY_GFX_DST="${UPSTREAM_DIR}/ext_mod/moy_gfx"
if [ -d "${MOY_GFX_SRC}" ]; then
  rm -rf "${MOY_GFX_DST}"
  cp -r "${MOY_GFX_SRC}" "${MOY_GFX_DST}"
  EXT_MOD_CMAKE="${UPSTREAM_DIR}/ext_mod/micropython.cmake"
  if ! grep -q 'moy_gfx/micropython.cmake' "${EXT_MOD_CMAKE}"; then
    sed -i '/moy_alloc\/micropython.cmake/a include(${CMAKE_CURRENT_LIST_DIR}/moy_gfx/micropython.cmake)' "${EXT_MOD_CMAKE}"
  fi
fi

# Stage the Moybyte moy_sd native C module (SD card attached to the display-shared
# SPI host -- see native/moy_sd/modmoy_sd.c) into the upstream ext_mod tree, same
# pattern as moy_gfx (ext_mod is wiped on re-clone, so re-stage every build).
MOY_SD_SRC="${SCRIPT_DIR}/native/moy_sd"
MOY_SD_DST="${UPSTREAM_DIR}/ext_mod/moy_sd"
if [ -d "${MOY_SD_SRC}" ]; then
  rm -rf "${MOY_SD_DST}"
  cp -r "${MOY_SD_SRC}" "${MOY_SD_DST}"
  EXT_MOD_CMAKE="${UPSTREAM_DIR}/ext_mod/micropython.cmake"
  if ! grep -q 'moy_sd/micropython.cmake' "${EXT_MOD_CMAKE}"; then
    sed -i '/moy_gfx\/micropython.cmake/a include(${CMAKE_CURRENT_LIST_DIR}/moy_sd/micropython.cmake)' "${EXT_MOD_CMAKE}"
  fi
fi

# Stage the Moybyte moy_audio native C module (focused PCM mixer for the v0.4
# console -- see native/moy_audio/modmoy_audio.c, #16) into the upstream ext_mod
# tree, same pattern as moy_sd (ext_mod is wiped on re-clone, so re-stage every
# build). DeviceAudio prefers it and falls back to the Python mixer when absent.
MOY_AUDIO_SRC="${SCRIPT_DIR}/native/moy_audio"
MOY_AUDIO_DST="${UPSTREAM_DIR}/ext_mod/moy_audio"
if [ -d "${MOY_AUDIO_SRC}" ]; then
  rm -rf "${MOY_AUDIO_DST}"
  cp -r "${MOY_AUDIO_SRC}" "${MOY_AUDIO_DST}"
  EXT_MOD_CMAKE="${UPSTREAM_DIR}/ext_mod/micropython.cmake"
  if ! grep -q 'moy_audio/micropython.cmake' "${EXT_MOD_CMAKE}"; then
    sed -i '/moy_sd\/micropython.cmake/a include(${CMAKE_CURRENT_LIST_DIR}/moy_audio/micropython.cmake)' "${EXT_MOD_CMAKE}"
  fi
fi

# Stage the shared host/device modules into the frozen modules tree. Canonical
# sources live in runtime/ (imported by the host as runtime.*); the device freezes
# these copies as top-level modules, so both consoles run literally the same code:
#   editors.py    -- CodeEditor / SpriteSheet / PaintEditor
#   audio.py      -- sound model + AudioEngine synth/mixer (#16)
#   console.py    -- launcher + desktop + cards/code/paint UI + Pointer
#   moy_carts.py  -- the .moy store (scan/load/save/create/duplicate/delete)
#   blocks.py     -- block model + blocks->Python compiler (#29; moy_carts imports it)
cp "${REPO_ROOT}/runtime/editors.py" "${SCRIPT_DIR}/modules/editors.py"
cp "${REPO_ROOT}/runtime/audio.py" "${SCRIPT_DIR}/modules/audio.py"
cp "${REPO_ROOT}/runtime/console.py" "${SCRIPT_DIR}/modules/console.py"
cp "${REPO_ROOT}/runtime/moy_carts.py" "${SCRIPT_DIR}/modules/moy_carts.py"
cp "${REPO_ROOT}/runtime/blocks.py" "${SCRIPT_DIR}/modules/blocks.py"
# carts_data.py is GENERATED from system_carts/ (it replaces the ~1800 lines of
# embedded carts moy_runtime used to hand-duplicate) so the device's seed /
# fallback carts can never drift from the host source of truth -- moy_runtime
# does `from carts_data import CARTS`.
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_device_carts.py" "${SCRIPT_DIR}/modules/carts_data.py"

# OTA channel stamp (#53 two-channel): write a tiny generated _ota_build.py that the
# device's moy_ota imports for its release CHANNEL + VERSION + LABEL. The committed
# default is "stable"; a beta build sets MOYBYTE_OTA_CHANNEL=unstable and gets an
# auto-incrementing version (epoch) so each publish reads as newer than the last. We
# also drop dist/current/ota_build.json so gen_ota_manifest stamps a MATCHING manifest
# (same channel/version/label) -- the device offers an install when the manifest's
# channel differs from the running one, or its version is higher within the channel.
OTA_CHANNEL="${MOYBYTE_OTA_CHANNEL:-stable}"
if [ -n "${MOYBYTE_OTA_VERSION:-}" ]; then
  OTA_VERSION="${MOYBYTE_OTA_VERSION}"
elif [ "${OTA_CHANNEL}" = "unstable" ]; then
  OTA_VERSION="$(date +%s)"                       # monotonic per-build version for beta
else
  OTA_VERSION="$(grep -oE 'FIRMWARE_VERSION = [0-9]+' "${SCRIPT_DIR}/modules/moy_ota.py" | head -1 | grep -oE '[0-9]+')"
  OTA_VERSION="${OTA_VERSION:-1}"
fi
if [ "${OTA_CHANNEL}" = "unstable" ]; then
  OTA_LABEL="beta $(date '+%Y-%m-%d %H:%M')"
else
  OTA_LABEL="v${OTA_VERSION}"
fi
cat > "${SCRIPT_DIR}/modules/_ota_build.py" <<EOF
# AUTO-GENERATED by build.sh -- moy_ota imports this for the build's OTA identity
# (MOYBYTE_OTA_CHANNEL / MOYBYTE_OTA_VERSION). Gitignored; do not edit or commit.
CHANNEL = "${OTA_CHANNEL}"
VERSION = ${OTA_VERSION}
LABEL = "${OTA_LABEL}"
EOF
cat > "${CURRENT_DIR}/ota_build.json" <<EOF
{"channel": "${OTA_CHANNEL}", "version": ${OTA_VERSION}, "label": "${OTA_LABEL}"}
EOF
echo "OTA build identity: channel=${OTA_CHANNEL} version=${OTA_VERSION} label='${OTA_LABEL}'"

BUILDER_ESP32="${UPSTREAM_DIR}/builder/esp32.py"
if [ -f "${BUILDER_ESP32}" ]; then
  sed -i \
    -e "s/f'--jobs {os.cpu_count()}'/'--jobs ${BUILD_JOBS}'/g" \
    -e "s/f'-j {os.cpu_count()}'/'-j ${BUILD_JOBS}'/g" \
    "${BUILDER_ESP32}"
  if ! grep -q "MOYBYTE_SKIP_UPSTREAM_SUBMODULES" "${BUILDER_ESP32}"; then
    sed -i "/    update_makefile()/a\\
\\
    if os.environ.get('MOYBYTE_SKIP_UPSTREAM_SUBMODULES', '1') == '1':\\
        print('skipping upstream MicroPython submodules target')\\
        return" "${BUILDER_ESP32}"
  fi
fi

export MAKEFLAGS="${MAKEFLAGS:--j${BUILD_JOBS}}"
export CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-${BUILD_JOBS}}"
export MOYBYTE_SKIP_UPSTREAM_SUBMODULES="${MOYBYTE_SKIP_UPSTREAM_SUBMODULES:-1}"
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
    echo "Unknown MOYBYTE_BOARD_CONFIG=${BOARD_CONFIG}; expected generic or tdeck" >&2
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

# Moybyte #43: patch esp-idf's SPI master so PSRAM TX buffers are DMA'd DIRECTLY (no
# internal MALLOC_CAP_DMA bounce) -- this lets the full-screen LCD flush ship as ONE
# async transfer that overlaps render (moy_compositor PSRAM_DIRECT_FLUSH). Guarded by a
# source marker so it applies exactly once. esp-idf persists in .build; on a fully fresh
# .build it is fetched DURING make.py below, so a from-scratch build needs a second run
# to pick this up (the marker check makes re-running safe / idempotent).
SPI_MASTER_C="${UPSTREAM_DIR}/lib/esp-idf/components/esp_driver_spi/src/gpspi/spi_master.c"
if [ -f "${SPI_MASTER_C}" ] && ! grep -q "Moybyte #43" "${SPI_MASTER_C}"; then
  echo "Moybyte: applying esp-idf PSRAM-TX-DMA patch (#43)"
  patch -d "${UPSTREAM_DIR}/lib/esp-idf" -p1 < "${PATCH_DIR}/spi_master_psram_tx_dma.patch"
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
    echo "Unknown MOYBYTE_REPL=${REPL_MODE}; expected cdc_uart, cdc, jtag, uart, or none" >&2
    exit 1
    ;;
esac

case "${BOARD_CONFIG}" in
  generic)
    # Moybyte OTA (#53): --ota makes the lvgl_micropython builder emit a DUAL-APP
    # partition table (nvs + otadata + phy_init + ota_0 + ota_1 + vfs) instead of a
    # single `factory` app. That is what lets the device flash a new image to the
    # INACTIVE slot from SD and ping-pong between ota_0/ota_1 (esp_ota / esp32.Partition).
    # --partition-size pins BOTH slots at 4MB (the app is ~3.3MB and growing); vfs takes
    # the ~8MB that remains on the 16MB part (carts live on SD, so a smaller internal vfs
    # is fine). Rollback is already on (sdkconfig.base CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE
    # =y): a freshly-flashed app that never calls
    # esp32.Partition.mark_app_valid_cancel_rollback() is auto-reverted by the bootloader
    # on the next boot. The device-side updater is modules/moy_ota.py; see the README OTA
    # section. NOTE: switching to OTA changes the partition layout, so the first flash of
    # this build MUST be a full-image USB flash (make firmware-flash-...-full-erase) --
    # an app-only reflash over the old single-factory layout will not boot.
    BUILD_COMMAND=(
      "${BUILD_PYTHON}" make.py esp32
      BOARD=ESP32_GENERIC_S3
      BOARD_VARIANT=SPIRAM_OCT
      DISPLAY=st7789
      FROZEN_MANIFEST="${MANIFEST}"
      --flash-size=16
      --partition-size=4194304
      --ota
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

# Moybyte OTA (#53): with --ota the bootable app partition is `ota_0`, which no longer
# sits at the legacy 0x10000 -- otadata is inserted before it, shifting it up (0x20000 on
# our 16MB table). Derive the real ota_0 offset from the generated partition table so the
# merged full image lands the app in the slot the bootloader will actually boot.
# Falls back to `factory` then 0x10000 so a non-OTA build still merges correctly.
APP_OFFSET="0x10000"
GEN_PARTITIONS="${UPSTREAM_DIR}/build/partitions.csv"
if [ -f "${GEN_PARTITIONS}" ]; then
  echo "Generated partition table (${GEN_PARTITIONS}):"
  cat "${GEN_PARTITIONS}"
  _app_off="$(awk -F',' '/^[[:space:]]*ota_0[[:space:]]*,/ { gsub(/[[:space:]]/, "", $4); print $4; exit }' "${GEN_PARTITIONS}")"
  if [ -z "${_app_off}" ]; then
    _app_off="$(awk -F',' '/^[[:space:]]*factory[[:space:]]*,/ { gsub(/[[:space:]]/, "", $4); print $4; exit }' "${GEN_PARTITIONS}")"
  fi
  if [ -n "${_app_off}" ]; then
    APP_OFFSET="${_app_off}"
  fi
fi
echo "Merging full image with app (ota_0) offset ${APP_OFFSET}"

"${ESPTOOL_PY}" -m esptool --chip esp32s3 merge_bin \
  -o "${FULL_DIO_BIN}" \
  --flash_mode dio \
  --flash_size 16MB \
  --flash_freq 80m \
  0x0 "${BOOTLOADER_BIN}" \
  0x8000 "${PARTITION_BIN}" \
  "${APP_OFFSET}" "${MPY_BUILD_DIR}/micropython.bin"
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
  "${APP_OFFSET}" "${MPY_BUILD_DIR}/micropython.bin"
echo "Wrote full flash image: ${FULL_QIO_BIN}"
cp "${FULL_QIO_BIN}" "${CURRENT_FULL_QIO_BIN}"
echo "Updated current full QIO alias: ${CURRENT_FULL_QIO_BIN}"
