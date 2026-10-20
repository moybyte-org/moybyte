"""A tap on what a bar's lent zone DREW fires what was drawn there.

The zone an app draws into and the zone it hit-tests are one rect
(`BarLayer.lent_zone`), on every tier: the Editor's tab ladder, undo, redo
and Play on the handheld's glass and in the desk's window (where the strip's
zone runs on to its end), and every other zone owner a desk window holds.
The drawn items are read off the draw itself -- the rects the Editor's
`draw_zone` paints each item at -- and tapped at their centres through the
real pointer feed, so a hit test that lays the zone out on any other rect
than the draw's fails here."""

import pytest

import test_shell_goldens as g

from runtime import editor_app

DESK = "p4_1024x600_cs2_fs1_windowed"
HANDHELD = "tdeck_320x240_fs1_dark"
TAPFLOOR = "guition_tapfloor_dark"
# glyph (or icon-set slot) -> the zone action it draws
_ACTION = {glyph: tab for tab, glyph in editor_app._ZONE_TABS}
_ACTION.update({slot: _ACTION[glyph] for glyph, slot in editor_app._SET_KIND.items()})


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


def _ws(tmp_path, config, theme, inputs):
    import moy_ui
    from runtime import host_api
    moy_ui.theme_uninstall()
    cfg = dict(g.CONFIGS[config])
    cfg["theme"] = theme
    ws = g._build(cfg, tmp_path / "carts")
    ws.use_inputs([("local", inputs)])
    return ws, host_api.ConsoleDriver(ws)


def _frames(ws, drv, n=4):
    for _ in range(n):
        g._quiesce(ws)
        ws._dirty = True
        drv.frame(g._DT)


def _bar_origin(ws):
    """The glass origin of the Editor's bar: its window's content origin plus
    the bar's place in it on the desk, the bar's place on the glass
    elsewhere."""
    win = getattr(ws.wm, "_wins", {}).get("menu")
    if win is not None and getattr(ws.wm, "mode", None) == "desktop":
        cx, cy = win.content_rect()[:2]
        bar = win.ctx.layout.bars[0][1]
        return cx + bar[0], cy + bar[1]
    bar = ws.layout.bars[0][1]
    return bar[0], bar[1]


def _drawn(ws, drv, monkeypatch):
    """[(action, rect)] of what the Editor's last zone draw painted, each
    item's rect in its bar's coordinates: a tab's chip or icon, undo's and
    redo's icons, Play's plate, or its glyph and the word beside it."""
    app = ws.editor_app
    got = []
    inside = []          # a draw helper that paints through ws._icon is running
    band = []            # the rect the draw was lent

    def zone(cv, rect):
        del got[:]
        band[:] = [rect]
        return real_zone(cv, rect)

    def tab(cv, th, r, active, label="", labels_on=True, *a, **kw):
        got.append((_ACTION[kw["kind"]], r))
        return real_tab(cv, th, r, active, label, labels_on, *a, **kw)

    def button(cv, th, r, label, *a, **kw):
        got.append((None, r))
        return real_button(cv, th, r, label, *a, **kw)

    def history(cv, glyph, x, y, ic, enabled):
        got.append((_ACTION[glyph], (x, y, ic, ic)))
        inside.append(1)
        try:
            return real_history(cv, glyph, x, y, ic, enabled)
        finally:
            inside.pop()

    def play(cv, rect, ink):
        got.append((None, rect))
        return real_play(cv, rect, ink)

    def bare(cv, r):
        real_bare(cv, r)
        # the word, a gap right of the glyph: Play's other half
        fs = app._zone_scale()
        x, y, w, h = got[-1][1]
        got.append((None, (x + w + 4 * fs, y,
                           editor_app._ui_draw.print_width("Play", fs), h)))

    def icon(kind, x, y, cv, *a, **kw):
        if not inside and kind in _ACTION:
            side = band[0][3]          # the ladder's icons are as tall as its zone
            got.append((_ACTION[kind], (x, y, side, side)))
        return real_icon(kind, x, y, cv, *a, **kw)

    real_zone = app.draw_zone
    real_tab, real_button = editor_app._ui_draw.tab, editor_app._ui_draw.button
    real_history, real_play, real_bare = app._draw_history_icon, app._draw_play, app._bare_play
    real_icon = ws._icon
    with monkeypatch.context() as m:
        m.setattr(app, "draw_zone", zone)
        m.setattr(editor_app._ui_draw, "tab", tab)
        m.setattr(editor_app._ui_draw, "button", button)
        m.setattr(app, "_draw_history_icon", history)
        m.setattr(app, "_draw_play", play)
        m.setattr(app, "_bare_play", bare)
        m.setattr(ws, "_icon", icon)
        ws.bar_layer.invalidate()
        _frames(ws, drv, 2)
    return list(got)


