# moycore (stage 2): the cart's whole frame in C. Makefile-port fragment; the
# boards use the cmake twin beside it, and the two must stay in step.
#
# This module compiles exactly TWO things: its own MicroPython binding
# (modmoycore.c) and libmoy's Lua binding (libmoy/moy_lua.c, vendored). It
# deliberately compiles neither the raster nor a Lua VM, because the binary
# already has one of each:
#
#   * the raster is moy_gfx's vendored libmoy, built by its libmoy_kernels.c.
#     A second compilation here would be a duplicate-symbol link error, not a
#     tidier vendoring -- so the include path points at that copy for moy.h and
#     the linker resolves moy_canvas/moy_sprite/moy_data from it. The vendor
#     manifest hashes both directories, so the header this compiles against and
#     the sources it links to cannot drift to different upstream versions.
#   * the VM is moy_lua's vendored Lua 5.4, for the same reason -- libmoy binds
#     to whatever lua_State it is handed and embeds none of its own.
#
# So moycore has a hard build-order dependency on both siblings being present.
# That is the same sibling shape moy_lua already relies on to reach moy_gfx's C
# API, and the same shape build.sh stages into ext_mod/ and .staged/.

MOYCORE_MOD_DIR := $(USERMOD_DIR)

SRC_USERMOD += $(MOYCORE_MOD_DIR)/modmoycore.c
SRC_USERMOD_LIB_C += $(MOYCORE_MOD_DIR)/libmoy_binding.c

# MOY_PIXEL_RGB565 changes sizeof(moy_pixel) and therefore the layout of
# moy_canvas, so it must be identical for every translation unit that sees
# moy.h -- moy_gfx sets the same define, and CFLAGS_USERMOD is global to the
# build, so the two agree by construction. MOY_WITH_LUA is what compiles
# libmoy's binding at all (moy.h guards it).
CFLAGS_USERMOD += -I$(MOYCORE_MOD_DIR) \
	-I$(MOYCORE_MOD_DIR)/../moy_gfx/libmoy \
	-I$(MOYCORE_MOD_DIR)/../moy_lua/lua \
	-DMOY_PIXEL_RGB565=1 -DMOY_WITH_LUA=1

# NB: libmoy/moy_lua.c is NOT listed above -- libmoy_binding.c includes it, so
# the two diagnostics a 32-bit Lua necessarily trips can be silenced by pragma
# in a file we own. Putting them here would not work: the ports append -Wall
# after py.mk folds CFLAGS_USERMOD in. See that file.
