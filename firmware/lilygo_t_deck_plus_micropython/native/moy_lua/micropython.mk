# Unix-port user-module glue (#189): moy_lua is plain C (vendored Lua 5.4 +
# the MP bridge), so like moy_gfx it also builds into ports/unix -- which is
# what lets tests/test_lua_draw_direct.py drive the REAL VM + the REAL
# libmoy-direct draw verbs against the REAL moy_gfx kernels on a desktop,
# byte-diffing buffers with no board attached. Point USER_C_MODULES at this
# tree's parent (native/) so moy_gfx sits beside this module and
# modmoy_lua.c's "../moy_gfx/moy_gfx_capi.h" probe finds it; the test prints
# the exact make line.
#
# This is the Makefile-port twin of micropython.cmake (the boards) and of the
# web runner's moy_lua_micropython.mk (emscripten -- kept separate because it
# must silence clang's ignored-pragma warning and this one must not assume
# clang). Same source set everywhere: the sandbox opens base/math/string/table
# only, so the unused stdlibs -- and linit.c, whose luaL_openlibs references
# all of them -- stay out entirely.

MOY_LUA_MOD_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_LUA_MOD_DIR)/modmoy_lua.c

MOY_LUA_VM_SRCS := $(filter-out \
	$(MOY_LUA_MOD_DIR)/lua/linit.c \
	$(MOY_LUA_MOD_DIR)/lua/liolib.c \
	$(MOY_LUA_MOD_DIR)/lua/loslib.c \
	$(MOY_LUA_MOD_DIR)/lua/loadlib.c \
	$(MOY_LUA_MOD_DIR)/lua/ldblib.c \
	$(MOY_LUA_MOD_DIR)/lua/lcorolib.c \
	$(MOY_LUA_MOD_DIR)/lua/lutf8lib.c \
	, $(wildcard $(MOY_LUA_MOD_DIR)/lua/*.c))
SRC_USERMOD_LIB_C += $(MOY_LUA_VM_SRCS)

CFLAGS_USERMOD += -I$(MOY_LUA_MOD_DIR) -I$(MOY_LUA_MOD_DIR)/lua
