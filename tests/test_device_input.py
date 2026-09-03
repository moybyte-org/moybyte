"""`device/device_input.py`, EXECUTED (#208, the single-consumer list).

The T-Deck's trackball and GT911 touch driver: 350 lines behind one importer
(`moy_runtime.run_desktop`), whose only nets were source-text greps in
`tests/test_micropython_spike.py` and two checks there that build a `Touch`
with `__new__` and hand-fill its fields -- so `__init__` (the address probe, the
INT-pin claim and every degrade-instead-of-die arm inside it) had never run at
all, and the hand-filled shape had already drifted off the real one (`_down` is
set there and no longer exists in the body). `tests/test_touch_sample_gaps.py`
said outright that the hold behaviour was "pinned by the firmware grep test".

Nothing here is a transcription: the real file is loaded and run, with the
hardware stubbed at its two import boundaries -- `from machine import Pin` /
`from machine import I2C, Pin` inside the constructors, and the `i2c=`
argument. The stubs live in a fixture and are removed on teardown, because a
`machine` left in `sys.modules` would silently change what every later suite in
the worker imports.

NOT executable on a host, and deliberately not faked into looking covered:

* the GT911's 20-45ms clock stretch and the C3 keyboard's bus contention (#74)
  -- the latency numbers `_stat` buckets are real bus time, and the doubles here
  supply them from an injected clock, so this suite pins the BOOKKEEPING, never
  that the bus really stalls;
* the INT line's address-strap-at-reset behaviour, which is why the pin is
  claimed input-only with no pull;
* IRQ delivery itself (a `Pin.irq` handler firing from silicon). The doubles
  call the registered handler directly, which pins the wiring and the ISR-safe
  counter idiom, not the interrupt.

All dt is injected (the wall-clock rule), so every latency figure below is
exact.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEVICE = ROOT / "device"

MODNAME = "moybyte_test_device_input"


# -- the doubles ---------------------------------------------------------------


class Clock:
    """One monotonic source behind `_ticks_us`/`_ticks_ms`, advanced explicitly
    and by the bus double inside each transaction -- which is what makes
    read_raw's per-phase attribution observable."""

    def __init__(self, us=0):
        self.us = us

    def ticks_us(self):
        return self.us

    def ticks_ms(self):
        return self.us // 1000

    def advance_us(self, n):
        self.us += n

    def advance_ms(self, n):
        self.us += n * 1000


class PinRecorder:
    """Builds the fake `machine.Pin` and keeps every pin the body claimed.

    `fail_on` / `irq_fail_on` are the two ways a real claim fails (the GPIO is
    already owned; the IRQ table is full) and both land in the same `except`.
    """

    def __init__(self):
        self.pins = {}
        self.fail_on = set()
        self.irq_fail_on = set()

    def make_class(self):
        rec = self

        class Pin:
            IN = 1
            OUT = 3
            PULL_UP = 2
            IRQ_FALLING = 4
            IRQ_RISING = 8

            def __init__(self, gpio, mode=None, pull=None):
                if gpio in rec.fail_on:
                    raise ValueError("GPIO%d already in use" % gpio)
                self.gpio = gpio
                self.mode = mode
                self.pull = pull
                self.handler = None
                self.trigger = None
                self._level = 1
                rec.pins[gpio] = self

            def irq(self, handler, trigger):
                if self.gpio in rec.irq_fail_on:
                    raise OSError("no free IRQ slot")
                self.handler = handler
                self.trigger = trigger

            def value(self, v=None):
                if v is None:
                    return self._level
                self._level = v

            # -- the observation hooks (not machine.Pin verbs) --------------
            def pulse(self, n=1):
                for _ in range(n):
                    self.handler(self)

        return Pin


