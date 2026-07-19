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

# Stage the Moybyte moy_lua native C module (#67 Phase 1: vendored Lua 5.4 +
# the cart bridge -- see native/moy_lua/modmoy_lua.c) into the upstream ext_mod
# tree, same pattern as moy_audio (ext_mod is wiped on re-clone, so re-stage
# every build). moy_runtime injects the Lua cart runtime only when this module
# imports, so a build without it still boots (lua carts panel gracefully).
MOY_LUA_SRC="${SCRIPT_DIR}/native/moy_lua"
MOY_LUA_DST="${UPSTREAM_DIR}/ext_mod/moy_lua"
if [ -d "${MOY_LUA_SRC}" ]; then
  rm -rf "${MOY_LUA_DST}"
  cp -r "${MOY_LUA_SRC}" "${MOY_LUA_DST}"
  EXT_MOD_CMAKE="${UPSTREAM_DIR}/ext_mod/micropython.cmake"
  if ! grep -q 'moy_lua/micropython.cmake' "${EXT_MOD_CMAKE}"; then
    sed -i '/moy_audio\/micropython.cmake/a include(${CMAKE_CURRENT_LIST_DIR}/moy_lua/micropython.cmake)' "${EXT_MOD_CMAKE}"
  fi
fi

# Stage the shared host/device modules into the frozen modules tree. Canonical
# sources live in runtime/ (imported by the host as runtime.*); the device freezes
# these copies as top-level modules, so both consoles run literally the same code:
#   editors.py    -- CodeEditor / SpriteSheet / PaintEditor
#   block_editor_ui.py -- the block editor's UI (#29 Part 2: BlockEditorUI +
#                    BlockLayout, extracted from console.py); console.py does
#                    `from block_editor_ui import BlockEditorUI, ...`
#   map_editor_ui.py -- the map/tilemap editor's UI (#32: MapEditorUI, extracted
#                    from console.py); console.py does `from map_editor_ui
#                    import MapEditorUI, ...`
#   music_editor_ui.py -- the music/sound editor's UI (#50: MusicEditorUI,
#                    extracted from console.py); console.py does `from
#                    music_editor_ui import MusicEditorUI, ...`
#   perf_hud.py   -- the perf HUD's rendering (#43/#44: PerfHud, extracted from
#                    console.py); console.py does `from perf_hud import PerfHud`
#   update_ui.py  -- the firmware-update (OTA) screen's UI (#53: UpdateUI,
#                    extracted from console.py); console.py does `from update_ui
#                    import UpdateUI`
#   system_menu_ui.py -- the ≡ dropdown / system menu's UI (#52: SystemMenuUI,
#                    extracted from console.py); console.py does `from
#                    system_menu_ui import SystemMenuUI`
#   achievements_ui.py -- the Easter-egg subsystem + achievement/egg drawing (#21:
#                    AchievementsUI, extracted from console.py); console.py does
#                    `from achievements_ui import AchievementsUI`
#   audio.py      -- sound model + AudioEngine synth/mixer (#16)
#   project.py    -- the open cart's live workspace (Stage 1: Project + the four
#                    builders + the commit_* persistence verbs, extracted from
#                    console.py); console.py does `from project import Project`
#   player.py     -- the cart PLAYER (Stage 2: Player.start/tick/handle_input/
#                    handle_pointer + crash/pause chrome, extracted from console.py);
#                    console.py does `from player import Player`
#   editor_app.py -- the EDITOR app (Stage 3: EditorApp -- the tab ladder + tab state
#                    + builders + PLAY, extracted from console.py); console.py does
#                    `from editor_app import EditorApp`
#   wm.py         -- the window manager (Stage 6: FullscreenStackWM -- the game<->system
#                    viewport composite + the back-stack screen projects onto + the
#                    memoized layer stack, extracted from console.py); console.py does
#                    `from wm import FullscreenStackWM`
#   chrome.py     -- the console's stateless base layer (MOY64 palette, Layout/CodeLayout
#                    geometry, the icon-glyph vocabulary + themeable IconSheet defaults,
#                    the small pure helpers), extracted from console.py; console.py does
#                    `from chrome import (...)` and re-exports under the old console.X names
#   console.py    -- launcher + desktop + cards/code/paint UI + Pointer
#   moy_carts.py  -- the .moy store (scan/load/save/create/duplicate/delete)
#   moy_fs.py     -- crash-safe file primitives (atomic write / .bak recover)
#   moy_image.py  -- the moyimg codec + cover-thumb sidecars
#   moy_journal.py -- the per-project undo/redo journal (#7 Stage 7)
#   blocks.py     -- block model + blocks->Python compiler (#29; moy_carts imports it)
#   web_view.py   -- shared web-view core (recorder + payloads + serve + constants);
#                    moy_webserver imports it as a frozen top-level `web_view` (#41/#22)
#   web_view_ws.py   -- the WS transport primitives (RFC 6455 handshake + framing),
#                    extracted from web_view.py; web_view re-imports + re-exports them
#   web_view_page.py -- the browser page (PAGE_HTML: <canvas> + JS replayer), extracted
#                    from web_view.py; web_view re-imports + re-exports it
#   moy_font.py   -- petme128 glyph blob (runtime/font.py) for the native
#                    moy_gfx.text kernel (#62), so device text rasterizes from the
#                    SAME bytes the host does (pixel parity)
cp "${REPO_ROOT}/runtime/editors.py" "${SCRIPT_DIR}/modules/editors.py"
cp "${REPO_ROOT}/runtime/editors_base.py" "${SCRIPT_DIR}/modules/editors_base.py"
cp "${REPO_ROOT}/runtime/editors_code.py" "${SCRIPT_DIR}/modules/editors_code.py"
cp "${REPO_ROOT}/runtime/editors_sheet.py" "${SCRIPT_DIR}/modules/editors_sheet.py"
cp "${REPO_ROOT}/runtime/editors_paint_map.py" "${SCRIPT_DIR}/modules/editors_paint_map.py"
cp "${REPO_ROOT}/runtime/editors_block.py" "${SCRIPT_DIR}/modules/editors_block.py"
cp "${REPO_ROOT}/runtime/editors_music.py" "${SCRIPT_DIR}/modules/editors_music.py"
cp "${REPO_ROOT}/runtime/block_editor_ui.py" "${SCRIPT_DIR}/modules/block_editor_ui.py"
cp "${REPO_ROOT}/runtime/map_editor_ui.py" "${SCRIPT_DIR}/modules/map_editor_ui.py"
cp "${REPO_ROOT}/runtime/audio.py" "${SCRIPT_DIR}/modules/audio.py"
cp "${REPO_ROOT}/runtime/music_editor_ui.py" "${SCRIPT_DIR}/modules/music_editor_ui.py"
cp "${REPO_ROOT}/runtime/perf_hud.py" "${SCRIPT_DIR}/modules/perf_hud.py"
cp "${REPO_ROOT}/runtime/update_ui.py" "${SCRIPT_DIR}/modules/update_ui.py"
cp "${REPO_ROOT}/runtime/system_menu_ui.py" "${SCRIPT_DIR}/modules/system_menu_ui.py"
cp "${REPO_ROOT}/runtime/achievements_ui.py" "${SCRIPT_DIR}/modules/achievements_ui.py"
cp "${REPO_ROOT}/runtime/layers.py" "${SCRIPT_DIR}/modules/layers.py"
cp "${REPO_ROOT}/runtime/bar_layer.py" "${SCRIPT_DIR}/modules/bar_layer.py"
cp "${REPO_ROOT}/runtime/cards_layer.py" "${SCRIPT_DIR}/modules/cards_layer.py"
cp "${REPO_ROOT}/runtime/paint_layer.py" "${SCRIPT_DIR}/modules/paint_layer.py"
cp "${REPO_ROOT}/runtime/settings_layer.py" "${SCRIPT_DIR}/modules/settings_layer.py"
cp "${REPO_ROOT}/runtime/code_layer.py" "${SCRIPT_DIR}/modules/code_layer.py"
cp "${REPO_ROOT}/runtime/widgets.py" "${SCRIPT_DIR}/modules/widgets.py"
cp "${REPO_ROOT}/runtime/wallpaper.py" "${SCRIPT_DIR}/modules/wallpaper.py"
cp "${REPO_ROOT}/runtime/artwork.py" "${SCRIPT_DIR}/modules/artwork.py"
cp "${REPO_ROOT}/runtime/appearance_app.py" "${SCRIPT_DIR}/modules/appearance_app.py"
cp "${REPO_ROOT}/runtime/app_shell.py" "${SCRIPT_DIR}/modules/app_shell.py"
cp "${REPO_ROOT}/runtime/writer_app.py" "${SCRIPT_DIR}/modules/writer_app.py"
cp "${REPO_ROOT}/runtime/storybook_app.py" "${SCRIPT_DIR}/modules/storybook_app.py"
cp "${REPO_ROOT}/runtime/sheets_app.py" "${SCRIPT_DIR}/modules/sheets_app.py"
cp "${REPO_ROOT}/runtime/formula.py" "${SCRIPT_DIR}/modules/formula.py"
cp "${REPO_ROOT}/runtime/launcher_layer.py" "${SCRIPT_DIR}/modules/launcher_layer.py"
cp "${REPO_ROOT}/runtime/project.py" "${SCRIPT_DIR}/modules/project.py"
cp "${REPO_ROOT}/runtime/player.py" "${SCRIPT_DIR}/modules/player.py"
cp "${REPO_ROOT}/runtime/editor_app.py" "${SCRIPT_DIR}/modules/editor_app.py"
cp "${REPO_ROOT}/runtime/wm.py" "${SCRIPT_DIR}/modules/wm.py"
cp "${REPO_ROOT}/runtime/chrome.py" "${SCRIPT_DIR}/modules/chrome.py"
cp "${REPO_ROOT}/runtime/ui.py" "${SCRIPT_DIR}/modules/ui.py"
cp "${REPO_ROOT}/runtime/calc_app.py" "${SCRIPT_DIR}/modules/calc_app.py"
cp "${REPO_ROOT}/runtime/console.py" "${SCRIPT_DIR}/modules/console.py"
cp "${REPO_ROOT}/runtime/moy_carts.py" "${SCRIPT_DIR}/modules/moy_carts.py"
cp "${REPO_ROOT}/runtime/moy_fs.py" "${SCRIPT_DIR}/modules/moy_fs.py"
cp "${REPO_ROOT}/runtime/moy_image.py" "${SCRIPT_DIR}/modules/moy_image.py"
cp "${REPO_ROOT}/runtime/moy_journal.py" "${SCRIPT_DIR}/modules/moy_journal.py"
cp "${REPO_ROOT}/runtime/blocks.py" "${SCRIPT_DIR}/modules/blocks.py"
cp "${REPO_ROOT}/runtime/web_view_ws.py" "${SCRIPT_DIR}/modules/web_view_ws.py"
cp "${REPO_ROOT}/runtime/web_view_page.py" "${SCRIPT_DIR}/modules/web_view_page.py"
cp "${REPO_ROOT}/runtime/web_view.py" "${SCRIPT_DIR}/modules/web_view.py"
cp "${REPO_ROOT}/runtime/font.py" "${SCRIPT_DIR}/modules/moy_font.py"
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

