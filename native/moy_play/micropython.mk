# moy_play for the unix and webassembly ports: the module `moy_play` and the
# tick model it carries (micropython.cmake has the board's list).

MOY_PLAY_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_PLAY_DIR)/modmoy_play.c
SRC_USERMOD_LIB_C += $(MOY_PLAY_DIR)/moy_tick.c $(MOY_PLAY_DIR)/moy_rt.c \
    $(MOY_PLAY_DIR)/moy_play_rule.c $(MOY_PLAY_DIR)/moy_play.c
CFLAGS_USERMOD += -I$(MOY_PLAY_DIR) -I$(MOY_PLAY_DIR)/../moy_store -I$(MOY_PLAY_DIR)/../moy_spine \
    -I$(MOY_PLAY_DIR)/../moycore -I$(MOY_PLAY_DIR)/../moy_input -I$(MOY_PLAY_DIR)/../moy_kernel \
    -I$(MOY_PLAY_DIR)/../moy_glass
