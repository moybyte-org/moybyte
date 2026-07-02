#!/usr/bin/env bash
# Stage the MoyByte Zero onto a XIAO ESP32-S3 already running stock MicroPython.
#
# The Zero is pure Python (no native build): this pushes the shared console modules -- taken
# from the SINGLE source, the T-Deck modules tree, so the two device ports can't drift -- plus
# the Zero's own headless backend, over mpremote in one connection. First-time flashing of
# MicroPython itself is a one-off documented in README.md.
#
#   usage: firmware/seeed_xiao_esp32s3_zero/stage.sh [PORT]
#          (PORT defaults to the first /dev/ttyACM*; the XIAO's USB-Serial/JTAG)
set -euo pipefail

PORT="${1:-$(ls /dev/ttyACM* 2>/dev/null | head -1)}"
[ -n "$PORT" ] || { echo "no serial port found; pass one, e.g. stage.sh /dev/ttyACM0" >&2; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
SHARED="$HERE/../lilygo_t_deck_plus_micropython/modules"
REPO="$(cd "$HERE/../.." && pwd)"
MPREMOTE="${MPREMOTE:-$REPO/.venv/bin/python -m mpremote}"

# Shared, pure-Python modules (byte-identical to what the T-Deck freezes). moy_runtime imports
# cleanly on stock MicroPython -- its native moy_gfx/compositor use is lazy + fallback-guarded,
# never reached on the headless Zero. blocks.py is required (console imports it).
SHARED_MODS="web_view.py moy_webserver.py console.py editors.py blocks.py moy_carts.py audio.py carts_data.py moy_runtime.py"
ZERO_MODS="zero_net.py moy_zero.py main.py"

echo "staging MoyByte Zero -> $PORT"
args=()
for m in $SHARED_MODS; do args+=(cp "$SHARED/$m" ":$m" +); done
args+=(cp -r "$SHARED/moybyte" : +)
for m in $ZERO_MODS; do args+=(cp "$HERE/$m" ":$m" +); done
unset 'args[${#args[@]}-1]'          # drop the trailing '+'

# shellcheck disable=SC2086  # MPREMOTE intentionally word-splits into "python -m mpremote"
$MPREMOTE connect "$PORT" "${args[@]}"
echo "done -- reset or power-cycle the board to run (main.py starts the console)."
