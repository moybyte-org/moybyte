"""The T-Deck's own input hardware, EXECUTED: the LILYGO keyboard's ASCII
and raw-matrix modes, the #69 input-poller thread's staging, the GT911 INT
gate, and the mode switches between them.

`device/moybyte/input.py` and `device/device_input.py` are loaded by path
under CPython and driven with recording I2C fakes; the console's InputState
is the real one, so every `held()`/`last_key` read is what a cart would see.
The board's WIRING of these drivers (which constructor run_desktop calls,
which fallback rung stays live) is pinned in tests/test_board_routing.py.
"""

import importlib.util
import sys
import types
from pathlib import Path

DEVICE = Path("device")


# -- the console's input order, for the driver tests below -------------------
#
# `InputState._held` is the union of the sources and `begin_frame` is its one
# author, so polling a driver and reading `state.held(...)` without merging
# reads the PREVIOUS frame. These helpers are the loop's order: poll, then
# merge.

def _kbd_frame(keyboard, state):
    keyboard.poll()
    state.begin_frame()


def _poller_frame(poller, state):
    poller.consume()
    state.begin_frame()


def test_capped_stall_holds_state_and_never_kills_the_keyboard():
    # #69: with the timeout cap a stall RAISES. That must cost ONE STALE FRAME --
    # the last good matrix state is held (returning "no buttons" would fake a
    # release+re-press, and btnp() would double-fire) -- and must NOT disable the
    # keyboard (the old any-exception -> available=False would have killed it
    # within a minute at the measured stall rate). Only ERR_RUN_LIMIT consecutive
    # failures (a genuinely absent keyboard) end the session.
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = module.InputState()
    keyboard = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    keyboard.input = state
    keyboard.available = True
    keyboard.raw_mode = True

    held_frame = bytes([0, 0x04, 0, 0, 0])          # "right" held in the matrix

    class FlakyI2C:
        def __init__(self):
            self.fail = False
        def readfrom(self, _addr, _size):
            if self.fail:
                raise OSError(116)                   # ETIMEDOUT (the capped stall)
            return held_frame

    i2c = FlakyI2C()
    keyboard._i2c = i2c
    _kbd_frame(keyboard, state)
    assert state.held("right")                       # baseline: the key is down
    i2c.fail = True                                  # one capped stall...
    _kbd_frame(keyboard, state)
    assert state.held("right")                       # ...held state survives the gap
    assert keyboard.available and keyboard.raw_mode  # nothing was disabled
    assert keyboard.stat_timeouts >= 1               # ...and it was counted
    i2c.fail = False
    _kbd_frame(keyboard, state)
    assert state.held("right")                       # clean resume, no phantom edge
    assert keyboard._err_run == 0                    # the run counter reset
    # A genuinely dead keyboard still disables after a solid failure run.
    i2c.fail = True
    for _ in range(module.TDeckKeyboard.ERR_RUN_LIMIT):
        _kbd_frame(keyboard, state)
    assert not keyboard.available


def test_tdeck_keyboard_latches_event_keys_for_hold_window():
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    state = module.InputState()
    keyboard = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    keyboard.input = state
    keyboard.available = True
    keyboard.raw_mode = False
    keyboard._i2c = object()
    keyboard._held_buttons = ()
    keyboard._held_until_ms = 0
    keys = [ord("d"), 0]
    keyboard._read_key = lambda: keys.pop(0)

    _kbd_frame(keyboard, state)
    assert state.held("right")

    _kbd_frame(keyboard, state)
    assert state.held("right")

    keyboard._held_until_ms = module._ticks_ms() - 1
    keyboard._read_key = lambda: 0
    _kbd_frame(keyboard, state)
    assert not state.held("right")


def test_tdeck_keyboard_reads_raw_matrix_for_real_holds():
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    state = module.InputState()
    keyboard = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    keyboard.input = state
    keyboard.available = True
    keyboard.raw_mode = True
    raw_frames = [bytes([0, 0x04, 0, 0, 0]), bytes([0, 0x04, 0, 0, 0]), bytes(5)]

    class FakeI2C:
        def readfrom(self, _addr, _size):
            return raw_frames.pop(0)

    keyboard._i2c = FakeI2C()

    _kbd_frame(keyboard, state)
    assert state.held("right")
    assert state.last_key == ord("d")

    _kbd_frame(keyboard, state)
    assert state.held("right")

    _kbd_frame(keyboard, state)
    assert not state.held("right")
    assert state.last_key == 0


