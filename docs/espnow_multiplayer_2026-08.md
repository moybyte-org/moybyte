# Two consoles, one game: ESP-NOW multiplayer on the S3 boards (2026-08)

**Status: BUILT (2026-08-22).** The transport was measured on glass on
2026-08-20 and those measurements are the whole argument below; the layer on top
of them landed two days later. Tracker: **#65** (the cart API and both phases),
**#7** (the radio), **#99** (the umbrella).

This document exists because the measurement session's findings lived only in a
terminal scrollback for two days. They are decisions with dates, so they belong
in the repo.

## The one line

Two kids each open the same game on their own console; the consoles find each
other over ESP-NOW and play one game on two screens, exchanging **inputs** at a
fixed 30Hz tick. No router, no pairing screen, no code to type — being in the
same room is the agreement, the same doctrine the OTA design set for the SD card.

## What the hardware actually said (T-Deck ↔ Guition, on glass, 2026-08-20)

Both S3 boards, shipped firmware, no reflash: mainline v1.28 defaults
`MICROPY_PY_ESPNOW` to 1 (`ports/esp32/mpconfigport.h:94`), neither S3 board
config overrides it, and both images already carried the symbols.

### Throughput, and the 26× that was sitting in a default

48 KB delivered end to end, receiver confirming every frame:

| config | time | rate | delivered |
|---|---|---|---|
| stock (`pm=PM_PERFORMANCE`, `RATE_1M`, rxbuf 526) | 1680 ms | 238 kbps | 200/200 |
| `pm=NONE` + `RATE_54M`, rxbuf 8192 | 62 ms | 6451 kbps | 193/200 |
| **`pm=NONE` + `RATE_54M`, rxbuf 32768** | **65 ms** | **6153 kbps** | **200/200** |

MicroPython ships ESP-NOW at **1 Mbps** with no rate adaptation, which is why a
stock link measures ~208 kbps — Espressif's own published open-air figure, near
enough exactly. `e.config(rate=espnow.RATE_54M)` is one line. Note the middle
row: the faster PHY made the sender outrun the receiver's drain loop, so the ring
had to grow with it — a clean causal chain, and 8K→32K closed it completely.

### Latency, which the radio cannot help

| payload | min | median | p90 | p99 |
|---|---|---|---|---|
| 16 B (an input frame) | 2.5 ms | **5.0 ms** | 7.0 ms | **14.2 ms** |
| 250 B (a state blob) | 8.8 ms | 15.0 ms | 16.8 ms | 17.1 ms |

Median round trip measured 4.9–5.2 ms at **every** rate from 1M to 54M. A 16-byte
frame is 128 µs of airtime at 1 Mbps and 2.4 µs at 54 Mbps; both are noise against
MicroPython call overhead and WiFi task scheduling. **The RTT floor is not the
radio.** Tuning did halve the tail (p90 14.5 → 7.6 ms), which is worth having free.

### The measurement that actually decided the design

Round trip was the wrong metric: real lockstep never does request/response, it
fires unsolicited every tick. So: a 150-tick, 30 fps, fire-and-forget input stream.

> `arrivals n=149 min=29 med=30 p90=40 p99=40 max=40 late(>2frames)=0`

**Zero late frames, in all three configs including the untuned baseline.** With
2 frames of input delay a stall needs a gap over 66 ms; the worst observed was 40.

### Three traps, each learned the hard way

1. **The link-layer ack is not delivery.** At the default `rxbuf` (526 B ≈ two
   frames) only **64 of 200** messages arrived while `send(sync=True)` returned
   `True` for all 200. Espressif documents that dropped packets are still acked.
   Never trust delivery; make loss self-healing.
2. **Reconfiguring `rxbuf` on a live radio permanently desyncs the ring** —
   `ValueError: ESPNow.recv(): buffer error` on every later `recv()`, including
   with nothing in flight, until `active(False)`/`active(True)`. Set it *before*
   activation.
3. **Both ends must keep draining.** With the receiver idle the sender's ack path
   degrades badly: p90 jumped to 9 ms at 16 B and 65 ms at 250 B, with 12% of
   sends unacked. That is a liveness requirement on the protocol, not an
   optimization.

### What did not transfer

`rate=` maps to `esp_now_set_peer_rate_config` and is ESP-NOW-only — ordinary
WiFi already negotiates its best rate, so there is no equivalent dial. `pm=PM_NONE`
measured **nothing** for bulk WiFi transfer (1137 vs 1062 KB/s board-to-board):
it is a latency lever, not a bandwidth one, which is exactly why it helped
ESP-NOW's tail and does nothing for a download.

