"""P4 display glue (#58): this board's backlight + the shared DSI compositor.

The compositor itself -- the triple-buffer rotation, the deferred present, the
fences and their meters -- is `device/dsi_panel.py` since 2026-09-06 (one body
for both P4 boards; its header carries the design). What is this board's is
the backlight: GPIO32, ACTIVE-LOW (the AP3032 boost's EN through a transistor
-- hardware-confirmed), which is the one fact the two P4 boards differ on.
"""

try:
    from dsi_panel import P4Compositor as _DsiCompositor
except ImportError:  # pragma: no cover - host package lane
    from device.dsi_panel import P4Compositor as _DsiCompositor

BACKLIGHT_GPIO = 32     # active-LOW (board fact, hardware-confirmed)

_bl_pin = None


def set_backlight(on):
    global _bl_pin
    from machine import Pin
    if _bl_pin is None:
        _bl_pin = Pin(BACKLIGHT_GPIO, Pin.OUT, value=0 if on else 1)
    else:
        _bl_pin.value(0 if on else 1)


class P4Compositor(_DsiCompositor):
    """The shared DSI compositor, dark until the first composed frame through
    THIS board's backlight."""

    def __init__(self):
        _DsiCompositor.__init__(self, set_backlight)
