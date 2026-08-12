"""The host raster, bound to libmoy instead of transcribed from it (rung 5).

`runtime/canvas.py` re-implements in Python the raster libmoy implements in C.
The conformance goldens keep the two in agreement, which works -- and is
exactly the "parallel implementations held together by a pin" shape the
zero-duplication directive exists to end. `runtime/raster_binding.py` is the
alternative: libmoy's own C, compiled indexed so it draws straight into the
bytearray `Canvas.buf` already is, reached from CPython by ctypes.

This test makes the claim checkable BEFORE anything is swapped: the spec's own
recorded traces are replayed through the binding and hashed against the same
golden frames `test_spec_conformance.py` holds the Python raster to. Passing
means a host canvas backed by libmoy would draw pixel-identical frames -- which
is what makes moving canvas.py's interior a mechanical change rather than a
leap.

It reuses that module's replayer verbatim. Writing a second one here would be
the same mistake in miniature: two replayers to disagree about what a trace
means.

Skipped where there is no C compiler, like the audio binding it copies.
"""

import hashlib

import pytest

from runtime import raster_binding
from tests import test_spec_conformance as conf


class _CanvasFacing:
    """moybyte's canvas signatures over the cart-facing binding.

    The trace replayer speaks `spr_tile(sheet, ...)` / `map(tilemap, sheet,
    ...)` because that is what a moybyte Canvas takes; libmoy's console holds
    its sheet and map instead, so the assets are registered once here and
    dropped from each call. Everything else forwards untouched.
    """

    def __init__(self, native):
        self._n = native

    def spr_tile(self, sheet, n, x, y, colorkey=-1, scale=1, flip=0):
        self._n.spr(n, x, y, colorkey, scale, flip)

    def sspr(self, sheet, sx, sy, sw, sh, dx, dy, dw=None, dh=None,
             colorkey=-1, flip=0):
        self._n.sspr(sx, sy, sw, sh, dx, dy, dw, dh, colorkey, flip)

    def map(self, tilemap, sheet, mx=0, my=0, w=None, h=None, sx=0, sy=0,
            colorkey=-1, scale=1):
        self._n.map(mx, my, w, h, sx, sy, colorkey, scale)

    def tline(self, tilemap, sheet, x0, y0, x1, y1, u, v, du, dv, colorkey=-1):
        self._n.tline(x0, y0, x1, y1, u, v, du, dv, colorkey)

    def __getattr__(self, name):
        return getattr(self._n, name)


def _render(scene):
    import json
    import os
    with open(os.path.join(conf.HERE, "traces", scene + ".json")) as fh:
        calls = json.load(fh)
    sheet, tilemap = conf._build_assets(scene)
    native = raster_binding.NativeRaster(conf.W, conf.H)
    try:
        native.set_sheet(sheet)
        native.set_map(tilemap)
        conf.replay(calls, _CanvasFacing(native), sheet, tilemap)
        return bytes(native.buf)
    finally:
        native.close()


@pytest.mark.skipif(not raster_binding.NativeRaster.available(),
                    reason="no C compiler for the host raster binding")
@pytest.mark.parametrize("scene,core,golden", conf._scene_names(),
                         ids=[s[0] for s in conf._scene_names()])
def test_the_binding_is_pixel_identical_to_the_spec_golden(scene, core, golden):
    frame = _render(scene)
    assert len(frame) == conf.W * conf.H
    got = hashlib.sha256(frame).hexdigest()
    assert got == golden, (
        "%s (%s) differs from the golden the Python raster matches:\n"
        "  golden %s\n  got    %s" %
        (scene, "core" if core else "provisional", golden, got))


@pytest.mark.skipif(not raster_binding.NativeRaster.available(),
                    reason="no C compiler for the host raster binding")
def test_the_binding_draws_into_the_buffer_python_owns():
    """The reason the swap is cheap: an indexed libmoy pixel IS a palette
    index, so there is no conversion layer to keep correct -- every existing
    reader of `.buf` (to_rgb888, the editors, the GIF writer) sees what it saw
    before, byte for byte."""
    n = raster_binding.NativeRaster(8, 4)
    try:
        buf = n.buf
        n.cls(5)
        assert set(buf) == {5}, "the C did not write the caller's bytearray"
        n.pix(2, 1, 9)
        assert buf[1 * 8 + 2] == 9
        assert n.pix(2, 1) == 9, "the 2-arg read must see the same buffer"
    finally:
        n.close()
