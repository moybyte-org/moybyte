"""`moybyte.media`: the media service's face (docs/os_2026-11/hardware.md
section 3) over its C (native/moy_media/moy_media.h, the module moy_media).
The service is the system's: one stream, which every app with the
permission is a client of.

  play(src, allow=None, label=None)
                     play `src` in place of what plays: a file (an MP3, or
                     an .m3u/.pls playlist) under `media`, an http:// URL (a
                     stream, its ICY now-playing text read, or a playlist)
                     under `media:url` and `http`. OSError: EPERM without
                     them, EPROTONOSUPPORT for https where `tls()` is False
                     (an https stream verifies the server's certificate as
                     `http` does), ENOSPC where THE SHARES give no MEDIA row
                     or it is full, ENOENT for a file that is not there.
                     With `allow` (a function of a file's path, as `where`
                     makes it), a file it refuses, the source or an entry
                     its list names, is EPERM before anything plays.
                     `label` is the caller's own name for what it plays
                     (Music's "kind<TAB>name"), read back as now()'s
  where(path) -> str the path the C plays `path` as (a URL as it is)
  tls() -> bool      whether an https:// URL plays here
  held(folder) -> ((name, size), ...)  the songs in `folder` that this
                     console plays but does not keep (a board-served page:
                     the board's music folder); () elsewhere
  probe(path) -> (seconds, title, artist) | None  a song's length (0
                     unknown) and its tag's title and artist, read by the C
                     without playing it
  pause() resume() stop()
  next() prev()      the list's next or previous entry: False at its end
  seek(seconds)      the file playing from that place: False for a stream,
                     or before its length is known
  volume(v=None)     the stream's level, 0..100 (the console's caps it)
  now() -> dict | None  state ("opening", "playing", "ended", "failed",
                     "idle"), paused, src, title (the station's now-playing
                     text, else the entry's own, else its file's tag's),
                     artist (its file's tag's), station, entry, entries,
                     rate, channels, kbps, volume, err, pid (the service's
                     run), client (the process whose play() set what plays),
                     who (its name), label (its label), and a file's place
                     and length heard, `at` and `len` in seconds (0 for a
                     stream, and `len` 0 until a frame decodes)
  stamp() -> int | None  one small int that moves when what now() shows
                     does (its place by the second): a poll that finds it
                     still reads nothing else and allocates nothing
  last() -> dict | None  how the last run ended: state ("idle" stopped,
                     "ended", "failed"), err, pid, client
  running() -> int   the run's pid, 0 for none

THE RUN is the service's, on demand (owner, 2026-11-09): a client's first
play() starts it (moy_media_open: a pid of its own, "media", its lease, its
MEDIA row, its task), and it ends by itself when its work is done -- a
stop, its list over, nothing of it played -- giving everything back. While
it runs it is nobody's process: a client's end, Music closing, an S3's VM
stop and a soft reset leave it playing. Its row in the task manager is the
scheduler's kernel-service row (`scheduler.adopt`, `_RT`), and this face
follows the run whatever started it (`_live`): a console that comes back
after a VM stop finds it playing and shows it.

THE CLIENTS. Every process the permissions let in is a client, and the last
command wins: a play() replaces what plays whoever played it (the service
names its new client, `moy_media_take`), and the other commands act on what
plays from any client. A play() that takes over a list another process's
play() started, still playing, posts a notification saying so
(`moy_media_tell`), so the person learns why their music changed. The shell
(pid 1) is a client too. A compiled program is a client by its `media`
handle, served in C on its own task (native/moy_kernel/moy_dev.h's media
row), the VM up or stopped; the console's frame finds a run one started
(`frame`) and follows it as it follows its own.

THE RADIO. While a URL of the list may play, the run holds the radio's
"media" tag under its own pid (`Workstation.wifi_hold`); its end lets it go
in C (moy_media.c's reap), and this face settles the radio after it. The
hold is taken after the C's play() begun the list, so a URL waits on the
service's task for the link (moy_net.h's THE NETWORK'S GATE), MOY_NET_DIAL_MS
at most, and then fails ENETUNREACH: "no network".

WHAT RUNS IN C. play() is the C's (moy_media_play: the list, a playlist file
read, the first entry begun), and a file plays with no Python at all: the
console's frame runs the service's job, which reads the file through the
store's volume seam and, where there is no task (the host, the browser),
decodes, and ends the run when its work is done.

The face's wake (`_pump`, every PUMP_MS) runs only while there is Python
work: the page's bytes to feed (the browser), or, while the run holds the
radio, its end to notice (WATCH_MS) so the radio settles. A file's run ends
with no Python, and the face's next call lets go of it.

IN THE BROWSER the C has no HTTP client and the VFS keeps no song, so the
bytes of a URL, and of a song the page holds (its OPFS store's, or a serving
board's), are the page's (`ws.media_net`, firmware/web_runner/media_link.py):
a reader the worker fills between frames, from the byte the C asks for (a
seek's), which answers None until bytes come and raises OSError with EACCES
for a station that does not let a page read it. A station's reader carries
its ICY head (`icy`: the metadata interval and the station's name) where the
page could read it, which the face hands the C before the first bytes."""

