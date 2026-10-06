# Moybyte ESP32 build library -- the blocks both board build.sh scripts share.
#
# SOURCED, not executed. Both boards build the same way (mainline MicroPython +
# out-of-tree board def + USER_C_MODULES), and until 2026-08-17 each build.sh
# carried its own copy of every step below -- near-verbatim twins that drifted
# exactly as twins do (the OTA stamp read FIRMWARE_VERSION from two different
# paths; the web blob was generated into the shared tree on one board and into
# the staged copy on the other). What stays in each build.sh is the genuinely
# per-board half: the patch ladder and the prose that explains the board. The
# sdkconfig options are not a list here either -- moybyte_sdkconfig_guard reads
# the board's own sdkconfig.board.
#
# Callers set (before sourcing or before the call that needs them):
#   REPO_ROOT SCRIPT_DIR BUILD_DIR MPY_DIR MPY_TAG DIST_DIR MODULES_DIR
# Every function says what else it reads.

# Parallelism for every make below (MOYBYTE_BUILD_JOBS overrides -- CI runners
# report more CPUs than they schedule).
BUILD_JOBS="${MOYBYTE_BUILD_JOBS:-$(nproc)}"

# Sets BUILD_PYTHON: the venv python when there is one (same interpreter the
# tests run), else the system python3. tools/board_config.py is stdlib-only ON
# PURPOSE (see its docstring): a board must be buildable on nothing but the
# system python3, without `make setup`. MOYBYTE_BUILD_PYTHON overrides.
moybyte_resolve_build_python() {
  BUILD_PYTHON="${MOYBYTE_BUILD_PYTHON:-}"
  if [ -z "${BUILD_PYTHON}" ]; then
    if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
      BUILD_PYTHON="${REPO_ROOT}/.venv/bin/python"
    else
      BUILD_PYTHON="python3"
    fi
  fi
}

# Mainline MicroPython at MPY_TAG in MPY_DIR: cloned when absent, and a warm
# tree whose commit is not the tag's is reset to stock MPY_TAG (the patchers
# below are idempotent from stock) or the build fails -- tools/mpy_tree.sh says
# how and why. A no-op, and silent, when the tree is already at the tag.
moybyte_clone_micropython() {
  bash "${REPO_ROOT}/tools/mpy_tree.sh" "${MPY_DIR}" "${MPY_TAG}"
}

# Resolve + activate ESP-IDF v5.5.1 for $1 (the idf.py target, e.g. esp32s3).
# Reuse an existing checkout when one is passed as $2..$n (they are ~500MB and
# the same version): an explicit IDF_DIR wins, then the candidates in order,
# then a clone into ${BUILD_DIR}/esp-idf.
#
# The toolchains + IDF python env live in ~/.espressif, OUTSIDE the esp-idf
# tree: a runner can have the tree (restored .build cache) but not the tools
# (evicted ~/.espressif cache). export.sh can NOT be trusted to report that --
# v5.5.1 ends in an unconditional `return 0` (only its inner activate.py
# fails) -- so probe the real outcome (idf.py on PATH) and self-heal with the
# official installer.
moybyte_setup_idf() {
  local chip="$1"; shift
  if [ -z "${IDF_DIR:-}" ]; then
    local cand
    for cand in "$@"; do
      if [ -f "${cand}/export.sh" ]; then IDF_DIR="${cand}"; break; fi
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
  set +u
  # shellcheck disable=SC1091
  source "${IDF_DIR}/export.sh" >/dev/null 2>&1 || true
  if ! command -v idf.py >/dev/null 2>&1; then
    echo "== ESP-IDF tools missing (fresh runner / evicted cache): running install.sh ${chip}"
    "${IDF_DIR}/install.sh" "${chip}"
    # shellcheck disable=SC1091
    source "${IDF_DIR}/export.sh" >/dev/null
    command -v idf.py >/dev/null 2>&1 || { echo "!! idf.py still missing after install.sh" >&2; exit 1; }
  fi
  set -u
  moybyte_ccache
}

# One compiler cache for every checkout of this repository on this machine: the
# main checkout and each worktree (tools/worktree.py) build through ccache's
# one directory. Paths under the main checkout are hashed relative to the
# build directory (CCACHE_BASEDIR) and the build directory itself is not
# hashed (CCACHE_NOHASHDIR: only the ELF's debug info names it, never the
# .bin), so a worktree's first build reuses what another tree compiled. The
# IDF is named by the path the build was given (a worktree's link), not by
# the directory activate.py resolves it to, so its sources sit at the same
# relative place from every tree's build. Local only: CI sets its own
# IDF_CCACHE_ENABLE and keeps its cache per board. Each setting the caller
# made wins.
moybyte_ccache() {
  [ -z "${CI:-}" ] || return 0
  command -v ccache >/dev/null 2>&1 || return 0
  local common
  common="$(git -C "${REPO_ROOT}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 0
  export IDF_CCACHE_ENABLE="${IDF_CCACHE_ENABLE:-1}"
  export CCACHE_BASEDIR="${CCACHE_BASEDIR:-$(dirname "${common}")}"
  export CCACHE_NOHASHDIR="${CCACHE_NOHASHDIR:-1}"
  export IDF_PATH="${IDF_DIR}"
}

