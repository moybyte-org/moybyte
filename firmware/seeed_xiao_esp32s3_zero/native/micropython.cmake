# Moybyte Zero USER_C_MODULES entry point.
#
# The other boards include a board-AUTHORED panel backend here beside the
# staged set. This board has none: no panel, no touch controller, nothing on a
# bus that wants C. So the file is one line, and what it includes is:
#
#   .staged/*  -- the SHARED native modules, staged by build.sh from native/
#                 per board.toml [native.shared]; gitignored. The set, and each
#                 denial's reason, are board.toml's.
include(${CMAKE_CURRENT_LIST_DIR}/.staged/micropython.cmake)
