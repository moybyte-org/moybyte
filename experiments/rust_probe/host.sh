#!/usr/bin/env bash
# The host checks: the cdylib through ctypes, the unit tests, Miri, and
# AddressSanitizer over the C ABI. Needs `rustup toolchain install nightly
# --profile minimal -c miri -c rust-src`.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${HERE}/crate"

SO="$("${HERE}/build.sh" so)"
python3 - "${SO}" <<'PY'
import ctypes, sys
lib = ctypes.CDLL(sys.argv[1])
assert lib.moy_rs_probe(41) == 124
print("ctypes: ok")
PY

cargo test --release --lib
cargo +nightly miri test --lib
# The deliberate bug: Miri must reject it, ASan must report it.
if cargo +nightly miri test --lib --features bug 2>/dev/null; then
  echo "miri missed the bug" >&2; exit 1
fi
echo "miri: caught the bug"

RUSTFLAGS="-Zsanitizer=address" cargo +nightly rustc --release --features bug \
  --target x86_64-unknown-linux-gnu --crate-type staticlib --target-dir target-asan
H="$(mktemp)"
trap 'rm -f "${H}"' EXIT
cc -fsanitize=address -g "${HERE}/host/asan_harness.c" \
  target-asan/x86_64-unknown-linux-gnu/release/libmoy_rs_probe.a -o "${H}"
"${H}"
if "${H}" oob >/dev/null 2>&1; then
  echo "asan missed the bug" >&2; exit 1
fi
echo "asan: caught the bug"
