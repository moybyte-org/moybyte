"""The `http` device API's driver (native/moy_net/moy_fetch.h) on CPython, by
ctypes: the module `moy_http` as the boards import it, name for name
(native/moy_net/face.json has the list). The C is the host's one library
(runtime/host/host_native.py) with the kernel's client under it, which speaks
plain TCP on the host; `run()` is the host's worker, serving what is queued.
The rows here are the hand ones; the rest are runtime/host/moy_http_face.py's,
generated."""

import ctypes
import os

import face_ctypes
import native_build
from moy_http_face import *  # noqa: F401,F403

_NET = os.path.join(native_build.ROOT, "native", "moy_net")
_INPUT = os.path.join(native_build.ROOT, "native", "moy_input")

PART = native_build.Part("http", ("moy_fetch.h", "moy_fetch.c", "moy_fetch_dev.c"), (_NET, _INPUT))

_I, _P = ctypes.c_int, ctypes.POINTER
_SIGS = (("moy_fetch_state", [ctypes.c_uint32, _P(_I), _P(ctypes.c_uint32), _P(_I)], _I),)


def state(h):
    status, n, err = _I(), ctypes.c_uint32(), _I()
    st = face_ctypes.lib(_SIGS).moy_fetch_state(h, ctypes.byref(status), ctypes.byref(n),
                                                 ctypes.byref(err))
    return st if st < 0 else (st, status.value, n.value, err.value)
