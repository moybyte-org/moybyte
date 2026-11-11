"""The battery's charge where a board has a gauge: a cell behind a resistor
divider on an ADC pin (the T-Deck's GPIO4 behind 100k/100k). A board with no
gauge constructs none, and the console's `battery_level` answers None.

`Battery(pin, ratio).level()` is the charge in percent, or None where no cell
reads (no ADC, or a voltage no cell rests at). It reads at most every
REFRESH_MS, since the bar and the popover ask every frame they draw, as the
mean of SAMPLES calibrated reads through a LiPo's resting curve. A charger
holds the cell above its resting voltage, so on USB the level reads high: the
T-Deck has no charge-status line to tell the two apart. A read allocates
nothing (tests/test_frame_alloc.py): the bar asks on every frame it paints.
"""

try:
    from time import ticks_ms as _ticks_ms, ticks_diff as _ticks_diff
except ImportError:  # host: CPython's time
    from time import monotonic as _mono

    def _ticks_ms():
        return int(_mono() * 1000)

    def _ticks_diff(a, b):
        return a - b

# A LiPo cell's resting voltage (mV) against its charge (%); linear between.
CURVE = ((3300, 0), (3500, 5), (3600, 10), (3700, 25), (3750, 40), (3800, 55),
         (3850, 65), (3900, 75), (4000, 85), (4100, 95), (4200, 100))
# Outside these no cell is being read: nothing rests below the one, and a
# LiPo is never above the other (a charger's rail can be).
NO_CELL_MV = 2500
NO_CELL_HIGH_MV = 4400
REFRESH_MS = 5000
SAMPLES = 8


def percent(mv):
    """A cell's resting `mv` as its charge in percent, or None where no cell
    reads (below NO_CELL_MV or above NO_CELL_HIGH_MV)."""
    if mv < NO_CELL_MV or mv > NO_CELL_HIGH_MV:
        return None
    lo_mv, lo_p = CURVE[0]
    if mv <= lo_mv:
        return lo_p
    for i in range(1, len(CURVE)):      # by index: a slice of CURVE is a new tuple
        hi_mv, hi_p = CURVE[i]
        if mv <= hi_mv:
            return lo_p + (hi_p - lo_p) * (mv - lo_mv) // (hi_mv - lo_mv)
        lo_mv, lo_p = hi_mv, hi_p
    return CURVE[-1][1]


class Battery:
    """The gauge on ADC `pin`, the cell at `ratio` times the pin's voltage.
    `adc` stands in for the pin's ADC (anything with `read_uv()`)."""

    def __init__(self, pin, ratio, adc=None):
        self.pin = pin
        self.ratio = ratio
        self._adc = adc
        self._at = None
        self._level = None

    def _open(self):
        if self._adc is None:
            from machine import ADC, Pin
            self._adc = ADC(Pin(self.pin), atten=ADC.ATTN_11DB)
        return self._adc

    def millivolts(self):
        """The cell's voltage now, in mV: the mean of SAMPLES reads."""
        adc = self._open()
        uv = 0
        for _ in range(SAMPLES):
            uv += adc.read_uv()
        return int(uv * self.ratio) // (SAMPLES * 1000)

    def level(self):
        """The charge in percent, or None where no cell reads."""
        now = _ticks_ms()
        if self._at is not None and _ticks_diff(now, self._at) < REFRESH_MS:
            return self._level
        self._at = now
        try:
            self._level = percent(self.millivolts())
        except Exception:  # noqa: BLE001 -- a gauge that will not read is no gauge
            self._level = None
        return self._level
