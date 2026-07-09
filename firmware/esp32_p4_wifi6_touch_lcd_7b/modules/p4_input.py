"""P4 touch input (#58): GT911 on I2C0 (SDA=7 / SCL=8, addr 0x5D).

Same chip as the T-Deck but with none of its calibration baggage: the 7B's
GT911 is self-configured for the native 1024x600 panel space (hardware-
confirmed at bring-up: a live swipe streamed native coords via plain status-
register polling; INT/RST are not wired, matching the factory config). So this
driver is just: read status 0x814E, read point 1, clear status.

CALIBRATION: the panel is mounted 180 degrees rotated and whether the GT911's
self-config accounts for that is a glass question. The three mapping knobs
below are LIVE module globals (read per poll), so they can be flipped from the
REPL without a reflash:

    import p4_input; p4_input.FLIP_X = True     # then re-run the desktop or
    import moy_runtime; moy_runtime.run_touch_calibrate()

`moy_runtime.run_touch_calibrate()` draws corner targets and prints every
sample (raw + mapped + the current knob state) over serial -- tap the targets,
read which corner the mapped coords land in, set the knobs so mapped == target,
then bake the winning values in here.
"""

GT911_ADDR = 0x5D
_REG_STATUS = 0x814E
_REG_POINT1 = 0x8150     # x lo/hi, y lo/hi (little-endian)

# Live-tweakable mapping knobs (module globals, read per poll -- see docstring).
# CALIBRATED on glass 2026-07-08 (run_touch_calibrate, 5-target pass): the GT911
# reports in the PANEL-NATIVE frame, which is mounted 180 degrees from our
# scan-out -- TL taps read as BR, center stays center. So both flips are on.
SWAP_XY = False          # axes are NOT swapped (landscape-configured controller)
FLIP_X = True            # 180-degree panel mount
FLIP_Y = True


class Touch:
    def __init__(self, w=1024, h=600, sda=7, scl=8, freq=400000):
        self.w = w
        self.h = h
        self.available = False
        self.raw = None          # last raw controller coords (pre-mapping), for calibrate
        self._down = False
        self._last = None
        self._i2c = None
        try:
            from machine import I2C, Pin
            self._i2c = I2C(0, scl=Pin(scl), sda=Pin(sda), freq=freq)
            self._i2c.readfrom_mem(GT911_ADDR, _REG_STATUS, 1, addrsize=16)
            self.available = True
        except Exception as exc:  # noqa: BLE001 -- input must never fail closed
            print("Moybyte P4 touch unavailable:", exc)

    def poll(self):
        """(x, y, press_edge) while a finger is on the glass, else None."""
        if not self.available:
            return None
        try:
            i2c = self._i2c
            status = i2c.readfrom_mem(GT911_ADDR, _REG_STATUS, 1, addrsize=16)[0]
            if not (status & 0x80):
                # No fresh buffer. The GT911 keeps reporting the held point only
                # via fresh buffers, so "no news" between frames means the finger
                # state is unchanged -- report release only when it says 0 points.
                return None if not self._down else self._last
            n = status & 0x0F
            i2c.writeto_mem(GT911_ADDR, _REG_STATUS, b"\x00", addrsize=16)
            if n < 1:
                self._down = False
                return None
            d = i2c.readfrom_mem(GT911_ADDR, _REG_POINT1, 4, addrsize=16)
            x = d[0] | (d[1] << 8)
            y = d[2] | (d[3] << 8)
            self.raw = (x, y)
            if SWAP_XY:
                x, y = y, x
            if FLIP_X:
                x = self.w - 1 - x
            if FLIP_Y:
                y = self.h - 1 - y
            if x >= self.w:
                x = self.w - 1
            if y >= self.h:
                y = self.h - 1
            edge = not self._down
            self._down = True
            self._last = (x, y, False)   # held-state repeat (no re-tap)
            return (x, y, edge)
        except Exception:  # noqa: BLE001 -- a flaky read = one missed frame, not a crash
            return None
