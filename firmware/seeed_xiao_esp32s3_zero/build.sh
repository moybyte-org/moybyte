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
#    Every shared patch is per-board and OPT-IN, and a board that does not call
#    one declines it HERE IN WRITING (`# DECLINED <fn> <reason>` -- board.toml's
#    `[[deny]] why=` in the one file that is not board.toml). Silence is neither,
#    and `tests/test_micropython_spike.py` fails a build.sh that is silent.
#
# DECLINED moybyte_patch_repr_c -- unboxed 30-bit floats (#66). The lever is a
#    CART INTERPRETER tax: REPR_A boxes every float RESULT in 16 bytes of heap,
#    sakura measured ~73KB of that per frame, and the heap-wrap collect it
#    forces is the 130-175ms micro-stutter. Every term in that sentence is a
#    cart frame loop, and this board has neither: carts run in the BROWSER when
#    they are paired with a Zero, and this image compiles in neither moy_lua nor
#    moycore. What is left is a non-default object representation carried by the
#    board with the least on-glass coverage in the fleet, for no measurable win.
#    Revisit the day something on this flash executes a cart.
#
# DECLINED moybyte_patch_psram_retune -- the #169 vendor-gate patch. This is
#    NOT a "no carts" argument, because the PSRAM is real (8MB octal, and the
#    heap and the lwIP buffers both live in it). It is a dependency argument:
#    the patch relaxes a vendor gate inside IDF's MSPI *timing tuning* task,
#    which is only compiled in by
#    CONFIG_SPIRAM_TIMING_TUNING_POINT_VIA_TEMPERATURE_SENSOR -- a member of the
#    five-flag 120MHz profile. sdkconfig.board keeps this board at
#    CONFIG_SPIRAM_SPEED_80M (its reasoning is beside the block), so the tuner
#    never runs and the patch would be inert. The repo already states this rule
#    in the direction that matters: a board that takes the patch must be on the
#    120MHz profile, and the spike suite fails one that is not. The empirical
#    half agrees -- this board has run 80MHz octal PSRAM unpatched since
#    2026-08-25 on stock MicroPython, which carries no such patch at all.
#    These two move together: take the 120MHz profile and take this with it.
#
# DECLINED moybyte_patch_native_code_free -- reclaims the @micropython.native
#    exec arena after a cart compile misses. Nothing here compiles a cart.
#
# DECLINED moybyte_patch_espnow_ring_race -- a torn-read race in modespnow's
#    recv ring (#7). modespnow is not in this image: no netplay, no cart net.*
#    inbox, and no second console to pair with.
#
#    Also not applied, and never were: the esp_lcd tx_color no-acquire patch
#    (there is no panel) and the #69 I2C GIL release (no input poller, no I2C
#    device on the bus).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 3) Stage: the shared native modules (board.toml [native.shared] -- moy_web
#    and nothing else) with the web blob generated INTO the staged copy, then
#    the shared Python modules (board.toml [modules.*]).
#
#    ...then the seed roster, PACKED (2026-08-30). This board shipped with NO
#    carts at all until then -- a flashed Zero came up an empty console and its
#    roster arrived over a USB cable, or never -- and the arithmetic is why.
#    BOTH FORMS WERE BUILT: with the plain `CARTS = [...]` the console boards
#    freeze, this image is 2,830,672 B of the 2,883,584 B slot and leaves
#    51 KB -- under the #168 warning floor, one cart from a build failure, in a
#    slot this board pays for twice. With the same 35 carts as one raw deflate
#    stream each it is 2,399,232 B and leaves 473 KB, and
#    `zero_host.seed_carts()` inflates them into an EMPTY store on first boot.
#    `--packed` is the only difference from the
#    console boards' invocation: same generator, same manifests, same
#    declarations.
# ---------------------------------------------------------------------------
moybyte_stage_native
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" stage "${SCRIPT_DIR}"
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_device_carts.py" --packed \
  "${MODULES_DIR}/carts_data.py"

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
