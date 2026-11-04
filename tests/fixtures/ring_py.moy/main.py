# The render-ahead ring from a Python cart (docs/os_2026-11/hardware.md
# section 2): a 441 Hz square wave, DEPTH frames deep, refilled from the loop.
# The ring's counters are moybyte.snd_counts() and the AUDIORATE line's snd=.

try:
    import moybyte
except ImportError:  # the host: the runtime package
    from runtime import moybyte

DEPTH = 2048
PERIOD = 50
_wave = bytearray(2 * PERIOD * 82)
for _i in range(PERIOD * 82):
    _v = 3000 if _i % PERIOD < PERIOD // 2 else -3000
    _wave[2 * _i] = _v & 255
    _wave[2 * _i + 1] = (_v >> 8) & 255
_mv = memoryview(_wave)
_ph = 0
_on = False


def _init():
    global _on
    _on = moybyte.snd_open(DEPTH)


def _update(dt):
    global _ph
    if not _on:
        return
    room = moybyte.snd()
    while room > 0:
        n = min(room, PERIOD * 82 - _ph)
        got = moybyte.snd(_mv[2 * _ph:2 * (_ph + n)])
        _ph = (_ph + got) % (PERIOD * 82)
        room -= got
        if got < n:
            break


def _draw():
    cls(1)
    print("RING %d %s" % (DEPTH, "ON" if _on else "OFF"), 8, 8, 7)
