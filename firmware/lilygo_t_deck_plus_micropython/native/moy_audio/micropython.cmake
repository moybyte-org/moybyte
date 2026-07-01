# Moybyte moy_audio native module: focused PCM mixer for the v0.4 console (#16).
# Modeled on ext_mod/moy_gfx/micropython.cmake.

add_library(usermod_moy_audio INTERFACE)

target_sources(usermod_moy_audio INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_audio.c
)

target_include_directories(usermod_moy_audio INTERFACE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(usermod INTERFACE usermod_moy_audio)
