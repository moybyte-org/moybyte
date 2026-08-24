"""Deterministic lockstep for two-console play (#65 Phase 2 / #7).

The radio is not here. What is here is the part that decides whether two consoles
show the same game -- the frame clock, the input exchange, the stall -- driven
through a pair of in-process endpoints so both sides of a match run with no
hardware, the way LoopbackNet does for net.*.

The measurements these numbers come from (T-Deck <-> Guition, 2026-08-20) are in
netplay.py's header; the tests below pin the BEHAVIOUR those numbers bought:
loss is healed by redundancy rather than retransmit, a missing input stalls
instead of guessing, and the two consoles' player slots agree about who is who.
"""

from runtime import netplay, players as players_mod

BUTTONS = ("left", "right", "up", "down", "a", "b", "run")


class _Wire:
    """Two endpoints with a controllable link: every packet is queued, and the
    test decides which ones are delivered. `drop` makes loss deliberate rather
    than hoped-for."""

    def __init__(self):
        self.queues = {0: [], 1: []}
        self.drop = set()          # frame numbers whose packets never arrive
        self.cut = set()           # senders whose packets all vanish
        self.sent = {0: 0, 1: 0}

    def sender(self, index):
        def send(payload):
            self.sent[index] += 1
            if index in self.cut:
                return
            newest = payload[3] | (payload[4] << 8)
            if newest in self.drop:
                return
            self.queues[1 - index].append(payload)
        return send

    def deliver(self, index, session):
        for p in self.queues[index]:
            session.on_packet(p)
        self.queues[index] = []


class _Input:
    """The local console's own controls: a bare held-set, the shape
    netplay.mask_of reads."""

    def __init__(self):
        self.down = set()

    def held(self, name, player=None):
        return name in self.down


def _match(wire, seed=1234, **kw):
    """Two consoles, one match. Console A's kid is global player 0, console B's
    is global player 1 -- the asymmetry the whole slot-0 override exists for."""
    out = []
    for index in (0, 1):
        inp = _Input()
        router = players_mod.PlayerRouter(inp)
        s = netplay.LockstepSession(index, seed, wire.sender(index),
                                    BUTTONS, router, **kw)
        out.append((s, router, inp))
    return out


def _step(a, b, wire, frames=1):
    """Run `frames` lockstep frames on both consoles, exchanging packets between
    them the way a real pair does: each console emits during its own advance,
    and reads whatever has landed before the next one."""
    (sa, _ra, ia), (sb, _rb, ib) = a, b
    results = []
    for _ in range(frames):
        wire.deliver(0, sa)
        wire.deliver(1, sb)
        oka = sa.advance(netplay.mask_of(ia, BUTTONS))
        okb = sb.advance(netplay.mask_of(ib, BUTTONS))
        results.append((oka, okb))
    return results


# -- the tape ---------------------------------------------------------------

def test_the_tape_reads_a_stale_slot_as_absent_not_as_old_input():
    """The single most important line in the module: a ring slot that has been
    lapped must read ABSENT, so the session stalls. Returning the old mask would
    be a plausible input and a silent, permanent desync."""
    t = netplay.InputTape()
    t.put(5, 0x11)
    assert t.get(5) == 0x11
    assert t.get(6) is None
    # Lap the ring: frame 5 + 256 lands in the same slot.
    t.put(5 + netplay._TAPE, 0x22)
    assert t.get(5 + netplay._TAPE) == 0x22
    assert t.get(5) is None, "a lapped slot must not answer for the old frame"


def test_mask_round_trips_every_button():
    inp = _Input()
    for i, name in enumerate(BUTTONS):
        inp.down = {name}
        assert netplay.mask_of(inp, BUTTONS) == 1 << i
    inp.down = set(BUTTONS)
    assert netplay.mask_of(inp, BUTTONS) == (1 << len(BUTTONS)) - 1
    inp.down = set()
    assert netplay.mask_of(inp, BUTTONS) == 0


# -- the clock --------------------------------------------------------------

