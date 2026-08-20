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
along the 480 axis). The CONSOLE frame on the first consumer is landscape
480x320 with the 90-degree rotation done in moy_axs's band copy (the panel's
MADCTL MV is dead on that glass), so the knobs below carry the same rotation:
for moy_axs rot 0 the mapping is SWAP_XY + FLIP_X (logical x = 479 - raw_y,
logical y = raw_x); rot 1 would be SWAP_XY + FLIP_Y. Calibrate on glass with
guition_smoke.touch() and bake the winners here with the date.

Unlike the GT911's status-register protocol there is no "no fresh buffer"
state: every successful read reports the CURRENT finger state, so a
zero-points read IS a release. The `gt911.HeldPoint` no-news contract still
applies to the pass that produced NO successful read -- an I2C error mid-drag
must hold the point (stale, bounded), never end the gesture (the phantom-
release lesson, all three clauses in gt911.py's docstring).

THE CONSTANT-BYTE STREAM IS THE IDLE FILLER -- the FINAL story
(2026-08-19 evening, after a full day of theories each killed by better
data; the arc is in git history and #202, kept there as a lesson in
diagnosing with side probes). Reads while NOTHING touches the glass
return eight identical bytes whose value varies per power-up
(0xA8/0xB0/0x41/0xAC all observed). That is NORMAL. It was misread, in
order, as: a bridge MCU wedged by MADCTL experiments, a second-instance
I2C artifact, and a cable-flash-induced crash needing a power cycle --
because every probe that "confirmed" those theories happened to sample
while no finger was on the glass. What the controller actually does,
measured through the live console's own driver: STREAM ~55-60Hz reports
while touched (moving OR resting -- a 5s held-still trace read 88%
fresh, worst touched gap ~50ms) and return pure filler once lifted.
The filler IS the lift signal; the driver's 90ms no-news bound below is
built on exactly that.

THE BOOT RACE is separate and real: the constructor's single probe read
can lose (fresh boot, controller settling) and `available` used to latch
False for the session -- the one genuinely dead-touch episode. The ctor
now retries and poll() re-probes every ~2s. Diagnose this driver through
the running console's own object (dev channel `py`), never a fresh side
instance or SoftI2C -- an idle-time probe reads filler and looks exactly
like the disproven "wedge".
"""

try:                                    # device: staged flat namespace
    from gt911 import HeldPoint
except ImportError:                     # host tests
    from device.gt911 import HeldPoint

# The read-touchpad command, verbatim from ESPHome (11 bytes; the trailing
# zeros are part of the command).
_READ_CMD = b"\xb5\xab\xa5\x5a\x00\x00\x00\x08\x00\x00\x00"

# Live-tweakable mapping knobs (module globals, read per poll) -- flip from
# the REPL during a calibration smoke, then bake the winners here with the
# date. These ARE the baked winners: the rot-0 landscape mapping (see the
# docstring), CALIBRATED ON GLASS 2026-08-19 -- taps land under the finger,
# corners included, and tests/test_guition_on_glass.py passed 10/10 on the
# landscape console the same evening.
SWAP_XY = True
FLIP_X = True
FLIP_Y = False


class Touch:
    """The same contract as the two GT911 drivers: poll() -> (x, y, press_edge)
    while a finger is down, else None; `fresh`/`raw`/`available` beside it."""

    def __init__(self, w=480, h=320, sda=4, scl=8, addr=0x3B, freq=400000,
                 i2c=None):
        self.w = w
        self.h = h
        self.addr = addr
        self.available = False
        self.raw = None          # last raw coords (pre-mapping), for calibrate
        self._hp = HeldPoint(extrapolate=True, w=w, h=h)
        # THE PER-CONTROLLER NO-NEWS BOUND (2026-08-19, measured on glass).
        # HeldPoint's 400ms default is sized for the GT911's #74 stall
        # clusters. This controller STREAMS while touched -- moving OR
        # resting: a 5s held-still trace read 88% fresh at ~55Hz, worst
        # touched gap 3 frames (~50ms) -- and goes SILENT only after a lift.
        # So no-news past ~2x the worst touched gap IS the lift: 90ms turns
        # "release" from a 400ms hold-expiry (the fling launched from
        # 400ms-stale velocity -- the drag-hang-then-move bug) into a prompt
        # one, with the velocity still current.
        self._hp.HOLD_SAMPLE_MS = 90
        # The hold-window EXTRAPOLATION (the owner's felt tail: "goes fast,
        # slows down a bit, speeds up again" -- freezing the held point made
        # the <=90ms release window a visible stall mid-flick) is the shared
        # gt911.HeldPoint's now, opted in above: born here, promoted the day
        # the T-Deck became its second consumer.
        self._i2c = i2c
        self._reprobe_n = 0
        try:
            if self._i2c is None:
                from machine import I2C, Pin
                self._i2c = I2C(0, scl=Pin(scl), sda=Pin(sda), freq=freq)
        except Exception as exc:  # noqa: BLE001 -- input must never fail closed
            print("Moybyte AXS touch: no I2C bus:", exc)
            return
        # Probe with retries (THE BOOT RACE above): one lost read at boot
        # must cost milliseconds, not the session.
        for attempt in range(3):
            try:
                self._read()
                self.available = True
                return
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    print("Moybyte AXS touch unavailable (will re-probe):", exc)
                else:
                    try:
                        import time
                        time.sleep_ms(50)
                    except Exception:  # noqa: BLE001
                        pass

    def _read(self):
        i2c = self._i2c
        i2c.writeto(self.addr, _READ_CMD)
        return i2c.readfrom(self.addr, 8)

    @property
    def fresh(self):
        return self._hp.fresh

    def poll(self):
        if not self.available:
            # Lazy re-probe (THE BOOT RACE): every ~120th poll pass (~2s at
            # frame rate), one read attempt. Success flips available for good.
            self._reprobe_n += 1
            if self._i2c is not None and self._reprobe_n >= 120:
                self._reprobe_n = 0
                try:
                    self._read()
                    self.available = True
                    print("Moybyte AXS touch: came up on re-probe")
                except Exception:  # noqa: BLE001
                    pass
            self._hp.fresh = True
            return None
        try:
            d = self._read()
        except Exception:  # noqa: BLE001 -- a flaky read = no news, never a release
            return self._hp.hold()
        if d[0] != 0:
            # Not a touch report: the IDLE FILLER (docstring). While a finger
            # is held this is a rare 1-3 frame gap -- HeldPoint extrapolates
            # through it; past the 90ms bound it is the lift.
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
        # BOTH bounds, not just the upper one. The controller reports a raw
        # 12-bit coordinate and a touch past the panel edge reads BIGGER than
        # the axis it is mapped onto -- which a flip then turns NEGATIVE, so
        # clamping only the top let an off-glass press arrive as a point off
        # the other side of the screen.
        if x < 0:
            x = 0
        elif x >= self.w:
            x = self.w - 1
        if y < 0:
            y = 0
        elif y >= self.h:
            y = self.h - 1
        return self._hp.sample(x, y)
