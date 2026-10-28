#!/usr/bin/env bash
# Build a board's firmware with the Rust probe linked in, never flashing it.
#
#   experiments/rust_probe/firmware.sh <firmware dir name> <path to libmoy_rs_probe.a>
#
# The board's tracked native/micropython.cmake gets one `include()` line for the
# duration of the build and is restored on exit, so no product file is left
# changed. The board's own build.sh does the rest.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
BOARD="$1"
LIB="$(readlink -f "$2")"
CMAKE="${REPO}/firmware/${BOARD}/native/micropython.cmake"
[ -f "${CMAKE}" ] || { echo "no ${CMAKE}" >&2; exit 2; }
[ -f "${LIB}" ] || { echo "no ${LIB}" >&2; exit 2; }
cp "${CMAKE}" "${CMAKE}.rust_probe_orig"
trap 'mv -f "${CMAKE}.rust_probe_orig" "${CMAKE}"' EXIT
echo "include(${HERE}/usermod/moy_rsprobe/micropython.cmake)" >> "${CMAKE}"
export MOY_RS_PROBE_LIB="${LIB}"
bash "${REPO}/firmware/${BOARD}/build.sh"
