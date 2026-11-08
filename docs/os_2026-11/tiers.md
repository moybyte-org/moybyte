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
flash an image.

**Decision (owner, 2026-11-07):** isolation is built for the P4 boards, the
primary target: there a C app runs in RISC-V U-mode, confined by PMP to its own
memory and what it is lent, and reaches the kernel only through the table. The
S3s get none: their C tier runs at the kernel's own trust level, with the crash
record and the strike ledger as its floor.

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
  the table's rules (below) hold. A **cross SDK** ships with it, the headers
  generated from the ordinal source (`sdk/README.md`).
- **Runtime.** No libc, libgcc or C++ runtime code crosses the table: the
  POSIX rows are the table's own functions behind the IDF's newlib headers
  in the sysroot (owner, 2026-11-03), and a module links the rest
  statically: the SDK's own C library over those headers (stdio, printf,
  the heap, getopt; 2026-11-08, why in `sdk/README.md`), the toolchain's
  stateless string members and libgcc. The loader runs `.init_array`, and a
  module asks for **no internal SRAM** on the S3s.
- **The loader**: a relocatable ELF per chip, text and data in PSRAM,
  Espressif's `elf_loader` vendored with our fixes
  (`native/moy_elf/UPSTREAM.md`). A module resolves **no** symbol by name:
  its calls into the OS are the table's slots by ordinal
  (`native/moy_elf/README.md`, the module ABI).
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
  pinned by a golden test (`native/moy_elf/os_table.json`,
  `tests/test_os_table.py`); the wasm adapter and the UI rows are generated
  from it. Spec verbs, if exported, sit under a Moybyte-owned ordinal layer
  frozen separately from the spec, which is not append-only by index.
  WASI's imports (§3) and the POSIX rows (above) are the same C
  functions under two standard names; neither is a second implementation.
- **Trust** is the overview's §4 C row: an ELF that has loaded runs at the
  kernel's trust, and no permission binds it past its consent. The strike ledger and crash record are the floor, keyed for
  an unsigned module by **content hash**, since an id is the author's to
  change (`processes.md` §10).

The deferred Rust rung (owner, 2026-11-02) uses this loader and table.

**A C app on an S3** is feasible within these limits: a foreground C app
uses the **cart's** PSRAM share (its text counting in its PROCS need); a
background C service's text is the ELF row of THE SHARES
(`native/moy_play/moy_play.h`), 256 KiB on each S3; its code lives in PSRAM, so no ISR, no
internal SRAM, and core 1 only.

**The function table (sketch).**

```c
typedef struct {
    uint32_t version;     // bumped on every append; never on anything else
    uint32_t rows;
    void    *fn[];        // row i is the same prototype forever; retired -> a typed stub
} moy_os_table_t;
```

A section `.moy.table` carries the version built against and `.moy.rows`
every row linked; the loader refuses a row at or past its own `rows`. The
source is `native/moy_elf/os_table.json`; `tests/test_os_table.py` pins the
ordinals.

## 2. Lua

One state per process (`lua_Alloc`, internal-SRAM-first,
`docs/moycore_direction.md`), the safe stdlib. `native/moycore` holds
several runs at once, each a row with its own state, console and media
(`moycore_lua.h`, P5.3, 2026-11-07), and the binding names a run by its
row's handle (`tests/test_lua_runs.py`); the kernel's Player plays several
(`native/moy_play/moy_play.h`), so Lua games join the running set
(`processes.md` §6). **The
role binding** is `native/moy_app/moy_app_lua.c` (P5.1, 2026-11-07), over the role
table and the ROLE door (`moy_app_role`) the wasm adapter reaches, for the
state's own run (§3): the Player makes a Lua app's run, its state's open
copies it into the state, and the state opens with the names its
permissions grant (`files`, `prefs`, `set_theme`, `open_app`), each
answering as the Python face does (the roles trace for a Lua app,
`tests/test_semantic_traces.py`), and no others. With it Lua's **`io` and
`os` libraries** are mapped onto the files tree under the app's permissions
(owner, 2026-11-03), so Lua code written elsewhere reads and writes files
the standard way: a path is a name of the grant's one kind, the process's
working folder, and one that climbs, is absolute or names a folder is
refused with `EACCES`.

