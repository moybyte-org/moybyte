"""Deterministic LOCKSTEP for two-console play (#65 Phase 2, over #7's radio).

The transport is somebody else's problem. This module owns the part that decides
whether two consoles show the same game: the frame clock, the input exchange, and
the rule about when a console is allowed to simulate.

WHY LOCKSTEP AND NOT STATE REPLICATION. Measured on glass, T-Deck <-> Guition,
2026-08-20: a 16-byte input frame round-trips in 5.0ms median / 14.2ms p99, and a
250-byte state blob in 15.0ms / 17.1ms -- 3x the cost for the smaller half of the
problem, because at these sizes the round trip is MicroPython call overhead and
WiFi task scheduling, not airtime. So the payload is INPUTS, never state, and both
consoles run the same simulation from the same inputs. The same measurement pass
found a 30fps fire-and-forget input stream arriving with `late(>2frames)=0` over
150 ticks, which is what makes the design below viable rather than hopeful.

THE THREE RULES, and what each one costs:

  1. INPUT DELAY. Frame N's input is sent during frame N-DELAY and played on
     frame N. Your OWN input is delayed identically, so the two consoles stay
     symmetric -- an asymmetric delay is just a desync with extra steps. The cost
     is a constant DELAY/TICK_HZ of input lag (2 frames at 30Hz = 66ms), which is
     the buffer a 14ms p99 has to overrun before anybody stalls.

  2. REDUNDANCY, NOT RETRANSMIT. Every packet carries the last REDUNDANCY frames
     of input, not just the newest. An input mask is ONE BYTE, so the redundancy
     is free, and a lost packet is healed by the NEXT one rather than by a round
     trip -- which matters because the radio's ack lies: at MicroPython's default
     rxbuf only 64 of 200 messages arrived while `send(sync=True)` returned True
     for all 200 (same measurement pass). Never trust delivery; make loss
     self-healing instead.

  3. A MISSING INPUT STALLS THE SIM. It does not guess. Advancing on a guess
     desyncs the two consoles permanently and INVISIBLY -- both keep drawing a
     plausible game, and they are different games. This is why DS/GBA link titles
     hitch with "waiting for player" instead of rubber-banding, and it is the one
     behaviour here that must never be "improved" into extrapolation.

DETERMINISM IS A PRECONDITION, not a consequence. Two things follow and both are
enforced here rather than left to carts:

  * FIXED TIMESTEP. `dt` is 1/TICK_HZ on both consoles, always. The Player feeds
    the cart this dt instead of the real frame delta while a session is live; a
    variable dt is a divergence on frame one.
  * SHARED SEED, RE-APPLIED EVERY TICK. Seeding once at match start is only
    correct while both consoles then draw from the stream the same number of
    times -- and they do not, because DRAWING consumes it too and the two boards
    render at different rates. So each logic frame re-seeds from (seed, frame),
    which no amount of drawing can move, and a cart may call `rnd()` wherever it
    likes. Note the tier limit: CPython and MicroPython ship DIFFERENT PRNGs, so
    host<->device lockstep would diverge on the first `rnd()`. Device<->device
    (the actual use case, and both S3 boards run the same MicroPython) agrees.

WHAT IS DELIBERATELY NOT HERE. Rollback/GGPO: it needs the cart's whole world
snapshotted and re-simulated several times a frame, which a kid-authored `.moy`
cart cannot promise. Lockstep + delay + redundancy is both simpler and what the
hardware this imitates actually did.

MicroPython-safe and IMPORT-FREE apart from `random`: the button order arrives as
a constructor argument (cart_api.CART_BUTTONS is its one author) so this module
stays a leaf that stages to every board unchanged, exactly like players.py.
"""

import random


# -- wire format -------------------------------------------------------------
#
# One byte of protocol, one of type. Everything is little-endian and hand-packed:
# `struct` exists on both tiers but a per-packet format string is an allocation
# on a path that runs at 30Hz, and these payloads are under ten bytes.
PROTO = 1

T_BEACON = 1     # I exist, here is my board/cart/state
T_JOIN = 2       # I want into your session
T_START = 3      # you are in: seed, session id, your player index
T_INPUT = 4      # REDUNDANCY frames of one player's button masks
T_MSG = 5        # a cart's net.send payload (not lockstep -- see EspNowNet)
T_BYE = 6        # leaving

