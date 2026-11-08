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


class StampedFace(Face):
    """A face with the board's stamp: one int over what now() shows, and a
    count of the now() reads."""

    def __init__(self):
        Face.__init__(self)
        self.reads = 0

    def now(self):
        self.reads += 1
        return Face.now(self)

    def stamp(self):
        n = self.n
        return None if n is None else hash((n["paused"], n["volume"], n["title"]))


def test_a_still_stamp_skips_the_read_and_a_moved_one_reads(tmp_path, face):
    """A station playing on under a game: the poll reads the stamp alone;
    a pause moves it and the card follows."""
    f = StampedFace()
    M.STREAM.face = f
    build_ws(tmp_path)
    M.STREAM.play("radio", "Groove Salad", GROOVE[2])
    M.STREAM.poll()
    reads = f.reads
    for _ in range(5):
        M.STREAM.poll()
    assert f.reads == reads
    f.n["paused"] = True
    M.STREAM.poll()
    assert f.reads == reads + 1 and M.STREAM.card["paused"]


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


def test_a_station_that_blocks_browsers_says_so(tmp_path, face):
    """In the browser a station's bytes are the page's fetch, which a server
    without CORS headers refuses: the face fails it with EACCES."""
    ws = build_ws(tmp_path)
    ws.open_app(_app(ws))
    M.STREAM.play("radio", "Secret Agent", "http://x/y")
    face.n["state"] = "failed"
    face.n["err"] = 13
    M.STREAM.poll()
    assert M.lines(M.STREAM.card) == ("Secret Agent", "Blocks browsers")


def test_the_folder_lists_the_songs_a_board_keeps_for_its_page(tmp_path, monkeypatch):
    """A board-served page keeps no song: the face's `held` names the board's,
    beside what the page's store has (a playlist rides the sync)."""
    folder = tmp_path / "music"

    class _Face:
        def held(self, f):
            return (("Board song.mp3", 4096),) if f == str(folder) else ()
    monkeypatch.setattr(M, "_media", lambda: _Face())
    assert M._scan(str(folder)) == ((("Board song", str(folder / "Board song.mp3"), ""),), ())
    folder.mkdir()
    (folder / "Mix.m3u").write_text("Board song.mp3\n")
    songs, lists = M._scan(str(folder))
    assert [s[0] for s in songs] == ["Board song"] and [x[0] for x in lists] == ["Mix"]


def test_a_stream_that_starts_during_the_desks_draw_waits_for_the_next_frame(tmp_path, face):
    """The card is drawn from the frame's snapshot: a station whose card
    arrives while the desk window draws (the browser's pump runs the service
    inside the frame) shows on the next frame, never as a card with no state."""
    ws = build_desktop_ws(tmp_path)
    app = _app(ws)
    ws.open_desk()
    ws.open_app(app)
    _settle(ws)
    assert M.STREAM.card is None
    rows_of = app._station_rows
    started = []

    def rows_that_start_a_station():
        rows = rows_of()
        if not started:
            started.append(1)
            app._open_row(0, 1, rows)
        return rows

    app._station_rows = rows_that_start_a_station
    _settle(ws, 1)
    assert started and M.STREAM.card is not None
    app._station_rows = rows_of
    _settle(ws)
    assert app.k.c is not None


def _list_on(tmp_path, desk):
    """The Radio list open on the handheld or in the desk's window, settled:
    (ws, its driver, the app, the list's view in the app's coordinates, the
    app's origin on the screen)."""
    ws = build_desktop_ws(tmp_path) if desk else build_ws(tmp_path)
    app = _app(ws)
    if desk:
        ws.open_desk()
    ws.open_app(app)
    drv = make_drv(ws)
    views = []
    rows = type(app)._rows

    def spy(self, rect, rows_, cur, oid):
        views.append(rect)
        return rows(self, rect, rows_, cur, oid)

    app._rows = spy.__get__(app)
    _settle(ws)
    del app._rows
    x, y, w, h = views[-1]
    rh = app._row_h()
    view = (x, y, w, h // rh * rh)          # the table ends on a whole row
    at = ws.wm._wins["jukebox"].content_rect()[:2] if desk else (0, 0)
    return ws, drv, app, view, at


def _offset(app, view):
    return ui_draw._C.api_offset(app._ui.ctx, view[0] + view[2] // 2, view[1] + 4)


@pytest.mark.parametrize("desk", (False, True))
def test_the_radio_list_drags_with_the_finger_and_flings_on(tmp_path, face, desk):
    """A drag over the stations scrolls them with the finger; let go at
    speed, the list coasts on painted frames with no input and comes to
    rest inside its rows (the Radio list once took a drag as a tap: its
    pointer never reported the finger held)."""
    ws, drv, app, view, at = _list_on(tmp_path, desk)
    rh = app._row_h()
    n = len(app._station_rows())
    assert n * rh > view[3], "the stations overflow the list"
    sx = at[0] + view[0] + view[2] // 2
    sy = at[1] + view[1] + view[3] - rh // 2
    step = rh * 3 // 5
    drv.touch(sx, sy)
    drv.frame(1 / 30)
    for k in range(1, 6):
        drv.touch_drag(sx, sy - step * k)
        drv.frame(1 / 30)
        assert _offset(app, view) == step * k, "the rows follow the finger"
    drv.touch_up()
    drv.frame(1 / 30)
    let_go = _offset(app, view)
    drawn = ws.frame_state.frames_drawn
    coast = [let_go]
    for _ in range(90):
        drv.frame(1 / 30)                   # no input: the fling's own frames
        coast.append(_offset(app, view))
    assert coast[5] > let_go, "the list coasts on after the release"
    assert coast == sorted(coast) and coast[-1] == coast[-2], "and slows to rest"
    assert coast[-1] <= n * rh - view[3]
    assert ws.frame_state.frames_drawn - drawn >= 5, "each coasting step is a painted frame"
    assert ws.wm.top_kind() == "jukebox" and face.played == [], "the drag opened nothing"


@pytest.mark.parametrize("desk", (False, True))
def test_the_keys_move_the_current_station_and_the_view_follows(tmp_path, face, desk):
    ws, drv, app, view, _at = _list_on(tmp_path, desk)
    rh = app._row_h()
    n = len(app._station_rows())
    for _ in range(n + 2):
        drv.press("down")
        for _ in range(12):                 # the glide's frames
            drv.frame(1 / 30)
        cur, off = app.cur[0], _offset(app, view)
        assert off <= cur * rh and (cur + 1) * rh <= off + view[3], "the current row shows"
    assert app.cur[0] == n - 1
    for _ in range(n):
        drv.press("up")
        for _ in range(12):
            drv.frame(1 / 30)
    assert app.cur[0] == 0 and _offset(app, view) == 0
