"""The board's ONE ESP-NOW owner: discovery, pairing, and the two-console link (#7/#65).

ESP-NOW has exactly ONE receive-callback slot for the whole firmware -- there is no
multi-subscriber concept in `esp_now_register_recv_cb()` -- so if two features ever
want the radio, the second one does not get it. This module is therefore the owner
for the whole console: it holds the slot, and it dispatches inbound frames BY TYPE
to whoever asked (netplay's lockstep session, a cart's net.* inbox). Anything else
that wants the radio registers here; it does not open its own.

THE TUNING RECIPE IS MEASURED, and every line of it cost something to learn
(T-Deck <-> Guition on glass, 2026-08-20):

  * `rxbuf` BEFORE `active(True)`, never after. Reconfiguring the ring on a LIVE
    radio permanently desyncs it -- every subsequent `recv()` raises
    `ValueError: ESPNow.recv(): buffer error`, including with nothing in flight,
    until a full active(False)/active(True) cycle.
  * `rxbuf=32768` and not the default 526 (~two frames). At the default, 64 of
    200 messages arrived while `send(sync=True)` returned True for all 200 -- the
    link-layer ack is NOT delivery, and Espressif documents that dropped packets
    are still acked to the sender. At 8192 a 54Mbps burst still lost 7 of 200
    because the faster PHY outran the drain loop; 32768 delivered 200/200.
  * `RATE_54M`. MicroPython ships ESP-NOW at its 1 Mbps default, which is why a
    stock link measures ~208 kbps -- Espressif's own published open-air figure.
    Setting the rate took a 48 KB payload from 1680ms to 65ms, a 26x change from
    one line, and made a typical cart beam in ~55ms instead of 1.6s. It trades
    range for speed, which is the correct trade for two kids sitting together;
    RATE_LORA_250K is the other end of that dial if range ever matters more.
  * `pm=PM_NONE` only WHILE LINKED. Power save parks the radio for hundreds of
    milliseconds at a time, which halved the latency tail when disabled (p90
    14.5ms -> 7.6ms) -- but it also raises idle draw on a battery-powered
    handheld, so it is a session lever and is restored on stop(), not a global.

The RTT floor is NOT the radio: median round trip measured 4.9-5.2ms at every
rate from 1M to 54M, because a 16-byte frame is 128us of airtime at 1Mbps and
2.4us at 54Mbps -- both noise against MicroPython call overhead and WiFi task
scheduling. So do not expect a radio setting to buy latency; it buys bandwidth
and it buys the tail.

Draining happens on the FRAME LOOP, no thread and no callback. At 30Hz an input
frame carries ~2 messages and a 32KB ring holds hundreds, so a per-frame slice is
comfortable -- and it keeps the radio off the same core as the panel flush.

Board-agnostic by construction: every hardware handle is created inside a guarded
method, so this module imports on CPython and its protocol half is exercised by
tests/test_espnow_link.py against a fake radio. Staged on all three console
boards. The P4's `espnow` module is not the SoC's (it has no radio): it is
stock modespnow.c over the moy_c6 shim -- seventeen esp_now_* wrappers riding
ESP-Hosted's custom RPC to the C6 (docs/espnow_p4_2026-08.md, which also
records the morning this same header said that was impossible). One rule that
is load-bearing there and mere hygiene on the S3s: wlan.active(True) BEFORE
the radio, because the C6's radio starts with the host's WLAN.
"""

try:
    from netplay import (PROTO, T_BEACON, T_BYE, T_INPUT, T_JOIN, T_MSG,
                         T_START, LockstepSession)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.netplay import (PROTO, T_BEACON, T_BYE, T_INPUT, T_JOIN,
                                 T_MSG, T_START, LockstepSession)
try:
    from players import NetService
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.players import NetService
try:
    from cart_api import CART_BUTTONS
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.cart_api import CART_BUTTONS

BROADCAST = b"\xff\xff\xff\xff\xff\xff"

