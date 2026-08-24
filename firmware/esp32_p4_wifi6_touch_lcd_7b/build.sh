#!/usr/bin/env bash
# Moybyte P4 port (#58): build mainline MicroPython (ESP32_GENERIC_P4, C6_WIFI
# variant) + the moy_dsi native module (EK79007 MIPI-DSI panel) for the
# Waveshare ESP32-P4-WIFI6-Touch-LCD-7B.
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
PATCH_DIR="${SCRIPT_DIR}/patches"
DIST_DIR="${REPO_ROOT}/dist/p4"
MODULES_DIR="${SCRIPT_DIR}/modules"
MANIFEST="${BUILD_DIR}/moybyte_p4_manifest.py"

# shellcheck source=../../tools/esp32_build_lib.sh
source "${REPO_ROOT}/tools/esp32_build_lib.sh"
moybyte_resolve_build_python

mkdir -p "${BUILD_DIR}" "${DIST_DIR}" "${MODULES_DIR}"

# ---------------------------------------------------------------------------
# 1) Toolchain: MicroPython at the pinned tag, and ESP-IDF v5.5.1, reusing the
#    T-Deck's checkout when it exists (same version, saves a 500MB clone).
# ---------------------------------------------------------------------------
moybyte_clone_micropython
moybyte_setup_idf esp32p4 \
  "${REPO_ROOT}/firmware/lilygo_t_deck_plus_mainline/.build/esp-idf"

# ---------------------------------------------------------------------------
# 2) The patch ladder -- THIS BOARD'S half of the build. All marker-guarded,
#    because both the .build tree and a reused IDF checkout persist.
# ---------------------------------------------------------------------------

# 2a) Steady-state BLE keyboard notifications must not wait behind MicroPython's
#     synchronous NimBLE IRQ/GIL path. The P4-only native queue consumes
#     registered HID handles before Python dispatch; pairing/bonding/discovery
#     remain on the supported synchronous path.
MODBLUETOOTH_C="${MPY_DIR}/extmod/modbluetooth.c"
if [ -f "${MODBLUETOOTH_C}" ] && \
   ! grep -q "moy_ble_hid_queue_on_notify" "${MODBLUETOOTH_C}"; then
  echo "== applying P4 BLE-HID native notification fast-path patch"
  patch -d "${MPY_DIR}" -p1 < "${PATCH_DIR}/modbluetooth_ble_hid_fastpath.patch"
fi

# 2b) #106: backport current ESP-IDF's dedicated DSI bridge-underrun ISR and
#     keep the frame-restart DW-GDMA interrupt above ESP-Hosted's SDIO
#     interrupt. IDF v5.5 checks the bridge only from the DMA callback; if SDIO
#     delays that callback, the display has already gone blue and the status
#     can be cleared unseen.
DSI_DPI_C="${IDF_DIR}/components/esp_lcd/dsi/esp_lcd_panel_dpi.c"
if [ -f "${DSI_DPI_C}" ] && \
   ! grep -q "Moybyte P4: dedicated DSI bridge underrun IRQ" "${DSI_DPI_C}"; then
  echo "== applying P4 DSI bridge IRQ/priority fix (#106)"
  patch -d "${IDF_DIR}" -p1 < "${PATCH_DIR}/esp_lcd_dsi_underrun_hook.patch"
fi

# 2c) moy_dsi needs esp_lcd, moy_ppa needs esp_driver_ppa (the P4 pixel
#     accelerator) in the main component's REQUIRES.
moybyte_idf_component esp_lcd
moybyte_idf_component esp_driver_ppa

# 2c') ESP-Hosted 2.7.0 -> 2.12.12 (the espnow-on-p4 track,
#      docs/espnow_p4_2026-08.md). MicroPython pins the hosted component at
#      exactly 2.7.0; 2.12.12 carries the custom-RPC seam
#      (esp_hosted_send_custom_data / register_custom_callback) the P4's
#      ESP-NOW shim rides, plus the streamed slave-OTA API that updates the C6
#      from this board over SDIO. esp_wifi_remote 0.15.2 constrains only
#      >=0.0.6, so the bump is manifest-legal. PROVEN ON GLASS 2026-08-24
#      against the FACTORY C6 slave before any shim existed: builds clean,
#      boots clean (with the MEMPOOL_PREFER_SPIRAM fragment line -- without it
#      the 2.12 transport mempool fails its internal-SRAM allocation at boot
#      and the board crash-loops), wifi at RX parity (2.9-3.0 MB/s vs 2.7.0's
#      3.2), BLE up and scanning. The stale per-target lockfile is dropped so
#      the component manager re-resolves; it pins the new tree on first build.
MAIN_MANIFEST="${MPY_DIR}/ports/esp32/main/idf_component.yml"
if grep -q 'version: "2.7.0"' "${MAIN_MANIFEST}"; then
  echo "== bumping esp_hosted 2.7.0 -> 2.12.12 (espnow-on-p4 track)"
  sed -i 's/^    version: "2.7.0"$/    version: "2.12.12"/' "${MAIN_MANIFEST}"
  rm -f "${MPY_DIR}/ports/esp32/lockfiles/dependencies.lock.esp32p4"
  rm -rf "${MPY_DIR}/ports/esp32/managed_components/espressif__esp_hosted"
fi

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

# DECLINED moybyte_patch_psram_retune -- not applicable. That patch relaxes the
# ESP32-S3 MSPI timing tuner's flash-vendor gate (#169); this is an ESP32-P4 and
# the file does not exist in its build. Its PSRAM constraint is a different one
# entirely (200MHz or the DSI scan-out underruns -- see this dir's README).

# ---------------------------------------------------------------------------
# 3) Stage: the shared native modules (board.toml [native.shared] -- the two
#    denials, moy_sd and moy_audio, live there WITH their reasons; all plain C,
#    the S3-specific pieces are include-guarded, so they compile unchanged on
#    RISC-V) with the browser console blob generated into the staged copy
#    (never into the shared native/ tree two builds read -- this used to
#    generate there and race a concurrent T-Deck build); then the shared
#    PYTHON modules (#58 console staging, #161 Phase 3 -- board.toml holds the
#    denylist over runtime/ and the allowlist over device/, and the stager
#    prunes untracked strays); then the generated modules.
# ---------------------------------------------------------------------------
moybyte_stage_native
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/board_config.py" stage "${SCRIPT_DIR}"

#    carts_data.py is GENERATED from system_carts/ (same as the T-Deck) so the
#    P4's seed/fallback carts can never drift from the host source of truth.
"${BUILD_PYTHON}" "${REPO_ROOT}/tools/gen_device_carts.py" "${MODULES_DIR}/carts_data.py"

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
