# Moybyte T-Deck on MAINLINE MicroPython (the ONE build strategy, #58's pattern
# -- and the ONE T-Deck build since the lvgl_micropython fork's deletion,
# 2026-08-17): plain mainline + USER_C_MODULES, exactly like the P4.
# There is no BOARD_VARIANT: the octal-PSRAM + 240MHz fragments the generic S3
# board reaches through `SPIRAM_OCT` are listed here directly, because a
# variant would put half this board's identity in a second file for no gain.
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
    MICROPY_HW_BOARD_NAME="Moybyte T-Deck (LilyGO T-Deck Plus, mainline)"
)