def test_a_match_advances_only_once_the_peer_input_has_arrived():
    wire = _Wire()
    a, b = _match(wire)
    (sa, _ra, _ia), (sb, _rb, _ib) = a, b
    # Frame 0 needs the peer's frame-0 input, which is only emitted once the peer
    # has run its own frame 0 -- so the very first pass stalls on both sides.
    assert _step(a, b, wire) == [(False, False)]
    assert sa.frame == 0 and sb.frame == 0
    assert sa.waiting and sb.waiting
    # Each stalled pass still EMITTED, so the next one has what it needs.
    assert _step(a, b, wire) == [(True, True)]
    assert sa.frame == 1 and sb.frame == 1
    assert not sa.waiting and not sb.waiting


def test_both_consoles_stay_on_the_same_frame():
    wire = _Wire()
    a, b = _match(wire)
    _step(a, b, wire, frames=40)
    assert a[0].frame == b[0].frame
    assert a[0].frame > 30, "40 passes with a clean link should nearly all advance"


def test_a_dropped_packet_is_healed_by_redundancy_not_by_a_retransmit():
    """The radio's ack lies (64 of 200 delivered while send() returned True for
    all 200, measured 2026-08-20), so loss must be self-healing. Every packet
    carries the last REDUNDANCY frames, so losing one costs nothing at all."""
    wire = _Wire()
    a, b = _match(wire)
    _step(a, b, wire, frames=5)
    before = a[0].frame
    # Drop the packets carrying frames 8 and 9 outright -- no retransmit exists.
    wire.drop = {8, 9}
    _step(a, b, wire, frames=2)
    wire.drop = set()
    _step(a, b, wire, frames=6)
    assert a[0].frame == b[0].frame
    assert a[0].frame > before + 5, "redundancy should have covered the loss"
    assert a[0].stalls == 1 and b[0].stalls == 1, (
        "only the unavoidable frame-0 stall: a 2-frame gap is inside the "
        "4-frame redundancy window, so nobody should have waited")


def test_losing_more_than_the_redundancy_window_stalls_and_then_recovers():
    """The honest failure mode: past the redundancy window the sim STOPS. It
    does not extrapolate -- that would desync both consoles invisibly."""
    wire = _Wire()
    a, b = _match(wire)
    _step(a, b, wire, frames=5)
    wire.drop = set(range(7, 20))          # a long outage, well past REDUNDANCY
    stalled = _step(a, b, wire, frames=10)
    assert all(not ok for ok, _ in stalled[-5:]), "the sim must stop, not guess"
    held = a[0].frame
    assert b[0].frame == held, "both consoles stop on the SAME frame"
    wire.drop = set()
    _step(a, b, wire, frames=10)
    assert a[0].frame > held, "and it recovers once the link comes back"
    assert a[0].frame == b[0].frame


# -- who is who -------------------------------------------------------------

def test_each_console_drives_its_own_global_slot_and_reads_the_peer_on_the_other():
    """The asymmetry that makes two screens ONE game: the local kid is global
    player 0 on console A and player 1 on console B, and both consoles must
    answer btn(name, 0) with the SAME player's buttons."""
    wire = _Wire()
    a, b = _match(wire)
    (sa, ra, ia), (sb, rb, ib) = a, b
    assert (sa.index, sa.peer) == (0, 1)
    assert (sb.index, sb.peer) == (1, 0)

    ia.down = {"left"}          # the kid holding console A
    ib.down = {"right"}         # the kid holding console B
    _step(a, b, wire, frames=4)

    # Player 0 is A's kid -- on BOTH screens.
    assert ra.held("left", 0) is True and rb.held("left", 0) is True
    # Player 1 is B's kid -- on both screens.
    assert ra.held("right", 1) is True and rb.held("right", 1) is True
    # And neither console has them crossed.
    assert ra.held("right", 0) is False and rb.held("right", 0) is False
    assert ra.held("left", 1) is False and rb.held("left", 1) is False
    assert ra.count() == 2 and rb.count() == 2


def test_press_edges_are_derived_from_held_so_both_consoles_agree():
    """Edges are computed from consecutive HELD masks on each console rather
    than sampled locally, so a press is one frame on both screens -- which is
    what a fire button needs to be in a game two people can both see."""
    wire = _Wire()
    a, b = _match(wire)
    (_sa, ra, ia), (_sb, rb, _ib) = a, b
    _step(a, b, wire, frames=3)
    assert ra.pressed("a", 0) is False

    ia.down = {"a"}
    _step(a, b, wire, frames=1)
    while not ra.held("a", 0):              # walk it through the input delay
        _step(a, b, wire, frames=1)
    assert ra.pressed("a", 0) is True, "the press edge fires on console A"
    assert rb.pressed("a", 0) is True, "and on the SAME frame on console B"

    _step(a, b, wire, frames=1)
    assert ra.held("a", 0) is True and ra.pressed("a", 0) is False, "held, no re-edge"
    assert rb.held("a", 0) is True and rb.pressed("a", 0) is False