## The design those numbers bought

**Exchange inputs, never state.** 250 B costs 3× the round trip of 16 B, so
#65's "trade both players' `btn` each frame" is the right architecture and
"replicate game state" is not. The measurement picked the winner.

**Lockstep at a fixed 30Hz** (`runtime/netplay.py`), with three rules:

1. **Input delay of 2 frames.** Frame N's input is sent during N−2 and played on
   N; your own input is delayed identically so both consoles stay symmetric. 66 ms
   of buffer against a 14 ms p99.
2. **Redundancy, not retransmit.** Every packet carries the last 4 frames of
   input. A mask is ONE BYTE, so redundancy is free and a lost packet is healed by
   the *next* one — no round trip, which matters because the ack lies.
3. **A missing input stalls the sim.** It does not extrapolate. Advancing on a
   guess desyncs both consoles permanently and *invisibly* — both keep drawing a
   plausible game, and they are different games. This is why DS link titles hitch
   with "waiting for player", and it is the one behaviour never to "improve".

**Rollback (GGPO-style) is a non-starter here**: it needs the cart's whole world
snapshotted and re-simulated several times a frame, which a kid-authored `.moy`
cart cannot promise.

**Determinism is a precondition, enforced rather than requested:** a fixed
timestep (a variable `dt` diverges on frame one), a shared seed applied at session
construction, and **the host's cart config wins** — "Make it mine" lets each kid
retune their own copy, and two consoles running the same cart with different
tuning diverge silently while both screens still look fine.

Tier limit worth knowing: CPython and MicroPython ship different PRNGs, so
host↔device lockstep would diverge on the first `rnd()`. Device↔device — the
actual use case, and both S3 boards run the same MicroPython — agrees.

## Why one owner module

ESP-NOW has exactly **one** receive-callback slot for the whole firmware;
`esp_now_register_recv_cb()` has no multi-subscriber concept, so a second feature
that wants the radio simply does not get one. `device/moy_espnow.py` is therefore
the owner for the whole console and dispatches inbound frames by type. (The same
call PURR OS made on the same silicon, and the reason #7's third comment flagged
it before any of this was built.)

Draining happens on the **frame loop**, no thread and no callback: at 30Hz an
input frame carries ~2 messages and a 32 KB ring holds hundreds, so a per-frame
slice is comfortable — and it keeps the radio off the core the panel flush needs.

## Board scope, and why the P4 is out

The two S3 boards only. The P4 compiles ESP-NOW **out**
(`MICROPY_PY_ESPNOW (0)` in `boards/MOYBYTE_P4/mpconfigboard.h`) and its WiFi
rides the C6 over SDIO, so whether ESP-NOW works through a co-processor at all is
a real question rather than a flag flip. Couch co-op is the handheld tier's story
anyway. The browser has no radio and never will.

## Local two-player came along for free

The T-Deck is the only board that already HAS a keyboard, which makes it the only
one where a *second* keyboard is a second controller: Settings → **2 PLAYERS**
hands a paired Bluetooth keyboard to player two, and two kids play one console on
two real keyboards with no radio between consoles at all.

That is the #26 source model doing exactly what it was built for — a source
carries a player, and two of them disagreeing *is* multiplayer — so `players()`
reports 2 with no transport, no session and no netcode. Which is the point of
#65's one API: `system_carts/brick_siege.moy` and `harpoon_pop.moy` read
`btn(name, i)` and never learn whether the second pad is a Bluetooth keyboard or
another console over the radio.

Two honesty rules fell out of it, both pinned by tests:

- the setting **refuses** on a board with no second keyboard. On the touch-only
  boards the Bluetooth keyboard *is* `ws.keyboard`, the only one there is, so
  giving it to player two would leave player one with nothing to press — and
  reporting ON anyway would be the frozen-meter bug in another costume;
- an **unconnected** keyboard does not hold a player slot. The slot is an intent
  resolved against the live connection every poll, because a latched slot means a
  cart fielding a second character nobody can move.

**Splitting the T-Deck's own keyboard between two kids was built and REVERTED the
same day** (owner call, 2026-08-22). It worked — W A S D + SPACE against
I J K L + ENTER, decoded from the raw matrix — and it was the wrong idea: that
thumb keyboard is about five centimetres wide and two children cannot share it.
Do not re-propose it; the second keyboard is the answer.

## Four bugs the desk found that the tests could not (2026-08-22)

All four passed every host test and failed on glass, which is the honest record
of what a fake radio cannot model. Each now has a regression test built from what
the hardware did.

