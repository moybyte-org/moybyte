#!/usr/bin/env bash
# Moybyte T-Deck on MAINLINE MicroPython -- one build strategy for both boards.
#
# The shipping T-Deck target (firmware/lilygo_t_deck_plus_micropython/) builds on
# the lvgl_micropython FORK: it clones the fork, stages our native modules into
# its ext_mod tree, drives its make.py wrapper, and edits a dozen of its files by
# sed. This target builds the SAME console the way the P4 does -- mainline
# MicroPython + an out-of-tree board definition + USER_C_MODULES -- so the two
# boards stop being two different build systems.
#
# THIS SCRIPT NEVER TOUCHES THE SHIPPING TARGET. It reads two things from it (a
# read is not a write): the shared native modules under native/, which stay
# single-sourced there, and two .patch files that are board-agnostic.
#
# The bring-up runs in six stages (panel, touch, keyboard, SD, audio, console);
# `modules/moybyte_shell.py` picks which one boots. Build -> flash -> look:
#
#   ./build.sh
#   make firmware-flash-tdeck-mainline PORT=/dev/ttyACM0
#
# There is no BOOT button on a T-Deck: the trackball CLICK is GPIO0, so hold the
# trackball in while powering the board on, then release, to reach the ROM
# loader. esptool's auto-reset does not sync over this board's native USB.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${SCRIPT_DIR}/.build"
MPY_DIR="${BUILD_DIR}/micropython"
MPY_TAG="${MPY_TAG:-v1.28.0}"
BOARD="MOYBYTE_TDECK"
BOARD_DIR="${SCRIPT_DIR}/boards/${BOARD}"
DIST_DIR="${REPO_ROOT}/dist/tdeck_mainline"
FORK_DIR="${REPO_ROOT}/firmware/lilygo_t_deck_plus_micropython"
PATCH_DIR="${FORK_DIR}/patches"
MODULES_DIR="${SCRIPT_DIR}/modules"
STAGED_NATIVE="${SCRIPT_DIR}/native/.staged"
MANIFEST="${BUILD_DIR}/moybyte_tdeck_manifest.py"
BUILD_JOBS="${MOYBYTE_BUILD_JOBS:-$(nproc)}"

# tools/board_config.py is stdlib-only ON PURPOSE (see its docstring): a board
# must be buildable on nothing but the system python3, without `make setup`.
# The venv is preferred when it is there so the stager runs the same
# interpreter the tests do.
BUILD_PYTHON="${MOYBYTE_BUILD_PYTHON:-}"
if [ -z "${BUILD_PYTHON}" ]; then
  if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
    BUILD_PYTHON="${REPO_ROOT}/.venv/bin/python"
  else
    BUILD_PYTHON="python3"
  fi
fi

mkdir -p "${BUILD_DIR}" "${DIST_DIR}"

# ---------------------------------------------------------------------------
# 1) MicroPython checkout (pinned tag) -- our OWN, not the P4's.
#    Sharing one checkout would mean two boards racing the same sed-patched
#    esp32_common.cmake and the same build-<BOARD>/ sdkconfig guards.
# ---------------------------------------------------------------------------
if [ ! -d "${MPY_DIR}" ]; then
  echo "== cloning micropython ${MPY_TAG}"
  git clone --depth 1 -b "${MPY_TAG}" https://github.com/micropython/micropython "${MPY_DIR}"
fi

# ---------------------------------------------------------------------------
# 2) ESP-IDF v5.5.1. Reuse a checkout that already exists (they are ~500MB and
#    the same version): an explicit IDF_DIR wins, then the P4 build's, then the
#    fork build's, then clone our own.
# ---------------------------------------------------------------------------
if [ -z "${IDF_DIR:-}" ]; then
  for _cand in \
      "${REPO_ROOT}/firmware/esp32_p4_wifi6_touch_lcd_7b/.build/esp-idf" \
      "${FORK_DIR}/.build/lvgl_micropython/lib/esp-idf"; do
    if [ -f "${_cand}/export.sh" ]; then IDF_DIR="${_cand}"; break; fi
  done
