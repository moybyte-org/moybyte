# Moybyte moy_gfx native module: VM-neutral RGB565 pixel kernel for the compositor.
# Modeled on ext_mod/moy_alloc/micropython.cmake.
#
# libmoy/ is moy-spec's own raster, vendored verbatim (libmoy/UPSTREAM.md) and
# compiled in with MOY_PIXEL_RGB565 -- the direct-colour build, because this
# console's canvas holds 16-bit words rather than palette indices (SPEC.md 1.1
# leaves that to the host). Some of moy_gfx's verbs are thin wrappers over it and
# the rest are still moybyte's own; UPSTREAM.md says which and why.

add_library(usermod_moy_gfx INTERFACE)

# libmoy's three .c files are NOT listed here: they are #included by
# libmoy_kernels.c, which exists to compile them at -O3 alongside the kernel
# that calls them. Listing them here as well would define every symbol twice.
# That file's header explains why the pragma cannot live in the vendored copies.
#
# moy_gfx_kernels.c is the COMPOSITOR (the loops runtime/moyhost_gfx.c also
# links, so there is one copy); it carries its own -O3 pragma. THIS LIST AND
# micropython.mk'S ARE TWINS -- the .mk is what the unix build reads, and the
# two have drifted before (when six verbs became libmoy calls, cmake gained the
# sources and the .mk did not, and the unix port stopped linking).
target_sources(usermod_moy_gfx INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_gfx.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_gfx_kernels.c
    ${CMAKE_CURRENT_LIST_DIR}/libmoy_kernels.c
)

target_include_directories(usermod_moy_gfx INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/libmoy
)

# The pixel format is a libmoy BUILD option and must be the same for every
# translation unit that sees moy.h -- it changes the size of moy_pixel and
# therefore the layout of moy_canvas. A mismatch links cleanly and corrupts
# memory, so it is set here, once, for the whole module.
target_compile_definitions(usermod_moy_gfx INTERFACE MOY_PIXEL_RGB565=1)

target_link_libraries(usermod INTERFACE usermod_moy_gfx)
