#!/usr/bin/env bash
# Stage-4 spike, option (b): compile the VENDORED libmoy (indexed pixel
# format, the default) + bench.c to wasm with the web runner's own emsdk,
# then run under node. Emits bench_O2.js and bench_O3.js (classic non-module
# output so node auto-runs main()).
#
#   ./build.sh          # build O2 + O3, run both
#   ./build.sh --build  # build only
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$HERE/../.."
LIBMOY="$REPO/firmware/lilygo_t_deck_plus_micropython/native/moy_gfx/libmoy"
EMSDK="$REPO/firmware/web_runner/.build/emsdk"

source "$EMSDK/emsdk_env.sh" >/dev/null 2>&1
echo "emcc: $(emcc --version | head -1)"

SRCS="$HERE/bench.c $LIBMOY/moy_canvas.c $LIBMOY/moy_sprite.c $LIBMOY/moy_data.c"
FLAGS="-I$LIBMOY -sENVIRONMENT=node -sINITIAL_MEMORY=64MB -sALLOW_MEMORY_GROWTH=1"

emcc -O2 $SRCS $FLAGS -o "$HERE/bench_O2.js"
emcc -O3 $SRCS $FLAGS -o "$HERE/bench_O3.js"

if [ "$1" != "--build" ]; then
    echo "== -O2 =="
    node "$HERE/bench_O2.js"
    echo "== -O3 =="
    node "$HERE/bench_O3.js"
fi
