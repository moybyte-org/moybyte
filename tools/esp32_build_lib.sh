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
#   REPO_ROOT SCRIPT_DIR BUILD_DIR MPY_DIR DIST_DIR MODULES_DIR
# Every function says what else it reads.

# The pinned upstream releases, MOYBYTE_MPY_TAG and MOYBYTE_IDF_VER, from the
# one file that names them. MPY_TAG in the environment overrides the tag for
# one build; nothing overrides the ESP-IDF release.
# shellcheck source=upstream_pins.env
source "$(dirname "${BASH_SOURCE[0]}")/upstream_pins.env"
MPY_TAG="${MPY_TAG:-${MOYBYTE_MPY_TAG}}"

# Parallelism for every make below (MOYBYTE_BUILD_JOBS overrides -- CI runners
# report more CPUs than they schedule).
BUILD_JOBS="${MOYBYTE_BUILD_JOBS:-$(nproc)}"

# Where a board build's time goes. A build.sh that sources this lib is timed
# from that moment: each `moybyte_phase NAME` ends the phase before it with a
# `== time:` line, and the build's exit, passing or failing, prints every
# phase, the total and the compiler cache's hits, and appends them to the
# machine's shared timing log (tools/timings.py, tool `build`, lane the
# board's [board] ota id). The port build splits into configure, compile,
# link and image (elf2image, the merged image, the size checks) from its own
# output when that output is not a terminal; on a terminal it stays one
# `port` phase, so ninja keeps its one status line.
MOYBYTE_T0=""
MOYBYTE_PHASE=""
MOYBYTE_PHASE_T=0
MOYBYTE_PHASES=()

moybyte_dur() {                 # moybyte_dur MS -> 3.2s / 14m32s
  local ms="$1"
  if [ "${ms}" -lt 60000 ]; then
    printf '%d.%ds' $(( ms / 1000 )) $(( ms % 1000 / 100 ))
  else
    printf '%dm%02ds' $(( ms / 60000 )) $(( ms % 60000 / 1000 ))
  fi
}

moybyte_phase() {               # moybyte_phase NAME [START_US]
  [ -n "${MOYBYTE_T0}" ] || return 0
  local now="${2:-${EPOCHREALTIME//[.,]/}}" ms
  if [ -n "${MOYBYTE_PHASE}" ]; then
    ms=$(( (now - MOYBYTE_PHASE_T) / 1000 ))
    MOYBYTE_PHASES+=("${MOYBYTE_PHASE}=${ms}")
    echo "== time: ${MOYBYTE_PHASE} $(moybyte_dur "${ms}")"
  fi
  MOYBYTE_PHASE="$1"
  MOYBYTE_PHASE_T="${now}"
}

# "HITS MISSES" from ccache's counters, nothing without ccache.
moybyte_ccache_counts() {
  command -v ccache >/dev/null 2>&1 || return 0
  ccache --print-stats 2>/dev/null | awk '
    $1 == "direct_cache_hit" || $1 == "preprocessed_cache_hit" { h += $2 }
    $1 == "cache_miss" { m += $2 }
    END { if (NR) print h + 0, m + 0 }'
}

# ONE ESP32 BUILD CONFIGURES AT A TIME on this machine. Every build's CMake
# configure runs the component manager over its one git cache
# (~/.cache/Espressif/ComponentManager), and two configuring at once race on
# its index.lock ("Unable to create index.lock: File exists": two P4s
# 2026-09-22, the T-Deck and a P4 2026-10-03). So the configure -- `make
# submodules` and the port build up to its first finished step -- holds an
# flock under ~/.cache/moybyte (MOYBYTE_LOCK_DIR overrides) that every
# checkout's builds share, and the compile and link run beside other builds.
# The wait prints as the `lock` phase. No flock(1), no lock.
moybyte_configure_lock() {
  command -v flock >/dev/null 2>&1 || return 0
  local dir="${MOYBYTE_LOCK_DIR:-${HOME}/.cache/moybyte}"
  mkdir -p "${dir}"
  exec {MOYBYTE_LOCK_FD}>"${dir}/esp-configure.lock"
  flock "${MOYBYTE_LOCK_FD}"
}

# Release it. Safe twice, and from the phase watcher's subshell: an flock
# belongs to the open file, which the subshell shares.
moybyte_configure_unlock() {
  [ -n "${MOYBYTE_LOCK_FD:-}" ] || return 0
  flock -u "${MOYBYTE_LOCK_FD}" 2>/dev/null || true
}

# The port build's stdout, passed through, with the moment each phase starts
# written to $1 as "NAME START_US" lines (this runs in a pipeline's subshell,
# so the marks come back through the file). Off a terminal ninja prints a
# step's line when the step FINISHES, so: configure lasts until the first
# finished step (a CMake re-run, "[0/1]", prints as it starts and is inside
# it); the link starts at the last step finished before the app's
# "Linking CXX executable micropython.elf" line, which is the link's end and
# the image steps' start.
moybyte_phase_watch() {
  local line now last="" compiling="" linked=""
  : > "$1"
  while IFS= read -r line || [ -n "${line}" ]; do
    printf '%s\n' "${line}"
    [ -z "${linked}" ] || continue
    now="${EPOCHREALTIME//[.,]/}"
    case "${line}" in
      *'Linking CXX executable micropython.elf')
        [ -z "${last}" ] || echo "link ${last}" >> "$1"
        echo "image ${now}" >> "$1"
        linked=1 ;;
      '['[1-9]*'/'*']'*)
        if [ -z "${compiling}" ]; then
          echo "compile ${now}" >> "$1"
          compiling=1
          moybyte_configure_unlock
        fi
        last="${now}" ;;
    esac
  done
}

