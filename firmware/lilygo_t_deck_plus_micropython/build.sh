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

# lvgl_micropython vendors micropython/lvgl/pycparser as git submodules that its
# make.py populates on the fly (builder.get_micropython -> `git submodule update
# --init --depth=1 -- lib/micropython`). But we patch files UNDER lib/micropython
# (main.c / _boot.py) *before* make.py runs, so on a from-scratch .build (CI, or a
# fresh dev checkout) that path doesn't exist yet and the patch/cp below fail. Init
# the micropython submodule now -- faithful to get_micropython, and a no-op once it
# is present (lvgl/pycparser/esp-idf are still fetched later by make.py).
if [ ! -f "${UPSTREAM_DIR}/lib/micropython/ports/esp32/main.c" ]; then
  git -C "${UPSTREAM_DIR}" submodule update --init --depth=1 -- lib/micropython
fi
# micropython has its OWN nested submodules; the esp32 port needs berkeley-db
# (MICROPY_PY_BTREE) and micropython-lib (the frozen manifest). We disable
# lvgl_micropython's own `make submodules` above (MOYBYTE_SKIP_UPSTREAM_SUBMODULES),
# which assumes a warm .build, so init them ourselves -- faithful to micropython's
# `make submodules`, a no-op once present (fixes a from-scratch build: CI / fresh dev).
if [ ! -e "${UPSTREAM_DIR}/lib/micropython/lib/berkeley-db-1.xx/btree/bt_open.c" ]; then
  git -C "${UPSTREAM_DIR}/lib/micropython" submodule update --init --depth=1 \
    lib/berkeley-db-1.xx lib/micropython-lib
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

# Stage the Moybyte moy_audio native C module into the upstream ext_mod tree,
# same pattern as moy_sd (ext_mod is wiped on re-clone, so re-stage every build).
# This is SPEC.md 8: native/moy_audio/libmoy/ is moy-spec's own C synth, vendored
# verbatim and compiled in (#97), with modmoy_audio.c the MicroPython binding
# over it -- so the cp must stay RECURSIVE, exactly like moy_lua's vendored lua/.
# DeviceAudio prefers it and falls back to the Python twin when absent.
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

# Stage the Moybyte moy_lua native C module (#67 Phase 1: vendored Lua 5.4 +
# the cart bridge -- see native/moy_lua/modmoy_lua.c) into the upstream ext_mod
# tree, same pattern as moy_audio (ext_mod is wiped on re-clone, so re-stage
# every build). moy_runtime injects the Lua cart runtime only when this module
# imports, so a build without it still boots (lua carts panel gracefully).
MOY_LUA_SRC="${SCRIPT_DIR}/native/moy_lua"
MOY_LUA_DST="${UPSTREAM_DIR}/ext_mod/moy_lua"
EXT_MOD_CMAKE="${UPSTREAM_DIR}/ext_mod/micropython.cmake"
if [ "${MOYBYTE_NO_LUA:-0}" = "1" ]; then
  # A/B knob (2026-08-03, #77 icache theory): build WITHOUT the Lua VM to
  # measure what ~1MB of vendored VM code costs the shared flash cache during
  # PYTHON-cart play. Lua carts open the runtime-missing panel on this build.
  # Must also UNDO a previous build's staging (ext_mod persists across builds).
  rm -rf "${MOY_LUA_DST}"
  sed -i '/moy_lua\/micropython.cmake/d' "${EXT_MOD_CMAKE}" 2>/dev/null || true
  echo "MOYBYTE_NO_LUA=1: moy_lua NOT staged (A/B build, lua carts will panel)"
elif [ -d "${MOY_LUA_SRC}" ]; then
  rm -rf "${MOY_LUA_DST}"
  cp -r "${MOY_LUA_SRC}" "${MOY_LUA_DST}"
  if ! grep -q 'moy_lua/micropython.cmake' "${EXT_MOD_CMAKE}"; then
    sed -i '/moy_audio\/micropython.cmake/a include(${CMAKE_CURRENT_LIST_DIR}/moy_lua/micropython.cmake)' "${EXT_MOD_CMAKE}"
  fi
fi

