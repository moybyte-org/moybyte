# moy_glass for the unix and webassembly ports: the twin of micropython.cmake.
# Its handle table is native/moy_spine's moy_htab.c, which that module builds.

MOY_GLASS_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_GLASS_DIR)/modmoy_glass.c
SRC_USERMOD_LIB_C += $(MOY_GLASS_DIR)/moy_glass.c
CFLAGS_USERMOD += -I$(MOY_GLASS_DIR) -I$(MOY_GLASS_DIR)/../moy_spine -DMOY_GLASS=1
