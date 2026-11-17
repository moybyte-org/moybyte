set(IDF_TARGET esp32c3)

# MicroPython's ESP32_GENERIC_C3 fragments without sdkconfig.ble (no NimBLE in
# this image: mpconfigboard.h), then this board's.
set(SDKCONFIG_DEFAULTS
    boards/sdkconfig.base
    boards/sdkconfig.riscv
    boards/ESP32_GENERIC_C3/sdkconfig.c3usb
    ${MICROPY_BOARD_DIR}/sdkconfig.board
)

list(APPEND MICROPY_DEF_BOARD
    MICROPY_HW_BOARD_NAME="Moybyte Zero (ESP32-C3)"
)

set(MICROPY_PY_BTREE OFF)
