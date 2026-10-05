#!/usr/bin/env bash
# Put an emsdk checkout at a version:   tools/emsdk_tree.sh DIR VERSION
#
# `emcc`'s output is not byte-reproducible across versions, and the browser
# console's bundle rides every board image (.claude/rules/web.md), so the
# emscripten a build compiles with is a property of the build, not of the day
# its machine first cloned emsdk. The web runner's build.sh names the version
# once (`EMSDK_VERSION`) and runs this on every full build:
#
#   * DIR absent  -- emsdk is cloned and VERSION installed and activated.
#   * DIR's active emscripten is VERSION -- nothing happens, silently.
#   * DIR anywhere else -- VERSION is installed and activated (a version the
#                    clone predates comes with a `git pull` of emsdk itself,
#                    tried once). Both moves are verified by reading the
#                    version back from the tree.
#   * VERSION cannot be had, or DIR is not an emsdk checkout -- exit 1 and say
#                    so. Compiling with whatever is there is the one thing
#                    never done.
#
# The active version is what `emsdk activate` leaves at
# DIR/upstream/emscripten/emscripten-version.txt, beside the `.emscripten` it
# writes. MOYBYTE_EMSDK_URL replaces upstream (the tests point it at a local
# repository holding a stub `emsdk`).

set -euo pipefail

UPSTREAM="${MOYBYTE_EMSDK_URL:-https://github.com/emscripten-core/emsdk.git}"

die() { echo "!! emsdk_tree: $*" >&2; exit 1; }

[ $# -eq 2 ] || die "usage: emsdk_tree.sh DIR VERSION"
dir="$1"; want="$2"

if [ ! -e "${dir}" ]; then
  echo "== cloning emsdk"
  git clone --quiet "${UPSTREAM}" "${dir}" || die "cannot clone ${UPSTREAM}"
fi
[ -x "${dir}/emsdk" ] || die "${dir} is not an emsdk checkout (no emsdk script; remove it to have it cloned)"

active() {
  local f="${dir}/upstream/emscripten/emscripten-version.txt"
  [ -f "${dir}/.emscripten" ] && [ -f "${f}" ] || return 0
  tr -d '"[:space:]' < "${f}"
}

have="$(active)"
[ "${have}" = "${want}" ] && exit 0

echo "== emsdk ${dir} is at ${have:-no activated emscripten}, not ${want}: installing ${want}"
if ! (cd "${dir}" && ./emsdk install "${want}"); then
  echo "== emsdk does not know ${want}: updating its checkout once"
  git -C "${dir}" pull --quiet --ff-only || true
  (cd "${dir}" && ./emsdk install "${want}") || die "cannot install emscripten ${want} in ${dir}"
fi
(cd "${dir}" && ./emsdk activate "${want}") || die "cannot activate emscripten ${want} in ${dir}"
have="$(active)"
[ "${have}" = "${want}" ] || die "${dir} is at ${have:-no emscripten}, not ${want}, after activating it"
