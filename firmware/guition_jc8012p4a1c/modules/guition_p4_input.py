"""Guition P4 touch input: the GSL3680 on I2C0 (SDA=7 / SCL=8 @ 0x40, RST=22,
INT=21 -- the factory demo's pins_config.h), over the shared driver in
`device/gsl3680.py` with this glass's firmware from `gsl_fw_jc8012.py`.

CALIBRATION: the panel is driven MIRRORED both ways (moy_dsi's
MOY_DSI_MIRROR_XY, the factory demo's orientation) and the controller reports
in its own frame; the three mapping knobs below are LIVE module globals read
when Touch() is constructed, so they can be flipped from the REPL without a
reflash:

    import guition_p4_input; guition_p4_input.FLIP_Y = False
    import moy_runtime; moy_runtime.run_touch_calibrate()

`run_touch_calibrate()` draws corner targets and prints every sample (raw +
mapped + the knob state) over serial -- tap the targets, read which corner
the mapped coords land in, set the knobs so mapped == target, bake the
winners in here.

The values below are NOT glass-calibrated: they reproduce the factory demo's
net mapping (esp_lcd_touch mirror_x + mirror_y, then `800 - x` in its read
callback, i.e. x raw, y mirrored) as the starting point for the first tap.
"""

try:
    from gsl3680 import Touch as _GslTouch
except ImportError:  # pragma: no cover - host package lane
    from device.gsl3680 import Touch as _GslTouch

from gsl_fw_jc8012 import FW

I2C_ID = 0
SDA = 7
SCL = 8
RST = 22
INT = 21

# Live-tweakable mapping knobs (module globals, read at construction).
# UNCALIBRATED -- see the module docstring for what they reproduce.
SWAP_XY = False
FLIP_X = False
FLIP_Y = True


class Touch(_GslTouch):
    def __init__(self, w=800, h=1280, progress=None):
        _GslTouch.__init__(self, FW, w, h, SDA, SCL, RST, INT, i2c_id=I2C_ID,
                           swap_xy=SWAP_XY, flip_x=FLIP_X, flip_y=FLIP_Y,
                           log=lambda m: print("Moybyte Guition P4", m),
                           progress=progress)
