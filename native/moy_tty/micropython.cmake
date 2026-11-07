# moy_tty: the shell's face over a byte stream (README.md) -- the face, its
# task and the hop to the console's task, the moy_sh session it runs, and the
# web terminal's WebSocket bridge -- as its own static library over moy_sh's,
# and the module `moy_tty`. SSH (native/moy_ssh) opens the same face.
#
# Built into every image that stages it: a board takes or denies native/moy_tty
# in its board.toml, and takes it with native/moy_sh, whose session it runs.

set(MOY_TTY ${CMAKE_CURRENT_LIST_DIR})

add_library(moy_tty_core STATIC
    ${MOY_TTY}/moy_tty.c
    ${MOY_TTY}/moy_tty_sys.c
    ${MOY_TTY}/moy_tty_sh.c
    ${MOY_TTY}/moy_tty_ws.c
    ${MOY_TTY}/moy_tty_sftp.c)

idf_build_get_property(_moy_tty_inc INCLUDE_DIRECTORIES GENERATOR_EXPRESSION)
idf_build_get_property(_moy_tty_opts COMPILE_OPTIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_tty_copts C_COMPILE_OPTIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_tty_defs COMPILE_DEFINITIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_tty_cfg CONFIG_DIR)

target_include_directories(moy_tty_core PRIVATE
    ${MOY_TTY}
    ${MOY_TTY}/../moy_sh
    ${MOY_TTY}/../moy_store
    ${MOY_TTY}/../moy_kernel
    ${MOY_TTY}/../moy_net
    ${_moy_tty_cfg}
    "${_moy_tty_inc}")
target_compile_definitions(moy_tty_core PRIVATE "${_moy_tty_defs}")
# -Os, as the shell: typed at, never on a frame's path.
target_compile_options(moy_tty_core PRIVATE
    "${_moy_tty_opts}"
    $<$<COMPILE_LANGUAGE:C>:${_moy_tty_copts}>
    -Os)
target_link_libraries(moy_tty_core PRIVATE
    idf::freertos idf::heap idf::newlib idf::vfs idf::esp_common idf::esp_system
    idf::esp_hw_support idf::esp_timer idf::soc idf::hal idf::log idf::lwip)

add_library(usermod_moy_tty INTERFACE)
target_sources(usermod_moy_tty INTERFACE ${MOY_TTY}/modmoy_tty.c)
target_include_directories(usermod_moy_tty INTERFACE ${MOY_TTY})
target_link_libraries(usermod_moy_tty INTERFACE moy_tty_core moy_sh_core)
target_link_libraries(usermod INTERFACE usermod_moy_tty)
