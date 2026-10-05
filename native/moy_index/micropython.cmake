# moy_index: the store's index, native (moy_index.h). Compiled in only when a
# build takes a twin -- MOY_INDEX_IMPL, which tools/moy_index_spike.py's header
# describes; with it unset or `py` the image keeps runtime/moy_index.py and
# nothing here is built. THIS FILE AND micropython.mk ARE TWINS.

set(MOY_INDEX_IMPL "$ENV{MOY_INDEX_IMPL}")
if(MOY_INDEX_IMPL STREQUAL "c" OR MOY_INDEX_IMPL STREQUAL "rust")
    add_library(usermod_moy_index INTERFACE)
    target_sources(usermod_moy_index INTERFACE
        ${CMAKE_CURRENT_LIST_DIR}/modmoy_index.c)
    if(MOY_INDEX_IMPL STREQUAL "c")
        target_sources(usermod_moy_index INTERFACE
            ${CMAKE_CURRENT_LIST_DIR}/moy_index.c)
    else()
        if(NOT EXISTS "$ENV{MOY_INDEX_RUST_LIB}")
            message(FATAL_ERROR "MOY_INDEX_IMPL=rust links MOY_INDEX_RUST_LIB, "
                "a static library implementing moy_index.h; it is "
                "'$ENV{MOY_INDEX_RUST_LIB}'")
        endif()
        target_link_libraries(usermod_moy_index INTERFACE
            "$ENV{MOY_INDEX_RUST_LIB}")
    endif()
    if("$ENV{MOY_INDEX_BENCH}" STREQUAL "1")
        target_sources(usermod_moy_index INTERFACE
            ${CMAKE_CURRENT_LIST_DIR}/bench_moy_index.c)
    endif()
    target_include_directories(usermod_moy_index INTERFACE
        ${CMAKE_CURRENT_LIST_DIR})
    target_link_libraries(usermod INTERFACE usermod_moy_index)
elseif(NOT MOY_INDEX_IMPL STREQUAL "" AND NOT MOY_INDEX_IMPL STREQUAL "py")
    message(FATAL_ERROR "MOY_INDEX_IMPL is py, c or rust, not '${MOY_INDEX_IMPL}'")
endif()
