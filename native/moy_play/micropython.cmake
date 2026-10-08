# moy_play: the kernel's Player (docs/kernel_cartpath_2026-10.md §3) -- the
# tick model (moy_tick.c), the runtime map (moy_rt.c), the VM-free rule
# (moy_play_rule.c) and the module `moy_play` (modmoy_play.c). A console TAKES
# it in board.toml ([[native.shared.take]]); the Zero, which runs no carts,
# denies it.

add_library(usermod_moy_play INTERFACE)

target_sources(usermod_moy_play INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_play.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_tick.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_rt.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_play_rule.c
)

# The catalogue entry the rule reads is the store's (moy_cat.h, moy_json.h).
target_include_directories(usermod_moy_play INTERFACE ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_store ${CMAKE_CURRENT_LIST_DIR}/../moy_spine)

target_link_libraries(usermod INTERFACE usermod_moy_play)
