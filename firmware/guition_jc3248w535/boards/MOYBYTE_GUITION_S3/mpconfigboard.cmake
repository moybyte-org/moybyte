# Moybyte Guition JC3248W535 (ESP32-S3, 3.5" 320x480 QSPI AXS15231B) -- the
# same one build strategy as the other boards: plain mainline + an out-of-tree
# board def + USER_C_MODULES. No BOARD_VARIANT, same reason as the T-Deck: the
# octal-PSRAM + 240MHz fragments are listed directly rather than half the
# board's identity living in a variant file.
set(IDF_TARGET esp32s3)

set(SDKCONFIG_DEFAULTS
    boards/sdkconfig.base
    boards/sdkconfig.ble
    boards/sdkconfig.spiram_sx
    boards/sdkconfig.240mhz
    boards/sdkconfig.spiram_oct
    ${MICROPY_BOARD_DIR}/sdkconfig.board
)

list(APPEND MICROPY_DEF_BOARD
    MICROPY_HW_BOARD_NAME="Moybyte Guition S3 (JC3248W535)"
)
