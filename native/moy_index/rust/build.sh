#!/usr/bin/env bash
# The Rust twin's static library for one target, compiler_builtins dropped; the
# last line printed is its path (tools/moy_index_spike.py's hook).
#
#   native/moy_index/rust/build.sh TARGET
#   native/moy_index/rust/build.sh test|miri  the crate's own tests: cargo test, Miri
#   native/moy_index/rust/build.sh fuzz SECONDS CORPUS
#                    fuzz/'s target under cargo-fuzz (`cargo install cargo-fuzz`)
#
# TARGET           Rust target                     toolchain        opt-level
# host             x86_64-unknown-linux-gnu        upstream         s
# host-sanitize    the same, AddressSanitizer      RUST_NIGHTLY     1, checked
# host-fuzz        host-sanitize with libFuzzer's  RUST_NIGHTLY     1, checked
#                  coverage (what cargo-fuzz passes)
# host-r32         i686-unknown-linux-gnu          upstream         s
# esp32s3          xtensa-esp32s3-none-elf         Espressif's fork 2
# esp32p4          riscv32imafc-unknown-none-elf   upstream         2
# web              wasm32-unknown-emscripten       upstream         s
#
# The opt-level is the C twin's on the same target: ESP-IDF's -O2 on the boards
# (CONFIG_COMPILER_OPTIMIZATION_PERF), MicroPython's -Os on the desktop and in
# the browser. host-sanitize also turns on overflow checks and debug
# assertions: Rust has no UndefinedBehaviorSanitizer, and Miri is `test`'s.
#
# THE DROP. A staticlib bundles compiler_builtins, which defines the C
# library's names (memcpy, strlen, sinf, the bare-metal targets' whole libm) as
# weak symbols, and on the riscv32 and x86 targets also compiler-rt's C objects
# (`<hash>-popcountsi2.o`, `__divdc3`, ... as strong ones). A definition
# extracts an archive member, so with the library ahead of libc, libm and
# libgcc in a link -- a usermod's always is -- Rust's copies replace the
# board's: about forty functions on an ESP-IDF image. Every member but the
# crate's own object (one, under `lto`) is deleted here, the target's own
# libgcc/libc/libm answer every reference, and a name only Rust has is a link
# error instead.
# tools/link_providers.py is the guard, on every image's link map;
# MOY_INDEX_RUST_KEEP_BUILTINS=1 skips the drop, for proving that it fires.
#
# The toolchains are pinned: upstream in rust-toolchain.toml, the other two
# below, and tools/rust_tree.sh puts each at its pin before it is used.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../../.." && pwd)"

RUST_ESP=1.97.0.0               # Espressif's fork, by espup: the S3s
RUST_NIGHTLY=nightly-2026-10-05 # Miri, -Zsanitizer=address
RUST_UPSTREAM="$(sed -n 's/^channel = "\(.*\)"$/\1/p' "${HERE}/rust-toolchain.toml")"
TREE="${REPO}/tools/rust_tree.sh"

CALLER="$(pwd)"
cd "${HERE}"
target="${1:-}"
export CARGO_TARGET_DIR="${REPO}/.build/moy_index_rust/${target}"
if [ "${target}" = "test" ]; then
  bash "${TREE}" "${RUST_UPSTREAM}" >&2
  exec cargo "+${RUST_UPSTREAM}" test --lib --quiet
fi
if [ "${target}" = "miri" ]; then
  bash "${TREE}" "${RUST_NIGHTLY}" --component miri --component rust-src >&2
  exec cargo "+${RUST_NIGHTLY}" miri test --lib --quiet
fi
if [ "${target}" = "fuzz" ]; then
  bash "${TREE}" "${RUST_NIGHTLY}" --component rust-src >&2
  corpus="$3"
  [ "${corpus#/}" != "${corpus}" ] || corpus="${CALLER}/${corpus}"
  mkdir -p "${corpus}"
  exec cargo "+${RUST_NIGHTLY}" fuzz run api "${corpus}" -- -max_total_time="$2" \
    -max_len=4096 -seed=1 -print_final_stats=1