# Stage moycore (plan stage 3: the cart's whole frame in C) beside it. It needs
# BOTH moy_gfx and moy_lua staged -- it compiles neither a raster nor a VM and
# reaches theirs by sibling include path -- so it is staged last and skipped
# entirely on the MOYBYTE_NO_LUA A/B build, where there is no VM to bind.
MOYCORE_SRC="${SCRIPT_DIR}/native/moycore"
MOYCORE_DST="${UPSTREAM_DIR}/ext_mod/moycore"
if [ "${MOYBYTE_NO_LUA:-0}" = "1" ] || [ ! -d "${MOYCORE_SRC}" ]; then
  rm -rf "${MOYCORE_DST}"
  sed -i '/moycore\/micropython.cmake/d' "${EXT_MOD_CMAKE}" 2>/dev/null || true
else
  rm -rf "${MOYCORE_DST}"
  cp -r "${MOYCORE_SRC}" "${MOYCORE_DST}"
  if ! grep -q 'moycore/micropython.cmake' "${EXT_MOD_CMAKE}"; then
    sed -i '/moy_lua\/micropython.cmake/a include(${CMAKE_CURRENT_LIST_DIR}/moycore/micropython.cmake)' "${EXT_MOD_CMAKE}"
  fi
fi

# Bake the BROWSER CONSOLE into the image (moy_web). The board serves the wasm
# console over WiFi (moy_webhost); until 2026-08-15 it could only serve a copy
# somebody had put on the SD card, which drifts silently -- and THIS board
# cannot be pushed to at all, because the push tool talks to the board over
# serial and this fork's USB-CDC RX is dead under the desktop. So the image
# carries the bundle: tools/gen_web_blob.py .incbin's the four PRE-GZIPPED
# assets (572,693 B; raw would be 1,155,953 B and would not fit this slot) into
# a generated C file, and moy_webhost falls back to it when storage has none.
# A pushed copy still WINS, so the fast dev loop survives.
#
# With no bundle built the generator emits an EMPTY table and shouts; under CI
# (or MOYBYTE_REQUIRE_WEB_BUNDLE=1) it fails the build instead, because a
# PUBLISHED image with no console is the drift this replaced.
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_web_blob.py" \
  --out "${SCRIPT_DIR}/native/moy_web/moy_web_blob.gen.c"
MOY_WEB_SRC="${SCRIPT_DIR}/native/moy_web"
MOY_WEB_DST="${UPSTREAM_DIR}/ext_mod/moy_web"
if [ -d "${MOY_WEB_SRC}" ]; then
  rm -rf "${MOY_WEB_DST}"
  cp -r "${MOY_WEB_SRC}" "${MOY_WEB_DST}"
  if ! grep -q 'moy_web/micropython.cmake' "${EXT_MOD_CMAKE}"; then
    sed -i '/moy_sd\/micropython.cmake/a include(${CMAKE_CURRENT_LIST_DIR}/moy_web/micropython.cmake)' "${EXT_MOD_CMAKE}"
  fi
fi

