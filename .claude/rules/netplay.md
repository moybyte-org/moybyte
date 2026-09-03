---
paths:
  - "runtime/netplay.py"
  - "device/moy_espnow.py"
  - "native/moy_c6/**"
  - "docs/netplay_v1.md"
---

<!-- Lockstep multiplayer: the contract and the radio facts. -->

### Two consoles, one game: ESP-NOW multiplayer SHIPPED (2026-08-22, #65/#7)

**`docs/netplay_v1.md` is the authority** -- the standing contract: the lockstep
model, the give-up doctrine, the radio facts and the hardware terms, dated and
sourced to whatever measurement decided them. Read it before touching any of
this. The two campaigns behind it are the RECORD, archived 2026-08-27:
`docs/history/espnow_multiplayer_2026-08.md` (the S3 transport ledger plus the
four on-glass bugs no host test caught) and
`docs/history/espnow_p4_2026-08.md` (the C6-shim track, phases A-G). What
belongs here is only what a coder must not undo:

- **`runtime/netplay.py`** is the deterministic core and an import-free leaf like
  `players.py` (the button order arrives as a constructor argument, so `cart_api`
  stays its one author). **`device/moy_espnow.py`** owns ESP-NOW's single global
  recv slot for the whole firmware and dispatches by frame type; anything else
  that ever wants the radio registers there rather than opening its own.
- **The payload is INPUTS, never state**, and that is a measurement rather than a
  taste. **A missing input STALLS the sim; it never extrapolates** -- a guessed
  frame desyncs the two sims for good, and silently. Do not "improve" either one.
- **Input delay is ADAPTIVE and raise-only since 2026-08-25**: matches start at
  DELAY=1 (33ms) and a session under real stall pressure raises itself to 2 --
  never down, because a lower delay mid-match can overwrite an input frame the
  peer already played. The 2026-08-22 "DELAY=1 measured and REJECTED (14%)"
  verdict was the PACING's fault, not the radio's -- burst catch-up
  self-hastened both consoles to the margin cliff; with debt-dropping,
  loop-rate stall retries and the guest phase slew, the S3 pair measures
  1.8-2.3% at DELAY=1 while the P4 pair (whose C6-shim transport genuinely
  consumes the one-tick budget) escalates to 2 by ~6s and plays clean. The
  archived campaign's "Input latency" section carries the whole measurement.
- **The BLE keyboard's background scan owned most of a radio board's packet
  loss** -- interval==window was 100% radio duty, 5s on/5s off, costing the P4
  ~40% of inbound espnow at an idle desk while hiding from every blocking
  bench (a stalled frame loop stops re-arming the scan). Background rescans
  are 10% duty + passive now; only the user-facing picker scans continuously.
  If a radio symptom appears only while the loop RUNS, suspect the scan first
  (`device/ble_keyboard.py` has the numbers).
- **modespnow's ring race is patched in every board build**
  (`patches/esp32_espnow_ring_race.patch`): the upstream reader raises
  `buffer error` on a healthy ring when it catches a record mid-write, and
  the ring then really is desynced. `_recover()` re-applies the PHY rate (an
  active() cycle silently resets it to 1M) and counts itself in stats().
- **The tuning recipe is ORDER-SENSITIVE and the ack LIES.** `rxbuf` before
  `active(True)`, the rate after. Both facts cost a session to learn; the module
  header carries each one beside the number that taught it.
- **The handshake is BROADCAST, addressed in the payload.** Unicast here needs a
  peer registration that an `active()` cycle wipes and the first beacon races,
  which cost a night to find. Do not tidy it back.
- **Every input packet carries the frame its sender is WAITING FOR.** A fixed
  redundancy window deadlocks two stalled consoles permanently.
- **Nothing waits forever.** An unanswered invite and a match that falls too far
  behind both give up and re-run the cart solo; a frozen screen with no
  explanation is the one outcome designed out.
- **A restart must not stop the radio** (`Player.release_world` stops the link
  only when `ws.netplay` is None) -- a forming match re-runs the cart, and the
  dying run used to kill the session that caused the restart.