class Gt911Bus:
    """A GT911 on I2C0.

    `reports` is a queue of `(status_byte, 8 point bytes)`; the STATUS CLEAR is
    what advances it, exactly as the part behaves -- so a body that stopped
    clearing re-reads the same report forever and a body that clears when it
    should not throws the next sample away.
    """

    REG_STATUS = 0x814E
    REG_POINT0 = 0x8150

    def __init__(self, clock, present=(0x5D,), reports=None):
        self.clock = clock
        self.present = set(present)
        self.reports = list(reports or [])
        self.cost = {"status": 0, "point": 0, "clear": 0}
        self.fail = set()
        self.calls = []          # (phase, addr, addrsize)
        self.probes = []

    def names(self):
        return [c[0] for c in self.calls]

    # -- the machine.I2C surface the body uses ---------------------------------

    def readfrom(self, addr, n):
        self.probes.append(addr)
        if addr not in self.present:
            raise OSError(19, "ENODEV")
        return b"\x00" * n

    def _report(self):
        return self.reports[0] if self.reports else (0x00, b"\x00" * 8)

    def readfrom_mem(self, addr, reg, n, addrsize=8):
        if reg == self.REG_STATUS:
            self.calls.append(("status", addr, addrsize))
            self.clock.advance_us(self.cost["status"])
            if "status" in self.fail:
                raise OSError("clock stretch")
            return bytes([self._report()[0]])
        if reg == self.REG_POINT0:
            self.calls.append(("point", addr, addrsize))
            self.clock.advance_us(self.cost["point"])
            if "point" in self.fail:
                raise OSError("nak")
            return bytes(self._report()[1])[:n]
        raise AssertionError("unexpected register 0x%04X" % reg)

    def writeto_mem(self, addr, reg, buf, addrsize=8):
        self.calls.append(("clear", addr, addrsize))
        assert reg == self.REG_STATUS and buf == b"\x00"
        self.clock.advance_us(self.cost["clear"])
        if "clear" in self.fail:
            raise OSError("nak")
        if self.reports:
            self.reports.pop(0)


def point(x, y):
    """The T-Deck part's byte layout: y(lo,hi) then x(lo,hi)."""
    return bytes([y & 0xFF, y >> 8, x & 0xFF, x >> 8, 0, 0, 0, 0])


DOWN = (0x81, point(100, 50))      # ready, one point
UP = (0x80, b"\x00" * 8)           # ready, zero points -- confirmed finger up
NOT_READY = (0x00, b"\x00" * 8)    # buffer not filled yet


class Board:
    """The loaded module plus everything the fixture stubbed under it."""

    def __init__(self, module, clock, pins, bus, notes, machine, i2c_ctors):
        self.module = module
        self.clock = clock
        self.pins = pins
        self.bus = bus
        self.notes = notes
        self.machine = machine
        self.i2c_ctors = i2c_ctors

    def note(self, tag):
        for t, m in self.notes:
            if t == tag:
                return m
        return None

    def trackball(self):
        return self.module.TrackBall()

    def touch(self, w=320, h=240, i2c=None, reports=None):
        if reports is not None:
            self.bus.reports = list(reports)
        return self.module.Touch(w, h, i2c=i2c)


