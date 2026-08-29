#!/usr/bin/env bash
# Provision a Moybyte Zero's STORE: the WiFi credentials, the pairing pin, and
# a cart roster when the board has none. Idempotent -- run it again whenever
# the credentials or the roster change.
#
#   ./provision.sh [--modules] [--web] [--carts] [--clean] [/dev/ttyACM0] [wifi.json]
#
# WHAT THIS SCRIPT IS, SINCE 2026-08-29. It used to be the whole port: this
# board ran stock MicroPython, and the nine shared modules it needs were PUSHED
# here as plain files, so the `cp` list below WAS the module set and
# `tests/test_zero_provision.py` existed to keep that list honest. The board
# has a frozen image now (`build.sh`), so the module set is `board.toml` and
# this script provisions what a PERSON has to supply: somebody's WiFi password
# and a pin minted per board.
#
# AND NOT THE CARTS, since 2026-08-30 -- this header said "what an image cannot
# carry: a kid's carts" and that stopped being true. The image carries the seed
# roster COMPRESSED (`carts_data.CARTS_Z`: the same 35 carts as one raw-deflate
# stream each, 202 KB where the plain form the console boards freeze is 732 KB
# and leaves 51 KB of this board's OTA slot), and `zero_host.seed_carts()`
# inflates it into an EMPTY store on first boot. That is what closed the real
# gap: the website's flasher can write a Zero, and a person who used it got an
# empty console with no hint that a second, cabled step existed.
#
# So the cart push became the SAME trade as `--modules` and `--web`: a dev loop
# for a roster that changed since the image was built, not the way carts
# arrive. Default is "push only if the board has no carts at all", which is the
# image's own rule so the two paths cannot fight; `--carts` forces the push.
#
# TWO THINGS FORCING IT MEANS, both of them real. It OVERWRITES the repo's copy
# of a cart over whatever is on the board, which on the one board that is the
# store OF RECORD is a thing to mean rather than to do idly. And the two paths
# NAME A FOLDER DIFFERENTLY -- `seed_builtins` names it from the cart's TITLE
# slug (hop_quest.moy) while this pushes the SOURCE folder (platformer.moy),
# which is the same split `gen_device_carts.title_to_folder` exists for -- so
# forcing a push onto an image-seeded store leaves the launcher showing both.
# Delete the store (or reflash) if that is what you have done.
#
# The push did not go away, it became OPT-IN (`--modules`). Same doctrine as
# the web bundle, one level up: STORAGE WINS, so a push stays the sub-minute
# dev loop and the image is the guarantee, not the ceiling. And the same
# hazard: MicroPython searches / before .frozen, so a pushed copy shadows the
# image silently and forever. That is why it is a flag, why `--clean` exists to
# undo it, and why `zero_host.serve()` prints which modules are shadowed.
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
PUSH_WEB=0
PUSH_CARTS=0
CLEAN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --modules) PUSH_MODULES=1; shift ;;
    --web)     PUSH_WEB=1; shift ;;
    --carts)   PUSH_CARTS=1; shift ;;
    --clean)   CLEAN=1; shift ;;
    -h|--help) sed -n '2,54p' "$0"; exit 0 ;;
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
for d in /moy /moy/carts /moy/web /moy/update; do
  run exec "import os
try: os.mkdir('${d}')
except OSError: pass" >/dev/null
done

if [ "${PUSH_WEB}" = "1" ]; then
  # The web bundle OVERRIDE. Baked into the image since this board became a
  # build target, so this is the same dev-loop trade as --modules: faster than
  # a reflash, and it wins until it is deleted. The asset SET is
  # moy_webhost.ASSETS's business, not this script's -- a file the worker
  # statically imports but nobody pushed is a console that cannot boot.
  echo "== web bundle (gzipped -- moy_webhost prefers the .gz copies)"
  if [ -d "${DIST}" ]; then
    ASSET_LIST="$(MOY_REPO="${REPO}" "${PY}" - <<'PYEOF'