try:
    import moy_media as _N
except ImportError:  # host, or a console with no media service
    _N = None
if _N is None:
    try:
        from runtime import moy_media as _N
    except ImportError:  # a console with no media service (the Guition S3)
        _N = None

try:
    import devices as _D
except ImportError:  # host: the runtime package
    from runtime import devices as _D

try:
    import moybyte as _moybyte
except ImportError:  # host: the runtime package
    from runtime import moybyte as _moybyte

try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    try:
        from runtime import moy_log as _L
    except ImportError:
        _L = None

EPERM, ENOENT, EIO, EBADF, EINVAL, ENOSPC = 1, 2, 5, 9, 22, 28
EPROTONOSUPPORT = 93
TAG = "media"
NAME = "media"                  # the run's row in the task manager
PUMP_MS = 50
WATCH_MS = 500
FEED_MAX = 8192
STATES = ("idle", "opening", "playing", "ended", "failed")
_HOST = _N is not None and hasattr(_N, "PART")
_STEP = _N is not None and getattr(_N, "TASK", 1) == 0     # no task: the C's job runs the loop
(_STATE, _PAUSED, _PID, _GEN, _ENTRY, _ENTRIES, _KIND, _ARMED, _RATE, _CH, _KBPS,
 _VOL, _INB, _INR, _PCM, _DEC, _PLAYED, _STARVED, _ERR, _TGEN, _IDLE, _AT_MS,
 _LEN_MS, _FEED_AT, _CLIENT) = range(25)

# The run the face follows and what the face's wake holds for it.
_s = {"pid": 0, "wake": None, "every": 0, "file": None, "gen": 0, "eof": False,
      "sized": False, "fed": False, "radio": 0}
_buf = [None]
_HEARD = [None]                 # the console's fn(), told when the face finds a run


def _shares_media():
    """THE SHARES' MEDIA row on this image's line: None where it is absent,
    True where there is no line (nothing is counted)."""
    try:
        import moy_play as _P
    except ImportError:  # host: the runtime package
        try:
            from runtime import moy_play as _P
        except ImportError:
            return True
    fn = getattr(_P, "shares", None)
    rows = fn() if fn is not None else None
    if rows is None:
        return True
    return rows[1]


def present():
    """Whether this console plays a stream: the service is in the image, it
    has an output, and THE SHARES give it a MEDIA row."""
    return _N is not None and _N.present() and _shares_media() is not None


def _need(perms):
    p = _D.pid()
    for perm in perms:
        if not _D.may(p, perm):
            raise OSError(EPERM, 'needs "%s"' % perm)
    return p


def _net():
    """The page's bytes (`ws.media_net`), where the console is a browser's."""
    return getattr(_D.console, "media_net", None)


def held(folder):
    net = _net()
    fn = getattr(net, "held", None)
    return fn(folder) if fn is not None else ()


def probe(path):
    return _N.probe(_abs(str(path)))


def tls():
    """Whether an https stream plays here: its certificate verifies (the
    page's fetch verifies its own)."""
    if _net() is not None:
        return True
    try:
        import moy_http
    except ImportError:  # host: the runtime package
        from runtime import moy_http
    return bool(moy_http.tls())


