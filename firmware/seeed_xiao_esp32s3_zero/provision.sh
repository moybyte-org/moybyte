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

echo "== modules"
run cp \
  "${REPO}/device/moy_webserver.py" \
  "${REPO}/device/moy_webhost.py" \
  "${REPO}/runtime/moy_sync.py" \
  "${REPO}/runtime/moy_fs.py" \
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

echo "== reboot into the host"
run reset >/dev/null 2>&1 || true
echo "done -- watch it come up with: ${MP} connect ${PORT} repl"
