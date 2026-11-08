# moy_elf — the native tier

A native app or command is an ELF module per chip, built with the SDK
(`sdk/README.md`) against the OS table and installed on the user's consent to
"Full access to the console". The design and the owner's decisions are
`docs/os_2026-11/tiers.md` §1; this file is the map of the code and the
decisions the code rests on.

| file | what it is |
|---|---|
| `os_table.json` | the ORDINAL SOURCE: one row a function, appended, never changed or removed |
| `moy_os_table.h`, `moy_os_rows.h`, `moy_os_table.c` | generated from it (`tools/gen_os_table.py`): the row enum, the kernel's prototypes, the table and its retired rows' typed stubs |
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

`tools/gen_os_table.py` checks the source: a row keeps its slot and its
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

## The process model

`moy_os.h`'s header is the authority. What it rests on:

- The OS makes every thread: a task at the process's caps (`moy_os_caps_t`),
  its stack in PSRAM. The defaults (`moy_os_default_caps`) are core 1 at the
  VM's priority on an S3 and core 0 on a P4, where core 1 is the frame's.
  `pthread_create` is the one thread verb; the table has no task, timer or
  ISR primitive.
- A service's hooks are dispatched on its own task from one mailbox; a timer
  is an OS-owned `esp_timer` posting to it, an event a reader of the input
  ring (`moy_input`) the VM task drains into it (`moy_os_poll`).
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

## Signing

A signed module carries the AOT modules' trailer (`moy_ota_verify_module`,
`native/moy_net/moy_ota.h`) under its own scheme, `moybyte-elf-sig 1`, so a
signature for one chip's module never verifies another kind. An unsigned
module runs (consent is the gate); a signature that is present must verify.
`tools/elf_cart.py` signs as `tools/wasm_module.py` does.

## The cart

A native cart's manifest names its chips, `"moybyte": {"native": ["esp32s3",
"esp32p4"], "permissions": ["native", ...]}`, and carries `main.<chip>.elf`
for each; a cart that does not ask for `native` is refused
(`runtime/shell/app_perms.py`). The shell runs it as a command through the
door WASI commands use (`runtime/shell/sh_door.py`, `run_compiled`). The
Player opens no native app.
