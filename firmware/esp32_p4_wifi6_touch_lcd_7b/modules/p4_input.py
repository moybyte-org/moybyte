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

from gt911 import HeldPoint, REG_STATUS as _REG_STATUS, \
    REG_POINT0 as _REG_POINT1        # x lo/hi, y lo/hi on THIS board's part

GT911_ADDR = 0x5D

# Live-tweakable mapping knobs (module globals, read per poll -- see docstring).
# CALIBRATED on glass 2026-07-08 (run_touch_calibrate, 5-target pass): the GT911
# reports in the PANEL-NATIVE frame, which is mounted 180 degrees from our
# scan-out -- TL taps read as BR, center stays center. So both flips are on.
SWAP_XY = False          # axes are NOT swapped (landscape-configured controller)
FLIP_X = True            # 180-degree panel mount
FLIP_Y = True


class Touch:
    # The hold/stale/bound contract is gt911.HeldPoint now (#202 Phase C, one
    # copy for every GT911 board) -- promoted after THIS driver proved the
    # drift risk: it "had the hold and neither guard until 2026-08-15", holding
    # the point without the staleness flag or the release bound.

    def __init__(self, w=1024, h=600, sda=7, scl=8, freq=400000):
        self.w = w
        self.h = h
        self.available = False
        self.raw = None          # last raw controller coords (pre-mapping), for calibrate
        self._hp = HeldPoint()
        self._i2c = None
        try:
            from machine import I2C, Pin
            self._i2c = I2C(0, scl=Pin(scl), sda=Pin(sda), freq=freq)
            self._i2c.readfrom_mem(GT911_ADDR, _REG_STATUS, 1, addrsize=16)
            self.available = True
        except Exception as exc:  # noqa: BLE001 -- input must never fail closed
            print("Moybyte P4 touch unavailable:", exc)

    @property
    def fresh(self):
        # Read by the frame loop after every poll (pointer.fresh) -- the
        # HeldPoint owns it.
        return self._hp.fresh

    def poll(self):
        """(x, y, press_edge) while a finger is on the glass, else None.

        Sets `self.fresh` beside the return (gt911.HeldPoint's no-news
        contract). T-Deck twin: device_input.Touch.poll."""
        if not self.available:
            self._hp.fresh = True
            return None
        try:
            i2c = self._i2c
            status = i2c.readfrom_mem(GT911_ADDR, _REG_STATUS, 1, addrsize=16)[0]
            if not (status & 0x80):
                # No fresh buffer. The GT911 keeps reporting the held point only
                # via fresh buffers, so "no news" between frames means the finger
                # state is unchanged -- report release only when it says 0 points.
                return self._hp.hold()
            n = status & 0x0F
            i2c.writeto_mem(GT911_ADDR, _REG_STATUS, b"\x00", addrsize=16)
            if n < 1:
                return self._hp.release()
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
            return self._hp.sample(x, y)
        except Exception:  # noqa: BLE001 -- a flaky read = one missed frame, not a crash
            # ...and "one missed frame" must mean NO NEWS, not a finger-up: this
            # used to `return None`, which the caller reads as a release and
            # which therefore ended any drag the flaky read landed in.
            return self._hp.hold()
