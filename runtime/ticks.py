"""Monotonic tick helpers -- ONE copy of the MicroPython-or-host clock shim.

Before 2026-08-17 this trio was copy-pasted into seven runtime modules and
device_util (nine `def _ticks_ms` in the tree), and the copies had already
begun to drift: two of the three `_ticks_us` host fallbacks read the WALL
clock (`time.time()`), one read `perf_counter()`. This module is the one body;
everything else imports it (directly, or through `device_util`, which
re-exports the trio so the device tier's import surface is unchanged).

The shape: on MicroPython `time.ticks_*` exists and wins; on CPython the
AttributeError branch synthesizes the same units. `_ticks_diff` works on
either clock's values (MicroPython's wraps, the host's doesn't need to).
`_ticks_us`'s host branch is `perf_counter` -- monotonic and sub-microsecond,
which is what the DRAWBRK/CHROMEBRK phase brackets want; a wall clock only
ever agreed with it by luck.

(`device/moybyte_diag.py` keeps its own hardened `_ticks_ms` on purpose: that
module is the offline diag ring and guards even `import time` having failed.)
"""

import time


def _ticks_ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def _ticks_us():
    try:
        return time.ticks_us()
    except AttributeError:
        return int(time.perf_counter() * 1000000)


def _ticks_diff(a, b):
    try:
        return time.ticks_diff(a, b)
    except AttributeError:
        return a - b


def _sleep_ms(ms):
    # The trio's sleeping sibling: MicroPython's sleep_ms, host-shimmed the
    # same way (device_boot's pace step, the input poller's period).
    try:
        time.sleep_ms(ms)
    except AttributeError:
        time.sleep(ms / 1000.0)
