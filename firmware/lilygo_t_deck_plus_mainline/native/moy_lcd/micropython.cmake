# moy_lcd: ST7789 SPI panel module for the T-Deck mainline build.
#
# NOTE (the same trap moy_dsi documents on the P4): the esp_lcd component
# dependency CANNOT be declared here. USER_C_MODULES is skipped during idf.py's
# early-expansion phase, which is exactly when component REQUIRES are collected,
# so appending IDF_COMPONENTS from a usermod cmake can never work. build.sh
# patches esp_lcd into esp32_common.cmake's IDF_COMPONENTS list instead.

add_library(usermod_moy_lcd INTERFACE)

target_sources(usermod_moy_lcd INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_lcd.c
)

target_link_libraries(usermod INTERFACE usermod_moy_lcd)
