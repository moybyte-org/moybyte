# moy_flush: the shared banded-flush engine under the two S3 panel drivers.
#
# NOT a MicroPython module -- it registers no qstr and no globals table. It is
# a support library the board-authored panel modules (the T-Deck's moy_lcd,
# the Guition's moy_axs) link and call; see moy_flush.h for the split. It
# rides the [native.shared] staging anyway, because that is where "which C
# crosses to which board" is written down (#161), and a board with no banded
# flush denies it there with a reason (the P4 does -- MIPI-DSI scans a PSRAM
# framebuffer continuously and has no bands to feed).
#
# The include directory is INTERFACE and lands on `usermod`, which every
# usermod source is compiled into -- which is what lets a BOARD-authored
# module in firmware/<board>/native/ say `#include "moy_flush.h"` without
# naming the staged path.

add_library(usermod_moy_flush INTERFACE)

target_sources(usermod_moy_flush INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moy_flush.c
)

target_include_directories(usermod_moy_flush INTERFACE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(usermod INTERFACE usermod_moy_flush)
