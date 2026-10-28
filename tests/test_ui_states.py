"""runtime/ui.py Phase 3a: the six-state interaction model and the two new
widget kinds (`row`, `cell`): the state model resolves right -- the full
precedence lattice, not a sample of it -- and the rows and cells paint their
state's tokens.
"""

from runtime import ui_draw
from runtime.chrome import THEME_VARIANTS
from ws_helpers import fixture_colors

# The palette themes whose states these resolve: the seeds of MOY64 colours
# docs/look_2026-10.md step 27 deleted, as fixture packages
# (tests/fixtures/themes/), in their old seed order; `moy`'s, `blocks`' and
# `contrast`'s RGB roles are tests/test_theme_rgb.py's and its goldens'.
THEME_NAMES = ("night", "indigo", "berry", "forest", "slate", "machine",
               "platinum", "win95", "outline")
from runtime.host_canvas import make_system_canvas as SystemCanvas


TH = fixture_colors("machine")


# --- test doubles --------------------------------------------------------------

def _glyph_draw(kind, rect, color, cv):
    """A leaf-safe stand-in for ws._glyph: deterministic, rect + colour only."""
    x, y, w, h = rect
    cv.rect(x + 1, y + 1, max(1, w - 2), max(1, h - 2), color)
    cv.rectb(x, y, w, h, color)


class _Img:
    """A duck-typed 16x16 indexed tile the system canvas accepts."""

    def __init__(self, seed=3):
        self.w = 16
        self.h = 16
        self.pix = bytearray(((i * 7 + seed) & 63) for i in range(256))
        self.transparent = -1
        self._paint = True


IMG = _Img()


# =============================================================================
# 1. the new arguments
# =============================================================================

def test_the_new_arguments_default_to_the_frozen_rendering():
    """`disabled`/`state` were added to chip and button. Passing them at their
    defaults, or not at all, must be the same call, branch by branch."""
    for th in (fixture_colors("night"), fixture_colors("machine", "light")):
        for kwargs in ({}, {"on": True}, {"hot": True}):
            a = SystemCanvas(80, 30)
            b = SystemCanvas(80, 30)
            ui_draw.chip(a, th, (2, 2, 70, 20), "HI", **kwargs)
            ui_draw.chip(b, th, (2, 2, 70, 20), "HI",
                    disabled=False, state=None, **kwargs)
            assert bytes(a._buf) == bytes(b._buf)
        for kind in ("normal", "play", "author", "danger"):
            a = SystemCanvas(80, 30)
            b = SystemCanvas(80, 30)
            ui_draw.button(a, th, (2, 2, 70, 20), "HI", kind=kind)
            ui_draw.button(b, th, (2, 2, 70, 20), "HI", kind=kind,
                      disabled=False, state=None)
            assert bytes(a._buf) == bytes(b._buf)


# =============================================================================
# 2. the state model
# =============================================================================

def test_precedence_over_every_combination():
    """disabled > pressed > hot > on > hover > rest, exhaustively: 2^3 semantic
    flags x the three things the registry can report."""
    for disabled in (False, True):
        for hot in (False, True):
            for on in (False, True):
                for interact in (None, ui_draw.HOVER, ui_draw.PRESSED):
                    got = ui_draw.widget_state(on=on, hot=hot, disabled=disabled,
                                          interact=interact)
                    if disabled:
                        want = ui_draw.DISABLED
                    elif interact == ui_draw.PRESSED:
                        want = ui_draw.PRESSED
                    elif hot:
                        want = ui_draw.HOT
                    elif on:
                        want = ui_draw.ON
                    elif interact == ui_draw.HOVER:
                        want = ui_draw.HOVER
                    else:
                        want = ui_draw.REST
                    assert got == want, (disabled, hot, on, interact, got)


def test_precedence_order_is_the_documented_one():
    assert ui_draw.STATES == (ui_draw.DISABLED, ui_draw.PRESSED, ui_draw.HOT, ui_draw.ON, ui_draw.HOVER,
                         ui_draw.REST)
    # A no-argument call is rest: every existing widget's default.
    assert ui_draw.widget_state() == ui_draw.REST


def test_state_names_are_distinct_constants():
    assert len(set(ui_draw.STATES)) == 6