# Stage the shared host/device modules into the frozen modules tree (#161
# Phase 3). WHAT crosses and WHY now lives in board.toml, not here: the stager
# copies every runtime/*.py EXCEPT the files that board file denies, each with
# its reason written beside it, and applies the one rename (font.py ->
# moy_font.py, the name device_canvas imports for the native text kernel).
# Canonical sources live in runtime/ (the host imports them as runtime.*); the
# device freezes these copies as top-level modules, so both consoles run
# literally the same code.
#
# This was ~70 hand-written `cp` lines until 2026-08-15. An allowlist asks "did
# somebody remember to add this module?", and when the answer is no nothing
# says so: every consumer in this tree is capability-gated, so a module that is
# not staged reads as a FEATURE THAT DOES NOT EXIST (which is how this board
# went without the web console while the P4 had it). A denylist asks "is there
# a reason this must not cross?" -- a question with a checkable answer.
#
# The stager also PRUNES. The generated manifest freezes the whole modules/
# DIRECTORY, not the list this script copied, and modules/ is gitignored and
# never cleaned -- so a file that stops being staged keeps being frozen forever
# on every tree that has built before. It removes untracked strays it did not
# just stage (board.toml's `keep` covers the ones generated below) and says so.
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" stage "${SCRIPT_DIR}"
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
  # The human release name (FIRMWARE_NAME, set by `make release NAME=`), NOT the
  # ordering counter -- "0.6" is what the update screen and the manifest show.
  OTA_NAME="$(grep -oE '^FIRMWARE_NAME = "[^"]*"' "${SCRIPT_DIR}/modules/moy_ota.py" | head -1 | cut -d'"' -f2)"
  OTA_LABEL="${OTA_NAME:-v${OTA_VERSION}}"
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

# Perf bench stamp (#63): MOYBYTE_BENCH=1 bakes an image that boots into
# moy_runtime.run_perf_bench (prints BENCH lines, then RETURNS to the REPL --
# USB-safe on a headless bench board) instead of the desktop takeover. The stamp
# is removed on every normal build so user images can never ship with it.
rm -f "${SCRIPT_DIR}/modules/_moy_bench.py"
if [ "${MOYBYTE_BENCH:-0}" = "1" ]; then
  cat > "${SCRIPT_DIR}/modules/_moy_bench.py" <<EOF
# AUTO-GENERATED by build.sh (MOYBYTE_BENCH=1) -- boots the perf bench, not the
# desktop. Gitignored; never ship this in a user image.
BENCH = True
EOF
  echo "PERF BENCH build: boots run_perf_bench (self-terminating)"
fi

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

# Cache geometry (#63, the kid-logic interpreter lever): the default build ran the
# S3's MINIMUM caches (16KB icache, 32KB dcache) while the VM reads frozen
# bytecode from flash AND the gc heap from PSRAM through that one small dcache --
# with the LCD DMA streaming 153KB/frame through the same path. Measured cost:
# the same kid float loop runs ~13.5ms on clean silicon vs 30-43ms here (~2.5x).
# Double both caches. Costs 48KB of internal SRAM (16 icache + 32 dcache) --
# verify WiFi/web-view still fits internal RAM on the hardware pass; if NO_MEM,
# drop the icache bump first (dcache is the bigger lever).
#
# The cache LINE stays 32B: the first pass also widened lines to 64B and the
# hardware answered with horizontal garbage bands on EVERY screen (desktop,
# carts, loading) -- the 64B coherency granularity breaks the CPU<->GDMA
# handoff somewhere in the PSRAM panel-flush path (observed 2026-07-03, fps
# gains were intact; only the pixels were wrong). Do not re-widen without an
# on-hardware A/B.
#
# MOYBYTE_CACHE_GEOMETRY=stock builds the upstream default geometry (16/32KB)
# for bisects. The options land in the port-wide sdkconfig.base (the same file
# the upstream builder itself appends to), so they apply to BOTH board configs
# (generic + tdeck). Previously-appended cache lines are stripped first, so a
# geometry change can never leave two conflicting choice symbols in the
# defaults (kconfig would silently pick one).
CACHE_GEOMETRY="${MOYBYTE_CACHE_GEOMETRY:-fast}"
SDKCONFIG_BASE="${UPSTREAM_DIR}/lib/micropython/ports/esp32/boards/sdkconfig.base"
sed -i \
  -e '/^CONFIG_ESP32S3_INSTRUCTION_CACHE_32KB=y$/d' \
  -e '/^CONFIG_ESP32S3_DATA_CACHE_64KB=y$/d' \
  -e '/^CONFIG_ESP32S3_DATA_CACHE_LINE_64B=y$/d' \
  -e '/^CONFIG_ESP32S3_DATA_CACHE_LINE_32B=y$/d' \
  "${SDKCONFIG_BASE}"
if [ "${CACHE_GEOMETRY}" = "fast" ]; then
  for opt in \
    'CONFIG_ESP32S3_INSTRUCTION_CACHE_32KB=y' \
    'CONFIG_ESP32S3_DATA_CACHE_64KB=y' \
    'CONFIG_ESP32S3_DATA_CACHE_LINE_32B=y'; do
    printf '%s\n' "${opt}" >> "${SDKCONFIG_BASE}"
  done
fi

# IRAM diet (#66/#67, 2026-08-10): move FreeRTOS/ringbuf/heap code out of IRAM
# into flash. The census measured .iram0.text at 124KB of the S3's 512KB SRAM
# while the internal HEAP was 269KB total with celeste's Lua heap winning just
# 9KB of it (97% PSRAM, the measured-2x-slower regime) -- these three flags are
# the movable slice (~13-20KB back to the heap, which the SRAM-first Lua
# allocator takes automatically). The cost is those calls running through the
# 32KB icache like everything else. Same strip+append+guard pattern as the
# cache geometry above.
sed -i \
  -e '/^CONFIG_FREERTOS_PLACE_FUNCTIONS_INTO_FLASH=y$/d' \
  -e '/^CONFIG_RINGBUF_PLACE_FUNCTIONS_INTO_FLASH=y$/d' \
  -e '/^CONFIG_HEAP_PLACE_FUNCTION_INTO_FLASH=y$/d' \
  "${SDKCONFIG_BASE}"
for opt in \
  'CONFIG_FREERTOS_PLACE_FUNCTIONS_INTO_FLASH=y' \
  'CONFIG_RINGBUF_PLACE_FUNCTIONS_INTO_FLASH=y' \
  'CONFIG_HEAP_PLACE_FUNCTION_INTO_FLASH=y'; do
  printf '%s\n' "${opt}" >> "${SDKCONFIG_BASE}"
done

# External MSPI speed (#66, T-Deck S3): run octal flash + octal PSRAM at 120MHz.
# PSRAM 120 selects a 240MHz MSPI timing tuple; leaving flash at 80MHz hit an
# unsupported ESP-IDF 240/80 timing-table path, so the tested build moves both
# memories together.
#
# #169: 120MHz octal is an EXPERIMENTAL IDF feature whose documented failure mode is
# random PSRAM/flash faults once the die drifts ~20C from its boot temperature -- an
# ordinary day for a handheld (indoors -> outdoors, pocket, a long self-heating play
# session), presenting as unexplained instability with no reproducer. The mitigation
# is IDF's temperature retune (a task re-picks the PSRAM timing point from the on-chip
# sensor), which the FIRST attempt could not use: IDF only starts it for verified flash
# vendor IDs (0xC8/0x20) and returns ESP_ERR_NOT_SUPPORTED otherwise -- from a SECONDARY
# ESP_SYSTEM_INIT_FN, i.e. it aborts the boot. patches/esp_psram_temp_retune_any_vendor.patch
# relaxes that gate (warn + run) and turns the task's other brick path -- an abort() when
# the scanned points share no temperature range -- into "stop adjusting", which degrades
# to exactly the un-mitigated build. So `fast` now ships the retune ON.
#   MOYBYTE_EXTMEM_SPEED=fast_notemp  120MHz with the retune OFF (the pre-#169 build --
#                                     the A/B if the retune is ever suspected)
#   MOYBYTE_EXTMEM_SPEED=stock        upstream 80/80, no experimental features (the
#                                     board-stability bisect / #169's fallback option 2)
EXTMEM_SPEED="${MOYBYTE_EXTMEM_SPEED:-fast}"
SDKCONFIG_SPIRAM_OCT="${UPSTREAM_DIR}/lib/micropython/ports/esp32/boards/sdkconfig.spiram_oct"
sed -i \
  -e '/^CONFIG_IDF_EXPERIMENTAL_FEATURES=y$/d' \
  -e '/^CONFIG_ESPTOOLPY_FLASHFREQ_80M=$/d' \
  -e '/^CONFIG_ESPTOOLPY_FLASHFREQ_120M=y$/d' \
  -e '/^CONFIG_SPIRAM_SPEED_80M=$/d' \
  -e '/^CONFIG_SPIRAM_SPEED_120M=y$/d' \
  -e '/^CONFIG_SPIRAM_TIMING_TUNING_POINT_VIA_TEMPERATURE_SENSOR=$/d' \
  -e '/^CONFIG_SPIRAM_TIMING_TUNING_POINT_VIA_TEMPERATURE_SENSOR=y$/d' \
  -e '/^CONFIG_SPIRAM_TIMING_MEASURE_TEMPERATURE_INTERVAL_SECOND=/d' \
  "${SDKCONFIG_SPIRAM_OCT}"
case "${EXTMEM_SPEED}" in
  fast|fast_notemp)
    if [ "${EXTMEM_SPEED}" = "fast" ]; then
      TEMP_RETUNE_OPTS=(
        'CONFIG_SPIRAM_TIMING_TUNING_POINT_VIA_TEMPERATURE_SENSOR=y'
        'CONFIG_SPIRAM_TIMING_MEASURE_TEMPERATURE_INTERVAL_SECOND=5'
      )
    else
      TEMP_RETUNE_OPTS=('CONFIG_SPIRAM_TIMING_TUNING_POINT_VIA_TEMPERATURE_SENSOR=')
    fi
    for opt in \
      'CONFIG_IDF_EXPERIMENTAL_FEATURES=y' \
      'CONFIG_ESPTOOLPY_FLASHFREQ_80M=' \
      'CONFIG_ESPTOOLPY_FLASHFREQ_120M=y' \
      'CONFIG_SPIRAM_SPEED_80M=' \
      'CONFIG_SPIRAM_SPEED_120M=y' \
      "${TEMP_RETUNE_OPTS[@]}"; do
      printf '%s\n' "${opt}" >> "${SDKCONFIG_SPIRAM_OCT}"
    done
    ;;
  stock)
    ;;
  *)
    echo "Unknown MOYBYTE_EXTMEM_SPEED=${EXTMEM_SPEED}; expected fast, fast_notemp or stock" >&2
    exit 1
    ;;
