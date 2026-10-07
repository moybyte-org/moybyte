# moy_audio: the kernel's audio -- sessions and the mix (moy_aud.c), the
# speaker (moy_aud_out.c), the ES8311 codec where a board names it
# (moy_codec_es8311.c), the MicroPython binding (modmoy_audio.c) -- over
# libmoy/, moy-spec's SPEC.md 8 synth, vendored verbatim and compiled in
# (libmoy/UPSTREAM.md). The table discipline is moy_spine's (moy_htab.h); the
# codec's bus is the kernel's (moy_kernel/moy_bus.h).
#
# The Makefile-port twin is micropython.mk (ports/unix and the browser).

add_library(usermod_moy_audio INTERFACE)

target_sources(usermod_moy_audio INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_audio.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_aud.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_aud_out.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_codec_es8311.c
    ${CMAKE_CURRENT_LIST_DIR}/libmoy/moy_audio.c
)

target_include_directories(usermod_moy_audio INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/libmoy
    ${CMAKE_CURRENT_LIST_DIR}/../moy_spine
    ${CMAKE_CURRENT_LIST_DIR}/../moy_kernel
)

# MOY_AUDIO_SND points moycore's half of the compiled tier at this board's
# speaker (moy_audio_snd.h); a board without this module leaves the cart's
# stream to the binding, which drains it by the clock. It reaches every
# usermod source, which is harmless: nothing else reads it.
target_compile_definitions(usermod_moy_audio INTERFACE MOY_AUDIO_SND=1)

target_link_libraries(usermod INTERFACE usermod_moy_audio)

# OPEN ITEM -- the mixer runs from FLASH on the device. The hand-written mixer
# this replaced carried IRAM_ATTR, because both cores share the flash cache and
# the core-1 task's mixing bursts evicting core 0's lines was a MEASURED
# whole-console slowdown while a sound played (2026-08-03). libmoy is vendored
# and cannot be annotated; the way to restore the placement is an ESP-IDF
# linker fragment mapping the object into IRAM (`noflash`), A/B'd against #66.
