# moy_usb: the `usb` device API's driver (moy_usb.h) -- the P4 silicon tier
# (native/p4): the USB host on the P4's OTG port, its class drivers over
# ESP-IDF's USB Host Library (the `usb` component, already in the esp32 port's
# IDF_COMPONENTS). Its records go through native/moy_input's rings.

add_library(usermod_moy_usb INTERFACE)

target_sources(usermod_moy_usb INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moy_usb.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_usb_host.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_usb.c
)

target_include_directories(usermod_moy_usb INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_input
    ${CMAKE_CURRENT_LIST_DIR}/../moy_spine
    ${CMAKE_CURRENT_LIST_DIR}/../moy_kernel
)

target_link_libraries(usermod INTERFACE usermod_moy_usb)
