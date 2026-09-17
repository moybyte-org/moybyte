# Moybyte Guition P4 USER_C_MODULES entry point.
#
# This board authors NO native module of its own: everything it compiles in
# is STAGED by board.toml (tools/board_config.py stage-native, which also
# generates the .staged/micropython.cmake include list) -- the shared modules
# from native/ ([native.shared]) and the P4 silicon tier from native/p4/
# ([native.p4]: the panel, the pixel accelerator, the BLE keyboard fast path,
# the C6 shim). So this file never names a module.
include(${CMAKE_CURRENT_LIST_DIR}/.staged/micropython.cmake)
