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
  "${HERE}/main.py" \
  : >/dev/null

echo "== dirs"
for d in /moy /moy/carts /moy/web; do
  run exec "import os
try: os.mkdir('${d}')
except OSError: pass" >/dev/null
done

echo "== web bundle (gzipped -- moy_webhost prefers the .gz copies)"
if [ -d "${DIST}" ]; then
  run cp "${DIST}/index.html.gz" "${DIST}/worker.js.gz" \
         "${DIST}/micropython.mjs.gz" "${DIST}/micropython.wasm.gz" \
         :/moy/web/ >/dev/null
else
  echo "   (no ${DIST} -- build firmware/web_runner first; skipping)"
fi

echo "== seed carts"
for cart in star_catcher.moy sakura.moy; do
  run cp -r "${REPO}/system_carts/${cart}" :/moy/carts/ >/dev/null
done

if [ -n "${WIFI}" ]; then
  echo "== wifi creds"
  run cp "${WIFI}" :/moy/wifi.json >/dev/null
fi

echo "== reboot into the host"
run reset >/dev/null 2>&1 || true
echo "done -- watch it come up with: ${MP} connect ${PORT} repl"