def test_tdeck_raw_backspace_is_the_one_console_key():
    # THE ONE CONSOLE KEY: BACKSPACE (matrix [4][3] -> d4 bit 3) maps to the "home"
    # button on the raw path (AND reports last_key 0x08). Stage 5 makes this the EXIT
    # key: a held/streamed "home" is exactly what the Player's hold-BACKSPACE exit
    # gesture watches (raw mode streams the held key each frame, so this held
    # frame -> st.held("home") is the device wiring the exit relies on). q and e are
    # PLAIN LETTERS now (last_key only, no chrome role), and so is x: the action
    # buttons moved off Z/X onto K/L on 2026-08-14 (owner call -- Z/X are bottom
    # row, under the same thumb as WASD), so B is the K key. The rest of that
    # scheme is pinned in tests/test_tdeck_keymap.py, which also checks every raw
    # bit against the vendor firmware's own matrix table.
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def poll_frame(frame):
        state = module.InputState()
        keyboard = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
        keyboard.input = state
        keyboard.available = True
        keyboard.raw_mode = True
        keyboard._i2c = type("F", (), {"readfrom": lambda s, a, n: frame})()
        _kbd_frame(keyboard, state)
        return state

    st = poll_frame(bytes([0, 0, 0, 0, 0x08]))    # backspace held
    assert st.held("home")
    assert st.last_key == 0x08

    st = poll_frame(bytes([0x01, 0, 0, 0, 0]))    # q held: a letter, not a button
    assert not st._held
    assert st.last_key == ord("q")

    st = poll_frame(bytes([0, 0x01, 0, 0, 0]))    # e held: a letter, not a button
    assert not st._held
    assert st.last_key == ord("e")

    st = poll_frame(bytes([0, 0, 0, 0, 0x40]))    # k held: THE b button
    assert st.held("b")
    st = poll_frame(bytes([0, 0, 0, 0, 0x02]))    # l held: THE a button
    assert st.held("a")
    st = poll_frame(bytes([0, 0x10, 0, 0, 0]))    # x is a plain letter now
    assert not st._held
    assert st.last_key == ord("x")
    st = poll_frame(bytes([0, 0, 0, 0, 0x08]))    # backspace is NOT b anymore
    assert not st.held("b")


def _load_fw_input():
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bare_kbd(module, state, raw):
    kbd = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    kbd.input = state
    kbd.available = True
    kbd.raw_mode = raw
    kbd._held_buttons = ()
    kbd._held_until_ms = 0
    return kbd


def test_input_poller_ascii_bytes_deliver_one_frame_each():
    # #69 poller thread, ASCII staging: key bytes are one-shot EVENTS (the C3
    # reports each press once), so the poller queues them and consume() delivers
    # each for exactly one main frame -- with a forced 0-frame between identical
    # bytes so keyp()'s edge detector fires for both presses. The poller reads
    # faster than the frame loop consumes; nothing may be lost or doubled.
    module = _load_fw_input()
    state = module.InputState()
    kbd = _bare_kbd(module, state, raw=False)
    seq = [b"a", b"a", b"\x00"]

    class FakeI2C:
        def readfrom(self, _a, _n):
            return seq.pop(0) if seq else b"\x00"

    kbd._i2c = FakeI2C()
    p = module.InputPoller(kbd, None)
    p._poll_once()
    p._poll_once()
    p._poll_once()                          # two rapid 'a' presses now queued
    _poller_frame(p, state)
    assert state.last_key == ord("a")       # frame 1: first press
    assert state.held("left")               # ...with its latched button alias
    _poller_frame(p, state)
    assert state.last_key == 0              # frame 2: forced release gap
    _poller_frame(p, state)
    assert state.last_key == ord("a")       # frame 3: second press, not dropped
    _poller_frame(p, state)
    assert state.last_key == 0              # queue drained


def test_input_poller_raw_holds_state_across_a_stall():
    # #69 poller thread, raw staging: buttons are LEVEL state (latest snapshot
    # wins) and a capped I2C stall keeps the last good matrix -- the same
    # hold-not-release contract the synchronous path has (no phantom edges).
    module = _load_fw_input()
    state = module.InputState()
    kbd = _bare_kbd(module, state, raw=True)
    seq = [bytes([0x08, 0, 0, 0, 0]), OSError(110), bytes(5)]

    class FlakyI2C:
        def readfrom(self, _a, _n):
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

    kbd._i2c = FlakyI2C()
    p = module.InputPoller(kbd, None)
    p._poll_once()
    _poller_frame(p, state)
    assert state.held("left") and state.last_key == ord("a")
    p._poll_once()                          # capped stall -> hold, don't release
    _poller_frame(p, state)
    assert state.held("left")
    p._poll_once()                          # clean empty matrix -> real release
    _poller_frame(p, state)
    assert not state.held("left")


