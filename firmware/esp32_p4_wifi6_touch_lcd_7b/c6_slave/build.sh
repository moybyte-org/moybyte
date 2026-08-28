#!/usr/bin/env bash
# The C6 co-processor firmware for the P4 (#7/#65): the STOCK esp-hosted-mcu
# slave ("network_adapter") + the moybyte ESP-NOW shim, built for esp32c6/SDIO.
#
# The slave sources are staged OUT OF THE SAME managed-component checkout the
# P4 host image builds against (make firmware-build-p4 must have run), so host
# and slave are the same hosted version BY CONSTRUCTION -- the property the
# hosted docs say to keep by discipline. The shim is this directory's
# slave_espnow_shim.c + the ONE-BODY protocol header from native/moy_c6; both
# are COPIED into the staged project, and the two stock files it touches
# (main/CMakeLists.txt, main/esp_hosted_coprocessor.c) are edited
# marker-guarded, same style as every build.sh patch in this repo.
#
# Output: dist/p4/c6_network_adapter.bin (+ the flasher args beside it).
# FLASHING IT IS PHASE D of docs/history/espnow_p4_2026-08.md -- the one hardware
# gate. Nothing in this script touches a board.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BOARD_DIR="$(dirname "${HERE}")"
REPO_ROOT="$(cd "${BOARD_DIR}/../.." && pwd)"
BUILD_DIR="${BOARD_DIR}/.build"
MPY_DIR="${BUILD_DIR}/micropython"
HOSTED="${MPY_DIR}/ports/esp32/managed_components/espressif__esp_hosted"
STAGE="${BUILD_DIR}/c6_slave"
DIST="${REPO_ROOT}/dist/p4"

[ -d "${HOSTED}/slave" ] || {
  echo "!! no esp_hosted managed component at ${HOSTED}" >&2
  echo "!! run 'make firmware-build-p4' first -- the slave is staged from the" >&2
  echo "!! SAME checkout the host builds against, so the versions cannot drift" >&2
  exit 1; }
HOSTED_VER="$(grep -m1 '^version:' "${HOSTED}/idf_component.yml" | awk '{print $2}')"
echo "== staging esp-hosted slave ${HOSTED_VER} for esp32c6/SDIO"

mkdir -p "${STAGE}"
rsync -a --delete --exclude build "${HOSTED}/slave/" "${STAGE}/project/"
# The slave project reaches for ../common when run in-tree.
rsync -a --delete "${HOSTED}/common/" "${STAGE}/common/"

# The shim: one .c staged in, one protocol header copied from its ONE body.
cp "${HERE}/slave_espnow_shim.c" "${STAGE}/project/main/"
cp "${BOARD_DIR}/native/moy_c6/espnow_shim_proto.h" "${STAGE}/project/main/"

# Marker-guarded: compile the shim...
CMAKE="${STAGE}/project/main/CMakeLists.txt"
if ! grep -q "slave_espnow_shim" "${CMAKE}"; then
  sed -i 's/^if(CONFIG_ESP_HOSTED_ENABLE_PEER_DATA_TRANSFER)$/list(APPEND COMPONENT_SRCS slave_espnow_shim.c) # moybyte espnow shim\n\nif(CONFIG_ESP_HOSTED_ENABLE_PEER_DATA_TRANSFER)/' "${CMAKE}"
  grep -q "slave_espnow_shim" "${CMAKE}" || { echo "!! CMakeLists patch missed" >&2; exit 1; }
fi
# ...and arm it at the same app_main tail where the stock examples init.
COPRO="${STAGE}/project/main/esp_hosted_coprocessor.c"
if ! grep -q "moyc6_espnow_shim_init" "${COPRO}"; then
  python3 - "$COPRO" <<'PYEOF'
import sys
p = sys.argv[1]
s = open(p).read()
old = "#ifdef CONFIG_EXAMPLE_PEER_DATA_TRANSFER\n\texample_peer_data_transfer_init();\n#endif"
new = ("\t{ // moybyte espnow shim (espnow_shim_proto.h)\n"
       "\t\textern esp_err_t moyc6_espnow_shim_init(void);\n"
       "\t\tmoyc6_espnow_shim_init();\n"
       "\t}\n\n" + old)
assert old in s, "coprocessor init-tail anchor moved -- re-pin the patch"
open(p, "w").write(s.replace(old, new))
PYEOF
  grep -q "moyc6_espnow_shim_init" "${COPRO}" || { echo "!! coprocessor patch missed" >&2; exit 1; }
fi

# Toolchain: the same IDF checkout every moybyte esp32 build uses.
# shellcheck disable=SC1091
source "${REPO_ROOT}/tools/esp32_build_lib.sh"
# The P4's OWN checkout, not the T-Deck's: this script already refuses to run
# before `make firmware-build-p4`, which guarantees the P4 tree exists -- and
# in CI's p4 job it is the ONLY one that does (pointing at the T-Deck's was a
# desk-topology accident that would have cloned a second ESP-IDF per CI run).
moybyte_setup_idf esp32c6 "${BOARD_DIR}/.build/esp-idf"

cd "${STAGE}/project"
SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.esp32c6;${HERE}/sdkconfig.moybyte"
export SDKCONFIG_DEFAULTS
idf.py -B build set-target esp32c6 >/dev/null
idf.py -B build build

mkdir -p "${DIST}"
cp build/network_adapter.bin "${DIST}/c6_network_adapter.bin"
cp build/flasher_args.json "${DIST}/c6_flasher_args.json"
# The image's identity, for the publisher: c6.version in latest-p4.json is
# READ FROM THE PROTO HEADER (the one body the slave compiled), so the number
# a device is offered is the number the image answers over MOYC6_V_VERSION.
SHIM_VER=$(grep -oP '#define MOYC6_SHIM_VERSION\s+\K[0-9]+' \
  "${BOARD_DIR}/native/moy_c6/espnow_shim_proto.h")
[ -n "${SHIM_VER}" ] || { echo "!! no MOYC6_SHIM_VERSION in the proto header" >&2; exit 1; }
printf '{"version": %s, "hosted": "%s"}\n' "${SHIM_VER}" "${HOSTED_VER}" \
  > "${DIST}/c6_build.json"
SIZE=$(stat -c%s "${DIST}/c6_network_adapter.bin")
echo "OK -> ${DIST}/c6_network_adapter.bin (${SIZE} B, hosted ${HOSTED_VER}, shim v${SHIM_VER})"
echo "     flashing it is Phase D of docs/history/espnow_p4_2026-08.md -- the C6 gate"
