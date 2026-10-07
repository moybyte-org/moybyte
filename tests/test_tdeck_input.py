"""The T-Deck's own input hardware, EXECUTED: the LILYGO keyboard's ASCII
and raw-matrix modes, the one-shot keys the ASCII path queues, the GT911's
INT gate and staging, and the mode switches between them.

The drivers are the kernel's C (native/moy_input/moy_kbd.c and
moy_touchdev.c), driven here through the host's binding (runtime/moy_input.py)
over recording I2C fakes; one `pass_()` is one pass of the input task, and the
table is the real one, so every `held()`/`last_key` read after its
`begin_frame` is what a cart would see. The board's WIRING of these drivers
is pinned in tests/test_board_routing.py.
"""

from pathlib import Path

from runtime import moy_input as mi

ROOT = Path(__file__).resolve().parent.parent


class _Clock:
    """A fake's clock: the drivers' ms and us read it, sleeps advance it."""

    def __init__(self):
        self.t = 1000

    def ms(self):
        return self.t

    def us(self):
        return self.t * 1000

    def sleep_ms(self, n):
        self.t += n


class _Kbd(_Clock):
    """The C3 at 0x55: a raw-matrix frame or an ASCII byte per read, writes
    recorded. `frames` is consumed one read at a time when it is a list."""

    def __init__(self, frames=None):
        super().__init__()
        self.frames = frames
        self.writes = []
        self.reads = []

    def readfrom(self, _addr, n):
        self.reads.append(n)
        f = self.frames
        if isinstance(f, list):
            r = f.pop(0) if f else bytes(n)
        else:
            r = f if f is not None else bytes(n)
        if isinstance(r, Exception):
            raise r
        return r

    def writeto(self, _addr, data):
        self.writes.append(bytes(data))


def _keyboard(frames=None, raw=False):
    """(table, driver, fake): the driver probed in ASCII (its first read), then
    put in raw mode when asked, as a game would."""
    fake = _Kbd([b"\x00"])
    table = mi.InputTable()
    drv = mi.KbdDriver(table, fake)
    fake.frames = frames
    fake.reads.clear()
    drv.raw_mode = raw
    return table, drv, fake


def _frame(drv, table):
    """One frame: the input task's pass, then the merge."""
    drv.pass_()
    table.begin_frame()


def test_capped_stall_holds_state_and_never_kills_the_keyboard():
    # #69: with the stretch cap a stall FAILS the read. That must cost ONE STALE
    # FRAME -- the last good matrix state is held (no buttons would fake a
    # release+re-press, and btnp() would double-fire) -- and must NOT disable the
    # keyboard. Only MOY_KBD_ERR_RUN consecutive failures end the session.
    held = bytes([0, 0x04, 0, 0, 0])                 # "right" held in the matrix
    table, drv, fake = _keyboard([held], raw=True)
    _frame(drv, table)
    assert table.held("right")
    fake.frames = [OSError(116)]                     # one capped stall...
    _frame(drv, table)
    assert table.held("right")                       # ...held state survives the gap
    assert drv.available and drv.raw_mode            # nothing was disabled
    assert drv.stat_timeouts >= 1                    # ...and it was counted
    fake.frames = held
    _frame(drv, table)
    assert table.held("right")                       # clean resume, no phantom edge
    assert drv.err_run == 0
    fake.frames = [OSError(116)] * mi.KBD_ERR_RUN
    for _ in range(mi.KBD_ERR_RUN):
        _frame(drv, table)
    assert not drv.available


def test_tdeck_keyboard_latches_event_keys_for_hold_window():
    table, drv, fake = _keyboard([b"d", b"\x00"])
    _frame(drv, table)
    assert table.held("right") and table.last_key == ord("d")
    _frame(drv, table)
    assert table.held("right")                       # inside the hold window
    fake.t += mi.KBD_HOLD_MS
    _frame(drv, table)
    assert not table.held("right")


