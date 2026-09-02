# Unix-port user-module glue: the vendored Lua 5.4 VM, and only that.
#
# The MicroPython BRIDGE that used to live here (modmoy_lua.c) is gone --
# LuaCartRun's ~40 registered trampolines, the libmoy-direct draw family, the
# batch protocol, the p8 map walk. moycore binds the same VM through libmoy's
# own binding, so there is one Lua runtime and this target exports no module:
# `import moy_lua` is meant to fail. What it still does is compile the VM that
# native/moycore's fragment includes and links against.
#
# It keeps building into ports/unix for the same reason it always did -- that
# is what lets tests/test_moycore_loop.py and tests/test_semantic_traces.py
# drive the REAL VM against the REAL kernels on a desktop, with no board
# attached. Point USER_C_MODULES at this tree's parent (native/) so moy_gfx and
# moycore sit beside it.
#
# Makefile-port twin of micropython.cmake (the boards) and of the web runner's
# moy_lua_micropython.mk (emscripten -- kept separate because it must silence
# clang's ignored-pragma warning and this one must not assume clang). Same
# source set everywhere: the sandbox opens base/math/string/table only, so the
# unused stdlibs -- and linit.c, whose luaL_openlibs references all of them --
# stay out entirely.

MOY_LUA_MOD_DIR := $(USERMOD_DIR)

MOY_LUA_VM_SRCS := $(filter-out \
	$(MOY_LUA_MOD_DIR)/lua/linit.c \
	$(MOY_LUA_MOD_DIR)/lua/liolib.c \
	$(MOY_LUA_MOD_DIR)/lua/loslib.c \
	$(MOY_LUA_MOD_DIR)/lua/loadlib.c \
	$(MOY_LUA_MOD_DIR)/lua/ldblib.c \
	$(MOY_LUA_MOD_DIR)/lua/lutf8lib.c \
	, $(wildcard $(MOY_LUA_MOD_DIR)/lua/*.c))
SRC_USERMOD_LIB_C += $(MOY_LUA_VM_SRCS)

CFLAGS_USERMOD += -I$(MOY_LUA_MOD_DIR) -I$(MOY_LUA_MOD_DIR)/lua
