# moy_prof: whole-image PC sampling. Device-only -- there is no .mk twin
# because the unix and wasm ports have no CPU of ours to sample, and the module
# compiles there to a stub that says so.
#
# esp_driver_gptimer is the timer; esp_system carries the Xtensa backtrace
# helper the S3 arm walks with. Both are IDF components rather than sources, so
# this stays a two-line module.

add_library(usermod_moy_prof INTERFACE)

target_sources(usermod_moy_prof INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_prof.c
)

target_include_directories(usermod_moy_prof INTERFACE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(usermod INTERFACE usermod_moy_prof)
