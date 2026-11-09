/* moy_umode: the trap side of a native module's U-mode (moy_umode.h).
 *
 * The core's vectors are ours once a module has run (moy_u_ready): mtvt
 * points at u_mtvt, one stub per interrupt line, and mtvec at u_exc. A trap
 * from M-mode costs the stub's mode test and goes on to ESP-IDF's own handler
 * for that line, as if the table were IDF's. A trap from U-mode swaps to the
 * thread's kernel stack through mscratchcsw (mscratch holds the thread's
 * uctx while it is in U-mode), saves the U context there and then:
 *
 *   an interrupt goes on to IDF's handler as an interrupt of M-mode code at
 *     u_resume, on the kernel stack. A context switch saves and restores that
 *     M-mode context, so IDF's interrupt exit never sees U-mode and no task
 *     runs at another's privilege. u_resume returns to U-mode through
 *     moy_u_return (the process's end, the PMP windows, the stack guard);
 *   an ecall is a table row (moy_u_ecall), or ordinal -1, the module
 *     returning to moy_u_enter's caller;
 *   any other exception is moy_u_fault's: a window to load and a retry, an
 *     FPU instruction for IDF's lazy enable (_panic_handler's coprocessor
 *     path, which returns to u_resume), or the thread killed.
 *
 * The stubs' targets are vectors_clic.S's; moy_u_ready refuses to install
 * when IDF's _mtvt_table says otherwise (u_mtvt_want). */

#include "sdkconfig.h"

    .equ CSR_MSCRATCHCSW, 0x348
    .equ CSR_MTVT,        0x307
    .equ CSR_EXT_ILL,     0x7F0
    .equ MSTATUS_MIE,  0x8
    .equ MSTATUS_MPIE, 0x80
    .equ MSTATUS_MPP,  0x1800
    /* mcause's MINHV (bit 30) and MPIL (23:16): an mret into U-mode clears them */
    .equ MCAUSE_LEVEL, 0x40FF0000

    /* The U frame, below the uctx: word i is register xi (x2 the U-mode sp),
     * word 0 the pc; word 35 is the stub's scratch for t0. */
    .equ FRAME, 144
    /* The uctx (moy_umode.c's moy_uctx_t), at the kernel stack's top while U runs */
    .equ UC_KTP,   0
    .equ UC_SPACE, 4
    .equ UC_GLO,   8
    .equ UC_GHI,   12
    .equ UC_RA,    16
    .equ UC_SP,    20
    .equ UC_S0,    24
    .equ UCTX,     80
    /* moy_u_go_t */
    .equ GO_SPACE, 0
    .equ GO_ENTRY, 4
    .equ GO_RET,   8
    .equ GO_SP,    12
    .equ GO_A,     16
    .equ GO_GLO,   36
    .equ GO_GHI,   40

#if CONFIG_ESP_SYSTEM_MEMPROT_FEATURE
#define U_MEMPROT _panic_handler
#else
#define U_MEMPROT _interrupt_handler
#endif
#if CONFIG_ESP_SYSTEM_HW_STACK_GUARD
#define U_ASTDBG _panic_handler
#else
#define U_ASTDBG _interrupt_handler
#endif
#if CONFIG_ESP_IPC_ISR_ENABLE
#define U_IPC esp_ipc_isr_handler
#else
#define U_IPC _interrupt_handler
#endif

