"""Guition P4 touch input: the GSL3680 on I2C0 (SDA=7 / SCL=8 @ 0x40, RST=22,
INT=21 -- the factory demo's pins_config.h), over the shared driver in
`device/gsl3680.py` with this glass's firmware from `gsl_fw_jc8012.py`.

CALIBRATED on glass 2026-09-06 (three corner holds, raw packets sampled over
the dev channel): the controller reports LANDSCAPE, aligned with the desk as
mounted (ROTATION 270) -- no swap, no flips -- in the firmware's own
1664x896 space (the resolution in its config block), so the driver SCALES it
onto the 1280x800 desk: top-left read (33, 17), top-right (1632, 27),
bottom-right (1640, 875). The knobs below are LIVE module globals read when
Touch() is constructed, and live attributes on the driver afterwards
(`py touch.flip_x = True` over the dev channel) -- if the desk is ever
turned the other way up (ROTATION 90), both flips go True.
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
RAW_W = 1664           # the firmware's coordinate space (config 0x3800680)
RAW_H = 896


class Touch(_GslTouch):
    def __init__(self, w=1280, h=800, progress=None):
        _GslTouch.__init__(self, FW, w, h, SDA, SCL, RST, INT, i2c_id=I2C_ID,
                           swap_xy=SWAP_XY, flip_x=FLIP_X, flip_y=FLIP_Y,
                           raw_w=RAW_W, raw_h=RAW_H,
                           log=lambda m: print("Moybyte Guition P4", m),
                           progress=progress)
