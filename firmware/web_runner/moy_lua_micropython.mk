# The vendored Lua 5.4 VM for the MicroPython webassembly build (#151/#67) --
# the Makefile-port twin of native/moy_lua/micropython.cmake (the boards are
# cmake). build.sh stages native/moy_lua/ into .build/usermods/moy_lua/ and
# drops this file in as micropython.mk.
#
# VM only: the MicroPython bridge that used to sit beside it is gone, because
# moycore binds the same VM through libmoy's own binding. `import moy_lua` is
# meant to fail; what this compiles is what native/moycore links against.
#
# Same source set as the cmake: the sandbox opens base/math/string/table only,
# so the unused stdlibs -- and linit.c, whose luaL_openlibs references all of
# them -- stay out entirely.
#
# The vendored sources carry in-source `#pragma GCC optimize("O2")` pins (a
# board-toolchain guard, see the moy_lua module notes); clang/emscripten
# ignores them with a warning, which -Werror would fatal -- silence just that.
# Here the VM compiles at the port's -Os like everything else.

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

CFLAGS_USERMOD += -I$(MOY_LUA_MOD_DIR) -I$(MOY_LUA_MOD_DIR)/lua \
	-Wno-ignored-pragma-optimize -Wno-unknown-pragmas
