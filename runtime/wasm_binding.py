"""Build + load the host's libmoy WASM binding (docs/wasm_tier_plan_2026-09.md).

The host runs a `"runtime": "wasm"` cart through the same C the boards run:
libmoy's wasm binding (`native/moycore/libmoy/moy_wasm.c`, vendored) over the
console the Lua shim uses too (`runtime/moyhost_console.h`), and WAMR -- the
fork the boards vendor, at the commit `native/moy_wasm/wamr_pin.h` names, built
for Linux. There is no wasmtime tier and no second engine; this is
`lua_binding.py`'s twin.

WAMR IS FETCHED, NOT VENDORED, for the host: the boards compile an AOT-only
ESP-IDF subset of it (`native/moy_wasm/wamr/`), and a Linux build needs the
interpreter and the Linux platform layer, which that subset leaves behind on
purpose. So the first build fetches the fork AT THE PIN into
`.build/host_wamr/` (gitignored; `fetch_sources` says from where) and builds
its interpreter with no WASI and no builtin libc -- exactly what moy-spec's
`make wasm-test` does -- and every later build reuses it. The pin is the
boards' own, so a host and a board can never run different runtimes.

How the host EXECUTES a module is host policy (the plan): the boards run a
per-chip AOT module under a provenance key; the host interprets the cart's own
`main.wasm`. Hardware bounds checks are off, so WAMR installs no signal
handler in the Python process: every access is checked in software.

`available()` is False with no C compiler, no `git`, no `cmake`, or no way to
fetch the fork -- and then there are no wasm carts on the host, exactly as
`lua_binding`'s rule says for Lua: the Player shows the runtime-missing panel.
"""

import contextlib
import ctypes
import hashlib
import os
import re
import shutil
import subprocess
import sys

from . import native_build
from .lua_binding import cfg_blob

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = native_build.ROOT
_LIBMOY = native_build.LIBMOY                           # the raster + moy.h
_BINDING_DIR = os.path.join(_ROOT, "native", "moycore", "libmoy")   # moy_wasm.c/.h
_ENGINE_DIR = os.path.join(_ROOT, "native", "moy_wasm")   # moy_wasm_footprint.h
_AUDIO_DIR = os.path.join(_ROOT, "native", "moy_audio", "libmoy")   # moy_stream
_PIN_H = os.path.join(_ENGINE_DIR, "wamr_pin.h")
_SHIM = os.path.join(_HERE, "moyhost_wasm.c")
_CACHE = os.path.join(_ROOT, ".build", "host_wasm")
WAMR_DIR = os.path.join(_ROOT, ".build", "host_wamr")
WAMR_REPO = "https://github.com/moybyte-org/wasm-micro-runtime.git"

# The interpreter with nothing a cart may not import: no WASI, no builtin libc
# (moy-spec's wasm-test build); software bounds checks, so no SIGSEGV handler
# lands in the Python process; and the Python process's GS register left alone.
WAMR_CMAKE = (
    "-DCMAKE_BUILD_TYPE=Release", "-DWAMR_BUILD_INTERP=1",
    "-DWAMR_BUILD_FAST_INTERP=1", "-DWAMR_BUILD_AOT=0", "-DWAMR_BUILD_JIT=0",
    "-DWAMR_BUILD_FAST_JIT=0", "-DWAMR_BUILD_LIBC_BUILTIN=0",
    "-DWAMR_BUILD_LIBC_WASI=0", "-DWAMR_BUILD_SIMD=0",
    "-DWAMR_DISABLE_HW_BOUND_CHECK=1", "-DWAMR_DISABLE_WRITE_GS_BASE=1",
)

_RASTER = ("moy.h", "moy_pixel.h", "moy_canvas.c", "moy_sprite.c", "moy_data.c")

_LIB = [None]
_WHY = [None]                 # why the last build attempt came back empty


def pin():
    """The fork commit the boards' vendored runtime was copied from."""
    with open(_PIN_H, encoding="utf-8") as f:
        m = re.search(r'MOY_WASM_FORK_COMMIT "([0-9a-f]{40})"', f.read())
    if not m:
        raise RuntimeError("no MOY_WASM_FORK_COMMIT in %s" % _PIN_H)
    return m.group(1)


