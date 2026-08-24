# P4 C6 plumbing: the esp_now_* shim over ESP-Hosted custom RPC (what lets
# MICROPY_PY_ESPNOW link on a radioless SoC) + the moy_c6 module (coprocessor
# version / shim ping / streamed slave OTA).

add_library(usermod_moy_c6 INTERFACE)

target_sources(usermod_moy_c6 INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_c6.c
)

target_include_directories(usermod_moy_c6 INTERFACE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(usermod INTERFACE usermod_moy_c6)
