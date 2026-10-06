# moy_index for the unix and webassembly ports: the twin of micropython.cmake,
# reading the same MOY_INDEX_IMPL (an environment or command-line variable).

MOY_INDEX_DIR := $(USERMOD_DIR)

ifeq ($(MOY_INDEX_IMPL),c)
SRC_USERMOD_C += $(MOY_INDEX_DIR)/modmoy_index.c
SRC_USERMOD_LIB_C += $(MOY_INDEX_DIR)/moy_index.c
CFLAGS_USERMOD += -I$(MOY_INDEX_DIR)
ifeq ($(MOY_INDEX_BENCH),1)
SRC_USERMOD_C += $(MOY_INDEX_DIR)/bench_moy_index.c
endif
else ifneq ($(filter-out py,$(MOY_INDEX_IMPL)),)
$(error MOY_INDEX_IMPL is py or c, not '$(MOY_INDEX_IMPL)')
endif
