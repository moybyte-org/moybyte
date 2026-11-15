# Moybyte Zero (ESP32-C3) USER_C_MODULES entry point: the XIAO Zero's shape,
# one line. No panel, no touch controller, nothing on a bus that wants C, so
# what it includes is only:
#
#   .staged/*  -- the SHARED native modules, staged by build.sh from native/
#                 per board.toml [native.shared]; gitignored.
include(${CMAKE_CURRENT_LIST_DIR}/.staged/micropython.cmake)
