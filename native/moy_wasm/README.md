# moy_wasm — the WebAssembly cart tier's engine

The compiled cart tier's engine; the decisions it rests on are below
("Decisions the tier rests on"), its measurements #158, and the plan that built
it `docs/history/wasm_tier_plan_2026-09.md`. It is the ENGINE only: the vendored
WAMR runtime and the thread a module runs on. There are no verbs here and no
import table — that is C in libmoy beside the Lua binding
(`native/moycore/libmoy/moy_wasm.c`, vendored), hosted by moycore — so a module
run through `start()` below can import nothing, and a cart runs as a SESSION
moycore drives (see "A cart's session").

| piece | where |
|---|---|
| the runtime | `wamr/`: the AOT-plus-interpreter subset of Moybyte's WAMR fork ("Decisions" above), copied by `tools/vendor_wamr.py` at the commit `wamr_vendor.json` records (`make vendor-wamr`; `tests/test_wamr_vendor.py` holds the copy to the fork) |
| the build | `micropython.cmake`: the runtime as its own static library -- AOT plus WAMR's classic interpreter (no JIT, no libc-wasi, no builtin libc); `MOY_WASM_FAST_INTERP` picks the fast interpreter instead, unset on every console board (#158: classic costs less flash for a fallback tier that is never the speed path) |
| the binding | `modmoy_wasm.c`: read a module file, run it on a thread, report; a cart's session, AOT or interpreted |
| the session | `moy_wasm_session.h`: the C surface moycore drives a compiled cart through -- the browser's engine (`native/moy_wasm_web`) implements it too |
| the key | `moy_wasm_key.h`: the provenance key an AOT module must carry, per chip, its compiled-code FORMAT VERSION, and the layout of its signature -- main.wasm on the interpreter carries none and needs none |
| the footprint | `moy_wasm_footprint.h`: what a load takes -- the pool, the block a module file is read into, the run stack -- stated once for the engine, the Player's fit check and the host twin |
| the thread | `moy_wasm_thread.c`: the run's pthread, stack placed per board; a cart's par lanes are made the same way |
| WASI | `moy_wasi.c`, `moy_wasi.h`: WASI preview1's imports for a compiled command, adapters over the shell's C; `modmoy_wasm.c`'s `command()` runs one in a session of its own ("A command") |

Which boards take it is `board.toml` data: each console board's
`[[native.shared.take]]` and the headless Zero's denial, each with its reason;
`tests/test_board_toml.py` holds every board file to deciding.

## Decisions the tier rests on

Taken 2026-09-25 after three adversarial reviews (architecture, hardware,
product), with the owner's later calls dated where they land.

