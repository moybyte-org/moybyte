# Moybyte Elecrow P4: Elecrow CrowPanel Advance 10.1" ESP32-P4 HMI
# (ESP32-P4NRW32 + ESP32-C6, 1024x600 EK79007 MIPI-DSI). Bakes in the C6_WIFI
# variant of ESP32_GENERIC_P4 (same in-tree sdkconfig fragments) plus the board
# fragment (PSRAM @200MHz for the DSI scan-out, 16MB flash, the OTA-shaped
# partition table, the ESP-Hosted pins of this PCB).
set(IDF_TARGET esp32p4)

set(SDKCONFIG_DEFAULTS
    boards/sdkconfig.base
    boards/sdkconfig.p4
    boards/sdkconfig.p4_wifi_common
    boards/sdkconfig.p4_wifi_c6
    ${MICROPY_BOARD_DIR}/sdkconfig.board
)

list(APPEND MICROPY_DEF_BOARD
    MICROPY_HW_BOARD_NAME="Moybyte Elecrow P4 (CrowPanel Advance 10.1, C6 WiFi)"
    MICROPY_PY_NETWORK_WLAN=1
    # No `bluetooth` module: the BLE keyboard is the kernel's central over
    # NimBLE (native/moy_input, MOY_INPUT_BLE in mpconfigboard.h).
    MICROPY_PY_BLUETOOTH=0
    # The PANEL the shared native/p4/moy_dsi drives: the EK79007 (1024x600),
    # the Waveshare 7B's controller on a bigger glass. Its reset is GPIO41
    # here (the 7B's is 33), and the panel's scan clock and blanking are the
    # vendor firmware's for this glass (modmoy_dsi.c's override macros).
    MOY_DSI_PANEL_EK79007=1
    MOY_DSI_LCD_RST_GPIO=41
    # The blanking the vendor firmware gives this glass (hsync pulse 70, vsync
    # pulse 10 against the vendored component's 10 and 1); the scan clock stays
    # the 7B's 60 MHz, a 65.8 Hz refresh over this 1414x645 timing, where the
    # vendor's 51 MHz is no divisor of the 240 MHz PLL (48 MHz is the divisor
    # below it: 52.6 Hz).
    MOY_DSI_HSYNC_PW=70
    MOY_DSI_VSYNC_PW=10
    # Its backlight, which moy_dsi drives (backlight(), and the kernel's
    # recovery floor): GPIO31 into the MT9201 boost driver's EN, active HIGH
    # (the vendor firmware's duty rises with brightness).
    MOY_DSI_BL_GPIO=31
    MOY_DSI_BL_ACTIVE_LOW=0
    # LDO channel 4 at 3.3V, acquired with the panel's PHY rail: it powers this
    # PCB's GPIO40-48 pad domain and the I2C bus's pull-ups (the touch
    # controller sits behind them), so nothing on I2C0 answers without it.
    MOY_DSI_IO_LDO_MV=3300
)
