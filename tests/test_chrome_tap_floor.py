"""The chrome tap-target floor (#203): `chrome_scale`, and what it may not move.

The mechanism is one sentence -- `fs` sizes TEXT, `cs` sizes what a FINGER
lands on, `cs >= fs` and they are EQUAL unless a board declares its glass --
and the whole risk is in the second half of it. A second scale threaded through
`chrome.Layout` touches every responsive surface in the shell, so the thing
that has to be executable is not "the Guition got bigger buttons" (the pixel
goldens show that) but "nothing else moved at all".

So this file is in three parts:

  * the DERIVATION -- millimetres in, an integer scale out, on the real panels.
    It is arithmetic, so it is pinned as arithmetic rather than as a rendered
    frame: a 4.5mm floor is a design number, and a test that only compared two
    hashes could not say which number it was defending.
  * the IDENTITY -- every layout in the tree, at every shipped tier, built with
    `chrome_scale=fs` and with none, compared field by field. `_base` is
    included on purpose: the frozen 320x240 branch is the one the whole #39
    degradation contract rests on, and it now has one more term in its
    predicate.
  * the GROWTH -- at cs > fs the tap targets grow and the TEXT metrics do not,
    which is the claim the issue makes and the one a reader will want checked
    without opening a PNG.

The pixel proof is `tests/test_shell_goldens.py`: `guition_480x320_fs1_dark`
and `guition_tapfloor_dark` are the same tier one axis apart, and every one of
their 19 surfaces hashes differently.
"""

import pytest

from runtime import host_app  # noqa: F401  (installs the bare-name aliases)
from runtime import chrome
from runtime.chrome import CodeLayout, Layout, chrome_scale_floor


# The panels this project actually ships or simulates, with the diagonal each
# one's board.toml declares (or would declare). `floor` is what the derivation
# must answer; `declares` is whether that board opts in today, and the reason a
# silence is a decision rather than an oversight.
PANELS = (
    ("t-deck", 320, 240, 2.8, 2, False,
     "DECLINED 2026-09-06 on the rendered frames: its floor IS 2, and at 320px "
     "wide that leaves the bar's lent zone too narrow for the tab ladder"),
    ("guition-s3", 480, 320, 3.5, 2, True,
     "touch-only 3.5in glass -- the board the floor exists for"),
    ("p4", 1024, 600, 7.0, 2, True,
     "declared 2026-09-06 over a shipped FONT_SCALE of 1: 1024px absorbs it"),
)


@pytest.mark.parametrize("name,w,h,diag,floor,_declares,_why", PANELS)
def test_the_floor_is_the_millimetres_it_claims(name, w, h, diag, floor,
                                                _declares, _why):
    """The derivation, checked against the physics rather than against itself:
    a 16px icon at the answered scale clears MIN_TAP_MM, and one scale lower
    does not (so the answer is the SMALLEST one that works, not merely one that
    does)."""
    got = chrome_scale_floor(w, h, diag)
    assert got == floor, name
    mm_per_px = 25.4 * diag / ((w * w + h * h) ** 0.5)
    assert 16 * got * mm_per_px >= chrome.MIN_TAP_MM
    if got > 1:
        assert 16 * (got - 1) * mm_per_px < chrome.MIN_TAP_MM


def test_a_board_that_declares_nothing_keeps_chrome_on_the_font_scale():
    """The opt-in. A panel's physical size is the one input, and no input means
    no floor -- not a guess from the resolution, which is what would silently
    move every tier that never asked."""
    assert chrome_scale_floor(480, 320, None) == 1
    assert chrome_scale_floor(480, 320, 0) == 1
    assert chrome_scale_floor(320, 240) == 1


def test_a_bigger_screen_of_the_same_pixels_needs_no_floor():
    """The floor is about millimetres, not pixels: the same 480x320 raster on a
    tablet-sized panel already clears the target and asks for nothing."""
    assert chrome_scale_floor(480, 320, 3.5) == 2
    assert chrome_scale_floor(480, 320, 7.0) == 1


