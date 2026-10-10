# moy_spine for the unix and webassembly ports: the twin of micropython.cmake.
# The spine is native on every image (docs/kernel_spine_2026-10.md); its
# handle table, moy_htab.c, is the one moy_index takes its slots from too.

MOY_SPINE_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_SPINE_DIR)/modmoy_spine.c
SRC_USERMOD_LIB_C += $(MOY_SPINE_DIR)/moy_htab.c $(MOY_SPINE_DIR)/moy_route.c \
	$(MOY_SPINE_DIR)/moy_ledger.c $(MOY_SPINE_DIR)/moy_settings.c
CFLAGS_USERMOD += -I$(MOY_SPINE_DIR)
