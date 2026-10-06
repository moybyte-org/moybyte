# moy_spine for the unix and webassembly ports: the twin of micropython.cmake,
# reading the same MOY_SPINE_IMPL and MOY_INDEX_IMPL (environment or
# command-line variables). moy_htab.c is built when either takes a native twin.

MOY_SPINE_DIR := $(USERMOD_DIR)

ifneq ($(filter c,$(MOY_SPINE_IMPL) $(MOY_INDEX_IMPL)),)
SRC_USERMOD_LIB_C += $(MOY_SPINE_DIR)/moy_htab.c
CFLAGS_USERMOD += -I$(MOY_SPINE_DIR)
endif
ifeq ($(MOY_SPINE_IMPL),c)
SRC_USERMOD_C += $(MOY_SPINE_DIR)/modmoy_spine.c
SRC_USERMOD_LIB_C += $(MOY_SPINE_DIR)/moy_route.c $(MOY_SPINE_DIR)/moy_settings.c
else ifneq ($(filter-out py,$(MOY_SPINE_IMPL)),)
$(error MOY_SPINE_IMPL is py or c, not '$(MOY_SPINE_IMPL)')
endif
