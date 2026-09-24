#!/usr/bin/env bash
# Flash the Doom spike to the T-Deck: app + the chosen module into the
# wasmaot partition (16-byte MOYAOT header + file) + the WAD.
#   flash_doom.sh /dev/ttyACM0 doom_xtensa_xip.aot   (or doom_xtensa_plain.aot, or doom.wasm)
# REPLACES the console firmware; restore with
#   make firmware-flash-tdeck-mainline PORT=/dev/ttyACM0
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${1:?port}"
MODULE="${2:-doom_xtensa_xip.aot}"
PY="${HERE}/../../../.venv/bin/python"
# CHIP=p4 flashes the P4 build (doom_spike/build_p4): bootloader at 0x2000,
# 32 MB flash, esptool's default reset on the CH343.
CHIP="${CHIP:-s3}"
python3 - "${HERE}/${MODULE}" "${HERE}/wasmaot.bin" <<'PY'
import struct, sys
d = open(sys.argv[1], "rb").read()
open(sys.argv[2], "wb").write(b"MOYAOT\0\0" + struct.pack("<II", len(d), 0) + d)
print("wasmaot.bin: %s, %d bytes" % (sys.argv[1], len(d)))
PY
if [ "${CHIP}" = p4 ]; then
  B="${HERE}/doom_spike/build_p4"; ESPCHIP=esp32p4; BEFORE=default_reset; FSIZE=32MB; BOOT_OFF=0x2000
else
  B="${HERE}/doom_spike/build"; ESPCHIP=esp32s3; BEFORE=usb_reset; FSIZE=16MB; BOOT_OFF=0x0
fi
"${PY}" -m esptool --chip "${ESPCHIP}" -p "${PORT}" -b 460800 --before "${BEFORE}" --after hard_reset \
  write_flash --flash_mode dio --flash_size "${FSIZE}" --flash_freq 80m \
  "${BOOT_OFF}" "${B}/bootloader/bootloader.bin" \
  0x8000 "${B}/partition_table/partition-table.bin" \
  0x10000 "${B}/doom_spike.bin" \
  0x210000 "${HERE}/wasmaot.bin" \
  ${SKIP_WAD:+} $( [ -n "${SKIP_WAD:-}" ] || echo "0x510000 ${HERE}/../doom1.wad" ) \
  2>&1 | grep -E "Wrote|rror|Hard"