def why_unavailable():
    """The reason the binding did not build this process, or None."""
    return _WHY[0]


def fetch_sources():
    """Where the pinned commit is fetched from, in order: `$MOYBYTE_WAMR_CLONE`,
    the fork clone the boards' copy is vendored from
    (`experiments/wasm_aot/wamr`, which tools/vendor_wamr.py reads too), then
    the fork itself. A local clone first, because it is what the vendored copy
    was taken from and it needs no network."""
    out = []
    for cand in (os.environ.get("MOYBYTE_WAMR_CLONE"),
                 os.path.join(_ROOT, "experiments", "wasm_aot", "wamr")):
        if cand and os.path.exists(os.path.join(cand, ".git")):
            out.append(cand)
    out.append(WAMR_REPO)
    return out


@contextlib.contextmanager
def _locked(path):
    """One builder at a time: parallel test workers share `.build/`."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
        except ImportError:                              # pragma: no cover
            pass
        yield


def _run(cmd, log):
    with open(log, "a") as out:
        out.write("$ %s\n" % " ".join(cmd))
        out.flush()
        return subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT).returncode


def wamr(verbose=False):
    """(include dir, static library) of WAMR for Linux at the pin, fetched and
    built on first use; None, with the reason in why_unavailable(), when it
    cannot be."""
    commit = pin()
    tag = commit[:12]
    src = os.path.join(WAMR_DIR, "src-" + tag)
    # The configuration names the build directory, and the directory rides
    # the shim's cache key through the link line: another feature set is
    # another library, never a stale one.
    cfg = hashlib.sha256(" ".join(WAMR_CMAKE).encode()).hexdigest()[:8]
    build = os.path.join(WAMR_DIR, "linux-%s-%s" % (tag, cfg))
    lib = os.path.join(build, "libiwasm.a")
    inc = os.path.join(src, "core", "iwasm", "include")
    if os.path.isfile(lib) and os.path.isfile(os.path.join(inc, "wasm_export.h")):
        return inc, lib
    for tool in ("git", "cmake"):
        if shutil.which(tool) is None:
            _WHY[0] = "no `%s` to build WAMR for the host" % tool
            return None
    if native_build.cc() is None:
        _WHY[0] = "no C compiler"
        return None
    log = os.path.join(WAMR_DIR, "build-%s.log" % tag)
    with _locked(os.path.join(WAMR_DIR, ".lock")):
        if os.path.isfile(lib):
            return inc, lib
        stamp = os.path.join(src, ".pin-" + commit)
        if not os.path.isfile(stamp):
            if verbose:
                print("wasm_binding: fetching WAMR %s" % tag, file=sys.stderr)
            shutil.rmtree(src, ignore_errors=True)
            os.makedirs(src)
            if _run(["git", "-C", src, "init", "-q"], log) != 0:
                _WHY[0] = "could not start a WAMR clone (see %s)" % log
                return None
            for origin in fetch_sources():
                if _run(["git", "-C", src, "fetch", "-q", "--depth", "1",
                         origin, commit], log) == 0:
                    break
            else:
                _WHY[0] = ("could not fetch WAMR %s from %s (see %s)"
                           % (tag, " or ".join(fetch_sources()), log))
                return None
            if _run(["git", "-C", src, "checkout", "-q", "FETCH_HEAD"], log) != 0:
                _WHY[0] = "could not check WAMR %s out (see %s)" % (tag, log)
                return None
            head = subprocess.run(["git", "-C", src, "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
            if head != commit:
                _WHY[0] = "fetched WAMR is %s, the pin is %s" % (head, commit)
                return None
            open(stamp, "w").close()
        if verbose:
            print("wasm_binding: building WAMR %s for Linux" % tag, file=sys.stderr)
        configure = ["cmake", "-S", os.path.join(src, "product-mini", "platforms", "linux"),
                     "-B", build] + list(WAMR_CMAKE)
        if (_run(configure, log) != 0
                or _run(["cmake", "--build", build, "--target", "vmlib",
                         "-j", str(os.cpu_count() or 2)], log) != 0
                or not os.path.isfile(lib)):
            _WHY[0] = "WAMR did not build (see %s)" % log
            return None
    return inc, lib


def build(verbose=False):
    """Compile (or reuse) the cached .so; None when a piece is absent."""
    if not os.path.isfile(os.path.join(_BINDING_DIR, "moy_wasm.c")):
        _WHY[0] = "no vendored moy_wasm.c"
        return None
    got = wamr(verbose)
    if got is None:
        return None
    inc, lib = got
    names = list(_RASTER) + ["moy_wasm.c", "moy_wasm.h", "moyhost_console.h",
                             "moy_wasm_footprint.h", "moy_audio.c", "moy_audio.h"]
    cflags = native_build.BASE_CFLAGS + [
        "-DMOY_WASM=1", "-DMOY_PIXEL_RGB565=1", "-isystem", inc,
        # The pin rides the cache key: a moved pin is another runtime.
        "-DMOYHOST_WAMR_PIN=%s" % pin()]
    path = native_build.build(
        "moyhost_wasm", _SHIM, names, _CACHE, cflags=cflags,
        libmoy_dir=(_LIBMOY, _BINDING_DIR, _HERE, _ENGINE_DIR, _AUDIO_DIR),
        link_flags=[lib, "-lm", "-lpthread", "-ldl"], verbose=verbose)
    if path is None:
        _WHY[0] = "no C compiler"
    return path


_I, _P, _F, _C = ctypes.c_int, ctypes.c_void_p, ctypes.c_float, ctypes.c_char_p


def _lib():
    if _LIB[0] is None:
        try:
            path = build()
        except Exception as exc:   # noqa: BLE001
            _WHY[0] = str(exc)
            path = None
        if path is None:
            _LIB[0] = False
        else:
            d = ctypes.CDLL(path)
            # Every function gets its argtypes: one left on the default int
            # conversion truncates a 64-bit pointer, which is a segfault with
            # no traceback rather than a TypeError.
            d.hw_runtime.argtypes = []
            d.hw_runtime.restype = _I
            # (pix, nbytes, w, h, wire, wire_swapped, snap, aq, aq_cap)
            d.hw_new.argtypes = [_P, _I, _I, _I, _P, _I, _P, _P, _I]
            d.hw_new.restype = _P
            d.hw_set_sheet.argtypes = [_P, _P, _I]
            d.hw_set_map.argtypes = [_P, _P, _I, _I, _I]
            d.hw_set_flags.argtypes = [_P, _P, _I]
            d.hw_set_cfg.argtypes = [_P, _P, _I]
            d.hw_load.argtypes = [_P, _C, _C, _I, _P, _I]
            d.hw_load.restype = _I
            d.hw_init.argtypes = [_P, _P, _I]
            d.hw_init.restype = _I
            d.hw_tick.argtypes = [_P, _F, _I, _P, _I]
            d.hw_tick.restype = _I
            d.hw_retarget.argtypes = [_P, _P]
            d.hw_pmem_image.argtypes = [_P, _P, _I]
            d.hw_pmem_image.restype = _I
            d.hw_pmem_load.argtypes = [_P, _P, _I]
            d.hw_get_view.argtypes = [_P, ctypes.POINTER(_I), ctypes.POINTER(_I)]
            d.hw_get_view.restype = _I
            d.hw_free.argtypes = [_P]
            d.hw_snd_mix.argtypes = [_P, _P, _I, _I, _I]
            d.hw_snd_mix.restype = None
            d.hw_snd_counts.argtypes = [_P, _P]
            d.hw_snd_counts.restype = None
            _U64P = ctypes.POINTER(ctypes.c_uint64)
            d.hw_footprint.argtypes = [ctypes.c_uint64, ctypes.c_uint64, _U64P, _U64P]
            d.hw_footprint.restype = None
            d.hw_interp_footprint.argtypes = [ctypes.c_uint64, ctypes.c_uint64, _U64P, _U64P]
            d.hw_interp_footprint.restype = None
            if not d.hw_runtime():
                _WHY[0] = "WAMR did not initialise"
                _LIB[0] = False
            else:
                _LIB[0] = d
    return _LIB[0] or None


def footprint(memory, module_len):
    """(total, block): what a load of a `module_len`-byte module for a cart
    declaring `memory` bytes of linear memory takes at its peak, and the
    largest single block it asks for -- the boards' own sizing
    (native/moy_wasm/moy_wasm_footprint.h), compiled into this binding."""
    d = _lib()
    if d is None:
        raise RuntimeError("no host wasm binding (%s)" % (why_unavailable() or "?"))
    total, block = ctypes.c_uint64(0), ctypes.c_uint64(0)
    d.hw_footprint(int(memory), int(module_len), ctypes.byref(total),
                   ctypes.byref(block))
    return total.value, block.value


def interp_footprint(memory, module_len):
    """`footprint`'s twin for a session with no AOT module -- the one the
    host always runs by (`wasm_host.WasmHostRuntime`): the module file is
    never freed back to the linear memory, so the two are separate,
    simultaneous allocations over the interpreter's own (smaller) pool."""
    d = _lib()
    if d is None:
        raise RuntimeError("no host wasm binding (%s)" % (why_unavailable() or "?"))
    total, block = ctypes.c_uint64(0), ctypes.c_uint64(0)
    d.hw_interp_footprint(int(memory), int(module_len), ctypes.byref(total),
                          ctypes.byref(block))
    return total.value, block.value


