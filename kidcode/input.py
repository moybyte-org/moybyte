"""Abstract KidCode button input."""

BUTTONS = [
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
]


class InputState:
    def __init__(self):
        self._held = set()
        self._last = set()
        self._pressed = set()
        self._released = set()

    def begin_frame(self):
        self._pressed = self._held - self._last
        self._released = self._last - self._held
        self._last = set(self._held)

    def set_button(self, name, held):
        if name not in BUTTONS:
            raise ValueError("unknown button: " + name)
        if held:
            self._held.add(name)
        else:
            self._held.discard(name)

    def press(self, name):
        self.set_button(name, True)

    def release(self, name):
        self.set_button(name, False)

    def held(self, name):
        return name in self._held

    def pressed(self, name):
        return name in self._pressed

    def released(self, name):
        return name in self._released
