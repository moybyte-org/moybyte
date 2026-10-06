# moy_spine: the kernel's spine, native (moy_htab.h, moy_route.h,
# moy_settings.h). Compiled in only when a build takes it -- MOY_SPINE_IMPL,
# which tools/moy_index_spike.py's header describes; with it unset or `py` the
# image keeps runtime/moy_spine.py and nothing of the module is built.
# moy_htab.c is the handle table moy_index shares: it is built when either
# takes a native twin, once -- the index's by default (MOY_INDEX_IMPL unset).
# THIS FILE AND micropython.mk ARE TWINS.

set(MOY_SPINE_IMPL "$ENV{MOY_SPINE_IMPL}")
set(MOY_INDEX_IMPL "$ENV{MOY_INDEX_IMPL}")
if(MOY_SPINE_IMPL STREQUAL "c" OR NOT MOY_INDEX_IMPL STREQUAL "py")
    add_library(usermod_moy_spine INTERFACE)
    target_sources(usermod_moy_spine INTERFACE
        ${CMAKE_CURRENT_LIST_DIR}/moy_htab.c)
    target_include_directories(usermod_moy_spine INTERFACE
        ${CMAKE_CURRENT_LIST_DIR})
    if(MOY_SPINE_IMPL STREQUAL "c")
        target_sources(usermod_moy_spine INTERFACE
            ${CMAKE_CURRENT_LIST_DIR}/modmoy_spine.c
            ${CMAKE_CURRENT_LIST_DIR}/moy_route.c
            ${CMAKE_CURRENT_LIST_DIR}/moy_ledger.c
            ${CMAKE_CURRENT_LIST_DIR}/moy_settings.c)
    endif()
    target_link_libraries(usermod INTERFACE usermod_moy_spine)
endif()
if(NOT MOY_SPINE_IMPL STREQUAL "" AND NOT MOY_SPINE_IMPL STREQUAL "py"
   AND NOT MOY_SPINE_IMPL STREQUAL "c")
    message(FATAL_ERROR "MOY_SPINE_IMPL is py or c, not '${MOY_SPINE_IMPL}'")
endif()
