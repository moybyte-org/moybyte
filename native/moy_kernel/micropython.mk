# moy_kernel for the unix and webassembly ports: the portable half only -- the
# frame loop, the idle ladder, PERF, the dev channel's reader and words, and
# the trace tier the host drives them with (the module `moy_loop`), and the
# VM-side stages a staged console's frames run through (moy_loop_board.c). The entry,
# the crash record, the floor and the bus are the esp32 port's
# (micropython.cmake).

MOY_KERNEL_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_KERNEL_DIR)/modmoy_loop.c
SRC_USERMOD_LIB_C += $(MOY_KERNEL_DIR)/moy_loop.c $(MOY_KERNEL_DIR)/moy_idle.c \
    $(MOY_KERNEL_DIR)/moy_perf.c $(MOY_KERNEL_DIR)/moy_devch.c \
    $(MOY_KERNEL_DIR)/moy_loop_host.c $(MOY_KERNEL_DIR)/moy_loop_board.c
CFLAGS_USERMOD += -I$(MOY_KERNEL_DIR)
