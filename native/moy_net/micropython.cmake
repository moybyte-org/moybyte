# moy_net: the links: WiFi, the ESP-NOW link, the HTTP core, the webhost, the
# sync RPC and the updater (docs/kernel_survival_2026-10.md section 6). Its
# CPython face is runtime/net_binding.py over the same C.
#
# The JSON scanner it reads a batch with is native/moy_spine/moy_json.c, and
# the store it applies one into and pulls from is native/moy_store, which
# compiles both: every image that takes moy_net takes moy_store. The bundle it
# serves is native/moy_web's, where the image takes that too.

add_library(usermod_moy_net INTERFACE)

target_sources(usermod_moy_net INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moy_http.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_net_port.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_sync.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_wifi.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_link.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_sync_apply.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_webhost.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_net.c)

target_include_directories(usermod_moy_net INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_spine
    ${CMAKE_CURRENT_LIST_DIR}/../moy_store
    ${CMAKE_CURRENT_LIST_DIR}/../moy_web)

target_link_libraries(usermod INTERFACE usermod_moy_net)