# Append an IDF component to the esp32 port's IDF_COMPONENTS list (idempotent).
# USER_C_MODULES is skipped during idf.py's early-expansion phase -- exactly
# when REQUIRES are collected -- so appending IDF_COMPONENTS from a usermod
# cmake can never work; the port's own list is the only place. Reads MPY_DIR.
moybyte_idf_component() {
  local comp="$1"
  local common_cmake="${MPY_DIR}/ports/esp32/esp32_common.cmake"
  if ! grep -q "^    ${comp}\$" "${common_cmake}"; then
    sed -i "/^list(APPEND IDF_COMPONENTS\$/a\\    ${comp}" "${common_cmake}"
    echo "== patched esp32_common.cmake: added ${comp} to IDF_COMPONENTS"
  fi
}

# Un-static esp_native_code_free_all (#66) so a cart-compile miss can reclaim
# the @micropython.native exec arena (otherwise grow-only until soft reset).
# moy_gfx binds it as a weak symbol, so builds work either way -- but the
# repeat-run cliff is real on BOTH boards (the P4's RV32 emitter feeds the same
# grow-only list, just from a bigger pool). Reads MPY_DIR, REPO_ROOT.
moybyte_patch_native_code_free() {
  if ! grep -q "moybyte_native_code_free" "${MPY_DIR}/ports/esp32/mpconfigport.h"; then
    echo "== applying native-code-free patch (#66)"
    patch -d "${MPY_DIR}" -p1 < "${REPO_ROOT}/patches/esp32_native_code_free.patch"
  fi
}

# ESP-NOW ring torn-read race (#7, measured 2026-08-24). modespnow's recv_cb
# (WiFi task) writes header/peer/msg as three separate ringbuf puts while
# recvinto waits only for the HEADER -- a reader that catches a record
# mid-write raises "ESPNow.recv(): buffer error" on a healthy ring and leaves
# it genuinely desynced (header consumed, body not). A busy irecv(0) drain
# hits the window about once a second on glass; every hit forces the link's
# _recover() active-cycle, which costs a packet burst mid-match. The patch
# waits (bounded, 10ms) for the body the writer commits microseconds later.
# All three radio boards call this; the wasm build compiles no modespnow.
# Reads MPY_DIR, REPO_ROOT.
moybyte_patch_espnow_ring_race() {
  if ! grep -q "Moybyte espnow_ring_race" "${MPY_DIR}/ports/esp32/modespnow.c"; then
    echo "== applying espnow ring torn-read patch (#7)"
    patch -d "${MPY_DIR}" -p1 < "${REPO_ROOT}/patches/esp32_espnow_ring_race.patch"
  fi
}

# REPR_C unboxed floats (#66). REPR_A boxes every float RESULT in 16 bytes of
# heap (~73KB/frame measured in sakura), and the heap-wrap gc collect that
# follows is a 130-175ms visible hitch. A GUARDED SED rather than a context
# diff, so it survives the line moving between MicroPython releases -- and the
# guard matters more than the edit, because a silent no-op here is a board that
# quietly runs boxed floats again with nothing naming the cause. Reads MPY_DIR.
#
# Per-board, and OPT-IN: a board that does not call this declines it in its own
# build.sh, in writing, the way board.toml's denials carry a `why`.
moybyte_patch_repr_c() {
  local h="${MPY_DIR}/ports/esp32/mpconfigport.h"
  if ! grep -q "MICROPY_OBJ_REPR_C" "${h}"; then
    sed -i 's|^#define MICROPY_OBJ_REPR  *(MICROPY_OBJ_REPR_A)|#define MICROPY_OBJ_REPR                    (MICROPY_OBJ_REPR_C) /* Moybyte #66: unboxed 30-bit floats */|' \
      "${h}"
    grep -q "MICROPY_OBJ_REPR_C" "${h}" || {
      echo "!! REPR_C patch did not apply -- mpconfigport.h's MICROPY_OBJ_REPR line changed shape" >&2
      exit 1
    }
    echo "== patched mpconfigport.h: MICROPY_OBJ_REPR_C (#66)"
  fi
}

