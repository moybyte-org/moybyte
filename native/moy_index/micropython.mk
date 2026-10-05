# moy_index for the unix and webassembly ports: the twin of micropython.cmake,
# reading the same MOY_INDEX_IMPL (an environment or command-line variable).

MOY_INDEX_DIR := $(USERMOD_DIR)

ifneq ($(filter c rust,$(MOY_INDEX_IMPL)),)
SRC_USERMOD_C += $(MOY_INDEX_DIR)/modmoy_index.c
CFLAGS_USERMOD += -I$(MOY_INDEX_DIR)
ifeq ($(MOY_INDEX_IMPL),c)
SRC_USERMOD_LIB_C += $(MOY_INDEX_DIR)/moy_index.c
else
ifeq ($(wildcard $(MOY_INDEX_RUST_LIB)),)
$(error MOY_INDEX_IMPL=rust links MOY_INDEX_RUST_LIB, a static library implementing moy_index.h; it is '$(MOY_INDEX_RUST_LIB)')
endif
LDFLAGS_USERMOD += $(MOY_INDEX_RUST_LIB)
endif
ifeq ($(MOY_INDEX_BENCH),1)
SRC_USERMOD_C += $(MOY_INDEX_DIR)/bench_moy_index.c
endif
else ifneq ($(filter-out py,$(MOY_INDEX_IMPL)),)
$(error MOY_INDEX_IMPL is py, c or rust, not '$(MOY_INDEX_IMPL)')
endif
