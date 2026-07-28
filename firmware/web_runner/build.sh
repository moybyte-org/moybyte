#!/usr/bin/env bash
# Moybyte WEB RUNNER build (#151): the shared runtime/ console compiled for the
# browser -- MicroPython's `webassembly` port (a custom `moybyte` variant) + the
# staged console modules + a cart bundle + the driver page, assembled into a
# fully STATIC dist/ (no per-visitor server process; host it anywhere).
#
# Follows the board build.sh pattern: toolchain cloned into .build/ (gitignored),
# canonical module sources staged as COPIES from runtime/ every build, output to
# dist/. Unlike the boards this needs no ESP-IDF -- just emsdk (auto-installed
# here) and node (for the harness).
#
#   ./build.sh              # stage + FROZEN build + assemble dist/ (the ship
#                           # shape: the console is frozen bytecode inside the
#                           # wasm -- no modules.json fetch, no in-browser
#                           # compile of the 2MB source tree)
#   ./build.sh --stage-only # re-stage modules/carts/page only (fast dev loop):
#                           # dist/ gains modules.json and the page loads the
#                           # staged sources into the VFS, which SHADOWS the
#                           # frozen copies (sys.path puts /modules first) --
#                           # so runtime/ edits are testable without a rebuild
#
# Variant notes (variant/mpconfigvariant.*, copied into the port):
#   - pyscript-shaped: GC_SPLIT_HEAP_AUTO -> collections defer to the JS<->Python
#     call boundary (frame boundary for us), which removes the
#     emscripten_scan_registers dependency -> NO -s ASYNCIFY (it instruments every
#     wasm function; dropping it halves the .wasm and speeds the whole VM).
#   - no periodic mp_js_hook (node REPL Ctrl-C plumbing; costs a call / 10 VM ops).
# Port patches applied idempotently below (upstream v1.28.0 vs emscripten 6.x):
#   - Makefile: two -Wno-* (new clang warnings tripping -Werror) + -Os on the
#     LINK step (emcc defaults the JS glue to -O0 + ASSERTIONS, and the
#     assertions abort on the port's own patterns).
#   - library.js: mp_hal_get_interrupt_char ccall passed 1 arg to a 0-arg C
#     function (node-only path; assertions abort).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export REPO_ROOT   # read by the python heredocs below
BUILD_DIR="${SCRIPT_DIR}/.build"
DIST_DIR="${SCRIPT_DIR}/dist"
MPY_DIR="${BUILD_DIR}/micropython"
EMSDK_DIR="${BUILD_DIR}/emsdk"
PORT_DIR="${MPY_DIR}/ports/webassembly"
STAGE_DIR="${BUILD_DIR}/stage"
MPY_TAG="${MPY_TAG:-v1.28.0}"

PY="${REPO_ROOT}/.venv/bin/python"
[ -x "${PY}" ] || PY=python3

STAGE_ONLY=0
[ "${1:-}" = "--stage-only" ] && STAGE_ONLY=1

# ---------------------------------------------------------------------------
# 1. Toolchain (skipped with --stage-only): emsdk + micropython clone + patches.
# ---------------------------------------------------------------------------
if [ "${STAGE_ONLY}" = "0" ]; then
  mkdir -p "${BUILD_DIR}"
  if [ ! -d "${EMSDK_DIR}" ]; then
    echo "== installing emsdk (one-time, ~1GB)"
    git clone --quiet https://github.com/emscripten-core/emsdk.git "${EMSDK_DIR}"
    (cd "${EMSDK_DIR}" && ./emsdk install latest && ./emsdk activate latest)
  fi
  if [ ! -d "${MPY_DIR}" ]; then
    # Prefer the P4 target's existing local checkout (same tag) to skip the network.
    P4_MPY="${REPO_ROOT}/firmware/esp32_p4_wifi6_touch_lcd_7b/.build/micropython"
    if [ -d "${P4_MPY}" ]; then
      echo "== cloning micropython ${MPY_TAG} (local, from the P4 checkout)"
      git clone --quiet "${P4_MPY}" "${MPY_DIR}"
    else
      echo "== cloning micropython ${MPY_TAG}"
      git clone --depth 1 -b "${MPY_TAG}" --quiet \
        https://github.com/micropython/micropython "${MPY_DIR}"
    fi
    (cd "${MPY_DIR}" && git submodule update --init lib/micropython-lib --quiet)
  fi

  # Port patches (idempotent -- grep-guarded).
  MK="${PORT_DIR}/Makefile"
  if ! grep -q 'Wno-unused-but-set-global' "${MK}"; then
    sed -i 's/^CFLAGS += -std=c99 -Wall -Werror -Wdouble-promotion -Wfloat-conversion$/& -Wno-unused-but-set-global -Wno-unused-but-set-variable/' "${MK}"
  fi
  if ! grep -q '^JSFLAGS += -Os$' "${MK}"; then
    sed -i 's|^JSFLAGS += -s EXPORTED_FUNCTIONS="\\$|JSFLAGS += -Os\nJSFLAGS += -s EXPORTED_FUNCTIONS="\\|' "${MK}"
  fi
  "${PY}" - "${PORT_DIR}/library.js" <<'PYEOF'
