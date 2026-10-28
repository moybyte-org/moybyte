# moy_index: the store's index, native (moy_index.h). Every image that takes
# the module builds it; MOY_INDEX_IMPL=py (tools/moy_index_spike.py's header)
# builds nothing here and freezes runtime/moy_index.py instead. THIS FILE AND
# micropython.mk ARE TWINS.

set(MOY_INDEX_IMPL "$ENV{MOY_INDEX_IMPL}")
if(MOY_INDEX_IMPL STREQUAL "")
    set(MOY_INDEX_IMPL "c")
endif()
if(MOY_INDEX_IMPL STREQUAL "c")
    add_library(usermod_moy_index INTERFACE)
    target_sources(usermod_moy_index INTERFACE
        ${CMAKE_CURRENT_LIST_DIR}/modmoy_index.c
        ${CMAKE_CURRENT_LIST_DIR}/moy_index.c)
    if("$ENV{MOY_INDEX_BENCH}" STREQUAL "1")
        target_sources(usermod_moy_index INTERFACE
            ${CMAKE_CURRENT_LIST_DIR}/bench_moy_index.c)
    endif()
    # moy_index.c's slots are native/moy_spine's handle table: moy_htab.h is
    # found beside it, and moy_htab.c is built by moy_spine's own fragment.
    target_include_directories(usermod_moy_index INTERFACE
        ${CMAKE_CURRENT_LIST_DIR}
        ${CMAKE_CURRENT_LIST_DIR}/../moy_spine)
    target_link_libraries(usermod INTERFACE usermod_moy_index)
elseif(NOT MOY_INDEX_IMPL STREQUAL "py")
    message(FATAL_ERROR "MOY_INDEX_IMPL is py or c, not '${MOY_INDEX_IMPL}'")
endif()
