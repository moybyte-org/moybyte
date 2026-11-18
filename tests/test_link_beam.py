"""Beaming a cart to a console nearby (docs/netplay_v1.md §7): two consoles in
one process over test_espnow_link's fake air, the C link carrying the bytes
(native/moy_play/moy_match.h, BEAMING A CART) and runtime/run/link_beam.py
packing, asking, installing and saying each step.

The air here is worse than a desk's: it drops frames, holds some back past
later ones, and in one test damages a chunk. The radio's ack still lies (the
fake's send answers True for every frame), so delivery rests on the
receiver's reports alone."""

import os
import random
import types

import pytest

from runtime import players as players_mod
from runtime import link_beam
from runtime import moy_carts
import test_espnow_link as tel

A_MAC, B_MAC = b"\x01" * 6, b"\x02" * 6


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

    def notice(self, title, sub="", kind="ok", ms=6000):
        self.said.append((title, sub, kind))


class _Spine:
    def __init__(self):
        self.held = set()

    def wifi_hold(self, tag, pid=None):
        self.held.add(tag)
        return True

    def wifi_release(self, tag, pid=None):
        self.held.discard(tag)


class _Carts:
    def __init__(self):
        self.rescanned = []
        self.store = types.SimpleNamespace(call=lambda fn: fn())

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


def _files(folder):
    out = {}
    for name in sorted(os.listdir(folder)):
        with open(os.path.join(folder, name), "rb") as f:
            out[name] = f.read()
    return out


def _link(air, clock, mac, name):
    lk = tel._link(air, clock, mac, name=name)
    lk.stop()                          # the console at its home: no game holds the radio
    return lk


class _BadAir(tel._Air):
    """Drops a share of the frames and holds some back past later ones; a
    `damage` hook may change a frame on its way."""

    def __init__(self, drop=0.25, hold=0.15, seed=3):
        tel._Air.__init__(self)
        self.rng = random.Random(seed)
        self.drop, self.hold = drop, hold
        self.held = []
        self.damage = None

    def carry(self, src, dst, payload):
        if self.damage is not None:
            payload = self.damage(payload)
        late, self.held = self.held, []
        if self.rng.random() < self.drop:
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


