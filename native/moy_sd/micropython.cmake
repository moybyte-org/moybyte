# moy_sd: the board's card in C (moy_sd.h), and its Python face.

add_library(usermod_moy_sd INTERFACE)

target_sources(usermod_moy_sd INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_sd.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_sd.c
)

target_include_directories(usermod_moy_sd INTERFACE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(usermod INTERFACE usermod_moy_sd)
