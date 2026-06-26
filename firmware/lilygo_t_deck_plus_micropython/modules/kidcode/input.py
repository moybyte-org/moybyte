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

            self._i2c = I2C(0, scl=Pin(8), sda=Pin(18), freq=400000)
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
            print("KidCode keyboard unavailable:", exc)

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
            print("KidCode keyboard mode revert failed:", exc)
        self.raw_mode = False
        self._held_buttons = ()

    def _read_key(self):
        if not self.available or self._i2c is None:
            return 0
        try:
            data = self._i2c.readfrom(self.KEYBOARD_ADDR, 1)
            if data:
                return data[0]
        except Exception as exc:
            print("KidCode keyboard read failed:", exc)
            self.available = False
        return 0

    def _enable_raw_mode(self):
        try:
            self._i2c.writeto(self.KEYBOARD_ADDR, self.RAW_MODE_CMD)
            self.raw_mode = True
        except Exception:
            self.raw_mode = False

    def _read_raw_buttons(self):
        try:
            data = self._i2c.readfrom(self.KEYBOARD_ADDR, 5)
        except Exception as exc:
            print("KidCode keyboard raw read failed:", exc)
            self.raw_mode = False
            self.available = False
            self.input.last_key = 0
            return ()
        if len(data) < 5:
            self.raw_mode = False
            self.input.last_key = 0
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
        elif key in (ord("x"), ord("X"), 0x08):
            return ("b",)
        elif key in (ord("r"), ord("R")):
            return ("run",)
        elif key in (ord("e"), ord("E"), 0x1B):
            return ("stop",)
        elif key in (ord("q"), ord("Q")):
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
