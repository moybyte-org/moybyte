"""Guition P4 touch input: the GSL3680 on I2C0 (SDA=7 / SCL=8 @ 0x40, RST=22,
INT=21 -- the factory demo's pins_config.h), over the shared driver in
`device/gsl3680.py` with this glass's firmware from `gsl_fw_jc8012.py`.

CALIBRATED on glass 2026-09-06, twice: three corner holds gave the axes
(the controller reports LANDSCAPE, aligned with the desk as mounted --
ROTATION 270 -- no swap, no flips), then the five-target tool
(`moy_runtime.run_touch_calibrate()`) gave the fit: the firmware's space
starts ~10 x ~21 counts in and spans 1640 x 865 over the 1280x800 glass
(targets read raw (92,86) (1575,84) (80,820) (1567,817) (835,462); the
firmware's nominal 1664x896 was ~10px off at the edges). The same session
found bit 14 of the raw y set on some packets -- masked in the driver now;
unmasked it threw the pointer to the bottom edge, which read as "inaccurate".
The knobs below are LIVE module globals read when Touch() is constructed, and
live attributes on the driver afterwards (`py touch.raw_x0 = 12` over the dev
channel); if the desk is ever turned the other way up (ROTATION 90), both
flips go True.
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
# Calibrated -- see the module docstring.
SWAP_XY = False
FLIP_X = False
FLIP_Y = False
RAW_X0 = 10            # the firmware's coordinate space, as FITTED (five targets):
RAW_Y0 = 21            # origin, then span, mapped onto the 1280x800 glass
RAW_W = 1640
RAW_H = 865


class Touch(_GslTouch):
    def __init__(self, w=1280, h=800, progress=None):
        _GslTouch.__init__(self, FW, w, h, SDA, SCL, RST, INT, i2c_id=I2C_ID,
                           swap_xy=SWAP_XY, flip_x=FLIP_X, flip_y=FLIP_Y,
                           raw_w=RAW_W, raw_h=RAW_H, raw_x0=RAW_X0, raw_y0=RAW_Y0,
                           log=lambda m: print("Moybyte Guition P4", m),
                           progress=progress)