RXBUF = 32768          # see the header: 526 loses 68%, 8192 loses 3.5%, this loses none
BEACON_MS = 400        # how often we say we exist; PEER_TTL is 5x this
PEER_TTL_MS = 2000     # a peer unheard this long has walked away
DRAIN_MAX = 24         # messages per frame -- a bound, not a target
START_TRIES = 12       # invites before the host gives up (~5s at BEACON_MS)


def _u16(b, i):
    return b[i] | (b[i + 1] << 8)


class Peer:
    """Another console we can hear. `cart` is what it is sitting on, which is how
    two consoles decide they are about to play the SAME game."""

    def __init__(self, mac, name="", board="", cart="", state=0, seen=0):
        self.mac = mac
        self.name = name
        self.board = board
        self.cart = cart
        self.state = state          # 0 idle (launcher), 1 in a cart, 2 matched
        self.seen = seen


class EspNowNet(NetService):
    """The cart-facing `net.*` backend over the radio (#65 architecture B).

    A cart's net.send() becomes ONE broadcast frame; inbound T_MSG frames land in
    the inbox the Player pumps before _update. Payloads are JSON so a kid can send
    a tuple or a small dict, capped at the radio's frame size -- an oversized
    message is DROPPED with a print rather than truncated, because a silently
    half-delivered message is the kind of bug that reads as a game logic error.
    """

    MAX = 240          # ESP-NOW's frame is 250; leave room for the header

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
        return self._link.broadcast(bytes(bytearray([PROTO, T_MSG])) + body)

    def peers(self):
        return len(self._link.peers)


