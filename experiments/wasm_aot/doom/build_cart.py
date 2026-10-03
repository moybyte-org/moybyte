#!/usr/bin/env python3
"""Doom as a compiled cart, built on this machine: the recipe.

    python3 experiments/wasm_aot/doom/build_cart.py            # -> out/doom.moy
    python3 experiments/wasm_aot/doom/build_cart.py --zone 2 --out /tmp/carts

Fetches doomgeneric at a pinned commit and the shareware `doom1.wad` from
Debian's archive, checks each against its sha256, prints both licences, builds
`main.wasm` with wasi-sdk 24 on the console's `"moy"` imports (dg_cart.c), and
compiles and signs the per-chip modules with tools/wasm_module.py's pinned
compilers and the OTA signing key -- a `"runtime": "wasm"` cart folder a board
runs from its launcher.

THE CART IS NOT OURS TO GIVE AWAY. doomgeneric is GPL-2.0, and the shareware
WAD's licence forbids consideration and derivative works, so the cart is built
here, for whoever built it, and is never committed, seeded or pushed to a store
(THIRD_PARTY.md, the Celeste rule). Every download and every build product
lives under this directory's gitignored `cache/`, `stage/` and `out/`.

The zone (`--zone`, Doom's `-mb`, whole MiB) sizes the cart: the manifest's
`memory` is the module's stack (`--stack`) and static data, the zone, and the
heap the rest of Doom mallocs (`--heap`), rounded up to whole 64 KiB pages.
The cart's config.json carries the zone and `args` -- more of Doom's own
flags, such as `-warp 1 3` -- which a board reads at _init, so a zone or a
level can change without a rebuild as long as the memory still holds it.
"""

import argparse
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import wasm_module  # noqa: E402

CACHE = os.path.join(HERE, "cache")
STAGE = os.path.join(HERE, "stage")
OUT = os.path.join(HERE, "out")
WASI_SDK = os.path.join(ROOT, "experiments", "wasm_aot", "toolchain", "wasi-sdk")

# doomgeneric at the commit the #158 spike ran. GitHub builds the tarball on
# request, so its bytes are not promised; the pin is the sha256 of the TREE --
# every file's path and sha256, sorted -- which is.
DOOMGENERIC_COMMIT = "dcb7a8dbc7a16ce3dda29382ac9aae9d77d21284"
DOOMGENERIC_URL = ("https://codeload.github.com/ozkl/doomgeneric/tar.gz/"
                   + DOOMGENERIC_COMMIT)
DOOMGENERIC_TREE_SHA256 = "57e10d1c9c5fbd4312b1d3d88a54d9007f5490ed0fb172fd5cc0ff8ac6396098"

# The shareware IWAD, v1.9, as Debian's archive carries it: the source
# package's upstream tarball (the WAD, unmodified) and its packaging (whose
# copyright file carries id's licence text).
DEBIAN = "http://deb.debian.org/debian/pool/non-free/d/doom-wad-shareware/"
WAD_TARBALL = (DEBIAN + "doom-wad-shareware_1.9.fixed.orig.tar.gz",
               "e02c8b5e01be7373d4c53f82556118e2aaaf8f83fa2af5eee1efadf9c55c4eb1")
WAD_PACKAGING = (DEBIAN + "doom-wad-shareware_1.9.fixed-5.debian.tar.xz",
                 "57ee8d82c6533475a7001297dca396a0645b96f37801c1cf0741e014c352b3b3")
WAD_MEMBER = "doom-wad-shareware-1.9.fixed/doom1.wad"
WAD_SHA256 = "1d7d43be501e67d927e415e0b8f3e29c3bf33075e859721816f652a526cac771"

# wasi-sdk 24 (clang 18 and wasi-libc), fetched when the toolchain directory
# does not have it.
WASI_SDK_URL = ("https://github.com/WebAssembly/wasi-sdk/releases/download/"
                "wasi-sdk-24/wasi-sdk-24.0-x86_64-linux.tar.gz")
WASI_SDK_SHA256 = "c6c38aab56e5de88adf6c1ebc9c3ae8da72f88ec2b656fb024eda8d4167a0bc5"

