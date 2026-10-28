"""A system modal dims what it covers (docs/look_2026-10.md, D-01): the
system menu over the handheld home and over the desk, and the pause menu
over a game. The scrim is drawn once a frame over pixels that frame painted,
so it never darkens twice: frames while a modal is open are identical, the
bars stay as they were, and closing it gives the glass back as it was."""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import test_shell_goldens as g  # noqa: E402

HANDHELD = "tdeck_320x240_fs1_dark"
DESK = "p4_1024x600_cs2_fs1_windowed"


def _ws(tmp_path, config, mode="light"):
    cfg = dict(g.CONFIGS[config], variant=mode)
    cfg["theme"] = "moy"
    return g._build(cfg, tmp_path / "carts")


def _frames(ws, n, dirty=True):
    """`n` frames, each quiesced as the goldens' are; the glass after each."""
    out = []
    for _ in range(n):
        g._quiesce(ws)
        if dirty:
            ws._dirty = True
        ws.frame(g._DT)
        out.append(bytes(ws.glass_canvas._buf))
    return out


def _px(buf, w, x, y):
    o = 2 * (y * w + x)
    return buf[o] << 8 | buf[o + 1]


def _lum(v):
    return ((v >> 11) & 31) * 2 + ((v >> 5) & 63) + (v & 31) * 2


def _open_menu(ws):
    ws._golden_popover = True             # the quiesce keeps it open
    ws.system_key()
    assert ws.sysmenu.open


def _close_menu(ws):
    ws.sysmenu.close()
    ws.popover_touch()
    ws._golden_popover = False


def _check_menu(ws, frames):
    cv = ws.glass_canvas
    lay = ws.layout
    before = _frames(ws, 3)[-1]
    _open_menu(ws)
    first = _frames(ws, 3)[-1]           # its first frame lays out, the next settle
    # the menu's own frames: a dirty one and the popover's quiet ones alike
    again = _frames(ws, frames) + _frames(ws, frames, dirty=False)
    for got in again:
        assert got == first, "the scrim darkens once, whatever repaints"
    sx, sy, sw, sh = ws.menu_ui.surface()                # the content less the home's strip
    x, y = sx + sw - 8, sy + sh - 8                      # clear of the card
    a, b = _px(before, cv.w, x, y), _px(first, cv.w, x, y)
    assert _lum(b) < _lum(a), "the content under the menu dims"
    bx = lay.cx + lay.cw - 2
    by = max(0, lay.cy - 2)
    assert _px(before, cv.w, bx, by) == _px(first, cv.w, bx, by), "the bar does not dim"
    _close_menu(ws)
    after = _frames(ws, frames, dirty=False) + _frames(ws, 2)
    assert after[-1] == before, "closing gives the glass back as it was"


def test_the_handheld_home_dims_under_the_system_menu(tmp_path):
    ws = _ws(tmp_path, HANDHELD)
    ws.go_home()
    _check_menu(ws, 3)


def test_the_desk_dims_under_the_system_menu_once(tmp_path):
    ws = _ws(tmp_path, DESK)
    ws.open_desk()
    ws.open_app(ws._apps_by_id["files"])
    _frames(ws, 2)
    ws.open_app(ws._apps_by_id["calc"])   # Files behind it: a window the desk may retain
    _frames(ws, 6)
    _check_menu(ws, 4)


def test_the_paused_game_dims_once_and_comes_back_whole(tmp_path):
    ws = _ws(tmp_path, HANDHELD, "dark")
    ws.go_home()
    ws._open_workspace(g._cart_by_title(ws, "Star Catcher"))
    ws.run(ws.project, ws.launcher_layer)
    _frames(ws, 6, dirty=False)
    cv = ws.glass_canvas
    frozen = bytes(cv._buf)               # the frame the pause holds
    ws.system_key()
    assert ws.pause.open
    shown = _frames(ws, 4)
    assert len(set(shown[1:])) == 1, "the scrim darkens the frozen frame once"
    lit = [(x, y) for y in range(0, cv.h, 7) for x in range(0, cv.w, 7)
           if _lum(_px(frozen, cv.w, x, y)) > 40]
    x, y = lit[0]
    assert _lum(_px(shown[0], cv.w, x, y)) < _lum(_px(frozen, cv.w, x, y)), "the game dims"
    ws.pause.close()
    assert bytes(cv._buf) == frozen, "the resumed game finds its frame whole"