esac

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

# Moybyte #66: patch esp_lcd's SPI tx_color so a CONTINUATION color write (cmd < 0)
# skips spi_device_acquire_bus -- acquire waits for the device's in-flight queued
# transactions, so every banded tx_color otherwise BLOCKS until the previous band's
# DMA completes (serializing the flush against the caller). Queue-only continuation
# bands are what let the SRAM-bounce flush (moy_compositor SRAM_BOUNCE_FLUSH) feed
# 15KB internal-DMA bands without ever blocking the pumping thread. Same
# marker-guard pattern as the #43 patch above.
ESP_LCD_SPI_C="${UPSTREAM_DIR}/lib/esp-idf/components/esp_lcd/spi/esp_lcd_panel_io_spi.c"
if [ -f "${ESP_LCD_SPI_C}" ] && ! grep -q "Moybyte #66" "${ESP_LCD_SPI_C}"; then
  echo "Moybyte: applying esp_lcd tx_color no-acquire patch (#66)"
  patch -d "${UPSTREAM_DIR}/lib/esp-idf" -p1 < "${PATCH_DIR}/esp_lcd_tx_color_noacquire.patch"
fi

# Moybyte #169: let the PSRAM temperature retune run on this board. Upstream gates the
# retune task on a verified flash vendor id and FAILS a SECONDARY system-init when it
# doesn't match (= no boot), and abort()s inside the task when the scanned timing points
# share no temperature range. Both become non-fatal here; see the MOYBYTE_EXTMEM_SPEED
# block below for why running 120MHz octal without the retune is the worse risk. Same
# marker-guard pattern as the #43/#66 patches above.
MSPI_TIMING_C="${UPSTREAM_DIR}/lib/esp-idf/components/esp_hw_support/mspi_timing_tuning/port/esp32s3/mspi_timing_by_mspi_delay.c"
if [ -f "${MSPI_TIMING_C}" ] && ! grep -q "Moybyte #169" "${MSPI_TIMING_C}"; then
  echo "Moybyte: applying PSRAM temperature-retune vendor-gate patch (#169)"
  patch -d "${UPSTREAM_DIR}/lib/esp-idf" -p1 < "${PATCH_DIR}/esp_psram_temp_retune_any_vendor.patch"
