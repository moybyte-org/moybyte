# moy_ui: the widget toolkit's bodies in C, with style and strings as data
# (README.md), and the module `moy_ui` runtime/ui.py's face calls. A console
# TAKES it in board.toml; the Zero, which runs no apps, denies it.
# THIS FILE AND micropython.mk ARE TWINS.

add_library(usermod_moy_ui INTERFACE)
target_sources(usermod_moy_ui INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moy_ui.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_ui_tables.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_ui.c)
target_include_directories(usermod_moy_ui INTERFACE ${CMAKE_CURRENT_LIST_DIR})
target_link_libraries(usermod INTERFACE usermod_moy_ui)
