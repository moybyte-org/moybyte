# Moybyte web runner variant: pyscript-shaped (growable memory, NO -s ASYNCIFY --
# see mpconfigvariant.h: split-heap-auto gc needs no register scan).
JSFLAGS += -s ALLOW_MEMORY_GROWTH

FROZEN_MANIFEST ?= $(VARIANT_DIR)/manifest.py
