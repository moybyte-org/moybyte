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

    def inject_button(self, name, down, pressed=False, released=False):
        """Drive a button from a source that runs AFTER begin_frame -- the virtual
        gamepad (#42), which hit-tests the pointer in handle_input. It can't rely on
        begin_frame's _prev edge snapshot (that ran before the touch was known AND, on
        the device, the keyboard's per-frame release_all rebuilds _held each frame), so
        the CALLER computes the edge from its own previous-frame state and passes it:
        `down` sets btn(), `pressed`/`released` force the btnp()/released() edge for
        THIS button. We force this button's membership in the SAME _pressed/_released
        the keyboard path feeds (no parallel mechanism), overriding begin_frame's
        (possibly spurious) verdict for it -- begin_frame's verdict for OTHER (keyboard)
        buttons is untouched. Single-pointer v1: the gamepad owns its 6 buttons while
        active, so a keyboard edge on the same button in the same frame isn't mixed."""
        if getattr(self, "_pressed", None) is None:
            self._pressed = set()
        if getattr(self, "_released", None) is None:
            self._released = set()
        if down:
            self._held.add(name)
        else:
            self._held.discard(name)
        (self._pressed.add if pressed else self._pressed.discard)(name)
        (self._released.add if released else self._released.discard)(name)

    def held(self, name):
        return name in self._held

    def pressed(self, name):
        return name in getattr(self, "_pressed", set())

    def released(self, name):
        return name in getattr(self, "_released", set())

    def release_all(self):
        self._held.clear()
