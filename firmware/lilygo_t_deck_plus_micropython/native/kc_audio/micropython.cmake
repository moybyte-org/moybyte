# KidCode kc_audio native module: focused PCM mixer for the v0.4 console (#16).
# Modeled on ext_mod/kc_gfx/micropython.cmake.

add_library(usermod_kc_audio INTERFACE)

target_sources(usermod_kc_audio INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modkc_audio.c
)

target_include_directories(usermod_kc_audio INTERFACE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(usermod INTERFACE usermod_kc_audio)