def _is_url(src):
    return src[:7].lower() == "http://" or src[:8].lower() == "https://"


def where(path):
    """The path the C plays `path` as (a URL as it is)."""
    return _abs(str(path))


def _abs(path):
    """A path the C resolves: a relative one is the VM's working folder's."""
    if path[:1] == "/" or _is_url(path):
        return path
    import os
    here = os.getcwd()
    return (here if here.endswith("/") else here + "/") + path


def _audio():
    if not _HOST:
        return None
    try:
        from runtime import audio_binding
    except ImportError:  # pragma: no cover - the host without its audio
        return None
    return audio_binding.get()


def _rate():
    a = _audio()
    return a.rate() if a is not None else 22050


def _sched():
    try:
        import scheduler
    except ImportError:  # host: the runtime package
        from runtime import scheduler
    return scheduler.SCHED


class _Run:
    """The run's row's runtime (scheduler.adopt): its figures while it
    lives, its end once the C has given everything back, and a stop."""

    def figures(self, pid):
        if _N.pid() != pid:
            return None
        return _N.share(), 0

    def reap(self):
        """[(pid, exit status)] of the runs that ended: 0, or 1 for a list
        of which nothing played."""
        runs, pid, _client, state, _err = _N.last()
        if not pid or pid == _N.pid():
            return []
        return [(pid, 1 if state == _N.FAILED else 0)]

    def stop(self, pid):
        if _N.pid() == pid:
            _N.stop()
            _settle()
        return 0


_RT = _Run()


def _holds(ws, pid):
    """Whether run `pid` holds the radio: the lease table's radio rows say
    (a console with no radio has none)."""
    leases = getattr(ws, "leases", None)
    return _s["radio"] == pid and leases is not None and leases.held(TAG)


def _hold():
    ws, pid = _D.console, _s["pid"]
    if ws is None or not pid or _holds(ws, pid):
        return
    ws.wifi_hold(TAG, pid)
    _s["radio"] = pid
    dial = getattr(getattr(ws, "cart_net", None), "dial", None)
    if dial is not None:
        dial()                     # never waited: the worker's task waits for the link


def _close_file():
    f = _s["file"]
    _s["file"] = None
    _s["gen"] = 0
    if f is not None:
        try:
            f.close()
        except OSError:
            pass


def _as_shell(fn):
    if _L is None:
        return fn()
    was = _L.current()
    _L.current(_D.SHELL)
    try:
        return fn()
    finally:
        _L.current(was)


def _wake(ms):
    """The face's wake every `ms`, 0 for none."""
    if _s["every"] == ms:
        return
    w = _s["wake"]
    _s["wake"] = None
    _s["every"] = 0
    if w is not None:
        _moybyte.WAKES.cancel(w)
    if ms:
        _s["wake"] = _as_shell(lambda: _moybyte.WAKES.every(ms, _pump))
        _s["every"] = ms


def _watch():
    """The face's wake while a run needs Python: the page's bytes to feed
    every PUMP_MS, else, while it holds the radio, its end to notice every
    WATCH_MS so the radio settles. A file plays with none: its run's end is
    the face's next call's to notice."""
    if not _s["pid"]:
        _wake(0)
    elif _net() is not None:
        _wake(PUMP_MS)
    elif _s["radio"]:
        _wake(WATCH_MS)
    else:
        _wake(0)


def _follow(pid):
    """Run `pid`, whoever started it, followed: its row, the host's mix, the
    page's reader, its radio as the lease table holds it, the face's wake."""
    _s["pid"] = pid
    _D.MEDIA[0] = pid
    _sched().adopt(pid, NAME, rt=_RT)
    if _HOST:
        a = _audio()
        if a is not None:
            _N.attach(a, True)
    if _net() is not None:
        _N.page(True)
    leases = getattr(_D.console, "leases", None)
    _s["radio"] = pid if leases is not None and leases.held(TAG) else 0
    _watch()


def _end():
    """The run ended (in C, which gave back what it held): the face lets go
    -- its wake, the host's mix, the page's reader -- and the radio settles."""
    _s["pid"] = 0
    _s["radio"] = 0
    _wake(0)
    if _HOST:
        a = _audio()
        if a is not None:
            _N.attach(a, False)
    _close_file()
    _D.MEDIA[0] = 0
    ws = _D.console
    settle = getattr(ws, "wifi_settle", None)
    if settle is not None:
        settle()


