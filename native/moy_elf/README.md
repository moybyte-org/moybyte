# moy_elf — the native tier

A native app or command is an ELF module per chip, built with the SDK
(`sdk/README.md`) against the OS table and installed on the user's consent to
`full_access` ("Full access to the console" on an S3, "Run its own machine
code" on a P4, where it runs isolated). The design and the owner's decisions are
`docs/os_2026-11/tiers.md` §1; this file is the map of the code and the
decisions the code rests on.

| file | what it is |
|---|---|
| `../os_calls.json` | the ONE SOURCE of Moybyte's calls: a row with an `elf` ordinal is a row of this table, appended, never changed or removed |
| `moy_os_table.h`, `moy_os_rows.h`, `moy_os_table.c` | generated from it (`tools/gen_os_table.py`): the row enum, the kernel's prototypes, the table, its retired rows' typed stubs, and the body of each app call (below) |
| `moy_os.h`, `moy_os.c` | the process model and every row's body |
| `moy_elf.h`, `moy_elf_check.c` | the verdict a module passes before it is relocated |
| `moy_umode.h`, `moy_umode.c`, `moy_umode.S` | the P4s' isolation: the boot PMP layout, the trap side, the windows |
| `moy_os_check.c` | generated from `os_table.json`'s `u`: each row's argument rule, checked before a U-mode call runs |
| `modmoy_elf.c` | the boards' binding (`moy_elf`): store file to PSRAM, signature, verdict, relocation |
| `elf_loader/` | Espressif's loader, vendored and patched (`UPSTREAM.md`) |
| `runtime/host/moyhost_elf.c` | the host's loader: a host module through the dynamic loader, under the same C |
| `runtime/run/elf_run.py` | `ws.runtimes["elf"]` on every tier: commands, services, and a game in the Player |
| `../../sdk/include/moybyte/game.h` | a game's frame loop and screen over the two screen rows and libmoy's raster |

## The module ABI

- **A module imports nothing by name.** The SDK's archive (`sdk/src/`) holds
  the entry (`_moy_entry`, the one address the loader calls) and one stub a
  row, which calls the table's slot by ordinal and records that ordinal in
  the module's `.moy.rows` section. The entry writes `.moy.table` ("MOYT" and
  the table version it was built against). The boards' resolver refuses every
  name, so a module that links a symbol it does not define fails the load.
- **The verdict** (`moy_elf_check`) reads the file before anything is
  relocated: the chip's machine, `.moy.table` present, every row in
  `.moy.rows` below this console's row count (else "needs a console update",
  naming the row), and on a board no undefined dynamic symbol.
- **The entry's ops** are `moy_os.h`'s `MOY_ELF_OP_*`: BIND hands the table
  and answers whether the module defines `main` (a hidden weak reference the
  link resolves to 0 where it lacks it); MAIN calls it, a command's run and a
  service's loop alike. Ops 2 to 5, a service's dispatched hooks, were
  retired with the one service model (2026-11-08) and are never reused. The
  loader's own call (no table) runs the constructors.
- **errno is a row** (`__errno`, the glibc name `__errno_location` on the
  host): a slot per thread in the kernel, so a module's C library and the
  rows agree on one errno.

## The table's rules

The table is the rows of `native/os_calls.json` that carry an ordinal: the
POSIX rows and the kernel's own (`moy_log`, `moy_term_size`, the service
model's `moy_send` and `moy_on`, which a WASI program imports too, and a
game's screen, `moy_screen` and `moy_present`, 117 and 118) with
their C prototypes as written there, and the app calls, whose prototypes
the generator writes from the row's neutral types. The service timers and
input readers (`moy_timer_every`, `moy_timer_cancel`, `moy_events`, 38 to
40) are retired: their slots answer ENOSYS, and the SDK no longer offers
them. `tools/gen_os_table.py`
checks the source: a row keeps its slot and its
prototype forever, a retired row answers through a stub typed like it (its
`fail` value, `ENOSYS`), and the version is the row count, so it moves on an
append and on nothing else. `tests/test_os_table.py` pins the generated files
to the source and the source to `tests/fixtures/os_table_frozen.txt`: a row
removed, renamed, reordered or retyped fails it.

A row's body is a C function the shell and WASI already serve: files and
streams through the spawned program's session calls (`native/moy_sh/moy_sh.h`)
under the policy WASI uses (`native/moy_sh/moy_sh_prog.h`: the folders the
permissions grant, the OS log for a background run's streams, Ctrl-C and the
budget). No row is a second implementation of a shell or WASI call.

