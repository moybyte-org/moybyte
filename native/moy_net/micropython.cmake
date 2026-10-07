# moy_net: the links: WiFi, the ESP-NOW link, the HTTP core, the webhost, the
# sync RPC and the updater (docs/kernel_survival_2026-10.md section 6). Its
# Python twin is runtime/moy_net.py.
#
# Every build already lists this directory and it compiles nothing yet: the
# pass that crosses the subsystem adds its sources here, and no build list
# changes when it does.

add_library(usermod_moy_net INTERFACE)

target_link_libraries(usermod INTERFACE usermod_moy_net)