fi

# Moybyte #66: REPR_C object representation -- floats live UNBOXED in the object
# word (30-bit). Kid float-physics carts (sakura: 120 petals) otherwise allocate
# a 16B heap box per float RESULT (~73KB/frame measured), and the periodic
# heap-wrap gc collect that follows is a 130-175ms visible hitch every ~1s (the
# micro-stutter, root-caused on-hardware 2026-07-04). REPR_C drops the churn to
# ~800B/frame (92x) -> a collect every ~5min. Same engine-side doctrine as the
# spr_gate: the kid's idiomatic code stays; the engine stops punishing it.
# Verified on the XIAO S3 (full console boot + bench + float sanity). The
# esp8266 port shipped REPR_C for years on the same Xtensa line.
MPCONFIGPORT_H="${UPSTREAM_DIR}/lib/micropython/ports/esp32/mpconfigport.h"
if [ -f "${MPCONFIGPORT_H}" ] && ! grep -q "Moybyte #66" "${MPCONFIGPORT_H}"; then
  echo "Moybyte: applying REPR_C unboxed-floats patch (#66)"
  patch -d "${UPSTREAM_DIR}/lib/micropython" -p1 < "${PATCH_DIR}/esp32_repr_c_floats.patch"
fi

# Moybyte #66: un-static esp_native_code_free_all so the cart loader can reclaim
# the @micropython.native exec arena (MALLOC_CAP_EXEC IRAM, otherwise GROW-ONLY
# until soft reset) on a compile miss -- the fix for the repeat-run cliff (the
# arena exhausted after ~5 heavy-cart compiles; the emitter then fell back to
# bytecode at half the logic speed). moy_gfx.native_code_free_all binds it.
if ! grep -q "moybyte_native_code_free" "${UPSTREAM_DIR}/lib/micropython/ports/esp32/mpconfigport.h"; then
  echo "Moybyte: applying native-code-free patch (#66)"
  patch -d "${UPSTREAM_DIR}/lib/micropython" -p1 < "${PATCH_DIR}/esp32_native_code_free.patch"
