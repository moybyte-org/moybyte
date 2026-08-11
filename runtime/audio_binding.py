"""Build + load the host's libmoy audio binding (#97, stage 0 of moycore).

The host sim's ``AudioEngine`` synthesizes through the SAME vendored libmoy C
the boards and the web runner compile -- this module is how that C reaches
CPython. It compiles the vendored source plus a small shim
(``runtime/moyhost_audio.c``) into one shared library and loads it with
ctypes; the ``.so`` is cached under ``<repo>/.build/host_audio/`` keyed by a
hash of the sources, the flags and the compiler, so the compile happens once
per toolchain, not once per run. ``make setup`` pre-builds it; a tree that
skipped that builds lazily on the first AudioEngine.

Two deliberate choices, recorded:

* **The C is compiled DOUBLE-WIDENED** -- the same two mechanical regexes the
  parity harness applies (``audio_parity._widen_to_double``): ``float`` ->
  ``double``, and the ``f`` suffix off float literals. The strict parity suite
  proved the retired Python twin bit-identical to exactly that program, so
  binding the widened build made stage 0 a provably zero-behavior-change swap:
  no sample the host ever played moved. (The boards run the float build; the
  float-vs-double spread is measured and gated by test_audio_parity.py's
  device-precision pass, unchanged.)

* **No compiler means SILENCE, not a fallback synth** (owner decision,
  2026-08-11 -- KISS). ``get()`` returns None and AudioEngine degrades to
  zero-filled PCM, the same graceful-absence pattern lupa uses for the Lua
  host runner. The old degradation lane WAS the Python twin, and the twin's
  drift class is what stage 0 exists to delete.

MicroPython never imports this file (the boards bind libmoy natively via
modmoy_audio.c); runtime/audio.py guards the import.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_LIBMOY = os.path.join(_ROOT, "firmware", "lilygo_t_deck_plus_micropython",
                       "native", "moy_audio", "libmoy")
_SHIM = os.path.join(_HERE, "moyhost_audio.c")
_CACHE = os.path.join(_ROOT, ".build", "host_audio")

# One compile recipe, shared with the parity reference so bit-comparisons stay
# meaningful: -ffp-contract=off keeps the compiler from fusing multiply-adds,
# which rounds differently from the two separate operations.
_CFLAGS = ["-std=c99", "-O2", "-ffp-contract=off", "-fPIC", "-shared"]

_lib = None          # the loaded wrapper, or None
_tried = False       # first acquire attempt happened (so we only warn once)


def _cc():
    return os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")


def _widen_to_double(src):
    """audio_parity._widen_to_double's two substitutions, applied to text.
    Kept mechanically identical to the harness (the recipe is the contract)."""
    src = re.sub(r"\bfloat\b", "double", src)
    src = re.sub(r"(\d)[fF]\b", r"\1", src)      # 0.5f -> 0.5, never 0x7FFF
    return src


def _sources():
    out = {}
    for name in ("moy_audio.c", "moy_audio.h"):
        with open(os.path.join(_LIBMOY, name)) as fh:
            out[name] = _widen_to_double(fh.read())
    with open(_SHIM) as fh:
        out["moyhost_audio.c"] = fh.read()
    return out


def _key(cc, sources):
    h = hashlib.sha256()
    for name in sorted(sources):
        h.update(name.encode())
        h.update(sources[name].encode())
    h.update(" ".join(_CFLAGS).encode())
    try:
        ver = subprocess.run([cc, "--version"], capture_output=True, text=True,
                             timeout=10).stdout.splitlines()[:1]
        h.update((ver[0] if ver else "").encode())
    except Exception:   # noqa: BLE001 -- version is only a cache key refiner
        pass
    return h.hexdigest()[:16]


def build(verbose=False):
    """Compile (or reuse) the cached .so. Returns its path, or None with no
    compiler. Raises on a compile failure -- that is a broken tree, not an
    absent toolchain."""
    cc = _cc()
    if cc is None:
        return None
    sources = _sources()
    so_path = os.path.join(_CACHE, "moyhost_audio-%s.so" % _key(cc, sources))
    if os.path.exists(so_path):
        return so_path
    os.makedirs(_CACHE, exist_ok=True)
    src_dir = so_path[:-3] + ".src"
    os.makedirs(src_dir, exist_ok=True)
    for name, text in sources.items():
        with open(os.path.join(src_dir, name), "w") as fh:
            fh.write(text)
    tmp = so_path + ".tmp"
    cmd = [cc] + _CFLAGS + ["-I", src_dir,
                            os.path.join(src_dir, "moyhost_audio.c"),
                            os.path.join(src_dir, "moy_audio.c"), "-o", tmp]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("moyhost_audio build failed:\n" + proc.stderr)
    os.replace(tmp, so_path)                    # atomic vs a parallel test run
    if verbose:
        print("moyhost_audio: built", os.path.relpath(so_path, _ROOT))
    return so_path


class _Lib:
    """ctypes wrapper: one method per shim export, argtypes pinned so a
    mismatched call fails loudly instead of corrupting a stack."""

    def __init__(self, path):
        import ctypes
        d = ctypes.CDLL(path)
        d.moyhost_new.argtypes = [ctypes.c_int]
        d.moyhost_new.restype = ctypes.c_void_p
        d.moyhost_free.argtypes = [ctypes.c_void_p]
        d.moyhost_bank_load.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        d.moyhost_bank_load.restype = ctypes.c_int
        d.moyhost_set_rate.argtypes = [ctypes.c_void_p, ctypes.c_int]
        d.moyhost_sfx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        d.moyhost_beep.argtypes = [ctypes.c_void_p, ctypes.c_double,
                                   ctypes.c_double]
        d.moyhost_music.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        d.moyhost_music_stop.argtypes = [ctypes.c_void_p]
        d.moyhost_sound_stop.argtypes = [ctypes.c_void_p, ctypes.c_int]
        d.moyhost_volume.argtypes = [ctypes.c_void_p, ctypes.c_int]
        d.moyhost_active.argtypes = [ctypes.c_void_p]
        d.moyhost_active.restype = ctypes.c_uint
        d.moyhost_render.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_int]
        self._d = d
        self._ctypes = ctypes

    def new(self, rate):
        return self._d.moyhost_new(int(rate))

    def free(self, h):
        self._d.moyhost_free(h)

    def bank_load(self, h, json_text):
        return bool(self._d.moyhost_bank_load(h, json_text.encode()))

    def set_rate(self, h, rate):
        self._d.moyhost_set_rate(h, int(rate))

    def sfx(self, h, n, chan):
        self._d.moyhost_sfx(h, n, chan)

    def beep(self, h, freq, dur):
        self._d.moyhost_beep(h, freq, dur)

    def music(self, h, track, loop):
        self._d.moyhost_music(h, track, loop)

    def music_stop(self, h):
        self._d.moyhost_music_stop(h)

    def sound_stop(self, h, chan):
        self._d.moyhost_sound_stop(h, chan)

    def volume(self, h, level):
        self._d.moyhost_volume(h, level)

    def active(self, h):
        return self._d.moyhost_active(h)

    def render_into(self, h, out, nframes):
        buf = (self._ctypes.c_char * (2 * nframes)).from_buffer(out)
        self._d.moyhost_render(h, buf, nframes)


def get():
    """The loaded binding, or None (no compiler). Memoized; warns once on
    stderr when synthesis is unavailable so a silent sim is not a mystery."""
    global _lib, _tried
    if _lib is not None or _tried:
        return _lib
    _tried = True
    try:
        path = build()
    except Exception as exc:   # noqa: BLE001 -- a broken compile: say so, run silent
        print("moyhost_audio: build failed, host audio SILENT: %s" % (exc,),
              file=sys.stderr)
        return None
    if path is None:
        print("moyhost_audio: no C compiler, host audio SILENT "
              "(boards/web unaffected -- they compile libmoy natively)",
              file=sys.stderr)
        return None
    try:
        _lib = _Lib(path)
    except Exception as exc:   # noqa: BLE001
        print("moyhost_audio: load failed, host audio SILENT: %s" % (exc,),
              file=sys.stderr)
        return None
    return _lib


if __name__ == "__main__":
    # `make setup` runs this so the first simulate_desktop needs no compile
    # pause. Exit 0 either way: an absent compiler is a degradation, not a
    # broken setup.
    p = build(verbose=True) if _cc() else None
    if p is None:
        print("moyhost_audio: no C compiler -- host audio will be silent")
    else:
        print("moyhost_audio: ready")
