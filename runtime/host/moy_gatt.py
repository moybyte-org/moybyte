"""The `ble` device API's driver (native/moy_input/moy_gatt.h) on CPython, by
ctypes: the module `moy_gatt` as the boards import it, name for name
(native/moy_input/modmoy_gatt.c has the list). The host has no Bluetooth: the
C's stand-in ops answer from peripherals a test declares (`sim_device`,
`sim_chr`; runtime/host/gatt_sim.py)."""

from __future__ import annotations

import ctypes
import os

try:                                    # the host's runtime package
    from . import host_native, native_build
except ImportError:                     # loaded flat, runtime/ on sys.path
    import host_native
    import native_build

_INPUT = os.path.join(native_build.ROOT, "native", "moy_input")
_SPINE = os.path.join(native_build.ROOT, "native", "moy_spine")

PART = native_build.Part("gatt", ("moy_gatt.h", "moy_gatt.c", "moy_scan.h", "moy_scan.c"),
                        (_INPUT, _SPINE))

(EV_ADV, EV_SCAN_DONE, EV_CONNECTED, EV_DISCONNECTED, EV_FOUND, EV_READ, EV_WRITTEN,
 EV_NOTIFY, EV_ADVERTISING) = range(1, 10)
CONNECTED = 2
VALUE = 244

_U8, _U16, _U32, _I, _I8 = (ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_int,
                            ctypes.c_int8)
_P = ctypes.c_void_p
_B = ctypes.c_char_p


class _Uuid(ctypes.Structure):
    _fields_ = [("len", _U8), ("v", _U8 * 16)]


class _Seen(ctypes.Structure):
    _fields_ = [("addr", _U8 * 6), ("type", _U8), ("rssi", _I8), ("name", ctypes.c_char * 21)]


_SIGS = (
    ("moy_gatt_present", [], _I),
    ("moy_gatt_open", [_U32], _I),
    ("moy_gatt_close", [_U32], _I),
    ("moy_gatt_sweep", [_U32], _U32),
    ("moy_gatt_owner", [], _U32),
    ("moy_gatt_scan", [_U32, _U32], _I),
    ("moy_gatt_seen", [_U32, ctypes.POINTER(_Seen)], _I),
    ("moy_gatt_seen_count", [], _U32),
    ("moy_gatt_connect", [_U32, _B, _U8], _I),
    ("moy_gatt_disconnect", [_U32], _I),
    ("moy_gatt_state", [], _I),
    ("moy_gatt_find", [_U32, ctypes.POINTER(_Uuid)], _I),
    ("moy_gatt_read", [_U32, _U16], _I),
    ("moy_gatt_value", [_U32, _P, ctypes.c_size_t], _I),
    ("moy_gatt_write", [_U32, _U16, _B, ctypes.c_size_t, _I], _I),
    ("moy_gatt_subscribe", [_U32, _U16, _I], _I),
    ("moy_gatt_notified", [_U32, _P, ctypes.c_size_t, ctypes.POINTER(_U16)], _I),
    ("moy_gatt_advertise", [_U32, _B, _B, ctypes.c_size_t, _U32], _I),
    ("moy_gatt_stop_advertising", [_U32], _I),
    ("moy_gatt_source", [], _U32),
    ("moy_gatt_sim", [_I], None),
    ("moy_gatt_sim_device", [_B, _I8, _B], _I),
    ("moy_gatt_sim_chr", [_B, ctypes.POINTER(_Uuid), _U16, _B, ctypes.c_size_t], _I),
    ("moy_gatt_sim_written", [_B, _U16, _P, ctypes.c_size_t], _I),
    ("moy_gatt_sim_notify", [_B, _U16, _B, ctypes.c_size_t], _I),
    ("moy_gatt_sim_adv", [_P, ctypes.c_size_t], _I),
    ("moy_gatt_reset", [], None),
)

_LIB = [None]


