"""The lease table (native/moy_kernel/moy_lease.h) on CPython, by ctypes: the
module `moy_lease` as the boards and the desktop MicroPython import it, name
for name (native/moy_kernel/modmoy_lease.c has the list). The table is the C
one in the host's one library (runtime/host/host_native.py), with the host's
stand-in pins under it: `sim_pin`, `sim_level` and `sim_get` are the far end
of a pin, and `pin_state` reads back what the stand-in made of it."""

from __future__ import annotations

import ctypes
import errno
import os

try:                                    # the host's runtime package
    from . import host_native, native_build
except ImportError:                     # loaded flat, runtime/ on sys.path
    import host_native
    import native_build

_KERNEL = os.path.join(native_build.ROOT, "native", "moy_kernel")

PART = native_build.Part("lease", ("moy_lease.h", "moy_lease.c"), (_KERNEL,))

GPIO, UART, SPI = 1, 2, 3
WIFI = 8
WIFI_TAGS = ("web", "update", "settings", "cart", "link", "carts", "dev", "http", "media", "ssh",
             "native")
VM, PINS = 0x01, 0x02
SHELL = 1
PIN_IN, PIN_OUT, PIN_PU, PIN_PD, PIN_PERIPH = 0x01, 0x02, 0x04, 0x08, 0x10
PIN_SAFE = PIN_IN
ROWS_MAX = 16
EDGES = 4
PIN_MAX = 64
_ERR = (0, errno.EBADF, errno.ENOSPC, errno.ENOMEM, errno.EINVAL, errno.EBUSY)

_U8, _U32, _U64, _I = ctypes.c_uint8, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int


class _Offer(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char * 8), ("cls", _U8), ("unit", _U8), ("npins", _U8),
                ("pin", _U8 * 3), ("pins", _U64)]


class _Row(ctypes.Structure):
    _fields_ = [("pins", _U64), ("owner", _U32), ("edge", _U32 * EDGES),
                ("edge_pin", _U8 * EDGES), ("tags", _U32), ("cls", _U8), ("unit", _U8), ("flags", _U8)]


_SIGS = (
    ("moy_lease_offer", [ctypes.c_char_p], _I),
    ("moy_lease_offer_at", [_U32, ctypes.POINTER(_Offer)], _I),
    ("moy_lease_take", [_U32, _U8, _U8, _U64, _U8, ctypes.POINTER(_U32)], _I),
    ("moy_lease_drop", [_U32], _I),
    ("moy_lease_sweep", [_U32], _U32),
    ("moy_lease_get", [_U32, ctypes.POINTER(_Row)], _I),
    ("moy_lease_live", [_U32], ctypes.c_bool),
    ("moy_lease_slots", [], _U32),
    ("moy_lease_at", [_U32], _U32),
    ("moy_lease_held", [_U8], _U32),
    ("moy_lease_of_pin", [_I], _U32),
    ("moy_lease_edge", [_U32, _I, _U32, ctypes.c_bool, ctypes.POINTER(_U32)], _I),
    ("moy_lease_edge_close", [_U32, _I], _I),
    ("moy_lease_pin_state", [_I], _U8),
    ("moy_lease_device_name", [_U8], ctypes.c_char_p),
    ("moy_lease_sim_pin", [_I, _U8, _I], None),
    ("moy_lease_sim_level", [_I, _I], None),
    ("moy_lease_sim_get", [_I], _I),
    ("moy_lease_reset", [], None),
    ("moy_lease_wifi_bit", [ctypes.c_char_p, ctypes.c_size_t], _U32),
    ("moy_lease_wifi_hold", [_U32, ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(_U32)], _I),
    ("moy_lease_wifi_release", [_U32, ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(_U32)],
     _I),
    ("moy_lease_wifi_mask", [], _U32),
    ("moy_lease_wifi_holder", [_U32, ctypes.POINTER(_U32), ctypes.POINTER(_U32)], _I),
    ("moy_lease_wifi_reset", [], None),
)

_LIB = [None]


def _lib():
    if _LIB[0] is None:
        path = host_native.build()
        if path is None:
            raise ImportError("moy_lease: no C compiler for the host build")
        d = ctypes.CDLL(path)
        for name, args, res in _SIGS:
            fn = getattr(d, name)
            fn.argtypes = args
            fn.restype = res
        _LIB[0] = d
    return _LIB[0]


