# Moybyte web runner variant: pyscript-shaped (growable memory, NO -s ASYNCIFY --
# see mpconfigvariant.h: split-heap-auto gc needs no register scan).
JSFLAGS += -s ALLOW_MEMORY_GROWTH
# A bounded maximum: the console boots in under 25 MB of wasm memory (the
# desk, 16 MB of it the gc heap) and grows inside it, so 512 MB is headroom no
# cart reaches; emcc's default is 2 GB, a limit an engine that reserves the
# maximum up front -- a phone's -- may refuse to instantiate at all.
JSFLAGS += -s MAXIMUM_MEMORY=512MB

FROZEN_MANIFEST ?= $(VARIANT_DIR)/manifest.py