moybyte_build_times() {         # the EXIT trap of a timed build: $1 its status
  local rc="$1" total p lane cc0 cc1 hits="" misses="" args=() status=ok
  moybyte_phase end
  total=$(( (${EPOCHREALTIME//[.,]/} - MOYBYTE_T0) / 1000 ))
  lane="$(sed -n 's/^ota *= *"\([^"]*\)".*/\1/p' "${MOYBYTE_BUILD_DIR}/board.toml" 2>/dev/null | head -1 || true)"
  lane="${lane:-$(basename "${MOYBYTE_BUILD_DIR}")}"
  [ "${rc}" = "0" ] || status=fail
  echo "== build times (${lane}, ${status}):"
  for p in "${MOYBYTE_PHASES[@]}"; do
    printf '   %9s  %s\n' "$(moybyte_dur "${p#*=}")" "${p%%=*}"
    args+=("${lane}/${p%%=*}=$(( ${p#*=} / 1000 )).$(( ${p#*=} % 1000 / 100 ))")
  done
  cc0="${MOYBYTE_CCACHE0}"
  cc1="$(moybyte_ccache_counts)"
  if [ -n "${cc0}" ] && [ -n "${cc1}" ]; then
    hits=$(( ${cc1% *} - ${cc0% *} ))
    misses=$(( ${cc1#* } - ${cc0#* } ))
    printf '   ccache: %s hits, %s misses (machine-wide, over this build)\n' "${hits}" "${misses}"
  fi
  printf '   %9s  total\n' "$(moybyte_dur "${total}")"
  "${BUILD_PYTHON:-python3}" "${REPO_ROOT}/tools/timings.py" add --tool build \
    --status "${status}" ${hits:+--ccache "${hits},${misses}"} \
    "${lane}=$(( total / 1000 )).$(( total % 1000 / 100 ))" "${args[@]}" || true
}

# Write stdin to $1 unless $1 already holds exactly it. Every generated file a
# build compiles is judged by its mtime (ninja, makemanifest), so rewriting the
# same text recompiles and relinks an image nothing changed in.
moybyte_write_if_changed() {
  local path="$1" tmp
  tmp="$(mktemp "${path}.XXXXXX")"
  cat > "${tmp}"
  chmod a+r "${tmp}"
  if cmp -s "${tmp}" "${path}"; then
    rm -f "${tmp}"
  else
    mv -f "${tmp}" "${path}"
  fi
}

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

# The release an ESP-IDF checkout at $1 is, as `vX.Y.Z`, read from its
# tools/cmake/version.cmake (a `--depth 1` clone holds no tag to describe).
# Prints nothing when $1 is not a checkout.
moybyte_idf_version() {
  local f="$1/tools/cmake/version.cmake"
  [ -f "${f}" ] || return 0
  awk -F'[( )]' '/^set\(IDF_VERSION_(MAJOR|MINOR|PATCH) /{v[$2]=$3}
    END{print "v" v["IDF_VERSION_MAJOR"] "." v["IDF_VERSION_MINOR"] "." v["IDF_VERSION_PATCH"]}' "${f}"
}

# MicroPython v1.28's ports/esp32/network_wlan.c pins the size of ESP-IDF's
# wifi_auth_mode_t with a static assertion that predates ESP-IDF v5.5.6, which
# appended WIFI_AUTH_UNKNOWN (WIFI_AUTH_MAX 17 -> 18). The constants table the
# assertion guards ends before it, so on v5.5.6 the assertion takes 18, and 17
# too: a P4 compiles the enum from esp_wifi_remote's own v5.5 copy. Run by
# moybyte_setup_idf for every board. A no-op on a tree already edited, and a
# FAILURE when the line changed shape. Reads MPY_DIR.
moybyte_mpy_wlan_auth_max() {
  local f="${MPY_DIR}/ports/esp32/network_wlan.c"
  [ -f "${f}" ] || return 0
  grep -q "Moybyte: ESP-IDF v5.5.6 appends WIFI_AUTH_UNKNOWN" "${f}" && return 0
  local old='#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)'
  if [ "$(grep -cF -- "${old}" "${f}")" != "2" ] \
     || ! grep -q '^_Static_assert(WIFI_AUTH_MAX == 17, ' "${f}"; then
    echo "!! network_wlan.c's WIFI_AUTH_MAX assertion changed shape" >&2
    exit 1
  fi
  awk -v old="${old}" '
    !done && $0 == old { getline nxt
      if (nxt ~ /^_Static_assert\(WIFI_AUTH_MAX == 17, /) {
        print "// Moybyte: ESP-IDF v5.5.6 appends WIFI_AUTH_UNKNOWN (tools/esp32_build_lib.sh)."
        print "#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 6)"
        print "_Static_assert(WIFI_AUTH_MAX == 17 || WIFI_AUTH_MAX == 18, \"Synchronize WIFI_AUTH_XXX constants with the ESP-IDF. Look at esp-idf/components/esp_wifi/include/esp_wifi_types_generic.h\");"
        print "#elif ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)"
        print nxt; done = 1; next }
      print; print nxt; next }
    { print }' "${f}" > "${f}.moy" && mv "${f}.moy" "${f}"
  grep -q "Moybyte: ESP-IDF v5.5.6 appends WIFI_AUTH_UNKNOWN" "${f}" || {
    echo "!! network_wlan.c's WIFI_AUTH_MAX assertion did not take the edit" >&2
    exit 1
  }
  echo "== patched ports/esp32/network_wlan.c: WIFI_AUTH_MAX of ESP-IDF v5.5.6"
}

# MicroPython v1.28's USB-Serial/JTAG console (ports/esp32/usb_serial_jtag.c)
# enables the start-of-frame interrupt and clears it in an ISR that does
# nothing else with it, once a millisecond. ESP-IDF v5.5.6's console VFS
# writes and reads only while its connection monitor saw that raw bit in the
# last tick, so with the ISR clearing it first every C printf on the console
# was dropped (the kernel's "interrupted -> REPL", a stop's error line, every
# ESP_LOG). The edit leaves the SOF interrupt off; nothing in the port reads
# it. Run by moybyte_setup_idf for every board. A no-op on a tree already
# edited, and a FAILURE when the line changed shape. Reads MPY_DIR.
moybyte_mpy_usj_sof() {
  local f="${MPY_DIR}/ports/esp32/usb_serial_jtag.c"
  [ -f "${f}" ] || return 0
  grep -q "Moybyte: no SOF interrupt" "${f}" && return 0
  local old='        USB_SERIAL_JTAG_INTR_SOF | USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY);'
  if [ "$(grep -cxF -- "${old}" "${f}")" != "1" ]; then
    echo "!! usb_serial_jtag.c's interrupt enable changed shape" >&2
    exit 1
  fi
  awk -v old="${old}" '
    $0 == old {
      print "        USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY);"
      print "    // Moybyte: no SOF interrupt: ESP-IDF v5.5.6'"'"'s console VFS reads its raw bit"
      print "    // (tools/esp32_build_lib.sh, moybyte_mpy_usj_sof)."
      next }
    { print }' "${f}" > "${f}.moy" && mv "${f}.moy" "${f}"
  grep -q "Moybyte: no SOF interrupt" "${f}" || {
    echo "!! usb_serial_jtag.c's interrupt enable did not take the edit" >&2
    exit 1
  }
  echo "== patched ports/esp32/usb_serial_jtag.c: the SOF interrupt stays off"
}

# Pick the ESP-IDF checkout for this build into IDF_DIR: an explicit IDF_DIR
# wins, then the first of the candidates $1..$n that exists (they are ~600MB
# and one release, so a board reuses its sibling's), then a clone of
# MOYBYTE_IDF_VER into ${BUILD_DIR}/esp-idf. Whichever it picks must BE
# MOYBYTE_IDF_VER, or the build stops and names it: a warm checkout at another
# release would otherwise compile, with the patches below cut against the
# wrong sources, and nothing would say so. The fix is to move that checkout
# aside; the owning board's next build clones the pinned release.
moybyte_resolve_idf() {
  if [ -z "${IDF_DIR:-}" ]; then
    local cand
    for cand in "$@"; do
      if [ -f "${cand}/export.sh" ]; then IDF_DIR="${cand}"; break; fi
    done
  fi
  if [ -z "${IDF_DIR:-}" ]; then
    IDF_DIR="${BUILD_DIR}/esp-idf"
    if [ ! -f "${IDF_DIR}/export.sh" ]; then
      echo "== cloning esp-idf ${MOYBYTE_IDF_VER}"
      git clone --depth 1 -b "${MOYBYTE_IDF_VER}" --recursive --shallow-submodules \
        https://github.com/espressif/esp-idf "${IDF_DIR}" || return 1
    fi
  fi
  local have
  have="$(moybyte_idf_version "${IDF_DIR}")"
  if [ "${have}" != "${MOYBYTE_IDF_VER}" ]; then
    echo "!! ${IDF_DIR} is ESP-IDF ${have:-of no known release}; this build needs ${MOYBYTE_IDF_VER}." >&2
    echo "!! Move it aside: the board that owns it clones ${MOYBYTE_IDF_VER} on its next build." >&2
    return 1
  fi
}

# Resolve (moybyte_resolve_idf, over the candidates $2..$n) and activate
# ESP-IDF for $1 (the idf.py target, e.g. esp32s3).
#
# The toolchains + IDF python env live in ~/.espressif, OUTSIDE the esp-idf
# tree: a runner can have the tree (restored .build cache) but not the tools
# (evicted ~/.espressif cache), and a machine can hold another release's
# compiler for this chip, or this release's for another chip only (the P4's
# build installs the RISC-V one; an S3 build after it needs Xtensa). export.sh
# can NOT be trusted to report either -- it ends in an unconditional
# `return 0` (only its inner activate.py fails) and puts idf.py on PATH while
# the chip's compiler is missing -- so probe the real outcome (idf.py AND the
# chip's compiler on PATH) and self-heal with the official installer.
moybyte_idf_compiler() {
  case "$1" in
    esp32|esp32s2|esp32s3) echo "xtensa-$1-elf-gcc" ;;
    *) echo "riscv32-esp-elf-gcc" ;;
  esac
}

moybyte_setup_idf() {
  local chip="$1"; shift
  moybyte_resolve_idf "$@" || exit 1
  echo "== using ESP-IDF ${MOYBYTE_IDF_VER} at ${IDF_DIR}"
  moybyte_mpy_wlan_auth_max
  moybyte_mpy_usj_sof
  local cc
  cc="$(moybyte_idf_compiler "${chip}")"
  set +u
  # shellcheck disable=SC1091
  source "${IDF_DIR}/export.sh" >/dev/null 2>&1 || true
  if ! command -v idf.py >/dev/null 2>&1 || ! command -v "${cc}" >/dev/null 2>&1; then
    echo "== ESP-IDF tools for ${chip} missing (fresh runner / evicted cache / new release): running install.sh ${chip}"
    "${IDF_DIR}/install.sh" "${chip}"
    # shellcheck disable=SC1091
    source "${IDF_DIR}/export.sh" >/dev/null
    command -v idf.py >/dev/null 2>&1 || { echo "!! idf.py still missing after install.sh" >&2; exit 1; }
    command -v "${cc}" >/dev/null 2>&1 || { echo "!! ${cc} still missing after install.sh" >&2; exit 1; }
  fi
  set -u
  moybyte_ccache
  moybyte_phase patches
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

# An equal-length slice store (`buf[a:b] = row`) moves the slice and nothing
# else: the stock no-grow path also memmoves the whole tail onto itself, a
# pass over the rest of the buffer per store -- ~2.2 ms a row into a 153 KB
# PSRAM bytearray on the P4. tools/patch_slice_store.py is the patch and the
# measurement: all-or-nothing, idempotent, and every other store unchanged.
# FatFS's long-name buffer a board's choice: the port's MICROPY_FATFS_ENABLE_LFN
# a default, so a re-entrant oofatfs (the store's lock, native/moy_store/
# moy_vol.h) can put it on the calling task's stack. tools/patch_fatfs_lfn.py
# is the patch; all-or-nothing, idempotent.
moybyte_patch_fatfs_lfn() {
  [ -n "${BUILD_PYTHON:-}" ] || moybyte_resolve_build_python
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/patch_fatfs_lfn.py" "${MPY_DIR}" || exit 1
}

moybyte_patch_slice_store() {
  [ -n "${BUILD_PYTHON:-}" ] || moybyte_resolve_build_python
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/patch_slice_store.py" "${MPY_DIR}" || exit 1
}

# An extensible built-in (`json`, `array`, `time`, ...) is entered in
# sys.modules once an import has found it: stock searches every sys.path entry
# for the name on EVERY import of it, 24 ms a miss on a T-Deck.
# tools/patch_import_cache.py is the patch and the measurement: all-or-nothing,
# idempotent, and the first import of a name still searches the path.
moybyte_patch_import_cache() {
  [ -n "${BUILD_PYTHON:-}" ] || moybyte_resolve_build_python
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/patch_import_cache.py" "${MPY_DIR}" || exit 1
}

# The VM's per-thread state in a `__thread` variable: every MP_STATE_THREAD
# access (each call's globals swap, the stack check, nlr, gc_alloc) otherwise
# asks FreeRTOS for a TLS slot through xTaskGetCurrentTaskHandle, which masks
# interrupts each time -- ~5% of a P4 Editor frame. tools/patch_thread_state_tls.py
# is the patch and the measurement: all-or-nothing, idempotent, the TLS slot
# still written.
moybyte_patch_thread_state_tls() {
  [ -n "${BUILD_PYTHON:-}" ] || moybyte_resolve_build_python
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/patch_thread_state_tls.py" "${MPY_DIR}" || exit 1
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

# machine.PWM takes its LEDC channels and timers from the kernel's book
# (native/moy_kernel/moy_ledc.h), which a compiled program's PWM handles and a
# panel's backlight take theirs from too, so none drives another's, and
# channels at one frequency share a timer whoever holds them.
# tools/patch_pwm_ledc_book.py is the patch; all-or-nothing, idempotent. Every
# board that links moy_kernel takes it: the book is the kernel's.
moybyte_patch_pwm_ledc_book() {
  [ -n "${BUILD_PYTHON:-}" ] || moybyte_resolve_build_python
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/patch_pwm_ledc_book.py" "${MPY_DIR}" || exit 1
}

# The mDNS responder's four function-static scratch buffers (about 1.8 KB of
# internal .bss in every image that links espressif/mdns) become one PSRAM
# block mdns_init allocates and mdns_free frees, so a console whose responder
# runs only while its SSH server serves holds none of it otherwise.
# tools/patch_mdns_psram.py is the patch and why ESP-IDF's external-.bss
# option was declined; all-or-nothing, idempotent. The patch goes to the
# component manager's copy, which exists only once the build has fetched it,
# so this records the opt-in and moybyte_submodules applies it after the fetch.
moybyte_patch_mdns_psram() {
  [ -n "${BUILD_PYTHON:-}" ] || moybyte_resolve_build_python
  MOYBYTE_MDNS_PSRAM=1
}

# TinyUSB's MSC endpoint buffer (native/moy_usbdisk) in PSRAM, taken at the
# drive's first start, not internal .bss for the whole power-on.
# tools/patch_tinyusb_msc_psram.py is the patch and why; all-or-nothing,
# idempotent. Applied, like the mDNS one, by moybyte_submodules after the
# component manager's fetch.
moybyte_patch_tinyusb_msc_psram() {
  [ -n "${BUILD_PYTHON:-}" ] || moybyte_resolve_build_python
  MOYBYTE_TUSB_MSC_PSRAM=1
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

# Split-heap growth step. A growth adds an area as large as the whole heap,
# so a small request a collect cannot place doubles it, and an area one
# survivor holds is never given back: on an 8 MB board the step, not the need,
# sets what the heap keeps. tools/patch_gc_grow_step.py caps one growth at the
# board's MOYBYTE_GC_GROW_MAX (a bigger request still gets its own size); a
# board that defines nothing keeps the stock doubling.
moybyte_patch_gc_grow_step() {
  [ -n "${BUILD_PYTHON:-}" ] || moybyte_resolve_build_python
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/patch_gc_grow_step.py" "${MPY_DIR}" || exit 1
}

# Split-heap growth reserve. MicroPython's esp32 port grows the Python heap
# on demand by DOUBLING it, from the same ESP heap the Lua VM, the panel DMA
# and the layer pool allocate from, and gives an area back only when a sweep
# finds it wholly empty. On an 8MB
# S3 board one fragmented Python allocation took every remaining byte of
# PSRAM and every big cart then failed at load with Lua's "not enough
# memory" until a hard reset (2026-09-02, #66). The patch caps what a growth
# may take so MOYBYTE_GC_SPLIT_RESERVE bytes of the C side's heap always stay
# outside the Python heap: PSRAM on a board that has it, and on a build without
# PSRAM (the C3 Zero) the one internal heap, which WiFi, lwIP and the native
# tier allocate from and which stock growth takes to the last byte. A board
# that defines nothing reserves nothing. A GUARDED SED on the one-line port
# hook, like repr_c. Reads MPY_DIR.
moybyte_patch_gc_split_reserve() {
  local f="${MPY_DIR}/ports/esp32/gccollect.c"
  if ! grep -q "MOYBYTE_GC_SPLIT_RESERVE" "${f}"; then
    sed -i 's|^    return heap_caps_get_largest_free_block(MALLOC_CAP_DEFAULT);|    size_t avail = heap_caps_get_largest_free_block(MALLOC_CAP_DEFAULT);\n#if CONFIG_SPIRAM\n    size_t pool = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);   /* Moybyte: MOYBYTE_GC_SPLIT_RESERVE stays C-side */\n#else\n    size_t pool = heap_caps_get_free_size(MALLOC_CAP_DEFAULT);  /* no PSRAM: the internal heap is the C side'"'"'s */\n#endif\n    if (pool <= MOYBYTE_GC_SPLIT_RESERVE) return 0;\n    return avail < pool - MOYBYTE_GC_SPLIT_RESERVE ? avail : pool - MOYBYTE_GC_SPLIT_RESERVE;|' "${f}"
    sed -i 's|^size_t gc_get_max_new_split(void) {|#include "sdkconfig.h"\n#ifndef MOYBYTE_GC_SPLIT_RESERVE\n#define MOYBYTE_GC_SPLIT_RESERVE 0\n#endif\nsize_t gc_get_max_new_split(void) {|' "${f}"
    grep -q "pool - MOYBYTE_GC_SPLIT_RESERVE" "${f}" || {
      echo "!! gc split reserve patch did not apply -- gccollect.c's gc_get_max_new_split changed shape" >&2
      exit 1
    }
    echo "== patched gccollect.c: split-heap growth keeps MOYBYTE_GC_SPLIT_RESERVE of the C side's heap (#66)"
  fi
}

# The ESP32-P4 SILICON patch, one-shot marker-guarded like the rest -- shared
# by every P4 board since 2026-09-06 (it was the Waveshare's own `patches/`
# until the Guition P4 became its second consumer):
#
#   dsi_underrun      #106: raise ESP-IDF's DSI bridge-underrun ISR to level
#                     3, keep the frame-restart DW-GDMA interrupt at level 3,
#                     both above ESP-Hosted's level-1 SDIO interrupt (a BLE
#                     burst there otherwise delays the restart past the
#                     panel's blanking window), and call the board's weak
#                     moy_dsi_note_underrun hook from the ISR so native/p4
#                     counts each underrun. Patches the (possibly shared)
#                     ESP-IDF checkout, so the marker is what makes two P4
#                     builds over one IDF safe.
moybyte_patch_p4_dsi_underrun() {
  local f="${IDF_DIR}/components/esp_lcd/dsi/esp_lcd_panel_dpi.c"
  if [ -f "${f}" ] && \
     ! grep -q "Moybyte P4: dedicated DSI bridge underrun IRQ" "${f}"; then
    echo "== applying P4 DSI bridge IRQ/priority fix (#106)"
    patch -d "${IDF_DIR}" -p1 < "${REPO_ROOT}/patches/p4_esp_lcd_dsi_underrun_hook.patch"
  fi
}

# The ESP32-P4 SRM macro-block-order patch (both P4 boards, marker-guarded
# like the DSI one because the IDF checkout is shared): ESP-IDF's DIG-734
# workaround, which measures an operation's last transfer unit on the input
# block only, extended to measure a 90/270 rotation's turned block as well.
# The SRM's macro-block reorder deadlocks against the 2D-DMA
# RX FIFO when an operation's last transfer unit is smaller than the FIFO:
# the TX channel delivers the whole input, the RX FIFO fills and never drains,
# no EOF comes, and every later PPA transaction queues behind it -- the
# Guition P4's 500ms present-fence timeouts and the watchdog reset after them
# (a 1280x258 block turned 270 into the portrait scan buffer, caught on glass
# 2026-11-01). The patch bypasses the reorder for exactly those operations.
moybyte_patch_p4_ppa_srm_mb_order() {
  local f="${IDF_DIR}/components/esp_driver_ppa/src/ppa_srm.c"
  if [ -f "${f}" ] && ! grep -q "Moybyte P4: the SRM engine's macro-block reorder" "${f}"; then
    echo "== applying P4 PPA SRM macro-block-order bypass (DIG-734)"
    patch -d "${IDF_DIR}" -p1 < "${REPO_ROOT}/patches/p4_ppa_srm_mb_order_bypass.patch"
  fi
}

# The ESP32-P4 core-dump DRAM patch (both P4 boards, marker-guarded like the
# two above): ESP-IDF's linker template for a rev < v3 P4 maps dram0_data a
# second time into the high SRAM's .dram1.data. That mapping places nothing
# (the first one already took every section it names) but defines the
# SURROUND symbols again, so _coredump_dram_start/_end end up equal and a core
# dump carries none of the COREDUMP_DRAM_ATTR data: neither ESP-IDF's nor the
# kernel's module table (native/moy_elf/moy_elf_maps.c), which
# `tools/coredump.py check-elf` refuses at the end of every build. The patch
# drops the second mapping.
moybyte_patch_p4_coredump_dram() {
  local f="${IDF_DIR}/components/esp_system/ld/ld.dram.sections"
  if [ -f "${f}" ] && ! grep -q "Moybyte P4: dram0_data is mapped once" "${f}"; then
    echo "== applying P4 core-dump DRAM linker patch (one dram0_data mapping)"
    patch -d "${IDF_DIR}" -p1 < "${REPO_ROOT}/patches/p4_coredump_dram_mapped_once.patch"
  fi
}

# The ESP32-P4 heap-priority patch (both P4 boards, marker-guarded like the
# ones above). ESP-IDF registers a rev < v3 P4's low SRAM as RETENT_RAM, which
# serves a plain internal allocation only at medium priority, after every
# L2MEM heap: each internal allocation then fills the high SRAM first, the one
# region with room for the crisp PPA path's two 64 KB DMA blocks, which the
# idle desk then no longer had. The patch puts the low SRAM at the high
# priority of the rest of L2MEM; at one priority the heaps are tried smallest
# first. The retention cap stays the low SRAM's alone.
moybyte_patch_p4_heap_low_sram() {
  local f="${IDF_DIR}/components/heap/port/esp32p4/memory_layout.c"
  if [ -f "${f}" ] && ! grep -q "Moybyte P4: on a rev < v3 P4 the low SRAM" "${f}"; then
    echo "== applying P4 low-SRAM heap priority patch"
    patch -d "${IDF_DIR}" -p1 < "${REPO_ROOT}/patches/p4_heap_low_sram_priority.patch"
  fi
}

# The ESP32-P4 PPA transaction patch (both P4 boards, marker-guarded like the
# ones above). ESP-IDF allocates each of a PPA client's transaction elements
# whole from internal RAM because one part of it, the 2D-DMA transaction
# context, holds an atomic; a client holds max_pending_trans_num of them for as
# long as it is registered, and moy_ppa's SRM client queues 24. The patch
# allocates the 16-byte context from internal RAM and the rest of the element
# with the driver's PPA_MEM_ALLOC_CAPS, which puts it in PSRAM.
moybyte_patch_p4_ppa_trans_context() {
  local f="${IDF_DIR}/components/esp_driver_ppa/src/ppa_core.c"
  if [ -f "${f}" ] && ! grep -q "Moybyte P4: only the 2D-DMA transaction context" "${f}"; then
    echo "== applying P4 PPA transaction context patch"
    patch -d "${IDF_DIR}" -p1 < "${REPO_ROOT}/patches/p4_ppa_trans_context_internal_only.patch"
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

# The native twins this board takes (board.toml [native.impl]), exported as the
# MOY_*_IMPL hooks the module fragments and `board_config.py stage` read -- the
# build's environment wins, so `MOY_INDEX_IMPL=py` still builds the other twin.
# Called first by moybyte_stage_native, ahead of everything that reads a hook.
# Reads BUILD_PYTHON SCRIPT_DIR.
moybyte_board_impls() {
  local line name
  while IFS= read -r line; do
    name="${line%%=*}"
    if [ -z "${!name:-}" ]; then
      export "${line}"
    fi
  done < <("${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" impls "${SCRIPT_DIR}")
}

# Stage the shared native modules per board.toml [native.shared] and generate
# the web-console blob INTO THE STAGED COPY (never into the shared native/
# tree two builds read). Reads SCRIPT_DIR, REPO_ROOT, BUILD_PYTHON. A missing
# web bundle only WARNS locally and FAILS under CI/MOYBYTE_REQUIRE_WEB_BUNDLE,
# because a PUBLISHED image with no console is the whole bug the baking fixes.
moybyte_stage_native() {
  moybyte_phase stage-native
  moybyte_board_impls
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
    moybyte_phase web-blob
    "${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_web_blob.py" "${args[@]}"
  fi
  moybyte_phase stage
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
  moybyte_write_if_changed "${MODULES_DIR}/_ota_build.py" <<EOF
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

# Write the frozen manifest ($1) for MODULES_DIR: MicroPython's own frozen
# modules ($2, a manifest; the port's default set when absent) + this board's
# modules. The md5 fingerprint makes the manifest CONTENT change whenever any
# frozen source changes -- ninja rests custom commands on identical manifest
# text, so without it a changed .py silently ships as stale .mpy.
moybyte_frozen_manifest() {
  local manifest="$1" port_manifest="${2:-\$(PORT_DIR)/boards/manifest.py}"
  rm -rf "${MODULES_DIR}/__pycache__" "${MODULES_DIR}/moybyte/__pycache__"
  {
    cat <<EOF
include("${port_manifest}")
freeze("${MODULES_DIR}", opt=3)
EOF
    echo "# frozen-source fingerprint: $(find "${MODULES_DIR}" -type f -name '*.py' -exec md5sum {} + 2>/dev/null | sort | md5sum | cut -d' ' -f1)"
  } | moybyte_write_if_changed "${manifest}"
}

# MicroPython's makemanifest rebuilds a module's .mpy when the .py is newer and
# frozen_content.c when any input is, so on its own it misses three changes:
# a module DELETED from the tree (nothing is newer), a new freeze option, and
# a new mpy-cross, nor a board config that changes the C build's qstrs
# (frozen_content.c names the ones the C provides, so a module turned off
# leaves it citing a qstr that no longer exists). $1 is the port's build dir;
# the manifest ($MANIFEST, whose fingerprint names every frozen file and its
# bytes), the board's mpconfigboard.* (BOARD_DIR) and the mpy-cross binary are
# the keys. A changed set or board config drops frozen_content.c; a changed
# option or compiler drops every .mpy too. Recorded before the build, like
# genhdr's.
moybyte_frozen_guard() {
  local bout="$1" set tool
  set="$( { cat "${MANIFEST}"; cat "${BOARD_DIR:-/nonexistent}"/mpconfigboard.* 2>/dev/null; } \
          | md5sum | cut -d' ' -f1)"
  tool="$( { grep -v '^# frozen-source fingerprint' "${MANIFEST}"
             md5sum < "${MPY_DIR}/mpy-cross/build/mpy-cross"; } | md5sum | cut -d' ' -f1)"
  mkdir -p "${bout}"
  if [ "$(cat "${bout}/.moybyte-frozen-tool" 2>/dev/null)" != "${tool}" ]; then
    rm -rf "${bout}/frozen_mpy" "${bout}/frozen_content.c"
  elif [ "$(cat "${bout}/.moybyte-frozen-set" 2>/dev/null)" != "${set}" ]; then
    rm -f "${bout}/frozen_content.c"
  fi
  printf '%s\n' "${tool}" > "${bout}/.moybyte-frozen-tool"
  printf '%s\n' "${set}" > "${bout}/.moybyte-frozen-set"
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
  moybyte_write_if_changed "${MPY_DIR}/ports/esp32/${csv_name}" < "${BOARD_PARTITION_CSV}"

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

# The #168 size guard: the app image ($2) must fit the app slot read from
# the board's OWN partition table ($1) -- ota_0, or the factory slot of a
# table with no OTA slots (the C3 Zero) -- read, not restated, so the check
# cannot drift from the layout it is checking -- and leave the board ($4) at
# least its headroom floor ($3 bytes, `[image] headroom_floor_kib` in its
# board.toml). Both FAIL the build. An image that does not fit cannot be
# cable-flashed and cannot be installed over OTA, so the alternatives to
# stopping here are esptool refusing it later or a published payload no board
# can take; an image under its floor is the growth the floor exists to stop
# before the next partition-table change. MOYBYTE_APP_SLOT_BYTES overrides the
# slot for a what-if; MOYBYTE_ALLOW_UNDER_FLOOR=1 builds an under-floor image
# anyway (an experiment, never a release).
moybyte_app_size_guard() {
  local csv="$1" app_bin="$2" floor_bytes="$3" board="$4"
  local slot slot_hex slot_bytes size_bytes headroom
  for slot in ota_0 factory; do
    slot_hex="$(awk -F',' -v s="${slot}" '$1 ~ "^[[:space:]]*" s "[[:space:]]*$" { gsub(/[[:space:]]/, "", $5); print $5; exit }' "${csv}")"
    [ -z "${slot_hex}" ] || break
  done
  slot_bytes="${MOYBYTE_APP_SLOT_BYTES:-$(( ${slot_hex:-0x400000} ))}"
  size_bytes="$(wc -c < "${app_bin}" | tr -d '[:space:]')"
  headroom=$(( slot_bytes - size_bytes ))
  printf 'App image: %s bytes of a %s-byte %s slot -- %s bytes headroom (%s KB), floor %s KB (%s)\n' \
    "${size_bytes}" "${slot_bytes}" "${slot}" "${headroom}" "$(( headroom / 1024 ))" \
    "$(( floor_bytes / 1024 ))" "${board}"
  if [ "${headroom}" -lt 0 ]; then
    echo "" >&2
    echo "!! Moybyte BUILD FAILED (#168): the app image does not fit its app slot" >&2
    echo "!!   board:    ${board}" >&2
    echo "!!   slot:     ${slot_bytes} bytes (${slot}, $(basename "${csv}"))" >&2
    echo "!!   image:    ${size_bytes} bytes" >&2
    echo "!!   OVERFLOW: $(( -headroom )) bytes ($(( (-headroom) / 1024 )) KB)" >&2
    echo "!! Trim it (the baked web console -- see tools/gen_web_blob.py)" >&2
    echo "!! or change the partition table, which costs every deployed device a" >&2
    echo "!! full-erase USB flash." >&2
    exit 1
  elif [ "${headroom}" -lt "${floor_bytes}" ]; then
    echo "" >&2
    echo "!! Moybyte BUILD FAILED: ${board}'s image is under its headroom floor" >&2
    echo "!!   headroom: ${headroom} bytes ($(( headroom / 1024 )) KB)" >&2
    echo "!!   floor:    ${floor_bytes} bytes ($(( floor_bytes / 1024 )) KB, [image] headroom_floor_kib in its board.toml)" >&2
    echo "!!   short by: $(( floor_bytes - headroom )) bytes" >&2
    echo "!! Trim the image, or change the floor with its reason" >&2
    echo "!! (board.toml's [image] says why). MOYBYTE_ALLOW_UNDER_FLOOR=1" >&2
    echo "!! builds it anyway, for an experiment." >&2
    if [ "${MOYBYTE_ALLOW_UNDER_FLOOR:-0}" != "1" ]; then
      exit 1
    fi
    echo "!! MOYBYTE_ALLOW_UNDER_FLOOR=1: continuing" >&2
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
# Reads: MPY_DIR BOARD BOARD_DIR SCRIPT_DIR MANIFEST DIST_DIR BUILD_JOBS
# BUILD_PYTHON REPO_ROOT.
# Leaves the cwd in ports/esp32 (both scripts end here).
# The kernel's entry (native/moy_kernel), on a board that takes it: its VM
# service is mp_task copied, so the pinned tag's mp_task must be the one the
# copy was reviewed against (tools/mp_task_calls.py, before anything compiles),
# and its recovery floor names this build's OTA label, board and channel (its
# `update` word fetches that release). The kernel reads the core dump a panic
# wrote (esp_core_dump.h), so the port's component list takes espcoredump.
# Reads SCRIPT_DIR MPY_DIR MODULES_DIR.
moybyte_kernel_entry() {
  local dest staged label
  dest="$("${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" native-dest "${SCRIPT_DIR}")"
  staged="${SCRIPT_DIR}/${dest}/moy_kernel"
  [ -d "${staged}" ] || return 0
  moybyte_idf_component espcoredump
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/mp_task_calls.py" check \
    "${MPY_DIR}" "${REPO_ROOT}/native/moy_kernel/mp_task_calls.txt" || exit 1
  local board channel
  label="$(sed -n 's/^LABEL = "\(.*\)"$/\1/p' "${MODULES_DIR}/_ota_build.py" 2>/dev/null)"
  board="$(sed -n 's/^BOARD = "\(.*\)"$/\1/p' "${MODULES_DIR}/_ota_build.py" 2>/dev/null)"
  channel="$(sed -n 's/^CHANNEL = "\(.*\)"$/\1/p' "${MODULES_DIR}/_ota_build.py" 2>/dev/null)"
  printf '#define MOY_FW_LABEL "%s"\n#define MOY_FW_BOARD "%s"\n#define MOY_FW_CHANNEL "%s"\n' \
    "${label:-unlabelled}" "${board:-unknown}" "${channel:-stable}" \
    | moybyte_write_if_changed "${staged}/moy_fw_label.gen.h"
}

# The build's native configuration, as one line: the native modules this board
# stages (board.toml's takes and denials) and every MOY_*_IMPL hook in the
# environment (tools/native_check.py names them).
# Reads BUILD_PYTHON SCRIPT_DIR.
moybyte_native_config() {
  {
    "${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" list-native "${SCRIPT_DIR}"
    env | { grep -E '^MOY_[A-Z]+_IMPL=' || true; } | sort
    # The board's own switches (MICROPY_PY_BLUETOOTH, MOY_NET_*) decide which
    # modules a source registers, as surely as a take or a deny does.
    if [ -n "${BOARD_DIR:-}" ] && [ -f "${BOARD_DIR}/mpconfigboard.h" ]; then
      cksum < "${BOARD_DIR}/mpconfigboard.h" | cut -d' ' -f1
    fi
  } | tr '\n' ' '
}

# MicroPython keeps a source's generated qstr and module entries (genhdr) until
# that source is preprocessed again, so a build that drops a source -- a hook
# back to py, a module the board file now denies -- links a module table that
# names code it no longer has (`undefined reference to moy_kernel_module`).
# $1 is the build dir: when the native configuration is not the one it was last
# built with, its genhdr starts afresh, and so does frozen_content.c, which
# cites the qstrs the C provided (a denied module's own name among them:
# `'MP_QSTR_moy_ssh' undeclared` once the C3 Zero denied it). The record is written here, before the
# build, so a build that fails half-way still leaves genhdr and the record
# naming the same configuration. A build dir with no record has none to trust.
# Reads BUILD_PYTHON SCRIPT_DIR.
moybyte_native_config_guard() {
  local bout="$1" want
  want="$(moybyte_native_config)"
  if [ "$(cat "${bout}/moy_native_config" 2>/dev/null)" != "${want}" ]; then
    echo "== native configuration changed: starting ${bout}/genhdr afresh"
    rm -rf "${bout}/genhdr" "${bout}/frozen_content.c"
  fi
  mkdir -p "${bout}"
  printf '%s\n' "${want}" > "${bout}/moy_native_config"
}

# `make submodules` configures the whole IDF project in a scratch build dir to
# learn which submodules the board needs (~4 s), every build. What it answers
# is fixed by the MicroPython tree's commit, the board def and the submodules'
# state, so it runs when one of those moved since it last ran here. $1 is the
# port's build dir. Reads MPY_DIR BOARD_DIR.
moybyte_submodules_key() {
  { git -C "${MPY_DIR}" rev-parse HEAD
    git -C "${MPY_DIR}" submodule status
    echo "${BOARD_DIR}"
    find "${BOARD_DIR}" -type f -exec md5sum {} + | sort
  } 2>&1 | md5sum | cut -d' ' -f1
}

moybyte_submodules() {
  local bout="$1"
  local mdns="managed_components/espressif__mdns/mdns.c"
  local msc="managed_components/espressif__tinyusb/src/class/msc/msc_device.c"
  if [ "$(cat "${bout}/.moybyte-submodules" 2>/dev/null)" != "$(moybyte_submodules_key)" ] \
     || { [ -n "${MOYBYTE_MDNS_PSRAM:-}" ] && [ ! -f "${mdns}" ]; } \
     || { [ -n "${MOYBYTE_TUSB_MSC_PSRAM:-}" ] && [ ! -f "${msc}" ]; }; then
    make submodules BOARD_DIR="${BOARD_DIR}"
    mkdir -p "${bout}"
    moybyte_submodules_key > "${bout}/.moybyte-submodules"
  fi
  # The submodules step's configure is the component manager's fetch, so the
  # managed components are there to patch before the build compiles them.
  if [ -n "${MOYBYTE_MDNS_PSRAM:-}" ]; then
    "${BUILD_PYTHON}" "${REPO_ROOT}/tools/patch_mdns_psram.py" "${MPY_DIR}" || exit 1
  fi
  if [ -n "${MOYBYTE_TUSB_MSC_PSRAM:-}" ]; then
    "${BUILD_PYTHON}" "${REPO_ROOT}/tools/patch_tinyusb_msc_psram.py" "${MPY_DIR}" || exit 1
  fi
}

moybyte_build_and_collect() {
  local csv="$1" stem="$2" flash_note="$3"
  moybyte_phase mpy-cross
  moybyte_kernel_entry
  make -C "${MPY_DIR}/mpy-cross" -j"${BUILD_JOBS}"
  cd "${MPY_DIR}/ports/esp32"
  local bout="build-${BOARD}"
  moybyte_native_config_guard "${bout}"
  moybyte_frozen_guard "${bout}"
  local port_make=(make -j"${BUILD_JOBS}" BOARD_DIR="${BOARD_DIR}"
    USER_C_MODULES="${SCRIPT_DIR}/native/micropython.cmake"
    FROZEN_MANIFEST="${MANIFEST}")
  moybyte_phase lock
  moybyte_configure_lock
  if [ -n "${MOYBYTE_T0}" ] && [ ! -t 1 ]; then
    local marks n at
    marks="$(mktemp)"
    moybyte_phase configure
    moybyte_submodules "${bout}"
    "${port_make[@]}" | moybyte_phase_watch "${marks}"
    while read -r n at; do moybyte_phase "${n}" "${at}"; done < "${marks}"
    rm -f "${marks}"
  else
    moybyte_phase port
    moybyte_submodules "${bout}"
    "${port_make[@]}"
  fi
  moybyte_configure_unlock
  moybyte_phase collect
  cp "${bout}/firmware.bin" "${DIST_DIR}/${stem}.bin"
  cp "${bout}/micropython.bin" "${DIST_DIR}/${stem}_app.bin"
  local floor board_id
  floor="$("${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" \
           headroom-floor "${SCRIPT_DIR}")"
  board_id="${floor#* }"
  floor="${floor%% *}"
  # The image's bytes by part, from the link map, for docs/perf/image.md
  # (tools/image_ledger.py); before the guard, so an image the guard refuses
  # still says what it is made of.
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/image_ledger.py" record "${SCRIPT_DIR}" \
    "${bout}" "${DIST_DIR}/${stem}_app.bin" \
    || echo "!! image ledger: no record for this build" >&2
  moybyte_app_size_guard "${csv}" "${DIST_DIR}/${stem}_app.bin" \
    "${floor}" "${board_id}"
  # A core dump must carry the loaded modules' table (tools/coredump.py).
  "${BUILD_PYTHON}" "${REPO_ROOT}/tools/coredump.py" check-elf "${bout}/micropython.elf" \
    || exit 1
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

# A board's build.sh is timed from here (see moybyte_phase above); a script or
# test that sources this lib for one function is not.
if [ "$(basename "${BASH_SOURCE[1]:-}")" = "build.sh" ]; then
  MOYBYTE_T0="${EPOCHREALTIME//[.,]/}"
  MOYBYTE_BUILD_DIR="${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)}"
  MOYBYTE_CCACHE0="$(moybyte_ccache_counts)"
  moybyte_phase toolchain
  trap 'moybyte_build_times $?' EXIT
fi
