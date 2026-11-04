"""The `usb` device API's driver (native/p4/moy_usb/moy_usb.h) on CPython, by
ctypes: the module `moy_usb` as the P4 boards import it, name for name
(native/p4/moy_usb/modmoy_usb.c has the list). The host has no USB host: the
C's stand-in answers for devices a test attaches (runtime/host/usb_sim.py)."""

from __future__ import annotations

import ctypes
import os

try:                                    # the host's runtime package
    from . import host_native, native_build
except ImportError:                     # loaded flat, runtime/ on sys.path
    import host_native
    import native_build

_USB = os.path.join(native_build.ROOT, "native", "p4", "moy_usb")
_INPUT = os.path.join(native_build.ROOT, "native", "moy_input")
_SPINE = os.path.join(native_build.ROOT, "native", "moy_spine")

PART = native_build.Part("usb", ("moy_usb.h", "moy_usb.c"), (_USB, _INPUT, _SPINE))

OTHER, HID, CDC, MIDI = 0, 1, 2, 3
KEYBOARD, MOUSE = 1, 2
EV_ATTACH, EV_DETACH, EV_REPORT, EV_DATA, EV_SENT = range(1, 6)

_U8, _U16, _U32, _I = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_int
_P = ctypes.c_void_p
_B = ctypes.c_char_p


class _Dev(ctypes.Structure):
    _fields_ = [("addr", _U8), ("cls", _U8), ("kind", _U8), ("iface_class", _U8),
                ("vid", _U16), ("pid", _U16), ("name", ctypes.c_char * 25)]


_SIGS = (
    ("moy_usb_present", [], _I),
    ("moy_usb_open", [_U32], _I),
    ("moy_usb_close", [_U32], _I),
    ("moy_usb_sweep", [_U32], _U32),
    ("moy_usb_owner", [], _U32),
    ("moy_usb_count", [], _U32),
    ("moy_usb_device", [_U32, ctypes.POINTER(_Dev)], _I),
    ("moy_usb_report", [_U32, _U8, _P, ctypes.c_size_t], _I),
    ("moy_usb_serial", [_U32, _U8, _U32], _I),
    ("moy_usb_write", [_U32, _U8, _B, ctypes.c_size_t], _I),
    ("moy_usb_read", [_U32, _U8, _P, ctypes.c_size_t], _I),
    ("moy_usb_source", [], _U32),
    ("moy_usb_sim", [_I], None),
    ("moy_usb_sim_attach", [_U8, _U8, _U8, _U16, _U16, _B], _I),
    ("moy_usb_sim_detach", [_U8], None),
    ("moy_usb_sim_in", [_U8, _B, ctypes.c_size_t], None),
    ("moy_usb_sim_out", [_U8, _P, ctypes.c_size_t, ctypes.POINTER(_U32)], _I),
    ("moy_usb_reset", [], None),
)

_LIB = [None]


def _lib():
    if _LIB[0] is None:
        path = host_native.build()
        if path is None:
            raise ImportError("moy_usb: no C compiler for the host build")
        d = ctypes.CDLL(path)
        for name, args, res in _SIGS:
            fn = getattr(d, name)
            fn.argtypes = args
            fn.restype = res
        _LIB[0] = d
    return _LIB[0]


def _wbuf(b):
    mv = memoryview(b).cast("B")
    return ctypes.cast((ctypes.c_char * len(mv)).from_buffer(mv), _P), len(mv)


def present():
    return bool(_lib().moy_usb_present())


def open(owner):  # noqa: A001 -- the board module's name
    return _lib().moy_usb_open(owner)


def close(owner):
    return _lib().moy_usb_close(owner)


def sweep(owner):
    return _lib().moy_usb_sweep(owner)


def owner():
    return _lib().moy_usb_owner()


def source():
    return _lib().moy_usb_source()


def count():
    return _lib().moy_usb_count()


def device(i):
    d = _Dev()
    if _lib().moy_usb_device(i, ctypes.byref(d)) != 0:
        return None
    return (d.addr, d.cls, d.kind, d.iface_class, d.vid, d.pid, d.name.decode())


def report(owner, addr, buf):
    p, n = _wbuf(buf)
    return _lib().moy_usb_report(owner, addr, p, n)


def serial(owner, addr, baud):
    return _lib().moy_usb_serial(owner, addr, baud)


def write(owner, addr, buf):
    b = bytes(buf)
    return _lib().moy_usb_write(owner, addr, b, len(b))


def read(owner, addr, buf):
    p, n = _wbuf(buf)
    return _lib().moy_usb_read(owner, addr, p, n)


def sim(on):
    _lib().moy_usb_sim(1 if on else 0)


def sim_attach(addr, cls, kind, vid, pid, name):
    return _lib().moy_usb_sim_attach(addr, cls, kind, vid, pid, name.encode())


def sim_detach(addr):
    _lib().moy_usb_sim_detach(addr)


def sim_in(addr, buf):
    b = bytes(buf)
    _lib().moy_usb_sim_in(addr, b, len(b))


def sim_out(addr):
    buf = bytearray(512)
    baud = _U32()
    p, n = _wbuf(buf)
    k = _lib().moy_usb_sim_out(addr, p, n, ctypes.byref(baud))
    return None if k < 0 else (bytes(buf[:k]), baud.value)


def reset():
    _lib().moy_usb_reset()
