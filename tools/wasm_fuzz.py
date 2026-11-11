#!/usr/bin/env python3
"""Fuzz the wasm tier's engine, and replay what the fuzzing kept.

    python3 tools/wasm_fuzz.py fuzz interp_fast64 --seconds 3600 --forks 3
    python3 tools/wasm_fuzz.py fuzz aot_rv32 --wamr /path/to/fork/clone
    python3 tools/wasm_fuzz.py replay interp_fast64 FILE...
    python3 tools/wasm_fuzz.py keep interp_fast64 CORPUS_DIR   # into the fixtures
    python3 tools/wasm_fuzz.py seeds DIR                        # the hostile seeds

Each TARGET is the fork's runtime (WAMR, native/moy_wasm/README.md) built from
its sources at the pin -- the host's fetched checkout (runtime/host/
wasm_binding.py) unless `--wamr` names a clone -- by one harness in
tools/wasm_fuzz/:

  interp_fast64     the host's configuration (wasm_binding.WAMR_CMAKE): the
                    fast interpreter, 64-bit, software bounds checks; load,
                    instantiate and a bounded run of every export
                    (fuzz_interp.c)
  interp_classic32  a board's configuration: the classic interpreter beside
                    AOT, 32-bit pointers (fuzz_interp.c)
  aot_rv32          a P4's module file through what the board does before
  aot_xtensa        any of it runs: moy_ota_admit_module, the AOT loader with
                    the chip's relocations, the provenance key, instantiation
                    with no code run (fuzz_aot.c); 32-bit; MOY_FUZZ_CONSENT=0
                    fuzzes a cart without full_access

`fuzz` builds under libFuzzer with AddressSanitizer and UndefinedBehavior-
Sanitizer (clang); `replay` builds the same harness with a plain main, under
the sanitizers where the compiler has them, and runs it over files: what
tests/test_wasm_hostile.py does with tests/fixtures/wasm_fuzz/. UBSan leaves
out three checks WAMR trips by design: misaligned access (both chips and
x86 take it), NULL plus 0, and a left shift of a negative number.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(ROOT, "tools", "wasm_fuzz")
BUILD = os.path.join(ROOT, ".build", "wasm_fuzz")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "wasm_fuzz")
TARGETS = ("interp_fast64", "interp_classic32", "aot_rv32", "aot_xtensa")

# What `keep` leaves in the fixtures, per target: no input past MAX_INPUT
# bytes, no more than MAX_DIR bytes in all.
MAX_INPUT = 8 * 1024
MAX_DIR = 384 * 1024

COMMON = """
iwasm/interpreter/wasm_loader.c iwasm/interpreter/wasm_runtime.c
iwasm/common/wasm_blocking_op.c iwasm/common/wasm_c_api.c iwasm/common/wasm_exec_env.c
iwasm/common/wasm_loader_common.c iwasm/common/wasm_memory.c iwasm/common/wasm_native.c
iwasm/common/wasm_runtime_common.c iwasm/common/wasm_shared_memory.c
shared/mem-alloc/ems/ems_alloc.c shared/mem-alloc/ems/ems_gc.c shared/mem-alloc/ems/ems_hmu.c
shared/mem-alloc/ems/ems_kfc.c shared/mem-alloc/mem_alloc.c
shared/platform/linux/platform_init.c
shared/platform/common/posix/posix_blocking_op.c shared/platform/common/posix/posix_malloc.c
shared/platform/common/posix/posix_memmap.c shared/platform/common/posix/posix_sleep.c
shared/platform/common/posix/posix_thread.c shared/platform/common/posix/posix_time.c
shared/utils/bh_assert.c shared/utils/bh_bitmap.c shared/utils/bh_common.c
shared/utils/bh_hashmap.c shared/utils/bh_leb128.c shared/utils/bh_list.c shared/utils/bh_log.c
shared/utils/bh_queue.c shared/utils/bh_vector.c shared/utils/runtime_timer.c
""".split()
AOT = "iwasm/aot/aot_intrinsic.c iwasm/aot/aot_loader.c iwasm/aot/aot_runtime.c".split()

# The board build's WAMR switches (native/moy_wasm/micropython.cmake), with
# instruction metering so a run is bounded and the Linux platform layer.
DEFS = """
-DBH_PLATFORM_LINUX -D_GNU_SOURCE -DWASM_HAVE_MREMAP=1 -DBH_MALLOC=wasm_runtime_malloc
-DBH_FREE=wasm_runtime_free -DWASM_ENABLE_JIT=0 -DWASM_ENABLE_LIBC_BUILTIN=0
-DWASM_ENABLE_LIBC_WASI=0 -DWASM_ENABLE_MULTI_MODULE=0 -DWASM_ENABLE_THREAD_MGR=0
-DWASM_ENABLE_SHARED_MEMORY=0 -DWASM_ENABLE_BULK_MEMORY=1 -DWASM_ENABLE_REF_TYPES=1
-DWASM_ENABLE_MINI_LOADER=0 -DWASM_ENABLE_LOAD_CUSTOM_SECTION=1 -DWASM_ENABLE_AOT_INTRINSICS=1
-DWASM_ENABLE_QUICK_AOT_ENTRY=0 -DWASM_ENABLE_SHRUNK_MEMORY=1 -DWASM_ENABLE_EXTENDED_CONST_EXPR=0
-DWASM_DISABLE_HW_BOUND_CHECK=1 -DWASM_DISABLE_STACK_HW_BOUND_CHECK=1
-DWASM_DISABLE_WAKEUP_BLOCKING_OP=0 -DWASM_ENABLE_INSTRUCTION_METERING=1
-DWASM_DISABLE_WRITE_GS_BASE=1
""".split()
SAN = ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined",
       "-fno-sanitize=alignment,pointer-overflow,shift-base"]
CLANG_ONLY = ["-fno-sanitize=function"]


def _wamr_src(wamr=None):
    if wamr:
        return os.path.abspath(wamr)
    sys.path.insert(0, ROOT)
    from runtime import wasm_binding
    got = wasm_binding.wamr()
    if got is None:
        raise RuntimeError("no WAMR at the pin: %s" % wasm_binding.why_unavailable())
    return os.path.dirname(os.path.dirname(os.path.dirname(got[0])))


def _target_args(target, core, out_dir):
    """(defines and sources, needs -m32) of `target` over the fork's core/."""
    srcs = [os.path.join(core, s) for s in COMMON]
    if target == "interp_fast64":
        return (["-DBUILD_TARGET_X86_64", "-DWASM_ENABLE_AOT=0", "-DWASM_ENABLE_INTERP=1",
                 "-DWASM_ENABLE_FAST_INTERP=1"] + srcs
                + [os.path.join(core, "iwasm/interpreter/wasm_interp_fast.c"),
                   os.path.join(core, "iwasm/common/arch/invokeNative_em64.s"),
                   os.path.join(HERE, "fuzz_interp.c")]), False
    aot = [os.path.join(core, s) for s in AOT]
    classic = os.path.join(core, "iwasm/interpreter/wasm_interp_classic.c")
    base = ["-DWASM_ENABLE_AOT=1", "-DWASM_ENABLE_INTERP=1", "-DWASM_ENABLE_FAST_INTERP=0"]
    if target == "interp_classic32":
        return (base + ["-DBUILD_TARGET_X86_32"] + srcs + aot
                + [os.path.join(core, "iwasm/aot/arch/aot_reloc_x86_32.c"), classic,
                   os.path.join(core, "iwasm/common/arch/invokeNative_ia32.s"),
                   os.path.join(HERE, "fuzz_interp.c")]), True
    if target == "aot_rv32":
        # The relocation's instruction-cache fence is RISC-V's: no-op it on x86.
        reloc = os.path.join(out_dir, "aot_reloc_riscv_host.c")
        with open(os.path.join(core, "iwasm/aot/arch/aot_reloc_riscv.c")) as f:
            text = f.read()
        with open(reloc, "w") as f:
            f.write(text.replace('__asm__ volatile("fence.i")', "(void)0")
                    .replace('__asm__ volatile("fence")', "(void)0"))
        chip = ["-DBUILD_TARGET_RISCV32_ILP32F", '-DFUZZ_CHIP="esp32p4"']
    elif target == "aot_xtensa":
        reloc = os.path.join(core, "iwasm/aot/arch/aot_reloc_xtensa.c")
        chip = ["-DBUILD_TARGET_XTENSA", '-DFUZZ_CHIP="esp32s3"']
    else:
        raise ValueError("no target %s" % target)
    net = os.path.join(ROOT, "native")
    return (base + chip + srcs + aot + [reloc, classic,
            "-I" + os.path.join(net, "moy_net"), "-I" + os.path.join(net, "moy_spine"),
            "-I" + os.path.join(net, "moy_store"),
            os.path.join(net, "moy_net", "moy_ota.c"), os.path.join(net, "moy_spine", "moy_json.c"),
            os.path.join(HERE, "aot_stubs.c"), os.path.join(HERE, "never.c"),
            os.path.join(HERE, "fuzz_aot.c")]), True


