#!/usr/bin/env bash
# Moybyte Zero (ESP32-C3) -- the headless cart-store board on a C3, built
# through the #202 port kit: everything shared lives in tools/esp32_build_lib.sh,
# the staged module sets are board.toml data, and what remains here is this
# board's patch ladder and the note beside each patch it DECLINES. The ladder
# is the XIAO Zero's (firmware/seeed_xiao_esp32s3_zero/build.sh), and a decline
# that cites "the XIAO's reason" is argued in full there.
#
# Build -> flash -> look:
#
#   ./build.sh
#   make firmware-flash-c3-zero PORT=/dev/ttyACM0
#   mpremote connect /dev/ttyACM0 repl
#
# The C3's USB-Serial/JTAG takes esptool's default reset into the ROM loader
# and its default hard_reset out of it (board.toml [flash] says why not the
# XIAO's watchdog_reset). If an image ever wedges the USB device: hold BOOT
# (GPIO9) while powering on.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${SCRIPT_DIR}/.build"
MPY_DIR="${BUILD_DIR}/micropython"
BOARD="MOYBYTE_C3_ZERO"
BOARD_DIR="${SCRIPT_DIR}/boards/${BOARD}"
DIST_DIR="${REPO_ROOT}/dist/c3_zero"
MODULES_DIR="${SCRIPT_DIR}/modules"
MANIFEST="${BUILD_DIR}/moybyte_c3_zero_manifest.py"

source "${REPO_ROOT}/tools/esp32_build_lib.sh"
moybyte_resolve_build_python
mkdir -p "${BUILD_DIR}" "${DIST_DIR}"

# ---------------------------------------------------------------------------
# 1) MicroPython + ESP-IDF: reused from the P4, which owns the shared
#    checkout; `install.sh esp32c3` brings the RISC-V toolchain on a first run.
# ---------------------------------------------------------------------------
moybyte_clone_micropython
moybyte_setup_idf esp32c3 \
  "${REPO_ROOT}/firmware/esp32_p4_wifi6_touch_lcd_7b/.build/esp-idf"

# ---------------------------------------------------------------------------
# 2) The patch ladder: the XIAO's four, and its declines.
# ---------------------------------------------------------------------------
# The kernel's entry (MICROPY_ESP_IDF_ENTRY) and the VM service it runs.
moybyte_patch_native_code_free
# An equal-length slice store moves no tail: every board takes it.
moybyte_patch_slice_store
# DECLINED moybyte_patch_fatfs_lfn -- the Zero runs no program on a task of its
#    own, so its FatFS stays the port's, unlocked (the XIAO's reason).
moybyte_patch_import_cache
# machine.PWM takes its LEDC channels and timers from the kernel's book: every
# board that links moy_kernel takes it.
moybyte_patch_pwm_ledc_book
# The split heap's growth keeps MOYBYTE_GC_SPLIT_RESERVE (mpconfigboard.h) of
# the internal heap outside the Python heap. With no PSRAM that heap is also
# WiFi's, lwIP's and the native tier's, and stock growth takes it to the last
# byte: a C3 Zero in setup mode measured 17 KB left for them.
moybyte_patch_gc_split_reserve

# DECLINED moybyte_patch_thread_state_tls -- measured on the P4s for an Editor
#    frame; this board has no frame (the XIAO's reason).
# DECLINED moybyte_patch_repr_c -- a cart interpreter's float tax; no cart runs
#    here (the XIAO's reason).
# DECLINED moybyte_patch_map_cache_for_repr_c -- REPR_C's consequence, and this
#    image is REPR_A (the XIAO's reason).
# DECLINED moybyte_patch_gc_run_hints -- paid inside a frame loop this board has
#    not got (the XIAO's reason).
# DECLINED moybyte_patch_gc_meters -- the PERF line and `heapcaps` it feeds run
#    on neither Zero (the XIAO's reason).
# DECLINED moybyte_patch_mdns_psram -- there is no PSRAM to move the responder's
#    buffers to.
# DECLINED moybyte_patch_tinyusb_msc_psram -- native/moy_usbdisk is denied, and
#    the C3 has no USB OTG for TinyUSB at all.
# DECLINED moybyte_patch_psram_retune -- the S3's MSPI timing for PSRAM; no PSRAM.
# DECLINED moybyte_patch_gc_grow_step -- a console's canvas and layers sharing
#    PSRAM with the heap; headless and without PSRAM.
# DECLINED moybyte_patch_p4_dsi_underrun -- an ESP32-P4 silicon patch.
# DECLINED moybyte_patch_p4_ppa_srm_mb_order -- an ESP32-P4 silicon patch.
# DECLINED moybyte_patch_p4_coredump_dram -- an ESP32-P4 linker patch: the
#    rev < v3 P4's high-SRAM data section. The C3's linker script has none.
# DECLINED moybyte_patch_p4_heap_low_sram -- an ESP32-P4 heap patch: the
#    rev < v3 P4's low-SRAM heap priority. The C3 has no such region.
# DECLINED moybyte_patch_p4_ppa_trans_context -- an ESP32-P4 driver patch: the
#    PPA's transaction elements. No PPA.
# DECLINED moybyte_patch_esp_hosted_bump -- the P4's radio; this one is on-die.
# DECLINED moybyte_patch_lfs_sizes -- measured on the P4s' flash only (the
#    XIAO's reason).
# DECLINED moybyte_patch_sdcard_runs -- no card.
# DECLINED moybyte_patch_usj_rx_init -- nothing writes to a Zero while it boots
#    (the XIAO's reason).
# DECLINED moybyte_patch_stdin_ring -- USB-Serial/JTAG backpressures (the XIAO's
#    reason).

# ---------------------------------------------------------------------------
# 3) Stage: the shared native modules (board.toml [native.shared]) with the web
#    blob generated INTO the staged copy, the shared Python modules (board.toml
#    [modules.*]), then the seed roster PACKED and cut by board.toml [roster],
#    as on the XIAO.
# ---------------------------------------------------------------------------
moybyte_stage_native
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" stage "${SCRIPT_DIR}"
ZERO_SKIP="$("${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" roster-skip "${SCRIPT_DIR}")"
ZERO_FIRST="$("${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" roster-first "${SCRIPT_DIR}")"
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_device_carts.py" --packed \
  "${MODULES_DIR}/carts_data.py" --skip "${ZERO_SKIP}" --first "${ZERO_FIRST}"

#    The version stamp (#53): `c3_zero` is the board id a manifest would name.
#    With one app slot no manifest installs here; the stamp is the version the
#    board reports.
moybyte_ota_identity c3_zero "${REPO_ROOT}/device/moy_ota.py"

# ---------------------------------------------------------------------------
# 4) Frozen manifest (MicroPython's half is boards/MOYBYTE_C3_ZERO/manifest.py)
#    + partition table + the stale-sdkconfig guard.
# ---------------------------------------------------------------------------
moybyte_frozen_manifest "${MANIFEST}" "${BOARD_DIR}/manifest.py"
moybyte_sdkconfig_guard "${BOARD_DIR}" \
  "${MPY_DIR}/ports/esp32/build-${BOARD}/sdkconfig"

# ---------------------------------------------------------------------------
# 5) Build + collect (shared lib: mpy-cross, the port, the two images and the
#    #168 size guard, which reads the factory slot out of this board's CSV).
#    The merged image cable-flashes at 0x0 (the C3's bootloader offset).
# ---------------------------------------------------------------------------
moybyte_build_and_collect "${BOARD_PARTITION_CSV}" \
  moybyte_c3_zero "full image, cable flash at 0x0"
