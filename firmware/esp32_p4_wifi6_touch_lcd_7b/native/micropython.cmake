# Moybyte P4 USER_C_MODULES entry point: the board-authored moy_dsi panel module
# plus the shared native modules staged from the T-Deck tree by build.sh
# (native/.staged/, gitignored -- single source of truth stays in
# firmware/lilygo_t_deck_plus_micropython/native/).
include(${CMAKE_CURRENT_LIST_DIR}/moy_dsi/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/moy_ppa/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/moy_ble_hid/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/.staged/moy_gfx/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/.staged/moy_alloc/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/.staged/moy_lua/micropython.cmake)
