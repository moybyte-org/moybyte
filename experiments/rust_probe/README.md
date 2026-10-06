# Rust toolchain probe (#224, sprint 1a)

**This directory is a record, kept for a revisit.** The kernel's language is C
(owner, 2026-10-06): `docs/native_kernel_2026-09.md` §5 is the decision and says
when Rust is considered again. The store's index was also written as a Rust twin
(a `no_std` crate over `moy_index.h`, its build script with the toolchain pins,
and a link-map guard against Rust's bundled C library objects), and that twin
was deleted when the decision was taken. `36115eaf` is the last commit whose
tree holds it (`git show 36115eaf:native/moy_index/rust/build.sh`); the commit
after it, "Kernel 1a: delete the Rust twin, the language is C (#224)", deletes
it. Nothing else in the tree builds or runs this probe.

The question it answered: can Rust build a `no_std` C-ABI static library for
every target this repo ships, with the toolchains each target pins, and link it
into the target's real build? The directory is the experiment: one crate, one
trivial usermod, and the commands that put the library into each build. It
changes no product file and flashes nothing. The numbers it produced (versions,
sizes, times, errors) are in #224's comment, not here.

```
crate/                 moy_rs_probe: `moy_rs_probe(int)` plus one export per thing
                       a kernel asks of the toolchain (64-bit divide, f32, a
                       32-bit atomic, an unaligned load, a checked index, a
                       deliberate out-of-bounds behind `--features bug`)
usermod/moy_rsprobe/   the MicroPython module that calls every export
build.sh               the staticlib for one target, compiler_builtins dropped
drop_builtins.sh       that step (read why in its header)
firmware.sh            a board's own build.sh with the usermod added, never flashed
unix.sh  web.sh        the unix MicroPython builds (64-bit and REPR_C 32-bit);
                       the browser build, run under the emsdk's node
host.sh                ctypes, unit tests, Miri, AddressSanitizer
host/asan_harness.c    the C side of the AddressSanitizer check
```

## Targets

| target | Rust target | toolchain | linked by |
|---|---|---|---|
| T-Deck, Guition S3, Zero | `xtensa-esp32s3-none-elf` | Espressif's fork, `+esp` | ESP-IDF's xtensa gcc, through the board's `build.sh` |
| Waveshare P4, Guition P4 | `riscv32imafc-unknown-none-elf` | upstream stable | ESP-IDF's riscv gcc, through the board's `build.sh` |
| browser | `wasm32-unknown-emscripten` | upstream stable | the repo's emsdk (`firmware/web_runner/.build/emsdk`) |
| host | `x86_64-unknown-linux-gnu`, `i686-unknown-linux-gnu` | upstream stable | `make unix-micropython`'s gcc; the cdylib for ctypes |

The P4's ABI is the ESP-IDF build's: its compile flags are
`-march=rv32imafc_zicsr_zifencei_xesppie -mabi=ilp32f`, Rust's target is
`rv32imafc` with ABI `ilp32f`. Floats travel in `fa0`/`fa1` on both sides.

## Install

```bash
rustup target add riscv32imafc-unknown-none-elf i686-unknown-linux-gnu wasm32-unknown-emscripten
rustup toolchain install nightly --profile minimal -c miri -c rust-src   # Miri, sanitizers
# the Xtensa fork: espup v0.17.1 (a release binary), user-level under ~/.rustup
espup install --targets esp32s3 --export-file "$PWD/export-esp.sh"
```

`espup` installs Espressif's rustc as the `esp` toolchain, plus its own GCC and
libclang that a staticlib build does not use (rust + rust-src are the part that
matters). It asks GitHub for the newest fork release unless given
`--toolchain-version`. The first `cargo +esp` build downloads the standard
library's lockfile dependencies from crates.io, once.

## Build and link

```bash
experiments/rust_probe/build.sh s3        # prints the library to link; also p4 wasm host64 host32 so
experiments/rust_probe/firmware.sh lilygo_t_deck_plus_mainline "$(experiments/rust_probe/build.sh s3)"
experiments/rust_probe/firmware.sh esp32_p4_wifi6_touch_lcd_7b "$(experiments/rust_probe/build.sh p4)"
make unix-micropython && experiments/rust_probe/unix.sh
firmware/web_runner/build.sh && experiments/rust_probe/web.sh
experiments/rust_probe/host.sh
```

`firmware.sh` appends one `include()` line to the board's tracked
`native/micropython.cmake` for the build and restores the file on exit. The
usermod takes the library from `$MOY_RS_PROBE_LIB`. The image delta is the
board's `App image:` line with and without it.

The profile is `panic = "abort"`, `opt-level = "s"`, `lto`, `codegen-units = 1`.
A panic is `abort()`: SIGABRT on the host, a trap in the browser, the crash path
on a board.

## What the probe found

- **compiler_builtins is a hazard in the archive.** A `staticlib` bundles it, and
  it defines the C library's names (`sinf`, `sqrtf`, `fmod`, `memcpy`, `strlen`,
  and on bare metal the whole libm) as WEAK symbols. A weak definition still
  counts for archive extraction, so when the archive precedes libm in the link
  the linker takes Rust's port and never opens the board's libm: about forty
  newlib functions changed provider on both ESP-IDF targets, `strlen` and
  `memcmp` on the browser build and `sqrtf` on the unix one. `drop_builtins.sh`
  removes the `compiler_builtins-*` members and only those: on the riscv32 and
  x86 targets the archive also bundles compiler-rt's C objects under hashed
  names (`<hash>-popcountsi2.o`, strong definitions, soft-float on riscv32), and
  the unix build here still took `__popcountdi2` from one. The deleted Rust
  twin's build script kept only the crate's own object, and its link-map guard
  failed any image whose C library names came from a Rust object. Localizing the
  weak symbols with
  `objcopy` also works on ELF, and cannot be done on wasm (`llvm-objcopy` only
  edits sections).
- **The hosted targets need `rust_eh_personality`.** Their prebuilt
  `compiler_builtins` is built for unwinding and its tables name the routine;
  `lib.rs` defines an empty one. The bare-metal targets' prebuilt core is
  abort-built and does not.
- **`-Zbuild-std=core` is the Xtensa way**: no prebuilt core exists for the
  target. The other targets use the prebuilt one under the same profile.
- **`cargo test` needs `--lib`**: without it cargo also builds the crate with
  unwinding panics and a `no_std` crate cannot.
- **Tests do not see the bug**: the out-of-bounds read passes `cargo test`; Miri
  rejects it and AddressSanitizer reports it across the C boundary.
- **Float code differs between twins.** GCC contracts `a * b + c` into
  `madd.s`/`fmadd.s` by default; rustc never does. A C twin that must match Rust
  bit for bit on a float path builds with `-ffp-contract=off`.
- **rustc's LLVM is older than emcc's** (22 against 24). It links because a
  `no_std` staticlib holds wasm object code, not bitcode; `-C linker-plugin-lto`
  or embedded bitcode in the archive would couple the two versions.
  `firmware/web_runner/build.sh` holds its emsdk to `EMSDK_VERSION`, so the
  emcc the Rust object links under is the pinned one.