def test_the_router_does_not_auto_advance_a_session_slot():
    """The session writes held and derives the edge in one breath, mid-tick. The
    router's own begin_frame runs EARLIER in the loop, so if it also advanced
    these slots it would clear the press the cart is about to read."""
    wire = _Wire()
    a, b = _match(wire)
    (_sa, ra, ia), _ = a, b
    ia.down = {"a"}
    for _ in range(6):
        _step(a, b, wire, frames=1)
        if ra.pressed("a", 0):
            break
    assert ra.pressed("a", 0) is True
    ra.begin_frame()                    # what console.handle_input calls each frame
    assert ra.pressed("a", 0) is True, (
        "PlayerRouter.begin_frame must leave a non-auto slot alone")


# -- determinism ------------------------------------------------------------

def test_construction_seeds_both_consoles_identically():
    """rnd() must agree on the two consoles or the sims diverge on the first
    draw. The seed is applied at CONSTRUCTION because that is the moment both
    sides share -- the cart's _init runs immediately after."""
    import random
    wire = _Wire()
    _match(wire, seed=777)
    first = [random.random() for _ in range(5)]
    wire2 = _Wire()
    _match(wire2, seed=777)
    assert [random.random() for _ in range(5)] == first
    wire3 = _Wire()
    _match(wire3, seed=778)
    assert [random.random() for _ in range(5)] != first


def test_the_tick_is_fixed_and_thirty_hertz():
    wire = _Wire()
    a, _b = _match(wire)
    assert abs(a[0].dt - 1.0 / 30.0) < 1e-9
    assert netplay.TICK_HZ == 30
    assert netplay.DELAY == 1, "matches START at one frame of delay (33ms)"
    assert netplay.DELAY_MAX == 2, "...and stall pressure can only raise it to two"
    assert netplay.REDUNDANCY == 4


# -- hygiene ----------------------------------------------------------------

def test_a_packet_from_a_previous_match_is_ignored():
    """A stale packet still in flight when a new match starts carries the old
    session id. Landing it would inject a dead game's inputs into a live one."""
    wire = _Wire()
    a, b = _match(wire, session=7)
    (sa, _ra, _ia), _ = a, b
    stale = bytearray([netplay.PROTO, netplay.T_INPUT, 3, 0, 0, 0xFF, 0, 0, 0])
    assert sa.on_packet(bytes(stale)) is False
    stale[2] = 7
    assert sa.on_packet(bytes(stale)) is True


def test_junk_and_other_frame_types_are_refused():
    wire = _Wire()
    a, _b = _match(wire)
    s = a[0]
    assert s.on_packet(b"") is False
    assert s.on_packet(b"\x99\x04\x00\x00\x00\x00") is False, "wrong protocol"
    assert s.on_packet(bytes([netplay.PROTO, netplay.T_BEACON, 0, 0, 0])) is False
    assert s.on_packet(bytes([netplay.PROTO, netplay.T_INPUT])) is False, "truncated"


def test_close_returns_the_console_to_one_local_player():
    wire = _Wire()
    a, _b = _match(wire)
    (s, r, _i) = a
    _step(a, _b, wire, frames=3)
    assert r.count() == 2
    s.close()
    assert r.count() == 1
    assert r.held("left", 1) is False


def test_the_frame_counter_survives_its_sixteen_bit_wrap():
    """36 minutes at 30Hz. The wire carries 16 bits and the peer is always
    within a few frames, so the nearest congruent value is the right one."""
    wire = _Wire()
    a, _b = _match(wire)
    s = a[0]
    s.frame = 65534
    assert s._expand(65535) == 65535
    assert s._expand(1) == 65537, "past the wrap, not 36 minutes into the past"
    s.frame = 2
    assert s._expand(65535) == -1, "and back across it the other way"


# -- the deadlock -----------------------------------------------------------

