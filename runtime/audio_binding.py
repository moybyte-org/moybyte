"""The kernel's audio (native/moy_audio) on CPython, by ctypes: the module
`moy_audio` as the boards and the browser import it, name for name, over the
same C built for the host.

`install()` puts it where `import moy_audio` finds it; `runtime/audio_session.py`
does that on first use. The library is the session table and the mix
(`moy_aud.c`), the output stub that has no speaker (`moy_aud_out.c`), the handle
table (`moy_htab.c`) and the vendored synth (`libmoy/moy_audio.c`), cached under
`<repo>/.build/host_audio/` by a hash of the sources, the flags and the
compiler.

Two choices, recorded:

* **The C is compiled DOUBLE-WIDENED** -- the parity harness's two mechanical
  regexes (`audio_parity._widen_to_double`): `float` -> `double`, and the `f`
  suffix off float literals. The strict parity suite proved the retired Python
  twin bit-identical to exactly that program, so the host plays what it always
  played. (The boards run the float build; the float-vs-double spread is
  measured and gated by tests/test_audio_parity.py's device-precision pass.)
* **No compiler means no module, not a fallback synth** (owner, 2026-08-11). A
  console without `moy_audio` holds no session, and its verbs are no-ops.

MicroPython never imports this file: there `moy_audio` is the usermod.
"""

import hashlib
import os
import re
import subprocess
import sys

from . import native_build

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_AUDIO = os.path.join(_ROOT, "native", "moy_audio")
_LIBMOY = os.path.join(_AUDIO, "libmoy")
_SPINE = os.path.join(_ROOT, "native", "moy_spine")
_CACHE = os.path.join(_ROOT, ".build", "host_audio")
# (directory, name, widened): every source, read for the cache key; the .c
# files are compiled. One compile recipe with the parity reference:
# -ffp-contract=off keeps the compiler from fusing multiply-adds.
_FILES = ((_LIBMOY, "moy_audio.c", True), (_LIBMOY, "moy_audio.h", True),
          (_AUDIO, "moy_aud.c", True), (_AUDIO, "moy_aud.h", True),
          (_AUDIO, "moy_aud_out.c", False), (_AUDIO, "moy_audio_snd.h", False),
          (_SPINE, "moy_htab.c", False), (_SPINE, "moy_htab.h", False))
_CFLAGS = ["-std=gnu99", "-O2", "-ffp-contract=off", "-fPIC", "-shared"]

OK, STALE, FULL, NOMEM, BANK, BAD = range(6)
OUT_NONE, OUT_RUNNING, OUT_FAILED, OUT_ABSENT = range(4)
CHANNELS = 4

_lib = None
_tried = False


def _widen_to_double(src):
    """audio_parity._widen_to_double's two substitutions, applied to text.
    Kept mechanically identical to the harness (the recipe is the contract)."""
    src = re.sub(r"\bfloat\b", "double", src)
    src = re.sub(r"(\d)[fF]\b", r"\1", src)      # 0.5f -> 0.5, never 0x7FFF
    return src


def _sources():
    out = {}
    for d, name, widen in _FILES:
        with open(os.path.join(d, name)) as fh:
            text = fh.read()
        out[name] = _widen_to_double(text) if widen else text
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
    except Exception:   # noqa: BLE001 -- the version only refines the key
        pass
    return h.hexdigest()[:16]


def build(verbose=False):
    """Compile (or reuse) the cached .so: its path, or None with no compiler.
    Raises on a compile failure -- a broken tree, not an absent toolchain."""
    cc = native_build.cc()
    if cc is None:
        return None
    sources = _sources()
    so_path = os.path.join(_CACHE, "moy_audio-%s.so" % _key(cc, sources))
    if os.path.exists(so_path):
        return so_path
    os.makedirs(_CACHE, exist_ok=True)
    with native_build.build_lock(so_path + ".lock"):
        if os.path.exists(so_path):
            return so_path
        src_dir = so_path[:-3] + ".src"
        os.makedirs(src_dir, exist_ok=True)
        for name, text in sources.items():
            with open(os.path.join(src_dir, name), "w") as fh:
                fh.write(text)
        tmp = "%s.%d.tmp" % (so_path, os.getpid())
        cmd = ([cc] + _CFLAGS + ["-I", src_dir]
               + [os.path.join(src_dir, n) for n in sorted(sources) if n.endswith(".c")]
               + ["-o", tmp])
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError("moy_audio host build failed:\n" + proc.stderr)
        os.replace(tmp, so_path)
    if verbose:
        print("moy_audio: built", os.path.relpath(so_path, _ROOT))
    return so_path


