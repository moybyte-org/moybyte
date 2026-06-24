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
    RAW_MODE_CMD = b"\x03"

    def __init__(self, input_state):
        self.input = input_state
        self.available = False
        self.raw_mode = False
        self._i2c = None
        self._held_buttons = ()
        self._held_until_ms = 0
        try:
            from machine import I2C, Pin

            self._i2c = I2C(0, scl=Pin(8), sda=Pin(18), freq=400000)
            self._i2c.readfrom(self.KEYBOARD_ADDR, 1)
            # The T-Deck keyboard returns clean 1-byte ASCII (verified by the
            # keyboard probe). We do NOT enable the 5-byte "raw matrix" mode
            # (RAW_MODE_CMD): it only decoded a fixed WASD/ZX subset and, once the
            # command was sent, the flag couldn't undo it -- which garbled the code
            # editor's text. poll() uses the 1-byte ASCII path; _buttons_for_key
            # maps letters to nav/game buttons (with the KEY_HOLD_MS latch).
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
                self.raw_mode = False
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
