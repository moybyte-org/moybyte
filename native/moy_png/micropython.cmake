# moy_png: the cover reader (SPEC.md 3.6). Pure C plus its binding; see
# moy_png.h. THIS LIST AND micropython.mk ARE TWINS.

add_library(usermod_moy_png INTERFACE)

target_sources(usermod_moy_png INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_png.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_png.c
)

target_include_directories(usermod_moy_png INTERFACE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(usermod INTERFACE usermod_moy_png)
