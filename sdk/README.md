# The SDK

What a program needs to become a Moybyte app on any tier: `moy app`, the
command line every tier shares (below), and what a C program needs to become
a native module for a console (`native/moy_elf/README.md`; the design is
`docs/os_2026-11/tiers.md` §1).

## `moy app`

**Decision (owner, 2026-11-09, #246): one SDK, one `moy` command.**
moy-spec's `moy` keeps to games and runs `moy-<x>` from PATH for a
subcommand it does not have; Moybyte's is `moy-app`, the package
`sdk/moybyte_sdk/`, which `pip install -e .` (`make setup`) puts on PATH:

```
moy app new <name> [--tier python|lua|wasm|wasi|c] [--service python|lua|wasi|c]
moy app check <cart> [--strict]
moy app build <cart> [--chip esp32s3|esp32p4|host ...] [--unsigned]
moy app stubs <cart>
moy app push <cart> [--board ID | --store DIR]
moy app coredump <board> [--gdb] [--port PORT] [--elf ELF] [--symbols DIR]
```

- `new` writes `<name>.moy` from a template per tier (`sdk/moybyte_sdk/templates.py`;
  `wasm` is an app compiled to wasm, `wasi` a WASI command or service, `c` a
  native module), its manifest carrying Moybyte's fields, and with
  `--service` the app's own service as its `entries`.
- `check` reads the manifest as the console's readers do (a Moybyte field at
  the top level, an unknown permission, a `required` one not asked for, a
  native cart without `full_access`, an `entries` that does not read are
  errors) and infers the permissions the cart's programs use: a wasm module's
  `moybyte.app` and `moybyte.os` imports, a native module's rows (its
  `.moy.rows` section), and, best effort, the names a Python or Lua script
  spells. **Decision (owner, 2026-11-09): a permission used and not declared,
  and one declared and unused, are each a warning.** A row the SDK's C
  library links (`open` under stdio) is only noted: the module links it
  whether its program reaches it or not. `front` counts only for a program
  that runs in the background (`docs/app_api_v1.md` §6). A compiled program
  not built yet is read from its C source.
