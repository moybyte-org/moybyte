#!/usr/bin/env bash
# Moybyte T-Deck on MAINLINE MicroPython -- THE T-Deck build (the only one since
# the lvgl_micropython fork was deleted, 2026-08-17: this port measured 2ms/frame
# faster on the Bench referee and is the only build whose serial RX works).
#
# One build strategy for both boards: mainline MicroPython + an out-of-tree
# board definition + USER_C_MODULES, exactly like the P4 -- and since
# 2026-08-17 the SHARED HALF of that strategy is literally shared:
# tools/esp32_build_lib.sh carries the steps both build.sh scripts used to
# carry as near-verbatim twins (toolchain setup, native staging, OTA stamp,
# frozen manifest, sdkconfig guard, size guard). What stays HERE is this
# board's own half: the patch ladder and the sdkconfig facts.
#
# Build -> flash -> look:
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
# The shared trees are repo-root (native/ the shared C usermods -- staged by
# board.toml [native.shared], never referenced by name here; patches/ the
# IDF/MicroPython patches). Nothing here may resolve inside a BOARD directory.
PATCH_DIR="${REPO_ROOT}/patches"
MODULES_DIR="${SCRIPT_DIR}/modules"
MANIFEST="${BUILD_DIR}/moybyte_tdeck_manifest.py"

# shellcheck source=../../tools/esp32_build_lib.sh
source "${REPO_ROOT}/tools/esp32_build_lib.sh"
moybyte_resolve_build_python

mkdir -p "${BUILD_DIR}" "${DIST_DIR}"

# ---------------------------------------------------------------------------
# 1) Toolchain: MicroPython at the pinned tag (our OWN checkout -- sharing one
#    with the P4 would mean two boards racing the same sed-patched
#    esp32_common.cmake and the same build-<BOARD>/ sdkconfig guards), and
#    ESP-IDF v5.5.1, reusing the P4's checkout when it exists.
# ---------------------------------------------------------------------------
moybyte_clone_micropython
moybyte_setup_idf esp32s3 \
  "${REPO_ROOT}/firmware/esp32_p4_wifi6_touch_lcd_7b/.build/esp-idf"

# ---------------------------------------------------------------------------
# 2) The patch ladder -- THIS BOARD'S half of the build. All marker-guarded,
#    because both .build trees persist across builds.
# ---------------------------------------------------------------------------
MACHINE_I2C_C="${MPY_DIR}/ports/esp32/machine_i2c.c"

# 2a) moy_lcd needs esp_lcd in the main component's REQUIRES.
moybyte_idf_component esp_lcd

# 2b) REPR_C unboxed floats (#66) -- the chip-class lever this board measured.
moybyte_patch_repr_c

# 2c) Release the GIL across machine.I2C's blocking wait (#69).
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

# 2d) esp_lcd tx_color no-acquire (#66), applied to the IDF tree.
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

# 2e) Un-static esp_native_code_free_all (#66) -- shared with the P4.
moybyte_patch_native_code_free

# 2f) PSRAM temperature retune (#169) -- REQUIRED by this board's 120MHz octal
#     MSPI setting in sdkconfig.board, not optional alongside it.
moybyte_patch_psram_retune

# ---------------------------------------------------------------------------
# 3) Stage: the shared native modules (board.toml [native.shared] -- the
#    C-module list is DATA, exactly like the Python one; there is no module
#    list in this script and there must never be one again) with the browser
#    console blob generated into the staged copy; then the shared PYTHON
#    modules (#161 Phase 3, board.toml [modules.*] -- runtime/ by denylist,
#    device/ by allowlist, with the stager pruning untracked strays); then the
#    two GENERATED modules (`keep` in board.toml names both).
# ---------------------------------------------------------------------------
moybyte_stage_native
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" stage "${SCRIPT_DIR}"

#    carts_data.py is built from system_carts/ so the seed + embedded-fallback
#    carts can never drift from the host source of truth.
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_device_carts.py" "${MODULES_DIR}/carts_data.py"

#    The OTA identity stamp (#53). BOARD "tdeck" is the same id the deleted
#    fork build stamped, kept on purpose: an OTA payload is an app-partition
#    image on a byte-identical partition table, so boards that took their last
#    update from the fork build move onto this one over plain OTA.
moybyte_ota_identity tdeck "${REPO_ROOT}/device/moy_ota.py"

# ---------------------------------------------------------------------------
# 4) Frozen manifest + partition table + the stale-sdkconfig guard.
# ---------------------------------------------------------------------------
moybyte_frozen_manifest "${MANIFEST}"
moybyte_sdkconfig_guard "${BOARD_DIR}" \
  "${MPY_DIR}/ports/esp32/build-${BOARD}/sdkconfig"

# ---------------------------------------------------------------------------
# 5) Build + collect (shared lib: mpy-cross, the port, the two images and the
#    #168 size guard -- moybyte_app_size_guard runs in there). The merged
#    image cable-flashes at 0x0 (the S3's bootloader offset; the P4's is
#    0x2000).
# ---------------------------------------------------------------------------
moybyte_build_and_collect "${BOARD_PARTITION_CSV}" \
  moybyte_tdeck "full image, cable flash at 0x0"
