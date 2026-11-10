"""The media service (native/moy_media/moy_media.h, its face
runtime/system/dev_media.py; docs/os_2026-11/hardware.md section 3) on the
host's build of the C: an ICY stream's now-playing text from a local server,
a playlist's next entry, a file decoded into the mix and read back (the host
loopback) on the frame's C job alone, its tag, its probe and its seek, the
duck under a sounding session, the MEDIA row of THE SHARES taken, given back
and absent, the page's bytes in the browser's shape, and the permissions, the
one owner and the end of its process."""

import errno
import http.server
import math
import os
import random
import shutil
import threading
import time
from array import array
from pathlib import Path

import pytest

from runtime import audio_binding, dev_media, devices, moybyte
from runtime import moy_log as L
from runtime import moy_input as mi
from runtime import moy_evq, moy_lease, moy_loop
from runtime import moy_media as N
from runtime import moy_play as mp

TONE = Path(__file__).resolve().parent / "fixtures" / "media" / "tone_1k.mp3"
MP3 = TONE.read_bytes()
METAINT = 4096
TITLES = ("First Song", "Caf\xe9 Song")       # the second sent as Latin-1
AUDIO = audio_binding.get()

pytestmark = pytest.mark.skipif(AUDIO is None, reason="no C compiler for the host's audio")


