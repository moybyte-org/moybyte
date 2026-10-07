# moy_glass: the glass: canvas rows, the off-heap buffer table, the layer pool,
# the surface table (docs/kernel_survival_2026-10.md section 3). Its handle
# table is native/moy_spine's moy_htab.c, which that module builds.
# THIS FILE AND micropython.mk ARE TWINS.

add_library(usermod_moy_glass INTERFACE)

target_sources(usermod_moy_glass INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moy_glass.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_glass.c)

target_include_directories(usermod_moy_glass INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_spine)

# The draw gates (native/moy_gfx) check a canvas row through moy_canvas.h.
target_compile_definitions(usermod_moy_glass INTERFACE MOY_GLASS=1)

target_link_libraries(usermod INTERFACE usermod_moy_glass)