1. **A restart tore down the session that caused it.** A match forms by the
   peer's invite arriving while this console is ALREADY playing: the link sets
   the session and re-runs the cart from frame zero. The dying run tore down on
   the way through and stopped the radio — killing the very session that
   triggered the restart. It read as the host stalled at frame 0 forever,
   showing a second tank nobody drove, while the guest played on alone. Nothing
   was logged anywhere.

2. **The invite was sent once, on a link whose ack lies.** Redundancy was applied
   to the input stream and not to the handshake, so a single dropped START
   deadlocked both consoles permanently — and the guest's JOINs were ignored
   because the host believed it was already matched. The invite is now re-sent
   until the guest's first input frame arrives, a still-asking guest is answered
   again, and a host whose peer never replies **gives up and re-runs the cart
   solo** rather than holding a frozen screen.

3. **The handshake was the protocol's only unicast, and unicast kept vanishing.**
   Measured: 376 input frames and 20 beacons received against **zero** invites,
   while a hand-sent unicast to the same MAC in the same session arrived fine.
   ESP-NOW delivers a unicast only from a *registered* peer, and that peer table
   is cleared by an `active()` cycle and races the first beacon. START and JOIN
   are broadcast now, with the destination MAC in the payload — six bytes, and it
   depends on nothing.

4. **A fixed redundancy window is a deadlock.** Redundancy heals a gap only while
   the sender is still inside the window. A console stalled longer than
   REDUNDANCY frames falls out of it, and then *neither* side can move: each
   waits for a frame the other has scrolled past, and because both are stalled
   neither ever sends what the other wants. Two boards ran happily to frame ~150
   and froze solid with packets still flowing both ways. Every packet now carries
   **the frame its sender is waiting for**, so a stalled console is served the
   frame it is stuck on; past a bound the match declares itself dead and the
   console returns the kid to a one-player game.

And one that was not a bug but a wrong rate: the session ticks on its own 30Hz
clock now, because ticking once per *frame* while feeding a fixed 1/30 dt ran the
game at 1.67× speed — both consoles agreeing perfectly on a game in fast-forward.

## On glass, 2026-08-22 (T-Deck ↔ Guition, both S3)

Two consoles, each opening Brick Siege with nothing else typed:

- they discover each other in **under a second** and pair with no UI at all;
- the lower MAC hosts (an arbitrary but symmetric rule, so nobody both hosts and
  waits), the guest re-runs the cart from frame zero, and both consoles report
  **two tanks, `players() == 2`**, one as global player 0 and one as player 1;
- the match runs continuously — frame 7 → 335 across a ten-second sample — with
  the two clocks never apart by more than the serial sampling skew;
- **28 lockstep ticks/s of shared logic** (target 30) while the boards render at
  **40 fps (Guition) and 55 fps (T-Deck)** — one shared game clock, two
  independent frame rates, which is the whole design in one measurement.

## Input latency: where it comes from, and what does not fix it

The felt lag is the lockstep buffer, not the radio. The radio is already at its
ceiling (`RATE_54M`, power save off), and it is not the constraint anyway — the
round-trip floor did not move between 1 Mbps and 54 Mbps.

| source | cost |
|---|---|
| 2 frames of input delay at 30 Hz | 66 ms |
| tick quantisation | up to 33 ms |
| the radio, one way | ~2.5 ms |

**Halving the buffer was measured and is a bad trade** (2026-08-22, both boards,
30 s each): `DELAY = 2` stalls **3.0%** of ticks; `DELAY = 1` stalls **14%** and
advances the game ~8% slower in real time. The limit is not packet loss — it is
PHASE. At `DELAY = 1` the two consoles must stay within one tick of each other,
and they pace on independent clocks, so ordinary ±1 tick drift becomes a stall.
Only a bigger buffer absorbs that, which is what `DELAY = 2` is.

Two things that DID help, both shipped:

- **Send on every frame, not every tick.** The loop runs faster than the
  lockstep clock, so the spare frames are free redundancy against a radio whose
  ack lies — and each copy carries whatever frame the peer last said it needed,
  so a stalled peer is served sooner. Stalls at `DELAY = 2`: 4.3% → **3.0%**.
- **Draw at the lockstep rate.** A linked game's world only changes on the
  shared 30 Hz tick, so drawing it at the panel's rate repainted an identical
  frame one to two times in three. Locking it hands that time back: **58 fps
  solo → 30 fps linked**, and a more regular loop is itself what makes a tick
  less likely to be late.

