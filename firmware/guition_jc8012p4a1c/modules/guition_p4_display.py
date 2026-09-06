"""Guition P4 display glue: this board's backlight + the shared DSI compositor.

The compositor -- the triple-buffer rotation, the deferred present, the fences
and their meters -- is `device/dsi_panel.py` (one body for both P4 boards; its
header carries the design). What is this board's is the backlight: GPIO23,
ACTIVE-HIGH (the factory demo drives it 1 = on; the vendor's own BSP runs it
as an LEDC PWM channel, so a duty is available the day something wants one --
today every caller asks for on/off, the Guition S3's verdict).
"""

try:
    from dsi_panel import P4Compositor as _DsiCompositor
except ImportError:  # pragma: no cover - host package lane
    from device.dsi_panel import P4Compositor as _DsiCompositor

BACKLIGHT_GPIO = 23     # active-HIGH (factory demo: pins_config.h LCD_LED)

_bl_pin = None


def set_backlight(on):
    global _bl_pin
    from machine import Pin
    if _bl_pin is None:
        _bl_pin = Pin(BACKLIGHT_GPIO, Pin.OUT, value=1 if on else 0)
    else:
        _bl_pin.value(1 if on else 0)


class P4Compositor(_DsiCompositor):
    """The shared DSI compositor, dark until the first composed frame through
    THIS board's backlight."""

    def __init__(self):
        _DsiCompositor.__init__(self, set_backlight)
