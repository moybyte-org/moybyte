# moy_app: the app ABI's kernel half (README.md), over native/moy_spine's
# tables and settings rows, and the pointer type of native/moy_input. It also
# builds native/moy_store's user-files layer and its module `moy_ufiles`
# (moy_ufiles.h): the files role's server, which an image that denies this
# module (the Zero) carries none of. The layer reads covers with native/moy_png
# and writes pictures with the port's `deflate` compressor.
# THIS FILE AND micropython.mk ARE TWINS.

add_library(usermod_moy_app INTERFACE)
target_sources(usermod_moy_app INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moy_app.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_app.c
    ${CMAKE_CURRENT_LIST_DIR}/../moy_store/moy_ufiles.c
    ${CMAKE_CURRENT_LIST_DIR}/../moy_store/modmoy_ufiles.c)
target_include_directories(usermod_moy_app INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_spine
    ${CMAKE_CURRENT_LIST_DIR}/../moy_input
    ${CMAKE_CURRENT_LIST_DIR}/../moy_store
    ${CMAKE_CURRENT_LIST_DIR}/../moy_png)
target_link_libraries(usermod INTERFACE usermod_moy_app)
