"""`device/gsl3680.py`, EXECUTED against a scripted I2C -- the Silead GSL3680
driver the Guition P4 carries (a RAM-loaded part: the host uploads its
firmware after every reset). The packet bytes below are REAL, sampled off
the board's dev channel during the 2026-09-06 calibration."""

import struct

import pytest

from device.gsl3680 import GSL3680, Touch, ADDR


class FakeI2C:
    """Records every write; answers reads from a scripted register map."""

    def __init__(self, regs=None):
        self.writes = []
        self.regs = dict(regs or {})

    def writeto_mem(self, addr, reg, data):
        assert addr == ADDR
        self.writes.append((reg, bytes(data)))

    def readfrom_mem(self, addr, reg, n):
        assert addr == ADDR
        v = self.regs.get(reg)
        if v is None:
            raise OSError("no such register")
        if callable(v):
            v = v()
        return bytes(v[:n])


class FakePin:
    OUT, IN, PULL_UP = 1, 0, 2

    def __init__(self):
        self.levels = []
        self.modes = []

    def value(self, v):
        self.levels.append(v)

    def init(self, mode, *a, **k):
        self.modes.append(mode)


def fw_blob(entries):
    return b"".join(struct.pack("<BI", o, v) for o, v in entries)


def test_the_firmware_upload_writes_page_selects_as_one_byte_and_data_as_four():
    fw = fw_blob([(0xF0, 0x02), (0x00, 0x11223344), (0x04, 0xAABBCCDD), (0xF0, 0x03)])
    i2c = FakeI2C({0xB0: b"\x5a\x5a\x5a\x5a"})
    chip = GSL3680(i2c, FakePin(), fw, FakePin())
    assert chip.init() is True
    data = [w for w in i2c.writes if w[0] in (0xF0, 0x00, 0x04)]
    assert (0xF0, b"\x02") in data
    assert (0x00, b"\x44\x33\x22\x11") in data          # little-endian, four bytes
    assert (0x04, b"\xdd\xcc\xbb\xaa") in data
    assert (0xF0, b"\x03") in data
    assert chip.loaded == 4


def test_the_vendor_reset_dance_brackets_the_upload():
    i2c = FakeI2C({0xB0: b"\x5a\x5a\x5a\x5a"})
    rst = FakePin()
    chip = GSL3680(i2c, rst, fw_blob([(0x00, 1)]), FakePin())
    chip.init()
    regs = [w[0] for w in i2c.writes]
    # clear_reg, reset, LOAD, startup, reset, startup: the 0xE0 power register
    # is written before and after the firmware, and the RST pin pulsed twice.
    assert regs.index(0x00) > regs.index(0xE0)
    assert regs.count(0xBC) == 2                        # two resets
    assert rst.levels.count(0) == 2 and rst.levels.count(1) == 2


def test_a_chip_that_never_signs_is_not_available():
    i2c = FakeI2C({0xB0: b"\x00\x00\x00\x00"})
    chip = GSL3680(i2c, FakePin(), fw_blob([(0x00, 1)]))
    assert chip.init() is False


# The real packets, sampled over the dev channel while a finger held the
# bottom-right and top-right corners of the Guition P4 (2026-09-06).
BOTTOM_RIGHT = bytes.fromhex("010094866a036a06")
TOP_RIGHT = bytes.fromhex("0100e09814005706")
RELEASED = bytes.fromhex("0000000016001c00")


# The same tap with the chip's y FLAG raised (bit 14 of y): what read as
# y = 17248 on 2026-09-06 and pinned the pointer to the bottom edge until the
# decode masked both axes to 12 bits, as Linux's silead.c does.
BOTTOM_RIGHT_FLAGGED = bytes.fromhex("010094866a436a06")


