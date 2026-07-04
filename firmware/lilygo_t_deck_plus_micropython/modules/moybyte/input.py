import time


BUTTONS = (
    "up",
    "down",
    "left",
    "right",
    "a",
    "b",
    "x",
    "y",
    "run",
    "stop",
    "home",
    "save",
    "share",
    "select",
    "start",
)


class InputState:
    def __init__(self):
        self._held = set()
        self._last = set()
        self._pressed = set()
        self._released = set()
        self.last_key = 0

    def begin_frame(self):
        self._pressed = self._held - self._last
        self._released = self._last - self._held
        self._last = set(self._held)

    def release_all(self):
        self._held.clear()

    def set_button(self, name, held):
        if name not in BUTTONS:
            raise ValueError("unknown button: " + name)
        if held:
            self._held.add(name)
        else:
            self._held.discard(name)

    def held(self, name):
        return name in self._held

    def pressed(self, name):
        return name in self._pressed

    def released(self, name):
        return name in self._released


class TDeckKeyboard:
    KEYBOARD_ADDR = 0x55
    KEY_HOLD_MS = 260
    RAW_MODE_CMD = b"\x03"     # LILYGO_KB_MODE_RAW_CMD: stream the raw key matrix
    KEY_MODE_CMD = b"\x04"     # LILYGO_KB_MODE_KEY_CMD: back to 1-byte ASCII
    # Use the raw key matrix for true hold-to-move while a cart runs. The matrix
    # is the only way a *held* direction keeps firing -- the 1-byte ASCII path
    # reports each key once on the press edge (no autorepeat), so the hold-latch
    # below can only fake it for KEY_HOLD_MS and then movement stalls. Raw mode
    # needs T-Deck keyboard firmware >= 2025-06-12; on older firmware the 0x03
    # command is ignored, the keyboard keeps sending ASCII, and _read_raw_buttons
    # detects that and sticks the session back on the ASCII + latch path. Set this
    # False to force the ASCII path regardless of firmware.
    RAW_GAME_MODE = True
    # #69 experiment knob: the esp32 machine.I2C default clock-stretch timeout is
    # 50000us -- and I2CSTAT sized the stalls on hardware (2026-07-04): kbd max
    # 21.6-59.8ms, touch max 41ms, several >20ms per play session, maxima hugging
    # that 50ms ceiling. 5000 caps a stretch at 5ms: a stall becomes a <=5ms
    # failed read (caught -> empty buttons for ONE frame) instead of a felt
    # 60ms input freeze. The touch shares this bus/I2C object, so the knob
    # governs both. Set None to restore the driver default (the A/B revert).
    I2C_TIMEOUT_US = 5000
    # #69 per-session I2C latency stats, updated by _timed_read on every keyboard
    # transaction and read by the I2CSTAT diag line (moy_runtime): total reads,
    # worst-case us (+ which mode it happened in), and how many crossed 5ms / 20ms.
    # The HITCH kbd= column only sees stalls inside >80ms frames; this sees ALL.
    # Class-level defaults so every construction path starts zeroed; the first
    # update shadows them per-instance.
    stat_n = 0
    stat_max_us = 0
    stat_max_raw = False
    stat_over5 = 0
    stat_over20 = 0
    stat_timeouts = 0     # reads that RAISED (capped stalls) -- one stale frame each
    # A capped stall (#69) raises on ONE read; only this many consecutive
    # failures mean the keyboard is genuinely gone (see _read_error).
    ERR_RUN_LIMIT = 10
    _err_run = 0
    _raw_last = ()        # last good raw matrix state, held across a capped stall

    def __init__(self, input_state):
        self.input = input_state
        self.available = False
        self.raw_mode = False
        self._raw_unsupported = False   # set once if 0x03 was ignored (old firmware)
        self._i2c = None
        self._held_buttons = ()
        self._held_until_ms = 0
        try:
            from machine import I2C, Pin

            if self.I2C_TIMEOUT_US is None:
                self._i2c = I2C(0, scl=Pin(8), sda=Pin(18), freq=400000)
            else:
                self._i2c = I2C(0, scl=Pin(8), sda=Pin(18), freq=400000,
                                timeout=self.I2C_TIMEOUT_US)
            self._i2c.readfrom(self.KEYBOARD_ADDR, 1)
            # Boot in 1-byte ASCII mode (clean ASCII; verified by the keyboard
            # probe). set_game_mode(True) switches to the raw matrix (0x03) while a
            # cart runs so a *held* direction keeps firing, and set_game_mode(False)
            # restores ASCII (0x04) for the code editor -- sending 0x04 is the revert
            # an earlier attempt missed, which is why raw mode used to garble editor
            # text irreversibly. __init__ never enables raw (the editor/launcher must
            # come up in ASCII); the console toggles it per screen.
            self.available = True
        except Exception as exc:
            print("Moybyte keyboard unavailable:", exc)

    def poll(self):
        if self.raw_mode:
            buttons = self._read_raw_buttons()
            self.input.release_all()
            for button in buttons:
                self.input.set_button(button, True)
            return

        now_ms = _ticks_ms()
        key = self._read_key()
        self.input.last_key = key
        # Text mode (a cart's textmode(True) / the code editor): report the key but do
        # NOT also fire its game-button alias (w/a/s/d/z/x -> up/left/down/right/a/b),
        # or a typed password/name would also trigger d-pad + A/B shortcut actions
        # (#38/#42). Clear any latched buttons and stop here -- key()/keyp() still work.
        if getattr(self.input, "text_mode", False):
            self._held_buttons = ()
            self.input.release_all()
            return
        if key != 0:
            self._held_buttons = self._buttons_for_key(key)
            self._held_until_ms = now_ms + self.KEY_HOLD_MS
        elif _ticks_diff(self._held_until_ms, now_ms) <= 0:
            self._held_buttons = ()

        self.input.release_all()
        for button in self._held_buttons:
            self.input.set_button(button, True)

    def set_game_mode(self, on):
        """Switch the keyboard between raw-matrix (on=True: true held-state for a
        running cart) and 1-byte ASCII (on=False: the code editor / launcher). The
        console calls this on every screen change; it is idempotent and only talks
        to the keyboard on a real transition. Honoured only when RAW_GAME_MODE is
        set and the firmware actually supports raw -- otherwise it stays on ASCII
        and the hold-latch fallback applies."""
        if not self.available or self._i2c is None:
            return
        want = bool(on) and self.RAW_GAME_MODE and not self._raw_unsupported
        if want == self.raw_mode:
            return
        if want:
            self._enable_raw_mode()      # sends 0x03; sets raw_mode True on success
        else:
            self._disable_raw_mode()     # sends 0x04; back to 1-byte ASCII

    def _disable_raw_mode(self):
        try:
            self._i2c.writeto(self.KEYBOARD_ADDR, self.KEY_MODE_CMD)
        except Exception as exc:  # noqa: BLE001
            print("Moybyte keyboard mode revert failed:", exc)
        self.raw_mode = False
        self._held_buttons = ()

    def _timed_read(self, nbytes):
        """The one place a keyboard I2C transaction happens: readfrom + #69 latency
        stats (a 5-byte read at 400kHz is ~135us nominal; anything in the ms range
        is the C3 clock-stretching or bus contention -- exactly what I2CSTAT sizes)."""
        t0 = _ticks_us()
        data = self._i2c.readfrom(self.KEYBOARD_ADDR, nbytes)
        el = _ticks_diff(_ticks_us(), t0)
        self.stat_n += 1
        if el > self.stat_max_us:
            self.stat_max_us = el
            self.stat_max_raw = self.raw_mode
        if el >= 5000:
            self.stat_over5 += 1
            if el >= 20000:
                self.stat_over20 += 1
        return data

    def _read_error(self, exc, label):
        """One failed keyboard I2C read (#69): with the I2C_TIMEOUT_US cap a C3
        clock-stretch now RAISES (ETIMEDOUT) instead of blocking 50ms -- that is
        ONE dropped input frame by design, not a dead keyboard. Only a solid run
        of consecutive failures (a genuinely absent/wedged keyboard -- the case
        the old immediate disable protected against) turns the session off."""
        self._err_run += 1
        self.stat_timeouts += 1
        if self._err_run >= self.ERR_RUN_LIMIT:
            print("Moybyte keyboard %s failed repeatedly:" % label, exc)
            self.available = False

    def _read_key(self):
        if not self.available or self._i2c is None:
            return 0
        try:
            data = self._timed_read(1)
            self._err_run = 0
            if data:
                return data[0]
        except Exception as exc:
            self._read_error(exc, "read")
        return 0

    def _enable_raw_mode(self):
        try:
            self._i2c.writeto(self.KEYBOARD_ADDR, self.RAW_MODE_CMD)
            self.raw_mode = True
        except Exception:
            self.raw_mode = False

    def _read_raw_buttons(self):
        try:
            data = self._timed_read(5)
            self._err_run = 0
        except Exception as exc:
            # Transient (a capped stall): keep raw_mode -- next frame reads again --
            # and HOLD the last known matrix state for this one frame. Returning ()
            # would release-then-repress a held button across the gap, which btnp()
            # reads as a spurious second press edge (double-jump/double-shot). Held
            # state one frame stale is invisible; a phantom edge is not. Only the
            # consecutive-failure limit ends the session (see _read_error).
            self._read_error(exc, "raw read")
            return self._raw_last
        if len(data) < 5:
            self.raw_mode = False
            self.input.last_key = 0
            self._raw_last = ()
            return ()
        if data[1] == 0 and data[2] == 0 and data[3] == 0 and data[4] == 0:
            key = data[0]
            buttons = self._buttons_for_key(key) if key > 0x20 else ()
            if buttons:
                # A printable ASCII byte arrived while we asked for raw: the
                # firmware ignored 0x03 (pre-2025-06-12). Stick the session on the
                # 1-byte ASCII + latch path so set_game_mode stops retrying raw.
                self.raw_mode = False
                self._raw_unsupported = True
                self._held_buttons = buttons
                self._held_until_ms = _ticks_ms() + self.KEY_HOLD_MS
                self.input.last_key = key
                return buttons

        d0, d1, _d2, d3, d4 = data[0], data[1], data[2], data[3], data[4]
        buttons = []
        key = 0
        if (d0 & 0x08) or (d3 & 0x02):
            buttons.append("left")
            key = ord("a")
        if (d1 & 0x04) or (d4 & 0x02):
            buttons.append("right")
            key = ord("d")
        if (d0 & 0x02) or (d4 & 0x40):
            buttons.append("up")
            key = ord("w")
        if (d1 & 0x02) or (d3 & 0x40):
            buttons.append("down")
            key = ord("s")
        if (d1 & 0x20) or (d0 & 0x20) or (d3 & 0x08):
            buttons.append("a")
            key = ord("z")
        if (d1 & 0x10) or (d4 & 0x08):
            buttons.append("b")
            key = ord("x")
        if _d2 & 0x01:
            buttons.append("run")
            key = ord("r")
        if d0 & 0x01:
            buttons.append("home")
            key = ord("q")
        if d1 & 0x01:
            buttons.append("stop")
            key = ord("e")
        self.input.last_key = key
        self._raw_last = buttons     # held across a capped-stall frame (see above)
        return buttons

    def _map_key(self, key):
        for button in self._buttons_for_key(key):
            self.input.set_button(button, True)

    def _buttons_for_key(self, key):
        if key in (ord("a"), ord("A"), ord("h"), ord("H")):
            return ("left",)
        elif key in (ord("d"), ord("D"), ord("l"), ord("L")):
            return ("right",)
        elif key in (ord("w"), ord("W"), ord("k"), ord("K")):
            return ("up",)
        elif key in (ord("s"), ord("S"), ord("j"), ord("J")):
            return ("down",)
        elif key in (ord("z"), ord("Z"), ord(" "), 0x0D):
            return ("a",)
        elif key in (ord("x"), ord("X")):
            return ("b",)
        elif key in (ord("r"), ord("R")):
            return ("run",)
        elif key == 0x1B:
            return ("stop",)
        elif key == 0x08:
            # BACKSPACE is the console key (#71 pause / HOME) on the typed-ASCII
            # path. q/Q and e/E lost their home/stop aliases here: typing carts
            # (Letter Blitz) read letters via key()/keyp(), and a letter that
            # ALSO fires console chrome is a stolen letter -- pressing Q paused
            # the game instead of shooting the Q target. Text-mode screens (code
            # editor, wifi password) suppress ALL aliases, so backspace still
            # deletes there; d-pad carts run on the RAW MATRIX path where the
            # PHYSICAL q key keeps its home/pause role (_read_raw_buttons).
            return ("home",)
        return ()


def _ticks_ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def _ticks_diff(end_ms, start_ms):
    try:
        return time.ticks_diff(end_ms, start_ms)
    except AttributeError:
        return end_ms - start_ms


def _ticks_us():
    try:
        return time.ticks_us()
    except AttributeError:
        return int(time.time() * 1000000)
