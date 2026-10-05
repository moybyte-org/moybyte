# moy_rsprobe for the ESP-IDF boards: the C binding plus the Rust staticlib
# named by $MOY_RS_PROBE_LIB (experiments/rust_probe/README.md builds it).
if(NOT DEFINED ENV{MOY_RS_PROBE_LIB})
    message(FATAL_ERROR "MOY_RS_PROBE_LIB names no Rust staticlib")
endif()

add_library(usermod_moy_rsprobe INTERFACE)
target_sources(usermod_moy_rsprobe INTERFACE ${CMAKE_CURRENT_LIST_DIR}/modmoy_rsprobe.c)
target_link_libraries(usermod_moy_rsprobe INTERFACE $ENV{MOY_RS_PROBE_LIB})
target_link_libraries(usermod INTERFACE usermod_moy_rsprobe)
