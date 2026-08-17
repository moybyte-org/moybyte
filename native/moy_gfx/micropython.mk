# Unix-port user-module glue (#63): moy_gfx is VM-neutral C (py/obj.h + py/runtime.h
# only), so it also builds into ports/unix -- used by the host-side MicroPython
# bench that runs the REAL C kernel + REAL allocator without ESP32 hardware.
#
# This must stay the twin of micropython.cmake. When six verbs became CALLS into
# vendored libmoy (#97), the cmake side gained those sources and this side did
# not, so the unix port stopped linking -- modmoy_gfx.c references moy_tri,
# moy_spr, moy_map_draw and friends with nothing defining them. Nothing caught
# it: the firmware tests grep frozen source rather than compile it, and
# bench_unix_mp.py is run by hand.
#
# libmoy_kernels.c is the shim that pulls libmoy's three .c files into one
# translation unit at -O3 (its header says why the pragma cannot live in the
# vendored copies). Listing the vendored sources here as well would define every
# symbol twice.
#
# moy_gfx_kernels.c is the COMPOSITOR -- the loops this module and
# runtime/moyhost_gfx.c both run, extracted so there is one copy. It carries its
# own -O3 pragma for the same reason libmoy_kernels.c does.

MOY_GFX_MOD_DIR := $(USERMOD_DIR)

SRC_USERMOD += $(MOY_GFX_MOD_DIR)/modmoy_gfx.c
SRC_USERMOD += $(MOY_GFX_MOD_DIR)/moy_gfx_kernels.c
SRC_USERMOD += $(MOY_GFX_MOD_DIR)/libmoy_kernels.c

# MOY_PIXEL_RGB565 changes sizeof(moy_pixel) and therefore the layout of
# moy_canvas, so it must be identical for every translation unit that sees
# moy.h. A mismatch links cleanly and corrupts memory.
CFLAGS_USERMOD += -I$(MOY_GFX_MOD_DIR) -I$(MOY_GFX_MOD_DIR)/libmoy -DMOY_PIXEL_RGB565=1