fi
if [ -z "${IDF_DIR:-}" ]; then
  IDF_DIR="${BUILD_DIR}/esp-idf"
  if [ ! -f "${IDF_DIR}/export.sh" ]; then
    echo "== cloning esp-idf v5.5.1"
    git clone --depth 1 -b v5.5.1 --recursive --shallow-submodules \
      https://github.com/espressif/esp-idf "${IDF_DIR}"
  fi
fi
echo "== using ESP-IDF at ${IDF_DIR}"
# export.sh cannot be trusted to REPORT a missing toolchain -- v5.5.1 ends in an
# unconditional `return 0` and only its inner activate.py fails -- so probe the
# real outcome (idf.py on PATH) and self-heal with the official installer.
set +u
# shellcheck disable=SC1091
source "${IDF_DIR}/export.sh" >/dev/null 2>&1 || true
if ! command -v idf.py >/dev/null 2>&1; then
  echo "== ESP-IDF tools missing: running install.sh esp32s3"
  "${IDF_DIR}/install.sh" esp32s3
  # shellcheck disable=SC1091
  source "${IDF_DIR}/export.sh" >/dev/null
  command -v idf.py >/dev/null 2>&1 || { echo "!! idf.py still missing after install.sh" >&2; exit 1; }
fi
set -u

# ---------------------------------------------------------------------------
# 3) The three fork patches, re-solved on mainline. All marker-guarded, because
#    both .build trees persist across builds.
# ---------------------------------------------------------------------------
COMMON_CMAKE="${MPY_DIR}/ports/esp32/esp32_common.cmake"
MPCONFIGPORT_H="${MPY_DIR}/ports/esp32/mpconfigport.h"
MACHINE_I2C_C="${MPY_DIR}/ports/esp32/machine_i2c.c"

# 3a) moy_lcd needs esp_lcd in the main component's REQUIRES. USER_C_MODULES is
#     skipped during idf.py's early-expansion phase -- exactly when REQUIRES are
#     collected -- so appending IDF_COMPONENTS from the usermod cmake can never
#     work. Patch the port's list, same as the P4 build does for esp_lcd/PPA.
if ! grep -q "^    esp_lcd$" "${COMMON_CMAKE}"; then
  sed -i '/^list(APPEND IDF_COMPONENTS$/a\    esp_lcd' "${COMMON_CMAKE}"
  echo "== patched esp32_common.cmake: added esp_lcd to IDF_COMPONENTS"
fi

# 3b) PATCH 1 of 3 -- REPR_C unboxed floats (#66).
#     Kid float-physics carts otherwise allocate a 16B heap box per float RESULT
#     (~73KB/frame measured in sakura), and the heap-wrap gc collect that follows
#     is a 130-175ms visible hitch. The fork ships this as a context diff against
#     ports/esp32/mpconfigport.h; here it is a guarded sed on the same line,
#     which survives the line moving between MicroPython releases.
if ! grep -q "MICROPY_OBJ_REPR_C" "${MPCONFIGPORT_H}"; then
  sed -i 's|^#define MICROPY_OBJ_REPR  *(MICROPY_OBJ_REPR_A)|#define MICROPY_OBJ_REPR                    (MICROPY_OBJ_REPR_C) /* Moybyte #66: unboxed 30-bit floats */|' \
    "${MPCONFIGPORT_H}"
  grep -q "MICROPY_OBJ_REPR_C" "${MPCONFIGPORT_H}" || {
    echo "!! REPR_C patch did not apply -- mpconfigport.h's MICROPY_OBJ_REPR line changed shape" >&2
    exit 1
  }
  echo "== patched mpconfigport.h: MICROPY_OBJ_REPR_C (#66)"
fi

