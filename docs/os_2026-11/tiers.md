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

**The consent (owner, 2026-11-08).** The permission is `full_access`. On the
S3s its install prompt line is "Full access to the console", the truth for a
module at the kernel's trust. On the P4s it is "Run its own machine code.
Unsigned code has full access to the console": a native module is confined
and cannot reach the rest of the console, but an unsigned compiled (AOT)
module runs with the kernel, unconfined (owner, 2026-11-09);
`app_perms.FULL_ACCESS_LINES` is where a board's line goes.

**As built (2026-11-08):** a module's code, its constructors and every hook
run in U-mode on its own threads; its memory is one block (the loader's
segment behind a gate the SDK's unchanged stubs call through) plus its
threads' stacks and its mappings, each a 128-aligned PMP window; every row
checks its pointers against them by its rule in the ordinal source; and any
fault ends that process alone with a fault record. No ESP-IDF file is
patched: the vectors are the kernel's own per core. How it works is
`native/moy_elf/README.md` ("Isolation") and `native/moy_elf/moy_umode.h`; its
costs are #227's.

- **Process model.** A command is `main(argc, argv)` over the POSIX rows,
  as a WASI command is its `_start` (§3); a service is `main` too, a loop
  that blocks in `poll` on its event descriptor with its nearest deadline as
  the timeout (`processes.md` §1, the service model; `moy_os.h`, A
  SERVICE): no hook is dispatched to it, and a timer is a deadline it
  passes. It has **no raw RTOS**: the table exports no task, timer or ISR
  primitive, and the OS creates the module's task with a priority and core
  cap (core 1 only on the S3s). Every registration is a handle under the
  process's OWNER, so "leases end with the process" stays true.
  `pthread_create` is the one thread verb (below): it makes an OS-owned task
  under the OWNER with the module's caps, swept on stop, never a raw
  FreeRTOS task. PSRAM text is never an ISR and never runs during a flash
  operation (`native/moy_kernel/moy_crash.h`'s cache rule).
- **A game (owner, 2026-11-09, #246)** is that service loop with a screen,
  started by the Player for its run: the kernel gives it a surface, two
  rows (`moy_screen`, its size, and `moy_present`, its own buffer of palette
  indices handed over once a frame) and the system's `frame` message on its
  descriptor, carrying the buttons. The game verbs are not rows: the SDK
  links libmoy's raster into the module, so it draws with the verbs and
  pixels of the Lua and wasm tiers, or writes pixels itself
  (`native/moy_elf/README.md`, "The cart"; `sdk/README.md`). The app rows
  stay as they are, and the toolkit's `ui` rows come later, for apps.
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
- **One source for every tier's calls (owner, 2026-11-08, #246).**
  `native/os_calls.json` holds Moybyte's app and OS calls, one row each: its
  name, its types in a neutral notation (`int`, `str`, `str?`, `bytes`,
  `cart`, `out`, and what the answer is), where it is served, the permission
  it answers under, and each tier's name for it: its ELF ordinal, whether a
  compiled app imports it from `moybyte.app` (its WAMR signature from the
  types) and whether a WASI program imports it from `moybyte.os`, its Lua and
  Python names (Lua's are Python's, verbatim), and whether the door's Python
  half decodes it. `tools/gen_os_table.py` writes
  every tier's table from it: this table and the SDK's header and stubs; the
  app ABI's roles, C rows, role table and allowlist
  (`native/moy_app/moy_app_rows.h`); the `moybyte.app` natives
  (`moy_app_wasm_rows.h`) and the toolkit's (`native/moy_ui/moy_ui_wasm_rows.h`);
  the names a Lua run and a Lua service hold (`moy_app_lua_rows.h`); the
  Python console's `ROLES`, `TABLE`, `DOOR_ARGS` and console calls; and
  `docs/app_api_v1.md`'s role and call tables. `tests/test_os_table.py`
  fails on a stale copy. moy-spec's game verbs are not in it (they are the
  `moy` module's and libmoy's); spec verbs, if ever exported here, would sit
  under a Moybyte-owned ordinal layer frozen separately from the spec, which
  is not append-only by index. WASI's imports (§3) keep WASI's names and are
  not rows; they and the POSIX rows (above) are the same C functions under
  two standard names, neither a second implementation. A row a compiled app
  imports whose server is the kernel's (`moybyte.on`, `at`, `send`, `log`,
  `every`, `after`, `cancel`, whose C runs on the caller's own task) is a row
  of the role table too, so its import has a door to cross.
- **A native module's app calls.** The app verbs a compiled app imports are
  rows of this table too (ordinals 62 to 79 and 82 to 109): the files role
  (`moy_files_readable`, `ready`, `list`, `count`, `load`, `save`,
  `delete`, `duplicate`, `rename`, `new_name`), prefs (`moy_prefs_get`, `set`, `clear`), the clipboard
  (`moy_clipboard_put_text`, `text`, `kind`, `seq`), `moy_nav_open_app`, and
  the console calls `moy_notify`, `moy_open_with`, `moy_share`, `moy_award`
  and `moy_progress`, and the wakes `moy_every`, `moy_after`, `moy_cancel`
  and `moy_at`, each heard on the event descriptor as the system's message
  carrying its id (`docs/app_api_v1.md` §9), and the app's own service,
  `moy_service_start`, `stop` and `running` (§7 there), and `moy_pick` with
  the item rows `moy_item_read` and `moy_item_write`: a pick's answer is the
  system's message `moy_open` (handle, type, mode, name; a cancel's handle 0)
  on the event descriptor, as a compiled app hears it, and the device
  handles `moy_dev_open` to `moy_dev_get` (leases and the `http`, `lora`
  (with LoRaWAN), `mic`, `ble`, `usb`, `midi` and `camera` devices, the
  media service and the `pins` permission's pins, `hardware.md` §1, §3 and
  §4), and the status item `moy_status_show` to `moy_status_remove` (a
  slot of the kernel's table, `native/moy_kernel/moy_status.h`, written on
  the program's own task but for its panel, which the shell draws; a press
  heard as the system's message `moybyte.status`, `docs/app_api_v1.md`
  §10). Each is
  its role table row run through the ROLE door for the process's app run: a
  grant the shell makes for the command, owned by its pid, over the roles
  its granted permissions name (`native/moy_elf/README.md`). The nav row and
  the console calls pass the front's gate and `notifications` as the
  process. A service has no session: a row over the kernel's own locked
  state (`how` "here": its messages, timers and log, the store's `files`
  and `prefs` rows, the clipboard's write, the device handles and
  `service_running`) runs on its own thread, and
  any other hops to the VM's task on the VM's queue
  (`native/moy_kernel/moy_vmq.h`), its thread waiting until the scheduler's
  poll runs it, and answers EAGAIN only when that queue is full. The toolkit's `ui` rows are deferred for this tier.
- **A permission's tiers are derived.** The permission table's tiers column
  is the set of tiers that serve the calls the permission hands an app (the
  rows of its role, the rows naming it, and moy-spec's tiers where its verbs
  are the spec's); only what no call serves states its own (a native module's
  run, a service's start). So `clipboard` is Python's, wasm's and ELF's,
  `network` Python's and ELF's (its sockets), `notifications` and `front`
  every tier's, `alarms` too, and an install prompt
  asks a cart only for what its tier can do (`tests/test_app_perms.py`).
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
source is `native/os_calls.json`'s rows with an `elf` ordinal;
`tests/test_os_table.py` pins the ordinals to `tests/fixtures/os_table_frozen.txt`.

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
permissions grant (`files`, `prefs`, `set_theme`, `open_app`: the source's
Lua names, §1, generated into `native/moy_app/moy_app_lua_rows.h`), each
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
`log`, `notify`, `send` and `exit`, with `print` to the OS log, on its own
task (core 0 on the P4s, core 1 on the S3s), blocked between its callbacks in the one kernel wait
(`processes.md` §1, the service model: `every`/`after`/`on` are Lua's
library over its event queue). Each call runs on a coroutine under a count
hook that yields it past its budget, so a `pcall` cannot keep a runaway
callback, and its cost is charged to the one supervisor
(`native/moy_kernel/moy_super.h`), whose last rung suspends it.
`moybyte.on(name, fn)` listens on its event queue, `fn(sender, ...)` for
each message of the name (the radios' `net` and `ble` from the system,
`between_apps.md` §4; what its app or another sends it). What enters the
console is the VM task's: `moybyte.notify`, `open_with`, `share`,
`award`, `progress` and `pick` (the source's console calls, §1, generated
into the state's `moybyte` table and the scheduler's `_CALLS`) and the functions the console hands a
system process (its `env`, whose values become globals) are CONSOLE CALLS,
posted on the VM's queue (`native/moy_kernel/moy_vmq.h`), which yield the
call's coroutine until the VM's task answers it at the scheduler's poll, so
a service on its own task never enters Python and its wait is not counted
against its budget; with no VM (an S3's VM stop, which the service runs on
through, `processes.md` §5) the call waits until the VM is back.
`moybyte.send` is the kernel's, a post on its own task (`moy_evq_send`). The
state also opens the role names its app's run would (owner, 2026-11-08,
`processes.md` §5): the start makes the service's grant from its granted
permissions, owned by its pid, and hands its run to the state, which opens
`files`, `io`, `prefs`, `set_theme`, `open_app`, `clipboard` and the
theme's reads through the one binding, `moy_app_lua.c`, as a Lua app's
does. A row of `how` "here" (the store's `files` and `prefs` rows, the
clipboard's write) runs on the service's own task; any other role row runs
on the VM's task: on a
P4 the binding's HOP is a hop on the VM's queue and the task waits until the
VM's task runs it at the poll (`moy_vmq_serve`), its wait not counted
against its budget; a stepped service is on the VM's task and calls it in
place.

**The tier-conformance test** (P5, `docs/os_2026-11.md`) runs
`tests/test_achievements_proc.py`'s bodies once per tier over the
achievements process ported to it: the Lua tier's is
`tests/fixtures/achievements_lua.moy`, a Lua service in the seed's place
in the store, with the same events in, the same store and the same
notifications out. Its wasm parameter is an xfail: the scheduler starts a
WASI service (§3), whose ABI is the event descriptor (owner, 2026-11-08:
WASI's `poll_oneoff`, built in `native/moy_wasm/moy_wasi.h`), and a WASI
program notifies, keeps its prefs and hears its messages, but the process
also asks the console for `game(app)` and `clock_day()`, the calls only a
Lua or Python service is handed.

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
unchanged (P5.5), and as a service when its cart is a service with no
source: its `_start` on a task of its own with no shell session, its stdout
and stderr the OS log's lines, no env and no files (`moy_wasi.h`, A
SERVICE; `processes.md` §1); drawing stays moy-spec's imports. The WASI imports keep
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
for the rest; a WASI program's `moybyte.os` (`send`, `on`: the service
model's, below; `notify`, `award`, `progress`, `open_with` and `pick`, the
console calls a native service posts on the VM's queue, their natives
generated from the source; the wakes `every`, `after`, `at` and `cancel`;
`log`; the files, `nav_open_app`, prefs, clipboard, device-handle (`dev_*`)
and item rows, `service_stop`, `service_running` and `share`, each its role table row for
the grant the program's start made, run as a C program's is: hopped to the
VM's task, or, for a row `how` "here" (the files, prefs and clipboard-write
rows, the device handles, the wakes, `service_stop`, `service_running`,
the status item's but its panel), run on the program's own task,
hopped only where the kernel's half answers NEEDS_VM (`moy_wasi.c`'s THE APP ROWS); and an app's `moybyte.app`, its
roles' rows. Those are rows of
the one source (§1): each import is `<role>_<verb>` at the WAMR signature its
row's types give, and the adapter's natives and the toolkit's are generated
from it (`native/moy_app/moy_app_wasm_rows.h`,
`native/moy_ui/moy_ui_wasm_rows.h`), each packing its fields for the ROLE
door and hopping to the VM's task, or, for a read of what the look and the
clipboard write, reading in place under the seqlock, or, for a row over the
kernel's own locked state, running in place on the app's own task, as its
row's `how` says. The console calls are imported too (`open_with`,
`share`, `pick`), each the `moybyte` module's verb run as the app's
process through the door (`runtime/shell/shell_servers.py`, ConsoleCalls),
under the permission it names there; `send`, `on`, `log`, the wakes
`every`, `after`, `at` and `cancel`, and `moybyte_notify`, `award` and
`progress` (the kernel's queue in front of the console's list and the
achievements process, `native/moy_kernel/moy_notes.h`) are the kernel's,
C rows run on the app's own task as its process (`how` "here",
`native/moy_app/moy_app.c`), so they need no VM, and so are the
store's `files` and `prefs` rows and the clipboard's write, under the
store's lock, the settings lock and the kernel's writer lock
(`native/moy_app/README.md`). **A compiled app
polls its messages (2026-11-08)**: `moybyte_on` is the kernel's listen and
`moybyte_recv` takes the next event of its process's queue (opened at its
launch, `runtime/system/moybyte.py`'s `_compiled_events`), a C row run on
its own task, taking it (its senders in turn, `moy_evq.h`) as the record the
event descriptor reads (`moy_evq_ev_t`, the arguments, a NUL), 0 when none
waits. A callback export the host called at
the frame's start would need room in the module's memory for three strings
and a new call in each engine's glue (a board's, the host's, the browser's);
the poll is one more generated import, cart-shaped as `btn` is (an app reads
it in `_update`), and the same record a WASI or ELF service reads, so a
compiled program parses messages one way. Its window events and its wakes are
the system's messages (`_minimise`, `_restore`, `moy_action`, `moy_open`;
`moybyte.at` and `moybyte.timer`, the wake's id), an item handed to it a handle its `moybyte_item_read` and
`item_write` take (`runtime/system/moybyte.py`: `hand_item`, which hands an
item to any process with a queue, a native command's and a service's too, and
`_compiled_events`). **Its leases and devices are handles it polls too
(2026-11-09)**: the `dev_*` rows on all three compiled tiers
(`hardware.md` §1 and §4), whose `dev_read`
takes a ring's records (a pin's edges, a packet, samples, MIDI messages,
the BLE and USB faces' events with their bytes) from the handle, a reader of the ring per handle.
The other way, a message from "system" a record, was refused: the queue
carries text, a device's records are binary and come at a rate (a
microphone's thousands a second) the queue's per-process depth is not
built for, and a read by handle lets the program choose its buffer.
