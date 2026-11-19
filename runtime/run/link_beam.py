"""Beaming a file to a console nearby (docs/netplay_v1.md §7): the console's
half of the beam the C link carries (native/moy_play/moy_match.h, BEAMING A
FILE). The link moves the bytes through a ring of a fixed size and checks
them; this feeds that ring from the store and empties it into the store, asks
the person on the receiving console, puts what arrived in its place and says
every step on whatever screen is up (`Notices.notice`).

A beam is one of two things a person starts. SEND offers a cart (a card's
verb, `HomeDetails.card_verbs`, or a project's in Files) as its `.moy`
archive, packed into the store's install folder (`moy_carts.pack`, the
browser's export), or one of the person's files (Files' Send, through
`intents.Intents.send`) as it is. RECEIVE NEARBY in the system menu opens the
receive state, outside which an offer is never heard. An offer the store has
no room for is refused before anything is asked or written, and its sender
told why; any other is asked about with the console's one dialog
(`DeleteConfirm.ask_close`): its title, the console it comes from and its
size.

THE STORE, A BLOCK A FRAME. A frame's look reads at most one BLOCK of the
file into the ring where the ring has that much room (or the file's last
bytes), and writes at most one BLOCK the ring holds in order into the
arrival's temporary (`install/beam-in`), each a store op at a block-aligned
offset (`ws.carts.store.call`: on the T-Deck that is the SD gate), so a beam
costs the frame one sector-aligned write or read at most and its RAM is the
link's ring and this one block, whatever the size.

WHAT LANDS. Once the C's streamed hash passes, a cart's archive is imported
onto the shelf as a dropped `.moy` is (`moy_carts.import_archive`), so it
arrived as files and its first run asks (`Notices.ask_first`,
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

import moy_carts as _carts
from moy_store_base import _sibling_path, files_root
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
BLOCK = 4096                      # one store op's bytes, at a block-aligned offset
SAY_MS = 1000                     # the progress line, at most this often
RESERVE = 65536                   # free space an arrival leaves the store
OUT = "beam-out.moy"              # a cart being sent, packed, in the install folder
IN = "beam-in"                    # an arrival on its way, in the install folder
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


def _op(ws, fn):
    """`fn` as one store op (the SD gate on the T-Deck); None where it failed."""
    try:
        return ws.carts.store.call(fn)
    except Exception as exc:  # noqa: BLE001 -- a store that refuses is a message
        print("Moybyte beam:", exc)
        return None


def _stage(ws):
    return _sibling_path(ws.carts_root, "install")


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
        self._clear()

    def _clear(self):
        self.answer = None          # the person's answer to the offer held
        self.asked = None           # the question as the dialog titles it
        self.src = None             # send: the file the bytes come from
        self.tmp = None             # the temporary this beam owns
        self.size = 0
        self.pos = 0                # bytes read (send) or written (receive)
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
            _remove(stage + "/" + IN)
            _remove(stage + "/" + OUT)
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
        """Offer `cart` (a cart dict with its folder's `path`), packed, to
        whichever console nearby is receiving. False where it could not
        start, said."""
        cart = cart or {}
        path = cart.get("path")
        if not path:
            _say(ws, "CAN'T SEND", "Nothing to send", "warn")
            return False
        if self._start(ws, "SEND") is not None:
            return False
        title = _short(cart.get("title"))
        tmp = _stage(ws) + "/" + OUT

        def _pack():
            _remove(_stage(ws) + "/" + IN)
            _remove(tmp)
            _mkdir(_stage(ws))
            _carts.pack(path, tmp)
            return os.stat(tmp)[6]
        size = _op(ws, _pack)
        if not size:
            _op(ws, lambda: _remove(tmp))
            self._let_go(ws)
            _say(ws, "CAN'T SEND", "%s would not pack" % title, "warn")
            return False
        return self._offer(ws, CART, cart.get("title") or title, tmp, size, tmp)

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
        return self._offer(ws, FILE, name, path, size, None)

    def _offer(self, ws, what, title, src, size, tmp):
        m = self.link._m
        rc = m.beam_send(title, what, size, self.link._ticks_ms())
        if rc != 0:
            m.beam_end()
            if tmp:
                _op(ws, lambda: _remove(tmp))
            self._let_go(ws)
            _say(ws, "CAN'T SEND", "%s can't be sent" % _short(title), "warn")
            return False
        self.src, self.tmp, self.size, self.pos = src, tmp, size, 0
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
                need = (2 * size if what == CART else size) + RESERVE
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
                self._end(ws)
                _say(ws, "SEND FAILED" if role == SEND else "RECEIVE FAILED",
                     "The store failed", "warn")
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

    def _feed(self, ws, m):
        """One BLOCK of the file into the ring, where the ring has room for
        it or for the file's last bytes."""
        room = m.beam_room()
        left = self.size - self.pos
        if room <= 0 or left <= 0 or (room < BLOCK and room < left):
            return True
        n = min(BLOCK, room, left)
        src, at, mv = self.src, self.pos, self.mv

        def _read():
            f = open(src, "rb")
            try:
                f.seek(at)
                return f.readinto(mv[:n])
            finally:
                f.close()
        got = _op(ws, _read)
        if not got:
            return False
        self.pos += m.beam_fill(self.buf, got)
        return True

    def _drain(self, ws, m, size):
        """One BLOCK the ring holds in order into the arrival's temporary,
        or the file's last bytes."""
        held = m.beam_held()
        if held <= 0 or (held < BLOCK and self.pos + held < size):
            return True
        n = m.beam_take(self.buf, self.link._ticks_ms())
        tmp, at, mv = self.tmp, self.pos, self.mv

        def _write():
            f = open(tmp, "r+b" if at else "wb")
            try:
                f.seek(at)
                f.write(mv[:n])
            finally:
                f.close()
            return True
        if not _op(ws, _write):
            return False
        self.pos += n
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
        """This beam's temporary gone, where it has one."""
        tmp = self.tmp
        if tmp:
            _op(ws, lambda: _remove(tmp))

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
                _remove(tmp)
                return True
            if not _op(ws, _prep):
                self._end(ws)
                _say(ws, "RECEIVE FAILED", "The store failed", "warn")
                return
            if m.beam_accept(self.link._ticks_ms()) == 0:
                self.tmp, self.pos, self.size = tmp, 0, size
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

        def _whole():
            try:
                return os.stat(tmp)[6] == size
            except OSError:
                return False
        if what == CART:
            def _install():
                try:
                    return _carts.import_archive(tmp, root) if _whole() else None
                finally:
                    _remove(tmp)
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