# The map-lookup cache's slot index, re-aimed for REPR_C (#77). py/map.c
# picks the slot as `index >> 2` -- "shift down by two to remove the tag
# bits", which is REPR_A's qstr layout `(q << 2) | 2`. REPR_C tags a qstr as
# `(q << 4) | 6` (py/obj.h), so after `>> 2` the two low bits are constant for
# EVERY qstr key and the 128-slot cache offers attribute, global and method
# lookups 32 slots; a gc-block pointer key (16-byte blocks) reaches the same
# 32. Shifting by the width of the tag REPR_C actually uses gives all 128 back
# for no RAM at all. Small-int keys fold 8-to-1 in exchange, which is a
# false-negative hint that falls through to the normal probe, on the rarest
# key kind. On its own this measured NULL on three boards; it pays together
# with the 512-slot table each console board's mpconfigboard.h declares.
#
# A consequence of REPR_C and REFUSED without it: a REPR_A board (the Zero
# declines REPR_C) is right at 2 and would be four times WORSE at 4, so this
# checks that moybyte_patch_repr_c has run on the tree first. Same
# guarded-sed shape, and the same reason the guard is the point. Reads
# MPY_DIR.
moybyte_patch_map_cache_for_repr_c() {
  local f="${MPY_DIR}/py/map.c"
  grep -q "MICROPY_OBJ_REPR_C" "${MPY_DIR}/ports/esp32/mpconfigport.h" || {
    echo "!! map-cache shift refused -- this tree is not REPR_C (call moybyte_patch_repr_c first, or decline both)" >&2
    exit 1
  }
  if ! grep -q "Moybyte: REPR_C tags a qstr" "${f}"; then
    sed -i 's|^#define MAP_CACHE_OFFSET(index) ((((uintptr_t)(index)) >> 2) % MICROPY_OPT_MAP_LOOKUP_CACHE_SIZE)$|#define MAP_CACHE_OFFSET(index) ((((uintptr_t)(index)) >> 4) % MICROPY_OPT_MAP_LOOKUP_CACHE_SIZE) /* Moybyte: REPR_C tags a qstr in 4 bits, not 2 */|' \
      "${f}"
    grep -q "Moybyte: REPR_C tags a qstr" "${f}" || {
      echo "!! map-cache shift patch did not apply -- py/map.c's MAP_CACHE_OFFSET line changed shape" >&2
      exit 1
    }
    echo "== patched py/map.c: MAP_CACHE_OFFSET >> 4 for REPR_C"
  fi
}

# Size-class free-run hints for gc_alloc (#66). The stock allocator keeps one
# hint per heap area and a multi-block allocation never advances it, so each
# one re-walks every hole below the live frontier that is too small for it --
# 100-800 us a call on the S3 boards with a cart up. tools/patch_gc_run_hints.py
# is the patch and its own documentation: all-or-nothing, idempotent, and it
# refuses a tree whose lines changed shape. Independent of REPR_C.
moybyte_patch_gc_run_hints() {
  [ -n "${BUILD_PYTHON:-}" ] || moybyte_resolve_build_python
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/patch_gc_run_hints.py" "${MPY_DIR}" || exit 1
}

# The gc meters: gc.pauses() (collections and their pause, for the PERF line)
# and gc.areas() (the split heap's areas and bytes held, for `heapcaps`); and
# the census's three reads, gc.area_map() (each area's range, held, used,
# largest free run), gc.growths() (every area added or freed, with the
# request that grew it) and gc.refs() (the heap blocks holding an address). tools/patch_gc_meters.py and tools/patch_gc_census.py
# are the patches and their own documentation: all-or-nothing, idempotent,
# independent of each other and of the run hints.
moybyte_patch_gc_meters() {
  [ -n "${BUILD_PYTHON:-}" ] || moybyte_resolve_build_python
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/patch_gc_meters.py" "${MPY_DIR}" || exit 1
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/patch_gc_census.py" "${MPY_DIR}" || exit 1
}

# A 4 KB stdin ring, in the ESP32-P4's TCM, for a board whose serial is a
# UART, and an RX ISR that wakes the reader. The port's stock ring is 260 bytes
# and the UART has no flow control, so a heap collection landing while a long
# line arrives drops bytes with no error; and the stock UART ISR never wakes
# the MicroPython task, so a reader waiting on the ring sleeps out its tick.
# tools/patch_stdin_ring.py is the patch, its sizing and its placement:
# all-or-nothing per file, idempotent. A USB-Serial/JTAG board backpressures,
# already wakes its reader, and declines it.
moybyte_patch_stdin_ring() {
  [ -n "${BUILD_PYTHON:-}" ] || moybyte_resolve_build_python
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/patch_stdin_ring.py" "${MPY_DIR}" || exit 1
}

# The USB-Serial/JTAG console takes, when it starts, the bytes a host sent
# before it did. The stock init clears the RX interrupt of a packet that
# landed during the bootloader and leaves the packet in the FIFO until the
# first read of stdin, a whole boot later; meanwhile a host that reopens the
# port gets its line-state request delivered as data, 0x03 included -- Ctrl-C
# to the boot. tools/patch_usj_rx_init.py is the patch and the measurement;
# idempotent. A board whose console is a UART declines it.
moybyte_patch_usj_rx_init() {
  [ -n "${BUILD_PYTHON:-}" ] || moybyte_resolve_build_python
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/patch_usj_rx_init.py" "${MPY_DIR}" || exit 1
}