def _run(a, b, wsa, wsb, clock, ms, step=16, until=None):
    for _ in range(ms // step):
        clock.advance(step)
        a.poll(wsa)
        b.poll(wsb)
        if until is not None and until():
            return True
    return False


def _parted(a, b, wsa, wsb, clock):
    """Past the time a beam's radio stays up after it ends (PART_MS)."""
    _run(a, b, wsa, wsb, clock, link_beam.PART_MS + 100)


def _shelf(ws):
    return sorted(os.listdir(ws.carts_root))


def test_a_cart_crosses_a_lossy_link_and_installs_byte_identical(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path, _BadAir(seed=5))
    cart = _cart(wsa)
    before = _shelf(wsb)
    assert b.beam_receive(wsb)
    assert a.beam_send(wsa, cart)
    assert wsa.spine.held == {"link"} and wsb.spine.held == {"link"}
    assert _run(a, b, wsa, wsb, clock, 3000, until=lambda: wsb.confirm.open)
    # Asked with the title and the console it comes from, before anything
    # is written to the store.
    assert wsb.confirm.title == "Receive Pong?"
    assert "From Ada." in wsb.confirm.line
    assert wsb.confirm.asked[-1][2:] == (("No", "Receive"), "primary")
    assert _shelf(wsb) == before
    assert not os.path.exists(os.path.dirname(wsb.carts_root) + "/install/beam.moy")
    wsb.confirm.yes()
    assert _run(a, b, wsa, wsb, clock, 30000,
                until=lambda: a.beam_role() is None and b.beam_role() is None)
    landed = [n for n in _shelf(wsb) if n not in before]
    assert landed == ["local.pong.moy"]
    assert _files(wsb.carts_root + "/local.pong.moy") == _files(cart["path"])
    assert wsb.carts.rescanned == [[wsb.carts_root + "/local.pong.moy"]]
    assert wsb.notices.said[-1][:2] == ("Got Pong", "From Ada. It's on your shelf")
    assert wsa.notices.said[-1][:2] == ("Sent Pong", "Bo has it now")
    assert any(s[0] == "Receiving Pong" and s[1].endswith("%") for s in wsb.notices.said)
    # Both let the radio go, and nothing is left behind.
    _parted(a, b, wsa, wsb, clock)
    assert not a.active and not b.active
    assert wsa.spine.held == set() and wsb.spine.held == set()
    assert a._m.beam_info() is None and b._m.beam_info() is None
    assert not os.path.exists(os.path.dirname(wsb.carts_root) + "/install/beam.moy")


def _damage_one_chunk(payload):
    if len(payload) > 20 and payload[1] == players_mod.T_BEAM and payload[2] == 3 \
            and payload[13] == 5 and payload[14] == 0 and not _damage_one_chunk.done:
        _damage_one_chunk.done = True
        payload = payload[:20] + bytes([payload[20] ^ 0x40]) + payload[21:]
    return payload


def test_a_damaged_chunk_fails_the_hash_and_installs_nothing(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path, _BadAir(drop=0, hold=0))
    _damage_one_chunk.done = False
    air.damage = _damage_one_chunk
    cart = _cart(wsa)
    before = _shelf(wsb)
    b.beam_receive(wsb)
    a.beam_send(wsa, cart)
    _run(a, b, wsa, wsb, clock, 3000, until=lambda: wsb.confirm.open)
    wsb.confirm.yes()
    assert _run(a, b, wsa, wsb, clock, 30000,
                until=lambda: a.beam_role() is None and b.beam_role() is None)
    assert _damage_one_chunk.done
    assert _shelf(wsb) == before and wsb.carts.rescanned == []
    assert wsb.notices.said[-1][:2] == ("RECEIVE FAILED",
                                        "Pong arrived damaged, so nothing was added")
    assert wsa.notices.said[-1][:2] == ("SEND FAILED", "Pong reached Bo damaged")


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
    assert not a.active and wsa.spine.held == set()


def test_a_decline_writes_nothing_and_the_sender_hears_no(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    before = _shelf(wsb)
    b.beam_receive(wsb)
    a.beam_send(wsa, _cart(wsa))
    _run(a, b, wsa, wsb, clock, 3000, until=lambda: wsb.confirm.open)
    wsb.confirm.cancel()
    assert _run(a, b, wsa, wsb, clock, 3000,
                until=lambda: a.beam_role() is None and b.beam_role() is None)
    assert _shelf(wsb) == before
    assert not os.path.exists(os.path.dirname(wsb.carts_root) + "/install/beam.moy")
    assert wsa.notices.said[-1][:2] == ("NOT SENT", "Bo said no to Pong")
    assert wsb.notices.said[-1][:2] == ("Stopped receiving", "")
    # The radio outlives the no by PART_MS, so a queued send still leaves.
    assert b.active and wsb.spine.held == {"link"}
    _parted(a, b, wsa, wsb, clock)
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
    _run(a, b, wsa, wsb, clock, 3000, until=lambda: wsb.confirm.open)
    getattr(wsb.confirm, answer)()
    _run(a, b, wsa, wsb, clock, 64)    # the link looks before the tail runs
    assert len(wsb.confirm.asked) == 1 and not wsb.confirm.open
    wsb.confirm.run_tail()
    assert _run(a, b, wsa, wsb, clock, 30000,
                until=lambda: a.beam_role() is None and b.beam_role() is None)
    assert len(wsb.confirm.asked) == 1 and not wsb.confirm.open
    assert wsa.notices.said[-1][0] == ("Sent Pong" if answer == "yes" else "NOT SENT")


def test_another_question_in_the_dialogs_place_is_a_no(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    b.beam_receive(wsb)
    a.beam_send(wsa, _cart(wsa))
    _run(a, b, wsa, wsb, clock, 3000, until=lambda: wsb.confirm.open)
    wsb.confirm.title = "Delete Pong?"     # the one dialog asks something else
    assert _run(a, b, wsa, wsb, clock, 3000,
                until=lambda: a.beam_role() is None and b.beam_role() is None)
    assert wsb.confirm.open and wsb.confirm.title == "Delete Pong?"
    assert wsa.notices.said[-1][:2] == ("NOT SENT", "Bo said no to Pong")


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
    _run(a, b, wsa, wsb, clock, 3000, until=lambda: wsb.confirm.open)
    wsb.confirm.cancel()
    assert _run(a, b, wsa, wsb, clock, 2000, until=lambda: a.beam_role() is None)
    assert lost == [B_MAC]
    assert wsa.notices.said[-1][:2] == ("NOT SENT", "Bo said no to Pong")


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
    assert _run(a, b, wsa, wsb, clock, 30000,
                until=lambda: a.beam_role() is None and b.beam_role() is None)
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
    assert _run(a, b, wsa, wsb, clock, 3000, until=lambda: wsb.confirm.open)
    assert not a._m.live and not b._m.live
    assert a.beam_role() == link_beam.SEND and b.beam_role() == link_beam.RECEIVE


def test_a_sender_that_vanishes_mid_transfer_ends_in_the_give_up(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    before = _shelf(wsb)
    b.beam_receive(wsb)
    a.beam_send(wsa, _cart(wsa, size=60000))
    _run(a, b, wsa, wsb, clock, 3000, until=lambda: wsb.confirm.open)
    wsb.confirm.yes()
    assert _run(a, b, wsa, wsb, clock, 3000,
                until=lambda: (b._m.beam_info() or (0,) * 8)[6] > 10)
    air.deaf = True                    # the sender walked out of range
    assert _run(a, b, wsa, wsb, clock, 5000, until=lambda: b.beam_role() is None)
    assert wsb.notices.said[-1][:2] == ("RECEIVE FAILED", "Ada stopped answering")
    assert _shelf(wsb) == before
    _run(a, b, wsa, wsb, clock, 5000, until=lambda: a.beam_role() is None)
    assert wsa.notices.said[-1][:2] == ("SEND FAILED", "Bo stopped answering")
    _parted(a, b, wsa, wsb, clock)
    assert not b.active and wsb.spine.held == set()


def test_the_receive_state_gives_up_when_nothing_comes(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    b.beam_receive(wsb)
    assert _run(a, b, wsa, wsb, clock, 61000, step=200, until=lambda: b.beam_role() is None)
    assert wsb.notices.said[-1][:2] == ("NOTHING CAME", "No console sent a cart")
    _parted(a, b, wsa, wsb, clock)
    assert not b.active


def test_a_beam_never_starts_beside_a_game_and_a_game_ends_one(tmp_path):
    air, clock, a, b, wsa, wsb = _setup(tmp_path)
    a.start()                          # a multiplayer cart holds the radio
    assert not a.beam_send(wsa, _cart(wsa))
    assert wsa.notices.said[-1][:2] == ("CAN'T SEND", "Not while a game is using the radio")
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
    assert m.beam_send("Pong", 100) == -2


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
        assert "RECEIVE CART" in labels
        assert labels.index("RECEIVE CART") == labels.index("SETTINGS") - 1
        x, y = _row_center(ws, "RECEIVE CART")
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
        ws.link = None
        assert "send" not in home.card_verbs()
    finally:
        ws.link = None
        ui_draw.input_kind("keys")


def test_the_consoles_numbers_are_the_headers():
    """link_beam and players mirror moy_match.h's beam numbers; the header
    is their one author."""
    import re
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "native", "moy_play", "moy_match.h")) as f:
        h = f.read()

    def val(name):
        m = re.search(r"(?:#define %s |\b%s = )(\d+)" % (name, name), h)
        return int(m.group(1))
    assert link_beam.MAX == val("MOY_MATCH_BEAM_MAX")
    assert link_beam.WAIT_MS == val("MOY_MATCH_BEAM_WAIT_MS")
    assert players_mod.T_BEAM == val("MOY_MATCH_T_BEAM")
    assert (link_beam.SEND, link_beam.RECEIVE) == (val("MOY_BEAM_SEND"), val("MOY_BEAM_RECEIVE"))
    for py, c in (("LOADING", "LOADING"), ("OFFERING", "OFFERING"), ("LISTENING", "LISTENING"),
                  ("OFFERED", "OFFERED"), ("MOVING", "MOVING"), ("DONE", "DONE"),
                  ("FAILED", "FAILED")):
        assert getattr(link_beam, py) == val("MOY_BEAM_" + c), py
    for w in ("NOBODY", "GONE", "DECLINED", "STOPPED", "DAMAGED", "MATCH", "ROOM"):
        assert getattr(link_beam, w) == val("MOY_BEAM_WHY_" + w), w
