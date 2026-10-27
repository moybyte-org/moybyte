"""The P4 canvas's DMA lanes: a big rect or the middle of a big rrect goes to
the backend's fill (`moy_ppa.fill`), and a full-screen window's stamp to its
row copy (`moy_ppa.copy`), with the pixels and counts the CPU path gives.

The host has no PPA, so a fake one stands in: its fill writes the colour word
and its copy moves the bytes, which is what the engine does (the real one was
held to the CPU path on P4 glass, 2026-10-27: 2304 rrect shapes over clips and
cameras, 0 differing pixels, equal counts). What these pin is the routing --
which ops go to the engine, where the kernel draws round them -- and that the
lane is absent wherever the backend has no fill. The rect gate's decline
(`ST_DMA_MIN`) runs on the desktop MicroPython's real gates
(tests/unix_mp.py)."""

import importlib.util as ilu
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from unix_mp import require_unix_mp

ROOT = Path(__file__).resolve().parent.parent
DEVICE = ROOT / "device"


class FakePpa:
    def __init__(self, fill=True, copy=True):
        self.takes_fill = fill
        self.takes_copy = copy
        self.fills = []
        self.copies = []

    def fill(self, dst, dw, dh, x, y, w, h, col):
        self.fills.append((x, y, w, h))
        if not self.takes_fill:
            return False
        word = struct.pack("<H", col & 0xFFFF)
        mv = memoryview(dst)
        for row in range(y, y + h):
            o = 2 * (row * dw + x)
            mv[o:o + 2 * w] = word * w
        return True

    def copy(self, dst, src, off, n, src_off=None):
        src_off = off if src_off is None else src_off
        self.copies.append((off, n, src_off))
        if not self.takes_copy:
            return False
        memoryview(dst)[off:off + n] = memoryview(src)[src_off:src_off + n]
        return True


@pytest.fixture
def p4():
    from runtime import host_canvas
    host_canvas.install()
    spec = ilu.spec_from_file_location("p4_canvas_dma", DEVICE / "p4_canvas.py")
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    comp = host_canvas.HostCompositor(320, 200)
    comp.rotated = True
    yield mod, comp
    mod.P4SystemCanvas._ppa = None


def _canvas(mod, comp, ppa):
    mod.P4SystemCanvas._ppa = ppa
    cv = mod.P4SystemCanvas(comp, font_scale=1)
    lay = cv.new_layer(320, 200)
    pat = bytes((i * 7 + (i >> 9)) & 255 for i in range(2 * 320 * 200))
    lay._buf[:] = pat
    return lay


def _shapes():
    for w, h in ((300, 180), (129, 129), (60, 40)):
        for r in (0, 3, 9):
            for ring, line, lo in ((None, 1, None), (6, 1, None), (6, 3, 9)):
                for corners, stepped, mask in ((15, False, 0), (5, True, 1), (15, False, 2)):
                    yield (w, h, r, ring, line, lo, corners, stepped, mask)


def test_a_big_solid_rrect_fills_its_middle_on_the_engine_and_draws_the_same(p4):
    mod, comp = p4
    clips = (None, (40, 30, 200, 120), (0, 0, 320, 25))
    took = 0
    for shape in _shapes():
        w, h, r, ring, line, lo, corners, stepped, mask = shape
        for clip in clips:
            for cam in ((0, 0), (5, -7)):
                out = []
                for ppa in (None, FakePpa()):
                    lay = _canvas(mod, comp, ppa)
                    lay.camera(*cam)
                    if clip:
                        lay.clip(*clip)
                    n = lay.rrect(11, 13, w, h, r, 12, ring, line, lo, corners, stepped, mask)
                    out.append((n, bytes(lay._buf)))
                    if ppa is not None:
                        took += len(ppa.fills)
                        if mask == 2 or w * h < 16384:
                            assert ppa.fills == [], shape
                    lay.release()
                assert out[0] == out[1], (shape, clip, cam)
    assert took, "no shape reached the engine: the lane is untested"


