# Moybyte moy_lua native module (#67 Phase 1): vendored Lua 5.4 + the cart
# bridge (modmoy_lua.c). Modeled on ext_mod/moy_gfx/micropython.cmake; the lua/
# subdir is the stock 5.4 library sources (no lua.c/luac.c standalone mains).

add_library(usermod_moy_lua INTERFACE)

file(GLOB MOY_LUA_VM_SRCS "${CMAKE_CURRENT_LIST_DIR}/lua/*.c")
# The cart sandbox opens base/math/string/table ONLY (modmoy_lua.c), so the
# unused stdlibs -- and linit.c, whose luaL_openlibs references all of them --
# stay out of the build entirely (no io/os symbols, less flash, no -Werror
# exposure on host-OS calls newlib stubs oddly).
list(REMOVE_ITEM MOY_LUA_VM_SRCS
    ${CMAKE_CURRENT_LIST_DIR}/lua/linit.c
    ${CMAKE_CURRENT_LIST_DIR}/lua/liolib.c
    ${CMAKE_CURRENT_LIST_DIR}/lua/loslib.c
    ${CMAKE_CURRENT_LIST_DIR}/lua/loadlib.c
    ${CMAKE_CURRENT_LIST_DIR}/lua/ldblib.c
    ${CMAKE_CURRENT_LIST_DIR}/lua/lcorolib.c
    ${CMAKE_CURRENT_LIST_DIR}/lua/lutf8lib.c
)

target_sources(usermod_moy_lua INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_lua.c
    ${MOY_LUA_VM_SRCS}
)

target_include_directories(usermod_moy_lua INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/lua
)

target_link_libraries(usermod INTERFACE usermod_moy_lua)
