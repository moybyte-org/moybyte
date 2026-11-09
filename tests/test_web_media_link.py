"""The Python half of Music's bytes in the browser
(firmware/web_runner/media_link.py): the listing the worker writes, and a
reader over the worker's spool segments, which reads a segment as far as it
has grown, removes it once it is whole and read, and raises the worker's
errno only once every byte that came is read."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MUSIC = "/moy/files/Music"


@pytest.fixture
def net(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "firmware" / "web_runner"))
    try:
        import carts_link
        import media_link
    finally:
        sys.path.pop(0)
    spool = tmp_path / "net"
    spool.mkdir()
    monkeypatch.setattr(media_link, "SPOOL", str(spool))
    monkeypatch.setattr(media_link, "LISTING", str(spool / "media.json"))
    link = carts_link.CartsLink()
    return media_link, link, media_link.WebMediaNet(link, MUSIC), spool


def _jobs(link):
    text = link.poll_json()
    return json.loads(text) if text else []


def test_the_listing_names_the_songs_and_follows_the_workers_rewrite(net):
    mod, _link, n, spool = net
    assert n.held(MUSIC) == () and not n.holds(MUSIC + "/a.mp3")
    (spool / "media.json").write_text(json.dumps({"b.mp3": 20, "a.mp3": 10, "x": "no"}))
    assert n.held(MUSIC) == (("a.mp3", 10), ("b.mp3", 20))
    assert n.held("/moy/files/Documents") == ()
    assert n.holds(MUSIC + "/a.mp3") and not n.holds("/elsewhere/a.mp3")
    (spool / "media.json").write_text(json.dumps({"a.mp3": 10, "b.mp3": 20, "c.mp3": 300}))
    assert len(n.held(MUSIC)) == 3


def test_a_song_and_a_url_are_asked_of_the_worker(net):
    _mod, link, n, spool = net
    (spool / "media.json").write_text(json.dumps({"a b.mp3": 1234}))
    song = n.open(MUSIC + "/a b.mp3")
    url = n.open("http://radio.example/live")
    assert song.size == 1234 and url.size is None
    assert _jobs(link) == [{"op": "mget", "id": song.rid, "song": "a b.mp3", "size": 1234, "at": 0},
                           {"op": "mget", "id": url.rid, "url": "http://radio.example/live"}]
    url.close()
    assert _jobs(link) == [{"op": "drop", "id": url.rid}]


def test_the_reader_walks_the_segments_as_they_grow_and_removes_each(net):
    _mod, link, n, spool = net
    r = n.open("http://radio.example/live")
    buf = bytearray(8)
    assert r.readinto(buf) is None                       # nothing yet
    link.event_json(json.dumps({"id": r.rid, "status": 200, "size": None}))
    seg0 = spool / ("m%d.0" % r.rid)
    seg0.write_bytes(b"abc")
    assert r.readinto(buf) == 3 and bytes(buf[:3]) == b"abc"
    assert r.readinto(buf) is None                       # the segment may grow yet
    with open(seg0, "ab") as f:
        f.write(b"defghijklm")
    assert r.readinto(buf) == 8 and bytes(buf) == b"defghijk"
    link.event_json(json.dumps({"id": r.rid, "seg": 1}))
    (spool / ("m%d.1" % r.rid)).write_bytes(b"NOP")
    assert r.readinto(buf) == 2 and bytes(buf[:2]) == b"lm"
    assert r.readinto(buf) == 3 and bytes(buf[:3]) == b"NOP"
    assert not seg0.exists()                             # whole and read: gone
    link.event_json(json.dumps({"id": r.rid, "end": 1, "segs": 2}))
    assert r.readinto(buf) == 0
    assert not (spool / ("m%d.1" % r.rid)).exists()


def test_an_error_is_raised_only_once_the_bytes_that_came_are_read(net):
    _mod, link, n, spool = net
    r = n.open("http://radio.example/live")
    (spool / ("m%d.0" % r.rid)).write_bytes(b"xy")
    link.event_json(json.dumps({"id": r.rid, "error": "cors", "errno": 13}))
    buf = bytearray(8)
    assert r.readinto(buf) == 2
    with pytest.raises(OSError) as e:
        r.readinto(buf)
    assert e.value.args[0] == 13