def _lib():
    if _LIB[0] is None:
        path = host_native.build()
        if path is None:
            raise ImportError("moy_gatt: no C compiler for the host build")
        d = ctypes.CDLL(path)
        for name, args, res in _SIGS:
            fn = getattr(d, name)
            fn.argtypes = args
            fn.restype = res
        _LIB[0] = d
    return _LIB[0]


def _uuid(b):
    b = bytes(b)
    if len(b) not in (2, 16):
        raise ValueError("uuid")
    u = _Uuid()
    u.len = len(b)
    u.v[:len(b)] = b
    return u


def _addr(a):
    a = bytes(a)
    if len(a) != 6:
        raise ValueError("address")
    return a


def _wbuf(b):
    mv = memoryview(b).cast("B")
    return ctypes.cast((ctypes.c_char * len(mv)).from_buffer(mv), _P), len(mv)


def present():
    return bool(_lib().moy_gatt_present())


def open(owner):  # noqa: A001 -- the board module's name
    return _lib().moy_gatt_open(owner)


def close(owner):
    return _lib().moy_gatt_close(owner)


def sweep(owner):
    return _lib().moy_gatt_sweep(owner)


def owner():
    return _lib().moy_gatt_owner()


def state():
    return _lib().moy_gatt_state()


def source():
    return _lib().moy_gatt_source()


def scan(owner, ms):
    return _lib().moy_gatt_scan(owner, ms)


def seen(i):
    s = _Seen()
    if _lib().moy_gatt_seen(i, ctypes.byref(s)) != 0:
        return None
    return bytes(s.addr), s.type, s.rssi, s.name.decode()


def seen_count():
    return _lib().moy_gatt_seen_count()


def connect(owner, addr, type_):
    return _lib().moy_gatt_connect(owner, _addr(addr), type_)


def disconnect(owner):
    return _lib().moy_gatt_disconnect(owner)


def find(owner, uuid):
    return _lib().moy_gatt_find(owner, ctypes.byref(_uuid(uuid)))


def read(owner, handle):
    return _lib().moy_gatt_read(owner, handle)


def value(owner, buf):
    p, n = _wbuf(buf)
    return _lib().moy_gatt_value(owner, p, n)


def write(owner, handle, buf, rsp):
    b = bytes(buf)
    return _lib().moy_gatt_write(owner, handle, b, len(b), 1 if rsp else 0)


def subscribe(owner, handle, on):
    return _lib().moy_gatt_subscribe(owner, handle, 1 if on else 0)


def notified(owner, buf, meta):
    p, n = _wbuf(buf)
    h = _U16()
    k = _lib().moy_gatt_notified(owner, p, n, ctypes.byref(h))
    if k > 0:
        meta[0] = h.value
    return k


def advertise(owner, name, mfg, interval_ms):
    m = bytes(mfg)
    return _lib().moy_gatt_advertise(owner, name.encode(), m, len(m), interval_ms)


def stop_advertising(owner):
    return _lib().moy_gatt_stop_advertising(owner)


def sim(on):
    _lib().moy_gatt_sim(1 if on else 0)


def sim_device(addr, rssi, name):
    return _lib().moy_gatt_sim_device(_addr(addr), rssi, name.encode())


def sim_chr(addr, uuid, handle, value):
    v = bytes(value)
    return _lib().moy_gatt_sim_chr(_addr(addr), ctypes.byref(_uuid(uuid)), handle, v, len(v))


def sim_written(addr, handle):
    buf = (ctypes.c_uint8 * VALUE)()
    n = _lib().moy_gatt_sim_written(_addr(addr), handle, buf, VALUE)
    return None if n < 0 else bytes(buf[:n])


def sim_notify(addr, handle, data):
    d = bytes(data)
    return bool(_lib().moy_gatt_sim_notify(_addr(addr), handle, d, len(d)))


def sim_adv():
    buf = (ctypes.c_uint8 * 31)()
    n = _lib().moy_gatt_sim_adv(buf, 31)
    return bytes(buf[:n])


def reset():
    _lib().moy_gatt_reset()
