#!/usr/bin/env bash
# Moybyte P4 port (#58): build mainline MicroPython (ESP32_GENERIC_P4, C6_WIFI
# variant) + the P4 silicon tier (native/p4: moy_dsi over the EK79007 MIPI-DSI
# panel, moy_ppa, moy_ble_hid, moy_c6) for the Waveshare
# ESP32-P4-WIFI6-Touch-LCD-7B.
#
# A plain mainline build with USER_C_MODULES -- the strategy both boards use
# now (this board went mainline first, because the deleted lvgl_micropython
# fork had no P4/DSI support, and became the T-Deck port's template). The
# SHARED half of the build lives in tools/esp32_build_lib.sh since 2026-08-17;
# what stays here is this board's patch ladder and sdkconfig facts. Output
# flashes at offset 0x2000:
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
MODULES_DIR="${SCRIPT_DIR}/modules"
MANIFEST="${BUILD_DIR}/moybyte_p4_manifest.py"

# shellcheck source=../../tools/esp32_build_lib.sh
source "${REPO_ROOT}/tools/esp32_build_lib.sh"
moybyte_resolve_build_python

mkdir -p "${BUILD_DIR}" "${DIST_DIR}" "${MODULES_DIR}"

# ---------------------------------------------------------------------------
# 1) Toolchain: MicroPython at the pinned tag, and ESP-IDF v5.5.1 -- no
#    candidates, so this board OWNS the shared checkout: `.build/esp-idf` here
#    is what the T-Deck and the Guition name rather than clone 600MB again.
#    (In CI every board is its own runner with no sibling to find, so each
#    clones its own -- the ownership only means anything on a desk.)
#
#    It used to name the T-Deck's checkout first, left over from before the
#    shared build lib (2026-08-17) pointed the T-Deck's own build here. Nothing
#    owned that directory afterwards, and BOTH this board's and the Guition's
#    CMake caches had pinned CMAKE_TOOLCHAIN_FILE into it -- an entry CMake
#    will not re-point after the first configure. So its eventual removal was a
#    build break waiting on the calendar, on two boards -- the same stale-cache
#    class that forced a wipe of the T-Deck's build dir on 2026-08-27.
#    Deleted 2026-08-27; one owner now, named in one direction only.
# ---------------------------------------------------------------------------
moybyte_clone_micropython
moybyte_setup_idf esp32p4

# ---------------------------------------------------------------------------
# 2) The patch ladder -- THIS BOARD'S half of the build. All marker-guarded,
#    because both the .build tree and a reused IDF checkout persist.
# ---------------------------------------------------------------------------

# 2a) The P4 SILICON patches (shared lib, both P4 boards): the BLE-HID
#     notification fast path into MicroPython's modbluetooth.c, and the #106
#     DSI bridge-underrun ISR backport into the (shared) ESP-IDF checkout.
#     Both were this directory's patches/ until 2026-09-06; they live in
#     patches/p4_*.patch now.
moybyte_patch_p4_ble_hid_fastpath
moybyte_patch_p4_dsi_underrun

# 2c) moy_dsi needs esp_lcd, moy_ppa needs esp_driver_ppa (the P4 pixel
#     accelerator) in the main component's REQUIRES.
moybyte_idf_component esp_lcd
moybyte_idf_component esp_driver_ppa

# 2c') ESP-Hosted 2.7.0 -> 2.12.12 -- the espnow-on-p4 track. The shared lib
#      carries the argument and the glass verdict; wifi measured at RX parity
#      here (2.9-3.0 MB/s against 2.7.0's 3.2).
moybyte_patch_esp_hosted_bump esp32p4

# 2d) Un-static esp_native_code_free_all (#66) -- shared with the T-Deck.
#     Mainline's ports/esp32/main.c has the identical grow-only
#     esp_native_code_commit list, and the P4's RV32 native emitter feeds it
#     (MICROPY_EMIT_RV32=1), so edit->PLAY sessions would hit the same cliff,
#     just later (bigger internal pool).
moybyte_patch_native_code_free
moybyte_patch_espnow_ring_race

