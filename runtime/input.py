"""Minimal input state for the v0.4 userland: edge-detected buttons + pointer.

Mirrors the firmware `kidcode` input contract (held / pressed / released) so
cartridges poll the same way on host and device.
"""


class InputState:
    BUTTONS = ("left", "right", "up", "down", "a", "b", "run", "home")

    def __init__(self):
        self._held = set()
        self._prev = set()
        self.pointer = None  # (x, y) or None
        self.last_key = 0    # last typed ASCII byte (for the shared code editor)

    def set_held(self, name, down):
        if down:
            self._held.add(name)
        else:
            self._held.discard(name)

    def begin_frame(self):
        # Snapshot for edge detection; call once per frame before polling.
        self._pressed = self._held - self._prev
        self._released = self._prev - self._held
        self._prev = set(self._held)

    def held(self, name):
        return name in self._held

    def pressed(self, name):
        return name in getattr(self, "_pressed", set())

    def released(self, name):
        return name in getattr(self, "_released", set())

    def release_all(self):
        self._held.clear()
