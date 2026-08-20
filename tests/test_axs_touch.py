"""axs_touch's coordinate mapping -- the half of the AXS15231 driver that
needs no glass.

The Guition is TOUCH-ONLY, so a mapping bug is not a rough edge, it is the
whole input system; and the mapping is exactly the part a host test can reach.
The wire format is eight bytes, the driver takes its I2C bus by injection
(`Touch(i2c=...)`), and everything in between is arithmetic over the module's
baked rotation knobs.

The clamp test is the one that came from a real defect: only the UPPER bounds
were clamped, and under `FLIP_X` a raw coordinate past the panel edge maps to
a NEGATIVE logical one -- an off-glass press arriving as a point off the
opposite side of the screen.
"""

from device import axs_touch


class _FakeI2C:
    """Hands back canned 8-byte reports; the constructor's probe eats one."""

    def __init__(self, *frames):
        self.frames = list(frames)
        self.writes = []

    def writeto(self, addr, data):
        self.writes.append((addr, bytes(data)))

    def readfrom(self, addr, n):
        return self.frames.pop(0) if self.frames else bytes(8)


def _report(x, y):
    """One touch point, in the controller's portrait-native coordinates."""
    return bytes((0, 1,
                  (x >> 8) & 0x0F, x & 0xFF,
                  (y >> 8) & 0x0F, y & 0xFF,
                  0, 0))


def _touch(*frames):
    t = axs_touch.Touch(w=480, h=320, i2c=_FakeI2C(bytes(8), *frames))
    assert t.available
    return t


def test_the_baked_landscape_mapping_is_the_rot_0_one():
    """SWAP_XY + FLIP_X: logical x = 479 - raw_y, logical y = raw_x (the
    docstring's rot-0 mapping, calibrated on glass 2026-08-19)."""
    assert (axs_touch.SWAP_XY, axs_touch.FLIP_X, axs_touch.FLIP_Y) \
        == (True, True, False)
    t = _touch(_report(100, 300))
    assert t.poll() == (479 - 300, 100, True)
    assert t.raw == (100, 300)          # raw stays pre-mapping, for calibration


def test_a_press_past_the_panel_edge_is_clamped_at_both_ends():
    """A raw 12-bit coordinate can read past the panel; the flip then turns it
    negative. Clamping only the top put that press on the far side of the
    screen."""
    t = _touch(_report(400, 600))       # 400 > h-1, and 479 - 600 < 0
    x, y, edge = t.poll()
    assert edge
    assert 0 <= x < 480 and 0 <= y < 320
    assert (x, y) == (0, 319)
