# Moybyte T-Deck (mainline) USER_C_MODULES entry point.
#
# Two kinds of module meet here, and the split is the whole point of this port:
#
#   moy_lcd          -- board-authored, lives here. The T-Deck's panel backend,
#                       the twin of the P4's moy_dsi.
#   .staged/*        -- the SHARED native modules, copied by build.sh from
#                       firmware/lilygo_t_deck_plus_micropython/native/, which
#                       stays their single source of truth. Same arrangement the
#                       P4 build uses; .staged/ is gitignored.
#
# Stage 1 (panel bring-up) stages moy_gfx + moy_alloc only: the pixel kernel and
# the off-gc-heap DMA allocator are what a framebuffer needs, and everything
# else (moy_sd, moy_audio, moy_lua, moycore) rides the later stages. build.sh
# generates .staged/micropython.cmake listing whatever it staged, so this file
# does not have to be edited when a stage lands.
include(${CMAKE_CURRENT_LIST_DIR}/moy_lcd/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/.staged/micropython.cmake)
