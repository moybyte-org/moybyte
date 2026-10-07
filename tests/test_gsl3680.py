"""The GSL3680's driver (native/moy_input/moy_touchdev.c), EXECUTED against a
scripted I2C through the host's binding -- the Silead part the Guition P4
carries (RAM-loaded: the host uploads its firmware after every reset). The
packet bytes below are REAL, sampled off the board's dev channel during the
2026-09-06 calibration."""

import struct

from runtime import moy_input as mi

ADDR = 0x40


class FakeI2C:
    """Records every write as (register, data); answers reads from a scripted
    register map."""

    def __init__(self, regs=None):
        self.writes = []
        self.reads = []                 # (reg, n) -- the two-stage poll's witness
        self.regs = dict(regs or {})

    def writeto(self, addr, data):
        assert addr == ADDR
        self.writes.append((data[0], bytes(data[1:])))

    def readfrom_mem(self, addr, reg, n, addrsize=8):
        assert addr == ADDR
        self.reads.append((reg, n))
        v = self.regs.get(reg)
        if v is None:
            raise OSError("no such register")
        if callable(v):
            v = v()
        return bytes(v[:n])


class FakePins:
    """The reset callback's record: True a pulse with INT low, False INT
    released to its pull-up."""

    def __init__(self):
        self.calls = []

    def __call__(self, before):
        self.calls.append(before)


def _chip(i2c, fw=b"", **knobs):
    t = mi.TouchDriver(i2c, mi.GSL3680, 1280, 800, addr=ADDR, fw=fw, **knobs)
    t.probe()
    return t


def fw_blob(entries):
    return b"".join(struct.pack("<BI", o, v) for o, v in entries)


def test_the_firmware_upload_writes_page_selects_as_one_byte_and_data_as_four():
    fw = fw_blob([(0xF0, 0x02), (0x00, 0x11223344), (0x04, 0xAABBCCDD), (0xF0, 0x03)])
    i2c = FakeI2C({0xB0: b"\x5a\x5a\x5a\x5a"})
    chip = _chip(i2c, fw)
    assert chip.gsl_init(FakePins()) is True and chip.available
    data = [w for w in i2c.writes if w[0] in (0xF0, 0x00, 0x04)]
    assert (0xF0, b"\x02") in data
    assert (0x00, b"\x44\x33\x22\x11") in data          # little-endian, four bytes
    assert (0x04, b"\xdd\xcc\xbb\xaa") in data
    assert (0xF0, b"\x03") in data
    assert chip.loaded == 4


def test_the_vendor_reset_dance_brackets_the_upload():
    i2c = FakeI2C({0xB0: b"\x5a\x5a\x5a\x5a"})
    pins = FakePins()
    chip = _chip(i2c, fw_blob([(0x00, 1)]))
    chip.gsl_init(pins)
    regs = [w[0] for w in i2c.writes]
    # clear_reg, reset, LOAD, startup, reset, startup: the 0xE0 power register
    # is written before and after the firmware, the RST pin pulsed twice with
    # INT low, and INT released once the firmware runs.
    assert regs.index(0x00) > regs.index(0xE0)
    assert regs.count(0xBC) == 2                        # two resets
    assert pins.calls == [True, True, False]


def test_a_chip_that_never_signs_is_not_available():
    i2c = FakeI2C({0xB0: b"\x00\x00\x00\x00"})
    chip = _chip(i2c, fw_blob([(0x00, 1)]))
    assert chip.gsl_init(FakePins()) is False and not chip.available


# The real packets, sampled over the dev channel while a finger held the
# bottom-right and top-right corners of the Guition P4 (2026-09-06).
BOTTOM_RIGHT = bytes.fromhex("010094866a036a06")
TOP_RIGHT = bytes.fromhex("0100e09814005706")
RELEASED = bytes.fromhex("0000000016001c00")


# The same tap with the chip's y FLAG raised (bit 14 of y): what read as
# y = 17248 on 2026-09-06 and pinned the pointer to the bottom edge until the
# decode masked both axes to 12 bits, as Linux's silead.c does.
BOTTOM_RIGHT_FLAGGED = bytes.fromhex("010094866a436a06")


def _touch(i2c, **knobs):
    """The driver up over a scripted chip, its upload skipped."""
    t = _chip(i2c, **knobs)
    t.available = True
    return t


def _poll(t, now=0):
    """One frame: the pass, then the frame's sample."""
    t.pass_()
    return t.poll(now)


def test_the_packet_decodes_like_linux_both_axes_twelve_bit():
    i2c = FakeI2C({0x80: BOTTOM_RIGHT})
    t = _touch(i2c)
    _poll(t)
    assert t.raw == (1642, 874) and t.fingers == 1     # x [6..7], y [4..5], 12-bit
    i2c.regs[0x80] = BOTTOM_RIGHT_FLAGGED
    _poll(t)
    assert t.raw == (1642, 874)                         # the flag is not a coordinate
    i2c.regs[0x80] = TOP_RIGHT
    _poll(t)
    assert t.raw == (1623, 20)
    i2c.regs[0x80] = RELEASED
    assert _poll(t) is None and t.fingers == 0


