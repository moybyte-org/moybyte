"""The media service (native/moy_media/moy_media.h, its face
runtime/system/dev_media.py; docs/os_2026-11/hardware.md section 3) on the
host's build of the C: an ICY stream's now-playing text from a local server,
a playlist's next entry, a file decoded into the mix and read back (the host
loopback), the duck under a sounding session, the MEDIA row of THE SHARES
taken, given back and absent, and the permissions, the one owner and the end
of its process."""

import errno
import http.server
import math
import os
import shutil
import threading
import time
from array import array
from pathlib import Path

import pytest

from runtime import audio_binding, dev_media, devices, moybyte
from runtime import moy_log as L
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
            dev_media._pump()
    devices.grants = None
    mp.share_use(None)
    assert N.owner() == 0


def _as(pid, fn):
    was = L.current()
    L.current(pid)
    try:
        return fn()
    finally:
        L.current(was)


def _drive(until, frames=2048, rounds=3000, out=None):
    """The pump and the host's mix, a block a round, until `until()`."""
    buf = bytearray(frames * 2)
    for _ in range(rounds):
        dev_media._pump()
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
    assert now["station"] == "Moy FM" and now["state"] == "playing" and now["pid"] == pid
    assert now["rate"] == 44100 and now["channels"] == 2 and now["kbps"] == 128
    pcm = array("h")
    assert _drive(lambda: moybyte.media.now()["title"] == TITLES[1], out=pcm)
    assert _drive(lambda: moybyte.media.now()["state"] == "ended", out=pcm)
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
    assert _drive(lambda: moybyte.media.now()["state"] == "ended")
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
    assert _drive(lambda: moybyte.media.now()["state"] == "ended", out=pcm)
    rate = AUDIO.rate()
    body = pcm[rate // 5:rate + rate // 2]
    assert abs(_freq(body, rate) - 1000) < 10
    # The fixture's tone peaks at -21.5 dBFS (ffmpeg's volumedetect: 2,760),
    # so a sine's RMS of 1,952 at full volume and the console's full level.
    assert abs(_rms(body) - 2760 / math.sqrt(2)) < 60
    assert N.status()[17] == 0                           # never starved


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
    assert N.owner() == 0 and mp.share_held(mp.SHARE_MEDIA) == 0
    mp.share_use("guition_s3")
    assert moybyte.media is None                         # absent, never 0
    assert N.open(pid, 22050) == -errno.ENOSPC          # the C declines it too
    assert N.owner() == 0


def test_permissions_one_owner_https_and_the_end_of_the_process(media, tmp_path):
    a, b = L.spawn("t.a"), L.spawn("t.b")
    devices.grants = lambda p: {a: {"media"}, b: {"media", "media:url"}}.get(p, set())
    with pytest.raises(OSError) as e:
        _as(a, lambda: moybyte.media.play("http://127.0.0.1:1/x"))
    assert e.value.errno == errno.EPERM and "media:url" in str(e.value)
    with pytest.raises(OSError) as e:
        _as(b, lambda: moybyte.media.play("http://127.0.0.1:1/x"))
    assert e.value.errno == errno.EPERM and '"http"' in str(e.value)
    devices.grants = None
    with pytest.raises(OSError) as e:
        _as(b, lambda: moybyte.media.play("https://example.com/x.mp3"))
    assert e.value.errno == errno.EPROTONOSUPPORT
    with pytest.raises(OSError) as e:
        _as(a, lambda: moybyte.media.play(str(tmp_path / "nope.mp3")))
    assert e.value.errno == errno.ENOENT and N.owner() == 0
    _as(a, lambda: moybyte.media.play(str(TONE)))
    with pytest.raises(OSError) as e:
        _as(b, lambda: moybyte.media.play(str(TONE)))
    assert e.value.errno == errno.EBUSY
    with pytest.raises(OSError):
        _as(b, moybyte.media.pause)                      # not b's stream
    _as(devices.SHELL, moybyte.media.pause)              # the shell's stop control may
    assert moybyte.media.now()["paused"] is True
    assert devices.MEDIA[0] == a
    moybyte.end(a)                                       # its process ends: the stream stops
    for _ in range(10):
        dev_media._pump()
    assert N.owner() == 0 and devices.MEDIA[0] == 0 and moybyte.media.now() is None


def test_the_radio_is_held_while_a_url_may_play(media, server, tmp_path):
    from ws_helpers import build_ws
    ws = build_ws(tmp_path)
    devices.grants = None
    pid = L.spawn("t.lease")
    _as(pid, lambda: moybyte.media.play(server + "/a.mp3"))
    assert "media" in ws.leases.holders()
    assert _drive(lambda: moybyte.media.now()["state"] == "ended")
    assert "media" not in ws.leases.holders()            # the list ended
    _as(pid, lambda: moybyte.media.play(server + "/a.mp3"))
    assert "media" in ws.leases.holders()
    _as(pid, moybyte.media.stop)
    assert "media" not in ws.leases.holders()
    _as(pid, lambda: moybyte.media.play(str(TONE)))
    assert "media" not in ws.leases.holders()            # a file needs no radio
    devices.console = None


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


def test_the_vms_end_closes_its_processes_stream(media):
    """The kernel's moy_media_close_vm (a soft reset or a VM stop): the stream
    of a process the VM held ends with it and its row comes back, though the
    face's own state went with the heap."""
    mp.share_use("tdeck")
    pid = L.spawn("t.vm")
    _as(pid, lambda: moybyte.media.play(str(TONE)))
    assert _drive(lambda: N.status()[0] == N.PLAYING)
    assert N.close_vm() == 0
    assert N.owner() == 0 and mp.share_held(mp.SHARE_MEDIA) == 0
    assert N.close_vm() == 0                             # nothing left: nothing to do
    dev_media._end()
