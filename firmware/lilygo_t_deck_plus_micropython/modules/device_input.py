"""The T-Deck pointer input drivers (extracted from moy_runtime.py): the trackball
(GPIO pulse counters) and the GT911 capacitive touch (I2C0).

Both are self-contained hardware wrappers -- they import only the leaf
device_util (tick + diag helpers) and lazily `machine`, so they sit low in the
device import DAG with no moy_runtime cycle. run_desktop constructs TrackBall() +
Touch(...) and feeds their poll() output into the shared Pointer.

The #69 input-poller LIFECYCLE stays in run_desktop (moybyte.input.InputPoller +
the MOY_INPUT_POLLER knob): it owns every I2C0 transaction off the frame loop and
pokes the public hooks on these objects (touch._source = poller.consume_touch,
keyboard._poller_owned). Only the drivers themselves live here; the threading and
its synchronous fallback remain orchestration in run_desktop.

Device-only module (authored in modules/, not staged from runtime/).
"""
from device_util import _ticks_us, _ticks_diff, _diag_note


class TrackBall:
    """T-Deck trackball: 4 direction GPIOs pulse low when rolled; GPIO0 = click.
    Falling-edge IRQs count pulses; poll() consumes them into nav moves."""

    DIRS = (("up", 3), ("down", 15), ("left", 1), ("right", 2))
    CLICK_PIN = 0

    def __init__(self):
        self.available = False
        self._counts = [0, 0, 0, 0]
        self._click = None
        self._click_prev = 1
        try:
            from machine import Pin

            self._pins = []
            for idx, (_name, gpio) in enumerate(self.DIRS):
                p = Pin(gpio, Pin.IN, Pin.PULL_UP)
                p.irq(self._handler(idx), Pin.IRQ_FALLING)
                self._pins.append(p)
            self._click = Pin(self.CLICK_PIN, Pin.IN, Pin.PULL_UP)
            self.available = True
        except Exception as exc:  # noqa: BLE001
            _diag_note("trackball", "unavailable: %s" % (exc,))

    def _handler(self, idx):
        counts = self._counts
        def _h(pin):
            counts[idx] += 1   # list item + small int: ISR-safe (no allocation)
        return _h

    def poll(self):
        # Returns per-direction pulse counts [up, down, left, right] + click edge,
        # so the cursor moves proportionally to how far the ball was rolled.
        counts = [0, 0, 0, 0]
        for idx in range(4):
            counts[idx] = self._counts[idx]
            self._counts[idx] = 0
        click = False
        if self._click is not None:
            lvl = self._click.value()
            if lvl == 0 and self._click_prev == 1:
                click = True
            self._click_prev = lvl
        return counts, click


# Touch -> canvas mapping, calibrated on hardware (RUN_TOUCH_CALIBRATE byte dump).
# This T-Deck's GT911 already reports landscape coords matching the 320x240 canvas
# (x ~0..320, y ~0..240), so no axis swap is needed -- only the Y axis is inverted
# (raw top=240, bottom=0). read_raw() handles the byte order (y in bytes 0-1, x in
# bytes 2-3); these just scale + flip into canvas space.
TOUCH_SWAP = False      # raw axes already match the landscape canvas
TOUCH_FLIP_X = False
TOUCH_FLIP_Y = True     # GT911 Y runs opposite the screen
TOUCH_RAW_W = 320       # GT911 reported max along x
TOUCH_RAW_H = 240       # GT911 reported max along y


