# Moybyte moy_web native module: the browser console bundle, baked into the
# image (see modmoy_web.c for why, and tools/gen_web_blob.py for how).
#
# moy_web_blob.gen.c is GENERATED and gitignored -- both boards' build.sh run
# tools/gen_web_blob.py before staging this directory, so the file is always
# present by the time cmake reads this list. If a configure fails here with
# "Cannot find source file", the generator did not run: build.sh is the only
# supported entry point.
#
# THIS LIST AND micropython.mk'S ARE TWINS. moy_gfx's pair drifted exactly once
# and the unix port stopped linking with nothing to say why (its header carries
# the story); a module this small has no excuse to repeat it.

add_library(usermod_moy_web INTERFACE)

target_sources(usermod_moy_web INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_web.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_web_blob.gen.c
)

target_include_directories(usermod_moy_web INTERFACE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(usermod INTERFACE usermod_moy_web)
