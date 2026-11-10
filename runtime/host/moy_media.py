"""The media service (native/moy_media/moy_media.h) on CPython, by ctypes: the
module `moy_media` as the boards import it, name for name
(native/moy_media/modmoy_media.c has the list). The C is the host's one
library (runtime/host/host_native.py) with the kernel's client under it, which
speaks plain TCP on the host. The host has no task: the frame's job
(`pump()`, which the loop's tail runs while the service is open) reads a
file and runs one pass of its loop, and `attach(audio)` hands the host's mix
(runtime/host/audio_binding.py, a library of its own) the service's pull,
which a board's open does in C."""

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
_STORE = os.path.join(native_build.ROOT, "native", "moy_store")
_INPUT = os.path.join(native_build.ROOT, "native", "moy_input")

PART = native_build.Part("media", ("moy_media.h", "moy_media.c", "moy_mp3.c", "minimp3.h",
                                   "moy_media_dev.c"),
                         (_MEDIA, _NET, _PLAY, _KERNEL, _STORE, _INPUT))

IDLE, OPENING, PLAYING, ENDED, FAILED = range(5)
TASK = 0                                # no task: the frame's job runs the loop
URL, FEED, FILE = 1, 2, 3
T_SRC, T_TITLE, T_STATION, T_ENTRY, T_SONG, T_ARTIST, T_WHO, T_LABEL = range(8)
FIELDS = ("state", "paused", "pid", "gen", "entry", "entries", "kind", "armed", "rate",
          "channels", "kbps", "volume", "in_bytes", "in_room", "pcm_frames", "decoded",
          "played", "starved", "err", "text_gen", "idle", "at_ms", "len_ms", "feed_at", "client")

_U32, _I, _P, _SZ = ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t


class _St(ctypes.Structure):
    _fields_ = [("state", _I), ("paused", _I), ("pid", _U32), ("client", _U32), ("gen", _U32),
                ("entry", _I), ("entries", _I), ("kind", _I), ("armed", _I),
                ("rate", _I), ("channels", _I), ("kbps", _I), ("volume", _I),
                ("in_bytes", _U32), ("in_room", _U32), ("pcm_frames", _U32),
                ("decoded", _U32), ("played", _U32), ("starved", _U32),
                ("err", _I), ("text_gen", _U32), ("idle", _I), ("at_ms", _U32),
                ("len_ms", _U32), ("feed_at", _U32)]


class _End(ctypes.Structure):
    _fields_ = [("runs", _U32), ("pid", _U32), ("client", _U32), ("state", _I), ("err", _I)]


class _Probe(ctypes.Structure):
    _fields_ = [("seconds", _U32), ("title", ctypes.c_char * 128),
                ("artist", ctypes.c_char * 128)]


