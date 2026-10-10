# moy_spine: the kernel's spine, native on every image (moy_htab.h,
# moy_route.h, moy_settings.h; docs/kernel_spine_2026-10.md). moy_htab.c is
# the handle table moy_index shares. THIS FILE AND micropython.mk ARE TWINS.

add_library(usermod_moy_spine INTERFACE)
target_sources(usermod_moy_spine INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moy_htab.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_spine.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_route.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_ledger.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_settings.c)
target_include_directories(usermod_moy_spine INTERFACE
    ${CMAKE_CURRENT_LIST_DIR})
target_link_libraries(usermod INTERFACE usermod_moy_spine)