@pytest.fixture
def board():
    """Load the REAL `device/device_input.py` with `machine` stubbed.

    Three sys.modules names are involved and all three are restored:

    * `device_util` -- the module's top-level import has NO host fallback, so it
      has to be present under its flat device name (the frozen tree's shape);
    * `gt911` -- the body tries the flat name first and falls back to
      `device.gt911`. Which lane it gets on the host depends on whether some
      other suite has already put `device/` on sys.path, and a flat import that
      succeeds builds a SECOND module object with its own `_ticks_ms`. So the
      flat name is bound to `device.gt911` here: one module either way, which is
      what the frozen device tree has and what makes the clock patch below
      reach the HeldPoint the driver actually builds;
    * `machine` -- installed so the real `__init__`s run their IRQ-registration
      and address-probe paths. Leaving it behind would poison the worker.

    The module object itself is never registered in sys.modules: a fresh one per
    test is what makes a `TOUCH_SWAP` or `Touch.INT_GATE` override self-restoring.
    """
    from device import gt911 as real_gt911

    saved = {k: sys.modules.get(k, KeyError) for k in
             ("device_util", "gt911", "machine")}

    if not isinstance(saved["device_util"], types.ModuleType):
        spec = importlib.util.spec_from_file_location(
            "device_util", DEVICE / "device_util.py")
        du = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(du)
        sys.modules["device_util"] = du
    sys.modules["gt911"] = real_gt911

    clock = Clock(us=7_000_000)      # a board that has been up a while
    pins = PinRecorder()
    bus = Gt911Bus(clock)
    ctors = []

    machine = types.ModuleType("machine")
    machine.Pin = pins.make_class()

    def I2C(*a, **kw):
        ctors.append((a, kw))
        return bus

    machine.I2C = I2C
    sys.modules["machine"] = machine

    spec = importlib.util.spec_from_file_location(
        MODNAME, DEVICE / "device_input.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    notes = []
    mod._ticks_us = clock.ticks_us
    mod._ticks_ms = clock.ticks_ms
    mod._diag_note = lambda tag, msg: notes.append((tag, msg))
    gt_clock = real_gt911._ticks_ms
    real_gt911._ticks_ms = clock.ticks_ms

    b = Board(mod, clock, pins, bus, notes, machine, ctors)
    try:
        yield b
    finally:
        real_gt911._ticks_ms = gt_clock
        for k, v in saved.items():
            if v is KeyError:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_the_module_under_test_is_the_real_shipped_file(board):
    """No host copy of the body anywhere in here: this is the file the T-Deck
    freezes, and the gt911 core it reaches is the shared one both GT911 boards
    use."""
    from device import gt911

    assert board.module.__file__ == str(DEVICE / "device_input.py")
    assert board.module.gt911 is gt911


def test_the_gt911_import_falls_back_to_the_package_off_the_device(board):
    """The other arm of the module's own try/except: on a host there is no flat
    `gt911`, and the fallback is what keeps this file importable at all. Loaded
    a second time here with the flat name FORCED to fail, because the fixture
    binds it (see its docstring) and the fallback would otherwise never run."""
    from device import gt911

    saved = sys.modules["gt911"]
    sys.modules["gt911"] = None            # the documented way to force ImportError
    try:
        spec = importlib.util.spec_from_file_location(
            MODNAME + "_fallback", DEVICE / "device_input.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.modules["gt911"] = saved
    assert mod.gt911 is gt911


# -- TrackBall -----------------------------------------------------------------


def test_the_trackball_claims_its_four_direction_gpios_and_the_click_pin(board):
    tb = board.trackball()
    assert tb.available is True
    assert sorted(board.pins.pins) == [0, 1, 2, 3, 15]
    Pin = board.machine.Pin
    for gpio in (1, 2, 3, 15):
        p = board.pins.pins[gpio]
        assert (p.mode, p.pull) == (Pin.IN, Pin.PULL_UP)
    # GPIO0 is the click, and it is polled, never IRQ'd.
    assert board.pins.pins[0].handler is None
    assert tb._click is board.pins.pins[0]


def test_the_direction_irqs_are_falling_edge_only(board):
    """The ball pulses the line LOW as it rolls; counting both edges would
    double every move."""
    board.trackball()
    Pin = board.machine.Pin
    for gpio in (1, 2, 3, 15):
        assert board.pins.pins[gpio].trigger == Pin.IRQ_FALLING


def test_each_direction_irq_counts_into_its_own_slot(board):
    """The handler is built in a loop, so a closure that captured the loop
    variable late -- or an index hard-wired to 0 -- puts every pulse in one
    bucket. poll() reports [up, down, left, right] on GPIOs 3/15/1/2."""
    tb = board.trackball()
    board.pins.pins[3].pulse(2)      # up
    board.pins.pins[15].pulse(5)     # down
    board.pins.pins[1].pulse(1)      # left
    board.pins.pins[2].pulse(7)      # right
    counts, _click = tb.poll()
    assert counts == [2, 5, 1, 7]


def test_poll_drains_the_counters_so_a_pulse_is_consumed_once(board):
    """The cursor moves proportionally to the roll; an undrained counter would
    keep moving it after the ball stopped."""
    tb = board.trackball()
    board.pins.pins[3].pulse(4)
    assert tb.poll()[0] == [4, 0, 0, 0]
    assert tb.poll()[0] == [0, 0, 0, 0]
    board.pins.pins[3].pulse(1)
    assert tb.poll()[0] == [1, 0, 0, 0]


def test_the_click_fires_once_per_press_edge_not_while_held(board):
    tb = board.trackball()
    click = board.pins.pins[0]
    assert tb.poll()[1] is False          # idle: pulled up
    click.value(0)
    assert tb.poll()[1] is True           # the press edge
    assert tb.poll()[1] is False          # ...and only the edge
    assert tb.poll()[1] is False


def test_a_release_re_arms_the_click(board):
    tb = board.trackball()
    click = board.pins.pins[0]
    click.value(0)
    tb.poll()
    click.value(1)
    assert tb.poll()[1] is False
    click.value(0)
    assert tb.poll()[1] is True


def test_a_trackball_that_cannot_claim_its_pins_degrades_to_no_input(board):
    """Every hardware claim in this file is optional: a board without a working
    trackball must still boot with a working touchscreen."""
    board.pins.fail_on.add(3)
    tb = board.trackball()
    assert tb.available is False
    assert tb.poll() == ([0, 0, 0, 0], False)
    assert "unavailable" in board.note("trackball")


# -- Touch: construction -------------------------------------------------------


def test_the_gt911_answers_on_its_default_address(board):
    t = board.touch()
    assert t.available is True
    assert t.addr == 0x5D
    assert board.bus.probes == [0x5D]     # found first: no second probe


def test_a_strapped_gt911_is_found_on_the_second_address(board):
    """INT straps the address at reset, so 0x14 is a real board, not a fallback
    nobody hits -- both entries of gt911.ADDRS have to be walked."""
    board.bus.present = {0x14}
    t = board.touch()
    assert t.available is True
    assert t.addr == 0x14
    assert board.bus.probes == [0x5D, 0x14]


def test_no_gt911_on_the_bus_leaves_touch_unavailable(board):
    board.bus.present = set()
    t = board.touch()
    assert t.available is False and t.addr is None
    assert board.note("touch") == "GT911 not found on I2C0"
    assert t._int_pin is None              # and no INT claim was attempted
    assert 16 not in board.pins.pins


def test_the_int_pin_is_claimed_as_input_only_and_counts_both_edges(board):
    """Input-only with no pull because INT also straps the I2C address at
    reset; both edges because that makes the gate polarity-agnostic."""
    t = board.touch()
    Pin = board.machine.Pin
    p = board.pins.pins[16]
    assert t._int_pin is p
    assert p.mode == Pin.IN and p.pull is None
    assert p.trigger == (Pin.IRQ_RISING | Pin.IRQ_FALLING)
    p.pulse(3)
    assert t._int_count == [3]


@pytest.mark.parametrize("how", ["pin", "irq"])
def test_an_int_pin_that_cannot_be_claimed_degrades_to_every_pass_polling(
        board, how):
    """The gate is optional and touch is not: a miswired or unclaimable INT must
    fall back to today's blind polling, never to dead touch."""
    getattr(board.pins, "fail_on" if how == "pin" else "irq_fail_on").add(16)
    t = board.touch()
    assert t.available is True             # the whole point
    assert t._int_pin is None
    assert "INT pin unavailable" in board.note("touch")
    assert t.should_read() is True
    assert t.should_read() is True
    assert t.stat_skipped == 0


def test_INT_GATE_False_is_the_revert_to_blind_polling(board):
    """The A/B knob has to actually revert: no pin claimed, and every pass
    reads."""
    board.module.Touch.INT_GATE = False
    t = board.touch()
    assert t.available is True
    assert t._int_pin is None
    assert 16 not in board.pins.pins
    assert t.should_read() is True


def test_an_injected_i2c_is_used_as_is_and_no_bus_is_constructed(board):
    """The #69 poller and the calibration tools hand in the bus they already
    own; constructing a second I2C(0) over the keyboard's would be the bug."""
    other = Gt911Bus(board.clock)
    t = board.touch(i2c=other)
    assert t._i2c is other
    assert board.i2c_ctors == []
    assert other.probes == [0x5D]


def test_the_bus_is_built_on_the_keyboards_i2c0_pins_when_none_is_given(board):
    board.touch()
    args, kw = board.i2c_ctors[0]
    assert args[0] == 0
    assert kw["scl"].gpio == 8 and kw["sda"].gpio == 18
    assert kw["freq"] == 400000


def test_no_machine_module_at_all_leaves_touch_unavailable(board):
    """A desktop-MicroPython or unix build has no `machine`; the constructor
    must come back with available=False rather than raise into boot."""
    sys.modules["machine"] = None
    t = board.touch()
    assert t.available is False
    assert "unavailable" in board.note("touch")


def test_the_safety_heartbeat_clock_is_anchored_at_construction(board):
    """Not 0: a heartbeat anchored at the epoch fires on the very first pass of
    a board that has been up for seven seconds, which hides a dead INT line."""
    t = board.touch()
    assert t._last_read_ms == board.clock.ticks_ms() == 7000


def test_the_register_map_is_the_shared_gt911_cores(board):
    """Calibration tooling reads these by name off the class; they must stay
    aliases of the #202 Phase C core rather than second copies."""
    from device import gt911

    T = board.module.Touch
    assert (T.ADDRS, T.REG_STATUS, T.REG_POINT0) == (
        gt911.ADDRS, gt911.REG_STATUS, gt911.REG_POINT0)


def test_the_no_news_contract_is_the_shared_HeldPoint_with_this_boards_verdict(
        board):
    """Extrapolation was measured and DECLINED on this glass (2026-08-19), so
    the T-Deck must build its HeldPoint with the glide OFF while still passing
    the canvas extent through."""
    from device import gt911

    t = board.touch(w=320, h=240)
    assert isinstance(t._hp, gt911.HeldPoint)
    assert t._hp.extrapolate is False
    assert (t._hp._w, t._hp._h) == (320, 240)
    assert t.fresh is t._hp.fresh


# -- should_read: the #74 INT gate ---------------------------------------------


def _proven(board, t):
    """Walk the gate to its engaged state: one edge, consumed."""
    board.pins.pins[16].pulse()
    assert t.should_read() is True
    assert t._int_seen is True


def test_no_int_pin_means_every_pass_reads(board):
    board.pins.fail_on.add(16)
    t = board.touch()
    for _ in range(5):
        assert t.should_read() is True
    assert t.stat_skipped == 0


def test_the_gate_never_engages_until_the_pin_proves_itself_with_a_first_edge(
        board):
    """A pin that comes up but never pulses (miswired, wrong polarity, dead
    trace) must read like no pin at all -- for as long as it stays silent."""
    t = board.touch()
    for _ in range(5):
        assert t.should_read() is True
    assert t._int_seen is False
    assert t.stat_skipped == 0


def test_int_activity_since_the_last_check_opens_the_gate(board):
    t = board.touch()
    _proven(board, t)
    t._last_read_ms = board.clock.ticks_ms()
    assert t.should_read() is False
    board.pins.pins[16].pulse()
    assert t.should_read() is True


def test_a_quiet_proven_pin_skips_the_transaction(board):
    """The whole lever: no edge, no finger, no heartbeat due -> zero bus time."""
    t = board.touch()
    _proven(board, t)
    t._last_read_ms = board.clock.ticks_ms()
    assert t.should_read() is False
    assert t.should_read() is False
    assert t.stat_skipped == 2


def test_a_touch_in_progress_reads_at_full_rate(board):
    """A missed finger-up report would wedge the pointer down, so a touch in
    progress outranks the gate even with no edges at all."""
    t = board.touch()
    _proven(board, t)
    t._last_read_ms = board.clock.ticks_ms()
    t._touching = True
    for _ in range(4):
        assert t.should_read() is True
    assert t.stat_skipped == 0


@pytest.mark.parametrize("since_ms,expected", [
    (0, False),
    (249, False),
    (250, True),        # SAFETY_POLL_MS, inclusive
    (400, True),
])
def test_a_missed_INT_edge_still_reads_on_the_safety_heartbeat(
        board, since_ms, expected):
    """~4Hz of blind polling under the gate is the miswire/missed-edge net; the
    boundary is pinned both sides so a `>` for `>=` shows up."""
    t = board.touch()
    _proven(board, t)
    t._last_read_ms = board.clock.ticks_ms() - since_ms
    assert t.should_read() is expected


def test_stat_int_edges_tracks_the_raw_counter_even_on_a_skipped_pass(board):
    """`I2CSTAT`'s int= is the only evidence the line is alive; it must be
    published from the pass that decides, including the ones that skip."""
    t = board.touch()
    _proven(board, t)
    t._last_read_ms = board.clock.ticks_ms()
    assert t.should_read() is False
    assert t.stat_int_edges == 1
    board.pins.pins[16].pulse(4)
    t.should_read()
    assert t.stat_int_edges == 5


# -- read_raw ------------------------------------------------------------------


def test_an_unavailable_touch_spends_no_bus_time(board):
    board.bus.present = set()
    t = board.touch()
    board.bus.calls.clear()
    assert t.read_raw() is None
    assert board.bus.calls == []


def test_a_point_is_read_y_low_high_then_x_low_high(board):
    """The hardware fact this board was calibrated on. Distinct x and y so a
    swapped pair cannot pass by coincidence."""
    t = board.touch(reports=[DOWN])
    assert t.read_raw() == (100, 50)


def test_the_high_byte_of_each_coordinate_is_carried(board):
    """Both coordinates exceed a byte on this panel; a dropped `<< 8` reads as
    a pointer that wraps every 256px."""
    t = board.touch(reports=[(0x81, point(0x0123, 0x00EF))])
    assert t.read_raw() == (0x0123, 0x00EF)


def test_a_ready_report_with_no_points_is_a_confirmed_finger_up(board):
    t = board.touch(reports=[UP])
    assert t.read_raw() is False
    assert board.bus.names() == ["status", "clear"]     # no point read at all


def test_a_not_ready_buffer_is_not_cleared_and_says_nothing_about_the_finger(
        board):
    """Clearing an unfilled buffer throws away the sample the controller is
    still assembling; and a not-ready read is not evidence the finger lifted."""
    t = board.touch(reports=[NOT_READY])
    t._touching = True
    assert t.read_raw() is None
    assert board.bus.names() == ["status"]
    assert t._touching is True


def test_only_a_confirmed_sample_moves_the_gate_state(board):
    t = board.touch(reports=[DOWN, UP, NOT_READY])
    t.read_raw()
    assert t._touching is True
    t.read_raw()
    assert t._touching is False
    t._touching = True
    t.read_raw()
    assert t._touching is True


def test_a_status_read_that_throws_reports_no_news(board):
    """A NAK'd status read must not be mistaken for a finger up -- that is a
    phantom release mid-drag."""
    t = board.touch(reports=[DOWN])
    t._touching = True
    board.bus.fail.add("status")
    assert t.read_raw() is None
    assert t._touching is True
    assert t.stat_n == 1               # ...and the failed span is still counted


def test_a_failed_point_read_still_clears_the_status_register(board):
    """Otherwise the controller never produces another sample and touch is dead
    for the rest of the session."""
    t = board.touch(reports=[DOWN, DOWN])
    board.bus.fail.add("point")
    assert t.read_raw() is None
    assert board.bus.names() == ["status", "point", "clear"]
    board.bus.fail.clear()
    assert t.read_raw() == (100, 50)   # the queue really did advance


def test_a_failed_status_clear_does_not_lose_the_point(board):
    t = board.touch(reports=[DOWN])
    board.bus.fail.add("clear")
    assert t.read_raw() == (100, 50)


def test_the_safety_heartbeat_is_stamped_before_the_read_not_after(board):
    """Stamping after would fold the stall INTO the heartbeat, so a board whose
    reads take 45ms would poll blind at a rate its own stalls set."""
    t = board.touch(reports=[DOWN])
    board.bus.cost["status"] = 45_000
    at_entry = board.clock.ticks_ms()
    t.read_raw()
    assert t._last_read_ms == at_entry
    assert board.clock.ticks_ms() > at_entry


def test_every_gt911_register_is_addressed_16_bit_at_the_probed_address(board):
    """The GT911's register file is 16-bit addressed; an 8-bit access reads a
    different register entirely. And every transaction must use the address the
    probe actually answered on."""
    board.bus.present = {0x14}
    t = board.touch(reports=[DOWN])
    board.bus.calls.clear()
    t.read_raw()
    assert board.bus.names() == ["status", "point", "clear"]
    for _phase, addr, addrsize in board.bus.calls:
        assert addr == 0x14
        assert addrsize == 16


# -- _stat: the #69/#74 latency bookkeeping ------------------------------------


def test_every_read_is_counted_and_the_worst_span_kept(board):
    t = board.touch(reports=[DOWN, DOWN, DOWN])
    board.bus.cost["status"] = 1_000
    t.read_raw()
    board.bus.cost["status"] = 9_000
    t.read_raw()
    board.bus.cost["status"] = 2_000
    t.read_raw()
    assert t.stat_n == 3
    assert t.stat_max_us == 9_000      # a max, not the last sample


@pytest.mark.parametrize("us,over5,over20", [
    (4_999, 0, 0),
    (5_000, 1, 0),
    (19_999, 1, 0),
    (20_000, 1, 1),        # the buckets are NESTED: a 20ms stall is also a 5ms one
])
def test_the_stall_buckets_are_nested_at_5ms_and_20ms(board, us, over5, over20):
    t = board.touch(reports=[DOWN])
    board.bus.cost["status"] = us
    t.read_raw()
    assert (t.stat_over5, t.stat_over20) == (over5, over20)


def test_a_stall_short_of_the_catastrophic_threshold_captures_nothing(board):
    t = board.touch(reports=[DOWN])
    board.bus.cost["status"] = 199_999
    t.read_raw()
    assert t.stat_first_big is None


def test_the_first_catastrophic_stall_is_captured_once_with_its_context(board):
    """#74's fingerprint: WHEN (boot ms), WHICH transaction, the status byte,
    and how many reads preceded it -- which is what separates a boot-wake
    one-shot from a steady-state fault. Captured ONCE: a later, bigger stall
    must not overwrite the first."""
    t = board.touch(reports=[DOWN, DOWN, DOWN])
    t.read_raw()                                   # a healthy read first
    board.bus.cost["status"] = 200_000
    at = board.clock.ticks_ms()
    t.read_raw()
    assert t.stat_first_big == (at + 200, "status", 0x81, 2)

    board.bus.cost["status"] = 900_000
    t.read_raw()
    assert t.stat_first_big[0] == at + 200         # still the FIRST


@pytest.mark.parametrize("costs,phase", [
    ({"status": 300_000, "point": 1_000, "clear": 1_000}, "status"),
    ({"status": 1_000, "point": 300_000, "clear": 1_000}, "point"),
    ({"status": 1_000, "point": 1_000, "clear": 300_000}, "clear"),
])
def test_the_phase_blamed_is_the_transaction_that_ate_the_time(
        board, costs, phase):
    """The whole reason the three transactions are timed separately: the earlier
    sessions could only say a read stalled, never where inside it."""
    t = board.touch(reports=[DOWN])
    board.bus.cost.update(costs)
    t.read_raw()
    assert t.stat_first_big[1] == phase


def test_a_tie_between_the_phases_blames_the_earliest_one(board):
    """Which phase wins a tie is arbitrary; that it is REPRODUCIBLE is not.
    `stat_first_big` is a fingerprint compared across sessions, so first among
    equals has to stay first -- the cascade's two comparisons are both strict
    for that reason."""
    t = board.touch(reports=[DOWN])
    board.bus.cost.update({"status": 100_000, "point": 100_000,
                           "clear": 100_000})
    t.read_raw()
    assert t.stat_first_big[1] == "status"


def test_a_stall_inside_the_status_read_itself_records_no_status_byte(board):
    """`status=None` on the I2CSTAT line means the read never got far enough to
    have one -- a different fault from a stall after a good status."""
    t = board.touch(reports=[DOWN])
    board.bus.cost["status"] = 400_000
    board.bus.fail.add("status")
    t.read_raw()
    ms, phase, status, n = t.stat_first_big
    assert (phase, status, n) == ("status", None, 1)


def test_a_not_ready_read_is_still_measured(board):
    """The gated-idle passes are the ones #74 blamed, so their bus time has to
    reach the stats too."""
    t = board.touch(reports=[NOT_READY])
    board.bus.cost["status"] = 30_000
    t.read_raw()
    assert t.stat_n == 1 and t.stat_max_us == 30_000 and t.stat_over20 == 1


# -- _map ----------------------------------------------------------------------


def test_the_y_axis_is_flipped_and_x_is_not(board):
    """This board's GT911 already reports landscape coords; only Y runs
    opposite the screen (raw top=240)."""
    t = board.touch(w=320, h=240)
    assert t._map(0, 0) == (0, 239)
    assert t._map(319, 239) == (319, 0)
    assert t._map(100, 50) == (100, 189)


def test_the_raw_extent_is_scaled_into_canvas_space(board):
    t = board.touch(w=640, h=480)
    assert t._map(160, 120) == (320, 238)
    assert t._map(0, 0) == (0, 478)


def test_TOUCH_SWAP_exchanges_the_axes(board):
    t = board.touch(w=320, h=240)
    board.module.TOUCH_SWAP = True          # per-test: the module is fresh
    assert t._map(30, 200) == (200, 239 - 30)


def test_TOUCH_FLIP_X_mirrors_x(board):
    t = board.touch(w=320, h=240)
    board.module.TOUCH_FLIP_X = True
    assert t._map(0, 0) == (319, 239)
    assert t._map(319, 0) == (0, 239)


def test_TOUCH_FLIP_Y_off_leaves_y_alone(board):
    t = board.touch(w=320, h=240)
    board.module.TOUCH_FLIP_Y = False
    assert t._map(10, 20) == (10, 20)


def test_an_out_of_range_raw_point_is_clamped_onto_the_glass(board):
    """A GT911 that reports past its configured extent (it does, at the edges)
    must not index off the canvas."""
    t = board.touch(w=320, h=240)
    assert t._map(400, 0) == (319, 239)     # x over, y at the flipped top
    assert t._map(0, 500) == (0, 0)         # the flip drives y negative
    assert t._map(-5, 0) == (0, 239)


# -- debug_read (the calibration path) -----------------------------------------


def test_debug_read_is_silent_when_touch_is_unavailable(board):
    board.bus.present = set()
    t = board.touch()
    board.bus.calls.clear()
    assert t.debug_read() is None
    assert board.bus.calls == []


def test_debug_read_returns_all_eight_raw_bytes_and_clears(board):
    """Eight, not four: the point of the calibration dump is to SEE the layout,
    including the bytes read_raw ignores."""
    blob = bytes(range(8))
    t = board.touch(reports=[(0x81, blob)])
    assert t.debug_read() == (0x81, blob)
    assert board.bus.names()[-1] == "clear"


def test_debug_read_says_nothing_when_no_fresh_sample(board):
    t = board.touch(reports=[NOT_READY])
    assert t.debug_read() is None
    assert board.bus.names() == ["status"]      # and does NOT clear


def test_debug_read_reports_a_ready_report_with_no_points(board):
    """(status, None) rather than None: "the controller answered and there is no
    finger" is exactly what a calibration session needs to distinguish."""
    t = board.touch(reports=[UP])
    assert t.debug_read() == (0x80, None)


def test_debug_read_still_returns_the_status_when_the_point_read_fails(board):
    t = board.touch(reports=[DOWN])
    board.bus.fail.add("point")
    assert t.debug_read() == (0x81, None)


def test_a_failed_status_read_is_reported_as_no_sample(board):
    t = board.touch(reports=[DOWN])
    board.bus.fail.add("status")
    assert t.debug_read() is None


def test_a_failed_clear_does_not_lose_the_calibration_dump(board):
    t = board.touch(reports=[DOWN])
    board.bus.fail.add("clear")
    assert t.debug_read() == (0x81, point(100, 50))


# -- poll: the routing through gt911.HeldPoint ---------------------------------


def test_poll_maps_a_finger_down_into_canvas_space_with_a_press_edge(board):
    t = board.touch(w=320, h=240, reports=[DOWN, DOWN])
    assert t.poll() == (100, 189, True)     # y flipped by _map
    board.pins.pins[16].pulse()             # keep the gate open
    assert t.poll() == (100, 189, False)    # ...only the first is an edge
    assert t.fresh is True


def test_a_confirmed_finger_up_releases_the_point(board):
    t = board.touch(reports=[DOWN, UP])
    t.poll()
    board.pins.pins[16].pulse()
    assert t.poll() is None
    assert t.fresh is True


def test_no_news_holds_the_last_point_stale_marked(board):
    """The #74 contract: a phantom release mid-drag ends the gesture and can
    launch a fling by itself. `fresh=False` is what keeps the held repeat out of
    the kinetic velocity."""
    t = board.touch(reports=[DOWN, NOT_READY])
    assert t.poll() == (100, 189, True)
    board.pins.pins[16].pulse()
    assert t.poll() == (100, 189, False)
    assert t.fresh is False


def test_a_gated_pass_holds_the_point_without_touching_the_bus(board):
    """The gate's saving is only real if the skipped pass costs zero I2C, and
    it is only safe if the skip reads as no-news rather than a release."""
    t = board.touch(reports=[DOWN])
    board.pins.pins[16].pulse()             # prove the pin, so the gate engages
    t.poll()
    t._touching = False                     # pretend the finger-up landed
    t._last_read_ms = board.clock.ticks_ms()
    board.bus.calls.clear()
    assert t.poll() == (100, 189, False)
    assert board.bus.calls == []
    assert t.stat_skipped == 1


def test_the_held_point_is_bounded_so_a_missed_release_never_wedges_it(board):
    """gt911.HeldPoint.HOLD_SAMPLE_MS, reached through this driver's own poll:
    past the bound the pointer frees itself."""
    from device import gt911

    t = board.touch(reports=[DOWN])
    board.pins.pins[16].pulse()
    t.poll()
    t._touching = False
    t._last_read_ms = board.clock.ticks_ms()
    board.clock.advance_ms(gt911.HeldPoint.HOLD_SAMPLE_MS)
    assert t.poll() is None
    assert t.fresh is True


def test_the_poller_thread_source_replaces_the_inline_read(board):
    """#69: in threaded mode every I2C0 transaction belongs to the poller, so
    poll() must consume its staged sample and touch the bus zero times -- and
    the staged value is RAW, so it still goes through _map."""
    t = board.touch(reports=[DOWN])
    staged = [(100, 50), None, False]
    t._source = lambda: staged.pop(0)
    board.bus.calls.clear()

    assert t.poll() == (100, 189, True)
    assert t.poll() == (100, 189, False)
    assert t.poll() is None
    assert board.bus.calls == []


def test_a_source_pass_never_consults_the_gate(board):
    """The poller has already applied should_read on its own thread; applying it
    again on the frame loop would drop staged samples."""
    t = board.touch()
    _proven(board, t)
    t._last_read_ms = board.clock.ticks_ms()
    t._source = lambda: (100, 50)
    assert t.poll() == (100, 189, True)
    assert t.stat_skipped == 0