import sys
p = sys.argv[1]
src = open(p).read()
bad = '"mp_hal_get_interrupt_char",\n                "number",\n                ["number"],\n                ["null"],'
good = '"mp_hal_get_interrupt_char",\n                "number",\n                [],\n                [],'
if bad in src:
    open(p, "w").write(src.replace(bad, good))
PYEOF

  # The moybyte variant (canonical copy lives in variant/, re-staged every build).
  mkdir -p "${PORT_DIR}/variants/moybyte"
  cp "${SCRIPT_DIR}/variant/"* "${PORT_DIR}/variants/moybyte/"

  # moy_lua usermod (#67, third architecture): stage the vendored VM + bridge
  # from the T-Deck native tree (single source of truth) and drop the Makefile
  # fragment in as micropython.mk (the boards use the cmake twin).
  USERMODS_DIR="${BUILD_DIR}/usermods"
  rm -rf "${USERMODS_DIR}"
  mkdir -p "${USERMODS_DIR}"
  cp -r "${REPO_ROOT}/firmware/lilygo_t_deck_plus_micropython/native/moy_lua" \
        "${USERMODS_DIR}/moy_lua"
  cp "${SCRIPT_DIR}/moy_lua_micropython.mk" "${USERMODS_DIR}/moy_lua/micropython.mk"

fi

# ---------------------------------------------------------------------------
# 2. Stage the shared console modules (canonical sources: runtime/), the same
#    copy-don't-import pattern both boards use. Denylist = host-only files and
#    the CPython-only palette.py (a literal twin is GENERATED below).
# ---------------------------------------------------------------------------
echo "== staging runtime/ modules"
rm -rf "${STAGE_DIR}"
mkdir -p "${STAGE_DIR}/modules"
DENY="host_app.py wm_windowed.py lua_host.py palette.py font.py __init__.py"
for f in "${REPO_ROOT}/runtime/"*.py; do
  base="$(basename "${f}")"
  skip=0
  for d in ${DENY}; do [ "${base}" = "${d}" ] && skip=1; done
  [ "${skip}" = "1" ] || cp "${f}" "${STAGE_DIR}/modules/${base}"
done
# font.py stages as moy_font.py (the boards' name for it -- canvas.py's staged-tree
# import path expects it).
cp "${REPO_ROOT}/runtime/font.py" "${STAGE_DIR}/modules/moy_font.py"
# The shared moy_lua cart-runtime glue (#67), staged from the T-Deck modules
# tree like the P4 does -- canvas-agnostic, works over the CommandCanvas.
cp "${REPO_ROOT}/firmware/lilygo_t_deck_plus_micropython/modules/moy_lua_glue.py" \
   "${STAGE_DIR}/modules/moy_lua_glue.py"
cp "${SCRIPT_DIR}/web_boot.py" "${STAGE_DIR}/modules/web_boot.py"

# palette.py: runtime/palette.py builds its HSV ramp with CPython's colorsys, so
# generate a LITERAL twin (parity by construction -- same table object).
"${PY}" - "${STAGE_DIR}/modules/palette.py" <<'PYEOF'
import sys, os
sys.path.insert(0, os.environ["REPO_ROOT"])
from runtime import palette as p
out = ['"""MOY64 palette -- GENERATED by web_runner/build.sh from runtime/palette.py',
       '(which needs CPython colorsys for its HSV ramp). Do not edit."""', ""]
out.append("MOY64 = %r" % (list(p.MOY64),))
out.append("")
out.append("NAMES = %r" % (p.NAMES,))
out.append('''

def color(name_or_index):
    """Resolve a color name or index to a 0-63 palette index."""
    if isinstance(name_or_index, str):
        return NAMES.get(name_or_index, 7)
    return int(name_or_index) & 63


def rgb888_table(palette=MOY64):
    """Flat bytes table [r,g,b]*len(palette) for fast index->RGB resolution."""
    out = bytearray(len(palette) * 3)
    for i, (r, g, b) in enumerate(palette):
        out[i * 3] = r
        out[i * 3 + 1] = g
        out[i * 3 + 2] = b
    return bytes(out)
''')
open(sys.argv[1], "w").write("\n".join(out))
PYEOF