# 2e) REPR_C -- applied 2026-08-24, and NOT for the S3's reason. This stood as
#     "DECLINED, an open question" for a week because the only argument was
#     perf (the S3's measured float-boxing gc hitch) and per-board perf
#     verdicts don't transfer. The espnow lockstep match (#7 Phase E) turned
#     it into a CORRECTNESS requirement: both consoles in a match run the same
#     sim from the same inputs, and a 30-bit REPR_C float (the S3s) against a
#     boxed 32-bit single (this board, until now) diverges the two worlds by
#     construction -- measured on glass, P4<->T-Deck Brick Siege: tanks
#     identical, 0/1105 world checksums agreeing, and the same accumulator
#     printing 0.21666668 on one board and 0.216666668 on the other. FLOAT
#     WIDTH IS PART OF THE LOCKSTEP CONTRACT: every board that can hold a
#     link runs REPR_C, and a future board that cannot take REPR_C is a
#     board that cannot join a match until something re-solves this. The
#     perf A/B ran the same day, paired on the same tree and flash cycle:
#     Sky Run 58.0 -> 56.5, Sakura 51.0 -> 51.5 -- ~1.5fps on one cart,
#     noise on the other. It would not have gotten a vote anyway.
moybyte_patch_repr_c

# DECLINED moybyte_patch_gc_split_reserve -- the split-heap growth cap (#66).
# The patch reserves MOYBYTE_GC_SPLIT_RESERVE bytes of PSRAM outside the Python
# heap, and that define is set by the two S3 boards' mpconfigboard.h alone, so a
# call here reserves 0 -- the patch applies and the cap computes to nothing.
# Whether a P4 with 32MB of PSRAM wants a reserve at all is unmeasured (#58);
# the day it is, the board sets the define and takes the call back.

# DECLINED moybyte_patch_psram_retune -- not applicable. That patch relaxes the
# ESP32-S3 MSPI timing tuner's flash-vendor gate (#169); this is an ESP32-P4 and
# the file does not exist in its build. Its PSRAM constraint is a different one
# entirely (200MHz or the DSI scan-out underruns -- see this dir's README).

# ---------------------------------------------------------------------------
# 3) Stage: the shared native modules (board.toml [native.shared] -- the
#    denials, moy_sd/moy_audio/moy_flush, live there WITH their reasons; all
#    plain C, the S3-specific pieces are include-guarded, so they compile
#    unchanged on RISC-V) plus the P4 silicon tier ([native.p4] over
#    native/p4/), with the browser console blob generated into the staged copy
#    (never into the shared native/ tree two builds read -- this used to
#    generate there and race a concurrent T-Deck build); then the shared
#    PYTHON modules (#58 console staging, #161 Phase 3 -- board.toml holds the
#    denylist over runtime/ and the allowlist over device/, and the stager
#    prunes untracked strays); then the generated modules.
# ---------------------------------------------------------------------------
moybyte_stage_native
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" stage "${SCRIPT_DIR}"

#    carts_data.py is built from system_carts/ so the seed + embedded-fallback
#    carts can never drift from the host source of truth. PACKED (2026-08-30):
#    one raw-deflate blob per cart instead of 732 KB of literal source, because
#    the roster only grows and the slot does not. `moy_carts.seed_any` picks the
#    decoder off the roster's FORM, so nothing else in the boot changed.
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_device_carts.py" --packed "${MODULES_DIR}/carts_data.py"

#    The OTA identity stamp (#53). An app image is board-specific in the
#    strongest way (Xtensa there, RISC-V here), so the manifest url carries the
#    board and the device refuses a manifest naming another one.
moybyte_ota_identity p4 "${REPO_ROOT}/device/moy_ota.py"

# ---------------------------------------------------------------------------
# 4) Frozen manifest + partition table (#58: OTA-shaped 2x4MB app slots +
#    auto-vfs tail -- the default 4MiBplus table's ~1.94MB app can't hold the
#    frozen console) + the stale-sdkconfig guard.
# ---------------------------------------------------------------------------
moybyte_frozen_manifest "${MANIFEST}"
moybyte_sdkconfig_guard "${BOARD_DIR}" \
  "${MPY_DIR}/ports/esp32/build-${BOARD}/sdkconfig"

# ---------------------------------------------------------------------------
# 5) Build + collect (shared lib: mpy-cross, the port, the two images and the
#    #168 size guard -- moybyte_app_size_guard runs in there).
# ---------------------------------------------------------------------------
moybyte_build_and_collect "${BOARD_PARTITION_CSV}" \
  moybyte_p4 "flash at offset 0x2000"