# 3c) PATCH 2 of 3 -- release the GIL across machine.I2C's blocking wait (#69).
#     This is what makes the input POLLER THREAD work: a T-Deck keyboard C3
#     clock-stretch stall (40-60ms, I2CSTAT-sized on hardware) then blocks only
#     the poller thread while the VM keeps rendering. Without it the stall holds
#     the GIL and freezes the whole loop no matter which thread reads.
#     Only pure IDF code runs unlocked -- the same pattern this port already uses
#     around its SPI/UART blocking waits.
if [ -f "${MACHINE_I2C_C}" ] && ! grep -q "Moybyte #69 GIL" "${MACHINE_I2C_C}"; then
  python3 - "${MACHINE_I2C_C}" <<'PY'
import sys
path = sys.argv[1]
src = open(path).read()
needle = "    esp_err_t err = i2c_master_cmd_begin("
i = src.find(needle)
if i < 0:
    sys.exit("machine_i2c.c: i2c_master_cmd_begin call not found -- re-solve #69 by hand")
end = src.index("\n", i) + 1
src = (src[:i]
       + "    // Moybyte #69 GIL: release the GIL across the blocking transaction\n"
         "    // wait so a slave clock-stretch stall (the T-Deck keyboard C3 stretches\n"
         "    // up to ~50ms per read) blocks only the CALLING Python thread (the input\n"
         "    // poller), never the whole VM / render loop. Only pure IDF code runs\n"
         "    // while unlocked -- no MP state is touched, the driver serializes per\n"
         "    // port internally, and the caller's buffers are non-moving MP heap the\n"
         "    // calling thread keeps alive.\n"
         "    MP_THREAD_GIL_EXIT();\n"
       + src[i:end]
       + "    MP_THREAD_GIL_ENTER();\n"
       + src[end:])
open(path, "w").write(src)
PY
  echo "== patched machine_i2c.c: GIL release around i2c_master_cmd_begin (#69)"
fi

# 3d) PATCH 3 of 3 -- esp_lcd tx_color no-acquire (#66), applied to the IDF tree.
#     A continuation colour write (lcd_cmd < 0) is queue-only, but esp_lcd still
#     calls spi_device_acquire_bus first, which waits for the device's in-flight
#     transactions -- so every banded flush blocks on the previous band's DMA.
#     Confirmed still present on IDF master: no esp_lcd_spi_flags_t controls it.
#
#     STAGE 1 DOES NOT DEPEND ON THIS. moy_lcd.show() fences on its own
#     completion counter, so with the patch absent it is merely serialized, and
#     with it present it is ready to overlap. It is applied here because the
#     patch file exists and is idempotent, and because the fork build applies it
#     to the same shared IDF checkout anyway.
ESP_LCD_SPI_C="${IDF_DIR}/components/esp_lcd/spi/esp_lcd_panel_io_spi.c"
if [ -f "${ESP_LCD_SPI_C}" ] && ! grep -q "Moybyte #66" "${ESP_LCD_SPI_C}"; then
  echo "== applying esp_lcd tx_color no-acquire patch (#66)"
  patch -d "${IDF_DIR}" -p1 < "${PATCH_DIR}/esp_lcd_tx_color_noacquire.patch"
fi

# 3e) Not one of the three, but the same class and the P4 build already reuses
#     it: un-static esp_native_code_free_all so a cart-compile miss can reclaim
#     the @micropython.native exec arena (MALLOC_CAP_EXEC IRAM, otherwise
#     grow-only until soft reset). moy_gfx binds it as a weak symbol, so the
#     module compiles with or without this -- but the repeat-run cliff is real.
if ! grep -q "moybyte_native_code_free" "${MPCONFIGPORT_H}"; then
  echo "== applying native-code-free patch (#66)"
  patch -d "${MPY_DIR}" -p1 < "${PATCH_DIR}/esp32_native_code_free.patch"
fi

