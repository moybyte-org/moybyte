# Moybyte Guition JC3248W535 USER_C_MODULES entry point.
#
# Same split as the other two boards:
#
#   moy_axs          -- board-authored, lives here. The AXS15231B QSPI panel
#                       backend, the third sibling of moy_lcd (T-Deck) and
#                       moy_dsi (P4).
#   .staged/*        -- the SHARED native modules, staged by build.sh from
#                       native/ per board.toml [native.shared]; gitignored.
include(${CMAKE_CURRENT_LIST_DIR}/moy_axs/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/.staged/micropython.cmake)
