# moy_media: the media service (moy_media.h, docs/os_2026-11/hardware.md
# section 3) -- the stream's task, the decoder (minimp3.h, vendored:
# UPSTREAM.md) and the module moy_media. It pulls into native/moy_audio's mix,
# fetches through native/moy_net's client, reads a file through native/
# moy_store's volume seam on a job of native/moy_kernel's frame and takes THE
# SHARES' MEDIA row (native/moy_play), answers the media keys on
# native/moy_input's table and serves native/moy_kernel's `media` device
# handle (moy_media_dev.c), so a board that takes it takes those;
# one without a speaker denies it.

add_library(usermod_moy_media INTERFACE)

target_sources(usermod_moy_media INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moy_media.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_media_dev.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_mp3.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_media.c
)

target_include_directories(usermod_moy_media INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_audio
    ${CMAKE_CURRENT_LIST_DIR}/../moy_net
    ${CMAKE_CURRENT_LIST_DIR}/../moy_play
    ${CMAKE_CURRENT_LIST_DIR}/../moy_store
    ${CMAKE_CURRENT_LIST_DIR}/../moy_kernel
    ${CMAKE_CURRENT_LIST_DIR}/../moy_input
)

target_link_libraries(usermod INTERFACE usermod_moy_media)
