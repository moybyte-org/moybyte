"""Get Carts in the browser (#124): the page's fetch behind `ws.cart_net`, the
OPFS keeper behind `ws.cart_keep` (which keeps compiled carts' written files
too) and the page's file picker behind `ws.cart_pick` -- what a board does with a socket and its own store, done by
the worker for this VM. Only a page that keeps its own carts (site mode) gets
them; a page a board serves shows the board's carts, and the board gets its
own (web_boot sets `ws.cart_home` instead). A board with no screen (a Zero)
gets its own when this page asks: `ws.cart_board` (`BoardCarts`) asks it
through its sealed /official for the carts of its indexes, a licence, an
install (whose prompt this page answers) or a removal, and once the board has
written a cart, pulls that cart's files into this page's copy of the store.

SHAPE: gpio_link's and update_link's. A capability reaches this VM by
QUEUEING on the Python side while the worker does the I/O and hands the answer
back between frames, through one poll (`poll_json`, the worker's carts pump)
and one way back (`event_json`). The difference is that nothing here blocks a
frame waiting for an answer: runtime/carts/cart_index.py's jobs give the frame back
while an answer is on its way (its docstring's non-blocking transport), which
is what a VM with no ASYNCIFY needs -- the worker cannot deliver a byte while
Python is running.

THE BYTES NEVER CROSS AS PYTHON OBJECTS. The worker streams a response body
into the spool file its job names (`path`: `SPOOL/<id>`, web_vfs), chunk by
chunk as fetch hands them over, and an answer here reads that file as far as
it has grown. The VFS keeps a file's bytes in the page's own memory, outside
the VM's 16 MB heap, so a 2 MB cart costs this heap the install pipeline's
two fixed buffers and nothing per byte; the worker deletes the spool when
the answer is closed. The same holds for a picked file (the pick job's
`path`, `SPOOL/pick-<id>`) and for a keeper commit, which the worker reads
out of the VFS staging folder by path.

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
    {"id": n, "status": s} and {"id": n, "end": 1}   /official's answer, in its spool
    {"id": n, "kept": 1}          a refold: the cart's files are this page's
"""

import json

try:
    import os
except ImportError:  # pragma: no cover
    os = None

from moy_store_base import cart_path as _cart_path
from web_vfs import SPOOL
from ticks import _ticks_ms, _ticks_diff

try:
    import moycore as _moycore
except ImportError:  # pragma: no cover -- a build without the module
    _moycore = None


class CartsLink:
    """The queue to the worker and the answers coming back, by request id."""

    def __init__(self, wake=None, landed=None):
        self.wake = wake            # an answer came: draw the next frame
        self.landed_hook = landed   # a kept folder moved into the carts folder
        self.room = None            # (usage, quota) as the page last said
        self._jobs = []
        self._live = {}
        self._next = 1
        self.files = False          # moycore logs a compiled cart's writes

    def _id(self):
        n = self._next
        self._next += 1
        return n

    def queue(self, job):
        self._jobs.append(job)

    def poll_json(self):
        """What the worker should start or stop, or "" -- asked by its pump."""
        if self.files:
            # A compiled cart's writes and erases, made in moycore's C.
            for op, cart, key in _moycore.files_kept():
                self._jobs.append({"op": op, "cart": cart, "key": key})
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
            if self.files and room[0] is not None and room[1] is not None:
                _moycore.files_room(room[0], room[1])
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
                    "host": host, "path": SPOOL + "/pick-%d" % p.rid})
        return p


class _Answer:
    """One response, read from the spool the worker fills: the transport
    answer runtime/carts/cart_index.py reads, non-blocking."""

    def __init__(self, link, rid):
        self.link = link
        self.rid = rid
        self.path = SPOOL + "/%d" % rid       # the get job names it
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
        job = {"op": "get", "id": a.rid, "url": url, "path": a.path}
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
        # A compiled cart's written files are moycore's C, which logs each
        # write and erase for the pump to hand over (`poll_json`) and refuses
        # a write past the room the page last reported.
        if _moycore is not None and hasattr(_moycore, "files_keep"):
            _moycore.files_keep(True)
            link.files = True

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

    # -- a compiled cart's written files ----------------------------------------
    # The console writes the VFS (moycore's C, whose log the pump drains); the
    # worker makes each file durable in OPFS (moy_store's commitWritten) in the
    # order they were written, and drops one, or with `key` None every file a
    # removed cart wrote (runtime/carts/cart_index.py's remove).

    def erased(self, cart, key):
        self.link.queue({"op": "wdel", "cart": cart, "key": key})


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


