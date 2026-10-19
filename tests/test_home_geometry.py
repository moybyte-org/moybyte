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
