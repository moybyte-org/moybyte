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
PATCH_DIR="${SCRIPT_DIR}/patches"
DIST_DIR="${REPO_ROOT}/dist/p4"
TDECK_DIR="${REPO_ROOT}/firmware/lilygo_t_deck_plus_micropython"
MODULES_DIR="${SCRIPT_DIR}/modules"
MANIFEST="${BUILD_DIR}/moybyte_p4_manifest.py"
BUILD_PYTHON="${MOYBYTE_BUILD_PYTHON:-}"

if [ -z "${BUILD_PYTHON}" ]; then
  if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
    BUILD_PYTHON="${REPO_ROOT}/.venv/bin/python"
  else
    BUILD_PYTHON="python3"
  fi
fi

mkdir -p "${BUILD_DIR}" "${DIST_DIR}" "${MODULES_DIR}"

# 1) MicroPython checkout (pinned tag).
if [ ! -d "${MPY_DIR}" ]; then
  echo "== cloning micropython ${MPY_TAG}"
  git clone --depth 1 -b "${MPY_TAG}" https://github.com/micropython/micropython "${MPY_DIR}"
fi

# Steady-state BLE keyboard notifications must not wait behind MicroPython's
# synchronous NimBLE IRQ/GIL path. The P4-only native queue consumes registered
# HID handles before Python dispatch; pairing/bonding/discovery remain on the
# supported synchronous path. Marker-guarded because .build persists.
MODBLUETOOTH_C="${MPY_DIR}/extmod/modbluetooth.c"
if [ -f "${MODBLUETOOTH_C}" ] && \
   ! grep -q "moy_ble_hid_queue_on_notify" "${MODBLUETOOTH_C}"; then
  echo "== applying P4 BLE-HID native notification fast-path patch"
  patch -d "${MPY_DIR}" -p1 < "${PATCH_DIR}/modbluetooth_ble_hid_fastpath.patch"
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
# The toolchains + IDF python env live in ~/.espressif, OUTSIDE the esp-idf
# tree: a runner can have the tree (restored .build cache) but not the tools
# (evicted ~/.espressif cache). export.sh can NOT be trusted to report that --
# v5.5.1 ends in an unconditional `return 0` (only its inner activate.py
# fails) -- so probe the real outcome: idf.py on PATH. Self-heal with the
# official installer and re-source.
set +u
# shellcheck disable=SC1091
source "${IDF_DIR}/export.sh" >/dev/null 2>&1 || true
if ! command -v idf.py >/dev/null 2>&1; then
  echo "== ESP-IDF tools missing (fresh runner / evicted cache): running install.sh esp32p4"
  "${IDF_DIR}/install.sh" esp32p4
  # shellcheck disable=SC1091
  source "${IDF_DIR}/export.sh" >/dev/null
  command -v idf.py >/dev/null 2>&1 || { echo "!! idf.py still missing after install.sh" >&2; exit 1; }
fi
set -u

# #106: backport current ESP-IDF's dedicated DSI bridge-underrun ISR and keep
# the frame-restart DW-GDMA interrupt above ESP-Hosted's SDIO interrupt. IDF
# v5.5 checks the bridge only from the DMA callback; if SDIO delays that callback,
# the display has already gone blue and the status can be cleared unseen.
# Marker-guarded because the reused IDF checkout persists.
DSI_DPI_C="${IDF_DIR}/components/esp_lcd/dsi/esp_lcd_panel_dpi.c"
if [ -f "${DSI_DPI_C}" ] && \
   ! grep -q "Moybyte P4: dedicated DSI bridge underrun IRQ" "${DSI_DPI_C}"; then
  echo "== applying P4 DSI bridge IRQ/priority fix (#106)"
  patch -d "${IDF_DIR}" -p1 < "${PATCH_DIR}/esp_lcd_dsi_underrun_hook.patch"
fi
# 2b) moy_dsi needs esp_lcd in the main component's REQUIRES. The
#     USER_C_MODULES cmake is skipped during idf.py's early-expansion phase --
#     exactly when REQUIRES are collected -- so appending IDF_COMPONENTS there
#     can never work; patch the port's list instead (idempotent).
COMMON_CMAKE="${MPY_DIR}/ports/esp32/esp32_common.cmake"
if ! grep -q "^    esp_lcd$" "${COMMON_CMAKE}"; then
  sed -i '/^list(APPEND IDF_COMPONENTS$/a\    esp_lcd' "${COMMON_CMAKE}"
  echo "== patched esp32_common.cmake: added esp_lcd to IDF_COMPONENTS"