class Touch:
    """T-Deck GT911 capacitive touch over I2C0 (the same bus as the keyboard,
    off the SPI bus -- no display contention). poll() returns an absolute
    (x, y, tap) in canvas coords, where tap is True only on the press edge."""

    ADDRS = (0x5D, 0x14)      # GT911 default / alternate I2C addresses
    REG_STATUS = 0x814E       # touch status: bit7 ready, low nibble = point count
    REG_POINT0 = 0x8150       # point 0: [track, xl, xh, yl, yh, sizel, ...]

    def __init__(self, w, h, i2c=None):
        self.w = w
        self.h = h
        self.available = False
        self.addr = None
        self._i2c = i2c
        self._down = False
        # #69 input-poller hook: when set, poll() consumes staged raw samples from
        # the poller thread instead of reading I2C inline (InputPoller wires it).
        self._source = None
        # #69 per-session I2C latency stats (same shape as TDeckKeyboard's): the
        # GT911 shares I2C0 with the keyboard C3, and the observed kbd= stalls
        # often coincide with inp= spikes -- these counters + the keyboard's let
        # the I2CSTAT line say whether the bus stalls on one peripheral or both.
        self.stat_n = 0
        self.stat_max_us = 0
        self.stat_over5 = 0
        self.stat_over20 = 0
        try:
            from machine import I2C, Pin

            if self._i2c is None:
                self._i2c = I2C(0, scl=Pin(8), sda=Pin(18), freq=400000)
            for a in self.ADDRS:
                try:
                    self._i2c.readfrom(a, 1)
                    self.addr = a
                    self.available = True
                    break
                except Exception:
                    pass
            if not self.available:
                _diag_note("touch", "GT911 not found on I2C0")
        except Exception as exc:  # noqa: BLE001
            _diag_note("touch", "unavailable: %s" % (exc,))

    def read_raw(self):
        """One GT911 read. Returns (rx, ry) when a finger is down, False when the
        controller reports a fresh sample with no touch (finger up), or None when
        no new sample is ready (state unknown -- keep whatever we had). Clears the
        status register after a ready read so the next sample is produced."""
        if not self.available:
            return None
        t0 = _ticks_us()                # #69: time the whole I2C span (1-3 transactions)
        try:
            status = self._i2c.readfrom_mem(self.addr, self.REG_STATUS, 1, addrsize=16)[0]
        except Exception:
            self._stat(t0)
            return None
        if not (status & 0x80):
            self._stat(t0)
            return None  # buffer not ready yet -- do NOT clear, do NOT change state
        raw = False      # ready sample, default "finger up"
        if (status & 0x0F) >= 1:
            try:
                d = self._i2c.readfrom_mem(self.addr, self.REG_POINT0, 4, addrsize=16)
                # This GT911 lays the point out as y(lo,hi) then x(lo,hi) -- see
                # the touch calibration byte dump. Return (x_raw, y_raw) for _map.
                raw = (d[2] | (d[3] << 8), d[0] | (d[1] << 8))
            except Exception:
                raw = None
        try:
            self._i2c.writeto_mem(self.addr, self.REG_STATUS, b"\x00", addrsize=16)
        except Exception:
            pass
        self._stat(t0)
        return raw

    def _stat(self, t0):
        # #69 latency bookkeeping for one read_raw I2C span.
        el = _ticks_diff(_ticks_us(), t0)
        self.stat_n += 1
        if el > self.stat_max_us:
            self.stat_max_us = el
        if el >= 5000:
            self.stat_over5 += 1
            if el >= 20000:
                self.stat_over20 += 1

    def debug_read(self):
        """Calibration only: return (status, 8 raw point bytes) and clear, or None
        when no fresh sample. Lets us see the exact GT911 byte layout."""
        if not self.available:
            return None
        try:
            status = self._i2c.readfrom_mem(self.addr, self.REG_STATUS, 1, addrsize=16)[0]
        except Exception:
            return None
        if not (status & 0x80):
            return None
        data = None
        if (status & 0x0F) >= 1:
            try:
                data = self._i2c.readfrom_mem(self.addr, self.REG_POINT0, 8, addrsize=16)
            except Exception:
                data = None
        try:
            self._i2c.writeto_mem(self.addr, self.REG_STATUS, b"\x00", addrsize=16)
        except Exception:
            pass
        return (status, data)

    def _map(self, rx, ry):
        if TOUCH_SWAP:
            rx, ry = ry, rx
        if TOUCH_FLIP_X:
            rx = TOUCH_RAW_W - 1 - rx
        if TOUCH_FLIP_Y:
            ry = TOUCH_RAW_H - 1 - ry
        x = rx * self.w // TOUCH_RAW_W
        y = ry * self.h // TOUCH_RAW_H
        return max(0, min(self.w - 1, x)), max(0, min(self.h - 1, y))

    def poll(self):
        # #69: threaded mode consumes the poller's staged raw sample (no I2C on
        # the frame loop); unthreaded mode reads the hardware inline as always.
        raw = self._source() if self._source is not None else self.read_raw()
        if not raw:                 # None (no new sample) or False (finger up)
            if raw is False:        # only a confirmed "up" clears the press state
                self._down = False
            return None
        x, y = self._map(raw[0], raw[1])
        tap = not self._down        # press edge -> single tap/click
        self._down = True
        return (x, y, tap)
