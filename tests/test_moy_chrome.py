"""The kernel's chrome (native/moy_play/moy_chrome.c): one body per piece,
drawn two ways -- replayed through the shell's canvas, and rasterised by the
kernel into a frame it draws with no VM -- and the two must agree pixel for
pixel. The shell's goldens hold the replay to the pixels the Python bodies
drew before they crossed; this holds the raster to the replay."""

import ctypes

import pytest

from runtime import chrome_ops as ui, moy_play
from runtime import ui_draw as toolkit
from runtime.chrome import NAMES
from ws_helpers import build_ws, use_theme


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    try:
        moy_play._clib()
    except RuntimeError:
        pytest.skip("no host Player (a C compiler builds it)")
    # a palette theme (the `night` fixture): the raster's palette here is MOY64's
    ws = build_ws(tmp_path_factory.mktemp("chrome"))
    use_theme(ws, "night", "dark")
    return ws


def _both(ws, piece, *args):
    """The canvas after a replay, and a raw buffer after the raster, from the
    same cleared start."""
    cv = ws.canvas
    cv.cls(0)
    mp = ui.chrome_inks(ws.theme_colors, NAMES)
    ops = getattr(mp, piece)(*args)
    # The raster draws petme128, the kernel's own font, with no VM and no
    # theme; the replay prints in the shell's print font, so it is compared
    # in petme128.
    was = toolkit.print_font(0)
    ui.replay(ops, cv, ws)
    toolkit.print_font(was)
    got = bytes(cv._buf)
    pal = [cv._wire[cv._pal_map[i]] for i in range(64)]
    raw = (ctypes.c_uint16 * (cv.w * cv.h))(*([pal[0]] * (cv.w * cv.h)))
    getattr(mp, piece)(*args, raster=(raw, cv.w, cv.h, pal, getattr(cv, "font_scale", 1)))
    return ops, got, bytes(raw)


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


def test_a_piece_is_cut_rather_than_overrun():
    """A list that outgrows its slots stops; it never writes past them."""
    try:
        moy_play._clib()
    except RuntimeError:
        pytest.skip("no host Player")
    rows = [(0, "ROW %d" % i) for i in range(16)]
    ops = moy_play.chrome_menu(rows, 3, 0, 18, 120, 200, 1, 1)
    assert 0 < len(ops) <= 128


def test_a_minimal_bars_title_is_in_the_chrome_ink(ws):
    """The title the tool/app bar prints reads in `chrome_ink`, the role the
    other bars' titles use: the dim ink is pale under an RGB theme's light
    bar, where it all but vanished."""
    th = dict(ws.theme_colors)
    th["chrome_ink"], th["chrome_ink_dim"] = 11, 22
    mp = ui.chrome_inks(th, NAMES)
    ops = mp.chrome_strip_cell("title", (40, 0, 160, 18), 1, 0, "Appearance", False)
    inks = {c for op, c, sc, x, y, w, h, t in ops if op == 3}
    assert inks == {11}


def test_the_strip_draws_in_the_inks_the_theme_names(ws):
    """A role may be an index of the shell's block (64-127: a theme's RGB
    colour), and the strip's cells draw in exactly the role they name -- the
    title, the clock and the menu glyph the bar's bright ink (the clock's
    since docs/look_2026-10.md step 27a, the title's beside it) -- for
    every theme and mode. (The inks were masked to 6 bits, so Moy's,
    Blocks' and Contrast's Calc bar on a handheld drew another colour.)"""
    placed = 0
    for name in ws.look.theme_names():
        for variant in ("dark", "light"):
            ws.look.set_theme(name, persist=False, variant=variant)
            th = ws.theme_colors
            mp = ui.chrome_inks(th, NAMES)
            for kind, s, role in (("title", "Calc", "chrome_ink"),
                                  ("clock", "14:32", "chrome_ink")):
                ops = mp.chrome_strip_cell(kind, (30, 0, 120, 18), 1, 0, s, False)
                assert ops and {op[1] for op in ops} == {th[role]}, (name, variant, kind)
            ops = mp.chrome_strip_cell("menu", (0, 0, 28, 18), 1, 0, None, False)
            assert {op[1] for op in ops} == {th["chrome_ink"]}, (name, variant, "menu")
            placed += th["chrome_ink_dim"] >= 64
    assert placed, "no theme here places its ink in the shell's block"


def test_a_run_the_kernel_drives_shows_the_banners_until_their_deadlines(ws):
    """The kernel's copies of the notice and the unlock banner draw over a
    run's frame in the live look (moy_ui_unlock) until each deadline, and
    nothing after."""
    cv = ws.canvas
    w, h = cv.w, cv.h
    pal = [cv._wire[cv._pal_map[i]] for i in range(64)]
    now = 1000000
    # The kernel's banners are process state: a notice an earlier test armed
    # on the real clock may still be up at this made-up `now`.
    moy_play.chrome_notice("", "", True, now)
    ui.chrome_inks(ws.theme_colors, NAMES)
    moy_play.chrome_toast_arm("You found a badge!", "Map Maker", "map", False, now + 2600)
    raw = (ctypes.c_uint16 * (w * h))()
    assert moy_play.chrome_overlay_draw(raw, w, h, pal, now)
    top = [i for i in range(w * h) if raw[i]]
    assert top and max(top) // w < 70, "the banner stands at the top of the frame"
    raw = (ctypes.c_uint16 * (w * h))()
    assert not moy_play.chrome_overlay_draw(raw, w, h, pal, now + 2601)
    assert not any(raw)
