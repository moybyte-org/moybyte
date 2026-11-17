#!/usr/bin/env bash
# Provision a Moybyte Zero's STORE: the WiFi credentials, the board's
# password, the browser console add-on and the official carts. Idempotent --
# run it again whenever the credentials change. A write the board refuses (a
# full store) stops the script with what the board said, and a non-zero exit.
#
#   ./provision.sh [--modules] [--no-carts] [--no-console] [--clean] [--password] [/dev/ttyACM0] [wifi.json]
#
# THE BROWSER CONSOLE is an add-on cart (native/moy_net/moy_page.h), and this
# board's whole product, so it is installed by default: the cart that fits the
# image this checkout built (dist/zero), copied into the store with the add-on
# folders that fit other images taken off (tools/board_flash.py's
# install_addon, which a cable flash of this board runs too). After an update,
# or on a store with none, the board installs the one that fits from its
# release on its own (zero_host.make_addon).
# `--no-console` skips it.
#
# WHAT THIS SCRIPT IS, SINCE 2026-08-29. It used to be the whole port: this
# board ran stock MicroPython, and the nine shared modules it needs were PUSHED
# here as plain files, so the `cp` list below WAS the module set and
# `tests/test_zero_provision.py` existed to keep that list honest. The board
# has a frozen image now (`build.sh`), so the module set is `board.toml` and
# this script provisions what a PERSON has to supply: somebody's WiFi password
# and the board's own password, which a browser types once to pair (the Zero
# has no screen for a pairing code; docs/os_2026-11/system.md section 3).
#
# THE CARTS. The image carries its seed carts compressed (board.toml's
# `[roster] image`: the core carts, every app and five games;
# `carts_data.CARTS_Z`), and `zero_host.seed_carts()` seeds them as a console
# seeds its own, so a Zero flashed from the website's flasher has a working
# console with no cabled step. The other system carts are official carts the
# release publishes, which the page the board serves installs; this script
# brings the ones a store holds at an older version to this tree, the kid's
# saves and config kept (tools/board_flash.py's install_carts, which a cable
# flash runs too). `--no-carts` skips it.
#
# The module push is OPT-IN (`--modules`): a sub-minute dev
# loop for a module that changed since the image was built. The hazard is why
# it is a flag -- MicroPython searches / before .frozen, so a pushed copy
# shadows the image silently and forever. `--clean` undoes it, and
# `zero_host.serve()` prints which modules are shadowed.
#
# The pushed list is DERIVED from board.toml, never typed here: a hand-list is
# the thing that falls behind the code it is a list of, and this board has
# already paid for that once (moy_store.mjs became an asset the worker
# statically imports and the hand-list missed it the same day, which is a
# console that cannot boot).
#
# wifi.json is the console's own shape ({"networks": [{"ssid", "password"}]})
# and is a SECRET -- it is never in the repo; hand the script a copy (e.g. one
# read off a console board) and it lands at /moy/wifi.json.
set -euo pipefail

PUSH_MODULES=0
PUSH_CARTS=1
PUSH_CONSOLE=1
CLEAN=0
SET_PASSWORD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --modules) PUSH_MODULES=1; shift ;;
    --password) SET_PASSWORD=1; shift ;;
    --no-carts) PUSH_CARTS=0; shift ;;
    --no-console) PUSH_CONSOLE=0; shift ;;
    --clean)   CLEAN=1; shift ;;
    -h|--help) sed -n '2,50p' "$0"; exit 0 ;;
    *) break ;;
  esac
done

PORT="${1:-/dev/ttyACM0}"
WIFI="${2:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
MP="${REPO}/.venv/bin/mpremote"
[ -x "${MP}" ] || MP="mpremote"
PY="${REPO}/.venv/bin/python"
[ -x "${PY}" ] || PY="python3"
DIST="${REPO}/firmware/web_runner/dist"

run() { "${MP}" connect "${PORT}" "$@"; }

# A write on the board, checked: CODE must run to its end and print MOYOK, or
# the script stops with what the board said. mpremote's exec answers 0 even
# when the code raised, and a full littlefs fails a write with a bare ENOENT
# from the mkdir before it, which `except OSError: pass` used to swallow.
board_write() {
  local what="$1" code="$2" out
  out="$(run exec "${code}
print('MOY' + 'OK')" 2>&1 | tr -d '\r')" || true
  if ! printf '%s\n' "${out}" | grep -qx 'MOYOK'; then
    echo "!! ${what}: the board refused the write" >&2
    printf '%s\n' "${out}" | sed 's/^/   /' >&2
    exit 1
  fi
}

# mkdir on the board, an existing folder fine and any other error fatal.
board_mkdir() {
  board_write "mkdir $1" "import os
try: os.mkdir('$1')
except OSError as e:
    if e.args[0] != 17: raise"
}

# Stop a running serve() loop so the fs is quiet while we copy. (This is also
# the check that the board's REPL is reachable at all -- it goes through the
# TinyUSB CDC path mpconfigboard.h keeps for exactly this.)
run exec "pass" >/dev/null 2>&1 || true

if [ "${CLEAN}" = "1" ]; then
  # UNDO A PUSH. A module pushed to / outranks the frozen one for as long as it
  # exists, so a board that was ever developed against keeps running whatever
  # was on it the day somebody stopped -- while every diagnostic points at the
  # image. The list is board.toml's, so this removes exactly what --modules
  # puts there and never a file that is somebody's own.
  echo "== removing pushed module copies (the image's own take over again)"
  NAMES="$(MOY_REPO="${REPO}" MOY_BOARD="${HERE}" "${PY}" - <<'PYEOF'
