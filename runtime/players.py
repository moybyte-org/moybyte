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

    def __init__(self):
        self._held = set()
        self._prev = set()
        self._pressed = set()
        self.connected = True     # a transport clears this on disconnect

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
    def held(self, name, player=0):
        if not player:
            return self._local.held(name)
        s = self._slots.get(player)
        return s.held(name) if s is not None else False

    def pressed(self, name, player=0):
        if not player:
            return self._local.pressed(name)
        s = self._slots.get(player)
        return s.pressed(name) if s is not None else False

    def count(self):
        """The number of connected players -- always the local one, plus any
        connected extra slots. A cart offers a 2P mode when this is >= 2."""
        n = 1
        for s in self._slots.values():
            if s.connected:
                n += 1
        return n

    # -- the transport-facing registration (a backend owns these) -----------
    def add_player(self, index):
        """Register (or re-connect) extra player `index` (>=1) and return its
        _Slot for the transport to feed. Idempotent."""
        index = int(index)
        s = self._slots.get(index)
        if s is None:
            s = _Slot()
            self._slots[index] = s
        s.connected = True
        return s

    def remove_player(self, index):
        """Drop an extra player (a controller unplugged / a peer left)."""
        self._slots.pop(int(index), None)

    def begin_frame(self):
        """Advance every extra slot's press-edge for this frame. Called next to
        the local InputState.begin_frame(). The truthiness guard matters on
        MicroPython: dict.values() allocates a view + iterator PER CALL, which
        on the single-player path was per-frame churn for an empty loop."""
        if self._slots:
            for s in self._slots.values():
                s.begin_frame()


class NetService:
    """The transport-neutral `net.*` message seam (architecture B shared state).

    The cart-facing surface is send() + on_message() (bound as `on_net`) + the
    pump() the Player drains each frame; a real transport subclasses and overrides
    send() (frame a ~250-byte ESP-NOW packet) and calls deliver() on each inbound
    frame. Mirrors the old radio contract (moybyte/radio.py: send/on_message/
    receive) so a cart written against radio ports cleanly."""

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
