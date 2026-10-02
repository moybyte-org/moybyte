# moy_wasm_web: the browser's compiled-cart engine (README.md). A Makefile-port
# fragment and nothing else: the web runner is the one build that stages this
# directory (firmware/web_runner/build.sh), the boards' native scan takes only
# directories with a micropython.cmake, and the unix build names its modules.
#
# MOY_WASM_JS is the engine switch, and it is global on purpose: it is what
# compiles libmoy's wasm binding (moycore's libmoy/moy_wasm.c) for a
# JavaScript embedder and moycore's compiled-cart half against this engine,
# exactly as native/moy_wasm's cmake defines MOY_WASM for the boards. The
# session surface moycore includes (moy_wasm_session.h, native/moy_wasm's) is
# staged into this directory by the build.

MOY_WASM_WEB_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_WASM_WEB_DIR)/modmoy_wasm_web.c
SRC_USERMOD_LIB_C += $(MOY_WASM_WEB_DIR)/libmoy/embed.c

# embed.c includes moy_wasm.h by its bare name, from moycore's vendored libmoy.
CFLAGS_USERMOD += -I$(MOY_WASM_WEB_DIR) -I$(MOY_WASM_WEB_DIR)/../moycore/libmoy \
	-DMOY_WASM_JS=1