# The lockstep clock. 30Hz because the p99 input round trip (14ms) fits a 33ms
# tick 2.4x over, where a 60Hz tick (16.7ms) leaves nothing for the console's own
# frame. It also lands on the frameskip model the console already ships (#77:
# logic at the full rate, motion at 30Hz).
TICK_HZ = 30
DELAY = 2          # frames of input delay -- 66ms of buffer at 30Hz
REDUNDANCY = 4     # frames of input per packet; one byte each, so ~free
MAX_SPAN = 24      # the widest history one packet may carry (see _emit)
GIVE_UP = 300      # consecutive stalled frames before a match is declared dead

_TAPE = 256        # frames of ring. 8.5s at 30Hz, vs a delay+redundancy window
                   # of six -- big enough that wrap can never race the window.


class InputTape:
    """A ring of one-byte button masks by frame number.

    Each slot stores the frame it holds, so a stale slot reads as ABSENT rather
    than as a plausible old input -- which is the difference between a stall (the
    correct behaviour) and a silent desync.
    """

    def __init__(self):
        self._frame = [-1] * _TAPE
        self._mask = bytearray(_TAPE)

    def put(self, frame, mask):
        i = frame & (_TAPE - 1)
        self._frame[i] = frame
        self._mask[i] = mask

    def get(self, frame):
        i = frame & (_TAPE - 1)
        return self._mask[i] if self._frame[i] == frame else None

    def clear(self):
        for i in range(_TAPE):
            self._frame[i] = -1


def mask_of(state, buttons, player=None):
    """Pack an InputState's held buttons into one byte, in `buttons` order.

    Reads `held`, not `pressed`: edges are DERIVED on both consoles from
    consecutive held masks (see LockstepSession._apply), so the two sides cannot
    disagree about what counts as a press the way two independently-sampled edge
    sets would.
    """
    m = 0
    for i, name in enumerate(buttons):
        if state.held(name, player):
            m |= 1 << i
    return m


