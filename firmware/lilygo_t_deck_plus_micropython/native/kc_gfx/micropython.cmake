# KidCode kc_gfx native module: VM-neutral RGB565 pixel kernel for the compositor.
# Modeled on ext_mod/kc_alloc/micropython.cmake.

add_library(usermod_kc_gfx INTERFACE)

target_sources(usermod_kc_gfx INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modkc_gfx.c
)

target_include_directories(usermod_kc_gfx INTERFACE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(usermod INTERFACE usermod_kc_gfx)
