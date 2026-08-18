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

TWO FAILURE MODES, both learned on the first consumer's glass (2026-08-19),
recorded together because they present identically (touch dead, taps do
nothing) and are nothing alike underneath:

1. THE SECOND-INSTANCE ARTIFACT. A probe that constructs its own
   machine.I2C(0) beside the driver's reads EIGHT IDENTICAL BYTES per read
   (a per-session constant -- 0xA8/0xB0/0x41/0xAC all observed), finger or
   no finger, while the FIRST instance keeps working. A whole "wedged
   controller" theory was built on such probes before a working desktop
   falsified it. Do not diagnose this driver with a second I2C instance;
   go through the running console's own object (dev channel `py`).

2. THE BOOT RACE. One real full-session touch death: the constructor's
   single probe read lost a race (fresh flash, controller still settling)
   and `available` latched False for the session -- cured by power cycling,
   which is what made it look like a hardware wedge. The constructor now
   RETRIES its probe, and poll() re-probes an unavailable controller every
   ~2s instead of staying dead, so losing the boot race costs seconds.

The constant-byte DETECTOR below covers whichever of these ever shows up
through the driver's own instance: it names the signature on serial after
~5s, because the silent version reads exactly like "nobody is touching the
screen" and cost a debugging session.
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
# date. Current values are the rot-0 landscape mapping (see the docstring);
# glass-calibration pending.
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
        self._hp = HeldPoint()
        self._i2c = i2c
        # Wedge detector state (see THE WEDGE above): consecutive reads that
        # were 8 identical bytes, and whether the one-shot warning fired.
        self._const_n = 0
        self._wedge_said = False
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
            # Not a touch report (the controller talks other frames too):
            # no news, same clause as a flaky read. But COUNT the wedge
            # signature -- 8 identical bytes, every read, forever -- and name
            # it once after ~5s of it, because it otherwise reads exactly
            # like an untouched screen (THE WEDGE, module docstring).
            if (d[0] == d[1] == d[2] == d[3] == d[4] == d[5] == d[6] == d[7]):
                self._const_n += 1
                if self._const_n == 300 and not self._wedge_said:
                    self._wedge_said = True
                    print("Moybyte AXS touch: constant 0x%02x responses -- "
                          "the bridge's touch MCU is wedged; POWER CYCLE the "
                          "board (no reset line, SWRESET does not clear it)"
                          % d[0])
            else:
                self._const_n = 0
            return self._hp.hold()
        self._const_n = 0
        if self._wedge_said:
            self._wedge_said = False
            print("Moybyte AXS touch: real reports again -- wedge cleared")
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
