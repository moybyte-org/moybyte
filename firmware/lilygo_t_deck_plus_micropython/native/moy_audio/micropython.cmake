# Moybyte moy_audio native module (#16, #97): the MicroPython binding for
# libmoy's SPEC.md 8 synth. Modeled on ext_mod/moy_gfx/micropython.cmake; the
# libmoy/ subdir is moy-spec's C library, vendored verbatim (libmoy/UPSTREAM.md)
# and COMPILED IN rather than reimplemented -- see modmoy_audio.c's header.
#
# The Makefile-port twin is micropython.mk (ports/unix + the wasm runner).

add_library(usermod_moy_audio INTERFACE)

target_sources(usermod_moy_audio INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_audio.c
    ${CMAKE_CURRENT_LIST_DIR}/libmoy/moy_audio.c
)

target_include_directories(usermod_moy_audio INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/libmoy
)

target_link_libraries(usermod INTERFACE usermod_moy_audio)

# OPEN ITEM -- the mixer runs from FLASH on the device.
# The previous hand-written mixer carried IRAM_ATTR, because both cores share the
# flash cache and the core-1 task's mixing bursts evicting core 0's lines was a
# MEASURED whole-console slowdown while a sound played (2026-08-03: Brick Siege's
# every-2-3s "metronome", logic/audio/chrome all inflated on sfx frames). The
# synth is vendored now, so it cannot be annotated without editing it -- the way
# to restore the placement is an ESP-IDF linker fragment mapping the object into
# IRAM (`noflash`), wired through idf_build_set_property(__LDFRAGMENTS ...).
# Deliberately NOT wired here: it cannot be compiled, let alone measured, without
# the IDF toolchain, and an untested build hack that breaks the firmware build is
# worse than a regression that shows up on a profile. Do this on the first device
# build and A/B it against #66.
