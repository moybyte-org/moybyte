# Moybyte M5Stack Core2 (classic ESP32-D0WDQ6-V3, 16MB flash, 8MB quad PSRAM)
# -- the same one build strategy as the other boards: plain mainline + an
# out-of-tree board def + USER_C_MODULES. The first board on the classic ESP32:
# no octal PSRAM fragment, no 120MHz MSPI profile; sdkconfig.spiram is the
# port's own quad-PSRAM fragment, and sdkconfig.board below turns its cache
# workaround off (this chip is revision 3, which does not need it).
set(IDF_TARGET esp32)

set(SDKCONFIG_DEFAULTS
    boards/sdkconfig.base
    boards/sdkconfig.ble
    boards/sdkconfig.spiram
    boards/sdkconfig.240mhz
    ${MICROPY_BOARD_DIR}/sdkconfig.board
)

list(APPEND MICROPY_DEF_BOARD
    MICROPY_HW_BOARD_NAME="Moybyte M5Stack Core2"
)
