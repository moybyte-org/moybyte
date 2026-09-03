# Moybyte Zero -- Seeed XIAO ESP32-S3 (ESP32-S3R8: 8MB flash, 8MB octal PSRAM).
#
# The headless board, so the fragment list is the SHORT one and every absence
# below is a decision:
#
#   sdkconfig.ble        NOT included. The console boards activate BLE at boot
#                        (the HID keyboard is the T-Deck-less game-exit path);
#                        this board has no game, no keyboard and no netplay, and
#                        on the Guition an active NimBLE stack left FOUR BYTES
#                        free in the internal region -- enough that WiFi could
#                        not initialise at all (its sdkconfig.board carries that
#                        measurement). WiFi is the only radio this board has a
#                        use for, so BLE stays out of the image entirely.
#   sdkconfig.spiram_sx  + spiram_oct: the R8's octal PSRAM, exactly the pair
#                        the ESP32_GENERIC_S3-SPIRAM_OCT variant this board has
#                        been running on since 2026-08-25 pulls in.
#   sdkconfig.240mhz     also from that variant. Kept: the OTA path is a
#                        sha256 over ~2.5MB plus an RSA modexp, and the TLS
#                        handshake is the same CPU.
#
# No BOARD_VARIANT, same reason as the other three: half a board's identity in
# a variant file is half a board's identity somewhere else.
set(IDF_TARGET esp32s3)

set(SDKCONFIG_DEFAULTS
    boards/sdkconfig.base
    boards/sdkconfig.spiram_sx
    boards/sdkconfig.240mhz
    boards/sdkconfig.spiram_oct
    ${MICROPY_BOARD_DIR}/sdkconfig.board
)

list(APPEND MICROPY_DEF_BOARD
    MICROPY_HW_BOARD_NAME="Moybyte Zero (XIAO ESP32-S3)"
)
