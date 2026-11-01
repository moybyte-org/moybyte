# moy_core2: the M5Stack Core2's AXP2101 power IC (board-authored).
add_library(usermod_moy_core2 INTERFACE)
target_sources(usermod_moy_core2 INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_core2.c
)
target_include_directories(usermod_moy_core2 INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/../.staged/moy_kernel
)
target_link_libraries(usermod INTERFACE usermod_moy_core2)
