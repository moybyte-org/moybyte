"""Guition P4 display glue: this board's backlight + the rotated DSI compositor.

The glass is PORTRAIT-native (800 wide, 1280 tall) and the desk is LANDSCAPE
(owner call, 2026-09-06): `device/dsi_panel.py`'s RotatedCompositor paints a
persistent 1280x800 landscape buffer and rotates it into the panel's scan
buffers on the PPA -- whole-frame for chrome, one rect for a quiet game frame.
Its header carries the design and the costs. What is this board's is the
backlight (GPIO23, ACTIVE-HIGH; the vendor BSP runs it as an LEDC PWM channel,
so a duty is available the day something wants one) and which way is up:

    ROTATION = 90     # counter-clockwise, the PPA's convention; 270 = the
                      # other way up. Live for a session: `py comp.set_angle(270)`
                      # over the dev channel, then re-map the touch the same way.
"""

try:
    from dsi_panel import RotatedCompositor as _Rotated
except ImportError:  # pragma: no cover - host package lane
    from device.dsi_panel import RotatedCompositor as _Rotated

BACKLIGHT_GPIO = 23     # active-HIGH (factory demo: pins_config.h LCD_LED)

# Which way the landscape desk lands on the portrait glass (see the docstring).
# 270, OWNER-VERIFIED 2026-09-06: the first build guessed 90 and came up
# upside down on the desk.
ROTATION = 270

_bl_pin = None


def set_backlight(on):
    global _bl_pin
    from machine import Pin
    if _bl_pin is None:
        _bl_pin = Pin(BACKLIGHT_GPIO, Pin.OUT, value=1 if on else 0)
    else:
        _bl_pin.value(1 if on else 0)


class P4Compositor(_Rotated):
    """The rotated DSI compositor, dark until the first composed frame through
    THIS board's backlight, turned this board's way up."""

    def __init__(self):
        _Rotated.__init__(self, set_backlight, angle=ROTATION)
