#!/usr/bin/env bash
# Provision a stock-MicroPython XIAO ESP32-S3 as the Zero (headless cart-store
# host, #41 / sync RPC). No ESP-IDF build: flash stock MP once (README), then
# this script PUSHES plain .py files + the web bundle + seed carts over USB.
# Idempotent -- run it again after editing any pushed module.
#
#   ./provision.sh [/dev/ttyACM0] [path/to/wifi.json]
#
# wifi.json is the console's own shape ({"networks": [{"ssid", "password"}]})
# and is a SECRET -- it is never in the repo; hand the script a copy (e.g. one
# read off a console board) and it lands at /moy/wifi.json.
set -euo pipefail

PORT="${1:-/dev/ttyACM0}"
WIFI="${2:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
MP="${REPO}/.venv/bin/mpremote"
[ -x "${MP}" ] || MP="mpremote"
DIST="${REPO}/firmware/web_runner/dist"

run() { "${MP}" connect "${PORT}" "$@"; }

# Stop a running serve() loop so the fs is quiet while we copy.
run exec "pass" >/dev/null 2>&1 || true

# THE ZERO SUPPORTS ALL FEATURES (owner call 2026-08-25), so the module set is
# the sync stack's whole import closure and not the minimum that boots:
#   moy_carts   the #108 files layer -- FILE_KINDS and files_root are what
#               moy_sync asks for before it will serve /files.json or apply a
#               v2 files batch, so without it the kid's drawings do not travel.
#   moy_journal the store-of-record journal: this board takes browser pushes,
#               so this board is where their undo history has to live.
#   moy_image   moy_carts' only other import (thumbnails / .moyimg codec).
# `blocks` is deliberately NOT here: moy_carts imports it lazily inside one
# function that only a console calls, and this board has no console.
echo "== modules"
run cp \
  "${REPO}/device/moy_webserver.py" \
  "${REPO}/device/moy_webhost.py" \
  "${REPO}/runtime/moy_sync.py" \
  "${REPO}/runtime/moy_fs.py" \
  "${REPO}/runtime/moy_carts.py" \
  "${REPO}/runtime/moy_journal.py" \
  "${REPO}/runtime/moy_image.py" \
  "${REPO}/runtime/web_view_ws.py" \
  "${REPO}/runtime/ticks.py" \
  "${HERE}/zero_host.py" \
  "${HERE}/zero_setup.py" \
  "${HERE}/zero_gpio.py" \
  "${HERE}/main.py" \
  : >/dev/null

echo "== dirs"
for d in /moy /moy/carts /moy/web; do
  run exec "import os
try: os.mkdir('${d}')
except OSError: pass" >/dev/null
done

echo "== web bundle (gzipped -- moy_webhost prefers the .gz copies)"
# The asset SET is moy_webhost.ASSETS's business, not this script's: a file the
# worker statically imports but nobody pushed is a console that cannot boot
# (moy_store.mjs joined 2026-08-25 and the hand-list here missed it same-day).
if [ -d "${DIST}" ]; then
  ASSET_LIST="$(MOY_REPO="${REPO}" "${REPO}/.venv/bin/python" - <<'PYEOF'
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

echo "== seed carts (the whole roster -- ~1.1MB against a ~6MB flash store)"
for cart in "${REPO}"/system_carts/*.moy; do
  run cp -r "${cart}" :/moy/carts/ >/dev/null
done

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
  echo "      http://${NAME}.local:8080/?pin=${PIN}"
  echo "  Everything but the console's own boot files needs that ?pin=."
  echo "  ================================================================"
else
  echo "  WARNING: could not read or mint /moy/zero.json -- this board will"
  echo "  serve its store to anyone on the network. Re-run this script."
fi
echo
echo "done -- watch it come up with: ${MP} connect ${PORT} repl"
