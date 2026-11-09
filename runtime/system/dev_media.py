"""`moybyte.media`: the media service's face (docs/os_2026-11/hardware.md
section 3) over its C (native/moy_media/moy_media.h, the module moy_media).
One stream at a time, one process's.

  play(src, allow=None)
                     play `src`, replacing what this process played: a file
                     (an MP3, or an .m3u/.pls playlist) under `media`, an
                     http:// URL (a stream, its ICY now-playing text read, or
                     a playlist) under `media:url` and `http`. OSError: EPERM
                     without them, EPROTONOSUPPORT for https where
                     `tls()` is False (an https stream verifies the
                     server's certificate as `http` does), EBUSY while
                     another process's stream plays, ENOSPC where THE
                     SHARES give no MEDIA row or it is full, ENOENT for a
                     file that is not there. With `allow` (a function of
                     a file's path, as `where` makes it), a file it refuses,
                     the source or an entry its list names, is EPERM before
                     anything plays, whether the file is there or not
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
                     rate, channels, kbps, volume, err, pid, and a file's
                     place and length heard, `at` and `len` in seconds
                     (0 for a stream, and `len` 0 until a frame decodes)
  stamp() -> int | None  one small int that moves when what now() shows
                     does (its place by the second): a poll that finds it
                     still reads nothing else and allocates nothing

A stream lives as long as its process (owner, 2026-11-03): the process's end
stops it (`sweep`), and the shell (pid 1) may stop it from anywhere. While a
URL may play the stream holds the radio lease under "media" and its
process's pid (`Workstation.wifi_hold`), let go when it stops, its list
ends or its process does.

WHAT RUNS IN C. play() is the C's (moy_media_play: the list, a playlist file
read, the first entry begun), and a file plays with no Python at all: the
console's frame runs the service's job, which reads the file through the
store's volume seam and, where there is no task (the host, the browser),
decodes. This face checks the permissions, holds the radio lease, and keeps
the stream's process.

The face's wake (`_pump`, every PUMP_MS) runs only while there is Python
work: the page's bytes to feed (the browser), the radio lease to let go
when a URL's list ends (WATCH_MS), or a stopped service the C has not yet
let go of. A stream the C closed from outside (its lease ended) is noticed
at the face's next call.

IN THE BROWSER the C has no HTTP client and the VFS keeps no song, so the
bytes of a URL, and of a song the page holds (its OPFS store's, or a serving
board's), are the page's (`ws.media_net`, firmware/web_runner/media_link.py):
a reader the worker fills between frames, from the byte the C asks for (a
seek's), which answers None until bytes come and raises OSError with EACCES
for a station that does not let a page read it."""

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

EPERM, ENOENT, EIO, EBADF, EBUSY, EINVAL, ENOSPC = 1, 2, 5, 9, 16, 22, 28
EPROTONOSUPPORT = 93
TAG = "media"
PUMP_MS = 50
WATCH_MS = 500
FEED_MAX = 8192
STATES = ("idle", "opening", "playing", "ended", "failed")
_HOST = _N is not None and hasattr(_N, "PART")
_STEP = _N is not None and getattr(_N, "TASK", 1) == 0     # no task: the C's job runs the loop
(_STATE, _PAUSED, _OWNER, _GEN, _ENTRY, _ENTRIES, _KIND, _ARMED, _RATE, _CH, _KBPS,
 _VOL, _INB, _INR, _PCM, _DEC, _PLAYED, _STARVED, _ERR, _TGEN, _IDLE, _AT_MS,
 _LEN_MS, _FEED_AT) = range(24)

# The stream's process and what the face's wake holds for it.
_s = {"pid": 0, "wake": None, "every": 0, "file": None, "gen": 0, "eof": False,
      "stopping": False, "sized": False, "radio": 0}
_buf = [None]


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


def _holds(ws, pid):
    """Whether process `pid`'s stream holds the radio: the lease table's
    radio rows say (a console with no radio has none)."""
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


def _let_go():
    ws, pid = _D.console, _s["radio"]
    _s["radio"] = 0
    if ws is not None and pid:
        ws.wifi_release(TAG, pid)


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
    """The wake this stream needs: the page's bytes to feed, a stop the C
    has not let go of, the radio to let go of at its list's end, or none."""
    if _s["stopping"] or (_s["pid"] and _net() is not None):
        _wake(PUMP_MS)
    elif _s["pid"] and _holds(_D.console, _s["pid"]):
        _wake(WATCH_MS)
    else:
        _wake(0)


def _end():
    """The service closed: the wake, the radio and the page's reader let go."""
    _s["pid"] = 0
    _s["stopping"] = False
    _wake(0)
    if _HOST:
        a = _audio()
        if a is not None:
            _N.attach(a, False)
    _close_file()
    _let_go()
    _D.MEDIA[0] = 0


def _gone():
    """The C closed the stream from outside (its lease ended): the face lets
    go too. True when it did."""
    if _s["pid"] and not _s["stopping"] and _N.owner() != _s["pid"]:
        _end()
        return True
    return False


def _try_close():
    if _STEP:
        _N.run()
    rc = _N.close(_s["pid"])
    if rc == 0 or rc == -EBADF:
        _end()
        return True
    return False