def test_a_stalled_console_is_served_the_frame_it_is_stuck_on():
    """THE ON-GLASS DEADLOCK (2026-08-22). A fixed redundancy window heals a gap
    only while the sender is still inside it. Once a console has been stalled
    longer than REDUNDANCY frames it has fallen out, and then NEITHER side can
    move: each is waiting for a frame the other has scrolled past, and because
    both are stalled neither ever sends the frame the other wants. Two boards ran
    happily to frame ~150 and froze solid with packets still flowing both ways.

    The cure is that every packet says which frame its sender is waiting for, so
    the peer knows how far back to reach."""
    wire = _Wire()
    a, b = _match(wire)
    (sa, _ra, ia), (sb, _rb, ib) = a, b
    _step(a, b, wire, frames=12)
    stuck = sa.frame
    assert stuck == sb.frame

    # B goes silent for far longer than the redundancy window. Both consoles end
    # up stalled -- which is correct, and is also exactly the state the old
    # protocol could never climb out of.
    wire.cut = {1}
    _step(a, b, wire, frames=netplay.REDUNDANCY * 6)
    # It coasts DELAY frames on the lookahead the input delay buys, then stops.
    stalled_at = sa.frame
    assert stalled_at <= stuck + netplay.DELAY
    _step(a, b, wire, frames=10)
    assert sa.frame == stalled_at, "A is stalled waiting for B, as it must be"

    wire.cut = set()
    _step(a, b, wire, frames=40)
    assert sa.frame > stalled_at, "A got the frame it was stuck on and moved again"
    # Within one frame: the harness advances A before B inside each pass, so a
    # perfect tie only happens between passes. Lockstep's promise is that they
    # cannot run APART, not that they are sampled at the same instant.
    assert abs(sa.frame - sb.frame) <= 1, "and the two clocks converged"
    assert sa.dead is False and sb.dead is False


def test_a_packet_says_which_frame_its_sender_is_waiting_for():
    """The one field that breaks the deadlock. Checked on a STALLED session, so
    the frame it advertises cannot drift under the assertion."""
    wire = _Wire()
    a, b = _match(wire)
    _step(a, b, wire, frames=6)
    sa = a[0]
    wire.cut = {1}                       # A is now stuck; its frame stops moving
    _step(a, b, wire, frames=netplay.REDUNDANCY + 3)
    stuck = sa.frame
    wire.queues[1] = []
    _step(a, b, wire, frames=1)
    payload = wire.queues[1][-1]
    need = payload[5] | (payload[6] << 8)
    assert need == stuck, "the packet names the frame its sender is waiting for"


def test_a_match_that_cannot_heal_declares_itself_dead():
    """A console that waits forever is the worst outcome: the game is frozen and
    nothing says why. The session says so, and the radio puts the kid back in a
    one-player game."""
    wire = _Wire()
    a, b = _match(wire)
    (sa, _ra, ia), _ = a, b
    _step(a, b, wire, frames=4)
    assert sa.dead is False
    wire.cut = {1}
    _step(a, b, wire, frames=netplay.GIVE_UP + 5)
    assert sa.dead is True, "it stops pretending the match is alive"


def test_the_session_paces_itself_to_its_own_tick_rate():
    """The console's frame loop runs as fast as the board manages (~50fps on an
    S3) and the lockstep dt is fixed at 1/30. Ticking once per FRAME therefore
    advances 1.67 seconds of game time per second of wall time -- both consoles
    agreeing perfectly on a game played in fast-forward, which is exactly what
    two boards did on the desk (2026-08-22)."""
    wire = _Wire()
    a, _b = _match(wire)
    s = a[0]
    assert s.tick_ms == 33

    t = 10_000
    assert s.due(t) is True, "the first tick is always due"
    assert s.due(t) is False, "...and the next one is not, one millisecond later"
    assert s.due(t + 32) is False
    assert s.due(t + 33) is True

    # 50 frames of a 50fps loop must yield ~30 ticks, not 50.
    ticks = sum(1 for i in range(50) if s.due(t + 40 + i * 20))
    assert 28 <= ticks <= 32, ticks


def test_a_long_stall_does_not_come_back_as_a_burst_of_catch_up_frames():
    wire = _Wire()
    a, _b = _match(wire)
    s = a[0]
    s.due(0)
    late = 5_000                       # five seconds of nothing
    assert s.due(late) is True
    assert s.due(late + 1) is False, "the schedule re-based rather than owing 150 ticks"


