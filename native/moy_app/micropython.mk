# moy_app for the unix and webassembly ports: the twin of micropython.cmake.
# The app ABI's kernel half (README.md); it reads native/moy_spine's tables
# and settings rows, so it builds beside it, and reads native/moy_input's
# pointer type. It also builds native/moy_store's user-files layer and its
# module `moy_ufiles` (moy_ufiles.h): the files role's server, which an image
# that denies this module (the Zero) carries none of. The layer reads covers
# with native/moy_png and writes pictures with the port's `deflate` compressor.

MOY_APP_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_APP_DIR)/modmoy_app.c $(MOY_APP_DIR)/../moy_store/modmoy_ufiles.c
SRC_USERMOD_LIB_C += $(MOY_APP_DIR)/moy_app.c $(MOY_APP_DIR)/moy_app_wasm.c $(MOY_APP_DIR)/../moy_store/moy_ufiles.c
CFLAGS_USERMOD += -I$(MOY_APP_DIR) -I$(MOY_APP_DIR)/../moy_spine -I$(MOY_APP_DIR)/../moy_input \
    -I$(MOY_APP_DIR)/../moy_store -I$(MOY_APP_DIR)/../moy_png