class EspNowLink:
    """Discovery, pairing and frame dispatch. One per console.

    `radio` and `wlan` are injectable so the protocol half runs on CPython under
    test; left None, start() builds the real ones with the recipe above.
    """

    def __init__(self, board="", name="", ticks_ms=None, radio=None, wlan=None,
                 launch=None):
        self.board = board
        self.name = name or board
        self.radio = radio
        self.wlan = wlan
        self.mac = b""
        self.active = False
        self.error = None
        self.peers = {}             # mac -> Peer
        self.session = None         # the live LockstepSession, or None
        self.net = EspNowNet(self)
        self.rx = 0
        self.tx = 0
        self.drops = 0
        self.recovers = 0           # ring-recover cycles (see _recover)
        self._deferred = []         # non-input frames a mid-frame drain parked
        self.state = 0              # 0 idle, 1 in a cart, 2 matched
        self.cart = ""              # the cart title we are sitting on
        self._session_id = 0
        self._start_frame = None    # the invite, kept so it can be re-sent
        self._start_peer = None
        self._start_tries = 0
        self._beacon_at = 0
        self._pm_was = None
        self._launch = launch       # launch(ws, title) -> bool, the peer-side open
        if ticks_ms is None:
            try:
                from device_util import _ticks_ms
                ticks_ms = _ticks_ms
            except ImportError:  # pragma: no cover - host/tests
                import time
                ticks_ms = lambda: int(time.time() * 1000)  # noqa: E731
        self._ticks_ms = ticks_ms

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        """Bring the radio up with the measured recipe. Idempotent; a board with
        no ESP-NOW degrades to an inactive link, never a crash."""
        if self.active:
            return True
        try:
            if self.wlan is None:
                import network
                self.wlan = network.WLAN(network.STA_IF)
                self.wlan.active(True)
            self.mac = self.wlan.config("mac")
            # Power save off for the session only -- it halves the latency tail
            # and it costs battery, so stop() puts it back.
            try:
                self._pm_was = self.wlan.config("pm")
                self.wlan.config(pm=self.wlan.PM_NONE)
            except Exception:  # noqa: BLE001 -- a port without pm still links
                self._pm_was = None
            if self.radio is None:
                import espnow
                self.radio = espnow.ESPNow()
                self._rate = getattr(espnow, "RATE_54M", None)
            # ORDER IS LOAD-BEARING: rxbuf before active(), never after.
            try:
                self.radio.config(rxbuf=RXBUF)
            except Exception as exc:  # noqa: BLE001
                print("Moybyte espnow rxbuf:", exc)
            self.radio.active(True)
            # ...and the rate only once the radio is up.
            rate = getattr(self, "_rate", None)
            if rate is not None:
                try:
                    self.radio.config(rate=rate)
                except Exception as exc:  # noqa: BLE001 -- a port without rate= still links
                    print("Moybyte espnow rate:", exc)
            try:
                self.radio.add_peer(BROADCAST)
            except Exception:  # noqa: BLE001 -- already added is fine
                pass
            self.active = True
            self.error = None
        except Exception as exc:  # noqa: BLE001 -- no radio -> no link, not a dead console
            self.error = str(exc)
            self.active = False
            print("Moybyte espnow unavailable:", exc)
        return self.active

    def stop(self):
        self.end_match()
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
        self.peers = {}
        self.active = False

    # -- transmit ------------------------------------------------------------

    def broadcast(self, payload):
        return self._send(BROADCAST, payload)

    def _send(self, mac, payload):
        if not self.active:
            return False
        try:
            # sync=False: we never trust the ack anyway (it lies), and waiting
            # for it puts the MAC-layer retry inside the frame budget.
            self.radio.send(mac, payload, False)
            self.tx += 1
            return True
        except Exception as exc:  # noqa: BLE001 -- a radio hiccup must not break a frame
            self.drops += 1
            self.error = str(exc)
            return False

    def _unicast(self, mac, payload):
        try:
            self.radio.add_peer(mac)
        except Exception:  # noqa: BLE001 -- already known
            pass
        return self._send(mac, payload)

    # -- the per-frame drain -------------------------------------------------

    def drain_input(self, budget=DRAIN_MAX):
        """Input-priority drain, safe MID-FRAME.

        The Player calls this right before a lockstep tick: the boards drain
        the radio in the frame TAIL, so without it the peer's input is up to a
        whole loop frame stale by the time advance() looks for it (measured on
        glass 2026-08-24: 8.0% -> 6.4% stalled ticks at DELAY=2). Only T_INPUT
        dispatches here; anything else is PARKED for the tail poll, because a
        non-input frame can relaunch the cart (T_START) or tear down the match
        (T_BYE), and this runs inside the Player's own frame."""
        if not self.active:
            return 0
        n = 0
        try:
            for _ in range(budget):
                mac, msg = self.radio.irecv(0)
                if msg is None:
                    break
                n += 1
                self.rx += 1
                m = bytes(msg)
                if len(m) >= 2 and m[0] == PROTO and m[1] == T_INPUT:
                    s = self.session
                    if s is not None:
                        s.on_packet(m, self._ticks_ms())
                else:
                    self._deferred.append((bytes(mac), m))
        except Exception as exc:  # noqa: BLE001 -- same contract as poll()
            self.error = str(exc)
            print("Moybyte espnow recv:", exc)
            self._recover()
        return n

    def poll(self, ws=None):
        """Drain the ring and beacon. Called once per frame from the board's
        frame tail. Never raises; a radio error disarms the link instead."""
        if not self.active:
            return 0
        n = 0
        if self._deferred:
            pend = self._deferred
            self._deferred = []
            for mac, m in pend:
                self._dispatch(ws, mac, m)
        try:
            for _ in range(DRAIN_MAX):
                mac, msg = self.radio.irecv(0)
                if msg is None:
                    break
                n += 1
                self.rx += 1
                self._dispatch(ws, bytes(mac), bytes(msg))
        except Exception as exc:  # noqa: BLE001
            # The documented failure is a desynced ring, and the documented cure
            # is an active cycle -- not a retry, which raises forever.
            self.error = str(exc)
            print("Moybyte espnow recv:", exc)
            self._recover()
        now = self._ticks_ms()
        if now - self._beacon_at >= BEACON_MS or now < self._beacon_at:
            self._beacon_at = now
            self._beacon()
            self._expire(now)
            self._maybe_match(ws)
        return n

    def _maybe_match(self, ws):
        """Try to pair, on the beacon tick.

        This -- not the offer at cart start -- is what actually forms most
        matches, and the reason is timing: when the second kid opens the game,
        their radio has not heard a single beacon yet, so their peer table is
        empty and an offer right then finds nobody. Both consoles are sitting in
        the cart a moment later with full peer tables and nothing further to
        trigger on, which is a match that never happens. Retrying on the beacon
        tick costs one dictionary walk every 400ms and makes the handshake
        insensitive to who opened the game first.
        """
        if self.session is not None:
            if self.session.dead:
                # The two clocks fell too far apart to heal. Ending it puts the
                # kid back in a one-player game instead of holding a frozen
                # screen, which is the worst outcome available here.
                print("Moybyte link: match lost, playing solo")
                self.end_match()
                self._start_frame = None
                if self._launch is not None and ws is not None and self.cart:
                    try:
                        self._launch(ws, self.cart)
                    except Exception:  # noqa: BLE001
                        pass
                return
            self._chase_start(ws)
            return
        if self.state != 1 or not self.cart:
            return
        p = self.candidate(self.cart)
        if p is None:
            return
        if self.mac > p.mac:
            self.broadcast(bytes(bytearray([PROTO, T_JOIN])) + p.mac)
        else:
            self._host(ws, p, None, None, self.cart, relaunch=True)

    def _recover(self):
        self.recovers += 1
        try:
            self.radio.active(False)
            self.radio.config(rxbuf=RXBUF)
            self.radio.active(True)
            self.radio.add_peer(BROADCAST)
            # The active() cycle resets the PHY rate to MicroPython's 1M
            # default, and until 2026-08-24 nothing re-applied it -- so one
            # ring error mid-match silently put the rest of the session at
            # 1 Mbps with no meter naming it. Same recipe as start(): the
            # rate only once the radio is up.
            rate = getattr(self, "_rate", None)
            if rate is not None:
                self.radio.config(rate=rate)
        except Exception as exc:  # noqa: BLE001
            print("Moybyte espnow recover failed:", exc)
            self.active = False

    def _expire(self, now):
        for mac in list(self.peers.keys()):
            if now - self.peers[mac].seen > PEER_TTL_MS:
                del self.peers[mac]

    def _beacon(self):
        head = bytearray([PROTO, T_BEACON, self.state])
        body = ("%s|%s|%s" % (self.name, self.board, self.cart)).encode()
        self.broadcast(bytes(head) + body[:200])

    # -- receive -------------------------------------------------------------

    def _dispatch(self, ws, mac, msg):
        if len(msg) < 2 or msg[0] != PROTO:
            return
        kind = msg[1]
        if kind == T_INPUT:
            s = self.session
            if s is not None:
                s.on_packet(msg, self._ticks_ms())
            return
        if kind == T_BEACON:
            self._on_beacon(mac, msg)
            return
        if kind == T_MSG:
            try:
                import json
                self.net.deliver(json.loads(bytes(msg[2:]).decode()))
            except Exception as exc:  # noqa: BLE001 -- a malformed peer message is not ours to crash on
                print("Moybyte net recv:", exc)
            return
        if kind == T_START:
            # 8 header bytes, THEN the destination -- a JOIN carries its at 2.
            if not self._for_us(msg, 8):
                return
            self._on_start(ws, mac, msg)
            return
        if kind == T_BYE:
            self.peers.pop(mac, None)
            if self.session is not None:
                self.end_match()
            return
        if kind == T_JOIN:
            if self._for_us(msg, 2):
                self._on_join(ws, mac)
            return

    def _for_us(self, msg, at):
        """Is this addressed frame ours?

        THE HANDSHAKE IS BROADCAST, and that is a hardware decision rather than
        a style one. Beacons and input frames are broadcast and arrive without
        loss; the invite was the protocol's ONE unicast and it kept vanishing --
        376 input frames and 20 beacons received against ZERO invites, while a
        hand-sent unicast to the same MAC in the same session arrived fine
        (measured 2026-08-22). ESP-NOW will only deliver a unicast from a
        registered peer, so the invite depended on peer-table state that an
        active() cycle clears and that races the very first beacon. Broadcasting
        it and carrying the destination in the payload costs six bytes and
        depends on nothing.
        """
        if len(msg) < at + 6:
            return False
        return bytes(msg[at:at + 6]) == self.mac

    def _on_beacon(self, mac, msg):
        if mac == self.mac:
            return
        parts = bytes(msg[3:]).decode().split("|")
        while len(parts) < 3:
            parts.append("")
        p = self.peers.get(mac)
        if p is None:
            p = Peer(mac)
            self.peers[mac] = p
            # REGISTER IT WITH THE RADIO, not just with us. ESP-NOW delivers a
            # UNICAST only from a registered peer, so a console that had only
            # the broadcast address in its peer table heard every beacon and
            # every input frame (both broadcast) and silently dropped the one
            # unicast in the protocol -- the invite. On glass that was 376 input
            # frames and 20 beacons received against zero STARTs, with both
            # boards insisting they could see each other (2026-08-22).
            try:
                self.radio.add_peer(mac)
            except Exception:  # noqa: BLE001 -- already known is fine
                pass
        p.name, p.board, p.cart = parts[0], parts[1], parts[2]
        p.state = msg[2]
        p.seen = self._ticks_ms()

    # -- pairing -------------------------------------------------------------

    def announce(self, cart="", state=0):
        """Tell the neighbourhood what we are sitting on. The console calls this
        when it opens or leaves a cart, so a peer's beacon carries a CART and
        pairing can require both consoles to be on the same one."""
        self.cart = cart or ""
        # A live match outranks whatever the caller thinks the state is: the
        # Player announces the cart on EVERY start, including the restart a
        # match itself triggers, and letting that write 1 over 2 tells the peer
        # we are available while we are already playing them.
        self.state = 2 if self.session is not None else state
        if self.active:
            self._beacon()

    def candidate(self, cart):
        """A peer that is idle, reachable and sitting on the SAME cart, or None.

        Same-cart is the whole handshake: two kids each open Brick Siege and the
        consoles find each other. There is no menu, no code to type and no
        pairing screen, which is the point -- physical proximity is the
        agreement, exactly as the OTA design treats writing to the card."""
        now = self._ticks_ms()
        for p in self.peers.values():
            if p.state == 2 or now - p.seen > PEER_TTL_MS:
                continue
            if p.cart and cart and p.cart == cart:
                return p
        return None

    def offer(self, ws, cart, router=None, seed=None):
        """Called by the Player just before a multiplayer cart starts.

        If a peer is standing by on the same cart, become HOST: pick the seed,
        tell them, and build the session. Returns True when this run became a
        match. The lower MAC hosts -- an arbitrary but SYMMETRIC rule, so two
        consoles never both host and never both wait.
        """
        if not self.active or self.session is not None:
            return False
        p = self.candidate(cart)
        if p is None:
            return False
        if self.mac > p.mac:
            # THEY host -- the lower MAC does, an arbitrary but symmetric rule so
            # two consoles never both host and never both wait. Ask, rather than
            # sit still: they may already be playing this cart, in which case
            # nothing on their side is ever going to offer again and a silent
            # wait here is a match that never happens.
            self.broadcast(bytes(bytearray([PROTO, T_JOIN])) + p.mac)
            return False
        return self._host(ws, p, seed, router, cart, relaunch=False)

    def _on_join(self, ws, mac):
        """A peer that heard us first and lost the MAC comparison. If we are on
        the same cart and free, we are the host it is asking for."""
        if not self.cart:
            return
        if self.session is not None:
            # We already believe we are playing this peer, and they are still
            # asking -- so our invite never landed. Say it again; same session,
            # same seed, so it costs nothing if we are wrong about who they are.
            if self.session.index == 0 and mac == self._start_peer \
                    and self._start_frame is not None:
                self.broadcast(self._start_frame)
            return
        p = self.peers.get(mac)
        if p is None or p.cart != self.cart or p.state == 2:
            return
        # We are already RUNNING the cart, so this half has to go back to frame
        # zero as well: a lockstep sim cannot join a game in progress.
        self._host(ws, p, None, None, self.cart, relaunch=True)

    def _chase_start(self, ws):
        """The invite is not delivered until the peer answers, and the ack does
        not know that (measured: 64 of 200 acked-and-lost). So the host RE-SENDS
        the START until the guest's first input frame arrives -- and gives up if
        it never does, because a host that waits forever is what this looked
        like on glass: both consoles frozen at frame 0, one of them showing a
        second tank that would never move.

        Idempotent by construction: the same session id, seed and config every
        time, so a guest that got the first one ignores the rest.
        """
        s = self.session
        if s is None or s.index != 0 or self._start_frame is None:
            return                      # only the host chases; the guest replies
        if s.packets_in:
            self._start_frame = None    # answered: stop chasing, keep the match
            return
        self._start_tries += 1
        if self._start_tries > START_TRIES:
            print("Moybyte link: peer never answered, playing solo")
            self.end_match()
            self._start_frame = None
            # Back to a ONE-player game: the cart is sitting there with a second
            # tank nobody drives, so re-run it rather than leave a ghost.
            if self._launch is not None and ws is not None and self.cart:
                try:
                    self._launch(ws, self.cart)
                except Exception:  # noqa: BLE001
                    pass
            return
        self.broadcast(self._start_frame)

    def _host(self, ws, p, seed, router, cart, relaunch):
        seed = self._draw_seed() if seed is None else int(seed)
        self._session_id = (self._session_id + 1) & 0xFF
        head = bytearray([PROTO, T_START, self._session_id, 1,
                          seed & 0xFF, (seed >> 8) & 0xFF,
                          (seed >> 16) & 0xFF, (seed >> 24) & 0xFF])
        # ...then the 6-byte destination MAC: the frame goes out as a BROADCAST
        # and the wrong console ignores it (see _for_us).
        # cart title, NUL, the host's cart config -- see LockstepSession.config
        # for why the guest has to take ours rather than keep its own.
        cfg = self._config_of(ws)
        body = cart.encode()[:120] + b"\x00" + cfg
        self._start_frame = bytes(head) + p.mac + body
        self._start_peer = p.mac
        self._start_tries = 0
        self.broadcast(self._start_frame)
        self._begin(ws, 0, seed, self._session_id, router,
                    config=self._decode_config(cfg))
        if relaunch and self._launch is not None and ws is not None:
            try:
                self._launch(ws, cart)
            except Exception as exc:  # noqa: BLE001
                print("Moybyte link relaunch failed:", exc)
                self.end_match()
        return self.session is not None

    def _config_of(self, ws):
        """This console's tuning for the cart it is about to host, as JSON."""
        try:
            import json
            cfg = getattr(ws, "config", None) if ws is not None else None
            if not cfg:
                return b"{}"
            body = json.dumps(cfg).encode()
            # The frame is 250 bytes and the seed matters more than the tuning:
            # an oversized config is DROPPED whole and both sides keep their own
            # defaults, which is wrong together rather than wrong apart.
            return body if len(body) <= 100 else b"{}"
        except Exception:  # noqa: BLE001
            return b"{}"

    @staticmethod
    def _decode_config(body):
        try:
            import json
            cfg = json.loads(bytes(body).decode())
            return cfg if isinstance(cfg, dict) and cfg else None
        except Exception:  # noqa: BLE001
            return None

    def _on_start(self, ws, mac, msg):
        """The guest half: the host has chosen a cart, a seed and our index."""
        if len(msg) < 14 or self.session is not None:
            return
        session = msg[2]
        index = msg[3]
        seed = msg[4] | (msg[5] << 8) | (msg[6] << 16) | (msg[7] << 24)
        body = bytes(msg[14:])          # 8 header + the 6-byte destination
        cut = body.find(b"\x00")
        if cut < 0:
            cart, cfg = body.decode(), None
        else:
            cart = body[:cut].decode()
            cfg = self._decode_config(body[cut + 1:])
        try:
            self.radio.add_peer(mac)
        except Exception:  # noqa: BLE001
            pass
        # The session is built BEFORE the cart opens, so Player.start finds
        # ws.netplay already set and the shared seed already applied.
        self._begin(ws, index, seed, session, None, config=cfg)
        if self._launch is not None and ws is not None:
            try:
                if not self._launch(ws, cart):
                    print("Moybyte link: no cart named %r here" % cart)
                    self.end_match()
            except Exception as exc:  # noqa: BLE001
                print("Moybyte link launch failed:", exc)
                self.end_match()

    def _begin(self, ws, index, seed, session, router=None, config=None):
        if router is None and ws is not None:
            router = getattr(ws.input, "players", None)
        if router is None:
            return False
        self.session = LockstepSession(index, seed, self.broadcast,
                                       CART_BUTTONS, router, session=session,
                                       config=config)
        self.state = 2
        if ws is not None:
            ws.netplay = self.session
        return True

    def end_match(self):
        if self.session is not None:
            try:
                self.session.close()
            except Exception:  # noqa: BLE001
                pass
            self.session = None
        if self.state == 2:
            self.state = 1 if self.cart else 0

    def _draw_seed(self):
        try:
            import random
            return random.getrandbits(30)
        except Exception:  # noqa: BLE001
            return self._ticks_ms() & 0x3FFFFFFF

    # -- diagnostics ---------------------------------------------------------

    def status(self):
        """(active, peers, matched, frame) -- the shape the dev channel's `state`
        and the Settings row read. `frame` is None with no match, never 0: a
        frozen 0 is also what a BROKEN lockstep looks like, and that ambiguity
        is exactly what hid the fold meter for a month."""
        s = self.session
        return (self.active, len(self.peers), s is not None,
                None if s is None else s.frame)

    def stats(self):
        s = self.session
        return {
            "active": self.active,
            "mac": self.mac.hex() if self.mac else None,
            "peers": [(p.name, p.board, p.cart, p.state) for p in self.peers.values()],
            "rx": self.rx, "tx": self.tx, "drops": self.drops,
            "recovers": self.recovers,
            "error": self.error,
            "match": None if s is None else {
                "index": s.index, "frame": s.frame, "stalls": s.stalls,
                "stall_ticks": s.stall_ticks, "delay": s.delay,
                "m_ema": s._m_ema,
                "waiting": s.waiting, "in": s.packets_in, "out": s.packets_out,
                "peer_frame": s.last_peer_frame,
            },
        }


def launch_cart(ws, title):
    """Open the cart the host named -- the guest half of a link.

    Two cases, and the second one is why this is not just the dev channel's
    `run`. The kid may ALREADY be playing this cart: two people each open Brick
    Siege, and whoever is second is what makes it a match. That console has to
    go back to frame zero, because a lockstep sim cannot join a game in
    progress -- so the same cart RE-RUNS in place (ws._start, the console's own
    re-run verb, which Player.start is documented to support), picking up the
    session and the shared seed on the way through. A restart three seconds
    after opening a game is a fair price for "and now your friend is here".
    """
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


def make_link(board="", name=""):
    """Injected backend factory, mirroring device_wifi.make_wifi: run_desktop
    hands this to the shared Workstation as `ws.link`."""
    return EspNowLink(board=board, name=name, launch=launch_cart)
