"""The media service (native/moy_media/moy_media.h) on CPython, by ctypes: the
module `moy_media` as the boards import it, name for name
(native/moy_media/modmoy_media.c has the list). The C is the host's one
library (runtime/host/host_native.py) with the kernel's client under it, which
speaks plain TCP on the host. The host has no task: `run()` is one pass of its
loop, and `attach(audio)` hands the host's mix (runtime/host/audio_binding.py,
a library of its own) the service's pull, which a board's open does in C."""

from __future__ import annotations

import ctypes
import os

try:                                    # the host's runtime package
    from . import host_native, native_build
except ImportError:                     # loaded flat, runtime/ on sys.path
    import host_native
    import native_build

_MEDIA = os.path.join(native_build.ROOT, "native", "moy_media")
_NET = os.path.join(native_build.ROOT, "native", "moy_net")
_PLAY = os.path.join(native_build.ROOT, "native", "moy_play")
_KERNEL = os.path.join(native_build.ROOT, "native", "moy_kernel")

PART = native_build.Part("media", ("moy_media.h", "moy_media.c", "moy_mp3.c", "minimp3.h"),
                         (_MEDIA, _NET, _PLAY, _KERNEL))

IDLE, OPENING, PLAYING, ENDED, FAILED = range(5)
TASK = 0                                # no task: the face calls run()
URL, FEED = 1, 2
T_SRC, T_TITLE, T_STATION, T_ENTRY = range(4)
FIELDS = ("state", "paused", "owner", "gen", "entry", "entries", "kind", "armed", "rate",
          "channels", "kbps", "volume", "in_bytes", "in_room", "pcm_frames", "decoded",
          "played", "starved", "err", "text_gen", "idle", "at_ms", "len_ms")

_U32, _I, _P, _SZ = ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t


class _St(ctypes.Structure):
    _fields_ = [("state", _I), ("paused", _I), ("owner", _U32), ("gen", _U32),
                ("entry", _I), ("entries", _I), ("kind", _I), ("armed", _I),
                ("rate", _I), ("channels", _I), ("kbps", _I), ("volume", _I),
                ("in_bytes", _U32), ("in_room", _U32), ("pcm_frames", _U32),
                ("decoded", _U32), ("played", _U32), ("starved", _U32),
                ("err", _I), ("text_gen", _U32), ("idle", _I), ("at_ms", _U32),
                ("len_ms", _U32)]


_SIGS = (
    ("moy_media_share", [], _U32),
    ("moy_media_present", [], _I),
    ("moy_media_open", [_U32, _I, _I], _I),
    ("moy_media_close", [_U32], _I),
    ("moy_media_owner", [], _U32),
    ("moy_media_clear", [_U32], _I),
    ("moy_media_add", [_U32, ctypes.c_char_p, ctypes.c_char_p], _I),
    ("moy_media_playlist", [_U32, ctypes.c_char_p, _SZ, ctypes.c_char_p], _I),
    ("moy_media_go", [_U32, _I], _I),
    ("moy_media_stop", [_U32], _I),
    ("moy_media_pause", [_U32, _I], _I),
    ("moy_media_volume", [_U32, _I], _I),
    ("moy_media_feed", [_U32, _U32, _P, _SZ], _I),
    ("moy_media_feed_end", [_U32, _U32], _I),
    ("moy_media_feed_fail", [_U32, _U32, _I], _I),
    ("moy_media_feed_size", [_U32, _U32, _U32], _I),
    ("moy_media_status", [ctypes.POINTER(_St)], _I),
    ("moy_media_text", [_I, ctypes.c_char_p, _SZ], _I),
    ("moy_media_entry", [_I, ctypes.c_char_p, _SZ, ctypes.c_char_p, _SZ], _I),
    ("moy_media_run", [], _I),
    ("moy_media_close_vm", [], _I),
    ("moy_media_icy_title", [ctypes.c_char_p, _SZ, ctypes.c_char_p, _SZ], _I),
    ("moy_media_list_kind", [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, _SZ], _I),
)

