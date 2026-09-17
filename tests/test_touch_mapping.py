"""The four touch drivers' raw -> glass mapping, pinned on sample points.

Every console board maps a controller's raw point onto its glass through the
same four steps -- swap the axes, scale the controller's space onto the
panel's, flip, clamp -- and each driver carried its own copy of that tail in
its own order with its own knob convention. These pins were written against
the copies BEFORE they were promoted into `gt911.map_point`, so the promotion
is provably neutral: corners, the centre, and a press past each axis, per
driver, with the knobs each board ships.

ONE DELIBERATE CHANGE, recorded here rather than hidden: the Waveshare P4's
`p4_input.Touch` clamped only the UPPER bound, and with both flips on (its
calibration) an off-glass raw coordinate flipped NEGATIVE and left the driver
as a point off the opposite edge. The shared body clamps both ends, as
`axs_touch` already did for the same reason; the last test in the P4 section
pins the shared behaviour.

The drivers' I2C halves are stubbed at the import boundary exactly the way
`tests/test_device_input.py` does it; nothing here is a transcription of a
mapping -- the real module is loaded and its `poll()` is what produces the
numbers.
"""

import contextlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEVICE = ROOT / "device"
P4_MODULES = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b" / "modules"


@contextlib.contextmanager
def _flat_device(machine=None):
    """Bind the frozen tree's flat names (`device_util`, `gt911`, `machine`)
    for one module load, and restore them afterwards."""
    from device import gt911 as real_gt911

    saved = {k: sys.modules.get(k, KeyError)
             for k in ("device_util", "gt911", "machine")}
    if not isinstance(saved["device_util"], types.ModuleType):
        spec = importlib.util.spec_from_file_location(
            "device_util", DEVICE / "device_util.py")
        du = importlib.util.module_from_spec(spec)
        sys.modules["device_util"] = du
        spec.loader.exec_module(du)
    sys.modules["gt911"] = real_gt911
    if machine is not None:
        sys.modules["machine"] = machine
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is KeyError:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _load(name, path, machine=None):
    with _flat_device(machine):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


# -- axs_touch (Guition JC3248W535): swap + flip_x, landscape 480x320 --------


class _AxsBus:
    def __init__(self, *frames):
        self.frames = list(frames)

    def writeto(self, addr, data):
        pass

    def readfrom(self, addr, n):
        return self.frames.pop(0) if self.frames else bytes(8)


def _axs_report(x, y):
    return bytes((0, 1, (x >> 8) & 0x0F, x & 0xFF, (y >> 8) & 0x0F, y & 0xFF,
                  0, 0))


def _axs_point(raw_x, raw_y):
    from device import axs_touch
    assert (axs_touch.SWAP_XY, axs_touch.FLIP_X, axs_touch.FLIP_Y) \
        == (True, True, False), "the Guition S3's baked knobs moved"
    t = axs_touch.Touch(w=480, h=320,
                        i2c=_AxsBus(bytes(8), _axs_report(raw_x, raw_y)))
    assert t.available
    x, y, _edge = t.poll()
    return x, y


@pytest.mark.parametrize("raw, glass", [
    ((0, 0), (479, 0)),            # portrait top-left -> landscape top-right
    ((319, 0), (479, 319)),
    ((0, 479), (0, 0)),
    ((319, 479), (0, 319)),
    ((160, 240), (239, 160)),      # the centre
])
def test_axs_maps_its_corners_and_centre(raw, glass):
    assert _axs_point(*raw) == glass


@pytest.mark.parametrize("raw, glass", [
    ((350, 240), (239, 319)),      # past the 320 axis -> clamped on y
    ((160, 500), (0, 160)),        # past the 480 axis -> flipped negative -> 0
    ((4095, 4095), (0, 319)),      # a 12-bit maximum on both
])
def test_axs_clamps_an_off_glass_press_at_both_ends(raw, glass):
    assert _axs_point(*raw) == glass


# -- gsl3680 (Guition JC8012P4A1C): a firmware space scaled onto 1280x800 ---


def _gsl_point(raw_x, raw_y, **knobs):
    from device.gsl3680 import Touch
    from device.gt911 import HeldPoint
    t = Touch.__new__(Touch)
    t.w, t.h = knobs.pop("w", 1280), knobs.pop("h", 800)
    for k in ("swap_xy", "flip_x", "flip_y"):
        setattr(t, k, knobs.pop(k, False))
    for k in ("raw_w", "raw_h", "raw_x0", "raw_y0"):
        setattr(t, k, knobs.pop(k, 0))
    assert not knobs, knobs
    t.available = True
    t.raw = None
    t.fingers = 0
    t._hp = HeldPoint()

    class _Chip:
        def read(self):
            return 1, raw_x, raw_y

    t._chip = _Chip()
    x, y, _edge = t.poll()
    return x, y


FIT = dict(raw_w=1640, raw_h=865, raw_x0=10, raw_y0=21)   # the board's fit


@pytest.mark.parametrize("raw, glass", [
    ((10, 21), (0, 0)),                    # the fitted origin
    ((1649, 885), (1279, 799)),            # the fitted far corner
    ((92, 86), (64, 60)),                  # the calibration's top-left target
    ((835, 462), (643, 407)),              # its centre target
])
def test_gsl_scales_the_firmware_space_onto_the_glass(raw, glass):
    assert _gsl_point(*raw, **FIT) == glass


@pytest.mark.parametrize("raw, glass", [
    ((0, 0), (0, 0)),                      # short of the origin -> negative -> 0
    ((1700, 900), (1279, 799)),            # past the span -> clamped
])
def test_gsl_clamps_past_the_fitted_span_at_both_ends(raw, glass):
    assert _gsl_point(*raw, **FIT) == glass


