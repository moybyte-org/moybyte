# Makefile-port glue for moy_audio -- the twin of micropython.cmake, used by the
# ports that build with make rather than cmake:
#
#   * ports/unix -- how the binding is TESTED without hardware. The module is
#     VM-neutral C (py/obj.h + py/runtime.h), so the same code that drives the
#     T-Deck's I2S runs under the desktop VM and can be compared sample by sample
#     against libmoy itself (tests/test_audio_parity.py, the moy_gfx bench
#     precedent in tools/bench_unix_mp.py).
#   * the webassembly runner -- firmware/web_runner/build.sh stages this
#     directory into .build/usermods/moy_audio and this fragment is what it
#     builds. Without ESP_IDF_VERSION the I2S half and the core-1 task compile
#     out and only the synth + the render entry remain, which is all the runner
#     needs: it pulls finished PCM per frame and the page plays it.
#
# libmoy/moy_audio.c is SPEC.md 8 itself (see libmoy/UPSTREAM.md) -- it is
# compiled in, not reimplemented, which is the whole point of the directory.

MOY_AUDIO_MOD_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_AUDIO_MOD_DIR)/modmoy_audio.c
SRC_USERMOD_C += $(MOY_AUDIO_MOD_DIR)/libmoy/moy_audio.c

CFLAGS_USERMOD += -I$(MOY_AUDIO_MOD_DIR) -I$(MOY_AUDIO_MOD_DIR)/libmoy
