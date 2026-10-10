# moy_app: the app ABI's kernel half (README.md), over native/moy_spine's
# tables and settings rows. THIS FILE AND micropython.mk ARE TWINS.

add_library(usermod_moy_app INTERFACE)
target_sources(usermod_moy_app INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moy_app.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_app.c)
target_include_directories(usermod_moy_app INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_spine)
target_link_libraries(usermod INTERFACE usermod_moy_app)
