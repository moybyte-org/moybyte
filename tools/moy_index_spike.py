#!/usr/bin/env python3
# Map (grep -n a name to jump there):
#   Component          one native component: its dir, hook and host sources
#   impl_of            the hook's value, checked
#   build_env          the hook's environment for one build
#   binding            runtime/moy_index.py's interface over a twin, by ctypes
#   host_bindings      the suite's native BINDINGS
#   fuzz_binary        fuzz_index under ASan+UBSan: libFuzzer's or the seeded one
#   unix_micropython   the desktop MicroPython with a twin in both binaries
#   cmd_host           the suite over ctypes and on the VM, the store trace
#   cmd_sanitize       the suite under the sanitizers, the fuzz
#   map_summary        a twin's bytes in a board's link map
#   cmd_sizes          every image built py and twin
#   size_table         the size table
#   bench_on           the hot path under the counters, on a board
#   catalogue_on       the index's real caller, timed and counted
#   objdump_loads      the loads in the twin's own code, by width
#   cmd_bench          build, flash, bench, census
#   cmd_ci_time        what the twin's tests cost a CI run
"""Sprint 1a's harness: the store's index built as the C twin for every target,
and the numbers the kernel's language was decided on (#224; the decision is
docs/native_kernel_2026-09.md section 5, C). Sprint 2 generalised it to a second
component, the kernel's spine (native/moy_spine, docs/kernel_spine_2026-10.md):
`--component spine` runs `host` and `sanitize` for it, with its own host
library, fuzz driver and tests; `sizes`, `bench` and `ci-time` stay the
index's, because the spine has no Python twin an image could take instead.

    tools/moy_index_spike.py host     [--impl c]      the suite over ctypes and on the
                                                      desktop MicroPython, the store trace
    tools/moy_index_spike.py sanitize [--impl c]      ASan+UBSan: the suite over ctypes,
                                                      the seeded fuzz, then libFuzzer
    tools/moy_index_spike.py sizes    [--impl c] [TARGET ...]
                                                      every image built py and twin: the table
    tools/moy_index_spike.py --board S3 bench [--impl c]
                                                      the hot path on an S3 under perfcnt,
                                                      with the store's sprint-0 readings
    tools/moy_index_spike.py ci-time  [--impl c]      what the twin's tests cost a CI run

    tools/moy_index_spike.py --component spine host|sanitize ...
                                                      the same two for the spine

Results land as JSON in .build/moy_index_spike/ as well as on stdout. `sizes`
and `bench` build in THIS tree, so run them from a worktree; `sizes` leaves the
browser console built as the default (`py`) and each board's image as the twin.

THE HOOK. MOY_INDEX_IMPL picks the store index a build compiles in:

    c     the default: native/moy_index/moy_index.c under modmoy_index.c, and
          runtime/moy_index.py left out of the frozen set, so the extensible
          builtin answers `import moy_index` on every image that takes the
          module (the boards, the browser)
    py    runtime/moy_index.py, frozen; no native index at all

native/moy_index/micropython.cmake (the boards) and micropython.mk (the
desktop MicroPython, the browser) read it, tools/board_config.py drops the
Python twin from a twin's frozen set, and `make unix-micropython` builds the
twin its UNIX_MP_INDEX names (default `c`).

The spine has no hook: native/moy_spine (modmoy_spine.c over moy_route.c,
moy_settings.c and moy_ledger.c, and moy_htab.c, the handle table both
components take their slots from) is in every image, the desktop MicroPython
and the CPython host (over ctypes), and its Python twin is the suites' oracle,
tests/spine_twin.py. runtime/moy_index.py stays the host's: CPython's simulator, tools and tests
import it, and it is the reference every binding is pinned against.

MOY_INDEX_BENCH=1 adds the `moy_index_bench` module (bench_moy_index.c) to a
twin build, for `bench`; no image the size table measures carries it. Each
build dir records the hooks it was built with and starts its generated headers
afresh when that changes, because MicroPython keeps a dropped source's module
registration until that source is preprocessed again. A board's record
(`moy_native_config`, tools/esp32_build_lib.sh) is every MOY_*_IMPL hook in the
environment and the board's staged native modules, so flipping a board.toml
take or denial is noticed the same way; the web runner's build.sh and the
Makefile keep their own, over the two hooks.
"""

import argparse
import ctypes
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

NATIVE = os.path.join(ROOT, "native", "moy_index")
NATIVE_SPINE = os.path.join(ROOT, "native", "moy_spine")
NATIVE_APP = os.path.join(ROOT, "native", "moy_app")
CACHE = os.path.join(ROOT, ".build", "host_index")
OUT = os.path.join(ROOT, ".build", "moy_index_spike")
WEB_DIR = os.path.join(ROOT, "firmware", "web_runner")
WEB_DIST = os.path.join(WEB_DIR, "dist")
UNIX_PORT = os.path.join(ROOT, ".build", "unix_micropython", "micropython",
                         "ports", "unix")

IMPLS = ("py", "c")
SANITIZE = ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined",
            "-fno-omit-frame-pointer"]