def test_even_one_tick_of_debt_is_dropped_not_burst_repaid():
    """The old threshold re-based only past 250ms, so a 100ms hitch owed three
    catch-up ticks that then fired at the LOOP rate -- outrunning the peer's
    30Hz emission, which is how one stall became a coupled oscillation
    (measured on glass 2026-08-25: p5 arrival margin ~0ms at DELAY=2 and ~30%
    stalls at DELAY=1, both of which relaxed when the debt was dropped)."""
    wire = _Wire()
    a, _b = _match(wire)
    s = a[0]
    s.due(0)
    assert s.due(100) is True, "the late tick itself still fires"
    assert s.due(101) is False, "...but the debt is gone, not owed as a burst"
    assert s.due(100 + s.tick_ms) is True, "the next tick is a full period out"


def test_stall_ticks_counts_ticks_not_retries():
    """The Player retries a stalled tick every loop frame now, so `stalls`
    (every stalled advance) inflates with the loop rate; `stall_ticks` is the
    honest per-tick meter the benches read."""
    wire = _Wire()
    a, _b = _match(wire)
    s = a[0]
    for _ in range(5):                 # five loop-frame retries of ONE tick
        assert s.advance(0) is False
    assert s.stall_ticks == 1
    assert s.stalls == 5


def test_stall_pressure_raises_the_delay_once_and_never_lowers_it():
    """The adaptive delay: matches start at DELAY=1 (33ms); a window with
    ESCALATE_AT distinct stalled ticks buys the second tick. Raise-only,
    because a LOWER delay mid-match can overwrite an input frame the peer has
    already played (the newer sample lands on an already-emitted frame)."""
    wire = _Wire()
    a, b = _match(wire)
    (sa, _ra, _ia), (sb, _rb, _ib) = a, b
    assert sa.delay == 1 and sb.delay == 1, "matches start at one frame"
    # distinct one-tick stalls: every other iteration sa's inbox is withheld
    # until its advance has already failed once, so each is a stalled TICK
    # healed by a loop-frame retry -- the P4-pair pattern, not one long outage
    for k in range(3 * netplay.ESCALATE_TICKS):
        if sa.advance(0) is False:
            wire.deliver(0, sa)          # the input lands; the retry heals it
            sa.advance(0)
        sb.advance(0)
        wire.deliver(1, sb)
        if k % 2 == 0:
            wire.deliver(0, sa)
        if k == netplay.ESCALATE_TICKS - 10:
            assert sa.delay == 1, "the FIRST window is formation grace, never a raise"
    assert sa.delay == 2, "sustained stall pressure bought the second tick"
    for _ in range(netplay.ESCALATE_TICKS + 10):
        sa.advance(0)
        sb.advance(0)
        wire.deliver(0, sa)
        wire.deliver(1, sb)
    assert sa.delay == 2, "clean windows never lower it back"


def test_the_delay_one_phase_controller_is_guest_only_and_gated():
    """At DELAY=1 the GUEST slews its tick phase toward SLEW_TARGET_MS of
    measured arrival margin; the host's clock is the anchor, and DELAY=2
    needs no controller at all (its margin floor is a whole extra tick)."""
    wire = _Wire()
    a, b = _match(wire, delay=1)
    (host, _rh, _ih), (guest, _rg, _ig) = a, b
    t = 1000
    host.due(t)
    guest.due(t)
    nx0 = guest._next_ms
    for _ in range(12):
        host.advance(0, t)
        guest.advance(0, t)
        for p in wire.queues[0]:
            host.on_packet(p, t)
        wire.queues[0] = []
        for p in wire.queues[1]:
            guest.on_packet(p, t)
        wire.queues[1] = []
        t += 33
    assert guest._m_ema is not None, "the guest measured arrival margin"
    assert host._m_ema is None, "the host never runs the controller"
    # inputs were banked a full tick before each need, so the margin is FAT --
    # and a fat margin moves NOTHING (the slew is one-sided: only a thin
    # margin pushes the phase later; pulling earlier shaves margin for zero
    # latency benefit and was measured chasing the stall cliff):
    assert guest._next_ms == nx0, "a fat margin leaves the phase alone"
    # Starve the margin directly: seed a thin EMA and stamp the next frame's
    # input as having landed 2ms before the need (the wire above cannot thin
    # the margin -- the host emits a frame ahead, so real deliveries are
    # always pre-banked; on glass the thinning comes from loss + jitter).
    guest._m_ema = 5.0
    f = guest.frame
    guest._arr_f[f & 63] = f
    guest._arr_t[f & 63] = t - 2
    nx1 = guest._next_ms
    assert guest.advance(0, t) is True
    assert guest._next_ms == nx1 + 1, "a thin margin pushes the guest's phase later"
    g_nx = guest._next_ms
    host.delay = 2
    guest.delay = 2
    host.advance(0, t)
    guest.advance(0, t)
    assert guest._next_ms == g_nx, "at DELAY=2 the controller is off"


