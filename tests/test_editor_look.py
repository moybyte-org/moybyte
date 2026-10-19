"""The Editor's tabs and the desk apps as the glass shows them, under every
seed theme and mode, on the shell the goldens build (`tests/test_shell_goldens.py`'s
configs and pinning): the measurements a design review takes off a render,
taken here off the same frame."""

import pytest

import test_shell_goldens as g

THEMES = [("moy", "dark"), ("moy", "light"), ("blocks", "light"),
          ("contrast", "dark"), ("contrast", "light")]
HANDHELD = "tdeck_320x240_fs1_dark"
DESK = "p4_1024x600_cs2_fs1_windowed"
# The desk Editor's window and the Code tab's caret, the targets'
# (tools/look_shots.py's `_code`): line 42, column 13, line 34 the first shown.
EDITOR_WIN = (112, 54, 900, 534)
CODE_TOP, CODE_ROW, CODE_COL = 33, 41, 12


@pytest.fixture(autouse=True)
def _uninstall(monkeypatch):
    from runtime import cover_cache
    monkeypatch.setattr(cover_cache, "_COVER_SLICE_MS", g._COVER_SLICE_UNBOUNDED)
    yield
    import moy_ui
    from runtime import iconsets, ui_draw
    moy_ui.theme_uninstall()
    iconsets.reset()
    ui_draw.input_kind("keys")


def _ws(tmp_path, config, theme, mode):
    import moy_ui
    moy_ui.theme_uninstall()
    cfg = dict(g.CONFIGS[config], variant=mode)
    if "layout" not in cfg:
        cfg["theme"] = theme
    return g._build(cfg, tmp_path / "carts")


def _frame(ws, n=4):
    for _ in range(n):
        g._quiesce(ws)
        ws._dirty = True
        ws.frame(g._DT)


def _px(ws):
    """The glass as rows of (r, g, b)."""
    cv = ws.glass_canvas
    buf = cv._buf
    out = []
    for y in range(cv.h):
        row = []
        for x in range(cv.w):
            i = (y * cv.w + x) * 2
            v = (buf[i] << 8) | buf[i + 1]
            row.append(((v >> 11) & 31, (v >> 5) & 63, v & 31))
        out.append(row)
    return out


def _ink_left(px, x0, x1, y0, y1, ground=None):
    """The first column in [x0, x1) where a row of [y0, y1) leaves its ground
    (the row's pixel at x0, or `ground`), or None."""
    for x in range(x0, x1):
        for y in range(y0, y1):
            if px[y][x] != (px[y][x0] if ground is None else ground):
                return x
    return None


def _place(ws, key, rect):
    win = ws.wm._wins[key]
    win.x, win.y = rect[0], rect[1]
    ws.wm._resize_window(win, rect[2], rect[3])
    return win


def _editor(ws, config, tab):
    """The Editor on Star Catcher's `tab`: fullscreen on the handheld, the
    target's window on the desk. (its CodeLayout, the glass x of its content's
    left, the glass y of its content's top)."""
    desk = config == DESK
    if desk:
        ws.open_desk()
    ws.open_in_editor(g._cart_by_title(ws, "Star Catcher"))
    ws.set_menu_view(tab)
    if tab == "code":
        ed = ws.editor
        ed.row, ed.col, ed.top = CODE_ROW, CODE_COL, CODE_TOP
    if not desk:
        _frame(ws)
        return ws.code_layout, 0, 0
    _frame(ws, 2)
    win = _place(ws, "menu", EDITOR_WIN)
    _frame(ws)
    cx, cy, _w, _h = win.content_rect()
    return win.ctx.code_layout, cx, cy


def _files(ws, cur=3):
    """The desk's Files window on Projects, file row `cur` current. (the
    app, its window)."""
    ws.open_desk()
    ws.open_app(ws._apps_by_id["files"])
    _frame(ws, 2)
    app = ws._apps_by_id["files"]
    app = getattr(app, "app", app)
    app.cur = cur
    _frame(ws)
    return app, ws.wm._wins["files"]


@pytest.mark.parametrize("theme,mode", THEMES)
def test_a_row_mark_stands_off_the_frame_and_the_divider(tmp_path, theme, mode):
    """No content pixel lies under a window's frame, and the current place
    and the current file keep three columns of the page's ground between
    their mark and the frame or the divider beside them (design review
    R3-25: Blocks' slivers fused with both)."""
    ws = _ws(tmp_path, DESK, theme, mode)
    app, win = _files(ws)
    lay = app.layout
    fs = lay.fs
    cx, cy, cw, ch = win.content_rect()
    px = _px(ws)
    side, row_h = lay.side, lay.row_h
    divider = cx + side[0] + side[2]                  # the rule's column
    for name, x0, y in (("places", cx, cy + side[1]),
                        ("files", divider + fs, cy + lay.list[1])):
        # every row of the list's top 6 rows: three ground columns first
        for yy in range(y, y + 6 * row_h):
            run = px[yy][x0:x0 + 3 * fs]
            assert len(set(run)) == 1, (name, yy - cy, run)
            assert run[0] == px[cy + ch - 2][x0], (name, yy - cy, run[0])
