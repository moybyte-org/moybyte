"""The home and the chrome round it under every shipped theme and mode, on
the shell the goldens build (`tests/test_shell_goldens.py`'s configs and
pinning)."""

import pytest

import test_shell_goldens as g
from runtime import ui_draw
from runtime import launcher_layer

THEMES = [("moy", "dark"), ("moy", "light"), ("blocks", "light"), ("blocks", "dark"),
          ("contrast", "dark"), ("contrast", "light")]
HANDHELD = "tdeck_320x240_fs1_dark"
DESK = "p4_1024x600_cs2_fs1_windowed"
CONSOLE = "p4_1024x600_cs2_fs1_console"
COVER = 128


def _ws(tmp_path, config, theme, mode):
    import moy_ui
    moy_ui.theme_uninstall()
    cfg = dict(g.CONFIGS[config], variant=mode)
    if "layout" not in cfg:
        cfg["theme"] = theme
    return g._build(cfg, tmp_path / "carts")


def _frame(ws, n=3):
    for _ in range(n):
        g._quiesce(ws)
        ws._dirty = True
        ws.frame(g._DT)


def _home(ws):
    # every frame a full paint: the retained-frame stamp would present the
    # last one without drawing a card
    ws.launcher_layer._lib_unsupported = True
    ws.go_home()
    if getattr(ws.wm, "mode", None) is not None:
        ws.open_library()
    ws.set_home_tab("games")
    titles = [it.get("title") for it in ws.launcher.items]
    if "Sky Run" in titles:
        ws.launcher.sel = titles.index("Sky Run")


@pytest.fixture(autouse=True)
def _uninstall():
    yield
    import moy_ui
    from runtime import iconsets
    moy_ui.theme_uninstall()
    iconsets.reset()                # the sets' masks back out of the font rows


@pytest.mark.parametrize("theme,mode", THEMES)
def test_the_hero_mark_lies_under_the_bar(tmp_path, monkeypatch, theme, mode):
    """The centre card's focus mark lies wholly on the content, under the
    bar, never clipped at the bar's line: the hero's rim or ring row is
    drawn (R2-01)."""
    ws = _ws(tmp_path, HANDHELD, theme, mode)
    _home(ws)
    _frame(ws, 1)
    marks = []
    real = launcher_layer._ui_draw.focus

    def focus(cv, th, rect, size=None, r=0, after=False, fs=None, ring=False, selected=False):
        got = real(cv, th, rect, size, r, after, fs, ring, selected)
        if size == "card" and not after:
            marks.append((tuple(rect), tuple(got[1])))
        return got

    monkeypatch.setattr(launcher_layer._ui_draw, "focus", focus)
    _frame(ws, 1)
    assert marks, "the hero drew no focus mark"
    band_top = ws.launcher.band_rect()[1]
    reach = ui_draw.mark_reach("card")
    for at, box in marks:
        # the mark's outer edge: the ring's outline, or the lift's rim one
        # pixel round its grown box
        top = min(at[1] - reach, box[1] - 1)
        assert top >= band_top, ("%s %s: the hero's mark starts at y %d, over "
                                 "the bar's line at %d" % (theme, mode, top, band_top))


