"""Beaming a cart to a console nearby (docs/netplay_v1.md §7): the console's
half of the beam the C link carries (native/moy_play/moy_match.h, BEAMING A
CART). The link moves the bytes and checks them; this packs the cart that
goes, asks the person on the receiving console, installs what arrived and
says every step on whatever screen is up (`Notices.notice`).

A beam is one of two things a person starts: SEND on a cart's card
(`HomeDetails.card_verbs`) offers its `.moy` archive (`moy_carts.pack`, the
browser's export), and RECEIVE CART in the system menu opens the receive
state, outside which an offer is never heard. An offer is asked about with
the console's one dialog (`DeleteConfirm.ask_close`): its title and the
console it comes from, before anything is written. A yes lets the bytes
move; once they pass the hash they land on the shelf as a dropped `.moy`
does (`moy_carts.import_archive`), so the cart arrived as files and its
first run asks (`Notices.ask_first`, docs/os_2026-11/processes.md §2): a
received cart never runs before its person says yes.

The radio is held for the beam as for a match (the "link" lease and the
link's own holder); a game that takes the radio ends the beam
(`EspNowLink.start`), and a beam never starts beside one. A beam that ends
keeps the radio up PART_MS more: its last frame (a no, a stop) is still in
the transport's send queue on a board whose radio is another chip (the
Waveshare P4's), and a radio stopped in the same call never sends it.
"""

import moy_carts as _carts
from moy_store_base import _sibling_path
from moy_fs import _mkdir, _read_bytes, _remove, _write_bytes

SEND, RECEIVE = 1, 2
LOADING, OFFERING, LISTENING, OFFERED, MOVING, DONE, FAILED = 1, 2, 3, 4, 5, 6, 7
NOBODY, GONE, DECLINED, STOPPED, DAMAGED, MATCH, ROOM = 1, 2, 3, 4, 5, 6, 7
MAX = 131072                    # moy_match.h's MOY_MATCH_BEAM_MAX
WAIT_MS = 60000                 # ...its MOY_MATCH_BEAM_WAIT_MS
PART_MS = 300                   # the radio stays up this long after a beam ends
FILE = "beam.moy"               # the archive in the store's install folder
_ANSWERS = ("No", "Receive")

# What a failed beam says, by its why: (title, line) for the sender and the
# receiver, {t} the cart's title and {p} the other console.
_SEND_WHY = {
    NOBODY: ("CAN'T SEND", "No console took {t}"),
    GONE: ("SEND FAILED", "{p} stopped answering"),
    DECLINED: ("NOT SENT", "{p} said no to {t}"),
    STOPPED: ("SEND FAILED", "{p} stopped receiving"),
    DAMAGED: ("SEND FAILED", "{t} reached {p} damaged"),
    MATCH: ("SEND STOPPED", "A match began"),
}
_RECEIVE_WHY = {
    NOBODY: ("NOTHING CAME", "No console sent a cart"),
    GONE: ("RECEIVE FAILED", "{p} stopped answering"),
    STOPPED: ("RECEIVE FAILED", "{p} stopped sending"),
    DAMAGED: ("RECEIVE FAILED", "{t} arrived damaged, so nothing was added"),
    MATCH: ("RECEIVE STOPPED", "A match began"),
    ROOM: ("RECEIVE FAILED", "No room for {t}"),
}


def _say(ws, title, line="", kind="ok", ms=6000):
    notices = getattr(ws, "notices", None)
    if notices is not None:
        notices.notice(title, line, kind, ms)


def _short(title):
    return str(title or "this cart")[:18]


