# moy_input: the merged input table, its sources and the pointer, and the
# drivers that write them (docs/kernel_survival_2026-10.md section 4). Its
# handle constants are native/moy_spine's moy_htab.h.
# THIS FILE AND micropython.mk ARE TWINS.

add_library(usermod_moy_input INTERFACE)

target_sources(usermod_moy_input INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moy_input.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_input.c)

target_include_directories(usermod_moy_input INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_spine)

target_link_libraries(usermod INTERFACE usermod_moy_input)
