"""AXS15231 touch driver (#202): the touch half of the AXS15231B panel bridge.

New hardware, so it starts life IN the shared tree (docs/board_ports_2026-08.md
Phase C: a driver that is new code starts in the right place), with the board's
facts -- pins, I2C address, panel geometry, mapping knobs -- as constructor
arguments and module globals. First consumer: the Guition JC3248W535
(I2C0 SDA=4 SCL=8, addr 0x3B, portrait 320x480).

THE PROTOCOL (from ESPHome's axs15231 component, proven on the first
consumer's glass by the owner's working ESPHome build): write the 11-byte
read-touchpad command, read 8 bytes back --

    [0] != 0            -> not a touch report; ignore
    [1] == 0            -> zero touch points, i.e. a RELEASE
    x = (d[2] & 0xF) << 8 | d[3]
    y = (d[4] & 0xF) << 8 | d[5]

Raw coordinates are portrait-native panel coords (x along the 320 axis, y
along the 480 axis) -- the working ESPHome landscape config's swap_xy +
mirror_y is exactly the portrait->rotated-90 coordinate change, which is what
says raw itself is unrotated. The knobs below exist for the calibration smoke
to falsify that on glass without a reflash, T-Deck/P4 style.

Unlike the GT911's status-register protocol there is no "no fresh buffer"
state: every successful read reports the CURRENT finger state, so a
zero-points read IS a release. The `gt911.HeldPoint` no-news contract still
applies to the pass that produced NO successful read -- an I2C error mid-drag
must hold the point (stale, bounded), never end the gesture (the phantom-
release lesson, all three clauses in gt911.py's docstring).
"""

try:                                    # device: staged flat namespace
    from gt911 import HeldPoint
except ImportError:                     # host tests
    from device.gt911 import HeldPoint

# The read-touchpad command, verbatim from ESPHome (11 bytes; the trailing
# zeros are part of the command).
_READ_CMD = b"\xb5\xab\xa5\x5a\x00\x00\x00\x08\x00\x00\x00"

# Live-tweakable mapping knobs (module globals, read per poll) -- flip from
# the REPL during the calibration smoke, then bake the winners here with the
# date. Starting values are the ESPHome-derived identity mapping (see the
# module docstring); not yet confirmed by this repo's own smoke.
SWAP_XY = False
FLIP_X = False
FLIP_Y = False


class Touch:
    """The same contract as the two GT911 drivers: poll() -> (x, y, press_edge)
    while a finger is down, else None; `fresh`/`raw`/`available` beside it."""

    def __init__(self, w=320, h=480, sda=4, scl=8, addr=0x3B, freq=400000,
                 i2c=None):
        self.w = w
        self.h = h
        self.addr = addr
        self.available = False
        self.raw = None          # last raw coords (pre-mapping), for calibrate
        self._hp = HeldPoint()
        self._i2c = i2c
        try:
            if self._i2c is None:
                from machine import I2C, Pin
                self._i2c = I2C(0, scl=Pin(scl), sda=Pin(sda), freq=freq)
            # Probe: a touch read must answer, or this driver reports itself
            # unavailable rather than throwing per frame.
            self._read()
            self.available = True
        except Exception as exc:  # noqa: BLE001 -- input must never fail closed
            print("Moybyte AXS touch unavailable:", exc)

    def _read(self):
        i2c = self._i2c
        i2c.writeto(self.addr, _READ_CMD)
        return i2c.readfrom(self.addr, 8)

    @property
    def fresh(self):
        return self._hp.fresh

    def poll(self):
        if not self.available:
            self._hp.fresh = True
            return None
        try:
            d = self._read()
        except Exception:  # noqa: BLE001 -- a flaky read = no news, never a release
            return self._hp.hold()
        if d[0] != 0:
            # Not a touch report (the controller talks other frames too):
            # no news, same clause as a flaky read.
            return self._hp.hold()
        if d[1] == 0:
            return self._hp.release()
        x = ((d[2] & 0x0F) << 8) | d[3]
        y = ((d[4] & 0x0F) << 8) | d[5]
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