# The engine's object list is doomgeneric's own Makefile's, less its platform
# halves and the stdio WAD class: dg_cart.c carries both.
OBJS = """dummy am_map doomdef doomstat dstrings d_event d_items d_iwad d_loop
d_main d_mode d_net f_finale f_wipe g_game hu_lib hu_stuff info i_cdmus
i_endoom i_joystick i_scale i_sound i_system i_timer memio m_argv m_bbox
m_cheat m_config m_controls m_fixed m_menu m_misc m_random p_ceilng p_doors
p_enemy p_floor p_inter p_lights p_map p_maputl p_mobj p_plats p_pspr
p_saveg p_setup p_sight p_spec p_switch p_telept p_tick p_user r_bsp r_data
r_draw r_main r_plane r_segs r_sky r_things sha1 sounds statdump st_lib
st_stuff s_sound tables v_video wi_stuff w_checksum w_main w_wad z_zone
i_input i_video doomgeneric dg_cart""".split()

CFLAGS = ["--target=wasm32-wasi", "-O2", "-DNORMALUNIX", "-DLINUX", "-DSNDSERV",
          "-D_DEFAULT_SOURCE", "-DDOOMGENERIC_RESX=320", "-DDOOMGENERIC_RESY=200",
          "-DCMAP256", "-Wno-everything", "-fno-strict-aliasing"]
PAGE = 65536

# Where the engine meets the cart: each (file, what the upstream text is,
# what it becomes). An anchor that moved is a failed build, never a silent one.
PATCHES = (
    # The IWAD search asks the filesystem whether doom1.wad exists; the cart's
    # folder owns it, and `read` is how it is reached.
    ("m_misc.c",
     "boolean M_FileExists(char *filename)\n{\n    FILE *fstream;\n",
     "boolean M_FileExists(char *filename)\n{\n    FILE *fstream;\n\n"
     "    if (!strcmp(filename, \"doom1.wad\"))\n    {\n        return true;\n    }\n"),
    # Doom draws straight into the cart's 320 x 240 frame, letterboxed.
    ("doomgeneric.c",
     "DG_ScreenBuffer = malloc(DOOMGENERIC_RESX * DOOMGENERIC_RESY * 4);",
     "DG_ScreenBuffer = DG_ScreenMemory();"),
    ("doomgeneric.h",
     "void doomgeneric_Create(int argc, char **argv);",
     "void doomgeneric_Create(int argc, char **argv);\npixel_t *DG_ScreenMemory(void);"),
    # No tic due is a return, never a wait: the cart waits by returning.
    ("d_loop.c",
     "\t    return;\n\t}\n\n        I_Sleep(1);\n    }",
     "\t    return;\n\t}\n\n        return;\n    }"),
    # The frame CRC and the zone's low-water mark, after every game tic.
    ("d_loop.c",
     "\t    gametic++;\n",
     "\t    gametic++;\n\t    DG_AfterTic();\n"),
    ("d_loop.c",
     "void TryRunTics (void)\n{",
     "void DG_AfterTic(void);\n\nvoid TryRunTics (void)\n{"),
    # The screen melt: a step a frame, never a loop waiting on the clock.
    ("d_main.c",
     "    if (nodrawers)\n    \treturn;                    // for comparative timing / profiling\n",
     "    if (nodrawers)\n    \treturn;                    // for comparative timing / profiling\n"
     "    if (DG_WipeStep())\n    \treturn;\n"),
    ("d_main.c",
     "    wipestart = I_GetTime () - 1;\n\n    do\n    {\n\tdo\n\t{\n"
     "\t    nowtime = I_GetTime ();\n\t    tics = nowtime - wipestart;\n"
     "            I_Sleep(1);\n\t} while (tics <= 0);\n        \n"
     "\twipestart = nowtime;\n\tdone = wipe_ScreenWipe(wipe_Melt\n"
     "\t\t\t       , 0, 0, SCREENWIDTH, SCREENHEIGHT, tics);\n"
     "\tI_UpdateNoBlit ();\n\tM_Drawer ();                            "
     "// menu is drawn even on top of wipes\n"
     "\tI_FinishUpdate ();                      // page flip or blit buffer\n"
     "    } while (!done);\n",
     "    DG_WipeBegin();\n"),
    ("d_main.c",
     "void D_Display (void)\n{",
     "void DG_WipeBegin(void);\nint DG_WipeStep(void);\n\nvoid D_Display (void)\n{"),
    # Doom draws straight into the cart's frame: I_VideoBuffer IS
    # DG_ScreenBuffer, so I_FinishUpdate has nothing to copy.
    ("i_video.c",
     "\tI_VideoBuffer = (byte*)Z_Malloc (SCREENWIDTH * SCREENHEIGHT, PU_STATIC, NULL);"
     "  // For DOOM to draw on\n",
     "\tI_VideoBuffer = (byte*)DG_ScreenBuffer;\n"),
    ("i_video.c", "\tZ_Free (I_VideoBuffer);\n", ""),
    ("i_video.c",
     "    y = SCREENHEIGHT;\n",
     "    y = I_VideoBuffer == (byte*)DG_ScreenBuffer ? 0 : SCREENHEIGHT;\n"),
    # The column and span drawers take their texture and colormap into locals
    # once a call: a store through the frame may alias any global, so read
    # through the globals they were loaded again for every pixel.
    ("r_draw.c",
     "    // Inner loop that does the actual texture mapping,\n    //  e.g. a DDA-lile scaling.\n"
     "    // This is as fast as it gets.\n    do \n    {\n"
     "\t// Re-map color indices from wall texture column\n"
     "\t//  using a lighting/special effects LUT.\n"
     "\t*dest = dc_colormap[dc_source[(frac>>FRACBITS)&127]];\n",
     "    const byte *source = dc_source, *colormap = dc_colormap;\n"
     "    do \n    {\n"
     "\t*dest = colormap[source[(frac>>FRACBITS)&127]];\n"),
    ("r_draw.c",
     "    dest = ylookup[ds_y] + columnofs[ds_x1];\n\n"
     "    // We do not check for zero spans here?\n    count = ds_x2 - ds_x1;\n\n"
     "    do\n    {\n\t// Calculate current texture index in u,v.\n"
     "        ytemp = (position >> 4) & 0x0fc0;\n        xtemp = (position >> 26);\n"
     "        spot = xtemp | ytemp;\n\n\t// Lookup pixel from flat texture tile,\n"
     "\t//  re-index using light/colormap.\n\t*dest++ = ds_colormap[ds_source[spot]];\n",
     "    dest = ylookup[ds_y] + columnofs[ds_x1];\n"
     "    const byte *source = ds_source, *colormap = ds_colormap;\n\n"
     "    // We do not check for zero spans here?\n    count = ds_x2 - ds_x1;\n\n"
     "    do\n    {\n\t// Calculate current texture index in u,v.\n"
     "        ytemp = (position >> 4) & 0x0fc0;\n        xtemp = (position >> 26);\n"
     "        spot = xtemp | ytemp;\n\n\t// Lookup pixel from flat texture tile,\n"
     "\t//  re-index using light/colormap.\n\t*dest++ = colormap[source[spot]];\n"),
)

