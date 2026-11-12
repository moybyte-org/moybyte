"""The `lora` device API's driver (native/moy_net/moy_lora.h, over the LoRaWAN
MAC native/moy_net/moy_lorawan.h) on CPython, by ctypes: the module
`moy_lora` as the boards import it, name for name (native/moy_net/
modmoy_lora.c has the list). The host's radio is the C's stand-in: `sim(on)`
makes one, `sim_sent` and `sim_air` are the air around it
(runtime/host/lora_sim.py), and a test's clock steps it (`step`)."""

from __future__ import annotations

import ctypes
import os

import host_native
import native_build

_NET = os.path.join(native_build.ROOT, "native", "moy_net")
_INPUT = os.path.join(native_build.ROOT, "native", "moy_input")
_KERNEL = os.path.join(native_build.ROOT, "native", "moy_kernel")

PART = native_build.Part("lora", ("moy_aes.h", "moy_aes.c", "moy_lorawan.h", "moy_lorawan.c",
                                  "moy_lora.h", "moy_lora.c", "moy_lora_dev.c"), (_NET, _INPUT, _KERNEL))

EV_RX, EV_TX_DONE, EV_JOINED, EV_JOIN_FAILED, EV_DOWN, EV_UP_DONE = range(1, 7)
IDLE, LISTEN = 0, 1
PKT = 255

_U8, _U16, _U32, _I, _I8, _I16 = (ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32,
                                  ctypes.c_int, ctypes.c_int8, ctypes.c_int16)
_B = ctypes.c_char_p
_P = ctypes.c_void_p


class _Cfg(ctypes.Structure):
    _fields_ = [("freq", _U32), ("bw", _U32), ("sf", _U8), ("cr", _U8), ("power", _I8),
                ("sync", _U8), ("preamble", _U16)]


_SIGS = (
    ("moy_lora_open", [_U32], _I),
    ("moy_lora_close", [_U32], _I),
    ("moy_lora_sweep", [_U32], _U32),
    ("moy_lora_owner", [], _U32),
    ("moy_lora_config", [_U32, ctypes.POINTER(_Cfg)], _I),
    ("moy_lora_listen", [_U32, _I], _I),
    ("moy_lora_send", [_U32, _P, ctypes.c_size_t], _I),
    ("moy_lora_recv", [_U32, _P, ctypes.c_size_t, ctypes.POINTER(_I16), ctypes.POINTER(_I8),
                       ctypes.POINTER(_U8)], _I),
    ("moy_lora_join", [_U32, _B, _B, _B, _U8], _I),
    ("moy_lora_uplink", [_U32, _U8, _P, ctypes.c_size_t, _I, _U8], _I),
    ("moy_lora_joined", [ctypes.POINTER(_U32), ctypes.POINTER(_U32)], _I),
    ("moy_lora_mode", [], _U8),
    ("moy_lora_step", [_U32], _U32),
    ("moy_lora_source", [], _U32),
    ("moy_lora_present", [], _I),
    ("moy_lora_sim", [_I], None),
    ("moy_lora_sim_sent", [_P, ctypes.c_size_t, ctypes.POINTER(_U32), ctypes.POINTER(_U8),
                           ctypes.POINTER(_U32), ctypes.POINTER(_U8), ctypes.POINTER(_U8),
                           ctypes.POINTER(_U16)], _I),
    ("moy_lora_sim_air", [_B, ctypes.c_size_t, _U32, _U8, _U32, _I, _I16, _I8], _I),
    ("moy_lora_reset", [], None),
    ("moy_aes_cmac", [_B, _B, ctypes.c_size_t, _P], None),
)

_LIB = [None]


def _lib():
    if _LIB[0] is None:
        path = host_native.build()
        if path is None:
            raise ImportError("moy_lora: no C compiler for the host build")
        d = ctypes.CDLL(path)
        for name, args, res in _SIGS:
            fn = getattr(d, name)
            fn.argtypes = args
            fn.restype = res
        _LIB[0] = d
    return _LIB[0]


def _buf(b):
    if isinstance(b, (bytes, str)):
        b = bytes(b, "latin-1") if isinstance(b, str) else b
        return ctypes.c_char_p(b), len(b)
    mv = memoryview(b).cast("B")
    return (ctypes.c_char * len(mv)).from_buffer(mv), len(mv)


def open(owner):  # noqa: A001 -- the board module's name
    return _lib().moy_lora_open(owner)


def close(owner):
    return _lib().moy_lora_close(owner)


def sweep(owner):
    return _lib().moy_lora_sweep(owner)


def owner():
    return _lib().moy_lora_owner()


def config(owner, freq, sf, bw, cr, power, sync, preamble):
    c = _Cfg(freq, bw, sf, cr, power, sync, preamble)
    return _lib().moy_lora_config(owner, ctypes.byref(c))


def listen(owner, on):
    return _lib().moy_lora_listen(owner, 1 if on else 0)


def send(owner, buf):
    p, n = _buf(buf)
    return _lib().moy_lora_send(owner, ctypes.cast(p, _P), n)


def recv(owner, buf, meta):
    p, n = _buf(buf)
    rssi, snr, port = _I16(), _I8(), _U8()
    k = _lib().moy_lora_recv(owner, ctypes.cast(p, _P), n, ctypes.byref(rssi),
                             ctypes.byref(snr), ctypes.byref(port))
    if k > 0:
        meta[0], meta[1], meta[2] = rssi.value, snr.value, port.value
    return k


def join(owner, dev_eui, join_eui, app_key, dr):
    for v, n in ((dev_eui, 8), (join_eui, 8), (app_key, 16)):
        if len(v) != n:
            raise ValueError("length")
    return _lib().moy_lora_join(owner, bytes(dev_eui), bytes(join_eui), bytes(app_key), dr)


def uplink(owner, port, buf, confirmed, dr):
    p, n = _buf(buf)
    return _lib().moy_lora_uplink(owner, port, ctypes.cast(p, _P), n, 1 if confirmed else 0, dr)


def joined():
    a, f = _U32(), _U32()
    j = _lib().moy_lora_joined(ctypes.byref(a), ctypes.byref(f))
    return bool(j), a.value, f.value


def mode():
    return _lib().moy_lora_mode()


def step(now_ms):
    return _lib().moy_lora_step(now_ms & 0xFFFFFFFF)


def source():
    return _lib().moy_lora_source()


def present():
    return bool(_lib().moy_lora_present())


def gate_stats():
    return None


def sim(on):
    _lib().moy_lora_sim(1 if on else 0)


def sim_sent():
    buf = (ctypes.c_uint8 * PKT)()
    f, sf, bw, sync, iq, pre = _U32(), _U8(), _U32(), _U8(), _U8(), _U16()
    n = _lib().moy_lora_sim_sent(buf, PKT, ctypes.byref(f), ctypes.byref(sf), ctypes.byref(bw),
                                 ctypes.byref(sync), ctypes.byref(iq), ctypes.byref(pre))
    if n <= 0:
        return None
    return bytes(buf[:n]), f.value, sf.value, bw.value, sync.value, bool(iq.value), pre.value


def sim_air(buf, freq, sf, bw, sync, rssi, snr):
    b = bytes(buf)
    return bool(_lib().moy_lora_sim_air(b, len(b), freq, sf, bw, sync, rssi, snr))


def reset():
    _lib().moy_lora_reset()