**An app call** (`moy_files_*`, `moy_prefs_*`, `moy_clipboard_*`,
`moy_nav_open_app`, `moy_notify`, `moy_open_with`, `moy_share`, `moy_award`,
`moy_progress`, the wakes `moy_every` to `moy_at`, and
`moy_service_start` to `moy_service_running`, and `moy_pick` with the
item rows `moy_item_read` and `moy_item_write`, a pick heard as the
system's message `moy_open`, the device handles `moy_dev_open` to
`moy_dev_get`, and the status item's rows; the table is
`docs/app_api_v1.md`'s call table) is a row of the app ABI's role table,
reached through the same door a compiled app's import is
(`native/moy_app/moy_app.h`'s `moy_app_role`): its generated body packs its
fields and calls `moy_os_call`, which runs it for the process's app run on the
task that serves its session (the VM's), or reads what the look and the
clipboard write where it is called, under the app ABI's seqlock, or runs a
row `how` "here" names (the clipboard's write, the device handles) where it
is called too, hopping only an answer of `MOY_APP_NEEDS_VM`. The run is a
grant of the command's own, owned by its pid, over the roles its granted
permissions name (`runtime/shell/sh_door.py`, as a service's is), handed to
`moy_os_command`; the console calls answer as that pid, under its
`notifications` and the front's gate. An answer is a number or a length, or
-1 with errno (EACCES a role or a folder the grant lacks, ERANGE a buffer too
small, EAGAIN the VM's queue full). A service has no session: its calls run
for a run grant the scheduler makes it the same way
(`scheduler.compiled_run`), as hops on the VM's queue
(`native/moy_kernel/moy_vmq.h`) that the scheduler's poll runs, its thread
waiting meanwhile and the wait not its wake's cost. The
toolkit's `ui` rows are deferred for this tier (`"elf": "deferred"`).

## The process model

`moy_os.h`'s header is the authority. What it rests on:

- The OS makes every thread: a task at the process's caps (`moy_os_caps_t`),
  its stack in PSRAM. The defaults (`moy_os_default_caps`) are core 1 at the
  VM's priority on an S3 and core 0 on a P4, where core 1 is the frame's.
  `pthread_create` is the one thread verb; the table has no task, timer or
  ISR primitive.
- A service is `main` on its own task, a loop blocked in `poll` on its
  event descriptor (`MOY_EVQ_FD`, its kernel event queue,
  `native/moy_kernel/moy_evq.h`) with its nearest deadline as the timeout:
  the one kernel wait (`moy_evq_wait`), so nothing is dispatched to it and
  nothing drains for it. Its stop hangs the descriptor up; its wakes are
  charged to the one supervisor (`moy_super.h`); a service that returns by
  itself is swept on the console's task (`moy_os_reap`), at the
  scheduler's poll (`runtime/system/scheduler.py`, COMPILED SERVICES),
  which starts, stops and restarts it as any service.
- Native code is not aborted where it stands by a timer. A wake that runs
  on without polling its descriptor again is struck at that poll by
  `moy_os_reap`'s watchdog (`moy_os.c`, THE WATCHDOG), once for each
  `MOY_SUPER_ABORT_US`, and killed (137) by the strike that suspends it; a
  kill deletes its threads where they stand, as a stop past its time does.
- A game is a service's loop with a screen (`moy_os.h`, A GAME), started by
  the Player for its run's pid (`moy_os_game`) and never the scheduler's: it
  hears the system's `frame` message ([held, pressed, ms]) once a frame
  while it has read since the last, so a slow game is never flooded and a
  paused one is sent nothing, and it presents a whole screen of palette
  indices with `moy_present`, which COPIES it into the process's frame
  (kernel memory, so a U-mode module is never handed a kernel pointer and
  its own buffer stays its own). The frame has one writer at a time: a
  present waits while the frame is READY or TAKEN, and the console's frame
  call (`moy_os_game_frame`) lets the taken one go only at its next call,
  once it has drawn it. That call serves the game's hops while it waits, as
  a stop does. A game draws with libmoy's raster, linked into the module
  from the SDK (`sdk/README.md`), or writes its indices itself; the verbs
  are not rows (owner, 2026-11-09, #246).
- Every registration is a handle in the process's record, swept with its
  pid's leases however the process ends (`moy_os_stats`' `swept`).
- A command's rows that touch the session run on the task that serves it.
  In the foreground that is the shell door's, a step at a time: the door
  answers RUNNING and the session steps it as it steps a role command
  (`native/moy_sh/moy_sh_board.c`'s `b_spawn`), so the console's frame and
  its dev channel go on while the command runs and Ctrl-C stops the job. In
  the background the door waits out the whole run, feeding the task
  watchdog. A WASI command is the same job (`native/moy_wasm/moy_wasi.h`),
  and both serve their steps through `native/moy_sh/moy_sh_prog.h`'s one loop.
- Memory a module maps (`mmap`, the SDK's heap arenas) and its sockets are
  handles in the same record, so the end frees and closes them
  (`moy_os.h`'s MEMORY and SOCKETS).
- The network is the radio's `native` lease tag
  (`native/moy_kernel/moy_lease.h`): a process's first socket calls the net
  hook (`moy_elf.on_net` on a board, `elf_binding.on_net` on the host) on
  the task that serves its session, and `runtime/run/elf_run.py` holds the
  WiFi for it until the last such process ends. The `network` permission
  reaches the kernel as the run's `MOY_OS_NET` flag (`runtime/shell/sh_door.py`).

## Isolation (the P4s)

How the P4s isolate a module (owner, 2026-11-07, `docs/os_2026-11/tiers.md`
section 1). `moy_umode.h`'s header is the authority on the PMP layout and the
traps; what the rest rests on:

- **One block a module.** The loader's one segment allocation is the block
  (`-Wl,--wrap=esp_elf_malloc` in `modmoy_elf.c`): 128-aligned, the gate
  first, then the module's text RX and its data RW, split at the first
  128-byte line of its data segment. A module whose text and data share a line
  is refused. The gate is the U-mode table the SDK's stubs call through (the
  S3's layout, so one SDK serves both chips), each row a stub that makes the
  ecall with its ordinal in t0, and the stub a hook returns through.
- **Everything else the process has is an extent**: each thread's U stack
  (its errno, its last `readdir` entry and, on the first thread,
  argv and the environment at the top), each mapping (128-aligned, a block of
  its own: the SDK's heap arenas are mappings), `getaddrinfo`'s answer. A row
  hands back nothing in kernel memory.
- **A row's arguments are checked by the row's rule** (`os_table.json`'s `u`;
  `tools/gen_os_table.py` refuses a pointer parameter without one) against
  the calling thread's process before the row runs. A pointer outside it ends
  the process, as does an ecall past the table, or any ecall from BIND, which
  has no rows. The thread counts as in a row from before the check
  (`moy_os_u_row`), so memory the check passed is not freed under the row by
  another thread's `munmap` or end. What another thread could change after the
  check is read once: a length read through a pointer (`accept`, `recvfrom`,
  `getsockopt`, `getsockname`, `getpeername`) is copied and the buffer checked
  again by the copy, a path is copied before the session's task reads it
  (`fcall`), a door row's texts are measured once, a text a row reads in place
  (a log line, a message's names, an environment key) is read no further than
  the end of its region (`text_len`, `moy_u_room`), and `getaddrinfo`'s answer
  is the process's only once it is in the process's memory.
- **A handle names its own process's object, never a reused slot's next one.**
  A `pthread_t`, a `DIR` and a mutex's id carry their slot's 16-bit
  generation; a descriptor and a socket are the process's own numbers, reused
  lowest-first as POSIX says, and a number the process does not hold is
  `EBADF`. An object a row is using outlives another thread's end of it: a
  socket closed under a `recv` is shut down and closed by the row's end, a
  mutex waited on refuses its destroy (`EBUSY`), a `readdir` holds the table's
  lock against a `closedir`, and a join claims its thread against a second
  join, a detach and a reap.
- **What a dead process held is scrubbed.** The module's block, each thread's
  U stack and BIND's stack are zeroed when taken and when let go, and a
  mapping when made and when let go (`u_scrub_free`), so no process reads
  what the heap's last holder left, the console's or another process's.
- **The module's code runs on the process's threads in U-mode**, BIND alone
  on the loading task (its fault refuses the module); the constructors run on
  the first thread before `main`. A service's wait (`moy_evq_wait`, inside
  `poll`) is kernel code: a row, so its thread parks in M-mode on its kernel
  stack.
- **A fault ends the process where it stands**: the record (`moy_os_faults`,
  `moy_elf.faults()`) names the process, the cause and the module's own
  address, the log and a command's stderr say it, and the code is 128 + the
  signal a POSIX shell names (SIGSEGV for memory, SIGILL, SIGBUS, SIGTRAP,
  SIGSYS for no such row). Its other threads park at their next way into
  U-mode (a tick at most), and `proc_end` frees once every thread is parked or
  deleted. A thread is deleted where it stands only in the module's code:
  `proc_end` suspends it, looks again, and lets one that went into a row
  meanwhile leave at the row's end, so a kill never strands a lock. Memory an
  extent lets go while another thread may be in a row, or on another core, is
  freed at the process's end. Which task is which thread is a static table
  (`self_thr`), so no row reads another process's record as it is freed.
- **A U call's way back is sealed** (`moy_umode.c`, THE SEAL): its saved
  kernel context carries a canary over its address, its space, the caller's
  ra and sp and the task, checked once at the call's end. A context that is
  not the task's ends the board with that name, never a jump through it.
- **No ESP-IDF file is patched.** The vectors are ours per core once a module
  loads (`moy_u_ready`): a trap from M-mode goes on to IDF's handler for its
  line, one from U-mode reaches IDF as an interrupt of M-mode code on the
  thread's kernel stack, so FreeRTOS never switches a U-mode context. The
  stubs' targets are `vectors_clic.S`'s; a build whose `_mtvt_table` names
  others refuses every module.
- **The PMP is the kernel's from reset.** The P4 boards build with
  `CONFIG_BOOTLOADER_REGION_PROTECTION_ENABLE=n` (their `sdkconfig.board`;
  `moy_umode.c` refuses to build otherwise) and `moy_u_layout` writes each
  core's layout at start. With the option on, the bootloader locks IDF's
  layout on core 0, a P4 lock holds until reset (no Smepmp), and those entries
  would open the CPU's peripherals to U-mode; ESP-IDF 6.2 takes the same step
  for the same reason. A board whose bootloader predates the change (an OTA
  never rewrites it) refuses every module until a cable flash.
- **A trap is closed with `mret` before kernel code runs in it**
  (`moy_u_close`): the P4's core locks up, with no panic, on an exception
  taken before the one being handled has had its `mret`. A row, a fault's
  ending and the way back to the caller all run after one; the lazy FPU
  enable (IDF's illegal-instruction trap) is the exception that finds it.

## Signing

A signed module carries the AOT modules' trailer under its own scheme,
`moybyte-elf-sig 1`, so a signature for one chip's module never verifies
another kind. It is admitted by the call the AOT tier makes,
`moy_ota_admit_module` (`native/moy_net/moy_ota.h`): a signature that is
present must verify, and an unsigned module runs only with the loader's
UNSIGNED flag, which `runtime/run/elf_run.py` sets from the cart's consent to
`full_access` (`app_perms.run_admits_unsigned`).
`tools/elf_cart.py` signs as `tools/wasm_module.py` does.

## A crash's core dump

A module is relocated into PSRAM, so a pc inside it means nothing to gdb
without the module's file and its load address. `moy_elf_maps.h`'s table
holds both for every module loaded now: the sha256 of the bytes the board
loaded (the signature trailer off) and the mapping of its code, the one
segment on a P4, the text at the instruction bus's alias on an S3. The table
sits in internal RAM the ESP-IDF core dump always carries
(`COREDUMP_DRAM_ATTR`), and each load also says it in the OS log.
`tools/elf_cart.py` keeps each module's unstripped twin (built with `-g`)
under `.build/sdk/symbols/<sha256>.elf`, and `tools/coredump.py` (`moy app
coredump BOARD`) finds the table in a board's dump, loads each twin's symbols
at its recorded address and hands the dump to the IDF's esp-coredump and gdb.
On a P4 a module's own fault is caught in U-mode and kills its process
(`moy_elf.faults()`) rather than panicking the board, so a dump there is a
kernel panic's, with any loaded module's frames named.

## The cart

A native cart's manifest names its chips, `"moybyte": {"native": ["esp32s3",
"esp32p4"], "permissions": ["full_access", ...]}`, and carries `main.<chip>.elf`
for each; a cart that does not ask for `full_access` is refused
(`runtime/shell/app_perms.py`). The shell runs a `command` as a command through the
door WASI commands use (`runtime/shell/sh_door.py`, `run_compiled`), the
scheduler a `service`, and the Player any other type as a game
(`runtime/run/elf_run.py`, `ElfGame`): its screen the run's canvas size,
what it presents drawn with the canvas class's `blit_indices`, the buttons
in its frame messages. Its main returning 0 quits; any other end is a stop
whose card names the signal and, on a P4, the fault record
(`elf_run.stop_text`), with Restart and Home and no See why, as a compiled
cart's. The Player checks its footprint (the module, the screen twice, its
stack) against free PSRAM first and gives the fit notice.