fi

# Moybyte #69 (A/B knob, DEFAULT OFF): switch machine.I2C to esp-idf's NEW
# i2c_master driver. Why: the legacy driver's `timeout=` programs only the S3's
# per-CLOCK-STRETCH-EVENT hardware register (exponential; 5000us -> 6.55ms per
# event, XIAO-verified via the TO_REG) while the transaction itself waits a
# hardcoded 100ms*(1+len) ("TODO proper timeout" in machine_i2c.c) -- so the
# T-Deck keyboard C3 stretching MANY sub-cap times stalls a 5-byte read
# 40-60ms "successfully" (I2CSTAT to=0, max 39-61ms, hardware 2026-07-04). The
# new driver passes timeout as the PER-TRANSACTION cap, turning a stall into a
# <=5ms ETIMEDOUT that the input layer absorbs as one held-state frame.
# MOYBYTE_I2C_NEW_DRIVER=1 to apply; default reverts (clean A/B, same toggle
# pattern as the early-board-init patch).
# 2026-07-05 T-DECK A/B RESULT: **BREAKS THE I2C BUS AT BOOT** -- keyboard
# ENODEV + GT911 not found (both peripherals NACK the new driver's probe-first
# transfer; internal pullups ARE enabled in the port's init, cause TBD --
# possibly the i2c_master_probe-before-every-transfer pattern or a legacy/new
# driver mix elsewhere in the image). DO NOT USE until root-caused; the #69
# path forward is the core-1 input poller / C3 keyboard firmware fix instead.
I2C_NEW_DRIVER="${MOYBYTE_I2C_NEW_DRIVER:-0}"
if [ -f "${MPCONFIGPORT_H}" ]; then
  if [ "${I2C_NEW_DRIVER}" = "1" ]; then
    if ! grep -q "Moybyte #69" "${MPCONFIGPORT_H}"; then
      echo "Moybyte: applying NEW i2c_master driver patch (#69, per-transaction timeout)"
      patch -d "${UPSTREAM_DIR}/lib/micropython" -p1 < "${PATCH_DIR}/esp32_i2c_new_driver.patch"
    fi
  else
    if grep -q "Moybyte #69" "${MPCONFIGPORT_H}"; then
      echo "Moybyte: reverting NEW i2c_master driver patch (#69)"
      patch -R -d "${UPSTREAM_DIR}/lib/micropython" -p1 < "${PATCH_DIR}/esp32_i2c_new_driver.patch"
    fi
  fi
fi

# Moybyte #69 GIL (DEFAULT ON): release the GIL across machine.I2C's blocking
# legacy-driver transaction wait (the "TODO proper timeout" i2c_master_cmd_begin).
# This is what makes the INPUT POLLER THREAD work: a T-Deck keyboard C3
# clock-stretch stall (40-60ms, I2CSTAT-sized on hardware) then blocks only the
# poller thread while the VM keeps rendering -- without it the stall holds the
# GIL and freezes the whole loop no matter which thread reads. Only pure IDF
# code runs unlocked (the port's SPI/UART blocking waits use the same pattern),
# so this is safe with or without the poller. MOYBYTE_I2C_GIL_RELEASE=0 reverts
# for a clean A/B.
I2C_GIL_RELEASE="${MOYBYTE_I2C_GIL_RELEASE:-1}"
MACHINE_I2C_C="${UPSTREAM_DIR}/lib/micropython/ports/esp32/machine_i2c.c"
if [ -f "${MACHINE_I2C_C}" ]; then
  if [ "${I2C_GIL_RELEASE}" = "1" ]; then
    if ! grep -q "Moybyte #69 GIL" "${MACHINE_I2C_C}"; then
      echo "Moybyte: applying I2C GIL-release patch (#69, poller-thread stall isolation)"
      patch -d "${UPSTREAM_DIR}/lib/micropython" -p1 < "${PATCH_DIR}/esp32_i2c_gil_release.patch"
    fi
  else
    if grep -q "Moybyte #69 GIL" "${MACHINE_I2C_C}"; then
      echo "Moybyte: reverting I2C GIL-release patch (#69)"
      patch -R -d "${UPSTREAM_DIR}/lib/micropython" -p1 < "${PATCH_DIR}/esp32_i2c_gil_release.patch"
    fi
  fi
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
    # --partition-size pins BOTH slots at 5MB (#168: 4MB -> 4.5MB on 2026-07-27 when the
    # app crossed 4MB -- 0x4148a0 -- and the size check hard-failed the build; -> 5MB on
    # 2026-07-29 so the next growth spurt doesn't cost another table change, since every
    # table change costs deployed devices a full-erase USB flash). Layout on the 16MB
    # part: ota_0 @0x20000 + ota_1 @0x520000, both 5MB, vfs takes the ~6MB tail (carts
    # live on SD, so a smaller internal vfs is free). The build prints the remaining slot
    # headroom at the end -- watch it, per #168 this must never again be discovered as a
    # hard build failure. Rollback is already on (sdkconfig.base CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE
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
      --partition-size=5242880
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