# -- tokens: derived, never new literals ---------------------------------------

def test_every_state_resolves_on_all_twelve_theme_sets():
    """The restyle promise: a theme that names NO state token still paints
    a correct cue, so all 12 family x variant sets keep working unmodified."""
    for name in THEME_NAMES:
        for variant in THEME_VARIANTS:
            th = fixture_colors(name, variant)
            palette = set(th.values())
            for state in ui_draw.STATES:
                for kind in ("row", "cell"):
                    field, ink, edge = ui_draw.state_colors(th, kind, state)
                    for c in (field, ink, edge):
                        assert isinstance(c, int) and 0 <= c < 64
                    # Token-DERIVED: every colour a state paints is one the
                    # theme already names (or the shared cream/black the whole
                    # toolkit uses), never a fresh literal invented here.
                    assert field in palette
                    assert ink in palette or ink in (0, 7)
                    assert edge in palette


def test_state_tokens_walk_the_alias_chain_in_c():
    """A state token the theme does not hold resolves through its alias chain
    (native/moy_ui), read here through the row kind's specs: hover's edge is
    hover_cue (-> focus -> accent), pressed's field pressed (-> selection ->
    hilite) and its ink pressed_ink (-> selection_ink -> ink)."""
    th = {"panel": 60, "dim": 42, "accent": 9, "hilite": 5, "ink": 3,
          "title_ink": 0}
    assert ui_draw.state_colors(th, "row", ui_draw.HOVER)[2] == 9
    field, ink, _edge = ui_draw.state_colors(th, "row", ui_draw.PRESSED)
    assert (field, ink) == (5, 3)
    # A theme that DOES name the token wins over the chain; index 0 is a
    # value, not a miss.
    th2 = dict(th, focus=0, selection=11)
    assert ui_draw.state_colors(th2, "row", ui_draw.HOVER)[2] == 0
    assert ui_draw.state_colors(th2, "row", ui_draw.PRESSED)[0] == 11
    # A theme silent on every role paints the chain's literal fallback, and a
    # spec with no fallback paints nothing.
    assert ui_draw.state_colors({}, "row", ui_draw.REST) == (60, 7, 1)
    assert ui_draw.state_colors({}, "row_menu", ui_draw.REST) == (None, 6, None)


def test_hover_is_additive_and_disabled_only_dims():
    """SS7's principles as assertions: hover changes the EDGE only (field and
    ink stay at rest), and disabled keeps the field but dims the ink."""
    th = fixture_colors("night")
    rest = ui_draw.state_colors(th, "row", ui_draw.REST)
    hover = ui_draw.state_colors(th, "row", ui_draw.HOVER)
    assert hover[0] == rest[0] and hover[1] == rest[1]
    assert hover[2] != rest[2] and hover[2] == th["focus"]
    dis = ui_draw.state_colors(th, "row", ui_draw.DISABLED)
    assert dis[0] == rest[0]
    assert dis[1] != rest[1] and dis[1] == th["ink_dim"]


def test_ui_stays_a_leaf():
    """The cycle ui -> chrome -> settings_layer -> ui must stay impossible.
    Checked on IMPORT STATEMENTS, not on substrings -- the module documents
    the cycle in prose."""
    import ast
    tree = ast.parse(open(ui_draw.__file__).read())
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert names, "no imports found -- is the scan still reading ui_draw.py?"
    for name in names:
        leaf = name.split(".")[-1]
        assert leaf not in ("chrome", "console", "settings_layer",
                            "wm_windowed"), name
    # The imports are the C this module is the face of, the array type its
    # token rows and hit buffers travel in, and struct to read a scroll
    # state's velocity.
    assert set(n.split(".")[-1] for n in names) == {
        "moy_ui", "array", "struct", "ustruct"}, names


# =============================================================================
# 5. the row kind
# =============================================================================

def test_row_rest_and_selected_paint_the_row_tokens():
    th = fixture_colors("night")
    cv = SystemCanvas(200, 60)
    ui_draw.row(cv, th, (0, 0, 200, 20), "REST")
    assert cv.pix(2, 2) == th["panel"]
    assert cv.pix(0, 0) == th["dim"]
    ui_draw.row(cv, th, (0, 40, 200, 20), "ARMED", hot=True)
    assert cv.pix(2, 42) == th["danger"]