- **WebAssembly is the sealed tier.** The lineup is three instruction sets and
  a browser; one `main.wasm` runs on all of them and a per-chip module is a
  derived cache. Linear memory is bounded, the imports are the only capability
  surface, there is no firmware symbol table to version, and the cart's memory
  is one block its manifest sizes. The author's toolchain is clang. C apps as
  ELF modules against a versioned OS interface are a tier too (owner,
  2026-11-01; #227, #246): trusted native code under the consent its
  install asks for (`full_access`, "Full access to the console"), where wasm
  stays the sealed one the store lists.
- **A per-chip module is native code, trusted only by provenance.** The
  sandbox is in the compiler's output, not the loader, so a board loads a
  module only when its key matches and its signature verifies ("Provenance").
  A module for another chip or format is never opened (the interpreter plays
  the cart); a present signature that does not verify is refused.
- **Signing is the default, not a lock (owner, 2026-09-29):** an unsigned
  module loads only for a cart whose consent grants `full_access`, and there
  is no console-wide switch (owner, 2026-11-02). The store compiles and signs
  what it lists, so its carts never need that consent.
- **A moy-spec binding, not a Moybyte runtime.** The import table is the verb
  table, which is the spec's subject: SPEC.md §16, an optional binding beside
  Lua (2026-09-30). A host without the tier refuses the runtime cleanly
  (SPEC.md §3.1) and pays nothing.
- **One engine, one import table, every tier.** The host runs the same C
  binding over WAMR through ctypes, as it runs Lua (`runtime/moycore.py`); a
  host and a device that disagree about a verb is what deleted lupa. There is
  no wasmtime tier (the spike's runner stays an oracle in
  `experiments/wasm_aot/doom/`). The browser adapts the same C thunks in
  JavaScript (`native/moy_wasm_web`).
- **The module shape is moycore's.** This module is the engine; the import
  table is C in libmoy beside the Lua binding, hosted by moycore with its
  snapshot-in, queue-out contract; the Player's runtime hook is a map of
  runtime factories, so a missing runtime is an absent key.
- **WAMR is a fork pinned by hash**, moybyte-org/wasm-micro-runtime over the
  upstream 2.4.5 tag, holding the ESP32-S3 and ESP32-P4 platform work, vendored
  as sources (no network fetch inside a build) with its compiler prebuilt. The
  pin moves only for a security fix, an ESP-IDF bump or a feature the tier
  needs, and a move rebuilds the compilers and every module and re-runs hello
  and Doom on both chip families and Linux. Upstreaming is optional.
- **How a host executes a module is host policy** (AOT, XIP, caches, an
  interpreter) and never enters the spec. Every console board carries WAMR's
  interpreter (owner, 2026-09-30), so no cart is refused for want of a module
  ("The interpreter tier").
- **A cart survives its firmware (owner, 2026-09-30, ESP 88).** A `.moy`
  carries `main.wasm` and any number of compiled modules, each named for its
  chip and format version (`tools/wasm_cart.py`'s `aot_name`); a console keeps
  only its own (`tools/push_cart.py`'s `push_files`, `runtime/cart_index.py`),
  and with none the cart plays on the interpreter. The key names a FORMAT
  VERSION, never the fork commit ("Provenance"), so re-vendoring does not stale
  an installed cart.
- **Every console board, not two.** Each board takes or denies the module in
  its `board.toml` with a reason. The Guition S3 is the floor board for memory.
- **The floor defines a portable cart; a bigger cart is allowed and says so
  (2026-09-26).** A cart within the floor board's share of the cart-runtime
  reserve runs on every board; one above it is a warning at check time and
  the fit notice on a board that cannot hold it ("A cart too big for the
  board"), never an error panel. Raising what a board fits is kernel work, not
  a per-cart exception; the larger reserve and module text in a flash
  partition were measured and declined (2026-09-26, #158).
- **Cart storage is the board's**: the store's card, or its internal volume
  without one. Assets are read through the cart's own folder and nothing else.
- **A compiled cart keeps files (owner, 2026-10-03):** SPEC.md §16.12's
  `writable` paths, kept beside the carts store in its app folder
  `apps/<id>/` (`runtime/cart_files.py`) on every tier, untouched by an
  install, an update or a push. A user's own files are a separate
  capability (#231): the console's picker (`docs/os_2026-11/between_apps.md`
  §2), which a compiled app reaches by its `moybyte.app` imports
  (`moybyte_pick`, the item a handle its `moybyte_item_read` and
  `item_write` take; `docs/app_api_v1.md` §10) and a game holds none of.
- **WASI preview1 is an adapter of ours, not WAMR's libc-wasi
  (2026-11-08).** A command's imports are a table of natives registered
  under WASI's module name (`moy_wasi.h`), each over the C a shell built-in
  calls: the session's descriptors, its files tree under the command's
  permissions, its variables; and `moybyte.os`, the calls a WASI program
  shares with an ELF module: the service model's (`moy_send`, `moy_on`),
  beside its event descriptor (`moy_wasi.h`, THE EVENT DESCRIPTOR), the OS
  log (`moy_log`), and the console calls `moy_notify`, `moy_award`,
  `moy_progress` and the wakes (`moy_every`, `moy_after`, `moy_at`,
  `moy_cancel`, each heard as the system's message carrying its id),
  `moy_open_with`, `moy_pick` and the app's own service
  (`moy_service_start`, `stop`, `running`), posted on the VM's queue as a native service's are
  (`moy_wasi.c`, THE CONSOLE CALLS), and the files, nav, prefs,
  clipboard, device and item rows and `share`, run for the grant the
  program's start made (THE APP ROWS). libc-wasi would reach the IDF's POSIX and VFS
  past the session's pipes and the permissions, and brings a socket and
  thread layer; the table leaves the vendored runtime and the compiler as
  they are, so `MOY_WASM_FORMAT_VERSION` did not move and no installed
  module went stale. The build keeps `WASM_ENABLE_LIBC_WASI=0`. The rows
  carry libc-wasi's signatures exactly: wamrc knows them, so a module's
  compiled code turns each `*` argument into a native pointer at the call
  site and calls the native directly, whatever the row says, and the
  interpreter must hand over the same pointer.
- **Doom lives in moybyte-org/carts, never in the firmware** (GPL glue,
  2026-09-26; the WAD given away unmodified from that site, owner, 2026-10-03;
  `THIRD_PARTY.md`). Which boards fit it is #158's.

## A run

```python
import moy_wasm
moy_wasm.start("/sd/carts/wasm_hello/hello.aot", "step", (20000,))
moy_wasm.done()          # False while the thread runs
moy_wasm.result()        # waits (the VM lock released), then a dict
```

`start(path, export, args=(), loops=1, stack=None, psram_stack=None, pool=None,
allow_unsigned=False)` reads the
module file through the VFS (the T-Deck's SD and the P4s' flash store look the
same) into a PSRAM buffer, checks its signature (see Provenance; a refused
module ends the run at once, with no thread; `allow_unsigned` is what a
cart's consent to `full_access` gives its load) and starts a thread that does,
`loops` times over: load, check the key, instantiate, call `export(*args)`
with i32 arguments, and unload. `stack`/`psram_stack` override the board's
setting and `pool` the pool's sizing rule, for a measurement: a module's
`pool_peak` under a pool bigger than the rule's is what the rule has to hold.
One run at a time.

**Everything WAMR does happens on that thread**, the runtime's init and
teardown included: WAMR's platform layer calls `pthread_self()`, and IDF
answers that only for a pthread -- which the MicroPython VM's task never is.
The thread runs on the VM's core at the VM's priority, so a run takes the VM's
time; the only wasm code that runs on core 0 is a cart's `par` items (below),
on a lane below every task already there.

`result()` carries `ok`/`error`, the export's `value` (and `mismatches`: passes
whose value differed from the first), `loops`, the module's `wasm` hash from
its key, `load_us`/`load_us_max`, `inst_us`, `call_us` (first, `_min`,
`_max`), `run_us`, `stack`/`stack_psram`/`stack_used` (the thread's high-water
mark), `pool`/`pool_peak` (the pool's size for this module and its high-water
mark), and the internal-SRAM account: `sram_before` (free
when the run was started) and `sram_min` (the heap's local-minimum monitor,
started before the thread exists, so the thread's own stack and control block
are in it), plus `psram_min`.

`mem()` → `(internal_free, internal_largest, internal_min_since_boot,
psram_free, psram_largest)`. `footprint(memory, module_bytes)` → `(total,
block)`: what loading a module file of `module_bytes` for a cart declaring
`memory` bytes of linear memory holds in PSRAM at its peak, and the largest
single free block that peak asks for (see "A cart too big for the board").
`KEY` is the key this build wants after the wasm
line, `FORMAT` the compiled-code format version alone, `FORK` the fork commit
this engine was vendored from (diagnostic only since 2026-09-30 -- it plays no
part in the key, see Provenance), `STACK` the board's default `(bytes,
in_psram)`, `POOL` the runtime pool's base size (a run's `pool` adds a share
of its module's).

## A cart's session

A compiled cart the Player runs is a session: the same admission (with the
cart's consent as it stands at that load),
thread, pool, load and provenance check as a run, held open for the cart's
life, with moycore — the host half, which owns the console — supplying the
callbacks (`moy_wasm_session.h`). The thread calls them once the runtime is up (moycore
registers libmoy's import table), once the module is loaded and its key checked
(the module's shape against the manifest's `memory`, `moy_wasm_check`), once it
is instantiated (bind it to the console) and for each hook, and before teardown.
`moy_wasm_session_open` also refuses a module whose key names another
`main.wasm` than the cart's (its sha256, which the board computes).

The MicroPython task blocks in each call while the thread runs the hook — the
thread is at the VM's priority on the VM's core, so it IS the VM's time — and
while it waits it serves `moy_wasm_on_vm` requests: the cart's files -- its
own folder's reads, the last file held open, and its written files (moy-spec
SPEC.md §16.12) -- are C over the kernel's volume
(`native/moy_store/moy_files.h`) and run on the task, whose stack is internal
where an internal-flash write needs it and whose mount table the volume is
resolved through. No request calls Python.
A session per compiled run, as many as memory allows, never beside a
`start()` run. One runtime serves them all, up while any session is: the
first session's thread makes it and the last one's takes it down. Each
session keeps its own pool, sized by the footprint rule for its module, and
the runtime allocates a session's load, instance, exec envs and import
table registration from that session's pool (an allocator over the pools,
`modmoy_wasm.c`), so a session's close gives back what it took; the
runtime's own structures come from PSRAM's heap. The calls act on the
current session (`moy_wasm_session_use`), and a session's close unregisters
its import tables, which the runtime would otherwise keep pointing into.

**A cart's `par` items run on the session's lanes** (SPEC.md §16.10's
"The cart's own work across the cores"): one thread on each core but the
session's -- the S3s' and P4s' core 0 -- at the session's priority, pinned,
with the run stack's size and placement, started the first time a cart calls
`par` and joined before the runtime is torn down (`moy_wasm_session_lanes`,
`_lane_go`, `_lane_wait`). libmoy's binding fills each with a sibling
instance of the cart over the same linear memory (the fork's
`wasm_runtime_instantiate_sibling`: its own globals, tables and exec env, the
memory borrowed) and the calling core and the lane each take the next untaken
item until none is left, so a core the display keeps busy takes fewer. At the
session's priority a lane runs below everything else on core 0 -- the flush
feed and fold, the radios, IDF's own tasks -- and only takes what they leave;
work it has not started when the calling core has taken every item is taken
back rather than waited for. `moy_wasm.lanes()` reports what the lanes did
since it was last asked: work handed over, work taken back unstarted, and the
microseconds from handing over to starting and from starting to finishing. A
board declines lanes with `MOY_WASM_ITEM_LANES` 0, and then a cart's items
run on the session's core, in order, as on any one-core host -- the same
fallback an INTERPRETED session takes unconditionally (`moy_wasm_session_lanes`
reports 0 lanes whenever the live session is running main.wasm rather than an
AOT module): no sibling-instance trick is attempted over the interpreter's
module, so a cart's `par` items run in declaration order on the calling core.

**A cart's timed lanes run on the P4s** (`MOY_WASM_TIMED_LANES`,
`docs/os_2026-11/hardware.md` §2): where a module exports `moy_audio` or
`moy_timer`, the OS calls it from a high-priority thread on the core the
session is not on, each a sibling instance as `par`'s lanes are, AOT or
interpreted alike, under a deadline monitor that strikes a lane whose call
runs long. `moy_wasm_session.h` is the contract, `moy_wasm_tlane.c` the code,
`moy_wasm.tlanes()` the meter; the interpreter's pool carries their room
(`moy_wasm_footprint.h`). The S3s have none.

Where a board finds a cart's compiled module is host policy: `<main>.<chip>.f<format>.aot`
beside `main.wasm` in the cart's folder, `CHIP`/`FORMAT` naming this engine's
chip and compiled-code format version (`tools/wasm_cart.py`'s `aot_name`
builds the name; `moycore_glue.aot_path` finds it). A cart may carry any
number of these -- one per chip and format it has been built for -- so it
stays portable off a console (SPEC.md 16: "a host may keep a compiled form of
the module beside it ... a cart is complete without it"); nothing is ever
evicted, a module simply counts toward the cart's size. **A cart with no
module by this console's own name is not refused** (2026-09-30, "A cart
survives its firmware", ESP 88): `device/moycore_glue.WasmRun` opens
`main.wasm` itself on the interpreter instead, with no key and no signature,
and the Player's notice says it needs an update to run at full speed (below).
A module that loaded and ran its `_init` (`moycore.wasm_ran`) is past every
reason to retry: an error from it is a trap in the cart's own code, shown on
the stop card as the cart's error, on the VM-stopped path
(`native/moy_play/moy_play_stop.c`) as on the VM's. The Deep Init fixture
traps in `_init` and counts its runs in a written file to hold both to it.
The sync RPC
declines binary files, so neither a module nor `main.wasm` crosses between a
browser and a board (`runtime/moy_sync.py`): a compiled cart plays where it
was put -- on a board natively or on the interpreter, in a page that keeps its
own carts on the browser's engine (`native/moy_wasm_web`); a page a board
serves never lists it, having no `main.wasm` to scan.

**A full-frame blit presents above the tick model's 30 on every board.** The
plan expected the S3 to sit at or under it; measured on 2026-09-25 (the figures
are #158's) the Blit Wasm fixture — the cart's own raster rewriting all
320 x 240 indices plus the host's 256-entry palette resolve, every frame — ran
unpaced at over 45 fps on every console board, the Guition P4 lowest, the
Waveshare P4 at its 60 cap. A frame on this tier costs what the CART's raster
costs; the resolve is a few milliseconds. Each suite pins a floor for that
fixture and for the hello cart (`tests/test_*_on_glass.py`), measured with WiFi
off, the state a cart plays in.

## A command

A cart whose manifest says `"type": "command"` and `"runtime": "wasm"` is a
WASI program: its module's `_start` run in the shell's session when the
shell spawns its name (`runtime/shell/sh_door.py`, `docs/os_2026-11/system.md`
section 1). `command_start()` opens a session of its own for it, as the
Player's glue opens a cart's (its module for this console where the cart
carries one, `main.wasm` on the interpreter otherwise or when that one is
refused for anything but tamper evidence, `device/moycore_glue.py`'s
`WasmRuntime`), registers the WASI table when the runtime is up, refuses a
module importing anything but WASI, `moybyte.term` and `moybyte.os` or exporting no
`_start` once it is loaded, begins its call into `_start` on the session's
thread and answers at once, making whichever session was current current
again. The run then goes in steps (`moy_wasi.h`, "IN STEPS"): each
`command_step()` serves the natives' bodies, which hop from the session's
thread to the MicroPython task (`moy_wasm_on_vm`, which finds the session by
its thread), for a few milliseconds inside the door's crossing with the
shell's session entered, so a body reaches the session's descriptors and
files as a built-in does and the console's frame goes on between the steps;
`command_stop()` ends the run as Ctrl-C does. A clock's wait (`sleep`)
waits on the session's thread, never in a hop. In the foreground the shell
door steps the run as a job, either compiled tier's alike
(`runtime/shell/sh_door.py`); with `&` the glue steps it to its end, feeding
the task watchdog. A command's exit status is `proc_exit`'s, 0 when
`_start` returns; a trap answers -1 with its message, a module that would
not run -3. The host runs the same table over its WAMR, `_start` on a thread
of its own stepped the same way (`runtime/host/moyhost_wasm.c`'s
`hw_wasi_start`).

## A service

A cart whose manifest says `"type": "service"` and `"runtime": "wasm"` and
carries no source is a WASI service, the scheduler's as any service is
(`runtime/system/scheduler.py`, COMPILED SERVICES; `moy_wasi.h`, A
SERVICE): a run with no shell session, its argv its cart's id, no env, no
preopen, stdin empty, stdout and stderr the OS log's lines under its pid,
its calls run in place on its own task with no hop. `service_start()` opens
a session of its own for it, its module chosen as a command's is, its task
on a Lua service's core and priority (`MOY_WASM_SVC_CORE`,
`MOY_WASM_SVC_PRIO`), and begins its call into `_start` there; the VM's task
never serves it. Several run at once, each a session. `service_stop()`
hangs its event descriptor up and waits `MOY_WASI_STOP_US` for `_start` to
return, then kills it (137); the watchdog (`moy_wasi_watch`) runs over the live
ones once a frame as a job of the kernel loop's while one lives, and
`service_reap()`, at the scheduler's poll, which a returned `_start` wants
(`moy_evq.h`, THE VM'S WAKE), runs it too and closes the session of each
that returned, answering its exit status (a trap's is 134, its line in
the OS log); `service_info()` reads its linear memory and its wakes' time.
A killed service's instance is terminated, which its `_start` reads at its
next import call ("Stopping a run" below), so a wake that calls none runs
on until it does. The task is on core 0 on the P4s and core 1 on the S3s
(`MOY_WASM_SVC_CORE`, as a Lua service's). The host runs the same C, each service's
`_start` on a pthread of its own, apart from the command's one job
(`runtime/host/moyhost_wasm.c`'s `hw_wasi_service`).

## Where the memory goes

Nothing is held between runs: the module allocates no memory at boot, so what
it costs the idle desk is its static data (about 0.25 KB of internal RAM) and
nothing else; a run's state is PSRAM, taken by the first `start`. During a run:

| what | where |
|---|---|
| the module file | PSRAM, read once; a run keeps it for its passes, a cart's session frees it once the module is loaded (below) |
| the runtime's pool (module and instance structures, the module's data segments, the loader's relocation tables while it relocates, the exec env) | PSRAM, `MOY_WASM_POOL_BYTES` (256 KB; 320 KB on the P4 boards, whose loader holds more) plus a quarter of the module (`MOY_WASM_POOL_SHARE`), allocated at the run's start and freed at its end |
| the AOT text | PSRAM: the S3 fetches it through the instruction-bus alias, the P4's external RAM carries no PMP entry |
| linear memory, AOT data sections, any runtime allocation of 1 KB or more | PSRAM only (`WASM_ESPIDF_PSRAM_THRESHOLD` in the fork's esp-idf platform) |
| the run's stack | per board, `MOY_WASM_STACK_BYTES` / `MOY_WASM_STACK_PSRAM` in `mpconfigboard.h`: 16 KB in PSRAM on every board |
| the thread's control block | internal SRAM, under 1 KB |
| a `par` lane, once a cart calls `par` | its thread's stack as the run stack's (16 KB, PSRAM) and control block (internal, under 1 KB); the sibling instance and its exec env, from the pool |

**A load PSRAM cannot serve is refused**, never served from internal SRAM:
the fork's allocator returns NULL for the text and for any data allocation over
the threshold rather than falling back, because internal SRAM on the S3 is what
WiFi, BLE and the display's DMA share. The stack defaults to PSRAM for the same
reason: measured on the Guition S3 on 2026-09-25, an internal 16 KB stack cost a run 17 KB of
internal SRAM against about 1 KB for a PSRAM one, at the same speed
(`step(400000)` 276 vs 277 ms), and with WiFi and BLE up that board has no
17 KB to give. A cart's session is no different: Jet Teapot on the Guition S3
and the Waveshare P4, and Doom on the Waveshare P4, drew the same frame rate
from the launcher with the stack internal or in PSRAM (2026-09-27), each
using under a third of its 16 KB. The number that pins this is `WASM_RUN_SRAM_MAX` in
`tests/on_glass.py`.

**A cart's module file is gone before its memory is allocated.** The
session reads the file into a block the size of the linear memory the
manifest declares, loads it, and frees it before instantiating, so the
linear memory takes that block back: read into a block of its own size, the
file left a hole below the text that the linear memory could not use, and a
cart that fit the free PSRAM in total was refused for want of one block. The
load is freeable: the fork's AOT loader copies everything the module keeps,
its data segments included, into the pool, so nothing points into the file
once the load returns. A data segment left pointing into the file would be
copied out of freed memory at instantiation, which the reused block has
already zeroed -- a Doom build traps on its first call through a function
table, and the hello cart's file name reads as nothing. The hello cart's
greeting check in every suite is the guard.

The AOT native-stack check is live: the run hands the runtime its stack's low
end plus a 2 KB guard (`wasm_runtime_set_native_stack_boundary`), and the fork
reports the calling task's real stack start where upstream reported none. An
overflow traps as "native stack overflow". A session's trap carries the
numbers the check compared, from the session thread's stack's low end at the
open: `[stack <low end>+<how far it has moved> bound +<the env's boundary> set
+<the boundary handed to it> free <least free> frame <largest entry check>
here|elsewhere]`, the last word saying whether the env was last entered on the
session's thread. A trap with bound and set at the guard, entered here, a
small frame and plenty free is one the check should not have made. The P4
suites' Deep Wasm fixture recurses without end to read them.

## A cart too big for the board

A cart above the tier's floor is allowed, and a board that cannot fit it says
so before anything loads (2026-09-26, "Decisions" above). The Player asks the
runtime what the cart's load takes -- `moycore_glue.WasmRuntime.footprint`:
the manifest's `memory`, this chip's module file as it sits in the store,
through `footprint()` above -- and compares it with `mem()`'s PSRAM free total
and largest block. A cart that does not fit opens the fit NOTICE: the Player's
panel under the title "Too big for this console." naming the cart, what it
needs and what the board has free, with the same way out as every panel and
no EDIT (`runtime/player_fit.py`'s `fit_notice`). The footprint is the file's
block (which the linear memory takes back), the pool, the module's own size
for the text and data the loader maps, and the run stack; it over-counts the
text by the module's relocations and symbols, so a cart it passes has the
room. A `par` lane is not in it: a lane whose stack or instance cannot be
made takes no items, and the session's core runs them all. That shape is
AOT's; a cart with no module for this console sizes by "The interpreter
tier"'s rule below instead, whose file is never reused.

A load that runs out of memory anyway -- the heap in more pieces than the
free total suggests -- gets the same notice: every allocation failure on the
start path, the engine's and moycore's and WAMR's "allocate ... failed", reads
`out of memory: ...`, and a MemoryError reads the same. The host twin refuses
by the same header against `wasm_host.MEMORY_LIMIT`, the biggest board's
PSRAM, so a cart the host refuses is one no board could run.

## A cart built for a newer console

A module that imports a name this console's import table lacks was built
against a newer one, and the Player refuses it before anything loads, as it
refuses a cart too big: `moycore_glue.WasmRuntime.missing` reads the module's
imports from its head (`wasm_head`) and holds them to `moycore.wasm_table()`,
the table the engine registers, and a name it lacks opens the NOTICE under
"Needs a newer console." naming the cart and every import missing
(`runtime/player_fit.py`'s `newer_notice`). The comparison is
`moycore_glue.missing_imports`, the one every tier's runtime uses: the host
twin holds the module to the same table compiled into its binding. A cart is
never linked short of an import and left to trap when it first calls it;
`moy_wasm_check` still refuses at load whatever else is wrong with a module's
imports -- another module, a row at the wrong type -- on the error panel.

## The cache sync

On the S3 the loader writes the text through the data bus and fetches it
through the instruction bus, so the two must be made to agree. The fork does
it by RANGE, twice (right after the copy, because the loader reads the text
back through the instruction alias before relocating it, and at the end of the
load): `esp_cache_msync` write-back on the data range, then invalidate on the
instruction range, under IDF's cross-core cache lock. Upstream wrote back the
whole data cache and toggled the instruction cache off and on, which faults the
other core if it is executing from flash or PSRAM at that moment — and on a
console it always is. The P4 does the same range sync plus `fence.i`. The
on-glass load/unload loop under a live cart and WiFi is the guard.

## Provenance

A per-architecture (AOT) module is native code; the sandbox is whatever the
compiler emitted. So a module loads only if its custom section `moybyte.key`
says:

```
moybyte-aot 1
wasm <sha256 of the canonical .wasm>
format <MOY_WASM_FORMAT_VERSION>
target xtensa          | riscv32
cpu esp32s3            | generic-rv32
abi -                  | ilp32f
features -             | +m,+a,+f,+c,+fast-unaligned-access
opt 3
size 0                 | 3
bounds 1
stack-bounds 1
xip 0
```

Everything after the wasm line must equal `"format " MOY_WASM_FORMAT_VERSION
"\n"` plus the chip's block in `moy_wasm_key.h`, byte for byte; an absent,
malformed or different key is refused with the first field that differs,
after the parse and before anything in the module runs. `tools/wasm_module.py`
reads the same header to write keys and to choose wamrc's flags, so a module
it builds carries exactly the key this check wants.

**The key names a compiled-code FORMAT VERSION, never the fork commit**
(owner, 2026-09-30, ESP 88: "A cart survives its firmware" above). `MOY_WASM_FORMAT_VERSION` is hand-bumped, only when a change
reaches what makes an old AOT module unsafe or wrong on a new runtime -- the
vendored AOT loader and runtime ABI, or the pinned compiler --
`wasm_format_version.json` names exactly which vendored files and compiler
pins that is, and `tests/test_wasm_format_version.py` fails the moment one of
them moves without the version moving too. Re-vendoring the fork for a reason
that does not touch that list (a security fix elsewhere, an IDF bump) no
longer stales a single module already on a board: `FORK` (`moy_wasm.FORK`)
still reports which fork commit the engine was vendored from, but it is
diagnostic only. This check never even SEES a stale-format module in the
ordinary case, because a board finds its module by a name that encodes the
format (`main.<chip>.f<format>.aot`, "A cart's session" above) -- a module
built against another format is simply the wrong file name, so it is never
opened at all, and the check below is the defence against a same-named file
that is corrupt or hand-tampered, not the everyday staleness path.

### Misaligned access

WebAssembly lets any load or store be misaligned, so an access whose address
the compiler cannot see is emitted at alignment 1, and a backend that thinks
its target cannot take a misaligned word splits it: four byte loads, shifts
and ors for every `i32.load`, four byte stores for every `i32.store`. Both
chips take a misaligned load or store of any width in hardware, so the
compiler is told so for both. On the P4 it is the `+fast-unaligned-access`
feature in the key (the name Espressif's LLVM gives it). The Xtensa backend has
no such feature, so the fork's compiler knows it from the cpu: for
`--cpu=esp32s3` an access is emitted at its own width.
What the split cost the Jet showcase, on both chips, is in #158.

The guard is a module that sweeps misaligned loads and stores of every
width, floating point included, across 72 KB of linear memory, so it crosses
every cache line in it and at least one MMU page, and counts every access
that read or wrote other bytes than byte-wise composition says
(`tests/fixtures/wasm/misaligned.c`). Every declaring board's suite runs it
and wants 0; a core that trapped instead would take the board down with it.

### Float-to-int conversions

wasm's saturating conversions give 0 for a NaN and the nearest end of the
range past either end. On the ESP32-S3, TRUNC.S already clamps past both
ends of the int32 range and gives INT32_MAX for a NaN of either sign, and
UTRUNC.S clamps above the range but gives neither 0 nor a clamp below it --
read off a T-Deck, not the ISA manual. So for `--cpu=esp32s3` the fork's
compiler emits TRUNC.S and 0 for a NaN, and UTRUNC.S and 0 for anything not
`>= 0`, where the generic expansion was three branches against two float
constants. A conversion to 64 bits has no instruction on either core: the
compiler calls a helper (`__fixsfdi` and its kin) that the runtime's
relocation table must resolve (`tests/test_aot_symbols.py`). The guard is a
module that converts NaNs of both signs, the infinities, both ends of the
range and their neighbours, from float and from double, to 32 and to 64
bits, saturating and (in range) trapping, and counts the results that are
not wasm's (`tests/fixtures/wasm/conversions.c`); every console board's suite
runs it and wants 0.

**And the file is signed the way OTA images are.** A module file is the
module, then an RSA signature (PKCS#1 v1.5, SHA-256), its length and the magic
`moybyte-sig1`; the signature covers a text naming the chip, the module's
length and its sha256, so every byte of the module -- the key section included
-- is under it (`moy_wasm_key.h` states the layout and the text, the magic is
`native/moy_net/moy_ota.h`'s, and `tools/wasm_module.py` reads them from
there). The module is ADMITTED by one call, `moy_ota_admit_module`
(`native/moy_net/moy_ota.h`), the same the native tier's loader makes
(`native/moy_elf/`): on the MicroPython task the moment the file is read,
before the thread exists, it hashes the module and checks the text with the
RSA body every OTA manifest is checked with, against the keys the image
trusts. A malformed trailer, a signature for another chip, a byte changed
anywhere or a key the image does not trust is refused as `refused: malformed
signature` / `bad signature`, and the runtime never sees it; an unsigned
module is `refused: unsigned module` unless the cart's consent admits it
(below). `tools/wasm_module.py` signs every module it builds with the OTA
signing key (`$MOYBYTE_OTA_SIGNING_KEY`, else the file `make ota-keygen`
writes) and its `verify` IS the board's call, through the host's binding
(`runtime/host/net_binding.py`; `--consented` answers for a cart granted
`full_access`); `--unsigned` builds a module with no signature, and so does
`tools/wasm_cart.py --unsigned` for a whole cart.

### An unsigned module

Signing is the default, not a lock (owner, 2026-09-29). A module with **no**
signature loads AT FULL SPEED (native trust) for a cart whose consent grants
`full_access`: the install prompt's "Full access to the console", the one line
that says what native code can do; there is no console-wide switch
(`docs/os_2026-11.md` section 1). So
someone who rebuilds a cart from its source -- Doom from moybyte-org/carts,
their own game -- adds `full_access` to its manifest and runs it on their own
console; a cart pushed by hand has no consent record and is asked for its
permissions, `full_access` among them, on its first run. The consent is the cart's (`app_perms.run_admits_unsigned`); the
engine is told it per load: `moycore_glue.WasmRun` hands it to
`moycore.wasm_open`, which hands it to `moy_wasm_session_open` as
`allow_unsigned`, and `moy_ota_admit_module` reads it on the MicroPython task
before the runtime sees a byte, after the signature.

**Without the consent an unsigned module is IGNORED, not refused**
("A cart survives its firmware" above): `WasmRun` catches
the engine's `refused: unsigned module` and retries the open on the
interpreter, `main.wasm` itself, which needs neither signature nor consent, so
the cart plays regardless -- natively with `full_access` granted, interpreted with
a short "isn't signed" notice without it. The same retry covers a
corrupted or mismatched AOT module (rare: its file name matched this
console's chip and format, its content did not; that one reads as "needs an
update", the same as no module at all) and a module calling a helper this
firmware's runtime does not register, which reads as "console needs an
update" because the cart is not what is out of date -- ANY AOT refusal falls
back to the interpreter **except tamper evidence**: a signature that is
PRESENT but does not verify is the one case that still refuses to the
ordinary error panel, because that module was changed after it was signed,
which is not staleness.

| the module file | no `full_access` | `full_access` granted |
|---|---|---|
| signed with a key the image trusts | loads natively | loads natively |
| no signature trailer | interpreter, "isn't signed" | loads natively |
| a byte changed after signing | `refused: bad signature` (panel) | `refused: bad signature` (panel) |
| signed for another chip, or with a key the image does not trust | `refused: bad signature` (panel) | `refused: bad signature` (panel) |
| a trailer whose length is out of range | `refused: malformed signature` (panel) | `refused: malformed signature` (panel) |
| key content does not match this console (name matched by chance, or corrupt) | interpreter, "needs an update" | interpreter, "needs an update" |
| no module by this console's name at all | interpreter, "needs an update" | interpreter, "needs an update" |
| calls a helper this firmware's runtime does not register (the loader's `resolve symbol <name> failed`) | interpreter, "console needs an update" | interpreter, "console needs an update" |

Whatever is admitted goes on to the provenance key, which is checked
either way: the consent says nothing about which format or chip a module was
built for. In the ordinary case the key never even matters for staleness,
because the FILE NAME already encodes chip and format (Provenance above) --
what the key still guards against is a same-named file whose content lies.

**A signature that is present must verify, whatever the setting says.** A
trailer is a claim that the module is the one its signer built. A claim that
does not check out is a module changed after it was signed -- damaged on the
card, or altered on purpose -- or one signed with a key this image does not
carry, and none of those is what the setting is for: a cart somebody rebuilt
from source carries no signature at all. Treating a failed signature as a
warning sign rather than as "unsigned" also keeps the signature the only
integrity check a signed module has, so a signed cart whose bytes went bad is
refused instead of run as native code -- and never silently downgraded to the
interpreter either, which would hide the same tampering behind a slower
frame rate instead of a panel. It is the policy OTA manifests already follow
(`.claude/rules/ota.md`: an unsigned manifest the owner put on the card may
be taken, a signature that is present is always checked). The way to run
your own build is therefore to build it unsigned; a module signed with your
own key is refused unless the image trusts that key.

### What the checks cost, and what the compiler does about it

Bounds checks stay on; the fork's compiler makes them cheap rather than
absent. On both chips (32-bit targets, 32-bit memories) an access is one
unsigned compare of the address against the memory's size less the access's
end, which also catches `addr + offset` wrapping. A memory declared with its
maximum equal to its initial size cannot grow, so that limit is a constant
the register allocator rematerializes instead of a bound held, and spilled,
across the function; the module says so (`WASM_FEATURE_FIXED_MEMORY_BOUND`)
and the runtime refuses an instance whose memory is not exactly that size. On
RISC-V a byte at offset 0 keeps the loaded bound, because LLVM turns a compare
against `size - 1` into a shift and a compare. Any other memory compares
against its bound loaded at entry, which a fixed-size memory also keeps across
calls when the code asks `memory.grow`.

A function that calls others directly checks the native stack once, at its
entry, for the largest frame it calls directly, and calls their bodies
straight; the precheck wrapper that checks a function's own frame is left for
what enters it another way, an export or `call_indirect`. On Xtensa the body
keeps its short call from its wrapper and the other functions reach it through
an alias, a long call.

A trapping float-to-int is one branch on two ordered compares, the trap path
telling NaN from overflow; the saturating conversion is LLVM's `fptosi.sat`
on RISC-V and TRUNC.S and a NaN check on the ESP32-S3 ("Float-to-int
conversions" below). Every branch to the exception path is weighted cold. On
Xtensa a square root is a call, and it goes to newlib's `__ieee754_sqrtf`
directly rather than through `sqrtf`'s errno wrapper. And the Xtensa backend
is patched to select EXTUI for shifts right by 16..31 and low-bit masks.

Tried and dropped, so not to be re-proposed without new evidence: LLVM's
inductive range-check elimination (it recognises the checks only in an
`addr < limit` form, constrains no loop in either showcase -- the wasm
induction variables carry no no-wrap flags -- and that form bloats Jet's
setup function 2.4x); reloading the bound at every check (it halves Jet's
setup pressure on the S3 and costs its rasterizer more); a `umax`-based single
check (slower on Xtensa); `fptosi.sat` on Xtensa; a constant limit for a byte
at offset 0 on RISC-V (the shift above, an instruction more in Doom's span
loops); letting LLVM inline a function's body at its direct calls (about 1%
on Doom, nothing on Jet, 10% more code); ordering an Xtensa frame's slots by
how often they are used, so the hot ones sit within the 1020 bytes `l32i`
reaches (it took 5% off Jet's S3 frame while its setup function's frame was
1184 bytes; with constant limits no function in Jet, ESP 88 or Doom has a
frame past 1020 bytes). What remains is the check itself (a compare and the
base add per access) and, on the S3, register pressure: 16 registers and the
float values of a setup loop in stack slots, and no zero-overhead `loop` --
Espressif's backend builds hardware loops only when asked, and never when
literals sit in the text, which the S3's module layout requires. The figures
are #158's.

## The interpreter tier

Every console board also carries WAMR's interpreter (classic;
`MOY_WASM_FAST_INTERP` picks the fast one instead, unset on every board here --
#158 measured it costing more flash for no speed win a fallback tier needs).
`moy_wasm_session_open`'s `interp` argument is what tells the engine to skip
the AOT path entirely: no `moybyte.key` section, no signature, no
`want_sha` cross-check, because it is loading `main.wasm` -- the canonical,
portable module every cart carries -- straight, the same `wasm_runtime_load_ex`
/ `wasm_runtime_instantiate` calls an AOT session makes, since WAMR dispatches
on the module's own bytes either way. Everything downstream of that load is
identical: the same import table (`native/moycore/libmoy/moy_wasm.c`, which
never distinguishes AOT from interpreted -- it is written at the
`wasm_exec_env_t` level, which is execution-mode-agnostic by construction),
the same session callbacks, the same hooks. `snd` works on it, because the
audio import is part of that same table; `par` runs its items in declaration
order on the calling core, never across lanes (`moy_wasm_session_lanes`
reports 0 for an interpreted session, "A cart's session" above).

**An interpreted session's pool is sized by its own rule, not AOT's**
(`moy_wasm_footprint.h`'s `moy_wasm_interp_pool_bytes`/`moy_wasm_interp_footprint`,
decided 2026-10-01): the AOT pool above is sized for relocations and a symbol
table an interpreted load never builds, and sizing one by the other was an
approximation that cost two things -- a session's own PSRAM pool could be the
wrong size for what classic actually needs, and the fit check's "needs N MB"
could name a number with no relation to why a load actually failed. The
engine picks the rule by `moy_wasm_session`'s own `interp` flag, so the real
load and `device/moycore_glue.WasmRuntime.footprint`'s pre-check (via
`moy_wasm.interp_footprint`, `footprint`'s twin) agree; the interpreted
footprint also counts the module file as RESIDENT rather than reused -- the
"module file is gone before its memory is allocated" guarantee above is
AOT's alone, so an interpreted cart's file and its linear memory are two live
allocations, not one reused as the other. The host (`runtime/wasm_host.py`)
always sizes by this rule too, because it always interprets. Measured peaks,
the margin and the classic-vs-fast decision are #158's.

**The engine never decides to fall back; `device/moycore_glue.WasmRun` does**,
in Python: it looks for this console's own module by name first, and only
opens the interpreter when there is none, or when the one it found does not
check out for a reason that is not tamper evidence (an unsigned module, ESP 88's
"Provenance" section). `self.interp` is the fact the Player reads --
`runtime/player.py`'s `_start_runtime` -- to arm the timed system notice
(`runtime/console_notices.py`'s `_draw_notice`, the same mechanism "MOYBYTE
UPDATED" uses, never `_draw_toast`'s achievement banner): `ws.notice(
INTERP_NOTICE_TITLE, sub, "warn")`, where `sub` is `self.interp_cause`
("missing", "unsigned" or "firmware") read through `INTERP_NOTICE_SUB`. It
never blocks the crash panel: the cart is already playing by the time the
notice appears.

## Stopping a run

**A runaway export cannot be stopped mid-loop.** `terminate()` calls
`wasm_runtime_terminate`, which raises "terminated by user" in the instance —
and AOT code reads the instance's exception only when an import call returns
(`aot_emit_function.c`, with bounds checks on, which the key pins). Measured on
the Guition S3 on 2026-09-25: `spin(400000000)` calls no import, ran its whole 1.7 s after
`terminate()`, and then returned the termination. A cart that calls any verb
would stop at its next verb; a loop that calls none runs until it returns.
WAMR's loop-edge checks (`check_suspend_flags`) are emitted only for
shared-memory modules under `--enable-multi-thread`, so stopping such a loop
takes a compiler change in the fork — a flag the key would then carry — or the
board restarting. `tests/on_glass.py`'s `wasm_runaway_runs_to_its_end` pins the
current answer. A cart's session is ended the same way
(`moy_wasm_session_terminate`), by the Player's runaway watch
(`native/moy_play/moy_play.h`). A `par` item is no different: the call that handed it over
waits for it, so an item that never returns holds the cart where it is. A
command stops at its next import: each WASI call checks the shell's Ctrl-C
and the door's budget first (`moy_wasi.h`). A loop calling none runs on
while the console goes on, but its stop (Ctrl-C, the budget) waits on the
console's task until the loop returns.

## Testing

- `tests/test_wasi_commands.py`: WASI commands built unchanged by wasi-sdk
  and rustc's `wasm32-wasip1` (`tools/wasm_cart.py`), run in the Terminal's
  session on the host's WAMR: args, env, stdio through pipes and
  redirections, exit codes, files under the `files` permission and none
  without it, the clocks, random, `term_size`, Ctrl-C, the budget, a trap,
  a module a command cannot run, the background; on glass,
  `tests/on_glass.py`'s `wasi_commands_run` (cat and the Rust hello on their
  modules, the probe on the interpreter) over the serial face.

- `tests/test_wasi_services.py`: a WASI service on the host's WAMR, started
  by the scheduler on a thread of its own: listed, a ping answered with a
  pong, its stdout in the OS log, its stop the descriptor's end of file, an
  end by itself into its restart policy, a wake that never polls again
  struck out, killed and terminated, and a console without the tier
  refusing one; the glue's half over a stubbed `moy_wasm` is
  `tests/test_moycore_glue.py`'s.

- `tests/test_wamr_vendor.py`: the copy is the fork at the pinned commit (read
  from the clone's git objects when `experiments/wasm_aot/wamr` has it), the
  compiler stayed behind, both interpreters and the plain loader came across,
  and one pin names the fork commit everywhere (diagnostic, not the key).
- `tests/test_wasm_format_version.py`: the format-version stamp matches the
  files and compiler pins `wasm_format_version.json` covers, every covered
  file is one `vendor-wamr` actually copies, and the key names the format,
  never a fork commit.
- `tests/test_wasm_module.py`: the key and the flags the builder derives.
- `tests/test_aot_symbols.py`: a module compiled by the pinned wamrc with each
  console chip's flags -- every wasm numeric instruction, every load and store
  width, and the runtime's memory, table and call entries -- names no symbol
  the loader cannot resolve, against `target_sym_map` as that chip's build
  preprocesses it (#229: the P4's rv32f table lacked `__fixsfdi` and
  `__fixunssfdi`). CI fetches the pinned compilers for it and fails rather
  than skips without them.
- `tests/test_wasm_signing.py`: the admission, executing the board's own
  `moy_ota_admit_module` with a throwaway key -- a signed module verifies and
  comes back as it was built; a byte changed anywhere, the key section
  included, another chip's signature, another key's, an unsigned module and a
  malformed trailer are refused -- and the table above, each kind of file
  without and with the consent; that the consent is `full_access` granted; and
  that only the two tamper-evidence refusals are what `WasmRun` never retries.
- `tests/test_moycore_glue.py`: the device glue's `WasmRun` over a fake moycore
  (the arguments `wasm_open` gets, AOT and interpreted), the retry -- an
  unsigned or key-mismatched AOT open falls back to the interpreter and
  `interp` is true, a bad signature never retries, a module found by no name
  at all goes straight to the interpreter with one `wasm_open` call -- and the
  runtime's fit report (what it asks `footprint()`, sizing against main.wasm
  itself when there is no module for this chip).
- `tests/test_wasm_cart.py`: the same import table on the host, over WAMR built
  for Linux at this pin (`runtime/wasm_binding.py`, which always runs
  main.wasm directly -- there is no per-chip AOT concept on the host) — the
  hello cart's pixel golden, the hooks under the tick model, par's items (one
  after another on the host), a trap, quit, the runtime-missing panel, the fit
  notice (the footprint against the header's rule, the huge fixture past any
  board, the host's limit, a load that still runs out), a folder in the cart
  reading as a missing file, and the store's handling of a compiled cart.
- `tests/test_push_cart.py` / `tests/test_refresh_wasm.py`: a push compiles an
  unsigned module only when this board's chip+format has none, replaces one
  built for another main.wasm, leaves every OTHER chip's module in the host's
  cart folder unpushed and undeleted, and leaves the board's folder holding
  only the modules it carried (the on-glass suites push through the same
  body); a refresh asks the BOARD which compiled
  carts it actually has (`ws.carts.all`, never a local folder walk -- Jet's
  own source carries no `main.wasm` to walk to), rebuilds one that has gone
  stale from a known local recipe (`tools/refresh_wasm.py`'s `known_sources`),
  and prunes whatever module the board can no longer use.
- On glass, every declaring board's suite: the idle cost against a module-free
  image of the same tree, the hello module and six a board refuses (no key,
  another format, other flags, another chip, no signature, one byte of a
  signed module's key changed), the same run with `allow_unsigned` -- the
  unsigned module runs, an unsigned module keyed for another format is
  refused by its key, the tampered and other-chip ones still by their
  signatures -- the two stack placements, the termination answer, a Lua cart
  after a wasm run, a load/unload loop under a live cart and WiFi, and a run
  with WiFi and BLE up; then the Player path — the hello and blit carts run
  from the launcher at their fps floors, the Par fixture's items across the
  cores leaving what the same items in order leave, with the board's lane
  having run some of them, a cart whose module was tampered with after
  signing refused, the huge fixture -- 40 MB of declared memory, past every
  board's PSRAM -- refused with the fit notice, the hello cart running after
  it, the newer fixture -- importing a name no table has -- refused with the
  newer-console notice, the hello cart running after it too, and the Read
  Dir fixture's `read` of its own `src/` folder reading
  nothing, as a missing file does. **The interpreter tier, on every board**:
  a cart with no module, one built for another chip, and one named for a
  stale format all play on the interpreter with the short "needs an update"
  notice and the hello cart's greeting check; an unsigned cart follows its
  consent -- the interpreter and its "isn't signed" notice without `full_access`,
  native and no notice with it, a tampered one still refused either way. The suites build the
  modules with the pinned compilers (`python3 tools/wasm_module.py
  compilers`), sign them with the OTA signing key (a suite without it skips
  the wasm checks, saying why), and push them into the board's store: the
  phase-1 modules under `wasm_hello/`, which is not a `.moy` folder and never
  lists as a cart, and the fixture carts as `wasm_hello.moy`, `wasm_blit.moy`,
  `wasm_par.moy`, `wasm_huge.moy` and `wasm_readdir.moy`.
