# moy_kernel — the kernel below the app verb table

The native kernel (#224, closed 2026-11-01; its leftovers are #245): the
board's entry and the VM service (`moy_kernel.c`), the frame loop
(`moy_loop.h`), the crash record and the boot decision (`moy_crash.h`), the
recovery floor and first light (`moy_recovery.h`, `moy_boot.h`), the idle
ladder (`moy_idle.h`), the PERF line (`moy_perf.h`), the dev channel
(`moy_devch.h`), the OS log (`moy_log.h`, the ring every writer shares and
the dev channel's `log` word reads), the scheduler's callback guard
(`modmoy_guard.c`, the VM abort), the lease table (`moy_lease.h`, the
resources a process holds raw, safe from any task under its one lock), a
compiled program's device handles over it (`moy_dev.h`, served on the
caller's own task, and the buses they drive, `moy_devbus.h`), the lock the app ABI's seqlock writers and the tiers' service tables take
(`moy_lock.h`), the process table (`moy_proc.h`, a row per
Lua or compiled run the Player launches), the service model's three pieces
(`moy_evq.h`, every process's event queue and the one wait; `moy_super.h`,
the one supervisor of background callbacks; `moy_vmq.h`, the one queue of
calls into the VM's task; the module `moy_evq`, `modmoy_evq.c`), a WASI
app's screen handed to the console (`moy_screen_slot.h`) and the
kernel's I2C bus (`moy_bus.h`). Each header states how
its piece works; `moy_kernel.h` holds the rules every kernel module keeps
(handles, PSRAM, stopping the VM), and `native/moy_play/moy_play.h` the
memory shares. The design record is `docs/history/native_kernel_2026-09.md`
and its sprint docs beside it.

## Decisions

- **The OS is native code below the app verb table; Python is an app runtime**
  (owner, accepted 2026-10-05). It reverses "MicroPython is the shell": a run
  that needs no VM gets the PSRAM the Python heap held.
- **The kernel's language is C (owner, 2026-10-06, #224).** Sprint 1a built
  the store's index in both C and Rust and measured both on every target
  (#224 holds the evidence). On Xtensa, Rust builds only with Espressif's
  forked nightly, which cannot use the S3's zero-overhead loop, so the hot path
  ran slower than C's, and forcing the loop crashed the fork. Rust's bundled
  runtime silently shadowed the board's libc and libm, and on the P4 failed to
  link at all on the float ABI. Rust doubled a small component's code size.
  And the kernel is mostly glue to C (ESP-IDF, libmoy, MicroPython, WAMR).
  What C gives up is memory safety by construction, so **containment is
  mandatory in any language**: the host builds under ASan and UBSan in CI,
  every parser of untrusted bytes is fuzzed, the kernel stays small and
  crash-only (it records its reason and reboots to the floor or the launcher),
  and runtimes hold handles, never pointers (`moy_kernel.h`).
  `tools/moy_index_spike.py` runs each component's suite and fuzz under both
  sanitizers.
- **Rust is revisited per subsystem, never assumed**: when upstream LLVM's
  Xtensa support lets the S3 build with a stock compiler, or for a
  self-contained core behind a narrow C interface. The bar is the spike's six:
  link into all five firmware targets; link into the web runner under the
  pinned emscripten; load on the host through ctypes and in the unix
  MicroPython build; report each image's delta against the C twin, the web
  bundle counted (it rides every board's image); bench unaligned loads and the
  instruction-cache miss rate on the S3's hottest path with no regression; run
  under CI in the time preflight allows. `experiments/rust_probe/` is the
  record of what a Rust build of each target needs.
- **Our own apps stay Python (2026-09-27).** Rewriting them frees no memory,
  because their memory goes when the VM stops; the editors and Paint change
  most, and a Python edit needs no firmware build; and our apps on the table a
  kid's app uses is the test that table gets. The launcher is an app too; its
  place was left to measurement (owner, 2026-10-05): it goes native only if a
  Python launcher's return misses its budget. The window managers and
  `console.py` stay Python: sprint 7, which would have moved them, is
  cancelled (owner, 2026-11-01, #246), its memory motive met by the VM stop.
- **The recovery floor is native**, because a kernel that reboots into a
  Python launcher has no floor when the VM cannot start (`moy_recovery.h`).
  Its screen is the recovery console (owner, #246: the terminal is mostly C,
  and the console is the same component): the Terminal's C face under the
  reason and the choices (`native/moy_sh/moy_sh_floor.h`), fed by the board's
  C input drivers through the kernel's input table, the card mounted from C
  as it opens.
- **Not built, by decision (2026-09-27):** a retained widget tree, in any
  language (`docs/surface_model_v1.md` L7); an interim memory mechanism (a
  release protocol, a soft-reset backstop and heap arenas were drafted and
  dropped: each is work the VM stop undoes); raising
  `MOYBYTE_GC_SPLIT_RESERVE`, or executing module text from a flash partition
  (both declined 2026-09-26, #158). The kernel sprints moved no shipped app,
  editor or Layout class out of Python and changed no cart verb. One VM at a
  time was a non-goal of the sprints; the 2026-11-01 OS direction (#246)
  reopens it with a spike of one MicroPython instance per process.
