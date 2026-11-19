# Map (grep -n a name to jump there):
#   size_text        a byte count as a person reads it
#   about            a time left as a person reads it
#   safe_name        a received file's name made safe for the store
#   free_name        a name no file in the folder holds yet
#   rel_ok           a path in a beam's head: relative, no empty, . or .. part, not skipped
#   listing          a cart's files as the beam sends them, walked as moy_pack.c walks
#   head_of          the head a beam opens with: MOYB, a length, the folder and its files
#   read_head        a head's folder and files, or None for a bad one
#   Beam             the console's half of a beam: offer, accept, stream, land
#   Beam.send        offer a cart, its files as they are
#   Beam.send_file   offer one file
#   Beam.receive     listen for an offer
#   Beam.stop        end the beam either way
#   Beam.stats       the link's beam counters
#   Beam.tend        one frame of the beam: one store op, the link polled
"""Beaming a file to a console nearby (docs/netplay_v1.md §7): the console's
half of the beam the C link carries (native/moy_play/moy_match.h, BEAMING A
FILE). The link moves the bytes through a ring of a fixed size and checks
them; this feeds that ring from the store and empties it into the store, asks
the person on the receiving console, puts what arrived in its place and says
every step on whatever screen is up (`Notices.notice`).

A beam is one of two things a person starts. SEND offers a cart (a card's
verb, `HomeDetails.card_verbs`, or a project's in Files) as its files, each
as it is, or one of the person's files (Files' Send, through
`intents.Intents.send`) as it is. RECEIVE NEARBY in the system menu opens the
receive state, outside which an offer is never heard. An offer the store has
no room for is refused before anything is asked or written, and its sender
told why; any other is asked about with the console's one dialog
(`DeleteConfirm.ask_close`): its title, the console it comes from and its
size.

A CART CROSSES AS ITS FILES. The bytes on the air are one stream the link
hashes whole: for a file, the file; for a cart, a HEAD (MAGIC, its length,
then JSON naming the cart's folder and each file's path and size, in the
order the store's packer walks it: `moy_carts.pack_skip`'s rule, DEPTH
folders deep) and then each file's bytes in that order, read from where they
lie. Nothing is packed and nothing unpacked: the receiver checks each path
in the head (`rel_ok`) and writes each file straight into its staged folder
(`install/beam-in/`), which lands whole, by one rename, once the hash
passes (`moy_carts.install_staged`, the adopt a dropped `.moy` lands by).

THE STORE, ONE OP A FRAME. A frame's look is one store op
(`ws.carts.store.call`: on the T-Deck that is the SD gate, which waits for
the panel's flush, so it is taken once): it reads BLOCKs into the ring while
the ring has room for one (or for a file's last bytes), or writes the BLOCKs
the ring holds in order, each at a block-aligned offset of its file, until
the ring is full or empty or STORE_MS has passed. The file being read or
written stays open from one frame to the next. A beam's RAM is the link's
ring, one BLOCK and a cart's head, whatever the size.

WHAT LANDS. Once the C's streamed hash passes, a cart's staged folder is
adopted onto the shelf as a dropped `.moy`'s is (`moy_carts.install_staged`),
so it arrived as files and its first run asks (`Notices.ask_first`,
docs/os_2026-11/processes.md §2); a file goes into the shared folder
Downloads (docs/os_2026-11/between_apps.md §2) under its own name made safe
(`safe_name`), as `name_2.ext` where that name is taken, and nothing runs
it. A beam that fails, is declined, stopped or given up leaves nothing: its
temporary goes, and every beam's start sweeps any a reset left behind (as
Get Carts' `cart_index.recover_steps` sweeps the install folder too).

The radio is held for the beam as for a match (the "link" lease and the
link's own holder); a game that takes the radio ends the beam
(`EspNowLink.start`), and a beam never starts beside one. A beam that ends
keeps the radio up PART_MS more, as its last frame (a no, a stop) is still in
the transport's send queue on a board whose radio is another chip (the
Waveshare P4's), and KEEP_MS, the link's BEAM_KEEP_MS, after a receive that
finished and after a no said to a console that offered or was bound, so a
peer that missed every DONE or NO hears it at its next frame.
"""

