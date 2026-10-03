#!/usr/bin/env python3
"""Re-vendor the WAMR runtime from Moybyte's fork into native/moy_wasm.

    make vendor-wamr                                  # the fork clone's HEAD
    make vendor-wamr WAMR=/path/to/clone COMMIT=<sha>
    python3 tools/vendor_wamr.py --check              # what would change

The runtime the boards compile is moybyte-org/wasm-micro-runtime, branch
moybyte-2.4.5 (docs/wasm_tier_plan_2026-09.md: "WAMR is carried as a fork,
pinned by hash"). This copies the subset the engine needs -- the AOT loader
and runtime, the interpreter tier (both of WAMR's interpreters and the plain
.wasm loader, landed 2026-09-30), the common layer, the esp-idf platform, the
allocator and the utilities -- and none of the compiler, WASI, the builtin
libc, the mini loader, the tests or the samples. The compilation-tier HEADERS
the AOT code includes for shared types come across; its sources do not.

Files are read from the clone's GIT OBJECTS at one commit (`git show
<commit>:<path>`), never from its working tree, so a stamp names exactly the
bytes that were copied and a dirty clone cannot leak into the board image.

Why vendor rather than fetch at build time is tools/vendor_libmoy.py's answer:
a board build is an ESP-IDF tree that wants its sources on disk, and a network
fetch in the middle of every build is the thing that answer declines.

The stamp is native/moy_wasm/wamr_vendor.json (commit + a sha256 per file);
tests/test_wamr_vendor.py holds the copy to it. The same commit is written to
native/moy_wasm/wamr_pin.h, a record only: `moy_wasm.FORK` reports it, and a
module's key names the compiled-code format instead (wasm_format_version.json).
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(ROOT, "native", "moy_wasm")
DEST = os.path.join(MODULE, "wamr")
MANIFEST = os.path.join(MODULE, "wamr_vendor.json")
PIN_HEADER = os.path.join(MODULE, "wamr_pin.h")
DEFAULT_CLONE = os.path.join(ROOT, "experiments", "wasm_aot", "wamr")
FORK_REPO = "moybyte-org/wasm-micro-runtime"
FORK_BRANCH = "moybyte-2.4.5"

# What crosses, by path in the fork. Explicit, like vendor_libmoy's table: a
# glob would start vendoring whatever the fork adds next, and what the boards
# compile is a decision. The two architectures are the two the console runs.
FILES = [
    "LICENSE",
    "ATTRIBUTIONS.md",
    "core/config.h",
    "core/version.h",
    # the public API
    "core/iwasm/include/lib_export.h",
    "core/iwasm/include/wasm_c_api.h",
    "core/iwasm/include/wasm_export.h",
    # the AOT loader and runtime, and the relocators for Xtensa and RISC-V
    "core/iwasm/aot/aot_intrinsic.c",
    "core/iwasm/aot/aot_intrinsic.h",
    "core/iwasm/aot/aot_loader.c",
    "core/iwasm/aot/aot_reloc.h",
    "core/iwasm/aot/aot_runtime.c",
    "core/iwasm/aot/aot_runtime.h",
    "core/iwasm/aot/arch/aot_reloc_riscv.c",
    "core/iwasm/aot/arch/aot_reloc_xtensa.c",
    # the common layer. wasm_application.c stays behind (the command-line
    # entry); wasm_c_api.c comes because the AOT runtime's import path calls
    # into it, and the linker keeps only what is reached.
    "core/iwasm/common/arch/invokeNative_riscv.S",
    "core/iwasm/common/arch/invokeNative_xtensa.s",
    "core/iwasm/common/wasm_blocking_op.c",
    "core/iwasm/common/wasm_c_api.c",
    "core/iwasm/common/wasm_c_api_internal.h",
    "core/iwasm/common/wasm_exec_env.c",
    "core/iwasm/common/wasm_exec_env.h",
    "core/iwasm/common/wasm_loader_common.c",
    "core/iwasm/common/wasm_loader_common.h",
    "core/iwasm/common/wasm_memory.c",
    "core/iwasm/common/wasm_memory.h",
    "core/iwasm/common/wasm_native.c",
    "core/iwasm/common/wasm_native.h",
    "core/iwasm/common/wasm_runtime_common.c",
    "core/iwasm/common/wasm_runtime_common.h",
    "core/iwasm/common/wasm_shared_memory.c",
    "core/iwasm/common/wasm_shared_memory.h",
    "core/iwasm/common/wasm_suspend_flags.h",
    # headers the AOT code includes for types it shares with the compiler --
    # headers only, no sources
    "core/iwasm/compilation/aot.h",
    "core/iwasm/compilation/aot_stack_frame.h",
    # the interpreter tier (docs/wasm_tier_plan_2026-09.md, "A cart survives
    # its firmware", 2026-09-30): the loader for plain .wasm bytecode and both
    # of WAMR's interpreters, so a board picks classic or fast by #158's
    # measurement the same way it already picks its reloc arch -- one file
    # compiled, one left on disk unused (micropython.cmake's
    # MOY_WASM_FAST_INTERP). No mini loader: WASM_ENABLE_MINI_LOADER stays 0,
    # the same decision the AOT build already made for the loader's full
    # validation.
    "core/iwasm/interpreter/wasm.h",
    "core/iwasm/interpreter/wasm_interp.h",
    "core/iwasm/interpreter/wasm_interp_classic.c",
    "core/iwasm/interpreter/wasm_interp_fast.c",
    "core/iwasm/interpreter/wasm_loader.c",
    "core/iwasm/interpreter/wasm_loader.h",
    "core/iwasm/interpreter/wasm_opcode.h",
    "core/iwasm/interpreter/wasm_runtime.c",
    "core/iwasm/interpreter/wasm_runtime.h",
    # the allocator
    "core/shared/mem-alloc/ems/ems_alloc.c",
    "core/shared/mem-alloc/ems/ems_gc.c",
    "core/shared/mem-alloc/ems/ems_gc.h",
    "core/shared/mem-alloc/ems/ems_gc_internal.h",
    "core/shared/mem-alloc/ems/ems_hmu.c",
    "core/shared/mem-alloc/ems/ems_kfc.c",
    "core/shared/mem-alloc/mem_alloc.c",
    "core/shared/mem-alloc/mem_alloc.h",
    # the esp-idf platform layer, whole
    "core/shared/platform/common/libc-util/libc_errno.c",
    "core/shared/platform/common/libc-util/libc_errno.h",
    "core/shared/platform/esp-idf/espidf_clock.c",
    "core/shared/platform/esp-idf/espidf_file.c",
    "core/shared/platform/esp-idf/espidf_malloc.c",
    "core/shared/platform/esp-idf/espidf_memmap.c",
    "core/shared/platform/esp-idf/espidf_platform.c",
    "core/shared/platform/esp-idf/espidf_socket.c",
    "core/shared/platform/esp-idf/espidf_thread.c",
    "core/shared/platform/esp-idf/platform_internal.h",
    "core/shared/platform/include/platform_api_extension.h",
    "core/shared/platform/include/platform_api_vmcore.h",
    "core/shared/platform/include/platform_common.h",
    "core/shared/platform/include/platform_wasi_types.h",
    # utilities
    "core/shared/utils/bh_assert.c",
    "core/shared/utils/bh_assert.h",
    "core/shared/utils/bh_atomic.h",
    "core/shared/utils/bh_bitmap.c",
    "core/shared/utils/bh_bitmap.h",
    "core/shared/utils/bh_common.c",
    "core/shared/utils/bh_common.h",
    "core/shared/utils/bh_hashmap.c",
    "core/shared/utils/bh_hashmap.h",
    "core/shared/utils/bh_leb128.c",
    "core/shared/utils/bh_leb128.h",
    "core/shared/utils/bh_list.c",
    "core/shared/utils/bh_list.h",
    "core/shared/utils/bh_log.c",
    "core/shared/utils/bh_log.h",
    "core/shared/utils/bh_platform.h",
    "core/shared/utils/bh_queue.c",
    "core/shared/utils/bh_queue.h",
    "core/shared/utils/bh_vector.c",
    "core/shared/utils/bh_vector.h",
    "core/shared/utils/gnuc.h",
    "core/shared/utils/runtime_timer.c",
    "core/shared/utils/runtime_timer.h",
]


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def git(clone, *args, binary=False):
    out = subprocess.check_output(("git", "-C", clone) + args,
                                  stderr=subprocess.DEVNULL)
    return out if binary else out.decode().strip()


def resolve(clone, commit):
    """`(full commit, commit date)` in the clone, or SystemExit."""
    try:
        full = git(clone, "rev-parse", "--verify", (commit or "HEAD") + "^{commit}")
    except Exception:
        sys.exit("vendor-wamr: %s is not a commit in %s" % (commit or "HEAD", clone))
    return full, git(clone, "log", "-1", "--format=%cs", full)


def blob(clone, commit, rel):
    """The file's bytes at `commit`, or None when it is not in that tree."""
    try:
        return git(clone, "show", "%s:%s" % (commit, rel), binary=True)
    except subprocess.CalledProcessError:
        return None


