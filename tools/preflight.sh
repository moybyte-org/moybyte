#!/usr/bin/env bash
# Run what CI runs, here, before pushing.
#
#   tools/preflight.sh          # the host job, in CI's own order
#   tools/preflight.sh --web    # ...plus the browser suites in real Chrome
#   tools/preflight.sh --no-web # ...without them even where the change reaches them
#   tools/preflight.sh --full   # the host job even when only docs changed
#   tools/preflight.sh --firmware     # ...plus every board's image, built here
#   tools/preflight.sh --no-firmware  # ...without them even where the change reaches them
#
# The browser suites run on their own when the change touches what CI's
# web-e2e.yml path filter covers (`web_touched`, below, mirrors that list);
# `--web` forces them, `--no-web` skips them. Every board's image is built
# (`tools/board.py pass --all --build-only`) when the change touches what
# firmware-build.yml's filter covers (`firmware_touched`); `--firmware` forces
# it, `--no-firmware` skips it. That lane is CI's firmware job: an image under
# its board's headroom floor, or a board whose staged modules no longer
# compile, fails here instead of after the push. A local image is CI's within
# a few KB (same pinned IDF and MicroPython), so the floor reads the same; a
# warm build is a minute or two a board, and it needs a worktree.
#
# A branch whose changes against origin/dev are ALL documents (`*.md`, anything
# under `docs/`) runs the docs check, the look-shot tests and every test file
# that names a changed file, and nothing else: no source moved, so no derived
# artifact can have gone stale. The main suite runs across cores as `make test`
# does; CI's serial run is still the net for a test that only fails in order.
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
NOWEB=0
FULL=0
FIRMWARE=0
NOFIRMWARE=0
for arg in "$@"; do
  case "${arg}" in
    --web) WEB=1 ;;
    --no-web) NOWEB=1 ;;
    --full) FULL=1 ;;
    --firmware) FIRMWARE=1 ;;
    --no-firmware) NOFIRMWARE=1 ;;
  esac
done
TOUR_DIR="$(mktemp -d)"
trap 'rm -rf "${TOUR_DIR}"' EXIT

fails=0
failed=()
step() {                        # step "name" cmd...
  local name="$1"; shift
  printf '== %s\n' "${name}"
  if "$@" > /tmp/preflight.$$ 2>&1; then
    printf '   ok\n'
  else
    printf '   FAIL\n'
    sed 's/^/   | /' /tmp/preflight.$$ | tail -25
    fails=$((fails + 1))
    failed+=("${name}")
  fi
  rm -f /tmp/preflight.$$
}

PYTEST=(env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "${PY}" -m pytest)

