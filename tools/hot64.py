#!/usr/bin/env python3
"""No new call to the compiler's 64-bit helpers in the hot C.

    tools/hot64.py check BUILD_DIR      what every board build runs (exit 1 on
                                        a new call or a stale allowance)
    tools/hot64.py list BUILD_DIR       every hot helper call the build links

Neither chip divides 64-bit integers in hardware: the S3's Xtensa and the P4's
RISC-V (RV32IMAFC) both call libgcc for `/` and `%` on an int64_t
(`__divdi3`, `__udivdi3`, `__moddi3`, `__umoddi3`), a loop of about a hundred
cycles where a 32-bit divide is one instruction, and the Xtensa may call
`__muldi3` for a multiply it cannot widen. A `moy_now_us() / 1000` in a
per-pixel or per-sample loop is that call every time round, and nothing in the
source says so. A 64-bit multiply the compiler inlines makes no call and shows
only in the disassembly: this guard does not see it.

WHAT IT READS. The objects of our hot C as the build LINKED them (the link
map's input objects under `native/.staged/`), each through the chip's own
objdump (`-r -t`): a relocation against a helper in an executable section is a
call, and the function it sits in is the defined function symbol covering its
offset in that section (else the section's name: `-ffunction-sections` names
`.text.<function>`). A literal pool's relocation (Xtensa's `.literal.*`) is the
call's address, not a second call. Compiler clones (`.part.0`, `$constprop$0`,
`.isra.0`) count as the function they were cut from.

THE HOT C (`HOT`): the raster (`moy_gfx`), the presenters, flush and fold
(`moy_glass`, `moy_flush`), the audio feeder and mixer (`moy_audio`, libmoy's
mixer under it), and the kernel's frame loop (`moy_loop.c`, `moy_loop_board.c`,
and `moy_wd.c`'s watchdog feed, which every frame calls).

THE ALLOWANCES (`ALLOW`): each call linked when the guard was made, by chip,
object, function and helper, with why it may stay. The list only shrinks: a
call it does not name fails the build, and so does an allowance whose function
this build links with the call gone (delete the line).
`tests/test_hot64.py` pins the scanner on captured objdump text, and the
allowance count with a ratchet.
"""

from __future__ import annotations

import fnmatch
import glob
import os
import re
import shutil
import subprocess
import sys

HELPERS = ("__divdi3", "__udivdi3", "__moddi3", "__umoddi3", "__muldi3")

# Object paths under native/.staged/ (the board's staged copy of native/).
HOT = (
    "moy_gfx/*",
    "moy_glass/*",
    "moy_flush/*",
    "moy_audio/*",
    "moy_kernel/moy_loop.c",
    "moy_kernel/moy_loop_board.c",
    "moy_kernel/moy_wd.c",
)

_BAKE = ("the theme backdrop's bake: drawn once into the wallpaper's kept "
         "picture when the look or the glass size changes "
         "(runtime/system/wallpaper.py `_draw_backdrop`), then blitted 1:1")
_SETUP = ("the clip's set-up, once a call and never a pixel: the origin moved "
          "by the clip in 64 bits so that no scale overflows")
_GLYPH = ("a story slide's outline text: a glyph's points scaled to the em and "
          "its curves flattened, once a point, never a pixel")
_RASTER = ("the 64-bit fallback of phi2 and x_at for an edge 1024 px wide or "
           "724 px tall; the per-pixel path divides in 32 bits")
_ICON = ("the boot splash's icons (moy_scr.h, no canvas): once a stroke's "
         "end, never a pixel")
_RESAMPLE = ("a clip's resample, once when a cart loads the sample, never in "
             "the mix")
_PROBE = ("the output's rate probe, a diagnostic the dev channel asks for, "
          "never in the feed")
_RATE = ("a stream's step, once when the output rate changes, never a frame "
         "(libmoy, vendored from moy-spec)")

