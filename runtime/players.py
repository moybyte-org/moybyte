# Map (grep -n a name to jump there):
#   PlayerRouter      input sources to player slots
#   NetService        the net.* seam
#   LoopbackNet       its host fake
#   mask_of           a held mask in the cart's button order
#   LockstepSession   the console's view of the kernel's session
#   Peer              a console the link can hear
#   EspNowNet         net.* over the link
#   EspNowLink        the radio's bring-up and the link's actions
#   launch_cart       the guest's half: open the cart a host named
"""The unified, transport-neutral MULTIPLAYER layer (#65).

Two concerns, one cart-facing API, and the transport is a backend detail (exactly
the host==device backend split): a cart never knows HOW an extra player's input
arrives or HOW a shared-state message crosses to the other console.

  * Input routing -- `PlayerRouter` maps input SOURCES to player SLOTS. Slot 0 is
    the local console's existing controls (the real InputState); it behaves
    byte-for-byte as today. Extra slots (1..N) default to NONE until a transport
    registers one (a second USB gamepad #58, a phone over the web-view #41, or an
    ESP-NOW peer #7). This is architecture A ("shared-screen, many controllers") --
    no netcode, the device is the single source of truth. `btn(name, player)` /
    `players()` read it.

  * Shared state -- `NetService` is the `net.*` seam (architecture B, the "two
    consoles" fantasy). `net.send(data)` pushes a message; `on_net(fn)` registers
    the handler the Player drains each frame. `LoopbackNet` is the in-process fake
    transport for host testing (mirrors the sim's fake radio/audio): two endpoints
    are link()ed and send() delivers to the peer's inbox, so both sides of a
    two-console exchange run with zero hardware. The real ESP-NOW transport is a
    LoopbackNet-shaped backend swapped in on the device.

Both are injected like the other services (ws.wifi / ws.updater): the router is
attached to the InputState in console.wire_workstation_core (host + both boards);
the net backend is set per board (a LoopbackNet on the host sim, None on the
device until the ESP-NOW radio lands). MicroPython-safe: plain classes, sets,
lists -- no host-only idioms, so this module stages to the device unchanged.
"""


class _Slot:
    """One extra player's input state (a transport feeds it). Its own held-set +
    press-edge, so btnp(name, player) reads a real 0->1 edge for that player. The
    local player (slot 0) is NOT a _Slot -- the router delegates slot 0 straight to
    the console's own InputState, so the single-player path is untouched."""

    def __init__(self, auto=True):
        self._held = set()
        self._prev = set()
        self._pressed = set()
        self.connected = True     # a transport clears this on disconnect
        # auto=False: the transport advances this slot's edges ITSELF, inside
        # its own per-frame step, and PlayerRouter.begin_frame must leave it
        # alone. A lockstep session needs that (netplay.LockstepSession._apply):
        # it writes held and derives the edge in the same breath, mid-tick,
        # where the router's begin_frame ran earlier in the loop and would
        # recompute `pressed` against a slot the session had not written yet.
        self.auto = auto

    def set_held(self, name, down):
        # Called by a transport backend as an extra controller's buttons change.
        if down:
            self._held.add(name)
        else:
            self._held.discard(name)

    def begin_frame(self):
        # Snapshot for edge detection; the router calls this once per frame for
        # every slot, aligned with the local InputState.begin_frame().
        self._pressed = self._held - self._prev
        self._prev = set(self._held)

    def held(self, name):
        return name in self._held

    def pressed(self, name):
        return name in self._pressed


