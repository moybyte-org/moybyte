# moy_uitree for the unix port: the twin of micropython.cmake.
# The UI engine (moy_uitree.h): a page a build function describes, drawn
# from C through native/moy_ui's verbs.
MOY_UITREE_DIR := $(USERMOD_DIR)
SRC_USERMOD_C += $(MOY_UITREE_DIR)/modmoy_uitree.c
SRC_USERMOD_LIB_C += $(MOY_UITREE_DIR)/moy_uitree.c $(MOY_UITREE_DIR)/moy_uitree_lang.c \
    $(MOY_UITREE_DIR)/moy_uitree_walk.c $(MOY_UITREE_DIR)/moy_uitree_aot.c $(MOY_UITREE_DIR)/moy_uitree_run.c \
    $(MOY_UITREE_DIR)/moy_uitree_check.c \
    $(MOY_UITREE_DIR)/moy_uitree_wasm.c
# UIT_TERSE: a refusal carries its code and the names involved, not prose.
# MOYCORE_UITREE: a build that takes this engine opens its Lua face in
# moycore (native/moycore/moycore_uitree.h, beside MOYCORE_UI).
CFLAGS_USERMOD += -DUIT_TERSE=1 -DMOYCORE_UITREE=1 -I$(MOY_UITREE_DIR) -I$(MOY_UITREE_DIR)/../moy_ui -I$(MOY_UITREE_DIR)/../moy_gfx \
    -I$(MOY_UITREE_DIR)/../moy_gfx/libmoy -I$(MOY_UITREE_DIR)/../moy_glass -I$(MOY_UITREE_DIR)/../moy_spine
