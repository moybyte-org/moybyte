# Moybyte's vendored Lua 5.4 (#67). The lua/ subdir is the stock 5.4 library
# sources (no lua.c/luac.c standalone mains) and that is now ALL this is: the
# cart bridge that used to live beside it (modmoy_lua.c -- LuaCartRun's ~40
# registered trampolines, the direct-draw family, the batch protocol) is gone,
# because moycore binds the same VM through libmoy and there is one Lua runtime.
# So this target exports no MicroPython module; `import moy_lua` is meant to
# fail. It exists to hand a lua_State to native/moycore, whose build fragment
# includes lua/ from here.

add_library(usermod_moy_lua INTERFACE)

file(GLOB MOY_LUA_VM_SRCS "${CMAKE_CURRENT_LIST_DIR}/lua/*.c")
# The cart sandbox opens base/math/string/table ONLY (libmoy's moy_lua_open),
# so the
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

target_sources(usermod_moy_lua INTERFACE ${MOY_LUA_VM_SRCS})

target_include_directories(usermod_moy_lua INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/lua
)

target_link_libraries(usermod INTERFACE usermod_moy_lua)
