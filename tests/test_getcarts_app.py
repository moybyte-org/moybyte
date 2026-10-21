"""Get Carts' pages on the public UI API's verbs (docs/media/look_2026-10/
APPS_RESTYLE.md, Get Carts): the shelf's cards and what they say, the cart's
page, Remove's question, the pages that say why there is no shelf and the
Wi-Fi page they open, the desk's pane, and a settled page painting nothing.
The fetching and installing under them are tests/test_cart_store.py's."""

import pytest

from cart_store_fixtures import MemNet, Repo
from runtime import getcarts_app as gc
from runtime import host_app
from ws_helpers import quiesce

_DT = 1 / 30


class _In:
    def __init__(self, *names):
        self.names = set(names)
        self.last_key = 0

    def pressed(self, name):
        return name in self.names


def _shelf():
    repo = Repo("https://mit.example/carts", "Test carts")
    repo.add("jet", name="Jet Pot")
    repo.add("rock", name="Rock Run", runtime="lua", modules=())
    repo.add("zap", name="Zap", runtime="lua", modules=())
    return repo, MemNet(repo.routes())


def _ws(tmp_path, net, urls, **kw):
    import json
    from runtime import cart_index as ci
    ws = host_app.build_workstation(str(tmp_path / "carts"), **kw)
    (tmp_path / ci.INDEXES_NAME).write_text(json.dumps({"indexes": urls}))
    ws.cart_net = net
    ws.pointer.visible = False
    return ws, ws._apps_by_id["getcarts"]


def _frames(ws, n=3):
    for _ in range(n):
        ws._dirty = True
        ws.frame(_DT)


def _open(ws, app):
    ws.open_app(app)
    for _ in range(40):
        _frames(ws, 1)
        if app.phase != "checking":
            break
    _frames(ws, 2)


def _press(ws, app, *names):
    app.handle_input(_In(*names))
    _frames(ws, 2)


def test_the_shelf_says_where_each_cart_stands(tmp_path):
    repo, net = _shelf()
    ws, app = _ws(tmp_path, net, [repo.url("index.json")])
    _open(ws, app)
    assert app.phase == "list"
    rows = {r["cart"]["name"]: r for r in app.rows}
    rows["Rock Run"]["state"] = "installed"
    rows["Zap"]["runs"] = False
    assert app._shown(rows["Jet Pot"]) == "get"
    assert app._shown(rows["Rock Run"]) == "installed"
    assert app._shown(rows["Zap"]) == "noplay"
    app.free = (1000, 4096)                       # no room for anything
    assert app._shown(rows["Jet Pot"]) == "too_big"
    left, right = app._caption()
    assert left == "3 carts" and right.endswith(" free")
    app.unreached = 1
    assert app._caption()[0].endswith("1 shelf away")


def test_a_card_opens_its_page_and_back_lands_on_it(tmp_path):
    repo, net = _shelf()
    ws, app = _ws(tmp_path, net, [repo.url("index.json")])
    _open(ws, app)
    assert app._focused == app._card_id(app.rows[0])
    _press(ws, app, "right")
    _press(ws, app, "a")
    assert app.phase == "cart" and app.cur is app.rows[1]
    assert app._focused == gc.vid("GET")
    assert ws.bar_layer._title("app") == app.rows[1]["cart"]["name"]
    assert app.back() and app.phase == "list"
    _frames(ws)
    assert app._focused == app._card_id(app.rows[1])
    assert ws.bar_layer._title("app") == "Get Carts"


def test_wifi_off_offers_the_wifi_page_and_looks_again_on_the_way_back(tmp_path):
    repo, net = _shelf()
    net.up = False
    ws, app = _ws(tmp_path, net, [repo.url("index.json")])
    _open(ws, app)
    assert app.phase == "nowifi"
    assert app._current_verbs() == ("TRY AGAIN", "WIFI")
    assert app._focused == gc.vid("WIFI"), "Wi-Fi is the verb that goes on"
    assert ws._wifi_icon_kind() == "wifi_off"
    _press(ws, app, "a")
    assert app._away and app._inst.wifi_open()
    assert ws.settings_layer.wifi_view
    net.up = True                                 # a network joined there
    ws.settings_layer.back()                      # B on the panel: back to Get Carts
    assert ws.wm.top_is("getcarts")
    for _ in range(40):
        _frames(ws, 1)
        if app.phase not in ("nowifi", "checking"):
            break
    assert not app._away
    assert app.phase == "list", app.phase


@pytest.mark.parametrize("phase,title", [("unreached", "Shelf away"), ("nomemory", "Memory full"),
                                         ("nonet", "No internet")])
def test_a_shelf_out_of_reach_offers_try_again_alone(tmp_path, phase, title):
    repo, net = _shelf()
    ws, app = _ws(tmp_path, net, [repo.url("index.json")])
    _open(ws, app)
    app._go(phase)
    _frames(ws)
    icon, got, _lines = app._empty_words()
    assert (icon, got) == ("warn", title)
    assert app._current_verbs() == ("TRY AGAIN",)
    assert app._focused == gc.vid("TRY AGAIN")


def test_remove_asks_in_a_dialog_whose_first_stop_is_cancel(tmp_path):
    repo, net = _shelf()
    ws, app = _ws(tmp_path, net, [repo.url("index.json")])
    _open(ws, app)
    app._tap_row(0)
    app._press("GET")
    for _ in range(40):
        _frames(ws, 1)
        if app.phase == "done":
            break
    assert app.phase == "done"
    _frames(ws)
    assert app.toast is not None and app.toast[0] == "Jet Pot is on your shelf"
    _press(ws, app, "left")                       # Remove
    assert app._focused == gc.vid("REMOVE")
    _press(ws, app, "a")
    assert app.asking and app._focused == "Cancel"
    assert app._kind("REMOVE") == "danger"
    _press(ws, app, "a")                          # Cancel: nothing removed
    assert not app.asking and (tmp_path / "carts" / "jet.moy").exists()


