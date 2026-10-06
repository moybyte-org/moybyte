# moy_store for the unix and webassembly ports: the twin of micropython.cmake.

MOY_STORE_DIR := $(USERMOD_DIR)
SRC_USERMOD_C += $(MOY_STORE_DIR)/modmoy_store.c
SRC_USERMOD_LIB_C += $(MOY_STORE_DIR)/moy_vol.c $(MOY_STORE_DIR)/moy_fs.c
CFLAGS_USERMOD += -I$(MOY_STORE_DIR) -DMOY_STORE_MICROPYTHON=1
