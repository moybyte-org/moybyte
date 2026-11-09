# moy_media for the webassembly port: the twin of micropython.cmake, for the
# browser's console (firmware/web_runner/build.sh stages it). moy_audio's mix
# is in the same image, so open() hands it the pull (MOY_MEDIA_MIX); a port
# with no HTTP client (MOY_NET_SERVE=0, the browser's variant) has its face
# feed a URL's bytes, and a song the page holds, from the page's fetch
# (MOY_MEDIA_CLIENT=0). A file in the VFS is read through moy_store's volume
# seam on a job of moy_kernel's frame, as on a board.

MOY_MEDIA_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_MEDIA_DIR)/modmoy_media.c
SRC_USERMOD_LIB_C += $(MOY_MEDIA_DIR)/moy_media.c $(MOY_MEDIA_DIR)/moy_mp3.c

CFLAGS_USERMOD += -I$(MOY_MEDIA_DIR) -I$(MOY_MEDIA_DIR)/../moy_audio -I$(MOY_MEDIA_DIR)/../moy_play \
    -I$(MOY_MEDIA_DIR)/../moy_store -I$(MOY_MEDIA_DIR)/../moy_kernel -DMOY_MEDIA_MIX=1
ifeq ($(MOY_NET_SERVE),0)
CFLAGS_USERMOD += -DMOY_MEDIA_CLIENT=0
else
CFLAGS_USERMOD += -I$(MOY_MEDIA_DIR)/../moy_net
endif

# minimp3 is vendored as upstream writes it (UPSTREAM.md): its warnings are
# not this tree's
$(BUILD)/moy_media/moy_mp3.o: CFLAGS += -Wno-double-promotion
