"""The host library's native parts and the store index's ctypes face.

A component is one native body the host's one library carries and the
sanitizer builds take alone: where its sources are, the hook that picks its
twin (MOY_<NAME>_IMPL; tools/native_check.py's header says what each value
builds), the sources the host library compiles and its fuzz driver's.
runtime/host/host_native.py takes `part()` of the spine, the store and the
index; runtime/host/moy_spine_binding.py and moy_app_binding.py load
`host_library()`; tools/native_check.py runs each component's suite and fuzz.

`binding()` is runtime/carts/moy_index.py's interface over the C index, by
ctypes: the suites' BINDINGS (`host_bindings()`).
"""

import ctypes
import os
import re

import native_build

ROOT = native_build.ROOT
NATIVE = os.path.join(ROOT, "native", "moy_index")
NATIVE_SPINE = os.path.join(ROOT, "native", "moy_spine")
NATIVE_APP = os.path.join(ROOT, "native", "moy_app")
# The platform layer every component includes (moy_platform.h, moy_kmem.h).
NATIVE_KERNEL = os.path.join(ROOT, "native", "moy_kernel")
NATIVE_INPUT = os.path.join(ROOT, "native", "moy_input")
# The crypto rows' C (moy_crypto.h): the door calls it; it compiles with
# moy_seal.c over mbedTLS (runtime/host/seal_binding.py).
NATIVE_NET = os.path.join(ROOT, "native", "moy_net")

SANITIZE = ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined",
            "-fno-omit-frame-pointer"]


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
    "index", [NATIVE, NATIVE_SPINE, NATIVE_KERNEL], "moy_index_host.c",
    ["moy_index.h", "moy_index.c", "moy_htab.h", "moy_htab.c"],
    ("fuzz_index.c", ["moy_index.c", "moy_htab.c"]),
    r"\((mod)?moy_index\.c\.obj\)$|/(mod)?moy_index\.c\.obj$",
    ["tests/test_moy_index.py", "tests/test_moy_index_twins.py",
     "tests/test_store_roots.py"],
    "index or store", "", "c")
