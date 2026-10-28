#!/usr/bin/env bash
# Link the Rust probe into the unix MicroPython builds -- the 64-bit one the
# ctypes/parity tests use and the 32-bit REPR_C one -- under their own BUILD
# directories (build-rsprobe, build-rsprobe-r32), beside `make unix-micropython`'s.
# Run `make unix-micropython` first: it clones and prepares the tree.
#
#   experiments/rust_probe/unix.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
UM="${REPO}/.build/unix_micropython"
MP="${UM}/micropython/ports/unix"
# The link is removed on exit: `make unix-micropython` builds every module in
# usermods/ and this one stops the build without a library to link.
ln -sfn "${HERE}/usermod/moy_rsprobe" "${UM}/usermods/moy_rsprobe"
trap 'rm -f "${UM}/usermods/moy_rsprobe"' EXIT
L64="$("${HERE}/build.sh" host64)"
L32="$("${HERE}/build.sh" host32)"
J="$(nproc)"
make -C "${MP}" VARIANT=standard MICROPY_PY_SSL=0 MICROPY_PY_FFI=0 BUILD=build-rsprobe \
  CFLAGS_EXTRA=-DMICROPY_PY_DEFLATE_COMPRESS=1 USER_C_MODULES="${UM}/usermods" \
  MOY_RS_PROBE_LIB="${L64}" -j"${J}"
make -C "${MP}" VARIANT=standard MICROPY_PY_SSL=0 MICROPY_PY_FFI=0 MICROPY_PY_BTREE=0 \
  MICROPY_FORCE_32BIT=1 BUILD=build-rsprobe-r32 \
  CFLAGS_EXTRA="-DMICROPY_PY_DEFLATE_COMPRESS=1 -DMICROPY_OBJ_REPR=MICROPY_OBJ_REPR_C -DMICROPY_FLOAT_IMPL=MICROPY_FLOAT_IMPL_FLOAT" \
  USER_C_MODULES="${UM}/usermods" MOY_RS_PROBE_LIB="${L32}" -j"${J}"
for b in build-rsprobe build-rsprobe-r32; do
  echo "import moy_rsprobe as r; print(r.probe(41), r.u64(1000,7), r.f32(2.0,3.0), r.atomic(5), r.sum(b'abc'))" \
    | "${MP}/${b}/micropython" | tail -1
done
