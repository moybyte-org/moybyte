"""Tiny5, the default theme's UI font (docs/theming_2026-09.md §4.7; theming
phase 4 step 3).

* The committed font is what tools/make_tiny5_font.py builds from its
  committed glyph source, and that source names its pinned upstream.
* The tables embed the file and its licence text; `night` names it for `ui`
  and `title`, and `mono` stays petme128.
* The text-size floor: what it answers across the lineup, and the effective
  font scale it lifts.
* A theme switch installs the roles and the print font, rebuilds the layout,
  and frees what no role keeps: the slots never leak.
* The print font: a canvas's print draws in it; a freed font falls back.
* The pixel wrap.
* The game domain -- a Python cart, a Lua cart, a wallpaper cart -- draws
  petme128 whatever the theme names.
"""

import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEMES = os.path.join(ROOT, "native", "moy_ui", "themes")
sys.path.insert(0, ROOT)

from tools import make_tiny5_font as tool          # noqa: E402
from runtime import ui_draw                        # noqa: E402
from runtime import appearance                     # noqa: E402
from runtime import iconsets                       # noqa: E402
from runtime.chrome import text_scale_floor        # noqa: E402

SLOTS = 16      # moy_text.h's MOY_FONT_SLOTS


def _fnt():
    with open(os.path.join(THEMES, "tiny5.fnt"), "rb") as f:
        return f.read()


# -- the file -------------------------------------------------------------------

def test_the_committed_font_is_what_the_tool_builds():
    with open(tool.GLYPHS) as f:
        assert tool.build(f.read()) == _fnt(), (
            "native/moy_ui/themes/tiny5.fnt is not what tools/make_tiny5_font.py "
            "builds from tiny5.glyphs; run `python tools/make_tiny5_font.py`")


def test_the_glyph_source_names_its_pinned_upstream():
    with open(tool.GLYPHS) as f:
        head = f.read(600)
    assert tool.SOURCE in head and tool.SHA256 in head
    assert re.search(r"google/fonts/blob/[0-9a-f]{40}/ofl/tiny5/", tool.SOURCE)
    with open(os.path.join(ROOT, "THIRD_PARTY.md")) as f:
        notices = f.read()
    assert "Tiny5" in notices and tool.SOURCE in notices, (
        "THIRD_PARTY.md carries Tiny5's OFL notice and its source")


def test_the_font_covers_ascii_on_one_byte_columns():
    rc, fid = ui_draw.font_load(_fnt())
    assert rc == 0
    try:
        w, h, line, ascent, first, count = ui_draw.font_info(fid)
        assert (first, count) == (0x20, 95)          # ' ' .. '~'
        assert (w, h, line, ascent) == (6, 7, 8, 6)
        assert ui_draw.text_width(" ", 1, None) == 8      # petme128's cell, unchanged
    finally:
        ui_draw.font_free(fid)


def test_the_tables_embed_the_file_and_night_names_it():
    assert ui_draw.font_file("tiny5.fnt") == _fnt()
    assert ui_draw.font_file("nothing.fnt") is None
    assert dict(ui_draw.theme_fonts("night")) == {"ui": "tiny5.fnt", "title": "tiny5.fnt"}
    with open(os.path.join(THEMES, "night.json")) as f:
        assert "mono" not in json.load(f)["fonts"]


def test_every_seed_font_ships_its_licence_in_the_image():
    """As in a published theme package, `X.txt` beside a font `X.fnt` is
    its licence's full text; the tables embed it with the font, so every
    image that carries the font carries its licence."""
    seeds = [f for f in os.listdir(THEMES) if f.endswith(".json")]
    fonts = set()
    for name in seeds:
        with open(os.path.join(THEMES, name)) as f:
            fonts |= set(json.load(f).get("fonts", {}).values())
    # a face of the font set is the image's (runtime/fontset.py), not the tables'
    fonts = {f for f in fonts if os.path.isfile(os.path.join(THEMES, f))}
    assert fonts
    for fnt in fonts:
        with open(os.path.join(THEMES, fnt[:-len(".fnt")] + ".txt"), "rb") as f:
            text = f.read()
        assert ui_draw.font_file(fnt[:-len(".fnt")] + ".txt") == text
    assert b"SIL OPEN FONT LICENSE Version 1.1" in ui_draw.font_file("tiny5.txt")
    assert b"Copyright 2022-2024 The Tiny5 Project Authors" in ui_draw.font_file("tiny5.txt")


# -- the text-size floor -----------------------------------------------------------