def test_input_poller_touch_subframe_tap_becomes_two_frames():
    # #69 poller thread, GT911 staging: the poller may see a whole tap (down +
    # up) between two main frames; consume_touch must deliver the point first
    # and the finger-up the NEXT frame so the tap edge is never swallowed.
    module = _load_fw_input()

    class FakeTouch:
        available = True

        def __init__(self):
            self.seq = [(100, 50), False]

        def read_raw(self):
            return self.seq.pop(0) if self.seq else None

    p = module.InputPoller(None, FakeTouch())
    p._poll_once()
    p._poll_once()                          # down + up both before one consume
    assert p.consume_touch() == (100, 50)   # frame 1: the press lands
    assert p.consume_touch() is False       # frame 2: the release
    assert p.consume_touch() is None        # steady state after


def test_input_poller_defers_mode_switch_to_the_bus_thread():
    # #69: with the poller owning the bus, set_game_mode from the main thread
    # must NOT write I2C (a write could collide with a poller read mid-stall) --
    # it queues the target and the poller applies it between reads.
    module = _load_fw_input()
    state = module.InputState()
    kbd = _bare_kbd(module, state, raw=False)
    kbd._raw_unsupported = False
    kbd._poller_owned = True
    writes = []

    class FakeI2C:
        def readfrom(self, _a, n):
            return bytes(n)

        def writeto(self, _a, buf):
            writes.append(bytes(buf))

    kbd._i2c = FakeI2C()
    kbd.set_game_mode(True)                 # main thread: queued only
    assert writes == [] and kbd.raw_mode is False
    p = module.InputPoller(kbd, None)
    p._poll_once()                          # poller applies it between reads
    assert writes[0] == b"\x03" and kbd.raw_mode is True