# What counter 1 counts in the bench, as XTPERF_CNT_* / XTPERF_MASK_* (the
# S3's Xtensa; dev_channel.PERF_EVENTS names the same events for `perfcnt`).
# The core-level cache masks read the core's own cache, which the S3 does not
# have (dev_channel.py says why); the instruction-fetch stall is the reading
# that sees Espressif's cache.
BENCH_EVENTS = (
    ("insn", 2, 0x8DFF),        # retired instructions: IPC
    ("loads", 10, 0x000F),      # D_LOAD_U1: load instructions retired
    ("dstall", 3, 0x01FE),      # data stall cycles
    ("istall", 4, 0x01FF),      # instruction-fetch stall cycles
    ("imiss", 4, 0x0001),       # I_STALL_CACHE_MISS (the core's cache)
)


class Component:
    """One native component the harness builds, fuzzes and sizes: where its
    sources are, the hook that picks its twin, the host library the ctypes
    binding loads and the fuzz driver's sources."""

    def __init__(self, name, dirs, shim, names, fuzz, objects, tests, trace,
                 stamp, default, cflags=()):
        self.name = name
        self.env = "MOY_%s_IMPL" % name.upper()     # the hook
        self.default = default      # what a build takes with the hook unset
        self.cflags = list(cflags)  # what its fuzz driver builds with, beyond these
        self.dirs = dirs            # source dirs, the component's own first
        self.shim = shim            # the host's allocator imports
        self.names = names          # what the host library compiles
        self.fuzz = fuzz            # the fuzz driver, then what it links
        self.objects = re.compile(objects)  # the twin's objects in a link map
        self.tests = tests          # the files `host` and `sanitize` run
        self.trace = trace          # the semantic trace's -k expression
        self.stamp = stamp          # the saved result files' prefix
        self.lib = "moy_" + name
        self.cache = os.path.join(ROOT, ".build", "host_" + name)
        self.glue = "modmoy_" + name


INDEX = Component(
    "index", [NATIVE, NATIVE_SPINE], "moy_index_host.c",
    ["moy_index.h", "moy_index.c", "moy_htab.h", "moy_htab.c"],
    ("fuzz_index.c", ["moy_index.c", "moy_htab.c"]),
    r"\((mod)?moy_index\.c\.obj\)$|/(mod)?moy_index\.c\.obj$",
    ["tests/test_moy_index.py", "tests/test_moy_index_twins.py",
     "tests/test_store_roots.py"],
    "index or store", "", "c")
# The spine's host library carries native/moy_app too: the app ABI's state
# reads the spine's tables and settings rows, so its ctypes binding
# (tools/moy_app_binding.py) loads this same library.
SPINE = Component(
    "spine", [NATIVE_SPINE, NATIVE_APP], "moy_spine_host.c",
    ["moy_htab.h", "moy_htab.c", "moy_route.h", "moy_route.c",
     "moy_settings.h", "moy_settings.c", "moy_ledger.h", "moy_ledger.c",
     "moy_json.h", "moy_json.c", "moy_app.h", "moy_app.c"],
    ("fuzz_spine.c", ["moy_htab.c", "moy_route.c", "moy_settings.c",
                      "moy_ledger.c", "moy_json.c"]),
    r"\((mod)?moy_(spine|route|settings|ledger|htab)\.c\.obj\)$"
    r"|/(mod)?moy_(spine|route|settings|ledger|htab)\.c\.obj$",
    ["tests/test_moy_spine.py", "tests/test_moy_spine_twins.py"],
    "spine", "spine-", "py")
# The store's volume seam and crash-safe write (native/moy_store). It has no
# twin hook -- every image links it -- so `py` names nothing here; its fuzz
# driver is the power-cut matrix's, over the oofatfs and littlefs2 sources of
# the desktop MicroPython's tree (`make unix-micropython`).
MPY = os.path.join(ROOT, ".build", "unix_micropython", "micropython")
NATIVE_STORE = os.path.join(ROOT, "native", "moy_store")
FS = Component(
    "fs", [NATIVE_STORE, os.path.join(NATIVE_STORE, "host"), NATIVE_SPINE, MPY,
           os.path.join(MPY, "lib", "oofatfs"), os.path.join(MPY, "lib", "littlefs"),
           os.path.join(MPY, "lib", "uzlib")],
    "moy_store_host.c", ["moy_vol.h", "moy_vol.c", "moy_fs.h", "moy_fs.c",
                         "moy_arena.h", "moy_cat.h", "moy_cat.c", "moy_load.h", "moy_json.h",
                         "moy_json.c"],
    ("fuzz_fs.c", ["moy_cache.c", "moy_vol.c", "moy_fs.c", "moy_cat.c", "moy_seed.c", "moy_journal.c",
                   "moy_pack.c",
                   "moy_json.c",
                   "ff.c", "ffunicode.c", "lfs2.c", "lfs2_util.c", "tinflate.c",
                   "adler32.c", "crc32.c"]),
    r"\((mod)?moy_(store|vol|fs)\.c\.obj\)$|/(mod)?moy_(store|vol|fs)\.c\.obj$",
    ["tests/test_moy_store.py"], "store", "fs-", "c",
    cflags=['-DFFCONF_H="lib/oofatfs/ffconf.h"', "-DMOY_VOL_FAT=1",
            "-DMOY_VOL_LFS2=1", "-DLFS2_NO_MALLOC", "-DLFS2_NO_DEBUG",
            "-DLFS2_NO_WARN", "-DLFS2_NO_ERROR", "-DLFS2_NO_ASSERT"])
