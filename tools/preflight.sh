#!/usr/bin/env bash
# Run what CI runs, here, before pushing.
#
#   tools/preflight.sh          # the host job, in CI's own order
#   tools/preflight.sh --web    # ...plus the browser suites in real Chrome
#
# WHY THIS EXISTS. `make test` is not the CI job. It is the part that needs
# nothing but the venv, and the steps it leaves out are the ones that compare a
# DERIVED ARTIFACT against the sources it was built from. Those are exactly the
# steps that break when you change a source and forget the artifact, and that is
# a class rather than an incident -- on 2026-09-11 alone it cost a stale
# `runner/` in moy-spec (caught only by CI, after a push), a stale
# `unix-micropython` blob here (`make test` green until the web bundle was
# rebuilt under it), and a regenerated `ports/p8` set.
#
# THE ORDER IS LOAD-BEARING, which is the other half of why this is a script and
# not a list in a README:
#
#   * `make unix-micropython` must run AFTER any `firmware/web_runner/build.sh`,
#     because the binary bakes the web blob and the cached one is not rebuilt by
#     a bundle rebuild. Out of order, `tests/test_web_blob.py` fails with a size
#     mismatch that reads like a generator bug. (Its own assertion message says
#     so, which is how that afternoon ended.)
#   * the redraw tests run ALONE: they assert exact repaint counts against real
#     wall-clock deadlines, and the top-bar clock legitimately repaints on a
#     minute rollover, so a long shared run can straddle :00. CI splits them for
#     this reason; so does this.
#   * the headless tour gets a FRESH save dir. A warm `~/.moybyte/projects`
#     holds carts from whatever was seeded months ago -- one of them still calls
#     `spr_batch`, retired 2026-08-14 -- so the tour fails on this machine and
#     passes in CI, which starts from an empty checkout. That is the same stale
#     artifact in a third costume.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
cd "${ROOT}"

PY="${ROOT}/.venv/bin/python"
WEB=0
[ "${1:-}" = "--web" ] && WEB=1
TOUR_DIR="$(mktemp -d)"
trap 'rm -rf "${TOUR_DIR}"' EXIT

fails=0
step() {                        # step "name" cmd...
  local name="$1"; shift
  printf '== %s\n' "${name}"
  if "$@" > /tmp/preflight.$$ 2>&1; then
    printf '   ok\n'
  else
    printf '   FAIL\n'
    sed 's/^/   | /' /tmp/preflight.$$ | tail -25
    fails=$((fails + 1))
  fi
  rm -f /tmp/preflight.$$
}

# The COMPILED-VS-COMPILED lane's prerequisite, and the blob check's. Not
# cached-and-skipped: a stale binary is the failure this is here to catch, and
# a warm build is under a minute.
step "desktop MicroPython with the native usermods" make unix-micropython
step "docs agree with the tree"                     "${PY}" tools/check_docs.py
step "suite (redraw excluded)" \
  "${PY}" -m pytest -q --ignore=tests/test_redraw_on_change.py
step "redraw suite, alone"     "${PY}" -m pytest -q tests/test_redraw_on_change.py
step "headless tour (fresh save dir)" \
  "${PY}" tools/simulate_desktop.py --demo --strict \
    --gif "${TOUR_DIR}/demo.gif" --save-dir "${TOUR_DIR}/projects"

if [ "${WEB}" = "1" ]; then
  # Path-filtered in CI on runtime/**, device/**, system_carts/**, the runner
  # and the vendored p8 files -- i.e. most of what this repo changes. The build
  # comes first: the suites drive whatever `dist/` currently holds.
  step "browser console builds"  firmware/web_runner/build.sh
  step "...and the blob it baked is still what the binary serves" \
    make unix-micropython
  step "browser suites in real Chrome" \
    env MOYBYTE_WEB_E2E=1 "${PY}" -m pytest -rs -q \
      tests/test_web_sync_e2e.py tests/test_web_persist_e2e.py \
      tests/test_web_p8_e2e.py
fi

if [ "${fails}" -ne 0 ]; then
  printf '\npreflight: %d step(s) failed -- CI would too.\n' "${fails}"
  exit 1
fi
printf '\npreflight: green. A push should be too.\n'
printf 'ON-GLASS IS NOT IN HERE: the four board suites need the boards, and\n'
printf 'nothing but a human with them plugged in can run them (see\n'
printf '.claude/rules/testing.md). `make device-port` names the ports.\n'
