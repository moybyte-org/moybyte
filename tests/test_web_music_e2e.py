"""Music in a real browser, in both web modes (#251): the boards' MP3 decoder
(native/moy_media) compiled into the wasm, pulled by the page's mix, its
samples reaching the page's audio.

SITE: a song dropped on the page lands in the browser's OPFS store
(files/Music), never in the VFS; on the next load Music lists it, plays it
from OPFS a range at a time, and the page's audio receives the tone.

BOARD-SERVED: the page a board serves keeps no song. The board is real C,
the kernel's webhost with its sign-in in the web terminal's host harness
(tests/test_moy_tty.py) with a files root, and serve.py --board serves the
bundle in front of it. Music lists the board's music folder and plays a song
read through the sealed GET /media, a range a request.

RADIO (site): a station is the page's fetch. One that sends CORS headers
plays; one that does not says so ("Blocks browsers"). The station refuses a
request that names its page, as SomaFM's do, so the fetch names none. A desk
that booted into the Editor (the project site's hero) plays one from Music
once the Editor is closed.

    MOYBYTE_WEB_E2E=1 .venv/bin/python -m pytest tests/test_web_music_e2e.py
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

import web_e2e
from web_e2e import ROOT

sys.path.insert(0, os.path.dirname(__file__))

pytestmark = pytest.mark.skipif(
    not os.environ.get("MOYBYTE_WEB_E2E"),
    reason="MOYBYTE_WEB_E2E not set (spawns headless Chrome)")

TONE = ROOT / "tests" / "fixtures" / "media" / "tone_1k.mp3"
SONG = "Tone One.mp3"


def _long_tone():
    """The fixture's two seconds five times over (its 61-byte ID3 tag once),
    so the song still plays when the harness reads it."""
    b = TONE.read_bytes()
    assert b[:3] == b"ID3"
    tag = 10 + ((b[6] & 0x7f) << 21 | (b[7] & 0x7f) << 14 | (b[8] & 0x7f) << 7 | (b[9] & 0x7f))
    return b + b[tag:] * 4


LONG = _long_tone()

# What the page's audio path saw: a spy on the worker's frames, counting the
# ones that carry PCM, the loudest sample, and the tone's zero crossings
# (a 1 kHz tone crosses zero about 2,000 times a second of what sounded, at
# the page's mix rate, which the spy reads from the page).
_SPY = """(() => {
  const was = onWorker;
  window.__pcm = {frames: 0, peak: 0, n: 0, cross: 0, last: 0, rate: AUDIO_RATE};
  onWorker = function (m) {
    if (m.t === 'frame' && m.s) {
      const a = JSON.parse(m.s).audio;
      if (a) {
        const b = atob(a), p = window.__pcm; p.frames++;
        for (let i = 0; i + 1 < b.length; i += 2) {
          let v = b.charCodeAt(i) | (b.charCodeAt(i + 1) << 8);
          if (v >= 32768) v -= 65536;
          if (!v) continue;
          p.n++; p.peak = Math.max(p.peak, Math.abs(v));
          if ((v < 0) !== (p.last < 0) && p.last) p.cross++;
          p.last = v;
        }
      }
    }
    return was(m);
  };
  return 'spying';
})()"""
_PCM = "'PCM ' + JSON.stringify(window.__pcm)"
_MEDIA = "__moyState().then(s => 'MEDIA ' + JSON.stringify(s.media))"
_LAST = "__moyState().then(s => 'LAST ' + JSON.stringify(s.media_last))"


def _playing(timeout=20000):
    return {"waitFor": "__moyState().then(s => s.media && s.media.state === 'playing'"
                       " && s.media.at >= 1)", "timeout": timeout}


def _tagged(js, tag):
    for j in js:
        if isinstance(j, str) and j.startswith(tag + " "):
            return json.loads(j[len(tag) + 1:])
    return None


def _assert_tone(pcm, out):
    assert pcm and pcm["frames"] > 0 and pcm["peak"] > 1000, \
        "the song's samples did not reach the page's audio: %r\n%s" % (pcm, out[-3000:])
    hz = pcm["cross"] / 2 / (pcm["n"] / pcm["rate"])
    assert abs(hz - 1000) < 60, "not the fixture's 1 kHz tone: %.0f Hz %r" % (hz, pcm)


# The Music app at the handheld's 320x240: E is the next tab (My music), and
# Enter plays the current row, the folder's one song.
_MY_MUSIC = [{"wait": 1500}, {"key": "e"}, {"wait": 800}, {"key": "Enter"}]


def _site(tmp_path):
    web_e2e.require("store", "media")
    return web_e2e.serve(web_e2e.DIST, web_e2e.free_port())


def test_site_a_dropped_song_is_kept_in_opfs_and_plays_from_it(tmp_path):
    server, base = _site(tmp_path)
    profile = tmp_path / "chrome"
    song = tmp_path / "song.mp3"
    song.write_bytes(LONG)
    try:
        out, js = web_e2e.run(tmp_path, "drop", "?dev=1&handheld=1", [
            {"file": str(song), "as": "__song"},
            {"js": "pzImport(new File([window.__song], %s)), 'sent'" % json.dumps(SONG)},
            {"waitFor": "window.__moyImported", "timeout": 15000},
            {"js": "'IMPORTED ' + JSON.stringify(window.__moyImported)"},
            {"js": "navigator.storage.getDirectory().then(d => d.getDirectoryHandle('files'))"
                   ".then(f => f.getDirectoryHandle('Music')).then(m => m.getFileHandle(%s))"
                   ".then(h => h.getFile()).then(f => 'KEPT ' + f.size)" % json.dumps(SONG)},
        ], base, profile, boot_ms=30000)
        assert _tagged(js, "IMPORTED") == "added %s to your music" % SONG, out[-3000:]
        assert "KEPT %d" % len(LONG) in js, js

        out, js = web_e2e.run(tmp_path, "play", "?dev=1&handheld=1&cart=Music", [
            {"js": _SPY}] + _MY_MUSIC + [
            _playing(), {"js": _MEDIA}, {"wait": 1500}, {"js": _PCM}, {"shot": "playing"},
        ], base, profile, boot_ms=30000)
        media = _tagged(js, "MEDIA")
        assert media and media["src"] == "/moy/files/Music/" + SONG, (media, out[-3000:])
        assert media["state"] == "playing" and 9 <= media["len"] <= 11, media
        _assert_tone(_tagged(js, "PCM"), out)
    finally:
        server.terminate()
        server.wait(timeout=10)


def _station(tmp_path, cors):
    host = web_e2e.Host(cors=cors, referer=False)
    host.files["/live"] = LONG
    return host


def _add_station(url):
    """Radio's Add a station (its first row): the address and a name typed
    into their fields at the handheld's 320x240, then Add, which plays it."""
    return ([{"key": "Enter"}, {"wait": 800}, {"click": [160, 66]}]
            + [{"key": c} for c in url]
            + [{"click": [160, 135]}] + [{"key": c} for c in "Test FM"]
            + [{"click": [263, 193]}, {"wait": 800}])