COMPONENTS = {"index": INDEX, "spine": SPINE, "fs": FS}


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


# -- the host's binding: the C ABI through ctypes ------------------------------


def host_library(impl="c", sanitize=False, comp=INDEX):
    """The host shared library for the C twin, built once per content (cached
    under .build/host_<component>; native_build serialises concurrent builds of
    one); None where there is no C compiler."""
    from runtime import native_build
    if impl != "c":
        raise ValueError("a host library is the C twin's, not %r" % impl)
    if sanitize:
        cflags = ["-std=c99", "-O1", "-g", "-fPIC", "-shared"] + SANITIZE
    else:
        cflags = list(native_build.BASE_CFLAGS)
    return native_build.build(
        "%s_%s%s" % (comp.lib, impl, "_san" if sanitize else ""),
        os.path.join(comp.dirs[0], comp.shim), comp.names, comp.cache,
        cflags=cflags, libmoy_dir=comp.dirs)


_SIGS = (
    ("moy_index_new", [], ctypes.c_void_p),
    ("moy_index_free", [ctypes.c_void_p], None),
    ("moy_index_intern", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t,
                          ctypes.POINTER(ctypes.c_uint32)], ctypes.c_int),
    ("moy_index_find", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t],
     ctypes.c_uint32),
    ("moy_index_path", [ctypes.c_void_p, ctypes.c_uint32,
                        ctypes.POINTER(ctypes.c_void_p),
                        ctypes.POINTER(ctypes.c_size_t)], ctypes.c_int),
    ("moy_index_valid", [ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
    ("moy_index_release", [ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
    ("moy_index_count", [ctypes.c_void_p], ctypes.c_uint32),
    ("moy_index_slots", [ctypes.c_void_p], ctypes.c_uint32),
    ("moy_index_at", [ctypes.c_void_p, ctypes.c_uint32], ctypes.c_uint32),
    ("moy_index_root", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t,
                        ctypes.POINTER(ctypes.c_uint32)], ctypes.c_int),
    ("moy_index_root_path", [ctypes.c_void_p, ctypes.c_uint32,
                             ctypes.POINTER(ctypes.c_void_p),
                             ctypes.POINTER(ctypes.c_size_t)], ctypes.c_int),
)


def binding(impl="c", sanitize=False):
    """An Index class with runtime/moy_index.py's interface over the C twin's
    host library, or None where there is no C compiler."""
    path = host_library(impl, sanitize)
    if path is None:
        return None
    lib = ctypes.CDLL(path)
    for name, args, res in _SIGS:
        fn = getattr(lib, name)
        fn.argtypes = args
        fn.restype = res
    from runtime import moy_index as ref

    def raw(path):
        if not isinstance(path, str):
            raise TypeError("store path must be a str")
        return path.encode("utf-8", "surrogateescape")

    def handle(h):
        if not isinstance(h, int):
            raise TypeError("store handle must be an int")
        return h if 0 < h < (1 << 30) else 0

    def ok(rc):
        if rc == 1:
            raise ref.StaleHandle("stale store handle")
        if rc == 2:
            raise OSError(28, "store index full")
        if rc:
            raise MemoryError("store index")

    class Index:
        __slots__ = ("_ix",)
        IMPL = impl

        def __init__(self):
            self._ix = lib.moy_index_new()
            if not self._ix:
                raise MemoryError("store index")

        def __del__(self):
            ix, self._ix = getattr(self, "_ix", None), None
            if ix:
                lib.moy_index_free(ix)

        def intern(self, path):
            b, h = raw(path), ctypes.c_uint32()
            ok(lib.moy_index_intern(self._ix, b, len(b), ctypes.byref(h)))
            return h.value

        def find(self, path):
            b = raw(path)
            return lib.moy_index_find(self._ix, b, len(b))

        def path(self, h):
            p, n = ctypes.c_void_p(), ctypes.c_size_t()
            ok(lib.moy_index_path(self._ix, handle(h), ctypes.byref(p),
                                  ctypes.byref(n)))
            return ctypes.string_at(p.value, n.value).decode(
                "utf-8", "surrogateescape")

        def valid(self, h):
            try:
                v = handle(h)
            except TypeError:
                return False
            return bool(lib.moy_index_valid(self._ix, v))

        def release(self, h):
            ok(lib.moy_index_release(self._ix, handle(h)))

        def handles(self):
            ix = self._ix
            return [h for h in (lib.moy_index_at(ix, s)
                                for s in range(lib.moy_index_slots(ix))) if h]

        def count(self):
            return lib.moy_index_count(self._ix)

        def root(self, path):
            b, rid = raw(path), ctypes.c_uint32()
            ok(lib.moy_index_root(self._ix, b, len(b), ctypes.byref(rid)))
            return rid.value

        def root_path(self, rid):
            p, n = ctypes.c_void_p(), ctypes.c_size_t()
            if lib.moy_index_root_path(self._ix, handle(rid), ctypes.byref(p),
                                       ctypes.byref(n)):
                return None
            return ctypes.string_at(p.value, n.value).decode(
                "utf-8", "surrogateescape")

        def rows(self, rid):
            r = handle(rid)
            if not 0 < r <= ref.ROOTS:
                return []
            return [h for h in self.handles() if self.path(h)[:1] == chr(r)]

    Index.__name__ = Index.__qualname__ = "Index_%s" % impl
    return Index


def host_bindings():
    """{name: Index class} for the suite's BINDINGS: the C twin; sanitized
    under MOY_INDEX_SANITIZE=1."""
    san = os.environ.get("MOY_INDEX_SANITIZE") == "1"
    cls = binding("c", sanitize=san)
    return {} if cls is None else {"c": cls}


# -- the fuzz driver ------------------------------------------------------------


def _cc_works(cc, flags, cache=CACHE):
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
    from runtime import native_build
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
    from runtime import native_build
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


# -- sizes ------------------------------------------------------------------------


def _boards():
    import board
    return board.boards(ROOT)


def _board_file(d):
    import board
    return board.board_file(d)


def app_image(d):
    image = _board_file(d).get("flash", {}).get("image", "")
    return os.path.join(ROOT, os.path.splitext(image)[0] + "_app.bin")


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def web_sizes():
    dist = WEB_DIST
    return {"wasm": _size(os.path.join(dist, "micropython.wasm")),
            "wasm_gz": _size(os.path.join(dist, "micropython.wasm.gz")),
            "bundle_gz": sum(_size(p) or 0
                             for p in glob.glob(os.path.join(dist, "*.gz")))}


def board_map(d):
    hits = glob.glob(os.path.join(d, ".build", "micropython", "ports", "esp32",
                                  "build-*", "micropython.map"))
    return max(hits, key=os.path.getmtime) if hits else None


_MAP_ROW = re.compile(r"^\s*(\.\S+)?\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)\s+(\S+)$")


def objects_of(comp=INDEX):
    """What names the twin's own objects in a link map."""
    return comp.objects


def map_rows(path, objects):
    """(object, section, address, bytes) for every input section of the
    twin's objects in a GNU ld map's memory map, allocated ones only."""
    with open(path, errors="replace") as f:
        lines = f.read().split("\n")
    try:
        start = lines.index("Linker script and memory map")
    except ValueError:
        start = 0
    pending = None
    for line in lines[start:]:
        if re.match(r"^ \.\S+$", line):
            pending = line.strip()
            continue
        m = _MAP_ROW.match(line)
        sec, pending = (m.group(1) or pending) if m else None, None
        if m and sec and objects.search(m.group(4)) and int(m.group(2), 16):
            yield (os.path.basename(m.group(4)), sec, int(m.group(2), 16),
                   int(m.group(3), 16))


def map_sections(path, objects):
    """{(object, section): bytes} for the twin's allocated input sections."""
    out = {}
    for obj, sec, _addr, n in map_rows(path, objects):
        out[(obj, sec)] = out.get((obj, sec), 0) + n
    return out


_KINDS = (("code", (".text", ".literal", ".iram")), ("rodata", (".rodata",)),
          ("data", (".data", ".sdata", ".dram")), ("bss", (".bss", ".sbss")))


def map_summary(path, comp=INDEX):
    """The twin's allocated bytes by kind, the binding (modmoy_<name>.c) apart
    from the twin's own code; and its function names.
    Merged string sections (`.str1.N`) are left out: the linker pools them
    across objects and the map charges the pool to whichever came first."""
    out = {"core": dict.fromkeys(k for k, _ in _KINDS),
           "glue": dict.fromkeys(k for k, _ in _KINDS)}
    for part in out.values():
        for k in part:
            part[k] = 0
    fns = set()
    for (obj, sec), n in map_sections(path, objects_of(comp)).items():
        if ".str1." in sec:
            continue
        part = out["glue" if obj.startswith(comp.glue) else "core"]
        for kind, prefixes in _KINDS:
            if sec.startswith(prefixes):
                part[kind] += n
                if kind == "code" and sec.startswith(".text."):
                    fns.add((obj, sec.split(".", 2)[-1]))
    out["core_functions"] = sorted(f for o, f in fns
                                   if not o.startswith(comp.glue))
    return out


def build_board(name, d, env, logdir):
    import board_pass
    log = os.path.join(logdir, "%s.build.log" % name)
    chip = _board_file(d).get("board", {}).get("chip", "")
    with board_pass.build_slot(chip):
        rc = board_pass.build(lambda cmd, lg: board_pass.run_logged(cmd, lg, env),
                              d, log)
    return rc, log


def build_web(env, logdir, tag):
    import board_pass
    log = os.path.join(logdir, "web.%s.build.log" % tag)
    return board_pass.run_logged(["bash", os.path.join(WEB_DIR, "build.sh")],
                                 log, env), log


def web_copy(which):
    return os.path.join(OUT, "web", which)


def use_web(which):
    """Put the browser console built as `which` where every board bakes it."""
    src = web_copy(which)
    if not os.path.isdir(src):
        raise SystemExit("no browser console built as %s: run `sizes web` "
                         "first" % which)
    shutil.rmtree(WEB_DIST, ignore_errors=True)
    shutil.copytree(src, WEB_DIST)


def cmd_sizes(a):
    """Each target built py and twin. `web` builds the browser console both
    ways and keeps both; a board then builds against the matching one, since
    every image bakes it -- so run `sizes web` before the boards, and give
    each invocation few enough targets to finish inside a shell's patience.
    The browser console is left as py; results merge into one JSON. With
    --no-web the boards bake the browser console as it stands in
    firmware/web_runner/dist (none, in a worktree made --no-web), so a delta is
    the board's own native code and nothing of the bundle."""
    comp = COMPONENTS[a.component]
    if comp is SPINE:
        raise SystemExit("the spine has no Python twin an image takes: no `sizes`")
    impl = impl_of(a.impl, comp)
    if impl == "py":
        raise SystemExit("`sizes` compares the twin with py: --impl c")
    boards = _boards()
    want = a.targets or ([] if a.no_web else ["web"]) + sorted(boards)
    unknown = [t for t in want if t != "web" and t not in boards]
    if unknown:
        raise SystemExit("no target %s (web, %s)" % (", ".join(unknown),
                                                     ", ".join(sorted(boards))))
    logdir = os.path.join(OUT, "logs-%s" % time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(logdir, exist_ok=True)
    print("logs: %s" % os.path.relpath(logdir, ROOT), flush=True)
    path = os.path.join(OUT, "sizes-%s%s.json" % (comp.stamp, impl))
    try:
        with open(path) as f:
            res = json.load(f)
    except (OSError, ValueError):
        res = {"impl": impl, "web": {}, "boards": {}}
    if "web" in want:
        for which in ("py", impl):
            t0 = time.time()
            rc, log = build_web(build_env(which, comp=comp), logdir, which)
            if rc != 0:
                raise SystemExit("web build (%s) failed: %s" % (which, log))
            shutil.rmtree(web_copy(which), ignore_errors=True)
            shutil.copytree(WEB_DIST, web_copy(which))
            res["web"][which] = dict(web_sizes(), seconds=round(time.time() - t0))
            print("web %-4s %s" % (which, res["web"][which]), flush=True)
    try:
        for name in [t for t in want if t != "web"]:
            d = boards[name]
            chip = _board_file(d).get("board", {}).get("chip", "")
            row = res["boards"].setdefault(name, {"chip": chip})
            for which in ("py", impl):
                if not a.no_web:
                    use_web(which)
                t0 = time.time()
                rc, log = build_board(name, d, build_env(which, comp=comp),
                                      logdir)
                if rc != 0:
                    row[which] = {"failed": os.path.relpath(log, ROOT)}
                    print("%s %s: BUILD FAILED (%s)" % (name, which, log),
                          flush=True)
                    continue
                got = {"app": _size(app_image(d)),
                       "seconds": round(time.time() - t0)}
                mp = board_map(d)
                if which != "py" and mp:
                    got["module"] = map_summary(mp, comp)
                    got["map"] = os.path.relpath(mp, ROOT)
                row[which] = got
                print("%s %-4s %s" % (name, which, got), flush=True)
    finally:
        if os.path.isdir(web_copy("py")):
            use_web("py")
        save("sizes-%s%s.json" % (comp.stamp, impl), res)
    print(size_table(res))
    return 0


def size_table(res):
    impl = res["impl"]
    w = res["web"]
    bundle = None
    if "py" in w and impl in w:
        bundle = w[impl]["bundle_gz"] - w["py"]["bundle_gz"]
    rows = [("target", "py bytes", "%s bytes" % impl, "delta",
             "of it the bundle", "twin + binding")]
    if "py" in w and impl in w:
        rows.append(("web wasm", w["py"]["wasm"], w[impl]["wasm"],
                     w[impl]["wasm"] - w["py"]["wasm"], "", ""))
        rows.append(("web wasm.gz", w["py"]["wasm_gz"], w[impl]["wasm_gz"],
                     w[impl]["wasm_gz"] - w["py"]["wasm_gz"], "", ""))
        rows.append(("web bundle .gz", w["py"]["bundle_gz"],
                     w[impl]["bundle_gz"], bundle, "", ""))
    for name, row in sorted(res["boards"].items()):
        p, t = row.get("py", {}), row.get(impl, {})
        if "app" not in p or "app" not in t:
            rows.append((name, p.get("app", "FAILED"), t.get("app", "FAILED"),
                         "", "", ""))
            continue
        mod = t.get("module") or {}
        own = ""
        if mod.get("core"):
            own = "%d + %d" % (sum(mod["core"].values()), sum(mod["glue"].values()))
        rows.append((name, p["app"], t["app"], t["app"] - p["app"],
                     bundle if bundle is not None else "", own))
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(str(c).rjust(widths[i]) if i else
                               str(c).ljust(widths[i])
                               for i, c in enumerate(r)) for r in rows)


# -- the bench ----------------------------------------------------------------------


BENCH_SETUP = r'''
import gc as _g
import moycore as _mc
_N = %(n)d
_paths = ["/sd/moybyte/carts/a cart named %%03d.moy" %% i for i in range(_N)]
try:
    import moy_index as _mi
    _ix = _mi.Index()
except ImportError:
    _mi = _ix = None
try:
    from moy_index_bench import run as _bench
except ImportError:
    _bench = None
for _p in _paths:
    _ix.intern(_p)
_hs = [_ix.find(_p) for _p in _paths]


def _win(fn, *a):
    best = None
    for _ in range(%(tries)d):
        _g.collect()
        x = _mc.perf_read()
        fn(*a)
        y = _mc.perf_read()
        d = ((y[0] - x[0]) & 0xffffffff, (y[1] - x[1]) & 0xffffffff)
        if best is None or d[0] < best[0]:
            best = d
    return best


def _vm_intern(r):
    ix = _ix
    for _ in range(r):
        for p in _paths:
            ix.intern(p)


def _vm_path(r):
    ix = _ix
    for _ in range(r):
        for h in _hs:
            ix.path(h)


def _vm_none(r):
    for _ in range(r):
        for p in _paths:
            pass


def _run(sel, mask, rounds):
    _mc.perf_counters(1, sel, mask)
    out = {}
    if _bench is not None:
        for op in range(4):
            out["c%%d" %% op] = (_win(_bench, _ix, _paths, rounds, op),
                                 _win(_bench, _ix, _paths, 0, op))
        _hs[:] = [_ix.find(p) for p in _paths]     # op 3 moved them on
    out["vm_intern"] = (_win(_vm_intern, rounds), _win(_vm_none, rounds))
    out["vm_path"] = (_win(_vm_path, rounds), _win(_vm_none, rounds))
    _mc.perf_counters(0)
    return out
'''


def _per_op(pair, calls):
    (c1, e1), (c0, e0) = pair
    return (c1 - c0) / float(calls), (e1 - e0) / float(calls)


def bench_on(b, n, rounds, tries):
    import board as bd
    # Uploads share one namespace (ws._g) and a `py` expression gets a fresh
    # one, so each reading is left on `ws` and read back from there.
    if not b.pyexec(BENCH_SETUP % {"n": n, "tries": tries}
                    + "\nws._bench_native = _bench is not None\n", timeout=120):
        raise bd.BoardError("bench setup: %s" % b.last_error)
    if b.pyval("__import__('moycore').perf_read()") is None:
        raise bd.BoardError("this board has no performance counters")
    has_native = bool(b.pyval("ws._bench_native"))
    hz = b.pyval("__import__('machine').freq()")
    calls = n * rounds
    rows = {}
    for ev, sel, mask in BENCH_EVENTS:
        # Two lines: a one-line snippet runs in a fresh namespace.
        if not b.pyexec("ws._bench_out = _run(%d, %d, %d)\npass"
                        % (sel, mask, rounds), timeout=600):
            raise bd.BoardError("bench %s: %s" % (ev, b.last_error))
        got = b.pyval("ws._bench_out", timeout=60)
        if not isinstance(got, dict):
            raise bd.BoardError("bench %s: %r %s" % (ev, got, b.last_error))
        for key, pair in got.items():
            cyc, evt = _per_op(pair, calls)
            row = rows.setdefault(key, {"cycles": round(cyc, 1)})
            row[ev] = round(evt, 2)
    for row in rows.values():
        row["ipc"] = round(row["insn"] / row["cycles"], 3) if row["cycles"] else None
        row["ns"] = round(row["cycles"] * 1e9 / hz, 1) if hz else None
    names = {"c0": "abi intern (live row)", "c1": "abi path", "c2": "abi valid",
             "c3": "abi release+intern", "vm_intern": "vm idx.intern(p)",
             "vm_path": "vm idx.path(h)"}
    return {"hz": hz, "native": has_native, "paths": n, "rounds": rounds,
            "ops": {names[k]: v for k, v in rows.items()}}


CATALOGUE = r'''
import gc as _g, time as _t
_cat = __import__("moy_catalogue")
_mc = __import__("moy_carts")
_ts, _ms = [], []
for _ in range(%(runs)d):
    _g.collect()
    _t0 = _t.ticks_us()
    _r = ws._with_sd(lambda: _cat.catalogue(str(ws.carts_root)))
    _ts.append(_t.ticks_diff(_t.ticks_us(), _t0))
    _g.collect()
    _t0 = _t.ticks_us()
    ws._with_sd(lambda: _mc.catalogue(str(ws.carts_root)))
    _ms.append(_t.ticks_diff(_t.ticks_us(), _t0))
_pm = __import__("moycore")
_st = {}
for _ev, _sel, _mask in ((("istall", 4, 0x01FF), ("dstall", 3, 0x01FE),
                          ("insn", 2, 0x8DFF))):
    _pm.perf_counters(1, _sel, _mask)
    for _k, _fn in (("with_index", _cat.catalogue), ("store_only", _mc.catalogue)):
        _g.collect()
        _x = _pm.perf_read()
        ws._with_sd(lambda: _fn(str(ws.carts_root)))
        _y = _pm.perf_read()
        _st.setdefault(_k, {})["cycles"] = (_y[0] - _x[0]) & 0xffffffff
        _st[_k][_ev] = (_y[1] - _x[1]) & 0xffffffff
_pm.perf_counters(0)
ws._bench_cat = (len(_r), _ts, _ms, _st)
del _cat, _mc, _ts, _ms, _r, _t0, _g, _t, _pm, _st, _x, _y, _fn, _k, _ev, _sel, _mask
'''


def catalogue_on(b, runs=5):
    """The index's real caller on the live console: moy_catalogue.catalogue
    (the shelf's read: the store's catalogue, every folder interned and the
    rest released) beside moy_carts.catalogue (the same read, no index), in
    microseconds, alternating, `runs` each; then one of each under the
    counters (cycles with the instruction-fetch and data stalls, and retired
    instructions), whose cycles split an image-wide slowdown from the index's."""
    import board as bd
    if not b.pyexec(CATALOGUE % {"runs": runs}, timeout=300):
        raise bd.BoardError("catalogue: %s" % b.last_error)
    n, ts, ms, st = b.pyval("ws._bench_cat", timeout=60)
    b.pyexec("del ws._bench_cat\npass", timeout=30)
    return {"carts": n, "with_index_us": ts, "store_only_us": ms,
            "index_share_us": sorted(t - m for t, m in zip(ts, ms)),
            "counters": st}


def objdump_loads(d, impl):
    """Load instructions by kind in the twin's own functions, from the ELF's
    disassembly: what a split of an unaligned access would show as."""
    mp = board_map(d)
    if mp is None:
        return None
    elf = os.path.join(os.path.dirname(mp), "micropython.elf")
    tool = (glob.glob(os.path.expanduser(
        "~/.espressif/tools/xtensa-esp-elf/*/xtensa-esp-elf/bin/"
        "xtensa-esp32s3-elf-objdump")) or [None])[-1]
    if tool is None or not os.path.exists(elf):
        return None
    out = {}
    for obj, sec, addr, n in map_rows(mp, objects_of(INDEX)):
        if obj.startswith("modmoy_index") or not sec.startswith(".text.") or not n:
            continue
        # The bytes, disassembled raw: the ELF's property tables mark some
        # of a function as literal data on one board and not on another.
        hexdump = subprocess.run([tool, "-s", "--start-address=0x%x" % addr,
                                  "--stop-address=0x%x" % (addr + n), elf],
                                 capture_output=True, text=True).stdout
        raw = bytearray()
        for row in re.findall(r"^ ([0-9a-f]{8}) ((?:[0-9a-f]{2,8} ){1,4})", hexdump,
                              re.M):
            raw += bytes.fromhex(row[1].replace(" ", ""))
        blob = os.path.join(OUT, "fn.bin")
        with open(blob, "wb") as f:
            f.write(bytes(raw[:n]))
        dis = subprocess.run([tool, "-D", "-b", "binary", "-m", "xtensa",
                              "--no-show-raw-insn", blob],
                             capture_output=True, text=True).stdout
        ops = re.findall(r"^\s*[0-9a-f]+:\s+([a-z][\w.]*)", dis, re.M)
        out[sec.split(".", 2)[-1]] = {
            "bytes": n, "insns": len(ops),
            "l8ui": sum(o == "l8ui" for o in ops),
            "l16": sum(o in ("l16ui", "l16si") for o in ops),
            "l32i": sum(o in ("l32i", "l32i.n") for o in ops),
            "l32r": sum(o == "l32r" for o in ops)}
    return out


def cmd_bench(a):
    import board as bd
    impl = impl_of(a.impl)
    dirs = _boards()
    if a.board not in dirs:
        raise SystemExit("bench drives a board: --board BOARD")
    d = dirs[a.board]
    chip = _board_file(d).get("board", {}).get("chip", "")
    if chip != "esp32s3":
        raise SystemExit("the bench reads the Xtensa counters: an S3 board")
    logdir = os.path.join(OUT, "logs-bench-%s" % time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(logdir, exist_ok=True)
    res = {"impl": impl, "board": a.board, "plain": a.plain}
    if not a.no_build:
        rc, log = build_board(a.board, d, build_env(impl, bench=not a.plain),
                              logdir)
        if rc != 0:
            raise SystemExit("build failed: %s" % log)
        res["app"] = _size(app_image(d))
    if not a.no_flash:
        rc = subprocess.call([sys.executable, os.path.join(HERE, "board.py"),
                              a.board, "flash"], cwd=ROOT)
        if rc != 0:
            raise SystemExit("flash failed")
    if impl != "py":
        res["loads"] = objdump_loads(d, impl)
    import board_pass
    if a.board in board_pass.QUIET:
        subprocess.call([sys.executable, os.path.join(HERE, "board.py"), a.board,
                         "tail", "1", "--send", "vol 0", "--grep", "REMOTE"],
                        cwd=ROOT)
    b = bd.attach(a.board, dirs)
    try:
        res["bench"] = bench_on(b, a.paths, a.rounds, a.tries)
        res["catalogue"] = catalogue_on(b)
        b.pyexec("del _mi, _ix, _paths, _hs, _bench, _run, _win, _vm_intern, "
                 "_vm_path, _vm_none, _mc, _g, _N\n"
                 "del ws._bench_out, ws._bench_native", timeout=30)
    finally:
        b.close()
    if a.census:
        res["census"] = {}
        for verb in ("scan", "launcher"):
            out = subprocess.run([sys.executable, "-u",
                                  os.path.join(HERE, "mem_census.py"),
                                  a.board, verb], cwd=ROOT,
                                 capture_output=True, text=True, timeout=900)
            lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
            res["census"][verb] = json.loads(lines[-1]) if lines else {
                "rc": out.returncode, "tail": out.stdout[-800:] + out.stderr[-800:]}
    save("bench-%s-%s%s.json" % (a.board, impl, "-plain" if a.plain else ""),
         res)
    ops = res["bench"]["ops"]
    cols = ("cycles", "ns", "insn", "ipc", "loads", "dstall", "istall", "imiss")
    print("per call, %d paths x %d rounds, %s at %s Hz"
          % (a.paths, a.rounds, a.board, res["bench"]["hz"]))
    print("%-24s" % "" + "".join("%10s" % c for c in cols))
    for name, row in ops.items():
        print("%-24s" % name + "".join("%10s" % row.get(c) for c in cols))
    cat = res["catalogue"]
    print("catalogue of %d carts, us: with the index %s, the store alone %s"
          % (cat["carts"], cat["with_index_us"], cat["store_only_us"]))
    print("  under the counters: %s" % cat["counters"])
    if res.get("loads"):
        tot = {k: sum(f[k] for f in res["loads"].values())
               for k in ("insns", "l8ui", "l16", "l32i", "l32r")}
        print("the twin's code: %s" % tot)
    return 0


# -- CI time ------------------------------------------------------------------------


def cmd_ci_time(a):
    impl = impl_of(a.impl)
    env = dict(os.environ, MOY_INDEX_IMPL=impl if impl != "py" else "c")
    shutil.rmtree(CACHE, ignore_errors=True)
    tests = ["tests/test_moy_index.py", "tests/test_moy_index_twins.py",
             "tests/test_semantic_traces.py::"
             "test_store_trace_holds_over_the_native_index", "--durations=0"]
    cold = pytest(tests, env)
    warm = pytest(tests, env)
    t0 = time.time()
    unix_micropython(impl if impl != "py" else "c")
    unix_s = round(time.time() - t0, 1)
    res = {"impl": impl, "tests_cold_s": cold[1], "tests_warm_s": warm[1],
           "rc": [cold[0], warm[0]], "unix_micropython_warm_s": unix_s,
           "slowest": [ln for ln in warm[2] if re.match(r"^\d+\.\d+s ", ln)][:8]}
    save("ci-time-%s.json" % impl, res)
    print(json.dumps(res, indent=1))
    return cold[0] or warm[0]


def main(argv=None):
    p = argparse.ArgumentParser(description="Sprint 1a's harness: the store's "
                                "index as the C twin, on every target")
    p.add_argument("--board", choices=sorted(_boards()),
                   help="the S3 board `bench` builds for, flashes and drives")
    p.add_argument("--component", choices=sorted(COMPONENTS), default="index",
                   help="the component `host`, `sanitize` and `sizes` run for; "
                        "`bench` and `ci-time` are the index's")
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("host")
    h.add_argument("--impl", default="c")
    s = sub.add_parser("sanitize")
    s.add_argument("--impl", default="c")
    s.add_argument("--runs", type=int, default=2000)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--seconds", type=int, default=120,
                   help="libFuzzer's budget; 0 skips it")
    z = sub.add_parser("sizes")
    z.add_argument("--impl", default="c")
    z.add_argument("--no-web", action="store_true",
                   help="leave the browser console out: build the boards "
                        "against whatever dist/ holds")
    z.add_argument("targets", nargs="*")
    b = sub.add_parser("bench")
    b.add_argument("--impl", default="c")
    b.add_argument("--paths", type=int, default=64)
    b.add_argument("--rounds", type=int, default=50)
    b.add_argument("--tries", type=int, default=5)
    b.add_argument("--no-build", action="store_true")
    b.add_argument("--no-flash", action="store_true")
    b.add_argument("--plain", action="store_true",
                   help="the image the size table measures: no moy_index_bench, "
                        "so only the VM rows")
    b.add_argument("--census", action="store_true",
                   help="also sprint 0's store readings: mem_census scan, launcher")
    c = sub.add_parser("ci-time")
    c.add_argument("--impl", default="c")
    a = p.parse_args(argv)
    if a.cmd in ("bench", "ci-time") and a.component != "index":
        raise SystemExit("`%s` is the index's: --component index" % a.cmd)
    return {"host": cmd_host, "sanitize": cmd_sanitize, "sizes": cmd_sizes,
            "bench": cmd_bench, "ci-time": cmd_ci_time}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
