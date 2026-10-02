"""Get Carts in the browser (#124): the page's fetch behind `ws.cart_net`, the
OPFS keeper behind `ws.cart_keep` and the page's file picker behind
`ws.cart_pick` -- what a board does with a socket and its own store, done by
the worker for this VM. Only a page that keeps its own carts (site mode) gets
them; a page a board serves shows the board's carts, and the board gets its
own (web_boot sets `ws.cart_home` instead).

SHAPE: gpio_link's and update_link's. A capability reaches this VM by
QUEUEING on the Python side while the worker does the I/O and hands the answer
back between frames, through one poll (`poll_json`, the worker's carts pump)
and one way back (`event_json`). The difference is that nothing here blocks a
frame waiting for an answer: runtime/cart_index.py's jobs give the frame back
while an answer is on its way (its docstring's non-blocking transport), which
is what a VM with no ASYNCIFY needs -- the worker cannot deliver a byte while
Python is running.

THE BYTES NEVER CROSS AS PYTHON OBJECTS. The worker streams a response body
into a spool file in the VFS (`SPOOL/<id>`), chunk by chunk as fetch hands
them over, and an answer here reads that file as far as it has grown. The VFS
keeps a file's bytes in the page's own memory, outside the VM's 16 MB heap,
so a 2 MB cart costs this heap the install pipeline's two fixed buffers and
nothing per byte; the worker deletes the spool when the answer is closed.
The same holds for a picked file (`SPOOL/pick-<id>`) and for a keeper commit,
which the worker reads out of the VFS staging folder by path.

Events the worker sends back (`event_json`), each a JSON object:
    {"id": n, "status": s}        a response head (0: no answer at all -- a
                                  host this page may not read, or no network;
                                  206 for a request with a range)
    {"id": n, "end": 1}           the body is all in the spool
    {"id": n, "error": "..."}     the fetch, the keep or the pick failed
    {"id": n, "kept": 1}          the keeper's commit is durable
    {"id": n, "full": 1, ...}     ...with "error": the store had no room
    {"id": n, "picked": path}     the player's own file, in the VFS
    {"id": n, "cancel": 1}        the player closed the picker
    {"room": [usage, quota]}      the browser's own estimate of its store
"""

import json

try:
    import os
except ImportError:  # pragma: no cover
    os = None

SPOOL = "/moy/net"


class CartsLink:
    """The queue to the worker and the answers coming back, by request id."""

    def __init__(self, wake=None, landed=None):
        self.wake = wake            # an answer came: draw the next frame
        self.landed_hook = landed   # a kept folder moved into the carts folder
        self.room = None            # (usage, quota) as the page last said
        self._jobs = []
        self._live = {}
        self._next = 1

    def _id(self):
        n = self._next
        self._next += 1
        return n

    def queue(self, job):
        self._jobs.append(job)

    def poll_json(self):
        """What the worker should start or stop, or "" -- asked by its pump."""
        if not self._jobs:
            return ""
        jobs, self._jobs = self._jobs, []
        return json.dumps(jobs)

    def event_json(self, text):
        """One answer from the worker."""
        try:
            ev = json.loads(text)
        except ValueError:
            return ""
        if not isinstance(ev, dict):
            return ""
        room = ev.get("room")
        if isinstance(room, list) and len(room) == 2:
            self.room = (room[0], room[1])
        who = self._live.get(ev.get("id"))
        if who is not None:
            who.hear(ev)
        if self.wake is not None:
            self.wake()
        return ""

    def forget(self, rid):
        self._live.pop(rid, None)

    # -- ws.cart_pick ------------------------------------------------------------

    def pick(self, name, size, host):
        p = _Pick(self, self._id())
        self._live[p.rid] = p
        self.queue({"op": "pick", "id": p.rid, "name": name, "size": size,
                    "host": host})
        return p


