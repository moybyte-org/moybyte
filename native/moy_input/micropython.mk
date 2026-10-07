# moy_input for the unix and webassembly ports: the twin of micropython.cmake.

MOY_INPUT_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_INPUT_DIR)/modmoy_input.c
SRC_USERMOD_LIB_C += $(MOY_INPUT_DIR)/moy_input.c
CFLAGS_USERMOD += -I$(MOY_INPUT_DIR) -I$(MOY_INPUT_DIR)/../moy_spine
