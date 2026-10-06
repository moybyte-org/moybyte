#!/usr/bin/env python3
# Map (grep -n a name to jump there):
#   impl_of            the hook's value, checked
#   build_env          the hook's environment for one build
#   rust_lib           the Rust twin's static library for a target
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
"""Sprint 1a's harness: the store's index built as each twin for every target,
and the numbers the kernel's language is decided on (#224;
docs/native_kernel_2026-09.md section 5).

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

Results land as JSON in .build/moy_index_spike/ as well as on stdout. `sizes`
and `bench` build in THIS tree, so run them from a worktree; `sizes` leaves the
browser console built as the default (`py`) and each board's image as the twin.

THE HOOK. MOY_INDEX_IMPL picks the store index a build compiles in:

    py    the default: runtime/moy_index.py, frozen; no native index at all
    c     native/moy_index/moy_index.c under modmoy_index.c, and
          runtime/moy_index.py left out of the frozen set, so the extensible
          builtin answers `import moy_index`
    rust  the same binding over a Rust static library

native/moy_index/micropython.cmake (the boards) and micropython.mk (the
desktop MicroPython, the browser) read it, tools/board_config.py drops the
Python twin from a twin's frozen set, and `make unix-micropython` builds the
twin its UNIX_MP_INDEX names (default `c`). For `rust` every build links
MOY_INDEX_RUST_LIB: a static library defining every function moy_index.h
declares, importing its two host functions. This harness gets one per target
from `native/moy_index/rust/build.sh TARGET`, whose last line of output is the
library's path; TARGET is a key of RUST_TARGETS. That script is all the Rust
twin supplies -- the binding, the tests, the fuzz driver and this table are
shared.

MOY_INDEX_BENCH=1 adds the `moy_index_bench` module (bench_moy_index.c) to a
twin build, for `bench`; no image the size table measures carries it. Each
build dir records the hook it was built with (`moy_index_impl`) and starts its
generated headers afresh when that changes (tools/esp32_build_lib.sh, the web
runner's build.sh, the Makefile), because MicroPython keeps a dropped source's
module registration until that source is preprocessed again.
"""

import argparse
import ctypes
import glob
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
CACHE = os.path.join(ROOT, ".build", "host_index")
OUT = os.path.join(ROOT, ".build", "moy_index_spike")
WEB_DIR = os.path.join(ROOT, "firmware", "web_runner")
WEB_DIST = os.path.join(WEB_DIR, "dist")
UNIX_PORT = os.path.join(ROOT, ".build", "unix_micropython", "micropython",
                         "ports", "unix")

IMPLS = ("py", "c", "rust")
RUST_BUILD = os.path.join(NATIVE, "rust", "build.sh")
RUST_TARGETS = {
    "host": "the host's own triple: the ctypes library, the 64-bit desktop "
            "MicroPython",
    "host-sanitize": "the same, instrumented for AddressSanitizer",
    "host-r32": "i686: the desktop MicroPython in the boards' object model",
    "esp32s3": "the T-Deck, the Guition S3 and the Zero",
    "esp32p4": "the two P4 boards",
    "web": "wasm32-unknown-emscripten, under the web runner's emsdk",
}
CHIP_TARGET = {"esp32s3": "esp32s3", "esp32p4": "esp32p4"}
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


def impl_of(arg=None):
    v = arg or os.environ.get("MOY_INDEX_IMPL") or "py"
    if v not in IMPLS:
        raise SystemExit("MOY_INDEX_IMPL is py, c or rust, not %r" % v)
    return v