class PlayerRouter:
    """Maps input sources to player slots behind `btn(name, player)` / `players()`.

    Slot 0 is the local console (the real InputState -- byte-for-byte as today).
    Slots 1..N are extra controllers a transport registers with add_player(); each
    is a _Slot the transport feeds. With no transport registered there is exactly
    one player and btn(name, p>0) is always False, so every existing single-player
    cart is unchanged (zero regression)."""

    def __init__(self, local):
        self._local = local          # the console's InputState (slot 0)
        self._slots = {}             # index (>=1) -> _Slot

    # -- the cart-facing reads (bound into make_api's btn/btnp/players) ------
    #
    # A registered slot wins at EVERY index, slot 0 included. Nothing registers
    # slot 0 in local play, so this is the old behaviour verbatim -- but a
    # lockstep match needs it (netplay.LockstepSession): the local kid is global
    # player 0 on one console and player 1 on the other, and the cart must
    # address the same character by the same index on both screens. Hardwiring
    # slot 0 to the local InputState would make that impossible to express.
    def held(self, name, player=0):
        s = self._slots.get(player)
        if s is not None:
            return s.held(name)
        if not player:
            # Slot 0 is the local console. With every source unassigned (the
            # universal case) that IS the union, byte-for-byte as before; once
            # a source carries a player of its own, slot 0 stops including it.
            return self._local.held(name, 0) if self._local_multi() \
                else self._local.held(name)
        return self._local.held(name, player) if self._local_multi() else False

    def pressed(self, name, player=0):
        s = self._slots.get(player)
        if s is not None:
            return s.pressed(name)
        if not player:
            return self._local.pressed(name, 0) if self._local_multi() \
                else self._local.pressed(name)
        return self._local.pressed(name, player) if self._local_multi() else False

    def _local_multi(self):
        """True once the local InputState has SOURCES assigned to more than one
        player -- a BLE keyboard given `src.player = 1` IS player 2, with no
        transport and no _Slot. False on a pre-source InputState (or a test
        stub), which is the old always-empty behaviour verbatim."""
        local = self._local
        # hasattr, not getattr: a bound method is an allocation every frame.
        return local.multi() if hasattr(local, "multi") else False

    def count(self):
        """The number of connected players: the DISTINCT slots anything is
        assigned to -- the local console's input sources (#26: every producer
        owns a source and carries a player) plus any extra slot a transport
        registered. A cart offers a 2P mode when this is >= 2."""
        if not self._slots and not self._local_multi():
            # THE UNIVERSAL CASE, and it must not allocate: a Lua cart asks this
            # every frame through the moycore snapshot, and the general path
            # below builds a tuple and a set to answer "one". moycore_glue's own
            # header records what a per-frame allocation on that path cost the
            # last time (~1ms on the S3, visible on glass as a Bench FLOOR gap).
            return 1
        ids = None
        srcp = getattr(self._local, "source_players", None)
        if srcp is not None:
            ids = set(srcp())
        if not self._slots:
            return len(ids) if ids else 1
        if ids is None:
            ids = set((0,))
        for i, s in self._slots.items():
            if s.connected:
                ids.add(i)
        return len(ids) or 1

    def button_masks(self, order, player):
        """(held, pressed) bitmasks for one player, in `order`.

        The bulk read the Lua tiers need: moycore takes the frame's buttons as
        an int snapshot, so asking name by name would put seven Python calls per
        player back on the path whose whole purpose is to have none. Resolves
        the same way the single reads do -- a registered slot wins at every
        index, then the local InputState's per-source players."""
        s = self._slots.get(player)
        if s is not None:
            h = p = 0
            held, pressed = s._held, s._pressed
            for i, name in enumerate(order):
                if name in held:
                    h |= 1 << i
                if name in pressed:
                    p |= 1 << i
            return h, p
        bm = getattr(self._local, "button_masks", None)
        if bm is None:
            return 0, 0
        if not player:
            return bm(order, 0) if self._local_multi() else bm(order)
        return bm(order, player) if self._local_multi() else (0, 0)

    # -- the transport-facing registration (a backend owns these) -----------
    def add_player(self, index, auto=True):
        """Register (or re-connect) player `index` and return its _Slot for the
        transport to feed. Idempotent.

        `index` may be 0: a lockstep session owns BOTH global slots, because
        which of them is the local kid differs per console. `auto=False` says
        the transport advances this slot's press edges itself -- see _Slot."""
        index = int(index)
        s = self._slots.get(index)
        if s is None:
            s = _Slot(auto)
            self._slots[index] = s
        else:
            s.auto = auto
        s.connected = True
        return s

    def remove_player(self, index):
        """Drop an extra player (a controller unplugged / a peer left)."""
        self._slots.pop(int(index), None)

    def begin_frame(self):
        """Advance every AUTO slot's press-edge for this frame. Called next to
        the local InputState.begin_frame(). The truthiness guard matters on
        MicroPython: dict.values() allocates a view + iterator PER CALL, which
        on the single-player path was per-frame churn for an empty loop.

        A non-auto slot is skipped: its transport advances it mid-tick, at the
        moment it writes held, which is the only order that keeps a lockstep
        frame's held and pressed describing the same frame."""
        if self._slots:
            for s in self._slots.values():
                if s.auto:
                    s.begin_frame()


