"""App status items and the handheld control center
(runtime/system/status_items.py, docs/theme_and_shell_2026-11.md §8.6): an
item is a preallocated record, ordered by `order` then age, swept with its
process, its section run as its process; the handheld bar's status cluster
is one focus stop that opens the control center; on the desk an item is a
cell that opens its menu. And Stop & play: a compiled game the VM must stop
for while the music holds the radio asks first."""

import pytest

import moybyte
from runtime import moy_log
from runtime.status_items import ITEMS, ON, PAUSED, CAP
from ws_helpers import build_desktop_ws, build_ws, make_drv


class Section:
    def __init__(self, ns="music"):
        self.drawn = []
        self.ids = ({"id": ns + ".pause"}, {"id": ns + ".stop"})
        self.stops = 0

    def height(self, w, desk):
        return 60

    def draw(self, desk):
        from runtime import ui
        self.drawn.append(moy_log.current())
        ui.info("Song", "Station")
        ui.link("Pause", "", self.ids[0])
        if ui.link("Stop", "", self.ids[1]):
            self.stops += 1


@pytest.fixture(autouse=True)
def _fresh():
    for it in ITEMS.slots:
        if it.pid:
            ITEMS._free(it)
    yield
    for it in ITEMS.slots:
        if it.pid:
            ITEMS._free(it)


def _as(pid, fn, *a):
    was = moy_log.current()
    moy_log.current(pid)
    try:
        return fn(*a)
    finally:
        moy_log.current(was)


def test_items_are_ordered_capped_and_swept_with_their_process():
    a = _as(7, moybyte.status, "music", Section(), 5)
    b = _as(8, moybyte.status, "pulse", Section("b"), 1)
    assert a.pid == 7 and b.pid == 8
    assert ITEMS.count() == 0, "an item shows only once it says so"
    a.show()
    b.show(PAUSED)
    assert [it.pid for it in ITEMS.shown] == [8, 7], "lower order first"
    assert b.state == PAUSED
    rest = [_as(9, moybyte.status, "stop", Section(), 0) for _ in range(CAP)]
    assert rest[-1] is None and sum(r is not None for r in rest) == CAP - 2
    moybyte.WAKES.end(7)
    assert a.pid == 0 and [it.pid for it in ITEMS.shown] == [8]
    b.remove()
    assert ITEMS.count() == 0


def test_a_change_moves_the_generation_and_allocates_no_record():
    it = _as(7, moybyte.status, "music", Section(), 0)
    it.show()
    gen, lgen = ITEMS.gen, ITEMS.layout_gen
    it.changed()
    assert ITEMS.gen != gen and ITEMS.layout_gen == lgen, "a change moves no layout"
    it.show(ON)
    assert ITEMS.gen == gen + 1, "showing the state it shows is no change"
    slots = list(ITEMS.slots)
    it.remove()
    it2 = _as(7, moybyte.status, "music", Section(), 0)
    assert it2 in slots, "the records are the preallocated ones"


def test_the_section_draws_as_its_process(tmp_path):
    ws = build_ws(tmp_path)
    sec = Section()
    it = _as(42, moybyte.status, "music", sec, 0)
    it.show()
    ws.widget_tap("battery")
    assert ws.sysmenu.open and type(ws.sysmenu.comp).__name__ == "ControlCenter"
    ws.frame(1 / 30)
    assert sec.drawn and set(sec.drawn) == {42}
    assert moy_log.current() != 42, "the shell's again after"


def test_the_handheld_cluster_is_one_stop_that_opens_the_control_center(tmp_path):
    ws = build_ws(tmp_path)
    it = _as(42, moybyte.status, "music", Section(), 0)
    it.show()
    ws.frame(1 / 30)
    items = ws.bar_layer.focus_items("home")
    names = [i[2] for i in items]
    assert names.count("cluster") == 1
    assert "wifi" not in names and "battery" not in names and "status" not in names
    drv = make_drv(ws)
    k = names.index("cluster")
    assert ws.bar_layer.focus_enter("home", "row", k=k)
    drv.press("a")
    drv.frame(1 / 30)
    assert ws.sysmenu.open and ws.sysmenu.widget == "battery"


def test_the_desk_item_is_a_cell_that_opens_its_menu(tmp_path):
    ws = build_desktop_ws(tmp_path, panel_diagonal_in=7.0)
    sec = Section()
    it = _as(42, moybyte.status, "music", sec, 0)
    it.show()
    ws.open_desk()
    ws.frame(1 / 30)
    got = ws.bar_layer.cell("desk", "status")
    assert got is not None
    bar, (x, y, w, h) = got
    ws.bar_layer.handle_bar_tap("desk", x + w // 2, y + h // 2)
    assert ws.sysmenu.open and ws.sysmenu.widget == "status"
    assert ws.status_open is it
    ws.frame(1 / 30)
    assert sec.drawn and set(sec.drawn) == {42}
    it.remove()
    assert not ws.sysmenu.open, "the item's menu closes with it"


def test_stop_and_play_stops_the_stream_then_opens_the_game(tmp_path, monkeypatch):
    ws = build_ws(tmp_path)
    from runtime import devices
    stopped = []
    opened = []

    class Media:
        def stop(self):
            stopped.append(moy_log.current())

    monkeypatch.setattr(devices, "api", lambda name: Media() if name == "media" else None)
    monkeypatch.setattr(ws, "open", lambda cart: opened.append(cart))
    cart = {"title": "Doom"}
    ws.ask_stop_music(cart)
    c = ws.confirm
    assert c.open and c.title == "Stop the music to play Doom?"
    c.cancel()
    assert not stopped and not opened, "Cancel leaves the music playing"
    ws.ask_stop_music(cart)
    ws.confirm._act()
    assert stopped and opened == [cart]


def test_a_vm_stop_kept_by_the_music_asks_first(tmp_path, monkeypatch):
    ws = build_ws(tmp_path)
    from runtime import player as P

    class Leases:
        def holders(self):
            return ("media",)

    ended = []
    monkeypatch.setattr(P._moy_play, "info", lambda run: (0,) * 11 + ("lease", 0), raising=False)
    monkeypatch.setattr(P._moy_play, "end", lambda run, *a: ended.append(run), raising=False)
    monkeypatch.setattr(ws, "leases", Leases(), raising=False)
    asked = []
    monkeypatch.setattr(ws, "ask_stop_music", lambda cart: asked.append(cart))
    cart = {"title": "Doom", "runtime": "wasm"}
    ws.run.player._log_begin(cart)
    assert moy_log.current() != moy_log.SHELL
    assert ws.run.player._music_keeps_the_vm(5, cart)
    assert asked == [cart] and ended == [5] and ws.run.player.declined
    # the declined run's process is over: the dialog's answer, which stops
    # the shell's stream, runs as the shell
    assert moy_log.current() == moy_log.SHELL and ws.run.player.pid is None
    monkeypatch.setattr(ws, "leases", None, raising=False)
    assert not ws.run.player._music_keeps_the_vm(5, cart), "no music: the verdict stands"