def _check(rc):
    if rc:
        e = _ERR[rc] if 0 < rc < len(_ERR) else errno.EIO
        raise OSError(e, os.strerror(e))


def _tuple(mask):
    return tuple(p for p in range(PIN_MAX) if mask >> p & 1)


def _mask(pins):
    m = 0
    for p in pins:
        p = int(p)
        if not 0 <= p < PIN_MAX:
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))
        m |= 1 << p
    return m


def offer(spec):
    _check(_lib().moy_lease_offer(None if spec is None else spec.encode()))


def offers():
    out, o, i = [], _Offer(), 0
    while _lib().moy_lease_offer_at(i, ctypes.byref(o)) == 0:
        out.append((o.name.decode(), o.cls, o.unit, tuple(o.pin[:o.npins]), _tuple(o.pins)))
        i += 1
    return out


def take(owner, cls, unit, pins, flags=0):
    h = _U32()
    _check(_lib().moy_lease_take(owner, cls, unit, _mask(pins), flags, ctypes.byref(h)))
    return h.value


def drop(h):
    _check(_lib().moy_lease_drop(h))


def sweep(owner):
    return _lib().moy_lease_sweep(owner)


def live(h):
    return bool(_lib().moy_lease_live(h))


def leases():
    lib = _lib()
    return [h for h in (lib.moy_lease_at(s) for s in range(lib.moy_lease_slots())) if h]


def get(h):
    r = _Row()
    _check(_lib().moy_lease_get(h, ctypes.byref(r)))
    edges = tuple((r.edge_pin[i], r.edge[i]) for i in range(EDGES) if r.edge[i])
    return (r.owner, r.cls, r.unit, r.flags, _tuple(r.pins), edges)


def held(flags=0):
    return _lib().moy_lease_held(flags)


def of_pin(pin):
    return _lib().moy_lease_of_pin(pin)


def edge(h, pin, depth, loop=False):
    src = _U32()
    _check(_lib().moy_lease_edge(h, pin, depth, bool(loop), ctypes.byref(src)))
    return src.value


def edge_close(h, pin):
    _check(_lib().moy_lease_edge_close(h, pin))


def pin_state(pin):
    return _lib().moy_lease_pin_state(pin)


def device_name(cls):
    n = _lib().moy_lease_device_name(cls)
    return n.decode() if n is not None else None


def sim_pin(pin, state, level):
    _lib().moy_lease_sim_pin(pin, state, level)


def sim_level(pin, level):
    _lib().moy_lease_sim_level(pin, level)


def sim_get(pin):
    return _lib().moy_lease_sim_get(pin)


def reset():
    _lib().moy_lease_reset()


class Wifi:
    """The radio's holders (moy_lease.h's THE RADIO): a view of its rows, the
    shell's (`owner` SHELL) and one a process that holds it owns."""

    @staticmethod
    def _tag(tag):
        if not isinstance(tag, str):
            raise TypeError("a lease tag is a str")
        b = tag.encode()
        if not _lib().moy_lease_wifi_bit(b, len(b)):
            raise ValueError("unknown lease tag %s" % tag)
        return b

    def hold(self, tag, owner=SHELL):
        b, mask = self._tag(tag), _U32()
        _check(_lib().moy_lease_wifi_hold(owner, b, len(b), ctypes.byref(mask)))
        return mask.value

    def release(self, tag, owner=SHELL):
        b, mask = self._tag(tag), _U32()
        _check(_lib().moy_lease_wifi_release(owner, b, len(b), ctypes.byref(mask)))
        return mask.value

    def mask(self):
        return _lib().moy_lease_wifi_mask()

    def held(self, tag):
        b = self._tag(tag)
        return bool(self.mask() & _lib().moy_lease_wifi_bit(b, len(b)))

    def holders(self):
        out, owner, tags, i = [], _U32(), _U32(), 0
        while _lib().moy_lease_wifi_holder(i, ctypes.byref(owner), ctypes.byref(tags)) == 0:
            for j, tag in enumerate(WIFI_TAGS):
                if tags.value >> j & 1:
                    out.append(tag if owner.value == SHELL else "%s:%d" % (tag, owner.value))
            i += 1
        return out


def wifi_reset():
    _lib().moy_lease_wifi_reset()
