# Netplay v1 — the two-console lockstep contract

**Status: SHIPPED and BINDING (last amended 2026-11-18).** Two kids open the
same cart on their own consoles; the boards hear each other over ESP-NOW inside
a second and simulate one game on two screens from a shared 30 or 60 Hz tape of button
masks. Being in the same room is the pairing ceremony.

**The campaigns are archived.** `docs/history/espnow_multiplayer_2026-08.md` is
the S3 transport ledger (the 26× that sat in a rate default, the RTT floor, the
four bugs the desk found that host tests could not, the input-latency hunt);
`docs/history/espnow_p4_2026-08.md` is the C6-shim track (phases A–G, the BLE
regression, the stall-rate hunt, the slave-image pipeline). Those are the
RECORD; this file BINDS — the rules a change must not break, each with its date
and the measurement that settled it. Trackers **#65**, **#7**, **#99**.

## 1. The bodies

`native/moy_play/moy_match.c` is the deterministic core — tick clock, input
tape, `advance()` — and the link's protocol, in C (#224, sprint 4): the kernel's
one instance owns the radio's receive ring for the whole console (ESP-NOW has
one firmware-wide receive slot, held by `native/moy_net/moy_link.c`), dispatches
by frame type and is polled from the loop's tail with no crossing into Python.
`runtime/run/players.py` is the console's view of it: the radio's bring-up, the
session's player slots, the cart it asks the console to open.
`runtime/run/player.py` adopts a session, feeds it and lets go; a Lua run's
lockstep frame is the kernel Player's (`moy_play_lockstep`). The same link
beams a cart from one console to another (§7); `runtime/run/link_beam.py` is
the console's half of that.

## 2. Lockstep: what crosses the air, and when a console may simulate

**Inputs cross; state never does.** On glass 2026-08-20: a 16-byte input frame
round-trips in 5.0 ms median, a 250-byte state blob in 15.0 ms — triple the
price for the larger and lossier idea, the round trip at these sizes being
interpreter and scheduler rather than airtime. Rollback is refused separately:
re-simulating the world several times a frame is not something kid-authored
`.moy` code can promise.

**Without the input it owes, a console STOPS** — it never predicts. A guessed
frame parts the two worlds permanently and with no symptom: both screens keep
painting something plausible, and the two games differ. Link-cable handhelds
show this as "waiting for player"; do not soften it.

**A frame's local input is sampled ONCE** (2026-11-06). A stalled tick
retries every loop frame (below), and each retry used to write the buttons
held by then over the frame it had already put on the air; the peer keeps the
first copy it hears, so a press or a release during a stall played one way on
one screen and the other way on the other, for good. That was the owner's
Brick Siege desync, and the host's two-console harness
(`tests/test_espnow_link.py`) parted the two worlds at frame 34 with a third of
the packets lost. The tape is first-write-wins at both ends, and every frame up
to the sampling lead is written before a packet carries it.

**Redundancy instead of retransmit, and the window must chase the peer.** Every
packet repeats the last few frames of input (a mask is one byte, so it is free,
and a drop heals on the next packet rather than a round trip — the
acknowledgement lies, §4) and states **the frame its sender is still stuck on**.
Fixed span is a deadlock: a console stalled past it drops out, and then each
side waits on a frame the other has scrolled past. Two boards ran to frame ~150
and froze solid with packets flowing both ways (2026-08-22) before that field
existed.

**Input delay is ADAPTIVE: it opens at one tick (33 ms at 30 Hz), rises under
stall pressure (2026-08-25) and comes back down when the peer has a tick to
spare (2026-11-06).** Delay is each console's private sampling lead over its
own tape, needs no agreement on the wire, and may differ between the sides — a
verification match held the T-Deck at 1 against the P4 at 2 throughout, 29.5
ticks/s and 2.0% stalls. Neither direction can rewrite an input the peer may
already hold, because a frame is sampled once and the tape is first-write-wins:
a raise writes the frame it skips with the same sample, and a drop finds the
next frame already sampled and plays it.

