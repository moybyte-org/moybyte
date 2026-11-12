#!/usr/bin/env python3
"""Each native component's host suite and fuzz, the twin hook's switch.

    tools/native_check.py host     [--impl c]      the suite over ctypes and on the
                                                   desktop MicroPython, the store trace
    tools/native_check.py sanitize [--impl c]      ASan+UBSan: the suite over ctypes,
                                                   the seeded fuzz, then libFuzzer
    tools/native_check.py --component spine host|sanitize ...
                                                   the same two for the spine
    tools/native_check.py --component fs sanitize  the store's, with the power-cut matrix
    tools/native_check.py --component theme sanitize
                                                   the theme loader's suite and fuzz

The components (index, spine, fs, theme) are runtime/host/native_parts.py's.
Results land as JSON in .build/native_check/ as well as on stdout.

THE HOOK. MOY_INDEX_IMPL picks the store index a build compiles in:

    c     the default: native/moy_index/moy_index.c under modmoy_index.c, and
          runtime/carts/moy_index.py left out of the frozen set, so the extensible
          builtin answers `import moy_index` on every image that takes the
          module (the boards, the browser)
    py    runtime/carts/moy_index.py, frozen; no native index at all

native/moy_index/micropython.cmake (the boards) and micropython.mk (the
desktop MicroPython, the browser) read it, tools/board_config.py drops the
Python twin from a twin's frozen set, and `make unix-micropython` builds the
twin its UNIX_MP_INDEX names (default `c`).

The spine has no hook: native/moy_spine (modmoy_spine.c over moy_route.c,
moy_settings.c and moy_ledger.c, and moy_htab.c, the handle table both
components take their slots from) is in every image, the desktop MicroPython
and the CPython host (over ctypes), and its Python twin is the suites' oracle,
tests/spine_twin.py. runtime/carts/moy_index.py stays the host's: CPython's
simulator, tools and tests import it, and it is the reference every binding is pinned against.

Each build dir records the hooks it was built with and starts its generated headers
afresh when that changes, because MicroPython keeps a dropped source's module
registration until that source is preprocessed again. A board's record
(`moy_native_config`, tools/esp32_build_lib.sh) is every MOY_*_IMPL hook in the
environment and the board's staged native modules, so flipping a board.toml
take or denial is noticed the same way; the web runner's build.sh and the
Makefile keep their own, over the two hooks.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from runtime import native_build  # noqa: E402
from runtime.native_parts import COMPONENTS, FS, INDEX, MPY, SANITIZE  # noqa: E402

OUT = os.path.join(ROOT, ".build", "native_check")
UNIX_PORT = os.path.join(MPY, "ports", "unix")
IMPLS = ("py", "c")


def impl_of(arg=None, comp=INDEX):
    v = arg or os.environ.get(comp.env) or comp.default
    if v not in IMPLS:
        raise SystemExit("%s is py or c, not %r" % (comp.env, v))
    return v


def build_env(impl, bench=False, comp=INDEX):
    """os.environ with the hook set for one build."""
    env = dict(os.environ)
    env[comp.env] = impl
    env.pop("MOY_INDEX_BENCH", None)
    if bench and impl != "py":
        env["MOY_INDEX_BENCH"] = "1"
    return env



# -- the fuzz driver ------------------------------------------------------------


def _cc_works(cc, flags, cache=INDEX.cache):
    probe = os.path.join(cache, "probe.c")
    os.makedirs(cache, exist_ok=True)
    with open(probe, "w") as f:
        f.write("#include <stdint.h>\n#include <stddef.h>\n"
                "int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n)"
                "{(void)d;(void)n;return 0;}\n"
                "#ifndef LIBFUZZER\nint main(void){return 0;}\n#endif\n")
    try:
        out = subprocess.run([cc] + flags + [probe, "-o", probe + ".bin"],
                             capture_output=True, text=True)
    except OSError:
        return False
    return out.returncode == 0


def fuzz_binary(impl="c", libfuzzer=False, comp=INDEX):
    """Build the component's fuzz driver (fuzz_index, fuzz_spine) for the C twin
    under ASan+UBSan: libFuzzer's (clang) or the seeded driver (any compiler).
    The path, or None with no toolchain that has the sanitizers. A build is
    keyed by the sources' content, made under native_build's lock and moved into
    place whole, so parallel test workers share one."""
    os.makedirs(comp.cache, exist_ok=True)
    driver, linked = comp.fuzz
    srcs = []
    for n in [driver] + linked:
        srcs.append([os.path.join(d, n) for d in comp.dirs
                     if os.path.isfile(os.path.join(d, n))][0])
    incs = [a for d in comp.dirs for a in ("-I", d)]
    if libfuzzer:
        cc = shutil.which("clang")
        flags = ["-fsanitize=fuzzer,address,undefined"] + SANITIZE[1:]
        if not cc or not _cc_works(cc, flags + ["-DLIBFUZZER"], comp.cache):
            return None
    else:
        cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
        flags = SANITIZE + ["-DMOY_%s_FUZZ_MAIN" % comp.name.upper()]
        if not cc or not _cc_works(cc, SANITIZE, comp.cache):
            return None
    flags = flags + comp.cflags
    h = hashlib.sha256(" ".join([cc] + flags).encode())
    for path in srcs + sorted(os.path.join(d, f) for d in comp.dirs
                              for f in os.listdir(d) if f.endswith(".h")):
        with open(path, "rb") as f:
            h.update(f.read())
    exe = os.path.join(comp.cache, "%s_%s%s-%s" % (
        os.path.splitext(driver)[0], impl, "_lf" if libfuzzer else "",
        h.hexdigest()[:12]))
    if os.path.exists(exe):
        return exe
    with native_build.build_lock(exe + ".lock"):
        if os.path.exists(exe):
            return exe
        tmp = "%s.%d.tmp" % (exe, os.getpid())
        cmd = ([cc, "-std=c99", "-O1", "-g", "-Wall", "-Wextra"] + incs
               + flags + srcs + ["-o", tmp])
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError("%s build failed:\n%s" % (driver, out.stderr))
        os.replace(tmp, exe)
    return exe


def fuzz_seeded(impl="c", seed=1, runs=300, comp=INDEX):
    """The seeded driver's run: (ok, seconds, output); None with no toolchain."""
    exe = fuzz_binary(impl, comp=comp)
    if exe is None:
        return None
    t = time.time()
    out = subprocess.run([exe, str(seed), str(runs)], capture_output=True,
                         text=True)
    return out.returncode == 0, time.time() - t, (out.stdout + out.stderr)