def _clean(side):
    if side >= COVER:
        return side % COVER == 0
    return side in (COVER // 2, COVER // 4, COVER // 8)


@pytest.mark.parametrize("config", [HANDHELD, CONSOLE])
@pytest.mark.parametrize("theme,mode", [("moy", "dark"), ("moy", "light"),
                                        ("contrast", "dark")])
def test_a_home_draws_whole_covers_at_a_clean_scale(tmp_path, monkeypatch, config, theme,
                                                    mode):
    """Every cover a home draws is the whole 128 px cover at a clean scale,
    a whole multiple of its side or exactly its half, quarter or eighth
    (owner call 2026-10-17), a lifted card's included (R2-07)."""
    ws = _ws(tmp_path, config, theme, mode)
    _home(ws)
    _frame(ws, 2)
    sizes = []
    for kind in {type(ws.sys_canvas), type(ws.glass_canvas)}:
        real = kind.cover

        def cover(cv, pix, w, sx, sy, sw, sh, dx, dy, dw, dh, *rest, _real=real):
            sizes.append((sw, sh, dw, dh))
            return _real(cv, pix, w, sx, sy, sw, sh, dx, dy, dw, dh, *rest)

        monkeypatch.setattr(kind, "cover", cover)
    _frame(ws, 1)
    assert sizes, "the home drew no cover"
    for sw, sh, dw, dh in sizes:
        assert dw == dh, "a cover drawn %dx%d" % (dw, dh)
        assert _clean(dw) and dw * sw == dw * sh, \
            "%s %s %s: a cover drawn at %d px, not a clean scale of %d" % (
                config, theme, mode, dw, COVER)


@pytest.mark.parametrize("config", [HANDHELD, DESK])
@pytest.mark.parametrize("theme", ["moy", "blocks", "contrast"])
def test_a_themes_bar_packs_alike_in_both_modes(tmp_path, config, theme):
    """A theme's bar packs alike in its light and dark modes, whatever faces
    they set (R2-13)."""
    got = {}
    for mode in ("dark", "light"):
        ws = _ws(tmp_path / mode, config, theme, mode)
        got[mode] = (ws.layout.clock_w, ws.layout.bars)
    assert got["dark"] == got["light"], "%s on %s: the bar moves with the mode" % (
        theme, config)


@pytest.mark.parametrize("theme", ["moy", "blocks", "contrast"])
def test_a_themes_caption_sits_on_one_baseline_in_both_modes(tmp_path, theme):
    """The handheld home's title and metadata draw on the same baselines
    under the hero in a theme's light and dark modes, whatever leading
    their faces set (R3-22: Moy's Day set its metadata 2 px lower than
    Night)."""
    got = {}
    for mode in ("dark", "light"):
        ws = _ws(tmp_path / mode, HANDHELD, theme, mode)
        _home(ws)
        ty, _th, tsc, my, _mh, _h = ws.launcher.foot()
        fs = ws.layout.fs
        hero = ws.launcher._geom()[7] + ws.launcher.card_side()     # the hero's foot
        got[mode] = (ty + ui_draw.font("hero")[3] * tsc - hero,
                     my + ui_draw.font()[3] * fs - hero)
    assert got["dark"] == got["light"], "%s: title and metadata baselines %s" % (theme, got)


@pytest.mark.parametrize("config", [HANDHELD, CONSOLE])
@pytest.mark.parametrize("theme,mode", THEMES)
def test_a_focused_cover_fills_its_mark(tmp_path, monkeypatch, config, theme, mode):
    """The selected card's cover fills the box its focus mark draws in, so
    a lift's rim hugs the cover with no plate between (R3-05): the mark
    keeps the card's side and the cover its clean scale."""
    ws = _ws(tmp_path, config, theme, mode)
    _home(ws)
    _frame(ws, 2)
    boxes, covers = [], []
    real_focus = launcher_layer._ui_draw.focus

    def focus(cv, th, rect, size=None, r=0, after=False, **kw):
        got = real_focus(cv, th, rect, size, r, after, **kw)
        if size == "card" and not after and rect[0] >= 0:
            boxes.append(tuple(got[1]))
        return got

    monkeypatch.setattr(launcher_layer._ui_draw, "focus", focus)
    for kind in {type(ws.sys_canvas), type(ws.glass_canvas)}:
        real = kind.cover

        def cover(cv, pix, w, sx, sy, sw, sh, dx, dy, dw, dh, *rest, _real=real):
            covers.append((dx, dy, dw, dh))
            return _real(cv, pix, w, sx, sy, sw, sh, dx, dy, dw, dh, *rest)

        monkeypatch.setattr(kind, "cover", cover)
    _frame(ws, 1)
    assert len(boxes) == 1, "one card wears the focus mark"
    assert boxes[0] in covers, "%s %s %s: the mark's box %s holds no cover (%s)" % (
        config, theme, mode, boxes[0], covers)


@pytest.mark.parametrize("theme,mode", [("moy", "dark"), ("contrast", "dark"),
                                        ("blocks", "light")])
def test_one_focus_mark_under_the_system_menu(tmp_path, theme, mode):
    """Under the system menu the screen draws one focus mark, the menu's
    (R2-11); closed, the home's comes back."""
    ws = _ws(tmp_path, HANDHELD, theme, mode)
    _home(ws)
    _frame(ws, 2)
    ws._golden_popover = True
    ws.system_key()
    assert ws.sysmenu.open
    _frame(ws, 1)
    ui_draw.marks(None, True)
    g._quiesce(ws)
    ws._dirty = True
    ws.frame(g._DT)
    assert ui_draw.marks(None, False)[1] == 1, \
        "the home behind the system menu still draws its focus"
    ws.toggle_sysmenu()
    assert not ws.sysmenu.open
    _frame(ws, 1)
    ui_draw.marks(None, True)
    g._quiesce(ws)
    ws._dirty = True
    ws.frame(g._DT)
    assert ui_draw.marks(None, False)[1] == 1, "the closed menu left the focus off"


def test_one_focus_mark_under_the_pause_menu(tmp_path):
    """...and under the pause menu."""
    ws = _ws(tmp_path, HANDHELD, "contrast", "dark")
    ws.go_home()
    ws._open_workspace(g._cart_by_title(ws, "Star Catcher"))
    ws.run(ws.project, ws.launcher_layer)
    _frame(ws, 3)
    ws.system_key()
    assert ws.pause.open
    _frame(ws, 1)
    ui_draw.marks(None, True)
    g._quiesce(ws)
    ws._dirty = True
    ws.frame(g._DT)
    assert ui_draw.marks(None, False)[1] == 1


@pytest.mark.parametrize("theme,mode", [("moy", "light"), ("contrast", "dark")])
def test_the_desk_homes_play_draws_the_icon_sets_play(tmp_path, monkeypatch, theme, mode):
    """The details pane's Play takes the icon set's play, as the dock, Files,
    the pause menu and the Editor do (R2-18)."""
    ws = _ws(tmp_path, CONSOLE, theme, mode)
    _home(ws)
    _frame(ws, 2)
    kinds = []
    real = ws.draw_icon

    def draw_icon(kind, rect, ink, cv=None):
        kinds.append(kind)
        return real(kind, rect, ink, cv)

    monkeypatch.setattr(ws, "draw_icon", draw_icon)
    _frame(ws, 1)
    assert "play" in kinds, "the details pane's Play drew the pixel triangle"


class _Rec:
    def __init__(self):
        self.rings = []

    def rrect(self, x, y, w, h, r, fill, ring=None, **kw):
        self.rings.append(ring)

    def rect(self, *a):
        pass


def test_the_star_badges_rim_is_the_dim_ink():
    """The favourite badge's chip has a rim that holds on a cover as dark as
    the chip (R2-23)."""
    th = {"surface": 3, "border": 3, "ink_dim": 9}
    cv = _Rec()
    launcher_layer._draw_star_badge(cv, {"white": 7, "light_grey": 6, "yellow": 10,
                                         "orange": 9}, th, (0, 0, 15, 15), False)
    assert cv.rings == [9], "the chip's rim must hold on a cover as dark as the chip"


def _cover_rects(ws, monkeypatch):
    """The covers one frame blits, (x, y, w, h)."""
    covers = []
    for kind in {type(ws.sys_canvas), type(ws.glass_canvas)}:
        real = kind.cover

        def cover(cv, pix, w, sx, sy, sw, sh, dx, dy, dw, dh, *rest, _real=real):
            covers.append((dx, dy, dw, dh))
            return _real(cv, pix, w, sx, sy, sw, sh, dx, dy, dw, dh, *rest)

        monkeypatch.setattr(kind, "cover", cover)
    _frame(ws, 1)
    monkeypatch.undo()
    return covers


@pytest.mark.parametrize("theme,mode", [("moy", "dark"), ("moy", "light")])
def test_the_desk_shelf_lifts_its_selected_card_under_every_input(tmp_path, monkeypatch,
                                                                 theme, mode):
    """The selected shelf card is the one the details pane describes: it
    keeps its lift under touch and while the pane's buttons hold the focus,
    as under the keys (design review R4-09); only the keys' focus counts as
    a focus mark."""
    ws = _ws(tmp_path, CONSOLE, theme, mode)
    _home(ws)
    _frame(ws, 2)
    home = ws.launcher_layer

    def lift():
        """(x of the raised card, how far it rises) on the shelf (the shelf
        itself moves with the hint strip, which touch hides)."""
        shelf = [r for r in _cover_rects(ws, monkeypatch) if r[2] < 200]
        rest = max(r[1] for r in shelf)
        up = [r for r in shelf if r[1] < rest]
        assert len(up) == 1, "one card stands raised over the shelf: %s" % shelf
        return up[0][0], rest - up[0][1]
    keys = lift()
    ws.use_inputs([("local", "touch")])
    _frame(ws, 2)
    assert lift() == keys, "touch flattens the selected card"
    ws.use_inputs([("local", "pad")])
    home._button_focus = 0
    _frame(ws, 2)
    assert lift() == keys, "the buttons' focus flattens the selected card"


@pytest.mark.parametrize("config", [HANDHELD, CONSOLE])
def test_the_new_tile_takes_a_covers_corners(tmp_path, monkeypatch, config):
    """The Make tab's + New tile is rounded as a cover of its size is, not
    square among rounded neighbours (design review R4-21)."""
    ws = _ws(tmp_path, config, "moy", "dark")
    _home(ws)
    ws.set_home_tab("make")
    _frame(ws, 2)
    calls = []
    for kind in {type(ws.sys_canvas), type(ws.glass_canvas)}:
        real = kind.rrect

        def rrect(cv, x, y, w, h, r, fill, *a, _real=real, **kw):
            calls.append((w, r, fill))
            return _real(cv, x, y, w, h, r, fill, *a, **kw)
        monkeypatch.setattr(kind, "rrect", rrect)
    _frame(ws, 1)
    focus = ws.theme_colors["focus"]
    tiles = [c for c in calls if c[2] == focus and c[0] >= 32]
    assert tiles and all(r > 0 for _w, r, _f in tiles), calls


def _glass(ws):
    """The glass's pixel at (x, y) as the canvas stores it, the form
    `_col` answers."""
    cv = ws.glass_canvas
    buf = bytes(cv._buf)
    return lambda x, y: buf[2 * (y * cv.w + x)] | (buf[2 * (y * cv.w + x) + 1] << 8)


@pytest.mark.parametrize("mode", ["dark", "light"])
@pytest.mark.parametrize("hero", [True, False])
def test_the_framed_new_tile_reads_rounded_as_a_cover(tmp_path, monkeypatch, mode, hero):
    """Where the theme casts no shadow (Contrast) the + New tile is framed:
    the page inside it keeps a same-size cover's corners and the frame runs
    round them, so both read rounded at 1x, as the hero and on the shelf,
    where a cover's corner is smallest (design review R5-10, F6-04)."""
    ws = _ws(tmp_path, HANDHELD, "contrast", mode)
    _home(ws)
    ws.set_home_tab("make")
    if not hero:
        ws.launcher.sel = 1             # the + tile on the shelf beside it
    _frame(ws, 2)
    drawn = []
    real = launcher_layer.draw_new_art

    def spy(cv, th, rect, r, *a, **kw):
        drawn.append((rect, r))
        return real(cv, th, rect, r, *a, **kw)
    monkeypatch.setattr(launcher_layer, "draw_new_art", spy)
    _frame(ws, 1)
    assert drawn, "the Make tab draws its + New tile"
    (x, y, w, h), r = drawn[-1]
    assert r > 0
    px = _glass(ws)
    cv = ws.glass_canvas
    border = cv._col(ws.theme_colors["border"])
    surface = cv._col(ws.theme_colors["surface"])
    line = 2 * ws.layout.fs
    assert px(x + w // 2, y) == border, "the frame takes the theme's border"
    ground = px(x + w // 2, y - 1)
    assert px(x, y) != border or border == ground, "the frame's outer corner is rounded"
    # the frame runs round the page's corner, its own as wide again: a
    # pixel in from the corner is still outside it, so it reads round at 1x
    assert px(x + 1, y + 1) != border or border == ground, "the frame's corner reads round"
    # the page's own corner, inside the frame, is rounded as the frame's is
    assert px(x + line, y + line) != surface
    assert px(x + line + r + 1, y + line) == surface == px(x + line, y + line + r + 1)


def test_the_star_stands_clear_of_its_chip():
    """The favourite star is about 10 pixels in a 16-pixel chip, its box
    centred, so the chip's rim stands clear of every point (R4-30)."""
    for side in (15, 17, 25):
        edge, inner = launcher_layer._star_runs(side)
        xs = [dx for dx, _dy, _w in edge + inner] + [dx + w - 1 for dx, _dy, w in edge + inner]
        ys = [dy for _dx, dy, _w in edge + inner]
        assert min(xs) >= 3 and max(xs) <= side - 4, (side, min(xs), max(xs))
        assert min(ys) >= 3 and max(ys) <= side - 4, (side, min(ys), max(ys))
        assert abs(min(xs) - (side - 1 - max(xs))) <= 1
        assert abs(min(ys) - (side - 1 - max(ys))) <= 1


def test_the_desk_facts_stand_on_the_buttons_columns(tmp_path, monkeypatch):
    """The details pane's facts, Played, Last and By, start on the left
    edges of the buttons under them (design review R4-31)."""
    ws = _ws(tmp_path, CONSOLE, "moy", "dark")
    _home(ws)
    y, m, d = g._DATE
    sel = ws.launcher.selected()
    ws.system.set("plays", {sel["path"]: [12, y * 10000 + m * 100 + d]})
    _frame(ws, 2)
    printed = {}
    for kind in {type(ws.sys_canvas), type(ws.glass_canvas)}:
        real = kind.print_scaled

        def print_scaled(cv, text, x, *a, _real=real, **kw):
            printed.setdefault(str(text), x)
            return _real(cv, text, x, *a, **kw)
        monkeypatch.setattr(kind, "print_scaled", print_scaled)
    _frame(ws, 1)
    xs = [r[0] for _v, r, _k in ws.launcher_layer._desk_buttons()]
    facts = [printed.get(k) for k in ("Played", "Last", "By")]
    assert facts == xs[:3], (facts, xs)


def test_the_narrow_editor_bar_keeps_play_clear_of_the_status(tmp_path):
    """The handheld Editor's bar: Play's icon ends 6 pixels before its zone
    does, so with the bar's gap its ink stands 10 clear of Wi-Fi's (design
    review R4-26)."""
    ws = _ws(tmp_path, HANDHELD, "moy", "dark")
    ws.open_in_editor(g._cart_by_title(ws, "Star Catcher"))
    ws.set_menu_view("code")
    _frame(ws, 2)
    app = ws.editor_app
    zone = ws.bar_layer._zone_rect("menu")
    assert app._compact(zone)
    rects = app._ladder_rects(zone)
    play = rects[-1][2]
    assert rects[-1][0] is None, "Play ends the ladder"
    assert zone[0] + zone[2] - (play[0] + play[2]) >= 6 * ws.layout.fs


@pytest.mark.parametrize("theme,mode", [("moy", "light"), ("contrast", "dark")])
def test_the_shelf_keeps_one_gap_under_the_details_at_any_window_size(tmp_path, theme,
                                                                      mode):
    """The PROJECTS window's shelf caption stands as far under the details
    block at the narrow size as at the full one, its wrapped buttons
    included, and the height the window has spare goes under the shelf
    (design review R5-08)."""
    ws = _ws(tmp_path, DESK, theme, mode)
    ui_draw.input_kind("keys")
    ws.open_desk()
    ws.open_projects()
    _frame(ws)
    win = ws.wm._wins["projects"]
    gaps = []
    for size in (None, (360, 534)):
        if size is not None:
            ws.wm._resize_window(win, *size)
            _frame(ws)
        grid = ws.projects_grid
        ws.wm._install(win.ctx)
        try:
            dk = grid._geom()[9]
            (_hx, hy, hero, _h), dy, pane = dk[0], dk[6], dk[5][5]
            foot = max(hy + hero, dy + pane)
            buttons = ws.projects_layer._desk_buttons()
            assert all(r[1] + r[3] <= foot for _v, r, _k in buttons)
            cap = grid.band_rect()[1]
            under = grid.foot()[5][1] - (dk[3] + hero // 2)
            gaps.append(cap - foot)
            assert under >= 20, "the shelf runs into the strip"
        finally:
            ws.wm._install(ws.wm._root_ctx)
    assert gaps[0] == gaps[1] == launcher_layer.SHELF_GAP, gaps


def test_wrapped_desk_buttons_take_one_width_and_keep_the_danger_apart():
    """Where the details pane's buttons wrap they take the widest one's
    width, and Delete starting a row stands a group's gap under the row
    above, the row form of its gap beside the others (design review F6-03);
    on one row each keeps its own width."""
    verbs = ("change", "copy", "delete")
    one = launcher_layer.desk_button_layout(verbs, 1000, 1, 1)
    assert [b[2] for b in one] == [0, 0, 0] and len({b[3] for b in one}) > 1
    assert all(b[5] == 0 for b in one)
    stack = launcher_layer.desk_button_layout(verbs, 220, 1, 1)
    assert [b[2] for b in stack] == [0, 1, 2]
    assert len({b[3] for b in stack}) == 1, "one width a stack"
    assert [b[5] for b in stack] == [0, 0, launcher_layer.DESK_ROW_GAP]
    assert launcher_layer.desk_button_span(220, 1, 1, 42) == \
        2 * (42 + launcher_layer.DESK_ROW_GAP) + launcher_layer.DESK_ROW_GAP


def test_the_desk_title_cuts_with_an_ellipsis(tmp_path):
    """A title wider than the details pane ends in an ellipsis, never a
    hard cut mid-letter (design review F6-03)."""
    ws = _ws(tmp_path, DESK, "moy", "light")
    was = ui_draw.print_font(ui_draw.font("title")[0])
    try:
        name = "Star Catcher copy"
        w = ui_draw.print_width(name, 2)
        assert ui_draw.print_ellipsis(name, w, 2) == name
        cut = ui_draw.print_ellipsis(name, w * 2 // 3, 2)
        assert cut.endswith(("…", "...")) and ui_draw.print_width(cut, 2) <= w * 2 // 3
    finally:
        if was is not None:
            ui_draw.print_font(was)
