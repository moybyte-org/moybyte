"""The `mic` device API's driver (native/moy_audio/moy_mic.h) on CPython, by
ctypes: the module `moy_mic` as the boards import it, name for name
(native/moy_audio/face.json has the list). The host has no microphone:
`sim(rate)` makes one and `push` is its producer (runtime/host/mic_sim.py).
The rows here are the hand ones; the rest are runtime/host/moy_mic_face.py's,
generated."""

import ctypes
import os

import face_ctypes
import native_build
from moy_mic_face import *  # noqa: F401,F403

_AUDIO = os.path.join(native_build.ROOT, "native", "moy_audio")
_KERNEL = os.path.join(native_build.ROOT, "native", "moy_kernel")

PART = native_build.Part("mic", ("moy_mic.h", "moy_mic.c", "moy_mic_dev.c"), (_AUDIO, _KERNEL))

_SIGS = (("moy_mic_push", [ctypes.POINTER(ctypes.c_int16), ctypes.c_uint32], None),)


def push(buf):
    if isinstance(buf, (bytearray, memoryview)):
        mv = memoryview(buf).cast("B")
        c = (ctypes.c_int16 * (len(mv) // 2)).from_buffer(mv)
    else:
        c = (ctypes.c_int16 * len(buf))(*buf)
    face_ctypes.lib(_SIGS).moy_mic_push(c, len(c))
