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
def _uninstall(monkeypatch):
    from runtime import cover_cache
    monkeypatch.setattr(cover_cache, "_COVER_SLICE_MS", g._COVER_SLICE_UNBOUNDED)
    yield
    import moy_ui
    moy_ui.theme_uninstall()


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

    def focus(cv, th, rect, size=None, r=0, after=False, fs=None, ring=False):
        got = real(cv, th, rect, size, r, after, fs, ring)
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
    assert ui_draw.marks(None, False)[2] == 1, \
        "the home behind the system menu still draws its focus"
    ws.toggle_sysmenu()
    assert not ws.sysmenu.open
    _frame(ws, 1)
    ui_draw.marks(None, True)
    g._quiesce(ws)
    ws._dirty = True
    ws.frame(g._DT)
    assert ui_draw.marks(None, False)[2] == 1, "the closed menu left the focus off"


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
    assert ui_draw.marks(None, False)[2] == 1


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