**The PEER's reports steer a console's delay** (2026-11-06), because a
console's lead is what the peer waits on: its delay sets the peer's stalls,
not its own. On glass the host of a Guition S3 <-> P4 match stalled on 4.6% of
its ticks behind the guest's one-tick lead while raising its OWN delay — which
served the guest — and lowering it again, four times in 90 s. Every input
packet carries the sender's stalled-tick count over its last settled window
and its arrival-margin EMA of the other side's inputs (how many ms before it
needed them they landed). A console raises a tick when the peer's last window
stalled ≥12 distinct ticks of a 3 s window at 30 Hz (24 at 60; each side's
first window is formation grace), up to 66 ms (two ticks at 30 Hz, four at 60),
and says so on serial; an earlier bar of 5 with no grace escalated every match
always, because ambient-RF loss arrives in bursts. It lowers a tick — the way
down is a design choice beside the 60 Hz tick — when the peer's last window
was clean (at most a quarter of the raise's bar), the lowest margin the peer
reported through this console's window was at least a tick plus 8 ms
(`MOY_MATCH_LOWER_GUARD_MS`), and two windows have passed since its last change
(`MOY_MATCH_LOWER_HOLD`), so a match that escalated on a burst of loss returns
to one tick once the air clears instead of keeping the higher delay for good.

**A cart paced 60 or "free" ticks at 60 Hz in a match when both consoles hold
it** (owner, 2026-11-06: "i dont want a higher delay"), so one tick of delay is
16.7 ms. Each console says the tick it holds for the cart in its beacon and its
JOIN (`players.match_rate` of the cart's pace: 60 for a cart paced 60 or
"free", else 30), and the host starts the match at the lower of the two, in
START; the guest takes it. `link rate 30|60|auto` on a console's dev channel
forces its own word, and a forced host never steps down. What is a time stays a
time at either rate: the delay's ceiling (`MOY_MATCH_DELAY_MAX_MS`) and the
escalation window are milliseconds and seconds, their tick counts scaled by the
rate.

**A 60 Hz match that cannot keep its rate steps down to 30 for the rest of the
match** (owner, 2026-11-06). Lockstep ticks both consoles at the slower one's
pace: the first 60 Hz build held a Guition S3 <-> P4 Brick Siege Lua match to
47.7 ticks/s, and the P4's waits on the S3 read as stall pressure that raised
the S3's delay to its 66 ms ceiling. After a settled window ticking under 93%
of the rate (`MOY_MATCH_STEP_BAR`) the host names a frame 32 on
(`MOY_MATCH_STEP_LEAD`) and every input packet carries it; the guest cannot
simulate that frame before it holds the host's input for it, which travels in
a packet that carries the notice, so both consoles tick every frame from there
at 30 with a dt of 1/30 (`moy_lockstep_hz_at`). The match's clock runs on
without a jump (`moy_lockstep_ms`: time() is the ms of the ticks at each rate),
each console's delay keeps its milliseconds rounded up to the new tick, and the
first window at the new rate is grace, like formation's. The step is one way:
a match never steps back up. It replaced a draw divisor (2026-11-06), which
kept the 60 Hz tick and drew every other one, and still ticked at 93%.

**The 2026-08-22 "DELAY=1 measured and REJECTED at 14%" verdict was the PACING's
fault, not the radio's**, and is reversed. Tick debt was repaid as a burst at
the loop rate, which outruns a peer emitting at 30 Hz, so both consoles
self-hastened to the edge of input availability (margin p5 ≈ 0 ms even at delay
2) and one stall became a coupled oscillation. Three changes fixed it: debt is
DROPPED, never burst-repaid; a stalled tick retries every LOOP frame instead of
sleeping out a whole one (a 5 ms miss used to cost 34 ms); and the GUEST slews
its phase toward 12 ms of margin, the host's clock being the anchor. The S3 pair
then measures 1.8–2.3% stalls at 29.5 ticks/s on delay 1.

**Determinism is a precondition, enforced here rather than asked of carts:** a
fixed `dt`, a re-seed each logic frame from (seed, frame) since drawing consumes
the stream too, the HOST's cart config winning for the match, and a cart's
`_draw` running exactly once for each tick it simulates (2026-11-06): a kid's
`_draw` keeps state as often as not, and a "free"-paced cart such as Brick
Siege drew every loop frame of a match, a count no two boards share. The
frame's plan (`Player.frame_plan`) decides whether a tick may start, and a
frame planned between ticks starts none even when the board's clock reaches
one before its lockstep step (2026-11-07): with a millisecond between the
two on a 4 ms loop, the host's harness simulated a fifth of the ticks in
frames that drew nothing. A cart's
`time()` reads the match's clock (frame × tick) and `key()` reads nothing,
because a board's wall clock and keyboard bytes are its own. A Lua tick's `dt`
is the C float of the match's tick whichever path runs it (2026-11-06): the
Guition S3 ran Brick Siege Lua on the console's path, handing the kernel a
`dt` that had crossed the VM as REPR_C's 30-bit float, while the P4 ran it in
the kernel's front frame at the full float, and the world check parted them
at its first checkpoint. CPython and
MicroPython ship different PRNGs, so only device↔device agrees. And the Player
drains the radio BEFORE deciding a tick (`drain_input`): tail-draining left the
peer's input a loop frame stale, worth 8.0% → 6.4% stalls on the P4 pair.

**A desync is SAID, never painted** (2026-11-06). Every 32 frames
(`MOY_MATCH_CHECK_EVERY`) each console stamps a checkpoint, the state entering
that frame: a hash of every input it applied, and a digest of the cart's world
when its tier gave one. Every input packet carries the sender's newest stamp,
and the first that disagrees prints `Moybyte link: OUT OF SYNC at frame N
(inputs|worlds differ)` and puts "Out of sync" over whatever screen is up (the
kernel's notice for a run it drives, the console's otherwise); the match plays
on. The input half is always on and is C alone. The world half is the
frame-aligned probe of §5 built in, and a diag: `link check on` on the dev
channel of BOTH consoles, a digest only being compared against another (a
Python cart's data globals as text, `players.world_text`; a Lua cart's by
`moycore_lua_digest`; a compiled cart gives none). A cart that reads local
state on purpose -- Harpoon Pop's high score is each console's own `pmem` --
reads as a world desync there. The check rides the input packet, so
`MOY_MATCH_PROTO` went to 2 with it (3 with the step down's notice), and
consoles on different protocols do not see each other.

**Two consoles meet on one channel** (2026-11-06). ESP-NOW is heard only on
the channel it is sent on, and a station associated to an AP -- SSH's while it
serves, the store's, a sync -- is pinned to the AP's channel: a Guition S3
serving SSH sat on 6 while the P4 sat on 1, and no match formed. Each beacon
says its sender's channel, negative when pinned. A console whose station is
free sits on `MOY_MATCH_HOME_CH`; while it hears no peer on its cart it spends
one beacon tick in `MOY_MATCH_PROBE_EVERY` on the next channel of a probe list
(6 and 11 first), and a pinned peer heard there is followed to its channel for
as long as it is heard, so the match forms there with the station still
associated. A pinned console never moves, and a live match never tunes
(`moy_link_channel`, the io's `channel`). A console in a beam's receive
state (§7) has a person watching it, so while it listens it walks the list
a channel every `MOY_MATCH_BEAM_PROBE_MS`, home each
`MOY_MATCH_BEAM_PROBE_HOME`-th dwell, and an offer is said again every
`MOY_MATCH_BEAM_OFFER_MS`, inside one dwell; the sender's own walk keeps the
match's pace, which is what a free sender takes to reach a listener pinned
to its AP's channel.

**The handshake is BROADCAST, addressed in the payload** — START and JOIN carry
the destination MAC as six bytes. Unicast was the protocol's only such frame and
kept vanishing: 376 input frames and 20 beacons arrived against **zero**
invites, while a hand-sent unicast to the same MAC in that session was fine
(2026-08-22). ESP-NOW delivers unicast only from a REGISTERED peer, an
`active()` cycle wipes that table, and it races the first beacon. Do not tidy it
back. The invite re-sends until the guest's first input frame lands; one dropped
START used to deadlock both consoles for good.

**A match hears only its peer** (2026-11-09). Frames are neither signed nor
encrypted, so the sender's MAC is the one check (`moy_match.h`, WHO IS HEARD):
a live match is bound to its peer's address, and an input frame, a BYE or a
`net` message from any other console is dropped; outside a match a `net`
message is taken from a console heard beaconing this cart. A START is taken
only from a console heard beaconing and only for the cart this console sits
on, so it re-runs that cart and opens nothing else. A console that takes the
peer's address can still do all of it: the check narrows who, it does not
authenticate.

**Ports: the link is a device any process opens** (owner, 2026-11-09,
recorded on #246). Like a socket, the link belongs to no one game: the
lockstep match stays the game in front's, and messages are delivered to each
holder by port. `net.send` and `on_net` are port 0, the game's, on the wire
they always spoke (`T_MSG`, no port byte). A process opens a port 1..65535
by a `net` handle (`runtime/system/dev_handles.py`, under `multiplayer`;
`docs/app_api_v1.md` §10), one holder a port, and a port's message is
`T_PORT` (`MOY_MATCH_T_PORT`, 7): the port as two little-endian bytes after
the type, then at most 240 bytes. A new frame type left `MOY_MATCH_PROTO` at
3: a console without ports drops a `T_PORT` as an unknown type and still
plays and talks on port 0. Each open port keeps its own queue
(`MOY_MATCH_PORT_DEPTH` deep, the newest dropped and counted while it is
full), a message for a port nobody opened is dropped, and a read takes whole
records carrying the sender's MAC. Who a port hears is chosen per port: port
0 keeps the rule above (the match's peer, else a console beaconing this
cart); an open port (`from=any`, the default) hears every console in range,
a datagram socket's rule, its holder judging the sender (radio input is
untrusted: `docs/os_2026-11/threat_model.md` §5); a `from=match` port hears
only the live match's peer and nothing outside a match. No port reaches the
session: an input frame is `T_INPUT` and a `T_PORT` never is, whatever its
bytes. A port sends while it is open and within its send budget (2026-11-09):
token buckets in `moy_match_port_send`, the port's own (`MOY_MATCH_PORT_RATE`
frames a second, a burst of a reader's queue) and the console's, which every
port's send also spends, tighter while a match is live (`MOY_MATCH_LIVE_*`).
A send past either answers BUSY (`MOY_MATCH_PORT_AGAIN`) and nothing goes on
air; the handle's `sent` and `refused` count both. The numbers come from the
link's cadence (`moy_match.h`, THE SEND BUDGET, has them and the
arithmetic): lockstep sends at most one frame per `MOY_MATCH_LOCK_MS`, and
the P4's transport clears one per ~10 ms in one queue with the inputs, so a
live match lets at most one port frame sit ahead of an input. Port 0 has no
budget. The radio is up while the game holds it, any port is open or a beam is under
way (`EspNowLink`'s three holders; a process's radio lease is `net`, a
beam's `link`); a game's end with a port open ends the match and leaves the
radio beaconing the launcher's state, and the last holder's let-go takes it
down. A process holds
four ports and the console `MOY_MATCH_PORTS` (8); the process's end closes
what it left open.

## 3. Nothing waits forever — every death has one exit

A frozen screen with no explanation is what this removes. Every way a match can
end funnels through **one exit in `native/moy_play/moy_match.c`** (`lost`) —
print, end the session (the console's `ws.netplay` is a view of it and goes
with it), and ask the console to re-run the cart solo. Its callers are the fall-behind death, a host whose invite is never
answered, and an inbound T_BYE. Clearing only the link's reference left
`ws.netplay` pointing at a corpse, and the re-run re-entered `Player.start`,
whose adoption of `ws.netplay` is how a match forms at all — so the "solo" game
picked the dead session back up and never simulated again (on glass: a survivor
stuck at frame 2874, stalls past 6856, the link reporting no match; 2026-08-27,
`387a68c`). **`Player.start` refuses to adopt a session that has declared itself
dead**, for the re-runs the link never hears about (Editor PLAY, the dev `run`,
a shelf tap) — which frees that run to form a NEW match. **T_BYE is receive-only
today** — nothing sends one, so that half rests on `tests/test_espnow_link.py`,
the protocol's host pin.

**A stopped link asks nothing of the next run** (2026-11-06). The kernel's tail
polls the link in C and the console syncs the cart it asks for in the Python
frame's tail, which a front run never reaches: a START or a give-up landing in
that gap leaves the request pending, and a run ended there stops the link with
it. `moy_match_stop` drops it. Kept, the next multiplayer start's `offer()`
synced it and re-ran the cart from inside `Player.start`, whose runtime then
found the nested run's open ("moycore: a run is already open").

**Two consoles on one bench pair the same way two kids do.** A board pass runs
every suite at once, so the on-glass checks of a multiplayer seed take one
machine-wide room (`tests/on_glass.py`'s `radio_room`): two boards in Brick
Siege Lua together formed a match, whose guest re-run reads stop `place` and
whose lockstep stalls on the peer.

**The PERF line names a match eating frames** (`13c3d9e`, 2026-08-27):
`net=<ticks/s>` on both emitters, right after `fps=`, because for a linked game
the drawn rate IS the tick rate — the world moves only on the shared tick and
`Player.frame_plan` gates every frame that tick is not due for (a genuine rate
lock; the tick model's draw divisor stays 1 under a session, #217). **A console with no
session prints `-`, never 0**: 0 is a real reading, matched but not advancing,
and a frozen 0 is what a broken meter looks like — the lesson `fold=` taught by
lying for a month.

**`lock=late/stall/slew/delay/margin` says where the rate went** (2026-11-07,
the line's last field, `-` with no session). The first three are ms of the
tick schedule given up per second, so their sum over the tick's ms is what
`net=` fell short by: `late` is a tick that came due a whole period late with
nothing stalled (this console's own frame: a collection, a heavy frame; read
it beside `gc=`), `stall` a slot that passed while a tick waited on the peer's
input, `slew` the guest's phase moved later. Then the input
delay in ms and the margin the peer's inputs land with (`-` before one is
timed). One console's `late` shows up as the other's `stall`. While a kernel
run plays the match in front (a Lua cart), no console frame runs to push
either field, and the kernel's loop writes both from its own match
(`moy_match_kernel_perf`).

## 4. Radio facts, one session each

- **A restart must not stop the link.** `Player.release_world` stops it only
  when `ws.netplay` is None, and the stop leaves the radio up while a
  process's port or a beam holds it (§2, Ports; §7). A match forms by a peer's invite reaching a console
  already playing, which re-runs the cart from frame zero; the dying run used to
  tear down the session that caused the restart, presenting as a host frozen at
  frame 0 showing a tank nobody drove.
- **The tuning recipe is ORDER-SENSITIVE and the ack LIES.** `rxbuf=32768` goes
  in BEFORE `active(True)` — reconfiguring the ring live desyncs it until a full
  activation cycle — and `RATE_54M` after. At the 526-byte default, 64 of 200
  messages arrived while `send(sync=True)` answered True for every one.
- **At 54M a frame's delivery depends on its bytes, so a beam sends at 12M**
  (2026-11-19, on glass, #7). A Guition S3's 54M frames reach a T-Deck and a
  Waveshare P4 by content: a given full frame arrives nearly every time or
  never, whatever the number of sends, and the same frames all arrive at 12M
  and at 6M. A match never notices (its frames are small, and each carries
  the inputs the last one did), but a beam resends the same bytes: a report
  whose bitmap was all ones never bound its sender, and a chunk of the wrong
  bytes would stall a file for good. `EspNowLink.phy` gives the radio
  `RATE_12M` while a beam holds it and the match's `RATE_54M` back when a
  game takes it; at a beam's pace the air time of 12M is not what bounds it.
- **The receive ring is the kernel's** (`native/moy_net/moy_link.c`, since
  2026-10-07). The port's modespnow committed a record in three ring puts while
  its reader waited only for the header, so a busy drain catching one
  mid-write raised `buffer error` on a healthy ring that was then really
  desynced (about once a second under load). The kernel's callback latches a
  whole record under a spinlock and the frame takes whole records.
  `_recover()` re-applies the PHY rate, which an activation cycle resets to
  1 Mbps, and counts itself in `stats()`.
- **Most of a radio board's packet loss was its own BLE keyboard hunting.**
  `gap_scan(5000, 30000, 30000)` is interval == window: continuous, 5 s on and 5
  s off forever with no keyboard connected. At an idle desk the P4 got 19.2/s of
  32.5 offered with it running, 29.5/s once stopped. Rescans are 30 ms in every
  300 ms now, and passive; only the picker scans continuously. **Suspect the
  scan first when a radio symptom shows up only while the frame loop RUNS** — a
  stalled loop stops re-arming it, so blocking benches read clean.

## 5. Hardware terms of the contract

**FLOAT WIDTH IS PART OF THE LOCKSTEP CONTRACT** (2026-08-24, owner-felt on the
first cross-architecture match). A frame-aligned world-checksum probe read **0
of 1105 frames agreeing**, and one dump line explained it: an accumulator
printing `0.21666668` on the T-Deck against `0.216666668` on the P4. The S3s run
REPR_C's 30-bit truncated floats, the P4 ran REPR_A's boxed singles — one
arithmetic at two precisions, which chaos amplifies into flipped branches in
seconds. The P4 took `moybyte_patch_repr_c` and the probe re-read **1106 of 1106
bit-identical**. Every board that can hold a link runs REPR_C; one that cannot
take it cannot join a match.

**Board scope is three consoles**: the T-Deck, the Guition S3 and the
Waveshare P4. The browser has no radio and is out, and the Guition P4's C6 runs
Guition's factory slave with no ESP-NOW shim, so its link comes up inactive (its
README). The S3 pair talks to its own silicon. The Waveshare P4 has none of its
own and reaches the C6 over ESP-Hosted 2.12.12's custom RPC through
`native/p4/moy_c6` — seventeen
`esp_now_*` wrappers plus the rate lever, under the same link protocol the S3s
run — against a shimmed C6 slave the console
flashes itself over SDIO. One rule is load-bearing there: `WLAN.active(True)`
before the radio, because the C6's radio starts with the host's WLAN. Its send
began as a synchronous RPC costing **10.2 ms blocked on the VM core per send**
against ~60 µs on an S3; a 12-deep TX queue plus sender task took that to **20
µs**, and the pair to 0.7–2.8% at 29.6 ticks/s.

## 6. One cart API — over the radio, or over a second keyboard

A cart sees `players()` and `btn(name, i)` and nothing else: it never learns
whether pad two is a keyboard beside it or a console across the room. The
two-player seeds `system_carts/moybyte.brick_siege.moy` and `moybyte.harpoon_pop.moy` read that
API, and their Lua twins are ported in step — both tiers have fed player two
since 2026-08-22 (libmoy's snapshot had the slots and nothing filled them, so a
faithful twin fielded one tank against the original's two). Brick Siege's roster
global had to be renamed `players` → `tanks`: a list under that name shadows the
verb, so the cart indexed its own roster where it meant to count pads. Touch and
mouse are refused for a match's duration: only buttons cross the air.

**LOCAL 2P is that same API with no radio** (#65 Phase 1). Settings → **2
PLAYERS** hands a paired Bluetooth keyboard the second slot, so two kids share
one screen on two real keyboards. The mechanism is #26's source model — a source
carries a player, two of them disagreeing IS multiplayer — so `players()`
answers 2 with no transport, session or netcode. Capability-gated on
`ws.toggles.second_keyboard()`, non-None only where the board owns a keyboard already
(the T-Deck); on a touch-only board the Bluetooth keyboard IS `ws.keyboard`, so
handing it to player two leaves player one with nothing. Two honesty rules, both
pinned: the setting **refuses** where it cannot work rather than reporting ON,
and a **disconnected** keyboard gives its slot back, the slot being an intent
re-resolved every poll — a latched one leaves a cart fielding a character nobody
can move. **Dividing the T-Deck's built-in keyboard between two kids shipped and
was pulled inside a day** (owner, 2026-08-22): it worked, and that thumb
keyboard is five centimetres wide.

## 7. Beaming a file (2026-11-18, #7; streamed 2026-11-19)

A person on one console sends a cart from its shelf, or one of their files,
to a console nearby, and a cart that crosses is one the two can then meet on
as any two consoles do. The C carries it (`native/moy_play/moy_match.h`,
BEAMING A FILE, which has the frames and every number), the console feeds
and empties it, asks and lands it (`runtime/run/link_beam.py`). The owner's
four terms (2026-11-19, #7): no size limit and RAM that does not grow with
what is carried, any file and not only a cart, as fast as the link allows,
and both consoles showing how far along it is and the time left, either side
able to stop it at any moment.

**What crosses is one stream**: a person's file as it is, or a cart's
files, each as it is, behind a head naming each one's path and size in the
order the store's packer walks the folder (2026-11-19: a cart is no longer
packed into a `.moy` archive first, which held a multi-megabyte cart's
frame for seconds and needed its size free twice on each side). One frame type, `T_BEAM` (8),
carries the beam's six kinds; like `T_PORT` it left `MOY_MATCH_PROTO` at 3,
since no release carried the first cut's layout, and a console without it
drops the frames as an unknown type. Every frame is broadcast, and every one
but the offer names its destination in the payload, as the handshake does
(§2). A size and a chunk's index are 32 bits, so what bounds a beam is the
receiver's free space, checked before it asks: an offer it has no room for
is refused with a NO that carries why, and its sender says so.

**The bytes go through a window, one store op a frame.** Each side's link
holds one ring of `MOY_MATCH_BEAM_WINDOW` chunks whatever the size; the
sender's console reads into it, and the receiver's writes what it holds in
order into the arrival's temporary (a file's, or the cart's staged folder),
`link_beam.BLOCK` at a time at block-aligned offsets of each file, in one
store op a frame that ends when the ring is full (or empty) or after
`link_beam.STORE_MS`, the file it is on held open from frame to frame. A
beam's RAM is fixed: the ring, one block and a cart's head. **A chunk is as
long as both links carry**: a beam's frames are the only ones longer than
250 bytes, ESP-NOW v2's 1470 between two consoles whose radio is the chip's
own (`moy_link_frame_max`); the offer names the sender's chunk and the
receiver takes the smaller of it and its own, so a P4, whose C6 shim carries
v1 frames, sends and takes chunks that fit 250 bytes.

**The receiver drives completion, because the ack lies** (§4). The sender
offers on the air until a console answers; the receiver's yes binds the two
by address and beam id, and from then on it reports, every
`MOY_MATCH_BEAM_NEED_MS` and as soon as its console has taken a quarter of
the window's room, the lowest chunk it lacks, the top of what it
holds, the end of its room and which of the window's chunks it lacks. The
sender sends what its console filled up to that room, then again what a
report names lost. **The pace is found**: it starts at
`MOY_MATCH_BEAM_RATE` and each report moves it, down where a send was
refused (the Waveshare P4's queue, §5) or the receiver's holes grew, up
where the rate held the sender back. **The hash is streamed**: FNV-1a over
the bytes as the sender's console fills them and as the receiver's takes
them; the sender answers a report of everything in with its hash (SUM), the
receiver says DONE, whole or damaged, and answers a SUM of that beam from
its verdict for `MOY_MATCH_BEAM_KEEP_MS` after, so a sender whose DONEs were
all lost still hears it. A copy that fails is dropped whole and both
consoles say it arrived damaged.

**A beam is taken only by a person who asked for one.** Frames are neither
signed nor encrypted (§2), so an offer is heard only while a person on the
receiving console has opened the receive state (the system menu's RECEIVE
NEARBY), and is asked about in the console's one dialog ("Receive
<title>?", the console it comes from and its size, No and Receive) before a
byte is written. The receiver checks every path a cart's head names
(`link_beam.rel_ok`: nothing outside the cart's folder, nothing the packer
skips) before it writes a byte of it, and a head that fails reads as a
damaged copy. A cart lands on the shelf by the adopt a dropped `.moy` lands
by (`moy_carts.install_staged`), its staged folder moved whole by one
rename once the hash passes: a cart that arrived as files, whose first run
asks with the install prompt's lines (`docs/os_2026-11/processes.md` §2), so
a received cart never runs before its person says yes. A file lands in the
shared folder Downloads (`docs/os_2026-11/between_apps.md` §2) under its own
name made safe (no folder, no reserved character, nothing hidden), beside
any file of that name rather than over it, and nothing runs it. The sender's
SEND is a verb of a cart's card (its menu: a long press, a pad's X, the
system menu's card group) and of a project's or a file's row in Files
(`intents.Intents.send`), wherever the console's link can come up: a link
whose bring-up failed (the Guition P4's C6 without its shim, §5) offers
neither, and a first attempt there refuses at once.

**Nothing waits forever, and a match outranks a beam.** An offer nobody takes
and a receive state nothing reaches end after `MOY_MATCH_BEAM_WAIT_MS`; a
bound side that hears nothing from its peer for `MOY_MATCH_BEAM_SILENT_MS`
ends; a beam its console stops looking at for `MOY_MATCH_BEAM_TEND_MS` (a VM
that went away) ends and tells its peer; a no, a refusal, a stop on either
side and a damaged copy each end both, every end is said on whatever screen
is up, and none leaves a temporary behind (each beam's start sweeps one a
reset left). A no is said as often as a DONE (`MOY_MATCH_BEAM_DONES`) and
kept as a verdict is: an offering sender spends one beacon tick in
`MOY_MATCH_PROBE_EVERY` off its channel and hears none of the copies said
meanwhile, so for `MOY_MATCH_BEAM_KEEP_MS` the console that said no answers
every frame of that beam its peer still sends (its next offer, a report, a
chunk) with the no again, from the channel it said it on. The console keeps
its radio up `link_beam.PART_MS` after a beam ends, because the Waveshare
P4's sends are queued (§5) and a radio stopped in the same call never sends
them, and `link_beam.KEEP_MS` after a receive it finished and after a no it
keeps. No beam starts while a match is live or a
game holds the radio, a beam under way ends when a match goes live (in C) or
a game takes the radio (`EspNowLink.start`), and a beam holds the channel
while it is bound, as a peer on the cart does (§2's probes): a free station
stays on the channel its beam's peer was last heard on
(`MOY_MATCH_BEAM_HERE_MS`), which is where an offer taken on a probe tick
came from. While a bound beam's peer is heard where it rests the link
asks the radio nothing (`tune`, 2026-11-19): on the Waveshare P4 the
channel's read is two round trips to the C6 over the link the chunks arrive
by, and with them a beam into that console stopped the C6 answering a few
seconds in, until the console's watchdog restarted it. A game's stop takes its cart out of the beacon
(`EspNowLink.stop`), so the radio a beam brings up at the home invites
nobody to the last game.

`tests/test_link_beam.py` is the net: two consoles over a lossy, reordering
air, a file of several megabytes against the beam's fixed RAM and one
store op a frame, a cart's folders and empty files crossing with nothing
packed, a head naming a path outside the cart, a chunk as long as both
links carry, a damaged
chunk, a receiver with no room, a name already taken, a stop from either
side, a stale temporary, a refusing send queue and the pace, the lost DONEs
over forty seeds, a console not receiving, a decline, a sender gone
mid-transfer, a beam nobody tends, the receive state's give-up, the game's
precedence, an inert link, and the shell's half (the system menu's row, the
dialog's keys, the shelf, the first-run ask, Files' Send).

## 8. Where the numbers live

Per-cart fps and frame ms in `docs/perf/ledger.md` (generated; the stories in
**#66**), P4 figures in **#58**; both win over any number here. The full ledgers — latency tables, phase-by-phase P4 verdicts,
refuted avenues, the unbuilt levers (more players, range, power)
— stay in the campaigns above.
