"""The Sprites tab as the glass shows it, under every seed theme and mode, on
the shell the goldens build: the marks a design review reads off a render
(the selection, the sprite map's current sprite, the grid's cursor), taken
here off the same frame (`tests/test_editor_look.py`'s fixtures)."""

import pytest

import test_editor_look as el
from test_editor_look import DESK, HANDHELD, THEMES

_uninstall = el._uninstall            # the theme and input put back after each


def _rgb8(p):
    """A `_px` pixel (5/6/5 bits) as 8-bit RGB."""
    return (p[0] * 255 // 31, p[1] * 255 // 63, p[2] * 255 // 31)


def _contrast(a, b):
    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def lum(c):
        return 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2])
    hi, lo = sorted((lum(_rgb8(a)), lum(_rgb8(b))), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _open(tmp_path, config, theme, mode):
    ws = el._ws(tmp_path, config, theme, mode)
    lay, ox, oy = el._sprites(ws, config)
    return ws, lay, ox, oy


@pytest.mark.parametrize("config", (HANDHELD, DESK))
@pytest.mark.parametrize("theme,mode", THEMES)
def test_the_selection_reads_against_the_grid_lines(tmp_path, config, theme, mode):
    """The selection is a two-toned ring, `ink` on and outside its grid
    lines and `surface` within: one of the two stands at 3:1 or more off
    the grid's lines in every theme-mode (design review R4-02; Contrast
    Night's lines are its ink, Moy Day's and Blocks' a hair from white)."""
    ws, lay, ox, oy = _open(tmp_path, config, theme, mode)
    ws.paint.set_selection(1, 1, 4, 4)
    el._frame(ws)
    px = el._px(ws)
    cv = ws.glass_canvas
    border = el._rgb(cv, ws.theme_colors["border"])
    cell = lay.pg_span // ws.paint.dim
    x0 = ox + lay.pg_x0 + cell          # the selection's first column
    ys = range(oy + lay.pg_y0 + 2 * cell, oy + lay.pg_y0 + 4 * cell)
    outer = {px[y][x0 - 1] for y in ys}
    inner = {px[y][x0] for y in ys}
    assert outer == {el._rgb(cv, ws.theme_colors["ink"])}, outer
    assert inner == {el._rgb(cv, ws.theme_colors["surface"])}, inner
    best = max(_contrast(c, border) for c in outer | inner)
    assert best >= 3, (theme, mode, best)