# External MSPI speed (#66, T-Deck S3): run octal flash + octal PSRAM at 120MHz.
# PSRAM 120 selects a 240MHz MSPI timing tuple; leaving flash at 80MHz hit an
# unsupported ESP-IDF 240/80 timing-table path, so the tested build moves both
# memories together. Keep the temperature retune OFF: on the T-Deck sample it
# aborts at secondary init because IDF only allows that hook for verified flash
# vendor IDs. MOYBYTE_EXTMEM_SPEED=stock restores the upstream 80/80 defaults for
# board-stability bisects.
EXTMEM_SPEED="${MOYBYTE_EXTMEM_SPEED:-fast}"
SDKCONFIG_SPIRAM_OCT="${UPSTREAM_DIR}/lib/micropython/ports/esp32/boards/sdkconfig.spiram_oct"
sed -i \
  -e '/^CONFIG_IDF_EXPERIMENTAL_FEATURES=y$/d' \
  -e '/^CONFIG_ESPTOOLPY_FLASHFREQ_80M=$/d' \
  -e '/^CONFIG_ESPTOOLPY_FLASHFREQ_120M=y$/d' \
  -e '/^CONFIG_SPIRAM_SPEED_80M=$/d' \
  -e '/^CONFIG_SPIRAM_SPEED_120M=y$/d' \
  -e '/^CONFIG_SPIRAM_TIMING_TUNING_POINT_VIA_TEMPERATURE_SENSOR=$/d' \
  -e '/^CONFIG_SPIRAM_TIMING_MEASURE_TEMPERATURE_INTERVAL_SECOND=/d' \
  "${SDKCONFIG_SPIRAM_OCT}"
case "${EXTMEM_SPEED}" in
  fast)
    for opt in \
      'CONFIG_IDF_EXPERIMENTAL_FEATURES=y' \
      'CONFIG_ESPTOOLPY_FLASHFREQ_80M=' \
      'CONFIG_ESPTOOLPY_FLASHFREQ_120M=y' \
      'CONFIG_SPIRAM_SPEED_80M=' \
      'CONFIG_SPIRAM_SPEED_120M=y' \
      'CONFIG_SPIRAM_TIMING_TUNING_POINT_VIA_TEMPERATURE_SENSOR='; do
      printf '%s\n' "${opt}" >> "${SDKCONFIG_SPIRAM_OCT}"
    done
    ;;
  stock)
    ;;
  *)
    echo "Unknown MOYBYTE_EXTMEM_SPEED=${EXTMEM_SPEED}; expected fast or stock" >&2
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