def test_tdeck_keyboard_reads_raw_matrix_for_real_holds():
    frames = [bytes([0, 0x04, 0, 0, 0]), bytes([0, 0x04, 0, 0, 0]), bytes(5)]
    table, drv, fake = _keyboard(frames, raw=True)
    _frame(drv, table)
    assert table.held("right") and table.last_key == ord("d")
    _frame(drv, table)
    assert table.held("right")
    _frame(drv, table)
    assert not table.held("right") and table.last_key == 0


def test_tdeck_raw_backspace_is_the_one_console_key():
    # BACKSPACE (matrix [4][3] -> d4 bit 3) is the `home` button on the raw path
    # and reports last_key 0x08: a held home is what the Player's hold-BACKSPACE
    # exit watches. q, e and x are plain letters; K is B and L is A
    # (tests/test_tdeck_keymap.py pins the scheme).
    def poll_frame(frame):
        table, drv, _fake = _keyboard(frame, raw=True)
        _frame(drv, table)
        return table

    st = poll_frame(bytes([0, 0, 0, 0, 0x08]))
    assert st.held("home") and st.last_key == 0x08
    st = poll_frame(bytes([0x01, 0, 0, 0, 0]))       # q: a letter, not a button
    assert not st.any_held() and st.last_key == ord("q")
    st = poll_frame(bytes([0, 0x01, 0, 0, 0]))       # e: a letter, not a button
    assert not st.any_held() and st.last_key == ord("e")
    assert poll_frame(bytes([0, 0, 0, 0, 0x40])).held("b")      # k
    assert poll_frame(bytes([0, 0, 0, 0, 0x02])).held("a")      # l
    st = poll_frame(bytes([0, 0x10, 0, 0, 0]))       # x is a plain letter now
    assert not st.any_held() and st.last_key == ord("x")
    assert not poll_frame(bytes([0, 0, 0, 0, 0x08])).held("b")


def test_ascii_bytes_deliver_one_frame_each():
    # ASCII key bytes are one-shot EVENTS (the C3 reports each press once): the
    # passes queue them and each frame's merge delivers one, with a zero frame
    # between identical bytes so keyp()'s edge detector fires for both presses.
    # The task reads faster than the loop merges; nothing is lost or doubled.
    table, drv, fake = _keyboard([b"a", b"a", b"\x00"])
    drv.pass_()
    drv.pass_()
    drv.pass_()                                      # two rapid 'a' presses queued
    table.begin_frame()
    assert table.last_key == ord("a") and table.held("left")
    table.begin_frame()
    assert table.last_key == 0                       # the forced gap
    table.begin_frame()
    assert table.last_key == ord("a")                # the second press, not dropped
    table.begin_frame()
    assert table.last_key == 0                       # drained


def test_a_text_surface_gets_the_key_and_no_button():
    table, drv, fake = _keyboard([b"d"])
    table.text_mode = True
    _frame(drv, table)
    assert table.last_key == ord("d") and not table.any_held()


def test_tdeck_keyboard_keeps_raw_mode_for_physical_a_bit():
    table, drv, _fake = _keyboard(bytes([0x08, 0, 0, 0, 0]), raw=True)
    _frame(drv, table)
    assert table.held("left") and table.last_key == ord("a")
    assert drv.raw_mode


def test_tdeck_keyboard_falls_back_when_raw_mode_is_ignored():
    table, drv, _fake = _keyboard(bytes([ord("d"), 0, 0, 0, 0]), raw=True)
    _frame(drv, table)
    assert table.held("right") and table.last_key == ord("d")
    assert not drv.raw_mode and drv.raw_unsupported


def test_set_game_mode_is_queued_and_applied_by_the_pass():
    # The input task owns the bus: set_game_mode writes nothing itself; the
    # next pass applies it, only on a real transition.
    table, drv, fake = _keyboard()
    drv.set_game_mode(True)
    assert fake.writes == [] and not drv.raw_mode
    drv.pass_()
    assert fake.writes == [b"\x03"] and drv.raw_mode
    drv.set_game_mode(True)
    drv.pass_()
    assert fake.writes == [b"\x03"]                  # idempotent
    drv.set_game_mode(False)
    drv.pass_()
    assert fake.writes == [b"\x03", b"\x04"] and not drv.raw_mode
    drv.raw_unsupported = True                       # firmware ignored 0x03 once
    drv.set_game_mode(True)
    drv.pass_()
    assert fake.writes == [b"\x03", b"\x04"] and not drv.raw_mode