.macro save_u
    sw    x1, 4(sp)
    sw    x3, 12(sp)
    sw    x4, 16(sp)
    sw    x6, 24(sp)
    sw    x7, 28(sp)
    sw    x8, 32(sp)
    sw    x9, 36(sp)
    sw    x10, 40(sp)
    sw    x11, 44(sp)
    sw    x12, 48(sp)
    sw    x13, 52(sp)
    sw    x14, 56(sp)
    sw    x15, 60(sp)
    sw    x16, 64(sp)
    sw    x17, 68(sp)
    sw    x18, 72(sp)
    sw    x19, 76(sp)
    sw    x20, 80(sp)
    sw    x21, 84(sp)
    sw    x22, 88(sp)
    sw    x23, 92(sp)
    sw    x24, 96(sp)
    sw    x25, 100(sp)
    sw    x26, 104(sp)
    sw    x27, 108(sp)
    sw    x28, 112(sp)
    sw    x29, 116(sp)
    sw    x30, 120(sp)
    sw    x31, 124(sp)
    lw    t1, 140(sp)
    sw    t1, 20(sp)
    csrr  t1, mscratch
    sw    t1, 8(sp)
    csrr  t1, mepc
    sw    t1, 0(sp)
    /* the kernel's gp and the task's tp */
    .option push
    .option norelax
    la    gp, __global_pointer$
    .option pop
    lw    tp, FRAME+UC_KTP(sp)
.endm

    .section .iram1.moy_umode, "ax"
    .option push
    .option norvc

/* One stub a line: from M-mode straight on to IDF's handler, from U-mode to
 * u_irq_u with that handler in t0. */
.macro u_vec n, target
u_vec_\n:
    csrrw sp, CSR_MSCRATCHCSW, sp
    sw    t0, -4(sp)
    csrr  t0, mstatus
    srli  t0, t0, 11
    andi  t0, t0, 3
    beqz  t0, 1f
    lw    t0, -4(sp)
    j     \target
1:  la    t0, \target
    j     u_irq_u
.endm

    .balign 4
    u_vec 0, _panic_handler
    u_vec 1, _panic_handler
    u_vec 2, _panic_handler
    u_vec 3, _panic_handler
    u_vec 4, _panic_handler
    u_vec 5, _panic_handler
    u_vec 6, _panic_handler
    u_vec 7, _panic_handler
    u_vec 8, _panic_handler
    u_vec 9, _panic_handler
    u_vec 10, _panic_handler
    u_vec 11, _panic_handler
    u_vec 12, _panic_handler
    u_vec 13, _panic_handler
    u_vec 14, _panic_handler
    u_vec 15, _panic_handler
    u_vec 16, _interrupt_handler
    u_vec 17, _interrupt_handler
    u_vec 18, _interrupt_handler
    u_vec 19, _interrupt_handler
    u_vec 20, _interrupt_handler
    u_vec 21, _interrupt_handler
    u_vec 22, _interrupt_handler
    u_vec 23, _interrupt_handler
    u_vec 24, _interrupt_handler
    u_vec 25, _interrupt_handler
    u_vec 26, _interrupt_handler
    u_vec 27, _interrupt_handler
    u_vec 28, _interrupt_handler
    u_vec 29, _interrupt_handler
    u_vec 30, _interrupt_handler
    u_vec 31, _interrupt_handler
    u_vec 32, _interrupt_handler
    u_vec 33, _interrupt_handler
    u_vec 34, _interrupt_handler
    u_vec 35, _interrupt_handler
    u_vec 36, _interrupt_handler
    u_vec 37, _interrupt_handler
    u_vec 38, _interrupt_handler
    u_vec 39, _interrupt_handler
    u_vec 40, _panic_handler
    u_vec 41, _panic_handler
    u_vec 42, U_MEMPROT
    u_vec 43, U_ASTDBG
    u_vec 44, U_IPC
    u_vec 45, _interrupt_handler
    u_vec 46, _interrupt_handler
    u_vec 47, _interrupt_handler

/* An interrupt taken in U-mode: sp is the uctx, t0 IDF's handler. */
u_irq_u:
    addi  sp, sp, -FRAME
    save_u
    la    t1, u_resume
    csrw  mepc, t1
    li    t1, MSTATUS_MPP
    csrs  mstatus, t1
    jr    t0

/* Where IDF's handler returns: M-mode, interrupts on, sp the U frame. */
    .global u_resume
u_resume:
    csrci mstatus, MSTATUS_MIE
    mv    a0, sp
    call  moy_u_return
    /* fall through */