import json

import moy_carts as _carts
from moy_store_base import _is_dir, _rmtree, _sibling_path, files_root, store_path
from moy_fs import _exists, _mkdir, _remove

try:
    import os
except ImportError:  # pragma: no cover
    os = None

SEND, RECEIVE = 1, 2
OFFERING, LISTENING, OFFERED, MOVING, DONE, FAILED = 2, 3, 4, 5, 6, 7
NOBODY, GONE, DECLINED, STOPPED, DAMAGED, MATCH, ROOM = 1, 2, 3, 4, 5, 6, 7
CART, FILE = 1, 2                 # moy_match.h's MOY_BEAM_CART, _FILE
WAIT_MS = 60000                   # ...its MOY_MATCH_BEAM_WAIT_MS
KEEP_MS = 3000                    # ...its MOY_MATCH_BEAM_KEEP_MS
PART_MS = 300                     # the radio stays up this long after a beam ends
BLOCK = 8192                      # one read or write, at a block-aligned offset
STORE_MS = 24                     # a frame's store op moves bytes for at most this long
SAY_MS = 1000                     # the progress line, at most this often
RESERVE = 65536                   # free space an arrival leaves the store
IN = "beam-in"                    # an arrival on its way, in the install folder
MAGIC = b"MOYB"                   # a cart's stream: MAGIC, the head's length (4), its JSON
HEAD_MAX = 65536                  # a cart's head, at most
DEPTH = 6                         # folders under a cart's, as the store's packer walks
ARRIVALS = "Downloads"            # where a received file lands, under files/
NAME_MAX = 64
ROW = "Receive nearby"            # the system menu's row (shell_cards._BEAM_ROW)
_ANSWERS = ("No", "Receive")

# What a failed beam says, by its why: (title, line) for the sender and the
# receiver, {t} the item's title and {p} the other console, each cut short
# so a line fits the T-Deck's notice (tests/test_link_beam.py holds them).
_SEND_WHY = {
    NOBODY: ("CAN'T SEND", "No console took {t}"),
    GONE: ("SEND FAILED", "{p} stopped answering"),
    DECLINED: ("NOT SENT", "{p} said no"),
    STOPPED: ("SEND FAILED", "{p} stopped receiving"),
    DAMAGED: ("SEND FAILED", "It arrived damaged"),
    MATCH: ("SEND STOPPED", "A match began"),
    ROOM: ("NOT SENT", "{p} has no room for it"),
}
_RECEIVE_WHY = {
    NOBODY: ("NOTHING CAME", "No console sent anything"),
    GONE: ("RECEIVE FAILED", "{p} stopped answering"),
    STOPPED: ("RECEIVE FAILED", "{p} stopped sending"),
    DAMAGED: ("RECEIVE FAILED", "Arrived damaged: nothing kept"),
    MATCH: ("RECEIVE STOPPED", "A match began"),
    ROOM: ("RECEIVE FAILED", "Not enough memory free"),
}


def _say(ws, title, line="", kind="ok", ms=6000):
    notices = getattr(ws, "notices", None)
    if notices is not None:
        notices.notice(title, line, kind, ms)


def _withdraw(ws, title):
    notices = getattr(ws, "notices", None)
    if notices is not None and hasattr(notices, "withdraw"):
        notices.withdraw(title)


def _short(s, n=16, none="this"):
    return str(s or none)[:n]


def _peer(p):
    return _short(p, 12, "The console")