def test_the_raw_to_ascii_revert_drains_the_byte_it_produces():
    """The mode switch swallows what the C3 was holding.

    Reverting to ASCII happens because a TEXT surface just took the keyboard,
    and the byte the C3 hands over at that moment was typed while the matrix
    was streaming. Delivered, it is a letter the kid never typed appearing in
    the code buffer. The drain stops at the first quiet read."""
    table, drv, fake = _keyboard(raw=True)
    drv.held = 1                                     # a matrix-era latch
    drv.held_until = fake.t + 10_000
    fake.frames = [b"m", b"\x00", b"\x00"]
    drv.set_game_mode(False)
    _frame(drv, table)
    assert fake.writes == [b"\x04"], "the 0x04 revert must be the first thing sent"
    assert fake.reads == [1, 1, 1], fake.reads       # m, the quiet read, the pass's own
    assert table.last_key == 0, "the drained 'm' reached the text surface"
    assert not table.any_held() and drv.held_until == 0


def test_the_ascii_revert_drain_is_bounded():
    table, drv, fake = _keyboard(b"x", raw=True)
    drv.set_game_mode(False)
    drv.pass_()
    assert len(fake.reads) == 4 + 1                  # MOY_KBD_DRAIN, then the pass's read


def test_the_keyboard_boots_in_ascii_and_the_bus_caps_a_stretch():
    """#69: the board's bus caps a clock-stretch at 5 ms (a stall is one stale
    frame, not a felt 60 ms freeze), and the keyboard comes up in ASCII: init
    sends no raw-mode command; raw is a per-cart switch."""
    fake = _Kbd([b"\x00"])
    drv = mi.KbdDriver(mi.InputTable(), fake)
    assert drv.available and not drv.raw_mode and fake.writes == []
    header = (ROOT / "firmware" / "lilygo_t_deck_plus_mainline" / "boards"
              / "MOYBYTE_TDECK" / "mpconfigboard.h").read_text()
    assert "#define MOY_BUS_I2C_STRETCH_US              (5000)" in header


# -- the GT911 on the input task ---------------------------------------------------

class _GT911(_Clock):
    """A GT911 at 0x5D: `frames` is (status, point bytes); the status clear
    consumes a frame."""

    def __init__(self, frames):
        super().__init__()
        self.frames = list(frames)
        self.reads = 0

    def readfrom_mem(self, _a, reg, n, addrsize=16):
        self.reads += 1
        st, pt = self.frames[0] if self.frames else (0, b"")
        return bytes([st]) if reg == 0x814E else pt[:n]

    def writeto(self, _a, data):
        if data[:2] == b"\x81\x4e" and self.frames:
            self.frames.pop(0)


def _touch(frames, gate=False):
    fake = _GT911(frames)
    t = mi.TouchDriver(fake, mi.GT911, 320, 240, addr=0x5D, yx=True, flip_y=True,
                       raw_w=320, raw_h=240)
    t.probe()
    t.gate = gate
    return t, fake


def test_touch_int_gate_semantics():
    # #74: reads are skipped ONLY once the pin has proven itself with a first
    # edge AND nothing is pending -- INT activity, a touch in progress and the
    # safety heartbeat all still read; no gate = every pass reads.
    t, fake = _touch([(0x00, b"")])
    t.pass_()
    assert fake.reads == 2                            # probe + this pass: no gate
    t.gate = True
    t.pass_()
    assert fake.reads == 3 and t.stat_skipped == 0    # gate not engaged yet
    t.int_count += 1                                  # a first edge
    t.pass_()
    assert fake.reads == 4 and t.int_seen
    t.pass_()
    assert fake.reads == 4 and t.stat_skipped == 1    # quiet: skipped
    t.int_count += 1
    t.pass_()
    assert fake.reads == 5
    t.touching = True
    t.pass_()
    assert fake.reads == 6                            # full rate while touching
    t.touching = False
    fake.t += 250
    t.pass_()
    assert fake.reads == 7                            # the safety heartbeat


