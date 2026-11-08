# The native SDK

What a C program needs to become a native module for a console
(`native/moy_elf/README.md`; the design is `docs/os_2026-11/tiers.md` §1).

- `include/moybyte/os.h` (generated, `tools/gen_os_table.py`): the table's
  version, its type, the event block a service's `moy_on` receives and the
  Moybyte rows (`moy_log`, `moy_term_size`, `moy_timer_every`,
  `moy_timer_cancel`, `moy_events`). The POSIX rows are declared by the
  toolchain's own headers (`unistd.h`, `fcntl.h`, `pthread.h`, ...).
- `src/moyos_crt.c`: the module's entry, `_moy_entry`, and its
  `.moy.table` note.
- `src/moyos_stubs.c` (generated): one stub a row, each compiled on its own
  (`-DMOY_OS_ONLY=<row>`) so the link takes only the rows a program calls.

`tools/elf_cart.py` is the build: it compiles these into `libmoyos.a` once per
chip and table (cached under `.build/sdk/`), compiles a cart's `src/*.c` with
the chip's ESP-IDF toolchain, links it against that archive and the
toolchain's C library, strips it, signs it, and writes `main.<chip>.elf`
into the cart. A program's hooks are plain functions: `main(argc, argv)` for
a command; `moy_start`, `moy_stop`, `moy_timer`, `moy_on` for a service.

The chips: `esp32s3` (Xtensa, `-mlongcalls`), `esp32p4` (RISC-V,
`rv32imafc`, `ilp32f`), and `host`, the simulator's, which links the host's C
library dynamically.