@pytest.mark.parametrize("w,h,diag,tiny5", [
    (320, 240, None, 1),        # the T-Deck declares no glass
    (480, 320, 3.5, 1),         # the Guition S3: the floor is its 1x
    (1024, 600, 7.0, 1),        # the Waveshare P4
    (480, 320, 3.0, 2),         # denser glass than the lineup's
    (1280, 800, 10.1, 1),       # the Guition P4
])
def test_the_floor_lifts_tiny5_and_never_petme128(w, h, diag, tiny5):
    assert text_scale_floor(w, h, diag, 6) == tiny5
    assert text_scale_floor(w, h, diag, 7) == 1


# -- a theme switch ------------------------------------------------------------------

def _free_slots():
    sample = os.path.join(ROOT, "tests", "fixtures", "fonts", "sample6x9.fnt")
    with open(sample, "rb") as f:
        data = f.read()
    got = []
    while True:
        rc, fid = ui_draw.font_load(data)
        if fid is None:
            break
        got.append(fid)
    for fid in got:
        ui_draw.font_free(fid)
    return len(got)


def test_a_theme_switch_installs_frees_and_relayouts(tmp_path, monkeypatch):
    from ws_helpers import build_ws
    ws = build_ws(tmp_path)
    fid = ui_draw.font("ui")[0]
    assert fid != 0 and ui_draw.font("title")[0] == fid and ui_draw.font("mono")[0] == 0
    assert ui_draw.print_font() == fid
    assert ws.layout.font_w == 6 and ws.layout.line_h == 8
    for _ in range(3):
        for name in appearance.THEME_NAMES:
            ws.look.set_theme(name, persist=False)
            # moy's Code tab adds JetBrains Mono (docs/look_2026-10.md step 23)
            # and its Night, this workstation's mode, Inter's four faces and
            # Calc's three in place of Tiny5 (steps 26a and 22); blocks and
            # contrast the same with Atkinson's (26b, 26c)
            assert len(appearance._FONTS) == (
                8 if name in ("moy", "blocks", "contrast") else 1)
    ws.look.set_theme("night", persist=False)
    assert len(appearance._FONTS) == 1
    iconsets.reset()            # an icon set's masks hold font rows of their own
    assert _free_slots() == SLOTS - 1
    # A theme that names no font: every role and the print font on petme128,
    # nothing left loaded, the layout rebuilt on petme128's cell.
    real = ui_draw.theme_fonts
    monkeypatch.setattr(appearance._ui_draw, "theme_fonts",
                        lambda n: () if n == "slate" else real(n))
    ws.look.set_theme("slate", persist=False)
    assert [ui_draw.font(r)[0] for r in ("ui", "title", "mono", "caption")] == [0, 0, 0, 0]
    assert ui_draw.print_font() == 0 and appearance._FONTS == {}
    assert ws.layout.font_w == 8 and _free_slots() == SLOTS
    ws.look.set_theme("night", persist=False)
    assert ui_draw.font("ui")[0] != 0 and ws.layout.font_w == 6


def test_the_floor_lifts_the_effective_font_scale(tmp_path):
    """On a 3.0" 480x320, denser than any panel in the lineup, the `ui`
    font's ascent needs scale 2: the shell lays out and draws at 2 while the
    setting stays 1; a theme on petme128 drops it back."""
    from ws_helpers import build_ws
    ws = build_ws(tmp_path, sys_size=(480, 320), panel_diagonal_in=3.0)
    assert ui_draw.font("ui")[1:] == (6, 8, 6)
    assert ws.look.font_scale == 1 and ws.look.effective_font_scale() == 2
    assert ws.sys_canvas.font_scale == 2
    assert ws.layout.font_w == 12 and ws.layout.line_h == 16
    import runtime.appearance as A
    real = ui_draw.theme_fonts
    A._ui_draw.theme_fonts = lambda n: () if n == "slate" else real(n)
    try:
        ws.look.set_theme("slate", persist=False)
        assert ws.look.effective_font_scale() == 1 and ws.layout.font_w == 8
        assert ws.sys_canvas.font_scale == 1
    finally:
        A._ui_draw.theme_fonts = real
    ws.look.set_theme("night", persist=False)
    assert ws.look.effective_font_scale() == 2


# -- the print font -------------------------------------------------------------

def test_a_canvas_prints_in_the_print_font_and_a_freed_one_falls_back():
    rc, fid = ui_draw.font_load(_fnt())
    try:
        assert ui_draw.print_font(fid) == 0 and ui_draw.print_font() == fid
        assert ui_draw.print_font(0x7777) is None and ui_draw.print_font() == fid
        assert ui_draw.print_width("HELLO") == ui_draw._C.text_width("HELLO", 1, fid)
    finally:
        ui_draw.font_free(fid)
    assert ui_draw.print_font() == 0


