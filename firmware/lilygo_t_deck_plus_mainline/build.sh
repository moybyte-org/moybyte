#!/usr/bin/env bash
# Moybyte T-Deck on MAINLINE MicroPython -- one build strategy for both boards.
#
# The shipping T-Deck target (firmware/lilygo_t_deck_plus_mainline/) builds on
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
FORK_DIR="${REPO_ROOT}/firmware/lilygo_t_deck_plus_mainline"
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
#     THE FLUSH OVERLAP DEPENDS ON THIS, as of the kick/pump/drain split. It
#     used to be optional here ("show() fences on its own completion counter, so
#     without the patch it is merely serialized") and that is no longer true:
#     without it, every band queued by moy_lcd_pump would first wait out the
#     previous band's DMA, INSIDE the interpreter -- in a 2ms timer callback and
#     in moy_gfx's draw gate, ~3ms a time. That is plausibly slower than the
#     blocking flush it replaced. The build has no way to check this at runtime,
#     so the on-glass tell is a PUMP line whose pump= is near the whole transfer
#     (~14ms) instead of ~3-4ms.
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

# 3f) PSRAM temperature retune, un-gated by flash vendor (#169). REQUIRED by
#     this board's 120MHz octal MSPI setting, not optional alongside it: IDF
#     only starts the retune for verified flash vendor IDs (0xC8/0x20) and
#     otherwise returns ESP_ERR_NOT_SUPPORTED from a SECONDARY
#     ESP_SYSTEM_INIT_FN -- which aborts the boot. The board then flashes
#     cleanly, says NOTHING on serial and never reaches the console, which
#     reads exactly like a PSRAM timing failure and is not one (measured here
#     2026-08-16). The patch relaxes the vendor gate to warn-and-run and turns
#     the task's other brick path -- an abort() when the scanned points share no
#     temperature range -- into "stop adjusting", degrading to the un-mitigated
#     build rather than a dead one.
#
#     If you ever set MOYBYTE_EXTMEM_SPEED=stock (80/80) in sdkconfig.board,
#     this patch is inert, not wrong: nothing calls the retune at 80MHz.
MSPI_TUNING_C="${IDF_DIR}/components/esp_hw_support/mspi_timing_tuning/port/esp32s3/mspi_timing_by_mspi_delay.c"
if [ -f "${MSPI_TUNING_C}" ] && ! grep -q "Moybyte #169" "${MSPI_TUNING_C}"; then
  echo "== applying PSRAM temperature-retune vendor-gate patch (#169)"
  patch -d "${IDF_DIR}" -p1 < "${PATCH_DIR}/esp_psram_temp_retune_any_vendor.patch"
fi

# ---------------------------------------------------------------------------
# 4) Stage the SHARED native modules. Their single source of truth stays
#    native/ -- this build reads that
#    tree and never writes to it. native/.staged/ is gitignored.
#
#    Which modules are here is a per-STAGE fact, so the list carries the stage:
#      moy_gfx    (1) the RGB565 pixel kernel every draw verb runs through, plus
#                     the vendored libmoy raster under it
#      moy_alloc  (1) the off-gc-heap DMA allocator the layer/window buffers use
#      moy_sd     (4) the SD card ATTACHED to the host moy_lcd already
#                     initialised -- sdspi_host_init_device, never a bus re-init
#      moy_audio  (5) the SPEC.md 8 synth: libmoy VENDORED and compiled in (#97),
#                     plus the I2S plumbing and its core-1 feeder task
#      moy_lua    (6) the vendored Lua 5.4 VM and nothing else -- it exports no
#                     MicroPython module, it hands a lua_State to moycore
#      moycore    (6) libmoy's own Lua binding + the C frame loop: _update and
#                     _draw back to back in C, one upcall per frame. Compiles
#                     NEITHER a raster nor a VM -- it reaches the two above by
#                     sibling include path, so all three must be staged together
#      moy_web    (6) the browser console, baked into the image
# ---------------------------------------------------------------------------
SHARED_NATIVE="${MOYBYTE_SHARED_NATIVE:-moy_gfx moy_alloc moy_sd moy_audio moy_lua moycore moy_web}"
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

# 4a) The BROWSER CONSOLE blob, generated INTO THE STAGED COPY.
#     tools/gen_web_blob.py .incbin's the four pre-gzipped wasm-console assets
#     into a C file inside moy_web, so the image serves a console with no card
#     and no push. The P4 build generates it into the fork tree and copies
#     afterwards; this one copies first and generates into `.staged/`, so this
#     script keeps its promise never to write into the shipping target.
#
#     No bundle built = an empty table + a loud warning, which is the right
#     default for a bring-up. Under CI (or MOYBYTE_REQUIRE_WEB_BUNDLE=1) it is a
#     hard failure instead, because a PUBLISHED image with no console is the
#     whole bug the baking exists to fix.
if [ -d "${STAGED_NATIVE}/moy_web" ]; then
  WEB_BLOB_ARGS=(--out "${STAGED_NATIVE}/moy_web/moy_web_blob.gen.c")
  if [ -n "${CI:-}" ] || [ "${MOYBYTE_REQUIRE_WEB_BUNDLE:-0}" = "1" ]; then
    WEB_BLOB_ARGS+=(--require)
  fi
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_web_blob.py" "${WEB_BLOB_ARGS[@]}"
fi

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

# 4c) The two GENERATED modules. `board.toml`'s `keep` names both, so the
#     stager's prune leaves them alone; they are gitignored like everything else
#     staged here.
#
#     carts_data.py is built from system_carts/ so the seed + embedded-fallback
#     carts can never drift from the host source of truth.
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_device_carts.py" "${MODULES_DIR}/carts_data.py"

