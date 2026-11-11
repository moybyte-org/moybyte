"""The host's stand-in microphone (docs/os_2026-11/hardware.md section 1):
the C driver's stand-in (`moy_mic.sim`) at a rate, fed here. Pure Python
over the driver, so a test hears what an app reads.

  install(rate=22050)     the console has a microphone from now on
  tone(hz, n, amp=8000)   push `n` samples of a sine
  push(samples)           push these
  remove()                no microphone again"""

import math

try:
    from . import moy_mic as _N
except ImportError:  # loaded flat
    import moy_mic as _N

_PHASE = [0.0]


def install(rate=22050):
    _N.sim(rate)


def remove():
    _N.reset()


def push(samples):
    _N.push([int(s) for s in samples])


def tone(hz, n, amp=8000):
    rate = _N.rate() or 22050
    step = 2 * math.pi * hz / rate
    out = []
    p = _PHASE[0]
    for _ in range(n):
        out.append(int(amp * math.sin(p)))
        p += step
    _PHASE[0] = p
    _N.push(out)
    return out