fi
# moy_ppa needs esp_driver_ppa (the P4 pixel accelerator) in REQUIRES -- same
# early-expansion reason as esp_lcd above, so patch the port list too.
if ! grep -q "^    esp_driver_ppa$" "${COMMON_CMAKE}"; then
  sed -i '/^list(APPEND IDF_COMPONENTS$/a\    esp_driver_ppa' "${COMMON_CMAKE}"
  echo "== patched esp32_common.cmake: added esp_driver_ppa to IDF_COMPONENTS"
fi

# 2b2) Moybyte #66: the same native-code-arena reclaim patch as the T-Deck
#      build -- mainline's ports/esp32/main.c has the identical grow-only
#      esp_native_code_commit list, and the P4's RV32 native emitter feeds it
#      (MICROPY_EMIT_RV32=1), so edit->PLAY sessions would hit the same cliff,
#      just later (bigger internal pool). One shared patch file; the moy_gfx
#      weak-symbol binding goes strong once this is applied.
NATIVE_FREE_PATCH="${REPO_ROOT}/firmware/lilygo_t_deck_plus_micropython/patches/esp32_native_code_free.patch"
if ! grep -q "moybyte_native_code_free" "${MPY_DIR}/ports/esp32/mpconfigport.h"; then
  echo "== applying native-code-free patch (#66)"
  patch -d "${MPY_DIR}" -p1 < "${NATIVE_FREE_PATCH}"
fi

# 2c) Stage the shared NATIVE modules from the T-Deck tree (single source of
#     truth: firmware/lilygo_t_deck_plus_micropython/native/). moy_gfx is the
#     VM-neutral RGB565 pixel kernel every draw verb runs through; moy_alloc is
#     the off-gc-heap PSRAM allocator the layer/window buffers use. Both are
#     plain-C usermods (the S3-specific pieces are include-guarded), so they
#     compile unchanged on the P4's RISC-V. native/micropython.cmake includes
#     moy_dsi + these staged copies.
STAGED_NATIVE="${SCRIPT_DIR}/native/.staged"
rm -rf "${STAGED_NATIVE}"
mkdir -p "${STAGED_NATIVE}"
cp -r "${TDECK_DIR}/native/moy_gfx" "${STAGED_NATIVE}/moy_gfx"
cp -r "${TDECK_DIR}/native/moy_alloc" "${STAGED_NATIVE}/moy_alloc"
# moy_lua (#67 Phase 1): the Lua cart VM + bridge -- plain C (vendored Lua 5.4
# + py/ API), compiles unchanged on RISC-V; the PSRAM lua_Alloc uses the same
# heap_caps the S3 does. The glue rides device_api.py (staged below).
cp -r "${TDECK_DIR}/native/moy_lua" "${STAGED_NATIVE}/moy_lua"
# moycore (stage 2): libmoy's own Lua binding + the C frame loop. Requires the
# two above -- it compiles neither a raster nor a VM, and reaches theirs by
# sibling include path.
cp -r "${TDECK_DIR}/native/moycore" "${STAGED_NATIVE}/moycore"

# 2d) Stage the shared PYTHON modules (#58 console staging).
#     From runtime/ (canonical, same list the T-Deck build stages) -- the whole
#     shared console -- PLUS wm_windowed.py: the P4 is the windowed presentation
#     tier (#73), deliberately NOT staged into the S3 build.
for f in editors.py editors_base.py editors_code.py editors_sheet.py \
         editors_paint_map.py editors_block.py editors_music.py \
         editors_scene.py block_editor_ui.py map_editor_ui.py \
         scene_editor_ui.py audio.py \
         music_editor_ui.py perf_hud.py update_ui.py system_menu_ui.py \
         achievements_ui.py layers.py bar_layer.py cards_layer.py \
         paint_layer.py settings_layer.py code_layer.py widgets.py \
         wallpaper.py artwork.py appearance_app.py app_shell.py file_widgets.py files_app.py writer_app.py storybook_app.py sheets_app.py formula.py launcher_layer.py project.py player.py editor_app.py \
         wm.py wm_windowed.py surface.py players.py chrome.py ui.py calc_app.py console.py moy_carts.py \
         moybuf.py moy_fs.py moy_image.py moy_journal.py op_history.py blocks.py lua_ext.py \
         canvas.py palette.py; do
  cp "${REPO_ROOT}/runtime/${f}" "${MODULES_DIR}/${f}"
