"""The OS log (native/moy_kernel/moy_log.h) on CPython, by ctypes: the module
`moy_log` as the boards and the desktop MicroPython import it, name for name
(native/moy_kernel/modmoy_log.c has the list). The ring is the C one in the
host's one library (runtime/host/host_native.py), the same state the kernel's
words and the loop write into."""

from __future__ import annotations

import ctypes

try:                                    # the host's runtime package
    from . import moy_loop as _loop
except ImportError:                     # loaded flat, runtime/ on sys.path
    import moy_loop as _loop

EMERG, ALERT, CRIT, ERR, WARNING, NOTICE, INFO, DEBUG = range(8)
KERNEL, SHELL = 0, 1
TEXT_MAX = 2048
_CURRENT = 0xFFFFFFFF

_U32 = ctypes.c_uint32
_P = ctypes.c_char_p


class _Rec(ctypes.Structure):
    _fields_ = [("t_us", ctypes.c_uint64), ("seq", _U32), ("pid", _U32),
                ("len", ctypes.c_uint16), ("level", ctypes.c_uint8), ("flags", ctypes.c_uint8)]


class _Cursor(ctypes.Structure):
    _fields_ = [("seq", _U32), ("off", _U32)]


class _Stats(ctypes.Structure):
    _fields_ = [("first", _U32), ("next", _U32), ("used", _U32), ("cap", _U32)]


_SIGS = (
    ("moy_log_write", [ctypes.c_int, _U32, _P, ctypes.c_size_t], None),
    ("moy_log_spawn", [_P], _U32),
    ("moy_log_name", [_U32], _P),
    ("moy_log_current", [], _U32),
    ("moy_log_set_current", [_U32], None),
    ("moy_log_read", [ctypes.POINTER(_Cursor), ctypes.POINTER(_Rec), _P, ctypes.c_size_t],
     ctypes.c_bool),
    ("moy_log_stats", [ctypes.POINTER(_Stats)], None),
    ("moy_log_level_name", [ctypes.c_int], _P),
    ("moy_log_level_of", [_P, ctypes.c_size_t], ctypes.c_int),
    ("moy_log_init", [ctypes.c_size_t], ctypes.c_bool),
    ("moy_log_pin_clock", [ctypes.c_uint64], None),
)

_LIB = [None]


def _lib():
    if _LIB[0] is None:
        d = _loop._lib()
        for name, args, res in _SIGS:
            fn = getattr(d, name)
            fn.argtypes = args
            fn.restype = res
        _LIB[0] = d
    return _LIB[0]


_EXITS = ("NOT_RUN", "INTERRUPTED", "TRAPPED", "KILLED")


def __getattr__(attr):
    """NOT_RUN, INTERRUPTED, TRAPPED, KILLED: the process table's exit
    statuses (native/moy_kernel/moy_proc_exit.h), read from the C's
    `moy_log_exits` at the first use."""
    if attr not in _EXITS:
        raise AttributeError(attr)
    v = (ctypes.c_uint8 * len(_EXITS)).in_dll(_lib(), "moy_log_exits")[_EXITS.index(attr)]
    globals()[attr] = v
    return v


def _bytes(v):
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    return str(v).encode("utf-8", "replace")


def write(level, pid, *parts):
    text = _bytes(parts[0]) if len(parts) == 1 else b" ".join(_bytes(p) for p in parts)
    _lib().moy_log_write(int(level), _CURRENT if pid is None else int(pid), text, len(text))


def spawn(name):
    return _lib().moy_log_spawn(_bytes(name))


def name(pid):
    return _lib().moy_log_name(int(pid)).decode("utf-8", "replace")


def current(pid=None):
    if pid is None:
        return _lib().moy_log_current()
    _lib().moy_log_set_current(int(pid))
    return None


def read(seq=0, n=32):
    c = _Cursor(int(seq), 0xFFFFFFFF)
    r = _Rec()
    buf = ctypes.create_string_buffer(TEXT_MAX)
    out = []
    while n > 0 and _lib().moy_log_read(ctypes.byref(c), ctypes.byref(r), buf, TEXT_MAX):
        text = buf.raw[:min(r.len, TEXT_MAX)].decode("utf-8", "replace")
        out.append((r.seq, r.t_us, r.level, r.pid, text))
        n -= 1
    return out


def stats():
    s = _Stats()
    _lib().moy_log_stats(ctypes.byref(s))
    return (s.first, s.next, s.used, s.cap)


def next():  # noqa: A001 -- the module's name for it on every tier
    return stats()[1]


def level_name(level):
    return _lib().moy_log_level_name(int(level)).decode()


def level_of(word):
    b = _bytes(word)
    return _lib().moy_log_level_of(b, len(b))


def init(nbytes):
    return bool(_lib().moy_log_init(int(nbytes)))


def pin_clock(us):
    _lib().moy_log_pin_clock(int(us))
