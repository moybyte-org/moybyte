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

# This build is Moybyte's own browser console and nothing else's (the spec repo
# builds its own player from libmoy -- CLAUDE.md's web-runner section).
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
case "${1:-}" in
  --stage-only) STAGE_ONLY=1 ;;
esac

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
  fi
  # OUTSIDE the clone guard on purpose. Two reasons, both learned from the first
  # Pages deploy: `--quiet` has to precede the path (after it, git reads it as a
  # second pathspec and dies with "pathspec '--quiet' did not match"), and a
  # checkout can exist WITHOUT its submodule -- which is exactly what that failed
  # run left in the CI cache, since the clone succeeded and only this line broke.
  # Guarding it meant a half-built .build/ could never repair itself. It is a
  # no-op once the submodule is there, so running it every build costs nothing.
  (cd "${MPY_DIR}" && git submodule update --init --quiet lib/micropython-lib)

  # Port patches (idempotent -- grep-guarded).
  MK="${PORT_DIR}/Makefile"
  if ! grep -q 'Wno-unused-but-set-global' "${MK}"; then
    sed -i 's/^CFLAGS += -std=c99 -Wall -Werror -Wdouble-promotion -Wfloat-conversion$/& -Wno-unused-but-set-global -Wno-unused-but-set-variable/' "${MK}"
  fi
  if ! grep -q '^JSFLAGS += -Os$' "${MK}"; then
    sed -i 's|^JSFLAGS += -s EXPORTED_FUNCTIONS="\\$|JSFLAGS += -Os\nJSFLAGS += -s EXPORTED_FUNCTIONS="\\|' "${MK}"
  fi
  # A usermod cannot silence -Wunknown-pragmas by itself: py.mk folds
  # CFLAGS_USERMOD into CFLAGS at its include (line ~32) and the port appends
  # its own -Wall AFTER that (line ~48), which re-enables the warning -- and
  # -Werror makes it fatal. moy_gfx's vendored kernels carry in-source
  # `#pragma GCC optimize("O3")` pins that clang parses and ignores, so the
  # suppression has to land here, after the -Wall. (moy_lua's own O2 pragmas
  # survive only because -Wignored-pragma-optimize is on by DEFAULT rather than
  # via -Wall, so its usermod-level -Wno- is not re-enabled.)
  if ! grep -q 'moybyte usermod pragma suppressions' "${MK}"; then
    sed -i 's|^CFLAGS += -Os -DNDEBUG$|# moybyte usermod pragma suppressions (see web_runner/build.sh)\nCFLAGS += -Wno-unknown-pragmas\n&|' "${MK}"
  fi
  # HEAPU8: how the finished FRAMEBUFFER leaves the VM (moycore stage 4). The
  # worker reads the canvas buffer straight out of the wasm heap by address, so
  # a painted frame costs one memcpy and no Python-side serialisation.
  #
  # It has to be PATCHED IN rather than passed as EXPORTED_RUNTIME_METHODS_EXTRA
  # on the make command line: the port sets that variable itself with `+=`, and
  # a command-line assignment overrides the makefile's entirely -- which silently
  # unexported getValue/setValue/UTF8ToString and broke the VM's own JS wrapper
  # at boot ("Module.getValue is not a function").
  if ! grep -q 'HEAPU8' "${MK}"; then
    sed -i 's|^EXPORTED_RUNTIME_METHODS_EXTRA += ,\\$|&\n\tHEAPU8,\\|' "${MK}"
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
  cp -r "${REPO_ROOT}/native/moy_lua" \
        "${USERMODS_DIR}/moy_lua"
  cp "${SCRIPT_DIR}/moy_lua_micropython.mk" "${USERMODS_DIR}/moy_lua/micropython.mk"

  # moy_audio usermod (#170, #97): libmoy -- moy-spec's own SPEC.md 8 synth --
  # plus its MicroPython binding, so the runner's audio IS the spec's C
  # implementation rather than a twin of it. Same source of truth as the T-Deck,
  # and unlike moy_lua it ships its OWN micropython.mk, so there is no fragment
  # for this script to supply.
  cp -r "${REPO_ROOT}/native/moy_audio" \
        "${USERMODS_DIR}/moy_audio"

  # moy_gfx usermod (moycore stage 4): the RASTER. The browser stopped being the
  # GPU here -- the wasm draws its own pixels with the SAME kernel the boards
  # run (modmoy_gfx.c + vendored libmoy at MOY_PIXEL_RGB565), and the page just
  # blits the finished framebuffer. Its ESP-IDF halves (async memcpy, membench)
  # are __has_include-guarded and simply compile out here, which is why this
  # needs no wasm fork of the module -- the same source builds for three
  # architectures plus the unix test build.
  #
  # Its stock micropython.mk (the unix twin of micropython.cmake) is used
  # as-is -- no runner-specific fragment, unlike moy_lua. The one clang
  # accommodation it needs is a warning suppression that has to outrank the
  # port's -Wall, so it lives in the Makefile patch above, not here.
  cp -r "${REPO_ROOT}/native/moy_gfx" \
        "${USERMODS_DIR}/moy_gfx"

  # moycore usermod (stage 2/3): the cart's WHOLE frame in C. The browser was
  # the last tier still running the trampoline registry -- ~40 Python closures
  # installed as Lua globals, several hundred upcalls a frame -- while both
  # boards and the host had moved to one upcall per frame. That is the drift
  # this project exists to prevent: celeste in a browser and celeste on a P4
  # would have been two different engines implementing the same spec.
  #
  # It ships its own micropython.mk (Makefile fragment) and needs no wasm
  # variant of it: the fragment compiles only modmoycore.c + libmoy's Lua
  # binding, and reaches its two SIBLINGS by relative path for the rest -- the
  # raster from moy_gfx's vendored libmoy, the VM from moy_lua's vendored Lua
  # 5.4. Both are staged directly above, so the sibling layout the boards get
  # from ext_mod/ holds here too. The board allocator it prefers is
  # __has_include-guarded on esp_heap_caps.h and compiles down to realloc.
  cp -r "${REPO_ROOT}/native/moycore" \
        "${USERMODS_DIR}/moycore"