def _fuzz_key(out_dir, pem=None):
    """fuzzkey.h in `out_dir`: the modulus the AOT harness's admission
    trusts, a throwaway key's (`pem`) or zeros (no signature verifies)."""
    n = bytes(256)
    if pem:
        from cryptography.hazmat.primitives import serialization
        with open(pem, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), None)
        n = key.public_key().public_numbers().n.to_bytes(256, "big")
    with open(os.path.join(out_dir, "fuzzkey.h"), "w") as f:
        f.write("static const uint8_t FUZZ_KEY_N[256] = {%s};\n" % ",".join(map(str, n)))


def _compiler(fuzz):
    cc = shutil.which("clang") if fuzz else (os.environ.get("CC") or shutil.which("cc")
                                             or shutil.which("clang") or shutil.which("gcc"))
    if cc is None:
        raise RuntimeError("no %s" % ("clang" if fuzz else "C compiler"))
    return cc


def _works(cc, flags):
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "t.c")
        with open(src, "w") as f:
            f.write("int main(void){return 0;}\n")
        return subprocess.run([cc] + flags + [src, "-o", os.path.join(d, "t")],
                              capture_output=True).returncode == 0


def build(target, fuzz=False, wamr=None, key=None):
    """The harness binary for `target`: libFuzzer's with `fuzz`, else a
    replay. None when this machine cannot build it (no 32-bit libc for a
    32-bit target)."""
    core = os.path.join(_wamr_src(wamr), "core")
    cc = _compiler(fuzz)
    tag = hashlib.sha256()
    for p in [core] + sorted(os.listdir(HERE)):
        tag.update(p.encode())
        f = os.path.join(HERE, p)
        if os.path.isfile(f):
            with open(f, "rb") as fh:
                tag.update(fh.read())
    tag.update(("%s %s %s %s" % (target, fuzz, cc, key)).encode())
    out_dir = os.path.join(BUILD, "%s-%s" % (target, tag.hexdigest()[:12]))
    exe = os.path.join(out_dir, ("fuzz_" if fuzz else "replay_") + target)
    if os.path.isfile(exe) and wamr is None:
        return exe
    os.makedirs(out_dir, exist_ok=True)
    args, m32 = _target_args(target, core, out_dir)
    flags = ["-m32"] if m32 else []
    if m32 and not _works(cc, flags):
        return None
    clang = "clang" in os.path.basename(cc)
    san = SAN + (CLANG_ONLY if clang else [])
    if fuzz:
        flags += ["-fsanitize=fuzzer"] + san
    elif _works(cc, flags + san):
        flags += san
    _fuzz_key(out_dir, key)
    inc = [os.path.join(core, d) for d in (
        "iwasm/include", "iwasm/common", "iwasm/aot", "iwasm/aot/arch", "iwasm/interpreter",
        "shared/platform/linux", "shared/platform/include", "shared/mem-alloc", "shared/utils", "")]
    cmd = ([cc, "-g", "-O1", "-fno-omit-frame-pointer", "-w"] + flags
           + ["-I" + d for d in inc + [os.path.join(ROOT, "native", "moy_wasm"), out_dir]]
           + DEFS + ([] if fuzz else ["-DMOY_FUZZ_REPLAY", os.path.join(HERE, "replay_main.c")])
           + args + ["-o", exe, "-lpthread", "-lm"] + (["-latomic"] if m32 else []))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("%s did not build:\n%s" % (target, r.stderr[-4000:]))
    return exe