SNAP_LEN = 14
SNAP_BTN, SNAP_BTNP, SNAP_BTN_P1, SNAP_BTNP_P1 = 0, 1, 2, 3
SNAP_PLAYERS, SNAP_TIME_MS = 4, 5
SNAP_TOUCH_X, SNAP_TOUCH_Y, SNAP_TOUCH_DOWN, SNAP_TOUCH_MS = 6, 7, 8, 9
SNAP_KEY, SNAP_KEY_DOWN, SNAP_TEXTMODE, SNAP_QUIT = 10, 11, 12, 13
AQ_SLOTS = 4
AQ_SFX, AQ_MUSIC, AQ_BEEP, AQ_MUSIC_STOP, AQ_SOUND_STOP, AQ_VOLUME = range(6)


class HostWasmRun:
    """One wasm cart run, in the same C the boards run.

    `buf` is the canvas's RGB565 framebuffer, borrowed and never copied,
    `wire` its 64-entry index -> word table (`DeviceCanvas._wire`) and
    `wire_swapped` whether that canvas stores its words byte-swapped (the
    module's `PAL565_WIRE` answers it), which a palette blit's 256 colours must
    match. The shape
    is `lua_binding.HostLuaRun`'s -- `snap`, `tick`, `audio`, `view`, `pmem`,
    `close` -- so `wasm_host.WasmHostRun` shares MoycoreHostRun's frame."""

    AUDIO_MAX = 32

    @staticmethod
    def available():
        return _lib() is not None

    def __init__(self, buf, w, h, cart_dir, main, pages, sheet=None,
                 tilemap=None, wire=None, wire_swapped=False, flags=None,
                 cfg=None):
        d = _lib()
        if d is None:
            raise RuntimeError("no host wasm binding (%s)" % (why_unavailable() or "?"))
        self._d = d
        self._r = None
        w, h = int(w), int(h)
        self.snap = (ctypes.c_int32 * SNAP_LEN)()
        self.aq = (ctypes.c_int32 * (1 + AQ_SLOTS * self.AUDIO_MAX))()
        self._cbuf = (ctypes.c_char * len(buf)).from_buffer(buf)
        self._wire = None
        if wire is not None:
            if len(wire) != 64:
                raise ValueError("wire table must have 64 entries")
            self._wire = (ctypes.c_uint16 * 64)(*(int(c) & 0xFFFF for c in wire))
        self._r = d.hw_new(ctypes.cast(self._cbuf, _P), len(buf), w, h,
                           None if self._wire is None else ctypes.cast(self._wire, _P),
                           1 if wire_swapped else 0, ctypes.cast(self.snap, _P),
                           ctypes.cast(self.aq, _P), len(self.aq))
        if not self._r:
            raise RuntimeError("host wasm: could not open a run (canvas %dx%d "
                               "needs %d bytes, got %d)" % (w, h, w * h * 2, len(buf)))
        self.snap[SNAP_PLAYERS] = 1
        self._sheet_ref = self._map_ref = None
        if sheet is not None:
            pix = sheet.pix if hasattr(sheet, "pix") else sheet
            if not isinstance(pix, bytearray):
                pix = bytearray(pix)
            self._sheet_ref = (ctypes.c_char * len(pix)).from_buffer(pix)
            d.hw_set_sheet(self._r, ctypes.cast(self._sheet_ref, _P), len(pix))
        if tilemap is not None:
            cells = tilemap.cells
            if not isinstance(cells, bytearray):
                cells = bytearray(cells)
            self._map_ref = (ctypes.c_char * len(cells)).from_buffer(cells)
            d.hw_set_map(self._r, ctypes.cast(self._map_ref, _P), len(cells),
                         int(tilemap.w), int(tilemap.h))
        if flags is not None:
            blob = bytes(flags)
            d.hw_set_flags(self._r, ctypes.c_char_p(blob), len(blob))
        blob = cfg_blob(cfg)
        if blob:
            d.hw_set_cfg(self._r, ctypes.c_char_p(blob), len(blob))
        err = ctypes.create_string_buffer(256)
        if d.hw_load(self._r, os.fsencode(cart_dir), os.fsencode(main),
                     int(pages or 0), ctypes.cast(err, _P), 256):
            msg = err.value.decode("utf-8", "replace")
            self.close()
            raise RuntimeError(msg)

    def init(self):
        """The cart's _init. None, or the trap's text."""
        err = ctypes.create_string_buffer(256)
        if self._d.hw_init(self._r, ctypes.cast(err, _P), 256):
            return err.value.decode("utf-8", "replace") or "the cart trapped"
        return None

    def tick(self, dt, draw=True):
        """_update, then _draw unless `draw` is False (#217). None, or the
        trap's text -- after which the run never calls the cart again and the
        canvas holds no partial frame."""
        err = ctypes.create_string_buffer(256)
        if self._d.hw_tick(self._r, ctypes.c_float(dt), 1 if draw else 0,
                           ctypes.cast(err, _P), 256):
            return err.value.decode("utf-8", "replace") or "the cart trapped"
        return None

    def retarget(self, buf):
        self._cbuf = (ctypes.c_char * len(buf)).from_buffer(buf)
        self._d.hw_retarget(self._r, ctypes.cast(self._cbuf, _P))

    def audio(self):
        """Drain the queue: [(op, a, b, c), ...] in the order the cart made
        them."""
        n = self.aq[0]
        out = []
        for i in range(n):
            p = 1 + i * AQ_SLOTS
            out.append((self.aq[p], self.aq[p + 1], self.aq[p + 2], self.aq[p + 3]))
        self.aq[0] = 0
        return out

    def snd_mix(self, out, nframes, rate, master):
        """Add `nframes` of the cart's `snd` stream into `out`, a bytearray of
        signed 16-bit mono the host's output renders at `rate` Hz, at the
        master level (0..7). The stream plays at its own 22050 Hz whatever
        `rate` is, and drains as fast as the output takes it."""
        nframes = min(int(nframes), len(out) // 2)
        if nframes <= 0 or not self._r:
            return
        buf = (ctypes.c_char * len(out)).from_buffer(out)
        self._d.hw_snd_mix(self._r, ctypes.cast(buf, _P), nframes, int(rate), int(master))

    def snd_counts(self):
        """(queued, played, starved, room): frames the cart's `snd` queued,
        frames the output mixed out, output frames that found the queue empty
        after the stream began, and the queue's room now."""
        c = (ctypes.c_uint32 * 4)()
        self._d.hw_snd_counts(self._r, ctypes.cast(c, _P))
        return tuple(c)

    def pmem(self):
        img = (ctypes.c_int32 * 256)()
        dirty = self._d.hw_pmem_image(self._r, ctypes.cast(img, _P), 256)
        return bool(dirty), list(img)

    def pmem_load(self, cells):
        img = (ctypes.c_int32 * 256)(*[int(c) for c in list(cells)[:256]])
        self._d.hw_pmem_load(self._r, ctypes.cast(img, _P), 256)

    def view(self):
        """(w, h) as the cart last declared with view(), or None."""
        w, h = _I(0), _I(0)
        if self._d.hw_get_view(self._r, ctypes.byref(w), ctypes.byref(h)):
            return (w.value, h.value)
        return None

    def close(self):
        if getattr(self, "_r", None):
            self._d.hw_free(self._r)
            self._r = None
