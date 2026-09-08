"""Silead GSL3680 capacitive touch (the Guition JC8012P4A1C's 10.1" glass),
the shared driver core -- a NEW part, so it starts in `device/` with the
board's facts (pins, firmware, size, flips) passed in, per the port checklist
(docs/board_ports_2026-08.md, stage 2).

WHAT MAKES THIS PART DIFFERENT from the GT911 both other touch boards carry:
it is RAM-LOADED. The controller has no flash, so after every reset the host
streams the vendor's firmware into it over I2C (~4.6K register writes, ~1.5s
from MicroPython at 400kHz) before it reports anything -- `init()` is that
upload plus the reset dance around it, transcribed from the vendor's factory
driver (esp_lcd_gsl3680.c) and cross-checked against Linux's silead.c, which
runs the same sequence: clear -> reset -> load -> startup -> reset ->
startup, then read 0xB0 and expect 0x5A5A5A5A. The firmware bytes are the
BOARD's (`gsl_fw_jc8012.py`): they carry the sensor geometry of one glass.

WHAT IS DELIBERATELY NOT HERE: Silead's `gsl_point_id` algorithm (78KB of
GPL C that tracks finger ids across frames and re-tunes the sensor) -- the
factory driver runs it on every read, Linux's driver does not, and a console
pointer needs one finger's position, which the chip reports raw at register
0x80 without it. Dropped, not deferred.

THE POLL: 8 bytes from 0x80 -- [0] finger count, [4..5] y, [6..7] x (BOTH
12-bit: the high nibble of [7] is the finger id and bit 14 of y is a flag the
chip raises on some packets; bytes [2..3] are a frame counter).
There is no "buffer ready" flag as on the GT911: every read is the chip's
current answer, so a count of 0 is a release, a count >0 a sample, and only
a FAILED read is "no news" -- the gt911.HeldPoint contract still carries the
hold across a flaky read so a drag never ends on an I2C hiccup. The chip
reports in the FIRMWARE's coordinate space (`raw_w` x `raw_h`), which need
not be the glass's; `Touch` scales.
"""

try:                                    # device: staged flat namespace
    from gt911 import HeldPoint
except ImportError:                     # host tests
    from device.gt911 import HeldPoint

ADDR = 0x40
REG_TOUCH = 0x80          # finger count + points
REG_STATUS = 0xB0         # 0x5A5A5A5A once the firmware runs
REG_PAGE = 0xF0           # page select -- ONE byte on the wire
REG_POWER = 0xE0
REG_CLOCK = 0xE4


def _sleep_ms(ms):
    try:
        import time
        time.sleep_ms(ms)
    except AttributeError:              # host
        import time as _t
        _t.sleep(ms / 1000.0)


class GSL3680:
    """The chip: reset, firmware upload, raw reads. `i2c` is a machine.I2C
    (or anything with writeto_mem/readfrom_mem); `rst`/`int_pin` are
    machine.Pin objects (INT is driven LOW through the reset to select
    address 0x40 -- the vendor's own sequence -- then released to an input);
    `fw` is the panel's firmware bytes (5-byte `<BI` records)."""

    def __init__(self, i2c, rst, fw, int_pin=None, log=None):
        self._i2c = i2c
        self._rst = rst
        self._int = int_pin
        self._fw = fw
        self._log = log
        self.loaded = 0                 # firmware records written (progress/diag)

    # -- register helpers -------------------------------------------------

    def _w(self, reg, data):
        self._i2c.writeto_mem(ADDR, reg, data)

    def _r(self, reg, n):
        return self._i2c.readfrom_mem(ADDR, reg, n)

    def _w32(self, reg, val):
        self._w(reg, bytes((val & 0xFF, (val >> 8) & 0xFF,
                            (val >> 16) & 0xFF, (val >> 24) & 0xFF)))

    # -- the vendor sequence ----------------------------------------------

    def hw_reset(self):
        """The RST pulse, with INT held low across it so the part wakes at
        0x40 (vendor sequence)."""
        if self._int is not None:
            try:
                self._int.init(self._int.OUT, value=0)
            except Exception:  # noqa: BLE001 -- a Pin double without init()
                pass
        self._rst.value(0)
        _sleep_ms(20)
        self._rst.value(1)
        _sleep_ms(20)

    def reset_chip(self):
        """`touch_gsl3680_reset` verbatim: hardware pulse, then the power/
        clock/PC-clear registers."""
        self.hw_reset()
        self._w(REG_POWER, b"\x88")
        _sleep_ms(10)
        self._w(REG_CLOCK, b"\x04")
        _sleep_ms(10)
        self._w32(0xBC, 0)
        _sleep_ms(10)

    def clear_reg(self):
        self._w(REG_POWER, b"\x88")
        _sleep_ms(20)
        self._w(0x88, b"\x01")
        _sleep_ms(5)
        self._w(REG_CLOCK, b"\x04")
        _sleep_ms(5)
        self._w(REG_POWER, b"\x00")
        _sleep_ms(20)

    def load_fw(self, progress=None):
        """Stream the firmware: a page select (offset 0xF0) is ONE byte, every
        other record all four. `progress(done, total)` every 512 records."""
        fw = self._fw
        n = len(fw) // 5
        w = self._i2c.writeto_mem
        mv = memoryview(fw)
        for k in range(n):
            o = k * 5
            reg = fw[o]
            if reg == REG_PAGE:
                w(ADDR, reg, mv[o + 1:o + 2])
            else:
                w(ADDR, reg, mv[o + 1:o + 5])
            if progress is not None and (k & 511) == 511:
                progress(k + 1, n)
        self.loaded = n

    def startup(self):
        self._w(REG_POWER, b"\x00")
        _sleep_ms(10)

    def init(self, progress=None):
        """The whole bring-up. Returns True when the chip reports its firmware
        running (0xB0 == 5A5A5A5A); False is a real answer, not an error."""
        self.clear_reg()
        self.reset_chip()
        self.load_fw(progress)
        self.startup()
        self.reset_chip()
        self.startup()
        if self._int is not None:
            try:
                self._int.init(self._int.IN, self._int.PULL_UP)
            except Exception:  # noqa: BLE001
                pass
        _sleep_ms(30)
        return self.alive()

    def alive(self):
        try:
            return bytes(self._r(REG_STATUS, 4)) == b"\x5a\x5a\x5a\x5a"
        except Exception:  # noqa: BLE001 -- no answer is "not alive"
            return False

    def read(self):
        """(fingers, x, y) of the FIRST point, raw controller coordinates --
        both axes 12-bit, as Linux's silead.c masks them. The vendor's driver
        masks only x, and the chip sets bit 14 of y on some packets (a flag,
        not a coordinate): unmasked, those read y = 16000+ and clamped the
        pointer to the bottom edge on the Guition P4 -- three of five
        calibration taps on 2026-09-06.

        TWO-STAGE, and only the FIRST stage runs on a frame nobody is touching
        (#220). The finger count is byte 0 of this register, so one byte
        answers "is anyone on the glass" -- and that is the answer on every
        frame of a game. Measured on the Guition P4's 400kHz bus: 205us for the
        one byte against 375us for the eight, and the loop pays the difference
        60 times a second forever. NOT a poll-rate change and NOT an interrupt
        gate: the chip is still asked, at the same cadence, on every frame, so
        no tap can fall between two samples that today's read would have seen.
        The point comes from the SECOND transaction's own count, never the
        first's, so a finger that lands between the two reads is reported with
        the coordinates that arrived beside it rather than with a stale pair."""
        if not self._r(REG_TOUCH, 1)[0]:
            return 0, 0, 0
        d = self._r(REG_TOUCH, 8)
        n = d[0]
        x = ((d[7] & 0x0F) << 8) | d[6]
        y = ((d[5] & 0x0F) << 8) | d[4]
        return n, x, y


