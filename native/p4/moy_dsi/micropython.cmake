# moy_dsi: the ESP32-P4 MIPI-DSI panel module (the P4 silicon tier, native/p4).
# Both vendored panel drivers compile in; the board's mpconfigboard.cmake
# names which one modmoy_dsi.c drives (MICROPY_DEF_BOARD MOY_DSI_PANEL_*).
# NOTE: the esp_lcd component dependency CANNOT be declared here -- USER_C_MODULES
# is skipped during idf.py's early-expansion phase, which is when component
# REQUIRES are collected. build.sh patches esp_lcd into esp32_common.cmake's
# IDF_COMPONENTS list instead (moybyte_idf_component esp_lcd).

add_library(usermod_moy_dsi INTERFACE)

target_sources(usermod_moy_dsi INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_dsi.c
    ${CMAKE_CURRENT_LIST_DIR}/vendor/esp_lcd_ek79007.c
    ${CMAKE_CURRENT_LIST_DIR}/vendor_jd9365/esp_lcd_jd9365.c
)

target_include_directories(usermod_moy_dsi INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/vendor/include
    ${CMAKE_CURRENT_LIST_DIR}/vendor_jd9365/include
)

# The vendored EK79007 driver expects these from its component CMake
# (cu_pkg_define_version); keep in sync with vendor/idf_component.yml.
target_compile_definitions(usermod_moy_dsi INTERFACE
    ESP_LCD_EK79007_VER_MAJOR=2
    ESP_LCD_EK79007_VER_MINOR=0
    ESP_LCD_EK79007_VER_PATCH=2
)

target_link_libraries(usermod INTERFACE usermod_moy_dsi)