#     _ota_build.py stamps the build's OTA identity (#53). The CHANNEL is a
#     BUILD choice, not a per-branch source edit, so it stays clean across
#     merges: CI derives it from the ref, a local beta sets
#     MOYBYTE_OTA_CHANNEL=unstable. A beta's VERSION is the build epoch, which
#     is auto-newer on every publish; a stable's is the counter in moy_ota.py,
#     which `make release` bumps.
#
#     BOARD is "tdeck" -- the SAME id the fork build stamps, and that is
#     correct rather than sloppy: an OTA payload is an app-partition image, and
#     these two builds produce interchangeable ones for the same Xtensa board on
#     a byte-identical partition table. A separate id would mean a board could
#     not move between them, which is exactly the migration this port needs.
OTA_CHANNEL="${MOYBYTE_OTA_CHANNEL:-stable}"
if [ -n "${MOYBYTE_OTA_VERSION:-}" ]; then
  OTA_VERSION="${MOYBYTE_OTA_VERSION}"
elif [ "${OTA_CHANNEL}" = "unstable" ]; then
  OTA_VERSION="$(date +%s)"                       # monotonic per-build beta version
else
  OTA_VERSION="$(grep -oE 'FIRMWARE_VERSION = [0-9]+' "${MODULES_DIR}/moy_ota.py" | head -1 | grep -oE '[0-9]+')"
  OTA_VERSION="${OTA_VERSION:-1}"
fi
if [ "${OTA_CHANNEL}" = "unstable" ]; then
  OTA_LABEL="beta $(date '+%Y-%m-%d %H:%M')"
else
  # The human release name (FIRMWARE_NAME, set by `make release NAME=`), not the
  # ordering counter -- "0.6" is what the update screen and the manifest show.
  OTA_NAME="$(grep -oE '^FIRMWARE_NAME = "[^"]*"' "${MODULES_DIR}/moy_ota.py" | head -1 | cut -d'"' -f2)"
  OTA_LABEL="${OTA_NAME:-v${OTA_VERSION}}"
fi
cat > "${MODULES_DIR}/_ota_build.py" <<EOF
# AUTO-GENERATED by build.sh -- moy_ota imports this for the build's OTA identity.
# Gitignored; do not edit or commit.
CHANNEL = "${OTA_CHANNEL}"
VERSION = ${OTA_VERSION}
LABEL = "${OTA_LABEL}"
BOARD = "tdeck"
EOF
cat > "${DIST_DIR}/ota_build.json" <<EOF
{"channel": "${OTA_CHANNEL}", "version": ${OTA_VERSION}, "label": "${OTA_LABEL}", "board": "tdeck"}
EOF
echo "OTA build identity: board=tdeck channel=${OTA_CHANNEL} version=${OTA_VERSION} label='${OTA_LABEL}'"

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

# The slot size is READ FROM THE BOARD'S OWN PARTITION TABLE, not restated, so
# the check cannot drift from the layout it is checking (the P4's guard learned
# this first). MOYBYTE_APP_SLOT_BYTES overrides for a what-if.
APP_SLOT_HEX="$(awk -F',' '/^[[:space:]]*ota_0[[:space:]]*,/ { gsub(/[[:space:]]/, "", $5); print $5; exit }' \
  "${BOARD_DIR}/partitions-moybyte-tdeck.csv")"
APP_SLOT_BYTES="${MOYBYTE_APP_SLOT_BYTES:-$(( ${APP_SLOT_HEX:-0x500000} ))}"
APP_HEADROOM_WARN_BYTES="${MOYBYTE_APP_HEADROOM_WARN_BYTES:-204800}"
APP_SIZE_BYTES="$(wc -c < "${DIST_DIR}/moybyte_tdeck_app.bin" | tr -d '[:space:]')"
APP_HEADROOM_BYTES=$(( APP_SLOT_BYTES - APP_SIZE_BYTES ))
printf 'App image: %s bytes of a %s-byte ota_0 slot -- %s bytes headroom (%s KB)\n' \
  "${APP_SIZE_BYTES}" "${APP_SLOT_BYTES}" "${APP_HEADROOM_BYTES}" "$(( APP_HEADROOM_BYTES / 1024 ))"
if [ "${APP_HEADROOM_BYTES}" -lt 0 ]; then
  # FAILS, it does not warn -- the P4's rule, and the right one. An image that
  # does not fit its slot cannot be cable-flashed and cannot be installed over
  # OTA by any board, so the alternatives to stopping here are esptool refusing
  # it later or a published payload nobody can take.
  echo "" >&2
  echo "!! Moybyte BUILD FAILED (#168): the app image does not fit its OTA slot" >&2
  echo "!!   slot:     ${APP_SLOT_BYTES} bytes (ota_0/ota_1, partitions-moybyte-tdeck.csv)" >&2
  echo "!!   image:    ${APP_SIZE_BYTES} bytes" >&2
  echo "!!   OVERFLOW: $(( -APP_HEADROOM_BYTES )) bytes ($(( (-APP_HEADROOM_BYTES) / 1024 )) KB)" >&2
  echo "!! Trim it (the baked web console is ~573KB -- see tools/gen_web_blob.py)" >&2
  echo "!! or change the partition table, which costs every deployed device a" >&2
  echo "!! full-erase USB flash." >&2
  exit 1
elif [ "${APP_HEADROOM_BYTES}" -lt "${APP_HEADROOM_WARN_BYTES}" ]; then
  echo "Moybyte WARNING (#168): under $(( APP_HEADROOM_WARN_BYTES / 1024 ))KB of OTA-slot headroom left" >&2
fi

echo "OK -> ${DIST_DIR}/moybyte_tdeck.bin (full image, cable flash at 0x0)"
echo "OK -> ${DIST_DIR}/moybyte_tdeck_app.bin (OTA payload, app partition)"