def _load(path):
    import ctypes as C
    d = C.CDLL(path)
    u32, i, p = C.c_uint32, C.c_int, C.c_void_p
    pu32 = C.POINTER(u32)
    sigs = (
        ("moy_aud_open", [pu32, u32, C.c_char_p, C.c_size_t], i),
        ("moy_aud_bank", [u32, C.c_char_p, C.c_size_t], i),
        ("moy_aud_focus", [u32], i),
        ("moy_aud_focused", [], u32),
        ("moy_aud_close", [u32], i),
        ("moy_aud_sfx", [u32, i, i], i),
        ("moy_aud_beep", [u32, C.c_double, C.c_double], i),
        ("moy_aud_music", [u32, i, i], i),
        ("moy_aud_music_stop", [u32], i),
        ("moy_aud_stop", [u32, i], i),
        ("moy_aud_level", [u32, i], i),
        ("moy_aud_active", [u32, pu32], i),
        ("moy_aud_volume", [i], None),
        ("moy_aud_console_level", [], i),
        ("moy_aud_hush", [], None),
        ("moy_aud_sample_load", [pu32, p, C.c_size_t, i], i),
        ("moy_aud_sample_play", [u32, u32, i], i),
        ("moy_aud_sample_free", [u32], i),
        ("moy_aud_set_rate", [i], None),
        ("moy_aud_rate", [], i),
        ("moy_aud_render", [p, i], None),
        ("moy_aud_stats", [C.POINTER(u32 * 13)], None),
        ("moy_aud_dump", [u32, p, i], i),
        ("moy_aud_stats_reset_max", [], None),
        ("moy_aud_trace_on", [i], None),
        ("moy_aud_trace_read", [C.POINTER(C.c_int32), C.c_size_t], C.c_size_t),
        ("moy_aud_out_start", [], i),
        ("moy_aud_out_state", [C.POINTER(C.c_char_p)], i),
        ("moy_aud_snd_counts", [pu32, C.POINTER(i)], i),
    )
    for name, args, res in sigs:
        fn = getattr(d, name)
        fn.argtypes = args
        fn.restype = res
    return d


def _check(rc):
    if rc in (OK, BANK):
        return rc
    if rc == STALE:
        raise ValueError("stale audio handle")
    if rc == FULL:
        raise MemoryError("the audio table is full")
    if rc == NOMEM:
        raise MemoryError()
    raise ValueError("audio: bad argument")


