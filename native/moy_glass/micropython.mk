# moy_glass for the unix and webassembly ports: the twin of micropython.cmake.
# Its handle table is native/moy_spine's moy_htab.c, which that module builds.
# A port that sets MOY_GLASS_PRESENT=0 (the browser's variant) builds no
# compositor (modmoy_glass.c's header).

MOY_GLASS_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_GLASS_DIR)/modmoy_glass.c
SRC_USERMOD_LIB_C += $(MOY_GLASS_DIR)/moy_glass.c
ifeq ($(MOY_GLASS_PRESENT),0)
CFLAGS_USERMOD += -DMOY_GLASS_PRESENT=0
else
SRC_USERMOD_LIB_C += $(MOY_GLASS_DIR)/moy_present_banded.c $(MOY_GLASS_DIR)/moy_present_dsi.c $(MOY_GLASS_DIR)/moy_present_rot.c
endif
CFLAGS_USERMOD += -I$(MOY_GLASS_DIR) -I$(MOY_GLASS_DIR)/../moy_spine -DMOY_GLASS=1
