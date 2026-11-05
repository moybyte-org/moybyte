"""`moybyte.media`: the media service's face (docs/os_2026-11/hardware.md
section 3) over its C (native/moy_media/moy_media.h, the module moy_media).
One stream at a time, one process's.

  play(src)          play `src`, replacing what this process played: a file
                     (an MP3, or an .m3u/.pls playlist) under `media`, an
                     http:// URL (a stream, its ICY now-playing text read, or
                     a playlist) under `media:url` and `http`. OSError: EPERM
                     without them, EPROTONOSUPPORT for https (as `http`
                     refuses it until P3.4), EBUSY while another process's
                     stream plays, ENOSPC where THE SHARES give no MEDIA row
                     or it is full, ENOENT for a file that is not there
  pause() resume() stop()
  next() prev()      the list's next or previous entry: False at its end
  volume(v=None)     the stream's level, 0..100 (the console's caps it)
  now() -> dict | None  state ("opening", "playing", "ended", "failed",
                     "idle"), paused, src, title (the station's now-playing
                     text, else the entry's own), station, entry, entries,
                     rate, channels, kbps, volume, err, pid, and a file's
                     place and length heard, `at` and `len` in seconds
                     (0 for a stream, and `len` 0 until a frame decodes)

A stream lives as long as its process (owner, 2026-11-03): the process's end
stops it (`sweep`), and the shell (pid 1) may stop it from anywhere. While a
URL may play the stream holds the radio lease under "media"
(`Workstation.wifi_hold`), let go when it stops or its list ends.

The face's pump is a shell wake every PUMP_MS while a stream is open: it
feeds a file's bytes to the C, closes the service once the C has let go after
a stop, and on the host, which has no task, runs the C's loop. A file is read
in the VM, so a store write never runs on the service's PSRAM stack."""

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

EPERM, ENOENT, EBADF, EBUSY, EINVAL, ENOSPC = 1, 2, 9, 16, 22, 28
EPROTONOSUPPORT = 93
TAG = "media"
PUMP_MS = 50
FEED_MAX = 8192
STATES = ("idle", "opening", "playing", "ended", "failed")
_HOST = _N is not None and hasattr(_N, "PART")
(_STATE, _PAUSED, _OWNER, _GEN, _ENTRY, _ENTRIES, _KIND, _ARMED, _RATE, _CH, _KBPS,
 _VOL, _INB, _INR, _PCM, _DEC, _PLAYED, _STARVED, _ERR, _TGEN, _IDLE, _AT_MS,
 _LEN_MS) = range(23)

# The stream's process and what the pump holds for it.
_s = {"pid": 0, "wake": None, "radio": False, "file": None, "gen": 0, "eof": False,
      "stopping": False}
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


def _is_url(src):
    return src[:7].lower() == "http://" or src[:8].lower() == "https://"


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


def _hold():
    ws = _D.console
    if ws is None or _s["radio"]:
        return
    _s["radio"] = True
    ws.wifi_hold(TAG)
    w = getattr(ws, "wifi", None)
    net = getattr(ws, "cart_net", None)
    try:
        up = w is not None and w.status()[0]
    except Exception:  # noqa: BLE001 -- a station that cannot say is down
        up = False
    if not up and net is not None and hasattr(net, "online"):
        try:
            net.online()
        except Exception:  # noqa: BLE001 -- the stream reports what failed
            pass


def _let_go():
    if _s["radio"]:
        _s["radio"] = False
        ws = _D.console
        if ws is not None:
            ws.wifi_release(TAG)


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


def _start_pump():
    if _s["wake"] is None:
        _s["wake"] = _as_shell(lambda: _moybyte.WAKES.every(PUMP_MS, _pump))


def _end():
    """The service closed: the pump, the radio and the file let go."""
    w = _s["wake"]
    _s["wake"] = None
    if w is not None:
        _moybyte.WAKES.cancel(w)
    if _HOST:
        a = _audio()
        if a is not None:
            _N.attach(a, False)
    _close_file()
    _let_go()
    _s["pid"] = 0
    _s["stopping"] = False
    _D.MEDIA[0] = 0


def _try_close():
    if _HOST:
        _N.run()
    rc = _N.close(_s["pid"])
    if rc == 0 or rc == -EBADF:
        _end()
        return True
    return False