class Module:
    """`moy_audio`, name for name (native/moy_audio/modmoy_audio.c)."""

    CHANNELS = CHANNELS
    OUT_NONE, OUT_RUNNING, OUT_FAILED, OUT_ABSENT = OUT_NONE, OUT_RUNNING, OUT_FAILED, OUT_ABSENT

    def __init__(self, d):
        import ctypes
        self._d = d
        self._C = ctypes
        self.__name__ = "moy_audio"

    def _text(self, text):
        b = text.encode() if isinstance(text, str) else bytes(text)
        return b, len(b)

    def open(self, owner, bank=None):
        h = self._C.c_uint32()
        b, n = self._text(bank) if bank is not None else (None, 0)
        _check(self._d.moy_aud_open(self._C.byref(h), int(owner), b, n))
        return h.value

    def bank(self, h, text):
        b, n = self._text(text)
        return _check(self._d.moy_aud_bank(int(h), b, n)) == OK

    def focus(self, h):
        _check(self._d.moy_aud_focus(int(h)))

    def focused(self):
        return self._d.moy_aud_focused()

    def close(self, h):
        _check(self._d.moy_aud_close(int(h)))

    def sfx(self, h, n, chan=-1):
        _check(self._d.moy_aud_sfx(int(h), int(n), -1 if chan is None else int(chan)))

    def beep(self, h, freq, dur=0.15):
        _check(self._d.moy_aud_beep(int(h), float(freq), float(dur)))

    def music(self, h, track, loop=True):
        _check(self._d.moy_aud_music(int(h), int(track), 1 if loop else 0))

    def music_stop(self, h):
        _check(self._d.moy_aud_music_stop(int(h)))

    def stop(self, h, chan=-1):
        _check(self._d.moy_aud_stop(int(h), -1 if chan is None else int(chan)))

    def level(self, h, level):
        _check(self._d.moy_aud_level(int(h), int(level)))

    def active(self, h=0):
        m = self._C.c_uint32()
        _check(self._d.moy_aud_active(int(h), self._C.byref(m)))
        return m.value

    def volume(self, level=None):
        if level is not None:
            self._d.moy_aud_volume(int(level))
        return self._d.moy_aud_console_level()

    def hush(self):
        self._d.moy_aud_hush()

    def sample_load(self, pcm, rate):
        buf = (self._C.c_char * len(pcm)).from_buffer_copy(bytes(pcm))
        h = self._C.c_uint32()
        _check(self._d.moy_aud_sample_load(self._C.byref(h), buf, len(pcm) // 2, int(rate)))
        return h.value

    def sample_play(self, h, clip, chan=-1):
        _check(self._d.moy_aud_sample_play(int(h), int(clip), -1 if chan is None else int(chan)))

    def sample_free(self, clip):
        _check(self._d.moy_aud_sample_free(int(clip)))

    def set_rate(self, rate):
        self._d.moy_aud_set_rate(int(rate))

    def rate(self):
        return self._d.moy_aud_rate()

    def render(self, buf, n):
        n = min(int(n), len(buf) // 2)
        if n <= 0:
            return 0
        cbuf = (self._C.c_char * (2 * n)).from_buffer(buf)
        self._d.moy_aud_render(cbuf, n)
        return n

    def attach(self):
        return True

    def dump(self, h, buf, n):
        n = min(int(n), len(buf) // 2)
        cbuf = (self._C.c_char * (2 * n)).from_buffer(buf)
        _check(self._d.moy_aud_dump(int(h), cbuf, n))
        return n

    def start(self):
        self._d.moy_aud_out_start()
        return self.out()

    def out(self):
        why = self._C.c_char_p()
        st = self._d.moy_aud_out_state(self._C.byref(why))
        return (st, (why.value or b"").decode())

    def stats(self, reset=False):
        v = (self._C.c_uint32 * 13)()
        self._d.moy_aud_stats(self._C.byref(v))
        if reset:
            self._d.moy_aud_stats_reset_max()
        return tuple(v)

    def probe(self):
        return None

    def trace(self, on=None):
        if on is not None:
            self._d.moy_aud_trace_on(1 if on else 0)
            return None
        rows = (self._C.c_int32 * (64 * 4))()
        n = self._d.moy_aud_trace_read(rows, 64)
        return [tuple(rows[4 * k:4 * k + 4]) for k in range(n)]

    def codec(self):
        return None

    def snd_counts(self):
        c = (self._C.c_uint32 * 4)()
        o = self._C.c_int()
        if not self._d.moy_aud_snd_counts(c, self._C.byref(o)):
            return None
        return (c[0], c[1], c[2], c[3], bool(o.value))


def get():
    """The module, or None (no compiler). Memoized; says once on stderr why
    the host is silent."""
    global _lib, _tried
    if _lib is not None or _tried:
        return _lib
    _tried = True
    try:
        path = build()
    except Exception as exc:   # noqa: BLE001 -- a broken compile: say so, run silent
        print("moy_audio: host build failed, host audio SILENT: %s" % (exc,),
              file=sys.stderr)
        return None
    if path is None:
        print("moy_audio: no C compiler, host audio SILENT", file=sys.stderr)
        return None
    _lib = Module(_load(path))
    return _lib


def install():
    """Put `moy_audio` where an `import` finds it; never displaces a real one."""
    mod = get()
    if mod is not None:
        sys.modules.setdefault("moy_audio", mod)
    return mod


if __name__ == "__main__":
    # `make setup` runs this so the first simulate_desktop needs no compile.
    p = build(verbose=True) if native_build.cc() else None
    print("moy_audio: ready" if p else "moy_audio: no C compiler -- host audio will be silent")
