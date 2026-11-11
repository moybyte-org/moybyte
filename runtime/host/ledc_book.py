"""The kernel's LEDC book (native/moy_kernel/moy_ledc.h) on CPython, by
ctypes, for the host's tests: the C one in the host's one library
(runtime/host/host_native.py), beside the lease table whose lock it takes."""

from __future__ import annotations

import ctypes

try:                                    # the host's runtime package
    from . import host_native
except ImportError:                     # loaded flat, runtime/ on sys.path
    import host_native

CHANNELS, TIMERS = 8, 4
VM, LIGHT, FRESH = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFF
FREE, SETTING, READY, CLOSING = range(4)

_U8, _U32, _I32, _I, _B = ctypes.c_uint8, ctypes.c_uint32, ctypes.c_int32, ctypes.c_int, ctypes.c_bool

_SIGS = (
    ("moy_ledc_chan_take", [_U32, _B], _I),
    ("moy_ledc_chan_claim", [_I, _U32], _B),
    ("moy_ledc_chan_open", [_I, _U32], _B),
    ("moy_ledc_chan_drop", [_I, _U32], _I),
    ("moy_ledc_chan_holder", [_I], _U32),
    ("moy_ledc_chan_timer", [_I], _I),
    ("moy_ledc_timer_join", [_I, _I, _U32], _B),
    ("moy_ledc_timer_retune", [_I, _I], _B),
    ("moy_ledc_timer_set", [_I, _U32, _U8, _I32], None),
    ("moy_ledc_timer_closed", [_I], None),
    ("moy_ledc_timer_usable", [_I, _U32], _B),
    ("moy_ledc_timer_users", [_I, _I], _I),
    ("moy_ledc_timer_cfg", [_I, ctypes.POINTER(_U32), ctypes.POINTER(_U8),
                            ctypes.POINTER(_I32)], _B),
    ("moy_ledc_timer_state", [_I], _I),
    ("moy_ledc_reserve", [_I, _I, _U32, _U32, _U8, _I32], _B),
    ("moy_ledc_vm_drop", [], _U32),
    ("moy_ledc_reset", [], None),
)

_LIB = [None]


def lib():
    """The library, its moy_ledc_* calls typed; ImportError with no C compiler."""
    if _LIB[0] is None:
        path = host_native.build()
        if path is None:
            raise ImportError("ledc_book: no C compiler for the host build")
        d = ctypes.CDLL(path)
        for name, args, res in _SIGS:
            fn = getattr(d, name)
            fn.argtypes = args
            fn.restype = res
        _LIB[0] = d
    return _LIB[0]


def cfg(t):
    """A READY timer's (freq, bits, clk); None for any other."""
    f, b, c = _U32(), _U8(), _I32()
    if not lib().moy_ledc_timer_cfg(t, ctypes.byref(f), ctypes.byref(b), ctypes.byref(c)):
        return None
    return f.value, b.value, c.value


def holders():
    """Each channel's holder, 0 for free."""
    return [lib().moy_ledc_chan_holder(ch) for ch in range(CHANNELS)]