# The spine's part carries native/moy_app too: the app ABI's state reads the
# spine's tables and settings rows. The user-files layer its files rows reach
# is native/moy_store's, which the console hands moy_app by its verb table
# (runtime/host/moy_ufiles_binding.py) and the host's one library compiles as its own
# part: only its headers are read here.
NATIVE_STORE = os.path.join(ROOT, "native", "moy_store")
SPINE = Component(
    "spine", [NATIVE_SPINE, NATIVE_APP, NATIVE_INPUT, NATIVE_STORE, NATIVE_NET,
              NATIVE_KERNEL], "moy_spine_host.c",
    ["moy_htab.h", "moy_htab.c", "moy_route.h", "moy_route.c",
     "moy_settings.h", "moy_settings.c", "moy_ledger.h", "moy_ledger.c",
     "moy_json.h", "moy_json.c", "moy_app.h", "moy_app_err.h", "moy_app_rows.h", "moy_app_wasm_rows.h", "moy_app_lua_rows.h", "moy_app.c", "moy_app_run.h", "moy_app_wasm.h",
     "moy_app_wasm.c", "moy_app_lua.h", "moy_app_lua.c", "moy_input.h", "moy_event.h", "moy_crypto.h",
     "moy_ufiles.h", "moy_fs.h", "moy_vol.h"],
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
FS = Component(
    "fs", [NATIVE_STORE, os.path.join(NATIVE_STORE, "host"), NATIVE_SPINE, MPY,
           os.path.join(MPY, "lib", "oofatfs"), os.path.join(MPY, "lib", "littlefs"),
           os.path.join(MPY, "lib", "uzlib"), os.path.join(ROOT, "native", "moy_png"),
           NATIVE_KERNEL],
    "moy_store_host.c", ["moy_vol.h", "moy_vol.c", "moy_fs.h", "moy_fs.c",
                         "moy_arena.h", "moy_cat.h", "moy_cat.c", "moy_load.h", "moy_json.h",
                         "moy_json.c", "moy_shelf.h", "moy_shelf.c", "moy_files.h",
                         "moy_files.c", "moy_journal.h", "moy_journal.c", "moy_removed.h",
                         "moy_removed.c", "moy_core.h"],
    ("fuzz_fs.c", ["moy_cache.c", "moy_vol.c", "moy_fs.c", "moy_cat.c", "moy_seed.c", "moy_journal.c",
                   "moy_pack.c",
                   "moy_json.c", "moy_ufiles.c", "moy_img.c", "moy_png.c",
                   "ff.c", "ffunicode.c", "lfs2.c", "lfs2_util.c", "tinflate.c",
                   "adler32.c", "crc32.c", "lz77.c", "header.c"]),
    r"\((mod)?moy_(store|vol|fs)\.c\.obj\)$|/(mod)?moy_(store|vol|fs)\.c\.obj$",
    ["tests/test_moy_store.py"], "store", "fs-", "c",
    cflags=['-DFFCONF_H="lib/oofatfs/ffconf.h"', "-DMOY_VOL_FAT=1",
            "-DMOY_VOL_LFS2=1", "-DLFS2_NO_MALLOC", "-DLFS2_NO_DEBUG",
            "-DLFS2_NO_WARN", "-DLFS2_NO_ERROR", "-DLFS2_NO_ASSERT"])
# The theme loader (native/moy_ui/moy_theme.c) over the spine's JSON scanner:
# a fuzz driver and its suite, no twin and no host library of its own (the
# host's one library builds it), so only `sanitize` takes it.
NATIVE_UI = os.path.join(ROOT, "native", "moy_ui")
THEME = Component(
    "theme", [NATIVE_UI, NATIVE_SPINE, os.path.join(ROOT, "native", "moy_gfx"),
              os.path.join(ROOT, "native", "moy_gfx", "libmoy"),
              os.path.join(ROOT, "native", "moy_glass"), NATIVE_KERNEL],
    None, [],
    ("fuzz_theme.c", ["moy_theme.c", "moy_json.c", "moy_ui_tables.c"]),
    r"\((mod)?moy_theme\.c\.obj\)$|/(mod)?moy_theme\.c\.obj$",
    ["tests/test_theme_loader.py", "tests/test_theme_store.py"],
    "theme", "theme-", "c")
COMPONENTS = {"index": INDEX, "spine": SPINE, "fs": FS, "theme": THEME}


def host_library(impl="c", sanitize=False, comp=INDEX):
    """The host shared library for the C twin: the host's one library
    (runtime/host/host_native.py), which carries every component; a sanitizer
    build is the component's alone, cached under .build/host_<component>
    (the spine's is the one library under ASan).
    None where there is no C compiler."""
    import host_native
    if impl != "c":
        raise ValueError("a host library is the C twin's, not %r" % impl)
    if not sanitize:
        return host_native.build()
    if comp is SPINE:
        # The spine's C calls the crypto rows' mbedTLS and the store's bodies,
        # which only the host's one library links: it is that library under
        # ASan (MOYHOST_ASAN, tools/asan_pytest.sh's build).
        os.environ["MOYHOST_ASAN"] = "1"
        return host_native.build()
    return native_build.build_parts(
        "%s_%s_san" % (comp.lib, impl), [part(comp)], comp.cache,
        cflags=["-std=c99", "-O1", "-g", "-fPIC", "-shared"] + SANITIZE)


def part(comp):
    """`comp`'s share of a host library (runtime/native_build.Part)."""
    return native_build.Part(comp.lib, comp.names, comp.dirs,
                             shim=os.path.join(comp.dirs[0], comp.shim))


SIGS = (
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
    """An Index class with runtime/carts/moy_index.py's interface over the C twin's
    host library, or None where there is no C compiler."""
    path = host_library(impl, sanitize)
    if path is None:
        return None
    lib = ctypes.CDLL(path)
    for name, args, res in SIGS:
        fn = getattr(lib, name)
        fn.argtypes = args
        fn.restype = res
    import moy_index as ref

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
