#!/usr/bin/env bash
# Build the probe staticlib for one target and print the path of the library to
# link (compiler_builtins already dropped, see drop_builtins.sh).
#
#   experiments/rust_probe/build.sh s3|p4|wasm|host64|host32
#   experiments/rust_probe/build.sh so        # the cdylib for ctypes
#
# s3   xtensa-esp32s3-none-elf    Espressif's fork (espup), -Zbuild-std=core
# p4   riscv32imafc-unknown-none-elf   upstream, prebuilt core (ilp32f, like IDF)
# wasm wasm32-unknown-emscripten  upstream; linked by the repo's emsdk
# host64, host32   x86_64 / i686 linux, for the unix MicroPython builds
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
cd "${HERE}/crate"
TARGET_AR=ar
case "$1" in
  s3)
    T=xtensa-esp32s3-none-elf
    cargo +esp rustc --release --target "${T}" --crate-type staticlib -Zbuild-std=core >&2
    TARGET_AR="$(ls -d "${HOME}"/.espressif/tools/xtensa-esp-elf/*/xtensa-esp-elf/bin/xtensa-esp32s3-elf-ar | tail -1)"
    ;;
  p4)
    T=riscv32imafc-unknown-none-elf
    cargo rustc --release --target "${T}" --crate-type staticlib >&2
    TARGET_AR="$(ls -d "${HOME}"/.espressif/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-ar | tail -1)"
    ;;
  wasm)
    T=wasm32-unknown-emscripten
    cargo rustc --release --target "${T}" --crate-type staticlib >&2
    TARGET_AR="$(ls -d "${REPO}"/firmware/web_runner/.build/emsdk/upstream/bin/llvm-ar)"
    ;;
  host64)
    T=x86_64-unknown-linux-gnu
    cargo rustc --release --target "${T}" --crate-type staticlib >&2
    ;;
  host32)
    T=i686-unknown-linux-gnu
    cargo rustc --release --target "${T}" --crate-type staticlib >&2
    ;;
  so)
    cargo rustc --release --crate-type cdylib >&2
    echo "${HERE}/crate/target/release/libmoy_rs_probe.so"
    exit 0
    ;;
  *) echo "usage: build.sh s3|p4|wasm|host64|host32|so" >&2; exit 2 ;;
esac
OUT="${HERE}/crate/target/${T}/release"
"${HERE}/drop_builtins.sh" "${OUT}/libmoy_rs_probe.a" "${OUT}/libmoy_rs_probe_nobi.a" "${TARGET_AR}" >&2
echo "${OUT}/libmoy_rs_probe_nobi.a"