def fuzz_matrix(comp):
    """The power-cut matrix (fuzz_fs --matrix): (ok, seconds, output); None
    with no toolchain or no MicroPython tree to take the file systems from."""
    if not os.path.isdir(os.path.join(MPY, "lib", "oofatfs")):
        return None
    exe = fuzz_binary("c", comp=comp)
    if exe is None:
        return None
    t = time.time()
    out = subprocess.run([exe, "--matrix"], capture_output=True, text=True)
    return out.returncode == 0, time.time() - t, (out.stdout + out.stderr)


def fuzz_libfuzzer(impl="c", seconds=60, comp=INDEX):
    exe = fuzz_binary(impl, libfuzzer=True, comp=comp)
    if exe is None:
        return None
    corpus = os.path.join(OUT, "corpus_%s%s" % (comp.stamp, impl))
    os.makedirs(corpus, exist_ok=True)
    # clang 14's ASan runtime dies in its own init under the address-space
    # entropy of Linux 6.6 and later (vm.mmap_rnd_bits 32), a third of starts
    # here: the run goes without ASLR.
    norand = ["setarch", "-R"] if shutil.which("setarch") else []
    t = time.time()
    out = subprocess.run(norand + [exe, "-max_total_time=%d" % seconds,
                                   "-max_len=4096", "-seed=1",
                                   "-print_final_stats=1", corpus],
                         capture_output=True, text=True, cwd=OUT)
    text = out.stdout + out.stderr
    done = re.findall(r"Done (\d+) runs", text)
    cov = re.findall(r"cov: (\d+) ft: (\d+)", text)
    return {"ok": out.returncode == 0, "seconds": round(time.time() - t, 1),
            "runs": int(done[-1]) if done else None,
            "cov": int(cov[-1][0]) if cov else None,
            "features": int(cov[-1][1]) if cov else None,
            "corpus": len(os.listdir(corpus)),
            "tail": text.strip().splitlines()[-12:]}


# -- running things ---------------------------------------------------------------