_SIGS = (
    ("moy_media_share", [], _U32),
    ("moy_media_present", [], _I),
    ("moy_media_open", [_I, _I], _I),
    ("moy_media_close", [], _I),
    ("moy_media_pid", [], _U32),
    ("moy_media_last", [ctypes.POINTER(_End)], None),
    ("moy_media_take", [_U32, ctypes.c_char_p, ctypes.c_char_p], _U32),
    ("moy_media_play", [ctypes.c_char_p, _I], _I),
    ("moy_media_page", [_I], _I),
    ("moy_media_seek", [_U32], _I),
    ("moy_media_pump", [], None),
    ("moy_media_probe", [ctypes.c_char_p, ctypes.POINTER(_Probe)], _I),
    ("moy_media_clear", [], _I),
    ("moy_media_add", [ctypes.c_char_p, ctypes.c_char_p], _I),
    ("moy_media_playlist", [ctypes.c_char_p, _SZ, ctypes.c_char_p], _I),
    ("moy_media_go", [_I], _I),
    ("moy_media_stop", [], _I),
    ("moy_media_pause", [_I], _I),
    ("moy_media_volume", [_I], _I),
    ("moy_media_feed", [_U32, _P, _SZ], _I),
    ("moy_media_feed_end", [_U32], _I),
    ("moy_media_feed_fail", [_U32, _I], _I),
    ("moy_media_feed_size", [_U32, _U32], _I),
    ("moy_media_feed_icy", [_U32, _U32, ctypes.c_char_p], _I),
    ("moy_media_status", [ctypes.POINTER(_St)], _I),
    ("moy_media_stamp", [], ctypes.c_int32),
    ("moy_media_tell", [_U32], _I),
    ("moy_media_text", [_I, ctypes.c_char_p, _SZ], _I),
    ("moy_media_entry", [_I, ctypes.c_char_p, _SZ, ctypes.c_char_p, _SZ], _I),
    ("moy_media_run", [], _I),
    ("moy_media_key", [ctypes.c_uint16], _I),
    ("moy_media_keys_on", [_P], None),
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


def open(rate, channels=1):  # noqa: A001 -- the board module's name
    return _lib().moy_media_open(rate, channels)


def close():
    return _lib().moy_media_close()


def pid():
    return _lib().moy_media_pid()


def last():
    """How the last run ended: (runs, pid, client, state, err)."""
    e = _End()
    _lib().moy_media_last(ctypes.byref(e))
    return e.runs, e.pid, e.client, e.state, e.err


def take(client, who=None, label=None):
    return _lib().moy_media_take(client, _b(who), _b(label))


def play(src, held=False):
    return _lib().moy_media_play(_b(src), 1 if held else 0)


def page(on):
    return _lib().moy_media_page(1 if on else 0)


def seek(ms):
    return _lib().moy_media_seek(max(0, int(ms)))


def pump():
    """The frame's job run once (a harness that steps no frames)."""
    _lib().moy_media_pump()


def probe(path):
    """(seconds, title, artist) of the file at `path`, or None."""
    p = _Probe()
    if _lib().moy_media_probe(_b(path), ctypes.byref(p)) != 0:
        return None
    return p.seconds, p.title.decode(errors="replace"), p.artist.decode(errors="replace")


def clear():
    return _lib().moy_media_clear()


def add(src, title=None):
    return _lib().moy_media_add(_b(src), _b(title))


def playlist(text, base=None):
    t = _b(text)
    return _lib().moy_media_playlist(t, len(t), _b(base))


def go(i):
    return _lib().moy_media_go(i)


def stop():
    return _lib().moy_media_stop()


def pause(on):
    return _lib().moy_media_pause(1 if on else 0)


def volume(v):
    return _lib().moy_media_volume(v)


def feed(gen, buf):
    mv = memoryview(buf).cast("B")
    if not len(mv):
        return 0
    if mv.readonly:
        b = bytes(mv)
        return _lib().moy_media_feed(gen, b, len(b))
    c = (ctypes.c_char * len(mv)).from_buffer(mv)
    return _lib().moy_media_feed(gen, ctypes.cast(c, _P), len(mv))


def feed_end(gen):
    return _lib().moy_media_feed_end(gen)


def feed_fail(gen, err):
    return _lib().moy_media_feed_fail(gen, err)


def feed_size(gen, size):
    return _lib().moy_media_feed_size(gen, size)


def feed_icy(gen, metaint, station):
    return _lib().moy_media_feed_icy(gen, metaint, str(station).encode("utf-8"))


def status():
    st = _St()
    if not _lib().moy_media_status(ctypes.byref(st)):
        return None
    return tuple(getattr(st, f) for f in FIELDS)


def stamp():
    h = _lib().moy_media_stamp()
    return None if h < 0 else h


def tell(was):
    return bool(_lib().moy_media_tell(was))


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


def key(usage):
    """A media key's consumer usage going down, answered: whether it acted."""
    return bool(_lib().moy_media_key(usage))


_KEYS = [None]


def keys_on(table):
    """The input table (runtime/host/moy_input.py's InputTable; None: the
    kernel's) whose MEDIA records the service answers: the host console's
    own table, where a board's console reads the kernel's. Held here while
    it is bound, so the C never reads a table that was freed."""
    _lib().moy_media_keys_on(table._t if table is not None else None)
    _KEYS[0] = table


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
