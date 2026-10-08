# moy_kernel: the kernel's entry, its crash record and its recovery floor
# (moy_kernel.c), its first light (moy_boot.c), the board's I2C bus (moy_bus.c), and the frame: the loop,
# the idle ladder, PERF and the dev channel (moy_loop.c, moy_idle.c,
# moy_perf.c, moy_devch.c, the module `moy_loop`). A board TAKES it in
# board.toml ([[native.shared.take]]), and taking it is what makes the kernel
# own app_main.
#
# The INTERFACE definitions reach every source of the main component
# (esp32_common.cmake links `usermod` into it), so MICROPY_ESP_IDF_ENTRY renames
# the port's app_main in ports/esp32/main.c; the link option reaches the
# executable through the same chain, and wraps the IDF's panic handler.

add_library(usermod_moy_kernel INTERFACE)

target_sources(usermod_moy_kernel INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moy_kernel.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_kernel.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_crash.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_recovery.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_boot.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_bus.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_loop.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_idle.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_perf.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_devch.c
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_loop.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_loop_board.c
)

target_include_directories(usermod_moy_kernel INTERFACE ${CMAKE_CURRENT_LIST_DIR})

target_compile_definitions(usermod_moy_kernel INTERFACE
    MICROPY_ESP_IDF_ENTRY=mp_port_app_main
)

target_link_options(usermod_moy_kernel INTERFACE "-Wl,--wrap=esp_panic_handler")

target_link_libraries(usermod INTERFACE usermod_moy_kernel)
