# Moybyte Guition P4: Guition JC8012P4A1C (ESP32-P4NRW32 + ESP32-C6, 10.1"
# 800x1280 MIPI-DSI). Bakes in the C6_WIFI variant of ESP32_GENERIC_P4 (same
# in-tree sdkconfig fragments) plus the board fragment (PSRAM @200MHz for the
# DSI scan-out, 16MB flash, the OTA-shaped partition table).
set(IDF_TARGET esp32p4)

set(SDKCONFIG_DEFAULTS
    boards/sdkconfig.base
    boards/sdkconfig.p4
    boards/sdkconfig.p4_wifi_common
    boards/sdkconfig.p4_wifi_c6
    ${MICROPY_BOARD_DIR}/sdkconfig.board
)

list(APPEND MICROPY_DEF_BOARD
    MICROPY_HW_BOARD_NAME="Moybyte Guition P4 (JC8012P4A1C, C6 WiFi)"
    MICROPY_PY_NETWORK_WLAN=1
    MICROPY_PY_BLUETOOTH=1
    MICROPY_HW_MOYBYTE_P4_BLE_HID_QUEUE=1
    # The PANEL the shared native/p4/moy_dsi drives: this board's JD9365
    # (800x1280 portrait-native, reset GPIO27, 2-lane DSI @1500Mbps, DPI
    # 60MHz -- the factory demo's numbers). The panel facts live behind this
    # one name in modmoy_dsi.c.
    MOY_DSI_PANEL_JD9365=1
    # The factory demo drives the JD9365 mirrored on both axes (MADCTL GS|SS)
    # -- a 180-degree image. 1 reproduces that; 0 is the panel's native scan.
    # Which way is "up" on the owner's desk is a glass question (README).
    MOY_DSI_MIRROR_XY=1
)
