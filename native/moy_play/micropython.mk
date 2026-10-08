# moy_play for the unix and webassembly ports: the module `moy_play` and the
# tick model it carries (micropython.cmake has the board's list).

MOY_PLAY_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_PLAY_DIR)/modmoy_play.c
SRC_USERMOD_LIB_C += $(MOY_PLAY_DIR)/moy_tick.c
CFLAGS_USERMOD += -I$(MOY_PLAY_DIR)
