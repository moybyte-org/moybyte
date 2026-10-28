# moy_rsprobe for the unix and webassembly ports: the twin of micropython.cmake.
ifndef MOY_RS_PROBE_LIB
$(error MOY_RS_PROBE_LIB names no Rust staticlib)
endif

SRC_USERMOD += $(USERMOD_DIR)/modmoy_rsprobe.c
LDFLAGS_USERMOD += $(MOY_RS_PROBE_LIB)
# Extra linker flags for diagnostics (a map file, --trace-symbol).
LDFLAGS_USERMOD += $(MOY_RS_PROBE_LDEXTRA)
