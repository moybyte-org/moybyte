"""Guition P4 touch input: the GSL3680 on I2C0 (SDA=7 / SCL=8 @ 0x40, RST=22,
INT=21 -- the factory demo's pins_config.h), over the shared driver in
`device/gsl3680.py` with this glass's firmware from `gsl_fw_jc8012.py`.

CALIBRATION: the controller reports in the PORTRAIT panel frame (x 0..799,
y 0..1279) and the desk is LANDSCAPE (1280x800, rotated on the PPA --
guition_p4_display.ROTATION), so the axes are SWAPPED and then flipped to
match; the three mapping knobs below are LIVE module globals read when
Touch() is constructed, and live attributes on the driver afterwards:

    py touch.flip_x = True          # over the dev channel, no reflash
    import moy_runtime; moy_runtime.run_touch_calibrate()   # from the REPL

`run_touch_calibrate()` draws corner targets and prints every sample (raw +
mapped + the knob state) over serial -- tap the targets, read which corner
the mapped coords land in, set the knobs so mapped == target, bake the
winners in here. Changing ROTATION (90 <-> 270) flips BOTH axes' sense.

The values below are NOT glass-calibrated: for the desk's 270-degree
rotation (guition_p4_display.ROTATION, owner-verified) the inverse map is
x = raw_y, y = 799 - raw_x, i.e. swap then flip y -- the starting point for
the first tap.
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
SWAP_XY = True
FLIP_X = False
FLIP_Y = True


class Touch(_GslTouch):
    def __init__(self, w=1280, h=800, progress=None):
        _GslTouch.__init__(self, FW, w, h, SDA, SCL, RST, INT, i2c_id=I2C_ID,
                           swap_xy=SWAP_XY, flip_x=FLIP_X, flip_y=FLIP_Y,
                           log=lambda m: print("Moybyte Guition P4", m),
                           progress=progress)
