# Map (grep -n a name to jump there):
#   Stream              what plays: the media face, the status item, the poll
#   card_of             the media card's answers from the face's
#   Card                the media card at its densities (strip, compact, wide, full)
#   MusicSection        the status item's section (the desk menu, the control center)
#   MusicAppLayer       the app's pages
#   _scan               the music folder's songs and playlists
"""Music -- the console's radio and music player (#110), a SYSTEM APP
(docs/app_api_v1.md) drawn with the public UI API's verbs, over the media
service's face, `moybyte.media` (runtime/system/dev_media.py). Its design is
docs/media/music_2026-11/README.md.

  * Radio: the curated stations and the ones the person added (Add a
    station: an http:// address, or https:// where the console verifies
    it, and a name), kept in the app's prefs;
  * My music: the MP3s and the .m3u/.pls playlists in the music folder of
    the user files (`files/Music/` on the SD card);
  * Now playing: the media card at its full density, its keys the page's
    own (Enter plays or pauses, Q E step, W S the volume, left and right
    seek a file SEEK_STEP seconds, M the list), a file's bar a stop a tap
    seeks along;
  * on the desk one window: the Library's two places beside the table, the
    media card in one row across its foot.

What plays is the media service's stream, the system's (the app is one of
its clients): leaving the app keeps it playing, and Back on the desk
minimises the window. While the service runs the app's status item is in
the bar (`moybyte.status`); its section, the compact card, is where it is
paused and stopped from anywhere (the desk's menu, a handheld's control
center). What another client plays, and what a VM stop left playing, the
card shows as well (`Stream.follow`, from the label each play() names).
A console with no media service (the Guition S3) shows the no-audio page.
"""

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui

try:
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui_draw as _ui_draw

try:
    from layout_base import bar_rows, content_of, metric_class, DESK
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import bar_rows, content_of, metric_class, DESK

try:
    from moy_store_base import builtin_name, files_root
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import builtin_name, files_root

try:
    import moybyte as _moybyte
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import moybyte as _moybyte

try:
    import devices as _devices
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import devices as _devices

try:
    import status_items as _status
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import status_items as _status

try:
    import moy_log as _L
except ImportError:  # host: the runtime package
    try:
        from runtime import moy_log as _L
    except ImportError:  # pragma: no cover - an image without the kernel
        _L = None

from array import array

# The curated stations: name, style, address (each answers 200 with
# audio/mpeg MP3 over plain http to the media client's request, and over
# https, every redirect https too, to a browser page's).
STATIONS = (
    ("Groove Salad", "Ambient beats", "http://ice1.somafm.com/groovesalad-128-mp3"),
    ("Drone Zone", "Ambient", "http://ice1.somafm.com/dronezone-128-mp3"),
    ("Deep Space One", "Space ambient", "http://ice1.somafm.com/deepspaceone-128-mp3"),
    ("Space Station Soma", "Electronica", "http://ice1.somafm.com/spacestation-128-mp3"),
    ("Mission Control", "Space", "http://ice1.somafm.com/missioncontrol-128-mp3"),
    ("Secret Agent", "Lounge", "http://ice1.somafm.com/secretagent-128-mp3"),
    ("Indie Pop Rocks!", "Indie pop", "http://ice1.somafm.com/indiepop-128-mp3"),
    ("Underground 80s", "Synthpop", "http://ice1.somafm.com/u80s-128-mp3"),
    ("Left Coast 70s", "70s rock", "http://ice1.somafm.com/seventies-128-mp3"),
    ("Sonic Universe", "Jazz", "http://ice1.somafm.com/sonicuniverse-128-mp3"),
    ("Folk Forward", "Folk", "http://ice1.somafm.com/folkfwd-128-mp3"),
    ("Thistle Radio", "Celtic", "http://ice1.somafm.com/thistle-128-mp3"),
    ("Radio Paradise", "Eclectic", "http://stream.radioparadise.com/mp3-128"),
    ("Radio Swiss Jazz", "Jazz", "http://stream.srg-ssr.ch/srgssr/rsj/mp3/128"),
)

# A station's (or list's) art: a colour field, two MOY64 colours top to
# bottom. One the table does not name takes a pair by its name.
ART = {"Groove Salad": (58, 40), "Drone Zone": (60, 22), "Deep Space One": (61, 43),
       "Space Station Soma": (59, 42), "Mission Control": (54, 12),
       "Secret Agent": (56, 36)}
ART_PAIRS = ((58, 40), (60, 22), (61, 43), (59, 42), (54, 12), (56, 36), (57, 37),
             (52, 20), (46, 28))

TABS = ("Radio", "My music")
MUSIC_DIR = "Music"
SONG_EXT = (".mp3",)
LIST_EXT = (".m3u", ".pls")
STATIONS_KEY = "stations"
VOLUME_KEY = "volume"
STATIONS_MAX = 24
URL_MAX = 200
NAME_MAX = 32
POLL_MS = 250
VOLUME_STEP = 10
SEEK_STEP = 10              # seconds a key seeks a file
ERR_ADDRESS = "This address won't play. Use one that starts with http://"
HELP_ADDRESS = "An MP3 stream or playlist"
FOLDER_MAX = 200

PLAYING, PAUSED_, OPENING, FAILED = "playing", "paused", "opening", "failed"
EACCES = 13                 # a station that does not let a browser's page read it


# -- what plays ----------------------------------------------------------------

def _media():
    return _devices.api("media")


def _is_url(src):
    return src[:7].lower() == "http://" or src[:8].lower() == "https://"


def _stem(path):
    name = path[path.rfind("/") + 1:]
    dot = name.rfind(".")
    return name[:dot] if dot > 0 else name


def _art_of(name):
    pair = ART.get(name)
    if pair is None:
        h = 0
        for c in name:
            h = (h * 31 + ord(c)) & 0xffff
        pair = ART_PAIRS[h % len(ART_PAIRS)]
    return pair


