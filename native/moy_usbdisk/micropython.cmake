# moy_usbdisk: the card on a computer over USB mass storage (moy_usbdisk.h).
# The espressif/tinyusb component the esp32 port already builds is configured
# by this module's moy_tusb_config.h instead of MicroPython's (whose USB device
# is off on every board): a board that takes the module gets the device stack.

add_library(usermod_moy_usbdisk INTERFACE)

target_sources(usermod_moy_usbdisk INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moy_usbdisk.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_usbdisk.c
)

target_include_directories(usermod_moy_usbdisk INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_sd
)
# the band-gap gate, where the card shares the panel's bus (a board with a flush)
if(EXISTS ${CMAKE_CURRENT_LIST_DIR}/../moy_flush)
    target_include_directories(usermod_moy_usbdisk INTERFACE ${CMAKE_CURRENT_LIST_DIR}/../moy_flush)
endif()

idf_component_get_property(moy_tusb_lib espressif__tinyusb COMPONENT_LIB)
target_compile_definitions(${moy_tusb_lib} PRIVATE
    CFG_TUSB_CONFIG_FILE="moy_tusb_config.h")
target_include_directories(${moy_tusb_lib} BEFORE PRIVATE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(usermod INTERFACE usermod_moy_usbdisk)
