"""Music in the browser: the page's bytes behind `ws.media_net`, which the
media face (runtime/system/dev_media.py) feeds the C decoder from where the
console has no HTTP client and keeps no song.

A page keeps no song in its VFS, in either mode: a site-mode page's songs
are the browser's OPFS store's (`files/Music`), a board-served page's are the
serving board's music folder. The worker lists them at boot into LISTING
({name: size}), and rewrites it when a song is dropped on the page, so
`held()` names them for the Music app and `holds()` lets the face play one.
`open(src)` reads a song or a URL: the worker reads the song out of OPFS, or
from the board's sealed GET /media a range at a time (native/moy_net/
moy_webhost.c, THE MEDIA ROUTE), or fetches the URL with the page's fetch.

THE BYTES GO THROUGH SPOOL SEGMENTS, never the VM's heap: the worker appends
what arrives to SPOOL/m<id>.<k>, each segment SEG bytes, and says when one is
whole; the reader reads a segment as far as it has grown and removes it once
read, and the worker reads no further while AHEAD segments wait. A live
stream therefore costs the page's memory a few segments, however long it
plays. Nothing here waits: a read with nothing yet answers None and the
face's pump comes back.

Events the worker sends (carts_link's `event_json`, by request id):
    {"id": n, "status": s, "size": b|null}  the head: b bytes in all, or a
                                    live stream's null
    {"id": n, "seg": k}             segments below k are whole
    {"id": n, "end": 1, "segs": k}  the body is all in k segments
    {"id": n, "error": "...", "errno": e}  the read failed: EACCES for a
                                    station that does not let a page read it
"""

import json

try:
    import os
except ImportError:  # pragma: no cover
    os = None

SPOOL = "/moy/net"
LISTING = SPOOL + "/media.json"
MUSIC = "Music"                    # the files root's folder of songs
EIO = 5


def _is_url(src):
    return src[:7].lower() == "http://" or src[:8].lower() == "https://"


class WebMediaNet:
    """`ws.media_net`: the songs of `music` (the page's own files root's
    music folder) that the worker listed, and a reader for each, or for a
    URL. `link` is carts_link's queue to the worker."""

    def __init__(self, link, music):
        self.link = link
        self.music = music
        self._songs = {}
        self._seen = None

    def _listing(self):
        try:
            st = os.stat(LISTING)
            key = (st[6], st[8])
        except OSError:
            return {}
        if key != self._seen:
            self._seen = key
            try:
                with open(LISTING) as f:
                    doc = json.load(f)
            except (OSError, ValueError):
                doc = None
            self._songs = {k: v for k, v in doc.items() if isinstance(v, int)} \
                if isinstance(doc, dict) else {}
        return self._songs

    def held(self, folder):
        if folder != self.music:
            return ()
        return tuple(sorted(self._listing().items()))

    def holds(self, src):
        folder, _, name = src.rpartition("/")
        return folder == self.music and name in self._listing()

    def open(self, src):
        link = self.link
        r = _Stream(link, link._id())
        link._live[r.rid] = r
        if _is_url(src):
            link.queue({"op": "mget", "id": r.rid, "url": src})
        else:
            name = src.rpartition("/")[2]
            r.size = self._listing().get(name)
            link.queue({"op": "mget", "id": r.rid, "song": name, "size": r.size})
        return r


class _Stream:
    """One read, from its spool segments: readinto() -> n, None (nothing
    yet) or 0 (the end); OSError with the worker's errno once what came is
    read. `size` is its bytes in all, None for a live stream or unknown."""

    def __init__(self, link, rid):
        self.link = link
        self.rid = rid
        self.size = None
        self.done = 0              # segments below this are whole
        self.ended = False
        self.err = None
        self._seg = 0
        self._pos = 0
        self._f = None

    def hear(self, ev):
        if "status" in ev and ev.get("size") is not None:
            self.size = int(ev["size"])
        if "seg" in ev:
            self.done = max(self.done, int(ev["seg"]))
        if ev.get("end"):
            self.done = max(self.done, int(ev.get("segs", 0)))
            self.ended = True
        if "error" in ev:
            self.err = int(ev.get("errno") or EIO)

    def _path(self, k):
        return "%s/m%d.%d" % (SPOOL, self.rid, k)

    def _drop_seg(self):
        f, self._f = self._f, None
        if f is not None:
            f.close()
        try:
            os.remove(self._path(self._seg))
        except OSError:
            pass
        self._seg += 1
        self._pos = 0

    def readinto(self, buf):
        while True:
            path = self._path(self._seg)
            try:
                have = os.stat(path)[6] - self._pos
            except OSError:
                have = 0
            if have > 0:
                if self._f is None:
                    self._f = open(path, "rb")
                mv = memoryview(buf)
                if len(mv) > have:
                    mv = mv[:have]
                self._f.seek(self._pos)
                n = self._f.readinto(mv)
                self._pos += n
                return n
            if self._seg < self.done:
                self._drop_seg()
                continue
            if self.err is not None:
                raise OSError(self.err, "the page could not read it")
            return 0 if self.ended else None

    def close(self):
        f, self._f = self._f, None
        if f is not None:
            f.close()
        if self.link is not None:
            self.link.forget(self.rid)
            self.link.queue({"op": "drop", "id": self.rid})
            self.link = None