def _rgb(v):
    return ((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31


def _contrast(a, b):
    def lum(v):
        out = 0.0
        for c, k in zip(_rgb(v), (0.2126, 0.7152, 0.0722)):
            c /= 255
            out += k * (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        return out
    la, lb = sorted((lum(a), lum(b)))
    return (lb + 0.05) / (la + 0.05)


@pytest.mark.parametrize("theme,mode", [("moy", "light"), ("moy", "dark"), ("blocks", "light"),
                                        ("contrast", "light"), ("contrast", "dark")])
@pytest.mark.parametrize("menu", ["system", "card"])
def test_the_home_strip_stays_unwashed_under_its_menus(tmp_path, theme, mode, menu):
    """The hint strip names the open menu's keys, so the scrim stops at its
    top as it does at the bars (design review R4-05): its ink holds 4.5:1
    on its ground under the system menu and under a card's menu."""
    cfg = dict(g.CONFIGS[HANDHELD], variant=mode)
    cfg["theme"] = theme
    ws = g._build(cfg, tmp_path / "carts")
    ws.go_home()
    _frames(ws, 2)
    ws._golden_popover = True
    if menu == "system":
        ws.system_key()
    else:
        assert ws.menu_ui.open_card(ws.launcher_layer, (ws.layout.cw // 2, ws.layout.cy + 20))
    assert ws.sysmenu.open and ws.menu_ui.looked()
    shown = _frames(ws, 3)[-1]
    cv = ws.glass_canvas
    x0, y0, w, h = ws.launcher_layer.grid.foot()[5]
    hist = {}
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            v = _px(shown, cv.w, x, y)
            hist[v] = hist.get(v, 0) + 1
    ground = max(hist, key=hist.get)
    best = max(_contrast(v, ground) for v in hist)
    assert best >= 4.5, "%s %s: the strip's ink %.2f:1" % (theme, mode, best)
    _close_menu(ws)


def _projects_own(tmp_path, theme, mode):
    """The desk's PROJECTS window on a project of the kid's own."""
    from runtime.moy_store_base import builtin_name
    cfg = dict(g.CONFIGS[DESK], variant=mode)
    cfg["theme"] = theme
    ws = g._build(cfg, tmp_path / "carts")
    ws.carts.dup(g._cart_by_title(ws, "Star Catcher"))
    ws.open_desk()
    ws.open_projects()
    _frames(ws, 2)
    grid = ws.projects_grid
    grid.sel = next(i for i, it in enumerate(grid.items)
                    if it.get("path") and builtin_name(it["path"]) is None)
    _frames(ws, 2)
    return ws


@pytest.mark.parametrize("theme,mode", [("moy", "light"), ("moy", "dark"), ("blocks", "light"),
                                        ("contrast", "light"), ("contrast", "dark")])
@pytest.mark.parametrize("menu", ["system", "card"])
def test_the_projects_strip_stays_unwashed_under_its_menus(tmp_path, theme, mode, menu):
    """On the desk the PROJECTS window's hint strip names the open menu's
    keys too, so the scrim washes around it (design review R5-03): its ink
    holds 4.5:1 on its ground under the card's menu and under the system
    menu over the window."""
    ws = _projects_own(tmp_path, theme, mode)
    ws._golden_popover = True
    if menu == "system":
        ws.system_key()
    else:
        assert ws.projects_layer.open_card_menu()
    assert ws.sysmenu.open and ws.menu_ui.looked()
    shown = _frames(ws, 3)[-1]
    cv = ws.glass_canvas
    proj = ws.menu_ui._projects()
    assert proj is not None and proj[1] is not None, "the window shows its strip"
    x0, y0, w, h = proj[1]
    hist = {}
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            v = _px(shown, cv.w, x, y)
            hist[v] = hist.get(v, 0) + 1
    ground = max(hist, key=hist.get)
    best = max(_contrast(v, ground) for v in hist)
    assert best >= 4.5, "%s %s: the strip's ink %.2f:1" % (theme, mode, best)
    # the rest of the window still dims: its lit pixels above the strip,
    # clear of the menu's card and its 2 px drop
    card = ws.menu_ui.look_card()
    card = (card[0], card[1], card[2], card[3] + 2 * ws.layout.fs)
    _close_menu(ws)
    clear = _frames(ws, 3)[-1]
    lit = [(x, y) for y in range(y0 - 120, y0, 3) for x in range(x0, x0 + w, 3)
           if _lum(_px(clear, cv.w, x, y)) > 40
           and not (card[0] <= x < card[0] + card[2] and card[1] <= y < card[1] + card[3])]
    assert lit and all(_lum(_px(shown, cv.w, x, y)) < _lum(_px(clear, cv.w, x, y))
                       for x, y in lit), "the window above its strip dims"