/* Into U-mode from the frame at sp. Interrupts are off. */
u_out:
    lw    t0, 0(sp)
    csrw  mepc, t0
    li    t0, MSTATUS_MPP
    csrc  mstatus, t0
    li    t0, MSTATUS_MPIE
    csrs  mstatus, t0
    li    t0, MCAUSE_LEVEL
    csrc  mcause, t0
    addi  t0, sp, FRAME
    csrw  mscratch, t0
    lw    x1, 4(sp)
    lw    x3, 12(sp)
    lw    x4, 16(sp)
    lw    x5, 20(sp)
    lw    x6, 24(sp)
    lw    x7, 28(sp)
    lw    x8, 32(sp)
    lw    x9, 36(sp)
    lw    x10, 40(sp)
    lw    x11, 44(sp)
    lw    x12, 48(sp)
    lw    x13, 52(sp)
    lw    x14, 56(sp)
    lw    x15, 60(sp)
    lw    x16, 64(sp)
    lw    x17, 68(sp)
    lw    x18, 72(sp)
    lw    x19, 76(sp)
    lw    x20, 80(sp)
    lw    x21, 84(sp)
    lw    x22, 88(sp)
    lw    x23, 92(sp)
    lw    x24, 96(sp)
    lw    x25, 100(sp)
    lw    x26, 104(sp)
    lw    x27, 108(sp)
    lw    x28, 112(sp)
    lw    x29, 116(sp)
    lw    x30, 120(sp)
    lw    x31, 124(sp)
    lw    sp, 8(sp)
    mret

/* An exception (mtvec's base: 64-aligned). */
    .balign 64
    .global u_exc
u_exc:
    csrrw sp, CSR_MSCRATCHCSW, sp
    sw    t0, -4(sp)
    csrr  t0, mstatus
    srli  t0, t0, 11
    andi  t0, t0, 3
    beqz  t0, u_exc_u
    lw    t0, -4(sp)
    j     _panic_handler

u_exc_u:
    addi  sp, sp, -FRAME
    save_u
    csrr  t1, mcause
    slli  t1, t1, 20
    srli  t1, t1, 20
    li    t2, 8
    bne   t1, t2, 2f
    lw    t1, 20(sp)
    li    t2, -1
    beq   t1, t2, u_leave
    li    a0, 1
    call  moy_u_close
    mv    a0, sp
    call  moy_u_ecall
    csrci mstatus, MSTATUS_MIE
    mv    a0, sp
    call  moy_u_return
    j     u_out
2:  mv    a0, sp
    csrr  a1, mcause
    csrr  a2, mtval
    csrr  a3, CSR_EXT_ILL
    call  moy_u_fault
    bnez  a0, 3f
    mv    a0, sp
    call  moy_u_return
    j     u_out
3:  la    t1, u_resume
    csrw  mepc, t1
    li    t1, MSTATUS_MPP
    csrs  mstatus, t1
    j     _panic_handler

/* The module's hook returned (the gate's ordinal -1): back to moy_u_enter's
 * caller with its a0. Interrupts are off. */
u_leave:
    li    a0, 0
    call  moy_u_close
    mv    a0, sp
    call  moy_u_kernel
    lw    a0, 40(sp)
    addi  t0, sp, FRAME
    lw    ra, UC_RA(t0)
    lw    s0, UC_S0+0(t0)
    lw    s1, UC_S0+4(t0)
    lw    s2, UC_S0+8(t0)
    lw    s3, UC_S0+12(t0)
    lw    s4, UC_S0+16(t0)
    lw    s5, UC_S0+20(t0)
    lw    s6, UC_S0+24(t0)
    lw    s7, UC_S0+28(t0)
    lw    s8, UC_S0+32(t0)
    lw    s9, UC_S0+36(t0)
    lw    s10, UC_S0+40(t0)
    lw    s11, UC_S0+44(t0)
    lw    sp, UC_SP(t0)
    csrsi mstatus, MSTATUS_MIE
    ret

/* void moy_u_close(int mie): ends the trap being handled with an mret to the
 * caller, in M-mode at level 0, MIE as asked. The core locks up on an
 * exception taken before the one being handled has had its mret, so kernel
 * code a trap runs (a row, a fault's ending) runs after this. */
    .global moy_u_close