def _icy_body():
    """The tone with an ICY metadata block every METAINT bytes: the first
    title in the first block, the second from the fourth on, empty ones
    between."""
    out, k = bytearray(), 0
    for i in range(0, len(MP3), METAINT):
        out += MP3[i:i + METAINT]
        if len(MP3[i:i + METAINT]) < METAINT:
            break
        if k == 0 or k == 3:
            title = TITLES[0] if k == 0 else TITLES[1]
            meta = ("StreamTitle='%s';StreamUrl='';" % title).encode("latin-1")
            meta += b"\0" * (-len(meta) % 16)
            out += bytes([len(meta) // 16]) + meta
        else:
            out += b"\0"
        k += 1
    return bytes(out)


class _H(http.server.BaseHTTPRequestHandler):
    seen = []

    def _send(self, body, ctype, extra=()):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 -- the stdlib's name
        _H.seen.append((self.path, self.headers.get("Icy-MetaData")))
        port = self.server.server_address[1]
        if self.path == "/radio":
            # Two halves a moment apart, as a station's titles come: the second
            # title is in the second.
            body = _icy_body()
            cut = 3 * (METAINT + 1) + 16 * 3
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("icy-metaint", str(METAINT))
            self.send_header("icy-name", "Moy FM")
            self.end_headers()
            self.wfile.write(body[:cut])
            self.wfile.flush()
            time.sleep(0.4)
            self.wfile.write(body[cut:])
        elif self.path in ("/a.mp3", "/b.mp3"):
            self._send(MP3, "audio/mpeg")
        elif self.path == "/list.pls":
            text = ("[playlist]\nNumberOfEntries=2\nFile1=a.mp3\nTitle1=A side\n"
                    "File2=http://127.0.0.1:%d/b.mp3\nTitle2=B side\nVersion=2\n" % port)
            self._send(text.encode(), "audio/x-scpls")
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % s.server_address[1]
    s.shutdown()


@pytest.fixture
def media():
    devices.grants = None
    AUDIO.volume(7)
    yield dev_media
    if dev_media._s["pid"]:
        _as(dev_media._s["pid"], dev_media.stop)
        for _ in range(50):
            if not dev_media._s["pid"]:
                break
            _frame()
    devices.grants = None
    mp.share_use(None)
    assert N.pid() == 0


def _as(pid, fn):
    was = L.current()
    L.current(pid)
    try:
        return fn()
    finally:
        L.current(was)


def _frame():
    """One frame's media work: the C's job (the loop's tail runs it), and the
    face's wake where the stream has one."""
    N.pump()
    if dev_media._s["wake"] is not None:
        dev_media._pump()


def _media_leases():
    """The owners of the MEDIA lease's rows (moy_lease.h's MOY_LEASE_MEDIA)."""
    return [moy_lease.get(h)[0] for h in moy_lease.leases() if moy_lease.get(h)[1] == 7]


def _state():
    """What plays, or how the run that played ended (the run ends itself)."""
    n = moybyte.media.now()
    if n is not None:
        return n["state"]
    e = moybyte.media.last()
    return e["state"] if e is not None else None


def _drive(until, frames=2048, rounds=3000, out=None):
    """The frame's media work and the host's mix, a block a round, until `until()`."""
    buf = bytearray(frames * 2)
    for _ in range(rounds):
        _frame()
        AUDIO.render(buf, frames)
        if out is not None:
            out.extend(array("h", bytes(buf)))
        if until():
            return True
    return False


def _freq(pcm, rate):
    """The tone's frequency over what sounded: the silence of a starved or
    finished stream left out."""
    pcm = [v for v in pcm if v]
    crossings = sum(1 for a, b in zip(pcm, pcm[1:]) if (a < 0) != (b < 0))
    return crossings / 2 / (len(pcm) / rate)


def _rms(pcm):
    return math.sqrt(sum(v * v for v in pcm) / max(1, len(pcm)))


def test_an_icy_streams_now_playing_text_and_its_station(media, server):
    pid = L.spawn("t.radio")
    _H.seen.clear()
    _as(pid, lambda: moybyte.media.play(server + "/radio"))
    assert ("/radio", "1") in _H.seen                   # the client asked for the metadata
    assert _drive(lambda: (moybyte.media.now() or {}).get("title") == TITLES[0])
    now = moybyte.media.now()
    assert now["station"] == "Moy FM" and now["state"] == "playing" and now["client"] == pid
    assert now["who"] == "t.radio" and now["pid"] == N.pid() != pid
    assert now["rate"] == 44100 and now["channels"] == 2 and now["kbps"] == 128
    pcm = array("h")
    assert _drive(lambda: moybyte.media.now()["title"] == TITLES[1], out=pcm)
    assert _drive(lambda: _state() == "ended", out=pcm)
    # The metadata never reached the decoder: what the mix played is the tone.
    assert abs(_freq(pcm, AUDIO.rate()) - 1000) < 20


def test_a_playlists_entries_and_its_next(media, server):
    pid = L.spawn("t.list")
    _as(pid, lambda: moybyte.media.play(server + "/list.pls"))
    assert _drive(lambda: (moybyte.media.now() or {}).get("entries") == 2)
    now = moybyte.media.now()
    assert now["entry"] == 0 and now["src"] == server + "/a.mp3" and now["title"] == "A side"
    assert _as(pid, moybyte.media.next) is True
    assert _drive(lambda: moybyte.media.now()["state"] == "playing")
    now = moybyte.media.now()
    assert now["entry"] == 1 and now["src"] == server + "/b.mp3" and now["title"] == "B side"
    assert _as(pid, moybyte.media.next) is False         # the list's end
    assert _as(pid, moybyte.media.prev) is True
    assert _drive(lambda: moybyte.media.now()["entry"] == 0)


def test_a_file_playlist_moves_to_its_next_entry_by_itself(media, tmp_path):
    for name in ("one.mp3", "two.mp3"):
        shutil.copy(TONE, tmp_path / name)
    lst = tmp_path / "set.m3u"
    lst.write_text("#EXTM3U\n#EXTINF:2,Tone One\none.mp3\n#EXTINF:2,Tone Two\n%s\n"
                   % (tmp_path / "two.mp3"))
    pid = L.spawn("t.files")
    _as(pid, lambda: moybyte.media.play(str(lst)))
    assert moybyte.media.now()["title"] == "Tone One"
    assert moybyte.media.now()["src"] == str(tmp_path / "one.mp3")
    assert _drive(lambda: moybyte.media.now()["entry"] == 1)
    assert moybyte.media.now()["title"] == "Tone Two"
    assert _drive(lambda: _state() == "ended")
    rate = AUDIO.rate()
    # Both files decoded whole: 2 s each at the mix's rate, less the encoder's padding.
    assert abs(N.status()[15] - 4 * rate) < rate // 5


def test_a_file_decodes_into_the_mix_and_reads_back_as_its_tone(media):
    """The host loopback: the service's output read back from the host's mix."""
    pid = L.spawn("t.tone")
    _as(pid, lambda: moybyte.media.volume(100))          # no stream yet: nothing to set
    _as(pid, lambda: moybyte.media.play(str(TONE)))
    _as(pid, lambda: moybyte.media.volume(100))
    pcm = array("h")
    assert _drive(lambda: _state() == "ended", out=pcm)
    rate = AUDIO.rate()
    body = pcm[rate // 5:rate + rate // 2]
    assert abs(_freq(body, rate) - 1000) < 10
    # The fixture's tone peaks at -21.5 dBFS (ffmpeg's volumedetect: 2,760),
    # so a sine's RMS of 1,952 at full volume and the console's full level.
    assert abs(_rms(body) - 2760 / math.sqrt(2)) < 60
    assert N.status()[17] == 0                           # never starved


def test_a_file_plays_on_the_frames_job_alone(media):
    """No Python runs for a playing file: the face holds no wake, and the
    loop's C jobs (what a frame's tail runs) read it, decode it and end it."""
    pid = L.spawn("t.job")
    _as(pid, lambda: moybyte.media.play(str(TONE)))
    _as(pid, lambda: moybyte.media.volume(100))
    assert dev_media._s["wake"] is None
    buf, pcm = bytearray(4096), array("h")
    for _ in range(400):
        moy_loop.jobs()
        AUDIO.render(buf, 2048)
        pcm.extend(array("h", bytes(buf)))
        if N.status()[0] == N.ENDED:
            break
    assert N.status()[0] == N.ENDED and dev_media._s["wake"] is None
    rate = AUDIO.rate()
    assert abs(_freq(pcm[rate // 5:rate + rate // 2], rate) - 1000) < 10
    assert N.status()[17] == 0                           # never starved


def test_a_teardown_on_another_task_frees_nothing_the_frames_job_reads(media):
    """The run's lease dropped from outside (a sweep, as a kill's) and a
    close() on a thread not the frame's, while the frame's job reads and
    decodes the file: the two take turns, the service is freed whole, and
    the job's slot goes on the loop's own thread."""
    rng = random.Random(7)
    for i in range(60):
        assert N.open(AUDIO.rate()) == 0
        run = N.pid()
        assert N.play(str(TONE)) >= 0
        if i % 3 == 1:
            N.stop()                                     # idle soon: the close frees it
        done = threading.Event()
        pause = rng.random() * 0.003

        def revoke(run=run, pause=pause, done=done):
            time.sleep(pause)
            moy_lease.sweep(run)
            N.close()
            done.set()

        t = threading.Thread(target=revoke)
        t.start()
        while not done.is_set():
            moy_loop.jobs()
        t.join()
        for _ in range(8):
            moy_loop.jobs()
        assert N.pid() == 0 and mp.share_held(mp.SHARE_MEDIA) == 0 and N.status() is None


def test_a_files_tag_and_probe(media, tmp_path):
    """The file's ID3v2 title and artist, read in C as it plays and by
    probe() without playing it, in the tag's encodings."""
    assert moybyte.media.probe(str(TONE)) == (2, "Tone", "")
    body = MP3[10 + 0x33:]                               # the fixture's frames, its tag dropped

    def frame(fid, text, ver):
        size = len(text)
        if ver == 4:
            sz = bytes([(size >> 21) & 0x7f, (size >> 14) & 0x7f, (size >> 7) & 0x7f, size & 0x7f])
        else:
            sz = size.to_bytes(4, "big")
        return fid + sz + b"\0\0" + text

    def tag(frames, ver):
        n = len(frames)
        return (b"ID3" + bytes([ver, 0, 0])
                + bytes([(n >> 21) & 0x7f, (n >> 14) & 0x7f, (n >> 7) & 0x7f, n & 0x7f]) + frames)

    v4 = tag(frame(b"TIT2", b"\x03Caf\xc3\xa9 Song\0", 4)
             + frame(b"TPE1", b"\x01\xff\xfe" + "Bj\xf6rk".encode("utf-16-le") + b"\0\0", 4)
             + b"\0" * 64, 4)
    (tmp_path / "v4.mp3").write_bytes(v4 + body)
    assert moybyte.media.probe(str(tmp_path / "v4.mp3")) == (2, "Caf\xe9 Song", "Bj\xf6rk")
    v3 = tag(frame(b"TPE1", b"\x00Mot\xf6rhead", 3)
             + frame(b"TIT2", b"\x02" + "Ace".encode("utf-16-be"), 3), 3)
    (tmp_path / "v3.mp3").write_bytes(v3 + body)
    assert moybyte.media.probe(str(tmp_path / "v3.mp3"))[1:] == ("Ace", "Mot\xf6rhead")
    v2 = tag(b"TT2\0\0\x05\x00Two!" + b"TP1\0\0\x04\x00Duo", 2)
    (tmp_path / "v2.mp3").write_bytes(v2 + body)
    assert moybyte.media.probe(str(tmp_path / "v2.mp3"))[1:] == ("Two!", "Duo")
    assert moybyte.media.probe(str(tmp_path / "none.mp3")) is None
    pid = L.spawn("t.tag")
    _as(pid, lambda: moybyte.media.play(str(tmp_path / "v4.mp3")))
    assert _drive(lambda: moybyte.media.now()["artist"] == "Bj\xf6rk")
    assert moybyte.media.now()["title"] == "Caf\xe9 Song"


def test_a_file_seeks_and_a_stream_does_not(media, server, tmp_path):
    """seek(): the file from that place, its decoded tail dropped, its length
    and tag kept; refused before its length is known and for a stream."""
    long_ = tmp_path / "long.mp3"
    long_.write_bytes(MP3 + MP3[10 + 0x33:] * 4)         # the tone, ten seconds
    pid = L.spawn("t.seek")
    _as(pid, lambda: moybyte.media.play(str(long_)))
    assert _as(pid, lambda: moybyte.media.seek(5)) is False       # no length yet
    assert _drive(lambda: moybyte.media.now()["len"] > 0)
    assert moybyte.media.now()["len"] == 10 and moybyte.media.now()["at"] < 2
    assert _as(pid, lambda: moybyte.media.seek(7)) is True
    n = moybyte.media.now()
    assert 6 <= n["at"] <= 7 and n["len"] == 10 and n["title"] == "Tone"
    pcm = array("h")
    assert _drive(lambda: _state() == "ended", out=pcm)
    rate = AUDIO.rate()
    assert len(pcm) < 5 * rate                            # three seconds left, not ten
    _as(pid, lambda: moybyte.media.play(server + "/radio"))
    assert _drive(lambda: moybyte.media.now()["state"] == "playing")
    assert _as(pid, lambda: moybyte.media.seek(1)) is False


def test_a_files_place_and_length_and_a_streams_none(media, server):
    """now()'s `at` and `len` for a file (#110: Now playing's bar): its
    length from its size at the bytes its frames take, its place what the
    mix has pulled of it; a stream has neither."""
    pid = L.spawn("t.place")
    _as(pid, lambda: moybyte.media.play(str(TONE)))
    assert _drive(lambda: moybyte.media.now()["len"] > 0)
    assert moybyte.media.now()["len"] == 2           # the fixture's two seconds
    assert _drive(lambda: moybyte.media.now()["at"] >= 1)
    n = moybyte.media.now()
    assert 1 <= n["at"] <= n["len"]
    _as(pid, lambda: moybyte.media.play(server + "/radio"))
    assert _drive(lambda: moybyte.media.now()["state"] == "playing")
    n = moybyte.media.now()
    assert n["at"] == 0 and n["len"] == 0


def test_the_stream_is_ducked_while_the_focused_session_sounds(media):
    pid = L.spawn("t.duck")
    _as(pid, lambda: moybyte.media.play(str(TONE)))
    _as(pid, lambda: moybyte.media.volume(100))
    pcm = array("h")
    assert _drive(lambda: len(pcm) > AUDIO.rate() // 2, out=pcm)
    loud = _rms(pcm[-2048:])
    h = AUDIO.open(9001)
    try:
        AUDIO.focus(h)
        AUDIO.level(h, 0)                                # it sounds, at nothing
        AUDIO.beep(h, 440.0, 5.0)
        ducked = array("h")
        assert _drive(lambda: len(ducked) > 8192, out=ducked)
        assert abs(_rms(ducked[-2048:]) / loud - 0.25) < 0.03
    finally:
        AUDIO.focus(0)
        AUDIO.close(h)


def test_the_media_row_is_taken_given_back_and_absent_on_the_guition_s3(media):
    mp.share_use("tdeck")
    pid = L.spawn("t.share")
    _as(pid, lambda: moybyte.media.play(str(TONE)))
    assert mp.share_held(mp.SHARE_MEDIA) == N.share() <= mp.shares("tdeck")[mp.SHARE_MEDIA]
    _as(pid, moybyte.media.stop)
    assert N.pid() == 0 and mp.share_held(mp.SHARE_MEDIA) == 0
    mp.share_use("guition_s3")
    assert moybyte.media is None                         # absent, never 0
    assert N.open(22050) == -errno.ENOSPC               # the C declines it too
    assert N.pid() == 0


def test_permissions_the_last_command_wins_and_a_clients_end_leaves_it_playing(media, tmp_path):
    """Every process the permissions let in is a client: a play() replaces
    what plays whoever played it, a notification telling the person whose
    list it took over, and any client pauses or stops what plays. The run is
    nobody's process: its clients' ends leave it playing, and the shell is a
    client too."""
    from runtime.notifications import NOTES
    a, b, c = L.spawn("t.a"), L.spawn("t.b"), L.spawn("t.c")
    devices.grants = lambda p: {a: {"media"}, b: {"media", "media:url"}}.get(p, set())
    with pytest.raises(OSError) as e:
        _as(a, lambda: moybyte.media.play("http://127.0.0.1:1/x"))
    assert e.value.errno == errno.EPERM and "media:url" in str(e.value)
    with pytest.raises(OSError) as e:
        _as(b, lambda: moybyte.media.play("http://127.0.0.1:1/x"))
    assert e.value.errno == errno.EPERM and '"http"' in str(e.value)
    devices.grants = lambda p: {a: {"media"}, b: {"media"}}.get(p, set())
    with pytest.raises(OSError) as e:
        _as(a, lambda: moybyte.media.play(str(tmp_path / "nope.mp3")))
    assert e.value.errno == errno.ENOENT and N.pid() == 0     # nothing left running
    _as(a, lambda: moybyte.media.play(str(TONE), label="song\tTone"))
    run = N.pid()
    assert run not in (0, a) and L.name(run) == "media"
    assert _drive(lambda: moybyte.media.now()["state"] == "playing")
    seen = len(NOTES.list)
    _as(b, lambda: moybyte.media.play(str(TONE), label="song\tAgain"))
    assert N.pid() == run, "one run: the last command wins"
    now = moybyte.media.now()
    assert now["client"] == b and now["who"] == "t.b" and now["label"] == "song\tAgain"
    note = NOTES.list[seen:]
    assert [(n.summary, n.body) for n in note] == [
        ("t.b took over the music", "Again now plays in place of what t.a played.")]
    _as(b, lambda: moybyte.media.play(str(TONE)))
    assert len(NOTES.list) == seen + 1, "its own list again: no one to tell"
    with pytest.raises(OSError):
        _as(c, moybyte.media.pause)                      # not a client
    _as(a, moybyte.media.pause)                          # any client acts on what plays
    assert moybyte.media.now()["paused"] is True
    moybyte.end(a)
    moybyte.end(b)                                       # its clients end: it plays on
    for _ in range(4):
        _frame()
    assert N.pid() == run and moybyte.media.now()["paused"] is True
    _as(devices.SHELL, moybyte.media.stop)               # the shell is a client
    assert N.pid() == 0 and devices.MEDIA[0] == 0 and moybyte.media.now() is None
    assert moybyte.media.last()["pid"] == run and moybyte.media.last()["client"] == b
    devices.grants = None


def test_an_https_stream_is_refused_at_play_only_where_nothing_verifies(media, monkeypatch):
    pid = L.spawn("t.tls")
    assert moybyte.media.tls() is False                  # the host: plain TCP
    with pytest.raises(OSError) as e:
        _as(pid, lambda: moybyte.media.play("https://127.0.0.1:1/x.mp3"))
    assert e.value.errno == errno.EPROTONOSUPPORT and N.pid() == 0
    # A console that verifies plays it through the client, which is where the
    # host's refusal then lands: the stream fails, it is not refused.
    monkeypatch.setattr(dev_media, "tls", lambda: True)
    _as(pid, lambda: moybyte.media.play("https://127.0.0.1:1/x.mp3"))
    assert _drive(lambda: _state() == "failed")


def test_the_radio_is_held_while_a_url_may_play(media, server, tmp_path):
    from ws_helpers import build_ws
    ws = build_ws(tmp_path)
    devices.grants = None
    pid = L.spawn("t.lease")
    _as(pid, lambda: moybyte.media.play(server + "/a.mp3"))
    run = N.pid()
    assert ws.leases.held("media") and "media:%d" % run in ws.leases.holders()
    assert _drive(lambda: N.pid() == 0) and moybyte.media.last()["state"] == "ended"
    assert not ws.leases.held("media")            # the list ended: the run let it go
    _as(pid, lambda: moybyte.media.play(server + "/a.mp3"))
    assert ws.leases.held("media")
    _as(pid, moybyte.media.stop)
    assert not ws.leases.held("media")
    _as(pid, lambda: moybyte.media.play(str(TONE)))
    assert not ws.leases.held("media")            # a file needs no radio
    devices.console = None


def test_the_media_keys_answer_the_stream_from_a_game_that_owns_the_keys(media, tmp_path):
    """A keyboard's media keys are MEDIA records on the console's table, the
    service's as Super is the console's: the frame's job answers them while a
    game owns the keys, and the game reads nothing of them."""
    for name in ("one.mp3", "two.mp3"):
        shutil.copy(TONE, tmp_path / name)
    lst = tmp_path / "set.m3u"
    lst.write_text("one.mp3\ntwo.mp3\n")
    table = mi.InputTable()
    table.cart_keys = True                               # a game owns the keys
    ble = table.source("ble")

    def key(usage):
        ble.post(mi.EV_MEDIA, usage, 0, 1)
        ble.post(mi.EV_MEDIA, usage, 0, 0)
        _frame()
        table.begin_frame()
        assert table.last_key == 0 and not table.any_pressed()

    N.keys_on(table)
    try:
        pid = L.spawn("t.keys")
        _as(pid, lambda: moybyte.media.play(str(lst)))
        assert _drive(lambda: moybyte.media.now()["state"] == "playing")
        key(0xCD)                                        # Play/Pause
        assert moybyte.media.now()["paused"] is True
        key(0xCD)
        assert moybyte.media.now()["paused"] is False
        key(0xB1)                                        # Pause
        key(0xB1)
        assert moybyte.media.now()["paused"] is True
        key(0xB0)                                        # Play
        assert moybyte.media.now()["paused"] is False
        vol = moybyte.media.now()["volume"]
        key(0xE9)                                        # Volume Increment
        assert moybyte.media.now()["volume"] == vol + 10
        key(0xEA)
        key(0xEA)
        assert moybyte.media.now()["volume"] == vol - 10
        key(0xB5)                                        # Scan Next
        assert _drive(lambda: moybyte.media.now()["entry"] == 1)
        key(0xB5)                                        # past the list's last: nothing
        assert moybyte.media.now()["entry"] == 1
        key(0xB6)                                        # Scan Previous
        assert _drive(lambda: moybyte.media.now()["entry"] == 0)
        key(0xE2)                                        # Mute: not answered
        assert moybyte.media.now()["volume"] == vol - 10
        assert N.key(0xCD) is True and moybyte.media.now()["paused"] is True
    finally:
        N.keys_on(None)


def test_a_media_key_with_no_stream_is_answered_by_nothing(media):
    assert N.pid() == 0
    assert N.key(0xCD) is False and N.key(0xE9) is False


def test_the_parsers():
    assert N.icy_title(b"StreamTitle='A - B';StreamUrl='x';\0\0\0") == "A - B"
    assert N.icy_title(b"StreamTitle='It''s';") == "It''s"
    assert N.icy_title(b"StreamTitle='Caf\xe9';") == "Caf\xe9"
    assert N.icy_title("StreamTitle='Caf\xe9';".encode()) == "Caf\xe9"
    assert N.icy_title(b"StreamUrl='x';") is None
    assert N.list_kind("audio/x-mpegurl") == 1 and N.list_kind("audio/x-scpls") == 2
    assert N.list_kind(None, "/a/b.M3U8?x=1") == 1 and N.list_kind(None, "/a.pls") == 2
    assert N.list_kind(None, None, "﻿[playlist]\nFile1=a") == 2
    assert N.list_kind("audio/mpeg", "/stream", b"\xff\xfb") == 0
    assert os.path.getsize(TONE) == len(MP3)


def test_the_run_is_on_demand_and_plays_on_through_a_vm_stop(media, tmp_path):
    """The service holds nothing before a client's first command, plays on
    while the VM is down (the face's state gone with the heap, the frame's
    job reading and decoding), is followed again by the console after it
    (its row and its state), gives everything back at its end, and the next
    play is a new run."""
    from runtime import scheduler as S
    mp.share_use("tdeck")
    assert N.pid() == 0 and mp.share_held(mp.SHARE_MEDIA) == 0 and N.status() is None
    assert _media_leases() == []
    pid = L.spawn("t.vm")
    _as(pid, lambda: moybyte.media.play(str(TONE), label="song\tTone"))
    run = N.pid()
    assert S.SCHED.procs[run].name == "media" and S.SCHED.procs[run].kernel
    assert mp.share_held(mp.SHARE_MEDIA) == N.share()
    assert _media_leases() == [run]
    assert _drive(lambda: N.status()[0] == N.PLAYING)
    # The VM stops: the face's state and the scheduler's rows go with its heap.
    dev_media._s.update(pid=0, wake=None, every=0, radio=0)
    devices.MEDIA[0] = 0
    moy_evq.vm(False)
    try:
        played = N.status()[16]
        for _ in range(20):
            moy_loop.jobs()
            AUDIO.render(bytearray(4096), 2048)
        assert N.pid() == run and N.status()[16] > played, "it plays on with no VM"
    finally:
        moy_evq.vm(True)
    S.SCHED.procs.pop(run, None)
    assert dev_media.attach() == run                     # the console after the stop follows it
    assert S.SCHED.procs[run].name == "media" and devices.MEDIA[0] == run
    now = moybyte.media.now()
    assert now["pid"] == run and now["client"] == pid and now["label"] == "song\tTone"
    _as(pid, moybyte.media.stop)
    assert N.pid() == 0 and mp.share_held(mp.SHARE_MEDIA) == 0 and N.status() is None
    assert _media_leases() == []
    S.SCHED.poll()
    assert S.SCHED.procs[run].state == S.EXITED, "its row reaped"
    _as(pid, lambda: moybyte.media.play(str(TONE)))
    assert N.pid() not in (0, run), "a second play is a new run"


class _Reader:
    """A page's reader (firmware/web_runner/media_link.py's shape): None
    until its bytes come, then them, then 0; OSError for a refusal."""

    def __init__(self, data, err=None, size=None):
        self.data, self.err, self.size, self.at, self.waits = data, err, size, 0, 2
        self.closed = False

    def readinto(self, buf):
        if self.waits:
            self.waits -= 1
            return None
        if self.err:
            raise OSError(self.err, "refused")
        n = min(len(buf), len(self.data) - self.at)
        buf[:n] = self.data[self.at:self.at + n]
        self.at += n
        return n

    def close(self):
        self.closed = True


class _Net:
    def __init__(self, songs, err=None):
        self.songs, self.err, self.opened = songs, err, []

    def holds(self, src):
        return src in self.songs

    def held(self, folder):
        return tuple((k.rsplit("/", 1)[1], len(v)) for k, v in sorted(self.songs.items())
                     if k.rsplit("/", 1)[0] == folder)

    def open(self, src, at=0):
        data = self.songs.get(src, b"")
        r = _Reader(data, self.err, len(data))
        r.at = at
        self.opened.append((src, r, at))
        return r


@pytest.fixture
def page(monkeypatch):
    class _Ws:
        media_net = None
    ws = _Ws()
    monkeypatch.setattr(devices, "console", ws)
    return ws


def test_a_song_the_page_reads_plays_where_the_console_does_not_keep_it(media, page):
    """A board-served page: the board's song is not in the page's store, so
    play() takes it on the page's word and the pump feeds the page's reader,
    waiting while it has nothing, to its end."""
    path = "/moy/files/Music/Tone.mp3"
    pid = L.spawn("t.page")
    with pytest.raises(OSError) as e:
        _as(pid, lambda: moybyte.media.play(path))
    assert e.value.args[0] == errno.ENOENT
    page.media_net = _Net({path: MP3})
    assert dev_media.held("/moy/files/Music") == (("Tone.mp3", len(MP3)),)
    assert dev_media.held("/moy/files/other") == ()
    _as(pid, lambda: moybyte.media.play(path))
    pcm = array("h")
    assert _drive(lambda: _state() == "ended", out=pcm)
    rate = AUDIO.rate()
    start = next(i for i, v in enumerate(pcm) if v)  # past the page's two empty reads
    assert abs(_freq(pcm[start + rate // 5:start + rate + rate // 2], rate) - 1000) < 10
    assert page.media_net.opened[0][1].closed
    assert moybyte.media.now()["len"] == 2           # its size, from the reader
    assert dev_media._s["wake"] is not None          # the page's bytes are the face's


def test_a_song_the_page_reads_seeks_from_the_byte_the_c_asks_for(media, page):
    path = "/moy/files/Music/Long.mp3"
    page.media_net = _Net({path: MP3 + MP3[10 + 0x33:] * 4})
    pid = L.spawn("t.pageseek")
    _as(pid, lambda: moybyte.media.play(path))
    assert _drive(lambda: moybyte.media.now()["len"] == 10)
    assert _as(pid, lambda: moybyte.media.seek(5)) is True
    assert _drive(lambda: len(page.media_net.opened) == 2)
    src, _r, at = page.media_net.opened[1]
    total = len(page.media_net.songs[path])
    assert src == path and abs(at - total // 2) < total // 20      # half-way in, by its bytes
    assert _drive(lambda: _state() == "ended")


def test_a_page_readers_icy_head_takes_the_metadata_out_of_its_bytes(media, page):
    """A station's reader in the browser carries the ICY head the page read
    (its `icy`): the face hands it to the C before the first bytes, the C
    takes each metadata block out of the feed as a board's connection's,
    and its StreamTitle and name are the now-playing text and station."""
    path = "/moy/files/Music/Radio.mp3"
    net = _Net({path: _icy_body()})
    page.media_net = net
    real = net.open

    def open_icy(src, at=0):
        r = real(src, at)
        r.icy = (METAINT, "Moy FM")
        read = r.readinto
        r.readinto = lambda buf: read(memoryview(buf)[:1024])   # as a network's reads come
        return r
    net.open = open_icy
    pid = L.spawn("t.pageicy")
    _as(pid, lambda: moybyte.media.play(path))
    assert _drive(lambda: (moybyte.media.now() or {}).get("title") == TITLES[0])
    assert moybyte.media.now()["station"] == "Moy FM"
    pcm = array("h")
    assert _drive(lambda: moybyte.media.now()["title"] == TITLES[1], out=pcm)
    assert _drive(lambda: _state() == "ended", out=pcm)
    # The metadata never reached the decoder: what the mix played is the tone.
    start = next(i for i, v in enumerate(pcm) if v)
    assert abs(_freq(pcm[start:], AUDIO.rate()) - 1000) < 20


def test_a_reader_that_refuses_fails_the_entry_with_its_errno(media, page):
    path = "/moy/files/Music/Tone.mp3"
    page.media_net = _Net({path: MP3}, err=errno.EACCES)
    pid = L.spawn("t.refused")
    _as(pid, lambda: moybyte.media.play(path))
    assert _drive(lambda: _state() == "failed")
    assert (moybyte.media.now() or moybyte.media.last())["err"] == errno.EACCES
    assert dev_media.tls()                           # the page's fetch verifies https


def test_the_stop_verdict_counts_the_music_and_asks_only_when_it_must():
    """The media service plays on through a VM stop, so the stop's fit counts
    what its run holds as not given back: a game that fits with the music
    playing stops the VM and keeps it, one that fits only without it is
    KEEP_MEDIA (the console asks first, a file's or a station's alike), and
    one that fits with neither is too big."""
    mb = 1 << 20
    assert mp.stop_fit(10 * mb, mb, 20 * mb, 8 * mb, 4 * mb, 2 * mb) == "fits"
    assert mp.stop_fit(10 * mb, mb, 7 * mb, 8 * mb, 4 * mb, 2 * mb) == "stops"
    assert mp.stop_fit(12 * mb, mb, 7 * mb, 8 * mb, 4 * mb, 2 * mb) == "media"
    assert mp.stop_fit(12 * mb, mb, 7 * mb, 8 * mb, 4 * mb, 0) == "big"      # no music: no question
    assert mp.stop_fit(14 * mb, mb, 7 * mb, 8 * mb, 4 * mb, 2 * mb) == "big"


def test_a_game_that_fits_only_without_the_music_is_asked_first(media, monkeypatch, tmp_path):
    """The Player reads the verdict's `media` and the shell asks "Stop the
    music to play?"; Stop & play stops the stream and opens the game once the
    service has given everything back."""
    from runtime import player as P
    from runtime import scheduler as S
    from ws_helpers import build_ws
    ws = build_ws(tmp_path)
    devices.grants = None
    pl = ws.run.player
    asked, opened = [], []
    monkeypatch.setattr(ws, "ask_stop_music", lambda cart: asked.append(cart))
    info = [0] * 13
    monkeypatch.setattr(P._moy_play, "info", lambda run: info)
    monkeypatch.setattr(P._moy_play, "end", lambda run: None)
    cart = {"title": "Doom"}
    info[11] = "stops"
    assert pl._music_keeps_the_vm(1, cart) is False
    info[11] = "media"
    pl.pid = L.spawn("t.doom")
    assert pl._music_keeps_the_vm(1, cart) is True and asked == [cart] and pl.declined
    # Stop & play: the open waits for the run's end, a frame of the C's job.
    pid = L.spawn("t.music")
    _as(pid, lambda: moybyte.media.play(str(TONE)))
    monkeypatch.setattr(ws, "open", lambda c: opened.append(c))
    monkeypatch.setattr(dev_media, "_STEP", False)       # a board: the job ends the run later
    _as(devices.SHELL, moybyte.media.stop)
    assert N.pid() != 0
    type(ws)._open_when_quiet(ws, moybyte.media, cart)
    assert opened == []
    N.pump()                                             # the frame's job: the run ends
    S.SCHED._n = S.SCHED._i = 0
    S.SCHED.tick(1_000_000)
    assert N.pid() == 0 and opened == []                 # its wake has not come yet
    time.sleep(0.06)
    S.SCHED._n = S.SCHED._i = 0
    S.SCHED.tick(1_000_000)
    assert opened == [cart]
    devices.console = None


def test_musics_status_item_shows_while_any_clients_run_plays(media, tmp_path, monkeypatch):
    """The console listens on the face (dev_media.listen): a run another
    client started puts Music's card and status item in the bar, and the
    run's end takes them out."""
    from runtime import music_app as M
    from runtime.status_items import ITEMS
    from ws_helpers import build_ws
    ws = build_ws(tmp_path)
    devices.grants = None
    monkeypatch.setattr(dev_media, "_HEARD", [None])
    dev_media.listen(ws._music_follows)
    s = M.STREAM
    try:
        pid = L.spawn("t.player")
        _as(pid, lambda: moybyte.media.play(str(TONE)))
        assert s.what[0] == "song" and s.what[2] == str(TONE)
        it = [i for i in ITEMS.slots if i.pid and i.app == "Music"]
        assert len(it) == 1
        _as(pid, moybyte.media.stop)
        s.poll()
        assert not [i for i in ITEMS.slots if i.pid and i.app == "Music"]
        assert s.what is None and s.card is None
    finally:
        s._end()
        s.section = None
        devices.console = None
