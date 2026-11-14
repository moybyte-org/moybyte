"""The OS log (native/moy_kernel/moy_log.h) on CPython, by ctypes: the module
`moy_log` as the boards and the desktop MicroPython import it, name for name
(native/moy_kernel/face.json has the list). The ring is the C one in the
host's one library (runtime/host/host_native.py), the same state the kernel's
words and the loop write into. The rows here are the hand ones; the rest are
runtime/host/moy_log_face.py's, generated."""

import ctypes as _c

from moy_log_face import *  # noqa: F401,F403
from moy_log_face import TEXT_MAX, _moy_log_cursor_t, _moy_log_rec_t, stats

_CURRENT = 0xFFFFFFFF
_HAND = (
    ("moy_log_write", [_c.c_int, _c.c_uint32, _c.c_char_p, _c.c_size_t], None),
    ("moy_log_current", [], _c.c_uint32),
    ("moy_log_set_current", [_c.c_uint32], None),
    ("moy_log_read", [_c.POINTER(_moy_log_cursor_t), _c.POINTER(_moy_log_rec_t), _c.c_char_p,
                      _c.c_size_t], _c.c_bool),
)


def _hand():
    import face_ctypes
    return face_ctypes.lib(_HAND)


def _bytes(v):
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    return str(v).encode("utf-8", "replace")


def write(level, pid, *parts):
    text = _bytes(parts[0]) if len(parts) == 1 else b" ".join(_bytes(p) for p in parts)
    _hand().moy_log_write(int(level), _CURRENT if pid is None else int(pid), text, len(text))


def current(pid=None):
    if pid is None:
        return _hand().moy_log_current()
    _hand().moy_log_set_current(int(pid))
    return None


def read(seq=0, n=32):
    c = _moy_log_cursor_t(int(seq), 0xFFFFFFFF)
    r = _moy_log_rec_t()
    buf = _c.create_string_buffer(TEXT_MAX)
    out = []
    while n > 0 and _hand().moy_log_read(_c.byref(c), _c.byref(r), buf, TEXT_MAX):
        text = buf.raw[:min(r.len, TEXT_MAX)].decode("utf-8", "replace")
        out.append((r.seq, r.t_us, r.level, r.pid, text))
        n -= 1
    return out


def next():  # noqa: A001 -- the module's name for it on every tier
    return stats()[1]