def _clock(s):
    s = max(0, int(s))
    return "%d:%02d" % (s // 60, s % 60)


def _as_shell(verb):
    """`verb` made as the shell (pid 1), whatever process is current."""
    def shell_verb(*a, **k):
        if _L is None:
            return verb(*a, **k)
        was = _L.current()
        _L.current(_devices.SHELL)
        try:
            return verb(*a, **k)
        finally:
            _L.current(was)
    return shell_verb


class Stream:
    """What plays and what the person chose to play: the media face's
    answers read on a wake while a stream is open, the status item that
    shows it in the bar, and the verbs every surface acts with. One for the
    console (`STREAM`): the app's pages and its section read the same.

    Music is the shell's, so its verbs are the shell's client and its wake
    and status item the shell's (`_as_shell`), never the process a running
    game, or the Editor's run, makes current."""

    def __init__(self):
        self.face = None            # the media face, else moybyte.media's (a test's stand-in)
        self.what = None            # (kind, name, src): "radio", "song" or "list"
        self.card = None            # the media card's answers (`card_of`), None: nothing
        self.item = None
        self.section = None
        self.wake = None
        self.sig = None
        self.stamp = None           # the face's stamp the last full read saw
        self.tick = 0
        self.gen = 0                # moves with anything the card shows
        self.on_change = None       # fn() the app sets: a frame owed
        self.stations = STATIONS    # the radio's list, the app's own after
        self.songs = ()             # the music folder's songs, for a song's step
        self.run = 0                # the service's run the card shows

    def media(self):
        """The media face: a stand-in's where one is set (False: none, a
        console with no audio), else `moybyte.media`, None where absent."""
        f = self.face
        if f is False:
            return None
        return f if f is not None else _media()

    # -- verbs -----------------------------------------------------------------

    @_as_shell
    def play(self, kind, name, src):
        """Play `src` as `kind` named `name`: the stream starts connecting
        (a station) or playing; False with the card failed when the face
        refuses it."""
        self.stamp = None
        m = self.media()
        if m is None:
            return False
        self.what = (kind, name, src)
        try:
            m.play(src, label=label_of(kind, name))
        except OSError:
            self.card = card_of(None, self.what, FAILED)
            self._changed()
            self._start()
            return False
        self._start()
        self.poll()
        return True

    @_as_shell
    def toggle(self):
        """Pause or play; a stream that failed is tried again."""
        self.stamp = None
        m = self.media()
        c = self.card
        if m is None or c is None:
            return
        if c["state"] == FAILED:
            if self.what is not None:
                self.play(*self.what)
            return
        if c["paused"]:
            m.resume()
        else:
            m.pause()
        self.poll()

    @_as_shell
    def seek(self, s):
        """The file played from `s` seconds, held inside it; False for a
        stream with no length (a station) or a seek the face refuses."""
        self.stamp = None
        m = self.media()
        c = self.card
        if m is None or c is None or not c["len"]:
            return False
        try:
            ok = m.seek(max(0, min(c["len"] - 1, int(s))))
        except OSError:
            ok = False
        self.poll()
        return bool(ok)

    @_as_shell
    def stop(self):
        m = self.media()
        if m is not None:
            try:
                m.stop()
            except OSError:
                pass
        self._end()

    @_as_shell
    def step(self, d):
        """The next (`d` 1) or previous station, song or list entry."""
        self.stamp = None
        w = self.what
        m = self.media()
        if w is None or m is None:
            return
        kind = w[0]
        if kind == "list":
            try:
                (m.next if d > 0 else m.prev)()
            except OSError:
                return
            self.poll()
            return
        pool = self.stations if kind == "radio" else self.songs
        names = [p[0] for p in pool]
        if not names:
            return
        i = names.index(w[1]) if w[1] in names else -1
        i = (i + d) % len(names)
        p = pool[i]
        self.play(kind, p[0], p[2] if kind == "radio" else p[1])

    @_as_shell
    def volume(self, v=None):
        self.stamp = None
        m = self.media()
        if m is None:
            return 0
        if v is not None:
            v = max(0, min(100, int(v)))
            try:
                m.volume(v)
            except OSError:
                pass
            self.poll()
        c = self.card
        return c["volume"] if c is not None else 0

    @_as_shell
    def follow(self):
        """The service's stream shown, whoever played it: Music's own after
        a VM stop, or another client's (its label says what, else its
        source). False where nothing plays."""
        m = self.media()
        n = m.now() if m is not None else None
        if n is None:
            return False
        self.what = what_of(n)
        self.stamp = None
        self._start()
        self.poll()
        return True

    # -- the wake ----------------------------------------------------------------

    def _start(self):
        if self.wake is None:
            self.wake = _moybyte.every(POLL_MS, self.poll)

    def _end(self):
        w = self.wake
        self.wake = None
        if w is not None:
            _moybyte.cancel(w)
        it = self.item
        self.item = None
        if it is not None:
            it.remove()
        self.card = None
        self.what = None
        self.sig = None
        self.stamp = None
        self._changed()

    def poll(self):
        """The face read: the card, the status item's state and a frame
        where what shows moved. A stream gone (stopped elsewhere, its list
        ended) takes the item out. While the face's stamp holds still
        (a station playing on under a game) it reads nothing else and
        allocates nothing."""
        m = self.media()
        if m is not None and self.sig is not None and self.sig[0] != OPENING:
            st = m.stamp() if hasattr(m, "stamp") else None
            if st is not None and st == self.stamp:
                return
            self.stamp = st
        n = None
        if m is not None:
            try:
                n = m.now()
            except OSError:
                n = None
        if n is None and self.what is not None and self._failed(m):
            return
        if n is None or self.what is None:
            self._end()
            return
        lab = n.get("label")
        if lab is not None and lab != label_of(*self.what[:2]):
            self.what = what_of(n)           # another client's play: what it named
        self.run = n.get("pid", 0)
        if n["state"] == "ended":
            self.stop()
            return
        c = card_of(n, self.what)
        self.card = c
        if c["state"] == OPENING:
            self.tick += 1
        sig = (c["state"], c["paused"], c["title"], c["station"], c["entry"],
               c["volume"], c["at"], c["len"], self.tick if c["state"] == OPENING else 0)
        if sig != self.sig:
            item_moved = self.sig is None or sig[:6] != self.sig[:6] or c["state"] == OPENING
            self.sig = sig
            self._item(c["state"] if not c["paused"] else PAUSED_, item_moved)
            self._changed()

    def _failed(self, m):
        """The card failed: the run it showed ended with nothing played (the
        service's own end), or a play the face refused. True while it shows
        that."""
        c = self.card
        if c is not None and c["state"] == FAILED:
            self._item(FAILED)
            return True
        e = m.last() if m is not None and self.run and hasattr(m, "last") else None
        if e is None or e["pid"] != self.run or e["state"] != "failed":
            return False
        self.run = 0
        self.card = card_of(None, self.what, FAILED)
        self._item(FAILED)
        self._changed()
        return True

    def _item(self, state, moved=True):
        it = self.item
        if it is None:
            sec = self.section
            if sec is None:
                return
            it = _moybyte.status("music", sec, 0)
            if it is None:
                return
            it.app = "Music"
            self.item = it
        it.show(_status.ERROR if state == FAILED else
                _status.PAUSED if state == PAUSED_ else _status.ON)
        if moved:
            it.changed()

    def _changed(self):
        self.gen = (self.gen + 1) & 0x3fffffff
        fn = self.on_change
        if fn is not None:
            fn()


STREAM = Stream()


def label_of(kind, name):
    """What Music names a play() it makes (the face's `label`)."""
    return "%s\t%s" % (kind, name)


def what_of(n):
    """(kind, name, src) of what the face's now() plays: Music's own label's,
    else the source's (a list, a station or a song) and its client's name."""
    kind, _, name = (n.get("label") or "").partition("\t")
    src = n.get("src") or ""
    if kind in ("radio", "song", "list") and name:
        return kind, name, src
    if (n.get("entries") or 1) > 1:
        kind = "list"
    else:
        kind = "radio" if _is_url(src) else "song"
    return kind, n.get("title") or n.get("who") or _stem(src), src


def follow(ws=None):
    """A run of the media service the face found (dev_media.listen: any
    client's play that started one, or the run a VM stop left playing)
    shown in the bar, its section opening the app."""
    s = STREAM
    if s.section is None and ws is not None:
        nav = None
        try:
            nav = ws.app_context(MusicAppLayer.id, ("nav",)).nav
        except Exception:  # noqa: BLE001 -- a section with no head still shows the card
            nav = None
        s.section = MusicSection(s, nav)
    return s.follow()


def card_of(n, what, state=None):
    """The media card's answers: state ("playing", "opening", "failed"),
    paused, the title, the station (or the list, or "My music"), the place
    in a list, the volume, the kind, and a file's place and length in
    seconds (0 where it has none)."""
    kind, name, _src = what
    st = state or (n["state"] if n else FAILED)
    if st not in (PLAYING, OPENING, FAILED):
        st = PLAYING if st == "idle" else st
    title = (n or {}).get("title") or ""
    if kind == "song":
        title = name
        station = "My music"
    elif kind == "list":
        title = title or name
        station = name
    else:
        station = name
    return {"state": st, "paused": bool(n and n.get("paused")), "title": title,
            "station": station, "entry": (n or {}).get("entry", 0) or 0,
            "entries": (n or {}).get("entries", 1) or 1,
            "volume": (n or {}).get("volume", 0) or 0, "kind": kind,
            "at": (n or {}).get("at", 0) or 0, "len": (n or {}).get("len", 0) or 0,
            "err": (n or {}).get("err", 0) or 0}


def _artist_title(text):
    """An ICY StreamTitle's "Artist - Title" as (title, artist)."""
    k = text.find(" - ")
    if k > 0:
        return text[k + 3:], text[:k]
    return text, ""


def lines(c):
    """The card's two lines, one order everywhere: the title, then the
    artist and the station (or the list and its place)."""
    if c["state"] == OPENING:
        return c["station"], "Connecting..."
    if c["state"] == FAILED:
        return c["station"], "Blocks browsers" if c.get("err") == EACCES else "Not answering"
    title, artist = _artist_title(c["title"])
    if c["kind"] == "list":
        return title, "%s · %d of %d" % (c["station"], c["entry"] + 1, c["entries"])
    if c["kind"] == "radio" and not title:
        return c["station"], "Live"
    if c["kind"] == "song":
        return title, c["station"]
    return title, (artist + " · " if artist else "") + c["station"]


def _fit(meta, w, c):
    """A line too wide for `w` drops a part at its separator, never cut
    inside one: a station keeps its name, a list its own."""
    if " · " not in meta or ui.text_size(meta)[0] <= w:
        return meta
    k = meta.find(" · ")
    return meta[k + 3:] if c["kind"] == "radio" else meta[:k]


# -- drawing ---------------------------------------------------------------------

def _canvas():
    return _ui_draw.UI_TARGET[1]


def paused_ink(th):
    """The paused state's ink: the theme's `paused` content role (MOY64 50
    in Moy Night), the dim ink where the theme names none."""
    v = th.get("paused")
    return v if v is not None else th["ink_dim"]


def eq(cv, rect, c, fs, lively=True, size=12):
    """The equalizer, the state of what plays: four bars on one baseline,
    lively while it plays, 2 px and still while paused."""
    x, y, w, h = rect
    s = size * fs
    bw = max(2 * fs, s // 4)
    gap = max(fs, s // 8)
    x0 = x + (w - 4 * bw - 3 * gap) // 2
    base = y + h // 2 + s // 2
    for k in range(4):
        bh = (s * _EQ[k] // 10) if lively else 2 * fs
        cv.rect(x0 + k * (bw + gap), base - bh, bw, bh, c)


_EQ = (6, 10, 4, 8)


def pulse(cv, rect, on, off, fs, lit=1):
    """Connecting: three 2 px dots centred on the line, one lit, stepping."""
    x, y, w, h = rect
    d = 2 * fs
    x0 = x + (w - 5 * d) // 2
    cy = y + (h - d) // 2
    for k in range(3):
        cv.rect(x0 + 2 * k * d, cy, d, d, on if k == lit else off)


def chevron(cv, x, y, h, c, fs, desk=False):
    """The toolkit's one chevron (a value row's), right pointing, its
    column at x, centred on a line at y `h` tall; its width."""
    w = _ui_draw.metrics("value_row")[3 if desk else 2] * fs
    half = w // fs - 1
    rows = 2 * half + 1
    y0 = y + (h - rows * fs) // 2
    for i in range(rows):
        o = i if i <= half else rows - 1 - i
        cv.rect(x + o * fs, y0 + i * fs, fs, fs, c)
    return w


def art(cv, rect, name, fs, r=8):
    """What plays, as art: its colour field. It carries no state."""
    x, y, w, h = rect
    a, b = _art_of(name)
    cv.rrect(x, y, w, h, r * fs, (0, 0, ((0, a), (1000, b))))
    cv.circ(x + w * 3 // 4, y + h // 3, max(3 * fs, w // 7), b)


class Card:
    """What plays, one component at its densities, the same lines, state
    and transport in each: `strip` (a band over a list's foot), `compact`
    (a card: the head over the transport), `wide` (compact in one row, the
    desk window's foot) and `full` (Now playing). Its stops' ids are the
    app's own (`music.*`): the control center lays every app's section in
    one page."""

    def __init__(self, cv, th, fs, desk):
        self.cv, self.th, self.fs, self.desk = cv, th, fs, desk
        self.c = None
        self.acted = None           # the verb a stop activated this frame
        self.to = 0                 # the second a tap on the file's bar seeks to

    def _main(self):
        c = self.c
        if c["state"] == FAILED:
            return "retry", "Try again"
        return ("play", "Play") if c["paused"] else ("pause", "Pause")

    def _button(self, rect, rid, kind, icon):
        ui.at(*rect)
        ui.icon(icon)
        if ui.button("", kind, {"id": rid}):
            self.acted = rid

    def transport(self, x, y, w, h, gap, stop=True):
        """Previous, the main button (play, pause or retry), next, and stop
        after a double gap, on a 1:2:1 + 1 grid across `w`."""
        n = 5 if stop else 4
        unit = (w - (n - 1) * gap) // n
        x += (w - n * unit - (n - 1) * gap) // 2
        g = self._main()[0]
        self._button((x, y, unit, h), "music.prev", "normal", "prev")
        x += unit + gap
        self._button((x, y, 2 * unit, h), "music.main", "primary", g)
        x += 2 * unit + gap
        self._button((x, y, unit, h), "music.next", "normal", "next")
        x += unit + gap
        if stop:
            x += gap
            self._button((x, y, unit, h), "music.stop", "normal", "stop")

    def state_line(self, x, y, w, size=8, meta=None):
        """The state glyph (the equalizer, or the pulse while connecting)
        and the second line, cut at its separator where it does not fit."""
        c, fs, th, cv = self.c, self.fs, self.th, self.cv
        if meta is None:
            meta = lines(c)[1]
        lh = ui.text_size("Ag", fs)[1] + 2 * fs
        tx = x
        gw = size * 2 * fs
        if c["state"] == PLAYING:
            eq(cv, (x, y, gw, lh), th["value"] if not c["paused"] else paused_ink(th), fs,
               not c["paused"], size)
            tx = x + gw + 2 * fs
        elif c["state"] == OPENING:
            pulse(cv, (x, y, gw, lh), th["value"], paused_ink(th), fs, STREAM.tick % 3)
            tx = x + gw + 2 * fs
        meta = _fit(meta, x + w - tx, c)
        ui.at(tx, y, max(1, x + w - tx), lh)
        ui.label(meta, _DANGER if c["state"] == FAILED else _DIM)

    def head(self, x, y, w, a, title_face="ui", chev=True):
        """Art, the title, the state line, and the chevron."""
        fs = self.fs
        art(self.cv, (x, y, a, a), self.c["station"], fs, 6 if a < 48 * fs else 8)
        tx = x + a + 8 * fs
        cw = 6 * fs if chev else 0
        tw = x + w - tx - cw - (6 * fs if chev else 0)
        title = lines(self.c)[0]
        lh = ui.text_size("Ag", fs)[1] + 2 * fs
        ty = y + (a - 2 * lh) // 2
        ui.at(tx, ty, tw, lh)
        ui.label(title, _FACE_TITLE if title_face == "title" else _FACE_UI)
        self.state_line(tx, ty + lh, tw)
        if chev:
            chevron(self.cv, x + w - cw, y, a, self.th["ink_dim"], fs, self.desk)

    def compact_h(self):
        fs = self.fs
        return 8 * fs + 36 * fs + 6 * fs + 26 * fs + 8 * fs

    def compact(self, x, y, w, head_id="music.open"):
        """The compact card's contents in (x, y, w), its card drawn by the
        caller: the head (one stop that opens the app) over the transport."""
        fs = self.fs
        p = 8 * fs
        a = 36 * fs
        bh = 26 * fs
        ui.at(x + 2 * fs, y + 4 * fs, w - 4 * fs, a + 8 * fs)
        st = ui.part(head_id, 0, 0, _HEAD)[4]
        if st & 2:
            self.acted = head_id
        self.head(x + p, y + p, w - 2 * p, a)
        self.transport(x + p, y + p + a + 6 * fs, w - 2 * p, bh, 6 * fs)

    def strip(self, x, y, w, h):
        """The strip: a band over the page's foot, the head (a stop that
        opens Now playing) and the main button."""
        fs, th, cv, c = self.fs, self.th, self.cv, self.c
        cv.rect(x, y, w, h, th["surface_alt"])
        cv.rect(x, y, w, fs, th["border"])
        bw = 34 * fs
        a = h - 10 * fs
        hx, hy, hw = x + 8 * fs, y + 5 * fs, w - bw - 24 * fs
        ui.at(x, y + fs, hw + 12 * fs, h - fs)
        if ui.part("music.now", 0, 0, _STRIP_HEAD)[4] & 2:
            self.acted = "music.now"
        art(cv, (hx, hy, a, a), c["station"], fs, 4)
        tx = hx + a + 8 * fs
        cw = 6 * fs
        title = lines(c)[0]
        lh = ui.text_size("Ag", fs)[1] + fs
        ty = y + (h - 2 * lh) // 2
        ui.at(tx, ty, hx + hw - tx - cw - 6 * fs, lh)
        ui.label(title, _FACE_UI)
        self.state_line(tx, ty + lh, hx + hw - tx - cw - 6 * fs, 7)
        chevron(cv, hx + hw - cw, y, h, th["ink_dim"], fs)
        self._button((x + w - bw - 8 * fs, y + 6 * fs, bw, h - 12 * fs), "music.strip",
                     "tonal", self._main()[0])

    def wide(self, x, y, w, h, vol=True):
        """The compact card in one row (the desk window's foot): the head,
        the transport, then the volume."""
        trans = 5 * 40 + 4 * 8
        vol_w = 200 if vol else 0
        bx = x + w - vol_w - (20 if vol else 0) - trans
        self.head(x, y, bx - 12 - x, h, "title", chev=False)
        self.transport(bx, y + (h - 34) // 2, trans, 34, 8)
        if vol:
            self.volume_row(x + w - vol_w, y + (h - 32) // 2, vol_w, 32)

    def full(self, x, y, w, a):
        """Now playing's head: the art, the title, the artist then the
        station (the list, then the place), LIVE or the file's bar."""
        fs, c = self.fs, self.c
        art(self.cv, (x, y, a, a), c["station"], fs)
        tx = x + a + 12 * fs
        tw = x + w - tx
        title, meta = lines(c)
        k = meta.find(" · ")
        first, second = (meta[:k], meta[k + 3:]) if k >= 0 else (meta, "")
        ui.at(tx, y, tw, 20 * fs)
        ui.label(title, _FACE_TITLE)
        self.state_line(tx, y + 20 * fs, tw, meta=first)
        if second:
            ui.at(tx, y + 37 * fs, tw, 18 * fs)
            ui.label(second, _DIM)
        self.status(tx, y + 56 * fs, tw)

    def status(self, x, y, w):
        """◆ LIVE for a station; a file's bar and its time."""
        fs, th, cv, c = self.fs, self.th, self.cv, self.c
        if c["state"] != PLAYING:
            return
        if c["kind"] == "radio":
            live = not c["paused"]
            ink = th["value"] if live else paused_ink(th)
            cy = y + 8 * fs
            for i in range(7):
                half = _DIAMOND[i]
                cv.rect(x + (3 - half) * fs, cy - 3 * fs + i * fs, (2 * half + 1) * fs, fs, ink)
            ui.at(x + 11 * fs, y, 60 * fs, 18 * fs)
            ui.label("LIVE", _VALUE if live else _DIM)
            return
        if not c["len"]:
            return
        t = "%s / %s" % (_clock(c["at"]), _clock(c["len"]))
        tw = ui.text_size(t)[0]
        bw = w - tw - 10 * fs
        ui.at(x, y, bw, 18 * fs)
        if ui.part("music.seek", 0, 0, _METER_PANEL)[4] & 2:
            at = ui.part_at()
            if at is not None:
                self.to = c["len"] * max(0, min(bw, at[0])) // max(1, bw)
                self.acted = "music.seek"
        cv.rrect(x, y + 8 * fs, bw, 4 * fs, 2 * fs, th["border"])
        done = bw * min(c["at"], c["len"]) // c["len"]
        if done > 0:
            cv.rrect(x, y + 8 * fs, done, 4 * fs, 2 * fs, th["value"])
        ui.at(x + bw + 8 * fs, y, tw + 2 * fs, 18 * fs)
        ui.label(t, _DIM)

    def volume_row(self, x, y, w, h, ground="surface"):
        """The volume: minus, the speaker and its meter (ten steps), plus.
        The meter is one stop, left and right its change."""
        fs = self.fs
        bw = 32 * fs
        self._button((x, y, bw, h), "music.quieter", "normal", "minus")
        self._button((x + w - bw, y, bw, h), "music.louder", "normal", "plus")
        mx, mw = x + bw + 6 * fs, w - 2 * bw - 12 * fs
        level = (self.c["volume"] + 5) // 10
        ui.at(mx, y, mw, h)
        st = ui.part("music.volume", 0, 0, _METER_PANEL if ground == "panel" else _METER)[4]
        isz = 16 * fs
        ui.at(mx + 6 * fs, y + (h - 18 * fs) // 2 + 2 * fs, isz + 2 * fs, 18 * fs)
        ui.icon("volume")
        ui.label(" ", _FACE_UI)
        sx = mx + 6 * fs + isz + 6 * fs
        sw = mx + mw - 6 * fs - sx
        gap = 2 * fs
        seg = (sw - 9 * gap) // 10
        sh = 8 * fs
        th = self.th
        for i in range(10):
            self.cv.rect(sx + i * (seg + gap), y + (h - sh) // 2, seg, sh,
                         th["value"] if i < level else th["border"])
        if st & 2:
            at = ui.part_at()
            if at is not None:
                self.level = max(0, min(10, (at[0] - (sx - mx) + seg) // (seg + gap)))
                self.acted = "music.volume"


_DIAMOND = (0, 1, 2, 3, 2, 1, 0)
_DIM = {"tone": "dim"}
_VALUE = {"tone": "value"}
_DANGER = {"tone": "danger"}
_FACE_UI = {"face": "ui"}
_FACE_TITLE = {"face": "title"}
_HEAD = {"fill": "surface", "border": "surface", "id": "music.open"}
_STRIP_HEAD = {"fill": "surface_alt", "border": "surface_alt"}
_METER = {"fill": "surface", "border": "surface"}
_METER_PANEL = {"fill": "panel", "border": "panel"}


def act(card, stream):
    """What the card's stop that activated this frame does; True where it
    acted."""
    a = card.acted
    if a is None:
        return False
    if a == "music.main" or a == "music.strip":
        stream.toggle()
    elif a == "music.prev":
        stream.step(-1)
    elif a == "music.next":
        stream.step(1)
    elif a == "music.stop":
        stream.stop()
    elif a == "music.quieter":
        stream.volume(stream.volume() - VOLUME_STEP)
    elif a == "music.louder":
        stream.volume(stream.volume() + VOLUME_STEP)
    elif a == "music.volume":
        stream.volume(card.level * 10)
    elif a == "music.seek":
        stream.seek(card.to)
    else:
        return False
    return True


# -- the status item's section ---------------------------------------------------

class MusicSection:
    """The status item's section (runtime/system/status_items.py): the
    compact card, on the desk with the volume under it. Its head opens the
    app; its buttons act there and then."""

    def __init__(self, stream, nav=None):
        self.stream = stream
        self.nav = nav

    def height(self, w, desk, fs):
        h = 8 * fs + 36 * fs + 6 * fs + 26 * fs + 8 * fs
        if desk:
            h += (8 + 1 + 8 + 30) * fs
        return h

    def draw(self, desk):
        s = self.stream
        if s.card is None:
            return
        cv = _canvas()
        fs = _fs()
        x, y, w, h, _st = ui.part("music.card", 0, self.height(0, desk, fs), _CARD_ROW)
        k = Card(cv, _theme(), fs, desk)
        k.c = s.card
        if desk:
            cv.rrect(x, y, w, k.compact_h(), 8 * fs, k.th["surface"], ring=k.th["border"],
                     line=fs)
        k.compact(x, y, w)
        if desk:
            p = 8 * fs
            ry = y + k.compact_h() + p
            cv.rect(x + p, ry, w - 2 * p, fs, k.th["border"])
            k.volume_row(x + p, ry + p, w - 2 * p, 30 * fs)
        if k.acted == "music.open":
            if self.nav is not None:
                self.nav.open_app("jukebox")
            return
        act(k, s)


_CARD_ROW = {"disabled": True, "fill": "surface", "border": "surface", "id": "music.card"}


def _fs():
    return max(1, int(getattr(_canvas(), "font_scale", 1)))


_THEME = [None]


def _theme():
    """The live token table: the app's context's, which the section
    borrows (the shell draws the section as the app)."""
    th = _THEME[0]
    return th.colors() if th is not None else {}


# -- the music folder -------------------------------------------------------------

def _list_count(path):
    """How many songs playlist `path` names."""
    try:
        with open(path, "rb") as f:
            text = f.read(8192)
    except OSError:
        return 0
    n = 0
    pls = path.lower().endswith(".pls")
    for ln in text.split(b"\n"):
        ln = ln.strip()
        if not ln:
            continue
        if pls:
            if ln[:4].lower() == b"file":
                n += 1
        elif ln[:1] != b"#":
            n += 1
    return n


def _scan(folder):
    """The music folder's songs and playlists, sorted by name: (songs,
    lists), each a tuple of (name, path, detail: a song's length, which the
    media face's `probe` reads in C, a list's count); None where the folder
    is not there. A song this console plays but does not keep (a
    board-served page's, which the board keeps: the media face's `held`) is
    listed too, with no length."""
    import os
    m = _media()
    held = getattr(m, "held", None)
    held = held(folder) if held is not None else ()
    probe = getattr(m, "probe", None)
    try:
        names = os.listdir(folder)
    except OSError:
        if not held:
            return None
        names = []
    names = set(names)
    names.update(nm for nm, _size in held)
    songs, lists = [], []
    for nm in sorted(names)[:FOLDER_MAX]:
        low = nm.lower()
        path = folder + "/" + nm
        if low.endswith(SONG_EXT):
            got = probe(path) if probe is not None else None
            sec = got[0] if got is not None else 0
            songs.append((_stem(nm), path, _clock(sec) if sec else ""))
        elif low.endswith(LIST_EXT):
            n = _list_count(path)
            lists.append((_stem(nm), path, "%d song%s" % (n, "" if n == 1 else "s")))
    return tuple(songs), tuple(lists)


# -- the app ------------------------------------------------------------------------

class MusicAppLayer:
    """The Music content Layer and app protocol (see the module docstring)."""

    id = "jukebox"            # "music" is the Editor's Music tab
    domain = "system"
    TITLE = "Music"
    NEEDS = ("surface", "theme", "damage", "prefs", "install", "nav")
    PREFS_NS = "music"          # its prefs, the stations it added: portable
    UI = True
    MIN_W = 300
    MIN_H = 220
    # Back on its first page leaves the app; on the desk it minimises the
    # window. Either way the stream plays on.
    BACK_MINIMISES = True

    def __init__(self, ctx, names):
        self.ctx = ctx
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = ctx.damage
        self._prefs = ctx.prefs
        self._install = ctx.install
        self._nav = ctx.nav
        self._ui = ctx.ui
        _THEME[0] = ctx.theme
        s = STREAM
        s.on_change = self._moved
        if s.section is None:
            s.section = MusicSection(s, ctx.nav)
        self.stream = s
        if s.what is None:
            s.follow()
        self.page = "list"          # "list", "now", "add"
        self.tab = 0
        self.cur = [-1, -1]         # each tab's current row
        self.files = None           # (songs, lists), None: not read; False: no SD
        self.idle_owed = False      # the folder owes a read (open, the tab)
        self.url = ""
        self.name = ""
        self.err = None
        self.leave_asked = False
        self.shown = False          # on screen: from a draw until close()
        self.wifi = True
        self._first = True          # a page or tab opened: its first draw is owed
        self._fresh = False         # this draw is its first
        self._r = array("i", [0] * 4)
        self._key = 0
        self._load_stations()

    # -- the app protocol ------------------------------------------------------

    @staticmethod
    def is_app(cart):
        """The shipped Music identity cart: its title, its marker
        permission, its folder."""
        if (not cart or cart.get("title") != "Music"
                or "music" not in (cart.get("permissions") or ())):
            return False
        path = cart.get("path")
        if not path:
            return int(cart.get("version", 0)) >= 1
        return builtin_name(path) == "moybyte.music.moy"

    def open(self):
        if self.page == "add":
            self.page = "list"
        self.idle_owed = True
        self.wifi = self._has_wifi()
        self._first = True
        self._ui.reset()
        self._damage.all()

    def relayout(self, w, h, fs, cs=None):
        if self.shown:
            self._damage.all()

    def close(self):
        self.shown = False
        if self.page == "add":
            self._nav.text_mode(False)

    def page_title(self):
        return {"now": "Now playing", "add": "Add a station"}.get(self.page)

    def has_page(self):
        return self.page != "list"

    def back(self):
        """Back a page (B, Esc, the bar's chevron): Add a station and Now
        playing step back to the list. False on the list: the host leaves."""
        if self.page == "list":
            return False
        if self.page == "add":
            self._nav.text_mode(False)
        self.page = "list"
        self._first = True
        self._damage.all()
        return True

    def cancel_field(self):
        """The T-Deck's Mic in a field: Add a station cancelled."""
        if self.page != "add":
            return False
        self.back()
        return True

    def _moved(self):
        if self.shown:
            self._damage.all()

    # -- prefs and the radio's list --------------------------------------------

    def _load_stations(self):
        mine = self._prefs.get(STATIONS_KEY, None)
        rows = list(STATIONS)
        if isinstance(mine, list):
            for r in mine[:STATIONS_MAX]:
                if isinstance(r, list) and len(r) == 2 and _is_url(str(r[1])):
                    rows.append((str(r[0])[:NAME_MAX], "Added", str(r[1])[:URL_MAX]))
        self.stream.stations = tuple(rows)

    def _add_station(self, name, url):
        mine = self._prefs.get(STATIONS_KEY, None)
        mine = list(mine) if isinstance(mine, list) else []
        mine.append([name, url])
        self._prefs.set(STATIONS_KEY, mine[-STATIONS_MAX:])
        self._load_stations()

    def _has_wifi(self):
        """Whether the radio can reach a network: a transport, and on a
        board a saved network to dial."""
        net = self._install.net()
        if net is None:
            return True             # no transport to ask: the stream says
        w = getattr(net, "wifi", None)
        if w is None:
            return True
        try:
            if w.status()[0]:
                return True
            return bool(w.known())
        except Exception:  # noqa: BLE001 -- a radio that cannot say has none
            return False

    # -- the folder, off the draw ---------------------------------------------

    def idle_step(self):
        self.idle_owed = False
        root = self._install.root()
        if root is None:
            self.files = False
        else:
            folder = files_root(root) + "/" + MUSIC_DIR
            got = self._install.op(lambda: _scan(folder))
            self.files = got if got is not None else ((), ())
            self.stream.songs = self.files[0]
        if self.shown:
            self._damage.all()

    def _file_rows(self):
        f = self.files
        if not f:
            return ()
        return f[1] + f[0]

    # -- draw --------------------------------------------------------------------

    def draw(self, dt):
        cv = self._surf.canvas()
        th = self._theme.colors()
        fs = max(1, int(getattr(cv, "font_scale", 1)))
        desk = metric_class() == DESK
        win = bool(self._surf.windowed())
        self.cv, self.th, self.fs, self.desk = cv, th, fs, desk
        self.shown = True
        self.touch = ui.input_kind() == "touch"
        # Taken now, not cleared after: a page or tab this draw opens (Enter
        # on Add a station) owes its first draw to the next frame.
        self._fresh, self._first = self._first, False
        s = self.stream
        if s.card is None and s.what is None:
            self._sync()
        self._ui.begin(cv, th, (0, 0, cv.w, cv.h), desk, win)
        ui.page("", _NAV_OFF if self.page == "now" else None)
        cs = max(fs, int(self._surf.chrome_scale() or fs))
        page = content_of(cv.w, cv.h, 0 if win else bar_rows(cs))
        k = Card(cv, th, fs, desk)
        k.c = s.card
        self.k = k
        if s.media() is None:
            self._noaudio(page)
        elif self.page == "add":
            self._add(page)
        elif self.page == "now" and s.card is not None:
            self._now(page)
        elif desk:
            self._desk(page)
        elif self.tab == 0 and not self.wifi:
            self._nowifi(page)
        else:
            self._list(page)
        if k.acted is not None:
            if k.acted == "music.now":
                self._go("now")
            elif act(k, s):
                self._damage.all()
        if self.page == "now":
            ui.focus(_NO_FOCUS)

    def _sync(self):
        """A stream the console already plays (this app's, before it was
        opened again): its card read."""
        m = self.stream.media()
        if m is None:
            return
        try:
            n = m.now()
        except OSError:
            n = None
        if n is not None and self.stream.what is None:
            src = n.get("src") or ""
            kind = "radio" if _is_url(src) else ("list" if n.get("entries", 1) > 1 else "song")
            name = n.get("station") or _stem(src)
            for st in self.stream.stations:
                if st[2] == src:
                    name = st[0]
            self.stream.what = (kind, name, src)
            self.stream._start()
            self.stream.poll()

    def _go(self, page):
        self.page = page
        self._first = True
        self._nav.text_mode(page == "add")
        if page == "add":
            self.url, self.name, self.err = "", "", None
        self._damage.all()

    def _hints(self, page, *choices):
        """The hint strip, 8 px in from either edge: the first of `choices`
        that fits, Back always last."""
        if self.desk:
            return page
        fs = self.fs
        items = choices[-1]
        for c in choices:
            if ui.hints_width(c) <= page[2] - 6 * fs:
                items = c
                break
        if ui.hints_width(items) > 0:
            band, page = ui.cut_bottom(page, 20 * fs)
            ui.at(band[0], band[1], band[2], band[3])
            ui.hints(items)
        return page

    def _row_h(self):
        return _ui_draw.metrics("value_row")[17 if self.desk else 16] * self.fs

    def _tabs(self, page):
        """The two tabs, the toolkit's, the open one filled: no stop of their
        own (Q and E, which ui.tabs takes, or a tap switch them)."""
        fs = self.fs
        band, page = ui.cut_top(page, self._row_h() + 12 * fs)
        ui.at(band[0] + 8 * fs, band[1] + 6 * fs, band[2] - 16 * fs, band[3] - 12 * fs)
        got = ui.tabs("music.tabs", TABS, self.tab, _TABS)
        if got != self.tab and got is not None and got >= 0:
            self._tab(got)
        self.cv.rect(band[0], band[1] + band[3] - fs, band[2], fs, self.th["border"])
        return (page[0], page[1] + 4 * fs, page[2], page[3] - 4 * fs)

    def _tab(self, t):
        self.tab = t
        self._first = True
        if t == 1 and self.files is None:
            self.idle_owed = True
        self._damage.all()

    # -- the rows ----------------------------------------------------------------

    def _station_rows(self):
        c = self.stream.card
        rows = [("plus", "Add a station", "", "lead")]
        for name, style, _url in self.stream.stations:
            rows.append(("music", name, style, _state(c, name, "radio")))
        return rows

    def _files_rows(self, desk=False):
        c = self.stream.card
        rows = []
        f = self.files or ((), ())
        for name, _path, detail in f[1]:
            rows.append(("playlist", name, "Playlist" if desk else detail,
                         _state(c, name, "list"), detail))
        for name, _path, detail in f[0]:
            rows.append(("music", name, "Song" if desk else "", _state(c, name, "song"),
                         detail))
        return rows

    def _rows(self, rect, rows, cur, oid):
        """`rows` (icon, name, right, state[, more]) as a table of parts in
        `rect`, cut to whole rows: (the new current row, opened)."""
        rh = self._row_h()
        x, y, w, h = rect
        shown = max(1, h // rh)
        keys = [(r[1] + "\t" + r[2] + "\t" + (r[3] or "") + "\t"
                 + (r[4] if self.desk and len(r) > 4 else ""),) for r in rows]
        ui.at(x, y, w, shown * rh)
        got = ui.table("", ("",), keys, cur, {"id": oid, "part": (0, rh), "current": False})
        r = self._r
        i = ui.table_part(r)
        while i >= 0:
            self._row(r[0], r[1], r[2], rh, rows[i])
            i = ui.table_part(r)
        return got

    def _row(self, cx, cy, cw, rh, row):
        """One row: its leading icon (the equalizer where it plays), its
        name, its right column (and on the desk its length)."""
        fs = self.fs
        icon, name, right, st = row[0], row[1], row[2], row[3]
        more = row[4] if self.desk and len(row) > 4 else None
        pad = 10 if self.desk else 8 * fs
        isz = 16 if self.desk else 16 * fs
        ix = cx + pad
        th, cv = self.th, self.cv
        if st in (PLAYING, PAUSED_):
            eq(cv, (ix, cy, isz, rh), th["value"] if st == PLAYING else paused_ink(th),
               fs, st == PLAYING, 10)
        elif st == OPENING:
            pulse(cv, (ix, cy, isz, rh), th["value"], paused_ink(th), fs,
                  self.stream.tick % 3)
        else:
            ui.at(ix, cy, isz + 2 * fs, rh)
            ui.icon(icon)
            ui.label(" ", _VALUE if st == "lead" else _FACE_UI)
        tx = ix + isz + 8 * fs
        rx = cx + cw - pad
        if more is not None:
            mw = ui.text_size(more)[0]
            ui.at(rx - mw, cy, mw + fs, rh)
            ui.label(more, _DIM)
            rx -= 90
        rw = ui.text_size(right)[0] if right else 0
        ui.at(tx, cy, max(1, rx - rw - 8 * fs - tx), rh)
        ui.label(name, _VALUE if st and st != "lead" else _FACE_UI)
        if right:
            ui.at(rx - rw, cy, rw + fs, rh)
            ui.label(right, _DIM)

    def _strip(self, page):
        if self.k.c is None:
            return page
        h = 34 * self.fs
        strip, page = ui.cut_bottom(page, h)
        self.k.strip(strip[0], strip[1], strip[2], h)
        return page

    # -- the handheld pages --------------------------------------------------------

    def _verb(self, rows, cur):
        """The confirm hint's word: what Enter does to the current row."""
        if 0 <= cur < len(rows):
            st = rows[cur][3]
            if st == "lead":
                return "Open"
            if st == PLAYING:
                return "Pause"
            if st == PAUSED_:
                return "Play"
        return "Play"

    def _list_hints(self, verb, other):
        if self.stream.card is not None:
            return (("confirm", verb, "tabs", other, "more", "Player", "back", "Back"),
                    ("confirm", verb, "more", "Player", "back", "Back"))
        return (("confirm", verb, "tabs", other, "back", "Back"),)

    def _list(self, page):
        t = self.tab
        if t == 0:
            rows = self._station_rows()
        else:
            rows = self._files_rows()
        cur = self.cur[t]
        empty = t == 1 and not rows
        if empty:
            page = self._hints(page, ("tabs", "Radio", "back", "Back"))
        else:
            page = self._hints(page, *self._list_hints(self._verb(rows, cur),
                                                       TABS[1 - t]))
        page = self._tabs(page)
        page = self._strip(page)
        if empty:
            ui.at(*self._empty_at(page))
            if self.files is False:
                ui.empty("folder", "No SD card",
                         ("Put in an SD card with your songs on it.",
                          "Radio plays without one."))
            elif self.files is not None:
                ui.empty("music", "No music yet",
                         ("Put MP3 songs in the Music folder", "on the SD card."))
            return
        if self._fresh and cur < 0:
            cur = self._playing_row(rows)
        if self._fresh:
            ui.focus("music.rows%d" % t)
        got, opened = self._rows(page, rows, cur, "music.rows%d" % t)
        if got != cur:
            self.cur[t] = got
            self._damage.all()
        if opened and 0 <= got < len(rows):
            self._open_row(t, got, rows)

    def _playing_row(self, rows):
        for k in range(len(rows)):
            if rows[k][3] in (PLAYING, PAUSED_, OPENING):
                return k
        return 1 if len(rows) > 1 and rows[0][3] == "lead" else 0

    def _open_row(self, t, i, rows):
        """Enter (or a tap) on row `i` of tab `t`: Add a station, or play it
        -- the one playing paused or played again."""
        row = rows[i]
        if row[3] == "lead":
            self._go("add")
            return
        if row[3] in (PLAYING, PAUSED_):
            self.stream.toggle()
            return
        if row[3] == FAILED:
            self.stream.toggle()
            return
        if t == 0:
            st = self.stream.stations[i - 1]
            self.stream.play("radio", st[0], st[2])
        else:
            f = self.files
            nl = len(f[1])
            if i < nl:
                self.stream.play("list", f[1][i][0], f[1][i][1])
            else:
                self.stream.play("song", f[0][i - nl][0], f[0][i - nl][1])
        self._damage.all()

    def _empty_at(self, page):
        x, y, w, h = page
        return (x, y - 4 * self.fs, w, h + 4 * self.fs)

    def _now(self, page):
        """Now playing, the card's full density on a 12 px grid. Its keys
        are the page's own: Enter plays, pauses or tries again, Q E step,
        W S the volume, left and right seek a file, M goes to the list,
        Back leaves."""
        fs, k = self.fs, self.k
        c = self.stream.card
        ui.bar("Now playing", (), (), True)
        verb = k._main()[1]
        skip = "Station" if c["kind"] == "radio" else "Song"
        if c["len"]:
            page = self._hints(page,
                               ("confirm", verb, "change", "Seek", "tabs", skip, "scroll",
                                "Volume", "more", "List", "back", "Back"),
                               ("confirm", verb, "change", "Seek", "tabs", "Skip", "scroll",
                                "Volume", "back", "Back"),
                               ("confirm", verb, "change", "Seek", "scroll", "Volume",
                                "back", "Back"),
                               ("confirm", verb, "change", "Seek", "back", "Back"))
        else:
            page = self._hints(page,
                               ("confirm", verb, "tabs", skip, "scroll", "Volume", "more",
                                "List", "back", "Back"),
                               ("confirm", verb, "tabs", "Skip", "scroll", "Volume", "more",
                                "List", "back", "Back"),
                               ("confirm", verb, "tabs", skip, "scroll", "Volume", "back",
                                "Back"),
                               ("confirm", verb, "scroll", "Volume", "more", "List", "back",
                                "Back"),
                               ("confirm", verb, "scroll", "Volume", "back", "Back"))
        ui.focus(_NO_FOCUS)         # the page's keys are its own: no focus mark
        x, y, w, h = page
        g = 12 * fs
        ix, iw = x + g, w - 2 * g
        a = (88 if self.touch else 72) * fs
        bh = (40 if self.touch else 36) * fs
        vh = (32 if self.touch else 28) * fs
        block = a + g + bh + g + vh
        iy = y + max(g, (h - block) // 2)
        k.full(ix, iy, iw, a)
        top = iy + a + g
        k.transport(ix, top, iw, bh, g)
        k.volume_row(ix, top + bh + g, iw, vh, "panel")

    def _add(self, page):
        """Add a station on the 12 px gutter: each field's label over it,
        its line under it, Cancel and Add at the foot. Enter goes to the
        next field and then adds; Mic (Esc, B) cancels."""
        fs = self.fs
        ui.bar("Add a station", (), (), True)
        page = self._hints(page, ("confirm", "Next", "cancel", "Cancel"))
        x, y, w, h = page
        g = 12 * fs
        fx, fw = x + g, w - 2 * g
        rh = self._row_h() + 4 * fs
        lh = ui.text_size("Ag", fs)[1] + 2 * fs
        cy = y + 8 * fs
        err = self.err
        if self._fresh:
            ui.focus("Address")
        nxt = None
        for label, help_ in (("Address", err or HELP_ADDRESS), ("Name", None)):
            ui.at(fx, cy, fw, lh)
            ui.label(label, _CAPTION)
            cy += lh
            ui.at(fx, cy, fw, rh)
            if label == "Address":
                o = _URL_ERR if err else _URL
                text, done = ui.field("", self.url, URL_MAX, o)
                if text != self.url:
                    self.url = text
                    self.err = _check(text, False)
                if done:
                    nxt = "Name"
            else:
                text, done = ui.field("", self.name, NAME_MAX, _NAME)
                self.name = text
                if done:
                    nxt = "Add"
            cy += rh + 2 * fs
            if help_:
                ui.at(fx, cy, fw, 2 * lh if err else lh)
                ui.label(help_, _ERR_LINE if err else _DIM)
                cy += 2 * lh if err else lh
            cy += 6 * fs
        bw = 88 * fs
        by = y + h - 28 * fs - g
        ui.at(x + w - g - bw, by, bw, 28 * fs)
        add = ui.button("Add", "primary", _ADD_OFF if self.err else _ADD)
        ui.at(x + w - 2 * g - 2 * bw + 4 * fs, by, bw, 28 * fs)
        if ui.button("Cancel", "normal", _CANCEL):
            self.back()
            return
        if nxt == "Name":
            ui.focus("Name")
        elif nxt == "Add":
            add = True
        if add:
            self._submit()

    def _submit(self):
        url = self.url.strip()
        err = _check(url, True)
        if err:
            self.err = err
            ui.focus("Address")
            self._damage.all()
            return
        name = self.name.strip()[:NAME_MAX] or _host(url)
        self._add_station(name, url)
        self.cur[0] = len(self.stream.stations)
        self.back()
        self.stream.play("radio", name, url)

    def _noaudio(self, page):
        fs = self.fs
        x, y, w, h = page
        top = y + max(0, h // 2 - 90 * fs)
        ui.at(x, top, w, y + h - top)
        under = ui.empty("mute", "No sound on this console", ())
        for k, line in enumerate(("This console has no speaker,",
                                  "so it can't play music or radio.")):
            ui.at(x, under + 2 * fs + k * 18 * fs, w, 16 * fs)
            ui.label(line, _CENTER)
        bw = 160 * fs
        ui.at(x + (w - bw) // 2, under + 48 * fs, bw, 40 * fs)
        ui.icon("home")
        if ui.button("Back to home", "primary", _HOME):
            self.leave_asked = True

    def _nowifi(self, page):
        fs = self.fs
        page = self._hints(page, ("confirm", "Open", "tabs", "My music", "back", "Back"))
        page = self._tabs(page)
        x, y, w, h = self._empty_at(page)
        ui.at(x, y, w, h)
        under = ui.empty("wifi_off", "Radio needs Wi-Fi",
                         ("Turn on Wi-Fi to listen to stations.",
                          "Your own music plays without it."))
        bw = 150 * fs
        bh = self._row_h() + 4 * fs
        by = min(under, y + h - bh - 8 * fs)
        ui.at(x + (w - bw) // 2, by, bw, bh)
        ui.icon("wifi")
        if self._fresh:
            ui.focus("Wi-Fi settings")
        if ui.button("Wi-Fi settings", "primary", _WIFI):
            self._install.wifi()

    # -- the desk window ---------------------------------------------------------

    def _desk(self, page):
        cv, th = self.cv, self.th
        s = self.stream
        foot_h = 88 if self.k.c is not None else 0
        foot, body = ui.cut_bottom(page, foot_h)
        side, main = ui.cut_left(body, 168)
        sx, sy, sw, sh = ui.inset(side, 6, 6)
        ui.at(sx + 4, sy, sw, 18)
        ui.label("Library", _CAPTION)
        rh = self._row_h()
        places = (("volume", "Radio", "", None), ("music", "My music", "", None))
        ui.at(sx, sy + 20, sw, 2 * rh)
        got, _opened = ui.table("", ("",), _PLACES, self.tab,
                                {"id": "music.library", "part": (0, rh)})
        r = self._r
        i = ui.table_part(r)
        while i >= 0:
            self._row(r[0], r[1], r[2], rh, places[i])
            i = ui.table_part(r)
        if got != self.tab and got is not None and got >= 0:
            self._tab(got)
        cv.rect(side[0] + side[2], side[1], 1, side[3], th["border"])
        mx, my, mw, mh = ui.inset(main, 4, 6)
        head = 20
        t = self.tab
        files = t == 1
        rows = self._files_rows(desk=True) if files else self._station_rows()
        heads = ("Name", "Kind", "Length") if files else ("Station", "Style")
        if self._fresh:
            ui.focus("music.rows%d" % t)
        body = (mx, my + head, mw, mh - head)
        if files and not rows:
            ui.at(*body)
            if self.files is False:
                ui.empty("folder", "No SD card",
                         ("Put in an SD card with your songs on it.",
                          "Radio plays without one."))
            elif self.files is not None:
                ui.empty("music", "No music yet",
                         ("Put MP3 songs in the Music folder", "on the SD card."))
        else:
            cur = self.cur[t]
            if self._fresh and cur < 0:
                cur = self._playing_row(rows)
            got, opened = self._rows(body, rows, cur, "music.rows%d" % t)
            if got != cur:
                self.cur[t] = got
                self._damage.all()
            if opened and 0 <= got < len(rows):
                self._open_row(t, got, rows)
            ui.at(mx + 10 + 16 + 8, my, 120, head)
            ui.label(heads[0], _CAPTION)
            rx = mx + mw - 10 - (8 if len(rows) * rh > body[3] else 0)
            for k in range(len(heads) - 1, 0, -1):
                text = heads[k]
                tw = ui.text_size(text)[0]
                ui.at(rx - tw, my, tw + 2, head)
                ui.label(text, _CAPTION)
                rx -= 90
        if self.k.c is not None:
            cv.rect(foot[0], foot[1], foot[2], 1, th["border"])
            fx, fy, fw, fh = ui.inset(foot, 16, 12)
            self.k.wide(fx, fy, fw, fh)

    # -- input -------------------------------------------------------------------

    def handle_input(self, i):
        k = getattr(i, "last_key", 0) or 0
        if not isinstance(k, int):
            k = ord(k[0]) if k else 0
        edge = k if k != self._key else 0
        self._key = k
        acted = False
        if self.page == "now" and self.stream.card is not None:
            acted = self._now_keys(i, edge)
        elif self.page == "list" and edge:
            ch = chr(edge).lower() if 32 < edge < 127 else ""
            if ch == "m" and self.stream.card is not None:
                self._go("now")
                acted = True
        if self.page == "list" and i.pressed("y") and self.stream.card is not None:
            self._go("now")
            acted = True
        if acted:
            self._damage.all()
            return True
        if self._ui.keys(i):
            self._damage.all()
        return True

    def _now_keys(self, i, edge):
        s = self.stream
        ch = chr(edge).lower() if 32 < edge < 127 else ""
        if i.pressed("a") or edge in (10, 13):
            s.toggle()
        elif ch == "q":
            s.step(-1)
        elif ch == "e":
            s.step(1)
        elif i.pressed("up") or ch == "w":
            s.volume(s.volume() + VOLUME_STEP)
        elif i.pressed("down") or ch == "s":
            s.volume(s.volume() - VOLUME_STEP)
        elif (i.pressed("left") or ch == "a") and s.card["len"]:
            s.seek(s.card["at"] - SEEK_STEP)
        elif (i.pressed("right") or ch == "d") and s.card["len"]:
            s.seek(s.card["at"] + SEEK_STEP)
        elif ch == "m" or i.pressed("y"):
            self._go("list")
        else:
            return False
        return True

    def handle_pointer(self, px, py, click):
        if self._ui.point(px, py, click):
            self._damage.all()
        return True


def _state(c, name, kind):
    """None, or how row `name` of `kind` stands against what plays."""
    if c is None or c["station"] != name and not (kind == "song" and c["title"] == name):
        return None
    if c["kind"] != kind:
        return None
    if c["state"] == OPENING:
        return OPENING
    if c["state"] == FAILED:
        return FAILED
    return PAUSED_ if c["paused"] else PLAYING


def _https():
    m = _media()
    return m is not None and bool(m.tls())


def _check(url, final):
    """The Address field's error: an address that names another scheme,
    and on Add one that is not http:// (or https:// where the media face
    verifies it) at all; None where it plays."""
    low = url.strip().lower()
    if not low:
        return ERR_ADDRESS if final else None
    if low.startswith("http://") and len(low) > 7:
        return None
    if low.startswith("https://") and len(low) > 8 and _https():
        return None
    if "://" in low or final or low.startswith("https"):
        return ERR_ADDRESS
    return None


def _host(url):
    rest = url[url.find("://") + 3:] if "://" in url else url
    k = rest.find("/")
    return (rest[:k] if k > 0 else rest)[:NAME_MAX]


_NAV_OFF = {"nav": False}
_NO_FOCUS = "-"
_TABS = {"nav": False, "mark": False}
_CAPTION = {"face": "caption", "tone": "dim"}
_CENTER = {"align": "center", "face": "ui"}
_URL = {"hint": "http://", "id": "Address"}
_URL_ERR = {"hint": "http://", "id": "Address", "border": "danger"}
_NAME = {"hint": "Optional", "id": "Name"}
_ERR_LINE = {"tone": "danger", "wrap": True, "lines": 2}
_ADD = {"id": "Add"}
_ADD_OFF = {"id": "Add", "disabled": True}
_CANCEL = {"id": "Cancel"}
_HOME = {"id": "Back to home"}
_WIFI = {"id": "Wi-Fi settings"}
_PLACES = (("Radio",), ("My music",))