# (arch, object under native/, function, helper): why the call may stay.
ALLOW = {
    ("riscv", "moy_gfx/moy_backdrop.c", "bd_read", "__divdi3"): _BAKE,
    ("riscv", "moy_gfx/moy_backdrop.c", "draw_wave", "__divdi3"): _BAKE,
    ("riscv", "moy_gfx/moy_backdrop.c", "mg_backdrop", "__divdi3"): _BAKE,
    ("riscv", "moy_gfx/moy_backdrop.c", "row_paint", "__divdi3"): _BAKE,
    ("riscv", "moy_gfx/moy_gfx_kernels.c", "mg_blit_indices_scale", "__divdi3"): _SETUP,
    ("riscv", "moy_gfx/moy_gfx_kernels.c", "mg_blit_indices_scale", "__moddi3"): _SETUP,
    ("riscv", "moy_gfx/moy_path.c", "glyph_edges", "__divdi3"): _GLYPH,
    ("riscv", "moy_gfx/moy_path.c", "mg_path_text", "__divdi3"): _GLYPH,
    ("riscv", "moy_gfx/moy_path.c", "moy_path_width", "__divdi3"): _GLYPH,
    ("riscv", "moy_gfx/moy_path.c", "quad", "__divdi3"): _GLYPH,
    ("riscv", "moy_gfx/moy_path.c", "raster", "__divdi3"): _RASTER,
    ("riscv", "moy_gfx/moy_scr.c", "moy_scr_icon", "__divdi3"): _ICON,
    ("xtensa", "moy_gfx/moy_backdrop.c", "bd_read", "__divdi3"): _BAKE,
    ("xtensa", "moy_gfx/moy_backdrop.c", "draw_wave", "__divdi3"): _BAKE,
    ("xtensa", "moy_gfx/moy_backdrop.c", "mg_backdrop", "__divdi3"): _BAKE,
    ("xtensa", "moy_gfx/moy_backdrop.c", "row_paint", "__divdi3"): _BAKE,
    ("xtensa", "moy_gfx/moy_gfx_kernels.c", "mg_blit_indices_scale", "__divdi3"): _SETUP,
    ("xtensa", "moy_gfx/moy_gfx_kernels.c", "mg_blit_indices_scale", "__moddi3"): _SETUP,
    ("xtensa", "moy_gfx/moy_path.c", "glyph_edges", "__divdi3"): _GLYPH,
    ("xtensa", "moy_gfx/moy_path.c", "mg_path_text", "__divdi3"): _GLYPH,
    ("xtensa", "moy_gfx/moy_path.c", "moy_path_width", "__divdi3"): _GLYPH,
    ("xtensa", "moy_gfx/moy_path.c", "quad", "__divdi3"): _GLYPH,
    ("xtensa", "moy_gfx/moy_path.c", "raster", "__divdi3"): _RASTER,
    ("xtensa", "moy_gfx/moy_scr.c", "moy_scr_icon", "__divdi3"): _ICON,
    ("riscv", "moy_audio/moy_aud.c", "moy_aud_sample_load", "__udivdi3"): _RESAMPLE,
    ("riscv", "moy_audio/moy_aud_out.c", "moy_aud_out_probe", "__udivdi3"): _PROBE,
    ("riscv", "moy_audio/moy_aud_out.c", "moy_aud_out_probe", "__umoddi3"): _PROBE,
    ("riscv", "moy_audio/libmoy/moy_audio.c", "moy_stream_mix", "__udivdi3"): _RATE,
    ("xtensa", "moy_audio/moy_aud.c", "moy_aud_sample_load", "__udivdi3"): _RESAMPLE,
    ("xtensa", "moy_audio/moy_aud_out.c", "moy_aud_out_probe", "__udivdi3"): _PROBE,
    ("xtensa", "moy_audio/moy_aud_out.c", "moy_aud_out_probe", "__umoddi3"): _PROBE,
    ("xtensa", "moy_audio/libmoy/moy_audio.c", "moy_stream_mix", "__udivdi3"): _RATE,
}

_STAGED = re.compile(r"(?:^|/)native/\.staged/(.+?)\.obj$")
_CLONE = re.compile(r"([.$](part|constprop|isra|cold|lto_priv)[.$]\d+)+$")
_SYM = re.compile(r"^([0-9a-fA-F]+)\s+(\S*)\s+(\S*)\s+(\S+)\s+([0-9a-fA-F]+)\s+(\S+)$")
_RELOC_FOR = re.compile(r"^RELOCATION RECORDS FOR \[(.+)\]:")
_RELOC = re.compile(r"^([0-9a-fA-F]+)\s+(\S+)\s+(\S+?)(?:[+-]0x[0-9a-fA-F]+)?$")


def base_name(fn):
    """A compiler clone's function name as the source names it."""
    return _CLONE.sub("", fn)


def is_hot(rel):
    """`rel`: an object's source under native/ (`moy_gfx/moy_shape.c`)."""
    return any(fnmatch.fnmatchcase(rel, pat) for pat in HOT)


def linked_objects(map_path):
    """{source under native/: object path as the map names it} of every object
    under native/.staged/ the link took in."""
    out = {}
    with open(map_path, errors="replace") as f:
        for line in f:
            for tok in line.split():
                m = _STAGED.search(tok)
                if m:
                    out.setdefault(m.group(1), tok)
    return out


def defined(dump):
    """The functions an object defines, by `objdump -t` text."""
    out = set()
    for line in dump.splitlines():
        if line.startswith("RELOCATION RECORDS"):
            break
        m = _SYM.match(line.rstrip())
        if m and "F" in m.group(3):
            out.add(base_name(m.group(6)))
    return out


