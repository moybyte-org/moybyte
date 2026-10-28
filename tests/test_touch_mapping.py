"""The four boards' raw -> glass touch mapping, pinned on sample points.

Every console board maps a controller's raw point onto its glass through the
same four steps -- swap the axes, scale the controller's space onto the
panel's, flip, clamp both ends -- in one body, the kernel's
(native/moy_input/moy_touch.c). Each board's knobs are its mpconfigboard.h's
MOY_INPUT_TOUCH_*, read here from the header the image is built from, and the
point goes through the board's real controller protocol (moy_touchdev.c) on a
fake bus: a report on the wire, one pass, the frame's poll. Corners, the
centre, and a press past each axis, per board.

The Waveshare P4's driver once clamped only the upper bound, so with both
flips on an off-glass raw coordinate left it as a point off the opposite edge;
the shared body clamps both ends, and the P4's section pins that.
"""

import re
from pathlib import Path

import pytest

from runtime import moy_input as mi

ROOT = Path(__file__).resolve().parent.parent
BOARDS = {
    "guition_s3": ("guition_jc3248w535", "MOYBYTE_GUITION_S3", 480, 320),
    "guition_p4": ("guition_jc8012p4a1c", "MOYBYTE_GUITION_P4", 1280, 800),
    "tdeck": ("lilygo_t_deck_plus_mainline", "MOYBYTE_TDECK", 320, 240),
    "p4": ("esp32_p4_wifi6_touch_lcd_7b", "MOYBYTE_P4", 1024, 600),
}


def knobs(board):
    """A board's MOY_INPUT_TOUCH_* defines, as ints."""
    d, b, _w, _h = BOARDS[board]
    src = (ROOT / "firmware" / d / "boards" / b / "mpconfigboard.h").read_text()
    out = {}
    for name, val in re.findall(r"#define MOY_INPUT_TOUCH_(\w+)\s+\(?(0x[0-9A-Fa-f]+|\d+)\)?",
                                src):
        out[name] = int(val, 0)
    return out


class _Bus:
    """Every controller's reads, answering one report: a GT911's status and
    point (by register), the GSL3680's eight bytes at 0x80, the AXS15231's
    eight after its command."""

    def __init__(self, report, gt911_status=None):
        self.report = report
        self.status = gt911_status

    def readfrom_mem(self, _a, reg, n, addrsize=8):
        if reg == 0x814E:
            return bytes([self.status])
        return self.report[:n]

    def readfrom(self, _a, n):
        return self.report[:n]

    def writeto(self, _a, _data):
        pass


def _report(kind, x, y, yx=False):
    if kind == mi.GT911:
        a, b = (y, x) if yx else (x, y)
        return bytes((a & 0xFF, a >> 8, b & 0xFF, b >> 8))
    if kind == mi.GSL3680:
        return bytes((1, 0, 0, 0, y & 0xFF, (y >> 8) & 0x0F, x & 0xFF, (x >> 8) & 0x0F))
    return bytes((0, 1, (x >> 8) & 0x0F, x & 0xFF, (y >> 8) & 0x0F, y & 0xFF, 0, 0))


def _point(board, raw_x, raw_y, **over):
    k = knobs(board)
    k.update(over)
    _d, _b, w, h = BOARDS[board]
    w = over.pop("W", w)
    h = over.pop("H", h)
    kind = k["KIND"]
    yx = bool(k.get("YX", 0))
    bus = _Bus(_report(kind, raw_x, raw_y, yx), gt911_status=0x81)
    t = mi.TouchDriver(bus, kind, w, h, addr=k.get("ADDR", 0),
                       swap_xy=bool(k["SWAP"]), flip_x=bool(k["FLIP_X"]),
                       flip_y=bool(k["FLIP_Y"]), raw_w=k.get("RAW_W", 0),
                       raw_h=k.get("RAW_H", 0), raw_x0=k.get("RAW_X0", 0),
                       raw_y0=k.get("RAW_Y0", 0), yx=yx,
                       clear_first=bool(k.get("CLEAR_FIRST", 0)),
                       extrapolate=bool(k.get("EXTRAPOLATE", 0)))
    t.probe()
    t.available = True                  # a GSL3680 is up once its upload signs
    t.pass_()
    x, y, _edge = t.poll(0)
    return x, y


# -- the Guition S3's AXS15231: swap + flip_x, landscape 480x320 -----------------

def test_the_guition_s3s_baked_knobs():
    k = knobs("guition_s3")
    assert (k["SWAP"], k["FLIP_X"], k["FLIP_Y"]) == (1, 1, 0)
    assert k["EXTRAPOLATE"] == 1 and k["HOLD_MS"] == 90