def test_row_field_none_paints_nothing_and_edge_is_optional():
    """Settings and the system menu paint no field on an unselected row and no
    border at all; a `row` that could not express that would not fit them."""
    th = fixture_colors("night")
    cv = SystemCanvas(100, 20)
    cv.cls(31)
    ui_draw.row(cv, th, (0, 0, 100, 20), "", colors=(None, 7, 3), edge=False)
    assert cv.pix(0, 0) == 31 and cv.pix(50, 10) == 31
    ui_draw.row(cv, th, (0, 0, 100, 20), "", colors=(None, 7, 3), edge=True)
    assert cv.pix(0, 0) == 3


def test_row_disabled_dims():
    th = fixture_colors("night")
    a, b = SystemCanvas(200, 20), SystemCanvas(200, 20)
    ui_draw.row(a, th, (0, 0, 200, 20), "LOCKED", disabled=True)
    ui_draw.row(b, th, (0, 0, 200, 20), "LOCKED")
    assert bytes(a._buf) != bytes(b._buf)


def test_row_truncates_the_label_and_the_value_inside_the_rect():
    """#174's lesson generalized: overflow must CLIP, never escape the rect and
    land ink on whatever is beside it."""
    th = fixture_colors("night")
    cv = SystemCanvas(200, 40)
    cv.cls(31)
    ui_draw.row(cv, th, (20, 10, 60, 20), "A VERY LONG ROW LABEL",
           value="ALSO LONG", colors=(None, 7, 3), edge=False)
    for x in range(20):                                # nothing left of the row
        assert cv.pix(x, 20) == 31
    for x in range(80, 200):                           # nothing right of it
        assert cv.pix(x, 20) == 31


def test_row_value_is_right_aligned_and_label_keeps_the_rest():
    th = fixture_colors("night")
    cv = SystemCanvas(200, 20)
    cv.cls(31)
    ui_draw.row(cv, th, (0, 0, 200, 20), "WIFI", value="OFF",
           colors=(None, 7, 3), edge=False, pad=4)
    # "OFF" ends one pad short of the right edge: 200 - 4 - 24 = 172.
    assert any(cv.pix(x, 6) == 7 for x in range(172, 196))
    assert cv.pix(198, 10) == 31


def test_row_icon_and_glyph_shift_the_label(monkeypatch):
    """Achievements' leading glyph, Settings' trailing status icon."""
    th = fixture_colors("night")
    plain = SystemCanvas(200, 20)
    ui_draw.row(plain, th, (0, 0, 200, 20), "NAME", colors=(None, 7, 3), edge=False)
    withg = SystemCanvas(200, 20)
    ui_draw.row(withg, th, (0, 0, 200, 20), "NAME", glyph="lock",
           glyph_draw=_glyph_draw, colors=(None, 7, 3), edge=False)
    assert bytes(plain._buf) != bytes(withg._buf)
    withi = SystemCanvas(200, 20)
    ui_draw.row(withi, th, (0, 0, 200, 20), "NAME", icon_img=IMG,
           colors=(None, 7, 3), edge=False)
    assert bytes(withi._buf) != bytes(plain._buf)


