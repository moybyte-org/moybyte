"""The kernel's chrome (native/moy_play/moy_chrome.c): one body per piece,
drawn two ways -- replayed through the shell's canvas, and rasterised by the
kernel into a frame it draws with no VM -- and the two must agree pixel for
pixel. The shell's goldens hold the replay to the pixels the Python bodies
drew before they crossed; this holds the raster to the replay."""

import ctypes

import pytest

from runtime import chrome_ops as ui, moy_play
from runtime.chrome import NAMES
from ws_helpers import build_ws


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    try:
        moy_play._clib()
    except RuntimeError:
        pytest.skip("no host Player (a C compiler builds it)")
    w = build_ws(tmp_path_factory.mktemp("chrome"))
    from runtime.console_notices import register_glyphs
    register_glyphs()
    return w


def _both(ws, piece, *args):
    """The canvas after a replay, and a raw buffer after the raster, from the
    same cleared start."""
    cv = ws.canvas
    cv.cls(0)
    mp = ui.chrome_inks(ws.theme_colors, NAMES)
    ops = getattr(mp, piece)(*args)
    ui.replay(ops, cv, ws)
    got = bytes(cv._buf)
    pal = [cv._wire[cv._pal_map[i]] for i in range(64)]
    raw = (ctypes.c_uint16 * (cv.w * cv.h))(*([pal[0]] * (cv.w * cv.h)))
    getattr(mp, piece)(*args, raster=(raw, cv.w, cv.h, pal, getattr(cv, "font_scale", 1)))
    return ops, got, bytes(raw)


@pytest.mark.parametrize("held", [0, 1, 350, 700, 2000])
def test_the_pill_rasters_as_the_canvas_draws_it(ws, held):
    ops, canvas, raster = _both(ws, "chrome_pill", 320, 240, held, 700)
    assert ops and canvas == raster


@pytest.mark.parametrize("notice,text", [
    (False, "cart:12: attempt to index a nil value (global 'player')"),
    (True, "This cart needs 5.2 MB and this console has 3.1 MB free."),
    (False, "a-very-long-token-that-must-be-hard-split-across-the-panel-lines "
            "and\nsecond paragraph   with  gaps"),
])
def test_the_panel_rasters_as_the_canvas_draws_it(ws, notice, text):
    ops, canvas, raster = _both(ws, "chrome_panel", 320, 240, notice, "Too big.", text,
                                False)
    assert ops and canvas == raster


def test_the_toast_and_banner_raster_as_the_canvas_draws_them(ws):
    ops, canvas, raster = _both(ws, "chrome_toast", "Little Artist", "paint")
    assert ops and canvas == raster
    ops, canvas, raster = _both(ws, "chrome_banner", 320, 1, 18, "MOYBYTE UPDATED",
                                "now 0.9.3", True)
    assert ops and canvas == raster


def test_a_piece_is_cut_rather_than_overrun():
    """A list that outgrows its slots stops; it never writes past them."""
    try:
        moy_play._clib()
    except RuntimeError:
        pytest.skip("no host Player")
    rows = [(0, "ROW %d" % i) for i in range(16)]
    ops = moy_play.chrome_menu(rows, 3, 0, 18, 120, 200, 1, 1)
    assert 0 < len(ops) <= 128