fi

opt=2 cfg=() flags="" tc="+${RUST_UPSTREAM}" build_std=()
case "${target}" in
  host) triple=x86_64-unknown-linux-gnu opt='"s"' ar=ar ;;
  host-sanitize)
    triple=x86_64-unknown-linux-gnu opt=1 ar=ar tc="+${RUST_NIGHTLY}"
    flags="-Zsanitizer=address -Cforce-frame-pointers=yes"
    cfg=(--config profile.release.debug=true
         --config profile.release.overflow-checks=true
         --config profile.release.debug-assertions=true) ;;
  host-fuzz)
    triple=x86_64-unknown-linux-gnu opt=1 ar=ar tc="+${RUST_NIGHTLY}"
    flags="-Zsanitizer=address -Cforce-frame-pointers=yes -Cpasses=sancov-module"
    for o in level=4 inline-8bit-counters pc-table trace-compares; do
      flags="${flags} -Cllvm-args=-sanitizer-coverage-${o}"
    done
    cfg=(--config profile.release.debug=true
         --config profile.release.overflow-checks=true
         --config profile.release.debug-assertions=true) ;;
  host-r32) triple=i686-unknown-linux-gnu opt='"s"' ar=ar ;;
  esp32s3)
    triple=xtensa-esp32s3-none-elf tc=+esp build_std=(-Zbuild-std=core,alloc)
    ar="$(ls -d "${HOME}"/.espressif/tools/xtensa-esp-elf/*/xtensa-esp-elf/bin/xtensa-esp32s3-elf-ar | tail -1)" ;;
  esp32p4)
    triple=riscv32imafc-unknown-none-elf
    ar="$(ls -d "${HOME}"/.espressif/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-ar | tail -1)" ;;
  web)
    triple=wasm32-unknown-emscripten opt='"s"'
    ar="${REPO}/firmware/web_runner/.build/emsdk/upstream/bin/llvm-ar" ;;
  *) echo "usage: build.sh host|host-sanitize|host-fuzz|host-r32|esp32s3|esp32p4|web|test|miri|fuzz" >&2; exit 2 ;;
esac

case "${tc}" in
  +esp) bash "${TREE}" "esp@${RUST_ESP}" >&2 ;;
  "+${RUST_NIGHTLY}") bash "${TREE}" "${RUST_NIGHTLY}" --component rust-src >&2 ;;
  *) bash "${TREE}" "${RUST_UPSTREAM}" --target "${triple}" >&2 ;;
esac

keep="${MOY_INDEX_RUST_KEEP_BUILTINS:-}"
dir="${CARGO_TARGET_DIR}"
out="${dir}/out${keep:+-builtins}"
RUSTFLAGS="${flags}" \
  cargo "${tc}" rustc --quiet --release --target "${triple}" --crate-type staticlib \
    "${build_std[@]}" --config "profile.release.opt-level=${opt}" "${cfg[@]}" >&2
mkdir -p "${out}"
cp "${dir}/${triple}/release/libmoy_index_rs.a" "${out}/libmoy_index_rs.a"
if [ -z "${keep}" ]; then
  mapfile -t members < <("${ar}" t "${out}/libmoy_index_rs.a" | grep -v '^moy_index_rs-.*\.rcgu\.o$' || true)
  [ "${#members[@]}" -eq 0 ] || "${ar}" d "${out}/libmoy_index_rs.a" "${members[@]}"
  echo "dropped ${#members[@]} members (compiler_builtins and compiler-rt)" >&2
else
  echo "MOY_INDEX_RUST_KEEP_BUILTINS: compiler_builtins kept" >&2
fi
echo "${out}/libmoy_index_rs.a"
