"""The `midi` device API's driver (native/moy_input/moy_midi.h) on CPython, by
ctypes: the module `moy_midi` as the boards import it, name for name
(native/moy_input/modmoy_midi.c has the list). The host has no UART reader:
`feed` is the wire (runtime/host/midi_sim.py)."""

from __future__ import annotations

import ctypes
import os

import host_native
import native_build

_INPUT = os.path.join(native_build.ROOT, "native", "moy_input")
_SPINE = os.path.join(native_build.ROOT, "native", "moy_spine")

PART = native_build.Part("midi", ("moy_midi.h", "moy_midi.c", "moy_midi_dev.c"), (_INPUT, _SPINE))

UART, USB, BLE = 1, 2, 3
BLE_CONNECTING, BLE_FINDING, BLE_READY, BLE_FAILED, BLE_GONE = range(5)

_U8, _U32, _I = ctypes.c_uint8, ctypes.c_uint32, ctypes.c_int
_P = ctypes.c_void_p
_B = ctypes.c_char_p

_SIGS = (
    ("moy_midi_open", [_U32], _I),
    ("moy_midi_close", [_U32], _I),
    ("moy_midi_sweep", [_U32], _U32),
    ("moy_midi_owner", [], _U32),
    ("moy_midi_source", [], _U32),
    ("moy_midi_port", [_U32, _I, _U32], _I),
    ("moy_midi_unport", [_U32, _I], _I),
    ("moy_midi_uart", [_U32, _I], _I),
    ("moy_midi_ports", [_U32], _I),
    ("moy_midi_ble", [_U32, _I, _B, _U8], _I),
    ("moy_midi_ble_state", [_U32, _I], _I),
    ("moy_midi_ble_send", [_U32, _I, _B, ctypes.c_size_t, _U32], _I),
    ("moy_midi_feed", [_I, _B, ctypes.c_size_t], None),
    ("moy_midi_usb_in", [_U8, _B, ctypes.c_size_t], None),
    ("moy_midi_ble_in", [_U32, _I, _B, ctypes.c_size_t], _I),
    ("moy_midi_usb_pack", [_B, ctypes.c_size_t, _P, ctypes.c_size_t], _I),
    ("moy_midi_ble_pack", [_B, ctypes.c_size_t, _U32, _P, ctypes.c_size_t], _I),
    ("moy_midi_reset", [], None),
)

_LIB = [None]


def _lib():
    if _LIB[0] is None:
        path = host_native.build()
        if path is None:
            raise ImportError("moy_midi: no C compiler for the host build")
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


def open(owner):  # noqa: A001 -- the board module's name
    return _lib().moy_midi_open(owner)


def close(owner):
    return _lib().moy_midi_close(owner)


def sweep(owner):
    return _lib().moy_midi_sweep(owner)


def owner():
    return _lib().moy_midi_owner()


def source():
    return _lib().moy_midi_source()


def port(owner, transport, unit):
    return _lib().moy_midi_port(owner, transport, unit)


def unport(owner, p):
    return _lib().moy_midi_unport(owner, p)


def ports(owner):
    return _lib().moy_midi_ports(owner)


def uart(owner, p):
    return _lib().moy_midi_uart(owner, p)


def feed(p, buf):
    b = bytes(buf)
    _lib().moy_midi_feed(p, b, len(b))


def usb_in(addr, buf):
    b = bytes(buf)
    _lib().moy_midi_usb_in(addr, b, len(b))


def ble_in(owner, p, buf):
    b = bytes(buf)
    return _lib().moy_midi_ble_in(owner, p, b, len(b))


def ble(owner, p, addr, type_):
    return _lib().moy_midi_ble(owner, p, bytes(addr), type_)


def ble_state(owner, p):
    return _lib().moy_midi_ble_state(owner, p)


def ble_send(owner, p, msg, ms):
    b = bytes(msg)
    return _lib().moy_midi_ble_send(owner, p, b, len(b), ms & 0xFFFFFFFF)


def usb_pack(msg, out):
    b = bytes(msg)
    p, n = _wbuf(out)
    return _lib().moy_midi_usb_pack(b, len(b), p, n)


def ble_pack(msg, ms, out):
    b = bytes(msg)
    p, n = _wbuf(out)
    return _lib().moy_midi_ble_pack(b, len(b), ms, p, n)


def reset():
    _lib().moy_midi_reset()