class NetService:
    """The transport-neutral `net.*` message seam (architecture B shared state).

    The cart-facing surface is send() + on_message() (bound as `on_net`) + the
    pump() the Player drains each frame; a real transport subclasses and overrides
    send() (frame a ~250-byte ESP-NOW packet) and calls deliver() on each inbound
    frame. The verbs are send / on_message / receive, the shape a cart written
    against a radio API expects, so such a cart ports cleanly."""

    def __init__(self):
        self._inbox = []
        self._handler = None

    def send(self, data):
        # Transport-specific: the base drops (no peer). LoopbackNet / ESP-NOW override.
        pass

    def on_message(self, fn):
        """Register the handler called with each inbound message (decorator-friendly:
        returns fn). Bound into the cart namespace as `on_net`."""
        self._handler = fn
        return fn

    def deliver(self, msg):
        """A transport queues an inbound message here; pump() dispatches it to the
        cart's handler on the next frame (never mid-transport, so the cart's state
        only changes at a known point)."""
        self._inbox.append(msg)

    def pump(self):
        """Dispatch every queued inbound message to the registered handler. Called
        once per frame by the Player (before the cart's _update), so incoming state
        is applied before the cart's logic runs -- the lockstep-friendly order."""
        inbox = self._inbox
        if not inbox:
            return
        self._inbox = []
        h = self._handler
        if h is None:
            return
        for m in inbox:
            h(m)

    def reset(self):
        """Drop the handler + any queued messages -- called at each cart (re)start so
        a fresh run never inherits the previous run's handler or stale packets."""
        self._inbox = []
        self._handler = None

    def peers(self):
        """How many peers this endpoint can reach (0 = solo, no second console)."""
        return 0


class LoopbackNet(NetService):
    """In-process fake transport for host testing (the sim's fake radio, for net).

    Two endpoints are link()ed; send() delivers straight to the peer's inbox, so a
    host test drives both sides of a two-console exchange with no hardware. Unlinked
    (a solo desktop sim -- no second console), send() drops, so a "multiplayer" cart
    still runs, just with nobody on the other end."""

    def __init__(self):
        NetService.__init__(self)
        self._peer = None

    def link(self, other):
        """Wire two endpoints together (both directions)."""
        self._peer = other
        other._peer = self

    def send(self, data):
        if self._peer is not None:
            self._peer.deliver(data)

    def peers(self):
        return 1 if self._peer is not None else 0


# -- two consoles: the kernel's link and its lockstep session ------------------
#
# The protocol and the session are C (native/moy_play/moy_match.h, which has
# the rules and the measurements behind them): beacons, the same-cart
# handshake, the input exchange, the stall. What is here is the console's view
# of them: the link's bring-up over the radio (the measured recipe below), the
# session's player slots in this router, a Python cart's random stream, a
# cart's net.* messages as JSON, and the actions the link leaves for the
# console (open the cart a host named, re-run it from frame zero).

try:
    import moy_play as _mp
except ImportError:                     # CPython: the host's ctypes module
    from runtime import moy_play as _mp

PROTO = 1
T_BEACON, T_JOIN, T_START, T_INPUT, T_MSG, T_BYE = 1, 2, 3, 4, 5, 6
TICK_HZ = 30
DELAY = 1
DELAY_MAX = 2
ESCALATE_TICKS = 90
ESCALATE_AT = 12
REDUNDANCY = 4
MAX_SPAN = 24
GIVE_UP = 300

BROADCAST = b"\xff\xff\xff\xff\xff\xff"
BEACON_MS = 400
PEER_TTL_MS = 2000
START_TRIES = 12
# The radio's recipe (T-Deck <-> Guition, 2026-08-20): rxbuf BEFORE active()
# (a live change desyncs the ring for good); 32768, not the default 526 (64 of
# 200 messages arrived at the default while every send said True); RATE_54M
# (a 48 KB payload went from 1680 ms to 65 ms); power save off only while
# linked (it halves the latency tail and costs battery).
RXBUF = 32768
DRAIN_MAX = 24


