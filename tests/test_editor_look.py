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


def _rgb(cv, colour):
    """Colour index (or role value) `colour` as `_px` reads it: the canvas
    word is the glass's big-endian RGB565 read as a native one."""
    w = cv._col(colour)
    v = ((w & 0xFF) << 8) | (w >> 8)
    return ((v >> 11) & 31, (v >> 5) & 63, v & 31)


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


@pytest.mark.parametrize("config", (HANDHELD, DESK))
@pytest.mark.parametrize("theme,mode", THEMES)
def test_the_code_tab_keeps_one_left_inset(tmp_path, config, theme, mode):
    """The code, the status line, the symbol keys and (on the desk) the tab
    strip start on one column at least 8 px in from the content's left
    edge, and the current line's mark stands inside that inset, clear of
    the frame (design review R3-10)."""
    ws = _ws(tmp_path, config, theme, mode)
    lay, ox, oy = _editor(ws, config, "code")
    ed = ws.editor
    fs = lay.fs
    x0 = lay.x0
    assert x0 - lay.cx >= 8 * fs
    px = _px(ws)
    # An unindented line that is not the current one: its line number
    # ends a cell before the code, which starts on the text column, each
    # glyph's bearing at most fs in; the widest number starts on x0.
    other = next(i for i in range(lay.rows) if ed.top + i != ed.row
                 and ed.lines[ed.top + i][:1].isalpha())
    y = oy + lay.y0 + other * lay.lh
    tx0 = ws.code_layer._text_x0(lay, ed)
    gc = ws.code_layer._gutter_cols(ed)
    assert gc and tx0 == x0 + gc * lay.cell
    num = _ink_left(px, ox + lay.cx + 1, ox + lay.cx + lay.cw, y, y + lay.lh)
    digits = len(str(ed.top + other + 1))
    at = x0 + (gc - 1 - digits) * lay.cell
    assert at <= num - ox <= at + fs, ("number", num - ox, at)
    code = _ink_left(px, ox + tx0 - lay.cell // 2, ox + lay.cx + lay.cw, y, y + lay.lh)
    assert tx0 <= code - ox <= tx0 + fs, ("code", code - ox, tx0)
    sx, sy, _sw, sh = lay.status_band
    status = _ink_left(px, ox + sx + 1, ox + sx + 200, oy + sy + 1, oy + sy + sh)
    assert x0 <= status - ox <= x0 + fs, ("status", status - ox, x0)
    # The symbol keys: the first key's edge on x0, the last one's on the
    # inset from the right.
    assert lay.sym_key_x(0) + fs == x0
    n = len(ws.code_layer._symbols())
    assert lay.sym_key_x(n) - fs == lay.cx + lay.cw - (x0 - lay.cx)
    for i in range(n):
        assert lay.sym_key_at(lay.sym_key_x(i)) == i
        assert lay.sym_key_at(lay.sym_key_x(i + 1) - 1) == i
    ky = oy + lay.sym_y + lay.sym_h // 2
    key = _ink_left(px, ox + lay.cx + 1, ox + lay.cx + lay.cw, ky, ky + 1)
    assert key - ox == x0, ("keys", key - ox, x0)
    # The current line's mark: inside the inset, 3 px or more off the frame.
    assert lay.cx + 3 * fs <= lay.mark_x and lay.mark_x + 2 * fs < x0
    cy = oy + lay.y0 + (ed.row - ed.top) * lay.lh + lay.lh // 2
    mark = _ink_left(px, ox + lay.cx, ox + x0, cy, cy + 1)
    assert mark - ox == lay.mark_x, ("mark", mark - ox, lay.mark_x)
    if config == DESK:
        # the tab strip: the window's band above the content
        band = ws.wm._wins["menu"].ctx.layout.bars[0][1]
        ty = oy + band[1] + band[3] // 2
        tab = _ink_left(px, ox + band[0] + 1, ox + band[0] + 200, ty - 6, ty + 6)
        assert x0 <= tab - ox <= x0 + fs, ("tabs", tab - ox, x0)


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
    # the frame's foot fills every row under the content to its inside edge
    mx = cx + cw // 2
    foot = [px[y][mx] for y in range(cy + ch, win.y + win.h - 1 - win.edge)]
    assert len(set(foot)) <= 1, foot
    for name, x0, y in (("places", cx, cy + side[1]),
                        ("files", divider + fs, cy + lay.list[1])):
        # every row of the list's top 6 rows: three ground columns first
        for yy in range(y, y + 6 * row_h):
            run = px[yy][x0:x0 + 3 * fs]
            assert len(set(run)) == 1, (name, yy - cy, run)
            assert run[0] == px[cy + ch - 2][x0], (name, yy - cy, run[0])


@pytest.mark.parametrize("theme,mode", THEMES)
def test_the_caret_stands_a_column_off_the_next_stem(tmp_path, theme, mode):
    """The caret before "b" (the targets' caret, line 42 column 13) is one
    column wide and a ground column stands between it and the stem, so the
    stem does not read as bold (design review R3-24)."""
    ws = _ws(tmp_path, HANDHELD, theme, mode)
    lay, ox, oy = _editor(ws, HANDHELD, "code")
    ed = ws.editor
    assert ed.lines[ed.row][ed.col] == "b"
    fs = lay.fs
    cv = ws.glass_canvas
    px = _px(ws)
    caret = _rgb(cv, ws.code_layer._tones()["caret"])
    y = oy + lay.y0 + (ed.row - ed.top) * lay.lh
    x = ws.code_layer._text_x0(lay, ed) + (ed.col - ed.left) * lay.cell
    cols = [c for c in range(x - lay.cell, x + lay.cell)
            if all(px[yy][c] == caret for yy in range(y + fs, y + lay.text_h - fs))]
    assert len(cols) == fs, cols
    ground = px[y + lay.text_h // 2][cols[-1] + 1]
    stem = next(c for c in range(cols[-1] + 1, x + lay.cell)
                if any(px[yy][c] != ground for yy in range(y + fs, y + lay.text_h - fs)))
    assert stem - cols[-1] >= 2, (cols, stem)


@pytest.mark.parametrize("theme,mode", THEMES)
def test_play_carries_as_much_full_ink_as_a_tab_icon(tmp_path, theme, mode):
    """The narrow bar's Play, drawn in one ink beside the tab icons, has at
    least as many pixels of that full ink as the lightest tab icon: its 16 px
    mask's diagonals no longer antialias it to a grey the tabs do not wear,
    which Contrast reads as disabled (design review R3-26)."""
    from runtime.editor_app import _SET_KIND, _ZONE_TABS
    ws = _ws(tmp_path, HANDHELD, theme, mode)
    _editor(ws, HANDHELD, "code")
    cv, th = ws.sys_canvas, ws.theme_colors
    ink = th["chrome_ink"]
    word = cv._col(ink)

    def full(draw):
        cv.rect(0, 0, 32, 32, th["surface"])
        draw((8, 8, 16, 16))
        n = 0
        for y in range(32):
            for x in range(32):
                i = (y * cv._stride + x) * 2
                n += (cv._buf[i] | (cv._buf[i + 1] << 8)) == word
        return n

    tabs = [full(lambda r, k=k: ws.draw_icon(_SET_KIND.get(k, k), r, ink, cv))
            for t, k in _ZONE_TABS if isinstance(t, str) and not t.startswith("\x00")]
    play = full(lambda r: ws.editor_app._draw_play(cv, r, ink))
    assert play >= min(tabs), (play, tabs)
    bare = full(lambda r: ws.draw_icon("play", r, ink, cv))
    assert bare < min(tabs), ("the set's 16 px play carries full ink of its own now: "
                              "_draw_play's second pass can go", bare, tabs)


def _sprites(ws, config):
    """The Sprites tab, as the targets show it (tools/look_shots.py's
    `_sprites`). (its PaintLayout, the glass origin of its content)."""
    desk = config == DESK
    if desk:
        ws.open_desk()
    ws.open_in_editor(g._cart_by_title(ws, "Star Catcher"))
    ws.set_menu_view("paint")
    if not desk:
        _frame(ws)
        return ws.paint_layer.layout, 0, 0
    _frame(ws, 2)
    win = _place(ws, "menu", EDITOR_WIN)
    _frame(ws)
    cx, cy, _w, _h = win.content_rect()
    return win.ctx.paint_layout, cx, cy


@pytest.mark.parametrize("config", (HANDHELD, DESK))
@pytest.mark.parametrize("theme,mode", THEMES)
def test_every_enabled_sprite_tool_wears_the_chrome_ink(tmp_path, config, theme, mode):
    """The sprite tools' icons take one ink role, the chrome's: no tool in
    the dim ink and no trash in the danger ink, louder than its row (design
    review R3-11). The tool in use wears the current mark; copy and paste,
    with nothing to copy or paste, the disabled ink."""
    from runtime import paint_layer, ui_draw
    ws = _ws(tmp_path, config, theme, mode)
    lay, ox, oy = _sprites(ws, config)
    th = ws.theme_colors
    cv = ws.glass_canvas
    px = _px(ws)
    ink = _rgb(cv, th["chrome_ink"])
    others = {_rgb(cv, th[r]) for r in ("ink_dim", "chrome_ink_dim")}
    others.add(_rgb(cv, ui_draw.button_ink(th, "danger")))
    others.discard(ink)
    pe = ws.paint
    for tid, (x, y, w, h) in zip(paint_layer._TOOLS, lay.tool_btns):
        if tid == "stamp" or ws.paint_layer._tool_on(pe, tid):
            continue
        seen = {px[oy + yy][ox + xx] for yy in range(y, y + h) for xx in range(x, x + w)}
        assert ink in seen, tid
        assert not (seen & others), (tid, seen & others)


@pytest.mark.parametrize("config", (HANDHELD, DESK))
@pytest.mark.parametrize("theme,mode", THEMES)
def test_the_sprite_tool_row_keeps_the_mockups_spacing(tmp_path, config, theme, mode):
    """The one tool row (owner, 2026-10-19) stands at the mockup's pitch,
    48 px on the desk and 24 on the handheld, from the grid's left edge
    under the grid or from the column's at its head."""
    from runtime import paint_layer
    ws = _ws(tmp_path, config, theme, mode)
    lay, _ox, _oy = _sprites(ws, config)
    row = lay.tool_btns
    assert len(row) == len(paint_layer._TOOL_ROW)
    pitch = 48 if config == DESK else 24 * lay.fs
    assert {b[0] - a[0] for a, b in zip(row, row[1:])} == {pitch}
    assert row[0][0] in (lay.pg_x0, lay.spr_prev[0])


# The desk Appearance window, the target's (tools/look_shots.py's
# `_desk_appearance`).
APPEARANCE_WIN = (226, 65, 680, 510)


def _desk_appearance(ws):
    ws.open_desk()
    ws.open_app(ws._apps_by_id["appearance"])
    _frame(ws, 2)
    win = _place(ws, "appearance", APPEARANCE_WIN)
    _frame(ws)
    app = ws._apps_by_id["appearance"]
    return getattr(app, "app", app), win


@pytest.mark.parametrize("theme,mode", THEMES)
def test_the_desk_appearance_foot_sits_on_the_rows_column_and_inset(tmp_path, theme, mode):
    """The window's foot: the theme's status starts on the rows' label
    column (where a desk row's label starts), and stands centred between
    the foot's rule and the frame's bottom, as far from the bottom as the
    rows from the sides (design review R3-27: 2 px left of the rows, 27 px
    above the bottom)."""
    ws = _ws(tmp_path, DESK, theme, mode)
    app, win = _desk_appearance(ws)
    lay = win.ctx.app_layouts["appearance"]
    cx, cy, _cw, _ch = win.content_rect()
    px = _px(ws)
    bx, _by, bw, _bh = lay.body
    fx, fy, _fw, fh = lay.foot
    ground = px[cy + fy + 4][cx + bx + bw // 2]
    # the status's ink: the rows between the rule and the frame's bottom,
    # left of the hints
    rule = cy + fy
    bottom = win.y + win.h - 1 - win.edge             # the frame's inside bottom edge
    cols = range(cx + bx, cx + bx + bw // 2)
    rows = [y for y in range(rule + 1, bottom) if any(px[y][x] != ground for x in cols)]
    assert rows, "no status"
    above, below = rows[0] - rule - 1, bottom - rows[-1] - 1
    # centred by the face's line box, which holds its ink a pixel or two
    # off its middle
    assert abs(above - below) <= 3, (above, below)
    assert below <= 17, below
    # on the rows' label column: the desk rows' label inset, style.json's
    # group pad
    from runtime import ui_draw
    pad = ui_draw.metrics("group")[1] * lay.fs
    x0 = _ink_left(px, cx + bx, cx + bx + bw // 2, rows[0], rows[-1] + 1, ground)
    assert 0 <= x0 - (cx + bx + pad) <= lay.fs, (x0 - cx - bx, pad)


@pytest.mark.parametrize("app_id", ("appearance", "calc"))
@pytest.mark.parametrize("theme,mode", THEMES)
def test_under_touch_a_handheld_app_keeps_no_hint_band(tmp_path, app_id, theme, mode):
    """Touch draws no keycaps, so Appearance and Calc on the handheld keep no
    empty band for them: the content's last 20 rows hold the page (design
    review R3-07); a key input brings the hints back."""
    ws = _ws(tmp_path, HANDHELD, theme, mode)
    ws.use_inputs([("local", "touch")])
    ws.open_app(ws._apps_by_id[app_id])
    _frame(ws)
    lay = ws.layout
    px = _px(ws)
    band = {px[y][x] for y in range(lay.cy + lay.ch - 20, lay.cy + lay.ch)
            for x in range(lay.cx, lay.cx + lay.cw)}
    assert len(band) > 1, "an empty band under touch"
    ws.use_inputs([("local", "keys")])
    _frame(ws)
    px = _px(ws)
    band = {px[y][x] for y in range(lay.cy + lay.ch - 20, lay.cy + lay.ch)
            for x in range(lay.cx, lay.cx + lay.cw)}
    assert len(band) > 1, "no hints under keys"


def _ink_box(px, rect, ground):
    """(x0, y0, x1, y1) of the pixels in `rect` that are not `ground`."""
    x, y, w, h = rect
    pts = [(xx, yy) for yy in range(y, y + h) for xx in range(x, x + w)
           if px[yy][xx] != ground]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


@pytest.mark.xfail(strict=True, reason=(
    "the engine draws a button's icon in a button.icon-sized box (16 px), "
    "style.json's and moy_ui_draw.c's: the desk's backspace needs the box at "
    "the key face's cap height (design review R3-09, Batch A)"))
@pytest.mark.parametrize("theme,mode", THEMES)
def test_the_desk_calcs_backspace_stands_as_tall_as_its_digits(tmp_path, theme, mode):
    """The desk Calc's ⌫ is as tall as the digits beside it, less a pixel
    (design review R3-09: a 12 px icon beside 15 px digits)."""
    ws = _ws(tmp_path, DESK, theme, mode)
    ws.open_desk()
    ws.open_app(ws._apps_by_id["calc"])
    _frame(ws)
    win = ws.wm._wins["calc"]
    lay = win.ctx.app_layouts["calc"]
    cx, cy, _cw, _ch = win.content_rect()
    px = _px(ws)

    def ink(label):
        x, y, w, h = lay.keys[lay.labels.index(label)]
        x, y = x + cx, y + cy
        # inside the key's rounded face, clear of its edge
        inner = (x + w // 4, y + 4, w // 2, h - 8)
        return _ink_box(px, inner, px[y + h // 2][x + 6])

    back = ink("<")
    seven = ink("7")
    assert back[3] - back[1] >= seven[3] - seven[1] - 1, (back, seven)
