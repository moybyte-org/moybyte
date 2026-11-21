"""The keys on a bar the bar page draws (`bar_ui`, a desk's console mode):
the item they are on wears its bar item's focus mark, drawn by the page,
and confirm runs the page's action for it. The page's actions reach the
console through `bar_ui.TAP`, `CHIP` and `ITEM`, which the bar layer sets.
A new console's bar hands the page everything again (`bar_engine.forget`)."""

import pytest

import moybyte
from runtime import moy_log
from runtime.status_items import ITEMS
from ws_helpers import build_desktop_ws, make_drv


class _Section:
    def height(self, w, desk, fs):
        return 40 * fs

    def draw(self, desk):
        from runtime import ui
        ui.info("Song", "Station")


@pytest.fixture(autouse=True)
def _fresh():
    for it in ITEMS.slots:
        if it.pid:
            ITEMS._free(it)
    yield
    for it in ITEMS.slots:
        if it.pid:
            ITEMS._free(it)


def _console(tmp_path):
    ws = build_desktop_ws(tmp_path, panel_diagonal_in=7.0)
    drv = make_drv(ws)
    ws.wm.enter_console()
    for _ in range(3):
        drv.frame(1 / 30)
    assert ws.router.bar_where() == "home"
    return ws, drv


def _onto(ws, drv, name):
    bl = ws.bar_layer
    items = bl.focus_items("home")
    k = [j for j, it in enumerate(items) if it[2] == name][0]
    assert bl.focus_enter("home", "page", k=k)
    for _ in range(2):
        drv.frame(1 / 30)


def _bar_px(ws):
    bl = ws.bar_layer
    bx, by, bw, bh = bl.bar_rects("home")[0]
    cv = ws.sys_canvas
    return [bytes(cv._buf[2 * ((by + y) * cv.w + bx):2 * ((by + y) * cv.w + bx + bw)])
            for y in range(bh)]


@pytest.mark.parametrize("name", ["wifi", "battery", "clock"])
def test_the_page_draws_the_focus_mark_and_confirm_runs_its_action(tmp_path, monkeypatch, name):
    import bar_engine
    import ui_draw
    ws, drv = _console(tmp_path)
    plain = _bar_px(ws)
    bl = ws.bar_layer
    old_ring = []
    monkeypatch.setattr(bl, "_pill", lambda w, i, it, f=bl._pill: (old_ring.append(it[2]), f(w, i, it))[1])
    _onto(ws, drv, name)
    assert bl._page_stop("home", bl.focus_item) == name
    assert ui_draw._C.api_focus(bar_engine._CTX[0], None) == name, "the bar's context holds it"
    assert not old_ring, "the bar layer draws no pill of its own over a page item"
    marked = _bar_px(ws)
    assert marked != plain and len(marked) == len(plain), "the bar item's mark shows"
    fired = []
    monkeypatch.setattr(bar_engine, "fire", lambda stop, f=bar_engine.fire: (fired.append(stop), f(stop)))
    drv.press("a")
    drv.frame(1 / 30)
    assert fired == [name], "confirm is the page's action"
    assert ws.sysmenu.open and ws.sysmenu.widget == name


def test_the_mark_leaves_with_the_keys(tmp_path):
    ws, drv = _console(tmp_path)
    plain = _bar_px(ws)
    _onto(ws, drv, "wifi")
    ws.bar_layer.focus_leave()
    for _ in range(2):
        drv.frame(1 / 30)
    assert _bar_px(ws)[:-1] == plain[:-1]


def test_confirm_on_a_status_item_opens_its_menu_through_the_page(tmp_path):
    ws, drv = _console(tmp_path)
    was = moy_log.current()
    moy_log.current(42)
    try:
        it = moybyte.status("music", _Section(), 0)
    finally:
        moy_log.current(was)
    it.show()
    for _ in range(3):
        drv.frame(1 / 30)
    _onto(ws, drv, "status")
    bl = ws.bar_layer
    assert bl._page_stop("home", bl.focus_item) == "0/item"
    drv.press("a")
    drv.frame(1 / 30)
    assert ws.sysmenu.open and ws.status_open is it


def test_the_pages_chip_action_is_the_window_managers_tap(tmp_path):
    import bar_ui
    ws = build_desktop_ws(tmp_path, panel_diagonal_in=7.0)
    drv = make_drv(ws)
    ws.home.open_desk()
    for _ in range(3):
        drv.frame(1 / 30)
    taps = []
    ws.wm.chip_tap = taps.append
    assert bar_ui.CHIP[0] is not None
    bar_ui.CHIP[0]("settings")
    assert taps == ["settings"]


def test_a_new_consoles_bar_hands_the_page_everything_again(tmp_path, monkeypatch):
    import bar_engine
    _console(tmp_path / "a")
    assert bar_engine._LAST[0] is not None, "the bar drew through the page"
    bar_engine.forget()
    assert bar_engine._LAST == [None] * len(bar_engine._LAST)
    assert bar_engine._WINS[0] is None and bar_engine._ITEMS_W[0] is None
    assert bar_engine._ITEMS[0] is None and bar_engine._DIRTY[0]
    calls = []
    monkeypatch.setattr(bar_engine, "forget", lambda f=bar_engine.forget: (calls.append(1), f()))
    build_desktop_ws(tmp_path / "b", panel_diagonal_in=7.0)
    assert calls, "a new console's bar forgets what the last one handed the page"