def _live():
    """The run's pid, 0 for none: the face follows the C's run, and lets go
    of one that ended."""
    if _N is None:
        return 0
    pid = _N.pid()
    if _s["pid"] and _s["pid"] != pid:
        _end()
    if pid and not _s["pid"]:
        _follow(pid)
    return pid


def _settle():
    """A stop's end: where there is no task the C's job runs now, so a stop
    that let go ends the run at once."""
    if _STEP:
        _N.pump()
    _live()


def listen(fn):
    """`fn()` told each time the face finds a run: a client's play() that
    started one, or the run a VM stop left playing (`attach`). The
    console's, one at a time: Music's card and status item follow it
    (music_app.follow), and a console that boots takes the place of the one
    before it."""
    _HEARD[0] = fn


def _tell():
    fn = _HEARD[0]
    if fn is not None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- a listener never stops the music
            print("Moybyte media: %s" % (exc,))


def attach():
    """The run a VM stop left playing, followed by the console after it."""
    pid = _live()
    if pid:
        _tell()
    return pid


def frame():
    """At the console's frame start: a run the face does not follow yet --
    one a compiled program's media handle started on its own task
    (native/moy_media/moy_media_dev.c) -- followed and told (`attach`), and
    one that ended let go. One read while nothing changed."""
    if _N is not None and _N.pid() != _s["pid"]:
        if _live():
            _tell()


def running():
    """The run's pid while one plays, else 0."""
    return _live()


def _ended(gen, err=0):
    _s["eof"] = True
    f = _s["file"]
    _s["file"] = None
    if err:
        _N.feed_fail(gen, err)
    else:
        _N.feed_end(gen)
    if f is not None:
        f.close()


def _feed(gen, room, at):
    """The page's bytes for entry generation `gen`, from byte `at`."""
    if _s["gen"] != gen:
        _close_file()
        _s["gen"] = gen
        _s["eof"] = False
        _s["sized"] = False
        _s["fed"] = False
        net = _net()
        src = _N.text(_N.T_SRC)
        if net is None or not (_is_url(src) or net.holds(src)):
            _s["eof"] = True
            _N.feed_fail(gen, ENOENT)
            return
        _s["file"] = net.open(src, at) if at else net.open(src)
    if _s["eof"] or _s["file"] is None:
        return
    if _buf[0] is None:
        _buf[0] = bytearray(FEED_MAX)
    mv = memoryview(_buf[0])
    f = _s["file"]
    for _ in range(4):
        want = min(FEED_MAX, room)
        if want <= 0:
            return
        try:
            n = f.readinto(mv[:want])
        except OSError as e:
            _ended(gen, (e.args[0] if e.args and isinstance(e.args[0], int) else 0) or EIO)
            return
        if not _s["sized"] and getattr(f, "size", None) is not None:
            _s["sized"] = True
            _N.feed_size(gen, f.size)
        if n is None:                  # the page's reader: nothing yet
            return
        if not n:
            _ended(gen)
            return
        if not _s["fed"]:
            # a station's ICY head, where the page could read it, before
            # the bytes it takes apart
            _s["fed"] = True
            icy = getattr(f, "icy", None)
            if icy is not None:
                _N.feed_icy(gen, icy[0], icy[1] or "")
        k = _N.feed(gen, mv[:n])
        room -= max(k, 0)
        if k < n:
            if k >= 0 and hasattr(f, "seek"):
                f.seek(k - n, 1)
            return


def _pump():
    if not _live():
        return
    st = _N.status()
    if st is None:
        return
    if st[_ARMED]:
        _feed(st[_GEN], st[_INR], st[_FEED_AT])
        if _STEP:
            _N.pump()
    if st[_STATE] in (_N.ENDED, _N.FAILED):
        _close_file()


def _client():
    """The caller, a client of a running service: a process with `media` or
    the shell. None while nothing runs."""
    p = _D.pid()
    if not _live():
        return None
    if p != _D.SHELL and not _D.may(p, "media"):
        raise OSError(EPERM, 'needs "media"')
    return p