done
cp "${REPO_ROOT}/runtime/font.py" "${MODULES_DIR}/moy_font.py"
#     From the T-Deck modules tree (canonical home of the device-shared backend
#     units): the drawing backend + the cart namespace + wifi + the leaf utils +
#     the moybyte input package (InputState). These are board-agnostic by
#     construction (their lvgl/S3-only imports are guarded).
#     moy_ota is here too (#53): maintained in the T-Deck tree because that is
#     the board it was written for, but board-agnostic -- its staging directory
#     is a constructor argument (this board has no SD; run_desktop passes a path
#     on the internal VFS) and its board identity is stamped into _ota_build.
#     moy_webserver/moy_webhost: the socket core + the "serve the wasm console
#     from this board" handler (plan 3.4 pull half). Board-agnostic -- the web
#     directory and the SD gate are constructor arguments.
for f in device_util.py device_canvas.py device_api.py \
         moycore_glue.py device_wifi.py \
         moy_webserver.py moy_webhost.py \
         moy_ota.py; do
  cp "${TDECK_DIR}/modules/${f}" "${MODULES_DIR}/${f}"
done
rm -rf "${MODULES_DIR}/moybyte"
mkdir -p "${MODULES_DIR}/moybyte"
cp "${TDECK_DIR}/modules/moybyte/__init__.py" "${TDECK_DIR}/modules/moybyte/input.py" \
   "${MODULES_DIR}/moybyte/"
#     carts_data.py is GENERATED from system_carts/ (same as the T-Deck) so the
#     P4's seed/fallback carts can never drift from the host source of truth.
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_device_carts.py" "${MODULES_DIR}/carts_data.py"

# OTA build identity (#53), the same shape the T-Deck stamps -- plus BOARD,
# which per-board manifests turn on. An app image is board-specific in the
# strongest way (Xtensa there, RISC-V here), so the manifest url carries the
# board and the device refuses a manifest naming another one.
OTA_CHANNEL="${MOYBYTE_OTA_CHANNEL:-stable}"
if [ -n "${MOYBYTE_OTA_VERSION:-}" ]; then
  OTA_VERSION="${MOYBYTE_OTA_VERSION}"
elif [ "${OTA_CHANNEL}" = "unstable" ]; then
  OTA_VERSION="$(date +%s)"                       # monotonic per-build beta version
else
  OTA_VERSION="$(grep -oE 'FIRMWARE_VERSION = [0-9]+' "${TDECK_DIR}/modules/moy_ota.py" | head -1 | grep -oE '[0-9]+')"
  OTA_VERSION="${OTA_VERSION:-1}"
fi
if [ "${OTA_CHANNEL}" = "unstable" ]; then
  OTA_LABEL="beta $(date '+%Y-%m-%d %H:%M')"
