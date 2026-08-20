# Moybyte moy_alloc native module: DMA-capable allocator for the canvas blitter.
# Modeled on ext_mod/lcd_utils/micropython.cmake.

add_library(usermod_moy_alloc INTERFACE)

target_sources(usermod_moy_alloc INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_alloc.c
)

target_include_directories(usermod_moy_alloc INTERFACE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(usermod INTERFACE usermod_moy_alloc)