# LittleFS's default program size and lookahead, 32 -> 256 each, so a P4's
# flash store programs 1 KB of a file at a time instead of 128 bytes and walks
# the filesystem for free blocks an eighth as often. tools/patch_lfs_sizes.py is
# the patch and the measurements; all-or-nothing, idempotent. Only a board
# whose cart store is its internal flash takes it.
moybyte_patch_lfs_sizes() {
  [ -n "${BUILD_PYTHON:-}" ] || moybyte_resolve_build_python
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/patch_lfs_sizes.py" "${MPY_DIR}" || exit 1
}

# machine.SDCard moves sectors in multi-block runs through an internal DMA
# bounce, where IDF moves a PSRAM buffer one single-block command per sector.
# tools/patch_sdcard_runs.py is the patch; all-or-nothing, idempotent. Only a
# board whose card is machine.SDCard takes it.
moybyte_patch_sdcard_runs() {
  [ -n "${BUILD_PYTHON:-}" ] || moybyte_resolve_build_python
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/patch_sdcard_runs.py" "${MPY_DIR}" || exit 1
}

# Split-heap growth reserve. MicroPython's esp32 port grows the Python heap
# on demand by DOUBLING it, from the same ESP heap the Lua VM, the panel DMA
# and the layer pool allocate from, and gives an area back only when a sweep
# finds it wholly empty. On an 8MB
# S3 board one fragmented Python allocation took every remaining byte of
# PSRAM and every big cart then failed at load with Lua's "not enough
# memory" until a hard reset (2026-09-02, #66). The patch caps what a growth
# may take so MOYBYTE_GC_SPLIT_RESERVE bytes of PSRAM always stay outside
# the Python heap; a board that defines nothing reserves nothing. A GUARDED
# SED on the one-line port hook, like repr_c. Reads MPY_DIR.
moybyte_patch_gc_split_reserve() {
  local f="${MPY_DIR}/ports/esp32/gccollect.c"
  if ! grep -q "MOYBYTE_GC_SPLIT_RESERVE" "${f}"; then
    sed -i 's|^    return heap_caps_get_largest_free_block(MALLOC_CAP_DEFAULT);|    size_t avail = heap_caps_get_largest_free_block(MALLOC_CAP_DEFAULT);\n    size_t psram = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);   /* Moybyte: MOYBYTE_GC_SPLIT_RESERVE stays C-side */\n    if (psram <= MOYBYTE_GC_SPLIT_RESERVE) return 0;\n    return avail < psram - MOYBYTE_GC_SPLIT_RESERVE ? avail : psram - MOYBYTE_GC_SPLIT_RESERVE;|' "${f}"
    sed -i 's|^size_t gc_get_max_new_split(void) {|#ifndef MOYBYTE_GC_SPLIT_RESERVE\n#define MOYBYTE_GC_SPLIT_RESERVE 0\n#endif\nsize_t gc_get_max_new_split(void) {|' "${f}"
    grep -q "psram - MOYBYTE_GC_SPLIT_RESERVE" "${f}" || {
      echo "!! gc split reserve patch did not apply -- gccollect.c's gc_get_max_new_split changed shape" >&2
      exit 1
    }
    echo "== patched gccollect.c: split-heap growth keeps MOYBYTE_GC_SPLIT_RESERVE of PSRAM (#66)"
  fi
}

# The two ESP32-P4 SILICON patches, one-shot marker-guarded like the rest --
# shared by every P4 board since 2026-09-06 (they were the Waveshare's own
# `patches/` until the Guition P4 became their second consumer):
#
#   ble_hid_fastpath  Steady-state BLE keyboard notifications must not wait
#                     behind MicroPython's synchronous NimBLE IRQ/GIL path.
#                     native/p4/moy_ble_hid's queue consumes registered HID
#                     handles before Python dispatch; pairing/bonding/
#                     discovery stay on the supported synchronous path.
#                     Patches THIS board's MicroPython checkout.
#   dsi_underrun      #106: backport current ESP-IDF's dedicated DSI
#                     bridge-underrun ISR and keep the frame-restart DW-GDMA
#                     interrupt above ESP-Hosted's SDIO interrupt. IDF v5.5
#                     checks the bridge only from the DMA callback; if SDIO
#                     delays that callback the panel has already gone blue.
#                     Patches the (possibly shared) ESP-IDF checkout, so the
#                     marker is what makes two P4 builds over one IDF safe.
moybyte_patch_p4_ble_hid_fastpath() {
  local f="${MPY_DIR}/extmod/modbluetooth.c"
  if [ -f "${f}" ] && ! grep -q "moy_ble_hid_queue_on_notify" "${f}"; then
    echo "== applying P4 BLE-HID native notification fast-path patch"
    patch -d "${MPY_DIR}" -p1 < "${REPO_ROOT}/patches/p4_modbluetooth_ble_hid_fastpath.patch"
  fi
}

moybyte_patch_p4_dsi_underrun() {
  local f="${IDF_DIR}/components/esp_lcd/dsi/esp_lcd_panel_dpi.c"
  if [ -f "${f}" ] && \
     ! grep -q "Moybyte P4: dedicated DSI bridge underrun IRQ" "${f}"; then
    echo "== applying P4 DSI bridge IRQ/priority fix (#106)"
    patch -d "${IDF_DIR}" -p1 < "${REPO_ROOT}/patches/p4_esp_lcd_dsi_underrun_hook.patch"
  fi
}

