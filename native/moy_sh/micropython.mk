# moy_sh for the webassembly port: the twin of micropython.cmake, in one of
# two shapes, chosen by firmware/web_runner/build.sh.
#
# MOY_WEB_SHELL=1 (`build.sh --site`, the project site's console): the shell
# itself -- the session, dash, the C built-ins and the text apps -- under the
# Terminal's face, over the page's own store. The host table is
# moy_sh_board.c built without MOY_SH_ON_BOARD, as the CPython host builds it
# (runtime/host/moy_sh.py). dash's sources take `-include dash_config.h` and
# dash's own include path, by target, so the shim and dash's generic header
# names reach no other file.
#
# Otherwise (the bundle every board bakes): the Terminal's face and grid with
# no shell behind them, a face of the serving board's (moy_sh_link.h), so the
# boards' images carry no shell of the browser's.

MOY_SH_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_SH_DIR)/modmoy_sh.c
CFLAGS_USERMOD += -I$(MOY_SH_DIR)

ifeq ($(MOY_WEB_SHELL),1)
MOY_SH_DASH_SRC := $(wildcard $(MOY_SH_DIR)/dash/*.c) \
    $(MOY_SH_DIR)/dash/bltin/printf.c $(MOY_SH_DIR)/dash/bltin/test.c \
    $(MOY_SH_DIR)/moy_sh.c $(MOY_SH_DIR)/moy_sh_sys.c $(MOY_SH_DIR)/moy_sh_cmds.c

SRC_USERMOD_LIB_C += $(MOY_SH_DASH_SRC) \
    $(MOY_SH_DIR)/moy_sh_arena.c $(MOY_SH_DIR)/moy_sh_fmt.c $(MOY_SH_DIR)/moy_sh_cands.c \
    $(MOY_SH_DIR)/moy_sh_board.c $(MOY_SH_DIR)/moy_sh_screen.c $(MOY_SH_DIR)/moy_sh_job.c \
    $(MOY_SH_DIR)/moy_sh_store.c \
    $(MOY_SH_DIR)/moy_term.c $(MOY_SH_DIR)/moy_sh_app.c $(MOY_SH_DIR)/moy_sh_edit.c \
    $(MOY_SH_DIR)/moy_sh_top.c $(MOY_SH_DIR)/moy_sh_face.c

MOY_SH_SHIM := -include $(MOY_SH_DIR)/dash_config.h -I$(MOY_SH_DIR)/dash
$(BUILD)/moy_sh/dash/%.o $(BUILD)/moy_sh/dash/bltin/%.o: CFLAGS += $(MOY_SH_SHIM)
$(BUILD)/moy_sh/moy_sh.o $(BUILD)/moy_sh/moy_sh_sys.o $(BUILD)/moy_sh/moy_sh_cmds.o: CFLAGS += $(MOY_SH_SHIM)
# a run holds the worker: stopped past its budget (moy_sh_screen.c)
CFLAGS_USERMOD += -DMOY_SH_RUN_BUDGET_S=3
else
SRC_USERMOD_LIB_C += $(MOY_SH_DIR)/moy_term.c $(MOY_SH_DIR)/moy_sh_app.c \
    $(MOY_SH_DIR)/moy_sh_face.c $(MOY_SH_DIR)/moy_sh_link.c
CFLAGS_USERMOD += -DMOY_SH_LINK=1
endif

# typed at, never on a frame's path; dash is vendored as upstream writes it:
# its warnings are not this tree's
$(BUILD)/moy_sh/%.o: CFLAGS += -Oz -w -std=gnu99
