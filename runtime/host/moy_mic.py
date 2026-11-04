"""The `mic` device API's driver (native/moy_audio/moy_mic.h) on CPython, by
ctypes: the module `moy_mic` as the boards import it, name for name
(native/moy_audio/modmoy_mic.c has the list). The host has no microphone:
`sim(rate)` makes one and `push` is its producer (runtime/host/mic_sim.py)."""

from __future__ import annotations

import ctypes
import os

try:                                    # the host's runtime package
    from . import host_native, native_build
except ImportError:                     # loaded flat, runtime/ on sys.path
    import host_native
    import native_build

_AUDIO = os.path.join(native_build.ROOT, "native", "moy_audio")
_KERNEL = os.path.join(native_build.ROOT, "native", "moy_kernel")

PART = native_build.Part("mic", ("moy_mic.h", "moy_mic.c"), (_AUDIO, _KERNEL))

RING = 16384

_U32, _I = ctypes.c_uint32, ctypes.c_int
_P16 = ctypes.POINTER(ctypes.c_int16)

_SIGS = (
    ("moy_mic_open", [_U32], _I),
    ("moy_mic_close", [_U32], _I),
    ("moy_mic_sweep", [_U32], _U32),
    ("moy_mic_owner", [], _U32),
    ("moy_mic_read", [_U32, _P16, _U32], _I),
    ("moy_mic_push", [_P16, _U32], None),
    ("moy_mic_rate", [], _U32),
    ("moy_mic_drops", [], _U32),
    ("moy_mic_pending", [], _U32),
    ("moy_mic_present", [], _I),
    ("moy_mic_sim", [_U32], None),
    ("moy_mic_reset", [], None),
)

_LIB = [None]


def _lib():
    if _LIB[0] is None:
        path = host_native.build()
        if path is None:
            raise ImportError("moy_mic: no C compiler for the host build")
        d = ctypes.CDLL(path)
        for name, args, res in _SIGS:
            fn = getattr(d, name)
            fn.argtypes = args
            fn.restype = res
        _LIB[0] = d
    return _LIB[0]


def _samples(buf):
    mv = memoryview(buf).cast("B")
    n = len(mv) // 2
    return (ctypes.c_int16 * n).from_buffer(mv), n


def open(owner):  # noqa: A001 -- the board module's name
    return _lib().moy_mic_open(owner)


def close(owner):
    return _lib().moy_mic_close(owner)


def sweep(owner):
    return _lib().moy_mic_sweep(owner)


def owner():
    return _lib().moy_mic_owner()


def read(owner, buf):
    c, n = _samples(buf)
    return _lib().moy_mic_read(owner, c, n)


def push(buf):
    c = (ctypes.c_int16 * len(buf))(*buf) if not isinstance(buf, (bytearray, memoryview)) \
        else _samples(buf)[0]
    _lib().moy_mic_push(c, len(c))


def rate():
    return _lib().moy_mic_rate()


def drops():
    return _lib().moy_mic_drops()


def pending():
    return _lib().moy_mic_pending()


def present():
    return bool(_lib().moy_mic_present())


def sim(rate):
    _lib().moy_mic_sim(rate)


def reset():
    _lib().moy_mic_reset()
