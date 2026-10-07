# Makefile-port glue for moy_audio -- the twin of micropython.cmake, for the
# ports that build with make:
#
#   * ports/unix -- how the binding is TESTED without hardware: the same C runs
#     under the desktop VM and is compared sample by sample against libmoy
#     (tests/test_audio_parity.py) and traced (tests/test_semantic_traces.py).
#   * the webassembly runner -- firmware/web_runner/build.sh stages this
#     directory and its siblings. Without a board's headers moy_aud_out.c has no
#     speaker: the page pulls render() once a frame.

MOY_AUDIO_MOD_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_AUDIO_MOD_DIR)/modmoy_audio.c
SRC_USERMOD_LIB_C += $(MOY_AUDIO_MOD_DIR)/moy_aud.c $(MOY_AUDIO_MOD_DIR)/moy_aud_out.c
SRC_USERMOD_C += $(MOY_AUDIO_MOD_DIR)/libmoy/moy_audio.c

CFLAGS_USERMOD += -I$(MOY_AUDIO_MOD_DIR) -I$(MOY_AUDIO_MOD_DIR)/libmoy -I$(MOY_AUDIO_MOD_DIR)/../moy_spine

# MOY_AUDIO_SND points moycore's compiled tier at this module's stream
# (moy_audio_snd.h): in the web runner a compiled cart's `snd` is mixed into
# what render() hands the page. The unix build carries no wasm engine.
CFLAGS_USERMOD += -DMOY_AUDIO_SND=1