def rust_lib(target):
    """The Rust twin's static library for `target`, from its build script."""
    if target not in RUST_TARGETS:
        raise ValueError("no Rust target %r" % target)
    if not os.path.exists(RUST_BUILD):
        raise RuntimeError("the Rust twin supplies %s TARGET (the header of %s "
                           "says what it prints); it is not there"
                           % (os.path.relpath(RUST_BUILD, ROOT),
                              os.path.relpath(__file__, ROOT)))
    out = subprocess.run(["bash", RUST_BUILD, target], cwd=ROOT,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("%s %s failed:\n%s" % (RUST_BUILD, target,
                                                  out.stderr or out.stdout))
    path = out.stdout.strip().splitlines()[-1].strip()
    if not os.path.isfile(path):
        raise RuntimeError("%s %s printed %r, which is not a file"
                           % (RUST_BUILD, target, path))
    return os.path.abspath(path)


def build_env(impl, target=None, bench=False):
    """os.environ with the hook set for one build of `target`."""
    env = dict(os.environ)
    env["MOY_INDEX_IMPL"] = impl
    env.pop("MOY_INDEX_RUST_LIB", None)
    env.pop("MOY_INDEX_BENCH", None)
    if bench and impl != "py":
        env["MOY_INDEX_BENCH"] = "1"
    if impl == "rust":
        env["MOY_INDEX_RUST_LIB"] = rust_lib(target)
    return env


# -- the host's binding: the C ABI through ctypes ------------------------------


def host_library(impl="c", sanitize=False):
    """The host shared library for a twin, built once per content (cached
    under .build/host_index); None where there is no C compiler."""
    from runtime import native_build
    if impl not in ("c", "rust"):
        raise ValueError("a host library is a twin's, not %r" % impl)
    if sanitize:
        cflags = ["-std=c99", "-O1", "-g", "-fPIC", "-shared"] + SANITIZE
    else:
        cflags = list(native_build.BASE_CFLAGS)
    names = ["moy_index.h"] + (["moy_index.c"] if impl == "c" else [])
    link = []
    if impl == "rust":
        link = [rust_lib("host-sanitize" if sanitize else "host")]
    return native_build.build(
        "moy_index_%s%s" % (impl, "_san" if sanitize else ""),
        os.path.join(NATIVE, "moy_index_host.c"), names, CACHE,
        cflags=cflags, libmoy_dir=NATIVE, link_flags=link)


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
)


def binding(impl="c", sanitize=False):
    """An Index class with runtime/moy_index.py's interface over a twin's
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

    Index.__name__ = Index.__qualname__ = "Index_%s" % impl
    return Index


def host_bindings():
    """{name: Index class} for the suite's BINDINGS: the C twin always, the
    Rust twin when MOY_INDEX_IMPL=rust; sanitized under MOY_INDEX_SANITIZE=1."""
    san = os.environ.get("MOY_INDEX_SANITIZE") == "1"
    out = {}
    for impl in ("c", "rust"):
        if impl == "rust" and os.environ.get("MOY_INDEX_IMPL") != "rust":
            continue
        cls = binding(impl, sanitize=san)
        if cls is not None:
            out[impl] = cls
    return out


# -- the fuzz driver ------------------------------------------------------------


def _cc_works(cc, flags):
    probe = os.path.join(CACHE, "probe.c")
    os.makedirs(CACHE, exist_ok=True)
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


def fuzz_binary(impl="c", libfuzzer=False):
    """Build fuzz_index for a twin under ASan+UBSan: libFuzzer's (clang) or
    the seeded driver (any compiler). The path, or None with no toolchain
    that has the sanitizers."""
    os.makedirs(CACHE, exist_ok=True)
    srcs = [os.path.join(NATIVE, "fuzz_index.c")]
    link = []
    if impl == "c":
        srcs.append(os.path.join(NATIVE, "moy_index.c"))
    else:
        link = [rust_lib("host-sanitize")]
    if libfuzzer:
        cc = shutil.which("clang")
        flags = ["-fsanitize=fuzzer,address,undefined"] + SANITIZE[1:]
        if not cc or not _cc_works(cc, flags + ["-DLIBFUZZER"]):
            return None
    else:
        cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
        flags = SANITIZE + ["-DMOY_INDEX_FUZZ_MAIN"]
        if not cc or not _cc_works(cc, SANITIZE):
            return None
    exe = os.path.join(CACHE, "fuzz_index_%s%s" % (impl, "_lf" if libfuzzer
                                                    else ""))
    cmd = ([cc, "-std=c99", "-O1", "-g", "-Wall", "-Wextra", "-I", NATIVE]
           + flags + srcs + ["-o", exe] + link)
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("fuzz_index build failed:\n%s" % out.stderr)
    return exe


def fuzz_seeded(impl="c", seed=1, runs=300):
    """The seeded driver's run: (ok, seconds, output); None with no toolchain."""
    exe = fuzz_binary(impl)
    if exe is None:
        return None
    t = time.time()
    out = subprocess.run([exe, str(seed), str(runs)], capture_output=True,
                         text=True)
    return out.returncode == 0, time.time() - t, (out.stdout + out.stderr)


