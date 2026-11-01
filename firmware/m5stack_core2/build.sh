#!/usr/bin/env bash
# Moybyte M5Stack Core2 (classic ESP32-D0WDQ6-V3, 2" 320x240 SPI ILI9342C) --
# the first classic-ESP32 board, built through the #202 port kit: everything
# shared lives in tools/esp32_build_lib.sh, the staged module sets are
# board.toml data, and what remains here is this board's patch ladder.
#
# Build -> flash -> look:
#
#   ./build.sh
#   tools/board.py core2 flash
#
# The CH9102 bridge reaches the ROM loader by esptool's DTR/RTS reset: no
# button is needed to flash, ever.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${SCRIPT_DIR}/.build"
MPY_DIR="${BUILD_DIR}/micropython"
MPY_TAG="${MPY_TAG:-v1.28.0}"
BOARD="MOYBYTE_CORE2"
BOARD_DIR="${SCRIPT_DIR}/boards/${BOARD}"
DIST_DIR="${REPO_ROOT}/dist/core2"
MODULES_DIR="${SCRIPT_DIR}/modules"
MANIFEST="${BUILD_DIR}/moybyte_core2_manifest.py"

# shellcheck source=../../tools/esp32_build_lib.sh
source "${REPO_ROOT}/tools/esp32_build_lib.sh"
moybyte_resolve_build_python

mkdir -p "${BUILD_DIR}" "${DIST_DIR}"

# ---------------------------------------------------------------------------
# 1) Toolchain: our own MicroPython checkout, the P4's ESP-IDF v5.5.1 (its
#    xtensa-esp-elf toolchain serves the classic ESP32 as it does the S3).
# ---------------------------------------------------------------------------
moybyte_clone_micropython
moybyte_setup_idf esp32 \
  "${REPO_ROOT}/firmware/esp32_p4_wifi6_touch_lcd_7b/.build/esp-idf"

# moy_lcd is esp_lcd's panel IO (the T-Deck's module, taken).
moybyte_idf_component esp_lcd

# ---------------------------------------------------------------------------
# 2) The patch ladder. The VM patches every console board takes:
# ---------------------------------------------------------------------------
moybyte_patch_repr_c
moybyte_patch_map_cache_for_repr_c
moybyte_patch_gc_run_hints
moybyte_patch_gc_meters
moybyte_patch_slice_store
moybyte_patch_import_cache
moybyte_patch_gc_split_reserve
moybyte_patch_native_code_free

# The console is UART0 behind the CH9102, so the 4 KB stdin ring is this
# board's, as it is the Waveshare P4's: a UART's bytes are lost when the ring
# is full, where a USB-Serial/JTAG backpressures.
moybyte_patch_stdin_ring

# DECLINED moybyte_patch_sdcard_runs -- machine.SDCard's; this board's card
# shares the panel's SPI host and is the native moy_sd's, as on the T-Deck.
# DECLINED moybyte_patch_usj_rx_init -- a USB-Serial/JTAG console's; this
# chip has none.
# DECLINED moybyte_patch_psram_retune -- the S3's 120MHz MSPI retune; this
# chip's PSRAM runs at 80MHz quad with no temperature tuning.
# DECLINED moybyte_patch_thread_state_tls -- measured on the P4s only.
# DECLINED moybyte_patch_lfs_sizes -- the store is the TF card when present.
# DECLINED moybyte_patch_p4_dsi_underrun -- ESP32-P4 silicon (the DSI bridge).
# DECLINED moybyte_patch_esp_hosted_bump -- the P4's C6 radio over SDIO; this
# chip's radio is on-die.

# ---------------------------------------------------------------------------
# 3) Stage: shared native modules, shared Python modules, generated modules.
# ---------------------------------------------------------------------------
moybyte_stage_native
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" stage "${SCRIPT_DIR}"
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_device_carts.py" --packed "${MODULES_DIR}/carts_data.py"
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/make_font.py" module "${SCRIPT_DIR}" "${MODULES_DIR}/fonts_data.py"
moybyte_ota_identity core2 "${REPO_ROOT}/device/moy_ota.py"

# ---------------------------------------------------------------------------
# 4) Frozen manifest + partition table + the stale-sdkconfig guard.
# ---------------------------------------------------------------------------
moybyte_frozen_manifest "${MANIFEST}"
moybyte_sdkconfig_guard "${BOARD_DIR}" \
  "${MPY_DIR}/ports/esp32/build-${BOARD}/sdkconfig"

# ---------------------------------------------------------------------------
# 5) Build + collect. The merged image cable-flashes at 0x1000, the classic
#    ESP32's bootloader offset.
# ---------------------------------------------------------------------------
moybyte_build_and_collect "${BOARD_PARTITION_CSV}" \
  moybyte_core2 "full image, cable flash at 0x1000"
