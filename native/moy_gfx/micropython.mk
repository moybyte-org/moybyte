# Unix-port user-module glue (#63): moy_gfx is VM-neutral C (py/obj.h + py/runtime.h
# only), so it also builds into ports/unix -- the board-model MicroPython the
# host suites run (`make unix-micropython`), the REAL C kernel and allocator
# without ESP32 hardware.
#
# This must stay the twin of micropython.cmake: a source the cmake side lists
# and this side does not (libmoy's, which modmoy_gfx.c calls into for moy_tri,
# moy_spr, moy_map_draw and friends) leaves the unix port unlinkable.
#
# libmoy_kernels.c is the shim that pulls libmoy's three .c files into one
# translation unit at -O3 (its header says why the pragma cannot live in the
# vendored copies). Listing the vendored sources here as well would define every
# symbol twice.
#
# moy_gfx_kernels.c is the COMPOSITOR and the libmoy bridge -- what this
# module and the host's runtime/host/gfx_binding.py both run, one copy. It carries its
# own -O3 pragma for the same reason libmoy_kernels.c does. moy_text.c is the
# one UI text path (its header), over that compositor's rasterizer.
# moy_backdrop.c rasterises a theme's backdrop (its header); moy_shape.c the
# 2026-10 look's shapes and shadows (its header); moy_path.c filled paths and the
# outline faces at any size (its header); moy_scr.c a whole screen with no
# canvas: the boot splash and the kernel's screens (its header); moy_gfx_base.c
# the wire order they all write in (its header).

MOY_GFX_MOD_DIR := $(USERMOD_DIR)

SRC_USERMOD += $(MOY_GFX_MOD_DIR)/modmoy_gfx.c
SRC_USERMOD += $(MOY_GFX_MOD_DIR)/moy_gfx_kernels.c
SRC_USERMOD += $(MOY_GFX_MOD_DIR)/moy_gfx_base.c
SRC_USERMOD += $(MOY_GFX_MOD_DIR)/moy_text.c
SRC_USERMOD += $(MOY_GFX_MOD_DIR)/moy_backdrop.c
SRC_USERMOD += $(MOY_GFX_MOD_DIR)/moy_shape.c
SRC_USERMOD += $(MOY_GFX_MOD_DIR)/moy_path.c
SRC_USERMOD += $(MOY_GFX_MOD_DIR)/moy_scr.c
SRC_USERMOD += $(MOY_GFX_MOD_DIR)/libmoy_kernels.c

# MOY_PIXEL_RGB565 changes sizeof(moy_pixel) and therefore the layout of
# moy_canvas, so it must be identical for every translation unit that sees
# moy.h. A mismatch links cleanly and corrupts memory.
CFLAGS_USERMOD += -I$(MOY_GFX_MOD_DIR) -I$(MOY_GFX_MOD_DIR)/libmoy -DMOY_PIXEL_RGB565=1
