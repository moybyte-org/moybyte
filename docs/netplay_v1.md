# Netplay v1 — the two-console lockstep contract

**Status: SHIPPED and BINDING (last amended 2026-08-27).** Two kids open the
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
lockstep frame is the kernel Player's (`moy_play_lockstep`).

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

**Input delay is ADAPTIVE, opens at one tick (33 ms at 30 Hz), and only ever RISES**
(2026-08-25). Delay is each console's private sampling lead over its own tape,
so a raise cannot rewrite an input the peer may already have played, needs no
agreement on the wire, and may differ between the sides — a verification match
held the T-Deck at 1 against the P4 at 2 throughout, 29.5 ticks/s and 2.0%
stalls. A DROP is the unsafe direction: it can overwrite a frame already
emitted. A session seeing ≥12 distinct stalled ticks in a 3 s window at 30 Hz
(24 at 60; the first window is formation grace) raises one tick and says so on
serial, up to 66 ms (two ticks at 30 Hz, four at 60); an earlier bar of 5 with
no grace escalated every match always, because ambient-RF loss arrives in bursts.

**A cart paced 60 or "free" ticks at 60 Hz in a match** (owner, 2026-11-06: "i
dont want a higher delay"), so one tick of delay is 16.7 ms. The host names the
tick in START from its cart's manifest pace (`players.match_rate`) and the
guest takes it; a cart paced 30, or undeclared, keeps 30, because its logic
counts frames. What is a time stays a time: the delay's ceiling
(`MOY_MATCH_DELAY_MAX_MS`) and the escalation window are milliseconds and
seconds, their tick counts scaled by the rate. A cart paced 60 also stops
running at half speed in a match, which the fixed 30 Hz tick did to it. `link
rate 30|60|auto` on the dev channel forces the tick a host asks, for an A/B.

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
Siege drew every loop frame of a match, a count no two boards share. CPython and
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
`MOY_MATCH_PROTO` went to 2 with it, and consoles on different protocols do not
see each other.

**The handshake is BROADCAST, addressed in the payload** — START and JOIN carry
the destination MAC as six bytes. Unicast was the protocol's only such frame and
kept vanishing: 376 input frames and 20 beacons arrived against **zero**
invites, while a hand-sent unicast to the same MAC in that session was fine
(2026-08-22). ESP-NOW delivers unicast only from a REGISTERED peer, an
`active()` cycle wipes that table, and it races the first beacon. Do not tidy it
back. The invite re-sends until the guest's first input frame lands; one dropped
START used to deadlock both consoles for good.

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

## 4. Radio facts, one session each

- **A restart must not stop the link.** `Player.release_world` stops it only
  when `ws.netplay` is None. A match forms by a peer's invite reaching a console
  already playing, which re-runs the cart from frame zero; the dying run used to
  tear down the session that caused the restart, presenting as a host frozen at
  frame 0 showing a tank nobody drove.
- **The tuning recipe is ORDER-SENSITIVE and the ack LIES.** `rxbuf=32768` goes
  in BEFORE `active(True)` — reconfiguring the ring live desyncs it until a full
  activation cycle — and `RATE_54M` after. At the 526-byte default, 64 of 200
  messages arrived while `send(sync=True)` answered True for every one.
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
`ws.second_keyboard()`, non-None only where the board owns a keyboard already
(the T-Deck); on a touch-only board the Bluetooth keyboard IS `ws.keyboard`, so
handing it to player two leaves player one with nothing. Two honesty rules, both
pinned: the setting **refuses** where it cannot work rather than reporting ON,
and a **disconnected** keyboard gives its slot back, the slot being an intent
re-resolved every poll — a latched one leaves a cart fielding a character nobody
can move. **Dividing the T-Deck's built-in keyboard between two kids shipped and
was pulled inside a day** (owner, 2026-08-22): it worked, and that thumb
keyboard is five centimetres wide.

## 7. Where the numbers live

Per-cart fps and frame budgets in **#66**, P4 figures in **#58**; both win over
any number here. The full ledgers — latency tables, phase-by-phase P4 verdicts,
refuted avenues, the unbuilt levers (beaming a cart, more players, range, power)
— stay in the campaigns above.