class LockstepSession:
    """One two-console match: the frame clock, the input exchange, the stall.

    `send(payload)` is the transport (an ESP-NOW unicast on the device, a
    LoopbackNet on the host). `router` is the PlayerRouter whose slots this
    session drives -- BOTH of them, including slot 0, because the local console
    is not necessarily global player 0. That is the whole reason slot 0 is
    overridable: on the host's screen the local kid may be player 1, and the cart
    must address the same tank by the same index on both screens or the two
    consoles are drawing different games with the same data.
    """

    def __init__(self, index, seed, send, buttons, router,
                 tick_hz=TICK_HZ, delay=DELAY, redundancy=REDUNDANCY,
                 session=0, config=None):
        # The HOST's cart config, applied over the guest's before _init runs.
        # Not a nicety: "Make it mine" lets each kid retune their own copy, and
        # two consoles running the same cart with different tuning (six enemy
        # tanks here, three there) diverge on frame one -- silently, because both
        # screens still look like a working game. One console's settings have to
        # win, and it is the one that started the match.
        self.config = config
        self.index = int(index)              # MY global player index (0 or 1)
        self.peer = 1 - self.index
        self.seed = int(seed)
        self.session = int(session) & 0xFF
        self.dt = 1.0 / tick_hz
        self.tick_ms = int(1000 // tick_hz)
        self._next_ms = None
        self.delay = int(delay)
        self.redundancy = int(redundancy)
        self._send = send
        self._buttons = buttons
        self.frame = 0                       # the next frame to simulate
        self.stalls = 0                      # frames spent waiting on the peer
        self.waiting = False                 # stalled RIGHT NOW (the cart/HUD reads it)
        self.packets_in = 0
        self.packets_out = 0
        self.last_peer_frame = -1
        self.peer_need = -1        # the frame the PEER says it is waiting for
        self._last_sent = None     # newest frame we have put on the air
        self.dead = False          # the gap grew past healing; stop pretending
        self._stall_mark = 0
        self._mine = InputTape()
        self._theirs = InputTape()
        # Both slots are transport-driven and NOT auto-advanced: this session
        # advances their press edges itself, inside advance(), so held and
        # pressed are always the same frame's truth. The router's own
        # begin_frame() runs earlier in the loop and would otherwise recompute
        # the edges against a half-updated slot.
        self.local = router.add_player(self.index, auto=False)
        self.remote = router.add_player(self.peer, auto=False)
        self._router = router
        # The seed is applied HERE, at construction, because construction is the
        # one moment both consoles agree on: the cart is (re)started immediately
        # after, so its _init draws from the same sequence on both.
        random.seed(self.seed)

    # -- the per-frame contract ---------------------------------------------

    def pending(self, now_ms):
        """Is a tick due? PURE -- no schedule side effect, unlike due().

        Two callers need the same answer for different jobs and must not both
        consume the schedule: the console asks whether this frame renders, and
        the Player asks whether to simulate."""
        nx = self._next_ms
        return nx is None or (now_ms - nx) >= 0

    def due(self, now_ms):
        """Is the next fixed tick due yet?

        The lockstep clock is 30Hz and the console's frame loop is NOT -- it runs
        as fast as the board manages, ~50fps on an S3. Ticking the sim once per
        FRAME while feeding it a fixed 1/30 dt means the game advances 1.67
        seconds of game time per second of wall time: both consoles agree
        perfectly, and both play the same game much too fast. (Which is what two
        boards did on the desk: a flawless match of Brick Siege in fast-forward.)

        So the session paces itself. A tick that arrives late does not try to
        make up the debt frame by frame -- past a quarter second behind the
        schedule is re-based, because after a long stall the alternative is a
        burst of catch-up frames nobody asked for.
        """
        nx = self._next_ms
        if nx is None:
            self._next_ms = now_ms + self.tick_ms
            return True
        if now_ms - nx < 0:
            return False
        nx += self.tick_ms
        if now_ms - nx > 250:
            nx = now_ms + self.tick_ms
        self._next_ms = nx
        return True

    def advance(self, held_mask):
        """One lockstep frame. True = simulate, False = STALL (do not simulate).

        Called once per cart tick with the local console's held-button mask.
        Broadcasts our input for frame+delay, then decides whether the peer's
        input for THIS frame has arrived. Never blocks, never guesses.
        """
        due = self.frame + self.delay
        self._mine.put(due, held_mask)
        self._emit(due)
        theirs = self._theirs.get(self.frame)
        if theirs is None:
            self.waiting = True
            self.stalls += 1
            # A match that cannot move is over. Saying so lets the console put
            # the kid back in a one-player game instead of holding a frozen
            # screen forever, which is the worst outcome available here.
            if self.stalls - self._stall_mark > GIVE_UP:
                self.dead = True
            return False
        self._stall_mark = self.stalls
        mine = self._mine.get(self.frame)
        if mine is None:
            mine = 0          # frames 0..delay-1 predate any input: nobody moved
        self._apply(self.local, mine)
        self._apply(self.remote, theirs)
        self.waiting = False
        self.frame += 1
        # RE-SEED EVERY TICK, and this is not belt-and-braces -- it is the only
        # thing that makes a shared random stream survive contact with a real
        # cart. Seeding once at match start is correct exactly as long as both
        # consoles then draw from the stream the SAME number of times, and they
        # do not: anything outside the logic tick consumes it too. Brick Siege
        # shakes the screen with rnd() in _draw, the two boards render at
        # different rates (6372 frames against 9335 over one measured window),
        # and from the first explosion onward the two sims were drawing
        # different numbers and playing different games. Nothing showed it:
        # both screens looked fine (2026-08-22).
        #
        # So every logic frame starts from a state derived from (seed, frame),
        # which no amount of drawing can move. It costs one seed() at 30Hz, and
        # it means a kid may call rnd() wherever they like.
        random.seed(self._frame_seed(self.frame))
        return True

    def _frame_seed(self, f):
        """A deterministic per-frame seed. Mixed rather than `seed ^ frame` so
        consecutive frames do not start from neighbouring states -- a cart that
        asks for one number a frame (`rnd(1.0) < 0.012`, the enemy fire roll
        here) would otherwise see the low bits of a counter."""
        x = (self.seed ^ (f * 0x9E3779B1)) & 0x7FFFFFFF
        x ^= (x >> 15)
        x = (x * 0x2545F491) & 0x7FFFFFFF
        return x or 1

    def _apply(self, slot, mask):
        buttons = self._buttons
        for i, name in enumerate(buttons):
            slot.set_held(name, bool(mask & (1 << i)))
        slot.begin_frame()        # edges from THIS frame's held, on both consoles

    # -- wire ----------------------------------------------------------------

    def resend(self):
        """Re-send the newest input packet, between ticks.

        The console's frame loop runs faster than the lockstep clock (40-55fps
        against 30Hz on the two S3 boards), so there are spare frames in which
        this costs nothing but eleven bytes of air -- and every one of them is
        another chance for a packet the radio quietly dropped to arrive. The ack
        lies, so more copies is the only honest answer to loss; it also serves a
        STALLED peer sooner, because each copy carries whatever frame the peer
        last said it was waiting for."""
        if self._last_sent is not None:
            self._emit(self._last_sent)

    def _emit(self, newest):
        """One input packet: the newest frames, plus whatever the peer still needs.

        THE FIXED WINDOW WAS A DEADLOCK, and it took two consoles on a desk to
        show it (2026-08-22). Redundancy heals a gap only while the sender is
        still inside the window; a console that stalls for longer than
        REDUNDANCY frames falls out of it, and then NEITHER side can move --
        each is waiting for a frame the other has already scrolled past, and
        both stop advancing, so neither ever sends the frame the other wants.
        On glass that was two games running happily to frame ~150 and then
        freezing solid, with packets still flowing in both directions.

        The cure is to say what you are waiting for. Every packet carries the
        sender's CURRENT frame, so the peer knows exactly how far back to reach,
        and a stalled console gets served the frame it is stuck on instead of
        four frames it already has.
        """
        self._last_sent = newest
        lo = newest - (self.redundancy - 1)
        need = self.peer_need
        if 0 <= need < lo:
            lo = need
            if newest - lo >= MAX_SPAN:
                lo = newest - MAX_SPAN + 1     # bounded: a packet stays small
        n = newest - lo + 1
        if n < 1:
            n = 1
        p = bytearray(7 + n)
        p[0] = PROTO
        p[1] = T_INPUT
        p[2] = self.session
        p[3] = newest & 0xFF
        p[4] = (newest >> 8) & 0xFF
        p[5] = self.frame & 0xFF               # ...and what I am waiting for
        p[6] = (self.frame >> 8) & 0xFF
        for i in range(n):
            m = self._mine.get(newest - i)
            p[7 + i] = 0 if m is None else m
        self.packets_out += 1
        self._send(bytes(p))

    def on_packet(self, data):
        """Feed one inbound frame. Ignores anything that is not this session's
        input -- the radio owner dispatches by type, but a stale packet from the
        PREVIOUS match carries the previous session id and must not land here."""
        if len(data) < 7 or data[0] != PROTO or data[1] != T_INPUT:
            return False
        if data[2] != self.session:
            return False
        newest = self._expand(data[3] | (data[4] << 8))
        self.peer_need = self._expand(data[5] | (data[6] << 8))
        n = len(data) - 7
        if n > MAX_SPAN:
            n = MAX_SPAN
        for i in range(n):
            f = newest - i
            if f >= 0 and self._theirs.get(f) is None:
                self._theirs.put(f, data[7 + i])
        if newest > self.last_peer_frame:
            self.last_peer_frame = newest
        self.packets_in += 1
        return True

    def _expand(self, f16):
        """A 16-bit wire frame back to our unbounded counter.

        The peer is within delay+redundancy frames of us, so the nearest
        congruent value is always the right one -- which is what makes the
        counter survive its 36-minute wrap without a session reset.
        """
        base = self.frame
        f = (base & ~0xFFFF) | f16
        if f - base > 32768:
            f -= 65536
        elif base - f > 32768:
            f += 65536
        return f

    # -- teardown ------------------------------------------------------------

    def close(self):
        """Drop the session's player slots so the console returns to whatever it
        had before the match -- normally one local player."""
        for i in (self.index, self.peer):
            try:
                self._router.remove_player(i)
            except Exception:  # noqa: BLE001 -- teardown must never raise
                pass
        self._mine.clear()
        self._theirs.clear()