def calls(dump):
    """[(function, helper)] of each helper call in `objdump -r -t` text, one
    per call site."""
    funcs = {}                                  # section -> [(start, end, name)]
    relocs = []
    section = None
    for line in dump.splitlines():
        line = line.rstrip()
        m = _RELOC_FOR.match(line)
        if m:
            section = m.group(1)
            continue
        if not line or line.startswith(("SYMBOL TABLE", "OFFSET", "RELOCATION")):
            continue
        m = _SYM.match(line)
        if m and section is None and "F" in m.group(3):
            start, size = int(m.group(1), 16), int(m.group(5), 16)
            funcs.setdefault(m.group(4), []).append((start, start + size, m.group(6)))
            continue
        if section is None:
            continue
        m = _RELOC.match(line)
        if m and m.group(3) in HELPERS and "literal" not in section:
            relocs.append((section, int(m.group(1), 16), m.group(3)))
    out = []
    for sec, off, helper in relocs:
        name = None
        for start, end, fn in funcs.get(sec, ()):
            if start <= off < end:
                name = fn
                break
        if name is None:
            name = sec[len(".text."):] if sec.startswith(".text.") else sec
        out.append((base_name(name), helper))
    return out


def arch_of(build_dir):
    """`xtensa` or `riscv`, and the chip's IDF target, from the build's
    sdkconfig."""
    target = None
    with open(os.path.join(build_dir, "sdkconfig")) as f:
        for line in f:
            if line.startswith("CONFIG_IDF_TARGET="):
                target = line.split("=", 1)[1].strip().strip('"')
                break
    if target is None:
        raise SystemExit("hot64: no CONFIG_IDF_TARGET in %s/sdkconfig" % build_dir)
    return ("xtensa" if target.startswith("esp32") and target in
            ("esp32", "esp32s2", "esp32s3") else "riscv"), target


def objdump_for(target):
    """The chip's objdump: on PATH in a build (the IDF's export.sh), else the
    newest under ~/.espressif/tools."""
    name = ("xtensa-%s-elf-objdump" % target
            if target in ("esp32", "esp32s2", "esp32s3") else "riscv32-esp-elf-objdump")
    found = shutil.which(name)
    if found:
        return found
    tools = os.environ.get("IDF_TOOLS_PATH") or os.path.expanduser("~/.espressif")
    cands = sorted(glob.glob(os.path.join(tools, "tools", "*", "*", "*", "bin", name)))
    if not cands:
        raise SystemExit("hot64: %s is not on PATH or under %s/tools" % (name, tools))
    return cands[-1]


def scan(build_dir):
    """(arch, {(object, function, helper): call sites}, {(object, function)}
    the scanned objects define)."""
    arch, target = arch_of(build_dir)
    tool = objdump_for(target)
    found = {}
    funcs = set()
    objs = {rel: p for rel, p in linked_objects(
        os.path.join(build_dir, "micropython.map")).items() if is_hot(rel)}
    for rel in sorted(objs):
        path = objs[rel]
        if not os.path.isabs(path):
            path = os.path.join(build_dir, path)
        dump = subprocess.run([tool, "-r", "-t", path], check=True,
                              capture_output=True, text=True).stdout
        funcs.update((rel, fn) for fn in defined(dump))
        for fn, helper in calls(dump):
            key = (rel, fn, helper)
            found[key] = found.get(key, 0) + 1
    return arch, found, funcs


def verdict(arch, found, funcs, allow=None):
    """(new calls, stale allowances) of one build against `allow`: an
    allowance is stale where this build defines its function and the function
    makes no such call (a board whose build leaves the function out says
    nothing about it)."""
    allow = ALLOW if allow is None else allow
    mine = {k[1:] for k in allow if k[0] == arch}
    new = sorted(k for k in found if k not in mine)
    stale = sorted(k for k in mine if k[:2] in funcs and k not in found)
    return new, stale


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    if len(argv) != 2 or argv[0] not in ("check", "list"):
        print(__doc__.split("\n\n")[1])
        return 2
    verb, build_dir = argv
    arch, found, funcs = scan(build_dir)
    if verb == "list":
        for (rel, fn, helper), n in sorted(found.items()):
            print("%s %s %s %s x%d" % (arch, rel, fn, helper, n))
        return 0
    new, stale = verdict(arch, found, funcs)
    print("hot64: %d hot objects, %d helper calls in %d functions, %d allowed (%s)"
          % (len({k[0] for k in funcs}), sum(found.values()), len(found), len(found) - len(new), arch))
    for rel, fn, helper in new:
        print("!! hot64: %s calls %s in %s (%s): a 64-bit divide or multiply in "
              "hot code -- make it 32-bit, or allow it in tools/hot64.py's ALLOW "
              "with why" % (fn, helper, rel, arch), file=sys.stderr)
    for rel, fn, helper in stale:
        print("!! hot64: ALLOW names %s's %s in %s (%s), which no longer calls it: "
              "delete the line" % (fn, helper, rel, arch), file=sys.stderr)
    return 1 if new or stale else 0


if __name__ == "__main__":
    sys.exit(main())
