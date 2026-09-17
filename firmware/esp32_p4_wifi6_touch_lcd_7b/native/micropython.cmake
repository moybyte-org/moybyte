# Moybyte P4 USER_C_MODULES entry point.
#
# Everything this board compiles in is STAGED by board.toml (tools/
# board_config.py stage-native, which also generates the .staged/
# micropython.cmake include list): the shared modules from native/
# ([native.shared]) and the P4 SILICON tier from native/p4/ ([native.p4] --
# the panel, the pixel accelerator, the BLE keyboard fast path and the C6
# shim, which lived in this directory until the Guition P4 became their second
# consumer on 2026-09-06). So this file never names a module.
include(${CMAKE_CURRENT_LIST_DIR}/.staged/micropython.cmake)
