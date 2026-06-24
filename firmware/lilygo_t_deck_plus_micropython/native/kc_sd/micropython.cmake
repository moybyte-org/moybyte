# KidCode kc_sd native module: SD card on the display-shared SPI host.
# Modeled on ext_mod/kc_gfx/micropython.cmake.

add_library(usermod_kc_sd INTERFACE)

target_sources(usermod_kc_sd INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modkc_sd.c
)

target_include_directories(usermod_kc_sd INTERFACE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(usermod INTERFACE usermod_kc_sd)
