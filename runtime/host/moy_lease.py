"""The lease table (native/moy_kernel/moy_lease.h) on CPython, by ctypes: the
module `moy_lease` as the boards and the desktop MicroPython import it, name
for name (native/moy_kernel/face.json has the list). The table is the C
one in the host's one library (runtime/host/host_native.py), with the host's
stand-in pins under it: `sim_pin`, `sim_level` and `sim_get` are the far end
of a pin, and `pin_state` reads back what the stand-in made of it. The rows
here are the hand ones; the rest are runtime/host/moy_lease_face.py's,
generated."""

import ctypes
import errno
import os

import face_ctypes
import native_build
from moy_lease_face import *  # noqa: F401,F403
from moy_lease_face import (EDGES, PIN_MAX, SHELL, _raise_lease,
                            moy_lease_offer_t as _moy_lease_offer_t,
                            moy_lease_row_t as _moy_lease_row_t)

_KERNEL = os.path.join(native_build.ROOT, "native", "moy_kernel")

PART = native_build.Part("lease", ("moy_lease.h", "moy_lease.c", "moy_dev.h", "moy_dev.c",
                                  "moy_dev_kinds.h", "moy_dev_kinds.c",
                                  "moy_devbus.h", "moy_devbus.c", "moy_ledc.h", "moy_ledc.c", "moy_lock.h",
                                  "moy_lock.c"),
                         (_KERNEL,))

WIFI_TAGS = ("web", "update", "settings", "cart", "link", "carts", "dev", "http", "media", "ssh",
             "native", "net", "kstop")

_U32 = ctypes.c_uint32

_SIGS = (
    ("moy_lease_offer_at", [_U32, ctypes.POINTER(_moy_lease_offer_t)], ctypes.c_int),
    ("moy_lease_take", [_U32, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint64, ctypes.c_uint8,
                        ctypes.POINTER(_U32)], ctypes.c_int),
    ("moy_lease_get", [_U32, ctypes.POINTER(_moy_lease_row_t)], ctypes.c_int),
    ("moy_lease_slots", [], _U32),
    ("moy_lease_at", [_U32], _U32),
    ("moy_lease_wifi_bit", [ctypes.c_char_p, ctypes.c_size_t], _U32),
    ("moy_lease_wifi_hold", [_U32, ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(_U32)],
     ctypes.c_int),
    ("moy_lease_wifi_release", [_U32, ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(_U32)],
     ctypes.c_int),
    ("moy_lease_wifi_mask", [], _U32),
    ("moy_lease_wifi_holder", [_U32, ctypes.POINTER(_U32), ctypes.POINTER(_U32)], ctypes.c_int),
)


def _lib():
    return face_ctypes.lib(_SIGS)


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



def offers():
    out, o, i = [], _moy_lease_offer_t(), 0
    while _lib().moy_lease_offer_at(i, ctypes.byref(o)) == 0:
        out.append((o.name.decode(), o.cls, o.unit, tuple(o.pin[:o.npins]), _tuple(o.pins)))
        i += 1
    return out


def take(owner, cls, unit, pins, flags=0):
    h = _U32()
    _raise_lease(_lib().moy_lease_take(owner, cls, unit, _mask(pins), flags, ctypes.byref(h)))
    return h.value






def leases():
    lib = _lib()
    return [h for h in (lib.moy_lease_at(s) for s in range(lib.moy_lease_slots())) if h]


def get(h):
    r = _moy_lease_row_t()
    _raise_lease(_lib().moy_lease_get(h, ctypes.byref(r)))
    edges = tuple((r.edge_pin[i], r.edge[i]) for i in range(EDGES) if r.edge[i])
    return (r.owner, r.cls, r.unit, r.flags, _tuple(r.pins), edges)












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
        _raise_lease(_lib().moy_lease_wifi_hold(owner, b, len(b), ctypes.byref(mask)))
        return mask.value

    def release(self, tag, owner=SHELL):
        b, mask = self._tag(tag), _U32()
        _raise_lease(_lib().moy_lease_wifi_release(owner, b, len(b), ctypes.byref(mask)))
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