def replay(target, files, timeout=120, wamr=None):
    """The files among `files` the replay of `target` does not survive, as
    (file, its last lines of output); None when the target cannot be built
    here."""
    exe = build(target, wamr=wamr)
    if exe is None:
        return None
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:allocator_may_return_null=1",
               UBSAN_OPTIONS="print_stacktrace=1")
    bad = []
    pending = list(files)
    while pending:
        r = subprocess.run([exe] + pending, capture_output=True, text=True, env=env,
                           timeout=timeout)
        if r.returncode == 0:
            break
        # The driver names each file before it runs it: the last named is
        # the one that did not survive; the rest go on after it.
        ran = [ln[len("replay ")::] for ln in r.stdout.splitlines() if ln.startswith("replay ")]
        if not ran:
            bad.append((pending[0], r.stderr[-2000:]))
            pending = pending[1:]
            continue
        bad.append((ran[-1], r.stderr[-2000:]))
        pending = pending[pending.index(ran[-1]) + 1:]
    return bad


def fuzz(target, seconds, forks, wamr=None, key=None, seeds=(), consent=True):
    exe = build(target, fuzz=True, wamr=wamr, key=key)
    if exe is None:
        raise RuntimeError("%s needs a 32-bit libc here" % target)
    work = os.path.join(BUILD, "runs", target + ("" if consent else "-admit"))
    for d in ("corpus", "crashes", "tmp"):
        os.makedirs(os.path.join(work, d), exist_ok=True)
    env = dict(os.environ, TMPDIR=os.path.join(work, "tmp"),
               MOY_FUZZ_CONSENT="1" if consent else "0")
    cmd = [exe, "-fork=%d" % forks, "-ignore_crashes=1", "-ignore_timeouts=1",
           "-ignore_ooms=1", "-timeout=20", "-rss_limit_mb=3072",
           "-max_total_time=%d" % seconds,
           "-artifact_prefix=" + os.path.join(work, "crashes") + "/",
           os.path.join(work, "corpus")] + list(seeds)
    print(" ".join(cmd))
    return subprocess.call(cmd, env=env, cwd=work)