@pytest.mark.parametrize("cors", [True, False])
def test_site_a_station_plays_when_it_lets_a_page_read_it_and_says_so_when_not(
        tmp_path, cors):
    station = _station(tmp_path, cors)
    server, base = _site(tmp_path)
    try:
        url = station.base + "/live"
        out, js = web_e2e.run(tmp_path, "radio", "?dev=1&handheld=1&cart=Music",
                              [{"js": _SPY}, {"wait": 1500}] + _add_station(url) + [
            {"js": _MEDIA},
            {"waitFor": "__moyState().then(s => (s.media && s.media.at >= 1)"
                        " || (s.media_last && s.media_last.state === 'failed'))",
             "timeout": 20000},
            {"wait": 1000}, {"js": _MEDIA}, {"js": _LAST}, {"js": _PCM}, {"shot": "radio"},
        ], base, tmp_path / "chrome", boot_ms=30000)
        media = [j for j in js if isinstance(j, str) and j.startswith("MEDIA ")]
        last = json.loads(media[-1][6:]) if media else None
        assert not station.refused, station.refused
        if cors:
            assert last, (js, out[-3000:])
            assert last["src"] == url and last["state"] == "playing", last
            _assert_tone(_tagged(js, "PCM"), out)
        else:
            # past its one entry, the list failed with the page's refusal and
            # the run ended: the card says "Blocks browsers"
            assert media and last is None, js
            ended = _tagged(js, "LAST")
            assert ended and ended["state"] == "failed" and ended["err"] == 13, (js, out[-3000:])
    finally:
        station.stop()
        server.terminate()
        server.wait(timeout=10)