changed() {                     # every path changed against origin/dev (or $PREFLIGHT_BASE)
  { git diff --name-only "${PREFLIGHT_BASE:-origin/dev}"...HEAD 2>/dev/null
    git diff --name-only HEAD
    git ls-files --others --exclude-standard; } | sort -u
}
docs_only() {
  local files f
  files="$(changed)"
  [ -n "${files}" ] || return 1
  while IFS= read -r f; do
    case "${f}" in
      *.md|docs/*) ;;
      *) return 1 ;;
    esac
  done <<< "${files}"
}

# Whether the change touches what web-e2e.yml's `paths:` covers: keep this
# list and that one the same (the workflow is the authority).
web_touched() {
  local f
  while IFS= read -r f; do
    case "${f}" in
      firmware/web_runner/*|runtime/*|device/*|system_carts/*|native/*|ports/jet/*) return 0 ;;
      tests/test_web_*|tests/web_e2e.py|tests/cart_store_fixtures.py) return 0 ;;
      tests/fixtures/*|tests/tier_frame.py) return 0 ;;
      tools/jet_cart.py|tools/zopfli_gz.py|tools/wasm_cart.py) return 0 ;;
      tools/p8_import.py|tools/p8_lua_port.py|tools/p8_writer.py) return 0 ;;
      .github/workflows/web-e2e.yml) return 0 ;;
    esac
  done <<< "$(changed)"
  return 1
}

# Whether the change touches what firmware-build.yml's `paths:` covers: keep
# this list and that one the same (tests/test_preflight_web_filter.py).
firmware_touched() {
  local f
  while IFS= read -r f; do
    case "${f}" in
      firmware/*|runtime/*|system_carts/*|device/*|native/*|patches/*) return 0 ;;
      tools/esp32_build_lib.sh|tools/board_config.py|tools/gen_web_blob.py) return 0 ;;
      tools/zopfli_gz.py|tools/gen_device_carts.py|tools/p8_import.py) return 0 ;;
      tools/p8_lua_port.py|tools/p8_writer.py|site/build.py) return 0 ;;
      tools/publish_firmware_release.py|.github/workflows/firmware-build.yml) return 0 ;;
    esac
  done <<< "$(changed)"
  return 1
}

say_failed() {
  printf '\npreflight: %d step(s) failed -- CI would too:\n' "${fails}"
  printf '   %s\n' "${failed[@]}"
}

if [ "${FULL}" = "0" ] && [ "${WEB}" = "0" ] && [ "${FIRMWARE}" = "0" ] && docs_only; then
  printf 'preflight: only documents changed -- the docs lane (--full runs everything)\n'
  named=()
  while IFS= read -r f; do
    base="$(basename "${f}")"
    while IFS= read -r t; do named+=("${t}"); done \
      < <(grep -lF -- "${base}" tests/test_*.py 2>/dev/null)
  done <<< "$(changed)"
  mapfile -t named < <(printf '%s\n' tests/test_look_shots.py tests/test_editor_look.py "${named[@]}" | sort -u)
  step "docs agree with the tree"  "${PY}" tools/check_docs.py
  step "tests that name a changed document" \
    "${PYTEST[@]}" -p xdist -n auto -q "${named[@]}"
  if [ "${fails}" -ne 0 ]; then
    say_failed
    exit 1
  fi
  printf '\npreflight: green (docs lane). A push should be too.\n'
  exit 0
fi

if [ "${WEB}" = "0" ] && [ "${NOWEB}" = "0" ] && web_touched; then
  printf 'preflight: the change reaches the browser suites (CI runs them) -- the web lane is on (--no-web skips it)\n'
  WEB=1
fi

# The COMPILED-VS-COMPILED lane's prerequisite, and the blob check's. Not
# cached-and-skipped: a stale binary is the failure this is here to catch, and
# a warm build is under a minute.
step "desktop MicroPython with the native usermods" make unix-micropython
step "docs agree with the tree"                     "${PY}" tools/check_docs.py
# CI's cached step: the toolchain the showcase cart's goldens build with,
# fetched by its pinned sha256 when absent (a no-op when it is here).
step "wasi-sdk for the compiled showcase cart"      "${PY}" tools/jet_cart.py --toolchain
# CI's cached step too: the pinned wamrc the AOT checks compile with, fetched
# and hash-checked when absent.
step "the pinned wamrc for the AOT checks"          "${PY}" tools/wasm_module.py compilers
step "suite (redraw excluded)" \
  "${PYTEST[@]}" -p xdist -n auto -q --ignore=tests/test_redraw_on_change.py
step "redraw suite, alone"     "${PY}" -m pytest -q tests/test_redraw_on_change.py
step "headless tour (fresh save dir)" \
  "${PY}" tools/simulate_desktop.py --demo --strict \
    --gif "${TOUR_DIR}/demo.gif" --save-dir "${TOUR_DIR}/projects"

if [ "${WEB}" = "1" ]; then
  # Path-filtered in CI (`web_touched` above). The build comes first: the
  # suites drive whatever `dist/` currently holds.
  step "browser console builds"  firmware/web_runner/build.sh
  step "...and the blob it baked is still what the binary serves" \
    make unix-micropython
  step "browser suites in real Chrome" \
    env MOYBYTE_WEB_E2E=1 "${PY}" -m pytest -p xdist -n 4 -rs -q \
      tests/test_web_sync_e2e.py tests/test_web_persist_e2e.py \
      tests/test_web_p8_e2e.py tests/test_web_update_e2e.py \
      tests/test_web_store_e2e.py tests/test_web_wasm_e2e.py \
      tests/test_web_fonts.py tests/test_web_look_parity.py \
      tests/test_editor_ladder_icons.py
fi

if [ "${FIRMWARE}" = "0" ] && [ "${NOFIRMWARE}" = "0" ] && firmware_touched; then
  printf 'preflight: the change reaches the board images (CI builds them) -- the firmware lane is on (--no-firmware skips it)\n'
  FIRMWARE=1
fi
if [ "${FIRMWARE}" = "1" ]; then
  step "every board's image builds, above its headroom floor" \
    "${PY}" tools/board.py pass --all --build-only
fi

if [ "${fails}" -ne 0 ]; then
  say_failed
  exit 1
fi
printf '\npreflight: green. A push should be too.\n'
printf 'ON-GLASS IS NOT IN HERE: the board suites need a board CONNECTED to\n'
printf 'this machine, which CI has none of. Whoever is at this checkout --\n'
printf 'person or agent -- runs them when one is plugged in, no permission\n'
printf 'needed. `tools/board.py ports` says which board is on which port.\n'