def pin_header(commit):
    return ("/* GENERATED by tools/vendor_wamr.py -- the fork commit the vendored\n"
            " * runtime in wamr/ was copied from (wamr_vendor.json). A record only:\n"
            " * moy_wasm.FORK reports it; a module's key names the compiled-code\n"
            " * format (wasm_format_version.json). Do not edit. */\n"
            "#ifndef MOY_WASM_PIN_H\n#define MOY_WASM_PIN_H\n"
            "#define MOY_WASM_FORK_COMMIT \"%s\"\n#endif\n" % commit)


def vendored_paths():
    """Every file this script owns under native/moy_wasm/wamr, repo-relative."""
    return sorted(os.path.relpath(os.path.join(DEST, rel), ROOT).replace(os.sep, "/")
                  for rel in FILES)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wamr", default=os.environ.get("MOYBYTE_WAMR", DEFAULT_CLONE),
                    help="a clone of %s (default: experiments/wasm_aot/wamr)" % FORK_REPO)
    ap.add_argument("--commit", help="the commit to vendor (default: the clone's HEAD)")
    ap.add_argument("--check", action="store_true",
                    help="report what would change; write nothing")
    args = ap.parse_args(argv)

    if not os.path.isdir(os.path.join(args.wamr, ".git")):
        print("vendor-wamr: no clone at %s\n  git clone -b %s https://github.com/%s.git %s"
              % (args.wamr, FORK_BRANCH, FORK_REPO, args.wamr), file=sys.stderr)
        return 2
    commit, date = resolve(args.wamr, args.commit)
    print("vendor-wamr: %s @ %s" % (args.wamr, commit[:12]))

    changed, missing, files = [], [], {}
    for rel in FILES:
        data = blob(args.wamr, commit, rel)
        if data is None:
            missing.append(rel)
            continue
        dst = os.path.join(DEST, rel)
        files[os.path.relpath(dst, ROOT).replace(os.sep, "/")] = sha256_bytes(data)
        if os.path.isfile(dst) and sha256_file(dst) == sha256_bytes(data):
            continue
        changed.append(os.path.relpath(dst, ROOT))
        if not args.check:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)
    if missing:
        print("  !! not in %s: %s" % (commit[:12], ", ".join(missing)), file=sys.stderr)
        return 2

    # Nothing in wamr/ is authored here, so anything not in the table goes.
    strays = []
    for dirpath, _dirs, names in os.walk(DEST):
        for name in names:
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT).replace(os.sep, "/")
            if rel not in files:
                strays.append(rel)
    for rel in strays:
        changed.append(rel + " (removed)")
        if not args.check:
            os.remove(os.path.join(ROOT, rel))

    header = pin_header(commit)
    if not os.path.isfile(PIN_HEADER) or open(PIN_HEADER).read() != header:
        changed.append(os.path.relpath(PIN_HEADER, ROOT))
        if not args.check:
            with open(PIN_HEADER, "w", newline="\n") as f:
                f.write(header)

    for path in changed:
        print("  %s %s" % ("would update" if args.check else "updated", path))
    if not changed:
        print("  already up to date")
    if args.check:
        return 1 if changed else 0

    for dirpath, _dirs, _names in os.walk(DEST, topdown=False):
        if not os.listdir(dirpath):
            os.rmdir(dirpath)
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"upstream": {"repo": FORK_REPO, "branch": FORK_BRANCH,
                                "commit": commit, "date": date},
                   "files": files}, f, indent=2, sort_keys=True)
        f.write("\n")
    print("  stamped %s" % os.path.relpath(MANIFEST, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