# The project site's hero: the desk boots into the Editor on a cart's code.
_HERO = "?dev=1&desktop=1&start=1&edit=moybyte.star_catcher.moy&tab=code&scheme=light"


def test_site_a_station_plays_on_a_desk_that_booted_into_the_editor(tmp_path):
    """The hero's start opens the Editor, which starts its cart for PLAY;
    with the Editor closed (its X), Music opened from the desk adds a
    station by keys alone (Enter on Add a station, its address typed
    straight into Address, Enter, a name, Enter) and it plays: Music's
    commands are the shell's, never the Editor's run's."""
    station = _station(tmp_path, True)
    server, base = _site(tmp_path)
    try:
        url = station.base + "/live"
        out, js = web_e2e.run(tmp_path, "hero", _HERO, [
            {"js": _SPY}, {"wait": 2500},
            {"js": "__moyState().then(s => 'EDIT ' + JSON.stringify(s.edit))"},
            {"click": [999, 19]}, {"wait": 1200},                   # the Editor's X
            {"click": [143, 80]}, {"wait": 2500},                   # Music on the desk
            {"key": "Enter"}, {"wait": 800}]                         # Add a station: Address
            + [{"key": c} for c in url] + [{"wait": 300}, {"key": "Enter"}, {"wait": 300}]
            + [{"key": c} for c in "Test FM"] + [{"wait": 300}, {"key": "Enter"}] + [
            {"waitFor": "__moyState().then(s => (s.media && s.media.at >= 1)"
                        " || (s.media_last && s.media_last.state === 'failed'))",
             "timeout": 20000},
            {"wait": 1000}, {"js": _MEDIA}, {"js": _LAST}, {"js": _PCM}, {"shot": "hero"},
        ], base, tmp_path / "chrome", boot_ms=40000)
        assert (_tagged(js, "EDIT") or {}).get("tab") == "code", (js, out[-3000:])
        media = _tagged(js, "MEDIA")
        assert media and media["src"] == url and media["state"] == "playing", \
            (media, _tagged(js, "LAST"), out[-3000:])
        assert media["client"] == 1, media                 # the shell's
        _assert_tone(_tagged(js, "PCM"), out)
    finally:
        station.stop()
        server.terminate()
        server.wait(timeout=10)


# -- the board-served page ----------------------------------------------------------------

@pytest.fixture(scope="module")
def tty_exe(tmp_path_factory):
    web_e2e.require("term", "signin", "media")
    import test_moy_tty
    return test_moy_tty._build(tmp_path_factory.mktemp("tty"))


@pytest.fixture
def board(tty_exe):
    import test_moy_tty
    d = Path(tempfile.mkdtemp(prefix="moym"))
    host = test_moy_tty.Host(tty_exe, d, files=True)
    store = host.tree / "sd" / "moybyte" / "carts"
    for cart in sorted((ROOT / "system_carts").glob("*.moy")):
        shutil.copytree(cart, store / cart.name)
    music = host.tree / "sd" / "moybyte" / "files" / "Music"
    music.mkdir(parents=True)
    (music / SONG).write_bytes(LONG)
    yield host
    host.end()
    shutil.rmtree(d, ignore_errors=True)


def test_board_served_a_song_streams_through_the_sealed_route_and_plays(tmp_path, board):
    import test_web_term_e2e as T
    server, base = T._serve(tmp_path, board.port)
    try:
        out, js = web_e2e.run(tmp_path, "board", "?dev=1&handheld=1&cart=Music"
                              + T._qr_fragment(board), [
            {"js": _SPY}] + _MY_MUSIC + [
            _playing(), {"js": _MEDIA}, {"wait": 1500}, {"js": _PCM}, {"shot": "board"},
            {"js": "window.__moyPersist ? 'MODE ' + JSON.stringify(window.__moyPersist.mode)"
                   " : 'MODE null'"},
        ], base, tmp_path / "chrome", boot_ms=30000)
        assert _tagged(js, "MODE") == "board", js
        media = _tagged(js, "MEDIA")
        assert media and media["src"] == "/moy/files/Music/" + SONG, (media, out[-3000:])
        assert media["state"] == "playing" and 9 <= media["len"] <= 11, media
        _assert_tone(_tagged(js, "PCM"), out)
    finally:
        server.terminate()
        server.wait(timeout=10)