def fuzz_libfuzzer(impl="c", seconds=60):
    exe = fuzz_binary(impl, libfuzzer=True)
    if exe is None:
        return None
    corpus = os.path.join(OUT, "corpus_%s" % impl)
    os.makedirs(corpus, exist_ok=True)
    t = time.time()
    out = subprocess.run([exe, "-max_total_time=%d" % seconds, "-max_len=4096",
                          "-seed=1", "-print_final_stats=1", corpus],
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


def unix_micropython(impl):
    """`make unix-micropython` with the twin `impl` in both binaries (the
    binaries are removed first: a changed link line alone relinks nothing)."""
    for b in ("build-moybyte", "build-moybyte-board"):
        try:
            os.remove(os.path.join(UNIX_PORT, b, "micropython"))
        except OSError:
            pass
    cmd = ["make", "--no-print-directory", "unix-micropython",
           "UNIX_MP_INDEX=%s" % impl]
    if impl == "rust":
        cmd += ["MOY_INDEX_RUST_LIB=%s" % rust_lib("host"),
                "MOY_INDEX_RUST_LIB_R32=%s" % rust_lib("host-r32")]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("make unix-micropython (%s) failed:\n%s"
                           % (impl, (out.stdout + out.stderr)[-3000:]))


HOST_TESTS = ["tests/test_moy_index.py", "tests/test_moy_index_twins.py",
              "tests/test_semantic_traces.py", "-k", "index or store"]


def cmd_host(a):
    impl = impl_of(a.impl)
    if impl == "py":
        raise SystemExit("`host` runs a twin: --impl c or rust")
    unix_micropython(impl)
    env = dict(os.environ, MOY_INDEX_IMPL=impl, MOYBYTE_REQUIRE_UNIX_MP="1")
    rc, secs, tail = pytest(HOST_TESTS, env)
    if impl != "c":
        unix_micropython("c")
    print("\n".join(tail[-15:]))
    save("host-%s.json" % impl, {"impl": impl, "rc": rc, "seconds": secs,
                                 "tail": tail[-15:]})
    return rc


def libasan():
    from runtime import native_build
    cc = native_build.cc() or "cc"
    out = subprocess.run([cc, "-print-file-name=libasan.so"],
                         capture_output=True, text=True)
    path = out.stdout.strip()
    return path if os.path.isabs(path) and os.path.exists(path) else None


def cmd_sanitize(a):
    impl = impl_of(a.impl)
    if impl == "py":
        raise SystemExit("`sanitize` runs a twin: --impl c or rust")
    res = {"impl": impl}
    asan = libasan()
    if asan:
        env = dict(os.environ, MOY_INDEX_IMPL=impl, MOY_INDEX_SANITIZE="1",
                   LD_PRELOAD=asan,
                   ASAN_OPTIONS="detect_leaks=0:abort_on_error=1",
                   UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
        rc, secs, tail = pytest(["tests/test_moy_index.py",
                                 "tests/test_moy_index_twins.py", "-k",
                                 "not vm and not fuzz"], env)
        res["suite"] = {"rc": rc, "seconds": secs, "tail": tail[-6:]}
        print("suite over ctypes, ASan+UBSan: rc=%d %.1fs  %s"
              % (rc, secs, tail[-1] if tail else ""))
    else:
        res["suite"] = None
        print("suite over ctypes, ASan+UBSan: no libasan beside the compiler")
    got = fuzz_seeded(impl, seed=a.seed, runs=a.runs)
    res["seeded"] = None if got is None else {
        "ok": got[0], "seconds": round(got[1], 1), "runs": a.runs,
        "seed": a.seed, "out": got[2].strip().splitlines()[-3:]}
    print("seeded fuzz, ASan+UBSan: %s" % (res["seeded"] or "no toolchain"))
    if a.seconds:
        lf = fuzz_libfuzzer(impl, a.seconds)
        res["libfuzzer"] = lf
        print("libFuzzer, ASan+UBSan: %s" % (
            "no clang with libFuzzer" if lf is None else
            "ok=%(ok)s %(runs)s runs in %(seconds)ss, cov %(cov)s, ft "
            "%(features)s, corpus %(corpus)s" % lf))
    save("sanitize-%s.json" % impl, res)
    bad = (res["suite"] and res["suite"]["rc"]) or not (res["seeded"] or {}).get("ok") \
        or (res.get("libfuzzer") and not res["libfuzzer"]["ok"])
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


def objects_of(impl):
    """What names the twin's own objects in a link map."""
    if impl == "c":
        return re.compile(r"\((mod)?moy_index\.c\.obj\)$|/(mod)?moy_index\.c\.obj$")
    lib = os.path.basename(os.environ.get("MOY_INDEX_RUST_LIB", "")) or "moy_index"
    return re.compile(r"(\(modmoy_index\.c\.obj\)$)|(%s\()" % re.escape(lib))


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


def map_summary(path, impl):
    """The twin's allocated bytes by kind, the binding (modmoy_index.c, shared
    by both twins) apart from the twin's own code; and its function names.
    Merged string sections (`.str1.N`) are left out: the linker pools them
    across objects and the map charges the pool to whichever came first."""
    out = {"core": dict.fromkeys(k for k, _ in _KINDS),
           "glue": dict.fromkeys(k for k, _ in _KINDS)}
    for part in out.values():
        for k in part:
            part[k] = 0
    fns = set()
    for (obj, sec), n in map_sections(path, objects_of(impl)).items():
        if ".str1." in sec:
            continue
        part = out["glue" if obj.startswith("modmoy_index") else "core"]
        for kind, prefixes in _KINDS:
            if sec.startswith(prefixes):
                part[kind] += n
                if kind == "code" and sec.startswith(".text."):
                    fns.add((obj, sec.split(".", 2)[-1]))
    out["core_functions"] = sorted(f for o, f in fns
                                   if not o.startswith("modmoy_index"))
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
    The browser console is left as py; results merge into one JSON."""
    impl = impl_of(a.impl)
    if impl == "py":
        raise SystemExit("`sizes` compares a twin with py: --impl c or rust")
    boards = _boards()
    want = a.targets or (["web"] + sorted(boards))
    unknown = [t for t in want if t != "web" and t not in boards]
    if unknown:
        raise SystemExit("no target %s (web, %s)" % (", ".join(unknown),
                                                     ", ".join(sorted(boards))))
    logdir = os.path.join(OUT, "logs-%s" % time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(logdir, exist_ok=True)
    print("logs: %s" % os.path.relpath(logdir, ROOT), flush=True)
    path = os.path.join(OUT, "sizes-%s.json" % impl)
    try:
        with open(path) as f:
            res = json.load(f)
    except (OSError, ValueError):
        res = {"impl": impl, "web": {}, "boards": {}}
    if "web" in want:
        for which in ("py", impl):
            t0 = time.time()
            rc, log = build_web(build_env(which, "web"), logdir, which)
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
                use_web(which)
                t0 = time.time()
                rc, log = build_board(name, d, build_env(
                    which, CHIP_TARGET.get(chip)), logdir)
                if rc != 0:
                    row[which] = {"failed": os.path.relpath(log, ROOT)}
                    print("%s %s: BUILD FAILED (%s)" % (name, which, log),
                          flush=True)
                    continue
                got = {"app": _size(app_image(d)),
                       "seconds": round(time.time() - t0)}
                mp = board_map(d)
                if which != "py" and mp:
                    got["module"] = map_summary(mp, which)
                    got["map"] = os.path.relpath(mp, ROOT)
                row[which] = got
                print("%s %-4s %s" % (name, which, got), flush=True)
    finally:
        if os.path.isdir(web_copy("py")):
            use_web("py")
        save("sizes-%s.json" % impl, res)
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
    for obj, sec, addr, n in map_rows(mp, objects_of(impl)):
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
        rc, log = build_board(a.board, d, build_env(impl, CHIP_TARGET[chip],
                                                    bench=not a.plain), logdir)
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
                                "index as each twin, on every target")
    p.add_argument("--board", choices=sorted(_boards()),
                   help="the S3 board `bench` builds for, flashes and drives")
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
    return {"host": cmd_host, "sanitize": cmd_sanitize, "sizes": cmd_sizes,
            "bench": cmd_bench, "ci-time": cmd_ci_time}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