Note this is a genuine RATE LOCK, unlike **frameskip** (#77), which is a phase
toggle — it renders every second *loop* frame, so it yields half of whatever the
loop is doing (~20 fps on the Guition's 40, ~27 on the T-Deck's 55), not the
30 Hz its own comment claimed from when the loop ran at 60. The two do not
stack: a linked game bypasses the frameskip gate, because frameskip's premise is
logic at the full loop rate and here logic *is* 30 Hz.

## Open, and deliberately not built

- **Beaming a cart** to a friend's console (#7's original milestone). The
  transport is proven — a typical 40 KB cart is ~55 ms at `RATE_54M`, which is
  tap-and-it's-there rather than a progress bar — but it needs its own delivery
  layer (sequencing + retransmit, because the ack lies) and a "do you want this?"
  UX. Nothing about the owner module blocks it.
- **More than two players.** The frame format carries one player's mask; three
  consoles would need a host-authoritative shape, not this one.
- **Range.** `RATE_54M` trades range for speed, which is right for two kids
  sitting together and untested at distance. `RATE_LORA_250K` is the other end of
  the dial.
- **Power.** `pm=PM_NONE` is armed only while a multiplayer cart runs and restored
  on exit, but the idle-draw cost of a linked session on battery is unmeasured —
  a tie-in to #130, which notes the console has no power policy at all.

## The P4 cannot join (settled 2026-08-24, empirically)

The commit that shipped this feature called the P4 "a real question rather than
a flag flip". The question is now answered, and the answer is NO — not by
reading, but by making the linker say it. `MICROPY_PY_ESPNOW (1)` on the P4
board header compiles clean (the headers resolve) and dies at link with 17
undefined symbols: every `esp_now_*` plus `esp_wifi_config_espnow_rate` — the
RATE_54M lever itself. `esp_now_init` is not defined in any archive that board
links; the probe was reverted the same hour.

Why there is nothing to link, each leg verified:

- **ESP-Hosted's RPC has no ESP-NOW.** The vendored 2.7.0 and upstream `main`
  both: zero hits for `esp_now_init` in the whole repo, and the only `ESPNOW`
  strings are the `espnow_max_encrypt_num` field inside `wifi_init_config_t`
  (marshalled to the C6 and ignored — nothing on the host can call the API it
  sizes). No raw-802.11-tx or promiscuous RPC exists to build one on. The full
  changelog through 2.12.12 has no ESP-NOW entry.
- **It is Espressif's own open feature request** — esp-hosted-mcu #19 (open
  since 2024-11), maintainer 2025-12-31: "definitely on our roadmap", still
  scoping which APIs to carry over RPC; last community ping 2026-08-14,
  unanswered. #34 is the same ask from the other direction.
- **`esp_wifi_remote` injects `esp_now.h` verbatim** (its changelog: "Add
  esp_now.h to injected headers") — declarations so consumers compile, nothing
  behind them. That header is byte-identical to IDF's.
- **IDF's one host-side ESP-NOW path does not fit this board.**
  `CONFIG_ESP_HOST_WIFI_ENABLED` links `lib/esp32_host/libespnow.a`, but its
  `ESP_WIFI_CONTROLLER_TARGET` has exactly one value, `"esp32"` — an ESP32
  companion over its own transport, not the C6 over SDIO, and it is mutually
  exclusive with the `esp_wifi_remote` stack this board's WiFi rides.
- **The community "enabler" (tymorton/esp32-p4-c6-espnow-enabler) is not a
  path**: it force-OTAs a newer hosted slave onto the C6 — it changes nothing
  on the host, where the undefined symbols are.

The viable FUTURE path, recorded so it is found and not re-derived: ESP-Hosted
≥ 2.8.1 ships a documented custom-RPC / Peer Data Transfer seam —
`esp_hosted_send_custom_data(msg_id, data, len)` +
`esp_hosted_register_custom_callback(...)` on BOTH halves, 8166-byte payloads,
and `CONFIG_ESP_HOSTED_COPROCESSOR_APP_MAIN=n` so the slave codebase embeds in
your own C6 app. An ESP-NOW shim on the C6 behind that seam would slot into
`moy_espnow`'s four-method transport surface (`start`/`stop`/`_send`/the
`irecv` drain) with the protocol above unchanged. What it costs, and why it is
deferred rather than done: the vendored hosted component is 2.7.0 (pinned by
MicroPython v1.28's lockfile), host and slave versions must match, so the C6 —
which carries ALL of that board's WiFi and BLE, including the keyboard that is
its only game-exit — gets reflashed. Recovery exists on paper (the `PROG_C6`
header; IO9-low download mode over C6_U0RXD/TXD, per Waveshare and the hosted
board doc) and is unverified on our unit. Do not start this casually.
