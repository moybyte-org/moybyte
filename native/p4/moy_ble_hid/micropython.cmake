# P4 BLE-HID fast path: NimBLE-host-task report queue drained by MicroPython.

add_library(usermod_moy_ble_hid INTERFACE)

target_sources(usermod_moy_ble_hid INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_ble_hid.c
)

target_link_libraries(usermod INTERFACE usermod_moy_ble_hid)