def _feed(gen):
    p = _s["pid"]
    if _s["gen"] != gen:
        _close_file()
        _s["gen"] = gen
        _s["eof"] = False
        src = _N.text(_N.T_SRC)
        try:
            f = open(src, "rb")
            _s["file"] = f
            size = f.seek(0, 2)
            f.seek(0)
            _N.feed_size(p, gen, size)
        except OSError:
            _close_file()
            _s["gen"] = gen
            _s["eof"] = True
            _N.feed_end(p, gen)
            return
    if _s["eof"] or _s["file"] is None:
        return
    if _buf[0] is None:
        _buf[0] = bytearray(FEED_MAX)
    buf = _buf[0]
    mv = memoryview(buf)
    f = _s["file"]
    for _ in range(4):
        n = f.readinto(buf)
        if not n:
            _s["eof"] = True
            _N.feed_end(p, gen)
            _s["file"] = None
            f.close()
            return
        k = _N.feed(p, gen, mv[:n])
        if k < n:
            if k >= 0:
                f.seek(k - n, 1)
            return


def _pump():
    if _s["pid"] == 0:
        _end()
        return
    if _s["stopping"]:
        _try_close()
        return
    if _HOST:
        _N.run()
    st = _N.status()
    if st is None:
        _end()
        return
    if st[_KIND] == _N.FEED and st[_ARMED]:
        _feed(st[_GEN])
        if _HOST:
            _N.run()
    if st[_STATE] in (_N.ENDED, _N.FAILED):
        _let_go()
        _close_file()


def _owned():
    """The stream's owner, when the caller is it or the shell; else EPERM.
    None when no stream is open."""
    p = _D.pid()
    if not _s["pid"] or _s["stopping"]:
        return None
    if p != _s["pid"] and p != _D.SHELL:
        raise OSError(EPERM, "another process's stream")
    return _s["pid"]


def play(src):
    src = str(src)
    url = _is_url(src)
    p = _need(("media:url", "http") if url else ("media",))
    if url and src[:8].lower() == "https://":
        raise OSError(EPROTONOSUPPORT, "https is refused")
    if _s["stopping"]:
        _try_close()
    if _s["pid"] and _s["pid"] != p:
        raise OSError(EBUSY, "another process's stream plays")
    if not _s["pid"]:
        _D.check(_N.open(p, _rate()))
        _s["pid"] = p
        _D.MEDIA[0] = p
        if _HOST:
            a = _audio()
            if a is not None:
                _N.attach(a, True)
        _start_pump()
    _close_file()
    _N.clear(p)
    if url:
        _D.check(_N.add(p, src, None))
    elif _N.list_kind(None, src, None):
        try:
            with open(src, "rb") as f:
                text = f.read()
        except OSError as e:
            stop()
            raise e
        if _N.playlist(p, text, src) <= 0:
            stop()
            raise OSError(EINVAL, "an empty playlist")
    else:
        try:
            open(src, "rb").close()
        except OSError:
            stop()
            raise OSError(ENOENT, src)
        _D.check(_N.add(p, src, None))
    if any(_is_url(_N.entry(i)[0]) for i in range(_N.status()[_ENTRIES])):
        _hold()
    _D.check(_N.go(p, 0))
    if _HOST:
        _N.run()


def stop():
    p = _owned()
    if p is None:
        return
    _N.stop(p)
    _s["stopping"] = True
    _close_file()
    _let_go()
    _try_close()


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
    return True


def next():  # noqa: A001 -- the verb's name
    return _move(1)


def prev():
    return _move(-1)


def volume(v=None):
    p = _owned()
    if p is None:
        return None
    if v is not None:
        _N.volume(p, int(v))
    return _N.status()[_VOL]


def now():
    st = _N.status()
    if st is None or not _s["pid"] or _s["stopping"]:
        return None
    title = _N.text(_N.T_TITLE) or _N.text(_N.T_ENTRY)
    fed = st[_LEN_MS] > 0             # a file's: only a fed entry has a size
    return {
        "state": STATES[st[_STATE]], "paused": bool(st[_PAUSED]),
        "src": _N.text(_N.T_SRC), "title": title, "station": _N.text(_N.T_STATION),
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
