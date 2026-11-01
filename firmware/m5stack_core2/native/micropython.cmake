# Moybyte M5Stack Core2 USER_C_MODULES entry point.
#
#   moy_lcd          -- the T-Deck's SPI panel module, TAKEN from that board's
#                       tree (one body; this board's pins, MADCTL and power
#                       hooks are defines in its mpconfigboard.h).
#   moy_core2        -- board-authored, lives here: the AXP2101 power IC (the
#                       panel rail, backlight, speaker) the panel module's
#                       MOY_LCD_POWER / MOY_LCD_BACKLIGHT hooks call.
#   .staged/*        -- the SHARED native modules, staged by build.sh from
#                       native/ per board.toml [native.shared]; gitignored.
include(${CMAKE_CURRENT_LIST_DIR}/../../lilygo_t_deck_plus_mainline/native/moy_lcd/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/moy_core2/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/.staged/micropython.cmake)
