"""The webhost's GET /media (native/moy_net/moy_webhost.c, THE MEDIA ROUTE):
the music folder's songs and their bytes by range, sealed under the sign-in,
for the Music a board-served page plays in the browser. Driven against the
kernel's own webhost in the web terminal's host harness (tests/test_moy_tty.py),
with a files root."""

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import seal_client  # noqa: E402
import test_moy_tty as T  # noqa: E402

TONE = Path(__file__).resolve().parent / "fixtures" / "media" / "tone_1k.mp3"
NAME = "Tone One.mp3"

pytestmark = pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return T._build(tmp_path_factory.mktemp("media"))


@pytest.fixture
def host(built, tmp_path):
    h = T.Host(built, tmp_path, files=True)
    music = h.tree / "sd" / "moybyte" / "files" / "music"
    music.mkdir(parents=True)
    shutil.copy(TONE, music / NAME)
    (music / "notes.txt").write_text("not a song")
    (music / "sub").mkdir()
    shutil.copy(TONE, music / "sub" / "deep.mp3")
    shutil.copy(TONE, music / ".hidden.mp3")
    h.page = h.browser()
    yield h
    h.end()


def _q(name, at, n):
    return "/media?f=%s&at=%d&len=%d" % (name.encode().hex(), at, n)


def test_the_listing_names_the_music_folders_songs_and_sizes(host):
    status, doc = host.page.json("GET", "/media")
    assert status == 200
    assert doc == {NAME: TONE.stat().st_size}


def test_a_song_reads_back_whole_by_range(host):
    want = TONE.read_bytes()
    got, at = b"", 0
    while True:
        status, plain = host.page.call("GET", _q(NAME, at, 32768))
        assert status == 200
        if not plain:
            break
        got += plain
        at += len(plain)
    assert got == want
    status, plain = host.page.call("GET", _q(NAME, 1000, 37))
    assert plain == want[1000:1037]


def test_a_request_with_no_session_is_refused_in_the_clear(host):
    for target in ("/media", _q(NAME, 0, 4096)):
        status, _body = T.get(host, target)
        assert status == 401


@pytest.mark.parametrize("name", ["../system/webkeys", "sub/deep.mp3", ".hidden.mp3",
                                  "notes.txt", ""])
def test_a_name_outside_the_music_folders_songs_is_refused(host, name):
    status, _plain = host.page.call("GET", _q(name, 0, 4096) if name else "/media?f=zz&at=0&len=1")
    assert status == 400


def test_a_range_past_the_piece_or_unparsed_is_refused_and_a_missing_song_is_404(host):
    assert host.page.call("GET", _q(NAME, 0, 32769))[0] == 400
    assert host.page.call("GET", "/media?f=%s&at=x&len=10" % NAME.encode().hex())[0] == 400
    assert host.page.call("GET", _q("gone.mp3", 0, 10))[0] == 404


def test_a_host_with_no_files_root_has_no_media_route(built, tmp_path):
    h = T.Host(built, tmp_path)
    try:
        status, _body = T.get(h, "/media")
        assert status == 404
    finally:
        h.end()


def test_the_open_host_answers_in_the_clear(built, tmp_path):
    h = T.Host(built, tmp_path, sealed=False, files=True)
    try:
        music = h.tree / "sd" / "moybyte" / "files" / "music"
        music.mkdir(parents=True)
        shutil.copy(TONE, music / NAME)
        status, body = T.get(h, _q(NAME, 0, 100))
        assert status == 200 and body == TONE.read_bytes()[:100]
    finally:
        h.end()


assert seal_client  # the harness's browser end