def mask_of(state, buttons, player=None):
    """An input's held buttons as one byte, in `buttons` order. `held`, not
    `pressed`: both consoles derive edges from consecutive held masks."""
    m = 0
    for i, name in enumerate(buttons):
        if state.held(name, player):
            m |= 1 << i
    return m


def _ticks():
    try:
        from device_util import _ticks_ms
        return _ticks_ms
    except ImportError:
        import time
        return lambda: int(time.time() * 1000)  # noqa: E731


def _decode_config(text):
    if not text:
        return None
    try:
        import json
        cfg = json.loads(text)
        return cfg if isinstance(cfg, dict) and cfg else None
    except Exception:  # noqa: BLE001
        return None


class _SendIo:
    """A lone session's transport: every packet to `send(payload)`."""

    def __init__(self, send):
        self._send = send

    def send(self, mac, payload):
        self._send(payload)
        return True

    def recv(self):
        return None

    def add_peer(self, mac):
        pass


class LockstepSession:
    """One two-console match as the console sees it: the C session
    (moy_lockstep_*) and its two global players' slots in `router`, both
    driven here -- slot 0 included, because the local kid is global player 0
    on one console and 1 on the other, and a cart must address the same tank
    by the same index on both screens.

    Built alone (`send` is the transport) by the tests and a host's loopback;
    a link builds its own over its instance (`_m`)."""

    SLEW_TARGET_MS = 12
    SLEW_BAND_MS = 4

    def __init__(self, index, seed, send, buttons, router,
                 tick_hz=TICK_HZ, delay=DELAY, redundancy=REDUNDANCY,
                 session=0, config=None, _m=None):
        if _m is None:
            _m = _mp.Match(_SendIo(send), "", "", 0)
            _m.start(b"\x00" * 6)
            cfg = None
            if config:
                import json
                cfg = json.dumps(config)
            _m.begin(index, seed, session, cfg)
            _m.tick_ms = int(1000 // tick_hz)
            _m.delay = delay
            _m.redundancy = redundancy
        self._m = _m
        self._buttons = buttons
        self.dt = 1.0 / tick_hz
        self.index = int(_m.index)
        self.peer = 1 - self.index
        self.seed = int(_m.seed)
        self.session = int(_m.session)
        self.config = _decode_config(_m.config)
        self._router = router
        self.local = self.remote = None
        if router is not None:
            # Both slots are the session's and NOT auto-advanced: their edges
            # move inside advance(), where held is written.
            self.local = router.add_player(self.index, auto=False)
            self.remote = router.add_player(self.peer, auto=False)
        # Construction is the one moment both consoles agree on: the cart is
        # (re)started right after, so a Python cart's _init draws alike.
        import random
        random.seed(self.seed)

    def __getattr__(self, name):
        if name in ("frame", "delay", "redundancy", "stalls", "stall_ticks",
                    "waiting", "dead", "packets_in", "packets_out",
                    "last_peer_frame", "peer_need", "tick_ms"):
            return getattr(self.__dict__["_m"], name)
        if name in ("_next_ms", "_m_ema", "_last_sent", "_tps_ms"):
            return getattr(self.__dict__["_m"], name[1:])
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name in ("delay", "redundancy", "frame", "_next_ms", "_m_ema"):
            setattr(self._m, name.lstrip("_"), value)
        else:
            object.__setattr__(self, name, value)

    @property
    def _arr_f(self):
        return self._m._m.s.arr_f          # host only: the tests' white box

    @property
    def _arr_t(self):
        return self._m._m.s.arr_t

    def pending(self, now_ms):
        return self._m.pending(now_ms)

    def due(self, now_ms):
        return self._m.due(now_ms)

    def advance(self, held_mask, now_ms=None):
        """One lockstep frame: True simulate (both slots now hold this frame's
        buttons and the random stream starts from the frame's seed), False
        STALL. Never blocks, never guesses."""
        if self._m.advance(held_mask, now_ms) != 1:
            return False
        held = self._m.held
        self._apply(self.local, held[self.index])
        self._apply(self.remote, held[self.peer])
        import random
        random.seed(self._m.frame_seed)
        return True

    def _apply(self, slot, mask):
        if slot is None:
            return
        for i, name in enumerate(self._buttons):
            slot.set_held(name, bool(mask & (1 << i)))
        slot.begin_frame()

    def _frame_seed(self, f):
        return _mp.seed_of(self.seed, f)

    def _expand(self, f16):
        return self._m.expand(f16)

    def resend(self):
        self._m.resend()

    def on_packet(self, data, now_ms=None):
        return self._m.packet(data, now_ms)

    def tps(self, now_ms):
        """Ticks per second since the last call: the PERF line's net=. ONE
        reader -- it consumes its window."""
        return self._m.tps(now_ms)

    def close(self):
        for i in (self.index, self.peer):
            try:
                if self._router is not None:
                    self._router.remove_player(i)
            except Exception:  # noqa: BLE001 -- teardown must never raise
                pass
        self._m.close()


class Peer:
    """Another console the link can hear, as its row stands."""

    def __init__(self, row):
        self.mac, self.name, self.board, self.cart, self.state, self.seen = row


class EspNowNet(NetService):
    """A cart's `net.*` over the link: net.send is one broadcast frame of
    JSON; an inbound message is pumped to on_net before the cart's _update.
    An oversized message is DROPPED with a line, never truncated."""

    MAX = 240

    def __init__(self, link):
        NetService.__init__(self)
        self._link = link

    def send(self, data):
        try:
            import json
            body = json.dumps(data).encode()
        except Exception as exc:  # noqa: BLE001 -- a cart must not crash on send
            print("Moybyte net.send: cannot encode:", exc)
            return False
        if len(body) > self.MAX:
            print("Moybyte net.send: message too big (%d > %d bytes), dropped"
                  % (len(body), self.MAX))
            return False
        m = self._link._m
        return m is not None and m.send_msg(body) == 0

    def pump(self):
        m = self._link._m
        if m is not None:
            import json
            while True:
                b = m.take_msg()
                if b is None:
                    break
                try:
                    self.deliver(json.loads(bytes(b).decode()))
                except Exception as exc:  # noqa: BLE001 -- not ours to crash on
                    print("Moybyte net recv:", exc)
        NetService.pump(self)

    def peers(self):
        return len(self._link.peers)


class _RadioIo:
    """An injected radio (the host's tests) as the C link's transport."""

    def __init__(self, link):
        self._link = link

    def send(self, mac, payload):
        self._link.radio.send(mac, payload, False)
        return True

    def recv(self):
        mac, msg = self._link.radio.irecv(0)
        return None if msg is None else (bytes(mac), bytes(msg))

    def add_peer(self, mac):
        self._link.radio.add_peer(mac)

    def recover(self):
        return self._link._cycle_radio()


class EspNowLink:
    """The console's half of the radio link: the radio's bring-up and
    teardown, the session's slots and the actions the C link leaves.

    `radio`, `wlan` and `launch(ws, title) -> bool` are injectable; left None
    on a board, the radio is the kernel's (`moy_net.Link`'s ring) and the link
    the kernel's instance, which the loop's tail polls with no crossing."""

    def __init__(self, board="", name="", ticks_ms=None, radio=None, wlan=None,
                 launch=None):
        self.board = board
        self.name = name or board
        self.radio = radio
        self.wlan = wlan
        self.mac = b""
        self.error = None
        self.net = EspNowNet(self)
        self._m = None
        self._view = None
        self._pm_was = None
        self._rate = None
        self._launch = launch
        self._ticks_ms = ticks_ms if ticks_ms is not None else _ticks()

    # -- the C instance's state --
    @property
    def active(self):
        return self._m is not None and bool(self._m.active)

    @property
    def state(self):
        return 0 if self._m is None else self._m.state

    @property
    def cart(self):
        return "" if self._m is None else self._m.cart

    @state.setter
    def state(self, v):
        if self._m is not None:
            self._m.state = v

    @cart.setter
    def cart(self, v):
        if self._m is not None:
            self._m.cart = v

    @property
    def peers(self):
        """{mac: Peer} of the consoles this link can hear (the kernel's table)."""
        out = {}
        if self._m is not None:
            for r in self._m.peers():
                out[r[0]] = Peer(r)
        return out

    @property
    def session(self):
        m = self._m
        if m is None or not m.live:
            self._view = None
            return None
        v = self._view
        if v is None or v.session != m.session or v.index != m.index:
            v = self._view = LockstepSession(
                m.index, m.seed, None, _cart_buttons(), self._router, _m=m)
        return v

    _router = None

    def __getattr__(self, name):
        if name in ("rx", "tx", "drops", "recovers"):
            m = self.__dict__.get("_m")
            return 0 if m is None else getattr(m, name)
        raise AttributeError(name)

    # -- lifecycle --
    def start(self):
        """Bring the radio up with the measured recipe. Idempotent; a board
        with no ESP-NOW degrades to an inactive link, never a crash."""
        if self.active:
            return True
        try:
            if self.wlan is None:
                from device_wifi import kernel_wlan
                self.wlan = kernel_wlan()
            # Every start: the radio lease stops the interface between matches.
            self.wlan.active(True)
            self.mac = self.wlan.config("mac")
            try:
                self._pm_was = self.wlan.config("pm")
                self.wlan.config(pm=self.wlan.PM_NONE)
            except Exception:  # noqa: BLE001 -- a port without pm still links
                self._pm_was = None
            kernel = self.radio is None
            if kernel:
                import moy_net
                self.radio = moy_net.Link()
                self._rate = getattr(moy_net, "RATE_54M", None)
            try:
                self.radio.config(rxbuf=RXBUF)      # BEFORE active(), never after
            except Exception as exc:  # noqa: BLE001
                print("Moybyte espnow rxbuf:", exc)
            self.radio.active(True)
            if self._rate is not None:
                try:
                    self.radio.config(rate=self._rate)
                except Exception as exc:  # noqa: BLE001
                    print("Moybyte espnow rate:", exc)
            if self._m is None:
                import random
                ent = random.getrandbits(30) | 1
                if kernel:
                    self._m = _mp.Match(None, self.name, self.board, ent)
                else:
                    self._m = _mp.Match(_RadioIo(self), self.name, self.board, ent)
            self._m.start(self.mac)
            self.error = None
        except Exception as exc:  # noqa: BLE001 -- no radio -> no link, not a dead console
            self.error = str(exc)
            print("Moybyte espnow unavailable:", exc)
        return self.active

    def _cycle_radio(self):
        """The recover: an active cycle with the recipe re-applied (the
        cycle resets the PHY rate to the 1M default, 2026-08-24)."""
        try:
            self.radio.active(False)
            self.radio.config(rxbuf=RXBUF)
            self.radio.active(True)
            self.radio.add_peer(BROADCAST)
            if self._rate is not None:
                self.radio.config(rate=self._rate)
            return True
        except Exception as exc:  # noqa: BLE001
            print("Moybyte espnow recover failed:", exc)
            return False

    def stop(self, ws=None):
        self.end_match(ws)
        if self.radio is not None:
            try:
                self.radio.active(False)
            except Exception:  # noqa: BLE001
                pass
        if self.wlan is not None and self._pm_was is not None:
            try:
                self.wlan.config(pm=self._pm_was)
            except Exception:  # noqa: BLE001
                pass
            self._pm_was = None
        if self._m is not None:
            self._m.stop()

    def broadcast(self, payload):
        return self.active and self._m.broadcast(payload)

    # -- the frame --
    def drain_input(self, budget=DRAIN_MAX):
        """Input-priority drain, safe mid-frame: inputs to the session, every
        other frame parked for the tail (a START or a BYE must not land
        inside the Player's frame)."""
        if not self.active:
            return 0
        return self._m.drain(self._ticks_ms(), budget)

    def poll(self, ws=None):
        """The tail's drain and beacon, then the console's half."""
        if not self.active:
            return 0
        n = self._m.poll(self._ticks_ms())
        self.sync(ws)
        return n

    def sync(self, ws):
        """The console's half of what the C link did: the session it formed
        or ended reaches `ws.netplay` FIRST (the run about to start adopts
        it), then the cart the link asked for is opened."""
        m = self._m
        if m is None or ws is None:
            return
        if getattr(ws, "input", None) is not None:
            self._router = getattr(ws.input, "players", None)
        s = self.session
        if s is not None:
            if ws.netplay is not s:
                ws.netplay = s
        elif ws.netplay is not None and isinstance(ws.netplay, LockstepSession) \
                and ws.netplay._m is m:
            ws.netplay = None
        title = m.action()
        if title is None:
            return
        ok = False
        if self._launch is not None:
            try:
                ok = self._launch(ws, title)
            except Exception as exc:  # noqa: BLE001
                print("Moybyte link launch failed:", exc)
        if not ok and m.live:
            print("Moybyte link: no cart named %r here" % title)
            self.end_match(ws)

    def _dispatch(self, ws, mac, msg):
        if self._m is not None:
            self._m.dispatch(mac, msg, self._ticks_ms())
            self.sync(ws)

    # -- pairing --
    def announce(self, cart="", state=0):
        if self._m is not None:
            self._m.announce(cart or "", state)

    def candidate(self, cart):
        if self._m is None:
            return None
        r = self._m.candidate(cart, self._ticks_ms())
        return None if r is None else Peer(r)

    def offer(self, ws, cart, router=None, seed=None):
        """At a multiplayer cart's start: a peer on the same cart makes this
        run a match (the lower MAC hosts). The host's tuning rides START."""
        if not self.active:
            return False
        cfg = getattr(ws, "config", None) if ws is not None else None
        try:
            import json
            self._m.set_config(json.dumps(cfg) if cfg else None)
        except Exception:  # noqa: BLE001
            self._m.set_config(None)
        if router is not None:
            self._router = router
        ok = self._m.offer(cart or "", self._ticks_ms(), seed)
        self.sync(ws)
        return ok and self.session is not None

    def end_match(self, ws=None):
        """Drop the match, and the console's reference to it."""
        if self._m is not None:
            if self._view is not None:
                self._view.close()
            self._m.end()
        self._view = None
        if ws is not None:
            ws.netplay = None

    # -- diagnostics --
    def status(self):
        """(active, peers, matched, frame); frame is None with no match,
        never 0 (a frozen 0 is what a broken lockstep reads as)."""
        s = self.session
        return (self.active, len(self.peers), s is not None,
                None if s is None else s.frame)

    def stats(self):
        m = self._m
        s = self.session
        return {
            "active": self.active,
            "mac": self.mac.hex() if self.mac else None,
            "peers": [(p.name, p.board, p.cart, p.state) for p in self.peers.values()],
            "rx": self.rx, "tx": self.tx, "drops": self.drops,
            "recovers": self.recovers,
            "error": self.error or (m.error if m is not None and m.error else None),
            "match": None if s is None else {
                "index": s.index, "frame": s.frame, "stalls": s.stalls,
                "stall_ticks": s.stall_ticks, "delay": s.delay,
                "m_ema": s._m_ema, "waiting": s.waiting, "in": s.packets_in,
                "out": s.packets_out, "peer_frame": s.last_peer_frame,
            },
        }


def _cart_buttons():
    try:
        from cart_api import CART_BUTTONS
    except ImportError:
        from runtime.cart_api import CART_BUTTONS
    return CART_BUTTONS


def launch_cart(ws, title):
    """The guest's half: open the cart the host named. The kid may already be
    playing it -- whoever is second makes the match, and a lockstep sim
    cannot join a game in progress, so the open cart re-runs in place."""
    want = (title or "").lower()
    if not want:
        return False
    open_now = getattr(ws, "cart", None)
    if open_now and str(open_now.get("title") or "").lower() == want:
        return bool(ws._start())
    items = getattr(ws.launcher, "items", [])
    for i in range(len(items)):
        it = items[i]
        if not it.get("path"):
            continue
        if want == str(it.get("title") or "").lower():
            ws.launcher.sel = i
            ws.launch_selected()
            return True
    return False


def __getattr__(name):              # CPython's module hook; a VM never asks
    if name == "CART_BUTTONS":
        return _cart_buttons()
    raise AttributeError(name)


def make_link(board="", name=""):
    """The board's link: `ws.link`."""
    return EspNowLink(board=board, name=name, launch=launch_cart)
