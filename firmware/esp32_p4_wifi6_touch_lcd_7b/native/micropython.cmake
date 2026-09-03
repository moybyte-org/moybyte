# Moybyte P4 USER_C_MODULES entry point.
#
# Two kinds of module meet here, same arrangement as the T-Deck's:
#
#   moy_dsi/moy_ppa/moy_ble_hid -- board-authored, live here (panel, pixel
#                                  accelerator, BLE keyboard fast path).
#   .staged/*                   -- the SHARED native modules, staged from the
#                                  repo-root native/ by board.toml
#                                  [native.shared] (tools/board_config.py
#                                  stage-native, which also generates the
#                                  .staged/micropython.cmake include list --
#                                  so this file never names a shared module).
include(${CMAKE_CURRENT_LIST_DIR}/moy_dsi/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/moy_ppa/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/moy_ble_hid/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/moy_c6/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/.staged/micropython.cmake)