# PSRAM temperature retune, un-gated by flash vendor (#169). REQUIRED by the
# 120MHz octal MSPI profile, not optional beside it: IDF only starts the retune
# for verified flash vendor IDs (0xC8/0x20) and otherwise returns
# ESP_ERR_NOT_SUPPORTED from a SECONDARY ESP_SYSTEM_INIT_FN -- which aborts the
# boot. The board then flashes cleanly, says NOTHING on serial and never reaches
# the console, which reads exactly like a PSRAM timing failure and is not one
# (measured 2026-08-16). The patch relaxes the vendor gate to warn-and-run and
# turns the task's other brick path -- an abort() when the scanned points share
# no temperature range -- into "stop adjusting", degrading to the un-mitigated
# build rather than a dead one. Inert, not wrong, at 80MHz.
#
# ESP32-S3 only: the file it patches is the S3 port of the MSPI timing tuner.
# Reads IDF_DIR, REPO_ROOT.
moybyte_patch_psram_retune() {
  local f="${IDF_DIR}/components/esp_hw_support/mspi_timing_tuning/port/esp32s3/mspi_timing_by_mspi_delay.c"
  if [ -f "${f}" ] && ! grep -q "Moybyte #169" "${f}"; then
    echo "== applying PSRAM temperature-retune vendor-gate patch (#169)"
    patch -d "${IDF_DIR}" -p1 < "${REPO_ROOT}/patches/esp_psram_temp_retune_any_vendor.patch"
  fi
}