def test_the_packet_decodes_like_linux_both_axes_twelve_bit():
    i2c = FakeI2C({0x80: BOTTOM_RIGHT})
    chip = GSL3680(i2c, FakePin(), b"")
    assert chip.read() == (1, 1642, 874)                # x from [6..7] & 0xfff, y from [4..5] & 0xfff
    i2c.regs[0x80] = BOTTOM_RIGHT_FLAGGED
    assert chip.read() == (1, 1642, 874)                # the flag is not a coordinate
    i2c.regs[0x80] = TOP_RIGHT
    assert chip.read() == (1, 1623, 20)
    i2c.regs[0x80] = RELEASED
    assert chip.read()[0] == 0


def _touch(i2c, **knobs):
    """A Touch over a scripted chip, bypassing the machine.I2C construction."""
    t = Touch.__new__(Touch)
    t.w, t.h = 1280, 800
    t.swap_xy = knobs.get("swap_xy", False)
    t.flip_x = knobs.get("flip_x", False)
    t.flip_y = knobs.get("flip_y", False)
    t.raw_w = knobs.get("raw_w", 0)
    t.raw_h = knobs.get("raw_h", 0)
    t.raw_x0 = knobs.get("raw_x0", 0)
    t.raw_y0 = knobs.get("raw_y0", 0)
    t.available = True
    t.raw = None
    t.fingers = 0
    from device.gt911 import HeldPoint
    t._hp = HeldPoint()
    t._chip = GSL3680(i2c, FakePin(), b"")
    return t


def test_the_firmware_space_is_scaled_onto_the_glass():
    """The Guition P4's calibration: the chip reports in its firmware's
    1664x896, the desk is 1280x800, no swap, no flips."""
    i2c = FakeI2C({0x80: BOTTOM_RIGHT})
    t = _touch(i2c, raw_w=1664, raw_h=896)
    x, y, edge = t.poll()
    assert (x, y, edge) == (1642 * 1280 // 1664, 874 * 800 // 896, True)
    assert (x, y) == (1263, 780)
    assert t.raw == (1642, 874)
    i2c.regs[0x80] = TOP_RIGHT
    x, y, edge = t.poll()
    assert (x, y, edge) == (1248, 17, False)


def test_the_fitted_origin_and_span_put_the_five_targets_on_their_boxes():
    """The Guition P4's second calibration (the five-target tool): the raw
    origin sits (10, 21) in and the spans are 1640 x 865, so each target's
    raw sample lands within a finger's width of the box it was tapped on."""
    def pkt(x, y):
        return bytes((1, 0, 0, 0, y & 0xFF, y >> 8, x & 0xFF, x >> 8))
    i2c = FakeI2C({})
    t = _touch(i2c, raw_w=1640, raw_h=865, raw_x0=10, raw_y0=21)
    for raw, box in (((92, 86), (60, 60)), ((1575, 84), (1219, 60)),
                     ((80, 820), (60, 739)), ((1567, 817), (1219, 739)),
                     ((835, 462), (640, 400))):
        i2c.regs[0x80] = pkt(*raw)
        t._hp.release()
        x, y, _ = t.poll()
        assert abs(x - box[0]) <= 12 and abs(y - box[1]) <= 12, (raw, box, (x, y))


def test_without_a_raw_space_the_glass_bounds_clamp():
    i2c = FakeI2C({0x80: BOTTOM_RIGHT})
    t = _touch(i2c)
    assert t.poll()[:2] == (1279, 799)


def test_swap_and_flips_still_apply_after_the_scale():
    i2c = FakeI2C({0x80: TOP_RIGHT})
    t = _touch(i2c, raw_w=1664, raw_h=896, flip_x=True, flip_y=True)
    x, y, _ = t.poll()
    assert (x, y) == (1279 - 1248, 799 - 17)


def test_a_release_is_news_and_a_failed_read_is_not():
    i2c = FakeI2C({0x80: BOTTOM_RIGHT})
    t = _touch(i2c, raw_w=1664, raw_h=896)
    assert t.poll()[2] is True
    i2c.regs[0x80] = None                                # the read raises
    held = t.poll()
    assert held is not None and held[2] is False and t.fresh is False
    i2c.regs[0x80] = RELEASED
    assert t.poll() is None and t.fresh is True
