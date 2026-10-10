# moy_app for the unix and webassembly ports: the twin of micropython.cmake.
# The app ABI's kernel half (README.md); it reads native/moy_spine's tables
# and settings rows, so it builds beside it, and reads native/moy_input's
# pointer type.

MOY_APP_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_APP_DIR)/modmoy_app.c
SRC_USERMOD_LIB_C += $(MOY_APP_DIR)/moy_app.c
CFLAGS_USERMOD += -I$(MOY_APP_DIR) -I$(MOY_APP_DIR)/../moy_spine -I$(MOY_APP_DIR)/../moy_input