GPL_NOTICE = """\
doomgeneric (id Software's DOOM, ozkl's portable fork) is licensed under the
GNU General Public License, version 2. Its LICENSE, as fetched:
"""

CART_NOTICE = """\
THIS CART IS A LOCAL BUILD. It links doomgeneric (GPL-2.0) and carries the
shareware doom1.wad under id Software's Limited Use Software License: it is
for the person who built it, and it must not be hosted, seeded, sold, pushed
to a store or shipped in a product image (THIRD_PARTY.md).
"""


class RecipeError(RuntimeError):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url, sha256, name):
    """The bytes at `url`, cached under cache/`name`, refused unless they hash
    to `sha256`."""
    path = os.path.join(CACHE, name)
    if not os.path.isfile(path):
        os.makedirs(CACHE, exist_ok=True)
        print("fetching %s" % url)
        with urllib.request.urlopen(url, timeout=300) as r:
            data = r.read()
        got = sha256_bytes(data)
        if got != sha256:
            raise RecipeError("%s hashes to %s; the pin is %s" % (url, got, sha256))
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
    with open(path, "rb") as f:
        data = f.read()
    got = sha256_bytes(data)
    if got != sha256:
        raise RecipeError("%s hashes to %s; the pin is %s (delete it to refetch)"
                          % (path, got, sha256))
    return data


def tree_sha256(files):
    """The sha256 of a source tree: every (path, sha256 of its bytes), sorted,
    one per line."""
    lines = sorted("%s %s" % (p, sha256_bytes(d)) for p, d in files.items())
    return sha256_bytes("\n".join(lines).encode())


def doomgeneric():
    """{path: bytes} of doomgeneric at the pinned commit, its tree checked."""
    path = os.path.join(CACHE, "doomgeneric-%s.tar.gz" % DOOMGENERIC_COMMIT[:12])
    if not os.path.isfile(path):
        os.makedirs(CACHE, exist_ok=True)
        print("fetching %s" % DOOMGENERIC_URL)
        with urllib.request.urlopen(DOOMGENERIC_URL, timeout=300) as r:
            data = r.read()
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
    files = {}
    with tarfile.open(path, "r:gz") as tar:
        for m in tar.getmembers():
            if m.isfile():
                files[m.name.split("/", 1)[1]] = tar.extractfile(m).read()
    got = tree_sha256(files)
    if got != DOOMGENERIC_TREE_SHA256:
        raise RecipeError("doomgeneric %s: the tree hashes to %s; the pin is %s "
                          "(delete %s to refetch)" % (DOOMGENERIC_COMMIT[:12], got,
                                                      DOOMGENERIC_TREE_SHA256, path))
    return files


