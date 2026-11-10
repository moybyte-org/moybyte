# moy_input for the unix and webassembly ports: the twin of micropython.cmake.
# A port that sets MOY_INPUT_DRIVERS=0 (the browser's variant) builds no
# driver: no keyboard, touch, trackball or BLE (modmoy_input.c's header).

MOY_INPUT_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_INPUT_DIR)/modmoy_input.c $(MOY_INPUT_DIR)/modmoy_gatt.c
SRC_USERMOD_LIB_C += $(MOY_INPUT_DIR)/moy_input.c $(MOY_INPUT_DIR)/moy_input_task.c $(MOY_INPUT_DIR)/moy_edge.c $(MOY_INPUT_DIR)/moy_hid.c $(MOY_INPUT_DIR)/moy_gatt.c $(MOY_INPUT_DIR)/moy_gatt_dev.c $(MOY_INPUT_DIR)/moy_scan.c
ifeq ($(MOY_INPUT_DRIVERS),0)
CFLAGS_USERMOD += -DMOY_INPUT_DRIVERS=0
else
SRC_USERMOD_C += $(MOY_INPUT_DIR)/modmoy_midi.c
SRC_USERMOD_LIB_C += $(MOY_INPUT_DIR)/moy_midi.c $(MOY_INPUT_DIR)/moy_midi_dev.c $(MOY_INPUT_DIR)/moy_touch.c $(MOY_INPUT_DIR)/moy_kbd.c $(MOY_INPUT_DIR)/moy_touchdev.c $(MOY_INPUT_DIR)/moy_ble_task.c
endif
CFLAGS_USERMOD += -I$(MOY_INPUT_DIR) -I$(MOY_INPUT_DIR)/../moy_spine