import os, sys
repo = os.environ["MOY_REPO"]
sys.path.insert(0, repo)
sys.path.insert(0, os.path.join(repo, "device"))
import moy_webhost
print("\n".join(moy_webhost.ASSETS))
PYEOF
)"
    for a in ${ASSET_LIST}; do
      run cp "${DIST}/${a}.gz" :/moy/web/ >/dev/null
    done
  else
    echo "   (no ${DIST} -- build firmware/web_runner first; skipping)"
  fi
fi

# THE CARTS, and the question of whether they are already there. The image
# seeds an EMPTY store itself now, so the default job here is to notice a board
# that has none -- an old image, a wiped vfs, an interrupted first boot -- and
# fill it; `--carts` forces the push for a roster that moved since the image
# was built. The emptiness question is asked ON THE BOARD, the same read
# `zero_host.store_is_empty()` does, so the cable and the image cannot disagree
# about what "already there" means.
HAS_CARTS="$(run exec "import os
try:
    print('CARTS %d' % len([n for n in os.listdir('/moy/carts') if n.endswith('.moy')]))
except OSError:
    print('CARTS 0')" | tr -d '\r' | grep '^CARTS ' | cut -d' ' -f2 || echo 0)"
if [ "${PUSH_CARTS}" = "1" ] || [ "${HAS_CARTS:-0}" = "0" ]; then
  echo "== seed carts (the whole roster -- 763KB measured, against a 2.4MB vfs)"
  for cart in "${REPO}"/system_carts/*.moy; do
    run cp -r "${cart}" :/moy/carts/ >/dev/null
  done
else
  echo "== carts: ${HAS_CARTS} already on the board -- left alone (--carts to re-push)"
fi

if [ -n "${WIFI}" ]; then
  echo "== wifi creds"
  run cp "${WIFI}" :/moy/wifi.json >/dev/null
fi

# THE PIN, MINTED HERE WHEN THERE IS NONE (2026-08-25). Since the pin gates
# everything but the boot assets, a board without one is a board that serves
# its whole store to the network -- and a USB-provisioned Zero never went
# through the AP setup form that mints one, so until now it had none. Minted ON
# THE BOARD so the "is there one already?" check and the write are the same
# read of the same filesystem: re-running this script must never rotate a pin
# somebody has already written down (or scanned into a phone).
echo "== pairing pin"
PIN_LINE="$(run exec "import os, json
try:
    with open('/moy/zero.json') as f:
        d = json.load(f)
except (OSError, ValueError):
    d = {}
if not isinstance(d, dict):
    d = {}
if not d.get('pin'):
    try:
        b = os.urandom(2)
        n = ((b[0] << 8) | b[1]) % 10000
    except Exception:
        import time
        n = time.ticks_us() % 10000
    d['pin'] = '%04d' % n
    if not d.get('name'):
        d['name'] = 'moybyte-zero'
    with open('/moy/zero.json', 'w') as f:
        json.dump(d, f)
    print('ZEROPIN new ' + d['pin'] + ' ' + d['name'])
else:
    print('ZEROPIN kept ' + d['pin'] + ' ' + (d.get('name') or 'moybyte-zero'))" \
  | tr -d '\r' | grep '^ZEROPIN ' || true)"
PIN_STATE="$(printf '%s' "${PIN_LINE}" | cut -d' ' -f2)"
PIN="$(printf '%s' "${PIN_LINE}" | cut -d' ' -f3)"
NAME="$(printf '%s' "${PIN_LINE}" | cut -d' ' -f4)"
[ -n "${NAME}" ] || NAME="moybyte-zero"

echo "== reboot into the host"
run reset >/dev/null 2>&1 || true
echo
if [ -n "${PIN}" ]; then
  # PRINTED LOUDLY, because it is the whole pairing gesture: a page that does
  # not carry ?pin= cannot read this board's carts, and the kid would see a pin
  # prompt with nothing to type into it.
  echo "  ================================================================"
  echo "  PAIRED URL (${PIN_STATE} pin) -- open this, or scan it:"
  echo "      http://${NAME}.local/?pin=${PIN}"
  echo "  Everything but the console's own boot files needs that ?pin=."
  echo "  ================================================================"
else
  echo "  WARNING: could not read or mint /moy/zero.json -- this board will"
  echo "  serve its store to anyone on the network. Re-run this script."
fi
echo
echo "done -- watch it come up with: ${MP} connect ${PORT} repl"