class Beam:
    """The console's side of one link's beams: what it holds and what the
    person was asked."""

    def __init__(self, link):
        self.link = link
        self.held = False           # this beam holds the radio
        self.parting = None         # the tick a beam let go at, its radio still up
        self.answer = None          # the person's answer to the offer held
        self.asked = None           # the question as the dialog titles it
        self.said = None            # the progress last said

    # -- the two starts and the stop --
    def role(self):
        """SEND or RECEIVE while a beam is under way, else None."""
        m = self.link._m
        info = m.beam_info() if m is not None else None
        return None if info is None or info[1] in (DONE, FAILED) else info[0]

    def _refusal(self, ws):
        if self.link._game or getattr(ws, "netplay", None) is not None:
            return "Not while a game is using the radio"
        if getattr(ws, "carts_root", None) is None:
            return "This console has no cart store"
        return None

    def _hold(self, ws):
        """The radio up for the beam: the station's lease, then the link."""
        spine = getattr(ws, "spine", None)
        if spine is not None:
            spine.wifi_hold("link")
        self.link._beaming = True
        self.held = True
        self.parting = None
        if self.link._up():
            return True
        self._let_go(ws, True)
        return False

    def _let_go(self, ws, now=False):
        """The beam's hold over: the radio goes down PART_MS from here (at
        once with `now`: the radio never came up)."""
        self.held = False
        self.answer = self.asked = self.said = None
        if now:
            self._part(ws)
        else:
            self.parting = self.link._ticks_ms()

    def _part(self, ws):
        """The radio let go, unless a game or a port took it meanwhile."""
        link = self.link
        self.parting = None
        link._beaming = False
        if link._game:
            return
        if link._m is None or not link._m.ports_open():
            link._down()
        spine = getattr(ws, "spine", None)
        if spine is not None:
            spine.wifi_release("link")

    def send(self, ws, cart):
        """Offer `cart` (a cart dict with its folder's `path`) to whichever
        console nearby is receiving. False where it could not start, said."""
        title = _short((cart or {}).get("title"))
        why = self._refusal(ws)
        path = (cart or {}).get("path")
        if why is None and not path:
            why = "That is not a cart"
        if why is None and not self._hold(ws):
            why = "This console's radio is off"
        if why is not None:
            _say(ws, "CAN'T SEND", why, "warn")
            return False
        m = self.link._m
        tmp = _sibling_path(ws.carts_root, "install") + "/" + FILE

        def _pack():
            _mkdir(_sibling_path(ws.carts_root, "install"))
            _carts.pack(path, tmp)
            data = _read_bytes(tmp, MAX + 1)
            _remove(tmp)
            return data
        try:
            data = ws.carts.store.call(_pack)
        except Exception as exc:  # noqa: BLE001 -- a cart that will not pack is a message
            print("Moybyte beam: pack failed:", exc)
            data = None
        rc = -1
        if data and len(data) <= MAX:
            rc = m.beam_send(title, len(data))
            if rc == 0:
                rc = m.beam_put(0, data)
            if rc == 0:
                rc = m.beam_offer(self.link._ticks_ms())
        if rc != 0:
            m.beam_end()
            self._let_go(ws)
            _say(ws, "CAN'T SEND", "%s is too big to send" % title
                 if data and len(data) > MAX else "%s would not pack" % title, "warn")
            return False
        _say(ws, "Sending %s" % title, "Open Receive cart on the other console", ms=WAIT_MS)
        return True

    def receive(self, ws):
        """The receive state opened: the first offer heard is asked about."""
        why = self._refusal(ws)
        if why is None and not self._hold(ws):
            why = "This console's radio is off"
        if why is None and self.link._m.beam_listen(self.link._ticks_ms()) != 0:
            self._let_go(ws)
            why = "This console's radio is off"
        if why is not None:
            _say(ws, "CAN'T RECEIVE", why, "warn")
            return False
        _say(ws, "Receiving", "Waiting for a console nearby to send a cart", ms=WAIT_MS)
        return True

    def stop(self, ws):
        """The beam under way ended by its person; the other console is told."""
        m = self.link._m
        info = m.beam_info() if m is not None else None
        if m is not None:
            m.beam_end()
        self._close_question(ws)
        if self.held:
            self._let_go(ws)
        if info is not None:
            _say(ws, "Stopped sending" if info[0] == SEND else "Stopped receiving")

    # -- the frame --
    def tend(self, ws):
        """One frame's look at the beam: the question, the progress, the end."""
        m = self.link._m
        info = m.beam_info() if m is not None else None
        if info is None:
            if self.held:
                self._close_question(ws)
                self._let_go(ws, self.link._game)
                if self.link._game:
                    _say(ws, "BEAM STOPPED", "A game is using the radio", "warn")
            elif self.parting is not None and (
                    self.link._game
                    or not 0 <= self.link._ticks_ms() - self.parting < PART_MS):
                self._part(ws)
            return
        role, state, why, title, peer, size, done, chunks = info
        title = _short(title)
        peer = peer or "The other console"
        if state == OFFERED:
            self._ask(ws, title, peer)
            return
        self._close_question(ws)
        if state == MOVING:
            pct = done * 100 // chunks if chunks else 0
            step = pct // 10
            if self.said != step:
                self.said = step
                if role == SEND:
                    _say(ws, "Sending %s" % title, "To %s: %d%%" % (peer, pct))
                else:
                    _say(ws, "Receiving %s" % title, "From %s: %d%%" % (peer, pct))
            return
        if state == DONE:
            if role == SEND:
                _say(ws, "Sent %s" % title, "%s has it now" % peer)
            else:
                self._land(ws, m, title, peer, size)
            self._end(ws)
        elif state == FAILED:
            words = (_SEND_WHY if role == SEND else _RECEIVE_WHY).get(why)
            if words is not None:
                _say(ws, words[0], words[1].format(t=title, p=peer), "warn")
            self._end(ws)

    def _end(self, ws):
        m = self.link._m
        if m is not None:
            m.beam_end()
        self._close_question(ws)
        if self.held:
            self._let_go(ws)

    def _ask(self, ws, title, peer):
        """The offer held, asked about once the dialog is free; the answer
        acts on the next frame."""
        m = self.link._m
        if self.answer is True:
            self.answer = None
            if m.beam_accept(self.link._ticks_ms()) == 0:
                self.said = None
                _say(ws, "Receiving %s" % title, "From %s" % peer)
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
        self.asked = "Receive %s?" % title
        confirm.ask_close(self.asked, "From %s. It goes on your shelf, and asks you "
                          "before it first runs." % peer,
                          self._yes, _ANSWERS, kind="primary", no=self._no)

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

    def _land(self, ws, m, title, peer, size):
        """The checked bytes onto the shelf as a dropped `.moy`: arrived as
        files, so its first run asks."""
        root = ws.carts_root
        tmp = _sibling_path(root, "install") + "/" + FILE
        data = m.beam_take(0, size)

        def _install():
            _mkdir(_sibling_path(root, "install"))
            _write_bytes(tmp, data)
            try:
                return _carts.import_archive(tmp, root)
            finally:
                _remove(tmp)
        try:
            got = ws.carts.store.call(_install) if len(data) == size else None
        except Exception as exc:  # noqa: BLE001 -- a bad archive is a message
            print("Moybyte beam: install failed:", exc)
            got = None
        if got is None:
            _say(ws, "RECEIVE FAILED", "%s could not be added" % title, "warn")
            return
        ws.carts.rescan([got])
        _say(ws, "Got %s" % title, "From %s. It's on your shelf" % peer)
