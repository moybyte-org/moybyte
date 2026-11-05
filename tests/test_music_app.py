"""The Music app (#110, runtime/apps/music_app.py, docs/media/music_2026-11/
README.md): its identity cart registers it; a station plays through the
media face and puts the app's status item in the bar, whose section's ids
are the app's own; Add a station refuses an address that will not play and
keeps the one that will; Back leaves (on the desk, minimises) and the stream
plays on; a console with no media service shows the no-audio page; and the
media face's place and length reach the card."""

from pathlib import Path

import pytest

from runtime import music_app as M
from runtime import ui_draw
from runtime.status_items import ITEMS, ON, PAUSED, ERROR
from ws_helpers import build_desktop_ws, build_ws, make_drv

TONE = Path(__file__).resolve().parent / "fixtures" / "media" / "tone_1k.mp3"
GROOVE = M.STATIONS[0]


class Face:
    """The media face, faked: what `now()` answers and what was asked."""

    def __init__(self):
        self.n = None
        self.played = []

    def now(self):
        return dict(self.n) if self.n else None

    def play(self, src):
        if src.startswith("https://"):
            raise OSError(93, "https is refused")
        self.played.append(src)
        self.n = {"state": "playing", "paused": False, "src": src, "station": "",
                  "title": "Boards of Canada - Dayvan Cowboy", "entry": 0, "entries": 1,
                  "volume": 60}

    def pause(self):
        self.n["paused"] = True

    def resume(self):
        self.n["paused"] = False

    def stop(self):
        self.n = None

    def next(self):
        return True

    def prev(self):
        return True

    def volume(self, v=None):
        if v is not None:
            self.n["volume"] = v
        return self.n["volume"]


@pytest.fixture
def face():
    s = M.STREAM
    s._end()
    f = Face()
    s.face = f
    yield f
    s._end()
    s.face = None
    s.stations = M.STATIONS
    for it in ITEMS.slots:
        if it.pid:
            ITEMS._free(it)


def _app(ws):
    return ws._apps_by_id["jukebox"]


def _settle(ws, n=4):
    for _ in range(n):
        ws._dirty = True
        ws.frame(1 / 30)


def test_music_is_a_registered_app_its_cart_claims(tmp_path):
    ws = build_ws(tmp_path)
    app = _app(ws)
    cart = next(c for c in ws.carts.all if c["title"] == "Music")
    assert app.is_app(cart) and ws.is_system_app(cart)
    assert ws.open_app(app) and ws.wm.top_kind() == "jukebox"


def test_a_station_plays_and_its_status_item_follows_the_stream(tmp_path, face):
    ws = build_ws(tmp_path)
    app = _app(ws)
    ws.open_app(app)
    _settle(ws)
    rows = app._station_rows()
    app._open_row(0, 1, rows)
    assert face.played == [GROOVE[2]]
    it = ITEMS.at(0)
    assert it is not None and it.icon == "music" and it.app == "Music" and it.state == ON
    c = M.STREAM.card
    assert M.lines(c) == ("Dayvan Cowboy", "Boards of Canada · Groove Salad")
    app._open_row(0, 1, app._station_rows())       # the playing row: Enter pauses
    assert face.n["paused"] and ITEMS.at(0).state == PAUSED
    M.STREAM.step(1)                                # the next station
    assert face.played[-1] == M.STATIONS[1][2]
    M.STREAM.stop()
    assert face.n is None and ITEMS.count() == 0


def test_a_station_that_fails_shows_the_danger_state_and_tries_again(tmp_path, face):
    ws = build_ws(tmp_path)
    ws.open_app(_app(ws))
    M.STREAM.play("radio", "Secret Agent", "http://x/y")
    face.n["state"] = "failed"
    M.STREAM.poll()
    assert M.lines(M.STREAM.card) == ("Secret Agent", "Not answering")
    assert ITEMS.at(0).state == ERROR
    M.STREAM.toggle()                               # Try again: played again
    assert face.played == ["http://x/y", "http://x/y"]


def test_the_section_draws_the_card_with_the_apps_own_ids(tmp_path, face):
    """The control center lays every app's section in one page: each of
    Music's stops is `music.*`."""
    ws = build_ws(tmp_path)
    M.STREAM.play("radio", GROOVE[0], GROOVE[2])
    ws.go_home()
    _settle(ws)
    ws.widget_tap("battery")
    assert ws.sysmenu.open and ws.sysmenu.widget == "battery"
    seen = []
    real = ui_draw.UI_TARGET
    from runtime import ui
    orig = ui.button

    def spy(label, kind="normal", o=None):
        seen.append((o or {}).get("id"))
        return orig(label, kind, o)
    ui.button = spy
    try:
        _settle(ws, 2)
    finally:
        ui.button = orig
    assert real is ui_draw.UI_TARGET
    ids = [i for i in seen if i and i.startswith("music.")]
    assert {"music.prev", "music.main", "music.next", "music.stop"} <= set(ids)


