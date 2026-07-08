# Moybyte P4 (#58): Waveshare ESP32-P4-WIFI6-Touch-LCD-7B.
# Bakes in the C6_WIFI variant of ESP32_GENERIC_P4 (same in-tree sdkconfig
# fragments) plus the board fragment (PSRAM @200MHz for the DSI scan-out,
# 32MB flash).
set(IDF_TARGET esp32p4)

set(SDKCONFIG_DEFAULTS
    boards/sdkconfig.base
    boards/sdkconfig.p4
    boards/sdkconfig.p4_wifi_common
    boards/sdkconfig.p4_wifi_c6
    ${MICROPY_BOARD_DIR}/sdkconfig.board
)

list(APPEND MICROPY_DEF_BOARD
    MICROPY_HW_BOARD_NAME="Moybyte P4 (Waveshare 7B, C6 WiFi)"
    MICROPY_PY_NETWORK_WLAN=1
    MICROPY_PY_BLUETOOTH=1
)
