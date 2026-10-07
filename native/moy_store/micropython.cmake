# moy_store: the store's volume seam and crash-safe write, native (moy_vol.h,
# moy_fs.h), on every board. THIS FILE AND micropython.mk ARE TWINS.

add_library(usermod_moy_store INTERFACE)
target_sources(usermod_moy_store INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_store.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_card.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_cache.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_vol.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_fs.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_cat.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_seed.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_journal.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_pack.c
    ${CMAKE_CURRENT_LIST_DIR}/../moy_spine/moy_json.c)
target_include_directories(usermod_moy_store INTERFACE ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_spine)
# moy_vol.c's FAT calls are weak references on a board (MOY_VOL_FAT_WEAK): an
# IDF image links IDF's fatfs too, whose f_open a strong one would pull in.
target_compile_definitions(usermod_moy_store INTERFACE MOY_STORE_MICROPYTHON=1
    MOY_VOL_FAT_WEAK=1)
target_link_libraries(usermod INTERFACE usermod_moy_store)