- `build` compiles the cart's programs from its `src/` into its own folder:
  a wasm main or service by `tools/wasm_cart.py` (the portable `.wasm`, and
  with a board's `--chip` that chip's AOT module), a native main or service
  by `tools/elf_cart.py` (a module per `--chip`, which a native cart must
  name; `host` is the host's, for its tests and terminal). Modules are
  signed as those tools sign them; `--unsigned` leaves it off. A Python or
  Lua cart runs from its source and has nothing to build.
- `stubs` copies what the cart's tier compiles or edits against into it:
  `include/moybyte/os.h` and `app.h` with a `compile_flags.txt` for C (a
  native cart's with `game.h` and libmoy's `moy.h`),
  `moybyte_os.lua` for the Lua language server, `__builtins__.pyi` and
  `moybyte.pyi` for a Python editor.
- `push` copies the cart, less those stubs and only once it checks, onto a
  board's store (`tools/board.py ID push`) or the simulator's
  (`~/.moybyte/projects`).
- `coredump` reads the core dump a panic left in the board's `coredump`
  partition over the cable and decodes it with the IDF's esp-coredump
  against the firmware ELF of the board's build (`tools/coredump.py show`):
  every task's backtrace, with a native module's frames under its own
  symbols, found by the sha256 the dump records from the unstripped twin
  each build keeps under `.build/sdk/symbols/` (`--symbols` adds a folder);
  `--gdb` opens gdb on it (`native/moy_elf/README.md`, "A crash's core
  dump").

What `check` and `stubs` know is generated from `native/os_calls.json` by
`tools/gen_os_table.py` (`sdk/moybyte_sdk/os_table.py`, `sdk/moybyte_sdk/stubs/`,
`include/moybyte/app.h`), so `tests/test_os_table.py` holds them to the
table; `tests/test_moy_app_cli.py` runs each verb, and
`tests/test_moy_app_carts.py` checks every seed and fixture cart, a warning
standing only where it lists the reason.

## The native module

- `include/moybyte/os.h` (generated, `tools/gen_os_table.py`): the table's
  version, its type, the event descriptor (`MOY_EVQ_FD`) and the record a
  read of it takes (`moy_evq_ev_t`, from `native/moy_kernel/moy_evq.h`), and
  the Moybyte rows: the kernel's (`moy_log`, `moy_term_size`, `moy_send`,
  `moy_on`, and a game's screen, `moy_screen` and `moy_present`) and the app calls (`moy_files_*`,
  `moy_prefs_*`, `moy_clipboard_*`, `moy_nav_open_app`, `moy_notify`,
  `moy_open_with`, `moy_share`, `moy_award`, `moy_progress`, and the wakes
  `moy_every`, `moy_after`, `moy_at` and `moy_cancel`, each heard as a
  message from the system, and the app's own service, `moy_service_start`,
  `moy_service_stop` and `moy_service_running`, and `moy_pick`, whose item
  is heard as the message `moy_open` and read and written by its handle with
  `moy_item_read` and `moy_item_write`, the leases and devices by handle,
  `moy_dev_open` to `moy_dev_get`, and a status item in the bar,
  `moy_status_show` to `moy_status_remove`:
  `docs/app_api_v1.md`'s call table), all from
  `native/os_calls.json`. The POSIX rows are declared by the toolchain's own
  headers (`unistd.h`, `fcntl.h`, `pthread.h`, ...).
- `include/moybyte/app.h` (generated): a C app compiled to wasm's
  `moybyte.app` imports, each under its import name.
- `include/moybyte/frame.h`: the frame loop (`native/moy_elf/moy_os.h`, A
  GAME): `moy_game_frame` waits for the console's next frame message and
  reads its buttons and an app's input words.
- `include/moybyte/game.h`: a native game's screen: `moy_game_screen` maps
  a screen-sized buffer and puts a libmoy canvas over it, and `moy_present`
  hands the screen over. It includes `frame.h` and libmoy's `moy.h`.
- `include/moybyte/ui.h` (generated): the public UI API for a native app
  (a manifest of type `app`), every `ui` row under app.h's name and
  signature, answered inline by the toolkit the app links:
  `src/moyui_link.c` and `native/moy_ui` with its RUN face, built into
  `libmoyui.a` (`tools/elf_cart.py`'s `libmoyui`, libmoy at RGB565 in it),
  which an app's module links before `libmoyos.a`. `moy_ui_link_frame`
  takes the frame and pulls the console's theme where it moved (the
  `moy_ui_pull` and `moy_ui_font_pull` rows), the rows draw on the app's
  own canvas, and `moy_ui_link_show` presents it, RGB565.
- libmoy's raster (**decision, owner, 2026-11-09, #246**: the game verbs
  are a library, not rows): `native/moy_gfx/libmoy`'s `moy_canvas.c`,
  `moy_sprite.c` and `moy_data.c`, the copy the console compiles, built
  into `libmoyos.a` as its index build (one byte a pixel) under the SDK's
  flags, so a game draws with the verbs and pixels the Lua and wasm tiers
  draw with. It holds no writable data, and a module links only the verbs it
  calls (`tests/test_elf_player.py`).
- `src/moyos_crt.c`: the module's entry, `_moy_entry`, and its
  `.moy.table` note.
- `src/moyos_stubs.c` (generated): one stub a row but a retired one, each compiled on its own
  (`-DMOY_OS_ONLY=<row>`) so the link takes only the rows a program calls.
- `libc/`: the SDK's C library (below), compiled into `libmoyos.a` with the
  stubs.
- `sysroot/include/`: on a board, the POSIX headers the IDF's newlib lacks or
  declares for another layout: `sys/socket.h`, `netinet/in.h`, `netdb.h`,
  `arpa/inet.h`, `poll.h` with lwIP's values (the kernel's sockets are
  lwIP's, `native/moy_elf/moy_os.h`), `sys/mman.h` with Linux's (the
  kernel's), and `stdio.h` adding `getline` and `getdelim`.

`tools/elf_cart.py` is the build: it compiles these into `libmoyos.a` once per
chip and table (cached under `.build/sdk/`), compiles a cart's `src/*.c` with
the chip's ESP-IDF toolchain, links it against that archive, strips it, signs
it, and writes `main.<chip>.elf` into the cart. A program's hooks are plain
function: `main(argc, argv)`, a command's run and a service's loop alike.
A service blocks in `poll` on `MOY_EVQ_FD` with its nearest deadline as the
timeout, reads each message it is woken for, and returns when the
descriptor reads end of file (its stop); `tests/fixtures/elf/elf_svc.moy`
is the example. A WASI program built by wasi-sdk includes the same header:
under `__wasm__` it declares the rows a WASI program imports (`moy_send`,
`moy_on`, `moy_notify`, `moy_log` and its levels, the wakes, `moy_prefs_*`,
`moy_clipboard_*` and `moy_open_with`; `docs/app_api_v1.md` §10) as its
`moybyte.os` imports (`tests/fixtures/wasm/wasi_svc.moy`, built by
`tools/wasm_cart.py`, which puts `include/` on its include path).

The chips: `esp32s3` (Xtensa, `-mlongcalls`), `esp32p4` (RISC-V,
`rv32imafc`, `ilp32f`), and `host`, the simulator's, which links the host's C
library dynamically behind the SDK's own.

## The C library

**Decision (2026-11-08): the SDK carries its own small C library, written
against the headers the program compiles with** (the IDF's newlib on a
board, glibc on the host), and takes from the toolchain only what holds no
state. Rebuilding picolibc or newlib position-independent per chip was the
alternative, and lost on three counts: the program's macros (`getc`,
`stdin`, `isdigit`) expand into the header's own layout (newlib's reentrancy
record, `_ctype_`, glibc's `__uflow`), so a library under other headers
would not meet them; a second libc build per chip is a toolchain to fetch,
pin and maintain; and the same sources compile into the host's modules,
so the host suite tests the library a board runs (`tests/test_sdk_libc.py`).

- `libc/` is stdio (buffered `FILE` over the file rows, the getc and putc
  slow paths each header names), the printf family (exact decimal
  conversion, bit for bit glibc's), scanf, `strtol` and its kin, `strtod`,
  a heap over `mmap` arenas, `getopt`/`getopt_long`, `atexit` (run before
  the `exit` row and after `main` returns), and on newlib ctype,
  `strerror`, `strtok` and the `inet_*` address calls. Every symbol is
  hidden, so a module exports none of it.
- `elf_cart.py`'s `PURE` list names the toolchain libc members a module
  takes as they are (string and memory calls, `qsort`, `bsearch`): each
  touches no reentrancy record, locale, errno or heap. `pure_libc()` copies
  only those and refuses one that needs more.
- libgcc comes from the toolchain, except on RISC-V, where its count-zeros,
  64-bit division, double division and float/64-bit conversion helpers are
  not position-independent; `libc/lc_rv32.c` carries those
  (`elf_loader` relocates only absolute and PLT words on RISC-V). On the S3
  a module links a copy without its `.eh_frame`: the S3's ld aborts a link
  that removes a function whose FDE it has counted (`elf_cart.py`'s
  `CHIPS`), and a module unwinds nothing.
- libm is the toolchain's on the S3. On the P4 the toolchain's libm
  addresses its constants absolutely, as its libgcc's helpers do, so
  `libc/m/` carries one: MicroPython's copies of fdlibm's float functions and
  musl's double ones (`libc/m/README.md`), compiled into the P4's libmoyos
  without FP contraction. It sets no errno and keeps no state.
  `tests/test_sdk_libm.py` holds a module that calls it to the loader's
  relocations and runs the same objects under qemu's RISC-V user mode.
  A libm function it lacks (`hypot`, `cbrt`, `exp2`, `log2`) links from the
  toolchain only where that member is position-independent, and otherwise
  fails the module's link.

## Building outside a cart

`tools/elf_cart.py module SRC.c... --chip CHIP -o OUT` builds a module from
loose C files. For another build system, `elf_cart.py sdk --chip CHIP`
prints the compiler, flags and libraries, and `elf_cart.py finish` strips and
signs a linked module. `cmake/` wraps those as toolchain files, one a chip:

```cmake
# cmake -DCMAKE_TOOLCHAIN_FILE=<repo>/sdk/cmake/esp32s3.cmake [-DMOY_UNSIGNED=ON]
add_executable(nc nc.c)
moy_module(nc)            # writes main.esp32s3.elf beside the build
```
