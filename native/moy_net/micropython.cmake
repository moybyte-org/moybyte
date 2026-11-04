# moy_net: the links: WiFi, the ESP-NOW link, the HTTP core, the webhost, the
# sync RPC, the updater, the `http` device API (moy_fetch.c, the module
# moy_http) and the `lora` one (moy_lora.c over the LoRaWAN MAC moy_lorawan.c
# and moy_aes.c, the module moy_lora) (docs/history/kernel_survival_2026-10.md section 6). Its
# CPython face is runtime/host/net_binding.py over the same C.
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
    ${CMAKE_CURRENT_LIST_DIR}/moy_ota.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_webconsole.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_gpio.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_dns.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_fetch.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_http.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_aes.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_lorawan.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_lora.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_lora.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_net.c)

target_include_directories(usermod_moy_net INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../moy_spine
    ${CMAKE_CURRENT_LIST_DIR}/../moy_store
    ${CMAKE_CURRENT_LIST_DIR}/../moy_web
    ${CMAKE_CURRENT_LIST_DIR}/../moy_input)

target_link_libraries(usermod INTERFACE usermod_moy_net)
