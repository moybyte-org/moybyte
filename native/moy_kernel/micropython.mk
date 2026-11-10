# moy_kernel for the unix and webassembly ports: the portable half only -- the
# frame loop, the idle ladder, PERF, the dev channel's reader and words, and
# the trace tier the host drives them with (the module `moy_loop`), the OS
# log (the module `moy_log`), the scheduler's callback guard (the module
# `moy_guard`, where the build defines MICROPY_ENABLE_VM_ABORT), the lease
# table with its host stand-in (the module `moy_lease`) and a compiled
# program's device handles over it (moy_dev.c, its buses' host twin
# moy_devbus.c), the writer lock moy_app's
# seqlock takes (moy_lock.c), the process table
# (moy_proc.c), the service model's queues and supervisor (the module
# `moy_evq`), a process's screen slot (moy_screen_slot.c), and the
# VM-side stages a staged console's frames run through (moy_loop_board.c). The entry,
# the crash record, the floor and the bus are the esp32 port's
# (micropython.cmake).

MOY_KERNEL_DIR := $(USERMOD_DIR)

SRC_USERMOD_C += $(MOY_KERNEL_DIR)/modmoy_loop.c $(MOY_KERNEL_DIR)/modmoy_log.c \
    $(MOY_KERNEL_DIR)/modmoy_guard.c $(MOY_KERNEL_DIR)/modmoy_lease.c \
    $(MOY_KERNEL_DIR)/modmoy_evq.c
SRC_USERMOD_LIB_C += $(MOY_KERNEL_DIR)/moy_loop.c $(MOY_KERNEL_DIR)/moy_idle.c \
    $(MOY_KERNEL_DIR)/moy_perf.c $(MOY_KERNEL_DIR)/moy_devch.c $(MOY_KERNEL_DIR)/moy_log.c \
    $(MOY_KERNEL_DIR)/moy_loop_host.c $(MOY_KERNEL_DIR)/moy_loop_board.c \
    $(MOY_KERNEL_DIR)/moy_lease.c $(MOY_KERNEL_DIR)/moy_dev.c $(MOY_KERNEL_DIR)/moy_devbus.c \
    $(MOY_KERNEL_DIR)/moy_ledc.c \
    $(MOY_KERNEL_DIR)/moy_lock.c $(MOY_KERNEL_DIR)/moy_proc.c \
    $(MOY_KERNEL_DIR)/moy_evq.c $(MOY_KERNEL_DIR)/moy_super.c $(MOY_KERNEL_DIR)/moy_vmq.c \
    $(MOY_KERNEL_DIR)/moy_screen_slot.c
CFLAGS_USERMOD += -I$(MOY_KERNEL_DIR)