import os, sys
repo = os.environ["MOY_REPO"]
sys.path.insert(0, repo)
from tools import board_config
names = sorted(board_config.staged_modules(os.environ["MOY_BOARD"], repo))
names += sorted(p for p in os.listdir(os.path.join(os.environ["MOY_BOARD"],
                                                   "modules"))
                if p.endswith(".py"))
print("\n".join(sorted(set(names))))
PYEOF
)"
  for n in ${NAMES}; do
    run exec "import os
try: os.remove('/${n}')
except OSError: pass" >/dev/null 2>&1 || true
  done
  echo "   (${PORT}: reset it to run the image's modules)"
fi

if [ "${PUSH_MODULES}" = "1" ]; then
  # THE DEV LOOP. Everything board.toml says this image freezes, pushed as
  # plain files so an edit is a second rather than a reflash. Derived, not
  # typed -- see the header.
  echo "== modules (DEV PUSH -- these SHADOW the image until ./provision.sh --clean)"
  FILES="$(MOY_REPO="${REPO}" MOY_BOARD="${HERE}" "${PY}" - <<'PYEOF'
import os, sys
repo = os.environ["MOY_REPO"]
board = os.environ["MOY_BOARD"]
sys.path.insert(0, repo)
from tools import board_config
# The staged set (board.toml's [modules.shared] allowlist + [modules.device]),
# then the board's OWN modules/ -- tracked files only, which is what separates
# them from copies a previous build staged into the same directory.
out = [str(p) for p in board_config.staged_modules(board, repo).values()]
import subprocess
tracked = subprocess.run(["git", "ls-files", "modules"], cwd=board,
                         capture_output=True, text=True).stdout.split()
out += [os.path.join(board, t) for t in tracked if t.endswith(".py")]
print("\n".join(out))
PYEOF
)"
  # shellcheck disable=SC2086
  run cp ${FILES} : >/dev/null
fi

echo "== dirs"
for d in /moy /moy/carts /moy/update; do
  board_mkdir "${d}"
done


if [ "${PUSH_CONSOLE}" = "1" ]; then
  echo "== the browser console add-on (the cart that fits dist/zero's image)"
  "${PY}" "${REPO}/tools/board_flash.py" addon "${HERE}" --port "${PORT}"
fi

if [ "${PUSH_CARTS}" = "1" ]; then
  echo "== the official carts (every older one the image does not carry)"
  "${PY}" "${REPO}/tools/board_flash.py" carts "${HERE}" --port "${PORT}"
fi

if [ -n "${WIFI}" ]; then
  echo "== wifi creds"
  run cp "${WIFI}" :/moy/wifi.json >/dev/null
  board_write "wifi.json" "import os
assert os.stat('/moy/wifi.json')[6] == $(wc -c < "${WIFI}")"
fi

# THE BOARD'S PASSWORD. A browser pairs with this board by typing it once
# (SPAKE2+, native/moy_net/moy_webauth.c) and signs in with a device key from
# then on; with none set, no browser can pair, because this board has no screen
# for a pairing code. `--password` asks for one (twice, never echoed) and
# writes its HASH into /moy/system/password, as `passwd` and the setup form
# do: the password itself never reaches the board. Without the flag a password
# already there is kept, and a board with none says so below.
if [ "${SET_PASSWORD}" = "1" ]; then
  echo "== the board's password"
  read -rsp "  password for this board (8 to 64 characters): " PW1; echo
  read -rsp "  again: " PW2; echo
  if [ "${PW1}" != "${PW2}" ] || [ "${#PW1}" -lt 8 ] || [ "${#PW1}" -gt 64 ]; then
    echo "  not set: the two differ, or the length is outside 8..64" >&2
    exit 1
  fi
  HASH="$(MOY_REPO="${REPO}" MOY_PW="${PW1}" "${PY}" - <<'PYEOF'
import os, sys
sys.path.insert(0, os.environ["MOY_REPO"])
from runtime.system import ssh_service
print(ssh_service.hashed(os.environ["MOY_PW"], os.urandom(16).hex()))
PYEOF
)"
  unset PW1 PW2
  board_mkdir /moy/system
  board_write "the password" "with open('/moy/system/password', 'w') as f: f.write('${HASH}\n')
assert open('/moy/system/password').read() == '${HASH}\n'"
fi
echo "== pairing"
PW_STATE="$(run exec "try:
    with open('/moy/system/password') as f: print('ZEROPW', 'set' if f.read(6) == '\$moy1\$' else 'none')
except OSError: print('ZEROPW none')" | tr -d '\r' | grep '^ZEROPW ' | cut -d' ' -f2 || true)"
NAME="$(run exec "import json
try:
    with open('/moy/zero.json') as f: print('ZERONAME', json.load(f).get('name') or 'moybyte-zero')
except (OSError, ValueError, AttributeError): print('ZERONAME moybyte-zero')" \
  | tr -d '\r' | grep '^ZERONAME ' | cut -d' ' -f2 || true)"
[ -n "${NAME}" ] || NAME="moybyte-zero"

echo "== reboot into the host"
run reset >/dev/null 2>&1 || true
echo
if [ "${PW_STATE}" = "set" ]; then
  echo "  ================================================================"
  echo "  Open http://${NAME}.local/ and pair the browser with this board's"
  echo "  password: it asks once, then remembers the board."
  echo "  ================================================================"
else
  echo "  WARNING: this board has no password, so no browser can pair with it"
  echo "  and its store stays shut. Re-run with --password."
fi
echo
echo "done -- watch it come up with: ${MP} connect ${PORT} repl"
