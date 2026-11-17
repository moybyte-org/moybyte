"""The `midi` device API's driver (native/moy_input/moy_midi.h) on CPython, by
ctypes: the module `moy_midi` as the boards import it, name for name
(native/moy_input/face.json has the list). The host has no UART reader:
`feed` is the wire (runtime/host/midi_sim.py), and `usb_in` the USB host's
(the boards' driver calls it in C). Every row is runtime/host/moy_midi_face.py's,
generated."""

import os

import native_build
from moy_midi_face import *  # noqa: F401,F403

_INPUT = os.path.join(native_build.ROOT, "native", "moy_input")
_SPINE = os.path.join(native_build.ROOT, "native", "moy_spine")

PART = native_build.Part("midi", ("moy_midi.h", "moy_midi.c", "moy_midi_dev.c"), (_INPUT, _SPINE))