fi

# ---------------------------------------------------------------------------
# 2. Stage the shared console modules (canonical sources: runtime/), the same
#    copy-don't-import pattern both boards use. Denylist = host-only files and
#    the CPython-only palette.py (a literal twin is GENERATED below).
#    wm_windowed.py IS staged, unlike on the S3: its only import is `time` (the
#    P4 freezes it too), so the browser can present BOTH tiers -- the handheld
#    320x240 and the windowed desktop (#73/#105).
# ---------------------------------------------------------------------------
echo "== staging runtime/ modules"
rm -rf "${STAGE_DIR}"
mkdir -p "${STAGE_DIR}/modules"
# WHAT crosses and WHY is declared in board.toml (#161 -- the last hand-rolled
# staging list to convert; the boards went in Phase 3). The stager applies the
# denylist over runtime/, the font.py -> moy_font.py rename, and the device/
# allowlist (moycore_glue / device_canvas / device_util), into
# .build/stage/modules per the file's `dest`. tests/test_staging_closure.py
# derives the web frozen set from that declaration, same as the boards'.
"${PY}" "${REPO_ROOT}/tools/board_config.py" stage "${SCRIPT_DIR}"
# The runner's own AUTHORED modules -- the analogue of a board's tracked
# modules/ files, copied by name because the stage dir is rebuilt from scratch.
cp "${SCRIPT_DIR}/web_boot.py" "${STAGE_DIR}/modules/web_boot.py"
cp "${SCRIPT_DIR}/web_canvas.py" "${STAGE_DIR}/modules/web_canvas.py"


# palette.py: runtime/palette.py builds its HSV ramp with CPython's colorsys, so
# generate a LITERAL twin (parity by construction -- same table object).
"${PY}" - "${STAGE_DIR}/modules/palette.py" <<'PYEOF'
import inspect, sys, os
sys.path.insert(0, os.environ["REPO_ROOT"])
from runtime import palette as p
out = ['"""MOY64 palette -- GENERATED by web_runner/build.sh from runtime/palette.py',
       '(which needs CPython colorsys for its HSV ramp). Do not edit."""', ""]
out.append("MOY64 = %r" % (list(p.MOY64),))
out.append("")
out.append("NAMES = %r" % (p.NAMES,))
out.append("")
# The verbs ride over VERBATIM (inspect.getsource) rather than as a second
# hand-typed copy of their bodies -- an edit to the canonical file propagates
# by construction.
out.append("")
out.append(inspect.getsource(p.color))
out.append("")
out.append(inspect.getsource(p.rgb888_table))
open(sys.argv[1], "w").write("\n".join(out))
PYEOF

