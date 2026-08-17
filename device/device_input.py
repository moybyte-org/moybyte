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
from device_util import _ticks_us, _ticks_ms, _ticks_diff, _diag_note


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
    # #74 INT gate: the GT911's INT line (BOARD_TOUCH_INT=16 in every T-Deck
    # reference example) pulses when the controller has a fresh report, so a
    # poll pass can skip the I2C transaction entirely when nothing happened --
    # the chronic 15-20%-of-reads >20ms clock-stretch stalls live exactly in
    # those blind no-data polls. INT also straps the I2C address at reset
    # (low=0x5D), so it is input-only here (never driven, no pull -- the GT911
    # pushes both levels); counting BOTH edges makes the gate polarity-agnostic.
    # INT_GATE=False is the A/B revert to blind every-pass polling.
    INT_PIN = 16
    INT_GATE = True
    SAFETY_POLL_MS = 250      # gated idle still reads at ~4Hz (miswire/missed-INT net)
    # How long a held finger's last position stays valid without a fresh sample.
    # #74 measured the GT911 clock-stretching 20-45ms on 75-90% of the reads made
    # while a finger is DOWN, so at 30-60fps most frames carry no new sample even
    # though the finger never left the glass -- poll() reports the held point for
    # up to this long (see poll()). Long enough to ride out those stalls (and the
    # rarer status-phase ones), short enough that a MISSED finger-up report frees
    # the pointer in well under a second instead of wedging it down.
    HOLD_SAMPLE_MS = 400

    def __init__(self, w, h, i2c=None):
        self.w = w
        self.h = h
        self.available = False
        self.addr = None
        self._i2c = i2c
        self._down = False
        # The last mapped point + when it landed: poll() re-reports it while the
        # finger is down and no new sample has arrived, and flags those repeats
        # via `fresh` so the console doesn't mistake them for a still finger.
        self._held = None
        self._held_ms = 0
        self.fresh = True
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
        # #74: the FIRST catastrophic stall's context, captured once -- (boot-relative
        # ms, the transaction phase that ate the time: status/point/clear, the status
        # byte if the read got that far, how many reads preceded it). The earlier
        # sessions showed one 1.3-2.5s stall early then a plateau; this says WHERE
        # inside read_raw it lives (wake? status clock-stretch? the clear write?).
        self.stat_first_big = None
        # #74 INT-gate state (fields live even when the pin never comes up, so
        # should_read()/read_raw() need no guards): _int_count is a one-element
        # list bumped by the IRQ handler (list item + small int: ISR-safe, no
        # allocation -- the TrackBall idiom); _int_seen stays False until the
        # pin PROVES itself with a first edge, and until then the gate never
        # engages -- a miswired/mispolarized INT line degrades to today's
        # every-pass polling, never to dead touch.
        self._int_pin = None
        self._int_count = [0]
        self._int_last = 0
        self._int_seen = False
        self._touching = False
        self._last_read_ms = _ticks_ms()
        self.stat_int_edges = 0
        self.stat_skipped = 0
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
            elif self.INT_GATE:
                try:
                    counts = self._int_count
                    def _h(_pin):
                        counts[0] += 1   # list item + small int: ISR-safe
                    p = Pin(self.INT_PIN, Pin.IN)
                    p.irq(_h, Pin.IRQ_RISING | Pin.IRQ_FALLING)
                    self._int_pin = p
                except Exception as exc:  # noqa: BLE001 -- the gate is optional
                    _diag_note("touch", "INT pin unavailable: %s" % (exc,))
        except Exception as exc:  # noqa: BLE001
            _diag_note("touch", "unavailable: %s" % (exc,))

    def should_read(self):
        """#74: should this pass spend a GT911 I2C transaction? True on INT
        activity since the last check, while a touch is in progress (full rate
        until the release report lands -- a missed finger-up would wedge the
        pointer down), on the SAFETY_POLL_MS heartbeat, and always while the
        gate hasn't engaged (no pin, or no edge ever seen). Both the poller
        thread and the synchronous poll() fallback consult this, so a skipped
        pass costs zero bus time either way."""
        if self._int_pin is None:
            return True
        n = self._int_count[0]
        self.stat_int_edges = n
        if n != self._int_last:
            self._int_last = n
            self._int_seen = True
            return True
        if not self._int_seen:
            return True
        if self._touching:
            return True
        if _ticks_diff(_ticks_ms(), self._last_read_ms) >= self.SAFETY_POLL_MS:
            return True
        self.stat_skipped += 1
        return False

    def read_raw(self):
        """One GT911 read. Returns (rx, ry) when a finger is down, False when the
        controller reports a fresh sample with no touch (finger up), or None when
        no new sample is ready (state unknown -- keep whatever we had). Clears the
        status register after a ready read so the next sample is produced.

        #74: each of the up-to-3 transactions is timed separately, so a stall is
        attributed to the phase that ate it (status read / point read / clear
        write), not just the whole span -- the earlier sessions couldn't say WHERE
        the 1.3-2.5s stall lived."""
        if not self.available:
            return None
        self._last_read_ms = _ticks_ms()   # #74: feeds the gate's safety heartbeat
        t0 = _ticks_us()
        status = None
        try:
            status = self._i2c.readfrom_mem(self.addr, self.REG_STATUS, 1, addrsize=16)[0]
        except Exception:
            self._stat(t0, _ticks_us(), 0, 0, "status", status)
            return None
        t1 = _ticks_us()
        if not (status & 0x80):
            self._stat(t0, t1, 0, 0, "status", status)
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
        t2 = _ticks_us()
        try:
            self._i2c.writeto_mem(self.addr, self.REG_STATUS, b"\x00", addrsize=16)
        except Exception:
            pass
        t3 = _ticks_us()
        sp = _ticks_diff(t1, t0)
        pp = _ticks_diff(t2, t1)
        cp = _ticks_diff(t3, t2)
        phase = "status"
        worst = sp
        if pp > worst:
            phase = "point"
            worst = pp
        if cp > worst:
            phase = "clear"
        self._stat(t0, t3, pp, cp, phase, status)
        # #74 gate state: only a confirmed sample moves it -- a not-ready read
        # (the early returns above) says nothing about the finger.
        if raw is False:
            self._touching = False   # confirmed up -> the idle gate may engage
        elif raw is not None:
            self._touching = True    # finger down -> stay at full poll rate
        return raw

    def _stat(self, t0, t_end=None, pp=0, cp=0, phase="status", status=None):
        # #69 latency bookkeeping for one read_raw I2C span; #74 adds the phase
        # attribution + the one-shot first-big-stall context capture.
        el = _ticks_diff(t_end if t_end is not None else _ticks_us(), t0)
        self.stat_n += 1
        if el > self.stat_max_us:
            self.stat_max_us = el
        if el >= 5000:
            self.stat_over5 += 1
            if el >= 20000:
                self.stat_over20 += 1
        if el >= 200000 and self.stat_first_big is None:
            # The first catastrophic stall (>=200ms): remember WHEN (boot ms), WHICH
            # transaction ate it, the status byte (None = the status read itself
            # failed/stalled), and how many reads preceded it -- the #74 fingerprint
            # that says boot-wake vs steady-state and which register access to blame.
            self.stat_first_big = (_ticks_ms(), phase, status, self.stat_n)

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
        # the frame loop); unthreaded mode reads the hardware inline as always
        # (#74: through the same INT gate the poller uses).
        if self._source is not None:
            raw = self._source()
        else:
            raw = self.read_raw() if self.should_read() else None
        if raw is False:            # only a confirmed "up" clears the press state
            self._down = False
            self._held = None
            self.fresh = True       # a release IS news
            return None
        if raw is None:
            # No new sample this pass -- but a finger that was down is still
            # down, so report its last position instead of nothing. The caller
            # reads "no sample" as "no finger" (pointer.down), and a phantom
            # release mid-drag ENDS the gesture: ui.DragTap.frame runs drag_end
            # (which can launch a kinetic fling all by itself, #113) and the
            # rest of the swipe then moves nothing, because a resumed hold
            # carries no new press edge to re-arm the drag. With #74's finger-
            # down stall rate (75-90% of reads take 20-45ms) that happened on
            # roughly every other frame -- the faster the console got, the worse
            # the shelf scrolled. The P4's p4_input.Touch.poll holds the point
            # for the same reason. `fresh` marks these repeats so the kinetic
            # velocity isn't charged a delta the hardware never measured.
            if self._down and self._held is not None:
                if _ticks_diff(_ticks_ms(), self._held_ms) < self.HOLD_SAMPLE_MS:
                    self.fresh = False
                    return (self._held[0], self._held[1], False)
                self._down = False   # missed release: never wedge the pointer down
                self._held = None
            self.fresh = True
            return None
        x, y = self._map(raw[0], raw[1])
        tap = not self._down        # press edge -> single tap/click
        self._down = True
        self._held = (x, y)
        self._held_ms = _ticks_ms()
        self.fresh = True
        return (x, y, tap)
