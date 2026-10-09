# moycore (stage 2): the cart's whole frame in C. The cmake twin of
# micropython.mk beside it -- the boards build cmake, the unix pin build and
# the wasm runner build make, and the two must stay in step.
#
# This module's MicroPython binding, libmoy's Lua binding wrapped by
# libmoy_binding.c (which carries the pragmas a 32-bit Lua needs and a vendored
# file must not), its PICO-8 machine the same way, and its wasm import table
# (libmoy/moy_wasm.c) -- which compiles to nothing unless the engine,
# native/moy_wasm, is in the build and defines MOY_WASM. It compiles NEITHER a raster NOR a VM -- the image
# already has moy_gfx's vendored libmoy and moy_lua's vendored Lua 5.4, and a
# second copy of either would be a duplicate-symbol link error. So the include
# paths point at those siblings, and moycore requires both in the build.

add_library(usermod_moycore INTERFACE)

target_sources(usermod_moycore INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoycore.c
    ${CMAKE_CURRENT_LIST_DIR}/moycore_run.c
    ${CMAKE_CURRENT_LIST_DIR}/moycore_lua.c
    ${CMAKE_CURRENT_LIST_DIR}/libmoy_binding.c
    ${CMAKE_CURRENT_LIST_DIR}/libmoy_p8_binding.c
    ${CMAKE_CURRENT_LIST_DIR}/libmoy/moy_wasm.c
)

target_include_directories(usermod_moycore INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_gfx/libmoy
    ${CMAKE_CURRENT_LIST_DIR}/../moy_lua/lua
    ${CMAKE_CURRENT_LIST_DIR}/../moy_spine
    ${CMAKE_CURRENT_LIST_DIR}/../moy_store
    ${CMAKE_CURRENT_LIST_DIR}/../moy_glass
)

# MOY_PIXEL_RGB565 changes sizeof(moy_pixel) and therefore moy_canvas's layout,
# so it must match every translation unit that sees moy.h -- moy_gfx compiles
# with the same define. MOY_WITH_LUA is what compiles libmoy's binding at all.
target_compile_definitions(usermod_moycore INTERFACE
    MOY_PIXEL_RGB565=1
    MOY_WITH_LUA=1
)

target_link_libraries(usermod INTERFACE usermod_moycore)