def size_text(n):
    """`n` bytes as a person reads them."""
    if n < 1024:
        return "%d B" % n
    if n < 1 << 20:
        return "%d KB" % (n >> 10)
    if n < 100 << 20:
        return "%d.%d MB" % (n >> 20, ((n * 10) >> 20) % 10)
    if n < 1 << 30:
        return "%d MB" % (n >> 20)
    return "%d.%d GB" % (n >> 30, ((n * 10) >> 30) % 10)


def about(ms):
    """A time left, no more precise than a person can use."""
    s = (int(ms) + 999) // 1000
    if s < 10:
        return "about %d s" % max(1, s)
    if s < 60:
        return "about %d s" % ((s + 4) // 5 * 5)
    m = (s + 30) // 60
    if m < 90:
        return "about %d min" % m
    return "about %d h" % ((m + 30) // 60)


def safe_name(name):
    """A received file's name as it may land: its last part only, no control
    or reserved characters, no leading dot (nothing hidden, no `..`), no
    suffix the store hides as its own (`.bak`, `.tmp`), at most NAME_MAX
    characters with its extension kept."""
    n = str(name or "")
    n = n[max(n.rfind("/"), n.rfind("\\")) + 1:]
    out = ""
    for c in n:
        out += "_" if ord(c) < 32 or ord(c) == 127 or c in '<>:"|?*' else c
    out = out.strip().lstrip(".").strip()
    if out.endswith(".bak") or out.endswith(".tmp"):
        out += "_"
    if len(out) > NAME_MAX:
        dot = out.rfind(".")
        ext = out[dot:] if 0 < dot and len(out) - dot <= 10 else ""
        out = out[:NAME_MAX - len(ext)] + ext
    return out or "file"


def free_name(folder, name):
    """`name` in `folder`, else the first free `stem_2.ext`, `stem_3.ext`...:
    an arrival never takes the place of a file already there."""
    if not _exists(folder + "/" + name):
        return name
    dot = name.rfind(".")
    stem, ext = (name[:dot], name[dot:]) if dot > 0 else (name, "")
    i = 2
    while _exists("%s/%s_%d%s" % (folder, stem, i, ext)):
        i += 1
    return "%s_%d%s" % (stem, i, ext)


def free_bytes(root):
    """The store's free space at `root`, or None where it cannot say."""
    try:
        st = os.statvfs(root)
        return st[0] * st[4]
    except (OSError, AttributeError, TypeError):
        return None


def rel_ok(rel):
    """Whether `rel` may name a file in a cart's staged folder: no empty,
    `.` or `..` part, nothing absolute, no backslash, nothing the store's
    packer skips, at most DEPTH folders deep."""
    if type(rel) is not str or not rel or "\\" in rel or rel[0] == "/":
        return False
    parts = rel.split("/")
    if len(parts) > DEPTH + 1:
        return False
    for s in parts:
        if not s or s == "." or s == ".." or _carts.pack_skip(s):
            return False
    return True


def listing(path):
    """The cart folder at `path` as the store's packer takes it: (its files'
    paths under it, each size), in name order, DEPTH folders deep."""
    out = []

    def walk(d, rel, depth):
        if depth > DEPTH:
            return
        for n in sorted(os.listdir(d)):
            if _carts.pack_skip(n):
                continue
            p = d + "/" + n
            st = os.stat(p)
            if st[0] & 0x4000:
                walk(p, rel + n + "/", depth + 1)
            else:
                out.append((rel + n, st[6]))
    walk(path, "", 0)
    return out


def head_of(folder, files):
    """A cart's stream head: MAGIC, the JSON's length (4, little-endian),
    the JSON naming `folder` and `files` ((path, size) pairs)."""
    body = json.dumps({"folder": folder, "files": [[r, n] for r, n in files]}).encode()
    return MAGIC + len(body).to_bytes(4, "little") + body


def read_head(head, size):
    """A received head (the whole of it) as (folder, files), or None where it
    is not one: a bad path, a size that is not a count, or files and head
    together not the stream's `size`."""
    try:
        d = json.loads(bytes(head[8:]))
        folder, files = d["folder"], d["files"]
    except (ValueError, KeyError, TypeError):
        return None
    if type(folder) is not str or type(files) is not list or not rel_ok(folder) \
            or "/" in folder:
        return None
    total = len(head)
    out = []
    for f in files:
        if type(f) is not list or len(f) != 2 or not rel_ok(f[0]) \
                or type(f[1]) is not int or f[1] < 0:
            return None
        out.append((f[0], f[1]))
        total += f[1]
    return (folder, out) if total == size else None


def _op(ws, fn):
    """`fn` as one store op (the SD gate on the T-Deck); None where it failed."""
    try:
        return ws.carts.store.call(fn)
    except Exception as exc:  # noqa: BLE001 -- a store that refuses is a message
        print("Moybyte beam:", exc)
        return None


def _stage(ws):
    return _sibling_path(ws.carts_root, "install")


def _gone(path):
    """The file or folder at `path` removed, where there is one."""
    if _is_dir(path):
        _rmtree(path)
    else:
        _remove(path)


class Beam:
    """The console's side of one link's beams: what it holds, the block
    between the ring and the store, and what the person was asked."""

    def __init__(self, link):
        self.link = link
        self.held = False           # this beam holds the radio
        self.parting = None         # the tick a beam let go at, its radio still up
        self.part_ms = PART_MS
        self.buf = bytearray(BLOCK)
        self.mv = memoryview(self.buf)
        self.fh = None              # the file being read or written, open across frames
        self._clear()

    def _clear(self):
        self.answer = None          # the person's answer to the offer held
        self.asked = None           # the question as the dialog titles it
        self.what = FILE
        self.tmp = None             # receive: the temporary (a file's, a cart's folder)
        self.head = b""             # a cart's head: built (send), as it arrives (receive)
        self.files = None           # (path, size) of each file the stream carries, in order
        self.k = 0                  # ...the one being read or written
        self.off = 0                # ...and the bytes of it done
        self.folder = None          # receive: the cart's folder name, from its head
        self.bad = False            # receive: what came is no cart's head
        self.size = 0
        self.pos = 0                # bytes of the stream read (send) or written (receive)
        self.roomy = False          # receive: the offer's room was checked
        self.t0 = self.d0 = None    # the progress's first look: tick and bytes
        self.said_at = None
        self.line = None            # the progress last said
        self.bps = None             # the measured rate, bytes/s
        self.left_ms = None

    # -- the two starts and the stop --
    def role(self):
        """SEND or RECEIVE while a beam is under way, else None."""
        m = self.link._m
        info = m.beam_info() if m is not None else None
        return None if info is None or info[1] in (DONE, FAILED) else info[0]

    def _refusal(self, ws):
        if self.link._game or getattr(ws, "netplay", None) is not None:
            return "A game is using the radio"
        if getattr(ws, "carts_root", None) is None:
            return "This console has no store"
        return None

    def _hold(self, ws):
        """The radio up for the beam: the station's lease, then the link."""
        spine = getattr(ws, "spine", None)
        if spine is not None:
            spine.wifi_hold("link")
        self.link._beaming = True
        self.held = True
        self.parting = None
        self.part_ms = PART_MS
        if self.link._up():
            if not self.link._game:
                self.link.phy(True)
            return True
        self._let_go(ws, True)
        return False

    def _let_go(self, ws, now=False):
        """The beam's hold over: the radio goes down `part_ms` from here (at
        once with `now`: the radio never came up)."""
        self.held = False
        self._clear()
        if now:
            self._part(ws)
        else:
            self.parting = self.link._ticks_ms()

    def _part(self, ws):
        """The radio let go, unless a game or a port took it meanwhile."""
        link = self.link
        self.parting = None
        link._beaming = False
        link.phy(False)
        if link._game:
            return
        if link._m is None or not link._m.ports_open():
            link._down()
        spine = getattr(ws, "spine", None)
        if spine is not None:
            spine.wifi_release("link")

    def _sweep(self, ws):
        """The install folder's beam temporaries gone (one a reset left)."""
        stage = _stage(ws)

        def _go():
            _mkdir(stage)
            _gone(stage + "/" + IN)
            return True
        return _op(ws, _go)

    def _start(self, ws, verb):
        """The common refusals and the hold: None, or why it cannot start."""
        why = self._refusal(ws)
        if why is None and not self.link.usable():
            why = "This console has no radio link"
        if why is None and not self._hold(ws):
            why = "This console has no radio link"
        if why is not None:
            _say(ws, "CAN'T " + verb, why, "warn")
        return why

    def send(self, ws, cart):
        """Offer `cart` (a cart dict with its folder's `path`), as its files,
        to whichever console nearby is receiving. False where it could not
        start, said."""
        cart = cart or {}
        path = str(cart.get("path") or "").rstrip("/")
        if not path:
            _say(ws, "CAN'T SEND", "Nothing to send", "warn")
            return False
        if self._start(ws, "SEND") is not None:
            return False
        title = _short(cart.get("title"))
        self._sweep(ws)
        files = _op(ws, lambda: listing(path))
        if not files:
            self._let_go(ws)
            _say(ws, "CAN'T SEND", "%s can't be read" % title, "warn")
            return False
        head = head_of(path[path.rfind("/") + 1:], files)
        size = len(head)
        for _r, n in files:
            size += n
        return self._offer(ws, CART, cart.get("title") or title, size, head,
                           [(path + "/" + r, n) for r, n in files])

    def send_file(self, ws, path):
        """Offer the person's file at `path` as it is. False where it could
        not start, said."""
        name = str(path or "")
        name = name[name.rfind("/") + 1:]
        if not name:
            _say(ws, "CAN'T SEND", "Nothing to send", "warn")
            return False
        if self._start(ws, "SEND") is not None:
            return False
        self._sweep(ws)
        size = _op(ws, lambda: os.stat(path)[6])
        if not size:
            self._let_go(ws)
            _say(ws, "CAN'T SEND", "%s can't be read" % _short(name), "warn")
            return False
        return self._offer(ws, FILE, name, size, b"", [(path, size)])

    def _offer(self, ws, what, title, size, head, files):
        m = self.link._m
        rc = m.beam_send(title, what, size, self.link._ticks_ms())
        if rc != 0:
            m.beam_end()
            self._let_go(ws)
            _say(ws, "CAN'T SEND", "%s can't be sent" % _short(title), "warn")
            return False
        self.what, self.size, self.pos = what, size, 0
        self.head, self.files, self.k, self.off = head, files, 0, 0
        _say(ws, "Sending %s" % _short(title), "Other console: " + ROW, ms=WAIT_MS)
        return True

    def receive(self, ws):
        """The receive state opened: the first offer heard is asked about."""
        if self._start(ws, "RECEIVE") is not None:
            return False
        self._sweep(ws)
        if self.link._m.beam_listen(self.link._ticks_ms()) != 0:
            self._let_go(ws)
            _say(ws, "CAN'T RECEIVE", "This console has no radio link", "warn")
            return False
        _say(ws, "Receiving", "Waiting for a console to send", ms=WAIT_MS)
        return True

    def stop(self, ws):
        """The beam under way ended by its person; the other console is told."""
        m = self.link._m
        info = m.beam_info() if m is not None else None
        self._end(ws)
        if info is not None:
            _say(ws, "Stopped sending" if info[0] == SEND else "Stopped receiving")

    def stats(self):
        """What the on-glass phase reads of the beam under way: DATA frames
        sent, sent again and refused, the pace (chunks/s), the measured
        bytes/s, the bytes across and the size. None with no beam."""
        m = self.link._m
        info = m.beam_info() if m is not None else None
        if info is None:
            return None
        return {"sent": info[8], "resent": info[9], "refused": info[10], "rate": info[11],
                "bps": self.bps, "done": info[6], "size": info[5], "ram": info[12]}

    # -- the frame --
    def tend(self, ws):
        """One frame's look at the beam: the question, a block, the progress,
        the end."""
        m = self.link._m
        info = m.beam_info() if m is not None else None
        if info is None:
            if self.held:
                self._close_question(ws)
                self._discard(ws)
                self._let_go(ws, self.link._game)
                if self.link._game:
                    _say(ws, "BEAM STOPPED", "A game is using the radio", "warn")
            elif self.parting is not None and (
                    self.link._game
                    or not 0 <= self.link._ticks_ms() - self.parting < self.part_ms):
                self._part(ws)
            return
        role, state, why, title, peer, size, done, what = info[:8]
        short = _short(title)
        peer = _peer(peer)
        if state == OFFERED:
            if not self.roomy:
                self.roomy = True
                need = size + RESERVE
                free = _op(ws, lambda: free_bytes(ws.carts_root))
                if free is not None and free < need:
                    m.beam_refuse(ROOM)
                    self.part_ms = KEEP_MS
                    self._end(ws)
                    _say(ws, "NO ROOM", "%s needs %s" % (short, size_text(need)), "warn")
                    return
            self._ask(ws, short, peer, size, what)
            return
        self._close_question(ws)
        if state == MOVING:
            if not (self._feed(ws, m) if role == SEND else self._drain(ws, m, size)):
                bad = self.bad
                self._end(ws)
                _say(ws, "SEND FAILED" if role == SEND else "RECEIVE FAILED",
                     _RECEIVE_WHY[DAMAGED][1] if bad else "The store failed", "warn")
                return
            self._progress(ws, role, short, size, done)
            return
        if state == DONE:
            if role == SEND:
                _say(ws, "Sent %s" % short, "%s has it now" % peer)
            else:
                self._land(ws, what, short, peer, title, size)
            self._end(ws)
        elif state == FAILED:
            words = (_SEND_WHY if role == SEND else _RECEIVE_WHY).get(why)
            self._end(ws)
            if words is not None:
                _say(ws, words[0], words[1].format(t=short, p=peer), "warn")

    def _next(self):
        """The bytes the stream's next piece is: the head's next BLOCK, else
        the next BLOCK of the file at `k` (its last bytes at its end), past
        every file of none. 0 once the stream is done."""
        if self.pos < len(self.head):
            return min(BLOCK, len(self.head) - self.pos)
        files = self.files
        while self.k < len(files) and self.off >= files[self.k][1]:
            self.k += 1
            self.off = 0
        if self.k >= len(files):
            return 0
        return min(BLOCK, files[self.k][1] - self.off)

    def _file(self, mode):
        """The file at `k`, open: opened (the one before closed) where it is
        not the one open."""
        fh = self.fh
        if fh is not None and fh[0] == self.k:
            return fh[1]
        self._shut()
        f = open(self.files[self.k][0], mode)
        self.fh = (self.k, f)
        return f

    def _shut(self):
        """The open file closed, where one is."""
        fh, self.fh = self.fh, None
        if fh is not None:
            fh[1].close()

    def _moved(self, n):
        self.pos += n
        if self.pos > len(self.head) and self.files is not None:
            self.off += n
            if self.off >= self.files[self.k][1]:
                self._shut()

    def _feed(self, ws, m):
        """The ring filled from the stream, BLOCK by BLOCK, while it has room
        for the next piece and STORE_MS has not passed: one store op."""
        n = self._next()
        if n == 0 or m.beam_room() < n:
            return True
        clock = self.link._ticks_ms
        t0 = clock()

        def _read():
            n = self._next()
            while n and m.beam_room() >= n and 0 <= clock() - t0 < STORE_MS:
                if self.pos < len(self.head):
                    got = m.beam_fill(self.head[self.pos:self.pos + n], n, clock())
                else:
                    if self._file("rb").readinto(self.mv[:n]) != n:
                        return False        # the file shrank since its listing
                    got = m.beam_fill(self.buf, n, clock())
                if got != n:
                    return False
                self._moved(n)
                n = self._next()
            return True
        return bool(_op(ws, _read))

    def _drain(self, ws, m, size):
        """What the ring holds in order written into the stream's files,
        BLOCK by BLOCK, until it holds less than the next piece or STORE_MS
        has passed: one store op. A cart's head is read first, and its
        files made."""
        held = m.beam_held()
        if held <= 0:
            return True
        if self.files is not None and held < self._next():
            return True
        clock = self.link._ticks_ms
        t0 = clock()

        def _write():
            while 0 <= clock() - t0 < STORE_MS:
                if self.files is None:
                    if not self._take_head(m, clock()):
                        self.bad = True
                        return False
                    if self.files is None:
                        return True
                    continue
                n = self._next()
                if n == 0 or m.beam_held() < n:
                    return True
                got = m.beam_take(self.mv[:n], clock())
                self._file("wb").write(self.mv[:got])
                self._moved(got)
            return True
        return bool(_op(ws, _write))

    def _take_head(self, m, now):
        """A cart's head taken in as it arrives; once whole, read, and its
        folder and its empty files made. False where it is not a head."""
        h = self.head
        want = 8 if len(h) < 8 else int.from_bytes(h[4:8], "little") + 8
        if want > HEAD_MAX or (len(h) >= 4 and h[:4] != MAGIC):
            return False
        n = min(want - len(h), m.beam_held(), BLOCK)
        if n > 0:
            got = m.beam_take(self.mv[:n], now)
            self.head = h = h + bytes(self.mv[:got])
            self.pos += got
        if len(h) < 8 or len(h) < int.from_bytes(h[4:8], "little") + 8:
            return len(h) < 4 or h[:4] == MAGIC
        got = read_head(h, self.size)
        if got is None:
            return False
        self.folder, rels = got
        root = self.tmp
        _mkdir(root)
        files = []
        for rel, n in rels:
            p = root
            for part in rel.split("/")[:-1]:
                p += "/" + part
                _mkdir(p)
            path = store_path(root, rel)
            files.append((path, n))
            if n == 0:
                open(path, "wb").close()
        self.files, self.k, self.off = files, 0, 0
        return True

    def _progress(self, ws, role, short, size, done):
        """Bytes across of the size, and the time left from the rate
        measured since the bytes began to move, said at most every SAY_MS."""
        now = self.link._ticks_ms()
        if self.t0 is None:
            self.t0, self.d0 = now, done
        took = now - self.t0
        if took >= SAY_MS and done > self.d0:
            self.bps = (done - self.d0) * 1000 // took
            self.left_ms = (size - done) * 1000 // self.bps if self.bps else None
        if self.said_at is not None and 0 <= now - self.said_at < SAY_MS:
            return
        self.said_at = now
        line = "%s of %s" % (size_text(done), size_text(size))
        if self.left_ms is not None:
            line += ", " + about(self.left_ms)
        if line != self.line:
            self.line = line
            _say(ws, ("Sending %s" if role == SEND else "Receiving %s") % short, line,
                 ms=3 * SAY_MS)

    def _discard(self, ws):
        """This beam's open file closed and its temporary gone, where it
        has them."""
        tmp = self.tmp

        def _go():
            try:
                self._shut()
            finally:
                if tmp:
                    _gone(tmp)
            return True
        if tmp or self.fh is not None:
            _op(ws, _go)

    def _end(self, ws):
        m = self.link._m
        if m is not None:
            info = m.beam_info()
            if info is not None and info[1] in (OFFERED, MOVING):
                self.part_ms = KEEP_MS      # the link keeps this no, to say it again
            m.beam_end()
        self._close_question(ws)
        self._discard(ws)
        if self.held:
            self._let_go(ws)

    def _ask(self, ws, short, peer, size, what):
        """The offer held, asked about once the dialog is free; the answer
        acts on the next frame."""
        m = self.link._m
        if self.answer is True:
            self.answer = None
            tmp = _stage(ws) + "/" + IN

            def _prep():
                _mkdir(_stage(ws))
                _gone(tmp)
                return True
            if not _op(ws, _prep):
                self._end(ws)
                _say(ws, "RECEIVE FAILED", "The store failed", "warn")
                return
            if m.beam_accept(self.link._ticks_ms()) == 0:
                self.tmp, self.pos, self.size, self.what = tmp, 0, size, what
                self.head, self.k, self.off = b"", 0, 0
                self.files = None if what == CART else [(tmp, size)]
                _say(ws, "Receiving %s" % short, "From %s" % peer)
            return
        if self.answer is False:
            self.answer = None
            self._end(ws)
            _say(ws, "Stopped receiving")
            return
        confirm = getattr(ws, "confirm", None)
        if confirm is None or self._asking(ws):
            return
        if self.asked is not None:
            # Asked already, and the dialog is down: its answer arrives at
            # the frame's tail (`ws.defer`), which may come after this look.
            # Another question in its place is a no.
            if confirm.open:
                self._end(ws)
            return
        if confirm.open:
            return
        _withdraw(ws, "Receiving")
        self.asked = "Receive %s?" % short
        if what == CART:
            line = "From %s, %s. It goes on your shelf, and asks you before it " \
                   "first runs." % (peer, size_text(size))
        else:
            line = "From %s, %s. It goes in %s." % (peer, size_text(size), ARRIVALS)
        confirm.ask_close(self.asked, line, self._yes, _ANSWERS, kind="primary", no=self._no)

    def _yes(self):
        self.answer = True

    def _no(self):
        self.answer = False

    def _asking(self, ws):
        confirm = getattr(ws, "confirm", None)
        return (self.asked is not None and confirm is not None and confirm.open
                and confirm.title == self.asked)

    def _close_question(self, ws):
        """The question taken down where the offer it asks about is gone."""
        if self._asking(ws):
            ws.confirm.cancel()
        self.asked = None
        self.answer = None

    def _land(self, ws, what, short, peer, title, size):
        """The checked arrival in its place: a cart onto the shelf as a
        dropped `.moy`, a file into Downloads under a name of its own."""
        root, tmp = ws.carts_root, self.tmp
        self.tmp = None             # landed or removed here, either way
        whole = self.pos == size and self._next() == 0
        folder = self.folder
        _op(ws, self._shut)

        def _whole():
            try:
                return whole and os.stat(tmp)[6] == size
            except OSError:
                return False
        if what == CART:
            def _install():
                try:
                    return _carts.install_staged(tmp, folder, root) if whole else None
                finally:
                    _gone(tmp)
            got = _op(ws, _install)
            if got is None:
                _say(ws, "RECEIVE FAILED", "%s wasn't added" % short, "warn")
                return
            ws.carts.rescan([got])
            _say(ws, "Got %s" % short, "From %s, on your shelf" % peer)
        else:
            want = safe_name(title)

            def _keep():
                if not _whole():
                    _remove(tmp)
                    return None
                folder = files_root(root)
                _mkdir(folder)
                folder += "/" + ARRIVALS
                _mkdir(folder)
                name = free_name(folder, want)
                os.rename(tmp, folder + "/" + name)
                return name
            name = _op(ws, _keep)
            if name is None:
                _op(ws, lambda: _remove(tmp))
                _say(ws, "RECEIVE FAILED", "%s wasn't kept" % short, "warn")
                return
            _say(ws, "Got %s" % _short(want),
                 "In %s, from %s" % (ARRIVALS, peer) if name == want
                 else "In %s as %s" % (ARRIVALS, _short(name)))
        self.part_ms = KEEP_MS
