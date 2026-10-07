# moy_net for the unix and webassembly ports: the twin of micropython.cmake.
# Its JSON scanner is native/moy_spine/moy_json.c, which moy_store compiles.

MOY_NET_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_NET_DIR)/modmoy_net.c
SRC_USERMOD_LIB_C += $(MOY_NET_DIR)/moy_http.c $(MOY_NET_DIR)/moy_sync.c $(MOY_NET_DIR)/moy_wifi.c $(MOY_NET_DIR)/moy_link.c
CFLAGS_USERMOD += -I$(MOY_NET_DIR) -I$(MOY_NET_DIR)/../moy_spine