# Stage the shared native modules per board.toml [native.shared] and generate
# the web-console blob INTO THE STAGED COPY (never into the shared native/
# tree two builds read). Reads SCRIPT_DIR, REPO_ROOT, BUILD_PYTHON. A missing
# web bundle only WARNS locally and FAILS under CI/MOYBYTE_REQUIRE_WEB_BUNDLE,
# because a PUBLISHED image with no console is the whole bug the baking fixes.
moybyte_stage_native() {
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" stage-native "${SCRIPT_DIR}"
  # WHERE it staged them is the board's to say ([native] dest). Asked rather
  # than restated: a board moving its dest would otherwise leave the blob
  # ungenerated, and an image with no console is a silent pass.
  local dest
  dest="$("${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" native-dest "${SCRIPT_DIR}")"
  local staged="${SCRIPT_DIR}/${dest}"
  if [ -d "${staged}/moy_web" ]; then
    local args=(--out "${staged}/moy_web/moy_web_blob.gen.c")
    if [ -n "${CI:-}" ] || [ "${MOYBYTE_REQUIRE_WEB_BUNDLE:-0}" = "1" ]; then
      args+=(--require)
    fi
    "${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_web_blob.py" "${args[@]}"
  fi
}

# The OTA build identity (#53): $1 is the board id inside the signed manifest
# ("tdeck"/"p4"), $2 the path to the moy_ota.py this image freezes (the SHARED
# device/moy_ota.py -- both boards freeze a staged copy of the same file, so
# both read the same source of FIRMWARE_VERSION/FIRMWARE_NAME). Writes
# ${MODULES_DIR}/_ota_build.py and ${DIST_DIR}/ota_build.json (which also
# names the commit), and echoes the identity. The CHANNEL is a BUILD choice
# (MOYBYTE_OTA_CHANNEL, default stable), so it stays clean across merges; a
# beta's VERSION is the build epoch, auto-newer on every publish.
moybyte_ota_identity() {
  local board_id="$1" ota_py="$2"
  OTA_CHANNEL="${MOYBYTE_OTA_CHANNEL:-stable}"
  if [ -n "${MOYBYTE_OTA_VERSION:-}" ]; then
    OTA_VERSION="${MOYBYTE_OTA_VERSION}"
  elif [ "${OTA_CHANNEL}" = "unstable" ]; then
    OTA_VERSION="$(date +%s)"                 # monotonic per-build beta version
  else
    OTA_VERSION="$(grep -oE 'FIRMWARE_VERSION = [0-9]+' "${ota_py}" | head -1 | grep -oE '[0-9]+')"
    OTA_VERSION="${OTA_VERSION:-1}"
  fi
  if [ "${OTA_CHANNEL}" = "unstable" ]; then
    OTA_LABEL="beta $(date '+%Y-%m-%d %H:%M')"
  else
    # The human release name (FIRMWARE_NAME, set by `make release NAME=`), NOT
    # the ordering counter -- "0.6" is what the update screen and the manifest
    # show.
    local ota_name
    ota_name="$(grep -oE '^FIRMWARE_NAME = "[^"]*"' "${ota_py}" | head -1 | cut -d'"' -f2)"
    OTA_LABEL="${ota_name:-v${OTA_VERSION}}"
  fi
  cat > "${MODULES_DIR}/_ota_build.py" <<EOF
# AUTO-GENERATED by build.sh -- moy_ota imports this for the build's OTA identity.
# Gitignored; do not edit or commit.
CHANNEL = "${OTA_CHANNEL}"
VERSION = ${OTA_VERSION}
LABEL = "${OTA_LABEL}"
BOARD = "${board_id}"
EOF
  # The commit the image is built from (REPO_ROOT's), `+` when tracked files
  # differ from it. The JSON only: the image does not change with the commit.
  local commit="unknown"
  if [ -n "${REPO_ROOT:-}" ] && commit="$(git -C "${REPO_ROOT}" rev-parse --short=8 HEAD 2>/dev/null)"; then
    git -C "${REPO_ROOT}" diff --quiet HEAD -- 2>/dev/null || commit="${commit}+"
  else
    commit="unknown"
  fi
  mkdir -p "${DIST_DIR}"
  cat > "${DIST_DIR}/ota_build.json" <<EOF
{"channel": "${OTA_CHANNEL}", "version": ${OTA_VERSION}, "label": "${OTA_LABEL}", "board": "${board_id}", "commit": "${commit}"}
EOF
  echo "OTA build identity: board=${board_id} channel=${OTA_CHANNEL} version=${OTA_VERSION} label='${OTA_LABEL}'"
}

# Write the frozen manifest ($1) for MODULES_DIR: the port's default frozen
# stdlib + this board's modules. The md5 fingerprint makes the manifest CONTENT
# change whenever any frozen source changes -- ninja rests custom commands on
# identical manifest text, so without it a changed .py silently ships as stale
# .mpy.
moybyte_frozen_manifest() {
  local manifest="$1"
  rm -rf "${MODULES_DIR}/__pycache__" "${MODULES_DIR}/moybyte/__pycache__"
  cat > "${manifest}" <<EOF
include("\$(PORT_DIR)/boards/manifest.py")
freeze("${MODULES_DIR}", opt=3)
EOF
  echo "# frozen-source fingerprint: $(find "${MODULES_DIR}" -type f -name '*.py' -exec md5sum {} + 2>/dev/null | sort | md5sum | cut -d' ' -f1)" >> "${manifest}"
}

# Apply the board's sdkconfig fragment: stage the partition table it names,
# and make sure the settings it decided are the settings this build carries.
# $1 is the board def dir (boards/<BOARD>), $2 the GENERATED sdkconfig.
# Reads BUILD_PYTHON REPO_ROOT MPY_DIR MPY_TAG; exports BOARD_PARTITION_CSV.
#
# `boards/<BOARD>/sdkconfig.board` is the ONE store of a board's decided IDF
# settings, prose included; nothing here restates it -- both the required
# option list and the partition-table filename are read out of it.
#
# Two different questions, answered separately because their right answers
# differ:
#
#   1. "Was this build tree configured from the current inputs?" -- a STAMP
#      over the fragment, mpconfigboard.cmake and MPY_TAG. IDF only generates a
#      build's sdkconfig when the file is ABSENT, so on a mismatch drop the
#      generated one. A stamp and not a grep list: it also catches a setting
#      REMOVED from the fragment, a change to which upstream fragments the
#      board pulls in, and a MicroPython tag bump underneath both.
#
#   2. "Did IDF honour what the fragment asked for?" -- a grep of the generated
#      config, meaningful ONLY once the stamp matches. A setting still missing
#      then is INERT because Kconfig refused it (e.g. an out-of-range value:
#      CONFIG_BT_CTRL_BLE_ADV_REPORT_FLOW_CTRL_NUM is `range 50 1000`, so a 20
#      silently stays 100). Deleting the sdkconfig would not fix that, it would
#      just rebuild the world forever, so this reports instead.
moybyte_sdkconfig_guard() {
  local board_dir="$1" gen="$2"
  local fragment="${board_dir}/sdkconfig.board"
  local cmake="${board_dir}/mpconfigboard.cmake"
  local bc="${REPO_ROOT}/tools/board_config.py"
  local f
  for f in "${fragment}" "${cmake}"; do
    [ -f "${f}" ] || { echo "!! missing ${f}" >&2; exit 1; }
  done

  # The partition table, named ONCE -- by the setting that has to name it.
  local csv_name
  csv_name="$("${BUILD_PYTHON}" "${bc}" sdkconfig-get "${board_dir}" \
              CONFIG_PARTITION_TABLE_CUSTOM_FILENAME)"
  BOARD_PARTITION_CSV="${board_dir}/${csv_name}"
  [ -f "${BOARD_PARTITION_CSV}" ] || {
    echo "!! sdkconfig.board names ${csv_name}, which is not in ${board_dir}" >&2
    exit 1; }
  # CONFIG_PARTITION_TABLE_CUSTOM_FILENAME resolves relative to ports/esp32.
  cp "${BOARD_PARTITION_CSV}" "${MPY_DIR}/ports/esp32/${csv_name}"

  local build_dir stamp want
  build_dir="$(dirname "${gen}")"
  stamp="${build_dir}/.moybyte-sdkconfig-stamp"
  want="$( { cat "${fragment}" "${cmake}"; echo "MPY_TAG=${MPY_TAG}"; } \
           | md5sum | cut -d' ' -f1)"

  if [ -f "${gen}" ]; then
    if [ ! -f "${stamp}" ] || [ "$(cat "${stamp}")" != "${want}" ]; then
      echo "== sdkconfig inputs changed since this build tree was configured -- regenerating"
      rm -f "${gen}"
    else
      moybyte_sdkconfig_report_inert "${board_dir}" "${gen}"
    fi
  fi
  mkdir -p "${build_dir}"
  printf '%s\n' "${want}" > "${stamp}"
}

# Report every decided setting the generated sdkconfig does not carry, with the
# fragment's prose for it. Only ever called when the stamp says this tree WAS
# configured from this fragment, so a miss is Kconfig's refusal, not staleness.
# WARNS locally and FAILS under CI / MOYBYTE_REQUIRE_SDKCONFIG=1.
moybyte_sdkconfig_report_inert() {
  local board_dir="$1" gen="$2"
  local bc="${REPO_ROOT}/tools/board_config.py"
  local want opt inert=0
  while IFS= read -r want; do
    [ -n "${want}" ] || continue
    grep -qxF "${want}" "${gen}" && continue
    opt="${want%%=*}"
    inert=1
    echo "" >&2
    echo "!! sdkconfig: $(basename "${board_dir}") asks for ${want}, and the build does not have it" >&2
    echo "!!   generated: $(grep -E "^(${opt}=|# ${opt} is not set)" "${gen}" \
                            || echo "(no such option -- renamed, or its dependency is off)")" >&2
    "${BUILD_PYTHON}" "${bc}" sdkconfig-why "${board_dir}" "${opt}" 2>/dev/null \
      | sed 's/^/!!   /' >&2 || true
  done < <("${BUILD_PYTHON}" "${bc}" sdkconfig-required "${board_dir}")
  if [ "${inert}" = "1" ]; then
    echo "" >&2
    echo "!! Kconfig refused the setting(s) above (a range, a missing dependency," >&2
    echo "!! or a renamed option). Fix the value in sdkconfig.board or delete the" >&2
    echo "!! line -- a setting that does nothing is worse than no setting, because" >&2
    echo "!! the comment beside it says the board is tuned when it is not." >&2
    if [ -n "${CI:-}" ] || [ "${MOYBYTE_REQUIRE_SDKCONFIG:-0}" = "1" ]; then
      exit 1
    fi
  fi
}

# The #168 size guard: the app image ($2) must fit the ota_0 slot read from
# the board's OWN partition table ($1) -- read, not restated, so the check
# cannot drift from the layout it is checking. An overflow FAILS: an image
# that does not fit cannot be cable-flashed and cannot be installed over OTA,
# so the alternatives to stopping here are esptool refusing it later or a
# published payload no board can take. MOYBYTE_APP_SLOT_BYTES overrides for a
# what-if; MOYBYTE_APP_HEADROOM_WARN_BYTES tunes the warning (default 200KB).
moybyte_app_size_guard() {
  local csv="$1" app_bin="$2"
  local slot_hex slot_bytes warn_bytes size_bytes headroom
  slot_hex="$(awk -F',' '/^[[:space:]]*ota_0[[:space:]]*,/ { gsub(/[[:space:]]/, "", $5); print $5; exit }' "${csv}")"
  slot_bytes="${MOYBYTE_APP_SLOT_BYTES:-$(( ${slot_hex:-0x400000} ))}"
  warn_bytes="${MOYBYTE_APP_HEADROOM_WARN_BYTES:-204800}"
  size_bytes="$(wc -c < "${app_bin}" | tr -d '[:space:]')"
  headroom=$(( slot_bytes - size_bytes ))
  printf 'App image: %s bytes of a %s-byte ota_0 slot -- %s bytes headroom (%s KB)\n' \
    "${size_bytes}" "${slot_bytes}" "${headroom}" "$(( headroom / 1024 ))"
  if [ "${headroom}" -lt 0 ]; then
    echo "" >&2
    echo "!! Moybyte BUILD FAILED (#168): the app image does not fit its OTA slot" >&2
    echo "!!   slot:     ${slot_bytes} bytes (ota_0/ota_1, $(basename "${csv}"))" >&2
    echo "!!   image:    ${size_bytes} bytes" >&2
    echo "!!   OVERFLOW: $(( -headroom )) bytes ($(( (-headroom) / 1024 )) KB)" >&2
    echo "!! Trim it (the baked web console is ~573KB -- see tools/gen_web_blob.py)" >&2
    echo "!! or change the partition table, which costs every deployed device a" >&2
    echo "!! full-erase USB flash." >&2
    exit 1
  elif [ "${headroom}" -lt "${warn_bytes}" ]; then
    echo "Moybyte WARNING (#168): under $(( warn_bytes / 1024 ))KB of OTA-slot headroom left -- trim the image or plan the next table change" >&2
  fi
}

# The build + collect step -- identical on both boards down to the artifact
# names. Builds mpy-cross, then the port (both -j BUILD_JOBS), then copies the
# two images out and runs the #168 size guard:
#   ${DIST_DIR}/<stem>.bin      bootloader + table + app merged (cable flash;
#                               the offset differs per board -- $3 is the note)
#   ${DIST_DIR}/<stem>_app.bin  the APP partition image, what an OTA writes
#                               into the inactive slot. Handing the merged
#                               image to esp32.Partition would write a
#                               bootloader into an app slot.
# Reads: MPY_DIR BOARD BOARD_DIR SCRIPT_DIR MANIFEST DIST_DIR BUILD_JOBS.
# Leaves the cwd in ports/esp32 (both scripts end here).
moybyte_build_and_collect() {
  local csv="$1" stem="$2" flash_note="$3"
  make -C "${MPY_DIR}/mpy-cross" -j"${BUILD_JOBS}"
  cd "${MPY_DIR}/ports/esp32"
  make submodules BOARD_DIR="${BOARD_DIR}"
  local bout="build-${BOARD}"
  # MicroPython keeps a source's generated qstr and module entries until that
  # source is preprocessed again, so a build that drops a usermod source
  # (MOY_INDEX_IMPL or MOY_SPINE_IMPL back to py, tools/moy_index_spike.py)
  # would link a module table naming code it no longer has. A changed hook
  # starts genhdr afresh.
  local index_hook="${MOY_INDEX_IMPL:-py}${MOY_INDEX_BENCH:++bench}"
  local spine_hook="${MOY_SPINE_IMPL:-py}"
  if [ "$(cat "${bout}/moy_index_impl" 2>/dev/null || echo py)" != "${index_hook}" ] \
     || [ "$(cat "${bout}/moy_spine_impl" 2>/dev/null || echo py)" != "${spine_hook}" ]; then
    rm -rf "${bout}/genhdr"
  fi
  make -j"${BUILD_JOBS}" BOARD_DIR="${BOARD_DIR}" \
    USER_C_MODULES="${SCRIPT_DIR}/native/micropython.cmake" \
    FROZEN_MANIFEST="${MANIFEST}"
  echo "${index_hook}" > "${bout}/moy_index_impl"
  echo "${spine_hook}" > "${bout}/moy_spine_impl"
  cp "${bout}/firmware.bin" "${DIST_DIR}/${stem}.bin"
  cp "${bout}/micropython.bin" "${DIST_DIR}/${stem}_app.bin"
  moybyte_app_size_guard "${csv}" "${DIST_DIR}/${stem}_app.bin"
  echo "OK -> ${DIST_DIR}/${stem}.bin (${flash_note})"
  echo "OK -> ${DIST_DIR}/${stem}_app.bin (OTA payload, app partition)"
}

# ESP-Hosted 2.7.0 -> 2.12.12 (the espnow-on-p4 track,
# docs/history/espnow_p4_2026-08.md). MicroPython pins the hosted component at
# exactly 2.7.0; 2.12.12 carries the custom-RPC seam
# (esp_hosted_send_custom_data / register_custom_callback) the P4's ESP-NOW shim
# rides, plus the streamed slave-OTA API that updates the C6 over SDIO.
# esp_wifi_remote 0.15.2 constrains only >=0.0.6, so the bump is manifest-legal.
# PROVEN ON GLASS 2026-08-24 against the FACTORY C6 slave before any shim
# existed: builds clean, boots clean (with the MEMPOOL_PREFER_SPIRAM fragment
# line -- without it the 2.12 transport mempool fails its internal-SRAM
# allocation at boot and the board crash-loops), wifi at RX parity, BLE up and
# scanning. The stale per-target lockfile is dropped so the component manager
# re-resolves; it pins the new tree on first build.
#
# ESP32-P4 only: it is the C6-over-SDIO arrangement that needs hosted at all.
# Takes the IDF target whose lockfile to drop. Reads MPY_DIR.
moybyte_patch_esp_hosted_bump() {
  local target="${1:-esp32p4}"
  local manifest="${MPY_DIR}/ports/esp32/main/idf_component.yml"
  if grep -q 'version: "2.7.0"' "${manifest}"; then
    echo "== bumping esp_hosted 2.7.0 -> 2.12.12 (espnow-on-p4 track)"
    sed -i 's/^    version: "2.7.0"$/    version: "2.12.12"/' "${manifest}"
    grep -q 'version: "2.12.12"' "${manifest}" || {
      echo "!! esp_hosted bump did not apply -- idf_component.yml changed shape" >&2
      exit 1
    }
    rm -f "${MPY_DIR}/ports/esp32/lockfiles/dependencies.lock.${target}"
    rm -rf "${MPY_DIR}/ports/esp32/managed_components/espressif__esp_hosted"
  fi
}
