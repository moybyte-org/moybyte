#!/usr/bin/env bash
# Moybyte Zero (Seeed XIAO ESP32-S3) -- the headless cart-store board, built
# through the #202 port kit like every other board since the Guition:
# everything shared lives in tools/esp32_build_lib.sh, the staged module sets
# are board.toml data, and what remains here is this board's (very short) patch
# ladder and the note beside each patch it DECLINES.
#
# This board was promoted to a real build target on 2026-08-29 (owner call).
# It spent 2026-07..2026-08 running stock MicroPython with the shared modules
# PUSHED as plain files -- see README.md for what that bought and why it ended.
#
# Build -> flash -> look:
#
#   ./build.sh
#   make firmware-flash-zero PORT=/dev/ttyACM0
#   mpremote connect /dev/ttyACM0 repl
#
# If an image ever wedges the USB device: hold the board's BOOT button while
# powering on to reach the ROM loader. From a RUNNING MicroPython the safe way
# in is `machine.bootloader()`, and the way OUT is
# `esptool --after watchdog_reset` -- NOT hard_reset, which does nothing here.
# An esptool DTR dance against the running TinyUSB CDC has wedged the USB
# device before (README, hardware facts).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${SCRIPT_DIR}/.build"
MPY_DIR="${BUILD_DIR}/micropython"
MPY_TAG="${MPY_TAG:-v1.28.0}"
BOARD="MOYBYTE_ZERO"
BOARD_DIR="${SCRIPT_DIR}/boards/${BOARD}"
DIST_DIR="${REPO_ROOT}/dist/zero"
MODULES_DIR="${SCRIPT_DIR}/modules"
MANIFEST="${BUILD_DIR}/moybyte_zero_manifest.py"

# shellcheck source=../../tools/esp32_build_lib.sh
source "${REPO_ROOT}/tools/esp32_build_lib.sh"
moybyte_resolve_build_python

mkdir -p "${BUILD_DIR}" "${DIST_DIR}"

# ---------------------------------------------------------------------------
# 1) Toolchain: our OWN MicroPython checkout (sharing one would race the other
#    boards' sed-patched esp32_common.cmake -- and this board patches NEITHER,
#    so a shared tree would silently hand it the siblings' patches), and
#    ESP-IDF v5.5.1 reused from the P4, which owns the shared checkout.
# ---------------------------------------------------------------------------
moybyte_clone_micropython
moybyte_setup_idf esp32s3 \
  "${REPO_ROOT}/firmware/esp32_p4_wifi6_touch_lcd_7b/.build/esp-idf"

# ---------------------------------------------------------------------------
# 2) The patch ladder -- EMPTY, and that is this board's shortest description.
#    Every patch the siblings apply is a fix for something this board does not
#    do, so each is declined here in writing rather than by omission:
#
#    * REPR_C unboxed floats (#66) -- a cart frame loop's float-boxing tax.
#      This board runs no carts and has no frame loop; the win is zero and the
#      cost is a non-default object representation on the board with the least
#      on-glass coverage in the fleet.
#    * esp_native_code_free_all (#66) -- reclaims the @micropython.native exec
#      arena after a cart compile. Nothing here compiles a cart.
#    * the ESP-NOW ring torn-read race (#7) -- modespnow is not in this image
#      (no [modules.device] espnow, no netplay, no second console to pair with).
#    * the PSRAM temperature retune (#169) -- REQUIRED by the 120MHz MSPI
#      profile and inert without it. sdkconfig.board stays at 80MHz and says
#      why, so the patch would be a no-op with a boot-abort failure mode.
#    * the esp_lcd tx_color no-acquire patch -- there is no panel.
#    * the #69 I2C GIL release -- there is no input poller and no I2C device.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 3) Stage: the shared native modules (board.toml [native.shared] -- moy_web
#    and nothing else) with the web blob generated INTO the staged copy, then
#    the shared Python modules (board.toml [modules.*]).
#
#    No tools/gen_device_carts.py step, unlike the console boards: they freeze
#    the seed roster as a fallback for a missing card, and this board's whole
#    product is a real cart store on real flash. Baking a second copy of 36
#    carts into both OTA slots would cost more flash than the store it is meant
#    to back up.
# ---------------------------------------------------------------------------
moybyte_stage_native
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" stage "${SCRIPT_DIR}"

#    The OTA identity stamp (#53). `xiao_zero` is the board id inside the signed
#    manifest -- reserved in board.toml since this board's rebuild and wired for
#    real on 2026-08-29.
moybyte_ota_identity xiao_zero "${REPO_ROOT}/device/moy_ota.py"

# ---------------------------------------------------------------------------
# 4) Frozen manifest + partition table + the stale-sdkconfig guard.
# ---------------------------------------------------------------------------
moybyte_frozen_manifest "${MANIFEST}"
moybyte_sdkconfig_guard "${BOARD_DIR}" \
  "${MPY_DIR}/ports/esp32/build-${BOARD}/sdkconfig"

# ---------------------------------------------------------------------------
# 5) Build + collect (shared lib: mpy-cross, the port, the two images and the
#    #168 size guard, which reads the ota_0 size out of this board's own CSV).
#    The merged image cable-flashes at 0x0 (S3 bootloader offset).
# ---------------------------------------------------------------------------
moybyte_build_and_collect "${BOARD_PARTITION_CSV}" \
  moybyte_zero "full image, cable flash at 0x0"
