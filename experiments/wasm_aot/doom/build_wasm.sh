#!/usr/bin/env bash
# doomgeneric -> doom.wasm (wasi-sdk), then -> Xtensa AOT variants if an
# Xtensa-capable wamrc exists (toolchain/build_wamrc_xtensa.sh builds one).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="${HERE}/.."
WASI="${ROOT}/toolchain/wasi-sdk"
SRC="${ROOT}/doomgeneric/doomgeneric"
STAGE="${HERE}/src"
CC="${WASI}/bin/clang"

# 1) Stage the sources: upstream untouched, two seams patched in the copy.
rm -rf "${STAGE}" && mkdir -p "${STAGE}"
cp "${SRC}"/*.c "${SRC}"/*.h "${STAGE}/"
cp "${HERE}/dg_moy.c" "${STAGE}/"
# The IWAD lookup asks the filesystem whether "doom1.wad" exists; the host
# owns that file, so the name alone answers.
python3 - "${STAGE}/m_misc.c" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
old = "boolean M_FileExists(char *filename)\n{\n    FILE *fstream;\n"
new = ("boolean M_FileExists(char *filename)\n{\n    FILE *fstream;\n\n"
       "    if (!strcmp(filename, \"doom1.wad\"))\n    {\n        return true;\n    }\n")
assert old in s, "M_FileExists anchor moved"
open(p, "w").write(s.replace(old, new, 1))
PY

# 2) The object list is the upstream Makefile's, minus the X11 platform and
#    the stdio WAD class (dg_moy.c carries both halves).
OBJS="dummy am_map doomdef doomstat dstrings d_event d_items d_iwad d_loop d_main d_mode d_net f_finale f_wipe g_game hu_lib hu_stuff info i_cdmus i_endoom i_joystick i_scale i_sound i_system i_timer memio m_argv m_bbox m_cheat m_config m_controls m_fixed m_menu m_misc m_random p_ceilng p_doors p_enemy p_floor p_inter p_lights p_map p_maputl p_mobj p_plats p_pspr p_saveg p_setup p_sight p_spec p_switch p_telept p_tick p_user r_bsp r_data r_draw r_main r_plane r_segs r_sky r_things sha1 sounds statdump st_lib st_stuff s_sound tables v_video wi_stuff w_checksum w_main w_wad z_zone i_input i_video doomgeneric dg_moy"

MEM=$((5 * 1024 * 1024))          # linear memory, fixed: no memory.grow on device
CFLAGS="--target=wasm32-wasi -O2 -DNORMALUNIX -DLINUX -DSNDSERV -D_DEFAULT_SOURCE \
  -DDOOMGENERIC_RESX=320 -DDOOMGENERIC_RESY=200 -DCMAP256 \
  -Wno-everything -fno-strict-aliasing"
LDFLAGS="-mexec-model=reactor -Wl,--initial-memory=${MEM} -Wl,--max-memory=${MEM} \
  -Wl,-z,stack-size=262144"

echo "== compiling doom.wasm"
files=""
for o in ${OBJS}; do files="${files} ${STAGE}/${o}.c"; done
# shellcheck disable=SC2086
"${CC}" ${CFLAGS} ${LDFLAGS} -o "${HERE}/doom.wasm" ${files}
ls -la "${HERE}/doom.wasm"
python3 "${HERE}/wasm_imports.py" "${HERE}/doom.wasm"

# 3) AOT variants for the S3, if there is a wamrc that knows Xtensa.
WAMRC="${WAMRC:-${ROOT}/wamr/wamr-compiler/build/wamrc}"
if [ -x "${WAMRC}" ]; then
  for variant in xip plain; do
    xipflag=""; [ "${variant}" = xip ] && xipflag="--xip"
    out="${HERE}/doom_xtensa_${variant}.aot"
    echo "== wamrc ${variant} -> ${out}"
    "${WAMRC}" --target=xtensa --cpu=esp32s3 ${xipflag} --opt-level=3 --size-level=0 \
      -o "${out}" "${HERE}/doom.wasm"
    ls -la "${out}"
  done
else
  echo "== no Xtensa wamrc at ${WAMRC}; wasm only"
fi