# ---------------------------------------------------------------------------
# 3. The cart bundle: the runner's shelf roster packed as one JSON
#    {"<cart>.moy/<relpath>": text}. Text files only; thumbs/ (a regenerable
#    binary cache) is skipped. Lua twins stay out until the wasm Lua VM lands.
# ---------------------------------------------------------------------------
echo "== packing carts"
# The WALLPAPER carts (moy_night + ocean/open_machine/wallpaper_space) ride
  # along even though they never appear on the run-grid: they are the Appearance
  # app's CARTS catalog, and shipping only moy_night left that tab with one
  # choice (owner report 2026-07-31).
  ROSTER="${MOYBYTE_WEB_CARTS:-star_catcher.moy sakura.moy tap_red.moy harpoon_pop.moy coin_quest.moy platformer.moy tiny_runner.moy brick_siege.moy brick_siege_lua.moy letter_blitz.moy scroll_demo.moy sakura_lua.moy ray_lua.moy moy_night.moy ocean.moy open_machine.moy wallpaper_space.moy paint.moy files.moy writer.moy sheets.moy storybook.moy calc.moy theme_picker.moy}"
"${PY}" - "${REPO_ROOT}/system_carts" "${STAGE_DIR}/carts.json" ${ROSTER} <<'PYEOF'
import json, os, sys
root, out = sys.argv[1], sys.argv[2]
bundle = {}
n = 0
for cart in sys.argv[3:]:
    # A bare name resolves under system_carts/; anything with a separator is a
    # path.
    src = cart if os.sep in cart else os.path.join(root, cart)
    name = os.path.basename(src.rstrip("/"))
    if not os.path.isdir(src):
        print("  !! missing cart:", cart)
        continue
    n += 1
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in ("thumbs", "__pycache__")]
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            rel = name + "/" + os.path.relpath(p, src).replace(os.sep, "/")
            try:
                bundle[rel] = open(p, encoding="utf-8").read()
            except UnicodeDecodeError:
                print("  !! skipping binary file:", rel)
json.dump(bundle, open(out, "w"))
print("  %d files, %d carts" % (len(bundle), n))
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
# 4. The driver page: page_core.html (present + input + audio) + page_tail.js
#    (the worker transport + the module-script loader) -- plain files in this
#    directory.
# ---------------------------------------------------------------------------
echo "== generating index.html"
# @MOY_BUILD@ becomes worker.js's CONTENT HASH, which the page appends to the
# worker url. Browsers cache worker scripts hard enough that a hard reload of
# the document keeps an old one, and the tier/boot logic lives in that file --
# measured the slow way, with a board serving a correct console to a browser
# running the previous worker. Content-addressed so the url changes when, and
# only when, the worker does.
MOY_BUILD="$(sha1sum "${SCRIPT_DIR}/worker.js" | cut -c1-12)"
cat "${SCRIPT_DIR}/page_core.html" "${SCRIPT_DIR}/page_tail.js" \
  | sed "s/@MOY_BUILD@/${MOY_BUILD}/" > "${STAGE_DIR}/index.html"

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
  echo "== building MicroPython webassembly (moybyte variant + moy_lua/moy_audio/moy_gfx/moycore, frozen console)"
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
# worker.js is a SEPARATE file, not inlined: a module Worker needs its own URL.
# It owns the VM + the frame loop; the page only replays (#176 smoothness).
cp "${SCRIPT_DIR}/worker.js" "${DIST_DIR}/"
if [ "${STAGE_ONLY}" = "1" ]; then
  cp "${STAGE_DIR}/modules.json" "${DIST_DIR}/"
else
  cp "${PORT_DIR}/build-moybyte/micropython.mjs" "${DIST_DIR}/"
  cp "${PORT_DIR}/build-moybyte/micropython.wasm" "${DIST_DIR}/"
  rm -f "${DIST_DIR}/modules.json"
fi
# A PRE-GZIPPED copy beside each asset, for the BOARDS. They serve `<name>.gz`
# with `Content-Encoding: gzip` and never compress anything themselves, so the
# wire cost halves (1,155,953 B -> ~572,747 B) and the browser does the
# inflating. Both copies ship: a plain static host (moybyte.com, serve.py) sets
# no Content-Encoding, and a browser handed gzip bytes without that header sees
# garbage -- so raw has to stay the default and .gz has to be opt-in by a server
# that knows to advertise it.
# -n omits the mtime, so an unchanged asset produces a byte-identical .gz and
# the push tool's per-file compare stays meaningful.
for f in index.html worker.js micropython.mjs micropython.wasm; do
  if [ -f "${DIST_DIR}/${f}" ]; then
    gzip -9 -n -c "${DIST_DIR}/${f}" > "${DIST_DIR}/${f}.gz"
  fi
done
echo "== dist/ ready:"
ls -la "${DIST_DIR}"