# -- ws.cart_board: the official carts of a board with no screen ----------------

POLL_MS = 400                   # how often a request in hand asks where it stands
POLL_LOST = 25                  # unanswered asks in a row before the board is lost
LOST = "The console isn't answering."


class BoardCarts:
    """`ws.cart_board` on a page a console with no screen serves (a Zero),
    whose webhost answers /official (device/moy_webhost.py,
    runtime/carts/web_addon.py's OfficialCarts): the carts of the indexes
    that console reads, which it installs and removes in its own store when
    this page asks. The page asks and waits; the board fetches and writes,
    the one writer of its carts.

    Each verb is a job the app steps a frame at a time (`BoardAsk`). An
    install ends by REFOLDING the cart: the worker pulls the board's carts
    again and replaces this page's copy of that one folder, and the watcher
    adopts it (`landed`), so nothing the board just wrote travels back as an
    edit; a removal deletes this page's copy the same way."""

    def __init__(self, link, root):
        self.link = link
        self.root = root

    def check(self):
        return BoardAsk(self, "check", None)

    def licence(self, cart, path):
        """The licence text of `cart`'s external file `path`, fetched and
        checked by the board: the job's `doc["text"]`."""
        return BoardAsk(self, "licence", cart["folder"], _which(cart, path=path))

    def install(self, cart, accepted=(), ask=None):
        """`cart` (a listing the board's check gave) installed by the board,
        the external files whose licence was `accepted` with it. `ask(man)`
        is the install prompt's check of the checked build's manifest
        (pkg.manifest_check): None goes on, lines are the question
        (`asking`), an InstallError refuses."""
        return BoardAsk(self, "install", cart["folder"],
                        _which(cart, accepted=list(accepted)), ask)

    def remove(self, folder):
        return BoardAsk(self, "remove", folder)

    def ask(self, doc=None):
        """An answer (`_Answer`) to /official: a POST of `doc`, or a GET."""
        a = _Answer(self.link, self.link._id())
        self.link._live[a.rid] = a
        self.link.queue({"op": "official", "id": a.rid, "path": a.path,
                         "body": None if doc is None else json.dumps(doc)})
        return a

    def refold(self, folder):
        """The board's copy of `folder` pulled over this page's: a job whose
        `kept` (True, or the error's text) says when it is done."""
        r = _Refold(self, folder)
        self.link._live[r.rid] = r
        self.link.queue({"op": "refold", "id": r.rid, "root": self.root, "folder": folder})
        return r

    def drop(self, folder):
        """This page's copy of `folder` deleted and adopted: the board took
        its own off."""
        import cart_index
        path = _cart_path(self.root, folder)
        for _ in cart_index.rmtree_steps(path):
            pass
        self.landed(folder)

    def landed(self, folder):
        hook = self.link.landed_hook
        if hook is not None:
            hook(folder)


class _Refold:
    def __init__(self, board, folder):
        self.board = board
        self.folder = folder
        self.rid = board.link._id()
        self.kept = None

    def hear(self, ev):
        if ev.get("kept"):
            self.board.link.forget(self.rid)
            self.board.landed(self.folder)
            self.kept = True
        elif "error" in ev:
            self.board.link.forget(self.rid)
            self.kept = str(ev["error"]) or "the cart did not come"


def _which(cart, **more):
    """The POST's members naming `cart`: its index and id, and `more`."""
    out = {"index": cart.get("index"), "id": cart.get("id")}
    out.update(more)
    return out


def _read_answer(a):
    """(status, document or None) of an ended answer, which it closes."""
    try:
        with open(a.path, "rb") as f:
            text = f.read()
        doc = json.loads(text.decode("utf-8")) if text else None
    except (OSError, ValueError):
        doc = None
    status = a.status if a.error is None else 0
    a.close()
    return status, doc if isinstance(doc, dict) else None


