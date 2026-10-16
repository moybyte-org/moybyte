# moy_ui: the widget toolkit's bodies in C, with style and strings as data
# (README.md), and the module `moy_ui` runtime/ui.py's face calls. A console
# TAKES it in board.toml; the Zero, which runs no apps, denies it.
# THIS FILE AND micropython.mk ARE TWINS.

add_library(usermod_moy_ui INTERFACE)
target_sources(usermod_moy_ui INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moy_ui.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_ui_glyph.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_ui_draw.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_ui_paint.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_ui_depth.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_ui_interact.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_ui_sink.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_ui_api.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_ui_wasm.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_ui_tables.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_theme.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_ui.c)
# It draws through native/moy_gfx's text path into native/moy_glass's canvas
# rows, whose handle table is native/moy_spine's.
target_include_directories(usermod_moy_ui INTERFACE ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_gfx ${CMAKE_CURRENT_LIST_DIR}/../moy_gfx/libmoy
    ${CMAKE_CURRENT_LIST_DIR}/../moy_glass
    ${CMAKE_CURRENT_LIST_DIR}/../moy_spine)
# The RUN sink (moy_ui_wasm.c) reads a compiled run's libmoy canvas, whose
# layout MOY_PIXEL_RGB565 sets: the same for every translation unit that sees
# moy.h, as native/moy_gfx's and native/moycore's say.
target_compile_definitions(usermod_moy_ui INTERFACE MOY_PIXEL_RGB565=1)
target_link_libraries(usermod INTERFACE usermod_moy_ui)
