# moy_ppa: ESP32-P4 PPA (pixel accelerator) module -- the P4 silicon tier
# (native/p4, every ESP32-P4 board takes it; born on the Waveshare 7B, #58).
# NOTE: like moy_dsi/esp_lcd, the esp_driver_ppa component dependency CANNOT be
# declared here -- USER_C_MODULES is skipped during idf.py's early-expansion
# phase, which is when component REQUIRES are collected. build.sh patches
# esp_driver_ppa into esp32_common.cmake's IDF_COMPONENTS list instead.

add_library(usermod_moy_ppa INTERFACE)

target_sources(usermod_moy_ppa INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_ppa.c
)

# blit_crisp expands through moy_gfx's mg_blit565_scale (the ONE kernel body).
# board_config stages the shared moy_gfx beside this module (both land in the
# board's native/.staged/) before idf.py runs, so
# the header is there whenever this file compiles.
target_include_directories(usermod_moy_ppa INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/../moy_gfx
)

target_link_libraries(usermod INTERFACE usermod_moy_ppa)
