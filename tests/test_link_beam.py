"""Beaming a file to a console nearby (docs/netplay_v1.md §7): two consoles in
one process over test_espnow_link's fake air, the C link carrying the bytes
through its ring (native/moy_play/moy_match.h, BEAMING A FILE) and
runtime/run/link_beam.py filling and emptying it from the store, asking,
landing and saying each step.

The air here is worse than a desk's: it drops frames, holds some back past
later ones, and in some tests damages a chunk or refuses sends the way the
Waveshare P4's 12-deep queue does. The radio's ack still lies (the fake's
send answers True for every frame it takes), so delivery rests on the
receiver's reports alone."""

import os
import random
import re

import pytest

from runtime import players as players_mod
from runtime import link_beam
from runtime import moy_carts
import test_espnow_link as tel

A_MAC, B_MAC = b"\x01" * 6, b"\x02" * 6
# What a notice's lines hold on the T-Deck's 320x240: the title, and the
# second line, which clips past 33 characters.
TITLE_MAX, LINE_MAX = 26, 33


class _Confirm:
    """The console's one dialog, as the beam uses it: one question at a
    time, answered by the test."""

    def __init__(self):
        self.open = False
        self.title = ""
        self.line = ""
        self.asked = []

    def ask_close(self, title, line, then, buttons=None, kind="danger", no=None):
        self.open, self.title, self.line = True, title, line
        self._yes, self._no = then, no
        self.asked.append((title, line, buttons, kind))

    def yes(self):
        self.open = False
        self._yes()

    def cancel(self):
        if self.open:
            self.open = False
            if self._no is not None:
                self._no()


class _Notices:
    def __init__(self):
        self.said = []
        self.up = None

    def notice(self, title, sub="", kind="ok", ms=6000):
        self.said.append((title, sub, kind))
        self.up = title

    def withdraw(self, title):
        if self.up == title:
            self.up = None
            self.said.append(("<withdrawn>", title, "ok"))


class _Spine:
    def __init__(self):
        self.held = set()

    def wifi_hold(self, tag, pid=None):
        self.held.add(tag)
        return True

    def wifi_release(self, tag, pid=None):
        self.held.discard(tag)


class _Store:
    """The store gate, every op counted."""

    def __init__(self):
        self.ops = 0

    def call(self, fn):
        self.ops += 1
        return fn()


class _Carts:
    def __init__(self):
        self.rescanned = []
        self.store = _Store()

    def rescan(self, paths=None):
        self.rescanned.append(paths)


def _console(tmp_path, name):
    ws = tel._Ws()
    ws.carts_root = str(tmp_path / name / "carts")
    os.makedirs(ws.carts_root)
    moy_carts.ensure_dirs(ws.carts_root)
    ws.notices, ws.confirm, ws.spine, ws.carts = _Notices(), _Confirm(), _Spine(), _Carts()
    return ws


def _cart(ws, size=20000):
    """A cart of the kid's own, big enough to take a few dozen chunks."""
    path = ws.carts_root + "/local.pong.moy"
    os.makedirs(path)
    with open(path + "/manifest.json", "w") as f:
        f.write('{"format": "moy-1", "title": "Pong", "id": "local.pong", '
                '"runtime": "python", "main": "main.py"}')
    with open(path + "/main.py", "w") as f:
        f.write("def _draw():\n    cls(1)\n")
    with open(path + "/data.bin", "wb") as f:
        f.write(random.Random(7).randbytes(size))
    return {"title": "Pong", "path": path}


def _file(ws, name="notes.txt", size=9000, seed=11):
    """A person's file in Documents."""
    folder = os.path.dirname(ws.carts_root) + "/files/Documents"
    os.makedirs(folder, exist_ok=True)
    path = folder + "/" + name
    with open(path, "wb") as f:
        f.write(random.Random(seed).randbytes(size))
    return path


def _bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _files(folder):
    out = {}
    for name in sorted(os.listdir(folder)):
        out[name] = _bytes(os.path.join(folder, name))
    return out


def _downloads(ws):
    d = os.path.dirname(ws.carts_root) + "/files/Downloads"
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def _stage(ws):
    d = os.path.dirname(ws.carts_root) + "/install"
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def _link(air, clock, mac, name):
    lk = tel._link(air, clock, mac, name=name)
    lk.stop()                          # the console at its home: no game holds the radio
    return lk


class _BadAir(tel._Air):
    """Drops a share of the frames and holds some back past later ones; a
    `damage` hook may change a frame on its way, `lose` take one."""

    def __init__(self, drop=0.25, hold=0.15, seed=3):
        tel._Air.__init__(self)
        self.rng = random.Random(seed)
        self.drop, self.hold = drop, hold
        self.held = []
        self.damage = None
        self.lose = None

    def carry(self, src, dst, payload):
        if self.damage is not None:
            payload = self.damage(payload)
        late, self.held = self.held, []
        if self.lose is not None and self.lose(src, payload):
            pass
        elif self.rng.random() < self.drop:
            pass
        elif self.rng.random() < self.hold:
            self.held.append((src, dst, payload))
        else:
            tel._Air.carry(self, src, dst, payload)
        for f in late:
            tel._Air.carry(self, *f)


def _setup(tmp_path, air=None):
    air, clock = air or tel._Air(), tel._Clock()
    a, b = _link(air, clock, A_MAC, "Ada"), _link(air, clock, B_MAC, "Bo")
    wsa, wsb = _console(tmp_path, "a"), _console(tmp_path, "b")
    return air, clock, a, b, wsa, wsb


