"""Every desk popover's card lies within the glass (runtime/system/popover.py,
`Workstation.popover_room`), at font scales 1 and 2, on the P4's desk
(1024x600) and the Guition P4's (1280x800), hung from a top bar and from a
bottom one: a card whose rows the glass cannot hold takes the room it has,
its rows tighter (the month) or scrolling (Wi-Fi, a status menu)."""

import json

import pytest

import moybyte
from runtime import moy_log
from runtime import ui_draw
from runtime.status_items import ITEMS, ROWS_MAX, parse
from ws_helpers import build_desktop_ws, make_drv

GLASSES = {"p4": ((1024, 600), 7.0), "guition_p4": ((1280, 800), 10.1)}

_BOTTOM = "fit-bottom-bar"


class _Tall:
    """An app's section taller than any glass."""

    def height(self, w, desk, fs):
        return 2000 * fs

    def draw(self, desk):
        from runtime import ui
        for k in range(40):
            ui.link("Row %d" % k, "", {"id": "tall.%d" % k})


@pytest.fixture(autouse=True)
def _fresh():
    for it in ITEMS.slots:
        if it.pid:
            ITEMS._free(it)
    yield
    for it in ITEMS.slots:
        if it.pid:
            ITEMS._free(it)
    ui_draw.theme_uninstall()


def _bottom_bar(ws):
    """The bar at the glass's foot: each popover opens up from it."""
    import moy_ui
    rc, _notes = moy_ui.theme_install(json.dumps(
        {"format": 2, "name": _BOTTOM, "layout": {"bars": [
            {"edge": "bottom", "size": 22, "left": ["menu", "zone"],
             "right": ["wifi", "battery", "clock"]}]}}).encode())
    assert rc == 0
    ws.system.set("layout_theme", _BOTTOM)
    ws.look.set_theme(ws.look.theme_name, persist=False)


def _as(pid, fn, *a):
    was = moy_log.current()
    moy_log.current(pid)
    try:
        return fn(*a)
    finally:
        moy_log.current(was)


def _opened(ws, drv, open_it, then=None):
    """The card's rect once it has settled: opened, `then` (what it lists
    grown), and the frames its own refit asks for."""
    ws.close_popover()
    open_it()
    if then is not None:
        then()
    for _ in range(3):
        drv.frame(1 / 30)
    assert ws.sysmenu.open
    return ws.sysmenu.panel_rect()


@pytest.mark.parametrize("bar", ["top", "bottom"])
@pytest.mark.parametrize("fs", [1, 2])
@pytest.mark.parametrize("glass", sorted(GLASSES))
def test_every_desk_popover_lies_within_the_glass(tmp_path, glass, fs, bar):
    size, diag = GLASSES[glass]
    ws = build_desktop_ws(tmp_path, sys_size=size, panel_diagonal_in=diag, font_scale=fs)
    if bar == "bottom":
        _bottom_bar(ws)
    ws.bar_layer._today = lambda: (2026, 11, 9)          # six weeks
    ws.open_desk()
    drv = make_drv(ws)
    drv.frame(1 / 30)

    def many():
        ws.settings_layer.wifi_nets = [("Net %d" % k, -40, k % 2 == 0) for k in range(24)]

    full = "\n".join("button b%d Row %d" % (k, k) for k in range(ROWS_MAX))
    h = ITEMS.show_compiled(41, "music", 0, 0)
    panel = ITEMS.slots[h - 1]
    panel.section.set(parse(full))
    tall = _as(42, moybyte.status, "pulse", _Tall(), 0)
    tall.show()

    cases = {
        "clock": lambda: _opened(ws, drv, lambda: ws.widget_tap("clock")),
        "clock paged": lambda: _opened(ws, drv, lambda: ws.widget_tap("clock"),
                                       lambda: ws.sysmenu.comp.page(-2)),
        "wifi": lambda: _opened(ws, drv, lambda: ws.widget_tap("wifi"), many),
        "battery": lambda: _opened(ws, drv, lambda: ws.widget_tap("battery")),
        "panel": lambda: _opened(ws, drv, lambda: ws.open_status_menu(panel)),
        "section": lambda: _opened(ws, drv, lambda: ws.open_status_menu(tall)),
    }
    W, H = ws.sys_canvas.w, ws.sys_canvas.h
    for name, rect_of in cases.items():
        x, y, w, h = rect_of()
        assert ws.sysmenu.up == (bar == "bottom"), name
        assert 0 <= x and x + w <= W and 0 <= y and y + h <= H, \
            (glass, fs, bar, name, (x, y, w, h), (W, H))
    ws.close_popover()
