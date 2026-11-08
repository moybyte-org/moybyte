# moy_elf — the native tier

A native app or command is an ELF module per chip, built with the SDK
(`sdk/README.md`) against the OS table and installed on the user's consent to
"Full access to the console". The design and the owner's decisions are
`docs/os_2026-11/tiers.md` §1; this file is the map of the code and the
decisions the code rests on.

| file | what it is |
|---|---|
| `../os_calls.json` | the ONE SOURCE of Moybyte's calls: a row with an `elf` ordinal is a row of this table, appended, never changed or removed |
| `moy_os_table.h`, `moy_os_rows.h`, `moy_os_table.c` | generated from it (`tools/gen_os_table.py`): the row enum, the kernel's prototypes, the table, its retired rows' typed stubs, and the body of each app call (below) |
| `moy_os.h`, `moy_os.c` | the process model and every row's body |
| `moy_elf.h`, `moy_elf_check.c` | the verdict a module passes before it is relocated |
| `modmoy_elf.c` | the boards' binding (`moy_elf`): store file to PSRAM, signature, verdict, relocation |
| `elf_loader/` | Espressif's loader, vendored and patched (`UPSTREAM.md`) |
| `runtime/host/moyhost_elf.c` | the host's loader: a host module through the dynamic loader, under the same C |
| `runtime/run/elf_run.py` | `ws.runtimes["elf"]` on every tier |

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
  and answers which hooks the module defines (a hook it lacks is a hidden
  weak reference the link resolves to 0); MAIN, START, STOP, TIMER and ON
  call them. The loader's own call (no table) runs the constructors.
- **errno is a row** (`__errno`, the glibc name `__errno_location` on the
  host): a slot per thread in the kernel, so a module's C library and the
  rows agree on one errno.

## The table's rules

The table is the rows of `native/os_calls.json` that carry an ordinal: the
POSIX rows and the kernel's own (`moy_log`, the timers, the events) with
their C prototypes as written there, and the app calls, whose prototypes
the generator writes from the row's neutral types. `tools/gen_os_table.py`
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
`moy_nav_open_app`, `moy_notify`, `moy_open_with`, `moy_share`; the table is
`docs/app_api_v1.md`'s call table) is a row of the app ABI's role table,
reached through the same door a compiled app's import is
(`native/moy_app/moy_app.h`'s `moy_app_role`): its generated body packs its
fields and calls `moy_os_call`, which runs it for the process's app run on the
task that serves its session (the VM's), or reads what the look and the
clipboard write where it is called, under the app ABI's seqlock. The run is a
grant of the command's own, owned by its pid, over the roles its granted
permissions name (`runtime/shell/sh_door.py`, as a service's is), handed to
`moy_os_command`; the console calls answer as that pid, under its
`notifications` and the front's gate. An answer is a number or a length, or
-1 with errno (EACCES a role or a folder the grant lacks, ERANGE a buffer too
small, EAGAIN a process with no session to run it on: a service). The
toolkit's `ui` rows are deferred for this tier (`"elf": "deferred"`).

## The process model

`moy_os.h`'s header is the authority. What it rests on:

- The OS makes every thread: a task at the process's caps (`moy_os_caps_t`),
  its stack in PSRAM. The defaults (`moy_os_default_caps`) are core 1 at the
  VM's priority on an S3 and core 0 on a P4, where core 1 is the frame's.
  `pthread_create` is the one thread verb; the table has no task, timer or
  ISR primitive.
- A service's hooks are dispatched on its own task from one mailbox; a timer
  is an OS-owned `esp_timer` posting to it, an event a reader of the input
  ring (`moy_input`) the VM task drains into it (`moy_os_poll_services`).
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

## Signing

A signed module carries the AOT modules' trailer under its own scheme,
`moybyte-elf-sig 1`, so a signature for one chip's module never verifies
another kind. It is admitted by the call the AOT tier makes,
`moy_ota_admit_module` (`native/moy_net/moy_ota.h`): a signature that is
present must verify, and an unsigned module runs only with the loader's
UNSIGNED flag, which `runtime/run/elf_run.py` sets from the cart's consent to
`native` (`app_perms.run_admits_unsigned`).
`tools/elf_cart.py` signs as `tools/wasm_module.py` does.

## The cart

A native cart's manifest names its chips, `"moybyte": {"native": ["esp32s3",
"esp32p4"], "permissions": ["native", ...]}`, and carries `main.<chip>.elf`
for each; a cart that does not ask for `native` is refused
(`runtime/shell/app_perms.py`). The shell runs it as a command through the
door WASI commands use (`runtime/shell/sh_door.py`, `run_compiled`). The
Player opens no native app.
