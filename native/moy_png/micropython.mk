# moy_png for the unix and webassembly ports: the twin of micropython.cmake.

MOY_PNG_MOD_DIR := $(USERMOD_DIR)

SRC_USERMOD += $(MOY_PNG_MOD_DIR)/modmoy_png.c
SRC_USERMOD += $(MOY_PNG_MOD_DIR)/moy_png.c

CFLAGS_USERMOD += -I$(MOY_PNG_MOD_DIR)