def test_add_a_station_refuses_https_and_keeps_an_http_one(tmp_path, face):
    ws = build_ws(tmp_path)
    app = _app(ws)
    ws.open_app(app)
    app._go("add")
    _settle(ws, 2)
    assert app.page == "add" and ws.input.text_mode
    assert M._check("https://stream.example.org/live.mp3", False) == M.ERR_ADDRESS
    assert M.ERR_ADDRESS == "This address won't play. Use one that starts with http://"
    assert M._check("http://ice1.somafm.com/lush-128-mp3", False) is None
    assert M._check("htt", False) is None and M._check("htt", True) == M.ERR_ADDRESS
    app.url = "https://stream.example.org/live.mp3"
    app._submit()
    assert app.page == "add" and app.err == M.ERR_ADDRESS
    app.url, app.name, app.err = "http://ice1.somafm.com/lush-128-mp3", "Lush", None
    app._submit()
    assert app.page == "list" and not ws.input.text_mode
    assert M.STREAM.stations[-1] == ("Lush", "Added", "http://ice1.somafm.com/lush-128-mp3")
    assert face.played[-1] == "http://ice1.somafm.com/lush-128-mp3"
    assert app._prefs.get(M.STATIONS_KEY, None) == [["Lush", "http://ice1.somafm.com/lush-128-mp3"]]
    M.STREAM.stations = M.STATIONS
    app._load_stations()
    assert M.STREAM.stations[-1][0] == "Lush", "the station is read back from the prefs"


def test_mic_cancels_add_a_station(tmp_path, face):
    ws = build_ws(tmp_path)
    app = _app(ws)
    ws.open_app(app)
    app._go("add")
    assert ws.cancel_field() and app.page == "list"


def test_back_leaves_and_the_stream_plays_on(tmp_path, face):
    ws = build_ws(tmp_path)
    app = _app(ws)
    ws.open_app(app)
    M.STREAM.play("radio", GROOVE[0], GROOVE[2])
    app._go("now")
    ws.app_back()
    assert app.page == "list" and ws.wm.top_kind() == "jukebox"
    ws.app_back()
    assert ws.wm.top_kind() != "jukebox"
    assert face.n is not None and ITEMS.count() == 1


def test_back_on_the_desk_minimises_the_window(tmp_path, face):
    ws = build_desktop_ws(tmp_path)
    app = _app(ws)
    ws.open_desk()
    ws.open_app(app)
    _settle(ws)
    assert "jukebox" in ws.wm._wins and not ws.wm._wins["jukebox"].minimized
    ws.app_back(app)
    assert ws.wm._wins["jukebox"].minimized and ws.wm._focus != "jukebox"
    assert ws.open_app(app) and not ws.wm._wins["jukebox"].minimized


def test_a_console_without_media_shows_the_no_audio_page(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "_media", lambda: None)
    M.STREAM.face = None
    assert M.STREAM.media() is None
    ws = build_ws(tmp_path)
    app = _app(ws)
    ws.open_app(app)
    texts = []
    from runtime import ui
    orig = ui.empty
    monkeypatch.setattr(ui, "empty", lambda i, t, ls=(): texts.append(t) or orig(i, t, ls))
    _settle(ws, 2)
    assert "No sound on this console" in texts
    app.leave_asked = True                            # Back to home
    drv = make_drv(ws)
    drv.frame(1 / 30)
    assert ws.wm.top_kind() != "jukebox"


def test_the_folder_lists_songs_and_playlists_with_their_lengths(tmp_path):
    folder = tmp_path / "music"
    folder.mkdir()
    (folder / "Beach trip.mp3").write_bytes(TONE.read_bytes())
    (folder / "Road trip.m3u").write_text("#EXTM3U\nBeach trip.mp3\na.mp3\n")
    (folder / "notes.txt").write_text("not music")
    songs, lists = M._scan(str(folder))
    assert [s[0] for s in songs] == ["Beach trip"] and songs[0][2] == "0:02"
    assert lists == (("Road trip", str(folder / "Road trip.m3u"), "2 songs"),)
    assert M._scan(str(tmp_path / "none")) is None


def test_a_files_place_reaches_the_card():
    c = M.card_of({"state": "playing", "paused": True, "title": "Sunny day drive",
                   "entry": 2, "entries": 12, "volume": 40, "at": 72, "len": 221},
                  ("list", "Road trip mix", "/m/r.m3u"))
    assert (c["at"], c["len"]) == (72, 221)
    assert M.lines(c) == ("Sunny day drive", "Road trip mix · 3 of 12")