moy_u_close:
    csrw  mepc, ra
    li    t0, MCAUSE_LEVEL
    csrc  mcause, t0
    li    t0, MSTATUS_MPP | MSTATUS_MPIE
    csrs  mstatus, t0
    bnez  a0, 1f
    li    t0, MSTATUS_MPIE
    csrc  mstatus, t0
1:  mret

/* void moy_u_bail(uint32_t *frame): the call whose U frame this is ends, its
 * moy_u_enter answering -1 (a fault in a call no thread owns). */
    .global moy_u_bail
moy_u_bail:
    csrci mstatus, MSTATUS_MIE
    mv    sp, a0
    li    t0, -1
    sw    t0, 40(sp)
    j     u_leave

/* int moy_u_enter(const moy_u_go_t *go): the module's code at go->entry in
 * U-mode on go->sp, returning when it returns to go->ret. sp drops past the
 * uctx and the frame before either is written: an interrupt taken here saves
 * IDF's context below sp, over anything kept there. */
    .global moy_u_enter
    .type moy_u_enter, @function
moy_u_enter:
    mv    t1, sp
    addi  sp, sp, -(UCTX + FRAME)
    addi  t0, sp, FRAME
    sw    ra, UC_RA(t0)
    sw    t1, UC_SP(t0)
    sw    s0, UC_S0+0(t0)
    sw    s1, UC_S0+4(t0)
    sw    s2, UC_S0+8(t0)
    sw    s3, UC_S0+12(t0)
    sw    s4, UC_S0+16(t0)
    sw    s5, UC_S0+20(t0)
    sw    s6, UC_S0+24(t0)
    sw    s7, UC_S0+28(t0)
    sw    s8, UC_S0+32(t0)
    sw    s9, UC_S0+36(t0)
    sw    s10, UC_S0+40(t0)
    sw    s11, UC_S0+44(t0)
    sw    tp, UC_KTP(t0)
    lw    t1, GO_SPACE(a0)
    sw    t1, UC_SPACE(t0)
    lw    t1, GO_GLO(a0)
    sw    t1, UC_GLO(t0)
    lw    t1, GO_GHI(a0)
    sw    t1, UC_GHI(t0)
    mv    t1, sp
    li    t2, 36
1:  sw    zero, 0(t1)
    addi  t1, t1, 4
    addi  t2, t2, -1
    bnez  t2, 1b
    lw    t1, GO_ENTRY(a0)
    sw    t1, 0(sp)
    lw    t1, GO_RET(a0)
    sw    t1, 4(sp)
    lw    t1, GO_SP(a0)
    sw    t1, 8(sp)
    lw    t1, GO_A+0(a0)
    sw    t1, 40(sp)
    lw    t1, GO_A+4(a0)
    sw    t1, 44(sp)
    lw    t1, GO_A+8(a0)
    sw    t1, 48(sp)
    lw    t1, GO_A+12(a0)
    sw    t1, 52(sp)
    lw    t1, GO_A+16(a0)
    sw    t1, 56(sp)
    csrci mstatus, MSTATUS_MIE
    mv    a0, sp
    call  moy_u_return
    j     u_out
    .size moy_u_enter, .-moy_u_enter

    .option pop

/* The table moy_u_ready installs (mtvt: 64-aligned, in internal RAM like
 * IDF's, since the core reads it on every interrupt, a flash operation's
 * included), and the handlers it expects IDF's own table to name. */
    .section .iram1.moy_umode_mtvt, "ax"
    .balign 64
    .global u_mtvt
u_mtvt:
    .irp n, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47
    .word u_vec_\n
    .endr

    .section .rodata.moy_umode, "a"
    .balign 4
    .global u_mtvt_want
u_mtvt_want:
    .rept 16
    .word _panic_handler
    .endr
    .rept 24
    .word _interrupt_handler
    .endr
    .word _panic_handler
    .word _panic_handler
    .word U_MEMPROT
    .word U_ASTDBG
    .word U_IPC
    .word _interrupt_handler
    .word _interrupt_handler
    .word _interrupt_handler