def test_gsl_scales_BEFORE_it_flips():
    """The one order that is observable: with a span that does not divide the
    glass, flipping the raw value and then scaling rounds differently from
    scaling and then flipping. The driver scales first."""
    assert _gsl_point(2, 0, w=5, h=5, raw_w=7, raw_h=7, flip_x=True) == (3, 0)


def test_gsl_swap_and_flips_apply_after_the_scale():
    assert _gsl_point(1248, 17, raw_w=1664, raw_h=896,
                      flip_x=True, flip_y=True) == (1279 - 960, 799 - 15)
    assert _gsl_point(100, 700, raw_w=1664, raw_h=896, swap_xy=True) \
        == (700 * 1280 // 1664, 100 * 800 // 896)


# -- device_input (T-Deck): a 320x240 controller space, y inverted ----------


def _tdeck_map(raw_x, raw_y, w=320, h=240):
    mod = _load("moybyte_test_tdeck_touch_map", DEVICE / "device_input.py")
    assert (mod.TOUCH_SWAP, mod.TOUCH_FLIP_X, mod.TOUCH_FLIP_Y,
            mod.TOUCH_RAW_W, mod.TOUCH_RAW_H) == (False, False, True, 320, 240), \
        "the T-Deck's baked knobs moved"
    t = mod.Touch.__new__(mod.Touch)
    t.w, t.h = w, h
    return t._map(raw_x, raw_y)


@pytest.mark.parametrize("raw, glass", [
    ((0, 0), (0, 239)),
    ((319, 0), (319, 239)),
    ((0, 239), (0, 0)),
    ((319, 239), (319, 0)),
    ((160, 120), (160, 119)),
])
def test_tdeck_maps_its_corners_and_centre(raw, glass):
    assert _tdeck_map(*raw) == glass


@pytest.mark.parametrize("raw, glass", [
    ((330, 120), (319, 119)),       # past the x span
    ((160, 250), (160, 0)),         # past the y span -> flipped negative -> 0
    ((65535, 65535), (319, 0)),     # a 16-bit maximum on both
])
def test_tdeck_clamps_an_off_glass_press_at_both_ends(raw, glass):
    assert _tdeck_map(*raw) == glass


# -- p4_input (Waveshare 7B): a self-configured GT911, both axes flipped -----


class _P4Bus:
    """A GT911 reporting x(lo,hi) y(lo,hi) -- the Waveshare part's order."""

    def __init__(self, x, y):
        self.point = bytes((x & 0xFF, x >> 8, y & 0xFF, y >> 8))

    def readfrom_mem(self, addr, reg, n, addrsize=8):
        if reg == 0x814E:
            return b"\x81"
        return self.point[:n]

    def writeto_mem(self, addr, reg, buf, addrsize=8):
        pass


def _p4_point(raw_x, raw_y):
    bus = _P4Bus(raw_x, raw_y)
    machine = types.ModuleType("machine")
    machine.Pin = lambda *a, **k: None
    machine.I2C = lambda *a, **k: bus
    with _flat_device(machine):
        spec = importlib.util.spec_from_file_location(
            "moybyte_test_p4_touch_map", P4_MODULES / "p4_input.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert (mod.SWAP_XY, mod.FLIP_X, mod.FLIP_Y) == (False, True, True), \
            "the Waveshare's baked knobs moved"
        t = mod.Touch()             # `machine` is imported inside __init__
    assert t.available
    x, y, _edge = t.poll()
    return x, y


@pytest.mark.parametrize("raw, glass", [
    ((0, 0), (1023, 599)),          # the 180-degree mount: TL reads as BR
    ((1023, 0), (0, 599)),
    ((0, 599), (1023, 0)),
    ((1023, 599), (0, 0)),
    ((512, 300), (511, 299)),
])
def test_p4_maps_its_corners_and_centre(raw, glass):
    assert _p4_point(*raw) == glass


@pytest.mark.parametrize("raw, glass", [
    ((1030, 300), (0, 299)),
    ((512, 610), (511, 0)),
])
def test_p4_clamps_an_off_glass_press_at_both_ends(raw, glass):
    """THE DELIBERATE CHANGE. With both flips on, a raw coordinate past the
    panel flips negative; the driver used to clamp only the upper bound and
    handed that negative point on, so an off-glass press landed on the far
    edge. The shared mapping clamps both ends, as axs_touch always did."""
    assert _p4_point(*raw) == glass


# -- the routing: one mapping, four takers ------------------------------------


@pytest.mark.parametrize("path", [
    DEVICE / "axs_touch.py", DEVICE / "gsl3680.py", DEVICE / "device_input.py",
    P4_MODULES / "p4_input.py"])
def test_every_touch_driver_maps_through_the_shared_body(path):
    """Routing only (the bodies are executed above): a driver that grows its
    own swap/flip/clamp tail again is a fifth copy of the one that drifted."""
    src = path.read_text(encoding="utf-8")
    assert "map_point(" in src, path
    assert "self.w - 1 - x" not in src and "self.h - 1 - y" not in src, path


def test_map_point_scales_then_flips_then_clamps_both_ends():
    from device.gt911 import map_point
    assert map_point(2, 0, 5, 5, False, True, False, 7, 7) == (3, 0)
    assert map_point(30, 30, 10, 10, True, True, True) == (0, 0)
    assert map_point(-3, 12, 10, 10, False, False, False) == (0, 9)
    assert map_point(92, 86, 1280, 800, False, False, False,
                     1640, 865, 10, 21) == (64, 60)