# The tiers whose pixels are frozen by tests/shell_goldens/hashes.json, plus the
# 320x240 baseline every other tier degrades to.
TIERS = ((320, 240, 1), (480, 320, 1), (800, 480, 3), (1024, 600, 2))

# Every layout head that takes a chrome scale. The editor/app layouts are here
# because `cs` reaches them too -- they inset for the OS bar, and a bar that
# grew without telling them would draw over their first 18 rows.
def _layout_heads():
    from runtime.block_editor_ui import BlockLayout
    from runtime.cards_layer import CardsLayout
    from runtime.map_editor_ui import MapLayout
    from runtime.music_editor_ui import MusicLayout
    from runtime.paint_layer import PaintLayout
    from runtime.scene_editor_ui import SceneLayout
    return (Layout, CodeLayout, BlockLayout, CardsLayout, MapLayout,
            MusicLayout, PaintLayout, SceneLayout)


@pytest.mark.parametrize("w,h,fs", TIERS)
def test_declaring_the_scale_it_already_had_moves_nothing(w, h, fs):
    """cs == fs is the DEFAULT, and it has to be indistinguishable from the
    argument never existing -- field by field, not surface by surface, because
    a field nothing draws today is a field something draws tomorrow."""
    for head in _layout_heads():
        plain = vars(head(w, h, fs))
        same = vars(head(w, h, fs, chrome_scale=fs))
        assert plain == same, "%s at %dx%d fs%d" % (head.__name__, w, h, fs)


@pytest.mark.parametrize("w,h,fs", TIERS)
def test_the_chrome_scale_can_never_go_under_the_font(w, h, fs):
    """Text has to fit in the box it is centred in, so a floor BELOW the font
    scale is clamped away rather than honoured."""
    for head in _layout_heads():
        lay = head(w, h, fs, chrome_scale=1)
        assert lay.cs == lay.fs


def test_the_frozen_baseline_branch_needs_both_scales_at_one():
    """`_base` is the #39 degradation contract's whole predicate. A 320x240
    panel dense enough to floor its chrome is a DIFFERENT layout from the
    T-Deck's, and must not reproduce constants that assume an 18px bar."""
    assert Layout(320, 240, 1)._base
    assert Layout(320, 240, 1, chrome_scale=1)._base
    assert not Layout(320, 240, 1, chrome_scale=2)._base


# The Guition, as its board.toml declares it.
GUITION = (480, 320, 1, 2)


def test_the_tap_targets_grow_and_the_text_does_not():
    """#203's claim, as geometry. Every rect a finger aims at doubles; every
    metric a GLYPH is measured in stays exactly where it was."""
    w, h, fs, cs = GUITION
    plain = Layout(w, h, fs)
    floored = Layout(w, h, fs, chrome_scale=cs)

    assert floored.status_h == 2 * plain.status_h
    assert floored.bar_icon == 2 * plain.bar_icon
    assert floored.bar_stride == 2 * plain.bar_stride
    assert floored.set_row_h == 2 * plain.set_row_h
    for rect in ("sysmenu_btn", "wifi_btn", "batt_btn", "context_x_btn",
                 "new_btn", "dup_btn", "del_btn", "set_back", "set_ach"):
        was = getattr(plain, rect)
        now = getattr(floored, rect)
        assert now[2] == 2 * was[2] and now[3] == 2 * was[3], rect

    # ...and the text side is untouched.
    assert floored.fs == plain.fs
    assert floored.font_w == plain.font_w
    assert floored.clock_w == plain.clock_w

    code = CodeLayout(w, h, fs, chrome_scale=cs)
    assert code.cell == CodeLayout(w, h, fs).cell        # char cell: text
    assert code.lh == CodeLayout(w, h, fs).lh            # line height: text
    assert code.y0 == floored.status_h                   # ...clears the taller bar
    assert code.sym_h == 2 * CodeLayout(w, h, fs).sym_h  # a key: a tap target