class _Answer:
    """One response, read from the spool the worker fills: the transport
    answer runtime/cart_index.py reads, non-blocking."""

    def __init__(self, link, rid):
        self.link = link
        self.rid = rid
        self.path = SPOOL + "/%d" % rid
        self.status = None
        self.length = None          # a cross-origin page cannot see the encoding
        self.ended = False
        self.error = None
        self._f = None
        self._pos = 0

    def hear(self, ev):
        if "status" in ev:
            self.status = ev["status"]
        if ev.get("end"):
            self.ended = True
        if "error" in ev:
            self.error = str(ev["error"])
            if self.status is None:
                self.status = 0

    def _have(self):
        try:
            return os.stat(self.path)[6] - self._pos
        except OSError:
            return 0

    def ready(self, n):
        if self.error is not None or self.ended:
            return True
        return self.status is not None and self._have() >= n

    def readinto(self, buf):
        if self.error is not None:
            raise OSError(self.error)
        have = self._have()
        if have <= 0:
            return 0 if self.ended else None
        if self._f is None:
            self._f = open(self.path, "rb")
        mv = memoryview(buf)
        if len(mv) > have:
            mv = mv[:have]
        self._f.seek(self._pos)
        n = self._f.readinto(mv)
        self._pos += n
        return n

    def close(self):
        f, self._f = self._f, None
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
        if self.link is not None:
            self.link.forget(self.rid)
            self.link.queue({"op": "drop", "id": self.rid})
            self.link = None


class WebCartNet:
    """`ws.cart_net` in a page that keeps its own carts: the page's fetch.

    `cors` because a page reads only what a server lets another origin read,
    which is why cart_index reads an asset's mirror first here. `ranges`
    because a fetch can ask for part of a file (`span`, a single Range a page
    may send any host without asking first), which is how this console --
    which keeps no compiled module -- reads a release asset without its
    modules. The page is online by the browser's lights, so `online()` has
    nothing to dial; a fetch that cannot reach anything answers status 0, and
    the store says it could not reach the shelf."""

    cors = True
    ranges = True

    def __init__(self, link):
        self.link = link

    def online(self):
        return True

    def open(self, url, span=None):
        a = _Answer(self.link, self.link._id())
        self.link._live[a.rid] = a
        job = {"op": "get", "id": a.rid, "url": url}
        if span is not None:
            job["range"] = [span[0], span[0] + span[1] - 1]
        self.link.queue(job)
        return a


class _Commit:
    def __init__(self, rid, done):
        self.rid = rid
        self.done = done

    def hear(self, ev):
        if ev.get("kept"):
            self.done("", False)
        elif "error" in ev:
            self.done(str(ev["error"]) or "the browser would not keep it",
                      bool(ev.get("full")))


class WebCartKeep:
    """`ws.cart_keep`: the browser's OPFS is the store of record, and the
    worker's moy_store.commitInstall is what makes an install durable there --
    the staged folder and the record together, behind one marker file, so a
    reload at any moment finds the old cart or the new one."""

    def __init__(self, link):
        self.link = link

    def commit(self, folder, stage, record, done):
        rid = self.link._id()
        c = _Commit(rid, None)

        def _done(why, full):
            self.link.forget(rid)
            done(why, full)
        c.done = _done
        self.link._live[rid] = c
        self.link.queue({"op": "keep", "id": rid, "folder": folder, "stage": stage,
                         "record": record})

    def landed(self, folder):
        hook = self.link.landed_hook
        if hook is not None:
            hook(folder)

    def record(self, text):
        self.link.queue({"op": "record", "record": text})

    def free(self):
        room = self.link.room
        if room is None or room[0] is None or room[1] is None:
            return None
        return max(0, room[1] - room[0]), 1


class _Pick:
    """A question to the player: their own copy of one file. `poll()` is
    None until they answer, then ("file", path) or ("cancel",)."""

    def __init__(self, link, rid):
        self.link = link
        self.rid = rid
        self.answer = None

    def hear(self, ev):
        if ev.get("picked"):
            self.answer = ("file", str(ev["picked"]))
        elif ev.get("cancel") or "error" in ev:
            self.answer = ("cancel",)

    def poll(self):
        return self.answer

    def close(self):
        if self.link is not None:
            self.link.forget(self.rid)
            self.link.queue({"op": "unpick", "id": self.rid})
            self.link = None
