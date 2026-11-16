"""The `mic` device API's driver (native/moy_audio/moy_mic.h) on CPython, by
ctypes: the module `moy_mic` as the boards import it, name for name
(native/moy_audio/face.json has the list). The host has no microphone:
`sim(rate)` makes one and `push` is its producer (runtime/host/mic_sim.py).
Every row is runtime/host/moy_mic_face.py's, generated."""

import os

import native_build
from moy_mic_face import *  # noqa: F401,F403

_AUDIO = os.path.join(native_build.ROOT, "native", "moy_audio")
_KERNEL = os.path.join(native_build.ROOT, "native", "moy_kernel")

PART = native_build.Part("mic", ("moy_mic.h", "moy_mic.c", "moy_mic_dev.c"), (_AUDIO, _KERNEL))

