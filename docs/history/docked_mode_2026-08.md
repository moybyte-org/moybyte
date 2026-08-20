# Docked mode: one console, two heads — the wasm browser head + the device as peripheral (2026-08)

**Status: DRAFT direction proposal — nothing built.** Written from the
2026-08-04 owner discussion; needs the adversarial arch + perf review pass
before anything lands. Supersedes (if adopted): the webview-as-editing-surface
investment (#76's perf goal, the per-WM-surface streams gate in #100) — NOT the
web runner (#151), which this builds on, and NOT the streaming view's
mirror role, which stays (frozen scope, see below).

## One line

Stop streaming the console's pixels to the browser and start running the
console *in* the browser (the existing MicroPython-WASM build), with the device
docked as its hardware peripheral — store, radio, pins — over a small
local RPC; the screen is always live wherever the session actually is, and
carts move between heads as a one-keystroke handoff, never a live stream.

## Principles (owner, 2026-08-04)

1. **Local-first is a selling feature.** Every flow in this document works on a
   LAN with zero internet, and — via the SoftAP and USB fallbacks — with zero
   infrastructure at all. Online paths (gallery, cloud sideload) are strictly
   opt-in extras and never the primary mechanism: a deliberate divergence from
   the cloud-mediated sideload pattern common in commercial handheld
   ecosystems, where "wireless" quietly means "through our servers".
2. **Physical possession is consent.** Pairing rides the doctrine the OTA
   design already established ("writing to the SD card is a physical act of
   consent"): the device shows a PIN/QR on its own glass; whoever can read the
   glass may dock. The RPC carries the resulting session token — an endpoint
   that writes flash and drives GPIO is never open to the whole classroom WiFi.
   A USB cable is the same consent expressed as hardware.
3. **One authoritative console at a time.** Divergence, not bandwidth, is the
   hard problem of any two-console design. Docking transfers *headship*; it
   never creates a second writer.

## The problem this solves

Today there are two web stacks:

- The **streaming web view** (#22/#41/#100): the live console's draw commands
  over WS to a browser page. On the S3 it is pinned at the WiFi ceiling
  (~72 KB/s → sub-10fps chrome, #76); as an *editing* surface it will never
  feel right, and its server half (deltas, keyframe latch, per-surface
  streams) is the most bug-prone subsystem in the tree (#179/#180/#182).
- The **wasm runner** (#151): the same frozen console compiled to the browser,
  full speed, browser-as-GPU. Today runner-only (no store, no hardware).

Two stacks, and the slow one carries the important job. Docked mode gives the
important job to the fast one and shrinks the slow one to an accessory. The
browser head is NOT a simulator of the console — it is the same frozen
runtime, VM and mixer, so there is no sim-vs-device drift class of bugs (the
known failure mode of browser makers whose runtime approximates the device).

## Session roles

A connection to the device declares one of three roles on the same channel +
token:

- **Head** (at most one): owns the store and the game. Either the device
  itself (undocked — the normal handheld/desktop console, exactly today) or a
  browser wasm console (docked — the device shows a DOCKED placard and serves
  hardware RPC).
- **Controller** (any number): an input-only feed with an assigned player
  slot. The page's existing touch joypad (#42) joins a running game as
  player N; the cart reads it through #65's `btn(name, p)` — the API already
  reserved for local co-op. Couch co-op on the P4's big screen with two phones
  as gamepads needs nothing else and no docked store machinery at all.
- **Mirror** (any number, watch-first): today's streaming view, demoted to an
  accessory with a deliberately frozen scope (below).

Head-vs-controller is also the answer to same-device multiplayer: one game
instance, extra people are gamepads. Two *full* consoles playing each other
stays the separate #65 two-console/ESP-NOW track (a browser head may use the
relay below, with its latency caveat).

## What the device exposes when docked

| backend | verdict | shape |
|---|---|---|
| cart store + saves | **yes** | attach = pull the full cart snapshot (KBs; the store is file-shaped and `carts_store` is already an injected seam). Reads from the snapshot; writes queue through async. Matches the console's own persistence model — commits are checkpoint-shaped and slower on-device (#154) than a LAN round trip. |
| pmem | **yes** | rides the store; stays where the game ran (no live save-sync — game state lives with the game). |
| GPIO | **yes** | `pin(n, v)` / `pin(n)` RPC verbs. 10–30 ms LAN latency is invisible for kid robotics; 30 Hz polling is 30 tiny messages/s. The natural substrate for #9 (Lego). Near-zero over USB. |
| ESP-NOW | **relay, with caveat** | the device radiates on the head's behalf and pushes received packets up. Adds a hop each way — fine for turn-based/casual, degrades twitch play. Device-vs-device stays native. |
| screen | **no — handoff instead** | streaming the browser head's frames down to the glass re-imports the bandwidth problem in reverse. The glass is served by choosing the right head (below). |

The wasm build has no asyncify, so store calls stay synchronous against the
pulled snapshot; only the write queue is async. Link drop mid-session loses
nothing: the queue drains on reattach, and the per-project undo journal (#111)
is the reconciliation safety net.

## Transports: the RPC is transport-agnostic — WiFi first, USB as the second lane

The RPC is small framed messages + file payloads; nothing about it assumes a
socket. Two lanes, chosen per session:

- **WiFi (WS/TCP)** — the default: no cable, works for phones-as-controllers,
  and SoftAP makes it infrastructure-free. Measured ceilings: S3 STA
  ~72 KB/s on the webview path, ~137 KB/s on the 2026-08-02 OTA download
  (both on-glass); SoftAP and the P4's C6 path unmeasured (open question).
  Note the shape of docked traffic is what makes even the SLOW board's WiFi
  sufficient: docking is artifact-shaped, not stream-shaped. A full cart
  store is a few hundred KB pulled ONCE at attach (~2–4 s at the T-Deck's
  ceiling); a commit is one KB-sized file (sub-second). The bandwidth that
  makes frame streaming miserable barely inconveniences docking — the same
  ceiling, opposite verdicts, which is the whole argument of this doc in
  one number.
- **USB serial** — the fallback when WiFi is slow/hostile, and the faster sync
  for bulk payloads (a whole-store pull, recordings, the self-served wasm
  page). Board reality, from the hard-constraints ledger:
  - **P4: available today.** The CH343 serial channel is bidirectional and
    already drives the live console — the #156 on-glass harness (`swipe` /
    `state` / `py` over serial) is a working RPC prototype in all but name.
    Open question: the practical baud ceiling (CH343 rates well above
    115200 are supported by the chip; measure, don't assume). The P4's
    *native* USB stays reserved for #83 (HID host — keyboards/gamepads);
    device-mode sync on it would compete with that and is not assumed here.
  - **T-Deck: WiFi-only, by positioning.** USB-CDC RX is dead under the
    shipped loop (no at-arrival interrupt-char scan in the fork's CDC stack;
    both workaround shapes were built, glass-tested and reverted — see the
    constraints section of CLAUDE.md). TX streams fine, so mirror-out over
    USB would work today. The owner call (2026-08-04): **the T-Deck is the
    enthusiast target** — it does not need the full docked feature set, so
    the fork's CDC RX fix is NOT on this design's critical path. The T-Deck
    docks over WiFi, mirrors everywhere, and anything further there is
    enthusiast-driven, not roadmap.
  - **USB data-disk** (device exposes the store as a mass-storage drive) is
    tempting as a zero-software export path but fights the running console
    for filesystem ownership; parked as an open question, not a plan.

## The screen is the head selector, not a casualty

While a browser is the head, the glass shows DOCKED — and that is the only
configuration in which nobody is looking at the glass anyway. The moments that
want the glass want a *different topology*, one gesture away:

- **RUN ON CONSOLE** (the handoff verb): from the browser head, one keystroke
  pushes the cart down (sub-second) and the device launches it natively.
  Undock-and-play. The glass then shows the *real* thing — native frame rate,
  native touch — which no mirror can, because a mirror shows browser
  performance wearing the device's clothes.
- **Couch co-op / demo:** the device is the head, phones join as controllers.

Docking and undocking must feel like one gesture (open the page → docked; RUN
ON CONSOLE → console again, with everything you just made already on it).

## Mirror mode: what it is still for

The streaming view keeps a real, narrow job list — all *watching*, none
editing:

- **Show-and-tell:** beam the handheld to a laptop + projector for a
  classroom/family demo without moving the session off the device.
- **Remote assist:** a kid is stuck; a parent/teacher on a laptop sees the
  live screen and can tap — the input path already exists.
- **Capture:** record real-hardware footage (GIFs, bug reports) without a
  camera; the sim can't show device-specific behavior.
- **Diagnostics:** the live screen + perf HUD from the field, over nothing
  but the LAN.
- Possibly the docked placard's thumbnail (open question).

Frozen scope: no per-surface streams, no editing ambitions, no further #76
perf work beyond keeping watch-mode usable. It exists, it is cheap to keep,
and every job above is bandwidth-tolerant.

## Local-first mechanics

- **Discovery:** the device shows its address/QR on the glass (it has a screen;
  use it). mDNS as a nicety, never a requirement.
- **Hostile or absent LAN (classroom AP isolation, the cabin):** SoftAP mode —
  the console hosts its own network and serves the wasm page itself. The page
  is a few MB served once, then browser-cached; P4 flash (32 MB) holds it
  comfortably, T-Deck needs a size check. USB is the other
  infrastructure-free lane where a cable is acceptable.
- **Cloud (strictly opt-in):** the gallery track (#122–#125) can later offer an
  account-mediated push (upload → device pulls), reusing the OTA
  manifest/signing plumbing. It is a sharing feature, not a transport this
  design depends on — and the LAN/card override always wins, as in OTA.

## What dies, what stays

- Dies (as investment targets): the streaming view as an editing surface; the
  per-WM-surface stream wiring gate (#100 Stage 9) as a perf roadmap; further
  #76 optimization beyond keeping mirror mode usable.
- Stays: `moy_webserver`'s socket/HTTP core (the RPC rides it), the page's
  input surfaces (they become the controller role), the wasm runner and its
  whole toolchain, the device console untouched when undocked.

## Board positioning (owner, 2026-08-04)

The **P4 is the reference target** for docked mode — the desktop-tier board a
kid docks a browser to, with both transports and the whole staging plan below.
The **T-Deck is the enthusiast device**: it gets what its constraints already
allow (WiFi docking, mirror, controllers) and is deliberately NOT feature-par;
gaps there are positioning, not debt. Per-board feature matrices in this doc
are therefore intentional, not TODOs.

## Staging (each phase independently shippable)

1. **Store RPC + docked placard + RUN ON CONSOLE** — kills editing-over-stream
   outright. Measure: attach-time snapshot pull, commit round-trip, handoff
   latency — P4 first (both transports); T-Deck WiFi when convenient.
2. **Controller role + player slots** — couch co-op; `btn(name, p)` wiring
   (#65 local half), input tagging in the WS layer.
3. **GPIO verbs** — with #9 as the pull.
4. **ESP-NOW relay** — last, with measured latency published before any cart
   API promises.

## Open questions for the review pass

- Wasm page size vs T-Deck flash for the SoftAP self-serve path.
- SoftAP throughput on each board; CH343 practical baud ceiling on the P4.
- Session-token lifetime/rotation; what a lost token looks like to a kid.
- Whether the docked placard should keep a live mirror thumbnail (cheap frames
  at placard fidelity) or stay static.
- Exact conflict rule if a device-side edit raced the last undock (proposed:
  last-writer-wins per file + journal entry both sides).
- USB data-disk export (store as mass storage): worth the FS-ownership fight?