def test_the_symbol_keys_never_grow_off_the_panel():
    """On a board with no keyboard that strip IS the keyboard, so a key that
    grew past the right edge is a key that cannot be pressed. It is capped at
    what the canvas holds -- and never below the font-scale width, which is
    what keeps font scale 3 (already wider than 800px) where it is."""
    from runtime.code_layer import _CODE_SYMBOLS
    n = len(_CODE_SYMBOLS)
    floored = CodeLayout(480, 320, 1, chrome_scale=2)
    assert floored.sym_cell * n <= 480
    assert floored.sym_cell > CodeLayout(480, 320, 1).sym_cell
    for w, h, fs in TIERS:
        assert (CodeLayout(w, h, fs).sym_cell
                == CodeLayout(w, h, fs, chrome_scale=fs).sym_cell)


def test_the_text_offsets_re_centre_only_when_the_box_grew():
    """The hand-tuned offsets (the bar's y=3, a Settings row's y=5) are frozen
    pixels. The `*_dy` fields shift the fs-tall band into the middle of a
    cs-tall box, so they must be exactly 0 wherever the box did not grow."""
    for w, h, fs in TIERS:
        lay = Layout(w, h, fs, chrome_scale=fs)
        assert lay.bar_text_dy == 0
        assert lay.row_text_dy == 0
        assert lay.set_head_dy == 0
        code = CodeLayout(w, h, fs, chrome_scale=fs)
        assert code.sym_text_dx == 0 and code.sym_text_dy == 0
    w, h, fs, cs = GUITION
    lay = Layout(w, h, fs, chrome_scale=cs)
    assert lay.bar_text_dy > 0 and lay.row_text_dy > 0 and lay.set_head_dy > 0


def test_the_console_resolves_the_floor_from_the_panel_not_a_setting(tmp_path):
    """The operational half of the design (#202's note): `font_scale` is
    PERSISTED, so a scale chosen in a constructor is not what a device with a
    store runs. The floor is resolved from the board's declared glass at
    construction and re-applied by every relayout, so cycling the font size
    cannot lose it."""
    ws = host_app.build_workstation(
        str(tmp_path / "carts"), sys_size=(480, 320), font_scale=1,
        panel_diagonal_in=3.5)
    assert ws.chrome_floor == 2
    assert ws.layout.cs == 2 and ws.layout.fs == 1
    ws.look.set_font_scale(2, persist=False)
    assert ws.layout.fs == 2 and ws.layout.cs == 2
    ws.look.set_font_scale(1, persist=False)
    assert ws.layout.fs == 1 and ws.layout.cs == 2

    bare = host_app.build_workstation(
        str(tmp_path / "carts2"), sys_size=(480, 320), font_scale=1)
    assert bare.chrome_floor == 1
    assert bare.layout.cs == bare.layout.fs == 1


def test_every_editor_tab_re_derives_on_BOTH_scales(tmp_path):
    """#216 made every Editor tab rebuild its own layout on entry, and that is a
    second place the scales have to travel together: a tab that re-derived on the
    font scale alone would inset for an 18*fs bar and draw under the taller one
    an opted-in board actually has. Asserted as a LAYOUT field rather than as
    pixels, because that is the thing a future `relayout(w, h, fs)` call site
    would silently drop -- the goldens would only show it on whichever surface
    happened to be captured after a tab entry."""
    from runtime.editor_app import _TAB_LAYOUT_UI, _ZONE_TABS

    ws = host_app.build_workstation(
        str(tmp_path / "carts"), sys_size=(480, 320), font_scale=1,
        panel_diagonal_in=3.5)
    cart = next(c for c in ws.carts.all if c.get("title") == "Star Catcher")
    tabs = [t for t, _glyph in _ZONE_TABS if t and not t.startswith("\x00")]
    assert tabs, "the Editor tab ladder is empty -- this test covers nothing"
    for tab in tabs:
        ws.open_in_editor(cart)
        ws.set_menu_view(tab)
        if tab == "code":
            lay = ws.code_layout
        else:
            owner = getattr(ws, _TAB_LAYOUT_UI[tab])
            lay = getattr(owner, "block_layout", None) or owner.layout
        assert (lay.fs, lay.cs) == (1, 2), (
            "the %s tab re-derived its layout on the font scale alone: "
            "fs=%d cs=%d" % (tab, lay.fs, lay.cs))