**A Lua service** (`"type": "service"`, `"runtime": "lua"`) is a state of its
own, not a row of the runs (`native/moycore/moycore_svc.h`): the safe stdlib,
`os.time` and `os.clock`, and `moybyte.every`, `after`, `on`, `cancel`,
`log`, `notify` and `exit`, with `print` to the OS log, on its own task on a
P4's core 0 (`processes.md` §1). Each call runs on a coroutine under a count
hook that yields it past its budget, so a `pcall` cannot keep a runaway
callback. `moybyte.on` takes the OS's messages (`processes.md` §8's
`achieve` and `award`, `between_apps.md` §4's `net` and `ble`, and `app`,
what its own app tells it, `processes.md` §5), queued on
the service's row. What enters the console
is the VM task's: `moybyte.notify` and the functions the console hands a
system process (its `env`, whose values become globals) are CONSOLE CALLS,
which yield the call's coroutine with the call posted on the row until the
VM's task answers it at the scheduler's poll, so a service on its own task
never enters Python and its wait is not counted against its budget. The
state also opens the role names its app's run would (owner, 2026-11-08,
`processes.md` §5): the start makes the service's grant from its granted
permissions, owned by its pid, and hands its run to the state, which opens
`files`, `io`, `prefs`, `set_theme` and `open_app` through the one binding,
`moy_app_lua.c`, as a Lua app's does. A role row runs on the VM's task: on a
P4 the binding's HOP posts it on the service's row and the task waits until
the VM's task runs it in place at the poll (`moycore_svc_call`), its wait
not counted against its budget; a stepped service is on the VM's task and
calls it in place. `moybyte.tell` is a console call.

**The tier-conformance test** (P5, `docs/os_2026-11.md`) runs
`tests/test_achievements_proc.py`'s bodies once per tier over the
achievements process ported to it: the Lua tier's is
`tests/fixtures/achievements_lua.moy`, a Lua service in the seed's place
in the store, with the same events in, the same store and the same
notifications out. Its wasm parameter is an xfail: a compiled service has
no ABI yet (its timer callbacks as exports the OS calls, or WASI's
`poll_oneoff`), which is the owner's call.

## 3. wasm and WASI

One instance per process, a session per compiled run
(`native/moy_wasm/README.md`, "A cart's session"); the browser's engine
holds one. **The grant is per run** (P5.2, 2026-11-07):
a run's grant is a value, `moy_app_run_t` (`native/moy_app/moy_app_run.h`:
the state, the grant, the roles its permissions name), which the run's
owner holds: the run's process row (`native/moy_kernel/moy_proc.h`), which
the runtime's open binds to its engine. The adapter's natives find it by
the engine's handle on the call (`moy_app_wasm_run_of`: an instance's exec
env, the browser's binding, mapped to the row bound to it), as a Lua state
finds its own in its registry; a run no Player launched has no row, and
its engine's per-run state holds the grant. Two
runs at once are two grants, and neither reaches the other's
(`tests/test_app_runs.py`). The Player makes the run (`moy_app.run`) and
the launch's namespace hands it to the runtime's open; a run the kernel
opens with no VM takes it from the kernel's state, where the Player held it
across the stop (`moy_app_stop_hold`). A Lua run's state closes before its
grant ends, so a file it left open is saved. A runaway export
calling no import cannot be stopped mid-loop, so a service's exports are
OS-called and return; a callback that overruns strikes. **The wasm tier's
OS interface is WASI preview1** (owner, 2026-11-03): files over the tree
under the app's permissions, clocks, args and env, stdio, random. Any WASI
program built with wasi-sdk, Rust `wasm32-wasip1` or Zig runs as a command
unchanged (P5.5); drawing stays moy-spec's imports. The WASI imports keep
WASI's names, so they are not rows of the ordinal source (§1) but adapters
over the same C functions (`native/moy_wasm/moy_wasi.h`): stdio is the shell
session's descriptors (the terminal, a pipe's buffer, a redirected file),
a file a descriptor of its files tree reaching only the folders the
command's `files` permissions grant, args the spawn's argv, env the
session's exported variables, the clocks the kernel's. `moybyte.term` keeps
only what WASI lacks (`term_size`, `system.md` §1). **The adapter is ours,
not WAMR's libc-wasi** (2026-11-08): libc-wasi reaches the IDF's own POSIX
and VFS, past the session's pipes and the permissions, and carries a
socket and thread layer; ours is a table of natives registered like any
other, so the runtime and the AOT format are untouched and the fork still
builds with libc-wasi off (`native/moy_wasm/micropython.cmake`,
`WASM_ENABLE_LIBC_WASI=0`). Its image cost is the build's to print and
#158's to record.

A module's imports: `wasi_snapshot_preview1` for files, clocks, args, env,
stdio and random; `moybyte.hw`, `moybyte.term` (`term_size`), `moybyte.media`
for the rest.
