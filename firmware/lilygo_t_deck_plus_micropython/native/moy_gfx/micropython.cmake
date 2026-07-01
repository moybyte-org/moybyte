# Moybyte moy_gfx native module: VM-neutral RGB565 pixel kernel for the compositor.
# Modeled on ext_mod/moy_alloc/micropython.cmake.

add_library(usermod_moy_gfx INTERFACE)

target_sources(usermod_moy_gfx INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_gfx.c
)

target_include_directories(usermod_moy_gfx INTERFACE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(usermod INTERFACE usermod_moy_gfx)
