"""The ESP-NOW owner: discovery, pairing and dispatch (#7/#65 Phase 2).

No radio here -- a fake one, which MODELS THE TRAPS the real hardware taught us
(2026-08-20, T-Deck <-> Guition) rather than an idealised link:

  * reconfiguring rxbuf on a LIVE radio raises, permanently, until an active
    cycle -- so the fake raises too, and the order test below is what stops
    somebody "tidying" the recipe back into the shape that desyncs the ring;
  * send() reports success whether or not anything is delivered, because the
    real link-layer ack does exactly that.

The protocol half is board-agnostic on purpose (every hardware handle is built
inside a guarded method), which is what lets the whole handshake and a full
two-console match run here instead of only on glass.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))
import moy_espnow  # noqa: E402
sys.path.remove(str(ROOT / "device"))

from runtime import netplay, players as players_mod  # noqa: E402

BROADCAST = moy_espnow.BROADCAST


class _Air:
    """The shared medium. `lossy` drops every nth frame; `deaf` cuts it dead."""

    def __init__(self):
        self.radios = {}
        self.deaf = False
        self.sent = 0

    def carry(self, src, dst, payload):
        self.sent += 1
        if self.deaf:
            return
        for mac, r in self.radios.items():
            if mac == src:
                continue
            if dst == BROADCAST or dst == mac:
                r.inbox.append((src, payload))


class _FakeRadio:
    def __init__(self, air, mac):
        self.air = air
        self.mac = mac
        self.inbox = []
        self.on = False
        self.peers = set()
        self.cfg = {}
        self.log = []
        air.radios[mac] = self

    def config(self, **kw):
        if "rxbuf" in kw and self.on:
            # The measured trap: a live rxbuf change desyncs the ring for good.
            raise ValueError("ESPNow.recv(): buffer error")
        self.cfg.update(kw)
        self.log.append(("config", tuple(sorted(kw))))

    def active(self, on=None):
        if on is None:
            return self.on
        self.on = bool(on)
        self.log.append(("active", self.on))
        return self.on

    def add_peer(self, mac):
        self.peers.add(bytes(mac))

    def send(self, mac, payload, sync=False):
        # Returns True regardless of delivery -- the ack lies, and code written
        # against this fake must not come to depend on it telling the truth.
        self.air.carry(self.mac, bytes(mac), bytes(payload))
        return True

    def irecv(self, timeout=0):
        if not self.inbox:
            return (None, None)
        return self.inbox.pop(0)


class _FakeWlan:
    PM_NONE = 0
    PM_PERFORMANCE = 1

    def __init__(self, mac):
        self._mac = mac
        self._pm = self.PM_PERFORMANCE

    def active(self, on=None):
        return True

    def config(self, *a, **kw):
        if kw:
            if "pm" in kw:
                self._pm = kw["pm"]
            return None
        if a and a[0] == "mac":
            return self._mac
        if a and a[0] == "pm":
            return self._pm
        return None


class _Clock:
    def __init__(self):
        self.t = 1000

    def __call__(self):
        return self.t

    def advance(self, ms):
        self.t += ms


class _Input:
    def __init__(self):
        self.down = set()

    def held(self, name, player=None):
        return name in self.down


class _Ws:
    """The slice of the Workstation a link touches."""

    def __init__(self):
        self.input = _Input()
        self.input.players = players_mod.PlayerRouter(self.input)
        self.netplay = None
        self.launched = []


def _link(air, clock, mac, board="tdeck", name="A", launch=None):
    lk = moy_espnow.EspNowLink(board=board, name=name, ticks_ms=clock,
                               radio=_FakeRadio(air, mac), wlan=_FakeWlan(mac),
                               launch=launch)
    lk.start()
    return lk


def _pair(launch=None):
    air, clock = _Air(), _Clock()
    a = _link(air, clock, b"\x01" * 6, "tdeck", "Ada", launch)
    b = _link(air, clock, b"\x02" * 6, "guition_s3", "Bo", launch)
    return air, clock, a, b


def _see_each_other(a, b, clock, cart="Brick Siege"):
    a.announce(cart, 1)
    b.announce(cart, 1)
    clock.advance(10)
    a.poll()
    b.poll()


# -- the recipe -------------------------------------------------------------

def test_start_applies_the_recipe_in_the_order_that_does_not_desync_the_ring():
    """rxbuf BEFORE active(True) and the rate AFTER. Reversing the first pair is
    the mistake that raises `buffer error` on every later recv until an active
    cycle -- measured, and the reason this ordering is a test and not a comment."""
    air, clock = _Air(), _Clock()
    lk = _link(air, clock, b"\x01" * 6)
    log = lk.radio.log
    kinds = [e[0] for e in log]
    first_active = kinds.index("active")
    rxbuf_at = next(i for i, e in enumerate(log)
                    if e[0] == "config" and "rxbuf" in e[1])
    assert rxbuf_at < first_active, "rxbuf must be set before the radio is active"
    rate_at = next((i for i, e in enumerate(log)
                    if e[0] == "config" and "rate" in e[1]), None)
    if rate_at is not None:
        assert rate_at > first_active, "the rate is only settable once active"
    assert lk.radio.cfg["rxbuf"] == moy_espnow.RXBUF == 32768
    assert BROADCAST in lk.radio.peers, "broadcast must be a peer or discovery is mute"


def test_the_session_disables_power_save_and_stop_puts_it_back():
    """pm=PM_NONE halves the latency tail and costs battery, so it is a session
    lever on a handheld, never a global one."""
    air, clock = _Air(), _Clock()
    lk = _link(air, clock, b"\x01" * 6)
    assert lk.wlan.config("pm") == _FakeWlan.PM_NONE
    lk.stop()
    assert lk.wlan.config("pm") == _FakeWlan.PM_PERFORMANCE


def test_a_board_with_no_radio_degrades_to_an_inactive_link():
    lk = moy_espnow.EspNowLink(board="p4", ticks_ms=_Clock())
    assert lk.start() is False        # no espnow module under CPython
    assert lk.active is False and lk.error
    assert lk.broadcast(b"\x01\x02") is False    # and every verb stays safe
    assert lk.poll(None) == 0
    assert lk.status() == (False, 0, False, None)


# -- discovery --------------------------------------------------------------

def test_beacons_make_peers_visible_and_silence_expires_them():
    air, clock, a, b = _pair()
    _see_each_other(a, b, clock)
    assert len(a.peers) == 1 and len(b.peers) == 1
    peer = list(a.peers.values())[0]
    assert (peer.name, peer.board, peer.cart) == ("Bo", "guition_s3", "Brick Siege")

    air.deaf = True                   # Bo walks into the next room
    a.poll()                          # drain whatever was already in flight
    clock.advance(moy_espnow.PEER_TTL_MS + 1)
    a.poll()
    assert a.peers == {}, "a peer that walked away must stop being a candidate"


def test_a_candidate_must_be_on_the_same_cart():
    air, clock, a, b = _pair()
    a.announce("Brick Siege", 1)
    b.announce("Harpoon Pop", 1)
    clock.advance(10)
    a.poll()
    assert a.candidate("Brick Siege") is None, "different games are not a match"
    b.announce("Brick Siege", 1)
    clock.advance(10)
    a.poll()
    assert a.candidate("Brick Siege") is not None


def test_a_console_already_in_a_match_is_not_a_candidate():
    air, clock, a, b = _pair()
    a.announce("Brick Siege", 1)
    b.announce("Brick Siege", 2)      # already playing with somebody else
    clock.advance(10)
    a.poll()
    assert len(a.peers) == 1, "still visible..."
    assert a.candidate("Brick Siege") is None, "...but not available"


# -- pairing ----------------------------------------------------------------

def test_the_lower_mac_hosts_so_two_consoles_never_both_host_or_both_wait():
    air, clock, a, b = _pair()
    _see_each_other(a, b, clock)
    wsa, wsb = _Ws(), _Ws()
    assert b.offer(wsb, "Brick Siege") is False, "the higher MAC waits"
    assert a.offer(wsa, "Brick Siege") is True, "the lower MAC hosts"
    assert a.session.index == 0


def test_a_full_handshake_gives_each_console_the_other_half_of_one_game():
    launched = []

    def launch(ws, title):
        launched.append(title)
        ws.launched.append(title)
        return True

    air, clock, a, b = _pair(launch)
    _see_each_other(a, b, clock)
    wsa, wsb = _Ws(), _Ws()
    assert a.offer(wsa, "Brick Siege") is True
    b.poll(wsb)                       # the START lands here

    assert wsa.netplay is not None and wsb.netplay is not None
    assert wsa.netplay.index == 0 and wsb.netplay.index == 1
    assert wsa.netplay.seed == wsb.netplay.seed, "one seed or the sims diverge"
    assert wsa.netplay.session == wsb.netplay.session
    assert launched == ["Brick Siege"], "the guest opened the host's cart"
    assert a.status()[2] is True and b.status()[2] is True


def test_the_guest_ends_the_match_when_it_does_not_have_the_cart():
    air, clock, a, b = _pair(lambda ws, title: False)     # "no such cart here"
    _see_each_other(a, b, clock)
    wsa, wsb = _Ws(), _Ws()
    a.offer(wsa, "Brick Siege")
    b.poll(wsb)
    assert b.session is None, "no cart, no match -- not a half-formed one"


# -- a whole match ----------------------------------------------------------

def _run(a, b, wsa, wsb, frames):
    """Both consoles tick, exchanging over the fake air the way the frame loop
    does: drain, then advance."""
    ok = []
    for _ in range(frames):
        clock_ticks = (a, b)
        for lk, ws in ((a, wsa), (b, wsb)):
            lk.poll(ws)
        ra = wsa.netplay.advance(netplay.mask_of(wsa.input, moy_espnow.CART_BUTTONS))
        rb = wsb.netplay.advance(netplay.mask_of(wsb.input, moy_espnow.CART_BUTTONS))
        ok.append((ra, rb))
        assert clock_ticks
    return ok


def test_two_consoles_play_one_game_over_the_radio():
    air, clock, a, b = _pair(lambda ws, title: True)
    _see_each_other(a, b, clock)
    wsa, wsb = _Ws(), _Ws()
    a.offer(wsa, "Brick Siege")
    b.poll(wsb)

    wsa.input.down = {"left"}         # the kid holding console A
    wsb.input.down = {"a"}            # the kid holding console B
    _run(a, b, wsa, wsb, 8)

    assert wsa.netplay.frame == wsb.netplay.frame > 3
    ra = wsa.input.players
    rb = wsb.input.players
    # Both screens agree about who is who -- the whole point.
    assert ra.held("left", 0) and rb.held("left", 0)
    assert ra.held("a", 1) and rb.held("a", 1)
    assert not ra.held("a", 0) and not rb.held("a", 0)
    assert ra.count() == 2 and rb.count() == 2


def test_a_dead_link_stalls_both_consoles_and_neither_runs_ahead():
    air, clock, a, b = _pair(lambda ws, title: True)
    _see_each_other(a, b, clock)
    wsa, wsb = _Ws(), _Ws()
    a.offer(wsa, "Brick Siege")
    b.poll(wsb)
    _run(a, b, wsa, wsb, 6)
    at = wsa.netplay.frame

    air.deaf = True                   # somebody walked into the next room
    _run(a, b, wsa, wsb, 10)
    assert wsa.netplay.frame == wsb.netplay.frame, "they stop TOGETHER"
    assert wsa.netplay.frame <= at + netplay.REDUNDANCY

    air.deaf = False
    _run(a, b, wsa, wsb, 10)
    assert wsa.netplay.frame > at, "and they carry on when it comes back"


# -- net.* over the radio ---------------------------------------------------

def test_a_cart_message_crosses_and_is_pumped_not_delivered_mid_send():
    air, clock, a, b = _pair()
    got = []
    b.net.on_message(got.append)
    assert a.net.send({"hi": 5}) is True
    b.poll()
    assert got == [], "queued, not dispatched inside the transport"
    b.net.pump()
    assert got == [{"hi": 5}]


def test_an_oversized_message_is_dropped_whole_rather_than_truncated():
    """A half-delivered message reads as a game-logic bug at the other end, so
    the honest failure is to refuse it and say so."""
    air, clock, a, b = _pair()
    got = []
    b.net.on_message(got.append)
    assert a.net.send("x" * (moy_espnow.EspNowNet.MAX + 10)) is False
    b.poll()
    b.net.pump()
    assert got == []


def test_net_peers_counts_what_the_radio_can_hear():
    air, clock, a, b = _pair()
    assert a.net.peers() == 0
    _see_each_other(a, b, clock)
    assert a.net.peers() == 1


# -- robustness -------------------------------------------------------------

def test_a_desynced_ring_is_recovered_by_an_active_cycle_not_a_retry():
    """The documented cure for `buffer error` is active(False)/active(True). A
    retry raises forever, which on a frame loop is a dead console."""
    air, clock, a, _b = _pair()

    calls = []

    def boom(timeout=0):
        calls.append(1)
        raise ValueError("ESPNow.recv(): buffer error")

    a.radio.irecv = boom
    a.poll(None)
    assert calls == [1], "one raise, then recovery -- not a retry loop"
    assert a.active is True, "the link recovered rather than giving up"
    assert a.radio.on is True, "and it went through a real active cycle"
    assert ("active", False) in a.radio.log
    assert a.radio.cfg["rxbuf"] == moy_espnow.RXBUF


def test_recover_reapplies_the_phy_rate_and_is_counted():
    """An active() cycle silently resets the PHY to MicroPython's 1M default,
    so _recover must re-apply the rate it started with -- until 2026-08-24 it
    did not, and one mid-match ring error put the rest of the session at 1Mbps
    with no meter naming it. The recover count rides stats() so a session that
    churned says so."""
    air, clock, a, _b = _pair()
    a._rate = 0x0B                     # what start() records when it builds the radio

    def boom(timeout=0):
        raise ValueError("ESPNow.recv(): buffer error")

    a.radio.irecv = boom
    a.poll(None)
    assert a.recovers == 1
    assert a.stats()["recovers"] == 1
    down = a.radio.log.index(("active", False))
    up = a.radio.log.index(("active", True), down)
    assert ("config", ("rate",)) in a.radio.log[up:], \
        "the rate must be re-applied AFTER the ring comes back up"


def test_drain_input_dispatches_inputs_and_parks_everything_else():
    """The Player's pre-tick drain must be mid-frame safe: T_INPUT goes to the
    session immediately (that is the point -- fresher input for this tick), and
    ANY other frame is parked for the tail poll, because a T_START can relaunch
    the cart and a T_BYE can tear down the match from inside the Player's own
    frame."""
    air, clock, a, _b = _pair()

    got = []

    class _S:
        session = 0

        def on_packet(self, m, now_ms=None):
            got.append(bytes(m))
            return True

    a.session = _S()
    peer_mac = b"\xaa" * 6
    inp = bytes([moy_espnow.PROTO, netplay.T_INPUT, 0, 1, 0, 1, 0, 5])
    beacon = bytes([moy_espnow.PROTO, netplay.T_BEACON, 0]) + b"kid|tdeck|"
    a.radio.inbox.append((peer_mac, inp))
    a.radio.inbox.append((peer_mac, beacon))
    n = a.drain_input()
    assert n == 2
    assert got == [inp], "only the input frame reaches the session mid-frame"
    assert a._deferred == [(peer_mac, beacon)]
    a.session = None
    a.poll(None)
    assert a._deferred == []
    assert peer_mac in a.peers, "the parked beacon was dispatched by the tail poll"


def test_a_stale_input_frame_from_a_previous_match_cannot_reach_a_new_one():
    air, clock, a, b = _pair(lambda ws, title: True)
    _see_each_other(a, b, clock)
    wsa, wsb = _Ws(), _Ws()
    a.offer(wsa, "Brick Siege")
    b.poll(wsb)
    old = a.session.session
    a.end_match()
    b.end_match()
    a.state = 1
    b.state = 1
    _see_each_other(a, b, clock)
    a.offer(wsa, "Brick Siege")
    assert a.session.session != old, "a new match gets a new session id"
    stale = bytes(bytearray([netplay.PROTO, netplay.T_INPUT, old, 9, 0, 0xFF,
                             0, 0, 0]))
    a._dispatch(wsa, b"\x02" * 6, stale)
    assert a.session.packets_in == 0


def test_status_reports_none_for_the_frame_when_there_is_no_match():
    """Never 0 -- a frozen 0 is also what a broken lockstep looks like, and that
    ambiguity is exactly what hid the scale-fold meter for a month."""
    air, clock, a, _b = _pair()
    assert a.status() == (True, 0, False, None)


def test_the_higher_mac_asks_rather_than_waiting_forever():
    """Both kids may already be PLAYING when they find each other -- neither
    side's Player.start will ever run again, so a console that loses the MAC
    comparison and just waits is a match that never happens. It asks instead."""
    launched = []
    air, clock, a, b = _pair(lambda ws, title: launched.append(title) or True)
    _see_each_other(a, b, clock)
    wsa, wsb = _Ws(), _Ws()
    a.cart = b.cart = "Brick Siege"

    assert b.offer(wsb, "Brick Siege") is False, "the higher MAC does not host"
    a.poll(wsa)                       # the JOIN lands: A hosts and re-runs
    assert a.session is not None and a.session.index == 0
    assert launched == ["Brick Siege"], "the host went back to frame zero too"
    b.poll(wsb)                       # ...and the START comes back
    assert wsb.netplay is not None and wsb.netplay.index == 1
    assert wsa.netplay.seed == wsb.netplay.seed


def test_the_host_config_wins_so_two_tunings_cannot_desync_the_sims():
    """'Make it mine' lets each kid retune their own copy. Two consoles running
    the same cart with different tuning diverge on frame one -- silently, since
    both screens still look like a working game."""
    air, clock, a, b = _pair(lambda ws, title: True)
    _see_each_other(a, b, clock)
    wsa, wsb = _Ws(), _Ws()
    wsa.config = {"enemies": 9}
    wsb.config = {"enemies": 3}
    a.offer(wsa, "Brick Siege")
    b.poll(wsb)
    assert wsa.netplay.config == {"enemies": 9}
    assert wsb.netplay.config == {"enemies": 9}, "the guest takes the host's"


def test_an_oversized_config_is_dropped_rather_than_half_applied():
    air, clock, a, b = _pair(lambda ws, title: True)
    _see_each_other(a, b, clock)
    wsa, wsb = _Ws(), _Ws()
    wsa.config = {("k%d" % i): i for i in range(40)}
    a.offer(wsa, "Brick Siege")
    b.poll(wsb)
    assert wsa.netplay.config is None and wsb.netplay.config is None, (
        "both keep their own defaults -- wrong together beats wrong apart")


def test_announce_cannot_downgrade_a_live_match():
    """The Player announces the cart on EVERY start, restart included -- and the
    restart is exactly what a forming match triggers. Writing "available" over
    "matched" there tells the peer we are free while we are already playing it."""
    air, clock, a, b = _pair(lambda ws, title: True)
    _see_each_other(a, b, clock)
    wsa, wsb = _Ws(), _Ws()
    a.offer(wsa, "Brick Siege")
    assert a.state == 2
    a.announce("Brick Siege", 1)          # what Player.start does on the restart
    assert a.state == 2, "a live match outranks the caller's idea of the state"
    a.end_match()
    a.announce("Brick Siege", 1)
    assert a.state == 1, "...and once the match is over the state is the caller's"


def test_a_restart_for_a_forming_match_does_not_stop_the_radio():
    """THE ON-GLASS BUG (2026-08-22). A match forms by the peer's invite arriving
    while this console is ALREADY playing: the link sets ws.netplay and re-runs
    the cart from frame zero. The dying run tears down on the way through, and it
    used to stop the radio there -- killing the session that caused the restart.
    It read as the host stalling at frame 0 forever while the guest played on
    alone, and nothing was logged anywhere."""
    from runtime import host_app

    ws = host_app.build_workstation(None)
    stops = []

    class _Link:
        net = None

        def stop(self):
            stops.append(1)

    ws.link = _Link()

    # A session is already arranged for the run about to start.
    ws.netplay = object()
    ws.player.release_world()
    assert stops == [], "the radio must survive a restart that a match asked for"
    assert ws.netplay is not None, "and so must the session"

    # An ordinary exit, with nothing pending, still puts the radio away.
    ws.netplay = None
    ws.player.release_world()
    assert stops == [1], "an ordinary exit stops the radio"


def _lose_invites(air, n):
    """Swallow the next n INVITES -- acked and never delivered, which is the
    measured behaviour of this radio rather than a hypothetical. Dropping by
    FRAME TYPE, not by addressing mode: the handshake is broadcast now (see
    EspNowLink._for_us for the on-glass reason), so "the invite went missing"
    can no longer be spelled as "a unicast went missing"."""
    real = air.carry
    state = {"left": n}

    def carry(src, dst, payload):
        if state["left"] > 0 and len(payload) > 1 and payload[1] == netplay.T_START:
            state["left"] -= 1
            return
        real(src, dst, payload)
    air.carry = carry


def test_a_lost_invite_is_re_sent_until_the_guest_answers():
    """THE ON-GLASS DEADLOCK (2026-08-22). START was sent ONCE on a link whose
    ack lies (64 of 200 acked-and-lost, measured). One dropped invite left the
    host stalled at frame 0 forever, showing a second tank nobody drove, while
    the guest played on alone -- and the guest's JOINs were ignored because the
    host believed it was already matched."""
    air, clock, a, b = _pair(lambda ws, title: True)
    _see_each_other(a, b, clock)
    wsa, wsb = _Ws(), _Ws()
    a.cart = b.cart = "Brick Siege"

    _lose_invites(air, 1)                       # the invite evaporates
    assert a.offer(wsa, "Brick Siege") is True
    b.poll(wsb)
    assert wsb.netplay is None, "the guest never heard it (that is the premise)"

    clock.advance(moy_espnow.BEACON_MS + 1)  # the host chases on the beacon tick
    a.poll(wsa)
    b.poll(wsb)
    assert wsb.netplay is not None, "the re-sent invite landed"
    assert wsb.netplay.index == 1
    assert wsa.netplay.seed == wsb.netplay.seed
    assert wsa.netplay.session == wsb.netplay.session


def test_a_guest_still_asking_gets_told_again():
    """The other half of the same heal: the guest that lost the invite keeps
    sending JOIN, and a host that thinks it is already matched used to ignore
    it. Same session and seed, so re-answering costs nothing."""
    air, clock, a, b = _pair(lambda ws, title: True)
    _see_each_other(a, b, clock)
    wsa, wsb = _Ws(), _Ws()
    a.cart = b.cart = "Brick Siege"
    _lose_invites(air, 1)
    a.offer(wsa, "Brick Siege")
    b.poll(wsb)
    assert wsb.netplay is None

    b._unicast(a.mac, bytes(bytearray([netplay.PROTO, netplay.T_JOIN])))
    a.poll(wsa)                              # the host answers the ask
    b.poll(wsb)
    assert wsb.netplay is not None


def test_a_host_whose_peer_never_answers_goes_back_to_playing_solo():
    """A console that waits forever is the worst outcome of the lot: the game is
    frozen and nothing says why. After a bounded chase it gives up and re-runs
    the cart, so the kid gets a one-player game instead of a dead screen."""
    launched = []
    air, clock, a, b = _pair(lambda ws, title: launched.append(title) or True)
    _see_each_other(a, b, clock)
    wsa, wsb = _Ws(), _Ws()
    a.cart = b.cart = "Brick Siege"
    _lose_invites(air, 10_000)                  # the peer is simply gone
    a.offer(wsa, "Brick Siege")
    assert a.session is not None

    for _ in range(moy_espnow.START_TRIES + 2):
        clock.advance(moy_espnow.BEACON_MS + 1)
        a.poll(wsa)
    assert a.session is None, "the host gave up rather than stalling forever"
    assert launched[-1] == "Brick Siege", "and re-ran the cart as a solo game"


def test_the_chase_stops_the_moment_the_guest_is_heard():
    air, clock, a, b = _pair(lambda ws, title: True)
    _see_each_other(a, b, clock)
    wsa, wsb = _Ws(), _Ws()
    a.cart = b.cart = "Brick Siege"
    a.offer(wsa, "Brick Siege")
    b.poll(wsb)
    # One exchange in each direction, then the host should stop re-inviting.
    wsb.netplay.advance(0)
    a.poll(wsa)
    assert a.session.packets_in > 0
    clock.advance(moy_espnow.BEACON_MS + 1)
    a.poll(wsa)
    assert a._start_frame is None, "an answered invite is not chased"
    assert a.session is not None


def test_hearing_a_peer_registers_it_with_the_radio():
    """ESP-NOW delivers a UNICAST only from a registered peer. A console with
    only the broadcast address in its table heard every beacon and every input
    frame -- both broadcast -- and silently dropped the one unicast in the
    protocol, the invite. Measured on glass: 376 input frames and 20 beacons
    received against ZERO STARTs, while both boards insisted they could see each
    other (2026-08-22)."""
    air, clock, a, b = _pair()
    assert a.radio.peers == {BROADCAST}, "only broadcast to begin with"
    _see_each_other(a, b, clock)
    assert b.mac in a.radio.peers, "hearing Bo must make Bo addressable"
    assert a.mac in b.radio.peers


def test_the_handshake_is_broadcast_and_addressed_in_the_payload():
    """The invite was the protocol's one unicast and it kept vanishing on glass
    while every broadcast arrived. ESP-NOW delivers a unicast only from a
    registered peer, and that peer table is cleared by an active() cycle and
    races the first beacon -- so the invite is broadcast now, with the
    destination in the payload, and the wrong console ignores it."""
    air, clock, a, b = _pair(lambda ws, title: True)
    _see_each_other(a, b, clock)
    wsa, wsb = _Ws(), _Ws()
    seen = []
    real = air.carry
    air.carry = lambda src, dst, p: (seen.append((dst, p[1])), real(src, dst, p))[1]

    a.offer(wsa, "Brick Siege")
    kinds = {k: d for d, k in seen}
    assert kinds[netplay.T_START] == BROADCAST, "the invite goes to everyone"
    b.poll(wsb)
    assert wsb.netplay is not None


def test_an_invite_meant_for_somebody_else_is_ignored():
    """Broadcasting it means every console in the room hears it, so the address
    in the payload is what stops a third kid being dragged into the match."""
    air, clock, a, b = _pair(lambda ws, title: True)
    c = _link(air, clock, b"\x03" * 6, "tdeck", "Cy", lambda ws, t: True)
    _see_each_other(a, b, clock)
    c.announce("Brick Siege", 1)
    clock.advance(10)
    for lk in (a, b, c):
        lk.poll()
    wsa, wsb, wsc = _Ws(), _Ws(), _Ws()
    a.offer(wsa, "Brick Siege")          # a invites b (the lower of the two)
    b.poll(wsb)
    c.poll(wsc)
    assert wsb.netplay is not None, "the addressee joined"
    assert wsc.netplay is None, "the bystander did not"
