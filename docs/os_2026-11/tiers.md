# The OS (2026-11): the C, Lua and wasm tiers

Phase 5 of the order of work. Read `docs/os_2026-11.md` first; Python's tier
is `processes.md` §3.

---

## 1. The native ELF tier

**Decision (owner, 2026-11-01, #227):** a C app is an ELF module compiled
against a **versioned, append-only OS function table**, loaded on demand, one
build per chip, installed on the user's consent to "full access to the
console" (the overview's §4): the tier for the engine wasm cannot serve (the
sandbox's per-core cost is #158's) and for the person who would otherwise
flash an image. C has no isolation: it runs at the kernel's own trust level.

- **Process model.** An ELF process has the same hooks as a wasm one
  (`moy_start`, `moy_stop`, `moy_timer`, `moy_on` for an event, the lane
  exports; a command is `main(argc, argv)` over the POSIX rows, as a wasm
  command is WASI's `_start`, §3) and
  **no raw RTOS**: the table exports no task, timer or ISR primitive. The OS
  creates the module's task with a priority and core cap (core 1 only on the
  S3s), dispatches edges as ring events and timers as OS-owned `esp_timer`
  entries calling the module on its own task. Every registration is a handle
  under the process's OWNER, so "leases end with the process" stays true.
  `pthread_create` is the one thread verb (below): it makes an OS-owned task
  under the OWNER with the module's caps, swept on stop, never a raw
  FreeRTOS task. PSRAM text is never an ISR and never runs during a flash
  operation (`native/moy_kernel/moy_crash.h`'s cache rule).
- **The OS interface is a POSIX subset (owner, 2026-11-03)**, so existing C
  software ports with little change: `open`/`read`/`write`/`close`/`stat`/
  `readdir` over the files tree (`between_apps.md` §2), gated by the app's
  permissions; BSD sockets (lwIP's) under `network`;
  `time`/`clock_gettime`/`nanosleep`;
  `pthread_create`/`join`/mutexes as above. Each is a row of the table, so
  the table's rules (below) hold. A **cross SDK** ships with it: a sysroot and a
  CMake toolchain file per chip (`+sdk/`), the headers generated from the
  ordinal source.
- **Runtime.** No libc, libgcc or C++ runtime code crosses the table: the
  POSIX rows are the table's own functions behind the IDF's newlib headers
  in the sysroot (owner, 2026-11-03), and a module links the rest
  statically, picolibc or newlib (memcpy, string, the soft-float helpers it
  needs), the loader runs `.init_array`, and a module asks for **no internal
  SRAM** on the S3s.
- **The loader**: a relocatable ELF per chip, text and data in PSRAM
  (executable PSRAM is what #158's AOT modules use, through fork patches the
  IDF's `elf_loader` does not carry: dual-bus/XIP, the PSRAM mirror, a
  cross-core cache sync), symbols resolved **only** against the table. The S3
  loader is a spike before P5.4.
- **The table** (sketch below): rows appended, never changed or removed; a retired
  row keeps its slot and answers through a **typed** stub (one `ENOSYS` stub
  is unsound with the P4's hard-float returns). A module records the table
  version and its highest row; a module built for a newer table is refused as
  "console needs an update". Every struct that crosses the table carries a
  version (`moy_event_t`, a lease's cfg).
- **One source, three consumers.** Today the wasm imports are WAMR signature
  strings in `roles.json` (many rows `null`), packed for the ROLE door and
  hopped to the VM task (`native/moy_app/moy_app_wasm.c`), with the UI rows a
  second source (`native/moy_ui/moy_ui_wasm.h`) and moy-spec's verbs a third.
  The ELF table needs **a per-row ordinal and a C prototype in one source**,
  pinned by a golden test; the wasm adapter and the UI rows are generated
  from it. Spec verbs, if exported, sit under a Moybyte-owned ordinal layer
  frozen separately from the spec, which is not append-only by index.
  WASI's imports (§3) and the POSIX rows (above) are the same C
  functions under two standard names; neither is a second implementation.
- **Trust** is the overview's §4 C row: once an ELF has loaded, child mode is
  not guaranteed. The strike ledger and crash record are the floor, keyed for
  an unsigned module by **content hash**, since an id is the author's to
  change (`processes.md` §10).

The deferred Rust rung (owner, 2026-11-02) uses this loader and table.

**A C app on an S3** is feasible within these limits: a foreground C app
uses the **cart's** PSRAM share (its text counting in its PROCS need); a
background C service's text is the ELF row of THE SHARES
(`native/moy_play/moy_play.h`), which is absent on the S3s, so an S3 runs
none until a spike gives it one; its code lives in PSRAM, so no ISR, no
internal SRAM, and core 1 only; and all of it depends on the `elf_loader`
spike.

**The function table (sketch).**

```c
typedef struct {
    uint32_t version;     // bumped on every append; never on anything else
    uint32_t rows;
    void    *fn[];        // row i is the same prototype forever; retired -> a typed stub
} moy_os_table_t;
```

A note section `moy.table` carries the version built against and the highest
row linked; the loader refuses `highest >= rows`. The source is the ordinal
table above; `tests/test_roles.py` pins the ordinals.

## 2. Lua

One state per process (`lua_Alloc`, internal-SRAM-first,
`docs/moycore_direction.md`), the safe stdlib. `native/moycore` runs one run
at a time (`moycore_lua.h`); the run becomes a process-table row. Lua has
**no role binding** today (only `moycore_wasm.c` links `moy_app`); a Lua
binding over the same table is phase 5, and with it Lua's **`io` and
`os` libraries** mapped onto the files tree under the app's permissions
(owner, 2026-11-03), so Lua code written elsewhere reads and writes
files the standard way.

## 3. wasm and WASI

One instance per process. One session at a time today
(`native/moy_wasm/README.md`), and the adapter binds **one global grant**
(`moy_app_wasm_bind`); per-process binding is a change. A runaway export
calling no import cannot be stopped mid-loop, so a service's exports are
OS-called and return; a callback that overruns strikes. **The wasm tier's
OS interface is WASI preview1** (owner, 2026-11-03): files over the tree
under the app's permissions, clocks, args and env, stdio, random. WAMR
ships libc-wasi and the fork builds with it off
(`native/moy_wasm/micropython.cmake`, `WASM_ENABLE_LIBC_WASI=0`); turning
it on is a build flag and a key-format bump. Any WASI program built with
wasi-sdk, Rust `wasm32-wasip1` or Zig then runs as a command unchanged;
drawing stays moy-spec's imports. The WASI imports keep WASI's names, so
they are not rows of the ordinal source (§1) but adapters over the same
C functions (the files tree, the clock, the pipes); `moybyte.term` keeps
only what WASI lacks (the terminal's size, `system.md` §1). Phase 5 with the
tier, unless the WASI spike (phase 3: the image cost on the Guition S3, the
key bump) fits the headroom floor, in which case WASI commands land with
the phase-3 terminal (owner, 2026-11-03).

A module's imports: `wasi_snapshot_preview1` for files, clocks, args, env,
stdio and random; `moybyte.hw`, `moybyte.term` (`term_size`), `moybyte.media`
for the rest.