def test_the_menu_rows_follow_the_chrome_scale_and_its_labels_do_not(tmp_path):
    """The ≡ dropdown is the one surface whose geometry lives on the widget
    rather than on `Layout`, so it takes the scale through `toggle_sysmenu` and
    is worth its own check: rows grow, the text-sized panel width does not."""
    ws = host_app.build_workstation(
        str(tmp_path / "carts"), sys_size=(480, 320), font_scale=1,
        panel_diagonal_in=3.5)
    ws.toggle_sysmenu()
    assert ws.sysmenu.cs == 2 and ws.sysmenu.fs == 1
    x, y, w, h = ws.sysmenu.panel_rect()
    rows = [i for i in ws.sysmenu.items if i[0] != "sep"]
    assert y == ws.layout.status_h            # hangs under the taller bar
    assert w == 128                           # panel width is TEXT-sized: fs
    assert h >= 24 * len(rows)                # ...rows are 12*cs, not 12*fs


def test_a_tap_box_centres_its_content_and_is_the_identity_at_cs_eq_fs():
    """`Layout.tap_box` is `row_band` for a BUTTON (owner report on Guition
    glass, 2026-09-06: "the achievements and x icons are tiny and in the top
    left corner, but their buttons are big"). It has to be the exact identity
    wherever the button did not grow, or every non-declaring tier moves."""
    for w, h, fs in TIERS:
        lay = Layout(w, h, fs, chrome_scale=fs)
        for rect, base in ((lay.set_back, (18, 14)), (lay.set_ach, (22, 14))):
            assert lay.tap_box(rect, *base) == tuple(rect), (w, h, fs)
    w, h, fs, cs = GUITION
    lay = Layout(w, h, fs, chrome_scale=cs)
    for rect, base in ((lay.set_back, (18, 14)), (lay.set_ach, (22, 14))):
        bx, by, bw, bh = lay.tap_box(rect, *base)
        assert (bw, bh) == (base[0] * fs, base[1] * fs)      # font-sized content
        assert bx > rect[0] and by > rect[1]                 # ...moved off the corner
        # Centred: the margin it left is the same on both sides (+/- the odd px).
        assert abs((bx - rect[0]) - (rect[0] + rect[2] - bx - bw)) <= 1
        assert abs((by - rect[1]) - (rect[1] + rect[3] - by - bh)) <= 1


def test_the_editor_zone_is_drawn_and_hit_tested_on_ONE_scale(tmp_path):
    """The bar's lent zone lays its chips out on `_zone_scale` and DRAWS them
    through `ui.tab_row`, which reads the scale off the canvas. Those were the
    same number until #203, after which the Guition sized the ladder at cs 2 and
    painted it at fs 1 -- a tap on the CODE chip opened Blocks. They are one
    number now, and all seven tabs stay reachable on the bar the tap floor
    shortened."""
    from runtime import editor_app as _ea
    from runtime import ui as _ui
    for diag, cs in ((None, 1), (3.5, 2)):
        ws = host_app.build_workstation(
            str(tmp_path / ("z%s" % diag)), sys_size=(480, 320), font_scale=1,
            panel_diagonal_in=diag)
        assert ws.layout.cs == cs
        cart = [c for c in ws.carts.all if c.get("edit")][0]
        ws.open_in_editor(cart)
        ed = ws.editor_app
        assert ed._zone_scale() == ws.sys_canvas.font_scale
        _proj, tabs_area, _play = ed._zone_parts(ws.layout.zone_left)
        slim = [(tid, label) for tid, label, _ic in _ea._TAB_CHIPS]
        rects = _ui.tab_row_rects(tabs_area, slim, ed._zone_scale())
        assert [t for t, _r, _l in rects] == [t for t, _l, _i in _ea._TAB_CHIPS]
        assert rects[0][1][3] == ws.layout.bar_icon      # ...as tall as the band