# ---------------------------------------------------------------------------
# 4) Stage the SHARED native modules. Their single source of truth stays
#    firmware/lilygo_t_deck_plus_micropython/native/ -- this build reads that
#    tree and never writes to it. native/.staged/ is gitignored.
#
#    Stages 1-3 need two: moy_gfx (the RGB565 pixel kernel every draw verb runs
#    through, plus the vendored libmoy raster under it) and moy_alloc (the
#    off-gc-heap DMA allocator). moy_sd / moy_audio / moy_lua / moycore / moy_web
#    land with their own stages.
# ---------------------------------------------------------------------------
SHARED_NATIVE="${MOYBYTE_SHARED_NATIVE:-moy_gfx moy_alloc}"
rm -rf "${STAGED_NATIVE}"
mkdir -p "${STAGED_NATIVE}"
{
  echo "# AUTO-GENERATED by build.sh -- the shared native modules staged this build."
  for m in ${SHARED_NATIVE}; do
    if [ ! -d "${FORK_DIR}/native/${m}" ]; then
      echo "!! shared native module ${m} not found in ${FORK_DIR}/native" >&2
      exit 1
    fi
    cp -r "${FORK_DIR}/native/${m}" "${STAGED_NATIVE}/${m}"
    echo "include(\${CMAKE_CURRENT_LIST_DIR}/${m}/micropython.cmake)"
  done
} > "${STAGED_NATIVE}/micropython.cmake"
echo "== staged shared native modules: ${SHARED_NATIVE}"

# ---------------------------------------------------------------------------
# 4b) Stage the shared PYTHON modules (#161 Phase 3). WHAT crosses and WHY is
#     declared in board.toml, not here -- there is no `cp runtime/*.py` list in
#     this file and there must never be one again. Two sources, two deliberately
#     different strategies, both spelled out there:
#
#       runtime/          -- DENYLIST. A shared tree's default answer is "yes,
#                            this crosses", so what needs writing down is the
#                            exclusions, with a reason on each. Identical to the
#                            fork build's list, because it is the same board and
#                            the same 320x240 tier.
#       the fork's tree   -- ALLOWLIST, and it stays one. That is a BOARD tree
#                            whose default answer is "no": three of its modules
#                            drive lcd_bus/lvgl and two are that build's own boot
#                            spine, which would SHADOW this one's.
#
#     The stager also PRUNES untracked strays it did not just stage: the frozen
#     manifest freezes the whole modules/ DIRECTORY and this one is gitignored,
#     so an unstaged module would otherwise stay in the image forever.
#
#     runtime/font.py crosses as `moy_font.py` (a declared rename): the moy_gfx
#     text kernel rasterizes petme128 from its blob, so host and device text are
#     the same pixels (#62).
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" stage "${SCRIPT_DIR}"

rm -rf "${MODULES_DIR}/__pycache__" "${MODULES_DIR}/moybyte/__pycache__"

# ---------------------------------------------------------------------------
# 5) Frozen manifest: the port's default frozen stdlib + this board's modules.
#    The md5 fingerprint makes the manifest CONTENT change whenever any frozen
#    source changes -- ninja rests custom commands on identical manifest text,
#    so without it a changed .py silently ships as stale .mpy.
# ---------------------------------------------------------------------------
cat > "${MANIFEST}" <<EOF
include("\$(PORT_DIR)/boards/manifest.py")
freeze("${MODULES_DIR}", opt=3)
EOF
echo "# frozen-source fingerprint: $(find "${MODULES_DIR}" -type f -name '*.py' -exec md5sum {} + 2>/dev/null | sort | md5sum | cut -d' ' -f1)" >> "${MANIFEST}"

