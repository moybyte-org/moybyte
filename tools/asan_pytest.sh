#!/bin/bash
# pytest over the host's one native library built under AddressSanitizer
# (runtime/host_native.py, MOYHOST_ASAN): a write into a freed or overrun
# glass buffer stops the run at the access, with where the memory was freed,
# instead of corrupting the heap for a later test to die on.
#
#   tools/asan_pytest.sh tests/test_notes_rows.py -x -q
#
# Reports land in .build/asan/ (pytest's capture would swallow them); the
# first one is printed when the run fails. Leaks are not checked: CPython
# keeps its own allocations to the end.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
CC=${CC:-cc}
ASAN_LIB=$("$CC" -print-file-name=libasan.so)
if [ ! -e "$ASAN_LIB" ]; then
  echo "asan_pytest: no libasan beside $CC" >&2
  exit 2
fi
OUT="$ROOT/.build/asan"
mkdir -p "$OUT"
rm -f "$OUT"/report.*
cd "$ROOT" || exit 2
MOYHOST_ASAN=1 LD_PRELOAD="$ASAN_LIB" \
  ASAN_OPTIONS="detect_leaks=0:abort_on_error=1:quarantine_size_mb=1024:log_path=$OUT/report" \
  .venv/bin/python -m pytest -p no:cacheprovider "$@"
rc=$?
first=$(ls "$OUT"/report.* 2>/dev/null | head -1)
if [ -n "$first" ]; then
  grep -v '^    #[0-9]* 0x[0-9a-f]* *(' "$first" | head -40
  echo "asan_pytest: full report $first" >&2
  [ "$rc" -eq 0 ] && rc=1
fi
exit "$rc"