def shareware_wad():
    """(doom1.wad's bytes, id's licence text from Debian's copyright file)."""
    tarball = fetch(*WAD_TARBALL, name="doom-wad-shareware.orig.tar.gz")
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        wad = tar.extractfile(WAD_MEMBER).read()
    if sha256_bytes(wad) != WAD_SHA256:
        raise RecipeError("doom1.wad hashes to %s; the pin is %s"
                          % (sha256_bytes(wad), WAD_SHA256))
    packaging = fetch(*WAD_PACKAGING, name="doom-wad-shareware.debian.tar.xz")
    with tarfile.open(fileobj=io.BytesIO(packaging), mode="r:xz") as tar:
        copyright_text = tar.extractfile("debian/copyright").read().decode()
    start = copyright_text.index("Files: doom1.wad")
    end = copyright_text.index("Files: debian/*")
    return wad, copyright_text[start:end].rstrip() + "\n"


def wasi_sdk():
    """The wasi-sdk 24 directory: the toolchain directory's, $WASI_SDK_PATH,
    or fetched into the toolchain directory."""
    for cand in (os.environ.get("WASI_SDK_PATH"), WASI_SDK):
        if cand and os.path.isfile(os.path.join(cand, "bin", "clang")):
            return cand
    data = fetch(WASI_SDK_URL, WASI_SDK_SHA256, "wasi-sdk-24.0-x86_64-linux.tar.gz")
    parent = os.path.dirname(WASI_SDK)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        top = tar.getmembers()[0].name.split("/")[0]
        tar.extractall(parent)
    os.replace(os.path.join(parent, top), WASI_SDK)
    return WASI_SDK


def stage(sources):
    """doomgeneric's engine and dg_cart.c in STAGE, patched."""
    if os.path.isdir(STAGE):
        shutil.rmtree(STAGE)
    os.makedirs(STAGE)
    for path, data in sources.items():
        if path.startswith("doomgeneric/") and path.count("/") == 1 \
                and path.endswith((".c", ".h")):
            with open(os.path.join(STAGE, path.split("/")[1]), "wb") as f:
                f.write(data)
    shutil.copy(os.path.join(HERE, "dg_cart.c"), STAGE)
    for name, old, new in PATCHES:
        p = os.path.join(STAGE, name)
        with open(p, encoding="latin-1") as f:
            text = f.read()
        if text.count(old) != 1:
            raise RecipeError("%s: the patch anchor %r moved" % (name, old[:40]))
        with open(p, "w", encoding="latin-1") as f:
            f.write(text.replace(old, new, 1))


def link(sdk, memory, stack_kb):
    """doom.wasm at `memory` bytes of linear memory; its bytes. The stack
    goes first, so an overflow leaves linear memory and traps rather than
    running into the data."""
    out = os.path.join(STAGE, "doom.wasm")
    cmd = ([os.path.join(sdk, "bin", "clang")] + CFLAGS
           + ["-nostartfiles", "-Wl,--no-entry", "-Wl,--strip-all",
              "-Wl,--initial-memory=%d" % memory, "-Wl,--max-memory=%d" % memory,
              "-Wl,--stack-first", "-Wl,-z,stack-size=%d" % (stack_kb * 1024),
              "-Wl,--export=__heap_base", "-o", out]
           + [os.path.join(STAGE, o + ".c") for o in OBJS])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RecipeError("clang failed:\n%s%s" % (r.stdout, r.stderr))
    with open(out, "rb") as f:
        return f.read()


def imports(wasm):
    """[(module, name)] a wasm binary imports."""
    out = []
    for sid, _name, body in wasm_module.sections(wasm):
        if sid != 2:
            continue
        n, i = wasm_module._read_leb(body, 0)
        for _ in range(n):
            ml, i = wasm_module._read_leb(body, i)
            mod = body[i:i + ml].decode()
            i += ml
            nl, i = wasm_module._read_leb(body, i)
            name = body[i:i + nl].decode()
            i += nl
            kind = body[i]
            i += 1
            if kind == 0:
                _t, i = wasm_module._read_leb(body, i)
            else:
                raise RecipeError("imports %s.%s as something other than a function"
                                  % (mod, name))
            out.append((mod, name))
    return out


