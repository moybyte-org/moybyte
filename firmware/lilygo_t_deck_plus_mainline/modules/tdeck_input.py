"""The T-Deck's input: the C3 keyboard, the trackball and the GT911, the
kernel's drivers (native/moy_input), and the optional BLE HID keyboard beside
the physical one.

`moy_runtime.run_desktop` constructs one `TDeckInput` over the kernel's table
before the spine and hands `build` to the spine as its `inputs()` (so the
GT911 comes up behind the splash). The frame's input stage is the kernel's
(`moy_input.loop_bind`): the keyboard and the GT911 are passed on its input
task (#69), which the frame's tail kicks once, so a C3 clock-stretch blocks
that task and never a frame; the trackball's pulses are counted by ISRs and
spent by the console as a caret's arrows or the cursor.
"""

import moy_input


class TDeckInput:
    """Every input source on this board. `inp` is the kernel's table every
    source writes into."""

    def __init__(self, inp):
        self.inp = inp
        self.keyboard = moy_input.keyboard()
        self.ball = moy_input.trackball()
        self.touch = None
        # BLE HID keyboard (#26): a SECOND, optional input source on this
        # board. On the touch-only boards a paired BLE keyboard is the only
        # keyboard and becomes ws.keyboard outright; here the physical C3
        # keyboard keeps that slot and the BLE driver hangs off
        # ws.ble_keyboard. Both write into the same table, so nothing in the
        # shared console needs to know which one a keypress came from.
        #
        # Not started here, deliberately: scanning is what makes BLE
        # expensive, and a board whose keyboard already works should not pay
        # for a radio nobody asked for. Settings starts it when the kid opens
        # the panel.
        self.ble_keyboard = moy_input.ble()

    def build(self, w, h):
        """The GT911, behind the splash, mapped onto the w x h system canvas."""
        self.touch = moy_input.touch(w, h)
        return self.touch
