# moy_net: the links: WiFi, the ESP-NOW link, the HTTP core, the webhost, the
# sync RPC and the updater (docs/kernel_survival_2026-10.md section 6). Its
# CPython face is runtime/net_binding.py over the same C.
#
# The JSON scanner it reads a batch with is native/moy_spine/moy_json.c, which
# native/moy_store compiles: every image that takes moy_net takes moy_store.

add_library(usermod_moy_net INTERFACE)

target_sources(usermod_moy_net INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moy_http.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_sync.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_wifi.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_link.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_net.c)

target_include_directories(usermod_moy_net INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_spine)

target_link_libraries(usermod INTERFACE usermod_moy_net)
