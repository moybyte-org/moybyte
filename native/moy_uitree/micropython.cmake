# moy_uitree: the UI engine (moy_uitree.h), a page a build function
# describes, drawn from C through native/moy_ui's verbs. A console takes it
# beside moy_ui.
# THIS FILE AND micropython.mk ARE TWINS.

add_library(usermod_moy_uitree INTERFACE)
target_sources(usermod_moy_uitree INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moy_uitree.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_uitree_lang.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_uitree_walk.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_uitree_aot.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_uitree_run.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_uitree.c)
target_include_directories(usermod_moy_uitree INTERFACE ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_ui
    ${CMAKE_CURRENT_LIST_DIR}/../moy_gfx ${CMAKE_CURRENT_LIST_DIR}/../moy_gfx/libmoy
    ${CMAKE_CURRENT_LIST_DIR}/../moy_glass
    ${CMAKE_CURRENT_LIST_DIR}/../moy_spine)
# UIT_TERSE: a refusal carries its code and the names involved, not prose.
# MOYCORE_UITREE: a build that takes this engine opens its Lua face in
# moycore (native/moycore/moycore_uitree.h, beside MOYCORE_UI), the store
# the UI tree layer's C back end lowers a Lua app's pages into.
target_compile_definitions(usermod_moy_uitree INTERFACE MOY_PIXEL_RGB565=1 UIT_TERSE=1 MOYCORE_UITREE=1)
target_link_libraries(usermod INTERFACE usermod_moy_uitree)