class BoardAsk:
    """One request to the board, stepped a frame at a time: the POST, then a
    GET every POLL_MS until the board says it is done, then (an install's or
    a removal's) this page's copy brought to the board's. The face
    cart_index.Install shows Get Carts: `step()` True while it runs, `done`
    and `total` the board's download, `asking` the install prompt's question
    until `agree()` or `cancel()`, `path` the cart's folder here once it
    landed, `error` the board's reason; and `doc`, a check's list or a
    licence's text."""

    keeping = False

    def __init__(self, board, action, folder, more=None, ask=None):
        self.board = board
        self.action = action
        self.folder = folder
        self.ask = ask
        self.asking = None
        self.doc = None
        self.error = None
        self.path = None
        self.done = 0
        self.total = 0
        self.finished = False
        self.dropping = False
        self._at = None
        self._lost = 0
        self._refold = None
        self._refused = None
        body = {"action": action}
        if folder is not None:
            body["folder"] = folder
        body.update(more or {})
        self._answer = board.ask(body)
        self._posting = True

    @property
    def landing(self):
        """The board has it, and this page's copy is coming."""
        return self._refold is not None and not self.finished

    def rate(self):
        return 0

    def cancel(self):
        """Ask the board to stop an install: False once it has landed there
        (this page's copy is being brought over) or for a check or removal
        that is past asking."""
        if self.finished or self._refold is not None or self.action != "install":
            return False
        if not self.dropping:
            self.dropping = True
            self.asking = None
            self._post({"action": "cancel"})
        return True

    def agree(self):
        """The install prompt answered yes: the board lands the build."""
        self.asking = None
        self._post({"action": "agree"})

    def _post(self, doc):
        self._drop_answer()
        self._answer = self.board.ask(doc)
        self._posting = True

    def finish(self):
        pass

    def close(self):
        """Let it go unfinished: the board goes on with what it was asked."""
        if not self.finished:
            self._end(None)

    def _drop_answer(self):
        a, self._answer = self._answer, None
        if a is not None:
            a.close()

    def _end(self, error=None):
        self._drop_answer()
        self.error = error
        self.finished = True
        return False

    def step(self, _budget=None):
        if self.finished:
            return False
        if self.asking is not None:
            return True                         # the player answers; the board waits
        if self._refold is not None:
            kept = self._refold.kept
            if kept is None:
                return True
            if kept is True:
                self.path = _cart_path(self.board.root, self.folder)
                return self._end(None)
            return self._end(kept)
        a = self._answer
        if a is None:
            if self._at is not None and _ticks_diff(_ticks_ms(), self._at) < POLL_MS:
                return True
            self._answer = self.board.ask()
            self._posting = False
            return True
        if not (a.ended or a.error is not None):
            return True
        self._answer = None
        posted = self._posting
        status, doc = _read_answer(a)
        self._at = _ticks_ms()
        if self.dropping and posted:
            return self._end(self._refused)     # the board stops; nothing landed
        if posted and status == 409:
            return self._end((doc or {}).get("message") or LOST)
        if status != 200 or doc is None:
            self._lost += 1
            return True if self._lost < POLL_LOST else self._end(LOST)
        self._lost = 0
        return self._heard(doc, posted)

    def _heard(self, doc, posted):
        state = doc.get("state")
        prog = doc.get("progress")
        if isinstance(prog, list) and len(prog) == 2:
            self.done, self.total = prog[0] or 0, prog[1] or 0
        if posted or state in ("checking", "installing", "removing"):
            return True
        if state == "asking" and self.action == "install":
            return self._prompt(doc.get("manifest"))
        if state == "listed" and self.action in ("check", "licence"):
            self.doc = doc
            return self._end(None)
        if state == "error":
            return self._end(doc.get("error") or LOST)
        if state == "done" and doc.get("folder") == self.folder \
                and doc.get("action") == self.action:
            if self.action == "remove":
                self.board.drop(self.folder)
                return self._end(None)
            self._refold = self.board.refold(self.folder)
            return True
        return self._end(LOST)

    def _prompt(self, manifest):
        """The board holds the checked build: the install prompt's check of
        its manifest, asked once. Nothing to ask goes on; a refusal stops
        the board with its reason."""
        if self.asking is not None:
            return True
        lines = None
        if self.ask is not None:
            try:
                lines = self.ask(manifest if isinstance(manifest, dict) else {})
            except Exception as exc:  # noqa: BLE001 -- cart_index.InstallError
                self._refused = getattr(exc, "text", None) or LOST
                self.cancel()
                return True
            self.ask = None
        if lines is None:
            self._post({"action": "agree"})
            return True
        self.asking = lines
        return True
