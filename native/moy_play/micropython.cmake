# moy_play: the kernel's Player (docs/kernel_cartpath_2026-10.md §3) -- the
# tick model (moy_tick.c), the runtime map (moy_rt.c), the VM-free rule
# (moy_play_rule.c), the Player (moy_play.c) and the module `moy_play`
# (modmoy_play.c). A console TAKES
# it in board.toml ([[native.shared.take]]); the Zero, which runs no carts,
# denies it.

add_library(usermod_moy_play INTERFACE)

target_sources(usermod_moy_play INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_play.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_match.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_chrome.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_tick.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_rt.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_play_rule.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_play.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_match.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_match_kernel.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_chrome.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_play_stop.c
)

# moy_play_stop.c is the stop's run (a board with MOY_VM_STOP; empty on any
# other), over the kernel's present, moycore's C open and the store's volume.
# The catalogue entry the rule reads is the store's (moy_cat.h, moy_json.h).
# The frame drives moycore's runs over the input table and the loop's crossing
# counts (moycore_lua.h, moy_input.h, moy_loop.h); the audio sessions are
# moy_audio's where the board takes it (moy_play.c asks for moy_aud.h by its
# relative path, so a board without it, the Guition S3, has no directory to
# name here).
target_include_directories(usermod_moy_play INTERFACE ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_store ${CMAKE_CURRENT_LIST_DIR}/../moy_spine
    ${CMAKE_CURRENT_LIST_DIR}/../moycore ${CMAKE_CURRENT_LIST_DIR}/../moy_gfx/libmoy
    ${CMAKE_CURRENT_LIST_DIR}/../moy_gfx
    ${CMAKE_CURRENT_LIST_DIR}/../moy_lua/lua ${CMAKE_CURRENT_LIST_DIR}/../moy_input
    ${CMAKE_CURRENT_LIST_DIR}/../moy_kernel ${CMAKE_CURRENT_LIST_DIR}/../moy_glass)

target_link_libraries(usermod INTERFACE usermod_moy_play)