def heap_base(wasm):
    """The exported __heap_base global's value: static data and stack end."""
    idx = None
    for sid, _name, body in wasm_module.sections(wasm):
        if sid == 7:
            n, i = wasm_module._read_leb(body, 0)
            for _ in range(n):
                nl, i = wasm_module._read_leb(body, i)
                name = body[i:i + nl].decode()
                i += nl
                kind = body[i]
                gi, i = wasm_module._read_leb(body, i + 1)
                if kind == 3 and name == "__heap_base":
                    idx = gi
    for sid, _name, body in wasm_module.sections(wasm):
        if sid == 6:
            n, i = wasm_module._read_leb(body, 0)
            for g in range(n):
                i += 2                           # valtype, mutability
                assert body[i] == 0x41, "a global not initialised by i32.const"
                v, i = wasm_module._read_leb(body, i + 1)
                i += 1                           # end
                if g == idx:
                    return v
    raise RecipeError("no __heap_base export")


def build(zone_mb, heap_kb, stack_kb, out_dir, chips=("esp32s3", "esp32p4")):
    """Build the cart folder `out_dir`/doom.moy. Returns its path."""
    sources = doomgeneric()
    wad, id_licence = shareware_wad()
    sdk = wasi_sdk()
    print("\n" + "=" * 72)
    print(GPL_NOTICE)
    print(sources["LICENSE"].decode("latin-1").split("Preamble")[0].rstrip())
    print("=" * 72)
    print("The shareware doom1.wad, id Software's licence (Debian's copyright file):\n")
    print(id_licence)
    print("=" * 72)
    print(CART_NOTICE)
    print("=" * 72 + "\n")

    stage(sources)
    probe = link(sdk, 16 << 20, stack_kb)
    base = heap_base(probe)
    memory = math.ceil((base + (zone_mb << 20) + heap_kb * 1024) / PAGE) * PAGE
    wasm = link(sdk, memory, stack_kb)
    foreign = [m + "." + n for m, n in imports(wasm) if m != "moy"]
    if foreign:
        raise RecipeError("doom.wasm imports outside \"moy\": %s" % ", ".join(foreign))

    cart = os.path.join(out_dir, "doom.moy")
    if os.path.isdir(cart):
        shutil.rmtree(cart)
    os.makedirs(cart)
    with open(os.path.join(cart, "main.wasm"), "wb") as f:
        f.write(wasm)
    with open(os.path.join(cart, "doom1.wad"), "wb") as f:
        f.write(wad)
    manifest = {"format": "moy-1", "title": "Doom", "runtime": "wasm",
                "main": "main.wasm", "memory": memory // PAGE, "fps": "free",
                "input": ["buttons", "touch", "keyboard"]}
    with open(os.path.join(cart, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    with open(os.path.join(cart, "config.json"), "w") as f:
        json.dump({"zone": zone_mb, "args": ""}, f, indent=2)
        f.write("\n")
    with open(os.path.join(cart, "LICENSES.txt"), "w") as f:
        f.write(CART_NOTICE + "\n" + GPL_NOTICE + "\n"
                + sources["LICENSE"].decode("latin-1") + "\n" + id_licence)
    from tools import wasm_cart
    for chip in chips:
        wasm_module.build(wasm, chip, os.path.join(cart, wasm_cart.aot_name("main.wasm", chip)))
    print("%s: memory %d pages (%d KB: static and stack %d KB, zone %d MB, heap %d KB)"
          % (cart, memory // PAGE, memory // 1024, base // 1024, zone_mb, heap_kb))
    for name in sorted(os.listdir(cart)):
        print("  %-22s %9d bytes" % (name, os.path.getsize(os.path.join(cart, name))))
    return cart


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--zone", type=int, default=1,
                    help="Doom's zone, whole MiB (-mb); 1 is the smallest the "
                    "shareware episode loads and plays in")
    ap.add_argument("--heap", type=int, default=64,
                    help="KB of linear memory for Doom's mallocs outside the zone")
    ap.add_argument("--stack", type=int, default=64, help="KB of wasm stack")
    ap.add_argument("--out", default=OUT, help="where doom.moy goes")
    ap.add_argument("--chip", action="append",
                    help="build only this chip's module (repeatable)")
    args = ap.parse_args(argv)
    try:
        build(args.zone, args.heap, args.stack, args.out,
              chips=tuple(args.chip) if args.chip else ("esp32s3", "esp32p4"))
    except (RecipeError, wasm_module.ToolError) as exc:
        print("build_cart: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