def test_an_untouched_frame_reads_one_byte_and_a_touched_one_reads_eight():
    """#220. The finger count is byte 0 of the point register, so the frame
    nobody is touching -- every frame of a game -- needs one byte, not eight
    (measured on the Guition P4's 400kHz bus: 205us against 375us). The CADENCE
    is untouched: the chip is still asked on every frame."""
    i2c = FakeI2C({0x80: RELEASED})
    t = _touch(i2c)
    t.pass_()
    assert i2c.reads == [(0x80, 1)]                     # the eight-byte read never ran
    i2c.reads = []
    i2c.regs[0x80] = BOTTOM_RIGHT
    t.pass_()
    assert i2c.reads == [(0x80, 1), (0x80, 8)]


def test_the_point_comes_from_the_second_transaction_never_the_first():
    """A finger that lands between the two reads is reported with the
    coordinates that arrived BESIDE its count: the count that wins is the
    eight-byte read's own byte 0."""
    seen = []

    def answer():
        seen.append(1)
        return RELEASED if len(seen) > 1 else BOTTOM_RIGHT

    i2c = FakeI2C({0x80: answer})
    t = _touch(i2c)
    assert _poll(t) is None                             # the SECOND read's count
    assert i2c.reads == [(0x80, 1), (0x80, 8)]


def test_an_untouched_poll_is_a_release_through_the_short_read():
    """The one-byte path still speaks the pointer contract: 0 fingers is a
    RELEASE (news), not the no-news a failed read produces."""
    i2c = FakeI2C({0x80: BOTTOM_RIGHT})
    t = _touch(i2c, raw_w=1640, raw_h=865, raw_x0=10, raw_y0=21)
    assert _poll(t) is not None
    i2c.regs[0x80] = RELEASED
    assert _poll(t, 10) is None
    assert t.fresh is True
    assert i2c.reads[-1] == (0x80, 1)


def test_the_firmware_space_is_scaled_onto_the_glass():
    """The chip reports in its firmware's 1664x896, the desk is 1280x800."""
    i2c = FakeI2C({0x80: BOTTOM_RIGHT})
    t = _touch(i2c, raw_w=1664, raw_h=896)
    x, y, edge = _poll(t)
    assert (x, y, edge) == (1642 * 1280 // 1664, 874 * 800 // 896, True)
    assert (x, y) == (1263, 780)
    i2c.regs[0x80] = TOP_RIGHT
    assert _poll(t, 10) == (1248, 17, False)


def test_the_fitted_origin_and_span_put_the_five_targets_on_their_boxes():
    """The five-target calibration: the raw origin sits (10, 21) in and the
    spans are 1640 x 865, so each target's raw sample lands within a finger's
    width of the box it was tapped on."""
    def pkt(x, y):
        return bytes((1, 0, 0, 0, y & 0xFF, y >> 8, x & 0xFF, x >> 8))
    i2c = FakeI2C({})
    t = _touch(i2c, raw_w=1640, raw_h=865, raw_x0=10, raw_y0=21)
    for raw, box in (((92, 86), (60, 60)), ((1575, 84), (1219, 60)),
                     ((80, 820), (60, 739)), ((1567, 817), (1219, 739)),
                     ((835, 462), (640, 400))):
        i2c.regs[0x80] = pkt(*raw)
        x, y, _ = _poll(t)
        assert abs(x - box[0]) <= 12 and abs(y - box[1]) <= 12, (raw, box, (x, y))


def test_without_a_raw_space_the_glass_bounds_clamp():
    t = _touch(FakeI2C({0x80: BOTTOM_RIGHT}))
    assert _poll(t)[:2] == (1279, 799)


def test_swap_and_flips_still_apply_after_the_scale():
    t = _touch(FakeI2C({0x80: TOP_RIGHT}), raw_w=1664, raw_h=896, flip_x=True, flip_y=True)
    assert _poll(t)[:2] == (1279 - 1248, 799 - 17)


def test_a_release_is_news_and_a_failed_read_is_not():
    i2c = FakeI2C({0x80: BOTTOM_RIGHT})
    t = _touch(i2c, raw_w=1664, raw_h=896)
    assert _poll(t)[2] is True
    i2c.regs[0x80] = None                                # the read raises
    held = _poll(t, 10)
    assert held is not None and held[2] is False and t.fresh is False
    i2c.regs[0x80] = RELEASED
    assert _poll(t, 20) is None and t.fresh is True


def test_the_boards_firmware_is_the_c_array_it_uploads():
    """The Guition P4's firmware is its board's C array: 4587 five-byte
    records, a page select first."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "firmware" / "guition_jc8012p4a1c"
           / "boards" / "MOYBYTE_GUITION_P4" / "gsl_fw_jc8012.c").read_text()
    body = src[src.index("moy_gsl_fw[] = {"):src.index("};")]
    data = [int(b, 16) for b in re.findall(r"0x([0-9a-f]{2}),", body)]
    assert len(data) == 4587 * 5
    assert data[0] == 0xF0