def test_a_toast_goes_with_the_next_key(tmp_path):
    repo, net = _shelf()
    ws, app = _ws(tmp_path, net, [repo.url("index.json")])
    _open(ws, app)
    app._say("Hello")
    _frames(ws)
    assert app.toast is not None
    _press(ws, app, "right")
    assert app.toast is None


def test_a_settled_shelf_paints_nothing(tmp_path):
    """With no input the redraw gate draws no frame, and a press that moves
    nothing draws one frame of the same pixels and asks for none after it."""
    repo, net = _shelf()
    ws, app = _ws(tmp_path, net, [repo.url("index.json")])
    _open(ws, app)
    quiesce(ws)
    drv = host_app.ConsoleDriver(ws)
    for _ in range(4):
        drv.frame(_DT)
    quiesce(ws)                                   # the first open's achievement toast
    ws._dirty = True
    for _ in range(4):
        drv.frame(_DT)
    drawn = ws._frames_drawn
    for _ in range(10):
        drv.frame(_DT)
    assert ws._frames_drawn == drawn, "an idle shelf painted"
    before = bytes(ws.sys_canvas._buf)
    drv.press("up")                               # on the first row: nothing to move to
    for _ in range(6):
        drv.frame(_DT)
    assert ws._frames_drawn == drawn + 1, "one frame for the press, then none"
    assert bytes(ws.sys_canvas._buf) == before


def test_the_desk_shows_the_focused_card_in_the_pane(tmp_path):
    repo, net = _shelf()
    ws, app = _ws(tmp_path, net, [repo.url("index.json")], sys_size=(1024, 600),
                  panel_diagonal_in=7.0, windowed=True)
    ws.open_desk()
    _open(ws, app)
    win = ws.wm._wins["getcarts"]
    win.x, win.y = 112, 52
    ws.wm._resize_window(win, 900, 536)           # the target's window
    _frames(ws)
    assert app.layout.desk and app.layout.cols == 4
    _press(ws, app, "right")
    assert app.cur is app.rows[1] and app.phase == "cart"
    assert not app.has_page(), "the pane is the shelf's: no back chevron"
    assert app._ui.box(gc.vid("GET")) is not None, "the pane's verbs are in the foot"
    assert app._ui.box("Check") is not None


def test_enter_never_cancels_a_download_and_b_does(tmp_path):
    """A kid presses A at anything, and twice after Get: while the download
    runs no key but B (Esc) reaches Cancel, and the hint strip says so."""
    import os
    repo = Repo("https://mit.example/carts", "Test carts")
    repo.add("jet", name="Jet Pot", extra={"big.bin": os.urandom(400000)})
    net = MemNet(repo.routes())
    ws, app = _ws(tmp_path, net, [repo.url("index.json")])
    _open(ws, app)
    app._tap_row(0)
    _frames(ws, 1)
    _press(ws, app, "a")                          # Get
    for _ in range(4):
        if app.phase == "getting":
            break
        _frames(ws, 1)
    assert app.phase == "getting"
    app.job.step_ms = 0                           # a slice a frame, never the whole
    assert app._focused != gc.vid("CANCEL"), "Enter must not land on Cancel"
    _press(ws, app, "a")                          # the second press of Get's
    _press(ws, app, "down")
    _press(ws, app, "a")
    assert app.phase == "getting", "A cancelled the download"
    app.handle_input(_In("b"))
    assert app.phase == "cart" and app.toast[0] == "Stopped. Nothing changed."
    assert not (tmp_path / "carts" / "jet.moy").exists()


def test_the_layouts_rects_are_where_the_buttons_draw(tmp_path):
    """The browser suites tap the layout's rects (`web_e2e.get_carts_taps`):
    they are the ones the pages' buttons draw at."""
    repo = Repo("https://mit.example/carts", "Test carts")
    repo.add("dm", name="Dungeon", runtime="lua", modules=(),
             external=[("game.wad", b"WAD!" * 3000, "pkg/game.wad")])
    ws, app = _ws(tmp_path, MemNet(repo.routes()), [repo.url("index.json")])
    _open(ws, app)
    lay = app.layout
    _press(ws, app, "a")
    assert app.phase == "cart"
    assert app._ui.box(gc.vid("GET")) == lay.split_verbs(lay.page_verbs(), 1)[0]
    _press(ws, app, "a")
    for _ in range(40):
        _frames(ws, 1)
        if app.phase == "licence":
            break
    assert app.phase == "licence"
    no, agree = lay.licence_verbs()
    assert app._ui.box(gc.vid("NO")) == no and app._ui.box(gc.vid("I AGREE")) == agree


def test_a_press_in_the_draw_owes_the_next_frame_a_repaint(tmp_path):
    """Get is pressed inside the cart page's draw, which spends that draw's
    `all`: the page it opens is owed to the frame after, as an `again`."""
    repo, net = _shelf()
    ws, app = _ws(tmp_path, net, [repo.url("index.json")])
    _open(ws, app)
    _press(ws, app, "a")
    assert app.phase == "cart"
    app.handle_input(_In("a"))
    ws._dirty = True
    ws.frame(_DT)
    assert app.phase != "cart" and ws._damage_take()