def _taps(ws, drv, monkeypatch, drawn):
    """[(action drawn, its rect, [the actions its centre's tap fired])]."""
    app = ws.editor_app
    fired = []
    monkeypatch.setattr(app, "_activate_zone_tab",
                        lambda tab: fired.append(tab) or True)
    ox, oy = _bar_origin(ws)
    out = []
    for action, (x, y, w, h) in drawn:
        del fired[:]
        drv.click(ox + x + w // 2, oy + y + h // 2)
        _frames(ws, drv, 2)
        out.append((action, (x, y, w, h), list(fired)))
    return out


def _editor(ws, drv, desk, size=None):
    if desk:
        ws.open_desk()
        _frames(ws, drv)
    ws.open_in_editor(g._cart_by_title(ws, "Star Catcher"))
    _frames(ws, drv)
    if desk:
        win = ws.wm._wins["menu"]
        assert getattr(ws.wm, "mode", None) == "desktop" and ws.wm.input_holder() == "menu"
        if size is not None:
            win.x, win.y = 160, 60
            ws.wm._resize_window(win, *size)
            _frames(ws, drv)
        assert win.content_rect()[:2] != (0, 0)


@pytest.mark.parametrize("config,inputs,size", [
    (DESK, "touch", None),            # the P4's Editor window, the labelled shelf
    (DESK, "touch", (520, 400)),      # a narrow window: the icon ladder
    (HANDHELD, "touch", None),
    (HANDHELD, "tdeck", None),        # the T-Deck's trackball pointer
    (TAPFLOOR, "touch", None),        # chrome scale above the font's (#203)
])
@pytest.mark.parametrize("theme", ["moy", "blocks", "contrast"])
def test_a_tap_on_a_drawn_editor_bar_item_fires_that_item(tmp_path, monkeypatch,
                                                          config, inputs, size, theme):
    ws, drv = _ws(tmp_path, config, theme, inputs)
    _editor(ws, drv, config == DESK, size)
    drawn = _drawn(ws, drv, monkeypatch)
    actions = [a for a, _r in drawn]
    assert "cards" in actions and len(set(actions)) >= 5, drawn
    if config != TAPFLOOR:        # its ladder holds the tabs and stops there
        assert None in actions and editor_app._ZONE_UNDO in actions, drawn
    taps = _taps(ws, drv, monkeypatch, drawn)
    wrong = [t for t in taps if t[2] != [t[0]]]
    assert not wrong, ("drawn item, its rect, what its tap fired", wrong)


@pytest.mark.parametrize("where,opener", [
    ("menu", lambda ws: ws.open_in_editor(g._cart_by_title(ws, "Star Catcher"))),
    ("projects", lambda ws: ws.open_projects()),
    ("settings", lambda ws: ws.open_settings()),
])
def test_a_desk_window_zone_is_tapped_on_the_rect_it_is_drawn_on(tmp_path, monkeypatch,
                                                                 where, opener):
    """Every zone owner a desk window holds is handed one rect for its draw
    and its tap, at the zone's far end as at its head."""
    ws, drv = _ws(tmp_path, DESK, "moy", "touch")
    ws.open_desk()
    _frames(ws, drv)
    opener(ws)
    _frames(ws, drv)
    assert ws.bar_layer._in_window(where)
    owner = ws.bar_layer._zone_owner(where)
    drew, tapped = [], []
    real_draw = owner.draw_zone
    monkeypatch.setattr(owner, "draw_zone",
                        lambda cv, rect: (drew.append(rect), real_draw(cv, rect))[1])
    monkeypatch.setattr(owner, "zone_tap",
                        lambda px, py, rect=None: (tapped.append(rect), False)[1])
    ws.bar_layer.invalidate()
    _frames(ws, drv, 2)
    assert drew, "the window's bar drew no zone"
    win = ws.wm._wins[ws.wm.input_holder()]
    cx, cy = win.content_rect()[:2]
    bar = win.ctx.layout.bars[0][1]
    x, y, w, h = drew[-1]
    for px in (x + 4, x + w - 4):
        del tapped[:]
        drv.click(cx + bar[0] + px, cy + bar[1] + y + h // 2)
        _frames(ws, drv, 2)
        assert tapped == [drew[-1]], (px, tapped, drew[-1])