- **Board scope: ALL THREE console boards since 2026-08-24** (a browser still
  has no radio). The morning of that day settled "the P4 cannot join" (the
  flag flip failed at link; ESP-Hosted's RPC carries no ESP-NOW; upstream
  esp-hosted-mcu #19 open and unshipped) -- and the rest of it un-settled the
  verdict by BUILDING the path the verdict named: hosted 2.12.12 + the moy_c6
  shim (seventeen esp_now_* wrappers over custom RPC) + a shimmed C6 slave
  flashed over its own SDIO link. **`docs/history/espnow_p4_2026-08.md` is that
  whole campaign** -- the phases, every on-glass verdict, the BLE regression
  and its fix, and the P4<->T-Deck Brick Siege match at 28.6 ticks/s. Its Phase F
  (2026-08-25) is the stall-rate hunt: the shim's blocking send RPC moved off
  the VM core onto a TX queue, which is what made the pair playable; the stall
  rate and the per-send cost are in that campaign record. Phase G (same day): the C6
  image SHIPS -- CI builds and publishes it, `latest-p4.json` carries a `c6`
  block under its own signature, and Settings -> UPGRADE C6 RADIO
  (`device/moy_c6_update.py`, P4-gated) downloads and flashes the slave over
  SDIO with the slave self-reporting its version (MOYC6_V_VERSION). On-glass
  end to end, including the second-run UP TO DATE.
  **FLOAT WIDTH IS PART OF THE LOCKSTEP CONTRACT** (found by the owner's
  hands, first cross-arch match): two consoles in a match run the same sim,
  and REPR_C's 30-bit floats against boxed 32-bit singles diverge the worlds
  by construction -- 0/1105 world checksums agreeing before the P4 took
  `moybyte_patch_repr_c`, 1106/1106 after. Every board that can hold a link
  runs REPR_C; a board that cannot take it cannot join a match. Found the same day, unrelated to the radio: the P4's WiFi
  buffer set had silently never applied -- `esp_wifi_remote` renames every
  `CONFIG_ESP_WIFI_*` to `CONFIG_WIFI_RMT_*`, so upstream's own fragment asked
  with a dead name and the build carried 10/32/32/6/6 against a 65534 TCP
  window (the S3 commit's "half the set is worse than none" state, 0890249).
  The P4's sdkconfig.board now states the set in the RMT namespace, with the
  prose; numbers in #58.
- **LOCAL 2P is the same cart API with no radio at all** (#65 Phase 1): Settings
  -> **2 PLAYERS** gives a paired Bluetooth keyboard the second player slot, so
  two kids share one screen using two real keyboards. Capability-gated on
  `ws.second_keyboard()`, which is non-None only where a board ALREADY has a
  keyboard of its own (the T-Deck); elsewhere the Bluetooth one is `ws.keyboard`
  and reassigning it would strand player one. The mechanism is entirely #26's: a
  source carries a player, two disagreeing IS multiplayer. Two honesty rules fell
  out and both are pinned -- the setting REFUSES where it cannot work, and a
  DISCONNECTED keyboard releases its slot rather than leaving a cart with a
  character nobody drives. **Dividing the T-Deck's built-in keyboard between two
  kids was built and REVERTED the same day (owner call, 2026-08-22): that thumb
  keyboard is far too small for two people, and the second keyboard is the
  answer.**
- **`system_carts/brick_siege.moy` and `harpoon_pop.moy` are two-player**, and
  read `btn(name, i)` without learning where the second pad came from -- the
  point of one API. The Lua twin is ported in step (its parity test compares
  every draw call for 3000 frames). Brick Siege's roster global had to be renamed
  `players` -> `tanks`: `players` is the API verb's name, and a list shadowing it
  made the cart call a list the moment it asked how many players there were.
- **A Lua cart could never see a second player** until 2026-08-22: the moycore
  snapshot has slots for player two and nothing filled them, so libmoy's
  `players()` answered 1 forever and the line-faithful Lua twin of a 2P cart
  fielded one tank where the Python original fielded two. Both tiers feed it now.
- **The master audio level persists across a cart start** (`ws.system["volume"]`,
  applied in `project._build_audio`). The backend is rebuilt per run, so `vol 0`
  at the launcher used to print "no audio backend" and change nothing -- a mute
  that looked like it worked until the next game played at full volume.

