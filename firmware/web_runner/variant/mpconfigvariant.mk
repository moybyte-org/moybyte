# Moybyte web runner variant: pyscript-shaped (growable memory, NO -s ASYNCIFY --
# see mpconfigvariant.h: split-heap-auto gc needs no register scan).
JSFLAGS += -s ALLOW_MEMORY_GROWTH
# A bounded maximum: the console boots in under 25 MB of wasm memory (the
# desk, 16 MB of it the gc heap) and grows inside it, so 512 MB is headroom no
# cart reaches; emcc's default is 2 GB, a limit an engine that reserves the
# maximum up front -- a phone's -- may refuse to instantiate at all.
JSFLAGS += -s MAXIMUM_MEMORY=512MB

FROZEN_MANIFEST ?= $(VARIANT_DIR)/manifest.py

# The code no frame runs is built for size (-Oz) and the rest stays -Os: the
# store's verbs, the theme parser, and the Python and Lua compilers run at a
# save, a scan, a theme change or a cart's load. -Oz over the whole VM measured
# its carts' frames slower in node; on these files it measured none, and the
# bytes ride every board's image (the Zero's headroom is the tightest).
# $(BUILD) is set before this file is read.
$(BUILD)/moy_store/%.o: CFLAGS += -Oz
$(BUILD)/moy_ui/moy_theme.o: CFLAGS += -Oz
$(BUILD)/moy_lua/lua/lparser.o $(BUILD)/moy_lua/lua/lcode.o $(BUILD)/moy_lua/lua/llex.o \
$(BUILD)/moy_lua/lua/ldump.o $(BUILD)/moy_lua/lua/lundump.o: CFLAGS += -Oz
$(BUILD)/py/compile.o $(BUILD)/py/emitbc.o $(BUILD)/py/parse.o $(BUILD)/py/lexer.o \
$(BUILD)/py/scope.o $(BUILD)/py/persistentcode.o $(BUILD)/py/builtinimport.o: CFLAGS += -Oz
