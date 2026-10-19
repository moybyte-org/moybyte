"""A system modal dims what it covers (docs/look_2026-10.md, D-01): the
system menu over the handheld home and over the desk, and the pause menu
over a game. The scrim is drawn once a frame over pixels that frame painted,
so it never darkens twice: frames while a modal is open are identical, the
bars stay as they were, and closing it gives the glass back as it was."""

import os
import sys

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
    x, y = lay.cx + lay.cw - 8, lay.cy + lay.ch - 8      # content, clear of the card
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