# -- the shared random stream ------------------------------------------------

def test_drawing_cannot_move_the_logic_random_stream():
    """THE DESYNC (2026-08-22). Seeding once at match start is correct only
    while both consoles then draw from the stream the same number of times --
    and they do not. Brick Siege shakes the screen with rnd() in _draw, the two
    boards render at different rates (6372 frames against 9335 over one measured
    window), and from the first explosion the two sims drew different numbers
    and played different games. Both screens looked fine the whole time."""
    import random
    wire = _Wire()
    a, b = _match(wire, seed=4242)
    (sa, _ra, _ia), (sb, _rb, _ib) = a, b

    seen_a, seen_b = [], []
    for i in range(24):
        wire.deliver(0, sa)
        wire.deliver(1, sb)
        # Console A renders twice as often as console B, and its draw code asks
        # for randomness -- exactly what the real pair does.
        if sa.advance(0):
            seen_a.append(random.random())
            for _ in range(3):
                random.random()          # A's _draw
        if sb.advance(0):
            seen_b.append(random.random())
            if i % 2:
                random.random()          # B's _draw, at its own rate
    n = min(len(seen_a), len(seen_b))
    assert n > 8
    assert seen_a[:n] == seen_b[:n], (
        "the logic stream must be identical however much either side draws")


def test_the_per_frame_seed_is_mixed_not_a_counter():
    """A cart that asks for one number a frame (`rnd(1.0) < 0.012` -- the enemy
    fire roll) would otherwise be reading the low bits of a frame counter."""
    import random
    wire = _Wire()
    a, _b = _match(wire, seed=99)
    s = a[0]
    firsts = []
    for f in range(200):
        random.seed(s._frame_seed(f))
        firsts.append(random.random())
    assert len(set(firsts)) == len(firsts), "no repeats across 200 frames"
    lo = sum(1 for v in firsts if v < 0.5)
    assert 70 < lo < 130, "and roughly even, not a ramp: %d/200 below half" % lo


def test_two_matches_with_the_same_seed_replay_identically():
    import random
    def run():
        wire = _Wire()
        a, b = _match(wire, seed=7)
        out = []
        for _ in range(30):
            wire.deliver(0, a[0]); wire.deliver(1, b[0])
            if a[0].advance(0):
                out.append(random.random())
        return out
    assert run() == run()


def test_pending_does_not_consume_the_schedule():
    """Two callers ask the same question for different jobs -- the console for
    'does this frame render', the Player for 'do I simulate' -- and only one of
    them may advance the clock."""
    wire = _Wire()
    a, _b = _match(wire)
    s = a[0]
    t = 5_000
    assert s.pending(t) is True
    assert s.pending(t) is True, "asking twice must not move the schedule"
    assert s.due(t) is True
    assert s.pending(t) is False, "...and now the tick has been taken"


def test_a_resend_puts_the_same_frame_back_on_the_air():
    wire = _Wire()
    a, b = _match(wire)
    _step(a, b, wire, frames=4)
    s = a[0]
    before = s.packets_out
    wire.queues[1] = []
    s.resend()
    assert s.packets_out == before + 1
    p = wire.queues[1][-1]
    assert (p[3] | (p[4] << 8)) == s._last_sent, "the newest input, again"


def test_a_resend_before_any_tick_is_harmless():
    wire = _Wire()
    a, _b = _match(wire)
    a[0].resend()
    assert a[0].packets_out == 0