@pytest.mark.parametrize("raw, glass", [
    ((0, 0), (479, 0)),            # portrait top-left -> landscape top-right
    ((319, 0), (479, 319)),
    ((0, 479), (0, 0)),
    ((319, 479), (0, 319)),
    ((160, 240), (239, 160)),      # the centre
])
def test_axs_maps_its_corners_and_centre(raw, glass):
    assert _point("guition_s3", *raw) == glass


@pytest.mark.parametrize("raw, glass", [
    ((350, 240), (239, 319)),      # past the 320 axis -> clamped on y
    ((160, 500), (0, 160)),        # past the 480 axis -> flipped negative -> 0
    ((4095, 4095), (0, 319)),      # a 12-bit maximum on both
])
def test_axs_clamps_an_off_glass_press_at_both_ends(raw, glass):
    assert _point("guition_s3", *raw) == glass


# -- the Guition P4's GSL3680: a firmware space scaled onto 1280x800 --------------

@pytest.mark.parametrize("raw, glass", [
    ((10, 21), (0, 0)),                    # the fitted origin
    ((1649, 885), (1279, 799)),            # the fitted far corner
    ((92, 86), (64, 60)),                  # the calibration's top-left target
    ((835, 462), (643, 407)),              # its centre target
])
def test_gsl_scales_the_firmware_space_onto_the_glass(raw, glass):
    assert _point("guition_p4", *raw) == glass


@pytest.mark.parametrize("raw, glass", [
    ((0, 0), (0, 0)),                      # short of the origin -> negative -> 0
    ((1700, 900), (1279, 799)),            # past the span -> clamped
])
def test_gsl_clamps_past_the_fitted_span_at_both_ends(raw, glass):
    assert _point("guition_p4", *raw) == glass


def test_gsl_scales_BEFORE_it_flips():
    """The one order that is observable: with a span that does not divide the
    glass, flipping and then scaling rounds differently from scaling and then
    flipping. The driver scales first."""
    assert _point("guition_p4", 2, 0, W=5, H=5, RAW_W=7, RAW_H=7, RAW_X0=0, RAW_Y0=0,
                  FLIP_X=1) == (3, 0)


def test_gsl_swap_and_flips_apply_after_the_scale():
    base = dict(RAW_W=1664, RAW_H=896, RAW_X0=0, RAW_Y0=0)
    assert _point("guition_p4", 1248, 17, FLIP_X=1, FLIP_Y=1, **base) \
        == (1279 - 960, 799 - 15)
    assert _point("guition_p4", 100, 700, SWAP=1, **base) \
        == (700 * 1280 // 1664, 100 * 800 // 896)


# -- the T-Deck's GT911: a 320x240 controller space, y inverted -------------------

def test_the_tdecks_baked_knobs():
    k = knobs("tdeck")
    assert (k["SWAP"], k["FLIP_X"], k["FLIP_Y"], k["RAW_W"], k["RAW_H"], k["YX"]) \
        == (0, 0, 1, 320, 240, 1)


@pytest.mark.parametrize("raw, glass", [
    ((0, 0), (0, 239)),
    ((319, 0), (319, 239)),
    ((0, 239), (0, 0)),
    ((319, 239), (319, 0)),
    ((160, 120), (160, 119)),
])
def test_tdeck_maps_its_corners_and_centre(raw, glass):
    assert _point("tdeck", *raw) == glass


@pytest.mark.parametrize("raw, glass", [
    ((330, 120), (319, 119)),       # past the x span
    ((160, 250), (160, 0)),         # past the y span -> flipped negative -> 0
    ((65535, 65535), (319, 0)),     # a 16-bit maximum on both
])
def test_tdeck_clamps_an_off_glass_press_at_both_ends(raw, glass):
    assert _point("tdeck", *raw) == glass


# -- the Waveshare P4's GT911: native 1024x600, mounted 180 degrees ---------------

def test_the_p4s_baked_knobs():
    k = knobs("p4")
    assert (k["SWAP"], k["FLIP_X"], k["FLIP_Y"], k["ADDR"], k["CLEAR_FIRST"]) \
        == (0, 1, 1, 0x5D, 1)


@pytest.mark.parametrize("raw, glass", [
    ((0, 0), (1023, 599)),          # the 180-degree mount: TL reads as BR
    ((1023, 0), (0, 599)),
    ((0, 599), (1023, 0)),
    ((1023, 599), (0, 0)),
    ((512, 300), (511, 299)),
])
def test_p4_maps_its_corners_and_centre(raw, glass):
    assert _point("p4", *raw) == glass


@pytest.mark.parametrize("raw, glass", [
    ((1030, 300), (0, 299)),
    ((512, 610), (511, 0)),
])
def test_p4_clamps_an_off_glass_press_at_both_ends(raw, glass):
    assert _point("p4", *raw) == glass


def test_every_board_names_a_controller_the_kernel_drives():
    for board in BOARDS:
        assert knobs(board)["KIND"] in (mi.GT911, mi.GSL3680, mi.AXS), board