# ---------------------------------------------------------------------------
# 6) Partition table + the stale-sdkconfig guard.
#    CONFIG_PARTITION_TABLE_CUSTOM_FILENAME resolves relative to ports/esp32, so
#    the board CSV is staged there. IDF only (re)generates a build's sdkconfig
#    from the defaults when the file is ABSENT -- editing sdkconfig.board does
#    NOT propagate into an existing build dir, which is how the fork build once
#    silently kept the small caches. Delete the generated file when it is
#    missing any override this board cannot be built without.
# ---------------------------------------------------------------------------
cp "${BOARD_DIR}/partitions-moybyte-tdeck.csv" "${MPY_DIR}/ports/esp32/partitions-moybyte-tdeck.csv"
GEN_SDKCONFIG="${MPY_DIR}/ports/esp32/build-${BOARD}/sdkconfig"
if [ -f "${GEN_SDKCONFIG}" ]; then
  for opt in \
    'CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions-moybyte-tdeck.csv"' \
    'CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y' \
    'CONFIG_ESP32S3_INSTRUCTION_CACHE_32KB=y' \
    'CONFIG_ESP32S3_DATA_CACHE_64KB=y' \
    'CONFIG_ESP32S3_DATA_CACHE_LINE_32B=y' \
    'CONFIG_SPIRAM_MODE_OCT=y' \
    'CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y'; do
    if ! grep -qF "${opt}" "${GEN_SDKCONFIG}"; then
      echo "== sdkconfig lacks ${opt} -- forcing regeneration"
      rm -f "${GEN_SDKCONFIG}"
      break
    fi
  done
fi

# ---------------------------------------------------------------------------
# 7) Build.
# ---------------------------------------------------------------------------
make -C "${MPY_DIR}/mpy-cross" -j"${BUILD_JOBS}"

cd "${MPY_DIR}/ports/esp32"
make submodules BOARD_DIR="${BOARD_DIR}"
make -j"${BUILD_JOBS}" BOARD_DIR="${BOARD_DIR}" \
  USER_C_MODULES="${SCRIPT_DIR}/native/micropython.cmake" \
  FROZEN_MANIFEST="${MANIFEST}"

# ---------------------------------------------------------------------------
# 8) Collect images + the #168 size guard.
#    firmware.bin is bootloader + table + app merged for a cable flash at 0x0
#    (the S3's bootloader offset; the P4's is 0x2000). micropython.bin is the
#    APP partition image -- what an OTA writes into the inactive slot. Handing
#    firmware.bin to esp32.Partition would write a bootloader into an app slot.
# ---------------------------------------------------------------------------
BOUT="build-${BOARD}"
cp "${BOUT}/firmware.bin" "${DIST_DIR}/moybyte_tdeck.bin"
cp "${BOUT}/micropython.bin" "${DIST_DIR}/moybyte_tdeck_app.bin"

APP_SLOT_BYTES="${MOYBYTE_APP_SLOT_BYTES:-5242880}"
APP_HEADROOM_WARN_BYTES="${MOYBYTE_APP_HEADROOM_WARN_BYTES:-204800}"
APP_SIZE_BYTES="$(wc -c < "${DIST_DIR}/moybyte_tdeck_app.bin" | tr -d '[:space:]')"
APP_HEADROOM_BYTES=$(( APP_SLOT_BYTES - APP_SIZE_BYTES ))
printf 'App image: %s bytes of a %s-byte ota_0 slot -- %s bytes headroom (%s KB)\n' \
  "${APP_SIZE_BYTES}" "${APP_SLOT_BYTES}" "${APP_HEADROOM_BYTES}" "$(( APP_HEADROOM_BYTES / 1024 ))"
if [ "${APP_HEADROOM_BYTES}" -lt 0 ]; then
  echo "Moybyte WARNING (#168): the app image does NOT fit the ${APP_SLOT_BYTES}-byte OTA slot" >&2
elif [ "${APP_HEADROOM_BYTES}" -lt "${APP_HEADROOM_WARN_BYTES}" ]; then
  echo "Moybyte WARNING (#168): under $(( APP_HEADROOM_WARN_BYTES / 1024 ))KB of OTA-slot headroom left" >&2
fi

echo "OK -> ${DIST_DIR}/moybyte_tdeck.bin (full image, cable flash at 0x0)"
echo "OK -> ${DIST_DIR}/moybyte_tdeck_app.bin (OTA payload, app partition)"