def _ended(p, gen, err=0):
    _s["eof"] = True
    f = _s["file"]
    _s["file"] = None
    if err:
        _N.feed_fail(p, gen, err)
    else:
        _N.feed_end(p, gen)
    if f is not None:
        f.close()


def _feed(gen, room, at):
    """The page's bytes for entry generation `gen`, from byte `at`."""
    p = _s["pid"]
    if _s["gen"] != gen:
        _close_file()
        _s["gen"] = gen
        _s["eof"] = False
        _s["sized"] = False
        net = _net()
        src = _N.text(_N.T_SRC)
        if net is None or not (_is_url(src) or net.holds(src)):
            _s["eof"] = True
            _N.feed_fail(p, gen, ENOENT)
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
            _ended(p, gen, (e.args[0] if e.args and isinstance(e.args[0], int) else 0) or EIO)
            return
        if not _s["sized"] and getattr(f, "size", None) is not None:
            _s["sized"] = True
            _N.feed_size(p, gen, f.size)
        if n is None:                  # the page's reader: nothing yet
            return
        if not n:
            _ended(p, gen)
            return
        k = _N.feed(p, gen, mv[:n])
        room -= max(k, 0)
        if k < n:
            if k >= 0 and hasattr(f, "seek"):
                f.seek(k - n, 1)
            return


def _pump():
    if _s["pid"] == 0:
        _end()
        return
    if _s["stopping"]:
        _try_close()
        return
    if _gone():
        return
    st = _N.status()
    if st is None:
        _end()
        return
    if st[_ARMED]:
        _feed(st[_GEN], st[_INR], st[_FEED_AT])
        if _STEP:
            _N.pump()
    if st[_STATE] in (_N.ENDED, _N.FAILED):
        _let_go()
        _close_file()
    _watch()


def _owned():
    """The stream's owner, when the caller is it or the shell; else EPERM.
    None when no stream is open."""
    p = _D.pid()
    if not _s["pid"] or _s["stopping"] or _gone():
        return None
    if p != _s["pid"] and p != _D.SHELL:
        raise OSError(EPERM, "another process's stream")
    return _s["pid"]


def play(src, allow=None):
    src = str(src)
    url = _is_url(src)
    p = _need(("media:url", "http") if url else ("media",))
    if allow is not None and not url and not allow(_abs(src)):
        raise OSError(EPERM, "not one of its folders")
    if url and src[:8].lower() == "https://" and not tls():
        raise OSError(EPROTONOSUPPORT, "https is refused")
    if _s["stopping"]:
        _try_close()
    _gone()
    if _s["pid"] and _s["pid"] != p:
        raise OSError(EBUSY, "another process's stream plays")
    net = _net()
    if not _s["pid"]:
        _D.check(_N.open(p, _rate()))
        _s["pid"] = p
        _D.MEDIA[0] = p
        if _HOST:
            a = _audio()
            if a is not None:
                _N.attach(a, True)
        if net is not None:
            _N.page(p, True)
    _close_file()
    src = _abs(src)
    n = _N.play(p, src, net is not None and not url and net.holds(src))
    if n < 0:
        stop()
        raise OSError(-n, "an empty playlist" if n == -EINVAL else src)
    if allow is not None:
        for i in range(n):
            e = _N.entry(i)[0]
            if not _is_url(e) and not allow(e):
                stop()
                raise OSError(EPERM, "its list names a file outside its folders")
    if any(_is_url(_N.entry(i)[0]) for i in range(n)):
        _hold()
    if _STEP:
        _N.run()
    _watch()


def stop():
    p = _owned()
    if p is None:
        return
    _N.stop(p)
    _s["stopping"] = True
    _close_file()
    _let_go()
    _try_close()
    _watch()


def pause():
    p = _owned()
    if p is not None:
        _N.pause(p, True)


def resume():
    p = _owned()
    if p is not None:
        _N.pause(p, False)


def _move(by):
    p = _owned()
    if p is None:
        return False
    st = _N.status()
    i = st[_ENTRY] + by
    if i < 0 or i >= st[_ENTRIES]:
        return False
    _D.check(_N.go(p, i))
    if _is_url(_N.entry(i)[0]):
        _hold()
        _watch()
    return True


def next():  # noqa: A001 -- the verb's name
    return _move(1)


def prev():
    return _move(-1)


def seek(seconds):
    p = _owned()
    if p is None:
        return False
    if _N.seek(p, int(float(seconds) * 1000)) != 0:
        return False
    if _STEP:
        _N.run()
    return True


def volume(v=None):
    p = _owned()
    if p is None:
        return None
    if v is not None:
        _N.volume(p, int(v))
    return _N.status()[_VOL]


def stamp():
    if not _s["pid"] or _s["stopping"] or _gone():
        return None
    return _N.stamp()


def now():
    if not _s["pid"] or _s["stopping"] or _gone():
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
        "err": st[_ERR], "pid": _s["pid"],
        "at": st[_AT_MS] // 1000 if fed else 0, "len": st[_LEN_MS] // 1000 if fed else 0,
    }


def sweep(pid):
    """Process `pid` ended: its stream stopped, the service closed once the C
    has let go."""
    if _s["pid"] and _s["pid"] == pid:
        _N.stop(pid)
        _s["stopping"] = True
        _close_file()
        _let_go()
        _try_close()
        _watch()