def test_touch_reads_track_the_gate_and_stage_one_sample_a_frame():
    # A point sets `touching`, a ready no-point sample clears it, a not-ready
    # read says nothing. A whole tap between two frames lands as the press,
    # then the release the frame after.
    t, fake = _touch([(0x81, bytes([50, 0, 100, 0])),   # y(lo,hi) then x(lo,hi)
                      (0x80, b""),                       # ready, no point: up
                      (0x00, b"")])                      # not ready
    t.pass_()
    assert t.touching
    t.pass_()
    assert not t.touching
    assert t.poll() == (100, 239 - 50, True)             # frame 1: the press
    assert t.poll() is None and t.fresh                  # frame 2: the release
    t.touching = True
    t.pass_()                                            # not ready
    assert t.touching
    assert t.poll() is None


class _TimedGT911(_GT911):
    """A GT911 whose transactions cost `costs` microseconds each, in order:
    the status read, the point read, the clear."""

    def __init__(self, frames, costs=(0, 0, 0)):
        super().__init__(frames)
        self.costs = list(costs)
        self.now_us = 0

    def us(self):
        return self.now_us

    def _spend(self):
        if self.costs:
            self.now_us += self.costs.pop(0)

    def readfrom_mem(self, a, reg, n, addrsize=16):
        self._spend()
        return super().readfrom_mem(a, reg, n, addrsize)

    def writeto(self, a, data):
        self._spend()
        super().writeto(a, data)


def _timed(costs, frames=((0x81, bytes([1, 0, 2, 0])),)):
    fake = _TimedGT911(frames, (0,) + tuple(costs))      # the probe's read is free
    t = mi.TouchDriver(fake, mi.GT911, 320, 240, addr=0x5D, yx=True)
    t.probe()
    return t, fake


def test_the_phase_blamed_is_the_transaction_that_ate_the_time():
    for costs, phase in (((300000, 10, 10), 0), ((10, 300000, 10), 1),
                         ((10, 10, 300000), 2)):
        t, _ = _timed(costs)
        t.pass_()
        assert t.fb_set and t.fb_phase == phase and t.fb_status == 0x81, (costs, phase)


def test_the_first_catastrophic_stall_is_captured_once():
    t, fake = _timed((250000, 0, 0, 400000, 0, 0),
                     ((0x81, bytes(4)), (0x81, bytes(4))))
    t.pass_()
    first = (t.fb_phase, t.fb_n)
    t.pass_()
    assert (t.fb_phase, t.fb_n) == first and t.stat_n == 2


def test_the_stall_buckets_are_nested_at_5ms_and_20ms():
    for us, o5, o20 in ((4999, 0, 0), (5000, 1, 0), (19999, 1, 0), (20000, 1, 1)):
        t, _ = _timed((us, 0, 0))
        t.pass_()
        assert (t.stat_over5, t.stat_over20) == (o5, o20), us
        assert t.stat_max_us == us and not t.fb_set


def test_a_strapped_gt911_is_found_on_the_second_address():
    class Strapped(_GT911):
        def readfrom_mem(self, a, reg, n, addrsize=16):
            if a != 0x14:
                raise OSError(19)
            return super().readfrom_mem(a, reg, n, addrsize)

    t = mi.TouchDriver(Strapped([(0, b"")]), mi.GT911, 320, 240)
    t.probe()
    assert t.available and t.addr == 0x14


def test_an_unavailable_touch_spends_no_bus_time():
    class Absent(_GT911):
        def readfrom_mem(self, *_a, **_k):
            self.reads += 1
            raise OSError(19)

    fake = Absent([])
    t = mi.TouchDriver(fake, mi.GT911, 320, 240)
    t.probe()
    n = fake.reads
    t.pass_()
    assert not t.available and fake.reads == n and t.poll() is None