def save(name, data):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    with open(path, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    print("-> %s" % os.path.relpath(path, ROOT))


def pytest(args, env=None):
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"] + args
    t = time.time()
    out = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    tail = (out.stdout + out.stderr).strip().splitlines()
    return out.returncode, round(time.time() - t, 1), tail


def unix_micropython(impl, comp=INDEX):
    """`make unix-micropython` with `comp`'s twin `impl` in both binaries (the
    other component keeps the Makefile's default; the binaries are removed
    first, so a changed hook relinks them)."""
    for b in ("build-moybyte", "build-moybyte-board"):
        try:
            os.remove(os.path.join(UNIX_PORT, b, "micropython"))
        except OSError:
            pass
    cmd = ["make", "--no-print-directory", "unix-micropython",
           "UNIX_MP_%s=%s" % (comp.name.upper(), impl)]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("make unix-micropython (%s) failed:\n%s"
                           % (impl, (out.stdout + out.stderr)[-3000:]))


def cmd_host(a):
    comp = COMPONENTS[a.component]
    impl = impl_of(a.impl, comp)
    if impl == "py":
        raise SystemExit("`host` runs the twin: --impl c")
    unix_micropython(impl, comp)
    env = dict(os.environ, MOYBYTE_REQUIRE_UNIX_MP="1")
    env[comp.env] = impl
    rc, secs, tail = pytest(comp.tests + ["tests/test_semantic_traces.py",
                                          "-k", comp.trace], env)
    print("\n".join(tail[-15:]))
    save("host-%s%s.json" % (comp.stamp, impl),
         {"impl": impl, "rc": rc, "seconds": secs, "tail": tail[-15:]})
    return rc


def libasan():
    cc = native_build.cc() or "cc"
    out = subprocess.run([cc, "-print-file-name=libasan.so"],
                         capture_output=True, text=True)
    path = out.stdout.strip()
    return path if os.path.isabs(path) and os.path.exists(path) else None


def cmd_sanitize(a):
    comp = COMPONENTS[a.component]
    impl = impl_of(a.impl, comp)
    if impl == "py":
        raise SystemExit("`sanitize` runs the twin: --impl c")
    res = {"impl": impl}
    asan = libasan()
    if asan:
        env = dict(os.environ, LD_PRELOAD=asan,
                   ASAN_OPTIONS="detect_leaks=0:abort_on_error=1",
                   UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
        env["MOY_%s_SANITIZE" % comp.name.upper()] = "1"
        env[comp.env] = impl
        rc, secs, tail = pytest(comp.tests + ["-k", "not vm and not fuzz"], env)
        res["suite"] = {"rc": rc, "seconds": secs, "tail": tail[-6:]}
        print("suite over ctypes, ASan+UBSan: rc=%d %.1fs  %s"
              % (rc, secs, tail[-1] if tail else ""))
    else:
        res["suite"] = None
        print("suite over ctypes, ASan+UBSan: no libasan beside the compiler")
    got = fuzz_seeded(impl, seed=a.seed, runs=a.runs, comp=comp)
    res["seeded"] = None if got is None else {
        "ok": got[0], "seconds": round(got[1], 1), "runs": a.runs,
        "seed": a.seed, "out": got[2].strip().splitlines()[-3:]}
    print("seeded fuzz, ASan+UBSan: %s" % (res["seeded"] or "no toolchain"))
    if comp is FS:
        got = fuzz_matrix(comp)
        res["matrix"] = None if got is None else {
            "ok": got[0], "seconds": round(got[1], 1),
            "out": got[2].strip().splitlines()[-3:]}
        print("power-cut matrix, ASan+UBSan: %s" % (res["matrix"] or "no toolchain"))
    if a.seconds:
        lf = fuzz_libfuzzer(impl, a.seconds, comp)
        res["libfuzzer"] = lf
        print("libFuzzer, ASan+UBSan: %s" % (
            "no clang with libFuzzer" if lf is None else
            "ok=%(ok)s %(runs)s runs in %(seconds)ss, cov %(cov)s, ft "
            "%(features)s, corpus %(corpus)s" % lf))
    save("sanitize-%s%s.json" % (comp.stamp, impl), res)
    bad = (res["suite"] and res["suite"]["rc"]) or not (res["seeded"] or {}).get("ok") \
        or (res.get("libfuzzer") and not res["libfuzzer"]["ok"]) \
        or (res.get("matrix") is not None and not res["matrix"]["ok"])
    return 1 if bad else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--component", choices=sorted(COMPONENTS), default="index",
                   help="the component `host` and `sanitize` run for")
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("host")
    h.add_argument("--impl", default="c")
    s = sub.add_parser("sanitize")
    s.add_argument("--impl", default="c")
    s.add_argument("--runs", type=int, default=2000)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--seconds", type=int, default=120,
                   help="libFuzzer's budget; 0 skips it")
    a = p.parse_args(argv)
    return {"host": cmd_host, "sanitize": cmd_sanitize}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