def test_a_big_rect_goes_to_the_engine_a_small_one_and_a_refusal_to_the_cpu(p4):
    mod, comp = p4
    for takes in (True, False):
        out = []
        for ppa in (None, FakePpa(fill=takes)):
            lay = _canvas(mod, comp, ppa)
            lay.camera(-3, 4)
            lay.clip(10, 5, 300, 180)
            lay.rect(0, 0, 250, 150, 9)          # big: the engine, clipped
            lay.rect(20, 20, 30, 30, 14)         # small: the CPU
            out.append(bytes(lay._buf))
            if ppa is not None:
                assert len(ppa.fills) == 1 and ppa.fills[0][2] * ppa.fills[0][3] >= 16384
            lay.release()
        assert out[0] == out[1]


def test_a_full_width_stamp_or_band_is_one_engine_copy(p4):
    mod, comp = p4
    for takes in (True, False):
        out = []
        for ppa in (None, FakePpa(copy=takes)):
            mod.P4SystemCanvas._ppa = ppa
            cv = mod.P4SystemCanvas(comp, font_scale=1)
            cv.cls(0)
            win = cv.new_layer(320, 150)
            win._buf[:] = bytes((i * 13) & 255 for i in range(len(win._buf)))
            cv.blit_strip(win, 0, 0)
            cv.blit_strip(win, 0, 70)            # lower: its rows on the glass
            cv.blit_strip(win, 2, 0)             # not at the left edge: the CPU
            narrow = cv.new_layer(300, 150)
            narrow._buf[:] = win._buf[:len(narrow._buf)]
            cv.blit_strip(narrow, 0, 0)          # not full width: the CPU
            band = cv.new_layer(320, 120)
            band.blit_strip(cv, 0, -50)          # a band of the glass taken
            out.append(bytes(cv._buf) + bytes(band._buf))
            if ppa is not None:
                row = 2 * 320
                assert ppa.copies == [(0, row * 150, 0), (row * 70, row * 130, 0),
                                      (0, row * 120, row * 50)]
            win.release()
            narrow.release()
            band.release()
        assert out[0] == out[1]


def test_no_fill_hook_no_lane():
    """A canvas whose backend has no DMA fill (every tier but the P4s) draws
    exactly as before and never asks the gate to decline."""
    from runtime import host_canvas
    from device.device_canvas import _ST_DMA_MIN
    host_canvas.install()
    cv = host_canvas.make_canvas(64, 32)
    assert cv._ppa_fill is None and cv._gate_state[_ST_DMA_MIN] == 0


DRIVER = r'''
import sys
sys.path.insert(0, @RUNTIME@)
sys.path.insert(0, @MODULES@)
sys.path.insert(0, @STAGE@)

import moy_gfx
import device_canvas


class FakeComp:
    def __init__(self, w, h):
        self._w = w
        self._h = h
        self._buf = bytearray(w * h * 2)

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._buf

    def gfx(self):
        return moy_gfx


class Dma(device_canvas.DeviceCanvas):
    asked = []

    def ppa_fill(self, x, y, w, h, col):
        Dma.asked.append((x, y, w, h))
        return False


plain = device_canvas.DeviceCanvas(FakeComp(320, 240))
dma = Dma(FakeComp(320, 240))
assert plain._gate_pal is not None and dma._gate_pal is not None
for cv in (plain, dma):
    cv.rect(0, 0, 200, 100, 9)
    cv.rect(10, 10, 20, 20, 3)
    cv.rect(0, 120, 300, 100, 5)
assert Dma.asked == [(0, 0, 200, 100), (0, 120, 300, 100)], Dma.asked
assert bytes(plain._buf) == bytes(dma._buf)
print("OK dma gate")
'''


def test_the_native_rect_gate_hands_a_big_block_to_the_canvas(tmp_path):
    exe = require_unix_mp(
        "moy_gfx",
        why="Without it nothing checks that the native rect gate declines a "
            "block of ST_DMA_MIN pixels or more to the canvas's DMA fill.")
    stage = tmp_path / "stage"
    stage.mkdir()
    shutil.copy(ROOT / "runtime" / "font.py", stage / "moy_font.py")
    shutil.copy(ROOT / "runtime" / "moy_image.py", stage / "moy_image.py")
    shutil.copy(ROOT / "runtime" / "moy_fs.py", stage / "moy_fs.py")
    src = (DRIVER
           .replace("@STAGE@", repr(str(stage)))
           .replace("@MODULES@", repr(str(DEVICE)))
           .replace("@RUNTIME@", repr(str(ROOT / "runtime"))))
    script = tmp_path / "driver.py"
    script.write_text(src)
    out = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=120)
    assert "OK dma gate" in out.stdout, out.stdout + out.stderr