# IDF only generates the build's sdkconfig FROM the defaults when the file is
# ABSENT -- appending options to sdkconfig.base does NOT propagate into an
# existing build dir (verified: a rebuild silently kept the 16KB/32KB caches).
# If the generated file doesn't match the requested cache geometry (any of the
# three options -- the LINE option is the one a 64KB-only check missed), delete
# it so the reconfigure regenerates it from the (now-updated) defaults.
GEN_SDKCONFIG="${MPY_BUILD_DIR}/sdkconfig"
if [ "${CACHE_GEOMETRY}" = "fast" ]; then
  WANT_CACHE='CONFIG_ESP32S3_INSTRUCTION_CACHE_32KB=y
CONFIG_ESP32S3_DATA_CACHE_64KB=y
CONFIG_ESP32S3_DATA_CACHE_LINE_32B=y'
else
  WANT_CACHE='CONFIG_ESP32S3_INSTRUCTION_CACHE_16KB=y
CONFIG_ESP32S3_DATA_CACHE_32KB=y
CONFIG_ESP32S3_DATA_CACHE_LINE_32B=y'
fi
if [ "${EXTMEM_SPEED}" = "fast" ]; then
  WANT_EXTMEM='CONFIG_ESPTOOLPY_FLASHFREQ_120M=y
CONFIG_SPIRAM_SPEED_120M=y
CONFIG_SPIRAM_TIMING_TUNING_POINT_VIA_TEMPERATURE_SENSOR=y
CONFIG_SPIRAM_SPEED=120
CONFIG_IDF_EXPERIMENTAL_FEATURES=y'
elif [ "${EXTMEM_SPEED}" = "fast_notemp" ]; then
  WANT_EXTMEM='CONFIG_ESPTOOLPY_FLASHFREQ_120M=y
CONFIG_SPIRAM_SPEED_120M=y
# CONFIG_SPIRAM_TIMING_TUNING_POINT_VIA_TEMPERATURE_SENSOR is not set
CONFIG_SPIRAM_SPEED=120
CONFIG_IDF_EXPERIMENTAL_FEATURES=y'
else
  WANT_EXTMEM='CONFIG_ESPTOOLPY_FLASHFREQ_80M=y
CONFIG_SPIRAM_SPEED_80M=y
CONFIG_SPIRAM_SPEED=80'
fi
if [ -f "${GEN_SDKCONFIG}" ]; then
  while IFS= read -r opt; do
    if ! grep -q "^${opt}$" "${GEN_SDKCONFIG}"; then
      echo "sdkconfig generated options stale (missing ${opt}) -- forcing regeneration"
      rm -f "${GEN_SDKCONFIG}"
      break
    fi
  done <<EOF
${WANT_CACHE}
${WANT_EXTMEM}
CONFIG_FREERTOS_PLACE_FUNCTIONS_INTO_FLASH=y
CONFIG_RINGBUF_PLACE_FUNCTIONS_INTO_FLASH=y
CONFIG_HEAP_PLACE_FUNCTION_INTO_FLASH=y
EOF
fi

"${RUNNER[@]}" "${BUILD_COMMAND[@]}"