def test_row_pad_and_text_dy_reproduce_the_frozen_geometries():
    """Settings' literal (4, 5), Files' (4*fs, 6*fs), Storybook's centred
    label -- the three the converting phases must be able to hit exactly."""
    th = fixture_colors("night")
    a = SystemCanvas(200, 26)
    ui_draw.row(a, th, (0, 0, 200, 26), "X", colors=(None, 7, 3), edge=False,
           pad=4, text_dy=5)
    b = SystemCanvas(200, 26)
    b.print("X", 4, 5, 7, 1)
    assert bytes(a._buf) == bytes(b._buf)
    c = SystemCanvas(200, 26)
    ui_draw.row(c, th, (0, 0, 200, 26), "X", colors=(None, 7, 3), edge=False, pad=6)
    d = SystemCanvas(200, 26)
    d.print("X", 6, (26 - 8) // 2, 7, 1)
    assert bytes(c._buf) == bytes(d._buf)


# =============================================================================
# 6. the cell kind
# =============================================================================

def test_cell_returns_the_art_rect_and_matches_the_pure_geometry():
    th = fixture_colors("night")
    cv = SystemCanvas(200, 120)
    art = ui_draw.cell(cv, th, (0, 0, 76, 66), "name")
    assert art == ui_draw.cell_art_rect((0, 0, 76, 66), 1, 2, 14)
    assert art == (2, 2, 72, 50)                       # FileGridView's numbers
    assert ui_draw.cell_art_rect((0, 0, 76, 66), 1, 3, 17) == (3, 3, 70, 46)
    # A frame-only cell (cards' choice cells): pad 0, no caption.
    assert ui_draw.cell_art_rect((5, 5, 20, 20), 1, 0, 0) == (5, 5, 20, 20)


def test_cell_selected_lifts_the_field_and_the_edge():
    th = fixture_colors("night")
    cv = SystemCanvas(200, 120)
    ui_draw.cell(cv, th, (0, 0, 76, 66), "a")
    assert cv.pix(1, 1) == th["panel"] and cv.pix(0, 0) == th["dim"]


def test_cell_band_fills_the_caption_zone():
    """The wallpaper cards' look: a filled strip with a left-aligned title,
    accented when selected. Without a band the label prints on the field."""
    th = fixture_colors("night")
    cv = SystemCanvas(200, 120)
    # y = 78 is inside the 17px caption zone (63..79) but below the label row
    # (67..74), so it reads the ZONE, not the glyphs.
    ui_draw.cell(cv, th, (0, 0, 100, 80), "TITLE", band=True, pad=3, caption_h=17)
    assert cv.pix(50, 78) == th["title"]
    plain = SystemCanvas(120, 90)
    ui_draw.cell(plain, th, (0, 0, 100, 80), "TITLE", pad=3, caption_h=17)
    assert plain.pix(50, 78) == th["panel"]             # no band: the field


def test_cell_art_never_covers_the_border_or_the_caption():
    """Draw order is field -> caption -> edge, and the art rect excludes both,
    so a caller's picture cannot land on the frame."""
    th = fixture_colors("night")
    cv = SystemCanvas(120, 90)
    ax, ay, aw, ah = ui_draw.cell(cv, th, (0, 0, 100, 80), "T", band=True, pad=3,
                             caption_h=17)
    assert ax >= 3 and ay >= 3
    assert ax + aw <= 100 - 3
    assert ay + ah <= 80 - 17


def test_cell_states_route_through_the_same_model_as_row():
    th = fixture_colors("night")
    for state in (ui_draw.HOVER, ui_draw.PRESSED):
        cv = SystemCanvas(60, 60)
        ui_draw.cell(cv, th, (0, 0, 40, 40), None, state=state)
        want = ui_draw.state_colors(th, "cell", state)
        assert cv.pix(0, 0) == want[2]
        assert cv.pix(1, 1) == want[0]


def test_cell_draws_a_centred_glyph_or_icon_in_the_art_rect():
    th = fixture_colors("night")
    a = SystemCanvas(60, 60)
    art = ui_draw.cell(a, th, (0, 0, 40, 40), None, glyph="dot",
                  glyph_draw=_glyph_draw, pad=0)
    assert art == (0, 0, 40, 40)
    cx = art[0] + (art[2] - 14) // 2
    assert a.pix(cx + 1, art[1] + (art[3] - 14) // 2 + 1) == \
        ui_draw.state_colors(th, "cell", ui_draw.REST)[1]
    b = SystemCanvas(60, 60)
    ui_draw.cell(b, th, (0, 0, 40, 40), None, icon_img=IMG, pad=0)
    assert bytes(a._buf) != bytes(b._buf)


def test_the_toggle_is_a_chip_that_reads_switched_on():
    """A kind added as data (sprint 6, docs/kernel_toolkit_2026-10.md §2.2):
    chip's shape and chip's look, except ON, which is the theme's play green
    with light ink."""
    th = fixture_colors("machine")
    for st in ui_draw.STATES:
        t, c = ui_draw.state_colors(th, "toggle", st), ui_draw.state_colors(th, "chip", st)
        if st == ui_draw.ON:
            assert t == (th["play"], 7, c[2])
        else:
            assert t == c, st