# ---------------------------------------------------------------------------
# 3. The cart bundle: the runner's shelf roster packed as one JSON
#    {"<cart>.moy/<relpath>": text}. Text files only; thumbs/ (a regenerable
#    binary cache) is skipped. Lua twins stay out until the wasm Lua VM lands.
# ---------------------------------------------------------------------------
echo "== packing carts"
ROSTER="${MOYBYTE_WEB_CARTS:-star_catcher.moy sakura.moy tap_red.moy bubble_trouble.moy coin_quest.moy platformer.moy tiny_runner.moy battle_city.moy letter_blitz.moy scroll_demo.moy sakura_lua.moy ray_lua.moy moy_night.moy}"
"${PY}" - "${REPO_ROOT}/system_carts" "${STAGE_DIR}/carts.json" ${ROSTER} <<'PYEOF'
import json, os, sys
root, out = sys.argv[1], sys.argv[2]
bundle = {}
for cart in sys.argv[3:]:
    src = os.path.join(root, cart)
    if not os.path.isdir(src):
        print("  !! missing cart:", cart)
        continue
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in ("thumbs", "__pycache__")]
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            rel = cart + "/" + os.path.relpath(p, src).replace(os.sep, "/")
            try:
                bundle[rel] = open(p, encoding="utf-8").read()
            except UnicodeDecodeError:
                print("  !! skipping binary file:", rel)
json.dump(bundle, open(out, "w"))
print("  %d files, %d carts" % (len(bundle), len(sys.argv) - 3))
PYEOF

# modules.json: one fetch for the whole staged module tree (bring-up loads the
# console from VFS; the frozen-manifest ship build is a later phase).
"${PY}" - "${STAGE_DIR}/modules" "${STAGE_DIR}/modules.json" <<'PYEOF'
import json, os, sys
d, out = sys.argv[1], sys.argv[2]
mods = {fn: open(os.path.join(d, fn), encoding="utf-8").read()
        for fn in sorted(os.listdir(d)) if fn.endswith(".py")}
json.dump(mods, open(out, "w"))
print("  %d modules, %.1f KB" % (len(mods), sum(len(v) for v in mods.values()) / 1024))
PYEOF

# ---------------------------------------------------------------------------
# 4. The driver page: the shared replayer core (runtime/web_view_page.PAGE_CORE)
#    + the wasm transport tail (page_tail.js) + the module-script loader.
# ---------------------------------------------------------------------------
echo "== generating index.html"
REPO_ROOT="${REPO_ROOT}" "${PY}" - "${SCRIPT_DIR}/page_tail.js" "${STAGE_DIR}/index.html" <<'PYEOF'
import os, sys
sys.path.insert(0, os.environ["REPO_ROOT"])
from runtime.web_view_page import PAGE_CORE
tail = open(sys.argv[1], encoding="utf-8").read()
open(sys.argv[2], "w", encoding="utf-8").write(
    PAGE_CORE + tail + "\n</script></body></html>")
PYEOF

# ---------------------------------------------------------------------------
# 5. The FROZEN build (skipped with --stage-only): freeze the staged console
#    into the wasm as bytecode -- the T-Deck manifest pattern (opt=3), pointed
#    at the stage dir via a generated manifest. This is why staging runs first.
# ---------------------------------------------------------------------------
if [ "${STAGE_ONLY}" = "0" ]; then
  cat > "${BUILD_DIR}/frozen_manifest.py" <<EOF
# GENERATED by web_runner/build.sh -- freezes the staged console (see stage/).
freeze("${STAGE_DIR}/modules", opt=3)
EOF
  echo "== building MicroPython webassembly (moybyte variant + moy_lua, frozen console)"
  # shellcheck disable=SC1091
  source "${EMSDK_DIR}/emsdk_env.sh" >/dev/null 2>&1
  make -C "${MPY_DIR}/mpy-cross" -j"$(nproc)" >/dev/null
  make -C "${PORT_DIR}" VARIANT=moybyte USER_C_MODULES="${USERMODS_DIR}" \
    FROZEN_MANIFEST="${BUILD_DIR}/frozen_manifest.py" -j"$(nproc)" >/dev/null
fi

# ---------------------------------------------------------------------------
# 6. Assemble dist/ (static -- host anywhere; python -m http.server works).
#    modules.json ships ONLY from --stage-only (the dev VFS override); a full
#    frozen build removes it so the page runs the frozen console.
# ---------------------------------------------------------------------------
mkdir -p "${DIST_DIR}"
cp "${STAGE_DIR}/carts.json" "${STAGE_DIR}/index.html" "${DIST_DIR}/"
if [ "${STAGE_ONLY}" = "1" ]; then
  cp "${STAGE_DIR}/modules.json" "${DIST_DIR}/"
else
  cp "${PORT_DIR}/build-moybyte/micropython.mjs" "${DIST_DIR}/"
  cp "${PORT_DIR}/build-moybyte/micropython.wasm" "${DIST_DIR}/"
  rm -f "${DIST_DIR}/modules.json"
fi
echo "== dist/ ready:"
ls -la "${DIST_DIR}"
