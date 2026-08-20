# Moybyte moy_sd native module: SD card on the display-shared SPI host.
# Modeled on ext_mod/moy_gfx/micropython.cmake.

add_library(usermod_moy_sd INTERFACE)

target_sources(usermod_moy_sd INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_sd.c
)

target_include_directories(usermod_moy_sd INTERFACE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(usermod INTERFACE usermod_moy_sd)