def _run(a, b, wsa, wsb, clock, ms, step=16, until=None, each=None):
    for _ in range(ms // step):
        clock.advance(step)
        a.poll(wsa)
        b.poll(wsb)
        if each is not None:
            each()
        if until is not None and until():
            return True
    return False


def _both_done(a, b):
    return lambda: a.beam_role() is None and b.beam_role() is None


def _parted(a, b, wsa, wsb, clock):
    """Past the time a beam's radio stays up after it ends."""
    _run(a, b, wsa, wsb, clock, link_beam.KEEP_MS + 100)


def _shelf(ws):
    return sorted(os.listdir(ws.carts_root))


def _offered(a, b, wsa, wsb, clock):
    assert _run(a, b, wsa, wsb, clock, 3000, until=lambda: wsb.confirm.open)


# -- a cart -------------------------------------------------------------------

def test_a_cart_crosses_a_lossy_link_and_installs_byte_identical(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path, _BadAir(seed=5))
    cart = _cart(wsa)
    before = _shelf(wsb)
    assert b.beam_receive(wsb)
    assert a.beam_send(wsa, cart)
    assert wsa.spine.held == {"link"} and wsb.spine.held == {"link"}
    _offered(a, b, wsa, wsb, clock)
    # Asked with the title, the console it comes from and its size, before
    # anything is written to the store; the waiting notice goes first.
    assert wsb.confirm.title == "Receive Pong?"
    assert wsb.confirm.line.startswith("From Ada, ") and "shelf" in wsb.confirm.line
    assert wsb.confirm.asked[-1][2:] == (("No", "Receive"), "primary")
    assert ("<withdrawn>", "Receiving", "ok") in wsb.notices.said
    assert _shelf(wsb) == before and _stage(wsb) == []
    wsb.confirm.yes()
    assert _run(a, b, wsa, wsb, clock, 30000, until=_both_done(a, b))
    landed = [n for n in _shelf(wsb) if n not in before]
    assert landed == ["local.pong.moy"]
    assert _files(wsb.carts_root + "/local.pong.moy") == _files(cart["path"])
    assert wsb.carts.rescanned == [[wsb.carts_root + "/local.pong.moy"]]
    assert wsb.notices.said[-1][:2] == ("Got Pong", "From Ada, on your shelf")
    assert wsa.notices.said[-1][:2] == ("Sent Pong", "Bo has it now")
    # Both let the radio go, and nothing is left behind on either side.
    _parted(a, b, wsa, wsb, clock)
    assert not a.active and not b.active
    assert wsa.spine.held == set() and wsb.spine.held == set()
    assert a._m.beam_info() is None and b._m.beam_info() is None
    assert _stage(wsa) == [] and _stage(wsb) == [] and _downloads(wsb) == []


def _damage_one_chunk(payload):
    if len(payload) > 20 and payload[1] == players_mod.T_BEAM and payload[2] == 3 \
            and payload[13:17] == b"\x05\x00\x00\x00" and not _damage_one_chunk.done:
        _damage_one_chunk.done = True
        payload = payload[:20] + bytes([payload[20] ^ 0x40]) + payload[21:]
    return payload


@pytest.mark.parametrize("what", ["cart", "file"])
def test_a_damaged_chunk_fails_the_hash_and_leaves_nothing(tmp_path, what):
    air, clock, a, b, wsa, wsb = _setup(tmp_path, _BadAir(drop=0, hold=0))
    _damage_one_chunk.done = False
    air.damage = _damage_one_chunk
    before = _shelf(wsb)
    b.beam_receive(wsb)
    if what == "cart":
        a.beam_send(wsa, _cart(wsa))
    else:
        a.beam_send_file(wsa, _file(wsa))
    _offered(a, b, wsa, wsb, clock)
    wsb.confirm.yes()
    assert _run(a, b, wsa, wsb, clock, 30000, until=_both_done(a, b))
    assert _damage_one_chunk.done
    assert _shelf(wsb) == before and wsb.carts.rescanned == []
    assert _downloads(wsb) == [] and _stage(wsb) == [] and _stage(wsa) == []
    assert wsb.notices.said[-1][:2] == ("RECEIVE FAILED", "Arrived damaged: nothing kept")
    assert wsa.notices.said[-1][:2] == ("SEND FAILED", "It arrived damaged")


def test_a_console_not_receiving_ignores_the_offer(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    b._up()                            # its radio up, as a port would hold it
    a.beam_send(wsa, _cart(wsa))
    _run(a, b, wsa, wsb, clock, 30000, step=100)
    assert b._m.beam_info() is None and wsb.confirm.asked == []
    assert a.beam_role() == link_beam.SEND
    # Nothing waits forever: the offer gives up and says so.
    assert _run(a, b, wsa, wsb, clock, 31000, step=100, until=lambda: a.beam_role() is None)
    assert wsa.notices.said[-1][:2] == ("CAN'T SEND", "No console took Pong")
    _parted(a, b, wsa, wsb, clock)
    assert not a.active and wsa.spine.held == set() and _stage(wsa) == []


def test_a_decline_writes_nothing_and_the_sender_hears_no(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    before = _shelf(wsb)
    b.beam_receive(wsb)
    a.beam_send(wsa, _cart(wsa))
    _offered(a, b, wsa, wsb, clock)
    wsb.confirm.cancel()
    assert _run(a, b, wsa, wsb, clock, 3000, until=_both_done(a, b))
    assert _shelf(wsb) == before and _stage(wsb) == []
    assert wsa.notices.said[-1][:2] == ("NOT SENT", "Bo said no")
    assert wsb.notices.said[-1][:2] == ("Stopped receiving", "")
    # The radio outlives the no by PART_MS, so a queued send still leaves.
    assert b.active and wsb.spine.held == {"link"}
    _run(a, b, wsa, wsb, clock, link_beam.PART_MS + 100)
    assert not b.active and wsb.spine.held == set()


class _TailConfirm(_Confirm):
    """The console's dialog: an answer closes it at once and reaches its
    asker at the frame's tail (`ws.defer`), after the link's next look."""

    def __init__(self):
        _Confirm.__init__(self)
        self.tail = []

    def yes(self):
        self.open = False
        self.tail.append(self._yes)

    def cancel(self):
        if self.open:
            self.open = False
            if self._no is not None:
                self.tail.append(self._no)

    def run_tail(self):
        tail, self.tail = self.tail, []
        for fn in tail:
            fn()


@pytest.mark.parametrize("answer", ["yes", "cancel"])
def test_an_answer_that_lands_after_the_links_look_is_asked_once(tmp_path, answer):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    wsb.confirm = _TailConfirm()
    b.beam_receive(wsb)
    a.beam_send(wsa, _cart(wsa))
    _offered(a, b, wsa, wsb, clock)
    getattr(wsb.confirm, answer)()
    _run(a, b, wsa, wsb, clock, 64)    # the link looks before the tail runs
    assert len(wsb.confirm.asked) == 1 and not wsb.confirm.open
    wsb.confirm.run_tail()
    assert _run(a, b, wsa, wsb, clock, 30000, until=_both_done(a, b))
    assert len(wsb.confirm.asked) == 1 and not wsb.confirm.open
    assert wsa.notices.said[-1][0] == ("Sent Pong" if answer == "yes" else "NOT SENT")


def test_another_question_in_the_dialogs_place_is_a_no(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    b.beam_receive(wsb)
    a.beam_send(wsa, _cart(wsa))
    _offered(a, b, wsa, wsb, clock)
    wsb.confirm.title = "Delete Pong?"     # the one dialog asks something else
    assert _run(a, b, wsa, wsb, clock, 3000, until=_both_done(a, b))
    assert wsb.confirm.open and wsb.confirm.title == "Delete Pong?"
    assert wsa.notices.said[-1][:2] == ("NOT SENT", "Bo said no")


class _PickyAir(tel._Air):
    """Loses the frames `lose(src, payload)` picks."""

    def __init__(self, lose):
        tel._Air.__init__(self)
        self.lose = lose

    def carry(self, src, dst, payload):
        if not self.lose(src, payload):
            tel._Air.carry(self, src, dst, payload)


def test_one_lost_no_still_reaches_the_sender(tmp_path):
    lost = []

    def first_no(src, payload):
        if payload[1] == players_mod.T_BEAM and payload[2] == 4 and not lost:
            lost.append(src)
            return True
        return False
    air, clock, a, b, wsa, wsb = _setup(tmp_path, _PickyAir(first_no))
    b.beam_receive(wsb)
    a.beam_send(wsa, _cart(wsa))
    _offered(a, b, wsa, wsb, clock)
    wsb.confirm.cancel()
    assert _run(a, b, wsa, wsb, clock, 2000, until=lambda: a.beam_role() is None)
    assert lost == [B_MAC]
    assert wsa.notices.said[-1][:2] == ("NOT SENT", "Bo said no")


def test_a_free_receiver_stays_on_the_channel_it_heard_the_offer_on(tmp_path):
    """The sender's station is on its AP's channel and its beacons are lost:
    the free receiver takes the offer on a probe tick and stays there while
    its person reads the question and while the chunks cross."""
    air = _PickyAir(lambda src, payload: src == A_MAC and payload[1] == players_mod.T_BEACON)
    air, clock, a, b, wsa, wsb = _setup(tmp_path, air)
    a.wlan.ch, a.wlan.assoc = 6, True
    cart = _cart(wsa)
    b.beam_receive(wsb)
    a.beam_send(wsa, cart)
    assert _run(a, b, wsa, wsb, clock, 30000, until=lambda: wsb.confirm.open)
    _run(a, b, wsa, wsb, clock, 5000)      # longer than BEAM_SILENT_MS
    assert wsb.confirm.open and b.wlan.ch == 6
    wsb.confirm.yes()
    assert _run(a, b, wsa, wsb, clock, 30000, until=_both_done(a, b))
    assert wsb.notices.said[-1][0] == "Got Pong" and wsa.notices.said[-1][0] == "Sent Pong"
    _parted(a, b, wsa, wsb, clock)
    assert _run(a, b, wsa, wsb, clock, 2000) is False and not b.active


def test_a_stopped_game_leaves_no_cart_in_the_beacon_a_beam_brings_up(tmp_path):
    """Two consoles that each last ran the same two-player cart meet for a
    beam at their homes: no match forms and the beam goes through."""
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    for lk in (a, b):
        lk.start()
        lk.announce("Brick Siege", 1)
        lk.stop()
        assert lk._m.cart == "" and lk._m.state == 0
    b.beam_receive(wsb)
    a.beam_send(wsa, _cart(wsa))
    _offered(a, b, wsa, wsb, clock)
    assert not a._m.live and not b._m.live
    assert a.beam_role() == link_beam.SEND and b.beam_role() == link_beam.RECEIVE


def test_a_sender_that_vanishes_mid_transfer_ends_in_the_give_up(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    before = _shelf(wsb)
    b.beam_receive(wsb)
    a.beam_send(wsa, _cart(wsa, size=60000))
    _offered(a, b, wsa, wsb, clock)
    wsb.confirm.yes()
    assert _run(a, b, wsa, wsb, clock, 3000, until=lambda: b._m.beam_info()[6] > 0)
    air.deaf = True                    # the sender walked out of range
    assert _run(a, b, wsa, wsb, clock, 5000, until=lambda: b.beam_role() is None)
    assert wsb.notices.said[-1][:2] == ("RECEIVE FAILED", "Ada stopped answering")
    assert _shelf(wsb) == before and _stage(wsb) == []
    _run(a, b, wsa, wsb, clock, 5000, until=lambda: a.beam_role() is None)
    assert wsa.notices.said[-1][:2] == ("SEND FAILED", "Bo stopped answering")
    assert _stage(wsa) == []
    _parted(a, b, wsa, wsb, clock)
    assert not b.active and wsb.spine.held == set()


def test_the_receive_state_gives_up_when_nothing_comes(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    b.beam_receive(wsb)
    assert _run(a, b, wsa, wsb, clock, 61000, step=200, until=lambda: b.beam_role() is None)
    assert wsb.notices.said[-1][:2] == ("NOTHING CAME", "No console sent anything")
    _parted(a, b, wsa, wsb, clock)
    assert not b.active


def test_a_beam_never_starts_beside_a_game_and_a_game_ends_one(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    a.start()                          # a multiplayer cart holds the radio
    assert not a.beam_send(wsa, _cart(wsa))
    assert wsa.notices.said[-1][:2] == ("CAN'T SEND", "A game is using the radio")
    a.stop()
    b.beam_receive(wsb)
    assert b.beam_role() == link_beam.RECEIVE
    b.start()                          # a game takes the radio: the beam ends
    b.poll(wsb)
    assert b.beam_role() is None
    assert wsb.notices.said[-1][:2] == ("BEAM STOPPED", "A game is using the radio")
    assert b.active and "link" in wsb.spine.held   # the game's hold stands


def test_a_live_match_refuses_a_beam_in_c(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    a._up()
    m = a._m
    assert m.beam_listen(clock()) == 0
    m.begin(0, 1, 1, None)
    m.poll(clock())
    assert m.beam_info()[1:3] == (link_beam.FAILED, link_beam.MATCH)
    assert m.beam_listen(clock()) == -2
    assert m.beam_send("Pong", link_beam.CART, 100, clock()) == -2


# -- a file, any size -----------------------------------------------------------

def test_a_file_larger_than_any_ram_the_beam_owns_crosses_byte_identical(tmp_path):
    """Several megabytes cross a lossy air into Downloads while each side's
    beam holds the same few tens of KB it holds for a small file, the store
    reached a block at a time at block-aligned offsets."""
    air, clock, a, b, wsa, wsb = _setup(tmp_path, _BadAir(drop=0.05, hold=0.05, seed=9))
    src = _file(wsa, "film.bin", size=3 * 1024 * 1024 + 123)
    rams = set()
    writes = []
    real_open = open

    def watch():
        for lk in (a, b):
            info = lk._m.beam_info() if lk._m is not None else None
            if info is not None and info[1] == link_beam.MOVING:
                rams.add((info[0], info[12]))
    b.beam_receive(wsb)
    assert a.beam_send_file(wsa, src)
    _offered(a, b, wsa, wsb, clock)
    assert "film.bin" in wsb.confirm.title and "3.0 MB" in wsb.confirm.line
    assert "Downloads" in wsb.confirm.line
    wsb.confirm.yes()

    class _File:
        def __init__(self, f):
            self.f = f

        def seek(self, at):
            self.at = at
            return self.f.seek(at)

        def write(self, data):
            writes.append((self.at, len(data)))
            return self.f.write(data)

        def __getattr__(self, name):
            return getattr(self.f, name)

    def spy(path, mode="r", *k, **kw):
        f = real_open(path, mode, *k, **kw)
        return _File(f) if "beam-in" in str(path) else f
    link_beam.open = spy
    try:
        assert _run(a, b, wsa, wsb, clock, 120000, until=_both_done(a, b), each=watch)
    finally:
        del link_beam.open
    assert _downloads(wsb) == ["film.bin"]
    assert _bytes(os.path.dirname(wsb.carts_root) + "/files/Downloads/film.bin") == _bytes(src)
    assert wsb.notices.said[-1][:2] == ("Got film.bin", "In Downloads, from Ada")
    # Fixed RAM: each side's beam held one size the whole way, the ring's.
    assert {role for role, _r in rams} == {link_beam.SEND, link_beam.RECEIVE}
    assert len({r for _role, r in rams}) == 1 and next(iter(rams))[1] < 40000
    # The store, a block a frame: every write but the last a whole BLOCK at a
    # BLOCK-aligned offset, in order.
    assert all(at % link_beam.BLOCK == 0 for at, _n in writes)
    assert all(n == link_beam.BLOCK for _at, n in writes[:-1])
    assert [at for at, _n in writes] == sorted(at for at, _n in writes)


def test_the_beam_holds_the_same_ram_for_a_small_and_a_large_file(tmp_path):
    rams = {}
    for size in (500, 2 * 1024 * 1024):
        air, clock, a, b, wsa, wsb = _setup(tmp_path / str(size))
        seen = rams[size] = set()

        def watch():
            for lk in (a, b):
                info = lk._m.beam_info()
                if info is not None and info[1] == link_beam.MOVING:
                    seen.add((info[0], info[12]))
        b.beam_receive(wsb)
        a.beam_send_file(wsa, _file(wsa, size=size))
        _offered(a, b, wsa, wsb, clock)
        wsb.confirm.yes()
        _run(a, b, wsa, wsb, clock, 600, each=watch)
    assert rams[500] == rams[2 * 1024 * 1024] and len(rams[500]) == 2


def test_a_file_lands_in_downloads_under_a_safe_name_and_is_never_run(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    src = _file(wsa, 'a:b?<c>.txt', size=3000)
    before = _shelf(wsb)
    b.beam_receive(wsb)
    a.beam_send_file(wsa, src)
    _offered(a, b, wsa, wsb, clock)
    wsb.confirm.yes()
    assert _run(a, b, wsa, wsb, clock, 30000, until=_both_done(a, b))
    assert _downloads(wsb) == ["a_b__c_.txt"]
    # A file is kept, never installed or opened: the shelf did not move.
    assert _shelf(wsb) == before and wsb.carts.rescanned == []
    assert wsa.notices.said[-1][:2] == ("Sent a:b?<c>.txt", "Bo has it now")


@pytest.mark.parametrize("name, safe", [
    ("../../system/settings", "settings"), ("..", "file"), ("...", "file"),
    (".hidden", "hidden"), ("a\\b\\c.md", "c.md"), ("tab\there\x7f.txt", "tab_here_.txt"),
    ("", "file"), ("x" * 100 + ".png", "x" * 60 + ".png"),
    ("notes.bak", "notes.bak_"), ("a.tmp", "a.tmp_"),
])
def test_a_received_name_is_made_safe(name, safe):
    assert link_beam.safe_name(name) == safe


def test_a_name_already_taken_lands_beside_it(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    src = _file(wsa, "note.txt", size=2000)
    dl = os.path.dirname(wsb.carts_root) + "/files/Downloads"
    os.makedirs(dl)
    with open(dl + "/note.txt", "wb") as f:
        f.write(b"mine")
    for _ in range(2):
        b.beam_receive(wsb)
        a.beam_send_file(wsa, src)
        _offered(a, b, wsa, wsb, clock)
        wsb.confirm.yes()
        assert _run(a, b, wsa, wsb, clock, 30000, until=_both_done(a, b))
    assert _downloads(wsb) == ["note.txt", "note_2.txt", "note_3.txt"]
    assert _bytes(dl + "/note.txt") == b"mine"
    assert _bytes(dl + "/note_3.txt") == _bytes(src)
    assert wsb.notices.said[-1][:2] == ("Got note.txt", "In Downloads as note_3.txt")


def test_a_receiver_without_room_refuses_before_writing_and_the_sender_hears_why(
        tmp_path, monkeypatch):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    monkeypatch.setattr(link_beam, "free_bytes", lambda root: 50000)
    b.beam_receive(wsb)
    a.beam_send_file(wsa, _file(wsa, "big.bin", size=200000))
    assert _run(a, b, wsa, wsb, clock, 3000, until=_both_done(a, b))
    assert wsb.confirm.asked == [] and _stage(wsb) == [] and _downloads(wsb) == []
    assert wsb.notices.said[-1][:2] == ("NO ROOM", "big.bin needs 259 KB")
    assert wsa.notices.said[-1][:2] == ("NOT SENT", "Bo has no room for it")


@pytest.mark.parametrize("who", ["sender", "receiver"])
def test_a_stop_from_either_side_mid_transfer_leaves_nothing(tmp_path, who):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    before = _shelf(wsb)
    b.beam_receive(wsb)
    a.beam_send(wsa, _cart(wsa, size=400000))
    _offered(a, b, wsa, wsb, clock)
    wsb.confirm.yes()
    assert _run(a, b, wsa, wsb, clock, 5000,
                until=lambda: b._m.beam_info()[6] > 3 * link_beam.BLOCK)
    assert _stage(wsb) == ["beam-in"] and _stage(wsa) == ["beam-out.moy"]
    if who == "sender":
        a.beam_stop(wsa)
    else:
        b.beam_stop(wsb)
    assert _run(a, b, wsa, wsb, clock, 3000, until=_both_done(a, b))
    assert _stage(wsa) == [] and _stage(wsb) == [] and _shelf(wsb) == before
    if who == "sender":
        assert wsa.notices.said[-1][0] == "Stopped sending"
        assert wsb.notices.said[-1][:2] == ("RECEIVE FAILED", "Ada stopped sending")
    else:
        assert wsb.notices.said[-1][0] == "Stopped receiving"
        assert wsa.notices.said[-1][:2] == ("SEND FAILED", "Bo stopped receiving")


def test_a_stale_temporary_is_swept_when_a_beam_starts(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    stage = os.path.dirname(wsb.carts_root) + "/install"
    os.makedirs(stage)
    for n in (link_beam.IN, link_beam.OUT):
        with open(stage + "/" + n, "wb") as f:
            f.write(b"left by a reset")
    b.beam_receive(wsb)
    assert _stage(wsb) == []


def test_a_beam_its_console_stops_tending_ends_and_tells_its_peer(tmp_path):
    """A VM that went away (a soft reset) leaves a beam nobody tends: the
    link ends it after BEAM_TEND_MS and its peer hears the stop."""
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    b.beam_receive(wsb)
    a.beam_send(wsa, _cart(wsa, size=200000))
    _offered(a, b, wsa, wsb, clock)
    wsb.confirm.yes()
    _run(a, b, wsa, wsb, clock, 300)
    for _ in range(300):               # the kernel's tail polls on; no frame looks
        clock.advance(16)
        b._m.poll(clock())
        a.poll(wsa)
        if a.beam_role() is None:
            break
    assert a.beam_role() is None
    assert wsa.notices.said[-1][:2] == ("SEND FAILED", "Bo stopped receiving")
    assert b._m.beam_info()[1:3] == (link_beam.FAILED, link_beam.STOPPED)


# -- the pace ------------------------------------------------------------------

class _QueueRadio(tel._FakeRadio):
    """The Waveshare P4's send: a queue QUEUE deep that its transport clears
    one frame every CLEAR_MS (docs/netplay_v1.md §5), refusing a send while
    it is full."""
    QUEUE, CLEAR_MS = 12, 10

    def __init__(self, air, mac, clock):
        tel._FakeRadio.__init__(self, air, mac)
        self.clock = clock
        self.q = 0
        self.at = clock()

    def send(self, mac, payload, sync=False):
        gone = (self.clock() - self.at) // self.CLEAR_MS
        if gone:
            self.q = max(0, self.q - gone)
            self.at += gone * self.CLEAR_MS
        if self.q >= self.QUEUE:
            return False
        self.q += 1
        return tel._FakeRadio.send(self, mac, payload, sync)


def _rates(tmp_path, queued):
    air, clock = tel._Air(), tel._Clock()
    a = _link(air, clock, A_MAC, "Ada")
    if queued:
        a.radio = _QueueRadio(air, A_MAC, clock)
        a.radio.wlan = a.wlan
    b = _link(air, clock, B_MAC, "Bo")
    wsa, wsb = _console(tmp_path, "a"), _console(tmp_path, "b")
    src = _file(wsa, size=1024 * 1024)
    b.beam_receive(wsb)
    a.beam_send_file(wsa, src)
    _offered(a, b, wsa, wsb, clock)
    wsb.confirm.yes()
    rates = []

    def watch():
        info = a._m.beam_info()
        if info is not None and info[1] == link_beam.MOVING:
            rates.append(info[11])
    last = []

    def keep():
        watch()
        info = a._m.beam_info()
        if info is not None:
            last[:] = [info]
    assert _run(a, b, wsa, wsb, clock, 120000, until=_both_done(a, b), each=keep)
    assert _bytes(os.path.dirname(wsb.carts_root) + "/files/Downloads/notes.txt") == _bytes(src)
    return rates, last[0]


def test_the_pace_rises_on_a_clean_air(tmp_path):
    rates, info = _rates(tmp_path, False)
    assert rates[0] == 60                       # MOY_MATCH_BEAM_RATE
    assert max(rates) > 4 * rates[0]
    assert info[10] == 0                        # nothing refused


def test_the_pace_backs_off_when_sends_are_refused(tmp_path):
    rates, info = _rates(tmp_path, True)
    assert info[10] > 0                         # the queue refused some
    # The pace settles near what the queue clears (100 frames/s), never at
    # the clean air's.
    tail = rates[len(rates) // 2:]
    assert max(tail) < 300 and sum(tail) / len(tail) < 200


def test_progress_and_time_left_are_said_and_only_grow(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    src = _file(wsa, size=600000)
    b.beam_receive(wsb)
    a.beam_send_file(wsa, src)
    _offered(a, b, wsa, wsb, clock)
    wsb.confirm.yes()
    dones = {link_beam.SEND: [], link_beam.RECEIVE: []}

    def watch():
        for lk in (a, b):
            info = lk._m.beam_info()
            if info is not None and info[1] == link_beam.MOVING:
                dones[info[0]].append(info[6])
    assert _run(a, b, wsa, wsb, clock, 60000, until=_both_done(a, b), each=watch)
    for role in dones:
        d = dones[role]
        assert d and d == sorted(d) and d[-1] > 0
    for ws, title in ((wsa, "Sending notes.txt"), (wsb, "Receiving notes.txt")):
        lines = [s[1] for s in ws.notices.said if s[0] == title and " of " in s[1]]
        assert len(lines) >= 2
        assert any(re.search(r", about \d+ (s|min)$", ln) for ln in lines)


@pytest.mark.parametrize("ms, said", [
    (400, "about 1 s"), (9000, "about 9 s"), (12000, "about 15 s"), (59000, "about 60 s"),
    (61000, "about 1 min"), (125000, "about 2 min"), (3 * 3600 * 1000, "about 3 h"),
])
def test_the_time_left_is_rounded_as_a_person_says_it(ms, said):
    assert link_beam.about(ms) == said


# -- the end that is not lost --------------------------------------------------

def test_lost_dones_do_not_read_as_failure_on_any_seed(tmp_path):
    """Over forty seeds of a lossy, reordering air, every beam that the
    receiver landed is a beam its sender says was sent: a sender whose DONEs
    were all lost asks again (SUM) and the receiver answers from the verdict
    it keeps."""
    for seed in range(40):
        air, clock, a, b, wsa, wsb = _setup(tmp_path / str(seed), _BadAir(seed=seed))
        b.beam_receive(wsb)
        a.beam_send_file(wsa, _file(wsa, size=6000, seed=seed))
        _offered(a, b, wsa, wsb, clock)
        wsb.confirm.yes()
        assert _run(a, b, wsa, wsb, clock, 30000, until=_both_done(a, b)), seed
        assert wsb.notices.said[-1][0] == "Got notes.txt", (seed, wsb.notices.said[-1])
        assert wsa.notices.said[-1][0] == "Sent notes.txt", (seed, wsa.notices.said[-1])


def test_every_done_lost_still_reaches_the_sender(tmp_path):
    air = _BadAir(drop=0, hold=0)
    lost = []

    def dones(src, payload):
        if src == B_MAC and payload[1] == players_mod.T_BEAM and payload[2] == 5 \
                and len(lost) < 3:
            lost.append(payload)
            return True
        return False
    air.lose = dones
    air, clock, a, b, wsa, wsb = _setup(tmp_path, air)
    b.beam_receive(wsb)
    a.beam_send_file(wsa, _file(wsa, size=5000))
    _offered(a, b, wsa, wsb, clock)
    wsb.confirm.yes()
    assert _run(a, b, wsa, wsb, clock, 30000, until=_both_done(a, b))
    assert len(lost) == 3                       # the verdict's three, all lost
    assert wsb.notices.said[-1][0] == "Got notes.txt"
    assert wsa.notices.said[-1][:2] == ("Sent notes.txt", "Bo has it now")


# -- a link that cannot come up ------------------------------------------------

class _DeadRadio(tel._FakeRadio):
    """A radio whose ESP-NOW never comes up: the Guition P4's C6 without its
    shim, whose init times out."""

    def active(self, on=None):
        if on:
            raise OSError("ESP_ERR_TIMEOUT")
        return False


def test_an_inert_link_refuses_at_once_and_then_offers_nothing(tmp_path):
    air, clock = tel._Air(), tel._Clock()
    radio, wlan = _DeadRadio(air, A_MAC), tel._FakeWlan(A_MAC)
    lk = players_mod.EspNowLink(board="guition_p4", name="Gu", ticks_ms=clock,
                                radio=radio, wlan=wlan)
    ws = _console(tmp_path, "g")
    assert lk.usable()
    assert not lk.beam_receive(ws)
    assert ws.notices.said[-1][:2] == ("CAN'T RECEIVE", "This console has no radio link")
    assert not lk.usable() and ws.spine.held == set()
    assert not lk.beam_send(ws, _cart(ws))
    assert ws.notices.said[-1][:2] == ("CAN'T SEND", "This console has no radio link")
    assert lk.beam_role() is None and _stage(ws) == []


# -- every word fits the T-Deck --------------------------------------------------

def test_every_beam_notice_fits_the_t_decks_notice():
    long_t, long_p = "T" * 120, "P" * 23
    words = list(link_beam._SEND_WHY.values()) + list(link_beam._RECEIVE_WHY.values())
    for title, line in words:
        line = line.format(t=link_beam._short(long_t), p=link_beam._peer(long_p))
        assert len(title) <= TITLE_MAX and len(line) <= LINE_MAX, (title, line)
    s16 = link_beam._short(long_t)
    for title, line in (
            ("Sending " + s16, "Other console: " + link_beam.ROW),
            ("Receiving " + s16, "Waiting for a console to send"),
            ("Receiving " + s16, "From " + link_beam._peer(long_p)),
            ("Got " + s16, "From %s, on your shelf" % link_beam._peer(long_p)),
            ("Got " + s16, "In Downloads, from " + link_beam._peer(long_p)),
            ("Got " + s16, "In Downloads as " + s16),
            ("NO ROOM", "%s needs %s" % (s16, link_beam.size_text(999 << 20))),
            ("CAN'T SEND", "%s would not pack" % s16),
            ("CAN'T SEND", "%s can't be sent" % s16),
            ("CAN'T SEND", "%s can't be read" % s16),
            ("CAN'T RECEIVE", "This console has no radio link"),
            ("RECEIVE FAILED", "%s wasn't added" % s16),
            ("RECEIVE FAILED", "%s wasn't kept" % s16),
            ("BEAM STOPPED", "A game is using the radio"),
            ("Receiving " + s16, "%s of %s, %s" % (link_beam.size_text(1023 << 10),
                                                   link_beam.size_text(1023 << 10),
                                                   link_beam.about(89 * 60000))),
            ("Receiving " + s16, "%s of %s, %s" % (link_beam.size_text(99 << 20),
                                                   link_beam.size_text(int(99.9 * (1 << 20))),
                                                   link_beam.about(89 * 60000)))):
        assert len(title) <= TITLE_MAX and len(line) <= LINE_MAX, (title, line)
    # The notice names the menu's row in the menu's own words.
    from runtime import shell_cards
    assert shell_cards._BEAM_ROW[None][2] == link_beam.ROW


# -- the shell's half: the entries, the question, the shelf ---------------------

def _press(drv, *names):
    for n in names:
        drv.press(n)
        drv.frame(1 / 30)
    drv.frame(1 / 30)
    drv.frame(1 / 30)


def _row_center(ws, label):
    for it in ws.sysmenu.items:
        if it[0] == "item" and it[1] == label:
            box = ws.menu_ui._host.box(it[4])
            return box[0] + box[2] // 2, box[1] + box[3] // 2
    raise AssertionError("no row %r in %r" % (label, ws.sysmenu.items))


@pytest.mark.first_run
def test_the_shell_receives_through_its_menu_and_its_one_dialog(tmp_path):
    from runtime import host_app, app_perms, ui_draw
    from ws_helpers import build_ws
    ws = build_ws(tmp_path / "b")
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    air, clock = tel._Air(), tel._Clock()
    a, b = _link(air, clock, A_MAC, "Ada"), _link(air, clock, B_MAC, "Bo")
    wsa = _console(tmp_path, "a")
    ws.home.go_home()
    try:
        assert ws.link is None
        ws.link = b
        # The system group offers the receive state where there is a link.
        ws.toggle_sysmenu()
        drv.frame(1 / 30)
        labels = [it[1] for it in ws.sysmenu.items if it[0] == "item"]
        assert "RECEIVE NEARBY" in labels
        assert labels.index("RECEIVE NEARBY") == labels.index("SETTINGS") - 1
        x, y = _row_center(ws, "RECEIVE NEARBY")
        drv.click(x, y)
        drv.frame(1 / 30)
        drv.frame(1 / 30)
        assert b.beam_role() == link_beam.RECEIVE
        ws.toggle_sysmenu()
        drv.frame(1 / 30)
        assert "STOP RECEIVING" in [it[1] for it in ws.sysmenu.items if it[0] == "item"]
        ws.sysmenu.close()
        before = {c.get("path") for c in ws.carts.all}
        a.beam_send(wsa, _cart(wsa, size=3000))
        for _ in range(200):
            clock.advance(16)
            a.poll(wsa)
            b._m.poll(clock())         # the kernel's tail, on a board
            drv.frame(1 / 30)
            if ws.confirm.open:
                break
        drv.frame(1 / 30)
        assert ws.confirm.title == "Receive Pong?" and ws.confirm.focused == "No"
        assert {c.get("path") for c in ws.carts.all} == before
        _press(drv, "right", "a")
        for _ in range(400):
            clock.advance(16)
            a.poll(wsa)
            b._m.poll(clock())         # the kernel's tail, on a board
            drv.frame(1 / 30)
            if b.beam_role() is None and a.beam_role() is None:
                break
        got = [c for c in ws.carts.all if c.get("path") not in before]
        assert [c.get("title") for c in got] == ["Pong"]
        # It arrived as files: its first run asks, as a dropped .moy's does.
        cart = got[0]
        assert app_perms.asks_first(cart, ws.consent.get(app_perms.app_id(cart)))
        assert ws.notice_up[0] == "Got Pong"
        # The waiting notice was taken down when the question opened.
        assert ws.notice_up[0] != "Receiving"
        assert wsa.notices.said[-1][:2] == ("Sent Pong", "Bo has it now")
        # SEND is a card verb where there is a link, in the card's menu and
        # not among the desk's buttons.
        ws.home.set_home_tab("make")
        ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                               if it.get("path") == cart["path"])
        home = ws.launcher_layer
        assert "send" in home.card_verbs()
        assert "send" not in home._desk_verbs()
        assert any(r[0] == "item" and r[1] == "SEND" for r in ws.menu_ui.card_rows())
        # A link that could not come up offers neither SEND nor RECEIVE.
        b.error = "ESP_ERR_TIMEOUT"
        assert "send" not in home.card_verbs()
        ws.toggle_sysmenu()
        drv.frame(1 / 30)
        assert "RECEIVE NEARBY" not in [it[1] for it in ws.sysmenu.items if it[0] == "item"]
        ws.sysmenu.close()
        b.error = None
        ws.link = None
        assert "send" not in home.card_verbs()
    finally:
        ws.link = None
        ui_draw.input_kind("keys")


@pytest.mark.first_run
def test_files_sends_a_note_and_the_other_console_keeps_it_in_downloads(tmp_path):
    from runtime import host_app, files_app, intents, ui_draw
    from ws_helpers import build_ws, select_on_home
    ws = build_ws(tmp_path / "a")
    air, clock = tel._Air(), tel._Clock()
    a, b = _link(air, clock, A_MAC, "Ada"), _link(air, clock, B_MAC, "Bo")
    wsb = _console(tmp_path, "b")
    moy_carts.save_file("docs", "plan", "# the plan\n" * 300, ws.carts_root)
    try:
        ws.link = a
        select_on_home(ws, "Files")
        ws.open()
        ws.input.begin_frame()
        drv = host_app.ConsoleDriver(ws)
        for _ in range(3):
            ws.mark_dirty()
            drv.frame(1 / 30)
        app = ws.app("files")
        app.open_place(files_app.DOCUMENTS, keep="plan")
        assert app._action_labels()[-2:] == ("SEND", "DEL")
        app.open_place(files_app.PROJECTS)
        assert "SEND" in app._action_labels()
        app.open_place(files_app.DOCUMENTS, keep="plan")
        b.beam_receive(wsb)
        app._act("SEND")
        assert a.beam_role() == link_beam.SEND
        for _ in range(300):
            clock.advance(16)
            a._m.poll(clock())         # the kernel's tail, on a board
            b.poll(wsb)
            drv.frame(1 / 30)
            if wsb.confirm.open:
                break
        assert wsb.confirm.title == "Receive plan.md?"
        wsb.confirm.yes()
        for _ in range(1000):
            clock.advance(16)
            a._m.poll(clock())
            b.poll(wsb)
            drv.frame(1 / 30)
            if a.beam_role() is None and b.beam_role() is None:
                break
        got = os.path.dirname(wsb.carts_root) + "/files/Downloads/plan.md"
        assert _bytes(got) == ("# the plan\n" * 300).encode()
        # Without a link that can send, Files offers no SEND.
        ws.link = None
        assert "SEND" not in app._action_labels()
        assert not intents.INTENTS.send(intents.SHELL, "Documents/plan.md")
    finally:
        ws.link = None
        ui_draw.input_kind("keys")


def test_the_consoles_numbers_are_the_headers():
    """link_beam and players mirror moy_match.h's beam numbers; the header
    is their one author."""
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "native", "moy_play", "moy_match.h")) as f:
        h = f.read()

    def val(name):
        m = re.search(r"(?:#define %s |\b%s = )(\d+)" % (name, name), h)
        return int(m.group(1))
    assert link_beam.WAIT_MS == val("MOY_MATCH_BEAM_WAIT_MS")
    assert link_beam.KEEP_MS == val("MOY_MATCH_BEAM_KEEP_MS")
    assert players_mod.T_BEAM == val("MOY_MATCH_T_BEAM")
    assert (link_beam.SEND, link_beam.RECEIVE) == (val("MOY_BEAM_SEND"), val("MOY_BEAM_RECEIVE"))
    assert (link_beam.CART, link_beam.FILE) == (val("MOY_BEAM_CART"), val("MOY_BEAM_FILE"))
    for s in ("OFFERING", "LISTENING", "OFFERED", "MOVING", "DONE", "FAILED"):
        assert getattr(link_beam, s) == val("MOY_BEAM_" + s), s
    for w in ("NOBODY", "GONE", "DECLINED", "STOPPED", "DAMAGED", "MATCH", "ROOM"):
        assert getattr(link_beam, w) == val("MOY_BEAM_WHY_" + w), w
