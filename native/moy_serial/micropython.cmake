# moy_serial: the console's serial line from C -- stdin read in blocks, and the
# REPL UART's rate. Device-only: the dev channel falls back to its per-byte
# reader where the module is absent (host CPython, the unix port).

add_library(usermod_moy_serial INTERFACE)

target_sources(usermod_moy_serial INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_serial.c
)

target_include_directories(usermod_moy_serial INTERFACE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(usermod INTERFACE usermod_moy_serial)
