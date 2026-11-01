"""The M5Stack Core2's input: the FT6336U touch, the Faces Gamepad3 on the
M5-Bus, and a BLE HID keyboard on the ESP32's own radio -- the kernel's
drivers (native/moy_input), all three writing the kernel's table.

The gamepad needs no construction here: its driver makes its "pad" source on
the input task's first pass, which the touch's bring-up starts, and from then
every frame's kick reads it when its INT line says a press changed. The BLE
keyboard is `ws.keyboard`, as on the touch-only boards.
"""

import moy_input


class Core2Input:
    """Every input source on this board. `inp` is the kernel's table every
    source writes into."""

    def __init__(self, inp):
        self.inp = inp
        self.keyboard = moy_input.ble()
        self.touch = None

    def build(self, w, h):
        """The FT6336U, behind the splash, mapped onto the w x h system
        canvas; its bring-up starts the input task the gamepad rides."""
        self.touch = moy_input.touch(w, h)
        return self.touch