def _who(p):
    name = _L.name(p) if _L is not None else None
    return name or ("pid %d" % p)


def _took(was):
    """take() answered `was`, whose list another client's play() replaced:
    the person is told, as the system (moy_media_tell's notification, its
    text the C's for every client), the console's list taking it now."""
    if was and _N.tell(was):
        try:
            from notifications import NOTES
        except ImportError:  # host: the runtime package
            from runtime.notifications import NOTES
        NOTES.drain()


def play(src, allow=None, label=None):
    src = str(src)
    url = _is_url(src)
    p = _need(("media:url", "http") if url else ("media",))
    if allow is not None and not url and not allow(_abs(src)):
        raise OSError(EPERM, "not one of its folders")
    if url and src[:8].lower() == "https://" and not tls():
        raise OSError(EPROTONOSUPPORT, "https is refused")
    net = _net()
    new = not _live()
    if new:
        _D.check(_N.open(_rate()))
        _live()
    _close_file()
    src = _abs(src)
    n = _N.play(src, net is not None and not url and net.holds(src))
    if n < 0:
        _N.stop()
        _settle()
        raise OSError(-n, "an empty playlist" if n == -EINVAL else src)
    if allow is not None:
        for i in range(n):
            e = _N.entry(i)[0]
            if not _is_url(e) and not allow(e):
                _N.stop()
                _settle()
                raise OSError(EPERM, "its list names a file outside its folders")
    was = _N.take(p, _who(p), label)
    if any(_is_url(_N.entry(i)[0]) for i in range(n)):
        _hold()
    if _STEP:
        _N.run()
    _took(was)
    _watch()
    if new:
        _tell()


def stop():
    if _client() is None:
        return
    _N.stop()
    _close_file()
    _settle()


def pause():
    if _client() is not None:
        _N.pause(True)


def resume():
    if _client() is not None:
        _N.pause(False)


def _move(by):
    if _client() is None:
        return False
    st = _N.status()
    i = st[_ENTRY] + by
    if i < 0 or i >= st[_ENTRIES]:
        return False
    _D.check(_N.go(i))
    if _is_url(_N.entry(i)[0]):
        _hold()
        _watch()
    return True


def next():  # noqa: A001 -- the verb's name
    return _move(1)


def prev():
    return _move(-1)


def seek(seconds):
    if _client() is None:
        return False
    if _N.seek(int(float(seconds) * 1000)) != 0:
        return False
    if _STEP:
        _N.run()
    return True


def volume(v=None):
    if _client() is None:
        return None
    if v is not None:
        _N.volume(int(v))
    return _N.status()[_VOL]


def stamp():
    if not _live():
        return None
    return _N.stamp()


def now():
    if not _live():
        return None
    st = _N.status()
    if st is None:
        return None
    title = _N.text(_N.T_TITLE) or _N.text(_N.T_ENTRY) or _N.text(_N.T_SONG)
    fed = st[_LEN_MS] > 0             # a file's: only a file has a size
    return {
        "state": STATES[st[_STATE]], "paused": bool(st[_PAUSED]),
        "src": _N.text(_N.T_SRC), "title": title, "artist": _N.text(_N.T_ARTIST),
        "station": _N.text(_N.T_STATION),
        "entry": st[_ENTRY], "entries": st[_ENTRIES], "rate": st[_RATE],
        "channels": st[_CH], "kbps": st[_KBPS], "volume": st[_VOL],
        "err": st[_ERR], "pid": st[_PID], "client": st[_CLIENT],
        "who": _N.text(_N.T_WHO), "label": _N.text(_N.T_LABEL),
        "at": st[_AT_MS] // 1000 if fed else 0, "len": st[_LEN_MS] // 1000 if fed else 0,
    }


def last():
    """How the last run ended, None before any: state ("idle" for a stop,
    "ended", "failed"), err (the last entry's errno), pid and client."""
    if _N is None:
        return None
    runs, pid, client, state, err = _N.last()
    if not runs:
        return None
    return {"state": STATES[state], "err": err, "pid": pid, "client": client}


def sweep(pid):
    """Process `pid` ended: the run is the system's, and plays on."""