if [ ! -f "${MPY_BUILD_DIR}/micropython.bin" ]; then
  echo "No ESP32 app image found at ${MPY_BUILD_DIR}/micropython.bin" >&2
  exit 1
fi

cp "${MPY_BUILD_DIR}/micropython.bin" "${APP_BIN}"
echo "Wrote SD launcher app image: ${APP_BIN}"
cp "${APP_BIN}" "${CURRENT_APP_BIN}"
echo "Updated current app alias: ${CURRENT_APP_BIN}"

# #168 size guard: the app crossing the OTA slot is a HARD build failure two steps
# later (esptool's size check), and the last time it happened ~73KB of the overflow
# had accumulated unnoticed over two weeks. Print image size + remaining headroom on
# every build, and warn loudly under the threshold -- growing the slot again costs
# every deployed device a full-erase USB flash, so the number has to be seen early.
#
# The overflow case FAILS the build now (it warned until 2026-08-15, and the
# next steps only merge the image -- so a warning produced a full set of
# artifacts that esptool would refuse and that no board could take over OTA).
# The margin got thin the moment the web console was baked in: ~573KB of the
# slot is the browser bundle, which the build prints above.
APP_SLOT_BYTES="${MOYBYTE_APP_SLOT_BYTES:-5242880}"
APP_HEADROOM_WARN_BYTES="${MOYBYTE_APP_HEADROOM_WARN_BYTES:-204800}"   # 200KB
APP_SIZE_BYTES="$(wc -c < "${APP_BIN}" | tr -d '[:space:]')"
APP_HEADROOM_BYTES=$(( APP_SLOT_BYTES - APP_SIZE_BYTES ))
printf 'App image: %s bytes of a %s-byte slot -- %s bytes headroom (%s KB)\n' \
  "${APP_SIZE_BYTES}" "${APP_SLOT_BYTES}" "${APP_HEADROOM_BYTES}" "$(( APP_HEADROOM_BYTES / 1024 ))"
if [ "${APP_HEADROOM_BYTES}" -lt 0 ]; then
  echo "" >&2
  echo "!! Moybyte BUILD FAILED (#168): the app image does not fit its OTA slot" >&2
  echo "!!   slot:     ${APP_SLOT_BYTES} bytes (ota_0/ota_1 on the 16MB part)" >&2
  echo "!!   image:    ${APP_SIZE_BYTES} bytes" >&2
  echo "!!   OVERFLOW: $(( -APP_HEADROOM_BYTES )) bytes ($(( (-APP_HEADROOM_BYTES) / 1024 )) KB)" >&2
  echo "!! An image this size cannot be flashed and cannot be installed over OTA." >&2
  echo "!! Trim it (the baked web console is ~573KB -- see tools/gen_web_blob.py)" >&2
  echo "!! or change the partition table, which costs every deployed device a" >&2
  echo "!! full-erase USB flash." >&2
  exit 1
elif [ "${APP_HEADROOM_BYTES}" -lt "${APP_HEADROOM_WARN_BYTES}" ]; then
  echo "Moybyte WARNING (#168): under $(( APP_HEADROOM_WARN_BYTES / 1024 ))KB of OTA-slot headroom left -- trim the image or plan the next table change" >&2
fi

BOOTLOADER_BIN="${MPY_BUILD_DIR}/bootloader/bootloader.bin"
PARTITION_BIN="${MPY_BUILD_DIR}/partition_table/partition-table.bin"
if [ ! -f "${BOOTLOADER_BIN}" ] || [ ! -f "${PARTITION_BIN}" ]; then
  echo "Skipping full image merge; bootloader or partition table is missing" >&2
  exit 0
fi

if [ -n "${IDF_PYTHON:-}" ]; then
  ESPTOOL_PY="${IDF_PYTHON}"
else
  # Find the ESP-IDF python env (it has esptool) regardless of its py-version suffix:
  # the env is named idf<idf>_py<py>_env, e.g. idf5.5_py3.10_env locally but
  # idf5.5_py3.11_env on the CI runner -- a hardcoded py3.10 path missed it and fell
  # through to a bare `python3` that has no esptool, so the final merge failed.
  ESPTOOL_PY=""
  for _cand in "${HOME}"/.espressif/python_env/idf*_py*_env/bin/python; do
    [ -x "${_cand}" ] && ESPTOOL_PY="${_cand}" && break
  done
  [ -n "${ESPTOOL_PY}" ] || ESPTOOL_PY="python3"
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