_LIB = [None]


def _lib():
    if _LIB[0] is None:
        path = host_native.build()
        if path is None:
            raise ImportError("moy_media: no C compiler for the host build")
        d = ctypes.CDLL(path)
        for name, args, res in _SIGS:
            fn = getattr(d, name)
            fn.argtypes = args
            fn.restype = res
        _LIB[0] = d
    return _LIB[0]


def _b(s):
    return s.encode() if isinstance(s, str) else (bytes(s) if s is not None else None)


def present():
    return bool(_lib().moy_media_present())


def share():
    return _lib().moy_media_share()


def open(owner, rate, channels=1):  # noqa: A001 -- the board module's name
    return _lib().moy_media_open(owner, rate, channels)


def close(owner):
    return _lib().moy_media_close(owner)


def owner():
    return _lib().moy_media_owner()


def clear(owner):
    return _lib().moy_media_clear(owner)


def add(owner, src, title=None):
    return _lib().moy_media_add(owner, _b(src), _b(title))


def playlist(owner, text, base=None):
    t = _b(text)
    return _lib().moy_media_playlist(owner, t, len(t), _b(base))


def go(owner, i):
    return _lib().moy_media_go(owner, i)


def stop(owner):
    return _lib().moy_media_stop(owner)


def pause(owner, on):
    return _lib().moy_media_pause(owner, 1 if on else 0)


def volume(owner, v):
    return _lib().moy_media_volume(owner, v)


def feed(owner, gen, buf):
    mv = memoryview(buf).cast("B")
    if not len(mv):
        return 0
    if mv.readonly:
        b = bytes(mv)
        return _lib().moy_media_feed(owner, gen, b, len(b))
    c = (ctypes.c_char * len(mv)).from_buffer(mv)
    return _lib().moy_media_feed(owner, gen, ctypes.cast(c, _P), len(mv))


def feed_end(owner, gen):
    return _lib().moy_media_feed_end(owner, gen)


def feed_fail(owner, gen, err):
    return _lib().moy_media_feed_fail(owner, gen, err)


def feed_size(owner, gen, size):
    return _lib().moy_media_feed_size(owner, gen, size)


def status():
    st = _St()
    if not _lib().moy_media_status(ctypes.byref(st)):
        return None
    return tuple(getattr(st, f) for f in FIELDS)


def stamp():
    st = status()
    if st is None:
        return None
    s = dict(zip(FIELDS, st))
    h = s["state"] * 2 + (1 if s["paused"] else 0)
    for v in (s["entry"], s["volume"], s["err"], s["text_gen"],
              s["at_ms"] // 1000, s["len_ms"] // 1000):
        h = (h * 31 + v) & 0xffffffff
    return h & 0x3fffffff


def text(which):
    buf = ctypes.create_string_buffer(512)
    n = _lib().moy_media_text(which, buf, len(buf))
    return buf.raw[:n].decode()


def entry(i):
    s, t = ctypes.create_string_buffer(512), ctypes.create_string_buffer(128)
    if _lib().moy_media_entry(i, s, len(s), t, len(t)) < 0:
        return None
    return s.value.decode(), t.value.decode()


def run():
    return bool(_lib().moy_media_run())


def close_vm():
    """The kernel's call when a VM ends: the stream stopped and closed."""
    return _lib().moy_media_close_vm()


def icy_title(meta):
    m = _b(meta)
    out = ctypes.create_string_buffer(256)
    n = _lib().moy_media_icy_title(m, len(m), out, len(out))
    return None if n < 0 else out.raw[:n].decode()


def list_kind(ctype=None, path=None, text=None):
    t = _b(text)
    return _lib().moy_media_list_kind(_b(ctype), _b(path), t, len(t) if t else 0)


def attach(audio, on=True):
    """The host's mix (an audio_binding Module) pulls the service while `on`."""
    fn = ctypes.cast(_lib().moy_media_mix, _P).value if on else None
    audio.bg(fn)