def keep(target, corpus, wamr=None):
    """Merge `corpus` into the fixtures' minimal set for `target`, inputs cut
    to MAX_INPUT, the smallest first until MAX_DIR."""
    exe = build(target, fuzz=True, wamr=wamr)
    dst = os.path.join(FIXTURES, target)
    with tempfile.TemporaryDirectory() as d:
        subprocess.run([exe, "-merge=1", "-max_len=%d" % MAX_INPUT, d, corpus]
                       + ([dst] if os.path.isdir(dst) else []),
                       capture_output=True, check=True)
        files = sorted((os.path.getsize(os.path.join(d, n)), n) for n in os.listdir(d))
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        os.makedirs(dst)
        total = 0
        for size, n in files:
            if total + size > MAX_DIR:
                break
            shutil.copy(os.path.join(d, n), os.path.join(dst, n))
            total += size
    return dst


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fuzz")
    f.add_argument("target", choices=TARGETS)
    f.add_argument("--seconds", type=int, default=3600)
    f.add_argument("--forks", type=int, default=2)
    f.add_argument("--wamr")
    f.add_argument("--key", help="a throwaway key's PEM the admission trusts")
    f.add_argument("--no-consent", action="store_true")
    f.add_argument("seeds", nargs="*")
    r = sub.add_parser("replay")
    r.add_argument("target", choices=TARGETS)
    r.add_argument("files", nargs="+")
    r.add_argument("--wamr")
    k = sub.add_parser("keep")
    k.add_argument("target", choices=TARGETS)
    k.add_argument("corpus")
    k.add_argument("--wamr")
    s = sub.add_parser("seeds")
    s.add_argument("dir")
    a = ap.parse_args(argv)
    if a.cmd == "fuzz":
        return fuzz(a.target, a.seconds, a.forks, a.wamr, a.key, a.seeds, not a.no_consent)
    if a.cmd == "replay":
        bad = replay(a.target, a.files, wamr=a.wamr)
        if bad is None:
            print("%s cannot be built here" % a.target)
            return 0
        for path, why in bad:
            print("FAILED %s\n%s" % (path, why))
        return 1 if bad else 0
    if a.cmd == "keep":
        print(keep(a.target, a.corpus, a.wamr))
        return 0
    return subprocess.call([sys.executable, "-I", os.path.join(HERE, "hostile.py"), a.dir])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
