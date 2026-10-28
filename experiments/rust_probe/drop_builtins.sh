#!/usr/bin/env bash
# Delete compiler_builtins from the staticlib.
#
#   drop_builtins.sh <in.a> <out.a> <ar>
#
# The bundled compiler_builtins defines the C library's names (sinf, memcpy,
# strlen, ...) as WEAK symbols, and a weak definition still counts as a
# definition for archive extraction: an archive that precedes libc/libm in the
# link supplies them, and the target's own libm is never opened. With the
# members gone, the target's libgcc/libc/libm answer every reference the Rust
# objects make, and a name only Rust provides (a 128-bit divide on a 32-bit
# target) is a LINK ERROR, not a silent substitution.
set -euo pipefail
IN="$1"; OUT="$2"; AR="$3"
cp "${IN}" "${OUT}"
mapfile -t M < <("${AR}" t "${OUT}" | grep '^compiler_builtins-' || true)
[ "${#M[@]}" -gt 0 ] && "${AR}" d "${OUT}" "${M[@]}"
echo "dropped ${#M[@]} compiler_builtins members: ${IN} -> ${OUT}"
