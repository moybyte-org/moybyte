# moy_net for the unix and webassembly ports: the twin of micropython.cmake.
# Its JSON scanner is native/moy_spine/moy_json.c and its store
# native/moy_store, both of which moy_store compiles. A port that sets
# MOY_NET_SERVE=0 (the browser's variant) builds the sync codec and apply
# alone, with moy_http.c's UTF-8 check they share (modmoy_net.c's header).

MOY_NET_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_NET_DIR)/modmoy_net.c
SRC_USERMOD_LIB_C += $(MOY_NET_DIR)/moy_http.c $(MOY_NET_DIR)/moy_sync.c $(MOY_NET_DIR)/moy_sync_apply.c
ifeq ($(MOY_NET_SERVE),0)
CFLAGS_USERMOD += -DMOY_NET_SERVE=0
else
SRC_USERMOD_LIB_C += $(MOY_NET_DIR)/moy_net_port.c $(MOY_NET_DIR)/moy_wifi.c $(MOY_NET_DIR)/moy_link.c \
    $(MOY_NET_DIR)/moy_webhost.c $(MOY_NET_DIR)/moy_ota.c $(MOY_NET_DIR)/moy_webconsole.c $(MOY_NET_DIR)/moy_gpio.c $(MOY_NET_DIR)/moy_dns.c
endif
CFLAGS_USERMOD += -I$(MOY_NET_DIR) -I$(MOY_NET_DIR)/../moy_spine -I$(MOY_NET_DIR)/../moy_store -I$(MOY_NET_DIR)/../moy_web