def test_the_cart_boundary_puts_the_shell_fonts_back():
    rc, fid = ui_draw.font_load(_fnt())
    try:
        ui_draw.use_font("ui", fid)
        ui_draw.print_font(fid)
        assert ui_draw.cart_text(True) is False
        assert ui_draw.font("ui")[0] == 0 and ui_draw.print_font() == 0
        assert ui_draw.print_font(0) == fid          # names the font prints come back to
        ui_draw.print_font(fid)
        assert ui_draw.cart_text(False) is True
        assert ui_draw.font("ui")[0] == fid and ui_draw.print_font() == fid
    finally:
        ui_draw.font_free(fid)


# -- the pixel wrap ------------------------------------------------------------------

def test_the_pixel_wrap_fits_its_lines_and_is_the_cell_wrap_on_petme128():
    text = "Open or create a project, then PLAY it on every board"
    for cols in (6, 10, 17):
        assert ui_draw.wrap_px(text, cols * 8) == ui_draw.wrap_words(text, cols)
    rc, fid = ui_draw.font_load(_fnt())
    try:
        ui_draw.print_font(fid)
        for maxw in (30, 60, 97):
            lines = ui_draw.wrap_px(text, maxw)
            assert " ".join(lines) == text
            assert all(ui_draw.print_width(ln) <= maxw for ln in lines)
            for a, b in zip(lines, lines[1:]):      # greedy: the next word did not fit
                assert ui_draw.print_width(a + " " + b.split(" ")[0]) > maxw
        assert ui_draw.print_width(ui_draw.print_fit("SETTINGS", 20)) <= 20
    finally:
        ui_draw.print_font(0)
        ui_draw.font_free(fid)


# -- the game domain draws petme128 -------------------------------------------------

_PY = '''
seen = []
def _draw():
    cls(0)
    print("HELLO", 44, 101, 7)
    seen.append((ui_draw.font("ui")[0], ui_draw.print_font()))
'''

_LUA = '''function _draw()
  cls(0)
  print("HELLO", 44, 101, 7)
end
'''


_BOX = (40, 100, 48, 10)            # around HELLO at (44, 101)


def _box(cv):
    x, y, w, h = _BOX
    return [cv.pix(xx, yy) for yy in range(y, y + h) for xx in range(x, x + w)]


def _petme_hello(cv):
    """The reference: HELLO in petme128 on a cleared canvas."""
    was = ui_draw.print_font(0)
    cv.cls(0)
    cv.print("HELLO", 44, 101, 7)
    ui_draw.print_font(was)
    return _box(cv)


def _frame(ws):
    for _ in range(3):
        ws._dirty = True
        ws.frame(1 / 30)


@pytest.mark.parametrize("kind", ["game", "app"])
def test_a_python_cart_prints_and_measures_in_petme128(tmp_path, kind):
    from ws_helpers import build_ws_with_cart
    ws = build_ws_with_cart(tmp_path, "import ui_draw\n" + _PY, type=kind)
    assert ui_draw.font("ui")[0] != 0               # the shell's theme names Tiny5
    _frame(ws)
    ns = ws.player.ns
    assert ws.player.cart_error is None, ws.player.cart_error
    assert ns["seen"] and set(ns["seen"]) == {(0, 0)}
    got = _box(ws.canvas)
    assert ui_draw.font("ui")[0] != 0 and ui_draw.print_font() != 0   # and back after
    assert got == _petme_hello(ws.canvas)


def test_a_lua_cart_prints_in_petme128(tmp_path):
    from runtime import lua_binding
    if not lua_binding.HostLuaRun.available():
        pytest.skip("host lua binding not built (needs a C compiler)")
    from ws_helpers import build_ws
    root = tmp_path / "carts"
    cart = root / "hello.moy"
    cart.mkdir(parents=True)
    (cart / "manifest.json").write_text(json.dumps(
        {"format": "moy-1", "title": "Hello Lua", "main": "main.lua",
         "runtime": "lua"}), encoding="utf-8")
    (cart / "main.lua").write_text(_LUA, encoding="utf-8")
    ws = build_ws(tmp_path)
    from ws_helpers import open_cart
    open_cart(ws, "Hello Lua")
    _frame(ws)
    assert ws.player.cart_error is None, ws.player.cart_error
    got = _box(ws.canvas)
    assert got == _petme_hello(ws.canvas)


def test_a_full_set_of_slots_is_taken_back(tmp_path):
    """The loader's slots are the process's. On a board the kernel frees
    them with the VM (moy_text_vm_stop); where nothing does, a theme install
    that finds them full takes them back."""
    from ws_helpers import build_ws
    while ui_draw.font_load(_fnt())[1] is not None:
        pass
    assert _free_slots() == 0
    appearance._FONTS.clear()                  # a console that knows none of them
    build_ws(tmp_path)
    assert ui_draw.font("ui")[0] not in (0, None) and len(appearance._FONTS) == 1
    assert _free_slots() == SLOTS - 1
