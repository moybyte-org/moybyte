# moy_input for the unix and webassembly ports: the twin of micropython.cmake.

MOY_INPUT_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_INPUT_DIR)/modmoy_input.c
SRC_USERMOD_LIB_C += $(MOY_INPUT_DIR)/moy_input.c $(MOY_INPUT_DIR)/moy_touch.c $(MOY_INPUT_DIR)/moy_kbd.c $(MOY_INPUT_DIR)/moy_touchdev.c $(MOY_INPUT_DIR)/moy_input_task.c $(MOY_INPUT_DIR)/moy_hid.c $(MOY_INPUT_DIR)/moy_ble_task.c
CFLAGS_USERMOD += -I$(MOY_INPUT_DIR) -I$(MOY_INPUT_DIR)/../moy_spine