else
  # The human release name (FIRMWARE_NAME, set by `make release NAME=`), NOT the
  # ordering counter -- "0.6" is what the update screen and the manifest show.
  OTA_NAME="$(grep -oE '^FIRMWARE_NAME = "[^"]*"' "${TDECK_DIR}/modules/moy_ota.py" | head -1 | cut -d'"' -f2)"
  OTA_LABEL="${OTA_NAME:-v${OTA_VERSION}}"
fi
cat > "${MODULES_DIR}/_ota_build.py" <<EOF
# AUTO-GENERATED by build.sh -- moy_ota imports this for the build's OTA identity.
# Gitignored; do not edit or commit.
CHANNEL = "${OTA_CHANNEL}"
VERSION = ${OTA_VERSION}
LABEL = "${OTA_LABEL}"
BOARD = "p4"
EOF
mkdir -p "${DIST_DIR}"
cat > "${DIST_DIR}/ota_build.json" <<EOF
{"channel": "${OTA_CHANNEL}", "version": ${OTA_VERSION}, "label": "${OTA_LABEL}", "board": "p4"}
EOF
echo "OTA build identity: board=p4 channel=${OTA_CHANNEL} version=${OTA_VERSION} label='${OTA_LABEL}'"
#     A stray host-side import can drop __pycache__ into modules/; the freeze
#     ignores it but keep the tree clean anyway.
rm -rf "${MODULES_DIR}/__pycache__" "${MODULES_DIR}/moybyte/__pycache__"

# 2e) Frozen manifest: the port's default frozen stdlib + our modules/ tree.
#     The md5 fingerprint makes the manifest content change whenever any frozen
#     source changes, so the build can never freeze stale .mpy (same trick as
#     the T-Deck build).
cat > "${MANIFEST}" <<EOF
include("\$(PORT_DIR)/boards/manifest.py")
freeze("${MODULES_DIR}", opt=3)
EOF
echo "# frozen-source fingerprint: $(find "${MODULES_DIR}" -type f -name '*.py' -exec md5sum {} + 2>/dev/null | sort | md5sum | cut -d' ' -f1)" >> "${MANIFEST}"

# 2f) Custom partition table (#58): OTA-shaped 2x4MB app slots + auto-vfs tail
#     (the default 4MiBplus table's ~1.94MB app can't hold the frozen console).
#     CONFIG_PARTITION_TABLE_CUSTOM_FILENAME resolves relative to ports/esp32, so
#     stage the board CSV there. IDF only (re)generates the build's sdkconfig
#     from the defaults when the file is ABSENT (the T-Deck build learned this).
#     Force regeneration when an existing config lacks either critical board
#     override; otherwise edits to sdkconfig.board silently leave a stale image.
cp "${BOARD_DIR}/partitions-moybyte-p4.csv" "${MPY_DIR}/ports/esp32/partitions-moybyte-p4.csv"
GEN_SDKCONFIG="${MPY_DIR}/ports/esp32/build-${BOARD}/sdkconfig"
if [ -f "${GEN_SDKCONFIG}" ]; then
  if ! grep -q '^CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions-moybyte-p4.csv"$' "${GEN_SDKCONFIG}" || \
     ! grep -q '^CONFIG_BT_NIMBLE_TRANSPORT_ACL_FROM_LL_COUNT=64$' "${GEN_SDKCONFIG}" || \
     ! grep -q '^CONFIG_BT_NIMBLE_HOST_TASK_STACK_SIZE=12288$' "${GEN_SDKCONFIG}" || \
     ! grep -q '^CONFIG_CACHE_L2_CACHE_256KB=y$' "${GEN_SDKCONFIG}" || \
     ! grep -q '^CONFIG_LCD_DSI_ISR_IRAM_SAFE=y$' "${GEN_SDKCONFIG}" || \
     ! grep -q '^CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y$' "${GEN_SDKCONFIG}"; then
    echo "== sdkconfig lacks a required P4 board override -- forcing regeneration"
    rm -f "${GEN_SDKCONFIG}"
  fi
fi

# 3) mpy-cross (host tool, needed by the port build).
make -C "${MPY_DIR}/mpy-cross" -j"$(nproc)"

# 4) The esp32 port build (out-of-tree board definition).
cd "${MPY_DIR}/ports/esp32"
make submodules BOARD_DIR="${BOARD_DIR}"
make BOARD_DIR="${BOARD_DIR}" \
  USER_C_MODULES="${SCRIPT_DIR}/native/micropython.cmake" \
  FROZEN_MANIFEST="${MANIFEST}"

# 5) Collect the merged image (bootloader+partitions+app; flash at 0x2000).
BOUT="build-${BOARD}"
cp "${BOUT}/firmware.bin" "${DIST_DIR}/moybyte_p4.bin"
echo "OK -> ${DIST_DIR}/moybyte_p4.bin (flash at offset 0x2000)"
# ...and the app-partition image beside it: what an OTA writes into the
# INACTIVE slot. firmware.bin is bootloader + table + app merged for a cable
# flash at 0x2000; handing that to esp32.Partition would write a bootloader
# into an app slot.
cp "${BOUT}/micropython.bin" "${DIST_DIR}/moybyte_p4_app.bin"
echo "OK -> ${DIST_DIR}/moybyte_p4_app.bin (OTA payload, app partition)"