class Touch:
    """The console-facing pointer over a GSL3680: constructs the bus and the
    chip from the board's facts, uploads the firmware once, and answers
    `poll()` in the shape every board's touch driver does --
    `(x, y, press_edge)` while a finger is down, else None, with `fresh`
    beside it (gt911.HeldPoint's no-news contract)."""

    def __init__(self, fw, w, h, sda, scl, rst, int_pin=None, i2c_id=0,
                 freq=400000, swap_xy=False, flip_x=False, flip_y=False,
                 raw_w=0, raw_h=0, raw_x0=0, raw_y0=0, log=print, progress=None):
        self.w = w
        self.h = h
        self.swap_xy = swap_xy
        self.flip_x = flip_x
        self.flip_y = flip_y
        # The controller's OWN coordinate space, when it is not the glass's:
        # the GSL firmware reports in a space of its own -- an origin
        # (raw_x0, raw_y0) and a span (raw_w, raw_h) that map onto the glass
        # (the Guition P4's: a 5-target fit, 2026-09-06). raw_w/raw_h 0 = the
        # glass's, no scaling.
        self.raw_w = raw_w
        self.raw_h = raw_h
        self.raw_x0 = raw_x0
        self.raw_y0 = raw_y0
        self.available = False
        self.raw = None          # last raw controller coords, for calibrate
        self.fingers = 0
        self._hp = HeldPoint()
        self._chip = None
        try:
            from machine import I2C, Pin
            i2c = I2C(i2c_id, scl=Pin(scl), sda=Pin(sda), freq=freq)
            rst_pin = Pin(rst, Pin.OUT, value=1)
            ip = Pin(int_pin, Pin.OUT, value=0) if int_pin is not None else None
            self._chip = GSL3680(i2c, rst_pin, fw, ip, log=log)
            self.available = self._chip.init(progress)
            if log is not None:
                log("gsl3680: firmware %s (%d records)"
                    % ("running" if self.available else "NOT RUNNING",
                       self._chip.loaded))
        except Exception as exc:  # noqa: BLE001 -- input must never fail closed
            if log is not None:
                log("gsl3680 touch unavailable: %s" % exc)

    @property
    def fresh(self):
        return self._hp.fresh

    def poll(self):
        if not self.available:
            self._hp.fresh = True
            return None
        try:
            n, x, y = self._chip.read()
        except Exception:  # noqa: BLE001 -- a flaky read = NO NEWS, never a release
            return self._hp.hold()
        self.fingers = n
        if n < 1:
            return self._hp.release()
        self.raw = (x, y)
        if self.swap_xy:
            x, y = y, x
        if self.raw_w:
            x = (x - self.raw_x0) * self.w // self.raw_w
        if self.raw_h:
            y = (y - self.raw_y0) * self.h // self.raw_h
        if self.flip_x:
            x = self.w - 1 - x
        if self.flip_y:
            y = self.h - 1 - y
        if x >= self.w:
            x = self.w - 1
        if y >= self.h:
            y = self.h - 1
        if x < 0:
            x = 0
        if y < 0:
            y = 0
        return self._hp.sample(x, y)
