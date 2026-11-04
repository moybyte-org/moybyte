# moy_sh: the terminal's C shell (README.md) -- dash's sources, the session,
# the C built-ins, the board's host table and the serial face -- and the
# MicroPython module the console's door handler writes a program's streams
# through (modmoy_sh.c). Device-only: there is no .mk twin; the unix and web
# ports have no kernel to host a session.
#
# The shell builds as its OWN static library, as moy_wasm's runtime does,
# because dash's sources take `-include dash_config.h` and the shell's POSIX
# shim (moy_sh_posix.h renames read, write, open, kill ... to the session's),
# which must reach no other usermod file, and because dash's generic header
# names (error.h, var.h, init.h, ...) must not join the MicroPython
# component's include path. moy_sh_board.c rides in the library without the
# shim: it is the kernel's side, over moy_kernel's and moy_store's headers.
# A usermod runs inside the main component's CMakeLists, so the library takes
# the IDF build's flags explicitly.

set(MOY_SH ${CMAKE_CURRENT_LIST_DIR})
file(GLOB MOY_SH_DASH_SRCS ${MOY_SH}/dash/*.c)
list(APPEND MOY_SH_DASH_SRCS
    ${MOY_SH}/dash/bltin/printf.c
    ${MOY_SH}/dash/bltin/test.c
    ${MOY_SH}/moy_sh.c
    ${MOY_SH}/moy_sh_sys.c
    ${MOY_SH}/moy_sh_cmds.c)

add_library(moy_sh_core STATIC ${MOY_SH_DASH_SRCS}
    ${MOY_SH}/moy_sh_arena.c
    ${MOY_SH}/moy_sh_fmt.c
    ${MOY_SH}/moy_sh_board.c)
set_source_files_properties(${MOY_SH_DASH_SRCS} PROPERTIES
    COMPILE_OPTIONS "-include;${MOY_SH}/dash_config.h;-I${MOY_SH}/dash")

idf_build_get_property(_moy_sh_inc INCLUDE_DIRECTORIES GENERATOR_EXPRESSION)
idf_build_get_property(_moy_sh_opts COMPILE_OPTIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_sh_copts C_COMPILE_OPTIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_sh_defs COMPILE_DEFINITIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_sh_cfg CONFIG_DIR)

target_include_directories(moy_sh_core PRIVATE
    ${MOY_SH}
    ${MOY_SH}/../moy_store
    ${MOY_SH}/../moy_kernel
    ${_moy_sh_cfg}
    "${_moy_sh_inc}")
target_compile_definitions(moy_sh_core PRIVATE "${_moy_sh_defs}")
# -Os after the build's own level (the consoles build -O2): a shell is typed
# at, never on a frame's path, and its image cost is what each board's
# headroom floor pays.
target_compile_options(moy_sh_core PRIVATE
    "${_moy_sh_opts}"
    $<$<COMPILE_LANGUAGE:C>:${_moy_sh_copts}>
    -Os)
target_link_libraries(moy_sh_core PRIVATE
    idf::freertos idf::heap idf::newlib idf::vfs idf::esp_common idf::esp_system
    idf::esp_hw_support idf::soc idf::hal idf::log)

add_library(usermod_moy_sh INTERFACE)
target_sources(usermod_moy_sh INTERFACE ${MOY_SH}/modmoy_sh.c)
target_include_directories(usermod_moy_sh INTERFACE ${MOY_SH})
target_link_libraries(usermod_moy_sh INTERFACE moy_sh_core)
target_link_libraries(usermod INTERFACE usermod_moy_sh)
