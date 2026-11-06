# moy_ssh: the SSH server (README.md) -- Dropbear's server, its crypto and
# the port, as its own static library (as moy_sh's dash is: Dropbear's
# headers and its POSIX renames, port/moy_ssh_port.h, reach no other file),
# and the module `moy_ssh`.
#
# Built into every image that stages it: a board takes or denies native/moy_ssh
# in its board.toml (README.md, Boards).

set(MOY_SSH ${CMAKE_CURRENT_LIST_DIR})
file(GLOB MOY_SSH_DB_SRCS ${MOY_SSH}/dropbear/src/*.c ${MOY_SSH}/dropbear/libtommath/*.c)
file(GLOB_RECURSE MOY_SSH_LTC_SRCS ${MOY_SSH}/dropbear/libtomcrypt/*.c)

add_library(moy_ssh_core STATIC ${MOY_SSH_DB_SRCS} ${MOY_SSH_LTC_SRCS}
    ${MOY_SSH}/moy_ssh.c
    ${MOY_SSH}/moy_ssh_face.c
    ${MOY_SSH}/moy_ssh_run.c
    ${MOY_SSH}/moy_ssh_sh.c)
set_source_files_properties(${MOY_SSH_LTC_SRCS} PROPERTIES COMPILE_DEFINITIONS LTC_SOURCE)

idf_build_get_property(_moy_ssh_inc INCLUDE_DIRECTORIES GENERATOR_EXPRESSION)
idf_build_get_property(_moy_ssh_opts COMPILE_OPTIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_ssh_copts C_COMPILE_OPTIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_ssh_defs COMPILE_DEFINITIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_ssh_cfg CONFIG_DIR)

target_include_directories(moy_ssh_core PRIVATE
    ${MOY_SSH}/port/esp
    ${MOY_SSH}/port
    ${MOY_SSH}
    ${MOY_SSH}/dropbear/src
    ${MOY_SSH}/dropbear/libtomcrypt/src/headers
    ${MOY_SSH}/dropbear/libtommath
    ${MOY_SSH}/../moy_sh
    ${MOY_SSH}/../moy_store
    ${_moy_ssh_cfg}
    "${_moy_ssh_inc}")
target_compile_definitions(moy_ssh_core PRIVATE "${_moy_ssh_defs}"
    DROPBEAR_SERVER LOCALOPTIONS_H_EXISTS)
# -Os, as the shell: a session's crypto runs at a connection's start, never on
# a frame's path, and its image cost is what each board's headroom pays.
target_compile_options(moy_ssh_core PRIVATE
    "${_moy_ssh_opts}"
    $<$<COMPILE_LANGUAGE:C>:${_moy_ssh_copts}>
    -Os -Wno-error -w
    -include ${MOY_SSH}/port/moy_ssh_names.h)
target_link_libraries(moy_ssh_core PRIVATE
    idf::freertos idf::heap idf::newlib idf::vfs idf::esp_common idf::esp_system
    idf::esp_hw_support idf::esp_timer idf::soc idf::hal idf::log idf::lwip)

add_library(usermod_moy_ssh INTERFACE)
target_sources(usermod_moy_ssh INTERFACE ${MOY_SSH}/modmoy_ssh.c)
target_include_directories(usermod_moy_ssh INTERFACE ${MOY_SSH})
target_link_libraries(usermod_moy_ssh INTERFACE moy_ssh_core)
target_link_libraries(usermod INTERFACE usermod_moy_ssh)