def _load_fw_device_input():
    # device_input imports the leaf device_util at module top; stage it into
    # sys.modules first (the test_device_canvas_parity loader pattern).
    du = importlib.util.spec_from_file_location(
        "device_util", DEVICE / "device_util.py"
    )
    dumod = importlib.util.module_from_spec(du)
    du.loader.exec_module(dumod)
    sys.modules["device_util"] = dumod
    spec = importlib.util.spec_from_file_location(
        "moybyte_device_input", DEVICE / "device_input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bare_touch(module):
    t = module.Touch.__new__(module.Touch)
    t.available = True
    t.addr = 0x5D
    t._i2c = None
    t._down = False
    t._source = None
    t.stat_n = 0
    t.stat_max_us = 0
    t.stat_over5 = 0
    t.stat_over20 = 0
    t.stat_first_big = None
    t.stat_int_edges = 0
    t.stat_skipped = 0
    t._int_pin = None
    t._int_count = [0]
    t._int_last = 0
    t._int_seen = False
    t._touching = False
    t._last_read_ms = module._ticks_ms()
    return t


def test_touch_int_gate_semantics():
    # #74 INT gate: reads are skipped ONLY once the pin has proven itself with a
    # first edge AND nothing is pending -- INT activity, a touch in progress and
    # the safety heartbeat all still read; no pin at all = today's blind polling.
    module = _load_fw_device_input()
    t = _bare_touch(module)
    assert t.should_read()                    # no INT pin -> always read

    t._int_pin = object()                     # pin came up, but no edge ever
    assert t.should_read()                    # gate not engaged -> still reads
    assert t.stat_skipped == 0

    t._int_count[0] += 1                      # first edge: data ready
    assert t.should_read()                    # ... consumed, gate now engaged
    assert t._int_seen
    t._last_read_ms = module._ticks_ms()      # a recent read
    assert not t.should_read()                # idle + no edge -> skipped
    assert t.stat_skipped == 1

    t._int_count[0] += 1                      # tap: an edge arrives
    assert t.should_read()                    # -> read this pass
    t._touching = True                        # finger down (read_raw saw a point)
    assert t.should_read()                    # full rate while touching, no edge needed
    t._touching = False
    t._last_read_ms = module._ticks_ms() - 1000
    assert t.should_read()                    # safety heartbeat past SAFETY_POLL_MS


def test_touch_read_raw_tracks_gate_state():
    # read_raw feeds the gate: a point sets _touching (full rate until the
    # release report lands), a fresh no-point sample clears it, and a not-ready
    # read changes nothing (it says nothing about the finger).
    module = _load_fw_device_input()
    t = _bare_touch(module)

    class FakeGT911:
        def __init__(self):
            self.frames = [(0x81, bytes([50, 0, 100, 0])),   # ready, 1 point
                           (0x80, b""),                       # ready, 0 points: up
                           (0x00, b"")]                       # not ready

        def readfrom_mem(self, _a, reg, _n, addrsize=16):
            st, pt = self.frames[0]
            return bytes([st]) if reg == module.Touch.REG_STATUS else pt

        def writeto_mem(self, _a, _reg, _buf, addrsize=16):
            self.frames.pop(0)                # the status clear consumes a frame

    t._i2c = FakeGT911()
    assert t.read_raw() == (100, 50)          # y(lo,hi) then x(lo,hi) layout
    assert t._touching
    assert t.read_raw() is False
    assert not t._touching
    t._touching = True                        # pretend mid-touch...
    assert t.read_raw() is None               # not-ready says nothing
    assert t._touching                        # ...state untouched


def test_input_poller_touch_respects_int_gate():
    # #69/#74: the poller consults Touch.should_read() before spending a GT911
    # transaction; a gated pass does zero touch I2C, and a fake without the
    # method (the older Touch shape) keeps the every-pass behaviour.
    module = _load_fw_input()

    class GatedTouch:
        available = True

        def __init__(self):
            self.reads = 0
            self.gate = [False, True]

        def should_read(self):
            return self.gate.pop(0) if self.gate else False

        def read_raw(self):
            self.reads += 1
            return (10, 20)

    t = GatedTouch()
    p = module.InputPoller(None, t)
    p._poll_once()                            # gated -> no I2C
    assert t.reads == 0 and p.consume_touch() is None
    p._poll_once()                            # gate opens -> one read
    assert t.reads == 1 and p.consume_touch() == (10, 20)


def test_tdeck_keyboard_keeps_raw_mode_for_physical_a_bit():
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    state = module.InputState()
    keyboard = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    keyboard.input = state
    keyboard.available = True
    keyboard.raw_mode = True

    class FakeI2C:
        def readfrom(self, _addr, _size):
            return bytes([0x08, 0, 0, 0, 0])

    keyboard._i2c = FakeI2C()

    _kbd_frame(keyboard, state)
    assert state.held("left")
    assert state.last_key == ord("a")
    assert keyboard.raw_mode


def test_tdeck_keyboard_falls_back_when_raw_mode_is_ignored():
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    state = module.InputState()
    keyboard = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    keyboard.input = state
    keyboard.available = True
    keyboard.raw_mode = True
    keyboard._held_buttons = ()
    keyboard._held_until_ms = 0

    class FakeI2C:
        def readfrom(self, _addr, _size):
            return bytes([ord("d"), 0, 0, 0, 0])

    keyboard._i2c = FakeI2C()

    _kbd_frame(keyboard, state)
    assert state.held("right")
    assert state.last_key == ord("d")
    assert not keyboard.raw_mode


def test_tdeck_keyboard_set_game_mode_toggles_raw():
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    writes = []

    class FakeI2C:
        def writeto(self, _addr, data):
            writes.append(bytes(data))

    kb = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    kb.input = module.InputState()
    kb.available = True
    kb.raw_mode = False
    kb._raw_unsupported = False
    kb._i2c = FakeI2C()
    kb._held_buttons = ()
    kb._held_until_ms = 0
    RAW = module.TDeckKeyboard.RAW_MODE_CMD
    KEY = module.TDeckKeyboard.KEY_MODE_CMD

    # Entering a cart -> raw matrix (0x03) for true hold-to-move.
    kb.set_game_mode(True)
    assert kb.raw_mode and writes == [RAW]

    # Idempotent: no extra I2C traffic while already in the wanted mode.
    kb.set_game_mode(True)
    assert writes == [RAW]

    # Opening the code editor -> back to 1-byte ASCII (0x04) so typing is clean.
    kb.set_game_mode(False)
    assert not kb.raw_mode and writes == [RAW, KEY]

    # A board whose keyboard firmware ignored 0x03 sticks on ASCII: no more retries.
    kb._raw_unsupported = True
    kb.set_game_mode(True)
    assert not kb.raw_mode and writes == [RAW, KEY]


def test_the_raw_to_ascii_revert_drains_the_byte_it_produces():
    """The keyboard mode switch swallows what the C3 was holding.

    Reverting to ASCII happens because a TEXT surface just took the keyboard
    (the Code tab, a password field), and the byte the C3 hands over at that
    moment was typed while the matrix was streaming -- before the surface
    existed. Delivered, it is a letter the kid never typed appearing in the
    code buffer, which is what the owner saw on entering the Code tab.

    The matrix decodes only sixteen keys, so this byte is not always the one
    the console already holds -- ws._set_text_mode's seed covers that one and
    cannot cover this. Both halves, or the letter still lands.
    """
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    reads = []
    pending = [ord("m"), 0, 0, 0, 0]

    class FakeI2C:
        def __init__(self):
            self.writes = []

        def writeto(self, _addr, data):
            self.writes.append(bytes(data))

        def readfrom(self, _addr, size):
            reads.append(size)
            return bytes([pending.pop(0) if pending else 0])

    kbd = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    kbd.input = module.InputState()
    kbd.available = True
    kbd.raw_mode = True
    kbd._i2c = FakeI2C()
    kbd._held_buttons = ("left",)
    kbd._held_until_ms = module._ticks_ms() + 10_000
    kbd._err_run = 0
    kbd.src = None
    kbd._poller_owned = False
    kbd._want_game = None

    kbd.set_game_mode(False)

    assert kbd._i2c.writes == [module.TDeckKeyboard.KEY_MODE_CMD], \
        "the 0x04 revert must still be the first thing sent"
    assert reads, "the revert read nothing -- a queued byte would survive it"
    assert pending[0] == 0, "the queued 'm' was left for the text surface"
    # The latch goes with it: a held-key window opened by the matrix must not
    # keep firing a button into the surface that just took the keyboard.
    assert kbd._held_buttons == ()
    assert kbd._held_until_ms == 0

    # ...and it STOPS at the first quiet read rather than draining the cap.
    assert len(reads) == 2, reads


def test_the_ascii_revert_drain_is_bounded():
    """A keyboard that answers with a byte forever must not hold the bus."""
    spec = importlib.util.spec_from_file_location(
        "moybyte_firmware_input", DEVICE / "moybyte" / "input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    n = [0]

    class ChattyI2C:
        def writeto(self, _addr, _data):
            pass

        def readfrom(self, _addr, _size):
            n[0] += 1
            return b"x"

    kbd = module.TDeckKeyboard.__new__(module.TDeckKeyboard)
    kbd.input = module.InputState()
    kbd.available = True
    kbd.raw_mode = True
    kbd._i2c = ChattyI2C()
    kbd._held_buttons = ()
    kbd._held_until_ms = 0
    kbd._err_run = 0
    kbd.src = None
    kbd._poller_owned = False
    kbd._want_game = None

    kbd.set_game_mode(False)
    assert n[0] == module.TDeckKeyboard.DRAIN_READS


def test_i2c_timeout_knob_engaged():
    """#69: the keyboard opens its bus with the 5ms clock-stretch cap, so a
    stall is a <=5ms failed read (one stale frame) rather than a felt 60ms
    freeze -- and it comes up in ASCII: __init__ sends no raw-mode command,
    raw is entered later by set_game_mode once a cart runs."""
    module = _load_fw_input()
    opened = []
    writes = []

    class _I2C:
        def __init__(self, *_a, **kw):
            opened.append(kw)

        def readfrom(self, _addr, n):
            return bytes(n)

        def writeto(self, _addr, data):
            writes.append(bytes(data))

    class _Pin:
        def __init__(self, *_a, **_kw):
            pass

    machine = types.ModuleType("machine")
    machine.I2C = _I2C
    machine.Pin = _Pin
    saved = sys.modules.get("machine")
    sys.modules["machine"] = machine
    try:
        kbd = module.TDeckKeyboard(module.InputState())
    finally:
        if saved is None:
            sys.modules.pop("machine", None)
        else:
            sys.modules["machine"] = saved
    assert kbd.available
    assert module.TDeckKeyboard.I2C_TIMEOUT_US == 5000
    assert opened and opened[-1]["timeout"] == 5000
    assert module.TDeckKeyboard.RAW_MODE_CMD not in writes, \
        "the keyboard must boot in ASCII; raw is a per-cart switch"
    assert not kbd.raw_mode
