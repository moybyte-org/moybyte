"""A host canvas's CANVAS row is the canvas's (docs/kernel_toolkit_2026-10.md
§3.1): its draw state, its colour table and its op counters are kept in step
on every canvas, gated or not, so a native/moy_ui primitive drawing through
the row draws with the clip, camera and palette the canvas has.

The row is read back through the glass's C by its handle, not through the
canvas's own view of it, so a canvas that kept a private copy would fail.
"""

import ctypes

from device import device_canvas as dc
from runtime import glass_binding
from runtime.host_canvas import make_system_canvas


def _row(cv):
    lib = glass_binding._lib()
    base = lib.moy_glass_host_canvas_row(cv._crow.h)
    st = (ctypes.c_int32 * dc._ST_LEN).from_address(
        base + lib.moy_glass_host_canvas_state_offset())
    pal = (ctypes.c_uint16 * 64).from_address(
        base + lib.moy_glass_host_canvas_pal_offset())
    return list(st), list(pal)


def test_the_row_follows_camera_clip_and_pal():
    cv = make_system_canvas(320, 240, font_scale=2)
    st, pal = _row(cv)
    assert st[dc._ST_W] == 320 and st[dc._ST_H] == 240
    assert st[dc._ST_FONT_SCALE] == 2
    assert (st[dc._ST_CX0], st[dc._ST_CY0], st[dc._ST_CX1], st[dc._ST_CY1]) == (
        0, 0, 320, 240), "a new canvas's row has an empty clip"
    assert pal == [cv._wire[i] for i in range(64)], "no palette in the row"

    cv.camera(7, -3)
    cv.clip(10, 20, 100, 50)
    cv.pal(8, 12)
    st, pal = _row(cv)
    assert (st[dc._ST_CAM_X], st[dc._ST_CAM_Y]) == (7, -3)
    assert (st[dc._ST_CX0], st[dc._ST_CY0], st[dc._ST_CX1], st[dc._ST_CY1]) == (
        10, 20, 110, 70)
    assert pal[8] == cv._wire[12] and pal[12] == cv._wire[12]

    cv.pal()
    cv.clip()
    cv.camera()
    st, pal = _row(cv)
    assert pal[8] == cv._wire[8]
    assert (st[dc._ST_CX1], st[dc._ST_CY1]) == (320, 240)

    cv.set_font_scale(3)
    assert _row(cv)[0][dc._ST_FONT_SCALE] == 3


def test_gate_counts_are_real_on_the_host():
    """One op is one count, as the boards' gates count it: rectb is one fill,
    print one text, whatever the font scale."""
    for fs in (1, 2):
        cv = make_system_canvas(64, 64, font_scale=fs)
        cv.gate_counts_reset()
        cv.rect(1, 1, 5, 5, 3)
        cv.rectb(1, 1, 5, 5, 4)
        cv.pix(2, 2, 5)
        cv.print("hi", 0, 0, 7)
        assert cv.gate_counts()[:2] == (3, 1)
        st, _pal = _row(cv)
        assert (st[dc._ST_N_FILL], st[dc._ST_N_TEXT]) == (3, 1)


def test_the_row_carries_the_fill_pattern_flag():
    """The native rect/rectb gates fill solid, so they decline while the row's
    ST_FILLP is set: it follows fillp(), the frame's reset_state and fillp(0)."""
    cv = make_system_canvas(64, 64)
    assert _row(cv)[0][dc._ST_FILLP] == 0
    cv.fillp(0x5A5A)
    assert _row(cv)[0][dc._ST_FILLP] == 1
    cv.fillp()
    assert _row(cv)[0][dc._ST_FILLP] == 0
    cv.fillp(0xA5A5, 3)
    assert _row(cv)[0][dc._ST_FILLP] == 1
    cv.reset_state()
    assert _row(cv)[0][dc._ST_FILLP] == 0
    cv.fillp(0)                          # an all-solid pattern is solid
    assert _row(cv)[0][dc._ST_FILLP] == 0
