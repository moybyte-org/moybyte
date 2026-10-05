#!/usr/bin/env bash
# Link the Rust probe into the browser build: the web runner's own make line
# (firmware/web_runner/build.sh) under BUILD=build-rsprobe, with moy_rsprobe
# staged beside the other usermods, then run it in the emsdk's node.
# Run firmware/web_runner/build.sh first: it clones and stages the tree.
#
#   experiments/rust_probe/web.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
WR="${REPO}/firmware/web_runner"
PORT="${WR}/.build/micropython/ports/webassembly"
LIB="$("${HERE}/build.sh" wasm)"
rm -rf "${WR}/.build/usermods/moy_rsprobe"
cp -r "${HERE}/usermod/moy_rsprobe" "${WR}/.build/usermods/moy_rsprobe"
trap 'rm -rf "${WR}/.build/usermods/moy_rsprobe" "${T:-}"' EXIT
# shellcheck disable=SC1091
source "${WR}/.build/emsdk/emsdk_env.sh" >/dev/null 2>&1
emcc --version | head -1
make -C "${PORT}" VARIANT=moybyte BUILD=build-rsprobe USER_C_MODULES="${WR}/.build/usermods" \
  FROZEN_MANIFEST="${WR}/.build/frozen_manifest.py" MOY_RS_PROBE_LIB="${LIB}" -j"$(nproc)"
NODE="$(ls -d "${WR}"/.build/emsdk/node/*/bin/node | head -1)"
T="$(mktemp --suffix=.mjs)"
cat > "${T}" <<JS
import { loadMicroPython } from "${PORT}/build-rsprobe/micropython.mjs";
const mp = await loadMicroPython({ stdout: (l) => console.log(l) });
await mp.runPythonAsync("import moy_rsprobe as r\nprint(r.probe(41), r.u64(1000,7), r.f32(2.0,3.0), r.atomic(5), r.sum(b'abc'))");
JS
"${NODE}" "${T}"
