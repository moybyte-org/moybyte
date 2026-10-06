# moy_store for the unix and webassembly ports: the twin of micropython.cmake.

MOY_STORE_DIR := $(USERMOD_DIR)
SRC_USERMOD_C += $(MOY_STORE_DIR)/modmoy_store.c $(MOY_STORE_DIR)/moy_card.c
SRC_USERMOD_LIB_C += $(MOY_STORE_DIR)/moy_vol.c $(MOY_STORE_DIR)/moy_fs.c \
    $(MOY_STORE_DIR)/moy_cat.c $(MOY_STORE_DIR)/moy_seed.c $(MOY_STORE_DIR)/moy_journal.c \
    $(MOY_STORE_DIR)/moy_pack.c \
    $(MOY_STORE_DIR)/../moy_spine/moy_json.c
CFLAGS_USERMOD += -I$(MOY_STORE_DIR) -I$(MOY_STORE_DIR)/../moy_spine -DMOY_STORE_MICROPYTHON=1
