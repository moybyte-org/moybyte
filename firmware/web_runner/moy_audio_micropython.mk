# moy_audio usermod fragment for the MicroPython webassembly build (#170 --
# the web-player "slowdown" fix): the SAME native mixer the T-Deck freezes,
# Makefile-port twin of native/moy_audio/micropython.cmake. Without
# ESP_IDF_VERSION the IDF half (I2S + the core-1 task) compiles out and only
# the per-block kernel (voice_set / render / voice_read) remains -- which is
# exactly what the runner needs: the Python per-sample loop costs whole
# milliseconds per frame under wasm, the C kernel is noise. build.sh stages
# native/moy_audio/ into .build/usermods/moy_audio/ and drops this file in as
# micropython.mk.

MOY_AUDIO_MOD_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_AUDIO_MOD_DIR)/modmoy_audio.c

CFLAGS_USERMOD += -I$(MOY_AUDIO_MOD_DIR)
