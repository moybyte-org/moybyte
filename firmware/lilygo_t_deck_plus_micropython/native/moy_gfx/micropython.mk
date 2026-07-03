# Unix-port user-module glue (#63): moy_gfx is VM-neutral C (py/obj.h + py/runtime.h
# only), so it also builds into ports/unix -- used by the host-side MicroPython
# bench that runs the REAL C kernel + REAL allocator without ESP32 hardware.
MOY_GFX_MOD_DIR := $(USERMOD_DIR)
SRC_USERMOD += $(MOY_GFX_MOD_DIR)/modmoy_gfx.c
