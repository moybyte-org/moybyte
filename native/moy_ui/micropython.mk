# moy_ui for the unix and webassembly ports: the twin of micropython.cmake.
# The widget toolkit's bodies in C with style and strings as data (README.md).

MOY_UI_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_UI_DIR)/modmoy_ui.c
SRC_USERMOD_LIB_C += $(MOY_UI_DIR)/moy_ui.c $(MOY_UI_DIR)/moy_ui_glyph.c $(MOY_UI_DIR)/moy_ui_draw.c $(MOY_UI_DIR)/moy_ui_paint.c $(MOY_UI_DIR)/moy_ui_depth.c \
    $(MOY_UI_DIR)/moy_ui_interact.c $(MOY_UI_DIR)/moy_ui_sink.c $(MOY_UI_DIR)/moy_ui_api.c $(MOY_UI_DIR)/moy_ui_wasm.c \
    $(MOY_UI_DIR)/moy_ui_tables.c $(MOY_UI_DIR)/moy_theme.c
CFLAGS_USERMOD += -I$(MOY_UI_DIR) -I$(MOY_UI_DIR)/../moy_gfx -I$(MOY_UI_DIR)/../moy_gfx/libmoy \
    -I$(MOY_UI_DIR)/../moy_glass \
    -I$(MOY_UI_DIR)/../moy_spine
